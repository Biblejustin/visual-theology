# Homiletics Engine — Phase 1: Analyzers + LLM Tools

**Date:** 2026-05-17
**Status:** Draft — awaiting user review
**Phase scope:** This spec covers Phase 1 only. Phases 2–4 are sketched at the end.

## Problem

The companion currently has strong exegetical tools (lexicons, grammars, cross-refs, commentaries, new full-text search) and skeleton homiletical tools (Christ thread, "So what" gate, time estimator), but no first-class layer that analyzes sermon prep for **homiletical** quality. The known weak spot, repeatedly confirmed by Bryan's wife and the project memory, is that his sermons run long (40 min vs. 25-30 target), trend dense (heavy exposition, thin application), and don't always land. The exegesis is excellent; the bridge to the pulpit is what consistently breaks.

A scattershot fix (a tool here, a prompt there) won't solve this — the right structure is a shared layer that produces homiletical insights from multiple angles, stores them as first-class objects, and feeds delivery surfaces over time (LLM tools now, save-hooks later, pre-flight review later, post-preach learning later).

## Goals

1. Ship a Homiletics Engine that produces structured `Insight` objects from seven independent analyzers covering illustration, objection, counseling, wisdom, Christ-thread, balance, and coherence.
2. Surface the engine to the LLM via seven tools so the companion agent can call them naturally during conversation.
3. Persist insights in a new table so subsequent phases (save-hooks, pre-flight review, post-preach learning) can consume them without rework.
4. Keep analyzers, the Insight type, and delivery surfaces orthogonal — adding a new analyzer or a new firing mode is independent work.

## Non-Goals (deferred to later phases)

- Real-time hooks on outline/card save (Phase 2).
- End-of-prep pre-flight review UI (Phase 3).
- Post-preach learning loop that scores insight value (Phase 4).
- Consolidating tools into a router (`homiletical_helper(facet=…)`) — only if 7 tools prove unwieldy in practice.
- Severity field on Insight (`info/warn/critical`) — deferred until Phase 3 needs it for the review UI.
- Phase field on Insight — deferred until Phase 4 needs it for learning.

## Approach

### Architecture

```
┌─────────────────────────────────────────────┐
│ Homiletics Engine                           │
│                                             │
│  Analyzers (pure functions, no shared state)│
│   • IllustrationFinder                      │
│   • ObjectionFinder                         │
│   • CounselingApplications                  │
│   • WisdomPrinciples                        │
│   • ChristThreadProposer                    │
│   • BalanceGauger                           │
│   • CoherenceChecker                        │
│           │ produce                         │
│           ▼                                 │
│  Insight (frozen dataclass)                 │
│   kind, title, body, citations[],           │
│   confidence, metadata                      │
│           │ stored in                       │
│           ▼                                 │
│  homiletics_insights  (SQLite table)        │
└─────────────────────────────────────────────┘
              │
              ▼ consumed by
   Phase 1: 7 LLM tools  (this spec)
   Phase 2: save hooks   (next spec)
   Phase 3: pre-flight   (next spec)
   Phase 4: learning     (next spec)
```

Analyzers don't know how their output is delivered. Delivery surfaces don't know how insights are computed. They communicate through the `Insight` shape and the `homiletics_insights` table.

### Components

| File | Responsibility |
|------|----------------|
| `tools/workbench/homiletics_engine.py` (new) | `Insight` dataclass; the seven analyzer classes; the embeddings client wrapper. |
| `tools/workbench/companion_db.py` (modify) | Schema migration for `homiletics_insights` table + `save_insight()` / `get_insights()` helpers. |
| `tools/workbench/companion_tools.py` (modify) | Seven new tool definitions + dispatch cases. |
| `tools/workbench/companion_agent.py` (modify) | System prompt addendum that teaches the LLM when to call each tool. |
| `tools/workbench/embeddings_client.py` (new) | Thin Voyage 3 wrapper. Used only by CoherenceChecker in Phase 1; available to others later. |
| `tools/workbench/tests/test_homiletics_*.py` (new) | One test file per analyzer + integration test for the tools. |

### Schema

Two new tables, added via the existing inline-migration pattern in `companion_db.init_db()`:

```sql
CREATE TABLE IF NOT EXISTS homiletics_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN (
        'illustration', 'objection', 'counseling', 'wisdom',
        'christ_thread', 'balance_flag', 'coherence_flag'
    )),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    citations TEXT NOT NULL,                -- JSON array of strings (resource IDs or sermon IDs)
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    metadata TEXT,                          -- JSON object, kind-specific extras (NULL ok)
    created_at TEXT NOT NULL,
    consumed_at TEXT,                       -- set when LLM/user marks it acted on; NULL otherwise
    source_tool TEXT NOT NULL               -- e.g. 'find_illustration', 'gauge_outline_balance'
);
CREATE INDEX IF NOT EXISTS idx_insights_session_kind ON homiletics_insights(session_id, kind);
CREATE INDEX IF NOT EXISTS idx_insights_session_created ON homiletics_insights(session_id, created_at);
```

Sermon-evaluation table for CoherenceChecker is already in place (`sermon_evaluations`, used by the existing sermon evaluator).

For embedding storage we need a small companion table so CoherenceChecker can look up prior claims by similarity without re-embedding every time:

```sql
CREATE TABLE IF NOT EXISTS claim_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_kind TEXT NOT NULL,              -- 'outline_node' | 'sermon_evaluation_claim'
    source_id INTEGER NOT NULL,             -- FK to outline_nodes.id or sermon_evaluations.id
    claim_text TEXT NOT NULL,
    embedding BLOB NOT NULL,                -- packed float32 vector
    embedding_model TEXT NOT NULL,          -- e.g. 'voyage-3'
    created_at TEXT NOT NULL,
    UNIQUE(source_kind, source_id, embedding_model)
);
CREATE INDEX IF NOT EXISTS idx_claim_emb_source ON claim_embeddings(source_kind, source_id);
```

Phase 1 backfills embeddings lazily — when CoherenceChecker is called on a claim, it embeds the claim, embeds any prior unembedded claims it scans, and writes them. Avoids a one-time batch job.

### The `Insight` type

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class Insight:
    kind: str                    # one of the seven 'kind' enum values
    title: str                   # one-line human-readable summary
    body: str                    # markdown body with citations inline
    citations: list[str]         # resource IDs (LLS:...) or sermon IDs (str)
    confidence: float            # 0.0–1.0
    metadata: dict = field(default_factory=dict)  # kind-specific; JSON-serializable
```

`metadata` examples by kind:
- `'illustration'`: `{"theme_tags": [...], "named_entities": [...], "snippet_offset": int}`
- `'christ_thread'`: `{"category": "typology" | "promise_fulfillment" | "contrast" | "redemptive_historical" | "direct_reference", "supporting_refs": [...]}`
- `'objection'`: `{"objection_class": "theological" | "pastoral" | "cultural" | "honest_doubt", "rebuttal_pointer": "..."}`
- `'counseling'`: `{"pastoral_situation": "marriage" | "suffering" | "anxiety" | ..., "tier": "preventative" | "remedial" | "diagnostic"}`
- `'wisdom'`: `{"principle_tags": [...], "cross_refs": [...]}`
- `'balance_flag'`: `{"ratio_exegetical": float, "ratio_application": float, "flagged_section_ids": [...]}`
- `'coherence_flag'`: `{"prior_sermon_id": int, "similarity": float, "conflict": bool, "conflict_summary": "..."}`

### The seven analyzers (Phase 1 contracts)

Each analyzer is a class with a `run()` method that returns `list[Insight]` and a `name: str` class attribute. All run methods are idempotent and take a `CompanionDB` reference; they don't share state.

| Analyzer class | `run()` signature | Method |
|----------------|-------------------|--------|
| `IllustrationFinder` | `run(db, theme: str, passage: dict \| None = None, count: int = 3) -> list[Insight]` | Calls `logos_search.get_searcher().search()` with `resource_type_prefix='text.monograph'`. Re-ranks snippets by a narrative-concreteness signal: count of Title Case name sequences (regex `\b[A-Z][a-z]+(?: [A-Z][a-z]+){0,2}\b`), year mentions (`\b1?[0-9]{3,4}\b`), and place-name density (lookup against a small built-in gazetteer of biblical + classical place names). Snippets with score above a fixed threshold survive. Wraps each surviving snippet as an `Insight(kind='illustration', citations=[resource_id], confidence=score)`. |
| `ObjectionFinder` | `run(db, claim: str, passage: dict | None = None, count: int = 3) -> list[Insight]` | Two-pronged: (a) `search_library` for monographs that engage common objections (apologetics resources, systematic theology); (b) single Opus call with a system prompt naming the four categories (theological, pastoral, cultural, honest_doubt) and asking for likely objections + rebuttal pointers. Merges results; tags each with `objection_class`. |
| `CounselingApplications` | `run(db, passage: dict, count: int = 3) -> list[Insight]` | `search_library` filtered to biblical-counseling/pastoral resources, plus an Opus pass mapping the passage to common pastoral situations. Each insight tagged with `pastoral_situation`. |
| `WisdomPrinciples` | `run(db, passage: dict, count: int = 3) -> list[Insight]` | Opus call distilling 2-3 transferable principles. Cross-refs via `search_library` to wisdom literature (Proverbs, Job, Ecclesiastes, Sirach if owned) and Puritan/patristic applications. |
| `ChristThreadProposer` | `run(db, passage: dict, point: str \| None = None, count: int = 3) -> list[Insight]` | Single Opus call with a Reformed homiletical system prompt that names the five categories (typology / promise_fulfillment / contrast / redemptive_historical / direct_reference) and asks for up to `count` angles distributed across them. Each angle is returned as one `Insight` with category in metadata and 1-2 supporting verse refs in `citations`. |
| `BalanceGauger` | `run(db, session_id: int) -> list[Insight]` | Reads `outline_nodes` for the session. If a node has no homiletical_type tag, runs an Opus tagging pass classifying each as `exegetical_claim` / `application` / `illustration` / `christ_thread`. Returns ONE summary insight with `metadata.ratio_exegetical`, plus zero-or-more flag insights for sections that are 100% exegetical. |
| `CoherenceChecker` | `run(db, claim: str, session_id: int, lookback_days: int = 365) -> list[Insight]` | Embeds the claim via `voyage-3`. Loads embeddings for prior outline-node claims + sermon-evaluation claims within the lookback window, embedding any unembedded ones lazily. Returns top-3 above similarity threshold 0.75; flags as `conflict=True` if a fast Opus call judges the prior position contradicts. |

All analyzers write their produced insights to `homiletics_insights` via `db.save_insight(insight, session_id, source_tool)` and return the list to the caller.

### The seven LLM tools (Phase 1 surface)

| Tool name | Wraps | Required input | Returns |
|-----------|-------|----------------|---------|
| `find_illustration` | `IllustrationFinder` | `theme: str` | `{insights: [...]}` |
| `find_objections` | `ObjectionFinder` | `claim: str` | `{insights: [...]}` |
| `find_counseling_applications` | `CounselingApplications` | (uses session passage) | `{insights: [...]}` |
| `find_wisdom_principles` | `WisdomPrinciples` | (uses session passage) | `{insights: [...]}` |
| `suggest_christ_thread` | `ChristThreadProposer` | (uses session passage) | `{insights: [...]}` |
| `gauge_outline_balance` | `BalanceGauger` | (uses session_id) | `{summary, insights: [...]}` |
| `check_sermon_coherence` | `CoherenceChecker` | `claim: str` | `{insights: [...]}` |

Each tool definition includes a docstring naming the prep phase it's most useful in, so the LLM picks well.

### Embeddings client

`tools/workbench/embeddings_client.py` exposes:

```python
class EmbeddingsClient:
    def __init__(self, api_key: str, model: str = 'voyage-3'): ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...   # batch
    def embed_one(self, text: str) -> list[float]: ...
    @property
    def dimension(self) -> int: ...   # 1024 for voyage-3
```

Vectors are stored in `claim_embeddings.embedding` as packed `float32` bytes (`struct.pack(f'{dimension}f', *vector)`). Cosine similarity is computed in Python via `numpy.dot` on unpacked vectors. With ~1000 prior claims and 1024-dim float32 vectors, in-memory ranking is well under a millisecond — no vector index needed in Phase 1.

Production uses Voyage AI's HTTP API. Tests use a `CannedEmbeddingsClient` returning deterministic 8-dim vectors keyed by text hash, matching the pattern of `CannedLLMClient` in `llm_client.py`.

API key lives in `app_secrets.voyage_api_key()` alongside the existing Anthropic key. Cost: ~$0.00005 per embedding at Voyage 3 prices; negligible at sermon-prep scale.

### Data flow — example

A pastor in conversation says "I want to find an illustration for grace as patronage." The LLM:

1. Calls `find_illustration(theme='grace as patronage')`.
2. Tool wrapper invokes `IllustrationFinder.run(db, theme='grace as patronage', passage=session.passage)`.
3. Analyzer calls `search_library` with type filter; re-ranks top hits.
4. For each of N surviving snippets, builds an `Insight(kind='illustration', title="Roman patronage as model for grace", body=snippet_text, citations=['LLS:GRECOROMWRLD'], confidence=0.78, metadata={'theme_tags': ['patronage','grace'], 'named_entities': ['Augustus','Pliny']})`.
5. Calls `db.save_insight(insight, session_id, source_tool='find_illustration')`.
6. Returns `{insights: [...]}` to the LLM.
7. LLM weaves the illustration into its response and may save the chosen one to the outline.

The same `Insight` object is now in `homiletics_insights` waiting for Phase 2/3/4 to find it.

## Error handling

- **Search subprocess down:** Analyzers that depend on `search_library` (illustration, objection, counseling, wisdom) return `[]` with a single low-confidence "search unavailable" insight; LLM degrades gracefully.
- **LLM call fails (network, rate-limit):** Analyzer returns `[]` and writes a debug entry to stderr; the calling tool surfaces a friendly error.
- **Embeddings client fails:** CoherenceChecker falls back to keyword overlap (Jaccard on token sets) so the feature still works degraded.
- **Schema constraint violation:** Caller's error — analyzers never produce out-of-range confidence or unknown kind; tests enforce this.

No silent failures: every analyzer logs both success and failure with structured fields.

## Testing

For each analyzer (`tests/test_homiletics_illustration_finder.py` etc.):
- Happy path with a canned search/LLM client returning a known shape.
- Empty-results path (`search_library` returns nothing → analyzer returns `[]`).
- Validation: returned insights respect the schema CHECK constraints (correct `kind`, confidence in range).
- Persistence: `run()` writes to `homiletics_insights` with correct `source_tool`.

For the tools (`tests/test_homiletics_tools.py`):
- `execute_tool('find_illustration', ...)` returns the analyzer's output.
- Tool call with missing required input returns a friendly error.

For the engine end-to-end (`tests/test_homiletics_engine_e2e.py`):
- Seed an outline with 5 nodes; call `gauge_outline_balance`; assert ratio is computed and at least one flag insight surfaces if all 5 nodes are exegetical.
- Seed two prior sermons mentioning "propitiation"; call `check_sermon_coherence(claim="Christ's death satisfied God's wrath")` with mocked embeddings client; assert prior sermons surface.

## Phase 1 sequencing (build order within this spec)

1. Schema migration for both new tables + `save_insight`/`get_insights` helpers.
2. `Insight` dataclass.
3. `EmbeddingsClient` + `CannedEmbeddingsClient` + `app_secrets.voyage_api_key()`.
4. `IllustrationFinder` — lowest risk, leverages existing search.
5. `ChristThreadProposer` — single Opus call, easy contract.
6. `WisdomPrinciples` — same shape as ChristThread.
7. `ObjectionFinder` — adds the merge logic.
8. `CounselingApplications` — same shape as ObjectionFinder.
9. `BalanceGauger` — first analyzer that reads other tables (outline_nodes).
10. `CoherenceChecker` — adds embeddings dependency; last.
11. Seven tool wrappers + dispatch cases.
12. System prompt addendum teaching tool selection.
13. E2E tests + manual verification on session 20.

## Future phases (sketched, not in this spec)

- **Phase 2 — save hooks.** `companion_db.save_outline_node()` and `save_card_response()` fire matching analyzers async (background thread), write Insights, push to a new `/study/session/<id>/insights/stream` SSE endpoint that updates the session UI.
- **Phase 3 — pre-flight review.** A new `/study/session/<id>/preflight` route renders all unconsumed Insights grouped by kind, with check/dismiss actions. Reads like a code review.
- **Phase 4 — post-preach learning.** Sermon evaluator joins `homiletics_insights.consumed_at` against transcript content (via fuzzy match) to learn which insight kinds and which analyzers actually moved the needle on evaluator scores. Feeds back into analyzer ranking and tool descriptions over time.

## Open decisions accepted during brainstorming

- **Analyzer count:** 7 (the original 4 + objection + counseling + wisdom). Confirmed.
- **Tool surface:** 7 separate tools, no router. Consolidate later if needed.
- **Coherence method:** Voyage 3 embeddings with keyword-overlap fallback.
- **Insight type:** ship with the 6 proposed fields; no `severity` or `phase` until Phase 3/4 needs them.
