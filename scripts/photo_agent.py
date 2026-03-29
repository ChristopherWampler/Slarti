#!/usr/bin/env python3
"""
photo_agent.py — Photo metadata processor for Slarti

Runs after a photo-containing session. Extracts EXIF metadata and writes
a photo metadata record to data/photos/metadata/.

Usage:
  python3 scripts/photo_agent.py --photo-url <url> --session-id <id> --author emily
  python3 scripts/photo_agent.py --photo-path <path> --session-id <id> --author christopher
  python3 scripts/photo_agent.py --photo-url <url> --session-id <id> --dry-run
"""

import sys
import os
import json
import argparse
import hashlib
import pathlib
import subprocess
import datetime
from urllib import request as urllib_request

from dotenv import load_dotenv

SCRIPT_DIR = pathlib.Path(__file__).parent
SLARTI_ROOT = SCRIPT_DIR.parent
load_dotenv(dotenv_path=SLARTI_ROOT / '.env')

PHOTOS_RAW_DIR      = SLARTI_ROOT / 'data' / 'photos' / 'raw'
PHOTOS_META_DIR     = SLARTI_ROOT / 'data' / 'photos' / 'metadata'
MARKITDOWN_SCRIPT   = SCRIPT_DIR / 'markitdown_ingest.py'


def atomic_write_json(path, data):
    tmp_path = str(path) + '.tmp'
    with open(tmp_path, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def download_photo(url: str, dest_dir: pathlib.Path, photo_id: str) -> pathlib.Path:
    """Download photo from URL to dest_dir. Returns local path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Guess extension from URL
    ext = '.jpg'
    url_lower = url.lower().split('?')[0]
    for candidate in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
        if url_lower.endswith(candidate):
            ext = candidate
            break

    dest = dest_dir / f'{photo_id}{ext}'
    req = urllib_request.Request(url, headers={'User-Agent': 'Slarti/1.0'})
    with urllib_request.urlopen(req, timeout=30) as resp:
        dest.write_bytes(resp.read())
    return dest


def extract_exif(photo_path: pathlib.Path) -> dict:
    """Run markitdown_ingest.py --exif and return parsed metadata."""
    try:
        result = subprocess.run(
            [sys.executable, str(MARKITDOWN_SCRIPT), '--exif', str(photo_path)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return {'raw_text': result.stdout.strip()}
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f'WARNING: EXIF extraction failed: {e}', file=sys.stderr)
    return {}


def parse_exif_timestamp(exif_data: dict) -> str | None:
    """Try to extract a capture timestamp from EXIF raw text."""
    raw = exif_data.get('raw_text', '')
    # Look for common EXIF date formats: "2026:03:29 17:45:00" or ISO variants
    import re
    patterns = [
        r'(\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2})',  # EXIF canonical
        r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})',   # ISO 8601
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})',   # ISO space-separated
    ]
    for pat in patterns:
        m = re.search(pat, raw)
        if m:
            ts = m.group(1).replace(':', '-', 2) if ':' in m.group(1)[:4] else m.group(1)
            return ts
    return None


def build_photo_id(session_id: str, photo_path: pathlib.Path) -> str:
    """Generate a stable photo ID from session + file hash."""
    date_str = datetime.datetime.now().strftime('%Y%m%d')
    hash_input = f'{session_id}-{photo_path.name}'.encode()
    short_hash = hashlib.sha1(hash_input).hexdigest()[:8]
    return f'photo-{date_str}-{short_hash}'


def main():
    parser = argparse.ArgumentParser(description='Slarti photo metadata processor')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--photo-url', metavar='URL', help='Discord attachment URL')
    source.add_argument('--photo-path', metavar='PATH', help='Local photo file path')
    parser.add_argument('--session-id', required=True, help='OpenClaw session ID')
    parser.add_argument('--author', default='unknown', help='emily / christopher / unknown')
    parser.add_argument('--mode', default='A', choices=['A', 'B', 'C', 'D'],
                        help='Photo interaction mode (default: A)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print output only — do not write files or download photos')
    args = parser.parse_args()

    now_iso = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat()

    # Resolve photo path
    if args.photo_path:
        photo_path = pathlib.Path(args.photo_path)
        if not photo_path.exists():
            print(f'ERROR: Photo not found: {photo_path}', file=sys.stderr)
            sys.exit(1)
        photo_id = build_photo_id(args.session_id, photo_path)
        storage_path = str(photo_path)
    else:
        # Generate ID from URL + session
        photo_id = build_photo_id(args.session_id, pathlib.Path(args.photo_url.split('/')[-1]))
        if args.dry_run:
            print(f'[dry-run] Would download photo from URL')
            photo_path = PHOTOS_RAW_DIR / f'{photo_id}.jpg'
            storage_path = str(photo_path)
        else:
            print(f'Downloading photo from URL...')
            try:
                photo_path = download_photo(args.photo_url, PHOTOS_RAW_DIR, photo_id)
                storage_path = str(photo_path)
                print(f'Downloaded to: {photo_path}')
            except Exception as e:
                print(f'ERROR: Download failed: {e}', file=sys.stderr)
                sys.exit(1)

    # Extract EXIF
    if not args.dry_run:
        print(f'Extracting EXIF from {photo_path.name}...')
        exif_data = extract_exif(photo_path)
        exif_timestamp = parse_exif_timestamp(exif_data)
        if exif_timestamp:
            print(f'EXIF timestamp: {exif_timestamp}')
        else:
            print('No EXIF timestamp found')
    else:
        print('[dry-run] Would extract EXIF')
        exif_timestamp = None

    # Build metadata record
    metadata = {
        'schema_version': '5.2',
        'entity_type': 'photo_metadata',
        'photo_id': photo_id,
        'session_id': args.session_id,
        'upload_timestamp': now_iso,
        'exif_timestamp': exif_timestamp,
        'source': 'discord_upload',
        'author': args.author,
        'mode_used': args.mode,
        'storage_path': storage_path,
    }

    # Write metadata
    PHOTOS_META_DIR.mkdir(parents=True, exist_ok=True)
    meta_path = PHOTOS_META_DIR / f'{photo_id}.json'

    if args.dry_run:
        print('[dry-run] Would write metadata:')
        print(json.dumps(metadata, indent=2))
    else:
        atomic_write_json(meta_path, metadata)
        print(f'Wrote metadata: {meta_path}')

    # Print photo_id to stdout so callers can reference it
    print(photo_id)


if __name__ == '__main__':
    main()
