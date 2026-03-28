# Slarti — Garden Companion AI

A family garden companion AI for Christopher and Emily in Farmington, Missouri (USDA Zone 6b). Slarti lives in Discord and responds by voice via a Siri Shortcut. It knows the garden, remembers what you've done, and talks like a knowledgeable friend — not a database.

**Orchestrator:** [OpenClaw](https://openclaw.ai) · **AI:** Claude Sonnet 4.6 · **Photos:** Gemini Flash · **Voice:** ElevenLabs

---

## Quick Start (after a reboot)

```bash
# In WSL2
/mnt/c/Openclaw/slarti/scripts/restart.sh
```

This starts: Docker (Postgres) → OpenClaw gateway → voice webhook → PWA server.

Or start pieces individually:

```bash
# Database
cd /mnt/c/Openclaw/slarti/db && docker compose up -d

# OpenClaw gateway (run in PowerShell or CMD)
openclaw gateway start

# Verify everything
openclaw gateway health
docker ps --filter "name=slarti_stack"
```

---

## Project Structure

```
slarti/
├── SOUL.md                    ← Slarti's personality (OpenClaw reads this)
├── AGENTS.md                  ← Behavioral rules: modes, memory, heartbeat
├── USER.md                    ← Emily and Christopher context
├── MEMORY.md                  ← Long-term memory (grows over time)
├── Slarti_v5_2.md             ← Master spec (architecture reference)
├── CHRISTOPHER_BUILD_GUIDE.md ← Phase-by-phase build walkthrough
├── config/                    ← app_config.json, provider_policy.json, etc.
├── data/                      ← All garden data (beds, plants, projects, events)
├── db/                        ← Docker Compose for Postgres + pgvector
├── docs/                      ← Reference docs and historical notes
├── prompts/system/            ← Mode-specific Claude prompts (Phases 11–13)
├── scripts/                   ← git_push.sh, restart.sh, voice_webhook.py
├── logs/daily/                ← Runtime logs
└── pwa/                       ← Voice PWA (Phase 13)
```

---

## Key Config Files

| File | Purpose |
|---|---|
| `.env` | API keys — never committed |
| `config/app_config.json` | App settings, model name, NWS coordinates |
| `config/discord_users.json` | Maps Discord user IDs → emily/christopher |
| `~/.openclaw/openclaw.json` | OpenClaw gateway config (outside this repo) |

---

## Build Status

| Phase | Description | Status |
|---|---|---|
| 1 | Folder structure, SOUL.md, config files | ✅ Done |
| 2 | Git, .gitignore, nightly push script | ✅ Done |
| 3 | Docker — Postgres + pgvector | ✅ Done |
| 4 | MarkItDown + ffmpeg | ✅ Done |
| 5 | OpenClaw gateway | ✅ Done |
| 6 | Claude API test — Slarti responds in character | ⏳ Next |
| 7 | Discord bot + 7 channels | ⏳ Pending |
| 8 | Memory layer (pgvector + extraction) | ⏳ Pending |
| 9 | Onboarding (`!setup`) | ⏳ Pending |
| 10 | Daily weather agent | ⏳ Pending |
| 11 | Image modes A/B/C/D | ⏳ Pending |
| 12 | Voice notes, plant DB, weekly summary | ⏳ Pending |
| 13 | Voice PWA (ElevenLabs + Siri Shortcut) | ⏳ Pending |

---

## Commit Convention

```
slarti: YYYY-MM-DD brief description
```
