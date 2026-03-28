#!/usr/bin/env python3
"""
discord_alert.py — sends a message to a Discord webhook
Usage: python3 discord_alert.py --channel admin-log --message "your message here"
Requires DISCORD_ADMIN_WEBHOOK in .env
"""
import sys
import os
import json
import argparse
from urllib import request as urllib_request
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))


def send(channel: str, message: str):
    if channel == 'admin-log':
        webhook_url = os.environ.get('DISCORD_ADMIN_WEBHOOK')
    else:
        raise ValueError(f'Unknown channel: {channel}')
    if not webhook_url:
        print('ERROR: DISCORD_ADMIN_WEBHOOK not set in .env', file=sys.stderr)
        sys.exit(1)
    payload = json.dumps({'content': message}).encode('utf-8')
    req = urllib_request.Request(
        webhook_url,
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    with urllib_request.urlopen(req) as resp:
        if resp.status not in (200, 204):
            print(f'WARNING: Discord returned {resp.status}', file=sys.stderr)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--channel', required=True)
    parser.add_argument('--message', required=True)
    args = parser.parse_args()
    send(args.channel, args.message)
