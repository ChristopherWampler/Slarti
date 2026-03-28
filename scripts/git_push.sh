#!/bin/bash
# Slarti nightly Git push — runs at 3:00 AM via cron
# Crontab: 0 3 * * * /mnt/c/Openclaw/slarti/scripts/git_push.sh >> /mnt/c/Openclaw/slarti/logs/daily/git_push.log 2>&1

SLARTI_DIR="/mnt/c/Openclaw/slarti"
HEALTH_FILE="$SLARTI_DIR/data/system/health_status.json"
LOG_DATE=$(date +%Y-%m-%d)

cd "$SLARTI_DIR" || exit 1

# --- Postgres backup ---
mkdir -p "$SLARTI_DIR/backups"
pg_dump -h localhost -U slarti slarti > "$SLARTI_DIR/backups/db_$LOG_DATE.sql" 2>/dev/null
# Keep only the last 14 backups
ls -t "$SLARTI_DIR/backups/db_"*.sql 2>/dev/null | tail -n +15 | xargs rm -f

# --- Log rotation (remove logs older than 90 days) ---
find "$SLARTI_DIR/logs/daily/" -name "*.log" -mtime +90 -delete 2>/dev/null

if git diff --quiet && git diff --staged --quiet; then
  echo "[$LOG_DATE] No changes to push"
  exit 0
fi

git add -A
git commit -m "slarti: nightly sync $LOG_DATE" 2>&1

if git push origin main 2>&1; then
  echo "[$LOG_DATE] Push successful"
  python3 -c "
import json, datetime
with open('$HEALTH_FILE') as f: h = json.load(f)
h['last_git_push_at'] = datetime.datetime.utcnow().isoformat() + 'Z'
h['git_push_failures_consecutive'] = 0
with open('$HEALTH_FILE','w') as f: json.dump(h, f, indent=2)
  "
else
  echo "[$LOG_DATE] Push FAILED"
  python3 -c "
import json
with open('$HEALTH_FILE') as f: h = json.load(f)
h['git_push_failures_consecutive'] = h.get('git_push_failures_consecutive', 0) + 1
with open('$HEALTH_FILE','w') as f: json.dump(h, f, indent=2)
  "
  FAILURES=$(python3 -c "import json; h=json.load(open('$HEALTH_FILE')); print(h['git_push_failures_consecutive'])")
  if [ "$FAILURES" -ge 2 ]; then
    python3 "$SLARTI_DIR/scripts/discord_alert.py" \
      --channel admin-log \
      --message "Git sync has not completed in $FAILURES nights. Local backups intact but remote sync needs attention."
  fi
  if [ "$FAILURES" -ge 3 ]; then
    python3 -c "
import json, sys
sys.path.insert(0, '$SLARTI_DIR/scripts')
from discord_alert import send
users = json.load(open('$SLARTI_DIR/config/discord_users.json'))
chris_id = next((k for k,v in users['users'].items() if v == 'christopher'), None)
mention = f'<@{chris_id}>' if chris_id else '@Christopher'
send('admin-log', f'{mention} — Slarti has not been able to sync to GitHub for $FAILURES nights. Worth checking the connection when you get a chance.')
    "
  fi
fi
