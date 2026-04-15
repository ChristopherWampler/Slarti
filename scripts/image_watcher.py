#!/usr/bin/env python3
"""
image_watcher.py — Fast marker watcher for image generation requests

Runs every 1 minute via WSL2 cron. Scans recent OpenClaw session files for
[DESIGN_REQUEST:] and [MOCKUP_REQUEST:] markers and spawns image_agent.py.

Much lighter than extraction_agent.py — only checks for image markers,
doesn't do fact extraction or embedding. Designed for fast turnaround
when a user asks for a design mockup.

Usage:
  python3 scripts/image_watcher.py           # normal run
  python3 scripts/image_watcher.py --dry-run # check only, don't spawn
"""

import sys
import os
import re
import json
import pathlib
import subprocess
import datetime

from dotenv import load_dotenv

SCRIPT_DIR = pathlib.Path(__file__).parent
SLARTI_ROOT = SCRIPT_DIR.parent
load_dotenv(dotenv_path=SLARTI_ROOT / '.env')

SESSIONS_DIR = pathlib.Path(os.path.expanduser('~/.openclaw/agents/slarti/sessions'))
if not SESSIONS_DIR.exists():
    SESSIONS_DIR = pathlib.Path('/mnt/c/Users/Chris/.openclaw/agents/slarti/sessions')

STATE_FILE = SLARTI_ROOT / 'data' / 'system' / 'image_watcher_state.json'
IMAGE_AGENT = SLARTI_ROOT / 'scripts' / 'image_agent.py'


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {'processed_markers': []}


def save_state(state: dict):
    tmp = str(STATE_FILE) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def extract_design_requests(text: str) -> list[str]:
    """Extract description from [DESIGN_REQUEST: description=...] markers."""
    results = []
    for match in re.finditer(r'\[DESIGN_REQUEST:\s*description=(.+?)\]', text, re.DOTALL):
        desc = match.group(1).strip()
        if desc:
            results.append(desc)
    return results


def extract_mockup_requests(text: str) -> list[str]:
    """Extract request text from [MOCKUP_REQUEST: request=...] markers."""
    results = []
    for match in re.finditer(
        r'\[MOCKUP_REQUEST:\s*(?:photo=[^,]*,\s*)?request=([^,\]]+)',
        text, re.DOTALL
    ):
        req = match.group(1).strip()
        if req:
            results.append(req)
    return results


def make_marker_id(session_id: str, description: str) -> str:
    """Create a dedup key from session + first 100 chars of description."""
    return f'{session_id}:{description[:100]}'


def main():
    dry_run = '--dry-run' in sys.argv

    state = load_state()
    processed = set(state.get('processed_markers', []))

    # Only check sessions modified in the last 10 minutes
    cutoff = datetime.datetime.now().timestamp() - 600

    found = 0
    for session_path in SESSIONS_DIR.glob('*.jsonl'):
        if session_path.stat().st_mtime < cutoff:
            continue

        session_id = session_path.stem
        try:
            raw = session_path.read_text(encoding='utf-8')
        except Exception:
            continue

        # Check for design request markers
        for desc in extract_design_requests(raw):
            marker_id = make_marker_id(session_id, desc)
            if marker_id in processed:
                continue

            found += 1
            print(f'DESIGN_REQUEST in {session_id}: {desc[:80]}...')

            if not dry_run:
                subprocess.Popen(
                    [sys.executable, str(IMAGE_AGENT),
                     '--mode', 'c',
                     '--description', desc,
                     '--channel', 'garden-design'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                processed.add(marker_id)
            else:
                print(f'  [dry-run] Would spawn image_agent.py --mode c')

        # Check for mockup request markers
        for req in extract_mockup_requests(raw):
            marker_id = make_marker_id(session_id, req)
            if marker_id in processed:
                continue

            found += 1
            print(f'MOCKUP_REQUEST in {session_id}: {req[:80]}...')

            if not dry_run:
                subprocess.Popen(
                    [sys.executable, str(IMAGE_AGENT),
                     '--mode', 'c',
                     '--description', req,
                     '--channel', 'garden-design'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                processed.add(marker_id)
            else:
                print(f'  [dry-run] Would spawn image_agent.py --mode c')

    if found == 0:
        pass  # Silent when nothing found (normal)
    else:
        print(f'Processed {found} marker(s)')

    # Save state (trim old entries — keep last 200)
    state['processed_markers'] = list(processed)[-200:]
    state['last_run'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if not dry_run:
        save_state(state)


if __name__ == '__main__':
    main()
