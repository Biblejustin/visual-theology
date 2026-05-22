# Library Retrieval Fidelity (quotes, grammars, datasets) — Problem Doc

**Date:** 2026-05-22
**Status:** Problem captured + root cause confirmed across 3 resource types. NOT yet brainstormed/designed. Needs its own design pass.
**Priority:** HIGH — higher than auto-phase-advance. This is a correctness/trust bug, not a convenience.

## Unifying defect

Across multiple resource types, the retrieval layer can tell the AI that material *exists* but **cannot reliably hand it the full, exact, relevant text.** The AI then does one of three bad things: fabricates a quote, punts ("the tools aren't giving me…"), or silently substitutes its **own untethered training knowledge** and presents it as analysis. All three violate the research-GA contract ([[feedback-llm-role-research-ga]]). Three confirmed instances below.

## The trigger (2026-05-22, Romans 3:25-26, patristic phase)

The study partner offered **fabricated patristic quotes** — "Chrysostom said…", "Augustine said…" — for Romans 3:25-26. Bryan caught it. The AI walked it back and admitted it could not produce exact quotes from the search results. For a tool whose entire purpose is **surfacing real sources with citations** (see memory `feedback-llm-role-research-ga`), fabricating a quote is the worst possible failure: it poisons trust in everything else the tool says.

Bryan's hard rule (verbatim intent): **"When you include any quotes from the fathers they need to be exact quotes from the library and not fabricated."**

## Root cause (confirmed in code, not guessed)

1. **Snippet-only retrieval.** `search_library` (companion_tools.py) returns, per resource, a list of sections each with a **~500-char snippet** (query terms in «guillemets»). There is **no tool to expand a hit into the full, exact section/article text.** So once the AI finds the right passage in a father, it cannot retrieve the verbatim words — it can quote a truncated snippet or fabricate. There is no `read_library_section(resource, locator)` equivalent.

2. **Underused dataset.** The "ancient literature" dataset Bryan referred to **already exists** as `get_passage_context(reference, context_types=["ancient_literature"])`. It maps a Bible passage → references in patristic/classical works (i.e. *direction* to "quotes and sections of books," exactly as Bryan described). But:
   - It returns **pointers/references, not full text** — so it still needs the "read that section" capability from gap #1 to yield an exact quote.
   - The patristic-phase ("Don't Live in a Historical Vacuum", Phase 10) prompt does **not** push the AI to use it. The AI reached for `search_library` and got snippets instead.

3. **No anti-fabrication guardrail.** Nothing in the agent prompt forbids emitting a "Father X said …" quotation that was not retrieved verbatim from the library. Without gap #1 fixed, such a rule would just make the AI punt (as it eventually did) — so the guardrail and the retrieval tool must ship together.

### Instance C — grammars (2026-05-22, πίστεως Ἰησοῦ / pistis Christou genitive)

The AI tried `lookup_grammar` for the subjective-vs-objective genitive debate and got nothing from Wallace/Robertson, then fell back to "general Greek grammar principles" (its own training) — exactly the substitution failure. **Wallace has a dedicated section on this**; the library has the answer.

**Root cause (confirmed in code):** `_lookup_grammar` → `_search_lexicon` → `idx.lookup(query)` reuses the **lexicon headword index.** Lexicons are headword-keyed (look up πίστις); grammars are NOT — their article IDs are opaque (e.g. `G16W22`) and content is discursive prose with section headings like "The Subjective and Objective Genitive." A headword lookup for "subjective genitive" matches nothing. **The grammar tool is keyed to the wrong access pattern.**

Note: `search_library` (Logos' 6.6 GB full-text index) *would* find Wallace's discussion if scoped to grammars by `resource_type` + `author_contains` — but it returns snippets (gap #1) and the AI used `lookup_grammar` instead of combining them.

**Direction:** route grammar queries through full-text/section search (Logos index or a grammar TOC/section index) instead of the headword index, then pull the full section via the gap-#1 read tool.

## Directions (NOT approved design — for the brainstorm)

- **A new "read full section" tool** that takes a `search_library` hit (resource id + section locator) and/or an `ancient_literature` reference and returns the full exact text of that section/article, so the AI can quote verbatim with a citation. This likely touches the LogosReader / `study.py` article-read layer (cf. `read_article_text`, `find_commentary_section`).
- **Wire `ancient_literature` into the patristic phase** — prompt guidance + possibly make `get_passage_context(ancient_literature)` a proactive call on entering Phase 10.
- **Anti-fabrication guardrail** in the agent prompt: never present a direct quotation (esp. "<Father> said") unless it was returned verbatim by a retrieval tool in this turn's tool results; otherwise describe the theme and say no exact quote was found. Ships *with* the retrieval tool, not before.
- Consider whether the same snippet→full-text gap affects other phases (commentaries, monographs) — it almost certainly does; the patristic phase just exposed it most sharply.

## Related
- Memory: `feedback-llm-role-research-ga`, `feedback-no-fabricated-quotes` (new), `architecture-four-agents`.
- Reverse-engineering history: encrypted `.lbs*` dataset volumes hold the pre-indexed passage data; `EncryptedVolume` API → raw sqlite3 (see MEMORY.md "Key Discoveries").
