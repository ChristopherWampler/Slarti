#!/usr/bin/env python3
"""
weather_agent.py — Daily weather check for Slarti

Runs at 6 AM via WSL2 cron. Fetches NWS hourly forecast for Farmington MO,
computes daily summary, and posts frost/heat advisories to #garden-log.

Usage:
  python3 scripts/weather_agent.py              # normal run
  python3 scripts/weather_agent.py --dry-run    # print only, no writes/posts
  python3 scripts/weather_agent.py --force      # post advisories even off-season
  python3 scripts/weather_agent.py --test-heat 92 --force --dry-run
  python3 scripts/weather_agent.py --test-frost 32 --dry-run
"""

import sys
import os
import json
import math
import time
import argparse
import pathlib
from datetime import datetime, timezone, timedelta
from urllib import request as urllib_request, error as urllib_error

from dotenv import load_dotenv

SCRIPT_DIR = pathlib.Path(__file__).parent
SLARTI_ROOT = SCRIPT_DIR.parent
load_dotenv(dotenv_path=SLARTI_ROOT / '.env')

sys.path.insert(0, str(SCRIPT_DIR))
import discord_alert

APP_CONFIG_PATH = SLARTI_ROOT / 'config' / 'app_config.json'
HEALTH_STATUS_PATH = SLARTI_ROOT / 'data' / 'system' / 'health_status.json'
WEATHER_TODAY_PATH = SLARTI_ROOT / 'data' / 'system' / 'weather_today.json'
WEATHER_WEEK_PATH = SLARTI_ROOT / 'data' / 'system' / 'weather_week.json'
WEATHER_ALERTS_PATH = SLARTI_ROOT / 'data' / 'system' / 'weather_alerts.json'
WEATHER_MD_PATH = SLARTI_ROOT / 'WEATHER.md'
USER_MD_PATH = SLARTI_ROOT / 'USER.md'

CRITICAL_ALERT_EVENTS = {
    'Tornado Warning',
    'Tornado Watch',
    'Severe Thunderstorm Warning',
    'Flash Flood Warning',
    'Flash Flood Emergency',
    'Extreme Wind Warning',
    'Winter Storm Warning',
    'Blizzard Warning',
    'Ice Storm Warning',
    'Freezing Rain Advisory',
    'Hard Freeze Warning',
    'Freeze Warning',
}


def load_app_config():
    with open(APP_CONFIG_PATH) as f:
        return json.load(f)


def fetch_with_retry(url, headers=None, max_retries=3, backoff=5):
    """Fetch a URL with retries and backoff. Returns parsed JSON."""
    req_headers = {'User-Agent': 'Slarti/1.0 (garden companion; farmington-mo)'}
    if headers:
        req_headers.update(headers)
    last_err = None
    for attempt in range(max_retries):
        try:
            req = urllib_request.Request(url, headers=req_headers)
            with urllib_request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except (urllib_error.URLError, Exception) as e:
            last_err = e
            if attempt < max_retries - 1:
                print(f'Retry {attempt + 1}/{max_retries - 1} for {url}: {e}', file=sys.stderr)
                time.sleep(backoff)
    raise RuntimeError(f'Failed to fetch {url} after {max_retries} attempts: {last_err}')


def fetch_hourly_forecast(lat, lng):
    """Two-step NWS API fetch. Returns list of hourly period dicts."""
    points_url = f'https://api.weather.gov/points/{lat},{lng}'
    points_data = fetch_with_retry(points_url)
    forecast_hourly_url = points_data['properties']['forecastHourly']
    forecast_data = fetch_with_retry(forecast_hourly_url)
    return forecast_data['properties']['periods']


def calc_heat_index(temp_f, rh):
    """Rothfusz regression with NWS low-humidity and high-humidity adjustments."""
    if temp_f < 80:
        return temp_f
    hi = (-42.379
          + 2.04901523 * temp_f
          + 10.14333127 * rh
          - 0.22475541 * temp_f * rh
          - 0.00683783 * temp_f ** 2
          - 0.05481717 * rh ** 2
          + 0.00122874 * temp_f ** 2 * rh
          + 0.00085282 * temp_f * rh ** 2
          - 0.00000199 * temp_f ** 2 * rh ** 2)
    if rh < 13 and 80 <= temp_f <= 112:
        hi -= ((13 - rh) / 4) * math.sqrt((17 - abs(temp_f - 95)) / 17)
    elif rh > 85 and 80 <= temp_f <= 87:
        hi += ((rh - 85) / 10) * ((87 - temp_f) / 5)
    return round(hi, 1)


def parse_temp(period):
    temp = period.get('temperature', 0)
    unit = period.get('temperatureUnit', 'F')
    if unit == 'C':
        temp = temp * 9 / 5 + 32
    return float(temp)


def parse_humidity(period):
    rh_obj = period.get('relativeHumidity', {})
    if isinstance(rh_obj, dict):
        return float(rh_obj.get('value', 50) or 50)
    return float(rh_obj or 50)


def parse_wind_speed(period):
    ws = period.get('windSpeed', '0 mph')
    if isinstance(ws, str):
        parts = ws.split()
        try:
            return float(parts[0])
        except (ValueError, IndexError):
            return 0.0
    return float(ws or 0)


def parse_precip(period):
    prob = period.get('probabilityOfPrecipitation', {})
    if isinstance(prob, dict):
        return float(prob.get('value', 0) or 0)
    return float(prob or 0)


def compute_daily_summary(periods):
    """Compute today's weather summary from hourly NWS periods."""
    # Farmington MO is UTC-5 (CST) / UTC-6 (CDT); use UTC-5 as conservative base
    central_offset = timedelta(hours=-5)
    now_local = datetime.now(timezone.utc) + central_offset
    today_str = now_local.strftime('%Y-%m-%d')

    today_periods = []
    noon_period = None

    for p in periods:
        start = p.get('startTime', '')
        if not start:
            continue
        try:
            period_date = start[:10]
            if period_date == today_str:
                today_periods.append(p)
                hour = int(start[11:13])
                if hour == 12 and noon_period is None:
                    noon_period = p
        except (ValueError, IndexError):
            continue

    if not today_periods:
        today_periods = periods[:24]

    if not noon_period and today_periods:
        noon_period = today_periods[len(today_periods) // 2]

    temps = [parse_temp(p) for p in today_periods]
    humidities = [parse_humidity(p) for p in today_periods]
    heat_indices = [calc_heat_index(t, h) for t, h in zip(temps, humidities)]
    wind_speeds = [parse_wind_speed(p) for p in today_periods]
    precip_probs = [parse_precip(p) for p in today_periods]

    return {
        'temp_high': round(max(temps)) if temps else None,
        'temp_low': round(min(temps)) if temps else None,
        'heat_index_max': round(max(heat_indices)) if heat_indices else None,
        'precip_chance_max': round(max(precip_probs)) if precip_probs else 0,
        'wind_speed_max': round(max(wind_speeds)) if wind_speeds else 0,
        'short_forecast': noon_period.get('shortForecast', 'Unknown') if noon_period else 'Unknown',
    }


def compute_week_summary(periods):
    """Group hourly periods by date and return 7-day high/low/precip summary."""
    days = {}
    for p in periods:
        start = p.get('startTime', '')
        if not start:
            continue
        date_str = start[:10]
        if date_str not in days:
            days[date_str] = []
        days[date_str].append(p)

    result = []
    for date_str in sorted(days.keys())[:7]:
        day_periods = days[date_str]
        temps = [parse_temp(p) for p in day_periods]
        precip_probs = [parse_precip(p) for p in day_periods]
        noon_period = next(
            (p for p in day_periods
             if '12:' in p.get('startTime', '') or '13:' in p.get('startTime', '')),
            day_periods[len(day_periods) // 2] if day_periods else None
        )
        result.append({
            'date': date_str,
            'temp_high': round(max(temps)) if temps else None,
            'temp_low': round(min(temps)) if temps else None,
            'precip_chance_max': round(max(precip_probs)) if precip_probs else 0,
            'short_forecast': noon_period.get('shortForecast', 'Unknown') if noon_period else 'Unknown',
        })
    return result


def determine_advisories(summary, config, args):
    """Return list of active advisory types based on thresholds."""
    advisories = []
    month = datetime.now().month
    growing_start = config.get('growing_season_start_month', 5)
    growing_end = config.get('growing_season_end_month', 10)
    in_growing_season = growing_start <= month <= growing_end

    temp_low = summary['temp_low']
    heat_index_max = summary['heat_index_max']

    if args.test_frost is not None:
        temp_low = args.test_frost
    if args.test_heat is not None:
        heat_index_max = args.test_heat

    # Frost — always active regardless of season
    if temp_low is not None and temp_low <= 36:
        advisories.append('frost')

    # Heat — growing season only (or --force)
    if in_growing_season or args.force:
        if heat_index_max is not None:
            if heat_index_max >= 90:
                advisories.append('heat_high_risk')
            elif heat_index_max >= 85:
                advisories.append('heat_contextual')
    else:
        if heat_index_max is not None and heat_index_max >= 85:
            print(f'Advisory suppressed: off-season (month {month}, '
                  f'heat_index_max {heat_index_max}°F). Use --force to override.')

    return advisories


def fetch_active_alerts(lat, lng):
    """Fetch active NWS alerts for the given point. Returns list of critical alert dicts."""
    url = f'https://api.weather.gov/alerts/active?point={lat},{lng}'
    try:
        data = fetch_with_retry(url)
    except Exception as e:
        print(f'WARNING: Could not fetch NWS alerts: {e}', file=sys.stderr)
        return []

    critical = []
    for feature in data.get('features', []):
        props = feature.get('properties', {})
        if props.get('status') != 'Actual':
            continue
        event = props.get('event', '')
        if event in CRITICAL_ALERT_EVENTS:
            critical.append({
                'id': props.get('id', ''),
                'event': event,
                'severity': props.get('severity', ''),
                'headline': props.get('headline', ''),
                'expires': props.get('expires', ''),
            })
    return critical


def generate_advisory_message(summary, advisory_type, config):
    """Call Claude Haiku to write Slarti's advisory message for #garden-log."""
    try:
        import anthropic
    except ImportError:
        print('ERROR: anthropic not installed. Run: pip install anthropic --break-system-packages', file=sys.stderr)
        sys.exit(1)

    advisory_descriptions = {
        'frost': f"frost risk tonight (low of {summary['temp_low']}°F)",
        'heat_contextual': f"warm day ahead (heat index up to {summary['heat_index_max']}°F)",
        'heat_high_risk': f"dangerous heat today (heat index up to {summary['heat_index_max']}°F)",
    }

    prompt = (
        "You are Slarti, a warm and curious garden companion AI for Christopher and Emily "
        "in Farmington, Missouri (USDA Zone 6b).\n\n"
        f"Today's weather: high {summary['temp_high']}°F, low {summary['temp_low']}°F, "
        f"heat index max {summary['heat_index_max']}°F, "
        f"precipitation chance {summary['precip_chance_max']}%, "
        f"{summary['short_forecast']}.\n\n"
        f"Advisory: {advisory_descriptions.get(advisory_type, advisory_type)}\n\n"
        "Write 1–3 sentences for the #garden-log Discord channel. "
        "Be warm, specific, and actionable. Sound like a knowledgeable friend, not a weather app. "
        "Focus on what this means for the garden right now (Zone 6b, Missouri). "
        "No emojis unless a single plant or weather symbol fits naturally. "
        "Do not start with \"I\"."
    )

    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    response = client.messages.create(
        model=config.get('claude_model_haiku', 'claude-haiku-4-5-20251001'),
        max_tokens=150,
        messages=[{'role': 'user', 'content': prompt}]
    )
    return response.content[0].text.strip()


def get_garden_log_channel_id():
    """Look up the #garden-log channel ID via Discord REST API."""
    guild_id = os.environ.get('DISCORD_GUILD_ID')
    bot_token = os.environ.get('DISCORD_BOT_TOKEN')
    if not guild_id or not bot_token:
        raise RuntimeError('DISCORD_GUILD_ID or DISCORD_BOT_TOKEN not set in .env')

    url = f'https://discord.com/api/v10/guilds/{guild_id}/channels'
    req = urllib_request.Request(
        url,
        headers={
            'Authorization': f'Bot {bot_token}',
            'User-Agent': 'Slarti/1.0',
        }
    )
    with urllib_request.urlopen(req, timeout=15) as resp:
        channels = json.loads(resp.read().decode('utf-8'))

    for channel in channels:
        if channel.get('name') == 'garden-log':
            return channel['id']

    raise RuntimeError('Could not find #garden-log channel in Discord guild')


def post_to_garden_log(message):
    """Post a message to #garden-log via Discord REST API."""
    bot_token = os.environ.get('DISCORD_BOT_TOKEN')
    if not bot_token:
        raise RuntimeError('DISCORD_BOT_TOKEN not set in .env')

    channel_id = get_garden_log_channel_id()
    url = f'https://discord.com/api/v10/channels/{channel_id}/messages'
    payload = json.dumps({'content': message}).encode('utf-8')
    req = urllib_request.Request(
        url,
        data=payload,
        headers={
            'Authorization': f'Bot {bot_token}',
            'Content-Type': 'application/json',
            'User-Agent': 'Slarti/1.0',
        },
        method='POST'
    )
    with urllib_request.urlopen(req, timeout=15) as resp:
        if resp.status not in (200, 201, 204):
            print(f'WARNING: Discord returned {resp.status}', file=sys.stderr)


def atomic_write_json(path, data):
    """Write JSON atomically using temp file + os.replace()."""
    tmp_path = str(path) + '.tmp'
    with open(tmp_path, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def update_health_status(timestamp_iso):
    """Update last_weather_refresh_at in health_status.json."""
    try:
        with open(HEALTH_STATUS_PATH) as f:
            health = json.load(f)
        health['last_weather_refresh_at'] = timestamp_iso
        atomic_write_json(HEALTH_STATUS_PATH, health)
    except Exception as e:
        print(f'WARNING: Could not update health_status.json: {e}', file=sys.stderr)


def write_weather_md(weather_today: dict):
    """Write WEATHER.md to workspace root for OpenClaw context injection.

    OpenClaw injects all workspace root .md files into Claude's context on
    every request. This file is the hot-context delivery mechanism for daily
    weather — it is a rendering of weather_today.json, not the source of truth.
    """
    date = weather_today.get('date', 'unknown')
    high = weather_today.get('temp_high', '?')
    low = weather_today.get('temp_low', '?')
    forecast = weather_today.get('short_forecast', 'unknown')
    precip = weather_today.get('precip_chance_max', '?')
    wind = weather_today.get('wind_speed_max', '?')
    heat_index = weather_today.get('heat_index_max', '?')
    advisories = weather_today.get('advisories', [])
    updated = weather_today.get('last_updated', 'unknown')

    advisory_line = ', '.join(advisories) if advisories else 'None'

    content = (
        '# WEATHER.md — Today\'s Conditions\n\n'
        '*Auto-updated daily at 6 AM by weather_agent.py. Do not edit manually.*\n\n'
        f'## Farmington, Missouri — {date}\n\n'
        f'**Today:** {high}°F high / {low}°F low | {forecast}\n'
        f'**Precipitation:** {precip}% chance | **Wind:** {wind} mph max | **Heat index:** {heat_index}°F\n'
        f'**Advisories:** {advisory_line}\n\n'
        f'*Updated: {updated}*\n'
    )

    tmp_path = str(WEATHER_MD_PATH) + '.tmp'
    with open(tmp_path, 'w') as f:
        f.write(content)
    os.replace(tmp_path, WEATHER_MD_PATH)


def update_user_md_weather(weather_today: dict):
    """Append/replace Today's Conditions section in USER.md for OpenClaw context injection.

    USER.md is loaded by OpenClaw on every Claude request. This ensures today's weather
    is always in context even if the read tool is unavailable.
    """
    date = weather_today.get('date', 'unknown')
    high = weather_today.get('temp_high', '?')
    low = weather_today.get('temp_low', '?')
    forecast = weather_today.get('short_forecast', 'unknown')
    precip = weather_today.get('precip_chance_max', '?')
    wind = weather_today.get('wind_speed_max', '?')
    heat_index = weather_today.get('heat_index_max', '?')
    advisories = weather_today.get('advisories', [])
    advisory_line = ', '.join(advisories) if advisories else 'None'
    refreshed_at = datetime.now().strftime('%-I:%M %p CDT')

    new_section = (
        '\n---\n\n'
        "## Today's Conditions \u2014 Farmington, MO\n"
        f'*Last refreshed: {refreshed_at}. Auto-updated by weather_agent.py. Do not edit manually.*\n\n'
        f'Date: {date}\n'
        f'Forecast: {forecast} | High: {high}\u00b0F / Low: {low}\u00b0F\n'
        f'Heat index: {heat_index}\u00b0F | Precip chance: {precip}% | Wind: {wind} mph\n'
        f'Advisories: {advisory_line}\n'
    )

    existing = USER_MD_PATH.read_text(encoding='utf-8') if USER_MD_PATH.exists() else ''
    marker = "\n---\n\n## Today's Conditions"
    idx = existing.find(marker)
    base = existing[:idx] if idx != -1 else existing.rstrip()

    content = base + new_section
    tmp = str(USER_MD_PATH) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(content)
    os.replace(tmp, USER_MD_PATH)


def main():
    parser = argparse.ArgumentParser(description='Slarti daily weather agent')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print output only — do not write files or post to Discord')
    parser.add_argument('--force', action='store_true',
                        help='Post heat advisories even in off-season months')
    parser.add_argument('--test-heat', type=float, metavar='N', dest='test_heat',
                        help='Override heat_index_max with N for advisory testing')
    parser.add_argument('--test-frost', type=float, metavar='N', dest='test_frost',
                        help='Override temp_low with N for frost testing')
    args = parser.parse_args()

    config = load_app_config()
    lat = config['nws_lat']
    lng = config['nws_lng']

    print(f'Fetching NWS hourly forecast for {lat},{lng}...')
    try:
        periods = fetch_hourly_forecast(lat, lng)
    except RuntimeError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        try:
            discord_alert.send('admin-log', f'[weather_agent] NWS API failed: {str(e)[:150]}')
        except Exception:
            pass
        sys.exit(1)

    summary = compute_daily_summary(periods)
    week_days = compute_week_summary(periods)
    now_iso = datetime.now(timezone.utc).astimezone().isoformat()
    today_str = datetime.now().strftime('%Y-%m-%d')

    print(f"Today ({today_str}): high {summary['temp_high']}°F, low {summary['temp_low']}°F, "
          f"heat index max {summary['heat_index_max']}°F, "
          f"precip {summary['precip_chance_max']}%, "
          f"wind {summary['wind_speed_max']} mph, "
          f"{summary['short_forecast']}")

    advisories = determine_advisories(summary, config, args)

    print(f'Checking NWS active alerts for {lat},{lng}...')
    active_alerts = fetch_active_alerts(lat, lng)
    if active_alerts:
        print(f'Active critical alerts: {[a["event"] for a in active_alerts]}')
    else:
        print('No active critical alerts.')

    weather_today = {
        'date': today_str,
        'temp_high': summary['temp_high'],
        'temp_low': summary['temp_low'],
        'heat_index_max': summary['heat_index_max'],
        'precip_chance_max': summary['precip_chance_max'],
        'wind_speed_max': summary['wind_speed_max'],
        'short_forecast': summary['short_forecast'],
        'advisories': advisories,
        'active_alert_events': [a['event'] for a in active_alerts],
        'last_updated': now_iso,
    }

    weather_week = {
        'days': week_days,
        'last_updated': now_iso,
    }

    if advisories:
        print(f'Active advisories: {advisories}')
        for advisory in advisories:
            print(f'Generating {advisory} message via Claude Haiku...')
            try:
                message = generate_advisory_message(summary, advisory, config)
                print(f'Message: {message}')
                if not args.dry_run:
                    post_to_garden_log(message)
                    print(f'Posted {advisory} advisory to #garden-log')
                else:
                    print(f'[dry-run] Would post to #garden-log: {message}')
            except Exception as e:
                print(f'ERROR generating/posting {advisory} advisory: {e}', file=sys.stderr)
    else:
        print('No advisories today.')

    weather_alerts = {
        'alerts': active_alerts,
        'last_checked': now_iso,
        'point': {'lat': lat, 'lng': lng},
    }

    if not args.dry_run:
        atomic_write_json(WEATHER_TODAY_PATH, weather_today)
        atomic_write_json(WEATHER_WEEK_PATH, weather_week)
        atomic_write_json(WEATHER_ALERTS_PATH, weather_alerts)
        write_weather_md(weather_today)
        update_user_md_weather(weather_today)
        update_health_status(now_iso)
        print('Wrote weather_today.json, weather_week.json, weather_alerts.json, WEATHER.md, USER.md, updated health_status.json')
    else:
        print('[dry-run] Would write weather_today.json:')
        print(json.dumps(weather_today, indent=2))
        print('[dry-run] Would write weather_week.json with', len(week_days), 'days')
        print('[dry-run] Would write weather_alerts.json:')
        print(json.dumps(weather_alerts, indent=2))


if __name__ == '__main__':
    main()
