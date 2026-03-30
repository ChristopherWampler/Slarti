#!/usr/bin/env python3
"""
onboarding_writer.py — Phase 9 onboarding bed writer for Slarti

Reads an OpenClaw session JSONL file, finds [ONBOARDING_BED: {...}] markers
emitted by Claude during !setup sessions, and writes bed JSON files to data/beds/.
Triggered automatically by extraction_agent.py, or run manually.

Usage:
  python3 scripts/onboarding_writer.py --session <path/to/session.jsonl>
  python3 scripts/onboarding_writer.py --session <path/to/session.jsonl> --dry-run
"""

import sys
import os
import re
import json
import datetime
import pathlib
import argparse
import subprocess
from urllib import request as urllib_request

from dotenv import load_dotenv

SCRIPT_DIR = pathlib.Path(__file__).parent
ROOT       = SCRIPT_DIR.parent
load_dotenv(dotenv_path=ROOT / '.env')

BEDS_DIR         = ROOT / 'data' / 'beds'
ONBOARDING_STATE = ROOT / 'data' / 'system' / 'onboarding_state.json'
EXTRACTION_AGENT = SCRIPT_DIR / 'extraction_agent.py'


def save_json_atomic(path: pathlib.Path, data):
    """Atomic write: temp file + rename."""
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def next_bed_id() -> str:
    """Return the next available bed ID (bed-01, bed-02, etc.)."""
    existing = [p.stem for p in BEDS_DIR.glob('bed-*.json')]
    nums = []
    for name in existing:
        try:
            nums.append(int(name.split('-')[1]))
        except (IndexError, ValueError):
            pass
    n = max(nums, default=0) + 1
    return f'bed-{n:02d}'


def parse_dimensions(est: str) -> dict | None:
    """Parse 'NxM feet' or 'N by M' or 'N x M' into {length, width}."""
    if not est:
        return None
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:[xX×]|by)\s*(\d+(?:\.\d+)?)', est)
    if m:
        try:
            return {'length': float(m.group(1)), 'width': float(m.group(2))}
        except ValueError:
            pass
    return None


def extract_json_from_marker(text: str) -> list[dict]:
    """
    Find all [ONBOARDING_BED: {...}] markers in text using balanced brace counting.
    Handles both single-line and multi-line JSON inside the marker.
    """
    results = []
    search_tag = '[ONBOARDING_BED:'
    pos = 0
    while True:
        idx = text.find(search_tag, pos)
        if idx == -1:
            break
        # Find the opening brace
        brace_start = text.find('{', idx + len(search_tag))
        if brace_start == -1:
            pos = idx + 1
            continue
        # Count balanced braces to find the end
        depth = 0
        brace_end = brace_start
        in_string = False
        escape_next = False
        for i, ch in enumerate(text[brace_start:], start=brace_start):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    brace_end = i
                    break
        else:
            # Never closed
            pos = idx + 1
            continue

        json_str = text[brace_start:brace_end + 1]
        try:
            data = json.loads(json_str)
            results.append(data)
        except json.JSONDecodeError as e:
            print(f'WARNING: Could not parse ONBOARDING_BED marker JSON: {e}', file=sys.stderr)
        pos = brace_end + 1
    return results


def find_onboarding_markers(session_path: pathlib.Path) -> list[dict]:
    """Parse session JSONL and extract all [ONBOARDING_BED: {...}] markers."""
    markers = []
    try:
        with open(session_path, encoding='utf-8') as f:
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
                if msg.get('role') != 'assistant':
                    continue
                for block in msg.get('content', []):
                    if isinstance(block, dict) and block.get('type') == 'text':
                        text = block.get('text', '')
                        if '[ONBOARDING_BED:' in text:
                            found = extract_json_from_marker(text)
                            markers.extend(found)
    except Exception as e:
        print(f'ERROR: Could not read session file: {e}', file=sys.stderr)
    return markers


def build_bed_entity(marker: dict, bed_id: str) -> dict:
    """Build a complete bed entity JSON from an onboarding marker payload."""
    now = datetime.datetime.utcnow().isoformat() + 'Z'
    name    = marker.get('name', 'Unnamed Bed')
    plants  = marker.get('current_plants', [])
    status  = marker.get('status') or ('planted' if plants else 'planning')
    dim_est = marker.get('dimensions_estimate', '')
    dims    = parse_dimensions(dim_est)
    author  = marker.get('author', 'emily')
    issues  = marker.get('known_issues', '') or ''

    initial_fact = {
        'id':         'fact-001',
        'text':       f'Bed documented during onboarding on {now[:10]}',
        'author':     author,
        'source':     'user-confirmed',
        'confidence': 1.0,
    }
    if issues:
        initial_fact['notes'] = issues

    return {
        'schema_version':    '5.2',
        'entity_type':       'bed',
        'bed_id':            bed_id,
        'name':              name,
        'aliases':           marker.get('aliases', []),
        'status':            status,
        'status_confidence': 0.9,
        'attention_level':   'medium',
        'sun_exposure':      marker.get('sun_exposure', ''),
        'dimensions_ft':     dims,
        'dimensions_estimate': dim_est if dims is None else None,
        'photo_angle_notes': marker.get('photo_angle_notes', ''),
        'design_intent':     '',
        'current_plants':    plants,
        'current_summary':   (
            f"{name} — {', '.join(plants)}." if plants
            else f"{name} — not yet planted."
        ),
        'facts':               [initial_fact],
        'observations':        [],
        'inferences':          [],
        'recommendations':     [],
        'linked_project_ids':  [],
        'linked_photo_ids':    [],
        'last_reviewed_at':    now,
        'last_updated_at':     now,
    }


def get_discord_channel_id(channel_name: str) -> str | None:
    guild_id  = os.environ.get('DISCORD_GUILD_ID')
    bot_token = os.environ.get('DISCORD_BOT_TOKEN')
    if not guild_id or not bot_token:
        return None
    url = f'https://discord.com/api/v10/guilds/{guild_id}/channels'
    req = urllib_request.Request(
        url, headers={'Authorization': f'Bot {bot_token}', 'User-Agent': 'Slarti/1.0'}
    )
    try:
        with urllib_request.urlopen(req, timeout=10) as resp:
            channels = json.loads(resp.read().decode('utf-8'))
        for ch in channels:
            if ch.get('name') == channel_name:
                return ch['id']
    except Exception:
        pass
    return None


def post_to_discord(channel_name: str, message: str):
    bot_token = os.environ.get('DISCORD_BOT_TOKEN')
    if not bot_token:
        return
    channel_id = get_discord_channel_id(channel_name)
    if not channel_id:
        return
    url     = f'https://discord.com/api/v10/channels/{channel_id}/messages'
    payload = json.dumps({'content': message}).encode('utf-8')
    req     = urllib_request.Request(
        url, data=payload,
        headers={
            'Authorization': f'Bot {bot_token}',
            'Content-Type':  'application/json',
            'User-Agent':    'Slarti/1.0',
        },
        method='POST'
    )
    try:
        with urllib_request.urlopen(req, timeout=10):
            pass
    except Exception:
        pass


def load_onboarding_state() -> dict:
    if ONBOARDING_STATE.exists():
        try:
            with open(ONBOARDING_STATE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        'schema_version':   '5.2',
        'status':           'not_started',
        'beds_completed':   [],
        'current_bed_draft': None,
        'last_updated_at':  None,
    }


def main():
    parser = argparse.ArgumentParser(description='Slarti onboarding bed writer')
    parser.add_argument('--session', required=True, metavar='PATH',
                        help='Path to OpenClaw session JSONL file')
    parser.add_argument('--dry-run', action='store_true',
                        help='Parse and print markers without writing any files')
    args = parser.parse_args()

    session_path = pathlib.Path(args.session)
    if not session_path.exists():
        print(f'ERROR: Session file not found: {session_path}', file=sys.stderr)
        sys.exit(1)

    print(f'Parsing onboarding markers: {session_path.name}')
    markers = find_onboarding_markers(session_path)

    if not markers:
        print('No [ONBOARDING_BED] markers found.')
        sys.exit(0)

    print(f'Found {len(markers)} bed marker(s).')

    if args.dry_run:
        for i, m in enumerate(markers, 1):
            bed_id = f'bed-{i:02d}'  # hypothetical IDs for dry run
            bed    = build_bed_entity(m, bed_id)
            print(f'\n--- Bed {i}: {m.get("name", "??")} ---')
            print(json.dumps(bed, indent=2))
        print('\n[dry-run] No files written.')
        sys.exit(0)

    BEDS_DIR.mkdir(parents=True, exist_ok=True)

    state       = load_onboarding_state()
    beds_written = []

    for marker in markers:
        bed_id   = next_bed_id()
        bed      = build_bed_entity(marker, bed_id)
        out_path = BEDS_DIR / f'{bed_id}.json'

        if out_path.exists():
            print(f'  SKIP: {bed_id}.json already exists — already processed')
            continue

        save_json_atomic(out_path, bed)
        print(f'  Written: {out_path}')
        beds_written.append(bed_id)

        state.setdefault('beds_completed', [])
        if bed_id not in state['beds_completed']:
            state['beds_completed'].append(bed_id)

        plant_list = ', '.join(bed['current_plants']) if bed['current_plants'] else 'nothing planted yet'
        post_to_discord(
            'garden-log',
            f"Bed on record — **{bed['name']}** ({bed_id}). {plant_list.capitalize()}."
        )

    if not beds_written:
        print('No new beds written (all already exist).')
        sys.exit(0)

    state['status']          = 'in_progress'
    state['last_updated_at'] = datetime.datetime.utcnow().isoformat() + 'Z'
    save_json_atomic(ONBOARDING_STATE, state)
    print(f'Onboarding state: {len(state["beds_completed"])} bed(s) on record.')

    # Regenerate garden.md
    try:
        result = subprocess.run(
            [sys.executable, str(EXTRACTION_AGENT), '--regen-garden'],
            timeout=90, capture_output=True, text=True,
        )
        if result.returncode == 0:
            print('garden.md regenerated.')
        else:
            print(f'WARNING: garden.md regen returned non-zero: {result.stderr[:200]}',
                  file=sys.stderr)
    except Exception as e:
        print(f'WARNING: garden.md regeneration failed: {e}', file=sys.stderr)


if __name__ == '__main__':
    main()
