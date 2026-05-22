# Library Retrieval Fidelity — Design Spec (Project A)

**Date:** 2026-05-22
**Status:** Design approved (brainstorm). Spec pending Bryan's review → implementation plan.
**Sequence:** A now, then **B — Dataset Utilization** in quick succession (see `2026-05-22-dataset-utilization-design.md`, to be written).
**Problem doc:** `docs/superpowers/ideas/2026-05-22-library-quote-fidelity-and-ancient-lit-datasets.md`

## Problem (recap)

The study partner can tell that material exists but can't deliver the full, exact text — so it fabricates quotes, punts, or substitutes its own untethered training as analysis. Confirmed across three resource types: library search (snippet-only), patristics (no full text + dataset unwired), grammars (`lookup_grammar` keyed to the wrong index). Violates the research-GA contract ([[feedback-llm-role-research-ga]], [[feedback-no-fabricated-quotes]]).

## Key enabling discovery

`logos_search` **already** computes the locator and reads the full article — then discards it:
- Every `search_library` result carries `resource_id`, `filename`, and per section `article_num` (via `pos2article`).
- `_attach_snippets` already calls `reader.read_article(filename, article_num, max_chars=20000)` and slices it to a ~250-char snippet.
- `study.read_article_text(resource_file, article_num, max_chars=20000)` is the reusable full-text reader.

So "pull the full quote" is **exposing an existing capability**, not new retrieval plumbing.

## Design

### 1. New tool: `read_library_section(resource_id, article_num, max_chars=8000)`
Thin wrapper over `study.read_article_text`. Resolves the resource (by `resource_id`→filename, or accepts `filename` directly as returned by `search_library`) and returns the **full, exact** section text. The agent already has `resource_id` + `article_num` from a prior `search_library` call. Flow: `search_library` (find + snippet) → `read_library_section` (verbatim) → quote with citation.

### 2. `search_library` output + description
The result fields already include the locator; update the **tool description** to instruct: *"To quote a source exactly, call `read_library_section` with the result's `resource_id` and the section's `article_num` — never quote from the snippet alone."* (Invocation half — see [[feedback-tools-only-as-useful-as-called]].)

### 3. Fix `lookup_grammar` (the πίστεως Ἰησοῦ miss)
Reroute from the headword index (wrong for discursive grammars — see problem doc Instance C) to the **full-text searcher scoped to grammar resources** (`resource_type` / author filter for Wallace, Robertson, BDF, etc.), then return the matching section's full text via `read_article_text`. Keep the `testament` (nt/ot) filter. Result: "subjective vs. objective genitive" returns Wallace's actual section, not silence.

### 4. Wire the `ancient_literature` dataset into the patristic phase
`get_passage_context(ancient_literature)` already maps a passage → citations in the fathers/classical works. Surface it as the **first stop** in the Phase-10 ("Don't Live in a Historical Vacuum") persona prompt, and follow its pointers with `search_library` + `read_library_section` to get exact, cited father quotes.

### 5. Anti-fabrication guardrail (agent prompt)
Hard rule: **never present a direct quotation unless a retrieval tool returned it verbatim this turn.** Otherwise describe the theme and state plainly that no exact quote was found. Ships *with* the retrieval tool so the agent can comply instead of punting. Reinforces [[feedback-no-fabricated-quotes]] and [[feedback-no-overclaiming-tool-actions]].

### 6. Invocation (make the agent actually call these)
Per [[feedback-tools-only-as-useful-as-called]]: tool descriptions name when to use them; the exegesis/grammar/patristic persona prompts proactively cue `search_library`→`read_library_section` and `lookup_grammar` at the right phases. An acceptance test confirms the agent *calls* them rather than answering from training.

## Scope boundary
- **In A:** the grammar *fix* is the `lookup_grammar` reroute to Wallace's prose — that alone answers the genitive question.
- **In B (immediately after):** the **syntax-graph datasets** (Cascadia Syntax Graphs NT/SBL/LXX, Andersen-Forbes Phrase Marker, Lexham Clauses, NT Syntactic Force, Discourse GNT/HOT). They're structurally complex (graph schemas) and fit B's per-schema adapter/registry framework. They *deepen* grammar answers; A already makes them correct via Wallace.

## Components touched
- `companion_tools.py` — new `read_library_section` tool + handler; `search_library` description update; `_lookup_grammar` reroute.
- `logos_search.py` / `study.py` — reuse `read_article_text`; resolve `resource_id`→filename helper if needed.
- `companion_agent.py` / `persona_prompts.py` — anti-fabrication guardrail + phase-aware invocation cues for exegesis/grammar/patristic phases.

## Acceptance tests
1. **Grammar fidelity:** ask the πίστεως Ἰησοῦ subjective/objective genitive question → `lookup_grammar` returns Wallace's actual section text (verbatim), and the agent quotes it with citation.
2. **No fabrication:** ask for a patristic voice on Romans 3:25 → either a real verbatim, cited quote (via ancient_literature → search → read_library_section) or an honest "no exact quote found" — never an invented "Chrysostom said…".
3. **Full quote:** `search_library` a topic → `read_library_section` returns the full section, not a 250-char snippet.
4. **Invocation:** the genitive question triggers a `lookup_grammar`/`read_library_section` tool call (not a training-only answer).

## Testing
TDD throughout. Unit tests: `read_library_section` returns full text for a known resource+article; `_lookup_grammar` uses the searcher path (mock searcher) and returns section text; anti-fabrication guardrail present in the assembled prompt. E2E/integration where a real reader is available (this Mac).
