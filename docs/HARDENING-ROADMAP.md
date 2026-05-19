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

## Pending (needs Bryan's input before proceeding)

### Polish #5a — Decide `/companion/` legacy status
The `/companion/` URL space holds the original card-based UI that
predates `/study/`. Both run live in production. Questions:

- Is `/companion/` still used? (UI smoke check, traffic check)
- If unused, when can it be removed? (One PR removes 8 routes +
  templates + tests, ~400 lines)
- If still used, should it move to its own blueprint? (`bp_companion`)

### Polish #5b — Decide `app.py` blueprint split timing
`app.py` is 2491 lines, 80+ routes. Natural blueprint boundaries:

| Blueprint | Routes | Approx. size |
|-----------|--------|--------------|
| `bp_auth` | /login, /logout, require_auth | ~100 lines |
| `bp_workbench` | / and /api/projects (legacy workbench) | ~250 lines |
| `bp_companion` | /companion/* | ~400 lines (if not removed first) |
| `bp_study` | /study/* and /api/study/* | ~1100 lines |
| `bp_sermons` | /sermons/* | ~400 lines |
| `bp_passage` | /api/passage, /api/interlinear, /api/library/* | ~250 lines |

Done as a single PR this would touch every test that mounts the app.
Done progressively (one blueprint per PR, smallest first) it's
6 small PRs with stable tests between.

**Recommendation:** progressive, starting with `bp_passage` (cleanest
boundary, no shared state with other blueprints). But this is a
multi-hour structural change — Bryan should decide whether to do it
now or defer.

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
