#!/usr/bin/env python3
"""
image_watcher.py — Fast marker watcher for image generation requests

Runs every 1 minute via Windows Task Scheduler. Scans recent OpenClaw session
files for [GENERATE_IMAGE: caption] markers and spawns image_agent.py.

The marker contains a short caption (~100 chars). The watcher extracts the full
assistant message surrounding the marker and builds a rich image prompt from it.

Also detects legacy [DESIGN_REQUEST:] and [MOCKUP_REQUEST:] markers for
backward compatibility.

Usage:
  python scripts/image_watcher.py           # normal run
  python scripts/image_watcher.py --dry-run # check only, don't spawn
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
# Windows native path fallback
if not SESSIONS_DIR.exists():
    SESSIONS_DIR = pathlib.Path(r'C:\Users\Chris\.openclaw\agents\slarti\sessions')

STATE_FILE = SLARTI_ROOT / 'data' / 'system' / 'image_watcher_state.json'
IMAGE_AGENT = SLARTI_ROOT / 'scripts' / 'image_agent.py'
APP_CONFIG = SLARTI_ROOT / 'config' / 'app_config.json'


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


def extract_generate_image_markers(raw_text: str) -> list[str]:
    """Extract caption from [GENERATE_IMAGE: caption] markers."""
    results = []
    for match in re.finditer(r'\[GENERATE_IMAGE:\s*(.+?)\]', raw_text):
        caption = match.group(1).strip()
        if caption:
            results.append(caption)
    return results


def extract_legacy_design_requests(raw_text: str) -> list[str]:
    """Legacy: [DESIGN_REQUEST: description=...] markers."""
    results = []
    for match in re.finditer(r'\[DESIGN_REQUEST:\s*description=(.+?)\]', raw_text, re.DOTALL):
        desc = match.group(1).strip().strip('{}')
        if desc:
            results.append(desc)
    return results


def extract_legacy_mockup_requests(raw_text: str) -> list[str]:
    """Legacy: [MOCKUP_REQUEST: request=...] markers."""
    results = []
    for match in re.finditer(
        r'\[MOCKUP_REQUEST:\s*(?:photo=[^,]*,\s*)?request=([^,\]]+)',
        raw_text, re.DOTALL
    ):
        req = match.group(1).strip()
        if req:
            results.append(req)
    return results


def get_assistant_context(raw_text: str, marker_text: str) -> str:
    """Extract the assistant's message text surrounding a marker.
    Returns up to 2000 chars of context before the marker."""
    pos = raw_text.find(marker_text)
    if pos < 0:
        return ''
    # Look backwards from the marker to find relevant context
    before = raw_text[max(0, pos - 3000):pos]
    # Extract text content from JSON blocks (assistant messages)
    texts = re.findall(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', before)
    if texts:
        # Get the last (most recent) assistant text block
        return texts[-1][:2000].replace('\\n', '\n').replace('\\"', '"')
    return before[-1000:]


def build_rich_prompt(caption: str, context: str) -> str:
    """Build a detailed image generation prompt from the short caption
    plus the surrounding conversation context."""
    # Use Claude Haiku to turn conversation context + caption into an image prompt
    try:
        import anthropic
    except ImportError:
        # Fallback: use caption + context directly
        return f'{caption}. {context[:500]}' if context else caption

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return caption

    try:
        with open(APP_CONFIG) as f:
            config = json.load(f)
        model = config.get('claude_model_haiku', 'claude-haiku-4-5-20251001')
    except Exception:
        model = 'claude-haiku-4-5-20251001'

    prompt = f"""You are writing a prompt for an AI image generator (Google Gemini or DALL-E 3).

Based on this garden design conversation and the image request caption, write a detailed,
specific image generation prompt. Be vivid about colors, materials, lighting, and aesthetic.
The image should be photorealistic and warm. Include "Zone 6b Missouri backyard" context.

Caption: {caption}

Design conversation context:
{context[:1500]}

Write ONLY the image prompt, nothing else. 150-250 words, photorealistic style."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=400,
            messages=[{'role': 'user', 'content': prompt}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f'WARNING: Haiku prompt generation failed: {e}', file=sys.stderr)
        # Fallback: combine caption + context
        return f'{caption}. Photorealistic garden design concept, warm natural light, Zone 6b Missouri.'


def make_marker_id(session_id: str, text: str) -> str:
    return f'{session_id}:{text[:100]}'


def main():
    dry_run = '--dry-run' in sys.argv

    state = load_state()
    processed = set(state.get('processed_markers', []))

    # Only check sessions modified in the last 10 minutes
    cutoff = datetime.datetime.now().timestamp() - 600

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

        # Primary: [GENERATE_IMAGE: caption] markers
        for caption in extract_generate_image_markers(raw):
            marker_id = make_marker_id(session_id, caption)
            if marker_id in processed:
                continue

            found += 1
            context = get_assistant_context(raw, f'[GENERATE_IMAGE: {caption}]')
            description = build_rich_prompt(caption, context)

            print(f'GENERATE_IMAGE in {session_id}: {caption[:80]}')
            print(f'  Prompt: {description[:120]}...')

            if not dry_run:
                subprocess.Popen(
                    [sys.executable, str(IMAGE_AGENT),
                     '--mode', 'c',
                     '--description', description,
                     '--channel', 'garden-design'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                processed.add(marker_id)
            else:
                print(f'  [dry-run] Would spawn image_agent.py --mode c')

        # Legacy: [DESIGN_REQUEST: description=...] markers
        for desc in extract_legacy_design_requests(raw):
            marker_id = make_marker_id(session_id, desc)
            if marker_id in processed:
                continue

            found += 1
            print(f'DESIGN_REQUEST (legacy) in {session_id}: {desc[:80]}...')

            if not dry_run:
                subprocess.Popen(
                    [sys.executable, str(IMAGE_AGENT),
                     '--mode', 'c',
                     '--description', desc[:4000],
                     '--channel', 'garden-design'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                processed.add(marker_id)

        # Legacy: [MOCKUP_REQUEST: request=...] markers
        for req in extract_legacy_mockup_requests(raw):
            marker_id = make_marker_id(session_id, req)
            if marker_id in processed:
                continue

            found += 1
            print(f'MOCKUP_REQUEST (legacy) in {session_id}: {req[:80]}...')

            if not dry_run:
                subprocess.Popen(
                    [sys.executable, str(IMAGE_AGENT),
                     '--mode', 'c',
                     '--description', req,
                     '--channel', 'garden-design'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                processed.add(marker_id)

    if found > 0:
        print(f'Processed {found} marker(s)')

    # Save state (trim old entries — keep last 200)
    state['processed_markers'] = list(processed)[-200:]
    state['last_run'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if not dry_run:
        save_state(state)


if __name__ == '__main__':
    main()
