# TOOLS.md — Slarti Environment

## Location

- Address: Farmington, Missouri
- USDA Zone: 6b (primary) / 7a (influence in warm microclimates)
- NWS coordinates: 37.78, -90.42
- Last spring frost: late April / early May
- First fall frost: mid to late October
- Growing window: ~170 days

## Discord Server

- Guild: Slarti Garden
- Channels: #garden-chat, #garden-photos, #garden-design, #garden-log, #garden-builds, #plant-alerts, #admin-log
- Primary conversation channel: #garden-chat
- Author mapping: config/discord_users.json

Channel roles:
- #garden-chat — main conversation, all modes
- #garden-photos — photo drops (Modes A, B, D)
- #garden-design — design sessions (Mode C), approval detection
- #garden-log — weather advisories, voice note confirmations, weekly summary
- #garden-builds — build summaries after design approval (Agent 6)
- #plant-alerts — plant ID flags (Mode D, future use)
- #admin-log — system errors, health alerts, provider failures (output-only)

## Data Paths (WSL2)

- Workspace: /mnt/c/Openclaw/slarti
- Bed data: data/beds/
- Events/journal: data/events/2026/
- Projects: data/projects/
- Tasks: data/tasks/
- Plants: data/plants/  ← seeded via scripts/populate_plants.py
- Plant sources: scripts/plant_sources/  ← hand-curated JSON, source of truth
- Voice sessions: data/voice_sessions/2026/
- Photos (metadata only — images not tracked by Git): data/photos/metadata/
- Photo mockups: data/photos/mockups/
- System state: data/system/
- Garden summary: docs/garden.md
- Daily logs: logs/daily/

## Database

- Postgres + pgvector in Docker
- Container: slarti_stack-postgres-1
- Port: 5432
- DB: slarti / User: slarti
- Tables: timeline_events (embeddings), garden_entities

## Providers

- Conversation/extraction: Anthropic Claude Sonnet (primary)
- Photo analysis: Google Gemini 2.0 Flash
- Embeddings: Google text-embedding-004
- Image generation (mockups): Google gemini-3.1-flash-image-preview
- Image fallback: OpenAI DALL-E 3
- Voice output: OpenAI gpt-4o-mini-tts (pay-as-you-go)

## Scripts

| Script | Trigger | What it does |
|---|---|---|
| `scripts/extraction_agent.py` | cron every 5 min | Reads OpenClaw sessions → extracts facts → pgvector + JSON |
| `scripts/weather_agent.py` | cron 6 AM daily | NWS forecast → frost/heat advisories → #garden-log |
| `scripts/weekly_summary_agent.py` | cron Sunday 6 PM | Week's events → Claude narrative → #garden-log |
| `scripts/voice_session_writer.py` | on audio upload | Transcribes audio → saves voice session → triggers extraction |
| `scripts/voice_webhook.py` | manual / startup | FastAPI voice PWA server (port 8080) — serves pwa/index.html |
| `scripts/photo_agent.py` | on photo upload | Downloads photo → EXIF extraction → metadata JSON |
| `scripts/image_agent.py` | on MOCKUP/DESIGN marker | Gemini/DALL-E image generation → Discord post |
| `scripts/populate_plants.py` | manual | Validates + seeds data/plants/ from scripts/plant_sources/ |
| `scripts/discord_alert.py` | called by git_push.sh | Posts failure alerts to #admin-log via webhook |
| `scripts/markitdown_ingest.py` | manual | Converts audio/PDF/Office/images to Markdown |
| `scripts/git_push.sh` | cron 3 AM daily | pg_dump + git commit + push |
| `scripts/restart.sh` | manual | Restarts Docker + cron + verifies health |
| `scripts/test_voice.sh` | manual | Tests /transcribe and /speak endpoints, saves audio to Desktop |

## Voice PWA (Mode P)

- Server: `scripts/voice_webhook.py` (FastAPI, port 8080, HTTPS)
- Frontend: `pwa/index.html` — hands-free VAD, auto noise calibration, tap-once-to-begin
- TTS config: `config/voice_profile.json` — provider, model, voice, instructions
- SSL certs: `config/ssl/` (git-ignored — generate with openssl, IP SAN required for LAN)
- Voice sessions: `data/voice_sessions/2026/`
- Access from iPhone: `https://[windows-ip]:8080?author=christopher`

## Key Config Files

- config/app_config.json — app settings, model names, provider routing
- config/provider_policy.json — provider role restrictions
- config/confidence_thresholds.json — confidence gates for memory writes
- config/discord_users.json — Discord ID → author mapping
- config/voice_profile.json — TTS provider, model, voice, spoken instructions
- data/system/health_status.json — system health tracker
- data/system/write_log.json — all memory writes logged here
