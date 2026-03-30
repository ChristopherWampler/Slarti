# Slarti Build Log

Complete record of what was built, what decisions were made, and where everything lives.
Last updated: 2026-03-29

---

## Phase 1 — Core Files

**Completed:** 2026-03-28

### What was built

| File | Purpose |
|---|---|
| `SOUL.md` | Slarti's personality — injected into every Claude call by OpenClaw |
| `prompts/system/SOUL.md` | Legacy copy (pre-OpenClaw); now superseded by root `SOUL.md` |
| `config/app_config.json` | App settings: location (Farmington MO), model name, NWS coordinates, schedule times, port numbers |
| `config/provider_policy.json` | Four-provider routing rules (Anthropic / Google / OpenAI fallback / ElevenLabs voice) |
| `config/confidence_thresholds.json` | Memory write gates: <0.50 requires confirmation; 0.50–0.79 surfaces first; ≥0.80 auto-saves |
| `config/discord_users.json` | Placeholder Discord user ID → emily/christopher map (fill in Phase 7) |
| `.env.example` | API key template — never fill in, never commit |
| `data/system/health_status.json` | System heartbeat — tracks last model call, last backup, push failures |
| `data/system/write_log.json` | Audit trail of all memory writes (starts as `[]`) |
| `data/system/onboarding_state.json` | Tracks `!setup` wizard progress |

### Key decisions

- **Claude model** stored in `config/app_config.json` as `"claude_model": "claude-sonnet-4-6"` — never hardcoded in scripts
- **SOUL.md** written to be read by a person as much as by Claude — warm, story-driven, not procedural
- **Emily-wins rule** encoded in SOUL.md (personality) and AGENTS.md (operational): Emily's most recent statement supersedes Christopher's silently; Emily contradicting herself triggers an `"unresolved"` flag, asked once, re-asked at 7 days, resolved by recency at 14 days

---

## Phase 2 — Git & Automation

**Completed:** 2026-03-28

### What was built

| File | Purpose |
|---|---|
| `.gitignore` | Excludes `.env`, all photo image files, `backups/`, `__pycache__`, `logs/*.log` |
| `scripts/git_push.sh` | Nightly cron script: pg_dump → git commit → git push → Discord alert on failure |
| `scripts/discord_alert.py` | Sends messages to Discord `#admin-log` via webhook URL |

### Git setup

- **Remote:** `https://github.com/ChristopherWampler/Slarti`
- **Branch:** `main`
- **Initial commit:** `slarti: initial setup v5.2`
- GitHub repo already had a `README.md` (auto-created by GitHub) — pulled with `--allow-unrelated-histories`

### Cron job (WSL2 only — must be set manually)

The nightly push runs at 3:00 AM in WSL2. This must be added manually because `crontab` doesn't exist in Git Bash:

```bash
# Run this in WSL2:
(crontab -l 2>/dev/null; echo "0 3 * * * /mnt/c/Openclaw/slarti/scripts/git_push.sh >> /mnt/c/Openclaw/slarti/logs/daily/git_push.log 2>&1") | crontab -
```

Verify: `crontab -l` — should show the `0 3 * * *` entry.

### What `git_push.sh` does every night at 3 AM

1. `pg_dump` the Postgres database → `backups/db_YYYY-MM-DD.sql` (keeps last 14)
2. Rotates logs older than 90 days from `logs/daily/`
3. If no changes: exits silently
4. `git add -A && git commit -m "slarti: nightly sync YYYY-MM-DD"`
5. `git push origin main`
6. On success: updates `health_status.json` → `last_git_push_at`
7. On failure: increments `git_push_failures_consecutive`; alerts Discord admin-log after 2 failures; @mentions Christopher after 3

---

## Phase 3 — Database

**Completed:** 2026-03-28

### What was built

| File | Purpose |
|---|---|
| `db/docker-compose.yml` | Docker Compose for `slarti_stack` — Postgres 16 + pgvector |
| `db/.gitignore` | Excludes `db/data/` (Postgres data files) from git |

### Stack details

- **Stack name:** `slarti_stack`
- **Container name:** `slarti_stack-postgres-1`
- **Port:** `5432` (localhost only)
- **Database:** `slarti`
- **User:** `slarti`
- **Password:** stored in `.env` as `POSTGRES_PASSWORD` — **write it down**
- **pgvector version:** 0.8.2 (enabled via `CREATE EXTENSION IF NOT EXISTS vector;`)

### Managing the database

```bash
# Start
cd /mnt/c/Openclaw/slarti/db && docker compose up -d

# Stop
docker compose down

# Connect directly
docker exec -it slarti_stack-postgres-1 psql -U slarti -d slarti

# Health check
docker ps --filter "name=slarti_stack"
```

### Note on .env for docker compose

The `docker-compose.yml` uses `env_file: ../.env` to load credentials from the parent directory. It does NOT use shell variable substitution — all Postgres vars (`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`) come entirely from `.env`.

---

## Phase 4 — MarkItDown

**Completed:** 2026-03-28

### What was installed

- **MarkItDown 0.1.5** — converts audio, PDFs, Office docs, images to Markdown for Claude
- **ffmpeg 8.1** — required by MarkItDown for audio transcription (Mode V)

### Install paths

- MarkItDown: `C:\Users\Chris\AppData\Local\Programs\Python\Python313\Lib\site-packages\markitdown`
- ffmpeg binaries: `C:\Users\Chris\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_...\bin\`

### WSL2 note

ffmpeg was installed on Windows. When running the voice webhook from WSL2 (Phase 13), install it there too:

```bash
sudo apt install ffmpeg
```

---

## Phase 5 — OpenClaw Gateway

**Completed:** 2026-03-28

### What OpenClaw actually is (vs. the spec)

The Slarti spec was written with an idealized OpenClaw in mind. Here is the reality:

| Spec assumed | Reality |
|---|---|
| `pip install openclaw` | Node.js gateway, installed via PowerShell |
| `config/openclaw.yaml` | `~/.openclaw/openclaw.json` (JSON5 format) |
| `config/mode_classifiers.yaml` | Goes in `AGENTS.md` as behavioral rules for Claude |
| `openclaw agents register` | Not a command — workspace files load automatically |
| `openclaw start --config` | `openclaw gateway start` |

OpenClaw is a general-purpose personal AI assistant that connects messaging apps to AI models. It reads `SOUL.md`, `AGENTS.md`, and `USER.md` from the configured workspace directory and injects them into Claude's context on every request.

### What was built

| File | Purpose |
|---|---|
| `SOUL.md` | Workspace root — OpenClaw reads this on every Claude call |
| `AGENTS.md` | Full behavioral instructions: mode A–P logic, memory write rules, Emily-wins, Zone 6b, heartbeat procedures |
| `USER.md` | Emily and Christopher context |
| `MEMORY.md` | Long-term memory (starts empty, grows as garden grows) |
| `scripts/restart.sh` | Full Slarti restart sequence |
| `~/.openclaw/openclaw.json` | Gateway config (outside this git repo) |

### Gateway config location

`~/.openclaw/openclaw.json` — NOT in the git repo (it contains the gateway auth token).

Key settings configured:
- Agent `slarti` — workspace: `C:\Openclaw\slarti`, model: `anthropic/claude-sonnet-4-6`
- Anthropic and Google providers with API keys from Windows env vars
- Session isolation per user (`dmScope: "per-channel-peer"`)
- Discord channel commented out — enable in Phase 7

### API keys — Windows environment variables

API keys were set as Windows user environment variables (in addition to `.env`) so the OpenClaw gateway service can access them. Set via `setx`:

```
ANTHROPIC_API_KEY — set ✅
GOOGLE_API_KEY    — set ✅
OPENAI_API_KEY    — set ✅
ELEVENLABS_API_KEY — set ✅
```

These persist in the Windows registry. New processes (including OpenClaw) will have them automatically.

### Starting and stopping the gateway

```powershell
# Start (run in PowerShell or CMD — not WSL2)
openclaw gateway start

# Health check
openclaw gateway health    # should return "OK (0ms)"

# Status
openclaw gateway status

# Stop
openclaw gateway stop
```

The gateway installs as a Windows startup item and starts automatically on login.
Path: `C:\Users\Chris\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\OpenClaw Gateway.cmd`

### Key architectural insight

The `AGENTS.md` file is not a registration file — it is read by Claude as behavioral instructions on every request. All of Slarti's mode classification logic (A–P modes), memory write policies, and operational rules live there as natural-language instructions to Claude. Claude follows them as part of its reasoning, not as separate infrastructure.

---

## Credentials Reference

All credentials live in `C:\Openclaw\slarti\.env` (never committed to git).

| Credential | Status | Where used |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ Set | Claude Sonnet — all conversation, agents, summaries |
| `GOOGLE_API_KEY` | ✅ Set | Gemini Flash (photos), text-embedding-004 (pgvector) |
| `OPENAI_API_KEY` | ✅ Set | DALL-E 3 fallback, web search plant lookup |
| `ELEVENLABS_API_KEY` | ✅ Set | Voice output (Phase 13) |
| `DISCORD_BOT_TOKEN` | ✅ Set | OpenClaw Discord channel |
| `DISCORD_GUILD_ID` | ✅ Set | Discord server ID |
| `DISCORD_ADMIN_WEBHOOK` | ✅ Set | #admin-log webhook |
| `POSTGRES_PASSWORD` | ✅ Set | slarti database user |

---

---

## Phase 6 — Claude API

**Completed:** 2026-03-28 (via OpenClaw gateway — no separate step required)

The spec assumed a custom `openclaw.yaml` with a `soul_path` field. In practice, OpenClaw natively loads `SOUL.md`, `AGENTS.md`, and `USER.md` from the configured workspace and injects them into Claude's context on every request. No additional wiring needed.

`ANTHROPIC_API_KEY` was set as a Windows environment variable (via `setx`) so the OpenClaw gateway service can access it at startup.

**Verified:** @Slarti responds in Discord with Claude Sonnet 4.6.

---

## Phase 7 — Discord Bot

**Completed:** 2026-03-29

### What was built / configured

| Item | Detail |
|---|---|
| Discord application | Created at discord.com/developers — bot named Slarti |
| Bot token | Set as `DISCORD_BOT_TOKEN` in `.env` and Windows env var |
| Guild (server) | Slarti Garden — ID set as `DISCORD_GUILD_ID` |
| 7 channels | #garden-chat, #garden-photos, #garden-design, #garden-log, #plant-alerts, #weekly-summary, #admin-log |
| Admin webhook | Created for #admin-log — set as `DISCORD_ADMIN_WEBHOOK` |
| Christopher's Discord ID | `314576001306722314` — set in `config/discord_users.json` |
| Emily's Discord ID | **⏳ Pending** — placeholder still in `config/discord_users.json` |
| OpenClaw Discord channel | Wired via `openclaw doctor --fix` — `channels.discord.enabled: true` |
| Pairing | Christopher's Discord account approved via `openclaw pairing approve discord` |

### Key discoveries / non-obvious fixes

- `openclaw doctor --fix` auto-detected `DISCORD_BOT_TOKEN` env var and configured Discord — no manual `channels` block needed in the agent config
- `groupPolicy` must be `"open"` for the bot to respond in all channels (doctor defaults to `"allowlist"` with no channels, which blocks everything)
- Discord native @mentions send `<@BOT_USER_ID>` in raw content, not `@Slarti`. Added `"<@1487572857482514542>"` to `mentionPatterns` in `~/.openclaw/openclaw.json` so OpenClaw's text pattern check matches it
- First DM response shows a pairing code — must run `openclaw pairing approve discord <code>` to authorize each user

### What's still needed

- Emily's Discord user ID in `config/discord_users.json` (add when she's ready, then `openclaw gateway restart`)

---

## Phase 8 — Memory Layer

**Completed:** 2026-03-29

### What was built

| File | Purpose |
|---|---|
| `scripts/extraction_agent.py` | Agent 2 — reads OpenClaw session JSONL files, extracts facts via Claude, writes JSON to `data/events/2026/`, stores embeddings in pgvector, triggers `garden.md` regeneration on BED_FACT or DECISION |
| `scripts/markitdown_ingest.py` | Converts audio, PDFs, Office docs, and images to Markdown for plant DB seeding and Mode V transcription |
| `data/system/health_status.json` | System health tracker initialized with all required fields |
| `data/system/processed_sessions.json` | Tracks which OpenClaw session files have been processed by the extraction agent |
| `docs/garden.md` | Placeholder — will be regenerated after first onboarding session |
| `IDENTITY.md` | OpenClaw agent identity metadata (name, vibe, emoji) |
| `HEARTBEAT.md` | Heartbeat checklist for Agent 3 (30-min cycles, max 2 posts/week) |
| `TOOLS.md` | Environment reference: Discord channels, data paths, provider routing |
| `CLAUDE.md` | Developer navigation guide for working on this project |
| `prompts/system/SOUL.md` | Reference copy of SOUL.md for spec compliance |
| `prompts/system/AGENTS.md` | Agent definitions spec (extraction, weather, heartbeat, weekly summary, onboarding, design approval, project review, command parser) |

### Database

- `timeline_events` table created in Postgres with pgvector `vector(768)` column
- IVFFlat index on embedding column for cosine similarity search
- Indexes on `subject_id`, `author`, `event_type`, `created_at`

### Extraction agent

- Runs every 5 minutes via WSL2 cron: `*/5 * * * * /usr/bin/python3 /mnt/c/Openclaw/slarti/scripts/extraction_agent.py`
- Session path: `/mnt/c/Users/Chris/.openclaw/agents/slarti/sessions/*.jsonl`
- Skips already-processed sessions (tracked in `data/system/processed_sessions.json`)
- Python packages installed in WSL2: `anthropic`, `psycopg2-binary`, `google-generativeai`, `python-dotenv`

### Key discovery

OpenClaw session files are JSONL with `type: "message"` events. User messages include Discord metadata block (`sender_id`, `sender`, `timestamp`) as untrusted metadata in the message content. The extraction agent parses this to resolve author attribution.

---

## Phase 10 — Daily Weather Agent

**Completed:** 2026-03-29

### What was built

| File | Purpose |
|---|---|
| `scripts/weather_agent.py` | Weather agent — fetches NWS hourly forecast, computes daily summary, posts frost/heat advisories to #garden-log via Claude Haiku |
| `data/system/weather_today.json` | Today's weather summary (date, high/low, heat index, precip, advisories) — overwritten daily |
| `data/system/weather_week.json` | 7-day summary (high/low/precip per day) — overwritten daily |

### How it works

1. Two-step NWS API fetch: `points/{lat},{lng}` → `forecastHourly` URL → hourly periods
2. Filters periods to today's date (Central time) → computes high/low/heat index/precip/wind
3. **Rothfusz regression** for heat index (NWS standard formula with low- and high-humidity adjustments)
4. Advisory thresholds:
   - `frost` — temp_low ≤ 36°F (always, any month)
   - `heat_contextual` — heat_index_max 85–89°F (growing season May–Oct only, or `--force`)
   - `heat_high_risk` — heat_index_max ≥ 90°F (growing season only, or `--force`)
5. Advisory message generated by Claude Haiku — Slarti's voice, not a canned template
6. Posts to #garden-log via Discord REST API (bot token, dynamic channel ID lookup)
7. Writes `weather_today.json` + `weather_week.json` atomically (temp file → `os.replace()`)
8. Updates `health_status.json` → `last_weather_refresh_at`

### WSL2 cron (add manually)

```bash
# Run in WSL2:
(crontab -l 2>/dev/null; echo "0 6 * * * /usr/bin/python3 /mnt/c/Openclaw/slarti/scripts/weather_agent.py >> /mnt/c/Openclaw/slarti/logs/daily/weather_agent.log 2>&1") | crontab -
```

Verify: `crontab -l` — should show the `0 6 * * *` entry.

If cron doesn't persist across WSL2 restarts, add to `/etc/wsl.conf`:
```ini
[boot]
command = service cron start
```

### Testing

```bash
# Dry run — no writes, no Discord post
python3 scripts/weather_agent.py --dry-run

# Heat advisory test
python3 scripts/weather_agent.py --test-heat 92 --force --dry-run

# Frost advisory test
python3 scripts/weather_agent.py --test-frost 32 --dry-run

# Off-season suppression (March — should suppress heat advisory)
python3 scripts/weather_agent.py --test-heat 92 --dry-run
```

### Key discoveries / notes

- NWS API requires a descriptive `User-Agent` header or it returns 403
- Heat index formula only applies when temp ≥ 80°F — returns raw temp below that
- Discord bot token REST API posts work for all channels without per-channel webhooks
- Claude Haiku used for advisory messages (cost ~$0.0001/call vs Sonnet)

---

## Phase 11 — Image Modes A/B/C/D

**Completed:** 2026-03-29

### What was built

| File | Purpose |
|---|---|
| `scripts/photo_agent.py` | Downloads Discord photo attachments, extracts EXIF via MarkItDown, writes `data/photos/metadata/[photo-id].json` |
| `scripts/image_agent.py` | Generates mockup/concept images via Gemini `gemini-3-1-flash-image`, falls back to DALL-E 3, posts to Discord |
| `data/photos/metadata/.gitkeep` | Keeps metadata directory tracked in git |
| `AGENTS.md` | Expanded Mode A/B/C/D instructions: confidence scoring, vantage point prompting, approval detection, plant ID workflow |
| `scripts/extraction_agent.py` | Now detects image attachments in sessions and triggers photo_agent.py |

### Mode summary

| Mode | What happens |
|---|---|
| A | Claude analyzes photo directly (multimodal), structured Observed/Inferred/Recommended with confidence scores. Vantage point asked once and stored. |
| B | Claude acknowledges photo + emits `[MOCKUP_REQUEST]` marker → image_agent.py generates visual via Gemini → posted to Discord |
| C | Claude iterates on design description → emits `[DESIGN_REQUEST]` → concept visual generated. Approval requires ≥0.85 confidence + clear intent. |
| D | Claude IDs plant with dual confidence (ID confidence + care advice confidence). Cross-references `data/plants/`, emits `[PLANT_LOOKUP]` if local confidence < 0.70 |

### Key decisions

- **"Nano Banana Pro"** was the spec's code name for Google's Gemini image generation model — `gemini-3-1-flash-image`
- **Photo analysis** handled by Claude directly (multimodal via OpenClaw passthrough) — no separate Gemini call needed for Modes A/D
- **Image generation** (Modes B/C) handled by `image_agent.py` as a standalone script triggered by structured markers in Claude's responses
- **DALL-E 3 fallback** via `--force-fallback` flag; kicks in automatically if Gemini image gen fails
- **Vantage point** stored in bed entity `data/beds/[bed-id].json` — asked once, never again

### Testing

```bash
# Mode B dry run
python3 scripts/image_agent.py --mode b --photo data/photos/raw/test.jpg \
    --request "add a trellis on the north side" --dry-run

# Mode C dry run
python3 scripts/image_agent.py --mode c \
    --description "raised bed with tomatoes and basil" --dry-run

# DALL-E fallback test
python3 scripts/image_agent.py --mode c --description "test" --force-fallback --dry-run

# Photo metadata dry run
python3 scripts/photo_agent.py --photo-url https://example.com/test.jpg \
    --session-id test-session --author emily --dry-run
```

---

## Phase 12 — Voice Notes, Plant Database, Weekly Summary

**Completed:** 2026-03-30

### What was built

| File | Purpose |
|---|---|
| `scripts/voice_session_writer.py` | Mode V handler — transcribes audio via MarkItDown, saves voice session JSON, posts to #garden-log, triggers extraction |
| `scripts/weekly_summary_agent.py` | Agent 4 — Sunday 6 PM narrative summary to #garden-log via Claude Sonnet |
| `scripts/populate_plants.py` | Seeds data/plants/ from hand-curated scripts/plant_sources/ JSON files |
| `scripts/plant_sources/*.json` | 10 seed plants: tomato-brandywine, basil-genovese, marigold-french, pepper-bell, zucchini, cucumber, thyme, oregano, lavender, black-eyed-susan |
| `data/plants/*.json` | Seeded plant database (10 plants, all Zone 6b grounded) |
| `data/voice_sessions/2026/` | Voice session storage directory |
| `prompts/system/voice_session_mode.md` | Mode V context — tells Claude how to treat voice transcripts |
| `prompts/system/weekly_summary_mode.md` | Agent 4 context — tone, format, what to include in weekly summaries |
| `scripts/extraction_agent.py` | Added `process_voice_session()` and `--voice-session PATH` flag |

### Mode V flow

1. Audio file dropped in Discord → OpenClaw detects audio attachment
2. `voice_session_writer.py --audio-url [url] --author [emily/christopher]` called
3. MarkItDown transcribes audio → raw transcript text
4. Session JSON saved to `data/voice_sessions/2026/session-YYYYMMDD-HHMMSS-[uuid6].json` with `extraction_status: 'pending'`
5. Discord post: "Voice note from Emily received — transcribed and saved. (N words)"
6. `extraction_agent.py --voice-session [path]` triggered in background
7. Facts extracted, pgvector stored, session updated to `extraction_status: 'complete'`

### Weekly summary flow

1. Runs Sunday 6 PM via WSL2 cron
2. Reads: past 7 days of events, beds, open tasks, treatment follow-ups, weather_week.json
3. Builds prompt with SOUL.md + weekly_summary_mode.md
4. Claude Sonnet writes 300–500 word narrative in Slarti's voice
5. Posts to #garden-log (chunked if >1900 chars)
6. Updates health_status.json → `last_weekly_summary_at`
7. Fallback if Claude fails: "Slarti is taking the week off — summary coming next Sunday."

### WSL2 cron entry (add manually)

```bash
(crontab -l 2>/dev/null; echo "0 18 * * 0 /usr/bin/python3 /mnt/c/Openclaw/slarti/scripts/weekly_summary_agent.py >> /mnt/c/Openclaw/slarti/logs/daily/weekly_summary.log 2>&1") | crontab -
```

### Plant database

10 hand-curated Zone 6b plants in `scripts/plant_sources/`. Add more by dropping a new JSON in that directory and running `python3 scripts/populate_plants.py`. All entries validated against required schema fields before copying to `data/plants/`.

### OpenClaw gateway stability fix

Gateway was dying silently (no auto-restart). Fixed with `~/.openclaw/gateway_restart_loop.cmd` — a restart loop wrapper called by the startup shortcut using `/k` instead of `/c`. The minimized CMD window stays alive and restarts the gateway within 10 seconds if it crashes.

---

## Phase 13 — Voice PWA

**Completed:** 2026-03-30

### What was built

| File | Purpose |
|---|---|
| `scripts/voice_webhook.py` | FastAPI server on port 8080 — serves PWA frontend, handles `/transcribe`, `/speak`, `/save-session`, `/health` endpoints |
| `pwa/index.html` | Complete PWA frontend — VAD, noise calibration, single tap-to-begin for iOS, orb state machine |
| `config/voice_profile.json` | TTS config: provider, model, voice, instructions — hot-reloadable without server restart |
| `config/ssl/cert.pem` + `key.pem` | Self-signed SSL cert with IP SAN (git-ignored) — required for iOS getUserMedia over LAN |
| `scripts/test_voice.sh` | Auto-incrementing test script for curl-based endpoint verification (saves to Desktop) |

### Mode P flow

1. iPhone opens `https://[windows-ip]:8080?author=christopher`
2. Tap BEGIN — iOS gesture unlocks mic and audio playback (`<audio playsinline>`)
3. VAD calibrates 30-frame noise floor; speech threshold = floor × 2.2 (minimum 0.010)
4. Speech detected → MediaRecorder starts → 1.4s of silence → audio sent to `/transcribe`
5. Whisper (`whisper-1`, `language='en'`) transcribes → text sent to `/speak`
6. Claude Sonnet responds (hot context injected on first turn) → OpenAI TTS streams mp3 back
7. End session → POST to `/save-session` → JSON written to `data/voice_sessions/2026/` → `extraction_agent.py` triggered in background → Discord post to #garden-log

### Starting the voice server (WSL2)

```bash
# Foreground (with output)
python3 /mnt/c/Openclaw/slarti/scripts/voice_webhook.py

# Background with log
nohup python3 /mnt/c/Openclaw/slarti/scripts/voice_webhook.py \
  > /mnt/c/Openclaw/slarti/logs/daily/voice_webhook.log 2>&1 &
```

### Key decisions and discoveries

- **ElevenLabs dropped** — quota-based subscription; replaced with OpenAI gpt-4o-mini-tts (pay-as-you-go, fractions of a cent per response)
- **Web Speech API dropped** — iOS Safari requires HTTPS for SpeechRecognition; switched to MediaRecorder + server-side Whisper entirely
- **Web search removed from voice** — Anthropic tool use calls take 10–30 seconds; PWA fetch timed out silently. Plant DB loaded directly into hot context instead (fast and sufficient for garden questions)
- **`language='en'` in Whisper** — Without it, Whisper hallucinates Chinese/Korean text from background noise during silence
- **`<audio playsinline>` not Web Audio API** — Web Audio API routes through the ambient audio channel (respects iPhone silent switch). The `<audio>` element routes through AVAudioSessionCategoryPlayback — plays even when the phone is on vibrate
- **Self-signed SSL with openssl IP SAN** — iOS Safari refuses `getUserMedia` on non-HTTPS origins. Hostname-based certs don't work for LAN IPs; the cert must have a `subjectAltName: IP:192.168.x.x` entry
- **netsh portproxy** — WSL2 network is isolated; iOS on the same LAN hits the Windows IP, not the WSL2 IP. `netsh interface portproxy add v4tov4` forwards traffic transparently
- **HTTP header sanitization** — h11 (uvicorn's HTTP library) enforces strict header value validation. Slarti's text responses go into `X-Slarti-Response` — any control characters (including newlines) cause a 500. Resolved with a whitelist filter: `0x20–0x7e or 0x80–0xff`, everything else replaced with a space

---

## Phase 9 — !setup Onboarding Wizard

**Completed:** 2026-03-30

### What was built

| File | Purpose |
|---|---|
| `prompts/system/onboarding_mode.md` | Wizard instructions for Claude — one-question-at-a-time bed setup, marker format |
| `scripts/onboarding_writer.py` | Parses `[ONBOARDING_BED: {...}]` markers from session JSONL, writes bed JSON files, updates onboarding_state.json, triggers garden.md regen |
| `AGENTS.md` | Expanded `!setup` and `!setup continue` command handling |
| `scripts/extraction_agent.py` | Detects onboarding markers in processed sessions → triggers onboarding_writer.py |
| `data/system/onboarding_state.json` | Expanded schema: `beds_completed`, `current_bed_draft`, `last_updated_at` |

### How it works

1. Emily types `!setup` in any Discord channel
2. Claude (via OpenClaw) conducts the wizard using `onboarding_mode.md` as instructions:
   - Checks `docs/garden.md` to see what's already documented
   - Asks one question at a time: name/aliases, size, sun, plants, issues, photo angle
   - After Emily confirms a bed: emits `[ONBOARDING_BED: {...}]` as a single-line compact JSON marker (invisible to Emily — just in the session JSONL)
   - When done for today: emits `[ONBOARDING_PAUSE]`
3. `extraction_agent.py` (running every 5 min via cron) picks up the session and detects the marker
4. Triggers `onboarding_writer.py` in background
5. `onboarding_writer.py`:
   - Parses the JSON payload using balanced brace counting (handles single or multi-line)
   - Auto-assigns next bed ID (`bed-01`, `bed-02`, etc.)
   - Writes full bed entity to `data/beds/bed-XX.json` (schema matches Slarti_v5_2.md)
   - Updates `data/system/onboarding_state.json`
   - Calls `extraction_agent.py --regen-garden` to regenerate `docs/garden.md`
   - Posts to Discord #garden-log: "Bed on record — [name] (bed-XX). [plants]."

### Resume

`!setup continue` → Claude reads `docs/garden.md` (which is in hot context) to know which beds are already documented, then picks up from there.

### Testing

```bash
# Dry run — parse markers from a session without writing
python3 scripts/onboarding_writer.py --session ~/.openclaw/agents/slarti/sessions/XXXX.jsonl --dry-run

# Manual trigger after a !setup session
python3 scripts/onboarding_writer.py --session <path>

# Check output
ls data/beds/
cat data/beds/bed-01.json
cat docs/garden.md
```

---

## Plant Database Expansion — 2026-03-30

**Goal:** Expand the plant database from 10 manual entries to a comprehensive, botanically accurate reference covering vegetables, herbs, berries, fruit trees, flowers, and shrubs.

### What was built

**`scripts/plant_lookup.py`** — NRCS CSV search utility
- Reads `docs/Missouri_NRCS_csv.txt` (12,904-line Missouri PLANTS registry)
- Searches by common name, scientific name, or USDA symbol
- Groups primary entries with synonyms
- Used to verify all scientific names and USDA symbols before creating plant files

**51 new plant source files** in `scripts/plant_sources/`

| Category | Count | Examples |
|---|---|---|
| Vegetables | 9 new | lettuce, spinach, broccoli, kale, eggplant, butternut squash, sweet potato, asparagus, pumpkin |
| Herbs | 8 | rosemary, sage, spearmint, chives, dill, cilantro, parsley, fennel |
| Berries / small fruit | 6 | strawberry (×2), raspberry, blackberry, blueberry, cantaloupe |
| Fruit trees | 5 | apple, peach, pear, tart cherry, plum |
| Flowers | 12 | coneflower, bee balm, daylily, yarrow, zinnia, sunflower, peony, hosta, daffodil... |
| Shrubs | 5 | hydrangea (Annabelle), viburnum (arrowwood), spirea, butterfly bush, Knock Out rose |

**10 existing plant source files updated** — added `category` and `usda_symbol` to all.

### New optional schema fields (no script changes required)

```json
"category": "vegetable | herb | fruit | fruit_tree | berry | flower | shrub | bulb",
"usda_symbol": "ECPU",
"bloom_season": "June–September",
"mature_height_ft": 3,
"mature_spread_ft": 2,
"years_to_first_harvest": 2,
"deer_resistant": true,
"native_to_missouri": true
```

### Key decisions

- **NRCS CSV as authoritative source** — all scientific names and USDA symbols verified against `docs/Missouri_NRCS_csv.txt` before writing. No guesses.
- **Non-native cultivars use `usda_symbol: null`** — lavender, oregano, thyme, pepper, eggplant, rosemary, sage, parsley, plum, butterfly bush, zinnia, sedum, peony, hosta, coreopsis are cultivated imports not in the Missouri registry
- **Missouri natives flagged** — coneflower, bee balm, arrowwood viburnum, black-eyed-susan, yarrow, smooth hydrangea all have `"native_to_missouri": true`
- **Zone 6b notes required** — every entry includes Farmington-specific planting dates, timing notes, and relevant warnings (e.g., peach frost risk, blueberry pH requirement, mint containment)

### Result

```bash
ls data/plants/ | wc -l   # 61
py scripts/populate_plants.py --dry-run  # 61 plants validated, 0 errors
```

---

## What's Left

All 13 phases are complete. Plant database is at 61 NRCS-referenced entries.

The remaining deferred item is **Emily's Discord ID** — still a placeholder in `config/discord_users.json`. Add it when Emily is ready to use the bot, then restart the OpenClaw gateway.
