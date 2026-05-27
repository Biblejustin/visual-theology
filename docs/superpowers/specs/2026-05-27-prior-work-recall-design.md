# Prior-Work Recall Tools — Design

**Date:** 2026-05-27
**Status:** Approved (Bryan said "just build it"); spec is the record, not a gate.
**Surface:** Study TA agent (in-`/study/` chat). Tools live in `prior_work_tools.py`, registered in `companion_tools.TOOL_DEFINITIONS`, automatically available to all three personas (Research / Composing / Editing) via `persona_tools.py`.

## Problem

While preaching session #23 (Romans 3:21-26 wrap-up, sermon #4 in a 4-sermon series), the AI has no access to:

1. What was actually preached in sermons 1-3 (the manuscript text from the pulpit)
2. The argument flow across the series (how earlier sermons built toward this wrap-up payoff)
3. The sermon coach's analyzer findings on prior sermons (helpers/hurters, application landing, one-change-for-next-Sunday)

Bryan's prior study sessions (his prep work) are also invisible. Three data buckets total, all in `companion.db` but unexposed to the agent.

## Principle

Give the agent **graduated toolkits** per data source — skim / search / read — and let it decide depth. Don't preload bulk; the AI should pick the lightest tool that answers the current question and escalate only on miss. See [[feedback_ai_agency_over_depth]].

## Tool surface (6 tools)

All list/search tools take `scope` ∈ `{series, passage, all}`, default `series`. AI escalates by passing a wider scope.

### Sessions (Bryan's prep work)

```
list_prior_sessions(scope='series')
  → {sessions: [{session_id, passage_ref, series_title, series_position,
                 current_phase, status, created_at, has_decisions,
                 has_outline, message_count}], scope_used, hint}

search_prior_sessions(query, scope='series',
                     search_in=['decisions','outline','chat','notepads','annotations'])
  → {hits: [{session_id, passage_ref, surface, snippet, hit_context}], scope_used}

read_prior_session(session_id, sections=['decisions','outline','notepads'])
  → {session_id, passage_ref, series_title, series_position,
     decisions{9 keys}, outline_tree, notepads_by_phase, annotations[],
     chat_summary? (last 20 messages by default; 'chat_full' for everything)}
```

### Sermons (preached + coach review)

```
list_prior_sermons(scope='series')
  → {sermons: [{sermon_id, title, preach_date, passage_ref, series,
                duration_seconds, sync_status, has_review,
                one_change_for_next_sunday?}], scope_used, hint}

search_prior_sermons(query, scope='series',
                    search_in=['transcript','review','moments','flags','insights'])
  → {hits: [{sermon_id, title, surface, snippet, hit_context, score?}], scope_used}

read_prior_sermon(sermon_id, sections=['review','transcript','moments','flags'])
  → {sermon_id, title, preach_date, passage_ref,
     review{full sermon_reviews row, cleaned}, transcript{full text},
     moments[], flags[]}
```

The coach review is a section of the sermon (because `sermon_reviews` is 1:1 with `sermons`). The agent's docstring will make explicit that "review" is where to look for analyzer findings.

## Scope resolution

- **series** — sessions returned by `db.get_series(current_session_id)`, excluding current. Sermons with active `sermon_links` to those sessions, deduped, ordered by `preach_date`. If no series, returns empty with hint to escalate.
- **passage** — sessions where `book=current.book AND chapter=current.chapter AND verse_start..verse_end overlaps current's range`, excluding current and series members. Sermons matched the same way.
- **all** — full history, recency-ordered (sessions by `updated_at DESC`, sermons by `preach_date DESC`), capped at 50 rows / 25 search hits.

`scope_used` always echoed in the response so the AI knows what it got.

## Search mechanics by surface

| Surface | Technique | Why |
|---------|-----------|-----|
| `decisions` | SQL LIKE on `session_decisions.value` | Short, structured strings |
| `outline` | SQL LIKE on `outline_nodes.content` | Short, structured nodes |
| `chat` | SQL LIKE on `conversation_messages.content` | Deterministic, cheap; agent picks specific terms |
| `notepads` | SQL LIKE on `card_notepads.content` | Short user notes |
| `annotations` | SQL LIKE on `card_annotations.note` + `starred_text` | User highlights |
| `transcript` | SQL LIKE on `sermons.transcript_text` | Long but deterministic substring is enough; embeddings would lose quote-fidelity |
| `review` | SQL LIKE across rubric text fields (excerpts, notes, top_impact_helpers/hurters, faithfulness_note, one_change_for_next_sunday) | Structured |
| `moments` | SQL LIKE on `sermon_moments.excerpt` + `rationale` | Tagged spans |
| `flags` | SQL LIKE on `sermon_flags.excerpt` + `rationale` | Structured |
| `insights` | Embedding cosine search on `claim_embeddings` (existing index) | Semantic retrieval over distilled claims |

All search calls cap at 25 hits. Snippet = up to 200 chars of surrounding context with the query term highlighted (`«query»` markers).

## Series detection

`db.get_series(session_id)` returns parent + all siblings ordered by `series_position`. Returns `[]` for standalone sessions. The current session is a member; tools exclude it.

For sermon-to-session linking, prefer `sermon_links.link_status='active'` (highest confidence). Auto-match is noisy — e.g., sermon 205 is "active" to session 20 but also has a stale "candidate" link to session 23 from passage-overlap auto-matching. The `active` filter handles this.

## System prompt guidance

Appended to the Research / Composing / Editing partner system prompts (in `persona_prompts.py`):

> **Prior-work recall.** When the current session is part of a series, or when a question echoes earlier work, you can reach back via `list_prior_sessions` / `search_prior_sessions` / `read_prior_session` for Bryan's prep work, and `list_prior_sermons` / `search_prior_sermons` / `read_prior_sermon` for what he preached and how the coach reviewed it. Use the lightest tool that answers the current question — list before search, search before read, single-section read before full-section read. Default scope is `series`; pass `scope='passage'` or `scope='all'` only when the tighter scope returned nothing relevant.

## Error handling

- Tools never raise. On missing data, return `{"<entity>": [], "scope_used": "<scope>", "hint": "..."}`.
- On invalid session_id / sermon_id: `{"error": "not_found", "id": <n>}`.
- On unknown `search_in` surface: silently drop the bad surface and search the remaining valid ones (don't punish the agent for a typo).

## Non-goals

- Cross-session writes (the AI cannot modify prior sessions or sermons)
- Live re-scoring or re-tagging of prior sermons
- Embedding generation (uses the existing `claim_embeddings` table; no new model calls)
- Sermon Coach / Meta Coach surfaces (Study TA only for now; tool module is portable)

## Test plan

`tests/test_prior_work_tools.py` covers:

1. **list_prior_sessions** — series scope returns siblings (not current); passage scope works when no series; all scope caps at 50.
2. **search_prior_sessions** — finds in each surface; respects search_in filter; cap at 25 hits; bad surface silently dropped.
3. **read_prior_session** — sections flag works; chat_summary truncates; missing session_id returns error.
4. **list_prior_sermons** — series via active sermon_links only (candidate links ignored); passage by book+chapter; review fields populated when present.
5. **search_prior_sermons** — transcript / review / moments / flags / insights surfaces; embedding surface uses existing claim_embeddings.
6. **read_prior_sermon** — sections flag; review is None when sermon not yet analyzed.
7. **Scope semantics** — series excludes current; scope_used echoed correctly; empty + hint path returns wider-scope suggestion.

Fixtures use a temp DB seeded with: parent session + 3 sibling sessions, 3 sermons with active links + 1 noisy candidate link, mixed analyzed/pending sermons.

## Files touched

- **NEW:** `tools/workbench/prior_work_tools.py` — implementations
- **NEW:** `tools/workbench/tests/test_prior_work_tools.py` — TDD
- **APPEND-ONLY:** `tools/workbench/companion_tools.py` — 6 TOOL_DEFINITIONS entries + 6 dispatch branches; does not touch Bryan's uncommitted dataset-adapter edits
- **APPEND-ONLY:** `tools/workbench/persona_prompts.py` — one paragraph added to each persona's prompt; does not touch Bryan's uncommitted edits
- **NEW:** This spec

## Deployment

After merge: `pm2 restart study-companion`. No DB migration. No new dependencies.
