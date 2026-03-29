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
- Channels: #garden-chat, #garden-photos, #garden-design, #garden-log, #plant-alerts, #weekly-summary, #admin-log
- Primary conversation channel: #garden-chat
- Author mapping: config/discord_users.json

## Data Paths (WSL2)

- Workspace: /mnt/c/Openclaw/slarti
- Bed data: data/beds/
- Events/journal: data/events/2026/
- Projects: data/projects/
- Plants: data/plants/
- Photos (metadata only — images not tracked by Git): data/photos/metadata/
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
- Image generation (mockups): Google (Nano Banana)
- Image fallback: OpenAI DALL-E 3
- Voice output: ElevenLabs

## Key Config Files

- config/app_config.json — app settings, provider routing
- config/provider_policy.json — provider role restrictions
- config/confidence_thresholds.json — confidence gates for memory writes
- config/discord_users.json — Discord ID → author mapping
- data/system/health_status.json — system health tracker
- data/system/write_log.json — all memory writes logged here
