#!/usr/bin/env python3
"""
weekly_summary_agent.py — Agent 4: Sunday evening garden summary for Slarti

Runs every Sunday at 6 PM via WSL2 cron. Reads the week's events, weather,
beds, and tasks, then posts a warm narrative summary to #garden-log.

Usage:
  python3 scripts/weekly_summary_agent.py              # normal run (Sunday only)
  python3 scripts/weekly_summary_agent.py --dry-run    # print prompt, no API call or post
  python3 scripts/weekly_summary_agent.py --force      # run regardless of day
  python3 scripts/weekly_summary_agent.py --force --dry-run
  python3 scripts/weekly_summary_agent.py --date 2026-03-24  # run for a specific week-end
"""

import sys
import os
import json
import time
import argparse
import pathlib
import datetime
from urllib import request as urllib_request, error as urllib_error

from dotenv import load_dotenv

SCRIPT_DIR  = pathlib.Path(__file__).parent
SLARTI_ROOT = SCRIPT_DIR.parent
load_dotenv(dotenv_path=SLARTI_ROOT / '.env')

APP_CONFIG_PATH     = SLARTI_ROOT / 'config' / 'app_config.json'
HEALTH_STATUS_PATH  = SLARTI_ROOT / 'data' / 'system' / 'health_status.json'
WEATHER_WEEK_PATH   = SLARTI_ROOT / 'data' / 'system' / 'weather_week.json'
EVENTS_DIR          = SLARTI_ROOT / 'data' / 'events' / '2026'
BEDS_DIR            = SLARTI_ROOT / 'data' / 'beds'
PROJECTS_DIR        = SLARTI_ROOT / 'data' / 'projects'
TASKS_DIR           = SLARTI_ROOT / 'data' / 'tasks'
SOUL_PATH           = SLARTI_ROOT / 'SOUL.md'
WEEKLY_MODE_PATH    = SLARTI_ROOT / 'prompts' / 'system' / 'weekly_summary_mode.md'


def load_app_config() -> dict:
    with open(APP_CONFIG_PATH) as f:
        return json.load(f)


def load_json_safe(path: pathlib.Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def atomic_write_json(path: pathlib.Path, data):
    tmp = str(path) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def get_week_cutoff(as_of_date: datetime.date) -> datetime.datetime:
    """Return datetime 7 days before the given date at 00:00 UTC."""
    cutoff = datetime.datetime.combine(as_of_date, datetime.time.min) - datetime.timedelta(days=7)
    return cutoff.replace(tzinfo=datetime.timezone.utc)


def load_events_this_week(cutoff: datetime.datetime) -> list[dict]:
    """Load timeline events from the past 7 days."""
    events = []
    if not EVENTS_DIR.exists():
        return events
    for path in sorted(EVENTS_DIR.glob('*.json')):
        data = load_json_safe(path)
        if not data:
            continue
        # Filter by created_at
        created_str = data.get('created_at', '')
        try:
            # Handle both UTC 'Z' suffix and offset-aware formats
            if created_str.endswith('Z'):
                created_str = created_str[:-1] + '+00:00'
            created_at = datetime.datetime.fromisoformat(created_str)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=datetime.timezone.utc)
            if created_at >= cutoff:
                events.append(data)
        except Exception:
            pass
    return events


def load_beds() -> list[dict]:
    beds = []
    if not BEDS_DIR.exists():
        return beds
    for path in sorted(BEDS_DIR.glob('*.json')):
        data = load_json_safe(path)
        if data:
            beds.append(data)
    return beds


def load_open_tasks(cutoff: datetime.datetime) -> list[dict]:
    """Load tasks created or updated this week that are open or in-progress."""
    tasks = []
    if not TASKS_DIR.exists():
        return tasks
    for path in sorted(TASKS_DIR.glob('*.json')):
        data = load_json_safe(path)
        if not data:
            continue
        if data.get('status') not in ('open', 'in_progress'):
            continue
        tasks.append(data)
    return tasks


def load_treatment_followups() -> list[dict]:
    """Load treatment events that still need follow-up."""
    followups = []
    if not EVENTS_DIR.exists():
        return followups
    for path in sorted(EVENTS_DIR.glob('*.json')):
        data = load_json_safe(path)
        if not data:
            continue
        if data.get('category') == 'TREATMENT' and data.get('follow_up_required') and not data.get('follow_up_resolved'):
            followups.append(data)
    return followups


def build_prompt(soul: str, mode_instructions: str, events: list[dict],
                 beds: list[dict], tasks: list[dict], followups: list[dict],
                 weather_week: dict | None, week_end_date: datetime.date) -> str:
    week_start = week_end_date - datetime.timedelta(days=6)
    date_range = f'{week_start.strftime("%B %d")} – {week_end_date.strftime("%B %d, %Y")}'

    sections = [
        soul,
        '',
        mode_instructions,
        '',
        f'## Week: {date_range}',
        '',
    ]

    # Weather
    if weather_week:
        sections.append('## Weather This Week')
        days = weather_week.get('days', [])
        if days:
            for day in days:
                hi = day.get('temp_high_f', '?')
                lo = day.get('temp_low_f', '?')
                hi_feels = day.get('heat_index_max_f')
                precip = day.get('precip_chance_pct', 0)
                line = f'  {day.get("date","")}: {lo}°–{hi}°F'
                if hi_feels and hi_feels > hi:
                    line += f' (feels like {hi_feels}°)'
                if precip >= 40:
                    line += f', {precip}% rain'
                sections.append(line)
        sections.append('')

    # Events this week
    sections.append(f'## Events This Week ({len(events)} total)')
    if events:
        for ev in events[:40]:  # cap at 40 to avoid token overrun
            cat = ev.get('category', 'OBSERVATION')
            author = ev.get('author', 'unknown')
            subject = ev.get('subject_id', 'garden')
            content = ev.get('content', '')
            date = ev.get('created_at', '')[:10]
            sections.append(f'  [{date}] {cat} ({author}, {subject}): {content}')
    else:
        sections.append('  No events recorded this week.')
    sections.append('')

    # Open tasks
    sections.append(f'## Open Tasks ({len(tasks)})')
    if tasks:
        for t in tasks[:20]:
            assignee = t.get('assignee', 'unassigned')
            title = t.get('title', t.get('content', ''))
            sections.append(f'  - [{assignee}] {title}')
    else:
        sections.append('  None.')
    sections.append('')

    # Treatment follow-ups
    if followups:
        sections.append(f'## Treatments Needing Follow-Up ({len(followups)})')
        for fu in followups[:10]:
            subject = fu.get('subject_id', 'garden')
            content = fu.get('content', '')
            date = fu.get('created_at', '')[:10]
            sections.append(f'  [{date}] {subject}: {content}')
        sections.append('')

    # Beds summary (brief)
    if beds:
        sections.append(f'## Current Beds ({len(beds)})')
        for bed in beds:
            name = bed.get('name', bed.get('bed_id', ''))
            plants = ', '.join(bed.get('current_plants', []) or [])
            status = bed.get('status', '')
            line = f'  {name}'
            if plants:
                line += f': {plants}'
            if status:
                line += f' ({status})'
            sections.append(line)
        sections.append('')

    sections.append('---')
    sections.append('Now write the weekly summary in Slarti\'s voice. Follow the mode instructions above exactly.')

    return '\n'.join(sections)


def call_claude(prompt: str, model: str) -> str | None:
    """Call Claude Sonnet for the summary narrative."""
    try:
        import anthropic
    except ImportError:
        print('ERROR: anthropic not installed', file=sys.stderr)
        return None

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print('ERROR: ANTHROPIC_API_KEY not set', file=sys.stderr)
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=1000,
            messages=[{'role': 'user', 'content': prompt}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f'ERROR: Claude call failed: {e}', file=sys.stderr)
        return None


def get_channel_id(channel_name: str) -> str | None:
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
        print(f'WARNING: Could not look up #{channel_name}: {e}', file=sys.stderr)
    return None


def post_to_discord(channel_name: str, message: str):
    bot_token = os.environ.get('DISCORD_BOT_TOKEN')
    if not bot_token:
        print('ERROR: DISCORD_BOT_TOKEN not set', file=sys.stderr)
        return
    channel_id = get_channel_id(channel_name)
    if not channel_id:
        print(f'ERROR: Could not find #{channel_name}', file=sys.stderr)
        return
    url = f'https://discord.com/api/v10/channels/{channel_id}/messages'

    # Discord messages cap at 2000 chars — split if needed
    chunks = [message[i:i+1900] for i in range(0, len(message), 1900)]
    for chunk in chunks:
        payload = json.dumps({'content': chunk}).encode('utf-8')
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
            with urllib_request.urlopen(req, timeout=15) as resp:
                if resp.status not in (200, 201):
                    print(f'WARNING: Discord returned {resp.status}', file=sys.stderr)
            if len(chunks) > 1:
                time.sleep(1)  # rate-limit courtesy pause between chunks
        except urllib_error.HTTPError as e:
            print(f'ERROR: Discord post failed: {e.code} {e.reason}', file=sys.stderr)


def update_health(config: dict, success: bool):
    health = {}
    if HEALTH_STATUS_PATH.exists():
        health = load_json_safe(HEALTH_STATUS_PATH) or {}
    now = datetime.datetime.utcnow().isoformat() + 'Z'
    if success:
        health['last_weekly_summary_at'] = now
        health['weekly_summary_failures_consecutive'] = 0
    else:
        failures = health.get('weekly_summary_failures_consecutive', 0) + 1
        health['weekly_summary_failures_consecutive'] = failures
    atomic_write_json(HEALTH_STATUS_PATH, health)


def main():
    parser = argparse.ArgumentParser(description='Slarti weekly summary agent (Agent 4)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Build prompt and print it — no Claude call, no Discord post')
    parser.add_argument('--force', action='store_true',
                        help='Run regardless of day of week (default: Sunday only)')
    parser.add_argument('--date', metavar='YYYY-MM-DD',
                        help='Run for a specific week-end date (default: today)')
    args = parser.parse_args()

    config = load_app_config()
    model = config.get('claude_model', 'claude-sonnet-4-6')

    # Determine the week-end date
    if args.date:
        try:
            week_end = datetime.date.fromisoformat(args.date)
        except ValueError:
            print(f'ERROR: Invalid date format: {args.date} (expected YYYY-MM-DD)', file=sys.stderr)
            sys.exit(1)
    else:
        week_end = datetime.date.today()

    # Check it's Sunday (weekday() == 6) unless --force
    if not args.force and week_end.weekday() != 6:
        day_name = week_end.strftime('%A')
        print(f'Today is {day_name} — weekly summary runs on Sundays. Use --force to override.')
        sys.exit(0)

    print(f'Weekly summary — week ending {week_end}')

    cutoff = get_week_cutoff(week_end)

    # Load data sources (skip missing gracefully)
    weather_week = load_json_safe(WEATHER_WEEK_PATH)
    if not weather_week:
        print('NOTE: weather_week.json not found — skipping weather section')

    events = load_events_this_week(cutoff)
    print(f'Found {len(events)} events this week')

    beds = load_beds()
    print(f'Found {len(beds)} bed(s)')

    tasks = load_open_tasks(cutoff)
    print(f'Found {len(tasks)} open/in-progress task(s)')

    followups = load_treatment_followups()
    if followups:
        print(f'Found {len(followups)} treatment follow-up(s)')

    # Load SOUL.md and weekly mode instructions
    soul = SOUL_PATH.read_text() if SOUL_PATH.exists() else '(SOUL.md not found)'
    mode_instructions = WEEKLY_MODE_PATH.read_text() if WEEKLY_MODE_PATH.exists() else ''

    prompt = build_prompt(soul, mode_instructions, events, beds, tasks, followups, weather_week, week_end)
    print(f'Prompt: {len(prompt)} chars')

    if args.dry_run:
        print('\n--- PROMPT PREVIEW (first 1000 chars) ---')
        print(prompt[:1000])
        print('...' if len(prompt) > 1000 else '')
        print('--- END PREVIEW ---')
        print(f'\n[dry-run] Would call {model}')
        print(f'[dry-run] Would post to #garden-log')
        return

    # Call Claude (retry once on failure)
    print(f'Calling {model}...')
    summary = call_claude(prompt, model)
    if summary is None:
        print('First attempt failed — retrying in 5 seconds...', file=sys.stderr)
        time.sleep(5)
        summary = call_claude(prompt, model)

    if summary is None:
        print('ERROR: Claude failed twice — posting fallback message', file=sys.stderr)
        post_to_discord('garden-log', "Slarti is taking the week off — summary coming next Sunday.")
        update_health(config, success=False)
        sys.exit(1)

    print(f'Summary: {len(summary)} chars')

    # Post to #garden-log
    post_to_discord('garden-log', summary)
    print('Posted to #garden-log')

    update_health(config, success=True)
    print('Health status updated')


if __name__ == '__main__':
    main()
