#!/usr/bin/env python3
"""
knowledge_agent.py — Regional knowledge ingestion for Slarti

Fetches, parses, chunks, and embeds horticultural knowledge from regional sources
(MU Extension, MOBOT Plant Finder, Almanac, etc.) into the regional_knowledge
pgvector table. Also runs plant discovery and enrichment post-processing.

Usage:
  python3 scripts/knowledge_agent.py                        # normal weekly run
  python3 scripts/knowledge_agent.py --dry-run              # show what would happen
  python3 scripts/knowledge_agent.py --backfill             # initial backfill all sources
  python3 scripts/knowledge_agent.py --source mu_extension  # run one source only
  python3 scripts/knowledge_agent.py --backfill-plants      # backfill existing plant DB
  python3 scripts/knowledge_agent.py --prune                # delete expired chunks >1yr old
  python3 scripts/knowledge_agent.py --growing-season-only  # skip if outside May-Oct
"""

import sys
import os
import json
import uuid
import hashlib
import argparse
import pathlib
import time
import re
import datetime
from typing import Optional
from urllib import request as urllib_request, error as urllib_error

from dotenv import load_dotenv

SCRIPT_DIR = pathlib.Path(__file__).parent
SLARTI_ROOT = SCRIPT_DIR.parent
load_dotenv(dotenv_path=SLARTI_ROOT / '.env')

APP_CONFIG_PATH     = SLARTI_ROOT / 'config' / 'app_config.json'
HEALTH_STATUS_PATH  = SLARTI_ROOT / 'data' / 'system' / 'health_status.json'
WRITE_LOG_PATH      = SLARTI_ROOT / 'data' / 'system' / 'write_log.json'
KNOWLEDGE_CACHE     = SLARTI_ROOT / 'data' / 'system' / 'knowledge_cache.json'
KNOWLEDGE_NEWS      = SLARTI_ROOT / 'data' / 'system' / 'knowledge_news.json'
PLANTS_DIR          = SLARTI_ROOT / 'data' / 'plants'
AGENTS_MD_PATH      = SLARTI_ROOT / 'AGENTS.md'

# Rate limiting: seconds between requests to same domain
RATE_LIMIT_SECONDS = 2

# Chunk target sizes
CHUNK_MIN_TOKENS = 300
CHUNK_MAX_TOKENS = 800

# ── Configuration ─────────────────────────────────────────────────────────────

def load_app_config():
    with open(APP_CONFIG_PATH) as f:
        return json.load(f)


def load_json_safe(path: pathlib.Path):
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def atomic_write_json(path: pathlib.Path, data):
    tmp = str(path) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ── HTTP Fetching ─────────────────────────────────────────────────────────────

_last_fetch_time = {}

def fetch_url(url: str, etag: str = None, last_modified: str = None,
              timeout: int = 20) -> dict:
    """Fetch a URL with ETag/If-Modified-Since caching and rate limiting.

    Returns dict with keys: status (200|304|error), content, etag, last_modified, url
    """
    from urllib.parse import urlparse
    domain = urlparse(url).netloc

    # Rate limiting per domain
    now = time.time()
    if domain in _last_fetch_time:
        elapsed = now - _last_fetch_time[domain]
        if elapsed < RATE_LIMIT_SECONDS:
            time.sleep(RATE_LIMIT_SECONDS - elapsed)
    _last_fetch_time[domain] = time.time()

    headers = {'User-Agent': 'Slarti/1.0 (garden companion; farmington-mo)'}
    if etag:
        headers['If-None-Match'] = etag
    if last_modified:
        headers['If-Modified-Since'] = last_modified

    try:
        req = urllib_request.Request(url, headers=headers)
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            return {
                'status': resp.status,
                'content': resp.read().decode('utf-8', errors='replace'),
                'etag': resp.headers.get('ETag'),
                'last_modified': resp.headers.get('Last-Modified'),
                'url': url,
            }
    except urllib_error.HTTPError as e:
        if e.code == 304:
            return {'status': 304, 'content': None, 'url': url}
        return {'status': 'error', 'content': None, 'url': url, 'error': str(e)}
    except Exception as e:
        return {'status': 'error', 'content': None, 'url': url, 'error': str(e)}


# ── Text Chunking ─────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~0.75 tokens per word for English."""
    return int(len(text.split()) * 1.33)


def chunk_text(text: str, title: str = '', max_tokens: int = CHUNK_MAX_TOKENS,
               overlap_sentences: int = 1) -> list[str]:
    """Split text into chunks at sentence boundaries with overlap."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    if not sentences:
        return []

    chunks = []
    current = []
    current_tokens = 0

    for sentence in sentences:
        sent_tokens = estimate_tokens(sentence)

        if current_tokens + sent_tokens > max_tokens and current:
            chunks.append(' '.join(current))
            # Overlap: keep last N sentences
            current = current[-overlap_sentences:] if overlap_sentences > 0 else []
            current_tokens = sum(estimate_tokens(s) for s in current)

        current.append(sentence)
        current_tokens += sent_tokens

    if current:
        chunk = ' '.join(current)
        if estimate_tokens(chunk) >= CHUNK_MIN_TOKENS or not chunks:
            chunks.append(chunk)
        elif chunks:
            # Too small — merge into previous chunk
            chunks[-1] = chunks[-1] + ' ' + chunk

    return chunks


# ── JSON Extraction ───────────────────────────────────────────────────────

def _extract_json(text: str):
    """Robustly extract JSON array or object from LLM responses.

    Handles markdown code fences, preamble text, and trailing content.
    Returns parsed list/dict, or None if extraction fails.
    """
    if not text:
        return None
    text = text.strip()
    # Strip markdown code fences
    text = re.sub(r'^```(?:json)?\s*\n?', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n?```\s*$', '', text, flags=re.MULTILINE)
    text = text.strip()
    # Try to find and parse the JSON structure
    for start_char, end_char in [('[', ']'), ('{', '}')]:
        start = text.find(start_char)
        if start == -1:
            continue
        end = text.rfind(end_char)
        if end <= start:
            continue
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            continue
    return None


# ── Embedding ─────────────────────────────────────────────────────────────────

def get_embedding(text: str) -> Optional[list[float]]:
    """Get text embedding from Google gemini-embedding-001 (768 dims)."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print('WARNING: google-genai not installed', file=sys.stderr)
        return None

    api_key = os.environ.get('GOOGLE_API_KEY')
    if not api_key:
        print('WARNING: GOOGLE_API_KEY not set', file=sys.stderr)
        return None

    try:
        client = genai.Client(api_key=api_key)
        result = client.models.embed_content(
            model='models/gemini-embedding-001',
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=768),
        )
        return result.embeddings[0].values
    except Exception as e:
        print(f'WARNING: Embedding failed: {e}', file=sys.stderr)
        return None


# ── Database ──────────────────────────────────────────────────────────────────

def db_connect():
    """Connect to Postgres. Returns connection or None."""
    try:
        import psycopg2
    except ImportError:
        print('ERROR: psycopg2 not installed', file=sys.stderr)
        return None

    db_url = (
        f"host=localhost port=5432 dbname={os.environ.get('POSTGRES_DB', 'slarti')} "
        f"user={os.environ.get('POSTGRES_USER', 'slarti')} "
        f"password={os.environ.get('POSTGRES_PASSWORD', '')}"
    )
    try:
        return psycopg2.connect(db_url)
    except Exception as e:
        print(f'ERROR: Could not connect to Postgres: {e}', file=sys.stderr)
        return None


def store_chunk(conn, chunk: dict) -> bool:
    """Insert a single chunk into regional_knowledge. Returns True if new."""
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO regional_knowledge
               (id, source_id, source_url, title, content, content_hash,
                category, season_tags, plant_tags, relevance_zone,
                authority_score, fetched_at, expires_at, embedding)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
               ON CONFLICT (id) DO NOTHING""",
            (chunk['id'], chunk['source_id'], chunk.get('source_url'),
             chunk['title'], chunk['content'], chunk['content_hash'],
             chunk['category'], chunk.get('season_tags'),
             chunk.get('plant_tags'), chunk.get('relevance_zone', '6b'),
             chunk.get('authority_score', 0.7),
             chunk['fetched_at'], chunk.get('expires_at'),
             json.dumps(chunk['embedding']) if chunk.get('embedding') else None)
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        conn.rollback()
        print(f'WARNING: Failed to store chunk {chunk["id"]}: {e}', file=sys.stderr)
        return False
    finally:
        cur.close()


def chunk_exists(conn, content_hash: str) -> bool:
    """Check if a chunk with this content hash already exists."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM regional_knowledge WHERE content_hash = %s LIMIT 1",
                    (content_hash,))
        return cur.fetchone() is not None
    finally:
        cur.close()


def get_knowledge_stats(conn) -> dict:
    """Get counts for AGENTS.md injection."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM regional_knowledge")
        total_chunks = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT source_id) FROM regional_knowledge")
        source_count = cur.fetchone()[0]
        return {'total_chunks': total_chunks, 'source_count': source_count}
    except Exception:
        return {'total_chunks': 0, 'source_count': 0}
    finally:
        cur.close()


def prune_expired(conn, older_than_days: int = 365) -> int:
    """Hard-delete expired chunks older than N days."""
    cur = conn.cursor()
    try:
        cur.execute(
            """DELETE FROM regional_knowledge
               WHERE expires_at < NOW() - INTERVAL '%s days'""",
            (older_than_days,))
        count = cur.rowcount
        conn.commit()
        return count
    except Exception as e:
        conn.rollback()
        print(f'WARNING: Prune failed: {e}', file=sys.stderr)
        return 0
    finally:
        cur.close()


# ── Haiku Summarization ──────────────────────────────────────────────────────

def summarize_with_haiku(text: str, max_tokens: int = 500) -> Optional[str]:
    """Summarize long text with Claude Haiku for chunking efficiency."""
    try:
        import anthropic
    except ImportError:
        return None

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return None

    config = load_app_config()
    model = config.get('claude_model_haiku', 'claude-haiku-4-5-20251001')

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{
                'role': 'user',
                'content': (
                    'Summarize this horticultural article in under 500 tokens. '
                    'Preserve all specific plant names, dates, zone references, '
                    'pest names, treatment recommendations, and Farmington/Missouri-specific '
                    'details. Output only the summary, no preamble.\n\n'
                    f'{text}'
                )
            }]
        )
        return response.content[0].text
    except Exception as e:
        print(f'WARNING: Haiku summarization failed: {e}', file=sys.stderr)
        return None


# ── Season / Category Detection ───────────────────────────────────────────────

SEASON_KEYWORDS = {
    'spring': ['spring', 'march', 'april', 'may', 'transplant', 'start indoors',
               'last frost', 'seed starting'],
    'summer': ['summer', 'june', 'july', 'august', 'heat', 'drought', 'water',
               'harvest', 'hot'],
    'fall': ['fall', 'autumn', 'september', 'october', 'november', 'first frost',
             'cover crop', 'garlic', 'bulb'],
    'winter': ['winter', 'december', 'january', 'february', 'dormant', 'prune',
               'cold', 'freeze', 'planning'],
}

CATEGORY_KEYWORDS = {
    'pest': ['pest', 'insect', 'beetle', 'aphid', 'mite', 'worm', 'grub',
             'bug', 'caterpillar', 'moth', 'slug', 'snail', 'ipm'],
    'disease': ['disease', 'blight', 'fungus', 'fungal', 'rot', 'mildew',
                'wilt', 'rust', 'canker', 'bacterial', 'viral'],
    'planting': ['plant', 'sow', 'seed', 'transplant', 'spacing', 'depth',
                 'germination', 'start indoors'],
    'soil': ['soil', 'compost', 'mulch', 'fertilizer', 'amendment', 'ph',
             'drainage', 'organic matter', 'nitrogen'],
    'technique': ['prune', 'pruning', 'harvest', 'water', 'watering', 'mulch',
                  'trellis', 'staking', 'deadhead', 'propagation'],
    'climate': ['frost', 'freeze', 'temperature', 'zone', 'weather',
                'rainfall', 'humidity', 'heat index'],
    'calendar': ['calendar', 'schedule', 'when to', 'timing', 'window',
                 'month', 'week'],
}


def detect_seasons(text: str) -> list[str]:
    text_lower = text.lower()
    return [season for season, keywords in SEASON_KEYWORDS.items()
            if any(kw in text_lower for kw in keywords)]


def detect_category(text: str) -> str:
    text_lower = text.lower()
    scores = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        scores[cat] = sum(1 for kw in keywords if kw in text_lower)
    best = max(scores, key=scores.get) if scores else 'technique'
    return best if scores.get(best, 0) > 0 else 'technique'


def detect_plant_tags(text: str) -> list[str]:
    """Extract plant names mentioned in text by checking against plant DB."""
    tags = []
    text_lower = text.lower()
    if PLANTS_DIR.exists():
        for pf in PLANTS_DIR.glob('*.json'):
            try:
                with open(pf) as f:
                    plant = json.load(f)
                names = [plant.get('common_name', '').lower()]
                names.extend(a.lower() for a in plant.get('aliases', []))
                if any(name and name in text_lower for name in names):
                    tags.append(plant.get('plant_slug', pf.stem))
            except Exception:
                continue
    return tags[:20]  # cap at 20 tags


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE PARSERS
# ══════════════════════════════════════════════════════════════════════════════

# ── MU Extension ──────────────────────────────────────────────────────────────

MU_EXT_BASE = 'https://extension.missouri.edu'
MU_EXT_NEWS_URL = MU_EXT_BASE + '/programs/master-gardener/news'
MU_EXT_TOPICS = [
    '/topics/horticulture-and-gardening',
    '/topics/flowers-and-houseplants',
    '/topics/fruits',
    '/topics/landscaping',
    '/topics/trees-and-shrubs',
    '/topics/turfgrass',
    '/topics/vegetables',
]

# Publication listing pages — each shows all publications for a category (no pagination needed)
MU_EXT_PUBLICATION_LISTINGS = [
    '/topic?topics=Horticulture+and+gardening&type=publication',  # 41 pubs
    '/topic?topics=Vegetables&type=publication',                   # 62 pubs
    '/topic?topics=Fruits&type=publication',                       # 39 pubs
    '/topic?topics=Flowers+and+houseplants&type=publication',      # 36 pubs
    '/topic?topics=Trees+and+shrubs&type=publication',             # 41 pubs
    '/topic?topics=Turfgrass&type=publication',                    # 30 pubs
    '/topic?topics=Landscaping&type=publication',                  # 15 pubs
]

# Keywords to filter out irrelevant MU Extension content
MU_EXT_EXCLUDE_KEYWORDS = [
    'livestock', 'cattle', 'swine', 'poultry', 'dairy', 'beef',
    'field crop', 'soybean', 'corn harvest', 'wheat', 'hay',
    'commercial agriculture', 'farm management', 'agribusiness',
    'chain saw', 'chainsaw', 'timber', 'logging',
    'permissions policy', 'cookie policy', 'privacy policy',
]


def _parse_html(html: str):
    """Parse HTML with BeautifulSoup, falling back to basic extraction."""
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, 'html.parser')
    except ImportError:
        print('WARNING: beautifulsoup4 not installed, using basic parsing',
              file=sys.stderr)
        return None


def mu_extension_discover_articles(max_pages: int = 8, backfill: bool = False) -> list[str]:
    """Discover article URLs from MU Extension news pages."""
    urls = []
    pages_to_fetch = max_pages if backfill else 2  # Only check first 2 pages normally

    for page_num in range(1, pages_to_fetch + 1):
        page_url = f'{MU_EXT_NEWS_URL}?pg={page_num}'
        result = fetch_url(page_url)
        if result['status'] != 200:
            break

        soup = _parse_html(result['content'])
        if not soup:
            break

        links = soup.find_all('a', href=True)
        for link in links:
            href = link['href']
            if href.startswith('/news/') and href != '/news/':
                full_url = MU_EXT_BASE + href
                if full_url not in urls:
                    urls.append(full_url)

    # Also discover from topic landing pages
    for topic in MU_EXT_TOPICS:
        result = fetch_url(MU_EXT_BASE + topic)
        if result['status'] != 200:
            continue
        soup = _parse_html(result['content'])
        if not soup:
            continue
        for link in soup.find_all('a', href=True):
            href = link['href']
            if (href.startswith('/news/') or href.startswith('/publications/')) and len(href) > 8:
                full_url = MU_EXT_BASE + href
                if full_url not in urls:
                    urls.append(full_url)

    # Deep crawl: publication listing pages (each shows all pubs for a category)
    print(f'  Crawling {len(MU_EXT_PUBLICATION_LISTINGS)} publication listing pages...')
    for listing_path in MU_EXT_PUBLICATION_LISTINGS:
        result = fetch_url(MU_EXT_BASE + listing_path)
        if result['status'] != 200:
            continue
        soup = _parse_html(result['content'])
        if not soup:
            continue
        count_before = len(urls)
        for link in soup.find_all('a', href=True):
            href = link['href']
            if href.startswith('/publications/') and len(href) > 15:
                full_url = MU_EXT_BASE + href
                if full_url not in urls:
                    urls.append(full_url)
        new_found = len(urls) - count_before
        if new_found > 0:
            category = listing_path.split('topics=')[1].split('&')[0].replace('+', ' ')
            print(f'    {category}: {new_found} new publication URLs')

    return urls


def mu_extension_parse_article(url: str, dry_run: bool = False) -> Optional[dict]:
    """Fetch and parse a single MU Extension article."""
    cache = load_json_safe(KNOWLEDGE_CACHE)
    cached = cache.get(url, {})

    # In dry-run, skip ETag caching — always fetch fresh
    if dry_run:
        result = fetch_url(url)
    else:
        result = fetch_url(url, etag=cached.get('etag'),
                           last_modified=cached.get('last_modified'))

    if result['status'] == 304:
        return None  # Not modified
    if result['status'] != 200:
        print(f'  SKIP {url}: HTTP {result.get("status")} {result.get("error", "")}',
              file=sys.stderr)
        return None

    # Update cache (only on real runs)
    if not dry_run:
        cache[url] = {
            'etag': result.get('etag'),
            'last_modified': result.get('last_modified'),
            'last_checked': now_iso(),
        }
        atomic_write_json(KNOWLEDGE_CACHE, cache)

    soup = _parse_html(result['content'])
    if not soup:
        return None

    # Extract title
    title_el = soup.find('h1')
    title = title_el.get_text(strip=True) if title_el else url.split('/')[-1].replace('-', ' ').title()

    # Extract article body — get all paragraph text
    # MU Extension uses semantic HTML with <p> tags for content
    paragraphs = []
    for p in soup.find_all(['p', 'li']):
        text = p.get_text(strip=True)
        if text and len(text) > 30:  # Skip very short fragments
            paragraphs.append(text)

    if not paragraphs:
        return None

    body = '\n\n'.join(paragraphs)

    # Filter out non-gardening content
    body_lower = body.lower()
    if any(kw in body_lower for kw in MU_EXT_EXCLUDE_KEYWORDS):
        if not any(kw in body_lower for kw in ['garden', 'plant', 'flower', 'tree', 'soil', 'pest']):
            return None

    # Extract date if available
    date_str = None
    for text_node in soup.find_all(string=re.compile(r'\w+ \d{1,2}, \d{4}')):
        date_str = text_node.strip()
        break

    return {
        'url': url,
        'title': title,
        'body': body,
        'date': date_str,
        'source_id': 'mu_extension',
        'authority_score': 1.0,
    }


def ingest_mu_extension(conn, dry_run: bool = False,
                         backfill: bool = False) -> dict:
    """Ingest MU Extension articles. Returns stats dict."""
    stats = {'articles_checked': 0, 'chunks_stored': 0, 'skipped': 0}

    print('  Discovering MU Extension articles...')
    urls = mu_extension_discover_articles(backfill=backfill)
    print(f'  Found {len(urls)} article URLs')
    stats['articles_checked'] = len(urls)

    for url in urls:
        article = mu_extension_parse_article(url, dry_run=dry_run)
        if not article:
            stats['skipped'] += 1
            continue

        body = article['body']
        title = article['title']

        # Summarize if too long
        if estimate_tokens(body) > 2000:
            summary = summarize_with_haiku(body) if not dry_run else None
            if summary:
                body = summary

        # Chunk the article
        chunks = chunk_text(body, title=title)

        for i, chunk_text_str in enumerate(chunks):
            content_hash = hashlib.sha256(chunk_text_str.encode()).hexdigest()

            if not dry_run and chunk_exists(conn, content_hash):
                stats['skipped'] += 1
                continue

            chunk_title = f'{title} ({i+1}/{len(chunks)})' if len(chunks) > 1 else title
            seasons = detect_seasons(chunk_text_str)
            category = detect_category(chunk_text_str)
            plant_tags = detect_plant_tags(chunk_text_str)

            chunk_data = {
                'id': str(uuid.uuid4()),
                'source_id': 'mu_extension',
                'source_url': url,
                'title': chunk_title,
                'content': chunk_text_str,
                'content_hash': content_hash,
                'category': category,
                'season_tags': seasons or None,
                'plant_tags': plant_tags or None,
                'relevance_zone': '6b',
                'authority_score': 1.0,
                'fetched_at': now_iso(),
                # News articles expire after 1 year; publications are permanent
                'expires_at': (
                    (datetime.datetime.now(datetime.timezone.utc)
                     + datetime.timedelta(days=365)).isoformat()
                    if '/news/' in url else None
                ),
                'embedding': None,
            }

            if dry_run:
                print(f'    [DRY RUN] Would store: {chunk_title} ({category}, '
                      f'{estimate_tokens(chunk_text_str)} tokens)')
                stats['chunks_stored'] += 1
                continue

            # Embed
            embedding = get_embedding(chunk_text_str)
            chunk_data['embedding'] = embedding

            if store_chunk(conn, chunk_data):
                stats['chunks_stored'] += 1
                print(f'    Stored: {chunk_title}')
            else:
                stats['skipped'] += 1

    return stats


# ── Plant Database Backfill ───────────────────────────────────────────────────

def ingest_plant_database(conn, dry_run: bool = False) -> dict:
    """Backfill existing plant JSON files into regional_knowledge for semantic search."""
    stats = {'plants_processed': 0, 'chunks_stored': 0, 'skipped': 0}

    if not PLANTS_DIR.exists():
        return stats

    plant_files = sorted(PLANTS_DIR.glob('*.json'))
    print(f'  Processing {len(plant_files)} plant files...')

    for pf in plant_files:
        try:
            with open(pf) as f:
                plant = json.load(f)
        except Exception:
            continue

        stats['plants_processed'] += 1
        slug = plant.get('plant_slug', pf.stem)
        common_name = plant.get('common_name', slug)
        scientific_name = plant.get('scientific_name', '')
        zone_notes = plant.get('zone_6b_notes', '')
        pests = ', '.join(plant.get('common_pests', []))
        companions = ', '.join(plant.get('companion_plants', []))
        category_str = plant.get('category', 'plant')
        sun = plant.get('sun', '')
        water = plant.get('water', '')
        days = plant.get('days_to_maturity', '')

        # Build a rich text description for embedding
        parts = [f'{common_name} ({scientific_name})' if scientific_name else common_name]
        if zone_notes:
            parts.append(f'Zone 6b notes: {zone_notes}')
        if pests:
            parts.append(f'Common pests: {pests}')
        if companions:
            parts.append(f'Companion plants: {companions}')
        if sun:
            parts.append(f'Sun: {sun}')
        if water:
            parts.append(f'Water: {water}')
        if days:
            parts.append(f'Days to maturity: {days}')

        planting = plant.get('planting', {})
        if planting:
            p_parts = []
            if planting.get('start_indoors_weeks_before_last_frost'):
                p_parts.append(f'Start indoors {planting["start_indoors_weeks_before_last_frost"]} weeks before last frost')
            if planting.get('direct_sow'):
                p_parts.append('Can direct sow')
            if planting.get('direct_sow_after_last_frost_weeks'):
                p_parts.append(f'Direct sow {planting["direct_sow_after_last_frost_weeks"]} weeks after last frost')
            if p_parts:
                parts.append('Planting: ' + '; '.join(p_parts))

        content = '. '.join(parts)
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        if not dry_run and chunk_exists(conn, content_hash):
            stats['skipped'] += 1
            continue

        chunk_data = {
            'id': str(uuid.uuid4()),
            'source_id': 'plant_database',
            'source_url': None,
            'title': f'{common_name} — Zone 6b Plant Profile',
            'content': content,
            'content_hash': content_hash,
            'category': 'planting',
            'season_tags': None,
            'plant_tags': [slug],
            'relevance_zone': '6b',
            'authority_score': 1.0,
            'fetched_at': now_iso(),
            'expires_at': None,
            'embedding': None,
        }

        if dry_run:
            print(f'    [DRY RUN] Would store: {common_name} ({estimate_tokens(content)} tokens)')
            stats['chunks_stored'] += 1
            continue

        embedding = get_embedding(content)
        chunk_data['embedding'] = embedding

        if store_chunk(conn, chunk_data):
            stats['chunks_stored'] += 1
            print(f'    Stored: {common_name}')
        else:
            stats['skipped'] += 1

    return stats


# Note: MOBOT Plant Finder code was removed — their robots.txt blocks AI crawlers.
# See git history for the original implementation if MOBOT access is needed in the future.


# ── Farmer's Almanac ──────────────────────────────────────────────────────

ALMANAC_URL = 'https://www.almanac.com/gardening/planting-calendar/mo/Farmington'
ALMANAC_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'text/html',
}


def _almanac_extract_first_date(cell) -> str:
    """Extract the first frost-based date range from an Almanac table cell."""
    text = cell.get_text(separator=' | ', strip=True)
    if not text or text == 'N/A':
        return 'N/A'
    parts = [p.strip() for p in text.split('|')]
    for p in parts:
        if p and p != 'N/A':
            match = re.match(r'([A-Z][a-z]+ \d{1,2}(?:-[A-Z][a-z]+ \d{1,2})?)', p)
            if match:
                return match.group(1)
            return p
    return 'N/A'


def ingest_farmers_almanac(conn, dry_run: bool = False,
                            backfill: bool = False) -> dict:
    """Ingest Farmington planting calendar from Old Farmer's Almanac."""
    stats = {'crops_processed': 0, 'chunks_stored': 0, 'skipped': 0}

    print("  Fetching Farmington planting calendar...")
    try:
        req = urllib_request.Request(ALMANAC_URL, headers=ALMANAC_HEADERS)
        with urllib_request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode('utf-8')
    except Exception as e:
        print(f'  ERROR: Could not fetch Almanac: {e}', file=sys.stderr)
        return stats

    soup = _parse_html(html)
    if not soup:
        return stats

    tables = soup.find_all('table')
    if not tables:
        print('  ERROR: No tables found on Almanac page', file=sys.stderr)
        return stats

    source_url = ALMANAC_URL

    # Parse spring planting table
    spring_table = tables[0]
    rows = spring_table.find_all('tr')
    print(f'  Spring table: {len(rows) - 2} crops')

    for row in rows[2:]:  # skip header rows
        cells = row.find_all(['th', 'td'])
        if len(cells) < 4:
            continue
        name = cells[0].get_text(strip=True)
        if not name:
            continue

        indoors = _almanac_extract_first_date(cells[1]) if len(cells) > 1 else 'N/A'
        transplant = _almanac_extract_first_date(cells[2]) if len(cells) > 2 else 'N/A'
        direct = _almanac_extract_first_date(cells[3]) if len(cells) > 3 else 'N/A'
        last_date = _almanac_extract_first_date(cells[4]) if len(cells) > 4 else 'N/A'

        # Build natural text for the chunk
        parts = [f'{name} — Farmington, MO Spring Planting Dates '
                 f'(based on average last frost April 20).']
        if indoors != 'N/A':
            parts.append(f'Start seeds indoors: {indoors}.')
        if transplant != 'N/A':
            parts.append(f'Transplant outdoors: {transplant}.')
        if direct != 'N/A':
            parts.append(f'Direct sow outdoors: {direct}.')
        if last_date != 'N/A':
            parts.append(f'Last date to plant: {last_date}.')

        content = ' '.join(parts)
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        if not dry_run and chunk_exists(conn, content_hash):
            stats['skipped'] += 1
            continue

        slug = name.lower().replace(' ', '-').replace('(', '').replace(')', '')
        slug = re.sub(r'[^a-z0-9-]', '', slug)

        chunk_data = {
            'id': str(uuid.uuid4()),
            'source_id': 'farmers_almanac',
            'source_url': source_url,
            'title': f'{name} — Farmington Spring Planting Dates',
            'content': content,
            'content_hash': content_hash,
            'category': 'calendar',
            'season_tags': ['spring'],
            'plant_tags': [slug] if slug else None,
            'relevance_zone': '6b',
            'authority_score': 1.0,
            'fetched_at': now_iso(),
            'expires_at': None,
            'embedding': None,
        }

        if dry_run:
            print(f'    [DRY RUN] Would store: {name} (spring)')
            stats['chunks_stored'] += 1
            stats['crops_processed'] += 1
            continue

        embedding = get_embedding(content)
        chunk_data['embedding'] = embedding
        if store_chunk(conn, chunk_data):
            stats['chunks_stored'] += 1
            print(f'    Stored: {name} (spring)')
        else:
            stats['skipped'] += 1
        stats['crops_processed'] += 1

    # Parse fall planting table
    if len(tables) > 1:
        fall_table = tables[1]
        rows = fall_table.find_all('tr')
        print(f'  Fall table: {len(rows) - 2} crops')

        for row in rows[2:]:
            cells = row.find_all(['th', 'td'])
            if len(cells) < 4:
                continue
            name = cells[0].get_text(strip=True)
            if not name:
                continue

            sow = _almanac_extract_first_date(cells[1]) if len(cells) > 1 else 'N/A'
            transplant = _almanac_extract_first_date(cells[2]) if len(cells) > 2 else 'N/A'
            days = cells[3].get_text(strip=True) if len(cells) > 3 else ''
            frost_tol = cells[4].get_text(strip=True) if len(cells) > 4 else ''

            parts = [f'{name} — Farmington, MO Fall Planting Dates '
                     f'(based on average first frost October 16).']
            if sow != 'N/A':
                parts.append(f'Direct sow outdoors: {sow}.')
            if transplant != 'N/A':
                parts.append(f'Transplant: {transplant}.')
            if days:
                parts.append(f'Average days to maturity: {days}.')
            if frost_tol:
                parts.append(f'Frost tolerance: {frost_tol}.')

            content = ' '.join(parts)
            content_hash = hashlib.sha256(content.encode()).hexdigest()

            if not dry_run and chunk_exists(conn, content_hash):
                stats['skipped'] += 1
                continue

            slug = name.lower().replace(' ', '-').replace('(', '').replace(')', '')
            slug = re.sub(r'[^a-z0-9-]', '', slug)

            chunk_data = {
                'id': str(uuid.uuid4()),
                'source_id': 'farmers_almanac',
                'source_url': source_url,
                'title': f'{name} — Farmington Fall Planting Dates',
                'content': content,
                'content_hash': content_hash,
                'category': 'calendar',
                'season_tags': ['fall'],
                'plant_tags': [slug] if slug else None,
                'relevance_zone': '6b',
                'authority_score': 1.0,
                'fetched_at': now_iso(),
                'expires_at': None,
                'embedding': None,
            }

            if dry_run:
                print(f'    [DRY RUN] Would store: {name} (fall)')
                stats['chunks_stored'] += 1
                stats['crops_processed'] += 1
                continue

            embedding = get_embedding(content)
            chunk_data['embedding'] = embedding
            if store_chunk(conn, chunk_data):
                stats['chunks_stored'] += 1
                print(f'    Stored: {name} (fall)')
            else:
                stats['skipped'] += 1
            stats['crops_processed'] += 1

    return stats


# ── Static Files ──────────────────────────────────────────────────────────

STATIC_DIR = SLARTI_ROOT / 'data' / 'static'


def ingest_static_files(conn, dry_run: bool = False,
                         backfill: bool = False) -> dict:
    """Ingest markdown files from data/static/ as high-authority knowledge."""
    stats = {'files_processed': 0, 'chunks_stored': 0, 'skipped': 0}

    if not STATIC_DIR.exists():
        STATIC_DIR.mkdir(parents=True, exist_ok=True)
        print('  Created data/static/ directory')
        return stats

    md_files = sorted(STATIC_DIR.glob('*.md'))
    if not md_files:
        print('  No .md files in data/static/')
        return stats

    print(f'  Processing {len(md_files)} static files...')

    for md_path in md_files:
        stats['files_processed'] += 1
        title = md_path.stem.replace('-', ' ').replace('_', ' ').title()
        body = md_path.read_text(encoding='utf-8')

        if not body.strip():
            continue

        chunks = chunk_text(body, title=title)

        for i, chunk_text_str in enumerate(chunks):
            content_hash = hashlib.sha256(chunk_text_str.encode()).hexdigest()

            if not dry_run and chunk_exists(conn, content_hash):
                stats['skipped'] += 1
                continue

            chunk_title = f'{title} ({i+1}/{len(chunks)})' if len(chunks) > 1 else title
            seasons = detect_seasons(chunk_text_str)
            category = detect_category(chunk_text_str)
            plant_tags = detect_plant_tags(chunk_text_str)

            chunk_data = {
                'id': str(uuid.uuid4()),
                'source_id': 'static',
                'source_url': None,
                'title': chunk_title,
                'content': chunk_text_str,
                'content_hash': content_hash,
                'category': category,
                'season_tags': seasons or None,
                'plant_tags': plant_tags or None,
                'relevance_zone': '6b',
                'authority_score': 1.0,
                'fetched_at': now_iso(),
                'expires_at': None,
                'embedding': None,
            }

            if dry_run:
                print(f'    [DRY RUN] Would store: {chunk_title}')
                stats['chunks_stored'] += 1
                continue

            embedding = get_embedding(chunk_text_str)
            chunk_data['embedding'] = embedding
            if store_chunk(conn, chunk_data):
                stats['chunks_stored'] += 1
                print(f'    Stored: {chunk_title}')
            else:
                stats['skipped'] += 1

    return stats


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

# Each source defines: ingest function, cadence in days, authority score
SOURCE_REGISTRY = {
    'mu_extension': {
        'ingest_fn': ingest_mu_extension,
        'cadence_days': 30,      # Monthly
        'description': 'MU Extension gardening guides and IPM',
    },
    'plant_database': {
        'ingest_fn': ingest_plant_database,
        'cadence_days': 999,     # Only on backfill
        'description': 'Existing Slarti plant JSON files',
    },
    'farmers_almanac': {
        'ingest_fn': ingest_farmers_almanac,
        'cadence_days': 180,     # Twice yearly (spring + fall refresh)
        'description': "Old Farmer's Almanac — Farmington planting calendar",
    },
    'static': {
        'ingest_fn': ingest_static_files,
        'cadence_days': 999,     # Manual only (--backfill --source static)
        'description': 'Static knowledge files from data/static/',
    },
}


def source_is_due(source_id: str, cadence_days: int) -> bool:
    """Check if a source is due for update based on its cadence."""
    cache = load_json_safe(KNOWLEDGE_CACHE)
    last_run = cache.get(f'{source_id}_last_run')
    if not last_run:
        return True
    try:
        last_dt = datetime.datetime.fromisoformat(last_run)
        elapsed = (datetime.datetime.now(datetime.timezone.utc) - last_dt).days
        return elapsed >= cadence_days
    except Exception:
        return True


def mark_source_run(source_id: str):
    """Record that a source was just run."""
    cache = load_json_safe(KNOWLEDGE_CACHE)
    cache[f'{source_id}_last_run'] = now_iso()
    atomic_write_json(KNOWLEDGE_CACHE, cache)


# ══════════════════════════════════════════════════════════════════════════════
# NEWS DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def record_news_items(source_id: str, chunks_stored: int, title: str = None):
    """Log new knowledge items for proactive surfacing by heartbeat."""
    if chunks_stored == 0:
        return

    news = load_json_safe(KNOWLEDGE_NEWS)
    if 'items' not in news:
        news['items'] = []

    news['items'].append({
        'source_id': source_id,
        'title': title or f'{chunks_stored} new items from {source_id}',
        'chunks_added': chunks_stored,
        'relevance_score': 0.8 if source_id in ('mu_extension', 'mobot') else 0.6,
        'detected_at': now_iso(),
        'surfaced': False,
    })

    # Keep only last 50 items
    news['items'] = news['items'][-50:]
    atomic_write_json(KNOWLEDGE_NEWS, news)


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2: PLANT DISCOVERY
# ══════════════════════════════════════════════════════════════════════════════

def discover_plants_from_chunks(conn, dry_run: bool = False) -> dict:
    """Scan recent regional_knowledge chunks for plant references.

    For unknown plants mentioned in 2+ authority sources, creates new plant
    JSON files in data/plants/ at confidence 0.7.
    For known plants, enriches zone_6b_notes with new information.
    """
    stats = {'plants_discovered': 0, 'plants_enriched': 0, 'skipped': 0}

    try:
        import anthropic
    except ImportError:
        print('  WARNING: anthropic not installed — skipping plant discovery',
              file=sys.stderr)
        return stats

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print('  WARNING: ANTHROPIC_API_KEY not set — skipping plant discovery',
              file=sys.stderr)
        return stats

    config = load_app_config()
    haiku_model = config.get('claude_model_haiku', 'claude-haiku-4-5-20251001')

    # Get chunks added in the last 24 hours (or all if backfill)
    cur = conn.cursor()
    cur.execute(
        """SELECT id, source_id, title, content, authority_score
           FROM regional_knowledge
           WHERE created_at > NOW() - INTERVAL '24 hours'
           AND source_id != 'plant_database'
           ORDER BY created_at DESC LIMIT 200"""
    )
    recent_chunks = cur.fetchall()
    cur.close()

    if not recent_chunks:
        print('  No recent chunks to scan for plants.')
        return stats

    print(f'  Scanning {len(recent_chunks)} recent chunks for plant references...')

    # Load existing plant database for matching
    existing_plants = {}
    if PLANTS_DIR.exists():
        for pf in PLANTS_DIR.glob('*.json'):
            try:
                with open(pf) as f:
                    plant = json.load(f)
                slug = plant.get('plant_slug', pf.stem)
                names = {slug, plant.get('common_name', '').lower()}
                names.update(a.lower() for a in plant.get('aliases', []))
                if plant.get('scientific_name'):
                    names.add(plant['scientific_name'].lower())
                existing_plants[slug] = {'names': names, 'path': pf, 'data': plant}
            except Exception:
                continue

    # Batch chunks for Haiku extraction (group by 5 for efficiency)
    plant_mentions = {}  # name -> {sources: set, contexts: list, scientific_name: str}

    batch_size = 5
    for i in range(0, len(recent_chunks), batch_size):
        batch = recent_chunks[i:i+batch_size]
        combined_text = '\n\n---\n\n'.join(
            f'[Source: {row[1]}, Authority: {row[4]}]\n{row[3]}'
            for row in batch
        )

        if dry_run:
            print(f'    [DRY RUN] Would scan batch {i//batch_size + 1} '
                  f'({len(batch)} chunks) with Haiku')
            continue

        try:
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=haiku_model,
                max_tokens=500,
                messages=[{
                    'role': 'user',
                    'content': (
                        'List every plant species mentioned in these texts as a JSON array.\n'
                        'Fields: common_name (lowercase), scientific_name (or null), '
                        'category (vegetable/herb/flower/shrub/tree/fruit/grass).\n'
                        'If no plants found, return []\n'
                        'Example: [{"common_name":"zinnia","scientific_name":"Zinnia elegans","category":"flower"}]\n\n'
                        f'{combined_text}'
                    )
                }]
            )

            mentions = _extract_json(response.content[0].text)
            if not mentions or not isinstance(mentions, list):
                continue

            for mention in mentions:
                name = mention.get('common_name', '').lower().strip()
                if not name or len(name) < 2:
                    continue

                source_ids = set()
                for row in batch:
                    source_ids.add(row[1])

                if name not in plant_mentions:
                    plant_mentions[name] = {
                        'sources': set(),
                        'contexts': [],
                        'scientific_name': mention.get('scientific_name') or '',
                        'category': mention.get('category') or 'flower',
                    }
                plant_mentions[name]['sources'].update(source_ids)
                plant_mentions[name]['contexts'].append(
                    mention.get('brief_context', ''))

        except Exception as e:
            print(f'    WARNING: Haiku extraction failed for batch: {e}',
                  file=sys.stderr)
            continue

    if dry_run:
        print(f'  [DRY RUN] Would process plant mentions from {len(recent_chunks)} chunks')
        return stats

    # Process discovered plants
    for name, info in plant_mentions.items():
        # Check if this plant already exists
        is_known = False
        known_slug = None
        sci = (info.get('scientific_name') or '').lower()
        for slug, existing in existing_plants.items():
            if name in existing['names'] or (sci and sci in existing['names']):
                is_known = True
                known_slug = slug
                break

        if is_known and known_slug:
            # Enrich existing plant with new context
            plant_data = existing_plants[known_slug]['data']
            plant_path = existing_plants[known_slug]['path']
            new_context = '; '.join(c for c in info['contexts'] if c)

            if new_context:
                current_notes = plant_data.get('zone_6b_notes', '')
                # Don't duplicate info
                if new_context.lower() not in current_notes.lower():
                    plant_data['zone_6b_notes'] = (
                        current_notes + ' ' + new_context
                    ).strip()
                    atomic_write_json(plant_path, plant_data)
                    stats['plants_enriched'] += 1
                    print(f'    Enriched: {name} ({known_slug})')

                    # Log the write
                    _log_write('enrichment', known_slug, new_context)

        elif len(info['sources']) >= 2:
            # New plant with 2+ authority sources — create it
            slug = name.replace(' ', '-').replace("'", '').lower()
            slug = re.sub(r'[^a-z0-9-]', '', slug)
            plant_path = PLANTS_DIR / f'{slug}.json'

            if plant_path.exists():
                stats['skipped'] += 1
                continue

            new_plant = {
                'schema_version': '5.2',
                'entity_type': 'plant',
                'plant_slug': slug,
                'common_name': name.title(),
                'scientific_name': info.get('scientific_name', ''),
                'category': info.get('category', 'flower'),
                'aliases': [],
                'zone_6b_notes': '; '.join(c for c in info['contexts'] if c),
                'source': 'knowledge_agent',
                'confidence': 0.7,
            }

            atomic_write_json(plant_path, new_plant)
            stats['plants_discovered'] += 1
            print(f'    Discovered: {name} ({slug}) — '
                  f'{len(info["sources"])} sources')

            # Log and add to news
            _log_write('discovery', slug, f'New plant from {", ".join(info["sources"])}')
            _record_plant_discovery(name, slug, info)

        else:
            stats['skipped'] += 1

    return stats


def _log_write(action: str, subject_id: str, content: str):
    """Log a write to the write_log.json."""
    log = load_json_safe(WRITE_LOG_PATH)
    if not isinstance(log, list):
        log = log.get('writes', []) if isinstance(log, dict) else []
    log.append({
        'timestamp': now_iso(),
        'author': 'system',
        'action': f'knowledge_{action}',
        'subject_id': subject_id,
        'content': content[:200],
    })
    # Keep last 500 entries
    atomic_write_json(WRITE_LOG_PATH, {'writes': log[-500:]})


def _record_plant_discovery(name: str, slug: str, info: dict):
    """Add plant discovery to news items for heartbeat surfacing."""
    news = load_json_safe(KNOWLEDGE_NEWS)
    if 'items' not in news:
        news['items'] = []
    news['items'].append({
        'source_id': 'plant_discovery',
        'title': f'Discovered new plant: {name.title()}',
        'plant_slug': slug,
        'sources': list(info['sources']),
        'relevance_score': 0.85,
        'detected_at': now_iso(),
        'surfaced': False,
    })
    atomic_write_json(KNOWLEDGE_NEWS, news)


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3: PLANT ENRICHMENT
# ══════════════════════════════════════════════════════════════════════════════

def enrich_discovered_plants(conn, dry_run: bool = False) -> dict:
    """Research auto-discovered plants to fill missing fields and promote confidence.

    Only runs when there are plants with confidence < 1.0 and source == 'knowledge_agent'.
    """
    stats = {'plants_enriched': 0, 'skipped': 0}

    if not PLANTS_DIR.exists():
        return stats

    # Find candidates: auto-discovered plants that need enrichment
    candidates = []
    for pf in PLANTS_DIR.glob('*.json'):
        try:
            with open(pf) as f:
                plant = json.load(f)
            if (plant.get('source') == 'knowledge_agent' and
                    plant.get('confidence', 1.0) < 0.9):
                candidates.append((pf, plant))
        except Exception:
            continue

    if not candidates:
        print('  No plants need enrichment.')
        return stats

    print(f'  Enriching {len(candidates)} discovered plants...')

    try:
        import anthropic
    except ImportError:
        print('  WARNING: anthropic not installed — skipping enrichment',
              file=sys.stderr)
        return stats

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return stats

    config = load_app_config()
    haiku_model = config.get('claude_model_haiku', 'claude-haiku-4-5-20251001')

    for plant_path, plant in candidates:
        slug = plant.get('plant_slug', plant_path.stem)
        name = plant.get('common_name', slug)
        scientific = plant.get('scientific_name', '')

        # Search regional_knowledge for all mentions
        cur = conn.cursor()
        search_terms = [name.lower()]
        if scientific:
            search_terms.append(scientific.lower())

        # Search by plant_tags and text
        cur.execute(
            """SELECT content, source_id, authority_score
               FROM regional_knowledge
               WHERE (plant_tags @> ARRAY[%s]::text[]
                      OR content ILIKE %s
                      OR content ILIKE %s)
               AND (expires_at IS NULL OR expires_at > NOW())
               ORDER BY authority_score DESC
               LIMIT 20""",
            (slug, f'%{name}%', f'%{scientific}%' if scientific else f'%{name}%')
        )
        rows = cur.fetchall()
        cur.close()

        if not rows:
            stats['skipped'] += 1
            continue

        # Count distinct authority sources
        source_ids = set(row[1] for row in rows)
        combined_knowledge = '\n\n'.join(
            f'[{row[1]}, authority: {row[2]}] {row[0]}' for row in rows
        )

        if dry_run:
            print(f'    [DRY RUN] Would enrich {name} from {len(rows)} chunks, '
                  f'{len(source_ids)} sources')
            stats['plants_enriched'] += 1
            continue

        # Synthesize with Haiku
        try:
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=haiku_model,
                max_tokens=800,
                messages=[{
                    'role': 'user',
                    'content': (
                        f'You are enriching a plant database entry for {name}'
                        f'{" (" + scientific + ")" if scientific else ""}.\n\n'
                        'Based on the following knowledge chunks, extract structured data.\n'
                        'Return ONLY a JSON object with these fields (omit any you cannot determine):\n'
                        '- zone_6b_notes: string (growing advice specific to Zone 6b / Missouri)\n'
                        '- sun: string (full, partial, shade)\n'
                        '- water: string (low, moderate, high + brief note)\n'
                        '- days_to_maturity: integer\n'
                        '- spacing_inches: integer\n'
                        '- type: string (annual, perennial, biennial)\n'
                        '- common_pests: array of strings\n'
                        '- companion_plants: array of strings\n'
                        '- planting: object with start_indoors_weeks_before_last_frost (int), '
                        'direct_sow (bool), direct_sow_after_last_frost_weeks (int)\n\n'
                        f'Knowledge:\n{combined_knowledge}'
                    )
                }]
            )

            enrichment = _extract_json(response.content[0].text)
            if not enrichment or not isinstance(enrichment, dict):
                stats['skipped'] += 1
                continue

            # Merge enrichment into plant data (only fill missing fields)
            updated = False
            for field in ['sun', 'water', 'days_to_maturity', 'spacing_inches',
                          'type', 'common_pests', 'companion_plants', 'planting']:
                if field in enrichment and not plant.get(field):
                    plant[field] = enrichment[field]
                    updated = True

            # Enrich zone_6b_notes (append, don't replace)
            if enrichment.get('zone_6b_notes'):
                current = plant.get('zone_6b_notes', '')
                new_notes = enrichment['zone_6b_notes']
                if new_notes.lower() not in current.lower():
                    plant['zone_6b_notes'] = (current + ' ' + new_notes).strip()
                    updated = True

            # Promote confidence based on source count
            if len(source_ids) >= 3 and plant.get('confidence', 0) < 0.85:
                plant['confidence'] = 0.85
                updated = True
            if (len(source_ids) >= 3 and
                    all(plant.get(f) for f in ['sun', 'water', 'zone_6b_notes', 'common_pests']) and
                    plant.get('confidence', 0) < 0.9):
                plant['confidence'] = 0.9
                updated = True

            if updated:
                atomic_write_json(plant_path, plant)
                stats['plants_enriched'] += 1
                print(f'    Enriched: {name} → confidence {plant.get("confidence", 0.7)}')
                _log_write('enrichment', slug,
                           f'Enriched from {len(source_ids)} sources, '
                           f'confidence → {plant.get("confidence", 0.7)}')
            else:
                stats['skipped'] += 1

        except Exception as e:
            print(f'    WARNING: Enrichment failed for {name}: {e}',
                  file=sys.stderr)
            stats['skipped'] += 1

    return stats


# ══════════════════════════════════════════════════════════════════════════════
# AGENTS.MD INJECTION
# ══════════════════════════════════════════════════════════════════════════════

def generate_seasonal_digest(conn) -> str:
    """Generate a compact seasonal digest of the most relevant knowledge for this week.

    Queries Almanac crops in the ±14 day planting window and top MU Extension
    seasonal tips. Returns formatted text (~300-500 tokens) for AGENTS.md injection.
    """
    today = datetime.date.today()
    month_name = today.strftime('%B')
    day = today.day

    # Determine current season
    month = today.month
    season_map = {1: 'winter', 2: 'winter', 3: 'spring', 4: 'spring',
                  5: 'spring', 6: 'summer', 7: 'summer', 8: 'summer',
                  9: 'fall', 10: 'fall', 11: 'fall', 12: 'winter'}
    current_season = season_map.get(month, 'spring')

    cur = conn.cursor()
    lines = []

    # 1. Find Almanac crops with planting dates mentioning this month
    month_abbr = today.strftime('%b')  # "Apr", "May", etc.
    cur.execute(
        """SELECT title, content FROM regional_knowledge
           WHERE source_id = 'farmers_almanac'
           AND content LIKE %s
           ORDER BY title""",
        (f'%{month_abbr}%',)
    )
    almanac_rows = cur.fetchall()

    # Priority crops — the ones Emily is most likely to ask about
    PRIORITY_CROPS = [
        'tomatoes', 'peppers', 'bell peppers', 'jalapeño peppers',
        'basil', 'cucumbers', 'squash', 'zucchini', 'lettuce',
        'herbs', 'beans', 'green beans', 'peas', 'garlic',
        'strawberries', 'corn', 'sweet corn', 'pumpkins',
        'watermelons', 'spinach', 'kale', 'carrots',
        'lavender', 'rosemary', 'mint', 'dill',
    ]

    if almanac_rows:
        # Group into spring vs fall
        spring_now = [r for r in almanac_rows if 'Spring' in r[0]]
        fall_now = [r for r in almanac_rows if 'Fall' in r[0]]

        active = spring_now if current_season in ('spring', 'summer') else (fall_now or spring_now)

        if active:
            # Sort: priority crops first, then alphabetical
            def crop_sort_key(row):
                crop = row[0].split(' — ')[0].lower() if ' — ' in row[0] else row[0].lower()
                for i, p in enumerate(PRIORITY_CROPS):
                    if p in crop:
                        return (0, i)  # priority crops first, in priority order
                return (1, crop)  # then alphabetical

            active_sorted = sorted(active, key=crop_sort_key)

            lines.append(f'Planting window ({month_name} {day}, Farmington MO):')
            for title, content in active_sorted[:6]:  # Show top 6 priority crops
                crop = title.split(' — ')[0] if ' — ' in title else title
                actions = []
                for part in content.split('. '):
                    if any(kw in part.lower() for kw in ['start seeds', 'transplant', 'direct sow', 'last date']):
                        actions.append(part.strip().rstrip('.'))
                if actions:
                    lines.append(f'- {crop}: {"; ".join(actions[:2])}')
            lines.append('')

    # 2. Get top MU Extension seasonal tips
    cur.execute(
        """SELECT title, content, source_url FROM regional_knowledge
           WHERE source_id = 'mu_extension'
           AND season_tags @> ARRAY[%s]::text[]
           AND (expires_at IS NULL OR expires_at > NOW())
           ORDER BY authority_score DESC, created_at DESC
           LIMIT 3""",
        (current_season,)
    )
    mu_rows = cur.fetchall()

    if mu_rows:
        for title, content, url in mu_rows[:1]:  # Just 1 tip to save space
            lines.append(f'MU Extension tip: {title}')
        lines.append('')

    cur.close()

    if not lines:
        lines.append(f'No seasonal updates for {month_name} {day}.')

    return '\n'.join(lines)


def update_agents_md_knowledge(stats: dict, plant_count: int, digest: str = ''):
    """Inject Regional Knowledge stats + seasonal digest into AGENTS.md."""
    refreshed_at = datetime.datetime.now().strftime('%Y-%m-%d %I:%M %p')

    # Build the combined section: stats + seasonal digest
    section_parts = [
        '\n\n## This Week in the Garden',
        f'*Auto-updated by knowledge_agent.py on {refreshed_at}. Do not edit manually.*',
        f'*Knowledge base: {stats.get("total_chunks", 0)} chunks from '
        f'{stats.get("source_count", 0)} sources | {plant_count} plants*',
        '',
    ]

    if digest:
        section_parts.append(digest)
    else:
        section_parts.append('(No seasonal digest available — run knowledge_agent.py --backfill)')

    new_section = '\n'.join(section_parts)

    existing = AGENTS_MD_PATH.read_text(encoding='utf-8') if AGENTS_MD_PATH.exists() else ''

    weather_marker = '\n---\n\n## Live Conditions'
    # Remove any existing knowledge/digest sections
    for marker in ['\n\n## This Week in the Garden', '\n\n## Regional Knowledge']:
        idx = existing.find(marker)
        if idx != -1:
            w_idx = existing.find(weather_marker, idx + len(marker))
            if w_idx != -1:
                existing = existing[:idx] + existing[w_idx:]
            else:
                existing = existing[:idx]

    # Insert before weather section
    w_idx = existing.find(weather_marker)
    if w_idx != -1:
        content = existing[:w_idx] + new_section + existing[w_idx:]
    else:
        content = existing.rstrip() + new_section

    tmp = str(AGENTS_MD_PATH) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(content)
    os.replace(tmp, AGENTS_MD_PATH)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='Slarti regional knowledge agent')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would happen without writing')
    parser.add_argument('--backfill', action='store_true',
                        help='Initial backfill — run all sources regardless of cadence')
    parser.add_argument('--backfill-plants', action='store_true',
                        help='Backfill only the existing plant database')
    parser.add_argument('--source', metavar='ID',
                        help='Run only this source (e.g., mu_extension, mobot)')
    parser.add_argument('--growing-season-only', action='store_true',
                        help='Exit immediately if outside growing season (May-Oct)')
    parser.add_argument('--prune', action='store_true',
                        help='Delete expired chunks older than 1 year')
    args = parser.parse_args()

    config = load_app_config()

    # Growing season check
    if args.growing_season_only:
        month = datetime.date.today().month
        start = config.get('growing_season_start_month', 5)
        end = config.get('growing_season_end_month', 10)
        if not (start <= month <= end):
            print('Outside growing season — skipping.')
            return

    print(f'Knowledge Agent — {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'  Mode: {"backfill" if args.backfill else "dry-run" if args.dry_run else "update"}')

    # Connect to DB (unless dry-run can skip)
    conn = None
    if not args.dry_run:
        conn = db_connect()
        if not conn:
            print('ERROR: Cannot connect to database', file=sys.stderr)
            sys.exit(1)

    # Handle prune
    if args.prune:
        if args.dry_run:
            print('  [DRY RUN] Would prune expired chunks older than 1 year')
        else:
            count = prune_expired(conn)
            print(f'  Pruned {count} expired chunks')
        if conn:
            conn.close()
        return

    # Handle backfill-plants shortcut
    if args.backfill_plants:
        print('\n── Backfilling plant database ──')
        stats = ingest_plant_database(conn, dry_run=args.dry_run)
        print(f'  Plants: {stats["plants_processed"]}, '
              f'Stored: {stats["chunks_stored"]}, '
              f'Skipped: {stats["skipped"]}')
        if conn:
            conn.close()
        return

    # Phase 1: Ingest from sources
    total_stored = 0
    sources_to_run = {}

    if args.source:
        if args.source not in SOURCE_REGISTRY:
            print(f'ERROR: Unknown source "{args.source}". '
                  f'Available: {", ".join(SOURCE_REGISTRY.keys())}',
                  file=sys.stderr)
            sys.exit(1)
        sources_to_run = {args.source: SOURCE_REGISTRY[args.source]}
    else:
        for source_id, source_info in SOURCE_REGISTRY.items():
            if args.backfill or source_is_due(source_id, source_info['cadence_days']):
                sources_to_run[source_id] = source_info

    if not sources_to_run:
        print('  No sources due for update.')
    else:
        for source_id, source_info in sources_to_run.items():
            print(f'\n── {source_info["description"]} ({source_id}) ──')
            ingest_fn = source_info['ingest_fn']
            stats = ingest_fn(conn, dry_run=args.dry_run, backfill=args.backfill)
            chunks_stored = stats.get('chunks_stored', 0)
            total_stored += chunks_stored

            print(f'  Result: {chunks_stored} chunks stored, '
                  f'{stats.get("skipped", 0)} skipped')

            if not args.dry_run:
                mark_source_run(source_id)
                if chunks_stored > 0:
                    record_news_items(source_id, chunks_stored)

    # Phase 2: Plant Discovery (requires DB connection)
    if (total_stored > 0 or args.backfill) and conn:
        print('\n── Phase 2: Plant Discovery ──')
        discovery_stats = discover_plants_from_chunks(
            conn, dry_run=args.dry_run)
        print(f'  Discovered: {discovery_stats["plants_discovered"]}, '
              f'Enriched: {discovery_stats["plants_enriched"]}, '
              f'Skipped: {discovery_stats["skipped"]}')
    elif (total_stored > 0 or args.backfill) and args.dry_run:
        print('\n── Phase 2: Plant Discovery ──')
        print('  [DRY RUN] Would scan new chunks for plant references')

    # Phase 3: Plant Enrichment (requires DB connection)
    if conn and not args.dry_run:
        print('\n── Phase 3: Plant Enrichment ──')
        enrichment_stats = enrich_discovered_plants(conn, dry_run=args.dry_run)
        print(f'  Enriched: {enrichment_stats["plants_enriched"]}, '
              f'Skipped: {enrichment_stats["skipped"]}')
    elif args.dry_run:
        print('\n── Phase 3: Plant Enrichment ──')
        print('  [DRY RUN] Would enrich discovered plants with missing fields')

    # Phase 4: Finalize
    if args.dry_run:
        plant_count = len(list(PLANTS_DIR.glob('*.json'))) if PLANTS_DIR.exists() else 0
        print(f'\n  [DRY RUN] Would update AGENTS.md with seasonal digest '
              f'({plant_count} plants)')
    elif conn:
        # Generate seasonal digest + update AGENTS.md
        print('\n── Phase 4: Seasonal Digest + AGENTS.md ──')
        digest = generate_seasonal_digest(conn)
        db_stats = get_knowledge_stats(conn)
        plant_count = len(list(PLANTS_DIR.glob('*.json'))) if PLANTS_DIR.exists() else 0
        update_agents_md_knowledge(db_stats, plant_count, digest=digest)
        print(f'  AGENTS.md updated: {db_stats["total_chunks"]} chunks, '
              f'{db_stats["source_count"]} sources, {plant_count} plants')
        print(f'  Digest:\n{digest[:300]}')

        # Update health status
        health = load_json_safe(HEALTH_STATUS_PATH)
        health['last_knowledge_refresh_at'] = now_iso()
        health['knowledge_chunks_total'] = db_stats['total_chunks']
        health['knowledge_sources'] = db_stats['source_count']
        atomic_write_json(HEALTH_STATUS_PATH, health)

    if conn:
        conn.close()

    print(f'\nDone. Total chunks stored this run: {total_stored}')


if __name__ == '__main__':
    main()
