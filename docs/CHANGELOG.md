# SIH Pipeline — Change Log (maintained by agent-01)

This file tracks changes agent-01 (Claude) has made to your pipeline code in chat, so you always have a record of what changed, why, and exactly how to undo it if a future decision reverses course.

---

## 2026-09-16 — Removed `flood_status_severity_code` from `auto_refresh.py`

**Why:** You said you didn't want `flood_status_severity_code` auto-refreshed alongside `river_level_m`. `river_level_m` itself was left untouched and still auto-fetches normally when `GOOGLE_FLOOD_API_KEY` is set.

**What changed in `auto_refresh.py` (5 edits, all reversible — `fetch_flood_status.py` itself was never touched):**

| # | Where | What was removed |
|---|---|---|
| 1 | Top imports | `from .fetch_flood_status import FIELD_NAME as FLOOD_STATUS_FIELD_NAME` and `from .fetch_flood_status import ingest_flood_statuses` |
| 2 | `_active_auto_fields()` | The line appending `FLOOD_STATUS_FIELD_NAME` whenever `river_level_is_configured()` |
| 3 | `refresh_static_fields()` | The `flood_status_results = ingest_flood_statuses(...)` call, the `"flood_status": {}` key in the early-return dict, and the `"flood_status": flood_status_results` key in the final return dict |
| 4 | `main()` | The two `print(...)` lines reporting `flood_status_severity_code` status |
| 5 | Module docstring | Added a note explaining the removal (so nobody reading the file later thinks it's an oversight) |

**Also verified same session (unrelated, but for the record):** `ingest_grid_csv.py` (the new script for ingesting `shoreline_change_rate_m_per_yr` and `mangrove_cover_pct` from downloaded point-grid CSVs, no geopandas needed) was compile-checked and dry-run tested against your real CSVs and real `zones.py`. Result: Puri and Wayanad get matched values, Patna/Guwahati/Joshimath correctly return `None` (inland, outside the coastal grid's coverage) — script behaves as documented.

---

## Reversal prompt

If you ever want `flood_status_severity_code` back, paste this into a chat with the current `auto_refresh.py` and `fetch_flood_status.py` attached:

> Re-add `flood_status_severity_code` to `auto_refresh.py`, the way it worked before it was removed on 2026-09-16: import `FIELD_NAME as FLOOD_STATUS_FIELD_NAME` and `ingest_flood_statuses` from `.fetch_flood_status`; append `FLOOD_STATUS_FIELD_NAME` in `_active_auto_fields()` right after `RIVER_LEVEL_FIELD_NAME`, gated the same way (only when `river_level_is_configured()`); add back the `flood_status_results = ingest_flood_statuses(refresh_ids, store) if river_level_is_configured() else {}` call and its `"flood_status"` key in both the early-return dict and the final return dict of `refresh_static_fields()`; and restore the two `flood_status_severity_code` print lines in `main()`.

---

*(This file will be updated as the chat continues, and condensed into a summary if the conversation gets long.)*
