#!/bin/bash
# Slarti restart sequence — run from WSL2
set -e

echo '=== Starting Slarti ==='
source ~/.bashrc 2>/dev/null || true

echo '1. Starting Docker stack...'
cd /mnt/c/Openclaw/slarti/db && docker compose up -d && sleep 10
docker exec slarti_stack-postgres-1 psql -U slarti -d slarti -c 'SELECT 1;' > /dev/null 2>&1 \
  || { echo 'ERROR: Postgres failed. Check Docker Desktop.'; exit 1; }
echo '   Postgres: OK'

echo '2. Starting OpenClaw gateway...'
openclaw gateway start --daemon 2>&1 || true
sleep 3
openclaw gateway health 2>&1
echo '   Gateway: OK'

echo '3. Starting voice webhook...'
cd /mnt/c/Openclaw/slarti
nohup python3 scripts/voice_webhook.py > logs/daily/voice_webhook.log 2>&1 &
echo '   Voice webhook PID: '$!

echo '4. Starting PWA server...'
cd /mnt/c/Openclaw/slarti/pwa
nohup python3 -m http.server 8080 --bind 0.0.0.0 > /mnt/c/Openclaw/slarti/logs/daily/pwa.log 2>&1 &
echo '   PWA server PID: '$!
cd /mnt/c/Openclaw/slarti

echo '5. Checking environment...'
source .env 2>/dev/null || true
[ -z "$ANTHROPIC_API_KEY" ]   && echo 'WARNING: ANTHROPIC_API_KEY not set'
[ -z "$GOOGLE_API_KEY" ]      && echo 'WARNING: GOOGLE_API_KEY not set'
[ -z "$DISCORD_BOT_TOKEN" ]   && echo 'WARNING: DISCORD_BOT_TOKEN not set'
[ -z "$ELEVENLABS_API_KEY" ]  && echo 'WARNING: ELEVENLABS_API_KEY not set'

echo '=== Slarti is running ==='
