# HEARTBEAT.md — Slarti Periodic Checks

# Runs every 30 minutes. Most cycles produce no output — that is correct.
# Maximum 2 proactive posts per week total. Check health_status.json before posting.

## Checklist (run in order, stop at first match this week)

1. Has a weather advisory been posted today? If not and thresholds crossed: check NWS API for Farmington, MO (37.78, -90.42) and post advisory to #garden-log if heat index 85+ or temp ≤ 36°F during growing season (May–October).

2. Treatment follow-up: any treatment events in data/events/2026/ where follow_up_required=true AND follow_up_resolved=false AND next_check_date within 48 hours? Post gentle check-in to #garden-chat.

3. Fabrication blocker: any projects in data/projects/ with fabricated_parts where qty_completed < qty_needed and approved more than 7 days ago? Gentle reminder in #garden-builds tagging Christopher.

4. Unresolved observation older than 14 days with no follow-up for same bed? During growing season: gentle check-in to #garden-chat.

5. Design approved but no task started after 7 days? Gentle reminder in #garden-builds.

6. Seasonal plant timing: anything in data/plants/ requiring action in the next 14 days, not mentioned in 7 days? Brief timely observation to #garden-chat.

7. Any bed in data/beds/ with no photo in past 60 days during growing season? Gentle nudge.

8. Nothing triggered: do nothing. Log cycle silently.

## Before every post

Apply the friend test: Would a knowledgeable friend actually say this, right now, in this way?

Check data/system/health_status.json → proactive_posts_this_week. If ≥ 2, skip and log skipped: true. Reset to 0 every Sunday at midnight.

Check last_heartbeat_post_subject_id — if already posted about this subject_id in last 24 hours, skip.
