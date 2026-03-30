# Slarti — Garden Companion AI

A family garden companion AI for Christopher and Emily in Farmington, Missouri (USDA Zone 6b). Slarti lives in Discord, knows the garden, remembers what you've planted and decided, and talks like a knowledgeable friend — not a database or a dashboard.

**Orchestrator:** [OpenClaw](https://openclaw.ai) · **AI:** Claude Sonnet 4.6 · **Photos:** Gemini Flash · **Voice:** ElevenLabs · **DB:** Postgres + pgvector

---

## What Slarti Does

Drop a photo in Discord and Slarti will analyze what it sees, flag anything wrong, and suggest what to do next. Describe a bed redesign in text and it will sketch a plan. Ask about a plant and it draws on Zone 6b-specific knowledge. Each morning at 6 AM it checks the weather and posts a frost or heat advisory to #garden-log when conditions matter. Everything gets remembered — plants, decisions, observations — and retrieved via semantic search when relevant.

---

## Interaction Modes

| Mode | Trigger | What happens |
|---|---|---|
| A | Photo only | Gemini analyzes the photo, Slarti responds with observations |
| B | Photo + change/mockup request | Analysis + gemini-3.1-flash-image-preview mockup |
| C | Text design description (no photo) | Written plan or layout recommendation |
| D | Plant ID or unfamiliar subject in photo | Plant identification + Zone 6b advice |
| E | Casual conversation | Normal chat, garden questions, recommendations |
| V | Audio file dropped in Discord | MarkItDown transcription → handled as voice note |
| P | Siri Shortcut → voice PWA | Live voice session, synced to Discord after |
| COMMAND | `!` prefix | Handled by command parser, bypasses mode classification |

---

## Architecture

| Task | Provider | Model |
|---|---|---|
| Conversation, agents, summaries | Anthropic | Claude Sonnet 4.6 |
| Photo analysis | Google | Gemini Flash |
| Image generation (mockups) | Google | gemini-3.1-flash-image-preview |
| Embeddings (memory search) | Google | text-embedding-004 |
| Advisory messages | Anthropic | Claude Haiku |
| Image generation fallback | OpenAI | DALL-E 3 |
| Voice output | ElevenLabs | eleven_flash_v2_5 |
| File/audio conversion | Local | MarkItDown |

**Three-tier memory:** Hot (SOUL.md + garden.md + weather_today.json always loaded) · Warm (bed/plant entities on demand) · Cold (timeline events via pgvector cosine search)

---

## Build Status

| Phase | Description | Status |
|---|---|---|
| 1 | Folder structure, SOUL.md, config files | ✅ Done |
| 2 | Git, .gitignore, nightly push script | ✅ Done |
| 3 | Docker — Postgres + pgvector | ✅ Done |
| 4 | MarkItDown + ffmpeg | ✅ Done |
| 5 | OpenClaw gateway | ✅ Done |
| 6 | Claude API — Slarti responding in character | ✅ Done |
| 7 | Discord bot + 7 channels | ✅ Done |
| 8 | Memory layer (pgvector + extraction agent) | ✅ Done |
| 9 | Onboarding (`!setup`) | ⏳ Deferred |
| 10 | Daily weather agent (NWS + frost/heat advisories) | ✅ Done |
| 11 | Image modes A/B/C/D | ✅ Done |
| 12 | Voice notes, plant DB, weekly summary | ✅ Done |
| 13 | Voice PWA (ElevenLabs + Siri Shortcut) | ⏳ Pending |

---

## Quick Start (after a reboot)

```bash
# In WSL2
/mnt/c/Openclaw/slarti/scripts/restart.sh
```

Or start pieces individually:

```bash
# Database (WSL2)
cd /mnt/c/Openclaw/slarti/db && docker compose up -d

# OpenClaw gateway (PowerShell or CMD)
openclaw gateway start

# Verify
openclaw gateway health
docker ps --filter "name=slarti_stack"
```

Scheduled agents run automatically via WSL2 cron:
- **Every 5 min** — Extraction agent → processes new sessions into memory
- **6:00 AM daily** — Weather agent → `#garden-log` advisory if frost or heat threshold
- **Sunday 6:00 PM** — Weekly summary → narrative digest to `#garden-log`

---

## Project Structure

```
slarti/
├── SOUL.md                    ← Slarti's personality (OpenClaw reads this)
├── AGENTS.md                  ← Behavioral rules: modes, memory, heartbeat
├── USER.md                    ← Emily and Christopher context
├── MEMORY.md                  ← Long-term memory (grows over time)
├── Slarti_v5_2.md             ← Master spec (Parts 1–15)
├── CHRISTOPHER_BUILD_GUIDE.md ← Phase-by-phase build walkthrough
├── config/                    ← app_config.json, provider_policy.json, discord_users.json
├── data/                      ← All garden data (beds, plants, projects, events, system)
├── db/                        ← Docker Compose for Postgres + pgvector
├── docs/                      ← BUILD_LOG.md, garden.md, OPENCLAW_INSTRUCTIONS.md
├── scripts/                   ← weather_agent.py, extraction_agent.py, weekly_summary_agent.py, voice_session_writer.py, populate_plants.py, git_push.sh, restart.sh
├── logs/daily/                ← Runtime logs (git-ignored)
└── pwa/                       ← Voice PWA (Phase 13)
```

---

## Key Config Files

| File | Purpose |
|---|---|
| `.env` | API keys — never committed |
| `config/app_config.json` | App settings, model name, NWS coordinates |
| `config/discord_users.json` | Maps Discord user IDs → emily/christopher |
| `~/.openclaw/openclaw.json` | OpenClaw gateway config (outside this repo — has auth token) |

---

## Commit Convention

```
slarti: YYYY-MM-DD brief description
```
