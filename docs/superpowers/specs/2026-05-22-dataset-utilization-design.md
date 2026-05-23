# Dataset Utilization — Design Spec (Project B)

**Date:** 2026-05-22
**Status:** Design approved (brainstorm). Spec pending Bryan's review → implementation plan.
**Sequence:** Project A (`docs/superpowers/specs/2026-05-22-library-retrieval-fidelity-design.md`) shipped first. This is the follow-on.
**Companion ideas doc:** `docs/superpowers/ideas/2026-05-22-library-quote-fidelity-and-ancient-lit-datasets.md`

## Problem (recap)

The study partner's library contains **~115 research datasets** (`.lbs*` encrypted volumes; 116 reverse-interlinears + 38 interactives + 6 pronunciation sets excluded as not research-grade). Only **~15 are wired** today, via one bespoke function per dataset in `dataset_tools.py`. The rest — including syntax graphs (Cascadia, Andersen-Forbes, Lexham Clauses) directly relevant to grammar questions, Bullinger's *Figures of Speech*, Discourse GNT/HOT, the "All the X in the Bible" topical sets, Counseling Themes, Jesus' Fulfillment of Messianic Prophecy, Lexham Cultural / Systematic Theology / Church History ontologies, Pericopes, Phrase Concordance, and more — are dark.

The current bespoke-function pattern doesn't scale to ~100 additional datasets, and per `[[feedback-tools-only-as-useful-as-called]]` simply *adding* tools isn't enough — the agent must reliably invoke them.

## Design principle

**Two jobs, both required:** (1) make every dataset *reachable* via a uniform, extensible mechanism, and (2) make sure the agent *actually reaches for it* at the right moment, through named high-frequency tools, phase-aware prompt cues, and a discoverable catalog (`list_datasets`).

## Architecture

### 1. Dataset registry — `tools/workbench/dataset_registry.py`

A single source of truth that **auto-discovers** what the library contains. Boots once per process:

- Joins `Data/<user>/LibraryCatalog/catalog.db` × `Data/<user>/ResourceManager/ResourceManager.db` for every resource whose `rm.Resources.Location` matches `%.lbs%`. Collects `resource_id`, `title`, `type` (`lbx.*`), `filename` (basename), full path.
- **Denylist** by `type`: `lbx.reverse-interlinear`, `lbx.interactive`, `lbx.pronunciations`. (Configurable constant `DENYLIST_TYPES`.)
- Tags each survivor with a `schema_family` inferred from the filename extension:

  | extension(s) | family | notes |
  |---|---|---|
  | `.lbssd` | `supplemental` | "Bible knowledgebase" + figurative + topical sets |
  | `.lbsxrf` | `xref` | systematic / biblical-theology / confessional / grammar xrefs |
  | `.lbsbpd .lbsplc .lbsthg .lbsevt .lbsbrd .lbsbio .lbsmps` | `entity` | people / places / things / events / referents / biographies / maps |
  | `.lbsclv .lbssyn` | `syntax` | Cascadia syntax graphs + Andersen-Forbes phrase markers |
  | `.lbscls` | `clauses` | Lexham Clauses datasets |
  | `.lbsgrm` | `grammar_ds` | grammatical-relationships graphs |
  | `.lbslco .lbslsto .lbsch .lbslcv .lbswsn .lbsuav` | `ontology` | cultural / ST / church-history / controlled-vocab / word-senses / annotation |
  | `.lbsptd .lbstod .lbsanc .lbsct .lbspsd .lbslms .lbspcd .lbsnt .lbsfbm .lbsexc .lbsspd` | `thematic` | preaching themes, thematic outlines, ancient lit, counseling themes, pericopes, lemmas, phrase concordance, etc. |
  | anything else | `misc` | falls back to generic try-everything via the encrypted-volume reader |

- Registry survives library updates: new datasets auto-appear, missing ones auto-drop. **No hand-maintained YAML** to keep in sync.
- Public API: `list_all() -> [Dataset]`, `find(dataset_id) -> Dataset | None`, `by_family(family) -> [Dataset]`, `by_phase(phase_key) -> [Dataset]` (phase→family→datasets via a small mapping; see §4).

### 2. Per-schema adapters — `tools/workbench/dataset_adapters/`

One adapter module per `schema_family` (not per dataset). All implement the same minimal interface:

```python
def query(reference: dict, dataset: Dataset, *, limit: int = 25) -> dict:
    """Returns {dataset, dataset_type, title, results: [...], note}.
       'results' is a list of dicts whose shape is family-specific but always
       includes at least a passage anchor + a payload field. 'note' carries
       diagnostic info (e.g. 'no matches at this passage')."""
```

Eight adapter files, scaled to the family's complexity:

- `supplemental.py` — wraps the existing `query_*` patterns in `dataset_tools.py` (`SupplementalData.db` schema). Most numerous family; reuses the existing pattern.
- `xref.py` — wraps `query_curated_cross_refs` / `query_theology_xrefs` machinery.
- `entity.py` — wraps `_query_entity_db` (people/places/things/events/etc.).
- `syntax.py` — Cascadia / Andersen-Forbes phrase-marker graphs. Reads constituency/phrase data from `.lbsclv`/`.lbssyn` via the `EncryptedVolume` reader; surfaces a digestible summary (head terms + relation types) — not raw graph dumps.
- `clauses.py` — Lexham Clauses (`.lbscls`); returns clause-by-clause structure for the passage.
- `grammar_ds.py` — `.lbsgrm` grammatical relationships (Andersen-Forbes He/Aram, Cascadia GNT). Tabular relations.
- `ontology.py` — `.lbslco`/`.lbslsto`/`.lbsch`/`.lbslcv`/`.lbswsn` — topic-tagged sections; returns entries whose passage matches.
- `thematic.py` — covers the long-tail `.lbs*` extensions in the thematic family. Best-effort schema introspection (`query_dataset_tables` already exists in `dataset_tools.py`).

Adding a new dataset in a known family = **zero adapter code**. Adding a new family = one new adapter file.

### 3. Three new tools (added in `companion_tools.py`)

- **`list_datasets(category=None)`** — returns the registry filtered by family, phase, or topic. Lets the agent discover what's available without scanning 100 enum entries.
  - Input: `{category?: str}` where category is a `schema_family`, a `lbx.*` type, or a phase key (`p10_patristics`, etc.).
  - Output: `{count, datasets: [{dataset_id, title, family, type}]}`.
- **`query_dataset(dataset_id, reference, limit?)`** — generic long-tail accessor. Routes through `registry.find(dataset_id)` → schema adapter → returns the normalized envelope. Anything in the registry is reachable from day one.
- **Named high-frequency tools (B-1 ships these two):**
  - `lookup_syntax_graph(reference, testament='nt')` — fulfills the A-handoff promise. Routes to Cascadia Syntax Graphs (NT) or Andersen-Forbes Hebrew (OT) via the `syntax` adapter. Returns digested structure for the passage (head + dependents + relation type per phrase).
  - `lookup_bullinger_figure(reference)` — Bullinger's *Figures of Speech* (`.lbssd` topical set, `BULLINGER-FIGURES.lbssd`). Rhetorical figures keyed to the passage. Classic homiletical mine; high reach-for frequency in exegesis/big-picture phases.

  **Follow-on slices (B-2+, not B-1):** counseling-themes, messianic-prophecy-fulfillment, discourse, pericopes, "All the X in the Bible" named tools, ontologies. Naming follows observed usage — the generic `query_dataset` makes them reachable in B-1 even without naming.

### 4. Phase-aware invocation cues — `tools/workbench/persona_prompts.py`

A small `PHASE_DATASET_HINTS` mapping (phase_key → list of dataset names + brief "use when..." cue) is rendered into the existing `_ACTIVE_FOCUS[*]['tools_first']` blocks. Examples:

- `p04_exegesis` → `lookup_syntax_graph`, `query_dataset("PhraseConcordance")`
- `p06_context` → `query_dataset("BiblicalEventsDataset")`, `query_dataset("BiblicalPlacesMaps")`
- `p07_big_picture` → existing `propositional_outline`, plus `query_dataset("PericopeSetsDataset")`
- `p08_christ` → `query_dataset("JesusFulfillmentOfMessianicProphecyDataset")`
- `p09_systematics` → existing systematic xrefs + `query_dataset("LSTO")` (Lexham Systematic Theology Ontology)
- `p10_patristics` → existing `ancient_literature` (from A) + `query_dataset("ChurchHistory")`
- `p13_sermon_work` → `lookup_bullinger_figure`, `query_dataset("AllThePrayersInTheBibleDataset")` (illustration material)

The research-partner system prompt mentions `list_datasets()` once so the agent knows the catalog exists for anything not pre-cued.

## Components touched

- New: `tools/workbench/dataset_registry.py`, `tools/workbench/dataset_adapters/__init__.py` + 8 family files.
- Modify: `tools/workbench/companion_tools.py` (add 3 new tool defs + dispatch + handlers); `tools/workbench/persona_prompts.py` (phase cues + `PHASE_DATASET_HINTS`); `tools/workbench/dataset_tools.py` (extract reusable query helpers into the adapters, keep existing call sites working).
- Tests: TDD for registry (mock catalog DBs at the boundary), each adapter (mock the `EncryptedVolume` reader at the boundary), the 3 new tools, prompt cues.

## Acceptance tests

1. **Syntax-graph reach:** post Project B, ask a Romans 3:22 *pistis Christou* genitive follow-up → agent calls **both** `lookup_grammar` (Wallace prose from A) **and** `lookup_syntax_graph` (Cascadia structure) and quotes/cites both.
2. **Catalog discovery:** `list_datasets("topical")` returns the "All the X in the Bible" sets, Bullinger figures, counseling themes, etc. — every dataset the registry classifies as thematic.
3. **Long-tail reachability:** `query_dataset("AllThePrayersInTheBibleDataset", "Romans 3:25-26")` returns a normalized envelope (either matched entries or `{note: "no matches at this passage"}`); same shape for any dataset in the registry.
4. **Phase invocation:** in phase 10 (patristic), agent proactively reaches for `ancient_literature` + `query_dataset("ChurchHistory")` without being told.
5. **Registry resilience:** delete one `.lbs*` file → registry boots cleanly with that dataset absent, no crash; add a new `.lbs*` file → registry surfaces it next boot.
6. **No regression:** existing `get_passage_data`, `get_passage_context`, `get_cross_reference_network` calls return the same results they did before (their internals migrate to delegate to the registry/adapters, the public tool shape is preserved).

## Scope boundary

- **In B-1:** registry, 8 adapters, `list_datasets` + `query_dataset` + `lookup_syntax_graph` + `lookup_bullinger_figure`, phase cues, full TDD coverage.
- **Out of B-1 (follow-on B-2+):** additional named tools (counseling themes, messianic-prophecy-fulfillment, discourse, pericopes, "All the X" set, ontologies) — added per observed agent reach + Bryan's feedback. Cheap because registry + adapters already exist.
- **Out of scope entirely (handled by A or other work):** the retrieval-fidelity fix (read full quote, lookup_grammar reroute, anti-fabrication) — already shipped in Project A.

## Connections
- Spec: `2026-05-22-library-retrieval-fidelity-design.md` (Project A — already shipped).
- Memories: `[[feedback-tools-only-as-useful-as-called]]`, `[[feedback-llm-role-research-ga]]`, `[[feedback-no-fabricated-quotes]]`.
- Reference: `dataset_tools.py` (current bespoke pattern — partially absorbed into adapters here).
