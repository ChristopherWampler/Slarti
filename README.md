# Slarti

*Named after Slartibartfast — a master designer who cared deeply about craft, shape, and the feel of places. He won an award for fjords. That level of care is what this garden deserves.*

Slarti is a family AI companion built for Christopher and Emily's garden in Farmington, Missouri (USDA Zone 6b). Not a dashboard. Not an app. A knowledgeable friend who knows the garden, remembers everything, and talks like a person — warm, curious, and grounded in the specific reality of this place.

It lives in Discord. It listens to voice notes. You can call it from your phone while your hands are in the dirt.

---

## What Slarti does

- **Analyzes garden photos** — drop a photo in Discord and Slarti describes what it sees, flags anything wrong, and recommends what to do next
- **Generates visual mockups** — describe a bed redesign in text or attach a photo with a change request; Slarti generates a concept image via Gemini
- **Identifies plants** — send a photo of something unfamiliar and Slarti identifies it with Zone 6b-specific care advice
- **Watches the weather** — every morning at 6 AM it checks the NWS forecast and posts a frost or heat advisory to #garden-log when conditions matter
- **Remembers everything** — plants, decisions, observations, and conversations are stored in pgvector and retrieved by semantic search when relevant
- **Talks on the phone** — voice PWA served over HTTPS; tap once, speak freely, hands-free VAD does the rest
- **Summarizes the week** — every Sunday at 6 PM it writes a warm narrative recap of the week's garden activity and posts it to #garden-log
- **Transcribes voice notes** — drop an audio file in Discord and it transcribes, extracts facts, and files them into memory automatically

---

## How it works

### Providers

| Task | Provider | Model |
|---|---|---|
| Conversation, agents, summaries | Anthropic | Claude Sonnet 4.6 |
| Photo analysis (Modes A, D) | Anthropic | Claude Sonnet 4.6 (multimodal) |
| Image generation (Modes B, C) | Google | gemini-3.1-flash-image-preview |
| Embeddings (memory search) | Google | text-embedding-004 |
| Advisory messages | Anthropic | Claude Haiku 4.5 |
| Image generation fallback | OpenAI | DALL-E 3 |
| Voice output | OpenAI | gpt-4o-mini-tts |
| Voice transcription | OpenAI | whisper-1 |
| File/audio conversion | Local | MarkItDown + ffmpeg |

Four providers. No subscription voice service — OpenAI TTS is pay-as-you-go (fractions of a cent per response).

### Memory

**Three tiers, assembled per-request:**

- **Hot** — `SOUL.md` + `garden.md` + `weather_today.json` + `weather_week.json` + plant database — always loaded
- **Warm** — bed and plant entities, loaded on demand by subject
- **Cold** — timeline events and conversation history stored in pgvector, retrieved by cosine similarity

Every observation, decision, and conversation is extracted into the timeline automatically. Nothing important falls through.

### Interaction modes

| Mode | Trigger | What happens |
|---|---|---|
| A | Photo only | Slarti analyzes the photo — observations, issues, recommendations with confidence scores |
| B | Photo + change request | Analysis + Gemini mockup image generated and posted to Discord |
| C | Text design description | Written design plan, then a concept visual on approval |
| D | Plant ID or unfamiliar photo | Species identification + Zone 6b care advice |
| E | Casual conversation | Just Slarti — warm, knowledgeable, unhurried |
| V | Audio file dropped in Discord | MarkItDown transcribes → treated as voice note → extracted to memory |
| P | Voice PWA on iPhone | Live voice session — VAD, Whisper STT, OpenAI TTS, saved to memory after |
| COMMAND | `!` prefix | Direct commands: `!status`, `!memory [subject]`, `!timeline [subject]`, `!setup` |

---

## Build status

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
| 9 | Onboarding (`!setup`) | ✅ Done |
| 10 | Daily weather agent (NWS + frost/heat advisories) | ✅ Done |
| 11 | Image modes A/B/C/D | ✅ Done |
| 12 | Voice notes, plant DB, weekly summary | ✅ Done |
| 13 | Voice PWA (hands-free, iPhone, OpenAI TTS) | ✅ Done |

---

## After a reboot

```bash
# WSL2 — restart everything
/mnt/c/Openclaw/slarti/scripts/restart.sh
```

Or individually:

```bash
# Database (WSL2)
cd /mnt/c/Openclaw/slarti/db && docker compose up -d

# OpenClaw gateway (PowerShell or CMD)
openclaw gateway start && openclaw gateway health

# Voice PWA (WSL2)
nohup python3 /mnt/c/Openclaw/slarti/scripts/voice_webhook.py \
  > /mnt/c/Openclaw/slarti/logs/daily/voice_webhook.log 2>&1 &
```

Scheduled agents run automatically via WSL2 cron — no manual start needed:
- **Every 5 min** — extraction agent → new sessions extracted into memory
- **6:00 AM daily** — weather agent → advisory to #garden-log if frost or heat threshold
- **Sunday 6:00 PM** — weekly summary → narrative digest to #garden-log

---

## Project structure

```
slarti/
├── SOUL.md                    ← Slarti's personality (loaded on every Claude call)
├── AGENTS.md                  ← Behavioral rules: modes, memory, heartbeat, commands
├── USER.md                    ← Emily and Christopher profiles
├── MEMORY.md                  ← Long-term memory (grows over time)
├── Slarti_v5_2.md             ← Master spec (Parts 1–15)
├── CHRISTOPHER_BUILD_GUIDE.md ← Phase-by-phase build walkthrough with exact commands
├── config/                    ← app_config.json, voice_profile.json, discord_users.json
├── data/                      ← All garden data: beds, plants, projects, events, system
├── db/                        ← Docker Compose for Postgres + pgvector
├── docs/                      ← BUILD_LOG.md, garden.md, OPENCLAW_INSTRUCTIONS.md
├── pwa/                       ← Voice PWA frontend (index.html)
├── scripts/                   ← All agents and utility scripts
│   ├── voice_webhook.py       ← FastAPI server for Mode P
│   ├── extraction_agent.py    ← Session → memory pipeline (runs every 5 min)
│   ├── weather_agent.py       ← Daily NWS forecast + advisories
│   ├── weekly_summary_agent.py← Sunday narrative summary
│   ├── voice_session_writer.py← Mode V audio transcription
│   ├── photo_agent.py         ← Photo metadata extraction
│   ├── image_agent.py         ← Gemini/DALL-E image generation
│   ├── populate_plants.py     ← Seeds data/plants/ from plant_sources/
│   └── git_push.sh            ← Nightly pg_dump + git commit + push
└── logs/daily/                ← Runtime logs (git-ignored)
```

---

## Configuration

| File | Purpose |
|---|---|
| `.env` | API keys — never committed |
| `config/app_config.json` | App settings, model name, NWS coordinates |
| `config/voice_profile.json` | TTS model, voice, spoken character instructions |
| `config/discord_users.json` | Maps Discord user IDs → emily / christopher |
| `~/.openclaw/openclaw.json` | OpenClaw gateway config (outside this repo — has auth token) |

---

## Commit convention

```
slarti: YYYY-MM-DD brief description
```

---

*Zone 6b. Farmington, Missouri. Late April frost, mid-October chill, humid Missouri summers. Every recommendation Slarti makes is grounded in this place.*
