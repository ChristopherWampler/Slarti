#!/usr/bin/env python3
"""
voice_webhook.py — Mode P voice PWA server for Slarti (Phase 13)

FastAPI server on port 8080. Serves the PWA frontend and handles voice API calls:
  - GET  /           → pwa/index.html
  - POST /transcribe → audio file → OpenAI Whisper → transcribed text
  - POST /speak      → Claude text response + OpenAI TTS audio stream
  - POST /save-session → save voice session JSON + trigger extraction
  - GET  /health     → health check

TTS: OpenAI gpt-4o-mini-tts — pay-as-you-go, no subscription required.
Voice/model/instructions configurable in config/voice_profile.json.

Usage:
  python3 scripts/voice_webhook.py        # normal start (binds 0.0.0.0:8080)

Access from iPhone:
  http://[WINDOWS_IP]:8080?author=christopher
"""

import sys
import os
import json
import uuid
import pathlib
import datetime
import subprocess
from urllib import request as urllib_request

from dotenv import load_dotenv

SCRIPT_DIR = pathlib.Path(__file__).parent
ROOT       = SCRIPT_DIR.parent
load_dotenv(dotenv_path=ROOT / '.env')

# Paths
PWA_DIR    = ROOT / 'pwa'
VOICE_DIR  = ROOT / 'data' / 'voice_sessions'
EXTRACT_PY = SCRIPT_DIR / 'extraction_agent.py'

# Load SOUL.md from workspace root (canonical), fall back to prompts/system/
_soul_path = ROOT / 'SOUL.md'
if not _soul_path.exists():
    _soul_path = ROOT / 'prompts' / 'system' / 'SOUL.md'
SOUL = _soul_path.read_text() if _soul_path.exists() else ''

_voice_mode_path = ROOT / 'prompts' / 'system' / 'voice_session_mode.md'
VOICE_MODE = _voice_mode_path.read_text() if _voice_mode_path.exists() else ''

SYSTEM = SOUL + '\n\n' + VOICE_MODE

# Load config
_app_config  = json.loads((ROOT / 'config' / 'app_config.json').read_text())
CLAUDE_MODEL = _app_config.get('claude_model', 'claude-sonnet-4-6')
PWA_PORT     = _app_config.get('pwa_port', 8080)

try:
    from fastapi import FastAPI, Request, UploadFile, File
    from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
    from fastapi.middleware.cors import CORSMiddleware
except ImportError:
    print('ERROR: fastapi not installed. Run: pip install fastapi uvicorn --break-system-packages',
          file=sys.stderr)
    sys.exit(1)

try:
    import anthropic
except ImportError:
    print('ERROR: anthropic not installed. Run: pip install anthropic --break-system-packages',
          file=sys.stderr)
    sys.exit(1)

try:
    from openai import OpenAI as OpenAIClient
except ImportError:
    print('ERROR: openai not installed. Run: pip install openai --break-system-packages',
          file=sys.stderr)
    sys.exit(1)

app = FastAPI(title='Slarti Voice PWA', version='5.2')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

claude  = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', ''))
tts     = OpenAIClient(api_key=os.environ.get('OPENAI_API_KEY', ''))


def load_hot_context() -> str:
    """Load garden.md + weather_today.json + weather_week.json — injected on first turn of every session."""
    garden_path      = ROOT / 'docs' / 'garden.md'
    weather_path     = ROOT / 'data' / 'system' / 'weather_today.json'
    weather_week_path = ROOT / 'data' / 'system' / 'weather_week.json'
    garden       = garden_path.read_text() if garden_path.exists() else '(Garden summary not yet available)'
    weather      = weather_path.read_text() if weather_path.exists() else '{"summary": "Weather data not yet available."}'
    weather_week = weather_week_path.read_text() if weather_week_path.exists() else ''
    ctx = f'## Current Garden State\n{garden}\n\n## Weather Today\n{weather}'
    if weather_week:
        ctx += f'\n\n## Weather This Week\n{weather_week}'
    plant_db = load_plant_db()
    if plant_db:
        ctx += f'\n\n## Plant Database\n{plant_db}'
    return ctx


def load_plant_db() -> str:
    """Load all plant JSON files from data/plants/ into a compact string for context."""
    plants_dir = ROOT / 'data' / 'plants'
    if not plants_dir.exists():
        return ''
    plants = []
    for p in sorted(plants_dir.glob('*.json')):
        try:
            plants.append(json.loads(p.read_text()))
        except Exception:
            pass
    if not plants:
        return ''
    # Compact JSON — no need for pretty-printing in a context block
    return json.dumps(plants, separators=(',', ':'))



def load_voice_profile() -> dict:
    return json.loads((ROOT / 'config' / 'voice_profile.json').read_text())


def get_discord_channel_id(channel_name: str) -> str | None:
    guild_id  = os.environ.get('DISCORD_GUILD_ID')
    bot_token = os.environ.get('DISCORD_BOT_TOKEN')
    if not guild_id or not bot_token:
        return None
    url = f'https://discord.com/api/v10/guilds/{guild_id}/channels'
    req = urllib_request.Request(
        url,
        headers={'Authorization': f'Bot {bot_token}', 'User-Agent': 'Slarti/1.0'}
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
    url = f'https://discord.com/api/v10/channels/{channel_id}/messages'
    payload = json.dumps({'content': message}).encode('utf-8')
    req = urllib_request.Request(
        url, data=payload,
        headers={
            'Authorization': f'Bot {bot_token}',
            'Content-Type': 'application/json',
            'User-Agent': 'Slarti/1.0',
        },
        method='POST'
    )
    try:
        with urllib_request.urlopen(req, timeout=10) as resp:
            pass
    except Exception:
        pass


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get('/')
async def index():
    """Serve the PWA frontend."""
    html_path = PWA_DIR / 'index.html'
    if not html_path.exists():
        return JSONResponse({'error': 'pwa/index.html not found'}, status_code=404)
    return FileResponse(str(html_path), media_type='text/html')


@app.get('/health')
async def health():
    """Health check — confirm voice profile and model are loaded."""
    try:
        vp = load_voice_profile()
        return JSONResponse({
            'status': 'ok',
            'tts_provider': vp.get('provider', 'openai'),
            'tts_model': vp.get('model'),
            'tts_voice': vp.get('voice'),
            'claude_model': CLAUDE_MODEL,
            'port': PWA_PORT,
        })
    except Exception as e:
        return JSONResponse({'status': 'error', 'error': str(e)}, status_code=500)


@app.post('/transcribe')
async def transcribe(audio: UploadFile = File(...)):
    """
    Receive audio blob from PWA (MediaRecorder), transcribe via OpenAI Whisper.
    Returns: {"text": "transcribed speech"}
    """
    audio_bytes = await audio.read()
    if not audio_bytes:
        return JSONResponse({'error': 'Empty audio'}, status_code=400)

    # Reject very short clips — almost certainly noise, not speech
    if len(audio_bytes) < 8000:
        return JSONResponse({'text': ''})

    # Whisper needs a filename with a known extension to detect format.
    # language='en' prevents Whisper from hallucinating non-English text from noise.
    filename = audio.filename or 'audio.webm'
    try:
        result = tts.audio.transcriptions.create(
            model='whisper-1',
            file=(filename, audio_bytes, audio.content_type or 'audio/webm'),
            language='en',
        )
        return JSONResponse({'text': result.text})
    except Exception as e:
        return JSONResponse({'error': f'Whisper failed: {e}'}, status_code=502)


@app.post('/speak')
async def speak(request: Request):
    """
    Receive spoken text from the PWA, call Claude, stream ElevenLabs audio back.

    Body: { text: str, author: str, history: [{role, text}...] }
    Returns: audio/mpeg stream with X-Slarti-Response header containing the text
    """
    body      = await request.json()
    user_text = body.get('text', '').strip()
    author    = body.get('author', 'unknown')
    history   = body.get('history', [])

    if not user_text:
        return JSONResponse({'error': 'No text provided'}, status_code=400)

    # Build Claude message list
    # First turn: inject hot context as a leading user message
    messages = []
    if not history:
        hot = load_hot_context()
        messages.append({'role': 'user', 'content': hot})
        messages.append({'role': 'assistant', 'content': 'Got it — I have the garden context. What\'s on your mind?'})

    for turn in history:
        messages.append({'role': turn['role'], 'content': turn.get('text', '')})
    messages.append({'role': 'user', 'content': user_text})

    try:
        resp = claude.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            system=SYSTEM,
            messages=messages,
        )
        slarti_text = next((b.text for b in resp.content if hasattr(b, 'text')), '')
        if not slarti_text:
            return JSONResponse({'error': 'No text in response'}, status_code=502)
    except Exception as e:
        return JSONResponse({'error': f'Claude failed: {e}'}, status_code=502)

    # Stream OpenAI TTS audio
    vp = load_voice_profile()

    def generate_audio():
        try:
            with tts.audio.speech.with_streaming_response.create(
                model=vp.get('model', 'gpt-4o-mini-tts'),
                voice=vp.get('voice', 'onyx'),
                input=slarti_text,
                instructions=vp.get('instructions', ''),
                response_format=vp.get('response_format', 'mp3'),
            ) as response:
                for chunk in response.iter_bytes(chunk_size=4096):
                    if chunk:
                        yield chunk
        except Exception as e:
            print(f'ERROR: OpenAI TTS stream failed: {e}', file=sys.stderr)

    # HTTP headers: h11 strict validation requires only printable ASCII (0x20-0x7e)
    # or latin-1 extended (0x80-0xff). Whitelist approach — replace everything else.
    safe_text = ''.join(
        c if (0x20 <= ord(c) <= 0x7e or 0x80 <= ord(c) <= 0xff) else ' '
        for c in slarti_text[:500]
    ).strip()
    headers = {
        'X-Slarti-Response': safe_text,
        'X-Session-Author': author,
    }
    return StreamingResponse(generate_audio(), media_type='audio/mpeg', headers=headers)


@app.post('/save-session')
async def save_session(request: Request):
    """
    Save a completed voice session to data/voice_sessions/2026/ and trigger extraction.

    Body: { author: str, started_at: str, transcript: [{role, text, timestamp}...] }
    """
    body = await request.json()
    now  = datetime.datetime.utcnow()
    year = now.strftime('%Y')
    ts   = now.isoformat() + 'Z'
    sid  = f"vs-pwa-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"

    transcript = body.get('transcript', [])
    author     = body.get('author', 'unknown')

    # Build session JSON — same schema as voice_session_writer.py
    session = {
        'schema_version': '5.2',
        'entity_type': 'voice_session',
        'session_id': sid,
        'author': author,
        'channel': 'pwa_voice',
        'started_at': body.get('started_at', ts),
        'ended_at': ts,
        'transcript': transcript,
        # Build raw_transcript for extraction_agent.py compatibility
        'raw_transcript': '\n'.join(
            f"{'USER' if t.get('role') == 'user' else 'SLARTI'}: {t.get('text', '')}"
            for t in transcript
        ),
        'extraction_status': 'pending',
        'extracted_events': [],
        'discord_synced': False,
    }

    out_dir = VOICE_DIR / year
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{sid}.json'

    tmp = str(out_path) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(session, f, indent=2)
    os.replace(tmp, out_path)

    print(f'Session saved: {out_path}')

    # Trigger extraction in background
    try:
        subprocess.Popen(
            [sys.executable, str(EXTRACT_PY), '--voice-session', str(out_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f'WARNING: Could not trigger extraction: {e}', file=sys.stderr)

    # Post to Discord #garden-log
    turn_count = len([t for t in transcript if t.get('role') == 'user'])
    author_display = author.capitalize()
    post_to_discord(
        'garden-log',
        f"Voice session with {author_display} ended — {turn_count} exchange(s). Extracting notes now."
    )

    return JSONResponse({'ok': True, 'session_id': sid})


if __name__ == '__main__':
    import uvicorn
    ssl_cert = ROOT / 'config' / 'ssl' / 'cert.pem'
    ssl_key  = ROOT / 'config' / 'ssl' / 'key.pem'
    use_ssl  = ssl_cert.exists() and ssl_key.exists()
    scheme   = 'https' if use_ssl else 'http'
    print(f'Starting Slarti voice webhook on port {PWA_PORT} ({"HTTPS" if use_ssl else "HTTP"})...')
    print(f'PWA: {scheme}://localhost:{PWA_PORT}?author=christopher')
    print(f'Health: {scheme}://localhost:{PWA_PORT}/health')
    uvicorn.run(
        app, host='0.0.0.0', port=PWA_PORT, log_level='warning',
        ssl_certfile=str(ssl_cert) if use_ssl else None,
        ssl_keyfile=str(ssl_key) if use_ssl else None,
    )
