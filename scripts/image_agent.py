#!/usr/bin/env python3
"""
image_agent.py — Image generation for Slarti Modes B and C

Generates garden mockups and design concept images using Google Gemini image
generation, with DALL-E 3 as fallback. Posts results to Discord.

Usage:
  # Mode B — photo + change request (grounded mockup)
  python3 scripts/image_agent.py --mode b --photo data/photos/raw/photo.jpg \
      --request "add a cedar trellis on the north side" --channel garden-photos

  # Mode C — text design description (concept visual)
  python3 scripts/image_agent.py --mode c \
      --description "raised bed with tomatoes, basil, and marigolds" \
      --channel garden-design

  # Testing flags
  python3 scripts/image_agent.py --mode c --description "test" --dry-run
  python3 scripts/image_agent.py --mode c --description "test" --force-fallback --dry-run
"""

import sys
import os
import json
import uuid
import argparse
import pathlib
import datetime
from urllib import request as urllib_request, error as urllib_error

from dotenv import load_dotenv

SCRIPT_DIR = pathlib.Path(__file__).parent
SLARTI_ROOT = SCRIPT_DIR.parent
load_dotenv(dotenv_path=SLARTI_ROOT / '.env')

APP_CONFIG_PATH     = SLARTI_ROOT / 'config' / 'app_config.json'
PHOTOS_MOCKUPS_DIR  = SLARTI_ROOT / 'data' / 'photos' / 'mockups'
HEALTH_STATUS_PATH  = SLARTI_ROOT / 'data' / 'system' / 'health_status.json'

# Gemini image generation model — "Nano Banana 2"
# See: https://aistudio.google.com/models/gemini-3.1-flash-image-preview
GEMINI_IMAGE_MODEL = 'gemini-3.1-flash-image-preview'


def atomic_write_json(path, data):
    tmp_path = str(path) + '.tmp'
    with open(tmp_path, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def load_app_config():
    with open(APP_CONFIG_PATH) as f:
        return json.load(f)


def build_mode_b_prompt(photo_path: str, request: str, bed_context: str) -> str:
    return (
        "You are generating a garden design mockup for a family in Farmington, Missouri (USDA Zone 6b). "
        "Based on the provided garden photo, create a realistic before/after visualization that shows "
        f"the following change: {request}\n\n"
        f"{bed_context}\n\n"
        "Requirements:\n"
        "- Maintain the exact same viewpoint and lighting as the original photo\n"
        "- Make the change look realistic and achievable\n"
        "- Use plants and materials appropriate for Zone 6b Missouri\n"
        "- The result should look like a professional garden design rendering"
    )


def build_mode_c_prompt(description: str, zone_notes: str = '') -> str:
    return (
        "You are generating a garden design concept visual for a family in Farmington, Missouri (USDA Zone 6b). "
        f"Create a beautiful, realistic garden visualization based on this design description: {description}\n\n"
        "Requirements:\n"
        "- Photorealistic garden design, soft natural lighting\n"
        "- Plants should be appropriate for USDA Zone 6b Missouri climate\n"
        "- Show the design at peak summer growth\n"
        "- Warm, inviting atmosphere — this is a family garden, not a commercial space\n"
        f"{zone_notes}"
    )


def generate_with_gemini(prompt: str, photo_path: str | None = None) -> bytes | None:
    """Call Gemini image generation. Returns raw image bytes or None on failure."""
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        print('ERROR: google-genai not installed. Run: pip install google-genai --break-system-packages',
              file=sys.stderr)
        return None

    api_key = os.environ.get('GOOGLE_API_KEY')
    if not api_key:
        print('ERROR: GOOGLE_API_KEY not set in .env', file=sys.stderr)
        return None

    try:
        import base64
        client = genai.Client(api_key=api_key)
        config = genai_types.GenerateContentConfig(
            response_modalities=['TEXT', 'IMAGE']
        )

        if photo_path:
            # Mode B: send photo + prompt
            photo_bytes = pathlib.Path(photo_path).read_bytes()
            # Detect mime type from extension
            ext = pathlib.Path(photo_path).suffix.lower()
            mime_map = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                        '.png': 'image/png', '.webp': 'image/webp'}
            mime_type = mime_map.get(ext, 'image/jpeg')
            contents = [
                genai_types.Part.from_bytes(data=photo_bytes, mime_type=mime_type),
                prompt,
            ]
        else:
            # Mode C: text only
            contents = prompt

        response = client.models.generate_content(
            model=GEMINI_IMAGE_MODEL,
            contents=contents,
            config=config,
        )

        # Extract image bytes from response parts
        for part in response.candidates[0].content.parts:
            if hasattr(part, 'inline_data') and part.inline_data:
                raw = part.inline_data.data
                # inline_data.data may be base64 string or raw bytes
                if isinstance(raw, (bytes, bytearray)):
                    return bytes(raw)
                # Try base64 decode
                try:
                    return base64.b64decode(raw)
                except Exception:
                    return raw.encode('latin-1') if isinstance(raw, str) else bytes(raw)

        print('WARNING: Gemini response contained no image data', file=sys.stderr)
        return None

    except Exception as e:
        print(f'WARNING: Gemini image generation failed: {e}', file=sys.stderr)
        return None


def generate_with_dalle(prompt: str) -> bytes | None:
    """Call DALL-E 3 for image generation. Returns raw image bytes or None on failure."""
    try:
        from openai import OpenAI
    except ImportError:
        print('ERROR: openai not installed. Run: pip install openai --break-system-packages',
              file=sys.stderr)
        return None

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        print('ERROR: OPENAI_API_KEY not set in .env', file=sys.stderr)
        return None

    try:
        client = OpenAI(api_key=api_key)
        response = client.images.generate(
            model='dall-e-3',
            prompt=prompt[:4000],  # DALL-E 3 prompt limit
            n=1,
            size='1024x1024',
            response_format='url'
        )
        image_url = response.data[0].url
        # Download the image
        req = urllib_request.Request(image_url, headers={'User-Agent': 'Slarti/1.0'})
        with urllib_request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except Exception as e:
        print(f'ERROR: DALL-E 3 generation failed: {e}', file=sys.stderr)
        return None


def get_discord_channel_id(channel_name: str) -> str | None:
    """Look up a Discord channel ID by name."""
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


def post_image_to_discord(channel_name: str, image_bytes: bytes, filename: str, caption: str):
    """Post an image file to a Discord channel via the REST API."""
    bot_token = os.environ.get('DISCORD_BOT_TOKEN')
    if not bot_token:
        print('ERROR: DISCORD_BOT_TOKEN not set', file=sys.stderr)
        return

    channel_id = get_discord_channel_id(channel_name)
    if not channel_id:
        print(f'ERROR: Could not find channel: {channel_name}', file=sys.stderr)
        return

    url = f'https://discord.com/api/v10/channels/{channel_id}/messages'

    # Multipart form data for file upload
    boundary = 'slartiboundary1234'
    body = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="payload_json"\r\n'
        f'Content-Type: application/json\r\n\r\n'
        f'{json.dumps({"content": caption})}\r\n'
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="files[0]"; filename="{filename}"\r\n'
        f'Content-Type: image/png\r\n\r\n'
    ).encode('utf-8') + image_bytes + f'\r\n--{boundary}--\r\n'.encode('utf-8')

    req = urllib_request.Request(
        url,
        data=body,
        headers={
            'Authorization': f'Bot {bot_token}',
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'User-Agent': 'Slarti/1.0',
        },
        method='POST'
    )
    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            if resp.status not in (200, 201):
                print(f'WARNING: Discord returned {resp.status}', file=sys.stderr)
            else:
                print(f'Posted image to #{channel_name}')
    except urllib_error.HTTPError as e:
        print(f'ERROR: Discord post failed: {e.code} {e.reason}', file=sys.stderr)


def save_mockup(image_bytes: bytes, mockup_id: str, mode: str, provider: str,
                request: str, source_photo: str | None) -> pathlib.Path:
    """Save image bytes + metadata JSON to data/photos/mockups/."""
    PHOTOS_MOCKUPS_DIR.mkdir(parents=True, exist_ok=True)

    image_path = PHOTOS_MOCKUPS_DIR / f'{mockup_id}.png'
    image_path.write_bytes(image_bytes)

    metadata = {
        'schema_version': '5.2',
        'entity_type': 'mockup',
        'mockup_id': mockup_id,
        'mode': mode.upper(),
        'source_photo_id': source_photo,
        'request': request,
        'provider_used': provider,
        'status': 'proposed',
        'created_at': datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(),
        'image_path': str(image_path),
    }
    meta_path = PHOTOS_MOCKUPS_DIR / f'{mockup_id}.json'
    atomic_write_json(meta_path, metadata)
    return image_path


def main():
    parser = argparse.ArgumentParser(description='Slarti image generation agent')
    parser.add_argument('--mode', required=True, choices=['b', 'c', 'B', 'C'],
                        help='B = grounded mockup (photo + request), C = concept visual (text)')
    parser.add_argument('--photo', metavar='PATH',
                        help='Source photo path (Mode B only)')
    parser.add_argument('--request', metavar='TEXT',
                        help='What change to visualize (Mode B)')
    parser.add_argument('--description', metavar='TEXT',
                        help='Design description (Mode C)')
    parser.add_argument('--channel', default='garden-design',
                        help='Discord channel to post to (default: garden-design)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print prompt only — do not call image API or post to Discord')
    parser.add_argument('--force-fallback', action='store_true',
                        help='Skip Gemini, use DALL-E 3 directly (for testing fallback)')
    args = parser.parse_args()

    mode = args.mode.lower()

    # Validate args
    if mode == 'b' and not args.photo:
        print('ERROR: --photo is required for Mode B', file=sys.stderr)
        sys.exit(1)
    if mode == 'b' and not args.request:
        print('ERROR: --request is required for Mode B', file=sys.stderr)
        sys.exit(1)
    if mode == 'c' and not args.description:
        print('ERROR: --description is required for Mode C', file=sys.stderr)
        sys.exit(1)

    # Build prompt
    if mode == 'b':
        prompt = build_mode_b_prompt(args.photo, args.request, bed_context='Zone 6b, Farmington Missouri')
        source_label = pathlib.Path(args.photo).stem
    else:
        prompt = build_mode_c_prompt(args.description)
        source_label = None

    print(f'Mode {mode.upper()} — generating image...')
    print(f'Prompt ({len(prompt)} chars): {prompt[:200]}...' if len(prompt) > 200 else f'Prompt: {prompt}')

    if args.dry_run:
        print(f'\n[dry-run] Would call {"DALL-E 3 (forced fallback)" if args.force_fallback else f"Gemini {GEMINI_IMAGE_MODEL}"}')
        print(f'[dry-run] Would save mockup to data/photos/mockups/')
        print(f'[dry-run] Would post to #{args.channel}')
        return

    # Generate image
    image_bytes = None
    provider_used = None

    if not args.force_fallback:
        print(f'Calling Gemini {GEMINI_IMAGE_MODEL}...')
        image_bytes = generate_with_gemini(
            prompt,
            photo_path=args.photo if mode == 'b' else None
        )
        if image_bytes:
            provider_used = 'gemini'

    if image_bytes is None:
        if not args.force_fallback:
            print('Gemini failed — falling back to DALL-E 3...')
        else:
            print('Using DALL-E 3 (forced fallback)...')
        image_bytes = generate_with_dalle(prompt)
        if image_bytes:
            provider_used = 'dalle3'

    if image_bytes is None:
        print('ERROR: Both Gemini and DALL-E 3 failed to generate an image', file=sys.stderr)
        sys.exit(1)

    print(f'Generated {len(image_bytes):,} bytes via {provider_used}')

    # Save mockup
    mockup_id = f'mockup-{datetime.datetime.now().strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:6]}'
    request_text = args.request if mode == 'b' else args.description
    image_path = save_mockup(image_bytes, mockup_id, mode, provider_used, request_text, source_label)
    print(f'Saved: {image_path}')

    # Post to Discord
    caption = (
        f"Here's what that could look like. Does this capture your vision, "
        f"or should we adjust something?"
    )
    filename = f'{mockup_id}.png'
    post_image_to_discord(args.channel, image_bytes, filename, caption)


if __name__ == '__main__':
    main()
