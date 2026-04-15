#!/usr/bin/env python3
"""
image_watcher.py — Persistent daemon that watches for image generation markers

Polls OpenClaw session files every 3 seconds for [GENERATE_IMAGE: caption]
markers (and legacy [DESIGN_REQUEST:] / [MOCKUP_REQUEST:] markers). When found,
uses Claude Haiku to expand the caption + conversation context into a rich
image prompt, then spawns image_agent.py to generate and post to Discord.

Typical latency: ~3s detection + ~15s generation = image in Discord in ~20s.

Usage:
  python scripts/image_watcher.py           # run as daemon (foreground)
  python scripts/image_watcher.py --once    # single check, then exit
  python scripts/image_watcher.py --dry-run # single check, no spawning
"""

import sys
import os
import re
import json
import time
import pathlib
import subprocess
import datetime

from dotenv import load_dotenv

SCRIPT_DIR = pathlib.Path(__file__).parent
SLARTI_ROOT = SCRIPT_DIR.parent
load_dotenv(dotenv_path=SLARTI_ROOT / '.env')

# Session directory — try WSL2 path, then Windows path
SESSIONS_DIR = pathlib.Path(os.path.expanduser('~/.openclaw/agents/slarti/sessions'))
if not SESSIONS_DIR.exists():
    SESSIONS_DIR = pathlib.Path('/mnt/c/Users/Chris/.openclaw/agents/slarti/sessions')
if not SESSIONS_DIR.exists():
    SESSIONS_DIR = pathlib.Path(r'C:\Users\Chris\.openclaw\agents\slarti\sessions')

STATE_FILE = SLARTI_ROOT / 'data' / 'system' / 'image_watcher_state.json'
IMAGE_AGENT = SLARTI_ROOT / 'scripts' / 'image_agent.py'
APP_CONFIG = SLARTI_ROOT / 'config' / 'app_config.json'

POLL_INTERVAL = 3       # seconds between checks
SESSION_WINDOW = 600    # only check sessions modified in last 10 minutes


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


# ── Marker extraction ────────────────────────────────────────────────────────

def extract_generate_image_markers(raw: str) -> list[str]:
    """[GENERATE_IMAGE: caption] — primary format."""
    return [m.group(1).strip() for m in re.finditer(r'\[GENERATE_IMAGE:\s*(.+?)\]', raw) if m.group(1).strip()]


def extract_legacy_design(raw: str) -> list[str]:
    """[DESIGN_REQUEST: description=...] — legacy."""
    results = []
    for m in re.finditer(r'\[DESIGN_REQUEST:\s*description=(.+?)\]', raw, re.DOTALL):
        d = m.group(1).strip().strip('{}')
        if d:
            results.append(d)
    return results


def extract_legacy_mockup(raw: str) -> list[str]:
    """[MOCKUP_REQUEST: request=...] — legacy."""
    return [m.group(1).strip() for m in re.finditer(r'\[MOCKUP_REQUEST:\s*(?:photo=[^,]*,\s*)?request=([^,\]]+)', raw, re.DOTALL) if m.group(1).strip()]


# ── Context extraction ───────────────────────────────────────────────────────

def get_assistant_context(raw: str, marker_text: str) -> str:
    """Get the assistant's message text surrounding a marker (up to 2000 chars)."""
    pos = raw.find(marker_text)
    if pos < 0:
        return ''
    before = raw[max(0, pos - 3000):pos]
    texts = re.findall(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', before)
    if texts:
        return texts[-1][:2000].replace('\\n', '\n').replace('\\"', '"')
    return before[-1000:]


def build_rich_prompt(caption: str, context: str) -> str:
    """Use Claude Haiku to turn caption + conversation into a rich image prompt."""
    try:
        import anthropic
    except ImportError:
        return f'{caption}. Photorealistic garden design, warm natural light, Zone 6b Missouri backyard.'

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return caption

    try:
        with open(APP_CONFIG) as f:
            model = json.load(f).get('claude_model_haiku', 'claude-haiku-4-5-20251001')
    except Exception:
        model = 'claude-haiku-4-5-20251001'

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=400,
            messages=[{'role': 'user', 'content':
                f"Write a detailed AI image generation prompt (for Gemini/DALL-E) based on this garden design.\n\n"
                f"Caption: {caption}\n\nConversation context:\n{context[:1500]}\n\n"
                f"Be vivid about colors, materials, lighting. Photorealistic, warm, Zone 6b Missouri backyard. "
                f"150-250 words. Write ONLY the prompt, nothing else."}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f'  WARNING: Haiku prompt failed: {e}', file=sys.stderr)
        return f'{caption}. Photorealistic garden design concept, warm natural light, Zone 6b Missouri backyard.'


# ── Core logic ───────────────────────────────────────────────────────────────

def make_marker_id(session_id: str, text: str) -> str:
    return f'{session_id}:{text[:100]}'


def check_once(processed: set, dry_run: bool = False) -> int:
    """Scan recent sessions for markers. Returns count of new markers found."""
    cutoff = time.time() - SESSION_WINDOW
    found = 0

    for session_path in SESSIONS_DIR.glob('*.jsonl'):
        try:
            if session_path.stat().st_mtime < cutoff:
                continue
        except Exception:
            continue

        session_id = session_path.stem
        try:
            raw = session_path.read_text(encoding='utf-8')
        except Exception:
            continue

        # Collect all markers from all formats
        markers = []
        for caption in extract_generate_image_markers(raw):
            markers.append(('GENERATE_IMAGE', caption, f'[GENERATE_IMAGE: {caption}]'))
        for desc in extract_legacy_design(raw):
            markers.append(('DESIGN_REQUEST', desc, f'[DESIGN_REQUEST:'))
        for req in extract_legacy_mockup(raw):
            markers.append(('MOCKUP_REQUEST', req, f'[MOCKUP_REQUEST:'))

        for kind, text, marker_text in markers:
            marker_id = make_marker_id(session_id, text)
            if marker_id in processed:
                continue

            found += 1
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            print(f'[{ts}] {kind} in {session_id[:12]}...: {text[:80]}')

            if dry_run:
                print(f'  [dry-run] Would spawn image_agent.py')
                continue

            # Build rich prompt from caption + conversation context
            context = get_assistant_context(raw, marker_text)
            description = build_rich_prompt(text, context)
            print(f'  Prompt: {description[:100]}...')

            # Spawn image_agent.py
            subprocess.Popen(
                [sys.executable, str(IMAGE_AGENT),
                 '--mode', 'c',
                 '--description', description,
                 '--channel', 'garden-design'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            processed.add(marker_id)
            print(f'  Spawned image_agent.py')

    return found


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    dry_run = '--dry-run' in sys.argv
    once = '--once' in sys.argv or dry_run

    state = load_state()
    processed = set(state.get('processed_markers', []))

    if once:
        check_once(processed, dry_run=dry_run)
        state['processed_markers'] = list(processed)[-200:]
        state['last_run'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if not dry_run:
            save_state(state)
        return

    # Daemon mode — run forever
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] image_watcher daemon started (polling every {POLL_INTERVAL}s)')
    print(f'  Sessions dir: {SESSIONS_DIR}')
    print(f'  State file: {STATE_FILE}')

    try:
        while True:
            try:
                found = check_once(processed, dry_run=False)
                if found > 0:
                    # Save state after finding markers
                    state['processed_markers'] = list(processed)[-200:]
                    state['last_run'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    save_state(state)
            except Exception as e:
                print(f'  ERROR in check cycle: {e}', file=sys.stderr)

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print('\nimage_watcher stopped.')
        state['processed_markers'] = list(processed)[-200:]
        state['last_run'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save_state(state)


if __name__ == '__main__':
    main()
