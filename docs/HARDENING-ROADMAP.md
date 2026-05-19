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

## Polish #5b — `app.py` blueprint split

| # | Blueprint | Routes | Status |
|---|-----------|--------|--------|
| 1 | `bp_passage` | /api/passage, /api/interlinear, /api/resource/*, /api/library/* | ✓ done (513a642) |
| 2 | `bp_auth` | /login, /logout, require_auth (before_app_request) | ✓ done (45e64c1) |
| 3 | `bp_workbench` | / + /project/* + /library + /api/projects/* + /api/chat | ✓ done (25491ae) |
| 4 | `bp_sermons` | /sermons/* (was sermons_bp, moved out of app.py) | ✓ done (75f9fd0) |
| 5 | `bp_study` | /study/* + /api/study/* | **DEFERRED** |

Bonus: `db_provider.py` added to break the circular import from
bp_sermons → app for get_db().

**Why bp_study is deferred:** /study/* IS the app at this point — 26
routes that touch every shared global (companion_db, study_analytics,
accuser_detector, mentor_store, pastor_feedback, session_decisions).
Extracting it would either (a) require lazy module-attribute lookup
from inside every route handler, or (b) require routing all shared
state through a new `app_state.py` registry. Both add a layer of
indirection for marginal benefit — moving 1100 lines from app.py to
bp_study.py doesn't materially improve the mental model when those
lines all reference the same six app-level globals.

The four extracted blueprints captured the genuinely separable
concerns. What's left in app.py is the /study/ surface + setup +
template helpers — and that's the right scope for the "main" app
module.

**Net result:** app.py 2491 → 1289 lines (-48%). Plus 7 new modules
(validators, db_provider, bp_passage, bp_auth, bp_workbench,
bp_sermons, and the existing tests). 926 tests passing.

## Polish #6 — Persona/agent layer dead-code purge ✓

The `/companion/` deletion (polish #5a) left ~600 lines of orphan
code in `companion_agent.py`. The v2 persona refactor had also
introduced `select_persona_prompt` to replace `build_study_prompt`,
but the old function stayed alive because 4 test files imported it.
Classic feature-then-patch trail.

**Deleted from `companion_agent.py`:**
- `stream_companion_response` (165 lines) — 0 callers post-/companion/
- `build_system_prompt` (135 lines) — only `stream_companion_response`
- `PHASE_DESCRIPTIONS` (62 lines) — only `build_system_prompt`
- `HOMILETICAL_GUARDRAILS_EMPHASIZED` (20 lines) — same
- `build_study_prompt` (180 lines) — only tests; superseded by
  `select_persona_prompt` in v2 refactor
- `study_ta_tool_defs` + `STUDY_TA_RETIRED_TOOLS` (22 lines) —
  duplicate of `persona_tools.tool_defs_for_persona`
- 4 unused imports (TOOL_DEFINITIONS, IDENTITY_CORE,
  HOMILETICAL_TRADITION, VOICE_GUARDRAILS)

**Test housekeeping:**
- `tests/test_companion_agent.py` deleted — pure dead-behavior tests
- `tests/test_study_ta_retired_generators.py` deleted; new
  `tests/test_persona_tool_filtering.py` preserves the SAME invariants
  (no generators leak into any persona; diagnostics stay) against the
  live `persona_tools.tool_defs_for_persona` API
- `tests/test_coaching_bridge*.py` migrated off `build_study_prompt`
  onto `build_research_partner_prompt` (what production actually uses
  for the Research Partner persona)

**Bonus — streaming abstraction unified:**
`stream_study_response` was the only one of 4 agents not using
`llm_client.stream_message`. It went direct to the Anthropic SDK with
its own raw `content_block_*` event handling — 125 lines of
duplicated SDK plumbing. Migrated to use `llm_client.AnthropicClient`
like the coach agents. Same tool-use loop shape across all surfaces.

**Net result:** `companion_agent.py` 943 → 315 lines (-67%). Tests
920 passing (was 926; the −6 drop is the deleted pure-dead-behavior
tests in `test_companion_agent.py`).

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
