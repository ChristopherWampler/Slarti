#!/usr/bin/env python3
"""
extraction_agent.py — Post-conversation memory extraction for Slarti (Agent 2)

Usage:
  python3 scripts/extraction_agent.py                    # process all unprocessed sessions
  python3 scripts/extraction_agent.py --session <id>     # process a specific session
  python3 scripts/extraction_agent.py --regen-garden     # regenerate garden.md only

Reads OpenClaw session JSONL files, extracts facts via Claude, writes to data/events/2026/,
stores embeddings in pgvector, and triggers garden.md regeneration when needed.
"""

import sys
import os
import json
import uuid
import argparse
import datetime
import pathlib
import glob as glob_module
from typing import Optional

from dotenv import load_dotenv

load_dotenv(dotenv_path=pathlib.Path(__file__).parent.parent / '.env')

SLARTI_ROOT = pathlib.Path(os.environ.get('SLARTI_ROOT', '/mnt/c/Openclaw/slarti'))
SESSIONS_DIR = pathlib.Path(os.path.expanduser('~/.openclaw/agents/slarti/sessions'))
# WSL2: if the above doesn't exist, fall back to Windows user path
if not SESSIONS_DIR.exists():
    SESSIONS_DIR = pathlib.Path('/mnt/c/Users/Chris/.openclaw/agents/slarti/sessions')
EVENTS_DIR   = SLARTI_ROOT / 'data' / 'events' / '2026'
BEDS_DIR     = SLARTI_ROOT / 'data' / 'beds'
HEALTH_FILE  = SLARTI_ROOT / 'data' / 'system' / 'health_status.json'
WRITE_LOG    = SLARTI_ROOT / 'data' / 'system' / 'write_log.json'
GARDEN_MD    = SLARTI_ROOT / 'docs' / 'garden.md'
USERS_FILE   = SLARTI_ROOT / 'config' / 'discord_users.json'
CONF_FILE    = SLARTI_ROOT / 'config' / 'confidence_thresholds.json'
PROCESSED_LOG = SLARTI_ROOT / 'data' / 'system' / 'processed_sessions.json'

EVENTS_DIR.mkdir(parents=True, exist_ok=True)

# Load model name from config (fall back to hardcoded if config missing)
try:
    with open(SLARTI_ROOT / 'config' / 'app_config.json') as _f:
        _cfg = json.load(_f)
    CLAUDE_MODEL = _cfg.get('claude_model', 'claude-sonnet-4-6')
except Exception:
    CLAUDE_MODEL = 'claude-sonnet-4-6'


def load_json(path: pathlib.Path) -> dict | list:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_json_atomic(path: pathlib.Path, data):
    """Atomic write: temp file + rename — never write JSON directly."""
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def get_discord_users() -> dict:
    users = load_json(USERS_FILE)
    return users.get('users', {})


def resolve_author(sender_id: str) -> str:
    users = get_discord_users()
    return users.get(str(sender_id), 'system')


def load_processed_sessions() -> set:
    data = load_json(PROCESSED_LOG)
    return set(data) if isinstance(data, list) else set()


def mark_session_processed(session_id: str):
    processed = load_processed_sessions()
    processed.add(session_id)
    save_json_atomic(PROCESSED_LOG, sorted(processed))


def parse_session(session_path: pathlib.Path) -> tuple[list[dict], list[dict]]:
    """Parse an OpenClaw JSONL session file.

    Returns:
        turns: list of {role, author, text} message turns
        photo_attachments: list of {url, author} for any image attachments found
    """
    turns = []
    photo_attachments = []
    with open(session_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get('type') != 'message':
                continue

            msg = event.get('message', {})
            role = msg.get('role')
            content = msg.get('content', [])

            # Extract text from content blocks
            text_parts = []
            sender_id = None
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get('type')

                    if block_type == 'text':
                        text = block.get('text', '')
                        # Parse sender_id from OpenClaw metadata block
                        if '"sender_id"' in text:
                            try:
                                import re
                                meta_match = re.search(r'```json\s*(\{[^`]+\})\s*```', text, re.DOTALL)
                                if meta_match:
                                    meta = json.loads(meta_match.group(1))
                                    sender_id = meta.get('sender_id')
                            except Exception:
                                pass
                        # Get the actual message text (last non-empty line after metadata)
                        lines = [l for l in text.split('\n') if l.strip()]
                        if lines:
                            actual_text = lines[-1].strip()
                            if actual_text and not actual_text.startswith('{') and not actual_text.startswith('```'):
                                text_parts.append(actual_text)

                    elif block_type in ('image_url', 'image'):
                        # Detect image attachments for photo_agent.py
                        url = (block.get('image_url', {}).get('url')
                               or block.get('source', {}).get('url')
                               or block.get('url', ''))
                        if url:
                            author = resolve_author(sender_id) if sender_id else 'unknown'
                            photo_attachments.append({'url': url, 'author': author})

            if text_parts:
                author = resolve_author(sender_id) if sender_id else ('slarti' if role == 'assistant' else 'unknown')
                turns.append({
                    'role': role,
                    'author': author,
                    'text': ' '.join(text_parts)
                })

    return turns, photo_attachments


def build_transcript(turns: list[dict]) -> str:
    lines = []
    for turn in turns:
        label = turn['author'].upper() if turn['role'] == 'user' else 'SLARTI'
        lines.append(f"{label}: {turn['text']}")
    return '\n'.join(lines)


def extract_facts(transcript: str) -> list[dict]:
    """Send transcript to Claude for fact extraction."""
    try:
        import anthropic
    except ImportError:
        print('ERROR: anthropic package not installed. Run: pip install anthropic', file=sys.stderr)
        return []

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print('ERROR: ANTHROPIC_API_KEY not set', file=sys.stderr)
        return []

    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""You are a memory extraction agent for Slarti, a garden companion AI for a family in Farmington, Missouri (Zone 6b).

Read this conversation transcript and extract only NEW, specific facts worth remembering.

Categories:
- BED_FACT: Physical facts about a garden bed (size, location, plants, soil, sun, history)
- DECISION: A design or planning decision made
- TASK: Something that needs to be done
- OBSERVATION: An observation about plants, conditions, or the garden state
- TREATMENT: A pesticide, fertilizer, or care treatment applied
- PREFERENCE: A stated preference about the garden or plants

For each extract, provide:
- category: one of the above
- author: "emily", "chris", or "system" (from the speaker label)
- subject_id: the bed name, plant name, or "garden" if general
- content: the specific fact in plain language
- confidence: 0.0–1.0 (how certain you are this is a real, new fact worth storing)

Rules:
- Only extract concrete, specific facts — not vague impressions
- Skip pleasantries, questions without answers, and conversational filler
- If nothing worth extracting: return {{"extracts": []}}
- Return JSON only, no explanation

Transcript:
{transcript}

Return format:
{{"extracts": [
  {{"category": "BED_FACT", "author": "emily", "subject_id": "herb-bed", "content": "Basil planted last week", "confidence": 0.9}},
  ...
]}}"""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            messages=[{'role': 'user', 'content': prompt}]
        )
        text = response.content[0].text.strip()
        # Strip markdown code fences if present
        if text.startswith('```'):
            text = '\n'.join(text.split('\n')[1:])
            if text.endswith('```'):
                text = text[:-3].strip()
        result = json.loads(text)
        return result.get('extracts', [])
    except Exception as e:
        print(f'ERROR: Claude extraction failed: {e}', file=sys.stderr)
        return []


def get_embedding(text: str) -> Optional[list[float]]:
    """Get text embedding from Google text-embedding-004."""
    try:
        from google import genai
    except ImportError:
        return None

    api_key = os.environ.get('GOOGLE_API_KEY')
    if not api_key:
        return None

    try:
        client = genai.Client(api_key=api_key)
        result = client.models.embed_content(
            model='models/text-embedding-004',
            contents=text
        )
        return result.embeddings[0].values
    except Exception as e:
        print(f'WARNING: Embedding failed: {e}', file=sys.stderr)
        return None


def store_in_pgvector(event_id: str, content: str, metadata: dict, embedding: Optional[list[float]]):
    """Store event + embedding in Postgres pgvector table."""
    try:
        import psycopg2
    except ImportError:
        return

    db_url = (
        f"host=localhost port=5432 dbname={os.environ.get('POSTGRES_DB','slarti')} "
        f"user={os.environ.get('POSTGRES_USER','slarti')} "
        f"password={os.environ.get('POSTGRES_PASSWORD','')}"
    )
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        if embedding:
            cur.execute(
                """INSERT INTO timeline_events (id, event_type, subject_id, author, content, confidence, created_at, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
                   ON CONFLICT (id) DO NOTHING""",
                (
                    event_id,
                    metadata.get('category', 'OBSERVATION'),
                    metadata.get('subject_id', 'garden'),
                    metadata.get('author', 'system'),
                    content,
                    metadata.get('confidence', 0.5),
                    datetime.datetime.utcnow(),
                    json.dumps(embedding)
                )
            )
        else:
            cur.execute(
                """INSERT INTO timeline_events (id, event_type, subject_id, author, content, confidence, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO NOTHING""",
                (
                    event_id,
                    metadata.get('category', 'OBSERVATION'),
                    metadata.get('subject_id', 'garden'),
                    metadata.get('author', 'system'),
                    content,
                    metadata.get('confidence', 0.5),
                    datetime.datetime.utcnow()
                )
            )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f'WARNING: pgvector store failed: {e}', file=sys.stderr)


def write_event_file(extract: dict, session_id: str) -> pathlib.Path:
    """Write extract to data/events/2026/ as a JSON file."""
    event_id = str(uuid.uuid4())
    now = datetime.datetime.utcnow().isoformat() + 'Z'
    event = {
        'schema_version': '5.2',
        'id': event_id,
        'category': extract['category'],
        'author': extract['author'],
        'subject_id': extract.get('subject_id', 'garden'),
        'content': extract['content'],
        'confidence': extract.get('confidence', 0.5),
        'source_session': session_id,
        'created_at': now,
        'outcome': 'saved' if extract.get('confidence', 0) >= 0.80 else 'pending_confirmation'
    }
    if extract['category'] == 'TREATMENT':
        event['follow_up_required'] = True
        event['follow_up_resolved'] = False

    slug = extract.get('subject_id', 'garden').replace(' ', '-').lower()
    filename = f"{now[:10]}_{extract['category'].lower()}_{slug}_{event_id[:8]}.json"
    path = EVENTS_DIR / filename
    save_json_atomic(path, event)
    return path


def log_write(event_path: pathlib.Path, extract: dict):
    """Append to write_log.json."""
    log = load_json(WRITE_LOG)
    if not isinstance(log, list):
        log = []
    log.append({
        'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
        'file': str(event_path.name),
        'category': extract['category'],
        'author': extract['author'],
        'subject_id': extract.get('subject_id', 'garden'),
        'confidence': extract.get('confidence', 0.5)
    })
    save_json_atomic(WRITE_LOG, log)


def regenerate_garden_md():
    """Regenerate docs/garden.md from current bed entities and recent events."""
    try:
        import anthropic
    except ImportError:
        return

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return

    # Load bed entities
    beds = []
    for bed_file in sorted(BEDS_DIR.glob('*.json')):
        try:
            beds.append(load_json(bed_file))
        except Exception:
            pass

    # Load last 30 events
    events = []
    event_files = sorted(EVENTS_DIR.glob('*.json'), reverse=True)[:30]
    for ef in event_files:
        try:
            events.append(load_json(ef))
        except Exception:
            pass

    if not beds and not events:
        print('No bed data yet — skipping garden.md regeneration')
        return

    bed_text = json.dumps(beds, indent=2) if beds else 'No beds recorded yet.'
    event_text = json.dumps(events, indent=2) if events else 'No events recorded yet.'

    prompt = f"""You are Slarti, a warm garden companion AI for Christopher and Emily in Farmington, Missouri (Zone 6b).

Write a concise, warm, human-readable summary of the current garden state in Slarti's voice — 500–800 words.
- No JSON fields or entity IDs
- Use plant names, seasons, and stories
- Ground everything in Zone 6b reality
- Written for Emily first

Current bed data:
{bed_text}

Recent events (most recent first):
{event_text}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1200,
            messages=[{'role': 'user', 'content': prompt}]
        )
        summary = response.content[0].text.strip()
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        content = f"---\nlast_regenerated_at: {now}\n---\n\n{summary}\n"
        tmp = GARDEN_MD.with_suffix('.tmp')
        tmp.write_text(content)
        os.replace(tmp, GARDEN_MD)
        print(f'garden.md regenerated at {now}')

        # Update health status
        health = load_json(HEALTH_FILE)
        health['garden_md_regeneration_failed'] = False
        health['last_memory_write_at'] = now
        save_json_atomic(HEALTH_FILE, health)

    except Exception as e:
        print(f'ERROR: garden.md regeneration failed: {e}', file=sys.stderr)
        health = load_json(HEALTH_FILE)
        health['garden_md_regeneration_failed'] = True
        save_json_atomic(HEALTH_FILE, health)


def process_photos(photo_attachments: list[dict], session_id: str):
    """Trigger photo_agent.py for each photo attachment found in a session."""
    import subprocess as sp
    photo_script = SLARTI_ROOT / 'scripts' / 'photo_agent.py'
    for attachment in photo_attachments:
        url = attachment.get('url', '')
        author = attachment.get('author', 'unknown')
        if not url:
            continue
        print(f'  Processing photo attachment (author: {author})...')
        try:
            result = sp.run(
                [sys.executable, str(photo_script),
                 '--photo-url', url,
                 '--session-id', session_id,
                 '--author', author],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                photo_id = result.stdout.strip().split('\n')[-1]
                print(f'  Photo processed: {photo_id}')
            else:
                print(f'  WARNING: photo_agent.py failed: {result.stderr[:200]}', file=sys.stderr)
        except Exception as e:
            print(f'  WARNING: Could not process photo: {e}', file=sys.stderr)


def process_voice_session(session_path: pathlib.Path) -> int:
    """Process a voice session JSON from data/voice_sessions/. Returns fact count."""
    try:
        session = load_json(session_path)
    except Exception as e:
        print(f'ERROR: Could not read voice session {session_path}: {e}', file=sys.stderr)
        return 0

    session_id = session.get('session_id', session_path.stem)
    transcript = session.get('raw_transcript', '').strip()
    author = session.get('author', 'unknown')

    if not transcript:
        print(f'  No transcript in voice session {session_id} — skipping')
        session['extraction_status'] = 'skipped'
        save_json_atomic(session_path, session)
        return 0

    print(f'Processing voice session: {session_id} (author: {author})')

    # Format as a single-speaker transcript for extract_facts()
    labelled = f"{author.upper()}: {transcript}"
    extracts = extract_facts(labelled)

    if not extracts:
        print(f'  No facts extracted')
        session['extraction_status'] = 'complete'
        session['extracted_events'] = []
        save_json_atomic(session_path, session)
        return 0

    print(f'  Extracted {len(extracts)} facts')
    conf = load_json(CONF_FILE)
    high_threshold = conf.get('thresholds', {}).get('high', {}).get('min', 0.80)
    medium_min = conf.get('thresholds', {}).get('medium', {}).get('min', 0.50)

    needs_garden_regen = False
    extracted_event_ids = []
    count = 0
    for extract in extracts:
        # Force author from session — voice sessions have a single known author
        extract['author'] = author
        confidence = extract.get('confidence', 0)
        if confidence < medium_min:
            print(f'  SKIP (low confidence {confidence:.2f}): {extract.get("content","")[:60]}')
            continue

        event_path = write_event_file(extract, session_id)
        log_write(event_path, extract)
        extracted_event_ids.append(event_path.name)

        embedding = get_embedding(extract['content'])
        store_in_pgvector(
            event_path.stem.split('_')[-1],
            extract['content'],
            extract,
            embedding
        )

        if extract['category'] in ('BED_FACT', 'DECISION'):
            needs_garden_regen = True

        status = 'saved' if confidence >= high_threshold else 'pending_confirmation'
        print(f'  [{status}] {extract["category"]}: {extract.get("content","")[:70]}')
        count += 1

    # Update voice session with extraction results
    session['extraction_status'] = 'complete'
    session['extracted_events'] = extracted_event_ids
    session['extracted_at'] = datetime.datetime.utcnow().isoformat() + 'Z'
    save_json_atomic(session_path, session)

    if needs_garden_regen:
        print('  Triggering garden.md regeneration...')
        regenerate_garden_md()

    return count


def process_session(session_path: pathlib.Path) -> int:
    """Process one session file. Returns count of facts extracted."""
    session_id = session_path.stem
    print(f'Processing session: {session_id}')

    turns, photo_attachments = parse_session(session_path)
    if not turns:
        print(f'  No message turns found — skipping')
        return 0

    transcript = build_transcript(turns)
    if len(transcript.strip()) < 20:
        print(f'  Transcript too short — skipping')
        return 0

    extracts = extract_facts(transcript)
    if not extracts:
        print(f'  No facts extracted')
        return 0

    print(f'  Extracted {len(extracts)} facts')
    conf = load_json(CONF_FILE)
    high_threshold = conf.get('thresholds', {}).get('high', {}).get('min', 0.80)
    medium_min = conf.get('thresholds', {}).get('medium', {}).get('min', 0.50)

    needs_garden_regen = False
    count = 0
    for extract in extracts:
        confidence = extract.get('confidence', 0)
        if confidence < medium_min:
            print(f'  SKIP (low confidence {confidence:.2f}): {extract.get("content","")[:60]}')
            continue

        event_path = write_event_file(extract, session_id)
        log_write(event_path, extract)

        # Store embedding in pgvector
        embedding = get_embedding(extract['content'])
        store_in_pgvector(
            event_path.stem.split('_')[-1],  # last 8 chars of uuid
            extract['content'],
            extract,
            embedding
        )

        if extract['category'] in ('BED_FACT', 'DECISION'):
            needs_garden_regen = True

        status = 'saved' if confidence >= high_threshold else 'pending_confirmation'
        print(f'  [{status}] {extract["category"]}: {extract.get("content","")[:70]}')
        count += 1

    if needs_garden_regen:
        print('  Triggering garden.md regeneration...')
        regenerate_garden_md()

    # Check raw session for onboarding bed markers
    try:
        raw = session_path.read_text(encoding='utf-8')
        if '[ONBOARDING_BED:' in raw:
            print('  Onboarding bed marker detected — triggering onboarding_writer...')
            import subprocess as _subprocess
            _subprocess.Popen(
                [sys.executable,
                 str(SLARTI_ROOT / 'scripts' / 'onboarding_writer.py'),
                 '--session', str(session_path)],
                stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL,
            )
    except Exception as _e:
        print(f'WARNING: Onboarding marker check failed: {_e}', file=sys.stderr)

    # Process any photo attachments found in this session
    if photo_attachments:
        print(f'  Found {len(photo_attachments)} photo attachment(s) — processing...')
        process_photos(photo_attachments, session_id)

    return count


def main():
    parser = argparse.ArgumentParser(description='Slarti extraction agent')
    parser.add_argument('--session', help='Process a specific session ID')
    parser.add_argument('--voice-session', metavar='PATH', help='Process a voice session JSON file')
    parser.add_argument('--regen-garden', action='store_true', help='Regenerate garden.md only')
    parser.add_argument('--all', action='store_true', help='Reprocess all sessions (including already-processed)')
    args = parser.parse_args()

    if args.regen_garden:
        print('Regenerating garden.md...')
        regenerate_garden_md()
        return

    if args.voice_session:
        session_path = pathlib.Path(args.voice_session)
        if not session_path.exists():
            print(f'ERROR: Voice session file not found: {session_path}', file=sys.stderr)
            sys.exit(1)
        count = process_voice_session(session_path)
        print(f'Done. {count} facts extracted from voice session.')
        return

    if args.session:
        session_path = SESSIONS_DIR / f'{args.session}.jsonl'
        if not session_path.exists():
            print(f'ERROR: Session not found: {session_path}', file=sys.stderr)
            sys.exit(1)
        process_session(session_path)
        mark_session_processed(args.session)
        return

    # Process all unprocessed sessions
    processed = load_processed_sessions() if not args.all else set()
    session_files = sorted(SESSIONS_DIR.glob('*.jsonl'))
    new_count = 0
    for sf in session_files:
        if sf.stem in processed:
            continue
        count = process_session(sf)
        mark_session_processed(sf.stem)
        new_count += count

    print(f'\nDone. {new_count} total facts extracted across {len(session_files)} sessions.')


if __name__ == '__main__':
    main()
