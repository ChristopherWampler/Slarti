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

## What's Left (Phases 9–13)

| Phase | What it builds |
|---|---|
| 9 | `!setup` onboarding wizard — walks Emily through each garden bed one at a time |
| 10 | Daily weather agent (NWS API → `weather_today.json` → frost/heat advisories to #garden-log) |
| 11 | Photo modes A/B/C/D (Gemini analysis, Nano Banana mockups, EXIF extraction) |
| 12 | Voice notes (Mode V), plant database seeding, weekly Sunday summary |
| 13 | Voice PWA (ElevenLabs + FastAPI + Siri Shortcut on iPhone) |

See `CHRISTOPHER_BUILD_GUIDE.md` for detailed commands for each phase.
