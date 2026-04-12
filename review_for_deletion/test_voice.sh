#!/usr/bin/env bash
# test_voice.sh — quick voice test with auto-incrementing output file
# Usage: bash scripts/test_voice.sh

set -e

DESKTOP="/mnt/c/Users/Chris/Desktop"
TMP="/tmp/slarti_voice_test.mp3"

# Find next available test number
n=1
while [ -f "${DESKTOP}/slarti_test${n}.mp3" ]; do
  n=$((n+1))
done

OUT="${DESKTOP}/slarti_test${n}.mp3"
echo "Running test → will save as slarti_test${n}.mp3"

curl -s -X POST http://localhost:8080/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"How does the herb bed look today? The tomatoes should be coming in nicely this time of year.","author":"christopher","history":[]}' \
  --output "$TMP"

# Check if output is non-empty (empty = server error)
SIZE=$(stat -c%s "$TMP" 2>/dev/null || echo 0)
if [ "$SIZE" -lt 1000 ]; then
  echo "ERROR: Output file too small (${SIZE} bytes) — check server logs"
  exit 1
fi

cp "$TMP" "$OUT"
echo "Saved: $OUT (${SIZE} bytes)"
