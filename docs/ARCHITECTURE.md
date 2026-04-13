# Slarti — Systems Architecture

> A production AI companion running continuously on a home server, serving a two-person household in Farmington, Missouri. This document explains the system design — not just what components exist, but why each was chosen and what problems they solve.

---

## Overview

Slarti is a compound AI system, not a simple chatbot. It is built around the problem of **contextual continuity** — the gap between a generic AI assistant and one that genuinely knows a specific place, two specific people, and the decisions they've made over time.

The system integrates seven concurrent concerns: real-time conversation, scheduled intelligence, multi-modal input (text, photo, voice), semantic long-term memory, live weather intelligence, proactive monitoring, and graceful failure recovery. Each concern has its own agent or pipeline. They share a common data layer, a common context assembly protocol, and a common Discord output channel.

It is intentionally not a SaaS product, not a dashboard, and not a general-purpose assistant. Every architectural decision was made under the constraint that this system exists for one garden, in one place, for two people.

---

## AI Provider Routing

Four providers. Each chosen for a specific reason.

| Task | Provider | Model | Rationale |
|---|---|---|---|
| Conversation, agents, summaries | Anthropic | Claude Sonnet 4.6 | Best instruction-following and personality consistency for long-running sessions |
| Advisory messages (weather, heartbeat) | Anthropic | Claude Haiku 4.5 | ~0.0001¢/call for messages that don't need Sonnet's reasoning depth |
| Photo analysis (Modes A, D) | Anthropic | Claude Sonnet 4.6 (multimodal) | Native multimodal passthrough via OpenClaw — no extra API call |
| Image generation (Modes B, C) | Google | gemini-3.1-flash-image-preview | Only production model generating image+text in a single API call |
| Embeddings | Google | text-embedding-004 | 768-dimension vectors, cost-effective, strong semantic performance |
| Voice output | OpenAI | gpt-4o-mini-tts | Pay-as-you-go fractions of a cent per response; no ElevenLabs subscription required |
| Voice transcription | OpenAI | whisper-1 | Reliable, fast, minimal hallucination with explicit `language='en'` |
| Image generation fallback | OpenAI | DALL-E 3 | Kicks in automatically when Gemini image generation fails |
| File and audio conversion | Local | MarkItDown + ffmpeg | Zero-latency, no API cost, runs entirely on the home server |

**The subscription decision:** Voice is the only pay-as-you-go service that required careful provider selection. ElevenLabs uses quota-based pricing that would impose artificial limits on how much Emily and Christopher can talk to the system. OpenAI TTS charges per character — a typical Slarti response costs under $0.001. No cap, no subscription tier, no friction.

---

## Context Assembly

Every Claude call assembles context from three sources in a deterministic order.

### Hot context (always loaded)

- `SOUL.md` — Slarti's personality, values, and Zone 6b grounding rules
- `AGENTS.md` — All behavioral instructions: mode classification, memory write policy, command handling, heartbeat procedures, and live weather conditions
- Loaded by the OpenClaw gateway automatically on every request — no code required

### Warm context (loaded on demand)

- Bed definitions from `data/beds/` — loaded when a bed name appears in the query
- Plant records from `data/plants/` — loaded for plant-specific questions
- Active projects and tasks from `data/projects/` + `data/tasks/`
- Loaded via Claude's read tool inside the session

### Cold context (semantic retrieval)

- Every extracted event, observation, and decision from every past conversation
- Stored as 768-dimension vectors in pgvector (Postgres 16 + pgvector extension)
- Retrieved via cosine similarity when relevant to the current query
- Nothing important is ever lost — it just settles to cold tier

### The AGENTS.md injection discovery

**The most important architectural insight of the build:** OpenClaw only auto-loads `SOUL.md` and `AGENTS.md` into Claude's context. `USER.md`, `MEMORY.md`, and all other workspace files are not loaded.

This was discovered when weather data injected into `USER.md` was invisible to Claude, which fell back to tool calls. OpenClaw renders all tool calls as visible `<tool_call>` XML blocks in Discord. The fix: move all dynamic live data into `AGENTS.md` directly. `weather_agent.py` now appends a `## Live Conditions — Farmington, MO` section to the bottom of `AGENTS.md` on every run. `USER.md` remains as a human-readable reference document only.

**Rule:** Any data that Claude must access without a tool call must live in `AGENTS.md` or `SOUL.md`.

---

## The Memory Pipeline

Every conversation contributes to permanent long-term memory through a fully automated extraction pipeline.

```
Discord message
    │
    ▼
OpenClaw gateway
    │  Classifies interaction mode (A–P or COMMAND)
    │  Assembles hot + warm + cold context
    │  Calls Claude Sonnet 4.6
    │
    ▼
Claude response → Discord
    │
    ▼
Session saved as JSONL
    (~/.openclaw/agents/slarti/sessions/*.jsonl)
    │
    ▼
extraction_agent.py  (WSL2 cron, every 5 min)
    │  Reads new sessions not in processed_sessions.json
    │  Claude extracts structured events (type, subject, content, confidence)
    │  Confidence gate: <0.50 skip; 0.50–0.79 surface; ≥0.80 auto-save
    │
    ▼
Events embedded via text-embedding-004 (768 dimensions)
    │
    ▼
pgvector (timeline_events table)
    │  HNSW index on embedding column
    │  Indexes on subject_id, author, event_type, created_at
    │
    ▼
If event_type is BED_FACT or DECISION:
    garden.md regenerated from all bed + plant records
    (auto-updated summary always current in hot context)
```

**The Emily-wins rule:** All memory writes carry `author: 'emily' | 'christopher' | 'system'`. When Emily and Christopher have conflicting statements about the same subject, Emily's most recent statement silently supersedes Christopher's. This is enforced at the data layer — Claude doesn't resolve the conflict aloud, it just records the current truth.

---

## Scheduled Agents

Six agents run on automatic schedules. Together they make Slarti feel present even when no one is talking to it.

| Agent | Schedule | What it does | Output |
|---|---|---|---|
| `extraction_agent.py` | Every 5 min (WSL2 cron) | Reads new sessions, extracts facts, embeds to pgvector, regenerates garden.md | pgvector, data/events/, docs/garden.md |
| `heartbeat_agent.py` | Every 30 min (WSL2 cron) | 8-check proactive pipeline: emergency alerts, weather, treatment follow-ups, seasonal timing, stale observations | Discord #garden-chat (when warranted) |
| `weather_agent.py` | 3× daily: 6 AM, noon, 4 PM (Windows Task Scheduler) | NWS forecast + emergency alert check + AGENTS.md injection | weather_today.json, weather_alerts.json, AGENTS.md |
| `weekly_summary_agent.py` | Sundays 6 PM (WSL2 cron) | Reads week's events + weather + observations, writes 300–500 word narrative in Slarti's voice | Discord #garden-log |
| `gateway_watchdog.ps1` | Every 5 min (Windows Task Scheduler) | `openclaw gateway health` → restart if down → Discord alert to #admin-log | Process management |
| `git_push.sh` | 3 AM nightly (WSL2 cron) | pg_dump → rotate logs → git commit → git push → Discord alert on failure | GitHub, backups/ |

**Why split WSL2 cron and Windows Task Scheduler?** The gateway runs as a Windows process (not WSL2), so the watchdog must run in Windows context to call `openclaw gateway start`. Weather agents were moved to Task Scheduler for reliability — WSL2 cron requires the WSL2 instance to be awake.

---

## The Heartbeat: Two-Tier Proactive System

The heartbeat is the part of the system that makes Slarti feel like a friend who's paying attention, not a tool waiting for input.

**Design constraint:** Unsolicited messages from an AI feel intrusive if they're too frequent or not obviously useful. The system must post only when it has something genuinely worth saying.

### Tier 1 — Emergency (no post limit)

Triggered when `weather_alerts.json` contains a new critical NWS alert (tornado warning, severe thunderstorm warning, flash flood warning, hard freeze warning, etc.) not already in `health_status.json:posted_alert_ids`.

Behavior: Post immediately to `#garden-chat` with a concrete, specific garden or safety action. This tier has no frequency cap — a tornado warning at 2 AM deserves an immediate message regardless of whether we've already posted twice this week.

Deduplication: Alert IDs stored in `posted_alert_ids` — the same alert never triggers a second post.

### Tier 2 — Routine (2 posts/week cap)

Before every potential post: Claude Haiku evaluates whether the message passes the **friend test** — "Would a friend say this unprompted, or would it feel like nagging?" Only messages that pass are sent.

Eight checks in order:
1. Emergency NWS alerts (above)
2. Overnight weather events affecting the garden (hard frost, heavy rain, extreme heat)
3. Treatment follow-ups due (sprays, fertilizer, transplant checks)
4. Fabrication or build blockers (materials needed, weather windows closing)
5. Stale observations (bed not mentioned in >14 days during active season)
6. Design stalls (approval pending >7 days with no reply)
7. Seasonal timing windows (e.g., "garlic should be going in this week")
8. Bed photo freshness (beds active but not photographed in >30 days)

The 2/week counter resets every Sunday midnight via `health_status.json`.

---

## Weather Intelligence

Weather is not a feature — it is load-bearing context for every plant care recommendation, timing suggestion, and seasonal observation Slarti makes.

### NWS API integration

Two-step fetch: `api.weather.gov/points/{lat},{lng}` → forecast grid → `forecastHourly` URL → hourly periods for today.

- **Coordinates:** `37.68, -90.42` — the actual property, not the city center. NWS alerts are polygon-based; using the city center address would miss alerts whose polygon edge falls between the city center and the property.
- **Heat index:** Rothfusz regression formula (the NWS standard) with separate low-humidity and high-humidity adjustment coefficients. Raw temperature returned below 80°F.
- **Advisory thresholds:** Frost at ≤36°F (always). Heat advisories only during growing season (May–October) to avoid crying wolf in January.

### AGENTS.md injection

On every run, `weather_agent.py` writes a `## Live Conditions — Farmington, MO` section to the bottom of `AGENTS.md` with:
- Timestamp ("Last refreshed: 11:03 AM CDT")
- Date, forecast, high/low
- Heat index, precip chance, wind
- Current advisories
- Active NWS alerts

Claude reads this section as part of its loaded context — no tool call, no XML, no latency.

**Staleness awareness:** If the refresh time is more than 2 hours before now, Claude mentions it and suggests `!weather`. If the section is absent or shows the wrong date, Claude reports "Weather data temporarily unavailable" and does not fall back to tool calls.

### !weather command

Runs `weather_agent.py` via exec tool on demand, then reads the freshly written `weather_today.json`. Provides a live NWS reading at any time — useful between the scheduled 3× daily refreshes.

---

## Data Layer

### Atomic writes

Every JSON and Markdown file is written via `os.replace()`:
```python
tmp = str(target_path) + '.tmp'
with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2)
os.replace(tmp, target_path)
```
A partial write never leaves a corrupted file. If the process dies mid-write, the original file is untouched.

### Confidence gates

Memory writes are confidence-gated at the extraction layer:

| Confidence | Behavior |
|---|---|
| < 0.50 | Skip — not written |
| 0.50 – 0.79 | Surface to Discord for human confirmation before writing |
| ≥ 0.80 | Auto-save to pgvector and event store |

Blueprint dimensions always require explicit confirmation regardless of confidence score — wrong garden dimensions propagate to every future recommendation.

### pgvector schema

```sql
CREATE TABLE timeline_events (
    id           SERIAL PRIMARY KEY,
    event_type   TEXT NOT NULL,
    subject_id   TEXT,
    author       TEXT,
    content      TEXT NOT NULL,
    confidence   REAL,
    embedding    vector(768),
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ON timeline_events USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON timeline_events (subject_id);
CREATE INDEX ON timeline_events (author);
CREATE INDEX ON timeline_events (created_at DESC);
```

HNSW index chosen over IVFFlat because it works on empty tables — IVFFlat requires a minimum number of rows before the index can be built, which fails on a fresh install.

---

## Interaction Modes

Every incoming message is classified into one of eight modes before any model is called. Mode determines model, context, and output format.

| Mode | Trigger | Pipeline |
|---|---|---|
| **A** | Photo only | Claude multimodal analysis → Observed / Inferred / Recommended with confidence scores |
| **B** | Photo + change request | Mode A analysis + `[MOCKUP_REQUEST]` marker → `image_agent.py` → Gemini image generated + posted |
| **C** | Text design description | Written design plan → approval detection → `[DESIGN_REQUEST]` → concept visual generated |
| **D** | Plant ID or unfamiliar photo | Species identification + Zone 6b care advice; cross-references `data/plants/`, emits `[PLANT_LOOKUP]` if local confidence < 0.70 |
| **E** | Casual conversation | Full context, Slarti's voice, no structured output required |
| **V** | Audio file dropped in Discord | MarkItDown transcription → treated as voice note → session saved → extraction triggered |
| **P** | Voice PWA (iPhone) | VAD → MediaRecorder → Whisper STT → Claude Sonnet → OpenAI TTS → saved to voice_sessions/ |
| **COMMAND** | `!` prefix | Routed to command parser: `!status`, `!memory`, `!timeline`, `!setup`, `!weather` |

Mode classification is implemented as behavioral instructions in `AGENTS.md`, read by Claude on every request. It is not separate routing code — Claude decides the mode as part of its reasoning.

---

## Reliability Engineering

### Gateway watchdog

The OpenClaw gateway is a Node.js process. If it crashes, Slarti goes dark. `gateway_watchdog.ps1` runs every 5 minutes via Windows Task Scheduler:

1. `openclaw gateway health` — checks response time
2. If no response or error: `openclaw gateway start --daemon`
3. Posts alert to Discord `#admin-log` via webhook
4. Logs to `logs/daily/gateway_watchdog.log`

### Restart sequence (`restart.sh`)

Full system restart in a single command from WSL2:
1. Docker compose up (Postgres + pgvector)
2. Postgres health check — exits if fails
3. `init_db.py` — idempotent schema init
4. `weather_agent.py` — fresh conditions in AGENTS.md before gateway starts
5. `openclaw gateway start --daemon`
6. Voice webhook server (`nohup` background)
7. Voice webhook health check
8. Heartbeat agent dry-run verification
9. Environment variable check (warns if any API key is missing)

### Session management

OpenClaw sessions are JSONL files. Existing sessions cache the gateway's tool schema at the time they were created. After significant config changes, sessions are reset by renaming `.jsonl` → `.jsonl.reset.TIMESTAMP`. OpenClaw creates a fresh session on the next message, picking up the new tool configuration.

### Error alerting

`discord_alert.py` sends fatal errors from any agent to `#admin-log` via webhook. Used by `weather_agent.py`, `extraction_agent.py`, `git_push.sh`, and `gateway_watchdog.ps1`. Christopher gets an `@mention` after 3 consecutive git push failures.

---

## Zone 6b Grounding

Every model call — conversation, advisory, extraction, heartbeat, weekly summary — operates under non-negotiable Zone 6b constraints:

- **USDA Zone 6b** (−5°F to 0°F minimum winter temperature) — Farmington was reclassified from 6a in the USDA's 2023 revision
- **Last spring frost:** late April / early May
- **First fall frost:** mid–late October
- **Humidity modifier:** Missouri summers are humid — all heat recommendations use heat index, not raw temperature
- **Missouri clay soil:** drainage and amendment recommendations always account for clay
- **Property coordinates:** 37.68°N, 90.42°W — used for NWS API weather and alert matching

Zone grounding is a design constraint, not a feature flag. It is encoded in `SOUL.md` (personality), `AGENTS.md` (behavioral rules), every plant record in `data/plants/` (via `zone_6b_notes` field), and the weather advisory thresholds in `weather_agent.py`.

---

*Built from scratch in 13 phases over April 2026. All 13 phases complete and running in production.*
