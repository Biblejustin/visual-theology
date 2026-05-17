# Session Split — One Prep, Multiple Sermons

**Date:** 2026-05-17
**Status:** Draft — awaiting user review

## Problem

A single study session sometimes prepares a passage that ends up being preached as a multi-sermon series. Session 20 (Romans 3:21-26) is the working example: the cards hold broad exegetical work covering all six verses (~5.7k char `read_translate`, ~3.2k char `digestion`), but the conversation produced one sermon — "But Now" (vv. 21-22, preached 2026-05-10). The pastor decided in-chat (messages 297-298) to split the rest into three more sermons:

1. **"But Now"** (vv. 21-22) — already prepped and preached
2. **"All Have Sinned"** (vv. 23-24)
3. **"God Put Forward"** (vv. 25-26)
4. **"The Heart of the Gospel"** (vv. 21-26) — synthesis

Sermons 2-4 need fresh per-sermon homiletical prep but should reuse the broad exegesis already in session 20. Today the workbench has no way to express this: each session is treated as one sermon, and there is no model of sibling prep sessions sharing an exegetical foundation.

A related defect on the same surface needs to be fixed at the same time: the "Candidate prep sessions" widget on `/study/session/<id>` renders the wrong copy, an under-specified query, and rows that are visually indistinguishable when one sermon has candidate links to multiple sessions.

## Goals

1. Let the pastor split a session into N sibling sessions, one per sermon, without losing or duplicating the broad exegesis.
2. Surface the in-chat series proposal automatically so the split is a confirmation, not a re-entry.
3. Keep the sermon matcher correct after a split — each sermon should match the right sibling.
4. Fix the candidates widget on session pages so it identifies sermons clearly and only shows relevant rows.

## Non-Goals

- Generic "sermon series planning" UI for sessions that have not yet been prepped.
- Cross-passage series (e.g., a topical series across books). Series here means one passage split into multiple sermons.
- Restructuring the conversation thread (e.g., per-slot threads inside one session). The split creates new sessions instead.
- Backfilling or auto-detecting splits on existing historical sessions other than session 20.

## Approach

A session can become the **parent** of a series. Children are full sessions in their own right (own conversation, outline, notepad, timer), but their card phases render the parent's content read-only. The matcher treats children as the true prep targets and excludes the parent once it has children.

### Schema

Three new columns on `sessions`, one new index:

```sql
ALTER TABLE sessions ADD COLUMN parent_session_id INTEGER REFERENCES sessions(id);
ALTER TABLE sessions ADD COLUMN series_position INTEGER;
ALTER TABLE sessions ADD COLUMN series_title TEXT;
CREATE INDEX idx_sessions_parent ON sessions(parent_session_id);
```

- `parent_session_id` — `NULL` for standalone or parent sessions; set to the parent's id for children.
- `series_position` — `NULL` for standalone sessions; `1..N` for parent (1) and children (2..N).
- `series_title` — per-sermon title (e.g., "But Now"). `NULL` for standalone sessions.

A session is a parent iff at least one row has `parent_session_id = its.id`. A session is a child iff `parent_session_id IS NOT NULL`.

Backfill: all existing rows get the three new columns as `NULL`. Behavior is unchanged for any session that is not split.

### The Split action

A "Split into series" button appears on the session view when the session has a verse range and is not already a child.

On click:

1. The agent reads `conversation_messages` for the session and proposes a series plan: a list of `{position, title, verse_start, verse_end}`. For session 20 this lifts directly from message 298. If no plan is found, the modal opens empty and the pastor types it.
2. A modal shows the proposed rows. The pastor can edit titles, edit verse ranges, add rows, delete rows, and reorder. Position 1 is always the existing session.
3. On confirm, in a single transaction:
   - The parent row is updated with `series_position = 1`, `series_title = <row 1 title>`, and `verse_start/verse_end = <row 1 range>` (narrows the parent to sermon 1's actual range, since that is what the existing homiletics covers).
   - One child session is inserted per remaining row, with the parent's `book`, `chapter`, and `genre`, the row's verse range, `parent_session_id = parent.id`, `series_position = N`, `series_title = <row N title>`, an empty conversation, no outline, an empty notepad, a default timer, and `current_phase = 'prayer'`.
   - The matcher is re-run for any sermon whose currently-active link points at the parent and whose verse range no longer matches the parent's narrowed range. Links that should now point at a sibling are moved; links that still match the parent stay.

### Child session UX

A child session behaves like a normal session except in card mode:

- Each card phase (`prayer`, `read_translate`, `digestion`, `study_bibles`) reads its content from `card_responses WHERE session_id = parent_session_id AND phase = ?`, renders it read-only, and shows a banner: *"From parent session: Romans 3:21-26 broad exegesis"* with a link to the parent.
- The child has its own `card_responses` table presence only for phases the pastor explicitly overrides per sermon (out of scope for v1 — v1 treats parent cards as canonical and child cards as untouched).
- The child has its own conversation, outline, notepad, timer, and current phase.

A breadcrumb at the top of any session in a series lists the siblings by position and title, with the active one highlighted, so the pastor can hop between them.

### Sermon matcher changes

Two rules in `sermon_matcher.py`:

1. **Parents are excluded.** In `apply_match_decision_for_sermon`, the session candidate set filters out any session that has children: `AND id NOT IN (SELECT parent_session_id FROM sessions WHERE parent_session_id IS NOT NULL)`. Children are matched as themselves; the parent never appears as a match target after a split.
2. **Children are first-class.** No special handling beyond (1). The existing tier1/tier2 logic with `verse_start/verse_end` will pick the right child by exact or overlap match against the sermon's range.

Sermon 4 ("The Heart of the Gospel", vv. 21-26) matches its own child by exact verse range. Sermons 1-3 match their respective children by exact range.

### Q1 — Candidates widget fix (folded in)

Split `templates/partials/sermon_candidates_list.html` into two partials with view-appropriate copy and queries:

- `sermon_candidates_for_session.html` — used on `/study/session/<id>`. Copy: *"Sermons that may have been prepped in this session. Approve one to link it."* Each row shows the sermon's title, passage, and preach date.
- `sermon_candidates_for_sermon.html` — used on the sermon detail and review pages. Copy stays as today. Each row shows the session's `passage_ref`.

The session-side query is corrected:

```sql
SELECT sl.id, sl.sermon_id, sl.match_reason, s.title, s.bible_text_raw, s.preach_date
FROM sermon_links sl
JOIN sermons s ON s.id = sl.sermon_id
WHERE sl.link_status = 'candidate' AND sl.session_id = ?
```

The session view also drops the `LIMIT 1` on linked sermons (`app.py:1006-1012`) and renders all active links, since a parent can in principle have multiple links during a transition and children of the same parent should also be visible.

## Data flow

```
Pastor on /study/session/20
   │
   │ click "Split into series"
   ▼
Agent reads conversation_messages → proposes plan
   │
   ▼
Modal shows {1: But Now 21-22, 2: All Have Sinned 23-24, ...}
   │
   │ confirm
   ▼
Transaction:
   parent row updated (narrow verses + title + position 1)
   N-1 child rows inserted
   sermon_matcher re-run for parent's existing active links
   │
   ▼
Redirect to /study/session/20 (parent), now showing series breadcrumb
```

## Components touched

| File | Change |
|------|--------|
| `companion_db.py` | Add migration for three columns + index. Helpers: `get_children(parent_id)`, `get_parent(child_id)`, `get_series(any_id)`. |
| `app.py` | New route `POST /study/session/<id>/split` (accepts confirmed plan). New route `POST /study/session/<id>/split/propose` (returns AI plan as JSON). Session view: render breadcrumb, render read-only parent cards in children, drop `LIMIT 1` on linked sermons. |
| `companion_agent.py` or new `series_planner.py` | `propose_series_plan(messages) → list[{position, title, verse_start, verse_end}]`. Reuses the existing Claude client; simple system prompt that asks for the plan as JSON. |
| `sermon_matcher.py` | Filter parents from candidate sessions. Re-match helper invoked from the split endpoint. |
| `templates/study_session.html` | Series breadcrumb. Parent-cards-read-only banner. Split button. |
| `templates/partials/sermon_candidates_list.html` | Split into two partials per view. |
| `templates/partials/series_split_modal.html` | New. Renders the editable plan. |
| `static/companion.js` | Open modal, call `/split/propose`, render rows, submit `/split`. |
| `tests/test_sermon_matcher.py` | New cases: parent excluded after split; children matched by verse range. |
| `tests/test_session_split.py` | New file: split endpoint, child creation, parent narrowing, matcher re-run. |

## Error handling

- **Already split.** Splitting a session that already has children returns a clear error; the pastor must delete children first if the plan needs to change.
- **Plan extraction fails.** If `propose_series_plan` returns no rows or invalid JSON, the modal opens empty and the pastor types the plan. No silent fallback.
- **Verse ranges overlap or cover more than the parent's range.** The modal validates client-side. The server re-validates and rejects with a 400.
- **Sermon match conflicts.** If the post-split re-match would leave a sermon orphaned (no child matches its range), the link stays on the parent with a `match_status = 'awaiting_candidates'` flag so the pastor can review.

## Testing

Unit tests:

- `propose_series_plan` parses messages 297-298 of session 20 into the four-sermon plan.
- Matcher excludes parents and routes each test sermon to the correct child.
- Split endpoint creates the right number of children with correct verse ranges and titles.
- Re-match after split moves the right links and leaves the right links in place.

Integration:

- E2E test: load session 20 fixture, hit `/split/propose`, confirm, verify three children exist with positions 2/3/4 and the parent narrowed to vv. 21-22 with position 1.
- Verify a child session's card view renders the parent's `read_translate` content read-only.

## Open questions deferred to implementation

- Whether to allow per-child card overrides (v2).
- Whether the breadcrumb should also appear on the sermon detail page when the linked session is part of a series.
- Whether deleting a child should be allowed once it has any conversation messages, or only while empty.
