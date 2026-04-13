#!/usr/bin/env python3
"""
heartbeat_agent.py — Agent 3: Slarti proactive heartbeat (every 30 min)

Runs the 8-check pipeline from HEARTBEAT.md. Most cycles produce no output.
Maximum 2 proactive posts per week, enforced via health_status.json.

Usage:
  python3 scripts/heartbeat_agent.py              # normal run
  python3 scripts/heartbeat_agent.py --dry-run    # check conditions, print only
  python3 scripts/heartbeat_agent.py --force      # ignore weekly limit + season
  python3 scripts/heartbeat_agent.py --check 2    # run only check #2
  python3 scripts/heartbeat_agent.py --check 6 --dry-run
"""
import sys
import os
import json
import argparse
import pathlib
import datetime
import subprocess
from urllib import request as urllib_request, error as urllib_error

from dotenv import load_dotenv

SCRIPT_DIR  = pathlib.Path(__file__).parent
SLARTI_ROOT = SCRIPT_DIR.parent
load_dotenv(dotenv_path=SLARTI_ROOT / '.env')

APP_CONFIG_PATH     = SLARTI_ROOT / 'config' / 'app_config.json'
HEALTH_STATUS_PATH  = SLARTI_ROOT / 'data' / 'system' / 'health_status.json'
WEATHER_TODAY_PATH  = SLARTI_ROOT / 'data' / 'system' / 'weather_today.json'
KNOWLEDGE_NEWS_PATH = SLARTI_ROOT / 'data' / 'system' / 'knowledge_news.json'
EVENTS_DIR          = SLARTI_ROOT / 'data' / 'events' / '2026'
BEDS_DIR            = SLARTI_ROOT / 'data' / 'beds'
PROJECTS_DIR        = SLARTI_ROOT / 'data' / 'projects'
TASKS_DIR           = SLARTI_ROOT / 'data' / 'tasks'
PLANTS_DIR          = SLARTI_ROOT / 'data' / 'plants'

sys.path.insert(0, str(SCRIPT_DIR))
import discord_alert


# ── Utility functions ─────────────────────────────────────────────────────────

def load_json_safe(path: pathlib.Path):
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


def load_app_config() -> dict:
    with open(APP_CONFIG_PATH) as f:
        return json.load(f)


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def parse_dt(s: str) -> datetime.datetime:
    """Parse ISO datetime string, handling both Z and offset formats."""
    if not s:
        raise ValueError('empty string')
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def is_growing_season(config: dict) -> bool:
    month = datetime.date.today().month
    return config.get('growing_season_start_month', 5) <= month <= config.get('growing_season_end_month', 10)


def load_all_json(directory: pathlib.Path) -> list[dict]:
    """Load all .json files from a directory."""
    results = []
    if not directory.exists():
        return results
    for path in sorted(directory.glob('*.json')):
        data = load_json_safe(path)
        if data and isinstance(data, dict):
            results.append(data)
    return results


# ── Discord posting ───────────────────────────────────────────────────────────

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
    payload = json.dumps({'content': message[:1900]}).encode('utf-8')
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
    except urllib_error.HTTPError as e:
        print(f'ERROR: Discord post failed: {e.code} {e.reason}', file=sys.stderr)


# ── Gating ────────────────────────────────────────────────────────────────────

def can_post(health: dict, subject_id: str, config: dict) -> tuple[bool, str]:
    """Check proactive post budget and dedup. Returns (allowed, reason)."""
    max_posts = config.get('max_proactive_posts_per_week', 2)

    # Determine current week (Sunday start)
    today = datetime.date.today()
    days_since_sunday = (today.weekday() + 1) % 7
    current_week_start = (today - datetime.timedelta(days=days_since_sunday)).isoformat()

    if health.get('proactive_posts_week_of') != current_week_start:
        health['proactive_posts_this_week'] = 0
        health['proactive_posts_week_of'] = current_week_start

    count = health.get('proactive_posts_this_week', 0)
    if count >= max_posts:
        return False, f'weekly limit reached ({count}/{max_posts})'

    # Dedup: don't repeat same subject in 24h
    last_subject = health.get('last_heartbeat_post_subject_id')
    last_post_at = health.get('last_heartbeat_post_at')
    if last_subject == subject_id and last_post_at:
        try:
            last_dt = parse_dt(last_post_at)
            if now_utc() - last_dt < datetime.timedelta(hours=24):
                return False, f'already posted about {subject_id} within 24h'
        except Exception:
            pass

    return True, 'ok'


def mark_posted(health: dict, subject_id: str):
    """Update health status after a successful proactive post."""
    health['proactive_posts_this_week'] = health.get('proactive_posts_this_week', 0) + 1
    health['last_heartbeat_post_at'] = now_utc().isoformat()
    health['last_heartbeat_post_subject_id'] = subject_id


# ── Friend test ───────────────────────────────────────────────────────────────

def friend_test(draft: str, subject_id: str, channel: str, config: dict) -> bool:
    """Call Claude Haiku to evaluate whether the message passes the friend test.
    Returns True if the message should be posted."""
    try:
        import anthropic
    except ImportError:
        return True  # If we can't test, default to posting

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return True

    model = config.get('claude_model_haiku', 'claude-haiku-4-5-20251001')
    today = datetime.date.today().isoformat()
    growing = is_growing_season(config)

    prompt = (
        "You are evaluating a proactive message from Slarti, a garden companion AI "
        "for a family in Farmington, Missouri (Zone 6b).\n\n"
        "Apply the friend test: Would a knowledgeable friend who knows this garden "
        "actually say this, right now, in this way? Is it timely and genuinely useful?\n\n"
        f"Proposed message: {draft}\n"
        f"Subject: {subject_id}\n"
        f"Channel: #{channel}\n"
        f"Date: {today}\n"
        f"Growing season: {'yes' if growing else 'no (planning season)'}\n\n"
        "Reply with ONLY 'yes' or 'no' followed by a one-sentence reason."
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=100,
            messages=[{'role': 'user', 'content': prompt}]
        )
        answer = response.content[0].text.strip().lower()
        passes = answer.startswith('yes')
        if not passes:
            print(f'  Friend test: {response.content[0].text.strip()}')
        return passes
    except Exception as e:
        print(f'WARNING: Friend test failed: {e}', file=sys.stderr)
        return True  # Default to posting if test fails


# ── Check functions ───────────────────────────────────────────────────────────
# Each returns None (nothing triggered) or a dict:
#   {'check': N, 'channel': str, 'subject_id': str, 'draft': str}

def check_1_weather(config: dict, health: dict) -> dict | None:
    """Has a weather advisory been posted today?"""
    weather = load_json_safe(WEATHER_TODAY_PATH)
    if not weather:
        return None

    today_str = datetime.date.today().isoformat()
    weather_date = weather.get('date', '')

    # If weather data is stale (not today), trigger the weather agent
    if weather_date != today_str:
        print('  Check 1: weather data is stale — triggering weather_agent.py')
        try:
            subprocess.run(
                [sys.executable, str(SLARTI_ROOT / 'scripts' / 'weather_agent.py')],
                capture_output=True, timeout=60
            )
        except Exception as e:
            print(f'  WARNING: weather_agent.py failed: {e}', file=sys.stderr)
        return None

    # Check if advisories exist and haven't been posted yet
    advisories = weather.get('advisories', [])
    if not advisories:
        return None

    # Check if an advisory was already posted today
    last_advisory_at = weather.get('advisory_posted_at', '')
    if last_advisory_at and last_advisory_at.startswith(today_str):
        return None

    advisory_text = weather.get('advisory_message', '')
    if not advisory_text:
        return None

    return {
        'check': 1,
        'channel': 'garden-log',
        'subject_id': 'weather',
        'draft': advisory_text,
    }


def check_2_treatment_followup(config: dict, health: dict) -> dict | None:
    """Treatment follow-up due within 48 hours."""
    events = load_all_json(EVENTS_DIR)
    now = now_utc()

    for event in events:
        if event.get('category') != 'TREATMENT':
            continue
        if not event.get('follow_up_required'):
            continue
        if event.get('follow_up_resolved'):
            continue

        # Determine follow-up date
        next_check = event.get('next_check_date')
        if next_check:
            try:
                check_dt = parse_dt(next_check)
            except Exception:
                continue
        else:
            # Default: 7 days after created_at
            try:
                created = parse_dt(event.get('created_at', ''))
                check_dt = created + datetime.timedelta(days=7)
            except Exception:
                continue

        # Is the follow-up within 48 hours?
        delta = check_dt - now
        if datetime.timedelta(hours=-24) <= delta <= datetime.timedelta(hours=48):
            subject = event.get('subject_id', 'garden')
            content = event.get('content', 'a treatment')
            days_ago = (now - parse_dt(event.get('created_at', now.isoformat()))).days

            draft = (
                f"It's been about {days_ago} days since {content.lower()} — "
                f"how's everything looking? Any change you've noticed?"
            )
            return {
                'check': 2,
                'channel': 'garden-chat',
                'subject_id': subject,
                'draft': draft,
            }

    return None


def check_3_fabrication_blocker(config: dict, health: dict) -> dict | None:
    """Fabrication blocker — project approved >7 days, parts incomplete."""
    projects = load_all_json(PROJECTS_DIR)
    now = now_utc()

    for project in projects:
        if project.get('status') != 'approved':
            continue
        approved_at = project.get('approved_at', '')
        if not approved_at:
            continue

        try:
            approved_dt = parse_dt(approved_at)
        except Exception:
            continue

        if (now - approved_dt).days < 7:
            continue

        # Check fabricated_parts
        parts = project.get('fabricated_parts', [])
        for part in parts:
            if part.get('qty_completed', 0) < part.get('qty_needed', 0):
                name = project.get('name', project.get('project_id', 'a project'))
                part_name = part.get('name', 'parts')
                draft = (
                    f"Hey Christopher — the {name} has been approved for about "
                    f"{(now - approved_dt).days} days now. Looks like {part_name} "
                    f"still needs finishing. Anything I can help with on the plan?"
                )
                return {
                    'check': 3,
                    'channel': 'garden-builds',
                    'subject_id': project.get('project_id', name),
                    'draft': draft,
                }

    return None


def check_4_unresolved_observation(config: dict, health: dict) -> dict | None:
    """Unresolved observation older than 14 days — growing season only."""
    if not is_growing_season(config):
        return None

    events = load_all_json(EVENTS_DIR)
    now = now_utc()

    # Group observations by subject and find unresolved ones
    observations = {}
    follow_ups = set()

    for event in events:
        cat = event.get('category', '')
        subject = event.get('subject_id', 'garden')

        if cat == 'OBSERVATION':
            try:
                created = parse_dt(event.get('created_at', ''))
                age_days = (now - created).days
                if age_days > 14:
                    key = subject
                    if key not in observations or age_days > observations[key]['age']:
                        observations[key] = {
                            'content': event.get('content', ''),
                            'age': age_days,
                            'subject': subject,
                        }
            except Exception:
                continue

        # Track any follow-up activity for the same subject in the last 14 days
        try:
            created = parse_dt(event.get('created_at', ''))
            if (now - created).days <= 14:
                follow_ups.add(subject)
        except Exception:
            continue

    # Find observations with no recent follow-up
    for subject, obs in observations.items():
        if subject not in follow_ups:
            draft = (
                f"I noticed something about {subject} about {obs['age']} days ago — "
                f"{obs['content'].lower()} — and I haven't heard how it turned out. "
                f"Everything okay there?"
            )
            return {
                'check': 4,
                'channel': 'garden-chat',
                'subject_id': subject,
                'draft': draft,
            }

    return None


def check_5_design_no_tasks(config: dict, health: dict) -> dict | None:
    """Design approved but no tasks started after 7 days."""
    projects = load_all_json(PROJECTS_DIR)
    tasks = load_all_json(TASKS_DIR)
    now = now_utc()

    # Build set of project IDs that have started tasks
    active_project_ids = set()
    for task in tasks:
        if task.get('status') in ('in_progress', 'completed'):
            pid = task.get('project_id', '')
            if pid:
                active_project_ids.add(pid)

    for project in projects:
        if project.get('status') != 'approved':
            continue

        project_id = project.get('project_id', '')
        if project_id in active_project_ids:
            continue

        approved_at = project.get('approved_at', '')
        if not approved_at:
            continue

        try:
            approved_dt = parse_dt(approved_at)
        except Exception:
            continue

        if (now - approved_dt).days >= 7:
            name = project.get('name', project_id)
            draft = (
                f"The {name} design was approved about {(now - approved_dt).days} "
                f"days ago — ready to start on tasks, or is something blocking?"
            )
            return {
                'check': 5,
                'channel': 'garden-builds',
                'subject_id': project_id,
                'draft': draft,
            }

    return None


def _get_knowledge_context(plant_name: str) -> str:
    """Try to get regional knowledge context for a plant to enrich the message."""
    try:
        from pgvector_search import search_knowledge
        results = search_knowledge(
            query=f'{plant_name} Zone 6b Missouri planting',
            plant=plant_name.lower().replace(' ', '-'),
            limit=1,
            min_similarity=0.6,
        )
        if results:
            content = results[0].get('content', '')
            source = results[0].get('source_id', '')
            # Extract a useful snippet (first 150 chars)
            snippet = content[:150].rsplit(' ', 1)[0] if len(content) > 150 else content
            if snippet and source:
                return f' According to {source.replace("_", " ").title()}, {snippet.lower()}'
    except Exception:
        pass
    return ''


def check_6_seasonal_timing(config: dict, health: dict) -> dict | None:
    """Seasonal plant timing — action needed in next 14 days."""
    plants = load_all_json(PLANTS_DIR)
    if not plants:
        return None

    # Approximate last frost date from config
    frost_str = config.get('last_frost_date_approx', '04-25')
    year = datetime.date.today().year
    try:
        last_frost = datetime.date(year, int(frost_str.split('-')[0]),
                                   int(frost_str.split('-')[1]))
    except Exception:
        last_frost = datetime.date(year, 4, 25)

    today = datetime.date.today()
    window_start = today
    window_end = today + datetime.timedelta(days=14)

    # Check recent events to avoid repeating
    events = load_all_json(EVENTS_DIR)
    recent_subjects = set()
    now = now_utc()
    for event in events:
        try:
            created = parse_dt(event.get('created_at', ''))
            if (now - created).days <= 7:
                recent_subjects.add(event.get('subject_id', ''))
        except Exception:
            continue

    for plant in plants:
        slug = plant.get('plant_slug', plant.get('common_name', ''))
        if not slug:
            continue

        # Skip if recently mentioned
        if slug.lower().replace(' ', '-') in recent_subjects:
            continue
        if slug.lower() in recent_subjects:
            continue

        planting = plant.get('planting', {})
        name = plant.get('common_name', slug)

        # Check start_indoors timing
        weeks_before = planting.get('start_indoors_weeks_before_last_frost')
        if weeks_before:
            start_date = last_frost - datetime.timedelta(weeks=weeks_before)
            if window_start <= start_date <= window_end:
                knowledge = _get_knowledge_context(name)
                draft = (
                    f"Just a heads up — if you're planning to grow {name} this year, "
                    f"now's about the time to start seeds indoors. Last frost is "
                    f"roughly {last_frost.strftime('%B %d')} for Farmington.{knowledge}"
                )
                return {
                    'check': 6,
                    'channel': 'garden-chat',
                    'subject_id': slug.lower().replace(' ', '-'),
                    'draft': draft,
                }

        # Check direct sow timing
        weeks_after = planting.get('direct_sow_after_last_frost_weeks', 0)
        if weeks_after:
            sow_date = last_frost + datetime.timedelta(weeks=weeks_after)
            if window_start <= sow_date <= window_end:
                knowledge = _get_knowledge_context(name)
                draft = (
                    f"Getting close to direct sow time for {name} — "
                    f"about {(sow_date - today).days} days out, "
                    f"weather permitting. Worth keeping an eye on the forecast.{knowledge}"
                )
                return {
                    'check': 6,
                    'channel': 'garden-chat',
                    'subject_id': slug.lower().replace(' ', '-'),
                    'draft': draft,
                }

    return None


def check_7_bed_photos(config: dict, health: dict) -> dict | None:
    """Bed with no photo in 60+ days — growing season only."""
    if not is_growing_season(config):
        return None

    beds = load_all_json(BEDS_DIR)
    now = now_utc()

    for bed in beds:
        last_photo = bed.get('last_photo_at')
        if not last_photo:
            # No photo ever taken — only nudge if bed is >30 days old
            created = bed.get('created_at', '')
            if not created:
                continue
            try:
                age = (now - parse_dt(created)).days
            except Exception:
                continue
            if age < 30:
                continue

            name = bed.get('name', bed.get('bed_id', 'a bed'))
            draft = (
                f"I don't think I've seen a photo of {name} yet — "
                f"whenever you get a chance, a quick snapshot would help me "
                f"keep track of how things are growing."
            )
            return {
                'check': 7,
                'channel': 'garden-chat',
                'subject_id': bed.get('bed_id', name),
                'draft': draft,
            }

        try:
            photo_dt = parse_dt(last_photo)
            if (now - photo_dt).days >= 60:
                name = bed.get('name', bed.get('bed_id', 'a bed'))
                draft = (
                    f"It's been a while since I've seen {name} — "
                    f"a fresh photo would help me keep up with how things "
                    f"are developing out there."
                )
                return {
                    'check': 7,
                    'channel': 'garden-chat',
                    'subject_id': bed.get('bed_id', name),
                    'draft': draft,
                }
        except Exception:
            continue

    return None


def check_8_knowledge_news(config: dict, health: dict) -> dict | None:
    """Surface new regional knowledge items that haven't been shared yet."""
    news = load_json_safe(KNOWLEDGE_NEWS_PATH)
    if not news or not isinstance(news, dict):
        return None

    items = news.get('items', [])
    unsurfaced = [
        item for item in items
        if not item.get('surfaced') and item.get('relevance_score', 0) >= 0.80
    ]

    if not unsurfaced:
        return None

    # Pick the most relevant unsurfaced item
    unsurfaced.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
    item = unsurfaced[0]

    source = item.get('source_id', 'regional knowledge')
    title = item.get('title', 'new gardening information')

    if item.get('plant_slug'):
        # Plant discovery news
        draft = (
            f"I came across some interesting information about "
            f"{title.replace('Discovered new plant: ', '')} while reading through "
            f"regional gardening resources. I've added it to the plant database — "
            f"want me to tell you more about it?"
        )
    else:
        # General knowledge news
        draft = (
            f"Something interesting from {source.replace('_', ' ').title()}: "
            f"{title}. Thought you'd want to know!"
        )

    return {
        'check': 8,
        'channel': 'garden-chat',
        'subject_id': f'knowledge-news-{item.get("detected_at", "")[:10]}',
        'draft': draft,
        '_news_item_index': items.index(item),  # track for marking surfaced
    }


# ── Main ──────────────────────────────────────────────────────────────────────

CHECK_FUNCTIONS = [
    check_1_weather,
    check_2_treatment_followup,
    check_3_fabrication_blocker,
    check_4_unresolved_observation,
    check_5_design_no_tasks,
    check_6_seasonal_timing,
    check_7_bed_photos,
    check_8_knowledge_news,
]


def main():
    parser = argparse.ArgumentParser(description='Slarti heartbeat agent (Agent 3)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Check conditions and print — do not post to Discord')
    parser.add_argument('--force', action='store_true',
                        help='Ignore weekly limit and season restrictions')
    parser.add_argument('--check', type=int, metavar='N',
                        help='Run only check N (1-8) for debugging')
    args = parser.parse_args()

    config = load_app_config()
    health = load_json_safe(HEALTH_STATUS_PATH) or {}

    timestamp = now_utc().isoformat()
    print(f'heartbeat: {timestamp}')

    for i, check_fn in enumerate(CHECK_FUNCTIONS, 1):
        if args.check and args.check != i:
            continue

        result = check_fn(config, health)
        if result is None:
            continue

        subject_id = result['subject_id']
        channel = result['channel']
        draft = result['draft']

        print(f'  Check {i} triggered: {subject_id} → #{channel}')

        # Gating
        if not args.force:
            allowed, reason = can_post(health, subject_id, config)
            if not allowed:
                print(f'  Gated: {reason}')
                continue  # Gated check doesn't count as "first match"

        # Friend test
        if not args.dry_run and not args.force:
            passes = friend_test(draft, subject_id, channel, config)
            if not passes:
                print(f'  Skipped: failed friend test')
                continue

        # Post or dry-run
        if args.dry_run:
            print(f'  [dry-run] Would post to #{channel}:')
            print(f'    {draft}')
        else:
            post_to_discord(channel, draft)
            mark_posted(health, subject_id)
            print(f'  Posted to #{channel}')

            # Mark knowledge news as surfaced after posting
            if result.get('check') == 8 and '_news_item_index' in result:
                try:
                    news = load_json_safe(KNOWLEDGE_NEWS_PATH)
                    if news and 'items' in news:
                        idx = result['_news_item_index']
                        if 0 <= idx < len(news['items']):
                            news['items'][idx]['surfaced'] = True
                            atomic_write_json(KNOWLEDGE_NEWS_PATH, news)
                except Exception:
                    pass

        break  # Stop at first match (HEARTBEAT.md rule)
    else:
        if not args.check:
            print('  nothing triggered')

    # Always update health with last run time
    health['last_heartbeat_run_at'] = timestamp
    if not args.dry_run:
        atomic_write_json(HEALTH_STATUS_PATH, health)


if __name__ == '__main__':
    main()
