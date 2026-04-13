#!/usr/bin/env python3
"""
pgvector_search.py — Semantic search over Slarti timeline events

Provides search_events() for use by other agents (heartbeat, weekly summary),
plus a CLI for manual testing and debugging.

Usage:
  python3 scripts/pgvector_search.py --query "tomato blight treatment"
  python3 scripts/pgvector_search.py --query "herb bed" --subject herb-bed --limit 5
  python3 scripts/pgvector_search.py --query "frost" --since-days 30
  python3 scripts/pgvector_search.py --query "test" --dry-run
"""
import sys
import os
import json
import argparse
import pathlib
import datetime
from typing import Optional

from dotenv import load_dotenv

SCRIPT_DIR = pathlib.Path(__file__).parent
SLARTI_ROOT = SCRIPT_DIR.parent
load_dotenv(dotenv_path=SLARTI_ROOT / '.env')


def get_embedding(text: str) -> Optional[list[float]]:
    """Get text embedding from Google gemini-embedding-001 (768 dims)."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None

    api_key = os.environ.get('GOOGLE_API_KEY')
    if not api_key:
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


def _connect():
    """Connect to Postgres. Returns connection or None."""
    try:
        import psycopg2
    except ImportError:
        print('WARNING: psycopg2 not installed', file=sys.stderr)
        return None

    db_url = (
        f"host=localhost port=5432 dbname={os.environ.get('POSTGRES_DB', 'slarti')} "
        f"user={os.environ.get('POSTGRES_USER', 'slarti')} "
        f"password={os.environ.get('POSTGRES_PASSWORD', '')}"
    )
    try:
        return psycopg2.connect(db_url)
    except Exception as e:
        print(f'WARNING: Could not connect to Postgres: {e}', file=sys.stderr)
        return None


def search_events(query: str, subject_id: str = None,
                  event_type: str = None, limit: int = 10,
                  since_days: int = None) -> list[dict]:
    """Search timeline events by semantic similarity.

    Falls back to ILIKE text search if embedding fails.

    Args:
        query: Natural language search query
        subject_id: Optional filter by subject (bed name, plant, etc.)
        event_type: Optional filter by event type (BED_FACT, TREATMENT, etc.)
        limit: Max results to return
        since_days: Optional filter to events within N days

    Returns:
        List of event dicts with keys: id, event_type, subject_id, author,
        content, confidence, created_at
    """
    conn = _connect()
    if not conn:
        return []

    try:
        cur = conn.cursor()

        # Try semantic search first
        embedding = get_embedding(query)

        if embedding:
            return _search_by_embedding(cur, embedding, subject_id, event_type,
                                        limit, since_days)
        else:
            print('WARNING: Embedding unavailable — falling back to text search',
                  file=sys.stderr)
            return _search_by_text(cur, query, subject_id, event_type,
                                   limit, since_days)
    except Exception as e:
        print(f'WARNING: Search failed: {e}', file=sys.stderr)
        return []
    finally:
        conn.close()


def _build_where(subject_id: str = None, event_type: str = None,
                 since_days: int = None) -> tuple[str, list]:
    """Build WHERE clause fragments and params."""
    clauses = []
    params = []

    if subject_id:
        clauses.append('subject_id = %s')
        params.append(subject_id)
    if event_type:
        clauses.append('event_type = %s')
        params.append(event_type)
    if since_days:
        clauses.append("created_at >= NOW() - INTERVAL '%s days'")
        params.append(since_days)

    where = ''
    if clauses:
        where = 'WHERE ' + ' AND '.join(clauses)

    return where, params


def _search_by_embedding(cur, embedding: list[float], subject_id: str = None,
                         event_type: str = None, limit: int = 10,
                         since_days: int = None) -> list[dict]:
    """Semantic search using cosine distance."""
    where, params = _build_where(subject_id, event_type, since_days)

    # Add embedding filter: only compare rows that have embeddings
    if where:
        where += ' AND embedding IS NOT NULL'
    else:
        where = 'WHERE embedding IS NOT NULL'

    sql = (
        f"SELECT id, event_type, subject_id, author, content, confidence, created_at "
        f"FROM timeline_events {where} "
        f"ORDER BY embedding <=> %s::vector LIMIT %s"
    )
    params.extend([json.dumps(embedding), limit])

    cur.execute(sql, params)
    return _rows_to_dicts(cur)


def _search_by_text(cur, query: str, subject_id: str = None,
                    event_type: str = None, limit: int = 10,
                    since_days: int = None) -> list[dict]:
    """Fallback text search using ILIKE."""
    where, params = _build_where(subject_id, event_type, since_days)

    # Add text search clause
    if where:
        where += ' AND content ILIKE %s'
    else:
        where = 'WHERE content ILIKE %s'
    params.append(f'%{query}%')

    sql = (
        f"SELECT id, event_type, subject_id, author, content, confidence, created_at "
        f"FROM timeline_events {where} "
        f"ORDER BY created_at DESC LIMIT %s"
    )
    params.append(limit)

    cur.execute(sql, params)
    return _rows_to_dicts(cur)


def _rows_to_dicts(cur) -> list[dict]:
    """Convert cursor rows to list of dicts."""
    columns = ['id', 'event_type', 'subject_id', 'author', 'content',
               'confidence', 'created_at']
    results = []
    for row in cur.fetchall():
        d = dict(zip(columns, row))
        if isinstance(d['created_at'], datetime.datetime):
            d['created_at'] = d['created_at'].isoformat()
        results.append(d)
    return results


# ── Regional Knowledge Search ─────────────────────────────────────────────────

def search_knowledge(query: str, category: str = None,
                     season: str = None, plant: str = None,
                     limit: int = 5, min_similarity: float = 0.55) -> list[dict]:
    """Search regional_knowledge by semantic similarity with metadata filters.

    Filters on category, season_tags, plant_tags, and expiration.
    Only returns chunks above the similarity threshold.

    Args:
        query: Natural language search query
        category: Optional filter (pest, disease, planting, soil, technique, climate, calendar)
        season: Optional season filter (spring, summer, fall, winter)
        plant: Optional plant slug filter
        limit: Max results to return
        min_similarity: Minimum cosine similarity (0-1, higher = more relevant)

    Returns:
        List of dicts with: id, source_id, source_url, title, content,
        category, authority_score, created_at, similarity
    """
    conn = _connect()
    if not conn:
        return []

    try:
        cur = conn.cursor()
        embedding = get_embedding(query)

        if not embedding:
            return _search_knowledge_by_text(cur, query, category, season,
                                              plant, limit)

        # Build WHERE clause with hard filters
        clauses = [
            'embedding IS NOT NULL',
            '(expires_at IS NULL OR expires_at > NOW())',
        ]
        params = []

        if category:
            clauses.append('category = %s')
            params.append(category)
        if season:
            clauses.append('season_tags @> ARRAY[%s]::text[]')
            params.append(season)
        if plant:
            clauses.append('plant_tags @> ARRAY[%s]::text[]')
            params.append(plant)

        where = 'WHERE ' + ' AND '.join(clauses)

        # Cosine distance: lower = more similar. Similarity = 1 - distance.
        # Embedding params must come first (SELECT/ORDER BY) before WHERE filter params
        emb_json = json.dumps(embedding)
        sql = (
            f"SELECT id, source_id, source_url, title, content, category, "
            f"authority_score, created_at, "
            f"1 - (embedding <=> %s::vector) AS similarity "
            f"FROM regional_knowledge {where} "
            f"ORDER BY embedding <=> %s::vector LIMIT %s"
        )
        all_params = [emb_json] + params + [emb_json, limit * 2]

        cur.execute(sql, all_params)

        columns = ['id', 'source_id', 'source_url', 'title', 'content',
                    'category', 'authority_score', 'created_at', 'similarity']
        results = []
        for row in cur.fetchall():
            d = dict(zip(columns, row))
            if d['similarity'] < min_similarity:
                continue
            if isinstance(d['created_at'], datetime.datetime):
                d['created_at'] = d['created_at'].isoformat()
            d['source'] = 'regional_knowledge'
            results.append(d)

        # Sort by weighted score: similarity * authority_score
        results.sort(key=lambda r: r['similarity'] * r.get('authority_score', 0.7),
                     reverse=True)
        return results[:limit]

    except Exception as e:
        print(f'WARNING: Knowledge search failed: {e}', file=sys.stderr)
        return []
    finally:
        conn.close()


def _search_knowledge_by_text(cur, query: str, category: str = None,
                               season: str = None, plant: str = None,
                               limit: int = 5) -> list[dict]:
    """Fallback text search for regional_knowledge."""
    clauses = [
        '(expires_at IS NULL OR expires_at > NOW())',
        'content ILIKE %s',
    ]
    params = [f'%{query}%']

    if category:
        clauses.append('category = %s')
        params.append(category)
    if season:
        clauses.append('season_tags @> ARRAY[%s]::text[]')
        params.append(season)
    if plant:
        clauses.append('plant_tags @> ARRAY[%s]::text[]')
        params.append(plant)

    where = 'WHERE ' + ' AND '.join(clauses)

    sql = (
        f"SELECT id, source_id, source_url, title, content, category, "
        f"authority_score, created_at "
        f"FROM regional_knowledge {where} "
        f"ORDER BY authority_score DESC, created_at DESC LIMIT %s"
    )
    params.append(limit)

    cur.execute(sql, params)
    columns = ['id', 'source_id', 'source_url', 'title', 'content',
                'category', 'authority_score', 'created_at']
    results = []
    for row in cur.fetchall():
        d = dict(zip(columns, row))
        if isinstance(d['created_at'], datetime.datetime):
            d['created_at'] = d['created_at'].isoformat()
        d['source'] = 'regional_knowledge'
        d['similarity'] = None  # text search, no similarity score
        results.append(d)
    return results


def search_all(query: str, limit: int = 10, **kwargs) -> list[dict]:
    """Search BOTH timeline_events and regional_knowledge, returning labeled results.

    This is the primary search function for OpenClaw — it gives Claude access
    to both personal garden history and regional horticultural knowledge.
    """
    events = search_events(query, limit=limit,
                           subject_id=kwargs.get('subject_id'),
                           event_type=kwargs.get('event_type'),
                           since_days=kwargs.get('since_days'))
    for e in events:
        e['source'] = 'garden_history'

    knowledge = search_knowledge(query, limit=limit,
                                  category=kwargs.get('category'),
                                  season=kwargs.get('season'),
                                  plant=kwargs.get('plant'))

    # Interleave: garden history first (personal context), then regional knowledge
    combined = events + knowledge
    return combined[:limit]


def main():
    parser = argparse.ArgumentParser(description='Search Slarti timeline events and regional knowledge')
    parser.add_argument('--query', required=True, help='Natural language search query')
    parser.add_argument('--subject', metavar='ID', help='Filter by subject_id (events)')
    parser.add_argument('--type', metavar='TYPE', help='Filter by event_type (events)')
    parser.add_argument('--limit', type=int, default=10, help='Max results (default: 10)')
    parser.add_argument('--since-days', type=int, metavar='N',
                        help='Only events from the past N days')
    parser.add_argument('--knowledge', action='store_true',
                        help='Search regional knowledge instead of timeline events')
    parser.add_argument('--all', action='store_true', dest='search_all',
                        help='Search both timeline events and regional knowledge')
    parser.add_argument('--category', metavar='CAT',
                        help='Knowledge category filter (pest, disease, planting, soil, etc.)')
    parser.add_argument('--season', metavar='SEASON',
                        help='Season filter (spring, summer, fall, winter)')
    parser.add_argument('--plant', metavar='SLUG',
                        help='Plant slug filter for knowledge search')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show query plan without executing')
    args = parser.parse_args()

    if args.dry_run:
        mode = 'all' if args.search_all else ('knowledge' if args.knowledge else 'events')
        print(f'Query: "{args.query}" (mode: {mode})')
        print(f'Filters: subject={args.subject}, type={args.type}, '
              f'since_days={args.since_days}, category={args.category}, '
              f'season={args.season}, plant={args.plant}, limit={args.limit}')
        embedding = get_embedding(args.query)
        if embedding:
            print(f'Embedding: {len(embedding)} dimensions (first 5: {embedding[:5]})')
        else:
            print('Embedding: unavailable — would fall back to text search')
        return

    if args.search_all:
        results = search_all(
            query=args.query, limit=args.limit,
            subject_id=args.subject, event_type=args.type,
            since_days=args.since_days, category=args.category,
            season=args.season, plant=args.plant,
        )
    elif args.knowledge:
        results = search_knowledge(
            query=args.query, limit=args.limit,
            category=args.category, season=args.season, plant=args.plant,
        )
    else:
        results = search_events(
            query=args.query, subject_id=args.subject,
            event_type=args.type, limit=args.limit,
            since_days=args.since_days,
        )

    if not results:
        print('No results found.')
        return

    print(f'Found {len(results)} result(s):\n')
    for r in results:
        source = r.get('source', 'events')
        if source == 'regional_knowledge':
            sim = f', similarity: {r["similarity"]:.3f}' if r.get('similarity') else ''
            print(f'  [{source}] {r["title"]}')
            print(f'    {r["content"][:200]}...' if len(r.get("content", "")) > 200
                  else f'    {r.get("content", "")}')
            print(f'    source: {r["source_id"]} | category: {r["category"]} | '
                  f'authority: {r["authority_score"]}{sim}')
            if r.get('source_url'):
                print(f'    url: {r["source_url"]}')
        else:
            print(f'  [{r.get("created_at", "")[:10]}] {r.get("event_type", "")} '
                  f'({r.get("author", "")}, {r.get("subject_id", "")})')
            print(f'    {r.get("content", "")}')
            print(f'    confidence: {r.get("confidence", "")}')
        print()


if __name__ == '__main__':
    main()
