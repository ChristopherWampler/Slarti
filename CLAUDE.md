# Slarti — Developer Navigation

This is the development reference for Claude Code when working on the Slarti project.

**Slarti's personality and operational rules are in the workspace root files — not here:**
- `SOUL.md` — personality, voice, character
- `AGENTS.md` — mode classification, memory write rules, scheduled agents, commands
- `USER.md` — Emily and Christopher profiles

## Project Overview

Slarti is a family garden companion AI for Christopher and Emily Wampler in Farmington, Missouri (Zone 6b). Built on OpenClaw + Claude Sonnet. Discord-first interaction with voice support (Phase 13).

## Key Files

| File | Purpose |
|---|---|
| `Slarti_v5_2.md` | Authoritative spec — Parts 1–15 |
| `CHRISTOPHER_BUILD_GUIDE.md` | Phase-by-phase build commands |
| `docs/BUILD_LOG.md` | What was built, decisions made, current status |
| `docs/OPENCLAW_INSTRUCTIONS.md` | OpenClaw runtime operating rules |
| `~/.openclaw/openclaw.json` | Gateway config (outside git — has auth token) |

## Active Build Status

| Phase | Status |
|---|---|
| 1–5 | ✅ Complete |
| 6 | ✅ Complete (Claude API wired via OpenClaw) |
| 7 | ✅ Complete (Discord bot live, @Slarti responding) |
| 8 | ✅ Complete (pgvector schema, extraction agent, garden.md regeneration) |
| 9 | ⏳ Deferred — onboarding wizard (!setup) |
| 10 | ✅ Complete (NWS weather agent, frost/heat advisories to #garden-log) |
| 11 | ✅ Complete (photo_agent.py, image_agent.py, AGENTS.md) |
| 12 | ⏳ Next — voice notes (Mode V), plant database, weekly summary |
| 13 | Not started |

## Architecture Quick Reference

- **Gateway:** OpenClaw (Node.js) → `openclaw gateway start/stop/status`
- **Database:** Postgres 16 + pgvector in Docker → `cd db && docker compose up -d`
- **Extraction agent:** runs every 5 min via WSL2 cron → `scripts/extraction_agent.py`
- **Weather agent:** runs at 6 AM via WSL2 cron → `scripts/weather_agent.py`
- **Restart everything:** `bash scripts/restart.sh` (from WSL2)

## Git Commit Format

```
slarti: YYYY-MM-DD brief description
```

## Zone Grounding (non-negotiable for all advice)

Zone 6b, Farmington MO. Last spring frost: late April/early May. First fall frost: mid–late October. Always use heat index, not raw temperature.
