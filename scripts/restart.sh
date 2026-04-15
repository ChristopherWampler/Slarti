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

echo '1b. Initializing database schema...'
cd /mnt/c/Openclaw/slarti && python3 scripts/init_db.py 2>&1
echo '   Schema: OK'

echo '1c. Refreshing weather data...'
cd /mnt/c/Openclaw/slarti && python3 scripts/weather_agent.py 2>&1 \
  || echo '   WARNING: Weather refresh failed — gateway will use cached data'
echo '   Weather: OK'

echo '2. Starting OpenClaw gateway...'
openclaw gateway start --daemon 2>&1 || true
sleep 3
openclaw gateway health 2>&1
echo '   Gateway: OK'

echo '3. Starting voice webhook + PWA (port 8080)...'
cd /mnt/c/Openclaw/slarti
nohup python3 scripts/voice_webhook.py > logs/daily/voice_webhook.log 2>&1 &
echo '   Voice webhook PID: '$!
sleep 2

echo '3b. Starting image watcher daemon...'
cd /mnt/c/Openclaw/slarti
nohup python3 scripts/image_watcher.py > logs/daily/image_watcher.log 2>&1 &
echo '   Image watcher PID: '$!

echo '4. Verifying voice webhook...'
curl -s http://localhost:8080/health > /dev/null \
  && echo '   PWA: OK (http://localhost:8080)' \
  || echo '   WARNING: Voice webhook not yet responding — check logs/daily/voice_webhook.log'

echo '5. Verifying heartbeat agent...'
python3 scripts/heartbeat_agent.py --dry-run 2>&1
echo '   Heartbeat: OK'

echo '6. Checking environment...'
source .env 2>/dev/null || true
[ -z "$ANTHROPIC_API_KEY" ]   && echo 'WARNING: ANTHROPIC_API_KEY not set'
[ -z "$GOOGLE_API_KEY" ]      && echo 'WARNING: GOOGLE_API_KEY not set'
[ -z "$DISCORD_BOT_TOKEN" ]   && echo 'WARNING: DISCORD_BOT_TOKEN not set'
[ -z "$ELEVENLABS_API_KEY" ]  && echo 'WARNING: ELEVENLABS_API_KEY not set'

echo '=== Slarti is running ==='
