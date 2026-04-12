#!/usr/bin/env python3
"""
setup_discord_channels.py — one-time Discord server finish-up for Slarti

Sets channel topics and posts a pinned guide message in #garden-chat.
Run once from WSL2 after the Discord server and bot are fully configured.

Usage:
  cd /mnt/c/Openclaw/slarti
  python3 scripts/setup_discord_channels.py

Requires DISCORD_BOT_TOKEN and DISCORD_GUILD_ID in .env
"""
import sys
import os
import json
from urllib import request as urllib_request
from urllib.error import HTTPError
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

DISCORD_API = 'https://discord.com/api/v10'

BOT_TOKEN  = os.environ.get('DISCORD_BOT_TOKEN', '')
GUILD_ID   = os.environ.get('DISCORD_GUILD_ID', '')

# ── Channel topics ────────────────────────────────────────────────────────────

CHANNEL_TOPICS = {
    'garden-chat': (
        'Talk to Slarti here. @mention for questions, updates, plant advice, '
        'or just to chat. Drop audio files for voice notes. '
        'Type !help to see all commands.'
    ),
    'garden-photos': (
        'Drop photos here. Photo only → Slarti analyzes it. '
        'Photo + "show me" / "what if" / "mockup" → Slarti generates a visual. '
        '"What is this?" → plant ID.'
    ),
    'garden-design': (
        'Text-only design sessions. Describe a new bed, planting scheme, or layout change '
        '— Slarti generates concept visuals and iterates until you approve. '
        'Approved designs become build summaries for Christopher.'
    ),
    'garden-log': (
        'Read-only. Slarti posts treatment reminders, follow-up prompts, '
        'and bed update notes here automatically.'
    ),
    'garden-builds': (
        'Build summaries from approved designs. Fabrication status, materials, '
        'task sequences. Christopher\'s workspace.'
    ),
    'plant-alerts': (
        'Frost and heat advisories for Zone 6b (Farmington MO). '
        'Automated — posts appear when NWS conditions warrant.'
    ),
    'weekly-summary': (
        "Slarti's Sunday 6 PM garden narrative — what happened this week, "
        "what's coming, what needs attention."
    ),
    'admin-log': (
        'System alerts only. API errors, provider fallbacks, git backup failures. '
        'Christopher only — restricted permissions.'
    ),
}

# ── Pinned guide for #garden-chat ─────────────────────────────────────────────

GUIDE_MESSAGE = """\
**Talking to Slarti**

@mention me anywhere in this server and I'll respond. A few things I'm good at:

**Chat & questions** — ask me anything garden-related: what to plant, when to water, what that bug is, whether it's too cold to transplant. I know your garden, your zone (6b), and your beds.

**Voice notes** — drop an audio file (m4a, mp3, wav) here and I'll transcribe it and remember anything useful you said.

**Photos** → go to #garden-photos
**Design ideas** → go to #garden-design

\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
**Commands** (start with !)

`!help` — show this command list
`!status` — system health check
`!projects` — open projects and current blockers
`!memory [name]` — everything I know about a bed, plant, or project
`!memory tasks` — open task list by assignee
`!memory garden` — full garden summary
`!timeline [name]` — the story of a bed or project over time
`!setup` — onboarding wizard (first-time garden setup)
`!confirm blueprint [project-id]` — lock in blueprint dimensions for a build
\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\
"""

# ── Discord API helpers ───────────────────────────────────────────────────────

class AccessDenied(Exception):
    pass


def api(method: str, path: str, body: dict | None = None):
    url = f'{DISCORD_API}{path}'
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib_request.Request(
        url,
        data=data,
        headers={
            'Authorization': f'Bot {BOT_TOKEN}',
            'Content-Type': 'application/json',
            'User-Agent': 'Slarti-Setup/1.0',
        },
        method=method,
    )
    try:
        with urllib_request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except HTTPError as e:
        raw = e.read().decode('utf-8', errors='replace')
        try:
            code = json.loads(raw).get('code')
        except Exception:
            code = None
        if code == 50001:  # Missing Access — channel is restricted, expected
            raise AccessDenied()
        print(f'  ERROR {e.code} on {method} {path}: {raw}', file=sys.stderr)
        return None


def get_guild_channels() -> list[dict]:
    return api('GET', f'/guilds/{GUILD_ID}/channels') or []


def set_channel_topic(channel_id: str, topic: str) -> str:
    """Returns 'ok', 'skip', or 'fail'."""
    try:
        result = api('PATCH', f'/channels/{channel_id}', {'topic': topic})
        return 'ok' if result is not None else 'fail'
    except AccessDenied:
        return 'skip'


def get_pinned_messages(channel_id: str) -> list[dict]:
    return api('GET', f'/channels/{channel_id}/pins') or []


def post_message(channel_id: str, content: str) -> dict | None:
    return api('POST', f'/channels/{channel_id}/messages', {'content': content})


def pin_message(channel_id: str, message_id: str) -> bool:
    result = api('PUT', f'/channels/{channel_id}/pins/{message_id}')
    return result is not None


def delete_message(channel_id: str, message_id: str) -> bool:
    result = api('DELETE', f'/channels/{channel_id}/messages/{message_id}')
    return result is not None


def get_bot_user() -> dict | None:
    return api('GET', '/users/@me')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        print('ERROR: DISCORD_BOT_TOKEN not set in .env', file=sys.stderr)
        sys.exit(1)
    if not GUILD_ID:
        print('ERROR: DISCORD_GUILD_ID not set in .env', file=sys.stderr)
        sys.exit(1)

    # Get bot user ID so we can identify our own pinned messages
    bot_user = get_bot_user()
    if not bot_user:
        print('ERROR: Could not authenticate with Discord. Check DISCORD_BOT_TOKEN.', file=sys.stderr)
        sys.exit(1)
    bot_id = bot_user['id']
    print(f'Authenticated as: {bot_user["username"]} (id={bot_id})')

    # Fetch all channels
    channels = get_guild_channels()
    if not channels:
        print('ERROR: No channels found or could not reach Discord API.', file=sys.stderr)
        sys.exit(1)

    # Build name → id map (text channels only, type 0)
    channel_map = {
        ch['name']: ch['id']
        for ch in channels
        if ch.get('type') == 0
    }
    print(f'Found {len(channel_map)} text channels: {", ".join(f"#{n}" for n in sorted(channel_map))}')
    print()

    # ── Step 1: Set channel topics ────────────────────────────────────────────
    print('Setting channel topics...')
    for name, topic in CHANNEL_TOPICS.items():
        if name not in channel_map:
            print(f'  SKIP  #{name} — channel not found in server')
            continue
        cid = channel_map[name]
        result = set_channel_topic(cid, topic)
        label = {'ok': 'OK', 'skip': 'SKIP (restricted — expected)', 'fail': 'FAILED'}[result]
        print(f'  {label}  #{name}')

    print()

    # ── Step 2: Post + pin guide in #garden-chat ──────────────────────────────
    if 'garden-chat' not in channel_map:
        print('SKIP  #garden-chat not found — cannot post guide.')
        return

    chat_id = channel_map['garden-chat']

    # Delete any previous pinned guide messages from this bot to avoid duplicates
    pinned = get_pinned_messages(chat_id)
    removed = 0
    for msg in pinned:
        if msg.get('author', {}).get('id') == bot_id:
            if delete_message(chat_id, msg['id']):
                removed += 1
    if removed:
        print(f'Removed {removed} previous pinned bot message(s) from #garden-chat')

    # Post the guide
    print('Posting guide message to #garden-chat...')
    msg = post_message(chat_id, GUIDE_MESSAGE)
    if not msg:
        print('ERROR: Failed to post guide message.', file=sys.stderr)
        return

    # Pin it
    ok = pin_message(chat_id, msg['id'])
    if ok:
        print(f'  Pinned guide message')
    else:
        msg_link = f'https://discord.com/channels/{GUILD_ID}/{chat_id}/{msg["id"]}'
        print(f'  Could not pin automatically (bot needs Manage Messages permission).')
        print(f'  Pin it manually: right-click this message in Discord → Pin Message')
        print(f'  Direct link: {msg_link}')

    print()
    print('Done. Verify in Discord:')
    print('  - Each channel should show its topic under the channel name')
    print('  - #garden-chat should have a pinned message (📌 icon at top right)')


if __name__ == '__main__':
    main()
