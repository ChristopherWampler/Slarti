# Slarti

> *"He had spent the last five hundred years creating the coastlines of Norway, and had done so with such a sense of personal involvement that he had requested — and been awarded — a fjord. His attention to detail was legendary."*
>
> — Douglas Adams, *The Hitchhiker's Guide to the Galaxy*

---

Slarti is a family AI companion built for a real garden, in a real place, for two real people.

Not a dashboard. Not an app. A knowledgeable friend who knows the garden, remembers everything, and talks like a person — warm, curious, and grounded in the specific reality of one small corner of Farmington, Missouri.

It lives in Discord. It listens to voice notes. You can call it from your phone while your hands are in the dirt.

---

## The idea

Christopher and Emily grow things in Zone 6b — the borderland between mild and punishing Missouri winters, humid summers, clay soil, and a garden that changes every year. The usual problem with garden apps: they're generic. *Water 1 inch per week.* Sure. But this week? In this heat? After that late frost that hit the hardneck garlic? After Emily decided to move the squash?

Slarti knows the difference. It knows the garden, the decisions, the history. It was built to hold the knowledge that lives in the heads of two people who care about this specific patch of earth — and give it back when it's needed.

---

## What Slarti does

- **Analyzes garden photos** — drop a photo in Discord and Slarti describes what it sees, flags anything wrong, and recommends what to do. Confidence-scored. Specific to Zone 6b.
- **Generates visual mockups** — describe a bed redesign or attach a photo with a change request. Slarti generates a concept image via Google Gemini and posts it to Discord.
- **Identifies plants** — send a photo of something unfamiliar and Slarti identifies the species and gives Zone 6b-appropriate care advice for Farmington's climate.
- **Watches the weather** — queries the NWS forecast API 3× daily (6 AM, noon, 4 PM) and posts frost and heat advisories to `#garden-log` when conditions matter. Type `!weather` for a live reading anytime.
- **Watches for emergencies** — monitors active NWS alerts (tornado warnings, severe thunderstorm warnings, flash flood warnings, hard freeze warnings) and posts immediately to Discord with a concrete garden or safety action — no weekly post cap applies.
- **Remembers everything** — plants, decisions, observations, and conversations are extracted into a timeline, embedded with Google gemini-embedding-001, and stored in pgvector for semantic retrieval.
- **Talks on the phone** — a voice PWA served over HTTPS lets you tap once and speak freely. Voice Activity Detection handles the silence. Whisper transcribes. Claude responds. OpenAI TTS reads it back.
- **Interviews Emily about the garden** — `!setup` launches a conversational onboarding wizard that asks one question at a time and builds structured bed records from the conversation.
- **Summarizes the week** — every Sunday at 6:00 PM it reads the week's events, weather, and observations and writes a warm narrative recap to `#garden-log`.

---

## Interaction modes

Every message is classified into one of eight modes before Claude is called:

| Mode | Trigger | What happens |
|---|---|---|
| **A** | Photo only | Slarti analyzes — observations, issues, recommendations with confidence scores |
| **B** | Photo + change request | Analysis + Gemini mockup image generated and posted to Discord |
| **C** | Text design description | Written design plan, then concept visual on approval |
| **D** | Plant ID or unfamiliar photo | Species identification + Zone 6b care advice |
| **E** | Casual conversation | Just Slarti — warm, knowledgeable, unhurried |
| **V** | Audio file dropped in Discord | MarkItDown transcribes → treated as voice note → extracted to memory |
| **P** | Voice PWA on iPhone | Live voice session — VAD, Whisper STT, OpenAI TTS, saved to memory |
| **COMMAND** | `!` prefix | Direct commands: `!status`, `!memory [subject]`, `!timeline [subject]`, `!setup` |

---

## Architecture

### AI providers

Four providers. No subscriptions except Anthropic for conversation.

| Task | Provider | Model |
|---|---|---|
| Conversation, agents, summaries | Anthropic | Claude Sonnet 4.6 |
| Photo analysis (Modes A, D) | Anthropic | Claude Sonnet 4.6 (multimodal) |
| Image generation (Modes B, C) | Google | gemini-3.1-flash-image-preview |
| Embeddings (memory search) | Google | gemini-embedding-001 |
| Advisory messages | Anthropic | Claude Haiku 4.5 |
| Image generation fallback | OpenAI | DALL-E 3 |
| Voice output | OpenAI | gpt-4o-mini-tts |
| Voice transcription | OpenAI | whisper-1 |
| File and audio conversion | Local | MarkItDown + ffmpeg |

Voice is pay-as-you-go — fractions of a cent per response. No ElevenLabs subscription required.

### Memory

Three tiers, assembled fresh on every Claude call:

**Hot** — always loaded:
- `SOUL.md` — personality, values, what Slarti notices and cares about
- `docs/garden.md` — auto-generated garden state: all beds, plants, and known history
- `AGENTS.md` Live Conditions section — NWS conditions refreshed 3× daily, injected directly into Claude's system context
- `data/plants/` — 61 NRCS-referenced plant entries for Zone 6b

**Warm** — loaded on demand when the subject matches:
- Bed definitions and current state
- Plant records
- Active projects and tasks

**Cold** — semantic search via pgvector:
- Every extracted event, observation, and decision from every conversation
- Retrieved by cosine similarity when relevant to the current query
- Nothing ever truly leaves — it just settles deeper

Every conversation is processed by the extraction agent within 5 minutes. Facts, decisions, and observations are embedded and stored. Nothing important falls through.

### The extraction pipeline

```
Discord message
    → OpenClaw gateway classifies mode
    → Claude responds (with SOUL.md + garden.md + weather loaded)
    → Session saved as JSONL
    → extraction_agent.py (cron, every 5 min) reads new sessions
    → Claude extracts structured events
    → Events embedded → pgvector
    → garden.md regenerated if beds or plants changed
```

---

## The plant database

61 plant entries covering the full range of what a Zone 6b Missouri garden might grow.

All scientific names and USDA symbols are sourced from the [USDA PLANTS Database](https://plants.usda.gov/) Missouri state download (`Missouri_NRCS_csv.txt`, 12,904 records). Non-native cultivars that don't appear in the registry use `usda_symbol: null` with a note explaining why.

**Categories:**

| Category | Count | Examples |
|---|---|---|
| Vegetables | 16 | tomato, garlic, broccoli, sweet potato, asparagus, sweet corn... |
| Herbs | 10 | basil, thyme, dill, cilantro, fennel, spearmint... |
| Berries | 6 | June-bearing strawberry, red raspberry, thornless blackberry, highbush blueberry... |
| Fruit trees | 5 | apple, peach, pear, tart cherry, plum |
| Flowers | 13 | purple coneflower, bee balm, daylily, peony, hosta, daffodil, yarrow... |
| Shrubs | 5 | Annabelle hydrangea, arrowwood viburnum, Knock Out rose, bridal wreath spirea... |

Every entry includes:
- Farmington-specific planting dates and timing notes
- Zone 6b warnings (peach frost risk, blueberry pH requirement, mint containment, etc.)
- Common pests and companion plants
- Optional fields: `deer_resistant`, `native_to_missouri`, `bloom_season`, `years_to_first_harvest`, `mature_height_ft`

Native Missouri plants (coneflower, bee balm, arrowwood viburnum, black-eyed-susan, yarrow, smooth hydrangea) are flagged with `"native_to_missouri": true`.

---

## Project structure

```
slarti/
├── SOUL.md                        ← Slarti's personality (loaded on every Claude call)
├── AGENTS.md                      ← Behavioral rules: modes, memory, commands, heartbeat
├── USER.md                        ← Emily and Christopher profiles
├── MEMORY.md                      ← Long-term memory (grows over time)
├── Slarti_v5_2.md                 ← Master spec (Parts 1–15)
├── CHRISTOPHER_BUILD_GUIDE.md     ← Phase-by-phase build walkthrough with exact commands
├── config/
│   ├── app_config.json            ← Model names, NWS coordinates, port, thresholds
│   ├── voice_profile.json         ← TTS provider, model, voice, spoken character instructions
│   └── discord_users.json         ← Discord user ID → emily / christopher mapping
├── data/
│   ├── beds/                      ← Bed definitions and current state
│   ├── events/2026/               ← Timestamped extracted events from conversations
│   ├── photos/                    ← Garden photos (metadata only; images git-ignored)
│   ├── plants/                    ← 61 NRCS-referenced plant entries
│   ├── projects/ + tasks/         ← Active garden work
│   ├── voice_sessions/2026/       ← Voice note sessions with transcripts
│   └── system/                    ← health_status.json, weather, write_log, onboarding_state
├── db/
│   └── docker-compose.yml         ← Postgres 16 + pgvector
├── docs/
│   ├── BUILD_LOG.md               ← Full build history — decisions, gotchas, phase notes
│   ├── garden.md                  ← Auto-generated garden state (regenerated by extraction agent)
│   └── OPENCLAW_INSTRUCTIONS.md   ← Orchestrator runtime specification
├── pwa/
│   └── index.html                 ← Voice PWA frontend (hands-free, VAD, served over HTTPS)
├── scripts/
│   ├── voice_webhook.py           ← FastAPI server — Mode P, port 8080
│   ├── extraction_agent.py        ← Session → memory pipeline (runs every 5 min)
│   ├── heartbeat_agent.py         ← Proactive garden checks every 30 min (two-tier: emergency + routine)
│   ├── weather_agent.py           ← NWS forecast 3× daily + emergency alert monitoring
│   ├── weekly_summary_agent.py    ← Sunday evening narrative summary
│   ├── voice_session_writer.py    ← Mode V audio transcription
│   ├── onboarding_writer.py       ← !setup bed record builder
│   ├── photo_agent.py             ← Photo metadata and EXIF extraction
│   ├── image_agent.py             ← Gemini / DALL-E image generation
│   ├── pgvector_search.py         ← Semantic timeline search via cosine similarity
│   ├── discord_alert.py           ← Fatal error alerts to #admin-log
│   ├── init_db.py                 ← Idempotent pgvector schema init
│   ├── populate_plants.py         ← Seeds data/plants/ from plant_sources/
│   ├── plant_lookup.py            ← NRCS CSV search utility
│   ├── restart.sh                 ← Full system restart sequence
│   ├── gateway_watchdog.ps1       ← Windows Task Scheduler restart loop
│   └── git_push.sh                ← Nightly pg_dump + git commit + push
└── logs/daily/                    ← Runtime logs (git-ignored)
```

---

## Build

13 phases. All complete.

| Phase | Description |
|---|---|
| 1 | Folder structure, SOUL.md, config files |
| 2 | Git, .gitignore, nightly push script |
| 3 | Docker — Postgres + pgvector |
| 4 | MarkItDown + ffmpeg |
| 5 | OpenClaw gateway |
| 6 | Claude API — Slarti responding in character |
| 7 | Discord bot + 7 channels |
| 8 | Memory layer — pgvector schema, extraction agent, garden.md |
| 9 | Onboarding wizard — `!setup` conversational bed interviewer |
| 10 | Weather agent — NWS API, 3× daily refresh, emergency alerts |
| 11 | Image modes A/B/C/D — Gemini mockups, plant ID |
| 12 | Voice notes (Mode V), plant database, weekly summary |
| 13 | Voice PWA — FastAPI, iPhone, VAD, Whisper STT, OpenAI TTS |

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

Scheduled agents run via WSL2 cron — no manual start needed:

WSL2 cron:
```
*/5 * * * *   python3 .../extraction_agent.py
*/30 * * * *  python3 .../heartbeat_agent.py
0 18 * * 0    python3 .../weekly_summary_agent.py
0 3 * * *     bash .../git_push.sh
```

Windows Task Scheduler:
```
Weather Agent 0600   — daily 6:00 AM   → weather_agent.py
Weather Agent 1200   — daily 12:00 PM  → weather_agent.py
Weather Agent 1600   — daily 4:00 PM   → weather_agent.py
Gateway Watchdog     — every 5 min     → gateway_watchdog.ps1
```

---

## Configuration

| File | Purpose |
|---|---|
| `.env` | API keys — never committed |
| `config/app_config.json` | App settings, model names, NWS coordinates, port |
| `config/voice_profile.json` | TTS model, voice, spoken character instructions |
| `config/discord_users.json` | Maps Discord user IDs → emily / christopher |
| `~/.openclaw/openclaw.json` | OpenClaw gateway config — outside this repo, has auth token |

---

## Commit convention

```
slarti: YYYY-MM-DD brief description
```

---

*Zone 6b. Farmington, Missouri. Late April frost, mid-October chill, humid summers, Missouri clay. Every recommendation Slarti makes is grounded in this place — not in a generic algorithm, but in the specific reality of this garden, these people, and this season.*
