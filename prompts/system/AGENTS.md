# AGENTS.md — Slarti Agent Definitions

All agent definitions. The orchestrator reads this file to route requests and schedule jobs.

---

## Agent 1 — Daily Weather Agent

**Schedule:** Every day at 6:00 AM local time.

**Steps:**
1. `GET https://api.weather.gov/points/37.78,-90.42` → extract `forecastHourly` URL
2. Call hourly forecast URL → extract temperature, humidity, wind speed per hour
3. Calculate heat index for each hour (temperature + humidity)
4. Write `data/system/weather_today.json` and update rolling `data/system/weather_week.json`
5. Evaluate — **growing season only (months 5–10):**
   - Heat index 80–84: mention heat casually if outdoor tasks pending — no dedicated post
   - Heat index 85–89: post contextual advisory to `#garden-log`
   - Heat index 90+: post high-risk advisory naming specific pending tasks and their risk level
   - Any hour ≤ 36°F: post frost warning naming most vulnerable plants and beds
6. All advisories: send Claude the pending task list, hourly forecast, and bed entity data. Write in Slarti's voice — not from a template.

**Error handling:** NWS fails → retry after 10 minutes → if still failing, log and skip advisory checks, write `last_weather_refresh_failed: true` to health_status.

---

## Agent 2 — Post-Conversation Extraction Agent

**Trigger:** Within 60 seconds of last message going quiet in any session. Also fires after every Mode V transcript and every completed voice session.

**Script:** `scripts/extraction_agent.py`

**Steps:**
1. Receive full conversation transcript with author tags
2. Send to Claude:
   ```
   You are a memory extraction agent. Extract only NEW facts.
   Categories: BED_FACT | DECISION | TASK | OBSERVATION | TREATMENT | PREFERENCE
   For each: category, author (emily/chris/system), subject_id (resolve from aliases),
   content, confidence (0.0–1.0). Return JSON only. No extracts: {"extracts": []}
   ```
3. Apply confidence thresholds from `config/confidence_thresholds.json`
4. For each extract passing threshold:
   - Check for duplicates — skip if same fact already exists
   - Resolve bed aliases to canonical ID
   - Apply Emily-wins rule if conflict exists
   - Write with author, timestamp, schema_version, source
   - If TREATMENT: create `treatment_application` event with `follow_up_required: true`
5. Medium-confidence (0.50–0.79): post @mention to originating channel, await confirmation
6. Trigger `garden.md` regeneration if any BED_FACT or DECISION extracted

**garden.md regeneration:**
- Trigger: after any BED_FACT or DECISION extraction, AND daily at 9:00 AM
- How: send Claude all current bed entities + last 30 timeline events
- Output: write to `docs/garden.md` with `last_regenerated_at` front matter
- Failure: retain previous `garden.md`, log `garden_md_regeneration_failed: true`

---

## Agent 3 — Proactive Heartbeat Agent

**Schedule:** Every 30 minutes. Most cycles produce no output — that is correct.
**Config:** See `HEARTBEAT.md` for full checklist.
**Max:** 2 proactive posts per week total.

---

## Agent 4 — Weekly Summary Agent

**Schedule:** Every Sunday at 6:00 PM. Post to `#garden-log`.

**Steps:**
1. Read: `data/system/weather_week.json`, timeline events from past 7 days, open tasks, design sessions from past 7 days, treatment events with `follow_up_required: true`
2. Send to Claude with `prompts/system/weekly_summary_mode.md` loaded as context
3. Post result to `#garden-log`

**Error handling:** Missing file → skip that source, continue. Claude fails → retry once → post *"Slarti is taking the week off — summary coming next Sunday"*.

---

## Agent 5 — Onboarding Agent

**Trigger:** `@Slarti !setup`

**Fresh start:**
1. Write `data/system/onboarding_state.json`: `{"status": "in_progress", "beds_completed": [], "current_bed": 0}`
2. Post welcome message conversationally — not a form
3. Per bed, ask one question at a time: name and aliases, rough size, sun exposure, what's in it, known issues, photo angle notes
4. Extract EXIF from any reference photo via MarkItDown
5. After each bed: write to `data/beds/bed-XX.json`, update `onboarding_state.json`
6. On completion: trigger `garden.md` regeneration

**Resume:** `@Slarti !setup continue` → read `onboarding_state.json` → pick up from last saved position.

---

## Agent 6 — Design Approval Agent

**Trigger:** Emily posts approval intent in `#garden-design` (Claude determines intent, not keyword matching).

**Confidence threshold:** Approval requires confidence ≥ 0.85.
- ≥ 0.85: proceed with approval
- 0.50–0.84: ask for explicit confirmation before locking in
- < 0.50: continue design session normally

**Steps:**
1. Identify most recent proposed design in the thread
2. Update design session: `status: "approved"`, `approved_at: timestamp`
3. Update bed entity: add `planned_plants`, `design_intent`, set status `'build-ready'`
4. Generate build summary using `prompts/system/build_project_mode.md`
5. Push build tasks to data files tagged `assignee: christopher`
6. Post build summary to `#garden-builds`
7. Pin approved design in `#garden-design`
8. Post: *"Locked in — build summary sent to #garden-builds."*

---

## Agent 7 — Project Review Agent

**Schedule:** Weekly, or `@Slarti !projects`

**Steps:**
1. Read all project JSON files from `data/projects/`
2. For each non-completed project: check fabricated_parts, materials_purchased, blueprint_dimensions_confirmed, stale tasks
3. Post project status summary to `#garden-builds` if there are active blockers

---

## Agent 8 — Command Parser

**Trigger:** Any Discord message starting with `!` — bypasses all mode classification.

| Command | Handler |
|---|---|
| `!status` | Load `data/system/health_status.json` → format as human-readable summary |
| `!health` | Full health check — provider status, last API call, file integrity, Postgres liveness |
| `!memory [entity_id]` | Load matching JSON from `data/beds/`, `data/plants/`, or `data/projects/` |
| `!memory tasks` | All tasks with `status: 'open'` or `'in_progress'` — summarize by assignee |
| `!memory garden` | Read `docs/garden.md` → post current summary |
| `!projects` | All open projects, blockers, fabrication status |
| `!timeline [bed_id]` | Chronological narrative of a bed in Slarti's voice |
| `!setup` | Trigger Agent 5 — fresh start |
| `!setup continue` | Trigger Agent 5 — resume from saved state |
| `!confirm blueprint [project_id]` | Set `blueprint_dimensions_confirmed: true`, log to `write_log.json` |

**Unknown `!` command:** *"I don't recognise that command. Try `!status`, `!projects`, `!memory [bed name]`, or `!timeline [bed name]`."*
