# Library Retrieval Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the study partner pull full, exact text from the library (so it quotes verbatim instead of fabricating), fix `lookup_grammar` to actually find discursive grammar sections, and make the agent reliably call these.

**Architecture:** Expose the full-article read that `logos_search` already performs (and discards as a snippet) via a new `read_library_section` tool wrapping `study.read_article_text`. Reroute `lookup_grammar` from the headword index to the full-text searcher filtered to grammar resources. Add an anti-fabrication guardrail + invocation cues to the prompts.

**Tech Stack:** Python, Flask, pytest. Spec: `docs/superpowers/specs/2026-05-22-library-retrieval-fidelity-design.md`.

**Environment for running tests:**
```bash
export PATH="/opt/homebrew/opt/dotnet@8/bin:$PATH"; export DOTNET_ROOT="/opt/homebrew/opt/dotnet@8/libexec"
cd /Volumes/External/Logos4/tools/workbench
```

---

## File structure

- `tools/workbench/companion_tools.py` — add `read_library_section` tool def + dispatch + `_read_library_section`; reroute `_lookup_grammar`; update `search_library` description.
- `tools/workbench/companion_agent.py` — add anti-fabrication guardrail to the ALWAYS-active block (~line 24).
- `tools/workbench/persona_prompts.py` — chain `ancient_literature` → `read_library_section` in the patristic (p10) prompt; add `read_library_section` invocation cue to the research prompt.
- `tools/workbench/tests/test_read_library_section.py` — new.
- `tools/workbench/tests/test_lookup_grammar_fulltext.py` — new.
- `tools/workbench/tests/test_retrieval_prompts.py` — new.

---

## Task 1: `read_library_section` tool

**Files:**
- Modify: `tools/workbench/companion_tools.py` (TOOLS list, dispatch chain, new `_read_library_section`)
- Test: `tools/workbench/tests/test_read_library_section.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_read_library_section.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import companion_tools


def test_read_library_section_returns_full_text(monkeypatch):
    captured = {}
    def fake_read(resource_file, article_num, max_chars=8000):
        captured['args'] = (resource_file, article_num, max_chars)
        return "Full verbatim article text from Wallace."
    monkeypatch.setattr(companion_tools, 'read_article_text', fake_read, raising=False)
    out = companion_tools.execute_tool(
        "read_library_section",
        {"filename": "WALLACEGGBB.logos4", "article_num": 4718, "max_chars": 8000},
        {})
    assert out["text"] == "Full verbatim article text from Wallace."
    assert captured['args'] == ("WALLACEGGBB.logos4", 4718, 8000)


def test_read_library_section_handles_missing_text(monkeypatch):
    monkeypatch.setattr(companion_tools, 'read_article_text', lambda *a, **k: None, raising=False)
    out = companion_tools.execute_tool(
        "read_library_section", {"filename": "X.logos4", "article_num": 1}, {})
    assert "error" in out
```

> Dispatch entry is `execute_tool(tool_name, tool_input, session_context)` (companion_tools.py:596), an `if/elif tool_name == ...` chain wrapped in `try/except: return {"error": str(e)}`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_read_library_section.py -q`
Expected: FAIL — `read_library_section` unknown tool / `read_article_text` attribute missing.

- [ ] **Step 3: Add the tool definition** to the `TOOLS` list in `companion_tools.py` (alongside `search_library`):

```python
{
    "name": "read_library_section",
    "description": (
        "Fetch the FULL, exact text of a library section found via search_library. "
        "search_library returns only a ~500-char snippet; to quote a source verbatim "
        "you MUST call this with the result's `filename` and the section's `article_num`. "
        "Never quote a source from the snippet alone, and never quote anything this tool "
        "(or another retrieval tool) did not return."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "The `filename` from a search_library result, e.g. 'WALLACEGGBB.logos4'."},
            "article_num": {"type": "integer", "description": "The section's `article_num` from a search_library result."},
            "max_chars": {"type": "integer", "description": "Max characters to return (default 8000)."},
        },
        "required": ["filename", "article_num"],
    },
},
```

- [ ] **Step 4: Add the dispatch + handler.** Add an `elif` next to `search_library` in the dispatch chain:

```python
        elif tool_name == "read_library_section":
            return _read_library_section(tool_input)
```

And define the handler (near `_search_library`):

```python
def _read_library_section(tool_input):
    """Return the full exact text of a library section (resolves to read_article_text)."""
    from study import read_article_text
    filename = (tool_input.get("filename") or "").strip()
    article_num = tool_input.get("article_num")
    if not filename or article_num is None:
        return {"error": "filename and article_num are required (from a search_library result)"}
    max_chars = max(500, min(int(tool_input.get("max_chars", 8000)), 30000))
    try:
        text = read_article_text(filename, int(article_num), max_chars=max_chars)
    except Exception as e:
        return {"error": f"read_failed: {type(e).__name__}: {e}"}
    if not text:
        return {"error": "no_text", "message": f"No text for {filename} article {article_num}."}
    return {"filename": filename, "article_num": int(article_num), "text": text}
```

> The test monkeypatches `companion_tools.read_article_text`, so also add a module-level import at the top of `companion_tools.py` (with the other `from study import (...)`): add `read_article_text` to that import list. The local import inside the handler is a fallback; the module-level name is what the test patches.

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_read_library_section.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add tools/workbench/companion_tools.py tools/workbench/tests/test_read_library_section.py
git commit -m "feat: read_library_section tool — full verbatim text from a search hit"
```

---

## Task 2: Reroute `lookup_grammar` to full-text search

**Files:**
- Modify: `tools/workbench/companion_tools.py` (`_lookup_grammar`)
- Test: `tools/workbench/tests/test_lookup_grammar_fulltext.py`

Context: `_lookup_grammar` currently calls `_search_lexicon` (headword index), which misses discursive grammar sections (e.g. Wallace on the subjective/objective genitive). `ALL_GRAMMARS` / `NT_GRAMMARS` / `OT_GRAMMARS` (companion_tools.py:48-64) are lists of dicts with `file` and `abbrev`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lookup_grammar_fulltext.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import companion_tools


class _FakeSearcher:
    def search(self, query, **kw):
        # One hit in a grammar resource (filename matches a known grammar).
        gram_file = companion_tools.NT_GRAMMARS[0]["file"]
        return {"ok": True, "results": [{
            "resource_id": "LLS:GRAMMAR", "filename": gram_file,
            "title": "Greek Grammar Beyond the Basics", "abbrev": "Wallace",
            "sections": [{"article_num": 1234, "snippet": "…the objective genitive…"}],
        }]}


def test_lookup_grammar_uses_fulltext_and_returns_section(monkeypatch):
    monkeypatch.setattr(companion_tools, 'get_searcher', lambda: _FakeSearcher(), raising=False)
    monkeypatch.setattr(companion_tools, 'read_article_text',
                        lambda f, a, max_chars=8000: "Wallace's full section on the objective genitive.",
                        raising=False)
    out = companion_tools._lookup_grammar({"query": "subjective vs objective genitive", "testament": "nt"}, {})
    entries = out["entries"]
    assert entries, "expected a grammar section"
    assert "objective genitive" in entries[0]["text"].lower()
    assert entries[0]["abbrev"]  # carries the source abbrev for citation
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_lookup_grammar_fulltext.py -q`
Expected: FAIL — `get_searcher` not patchable / `_lookup_grammar` still uses the headword index and returns no entry for this mocked path.

- [ ] **Step 3: Rewrite `_lookup_grammar`** to use the searcher filtered to grammar files, then read the best section's full text:

```python
def _lookup_grammar(tool_input, session_context):
    """Search Greek/Hebrew grammars by full text (not headword index), then
    return the matching section's full text. The headword index missed
    discursive sections like Wallace on the subjective/objective genitive."""
    from logos_search import get_searcher  # module-level alias added below for tests
    query = (tool_input.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    testament = tool_input.get("testament", "nt")
    requested = tool_input.get("grammars")
    pool = ALL_GRAMMARS if requested else (NT_GRAMMARS if testament == "nt" else OT_GRAMMARS)
    if requested:
        pool = [g for g in ALL_GRAMMARS if g["abbrev"] in requested] or pool
    grammar_files = {g["file"] for g in pool}

    try:
        result = get_searcher().search(query, limit=15)
    except Exception as e:
        return {"error": f"grammar_search_failed: {type(e).__name__}: {e}", "query": query}
    hits = (result or {}).get("results") or []

    entries = []
    for h in hits:
        if h.get("filename") not in grammar_files:
            continue
        sections = h.get("sections") or []
        if not sections:
            continue
        art = sections[0].get("article_num")
        if art is None:
            continue
        text = read_article_text(h["filename"], int(art), max_chars=6000)
        if not text:
            continue
        entries.append({
            "abbrev": h.get("abbrev") or "",
            "title": h.get("title") or "",
            "filename": h.get("filename"),
            "article_num": int(art),
            "text": text,
        })
        if len(entries) >= 2:
            break

    return {"query": query, "testament": testament, "entries": entries,
            "searched": sorted(grammar_files)}
```

Add a module-level alias so the test can patch it: near the top of `companion_tools.py`, add `from logos_search import get_searcher`. (Keeps the patch target stable; the local import inside the function is harmless.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_lookup_grammar_fulltext.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/companion_tools.py tools/workbench/tests/test_lookup_grammar_fulltext.py
git commit -m "fix: lookup_grammar uses full-text search + reads full section (genitive miss)"
```

---

## Task 3: Anti-fabrication guardrail + invocation cues

**Files:**
- Modify: `tools/workbench/companion_agent.py` (ALWAYS-active guardrail block ~line 24)
- Modify: `tools/workbench/persona_prompts.py` (research prompt invocation cue; p10 patristic chain)
- Test: `tools/workbench/tests/test_retrieval_prompts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_retrieval_prompts.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from persona_prompts import build_research_partner_prompt


def _research_prompt():
    # Signature (persona_prompts.py:281): keyword-only —
    # passage, genre, session_elapsed_seconds, current_phase, outline_summary,
    # card_work_summary, coaching_context, elapsed_in_phase_seconds.
    return build_research_partner_prompt(
        passage="Romans 3:25-26", genre="epistle", session_elapsed_seconds=0,
        current_phase="p10_patristics", card_work_summary="", coaching_context="")


def test_prompt_forbids_fabricated_quotes():
    p = _research_prompt().lower()
    assert "verbatim" in p
    assert "read_library_section" in p
    # Must tell the model not to quote what a tool didn't return.
    assert "do not" in p or "never" in p


def test_patristic_phase_chains_ancient_literature_to_full_text():
    p = _research_prompt()
    assert "ancient_literature" in p.lower()
    assert "read_library_section" in p
```

> `build_research_partner_prompt` is keyword-only; `current_phase` carries the v2 phase key (e.g. `'p10_patristics'`), which drives the active-focus block.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_retrieval_prompts.py -q`
Expected: FAIL — prompt lacks "verbatim"/"read_library_section".

- [ ] **Step 3: Add the anti-fabrication guardrail** to the ALWAYS-active guardrail string in `companion_agent.py` (the block starting ~line 24):

```
SOURCE FIDELITY (always): Never present a direct quotation — from a Church
father, commentator, theologian, grammar, or confession — unless a retrieval
tool returned it verbatim in this turn. If you don't have the exact text, say
so plainly and describe the idea in your own words; do NOT invent attributed
quotes. To quote a source found via search_library, first call
read_library_section with its filename + article_num.
```

- [ ] **Step 4: Add invocation cues** in `persona_prompts.py`:
  - In `build_research_partner_prompt`'s tools paragraph (~line 337-339), append: ``"To quote any source exactly, call `search_library` then `read_library_section` — never quote from a snippet."``
  - In the p10 patristic prompt section (~line 227-229), append: ``"Pull patristic references with `get_passage_context (ancient_literature)`, then fetch the exact text of any father you cite via `search_library` + `read_library_section`. Quote verbatim or not at all."``

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_retrieval_prompts.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tools/workbench/companion_agent.py tools/workbench/persona_prompts.py tools/workbench/tests/test_retrieval_prompts.py
git commit -m "feat: anti-fabrication guardrail + read_library_section invocation cues"
```

---

## Task 4: Full-suite regression + live acceptance

- [ ] **Step 1: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: all pass (prior baseline 1090 + the new tests).

- [ ] **Step 2: Live acceptance (this Mac, reader available)** — write a one-off script `tools/_accept_retrieval.py` that, against the real searcher + reader:
  1. `search_library("subjective objective genitive", resource_type=None)` → take a grammar hit → `read_library_section(filename, article_num)` → assert non-empty full text longer than a snippet (>1000 chars).
  2. `_lookup_grammar({"query": "subjective vs objective genitive", "testament": "nt"})` → assert an entry whose text mentions "genitive".
  Run it, confirm real verbatim text returns. Delete the script after (it's a smoke check, not a kept test).

- [ ] **Step 3: Deploy + verify**

```bash
pm2 restart study-companion && pm2 save
```
Confirm: single fresh LogosReader as a child of the new server PID; `Batch reader ready` in logs; `curl -s -o /dev/null -w "%{http_code}" http://localhost:5111/study/` → 302. (Per [[feedback_ui_verify_before_done]].)

- [ ] **Step 4: Final commit (if any cleanup)** and report to Bryan with the acceptance evidence (the genitive question now returns Wallace's actual text; no fabricated patristic quotes).

---

## Notes for the implementer
- TDD strictly: failing test first, watch it fail, minimal code, watch it pass.
- Mocks only for the subprocess boundaries (searcher, reader) — never mock the logic under test.
- `read_article_text` accepts a bare filename (e.g. `"WALLACEGGBB.logos4"`), which is exactly what `search_library` returns as `filename`.
- Do NOT touch the cutoff override (auto-expires Sat 5pm; separate cleanup) or unrelated files.
- After this plan: **Project B (dataset utilization)** follows immediately — syntax-graph datasets are its first targets.
