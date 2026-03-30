#!/usr/bin/env python3
"""
voice_session_writer.py — Mode V voice note handler for Slarti

Downloads or reads an audio file, transcribes it with MarkItDown, saves a voice
session JSON to data/voice_sessions/2026/, and posts a summary to Discord.

Usage:
  python3 scripts/voice_session_writer.py --audio /path/to/note.m4a --author emily
  python3 scripts/voice_session_writer.py --audio-url https://cdn.discordapp.com/... --author christopher
  python3 scripts/voice_session_writer.py --audio /path/to/note.m4a --author emily --dry-run
"""

import sys
import os
import json
import uuid
import argparse
import datetime
import pathlib
import subprocess
from urllib import request as urllib_request, error as urllib_error

from dotenv import load_dotenv

SCRIPT_DIR = pathlib.Path(__file__).parent
SLARTI_ROOT = SCRIPT_DIR.parent
load_dotenv(dotenv_path=SLARTI_ROOT / '.env')

VOICE_DIR     = SLARTI_ROOT / 'data' / 'voice_sessions' / '2026'
HEALTH_FILE   = SLARTI_ROOT / 'data' / 'system' / 'health_status.json'
MARKITDOWN_PY = SCRIPT_DIR / 'markitdown_ingest.py'
EXTRACT_PY    = SCRIPT_DIR / 'extraction_agent.py'

VOICE_DIR.mkdir(parents=True, exist_ok=True)


def atomic_write_json(path: pathlib.Path, data):
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def download_audio(url: str, dest: pathlib.Path) -> bool:
    """Download audio from a URL (Discord CDN) to a local path."""
    bot_token = os.environ.get('DISCORD_BOT_TOKEN', '')
    headers = {'User-Agent': 'Slarti/1.0'}
    if bot_token and 'discord' in url:
        headers['Authorization'] = f'Bot {bot_token}'
    try:
        req = urllib_request.Request(url, headers=headers)
        with urllib_request.urlopen(req, timeout=60) as resp:
            dest.write_bytes(resp.read())
        return True
    except Exception as e:
        print(f'ERROR: Could not download audio: {e}', file=sys.stderr)
        return False


def transcribe(audio_path: pathlib.Path) -> str | None:
    """Transcribe audio file using markitdown_ingest.py --audio."""
    try:
        result = subprocess.run(
            [sys.executable, str(MARKITDOWN_PY), '--audio', str(audio_path)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            print(f'ERROR: MarkItDown transcription failed: {result.stderr[:300]}', file=sys.stderr)
            return None
        transcript = result.stdout.strip()
        if not transcript:
            print('ERROR: Transcription returned empty text', file=sys.stderr)
            return None
        return transcript
    except subprocess.TimeoutExpired:
        print('ERROR: Transcription timed out (>120s)', file=sys.stderr)
        return None
    except Exception as e:
        print(f'ERROR: Transcription error: {e}', file=sys.stderr)
        return None


def get_discord_channel_id(channel_name: str) -> str | None:
    guild_id = os.environ.get('DISCORD_GUILD_ID')
    bot_token = os.environ.get('DISCORD_BOT_TOKEN')
    if not guild_id or not bot_token:
        return None
    url = f'https://discord.com/api/v10/guilds/{guild_id}/channels'
    req = urllib_request.Request(
        url,
        headers={'Authorization': f'Bot {bot_token}', 'User-Agent': 'Slarti/1.0'}
    )
    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            channels = json.loads(resp.read().decode('utf-8'))
        for ch in channels:
            if ch.get('name') == channel_name:
                return ch['id']
    except Exception as e:
        print(f'WARNING: Could not look up channel {channel_name}: {e}', file=sys.stderr)
    return None


def post_to_discord(channel_name: str, message: str):
    bot_token = os.environ.get('DISCORD_BOT_TOKEN')
    if not bot_token:
        return
    channel_id = get_discord_channel_id(channel_name)
    if not channel_id:
        print(f'WARNING: Could not find #{channel_name}', file=sys.stderr)
        return
    url = f'https://discord.com/api/v10/channels/{channel_id}/messages'
    body = json.dumps({'content': message}).encode('utf-8')
    req = urllib_request.Request(
        url, data=body,
        headers={
            'Authorization': f'Bot {bot_token}',
            'Content-Type': 'application/json',
            'User-Agent': 'Slarti/1.0'
        },
        method='POST'
    )
    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            if resp.status not in (200, 201):
                print(f'WARNING: Discord returned {resp.status}', file=sys.stderr)
    except Exception as e:
        print(f'WARNING: Discord post failed: {e}', file=sys.stderr)


def write_session(session_id: str, author: str, transcript: str, audio_filename: str) -> pathlib.Path:
    session = {
        'schema_version': '5.2',
        'entity_type': 'voice_session',
        'session_id': session_id,
        'author': author,
        'audio_filename': audio_filename,
        'raw_transcript': transcript,
        'extraction_status': 'pending',
        'extracted_events': [],
        'created_at': datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(),
    }
    path = VOICE_DIR / f'{session_id}.json'
    atomic_write_json(path, session)
    return path


def trigger_extraction(session_path: pathlib.Path):
    """Fire extraction_agent.py on the voice session transcript."""
    try:
        subprocess.Popen(
            [sys.executable, str(EXTRACT_PY), '--voice-session', str(session_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f'WARNING: Could not trigger extraction: {e}', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description='Slarti voice session writer (Mode V)')
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument('--audio', metavar='PATH', help='Local audio file path')
    src.add_argument('--audio-url', metavar='URL', help='Discord CDN URL for the audio file')
    parser.add_argument('--author', required=True, choices=['emily', 'christopher'],
                        help='Who dropped the audio file')
    parser.add_argument('--channel', default='garden-log',
                        help='Discord channel to post summary (default: garden-log)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Transcribe only — do not save or post')
    args = parser.parse_args()

    session_id = f'session-{datetime.datetime.now().strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:6]}'
    tmp_audio = None

    # Resolve audio path
    if args.audio:
        audio_path = pathlib.Path(args.audio)
        if not audio_path.exists():
            print(f'ERROR: Audio file not found: {audio_path}', file=sys.stderr)
            sys.exit(1)
        audio_filename = audio_path.name
    else:
        # Download from URL to temp file
        ext = args.audio_url.rsplit('.', 1)[-1].split('?')[0] or 'm4a'
        tmp_audio = VOICE_DIR / f'tmp_{session_id}.{ext}'
        print(f'Downloading audio from Discord CDN...')
        if not download_audio(args.audio_url, tmp_audio):
            sys.exit(1)
        audio_path = tmp_audio
        audio_filename = f'{session_id}.{ext}'

    print(f'Transcribing {audio_path.name}...')
    transcript = transcribe(audio_path)

    # Clean up temp download
    if tmp_audio and tmp_audio.exists():
        tmp_audio.unlink()

    if not transcript:
        print('ERROR: Transcription failed — nothing saved', file=sys.stderr)
        sys.exit(1)

    word_count = len(transcript.split())
    print(f'Transcript: {word_count} words')

    if args.dry_run:
        print('\n[dry-run] Transcript preview:')
        print(transcript[:500] + ('...' if len(transcript) > 500 else ''))
        print(f'\n[dry-run] Would save to: {VOICE_DIR}/{session_id}.json')
        print(f'[dry-run] Would post to #{args.channel}')
        return

    session_path = write_session(session_id, args.author, transcript, audio_filename)
    print(f'Saved: {session_path}')

    # Post to Discord
    author_display = args.author.capitalize()
    post_to_discord(
        args.channel,
        f"Voice note from {author_display} received — transcribed and saved. "
        f"({word_count} words)"
    )
    print(f'Posted to #{args.channel}')

    # Trigger extraction in background
    trigger_extraction(session_path)
    print('Extraction queued')


if __name__ == '__main__':
    main()
