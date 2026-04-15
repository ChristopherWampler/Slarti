# Slarti — Christopher's Build Guide

> **Historical reference.** This guide was used during the initial build (Phases 1-13, completed March 2026). All phases are complete. For current operational reference, see `CLAUDE.md` and `AGENTS.md`.

The spec had all the detail. This had all the steps, in order, with what to actually do and when.

---

## Before You Start — Pre-Flight Checklist

Complete every item before touching Phase 1.

### Accounts to create or verify
- [ ] **Anthropic** — get API key at console.anthropic.com
- [ ] **Google AI Studio** — get API key at aistudio.google.com (covers Gemini, Nano Banana, embeddings)
- [ ] **OpenAI** — get API key at platform.openai.com (fallback only — DALL-E 3 and web search)
- [ ] **ElevenLabs** — create account at elevenlabs.io, get API key, note your monthly character budget
- [ ] **Discord** — have your account; enable Developer Mode (Settings → Advanced → Developer Mode)
- [ ] **GitHub** — have an account for the private backup repo

### Software to verify on your PC (Minisforum)
- [ ] Docker Desktop — running and green
- [ ] WSL2 with Ubuntu — open a terminal and run `wsl --version`
- [ ] Python 3.11+ in WSL2 — run `python3 --version` inside WSL2
- [ ] Git in WSL2 — run `git --version`

### Decisions to make before Phase 5
- [ ] Confirm your project drive: is the project going on `C:\`? If not, update all paths in `.env` and config files.
- [ ] Choose ElevenLabs voice (do this in Phase 13 — but browse the Voice Library now to save time later)

---

## Phase 1 — SOUL.md and Folder Structure
**Spec reference: Part 12, Phase 1 + Part 6 (folder layout) + Part 10 (config files)**

```bash
# In WSL2
mkdir -p /mnt/c/Openclaw/slarti
cd /mnt/c/Openclaw/slarti
# Create every subfolder from the layout in Part 6
mkdir -p prompts/system prompts/write_policies
mkdir -p data/beds data/projects data/events/2026 data/plants
mkdir -p data/voice_sessions/2026 data/photos/metadata data/photos/raw
mkdir -p data/photos/baselines data/photos/comparisons data/photos/mockups
mkdir -p data/system data/tasks
mkdir -p migrations/scripts migrations/backups
mkdir -p logs/daily exports/timeline_reports exports/weekend_plans
mkdir -p scripts pwa docs/runbooks config backups db
```

**What to create:**
1. `prompts/system/SOUL.md` — copy from Part 1 of the spec
2. `config/app_config.json` — copy from Part 10
3. `config/provider_policy.json` — copy from Part 10
4. `config/confidence_thresholds.json` — copy from Part 10
5. `.env.example` — copy from Part 10 (do NOT fill in real keys yet)
6. `config/discord_users.json` — copy from Part 10 (leave placeholder IDs for now)
7. `data/system/health_status.json` — copy from Part 5 (System Health schema)
8. `data/system/write_log.json` — start as `[]`
9. `data/system/onboarding_state.json` — start as `{"status": "not_started", "beds": []}`

**Verify Python 3.11+:**
```bash
python3 --version   # must show 3.11 or higher
```

✅ **Done when:** All folders exist. SOUL.md written. Config files created. Python 3.11+ confirmed.

---

## Phase 2 — Git Setup
**Spec reference: Part 12, Phase 2**

```bash
cd /mnt/c/Openclaw/slarti
git init
git config core.autocrlf input    # prevents CRLF issues on Windows editors
```

Create `.gitignore` — copy exact content from Part 6 of the spec.

**Verify photos are excluded:**
```bash
touch data/photos/raw/test_photo.jpg
git status                         # test_photo.jpg must NOT appear
rm data/photos/raw/test_photo.jpg
```

```bash
# Connect to GitHub
ssh-keygen -t ed25519 -C 'slarti@minisforum'
# Copy public key to GitHub → Settings → SSH Keys
git remote add origin git@github.com:[your-username]/slarti.git
git add -A && git commit -m 'slarti: initial setup v5.2'
git push -u origin main
```

**Set up nightly cron:**
```bash
chmod +x scripts/git_push.sh
crontab -e
# Add this line:
0 3 * * * /mnt/c/Openclaw/slarti/scripts/git_push.sh >> /mnt/c/Openclaw/slarti/logs/daily/git_push.log 2>&1
```

Copy `scripts/git_push.sh` from Part 6 of the spec. Also copy `scripts/discord_alert.py` from Part 6.

✅ **Done when:** `.gitignore` verified. Repo pushed to GitHub. Cron entry added.

---

## Phase 3 — Postgres + pgvector
**Spec reference: Part 12, Phase 3**

Create `db/docker-compose.yml` — copy from Phase 3 in the spec. Set a strong postgres password.

```bash
cd /mnt/c/Openclaw/slarti/db
docker compose up -d
# Wait ~15 seconds for container to start
docker exec -it db-postgres-1 psql -U slarti -d slarti -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

**Create your `.env` file now:**
```bash
cp .env.example .env
# Edit .env: fill in POSTGRES_PASSWORD with the password you chose above
# Leave all API keys blank for now — you'll add them in later phases
```

✅ **Done when:** Docker Desktop shows postgres container healthy. pgvector extension confirmed. `.env` created and not tracked by git (`git status` should not show `.env`).

---

## Phase 4 — MarkItDown
**Spec reference: Part 12, Phase 4**

```bash
# Confirm Python version first
python3 --version   # 3.11+ required

pip install markitdown[all] --break-system-packages

# Test with a voice file (find any .mp3 or .m4a on your PC)
python3 -c "from markitdown import MarkItDown; md=MarkItDown(enable_plugins=True); r=md.convert('/path/to/any.mp3'); print(r.text_content[:200])"

# Test EXIF with any photo
python3 -c "from markitdown import MarkItDown; md=MarkItDown(); r=md.convert('/path/to/any.jpg'); print(r.text_content)"
```

Create `scripts/markitdown_ingest.py` — copy from Part 7 of the spec.

✅ **Done when:** MarkItDown converts audio to text and extracts EXIF from photos. Both tested.

---

## Phase 5 — OpenClaw Orchestrator
**Spec reference: Part 12, Phase 5 (full install steps)**

```bash
pip install openclaw --break-system-packages
openclaw --version
```

**Create `config/openclaw.yaml`** — copy from Phase 5 in the spec.

**Create `config/mode_classifiers.yaml`** — copy from Phase 5 in the spec.

```bash
# Register agents
openclaw agents register --file prompts/system/AGENTS.md
openclaw agents list   # verify 8 agents appear

# Start and health check
openclaw start --config config/openclaw.yaml
openclaw health
```

**Test routing (no AI yet):**
```bash
openclaw test --mode E --message "How is the garden doing today?" --author christopher
```

**Update `scripts/restart.sh`** — copy from Part 14 of the spec (the OpenClaw command is already filled in).

✅ **Done when:** `openclaw health` reports green. Test message routes and logs. No API keys needed yet.

---

## Phase 6 — Claude API
**Spec reference: Part 12, Phase 6**

Add to `.env`:
```
ANTHROPIC_API_KEY=your_key_here
```

Configure OpenClaw to load SOUL.md as the system prompt and inject hot context (garden.md + weather_today.json) on every Claude call. Reference `config/openclaw.yaml` — the `soul_path` and `hot_context` fields handle this.

**Test:**
```bash
openclaw test --mode E --message "The tomatoes look a little droopy this morning." --author emily
```

Read the response. Does it sound like Slarti? Warm and grounded? Not robotic?

✅ **Done when:** Claude responds in Slarti's voice. Warm. Conversational. SOUL.md loading confirmed in logs.

---

## Phase 7 — Discord Bot
**Spec reference: Part 12, Phase 7**

1. Go to discord.com/developers/applications → New Application → name it **Slarti**
2. Bot section → Add Bot → copy token
3. Enable Privileged Gateway Intents: **Message Content Intent** ← critical, don't skip
4. Create private Discord server
5. Create all 7 channels (exact names matter for routing):
   - `#garden-chat` `#garden-design` `#garden-photos` `#garden-builds`
   - `#garden-builds` `#garden-log` `#admin-log`
6. OAuth2 → URL Generator → Scopes: `bot` → Permissions: `Send Messages`, `Read Message History`, `Attach Files`, `Mention Everyone` → invite to your server

**Fill in Discord IDs in `.env` and `config/discord_users.json`:**
```
DISCORD_BOT_TOKEN=your_bot_token
DISCORD_GUILD_ID=your_server_id
```

**Find your Discord user IDs** (Developer Mode must be on): right-click your username → Copy User ID. Do this for both your account and Emily's. Fill them into `config/discord_users.json`.

**Create `#admin-log` webhook:**
- `#admin-log` channel → Edit → Integrations → New Webhook → copy URL
- Add to `.env` as `DISCORD_ADMIN_WEBHOOK=`

**Test:**
- `@Slarti hello` in `#garden-chat` → Slarti should respond

✅ **Done when:** Slarti responds in `#garden-chat` in character. All 7 channels exist. Discord user IDs in config.

---

## Phase 8 — Memory Layer
**Spec reference: Part 12, Phase 8**

Add to `.env`:
```
GOOGLE_API_KEY=your_key_here
```

1. Configure OpenClaw to use Agent 2 (extraction) after every conversation
2. Configure gemini-embedding-001 for pgvector in OpenClaw config
3. Set up `garden.md` auto-regeneration trigger (fires after BED_FACT or DECISION extraction, and daily at 9 AM)

**Test:**
- Have a short conversation: *"I planted basil in the herb bed yesterday"*
- Start a new conversation: *"What did I plant recently?"*
- Slarti should recall the basil planting
- Check `data/events/2026/` — there should be a new timeline event JSON
- Check `data/system/write_log.json` — the write should be logged

✅ **Done when:** Facts persist across conversations. `garden.md` regenerates. Journal entry has correct author field.

---

## Phase 9 — Onboarding
**Spec reference: Part 12, Phase 9**

Walk the garden with Emily. In Discord on your phone:
```
@Slarti !setup
```

Answer questions one bed at a time. Stop whenever — progress saves automatically.

Emily can continue later:
```
@Slarti !setup continue
```

Upload a reference photo for each bed:
```
@Slarti this is the reference photo for [bed name]
```
(attach the photo to the message)

✅ **Done when:** At least one complete bed file in `data/beds/`. Slarti recognises a photo of that bed without being told which it is.

---

## Phase 10 — Weather Agent
**Spec reference: Part 12, Phase 10**

Test NWS API first:
```bash
curl 'https://api.weather.gov/points/37.68,-90.42'
```
Should return a JSON object with `properties.forecastHourly` URL.

Configure Agent 1 in OpenClaw to run at 6:00 AM daily.

**Simulate and verify:**
- Check `data/system/weather_today.json` after the first 6 AM run
- Or trigger manually: `openclaw agents run weather`
- Simulate heat index 92: Agent should post to `#garden-log`
- Simulate temp 33°F: Agent should post frost warning to `#garden-log`

✅ **Done when:** `weather_today.json` updated by 6:05 AM. Heat and frost thresholds both tested.

---

## Phase 11 — Image Modes A, B, C, D
**Spec reference: Part 12, Phase 11**

1. Configure Gemini 3 Flash for Modes A and D (uses `GOOGLE_API_KEY`)
2. Set up confidence flagging per observation (thresholds in `config/confidence_thresholds.json`)
3. Wire MarkItDown EXIF extraction on every photo upload
4. Set up vantage_point prompt: ask once per bed, store in bed JSON, never ask again
5. Configure Nano Banana Pro for Mode B (uses `GOOGLE_API_KEY`)
6. Add to `.env`: `OPENAI_API_KEY=` (for DALL-E 3 fallback and plant lookup)
7. Wire Mode C in `#garden-design` — use `prompts/system/mode_c_design_vision.md`
8. Wire design approval routing: confidence ≥ 0.85 → Agent 6 → `#garden-builds`

**Test each mode:**
- Mode A: upload a garden photo in `#garden-photos` → Slarti describes what it sees with confidence language
- Mode B: upload a photo in `#garden-photos` + *"show me with a trellis on the north side"* → mockup returned
- Mode C: describe a design in `#garden-design` (no photo) → concept visual + "Does this match your vision?"
- Mode D: *"@Slarti what is this plant?"* with a photo → identification with Zone 6b notes

✅ **Done when:** All four modes work. Mode C approval triggers build summary in `#garden-builds`.

---

## Phase 12 — Plant Database, Voice Notes, Weekly Summary
**Spec reference: Part 12, Phase 12**

**Voice notes (Mode V):**
- Drop any voice file in `#garden-chat` with `@Slarti`
- Should transcribe and extract facts just like a typed message

**Plant database seeding:**
```bash
# Download USDA PLANTS Database CSV from plants.usda.gov
# Place source files in scripts/plant_sources/
python3 scripts/markitdown_ingest.py
# Send each converted file to Claude with extraction prompt from Part 7
# Save resulting JSON arrays to data/plants/
python3 scripts/populate_plants.py   # validates all entries, flags missing zone_6b_notes
```

**Weekly summary:**
- Configure Agent 4 to run every Sunday at 6:00 PM
- Trigger manually: `openclaw agents run weekly-summary`
- Read the result in `#garden-log` — should sound like Slarti writing a note, not a report

✅ **Done when:** Voice notes processed. Plant queries return local results. Weekly summary reads warmly.

---

## Phase 13 — Voice Interface
**Spec reference: Part 12, Phase 13 + Part 15**

Add to `.env`:
```
ELEVENLABS_API_KEY=your_key_here
```

**Choose a voice:**
1. Go to elevenlabs.io/voice-library
2. Test **Jonathan** first (start here), then Alan Keith Scotland, then Nathaniel
3. Listen for: unhurried, warm, British, quietly intelligent
4. Click the voice → copy the voice ID from the URL

**Create `config/voice_profile.json`** — copy from Part 15, paste your voice ID.

```bash
pip install fastapi uvicorn elevenlabs anthropic python-dotenv --break-system-packages
```

Create `scripts/voice_webhook.py` — copy from Part 15 of the spec.

**Test the webhook:**
```bash
# Start the webhook
python3 scripts/voice_webhook.py &

# Test it
curl -X POST http://localhost:8080/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"How does the west bed look today?","author":"christopher","history":[]}' \
  --output test_response.mp3

# Play test_response.mp3 — this should be Slarti's voice
```

Create `pwa/index.html` — copy from Part 15 of the spec.

**Start everything:**
```bash
bash scripts/restart.sh
```

**Test on iPhone:**
1. On your phone's Safari: `http://slarti.local:8080?author=christopher`
2. Tap the mic, say something about the garden
3. Slarti should respond in voice

**Set up Siri Shortcuts** (see Part 15 — takes ~3 minutes per phone).

✅ **Done when:** Curl test plays Slarti's voice. Full voice loop works on both iPhones. Post-session transcript saved and extracted.

---

## After Build — Ongoing Maintenance

### Daily (automated)
- Weather agent runs at 6:00 AM — check `#garden-log` if you expect an advisory
- Git backup runs at 3:00 AM — check `#admin-log` if you see a failure alert

### Weekly
- Read the Sunday summary in `#garden-log`
- Run `@Slarti !projects` to review blockers
- Check `logs/daily/` for any accumulated errors

### When something breaks
1. `@Slarti !status` — first always
2. Check `logs/daily/` for the relevant service
3. Part 14 of the spec has a section-by-section troubleshooting guide

### After adding or changing a bed
- `@Slarti update bed [name]` to edit facts
- Upload a new reference photo if the bed changes significantly
- Run `@Slarti !memory [bed name]` to confirm Slarti has the right picture

### API key rotation
When you rotate an API key, update `.env` and restart: `bash scripts/restart.sh`

---

## Quick Reference

| Command | What It Does |
|---|---|
| `bash scripts/restart.sh` | Full system restart (Docker → OpenClaw → voice webhook → PWA) |
| `openclaw health` | Service health check in terminal |
| `openclaw agents run [agent-name]` | Trigger any agent manually for testing |
| `@Slarti !status` | Human-readable health summary in Discord |
| `@Slarti !setup` | Start or resume onboarding |
| `@Slarti !projects` | Open projects and blockers |
| `@Slarti !memory [bed-name]` | Everything known about a bed |
| `@Slarti !confirm blueprint [project-id]` | Lock in blueprint dimensions after measuring on-site |

---

*Spec: `Slarti_v5_2.md` · Review: `Slarti_v5_2_review.md` · OpenClaw runtime: `OPENCLAW_INSTRUCTIONS.md`*
