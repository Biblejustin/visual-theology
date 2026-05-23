# Sermon-Pack Deliverables — Design Spec

**Date:** 2026-05-23
**Status:** Design approved (brainstorm). Spec pending Bryan's review → implementation plan.
**Companion / follow-on:** A second spec, after this ships, will close the **upstream gap** (agent doesn't call `save_to_outline` / decision-commit during composing & editing phases). That follow-on uses this spec's mining-percentage metric as its acceptance criterion. The visual chalk-and-talk (TEAM page) is OUT of scope and remains a separate spawned-session workflow.

## Problem

Every preaching week, Bryan needs three deliverables from a finished session: a **full manuscript** (preach-ready prose), a **glance-down preaching outline** (one sheet at the pulpit), and a **congregation handout** (fill-in-blank, one sheet front + back). Tonight (2026-05-23) producing them required Claude to hand-extract from `session_decisions` + `outline_nodes` + conversation thread mining, then assemble the three files file-by-file. That's not sustainable weekly.

Bryan's framing: *"We want to get to the place in the tooling we don't have to keep tinkering."*

## Design principle (load-bearing)

**The generator is also instrumentation.** Each artifact records which sections came from structured panels (decisions / outline_nodes / card_notepads / card_responses) and which had to be mined from the conversation thread. The aggregate **mining-percentage per session** is logged. Driving that percentage toward zero is the explicit goal of the follow-on upstream-fix spec. Until then, mining keeps Bryan unblocked weekly; the metric makes the upstream gaps visible instead of hidden.

## Scope

**In:** the three sermon docs (manuscript, preaching outline, congregation handout), backend generator, UI surface in the workbench, observability/metrics.
**Out:** TEAM visual chalk-and-talk page (separate workflow); upstream agent prompt-fixing (next spec); `.docx` output (follow-on; `.html` opens fine in Pages today).

## Architecture

### 1. `tools/sermon_pack.py` — pure generator module

A passage-agnostic Python module + CLI. No Flask, no LLM. Pure data transformation.

```
sermon_pack.generate(session_id, *, out_dir, artifacts=("manuscript","outline","handout"))
    -> { artifact_name -> { path, mining_percentage, source_breakdown } }
```

CLI: `python3 tools/sermon_pack.py <session_id> [--out DIR] [--artifact NAME]`

Used by the new routes (§3), also runnable directly when the UI is unavailable or for backfill / debugging.

### 2. Source-of-truth precedence (per artifact field)

Each field in each artifact is resolved by walking these sources in order; the first non-empty wins, and the source is recorded:

1. **`session_decisions`** — the 9 keyed decisions (subject, theme, ep_otm, transition, fcf, hp_tht, purpose_statement, target_length_min, exegetical_outline).
2. **`outline_nodes`** — the structured outline tree (main_point / sub_point / note / cross_ref / illustration).
3. **`card_notepads`** + **`card_responses`** — Bryan's prayer / read_translate / digestion / study_bibles notepad work.
4. **`conversation_messages`** — mined when nothing above provides the field. Mining uses a small set of heuristics keyed to the assembled-outline pattern (Phase-13/14/15/16 assistant turns that produced markdown sermon outlines).

Every field carries a `source` tag in its provenance record: `"decisions"`, `"outline"`, `"notepad"`, `"card"`, `"mined"`, or `"missing"`.

### 3. Three new routes

- `GET /study/session/<id>/pack/manuscript.md`
- `GET /study/session/<id>/pack/outline.md`
- `GET /study/session/<id>/pack/handout.md`

Each returns the rendered Markdown as a download (`Content-Disposition: attachment`). A `?format=html` query renders via `tools/md_to_html.py` (existing — picks the tight-print profile automatically for the handout).

Existing `/outline/export.pdf` and `.docx` routes (outline-tree-only) stay in place but are clearly relabeled in the UI as "outline tree only" so they're not confused with the new sermon-pack outline.

### 4. UI surface: a "Sermon Pack" block in the existing outline sidebar

Reuse the existing `.outline-export-actions` block in `templates/study_session.html`. Restructure into two clearly-labeled groups:

```
SERMON PACK
  • Manuscript  (.md · .html)
  • Outline     (.md · .html)
  • Handout     (.md · .html)
  ⓘ Source breakdown (last build)

OUTLINE TREE ONLY (existing)
  • PDF · Word
```

The "Source breakdown" tooltip / pill shows the mining-percentage and per-artifact breakdown from the last build, so Bryan can SEE the metric without leaving the page.

### 5. Templates (one per artifact, in `tools/sermon_pack_templates/`)

Three Python f-string-based templates: `manuscript.md.tmpl`, `outline.md.tmpl`, `handout.md.tmpl`. Each receives a `Context` dataclass with the resolved fields + their sources. Templates are pure rendering — no logic beyond field substitution and simple conditionals.

The templates are version-controlled — Bryan edits them in the repo when he wants format tweaks long-term (not weekly tweaking). For weekly per-sermon adjustments, he opens the generated `.md`/`.html` in Pages and edits there.

### 6. Observability — `sermon_pack_metrics` table

A new SQLite table records each generation:
```
(session_id, generated_at, artifact, source_breakdown_json, mining_percentage)
```

A small CLI report (`python3 tools/sermon_pack.py --report`) shows the trend over time — mining-% per artifact per session. **This is the success metric for the follow-on upstream-fix spec.**

### 7. Style discipline (load-bearing)

The generator MUST honor existing style memory:
- `feedback-no-em-dashes-prose` — no em-dashes in output (templates use real punctuation).
- `feedback-no-fabricated-quotes` — never invent quotes; mining only surfaces text the agent or Bryan actually wrote.
- `feedback-no-overclaiming-tool-actions` — if a field is genuinely missing, output a clear `[missing: <field>]` marker, not a fabricated placeholder.

The handout template uses the tight-print profile (already wired in `md_to_html.py`) and the page-break marker support.

## Acceptance tests

1. **Three-click reproducibility:** Click each of the three buttons → three `.md` files download → opening any `.html` variant in Pages renders clean.
2. **Mining metric:** After tonight's session (22), `sermon_pack.generate(22)` reports a non-trivial mining-percentage (today, most prose comes from conversation; outline_nodes has only the title). The number is recorded in the metrics table.
3. **Resilience to sparse data:** If only `session_decisions` is populated (a fresh session in Phase 13), the generator still produces all three artifacts with `[missing: ...]` markers in unknown fields. No crash, no fabrication.
4. **Determinism:** Two consecutive generations of the same session produce byte-identical `.md` output (timestamps go in metadata, not body).
5. **Style:** No em-dashes in any generated file (grep `—` returns 0).
6. **Verify-before-done:** UI changes (the new sidebar block) verified in a real browser per `feedback_ui_verify_before_done` before claiming deployed.

## Components touched

- **New:** `tools/sermon_pack.py`, `tools/sermon_pack_templates/{manuscript,outline,handout}.md.tmpl`.
- **Modify:** `tools/workbench/app.py` (three new routes + a small report endpoint for the source-breakdown pill), `tools/workbench/templates/study_session.html` (restructured outline-sidebar export block), `tools/workbench/companion_db.py` (new `sermon_pack_metrics` table + migration), `tools/workbench/static/study.js` (small fetch for the source-breakdown pill).
- **Reuse:** `tools/md_to_html.py` (existing — handles MD→HTML with handout-tight profile).

## Out of scope (follow-on work)

- **Next spec (right after this ships): "Sermon-pack upstream wiring."** Add prompt cues so the agent calls `save_to_outline` during Phase 13/14, and a `commit_decision(key, value)` tool wired to `session_decisions`. Acceptance criterion: mining-percentage trends toward zero across new sessions. This is when "we stop tinkering."
- **`.docx` output:** `.html` opens in Pages cleanly today; native `.docx` is nice-to-have, deferred.
- **TEAM page generation:** separate workflow (spawn a fresh Claude Code session with the customized prompt), tracked elsewhere.

## Connections

- Memories: `[[feedback-tools-only-as-useful-as-called]]`, `[[feedback-no-em-dashes-prose]]`, `[[feedback-no-fabricated-quotes]]`, `[[feedback-no-overclaiming-tool-actions]]`, `[[feedback-ui-verify-before-done]]`.
- Tooling reused: `tools/md_to_html.py` (handout tight profile, `<!--PAGEBREAK-->` marker).
- Existing routes preserved: `/outline/export.{pdf,docx}` (outline-tree-only), now clearly labeled in UI.
