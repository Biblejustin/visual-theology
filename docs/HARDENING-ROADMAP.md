# Study Companion Hardening Roadmap

This doc tracks the "polish and harden" cleanup pass triggered by the
2026-05 project assessment. The assessment characterized the
codebase as "a working prototype that has grown into an operational
personal system faster than its foundations were cleaned up" — the
classic feature-then-patch pattern.

The polish pass addresses substrate-level consolidation. Feature work
continues in parallel.

## Completed

### Polish #1 — Test-suite hygiene *(pre-thread)*
- Shared `init_all_app_globals(db_path)` fixture in `tests/conftest.py`
- `_require_base_url` helper for E2E tests when port 5111 is busy
- All test files initialize the same set of module globals together
- Full suite: 948 passed, 22 skipped

### Polish #2 — Shared request validators *(commit f1f20a7)*
`tools/workbench/validators.py`:
- `parse_int_arg(name, default, lo, hi)` — query-string ints, never raises
- `json_body()` — silent parse, coerce None/non-dict
- `require_string(data, key, min_length, max_length, allow_missing)`
- `require_list_of_strings`, `require_int`
- `require_session(db, sid)` / `require_sermon_row(db, sid)` — 404 on miss
- `*_or_raise` variants for nested flows (raise `ValidationError`)
- 29 tests pin every code path

### Polish #3 — DB connection discipline *(commit 22e5b01)*
`CompanionDB._safe_write()` context manager:
- Commits on normal exit
- Rolls back on exception
- Always closes
- Replaces three inconsistent patterns (naked / try-finally / full
  try-except-rollback-finally) that were applied unevenly

Migrated 15 write helpers: `create_session`, `delete_session`,
`complete_session`, `update_phase`, `update_timer`, `save_message`,
`save_card_response`, `save_card_annotation`, `save_card_notepad`,
`save_audio_recording`, `save_insight`, `save_claim_embedding`,
`add_outline_node`, `update_outline_node`, `delete_outline_node`.

Net: -166 lines, +129 lines. 6 new tests pin commit/rollback/close
semantics + verify each migrated helper still works.

### Polish #4 — Route migration onto validators *(commit 850551d)*
Demonstration migration of 9 high-traffic routes:
- `/api/projects/<id>/phase`
- `/api/chat`
- `/api/library/search`
- `/study/patterns`
- `/companion/session/<id>/discuss`
- `/study/session/<id>/transcribe`
- `/study/session/<id>/lookup-word`
- `/study/session/<id>/annotate`
- `/study/session/<id>/notepad`

Remaining routes migrate progressively as they're touched. HTML routes
(redirects, not JSON) intentionally not migrated — `require_session`
is JSON-shaped.

## Polish #5a — `/companion/` legacy DELETED ✓

Bryan confirmed (2026-05-18) he never visits `/companion/`. Deleted:
- 15 routes from app.py (~360 lines)
- 4 page templates (companion_base/start/session/export.html)
- 3 orphan partials (card/discussion/progress_dots.html)
- 2 static assets (companion.css/.js)
- 21 /companion/ tests in test_routes.py (kept 8 /study/ tests)
- 2 /companion/ "still works" tests in test_study_routes.py

Retained: `card_responses` table + `save_card_response` /
`get_card_responses` helpers — /study/ and the persona agents
still read them for prior-session context.

Tests: 926 passed, 22 skipped (was 948 — drop is the deleted
companion tests). bp_companion is no longer needed in the split.

## Polish #5b — `app.py` blueprint split (in progress)

Plan: 5 blueprints (down from 6 after /companion/ deletion), one
PR each, smallest/safest first.

| # | Blueprint | Routes | Status |
|---|-----------|--------|--------|
| 1 | `bp_passage` | /api/passage, /api/interlinear, /api/resource/*, /api/library/* | ✓ done (commit 513a642) |
| 2 | `bp_auth` | /login, /logout, require_auth (before_app_request) | ✓ done (commit 45e64c1) |
| 3 | `bp_workbench` | / + /api/projects/* (legacy workbench) | pending |
| 4 | `bp_study` | /study/* + /api/study/* | pending |
| 5 | `bp_sermons` | /sermons/* | already a blueprint |

After #4 lands, `app.py` should be ~500 lines (setup + lifecycle +
template helpers + blueprint registration) instead of the original
2491.

## Not in scope (separate threads)

- Three-persona architecture refinement (Composing/Editing/Research)
- Time Coach phrasing rotation pool growth
- Sermon Coach calibration corpus research
- Visual Theology WS1 unblock (deferred priority)

## Quick-reference commands

```bash
# Run full suite
cd tools/workbench && python3 -m pytest tests/ -q

# Run just the polish-suite tests
python3 -m pytest tests/test_validators.py tests/test_safe_write.py -v

# Restart the live app after a polish-pass change
pm2 restart study-companion
```
