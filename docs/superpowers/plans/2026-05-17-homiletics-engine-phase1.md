# Homiletics Engine — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Phase 1 of the Homiletics Engine — seven analyzers (illustration, objection, counseling, wisdom, christ_thread, balance, coherence) producing a common `Insight` type, persisted to a new SQLite table, and exposed to the companion LLM via seven tools.

**Architecture:** A pure-function analyzer layer in `homiletics_engine.py` writes `Insight` rows to `homiletics_insights`. Each analyzer takes a `CompanionDB` reference; they share no state. Seven LLM tool wrappers in `companion_tools.py` dispatch to the analyzers. CoherenceChecker uses local `BAAI/bge-large-en-v1.5` embeddings (via `sentence-transformers`) with all model + framework caches on the external drive.

**Tech Stack:** Python 3 + Flask, SQLite via `sqlite3`, `anthropic` SDK via existing `llm_client.py`, `sentence-transformers` + `torch` + `numpy` (new local dependencies stored on `/Volumes/External/Logos4/Data/`), pytest.

**Spec:** `docs/superpowers/specs/2026-05-17-homiletics-engine-phase1-design.md`

**Note on table name:** the spec references `sermon_evaluations` but the actual existing table is `sermon_reviews`. CoherenceChecker tasks in this plan use `sermon_reviews`.

---

### Task 1: Schema migration — homiletics_insights, claim_embeddings, outline_nodes tag columns

**Files:**
- Modify: `tools/workbench/companion_db.py` (extend the inline migration block)
- Test: `tools/workbench/tests/test_homiletics_schema.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_homiletics_schema.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    yield d
    os.unlink(path)


def test_homiletics_insights_table_exists_with_correct_columns(db):
    cols = {r[1] for r in db._conn().execute("PRAGMA table_info(homiletics_insights)").fetchall()}
    required = {'id', 'session_id', 'kind', 'title', 'body', 'citations',
                'confidence', 'metadata', 'created_at', 'consumed_at', 'source_tool'}
    assert required.issubset(cols), f"missing: {required - cols}"


def test_homiletics_insights_kind_check_constraint(db):
    conn = db._conn()
    # Invalid kind should fail
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO homiletics_insights (session_id, kind, title, body, "
            "citations, confidence, created_at, source_tool) "
            "VALUES (1, 'invalid_kind', 't', 'b', '[]', 0.5, '2026-05-17', 'x')"
        )


def test_claim_embeddings_table_exists(db):
    cols = {r[1] for r in db._conn().execute("PRAGMA table_info(claim_embeddings)").fetchall()}
    required = {'id', 'source_kind', 'source_id', 'claim_text', 'embedding',
                'embedding_model', 'created_at'}
    assert required.issubset(cols), f"missing: {required - cols}"


def test_outline_nodes_has_homiletical_type_columns(db):
    cols = {r[1] for r in db._conn().execute("PRAGMA table_info(outline_nodes)").fetchall()}
    assert 'homiletical_type' in cols
    assert 'homiletical_type_at' in cols


def test_init_db_is_idempotent(db):
    db.init_db()  # second call must not raise
    db.init_db()
    cols = {r[1] for r in db._conn().execute("PRAGMA table_info(homiletics_insights)").fetchall()}
    assert 'kind' in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_homiletics_schema.py -v`
Expected: 5 failures — tables and columns don't exist yet.

- [ ] **Step 3: Add migration to `companion_db.py`**

In `tools/workbench/companion_db.py`, find the existing inline-migration block (look for the `parent_session_id` migration added by the previous task — `grep -n "parent_session_id" tools/workbench/companion_db.py` to locate). Immediately AFTER that block, add:

```python
        # Homiletics engine: insight storage + claim embeddings + outline tag cache
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS homiletics_insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK (kind IN (
                    'illustration', 'objection', 'counseling', 'wisdom',
                    'christ_thread', 'balance_flag', 'coherence_flag'
                )),
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                citations TEXT NOT NULL,
                confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
                metadata TEXT,
                created_at TEXT NOT NULL,
                consumed_at TEXT,
                source_tool TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_insights_session_kind
                ON homiletics_insights(session_id, kind);
            CREATE INDEX IF NOT EXISTS idx_insights_session_created
                ON homiletics_insights(session_id, created_at);

            CREATE TABLE IF NOT EXISTS claim_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_kind TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                claim_text TEXT NOT NULL,
                embedding BLOB NOT NULL,
                embedding_model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(source_kind, source_id, embedding_model)
            );
            CREATE INDEX IF NOT EXISTS idx_claim_emb_source
                ON claim_embeddings(source_kind, source_id);
        """)
        existing_outline_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(outline_nodes)").fetchall()}
        if 'homiletical_type' not in existing_outline_cols:
            conn.execute(
                "ALTER TABLE outline_nodes ADD COLUMN homiletical_type TEXT "
                "CHECK (homiletical_type IS NULL OR homiletical_type IN ("
                "  'exegetical_claim', 'application', 'illustration', 'christ_thread',"
                "  'question', 'transition', 'other'))"
            )
        if 'homiletical_type_at' not in existing_outline_cols:
            conn.execute("ALTER TABLE outline_nodes ADD COLUMN homiletical_type_at TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_outline_nodes_session_type "
            "ON outline_nodes(session_id, homiletical_type)"
        )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_homiletics_schema.py -v`
Expected: 5 passes.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/companion_db.py tools/workbench/tests/test_homiletics_schema.py
git commit -m "feat(db): homiletics_insights + claim_embeddings tables + outline tag cache"
```

---

### Task 2: DB helpers — save_insight, get_insights, embedding helpers

**Files:**
- Modify: `tools/workbench/companion_db.py` (add four methods to `CompanionDB`)
- Test: `tools/workbench/tests/test_homiletics_db_helpers.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_homiletics_db_helpers.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, json, struct, pytest
from companion_db import CompanionDB


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    sid = d.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                            verse_start=21, verse_end=26, genre='epistle')
    yield d, sid
    os.unlink(path)


def test_save_insight_roundtrip(db):
    d, sid = db
    insight_id = d.save_insight(
        session_id=sid, kind='illustration',
        title='Roman patronage as model for grace',
        body='Augustus dispensed honors to his clients...',
        citations=['LLS:GRECOROMWRLD'],
        confidence=0.85,
        metadata={'theme_tags': ['patronage', 'grace']},
        source_tool='find_illustration',
    )
    assert insight_id > 0
    rows = d.get_insights(sid)
    assert len(rows) == 1
    assert rows[0]['kind'] == 'illustration'
    assert rows[0]['title'] == 'Roman patronage as model for grace'
    assert json.loads(rows[0]['citations']) == ['LLS:GRECOROMWRLD']
    assert json.loads(rows[0]['metadata'])['theme_tags'] == ['patronage', 'grace']


def test_get_insights_filters_by_kind(db):
    d, sid = db
    d.save_insight(session_id=sid, kind='illustration', title='a', body='b',
                    citations=[], confidence=0.5, metadata={}, source_tool='t')
    d.save_insight(session_id=sid, kind='wisdom', title='c', body='d',
                    citations=[], confidence=0.5, metadata={}, source_tool='t')
    illus = d.get_insights(sid, kind='illustration')
    assert len(illus) == 1 and illus[0]['kind'] == 'illustration'
    wisdom = d.get_insights(sid, kind='wisdom')
    assert len(wisdom) == 1 and wisdom[0]['kind'] == 'wisdom'
    all_ = d.get_insights(sid)
    assert len(all_) == 2


def test_save_claim_embedding_packs_float32_blob(db):
    d, sid = db
    vec = [0.1, 0.2, 0.3, 0.4]
    eid = d.save_claim_embedding(
        source_kind='outline_node', source_id=42,
        claim_text='Christ satisfied God\'s wrath',
        vector=vec, model='bge-large-en-v1.5',
    )
    assert eid > 0
    row = d._conn().execute(
        "SELECT embedding FROM claim_embeddings WHERE id = ?", (eid,)
    ).fetchone()
    unpacked = list(struct.unpack(f'{len(vec)}f', row['embedding']))
    for a, b in zip(unpacked, vec):
        assert abs(a - b) < 1e-6


def test_get_claim_embedding_returns_unpacked_vector(db):
    d, sid = db
    vec = [0.5, -0.5, 1.0]
    d.save_claim_embedding(source_kind='outline_node', source_id=1,
                            claim_text='x', vector=vec, model='test-model')
    rows = d.get_claim_embeddings(source_kind='outline_node', model='test-model')
    assert len(rows) == 1
    assert rows[0]['source_id'] == 1
    for a, b in zip(rows[0]['vector'], vec):
        assert abs(a - b) < 1e-6


def test_save_claim_embedding_is_idempotent_per_source_and_model(db):
    d, sid = db
    d.save_claim_embedding(source_kind='outline_node', source_id=1,
                            claim_text='x', vector=[1.0, 0.0], model='m')
    # Second save with same key replaces, not duplicates
    d.save_claim_embedding(source_kind='outline_node', source_id=1,
                            claim_text='x', vector=[0.0, 1.0], model='m')
    rows = d.get_claim_embeddings(source_kind='outline_node', model='m')
    assert len(rows) == 1
    assert rows[0]['vector'] == [0.0, 1.0]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_homiletics_db_helpers.py -v`
Expected: 5 failures with `AttributeError: 'CompanionDB' object has no attribute 'save_insight'` (and similar).

- [ ] **Step 3: Add the helpers to `CompanionDB`**

In `tools/workbench/companion_db.py`, add these four methods inside the `CompanionDB` class. Place them after the series-helpers added by the prior task (search for `def get_series` and place after).

```python
    def save_insight(self, *, session_id, kind, title, body, citations,
                     confidence, metadata, source_tool):
        """Insert a homiletics_insights row. Returns the new row id."""
        import json
        from datetime import datetime, timezone
        conn = self._conn()
        try:
            cur = conn.execute(
                "INSERT INTO homiletics_insights "
                "(session_id, kind, title, body, citations, confidence, metadata, "
                " created_at, source_tool) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, kind, title, body,
                 json.dumps(citations or []),
                 float(confidence),
                 json.dumps(metadata or {}),
                 datetime.now(timezone.utc).isoformat(),
                 source_tool),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def get_insights(self, session_id, kind=None):
        """Return homiletics_insights rows for a session, optionally filtered by kind.

        citations and metadata are returned as raw JSON strings — callers decode.
        """
        conn = self._conn()
        try:
            if kind:
                rows = conn.execute(
                    "SELECT * FROM homiletics_insights "
                    "WHERE session_id = ? AND kind = ? ORDER BY created_at DESC",
                    (session_id, kind),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM homiletics_insights "
                    "WHERE session_id = ? ORDER BY created_at DESC",
                    (session_id,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def save_claim_embedding(self, *, source_kind, source_id, claim_text,
                              vector, model):
        """Insert/replace a claim_embeddings row. Vector is packed as float32 bytes."""
        import struct
        from datetime import datetime, timezone
        blob = struct.pack(f'{len(vector)}f', *vector)
        conn = self._conn()
        try:
            cur = conn.execute(
                "INSERT INTO claim_embeddings "
                "(source_kind, source_id, claim_text, embedding, embedding_model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_kind, source_id, embedding_model) DO UPDATE SET "
                "  claim_text = excluded.claim_text, "
                "  embedding = excluded.embedding, "
                "  created_at = excluded.created_at",
                (source_kind, source_id, claim_text, blob, model,
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def get_claim_embeddings(self, *, source_kind, model, source_ids=None):
        """Return claim_embeddings rows with the vector unpacked to list[float].

        If source_ids is provided, returns only embeddings for those ids.
        """
        import struct
        conn = self._conn()
        try:
            if source_ids is not None:
                if not source_ids:
                    return []
                placeholders = ",".join("?" for _ in source_ids)
                rows = conn.execute(
                    f"SELECT * FROM claim_embeddings "
                    f"WHERE source_kind = ? AND embedding_model = ? "
                    f"  AND source_id IN ({placeholders})",
                    [source_kind, model, *source_ids],
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM claim_embeddings "
                    "WHERE source_kind = ? AND embedding_model = ?",
                    (source_kind, model),
                ).fetchall()
            out = []
            for r in rows:
                blob = r['embedding']
                n = len(blob) // 4   # float32 = 4 bytes
                vec = list(struct.unpack(f'{n}f', blob))
                out.append({
                    'id': r['id'],
                    'source_kind': r['source_kind'],
                    'source_id': r['source_id'],
                    'claim_text': r['claim_text'],
                    'vector': vec,
                    'model': r['embedding_model'],
                })
            return out
        finally:
            conn.close()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_homiletics_db_helpers.py -v`
Expected: 5 passes.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/companion_db.py tools/workbench/tests/test_homiletics_db_helpers.py
git commit -m "feat(db): homiletics save_insight/get_insights + claim embedding helpers"
```

---

### Task 3: Insight dataclass + Analyzer base + canned LLM/search clients for tests

**Files:**
- Create: `tools/workbench/homiletics_engine.py`
- Create: `tools/workbench/tests/test_homiletics_engine_base.py`

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_homiletics_engine_base.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from homiletics_engine import Insight, ANALYZER_KINDS


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


def test_insight_is_frozen_dataclass():
    i = Insight(kind='illustration', title='t', body='b',
                citations=['LLS:X'], confidence=0.5, metadata={'k': 'v'})
    with pytest.raises(Exception):
        i.title = 'changed'  # frozen


def test_insight_defaults():
    i = Insight(kind='illustration', title='t', body='b',
                citations=[], confidence=0.5)
    assert i.metadata == {}


def test_analyzer_kinds_match_schema():
    # Must match the CHECK constraint in homiletics_insights schema.
    assert ANALYZER_KINDS == {
        'illustration', 'objection', 'counseling', 'wisdom',
        'christ_thread', 'balance_flag', 'coherence_flag',
    }


def test_insight_kind_validation():
    with pytest.raises(ValueError):
        Insight(kind='not_a_kind', title='t', body='b',
                citations=[], confidence=0.5)


def test_insight_confidence_range():
    with pytest.raises(ValueError):
        Insight(kind='illustration', title='t', body='b',
                citations=[], confidence=1.5)
    with pytest.raises(ValueError):
        Insight(kind='illustration', title='t', body='b',
                citations=[], confidence=-0.1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_homiletics_engine_base.py -v`
Expected: import error — `homiletics_engine` does not exist.

- [ ] **Step 3: Create `homiletics_engine.py`**

Create `tools/workbench/homiletics_engine.py`:

```python
"""Homiletics Engine — analyzers that produce Insight objects for the companion.

Each analyzer is an independent class with a `run()` method returning
`list[Insight]`. Analyzers share no state. The Insight type is the only
contract between analyzers and the delivery layer (Phase 1: LLM tools).

Phase 2-4 (save hooks, pre-flight review, post-preach learning) plug into
the same engine without rework.
"""

from __future__ import annotations
from dataclasses import dataclass, field

ANALYZER_KINDS = frozenset({
    'illustration', 'objection', 'counseling', 'wisdom',
    'christ_thread', 'balance_flag', 'coherence_flag',
})


@dataclass(frozen=True)
class Insight:
    """A homiletics insight. Frozen so it can be safely shared across threads."""
    kind: str                              # one of ANALYZER_KINDS
    title: str                             # one-line human-readable
    body: str                              # markdown detail
    citations: list                        # resource IDs (LLS:...) or sermon IDs
    confidence: float                      # 0.0-1.0
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.kind not in ANALYZER_KINDS:
            raise ValueError(f"unknown kind: {self.kind!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence {self.confidence} out of [0, 1]")


def persist(db, insight: Insight, *, session_id: int, source_tool: str) -> int:
    """Persist an Insight to homiletics_insights. Returns the row id."""
    return db.save_insight(
        session_id=session_id,
        kind=insight.kind,
        title=insight.title,
        body=insight.body,
        citations=insight.citations,
        confidence=insight.confidence,
        metadata=insight.metadata,
        source_tool=source_tool,
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_homiletics_engine_base.py -v`
Expected: 5 passes.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/homiletics_engine.py tools/workbench/tests/test_homiletics_engine_base.py
git commit -m "feat(homiletics): Insight dataclass + ANALYZER_KINDS + persist helper"
```

---

### Task 4: Bootstrap script + Flask env-var setup for external-drive embedding deps

**Files:**
- Create: `tools/setup_embeddings.sh`
- Modify: `tools/workbench/app.py` (env vars + sys.path before workbench imports)

This task does NOT install anything — it sets up the paths and adds the script for Bryan to run once. The CoherenceChecker task (Task 12) imports `sentence_transformers` and will fail with a friendly message if the script wasn't run.

- [ ] **Step 1: Create the bootstrap script**

Create `tools/setup_embeddings.sh`:

```bash
#!/bin/bash
# Bootstrap embedding model + dependencies on the external drive.
#
# Pins HuggingFace + torch caches and Python deps to
# /Volumes/External/Logos4/Data/ so we don't pollute the system disk.
# Run once after cloning the repo and before CoherenceChecker is first used.

set -euo pipefail

EXTERNAL_DATA=/Volumes/External/Logos4/Data
DEPS_DIR="$EXTERNAL_DATA/python-deps"
HF_HOME="$EXTERNAL_DATA/huggingface"
ST_HOME="$EXTERNAL_DATA/huggingface/sentence-transformers"
TORCH_HOME="$EXTERNAL_DATA/torch"

mkdir -p "$DEPS_DIR" "$HF_HOME" "$ST_HOME" "$TORCH_HOME"

export HF_HOME ST_HOME TORCH_HOME
export SENTENCE_TRANSFORMERS_HOME="$ST_HOME"

echo "Installing sentence-transformers, torch, numpy to $DEPS_DIR"
python3 -m pip install --upgrade --target "$DEPS_DIR" \
  sentence-transformers torch numpy

echo "Downloading BAAI/bge-large-en-v1.5 to $ST_HOME"
PYTHONPATH="$DEPS_DIR" python3 -c "
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('BAAI/bge-large-en-v1.5')
print(f'Model loaded. dim={m.get_sentence_embedding_dimension()}')
"

echo
echo "Done. Disk used on $EXTERNAL_DATA:"
du -sh "$DEPS_DIR" "$HF_HOME" "$TORCH_HOME"
```

Mark executable:

```bash
chmod +x /Volumes/External/Logos4/tools/setup_embeddings.sh
```

- [ ] **Step 2: Wire env vars + sys.path into app.py BEFORE workbench imports**

In `tools/workbench/app.py`, find the first import statement (line 11-15: `import atexit`, `import json`, etc.). Immediately AFTER those stdlib imports (before any local workbench imports), insert:

```python
# Pin embedding model + framework caches to the external drive so we don't
# bloat the system disk. Set BEFORE any import that might pull in torch
# or sentence-transformers.
_EXT = "/Volumes/External/Logos4/Data"
os.environ.setdefault("HF_HOME", f"{_EXT}/huggingface")
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", f"{_EXT}/huggingface/sentence-transformers")
os.environ.setdefault("TORCH_HOME", f"{_EXT}/torch")
# Extend sys.path with the externally-installed dependencies so workbench
# imports of sentence_transformers/torch/numpy resolve to the external copy.
_DEPS = f"{_EXT}/python-deps"
if os.path.isdir(_DEPS) and _DEPS not in sys.path:
    sys.path.insert(0, _DEPS)
```

(`os` and `sys` are already imported at lines 13-14.)

- [ ] **Step 3: Smoke test the script logic (not the install itself)**

The actual install takes ~5 minutes and downloads ~4 GB; do not run it during this task. Instead verify the script parses and shows the expected paths:

```bash
bash -n /Volumes/External/Logos4/tools/setup_embeddings.sh
echo "Script syntax OK"
```

Expected: `Script syntax OK`, no errors.

Also restart the workbench to pick up the env vars, and confirm it still loads:

```bash
cd /Volumes/External/Logos4/tools/workbench && python3 -c "
import app
print('app loaded; HF_HOME=', __import__('os').environ.get('HF_HOME'))
"
```

Expected output: `app loaded; HF_HOME= /Volumes/External/Logos4/Data/huggingface`.

- [ ] **Step 4: Commit**

```bash
git add tools/setup_embeddings.sh tools/workbench/app.py
git commit -m "feat(homiletics): external-drive bootstrap for sentence-transformers + torch"
```

---

### Task 5: LocalEmbeddingsClient + CannedEmbeddingsClient

**Files:**
- Create: `tools/workbench/embeddings_client.py`
- Create: `tools/workbench/tests/test_embeddings_client.py`

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_embeddings_client.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from embeddings_client import CannedEmbeddingsClient


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


def test_canned_client_returns_deterministic_vectors_by_text():
    c = CannedEmbeddingsClient(dimension=8)
    v1 = c.embed_one("hello world")
    v2 = c.embed_one("hello world")
    assert v1 == v2
    assert len(v1) == 8
    assert all(isinstance(x, float) for x in v1)


def test_canned_client_different_text_different_vectors():
    c = CannedEmbeddingsClient(dimension=8)
    v1 = c.embed_one("Christ propitiates")
    v2 = c.embed_one("the wages of sin")
    assert v1 != v2


def test_canned_client_embed_batch():
    c = CannedEmbeddingsClient(dimension=8)
    vs = c.embed(["a", "b", "c"])
    assert len(vs) == 3
    assert all(len(v) == 8 for v in vs)
    # Single calls match batch calls
    assert c.embed_one("a") == vs[0]


def test_canned_client_vectors_are_unit_normalized():
    import math
    c = CannedEmbeddingsClient(dimension=8)
    v = c.embed_one("Christ")
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-6


def test_canned_client_dimension_property():
    c = CannedEmbeddingsClient(dimension=16)
    assert c.dimension == 16
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_embeddings_client.py -v`
Expected: ImportError on `embeddings_client`.

- [ ] **Step 3: Create `embeddings_client.py`**

Create `tools/workbench/embeddings_client.py`:

```python
"""Embedding clients for the homiletics engine.

Production: LocalEmbeddingsClient wraps sentence-transformers / BAAI/bge-large-en-v1.5.
The model lazy-loads on first embed() call. Subsequent calls reuse the in-process
model. Thread-safe via a single mutex around encode().

Tests: CannedEmbeddingsClient returns deterministic vectors keyed by text hash —
unit-normalized so cosine similarity tests behave predictably.
"""

from __future__ import annotations
import hashlib
import math
import struct
import threading
from typing import Protocol


class EmbeddingsClient(Protocol):
    def embed(self, texts: list) -> list: ...
    def embed_one(self, text: str) -> list: ...
    @property
    def dimension(self) -> int: ...


class CannedEmbeddingsClient:
    """Deterministic test double. Vectors derived from SHA-256 of text."""

    def __init__(self, dimension: int = 8):
        self._dim = dimension

    @property
    def dimension(self) -> int:
        return self._dim

    def embed_one(self, text: str) -> list:
        return self._vector(text)

    def embed(self, texts: list) -> list:
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list:
        # Stretch SHA-256 into `dimension` float32s, then unit-normalize.
        h = hashlib.sha256(text.encode("utf-8")).digest()
        # Repeat the hash until we have enough bytes
        needed = self._dim * 4
        buf = b""
        while len(buf) < needed:
            buf += hashlib.sha256(buf + h).digest()
        raw = list(struct.unpack(f"{self._dim}f", buf[:needed]))
        # Normalize to unit length
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]


class LocalEmbeddingsClient:
    """sentence-transformers wrapper. Lazy model load on first embed()."""

    def __init__(self, model_name: str = "BAAI/bge-large-en-v1.5"):
        self._model_name = model_name
        self._model = None
        self._lock = threading.Lock()
        self._dim = None

    @property
    def dimension(self) -> int:
        if self._dim is None:
            self._ensure_loaded()
        return self._dim

    def embed_one(self, text: str) -> list:
        return self.embed([text])[0]

    def embed(self, texts: list) -> list:
        self._ensure_loaded()
        with self._lock:
            vecs = self._model.encode(texts, normalize_embeddings=True,
                                      show_progress_bar=False)
        return [v.tolist() for v in vecs]

    def _ensure_loaded(self):
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise RuntimeError(
                    f"sentence_transformers not installed. Run "
                    f"tools/setup_embeddings.sh to install to /Volumes/External/Logos4/Data/python-deps. "
                    f"Original error: {e}"
                )
            self._model = SentenceTransformer(self._model_name)
            self._dim = self._model.get_sentence_embedding_dimension()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_embeddings_client.py -v`
Expected: 5 passes.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/embeddings_client.py tools/workbench/tests/test_embeddings_client.py
git commit -m "feat(homiletics): LocalEmbeddingsClient + CannedEmbeddingsClient"
```

---

### Task 6: IllustrationFinder (Opus-ranked)

**Files:**
- Modify: `tools/workbench/homiletics_engine.py` (add IllustrationFinder class)
- Test: `tools/workbench/tests/test_illustration_finder.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_illustration_finder.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from homiletics_engine import IllustrationFinder, Insight


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    sid = d.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                            verse_start=21, verse_end=26, genre='epistle')
    yield d, sid
    os.unlink(path)


class _FixedSearcher:
    """Stub LogosSearcher returning a fixed result list."""
    def __init__(self, results):
        self._results = results
    def search(self, query, **kwargs):
        return {'ok': True, 'results': self._results}


class _FixedLLM:
    """Stub LLM returning a fixed ranking."""
    def __init__(self, output):
        self._output = output
    def call(self, prompt, schema, **kwargs):
        return {'output': self._output, 'input_tokens': 0,
                'output_tokens': 0, 'model': 'test'}


def test_illustration_finder_happy_path(db):
    d, sid = db
    candidates = [
        {'resource_id': 'LLS:GRECOROMWRLD', 'title': 'Greco-Roman World',
         'authors': 'Jeffers, James S.', 'sections': [{
             'snippet': 'Pliny wrote to Trajan in AD 112 about Christians refusing to sacrifice.',
             'section_offset': 100}]},
        {'resource_id': 'LLS:IRRELEVANT', 'title': 'Boring Book',
         'authors': 'Anon', 'sections': [{'snippet': 'theory of patronage abstract',
                                            'section_offset': 50}]},
    ]
    llm = _FixedLLM({'ranked': [
        {'index': 0, 'score': 0.92, 'rationale': 'Concrete: names Pliny and Trajan with date AD 112.'},
        {'index': 1, 'score': 0.21, 'rationale': 'Abstract; no narrative.'},
    ]})
    finder = IllustrationFinder(searcher=_FixedSearcher(candidates), llm=llm)
    insights = finder.run(d, session_id=sid, theme='Greco-Roman patronage', count=1)
    assert len(insights) == 1
    assert insights[0].kind == 'illustration'
    assert 'Pliny' in insights[0].body
    assert insights[0].citations == ['LLS:GRECOROMWRLD']
    assert insights[0].confidence == 0.92
    assert insights[0].metadata['rationale'].startswith('Concrete')
    # Persisted
    rows = d.get_insights(sid, kind='illustration')
    assert len(rows) == 1


def test_illustration_finder_returns_empty_when_search_fails(db):
    d, sid = db
    class _Fail:
        def search(self, q, **kw): return {'ok': False, 'error': 'search_down'}
    finder = IllustrationFinder(searcher=_Fail(), llm=None)
    insights = finder.run(d, session_id=sid, theme='anything', count=3)
    assert insights == []


def test_illustration_finder_returns_empty_when_no_candidates(db):
    d, sid = db
    finder = IllustrationFinder(searcher=_FixedSearcher([]), llm=None)
    insights = finder.run(d, session_id=sid, theme='nothing matches', count=3)
    assert insights == []


def test_illustration_finder_caps_to_count(db):
    d, sid = db
    candidates = [{'resource_id': f'LLS:R{i}', 'title': f't{i}',
                    'authors': 'a', 'sections': [{'snippet': f's{i}', 'section_offset': i}]}
                   for i in range(5)]
    llm = _FixedLLM({'ranked': [
        {'index': i, 'score': 0.9 - i * 0.1, 'rationale': f'r{i}'}
        for i in range(5)
    ]})
    finder = IllustrationFinder(searcher=_FixedSearcher(candidates), llm=llm)
    insights = finder.run(d, session_id=sid, theme='x', count=2)
    assert len(insights) == 2
    assert insights[0].confidence > insights[1].confidence
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_illustration_finder.py -v`
Expected: ImportError on `IllustrationFinder`.

- [ ] **Step 3: Add `IllustrationFinder` to `homiletics_engine.py`**

Append to `tools/workbench/homiletics_engine.py`:

```python
# ── Analyzers ───────────────────────────────────────────────────────────────


_ILLUSTRATION_RANK_SCHEMA = {
    'type': 'object',
    'properties': {
        'ranked': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'index': {'type': 'integer', 'minimum': 0},
                    'score': {'type': 'number', 'minimum': 0, 'maximum': 1},
                    'rationale': {'type': 'string'},
                },
                'required': ['index', 'score', 'rationale'],
            },
        },
    },
    'required': ['ranked'],
}

_ILLUSTRATION_RANK_SYSTEM = (
    "You rank passages as sermon illustrations. Score each on narrative "
    "concreteness (named people, dates, places, story arc) and vividness "
    "(0=abstract/theoretical, 1=vivid and immediately preachable). Return "
    "the indexes in descending score order with a one-line rationale."
)


class IllustrationFinder:
    """Find sermon illustrations for a theme. Uses search_library + Opus ranking."""

    name = 'find_illustration'

    def __init__(self, searcher=None, llm=None, candidate_multiplier: int = 5):
        self._searcher = searcher           # logos_search.LogosSearcher
        self._llm = llm                     # llm_client.AnthropicClient
        self._mult = candidate_multiplier

    def run(self, db, *, session_id: int, theme: str, count: int = 3,
            passage: dict = None) -> list:
        if not theme:
            return []
        searcher = self._searcher or self._default_searcher()
        candidates = self._search(searcher, theme, count)
        if not candidates:
            return []
        llm = self._llm or self._default_llm()
        ranked = self._rank(llm, theme, candidates)
        results = []
        for entry in ranked[:count]:
            cand = candidates[entry['index']]
            section = (cand.get('sections') or [{}])[0]
            insight = Insight(
                kind='illustration',
                title=f"{cand.get('title', '?')[:80]} — score {entry['score']:.2f}",
                body=section.get('snippet') or '',
                citations=[cand.get('resource_id')] if cand.get('resource_id') else [],
                confidence=float(entry['score']),
                metadata={
                    'rationale': entry['rationale'],
                    'author': cand.get('authors'),
                    'section_offset': section.get('section_offset'),
                },
            )
            persist(db, insight, session_id=session_id, source_tool=self.name)
            results.append(insight)
        return results

    def _search(self, searcher, theme: str, count: int) -> list:
        try:
            response = searcher.search(
                theme, limit=count * self._mult,
                resource_type_prefix='text.monograph',
            )
        except Exception:
            return []
        if not response.get('ok'):
            return []
        return response.get('results') or []

    def _rank(self, llm, theme: str, candidates: list) -> list:
        prompt_lines = [f"Theme: {theme}", "", "Candidates:"]
        for i, c in enumerate(candidates):
            section = (c.get('sections') or [{}])[0]
            prompt_lines.append(f"[{i}] {c.get('title', '?')}")
            prompt_lines.append(f"    {section.get('snippet', '')[:600]}")
        prompt = "\n".join(prompt_lines)
        result = llm.call(
            prompt=prompt, schema=_ILLUSTRATION_RANK_SCHEMA,
            system=_ILLUSTRATION_RANK_SYSTEM,
            tool_name='rank_illustrations',
            tool_description='Rank illustrations by narrative concreteness.',
        )
        if 'error' in result or 'output' not in result:
            return []
        return result['output'].get('ranked') or []

    def _default_searcher(self):
        from logos_search import get_searcher
        return get_searcher()

    def _default_llm(self):
        from llm_client import AnthropicClient
        from app_secrets import anthropic_api_key
        return AnthropicClient(api_key=anthropic_api_key())
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_illustration_finder.py -v`
Expected: 4 passes.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/homiletics_engine.py tools/workbench/tests/test_illustration_finder.py
git commit -m "feat(homiletics): IllustrationFinder with Opus ranking"
```

---

### Task 7: ChristThreadProposer

**Files:**
- Modify: `tools/workbench/homiletics_engine.py` (append class)
- Test: `tools/workbench/tests/test_christ_thread_proposer.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_christ_thread_proposer.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from homiletics_engine import ChristThreadProposer, Insight


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    sid = d.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                            verse_start=21, verse_end=26, genre='epistle')
    yield d, sid
    os.unlink(path)


class _FixedLLM:
    def __init__(self, output):
        self._output = output
    def call(self, prompt, schema, **kwargs):
        return {'output': self._output, 'input_tokens': 0,
                'output_tokens': 0, 'model': 'test'}


def test_proposes_christ_threads_with_categories(db):
    d, sid = db
    output = {'angles': [
        {'category': 'direct_reference', 'angle': 'God set forth Christ as a propitiation',
         'rationale': 'The verse names Christ directly as hilastērion.',
         'supporting_refs': ['Romans 3:25', 'Hebrews 9:5']},
        {'category': 'typology',
         'angle': 'Mercy seat in the OT prefigures Christ',
         'rationale': 'hilastērion echoes Exodus mercy seat.',
         'supporting_refs': ['Exodus 25:17', 'Leviticus 16:14']},
    ]}
    proposer = ChristThreadProposer(llm=_FixedLLM(output))
    passage = {'book': 66, 'chapter': 3, 'verse_start': 25, 'verse_end': 25,
               'passage_ref': 'Romans 3:25'}
    insights = proposer.run(d, session_id=sid, passage=passage, count=2)
    assert len(insights) == 2
    assert all(i.kind == 'christ_thread' for i in insights)
    kinds = {i.metadata['category'] for i in insights}
    assert kinds == {'direct_reference', 'typology'}
    # Persisted
    rows = d.get_insights(sid, kind='christ_thread')
    assert len(rows) == 2


def test_returns_empty_on_llm_error(db):
    d, sid = db
    class _Err:
        def call(self, prompt, schema, **kwargs):
            return {'error': 'sdk down'}
    proposer = ChristThreadProposer(llm=_Err())
    passage = {'book': 66, 'chapter': 3, 'verse_start': 25, 'verse_end': 25,
               'passage_ref': 'Romans 3:25'}
    insights = proposer.run(d, session_id=sid, passage=passage)
    assert insights == []


def test_passes_point_into_prompt_when_given(db):
    d, sid = db
    captured = {}
    class _Capture:
        def call(self, prompt, schema, **kwargs):
            captured['prompt'] = prompt
            return {'output': {'angles': []}, 'input_tokens': 0,
                    'output_tokens': 0, 'model': 'test'}
    proposer = ChristThreadProposer(llm=_Capture())
    passage = {'book': 66, 'chapter': 3, 'verse_start': 25, 'verse_end': 25,
               'passage_ref': 'Romans 3:25'}
    proposer.run(d, session_id=sid, passage=passage, point='God\'s wrath is satisfied')
    assert 'wrath is satisfied' in captured['prompt']
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_christ_thread_proposer.py -v`
Expected: ImportError on `ChristThreadProposer`.

- [ ] **Step 3: Append `ChristThreadProposer` to `homiletics_engine.py`**

```python
_CHRIST_THREAD_CATEGORIES = (
    'typology', 'promise_fulfillment', 'contrast',
    'redemptive_historical', 'direct_reference',
)

_CHRIST_THREAD_SCHEMA = {
    'type': 'object',
    'properties': {
        'angles': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'category': {'type': 'string', 'enum': list(_CHRIST_THREAD_CATEGORIES)},
                    'angle': {'type': 'string'},
                    'rationale': {'type': 'string'},
                    'supporting_refs': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['category', 'angle', 'rationale'],
            },
        },
    },
    'required': ['angles'],
}

_CHRIST_THREAD_SYSTEM = (
    "You propose Christ-thread angles for sermon prep from a Reformed homiletical "
    "perspective. Use these five categories, in descending preference for the "
    "passage:\n"
    "  - direct_reference: the text names or describes Christ directly\n"
    "  - promise_fulfillment: a promise the text makes that Christ fulfills (or vice versa)\n"
    "  - typology: a person, event, or institution that prefigures Christ\n"
    "  - contrast: how the text shows the inability that Christ alone meets\n"
    "  - redemptive_historical: where this passage sits in salvation history with Christ at the center\n"
    "For each angle: state it in one sentence, give a one-line rationale tied to the "
    "text, and cite 1-2 supporting biblical references. Distribute across categories "
    "where the text supports it; do not force five if fewer fit."
)


class ChristThreadProposer:
    """Propose Christ-thread angles for the passage (optionally focused on a point)."""

    name = 'suggest_christ_thread'

    def __init__(self, llm=None):
        self._llm = llm

    def run(self, db, *, session_id: int, passage: dict,
            point: str = None, count: int = 3) -> list:
        llm = self._llm or self._default_llm()
        prompt = self._build_prompt(passage, point, count)
        result = llm.call(
            prompt=prompt, schema=_CHRIST_THREAD_SCHEMA,
            system=_CHRIST_THREAD_SYSTEM,
            tool_name='emit_christ_thread_angles',
            tool_description='Emit Christ-thread angles for the passage.',
        )
        if 'error' in result or 'output' not in result:
            return []
        angles = result['output'].get('angles') or []
        results = []
        for a in angles[:count]:
            insight = Insight(
                kind='christ_thread',
                title=f"{a['category']}: {a['angle'][:80]}",
                body=f"**{a['angle']}**\n\n{a['rationale']}",
                citations=list(a.get('supporting_refs') or []),
                confidence=0.85,
                metadata={
                    'category': a['category'],
                    'supporting_refs': list(a.get('supporting_refs') or []),
                },
            )
            persist(db, insight, session_id=session_id, source_tool=self.name)
            results.append(insight)
        return results

    def _build_prompt(self, passage: dict, point: str, count: int) -> str:
        ref = passage.get('passage_ref') or '?'
        lines = [f"Passage: {ref}", f"Requested angles: {count}"]
        if point:
            lines.append(f"Specific point under consideration: {point}")
        return "\n".join(lines)

    def _default_llm(self):
        from llm_client import AnthropicClient
        from app_secrets import anthropic_api_key
        return AnthropicClient(api_key=anthropic_api_key())
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_christ_thread_proposer.py -v`
Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/homiletics_engine.py tools/workbench/tests/test_christ_thread_proposer.py
git commit -m "feat(homiletics): ChristThreadProposer with Reformed category prompt"
```

---

### Task 8: WisdomPrinciples

**Files:**
- Modify: `tools/workbench/homiletics_engine.py` (append class)
- Test: `tools/workbench/tests/test_wisdom_principles.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_wisdom_principles.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from homiletics_engine import WisdomPrinciples


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    sid = d.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                            verse_start=21, verse_end=26, genre='epistle')
    yield d, sid
    os.unlink(path)


class _FixedLLM:
    def __init__(self, output): self._output = output
    def call(self, prompt, schema, **kwargs):
        return {'output': self._output, 'input_tokens': 0,
                'output_tokens': 0, 'model': 'test'}


def test_distills_wisdom_principles_with_cross_refs(db):
    d, sid = db
    output = {'principles': [
        {'principle': 'Boasting in human righteousness is excluded by the gospel.',
         'rationale': 'Paul shuts down self-justification by grounding righteousness in God\'s gift.',
         'cross_refs': ['Ephesians 2:9', 'Galatians 6:14'],
         'principle_tags': ['humility', 'grace']},
        {'principle': 'God\'s justice and mercy meet in the cross.',
         'rationale': 'God is both just and the justifier through Christ\'s propitiation.',
         'cross_refs': ['Psalm 85:10'],
         'principle_tags': ['atonement']},
    ]}
    p = WisdomPrinciples(llm=_FixedLLM(output))
    passage = {'book': 66, 'chapter': 3, 'verse_start': 21, 'verse_end': 26,
               'passage_ref': 'Romans 3:21-26'}
    insights = p.run(d, session_id=sid, passage=passage, count=2)
    assert len(insights) == 2
    assert all(i.kind == 'wisdom' for i in insights)
    assert 'humility' in insights[0].metadata['principle_tags']
    assert 'Ephesians 2:9' in insights[0].citations
    rows = d.get_insights(sid, kind='wisdom')
    assert len(rows) == 2


def test_returns_empty_on_llm_error(db):
    d, sid = db
    class _Err:
        def call(self, prompt, schema, **kwargs):
            return {'error': 'sdk down'}
    p = WisdomPrinciples(llm=_Err())
    passage = {'book': 66, 'chapter': 3, 'verse_start': 21, 'verse_end': 26,
               'passage_ref': 'Romans 3:21-26'}
    assert p.run(d, session_id=sid, passage=passage) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_wisdom_principles.py -v`
Expected: ImportError on `WisdomPrinciples`.

- [ ] **Step 3: Append `WisdomPrinciples` to `homiletics_engine.py`**

```python
_WISDOM_SCHEMA = {
    'type': 'object',
    'properties': {
        'principles': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'principle': {'type': 'string'},
                    'rationale': {'type': 'string'},
                    'cross_refs': {'type': 'array', 'items': {'type': 'string'}},
                    'principle_tags': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['principle', 'rationale'],
            },
        },
    },
    'required': ['principles'],
}

_WISDOM_SYSTEM = (
    "Distill 2-3 transferable wisdom principles from the passage. Each principle "
    "should be a single proverbial sentence that carries beyond the specific "
    "situation in the text. Give a brief rationale grounded in the text, 1-3 "
    "cross-references (preferring wisdom literature — Proverbs, Job, Ecclesiastes "
    "— and Pauline epistles), and a short list of topical tags. Avoid generic "
    "moralism: each principle should be something a Reformed pastor would happily "
    "preach as a sub-point."
)


class WisdomPrinciples:
    """Extract transferable wisdom principles from the passage."""

    name = 'find_wisdom_principles'

    def __init__(self, llm=None):
        self._llm = llm

    def run(self, db, *, session_id: int, passage: dict, count: int = 3) -> list:
        llm = self._llm or self._default_llm()
        prompt = f"Passage: {passage.get('passage_ref', '?')}\nRequested principles: {count}"
        result = llm.call(
            prompt=prompt, schema=_WISDOM_SCHEMA,
            system=_WISDOM_SYSTEM,
            tool_name='emit_wisdom_principles',
            tool_description='Emit transferable wisdom principles from the passage.',
        )
        if 'error' in result or 'output' not in result:
            return []
        principles = result['output'].get('principles') or []
        results = []
        for p in principles[:count]:
            cross_refs = list(p.get('cross_refs') or [])
            insight = Insight(
                kind='wisdom',
                title=p['principle'][:100],
                body=f"**{p['principle']}**\n\n{p['rationale']}",
                citations=cross_refs,
                confidence=0.80,
                metadata={
                    'principle_tags': list(p.get('principle_tags') or []),
                    'cross_refs': cross_refs,
                },
            )
            persist(db, insight, session_id=session_id, source_tool=self.name)
            results.append(insight)
        return results

    def _default_llm(self):
        from llm_client import AnthropicClient
        from app_secrets import anthropic_api_key
        return AnthropicClient(api_key=anthropic_api_key())
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_wisdom_principles.py -v`
Expected: 2 passes.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/homiletics_engine.py tools/workbench/tests/test_wisdom_principles.py
git commit -m "feat(homiletics): WisdomPrinciples analyzer"
```

---

### Task 9: ObjectionFinder

**Files:**
- Modify: `tools/workbench/homiletics_engine.py`
- Test: `tools/workbench/tests/test_objection_finder.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_objection_finder.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from homiletics_engine import ObjectionFinder


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    sid = d.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                            verse_start=21, verse_end=26, genre='epistle')
    yield d, sid
    os.unlink(path)


class _FixedSearcher:
    def __init__(self, results): self._results = results
    def search(self, q, **kw): return {'ok': True, 'results': self._results}


class _FixedLLM:
    def __init__(self, output): self._output = output
    def call(self, prompt, schema, **kwargs):
        return {'output': self._output, 'input_tokens': 0,
                'output_tokens': 0, 'model': 'test'}


def test_returns_objections_from_both_sources(db):
    d, sid = db
    candidates = [{'resource_id': 'LLS:RECTHEO', 'title': 'Reformed Theology',
                    'authors': 'Berkhof, L.',
                    'sections': [{'snippet': 'Objections to penal substitution...',
                                   'section_offset': 0}]}]
    output = {'objections': [
        {'objection_class': 'theological',
         'objection': 'If God forgives freely, why does justice require punishment?',
         'rebuttal_pointer': 'God\'s justice and mercy meet in Christ; v. 26 explicit.'},
        {'objection_class': 'pastoral',
         'objection': 'But I don\'t feel forgiven.',
         'rebuttal_pointer': 'Justification is forensic, not felt; assurance flows from faith.'},
    ]}
    f = ObjectionFinder(searcher=_FixedSearcher(candidates), llm=_FixedLLM(output))
    insights = f.run(d, session_id=sid, claim='God satisfied his wrath in Christ', count=4)
    # 2 from LLM + at most 1 from search (capped)
    assert len(insights) >= 2
    classes = {i.metadata.get('objection_class') for i in insights if 'objection_class' in i.metadata}
    assert {'theological', 'pastoral'}.issubset(classes)
    rows = d.get_insights(sid, kind='objection')
    assert len(rows) == len(insights)


def test_caps_total_to_count(db):
    d, sid = db
    output = {'objections': [
        {'objection_class': 'theological', 'objection': f'o{i}',
         'rebuttal_pointer': f'r{i}'}
        for i in range(10)
    ]}
    f = ObjectionFinder(searcher=_FixedSearcher([]), llm=_FixedLLM(output))
    insights = f.run(d, session_id=sid, claim='x', count=3)
    assert len(insights) == 3
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_objection_finder.py -v`
Expected: ImportError on `ObjectionFinder`.

- [ ] **Step 3: Append `ObjectionFinder` to `homiletics_engine.py`**

```python
_OBJECTION_CLASSES = ('theological', 'pastoral', 'cultural', 'honest_doubt')

_OBJECTION_SCHEMA = {
    'type': 'object',
    'properties': {
        'objections': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'objection_class': {'type': 'string', 'enum': list(_OBJECTION_CLASSES)},
                    'objection': {'type': 'string'},
                    'rebuttal_pointer': {'type': 'string'},
                },
                'required': ['objection_class', 'objection', 'rebuttal_pointer'],
            },
        },
    },
    'required': ['objections'],
}

_OBJECTION_SYSTEM = (
    "List likely objections a congregation might raise to this claim, drawn from "
    "four categories:\n"
    "  - theological: doctrinal pushback from non-Reformed positions\n"
    "  - pastoral: 'but what if I/my situation...' (felt-need objections)\n"
    "  - cultural: modern skepticism, scientific naturalism, moral intuitions\n"
    "  - honest_doubt: thoughtful Christian wrestling that deserves charity\n"
    "For each: state the objection in the objector's voice (one sentence), then "
    "give a one-line rebuttal pointer (not a full answer — a direction to take in "
    "preaching). Distribute across categories where realistic."
)


class ObjectionFinder:
    """Find likely objections to a claim. Combines library search + LLM taxonomy."""

    name = 'find_objections'

    def __init__(self, searcher=None, llm=None):
        self._searcher = searcher
        self._llm = llm

    def run(self, db, *, session_id: int, claim: str, count: int = 3,
            passage: dict = None) -> list:
        if not claim:
            return []
        llm_objections = self._llm_objections(claim, count)
        # Reserve at least 60% for LLM-generated; allow a few from library if room.
        llm_cap = max(1, int(count * 0.6))
        from_llm = llm_objections[:llm_cap]
        remaining = max(0, count - len(from_llm))
        from_library = self._library_objections(claim, remaining) if remaining else []
        combined = from_llm + from_library
        results = []
        for o in combined[:count]:
            insight = Insight(
                kind='objection',
                title=f"[{o.get('objection_class', '?')}] {o['objection'][:80]}",
                body=f"**Objection:** {o['objection']}\n\n**Direction:** {o['rebuttal_pointer']}",
                citations=o.get('citations', []),
                confidence=o.get('confidence', 0.75),
                metadata={
                    'objection_class': o.get('objection_class'),
                    'source': o.get('source', 'llm'),
                },
            )
            persist(db, insight, session_id=session_id, source_tool=self.name)
            results.append(insight)
        return results

    def _llm_objections(self, claim: str, count: int) -> list:
        llm = self._llm or self._default_llm()
        result = llm.call(
            prompt=f"Claim: {claim}\nProduce up to {count*2} objections.",
            schema=_OBJECTION_SCHEMA, system=_OBJECTION_SYSTEM,
            tool_name='emit_objections',
            tool_description='Emit objections by category.',
        )
        if 'error' in result or 'output' not in result:
            return []
        return [{**o, 'source': 'llm'} for o in (result['output'].get('objections') or [])]

    def _library_objections(self, claim: str, count: int) -> list:
        if count <= 0:
            return []
        searcher = self._searcher or self._default_searcher()
        try:
            response = searcher.search(
                f"objections to {claim}", limit=count * 3,
                resource_type_prefix='text.monograph',
            )
        except Exception:
            return []
        results = []
        for c in (response.get('results') or [])[:count]:
            section = (c.get('sections') or [{}])[0]
            results.append({
                'objection_class': 'theological',
                'objection': f"From {c.get('title', '?')}: {(section.get('snippet') or '')[:200]}",
                'rebuttal_pointer': 'See the cited resource for the author\'s treatment.',
                'citations': [c.get('resource_id')] if c.get('resource_id') else [],
                'source': 'library',
                'confidence': 0.6,
            })
        return results

    def _default_searcher(self):
        from logos_search import get_searcher
        return get_searcher()

    def _default_llm(self):
        from llm_client import AnthropicClient
        from app_secrets import anthropic_api_key
        return AnthropicClient(api_key=anthropic_api_key())
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_objection_finder.py -v`
Expected: 2 passes.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/homiletics_engine.py tools/workbench/tests/test_objection_finder.py
git commit -m "feat(homiletics): ObjectionFinder combining LLM taxonomy + library search"
```

---

### Task 10: CounselingApplications

**Files:**
- Modify: `tools/workbench/homiletics_engine.py`
- Test: `tools/workbench/tests/test_counseling_applications.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_counseling_applications.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from homiletics_engine import CounselingApplications


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    sid = d.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                            verse_start=21, verse_end=26, genre='epistle')
    yield d, sid
    os.unlink(path)


class _FixedLLM:
    def __init__(self, output): self._output = output
    def call(self, prompt, schema, **kwargs):
        return {'output': self._output, 'input_tokens': 0,
                'output_tokens': 0, 'model': 'test'}


def test_maps_passage_to_pastoral_situations(db):
    d, sid = db
    output = {'applications': [
        {'pastoral_situation': 'guilt',
         'application': 'The believer who feels their sin disqualifies them needs to hear...',
         'tier': 'remedial',
         'cross_refs': ['1 John 1:9']},
        {'pastoral_situation': 'self_righteousness',
         'application': 'The believer who quietly trusts their own goodness needs...',
         'tier': 'preventative',
         'cross_refs': ['Luke 18:9-14']},
    ]}
    c = CounselingApplications(llm=_FixedLLM(output))
    passage = {'book': 66, 'chapter': 3, 'verse_start': 21, 'verse_end': 26,
               'passage_ref': 'Romans 3:21-26'}
    insights = c.run(d, session_id=sid, passage=passage, count=2)
    assert len(insights) == 2
    sits = {i.metadata['pastoral_situation'] for i in insights}
    assert sits == {'guilt', 'self_righteousness'}
    rows = d.get_insights(sid, kind='counseling')
    assert len(rows) == 2


def test_returns_empty_on_llm_error(db):
    d, sid = db
    class _Err:
        def call(self, prompt, schema, **kwargs):
            return {'error': 'down'}
    c = CounselingApplications(llm=_Err())
    passage = {'book': 66, 'chapter': 3, 'verse_start': 21, 'verse_end': 26,
               'passage_ref': 'Romans 3:21-26'}
    assert c.run(d, session_id=sid, passage=passage) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_counseling_applications.py -v`
Expected: ImportError on `CounselingApplications`.

- [ ] **Step 3: Append `CounselingApplications` to `homiletics_engine.py`**

```python
_COUNSELING_TIERS = ('preventative', 'remedial', 'diagnostic')

_COUNSELING_SCHEMA = {
    'type': 'object',
    'properties': {
        'applications': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'pastoral_situation': {'type': 'string'},
                    'application': {'type': 'string'},
                    'tier': {'type': 'string', 'enum': list(_COUNSELING_TIERS)},
                    'cross_refs': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['pastoral_situation', 'application', 'tier'],
            },
        },
    },
    'required': ['applications'],
}

_COUNSELING_SYSTEM = (
    "Map this passage to common pastoral-counseling situations the preacher might "
    "address. For each: name the situation in one or two words "
    "(e.g. 'anxiety', 'marriage_conflict', 'guilt', 'addiction', 'grief', 'doubt', "
    "'self_righteousness'), give a one-paragraph application targeted to someone in "
    "that situation, tag the tier:\n"
    "  - preventative: forming healthy patterns before crisis\n"
    "  - remedial: healing/restoration where there's already damage\n"
    "  - diagnostic: helping the person recognize the issue\n"
    "and give 1-2 cross-references from Scripture or biblical-counseling literature."
)


class CounselingApplications:
    """Map a passage to common pastoral-counseling situations."""

    name = 'find_counseling_applications'

    def __init__(self, llm=None):
        self._llm = llm

    def run(self, db, *, session_id: int, passage: dict, count: int = 3) -> list:
        llm = self._llm or self._default_llm()
        result = llm.call(
            prompt=f"Passage: {passage.get('passage_ref', '?')}\nRequested applications: {count}",
            schema=_COUNSELING_SCHEMA, system=_COUNSELING_SYSTEM,
            tool_name='emit_counseling_applications',
            tool_description='Emit pastoral-counseling applications.',
        )
        if 'error' in result or 'output' not in result:
            return []
        apps = result['output'].get('applications') or []
        results = []
        for a in apps[:count]:
            cross_refs = list(a.get('cross_refs') or [])
            insight = Insight(
                kind='counseling',
                title=f"[{a['tier']}] {a['pastoral_situation']}",
                body=a['application'],
                citations=cross_refs,
                confidence=0.80,
                metadata={
                    'pastoral_situation': a['pastoral_situation'],
                    'tier': a['tier'],
                },
            )
            persist(db, insight, session_id=session_id, source_tool=self.name)
            results.append(insight)
        return results

    def _default_llm(self):
        from llm_client import AnthropicClient
        from app_secrets import anthropic_api_key
        return AnthropicClient(api_key=anthropic_api_key())
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_counseling_applications.py -v`
Expected: 2 passes.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/homiletics_engine.py tools/workbench/tests/test_counseling_applications.py
git commit -m "feat(homiletics): CounselingApplications analyzer"
```

---

### Task 11: BalanceGauger (with outline tag cache)

**Files:**
- Modify: `tools/workbench/homiletics_engine.py`
- Test: `tools/workbench/tests/test_balance_gauger.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_balance_gauger.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from homiletics_engine import BalanceGauger


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    sid = d.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                            verse_start=21, verse_end=26, genre='epistle')
    yield d, sid
    os.unlink(path)


class _TaggingLLM:
    """Fake LLM that tags each node according to a provided mapping."""
    def __init__(self, by_content: dict):
        self._map = by_content
    def call(self, prompt, schema, **kwargs):
        # Parse 'Node N: <content>' lines from the prompt and assign tags from the map.
        import re
        tags = []
        for m in re.finditer(r'^Node (\d+) \[(\d+)\]: (.+)$', prompt, re.MULTILINE):
            node_id = int(m.group(2))
            content = m.group(3)
            tag = self._map.get(content, 'other')
            tags.append({'node_id': node_id, 'homiletical_type': tag})
        return {'output': {'tags': tags}, 'input_tokens': 0,
                'output_tokens': 0, 'model': 'test'}


def _seed_nodes(d, sid, contents_and_tags):
    """Insert outline nodes; returns list of (id, content)."""
    from datetime import datetime, timezone
    conn = d._conn()
    now = datetime.now(timezone.utc).isoformat()
    ids = []
    for i, (content, _) in enumerate(contents_and_tags):
        cur = conn.execute(
            "INSERT INTO outline_nodes (session_id, type, content, rank, created_at, updated_at) "
            "VALUES (?, 'point', ?, ?, ?, ?)", (sid, content, i, now, now))
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def test_gauges_balance_and_flags_pure_exegesis(db):
    d, sid = db
    nodes = [
        ('Paul echoes Habakkuk 2:4 in 3:22.', 'exegetical_claim'),
        ('hilastērion picks up Septuagint Exodus 25:17.', 'exegetical_claim'),
        ('The participle "being justified" is divine passive.', 'exegetical_claim'),
    ]
    _seed_nodes(d, sid, nodes)
    g = BalanceGauger(llm=_TaggingLLM({c: t for c, t in nodes}))
    insights = g.run(d, session_id=sid)
    # Should have at least the summary + one flag
    summary = next(i for i in insights if i.metadata.get('summary'))
    assert summary.metadata['ratio_exegetical'] == 1.0
    assert any(i.metadata.get('flag') == 'all_exegetical' for i in insights)
    # Tags cached
    conn = d._conn()
    types = {r['homiletical_type'] for r in conn.execute(
        "SELECT homiletical_type FROM outline_nodes WHERE session_id = ?", (sid,))}
    assert types == {'exegetical_claim'}


def test_balance_skips_retagging_unchanged_nodes(db):
    d, sid = db
    nodes = [('A', 'exegetical_claim'), ('B', 'application')]
    ids = _seed_nodes(d, sid, nodes)
    g = BalanceGauger(llm=_TaggingLLM({'A': 'exegetical_claim', 'B': 'application'}))
    g.run(d, session_id=sid)
    # Second run: track how many times the LLM was called
    class _Counting:
        def __init__(self, inner): self._inner = inner; self.calls = 0
        def call(self, *a, **kw): self.calls += 1; return self._inner.call(*a, **kw)
    counting = _Counting(_TaggingLLM({'A': 'exegetical_claim', 'B': 'application'}))
    g2 = BalanceGauger(llm=counting)
    g2.run(d, session_id=sid)
    # No nodes were edited between runs, so no LLM call expected.
    assert counting.calls == 0


def test_balance_reports_ratio_for_mixed_outline(db):
    d, sid = db
    nodes = [
        ('exposition of v.21', 'exegetical_claim'),
        ('cross-ref to Habakkuk', 'exegetical_claim'),
        ('illustration: Roman patronage', 'illustration'),
        ('application: trust Christ alone', 'application'),
    ]
    _seed_nodes(d, sid, nodes)
    g = BalanceGauger(llm=_TaggingLLM({c: t for c, t in nodes}))
    insights = g.run(d, session_id=sid)
    summary = next(i for i in insights if i.metadata.get('summary'))
    assert abs(summary.metadata['ratio_exegetical'] - 0.5) < 0.01
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_balance_gauger.py -v`
Expected: ImportError on `BalanceGauger`.

- [ ] **Step 3: Append `BalanceGauger` to `homiletics_engine.py`**

```python
_HOMILETICAL_TAGS = (
    'exegetical_claim', 'application', 'illustration', 'christ_thread',
    'question', 'transition', 'other',
)

_BALANCE_TAG_SCHEMA = {
    'type': 'object',
    'properties': {
        'tags': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'node_id': {'type': 'integer'},
                    'homiletical_type': {'type': 'string', 'enum': list(_HOMILETICAL_TAGS)},
                },
                'required': ['node_id', 'homiletical_type'],
            },
        },
    },
    'required': ['tags'],
}

_BALANCE_TAG_SYSTEM = (
    "Classify each outline node as one of: exegetical_claim (an exposition / "
    "textual observation), application (so-what for the listener), illustration "
    "(a story, image, anecdote), christ_thread (a Christ-centered angle), "
    "question (rhetorical or pastoral), transition (connective tissue), other. "
    "Use your best single label."
)


class BalanceGauger:
    """Tag outline nodes and report exegesis-vs-rest balance."""

    name = 'gauge_outline_balance'

    def __init__(self, llm=None):
        self._llm = llm

    def run(self, db, *, session_id: int) -> list:
        nodes = self._load_nodes(db, session_id)
        if not nodes:
            return []
        stale = [n for n in nodes if self._is_stale(n)]
        if stale:
            self._retag(db, stale)
            nodes = self._load_nodes(db, session_id)
        counts = self._counts(nodes)
        total = sum(counts.values()) or 1
        ratio_exegetical = counts.get('exegetical_claim', 0) / total
        summary = Insight(
            kind='balance_flag',
            title=f"Outline balance: {int(ratio_exegetical * 100)}% exegetical",
            body=self._summary_body(counts, total),
            citations=[],
            confidence=1.0,
            metadata={
                'summary': True,
                'ratio_exegetical': round(ratio_exegetical, 3),
                'total_nodes': total,
                'tag_counts': counts,
            },
        )
        persist(db, summary, session_id=session_id, source_tool=self.name)
        flags = [summary]
        if ratio_exegetical >= 0.9 and total >= 3:
            flag = Insight(
                kind='balance_flag',
                title='Outline is almost entirely exegetical',
                body=(f"{counts.get('exegetical_claim', 0)} of {total} nodes are "
                      f"exegetical. Consider adding application, illustration, or "
                      f"a Christ-thread."),
                citations=[],
                confidence=0.95,
                metadata={'flag': 'all_exegetical',
                          'ratio_exegetical': round(ratio_exegetical, 3)},
            )
            persist(db, flag, session_id=session_id, source_tool=self.name)
            flags.append(flag)
        elif ratio_exegetical <= 0.1 and total >= 3 and counts.get('application', 0) >= 1:
            flag = Insight(
                kind='balance_flag',
                title='Outline has little exegetical grounding',
                body=('Application without rooting in the text can drift. '
                      'Consider adding exegetical anchors per section.'),
                citations=[],
                confidence=0.85,
                metadata={'flag': 'thin_exegesis',
                          'ratio_exegetical': round(ratio_exegetical, 3)},
            )
            persist(db, flag, session_id=session_id, source_tool=self.name)
            flags.append(flag)
        return flags

    def _load_nodes(self, db, session_id: int) -> list:
        conn = db._conn()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT id, content, type, updated_at, homiletical_type, "
                "homiletical_type_at FROM outline_nodes WHERE session_id = ?",
                (session_id,)).fetchall()]
        finally:
            conn.close()

    def _is_stale(self, node: dict) -> bool:
        if not node.get('homiletical_type'):
            return True
        tag_at = node.get('homiletical_type_at')
        if not tag_at:
            return True
        return node.get('updated_at') and node['updated_at'] > tag_at

    def _retag(self, db, nodes: list):
        llm = self._llm or self._default_llm()
        prompt_lines = ['Outline nodes to classify:', '']
        for i, n in enumerate(nodes):
            prompt_lines.append(f"Node {i} [{n['id']}]: {n['content']}")
        prompt = "\n".join(prompt_lines)
        result = llm.call(
            prompt=prompt, schema=_BALANCE_TAG_SCHEMA, system=_BALANCE_TAG_SYSTEM,
            tool_name='emit_node_tags',
            tool_description='Classify outline nodes by homiletical type.',
        )
        if 'error' in result or 'output' not in result:
            return
        tags = result['output'].get('tags') or []
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn = db._conn()
        try:
            for t in tags:
                conn.execute(
                    "UPDATE outline_nodes SET homiletical_type = ?, "
                    "homiletical_type_at = ? WHERE id = ?",
                    (t['homiletical_type'], now, t['node_id']),
                )
            conn.commit()
        finally:
            conn.close()

    def _counts(self, nodes: list) -> dict:
        counts = {tag: 0 for tag in _HOMILETICAL_TAGS}
        for n in nodes:
            t = n.get('homiletical_type') or 'other'
            counts[t] = counts.get(t, 0) + 1
        return {k: v for k, v in counts.items() if v > 0}

    def _summary_body(self, counts: dict, total: int) -> str:
        lines = [f"Total nodes: {total}"]
        for tag, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            pct = int(100 * n / total)
            lines.append(f"  {tag}: {n} ({pct}%)")
        return "\n".join(lines)

    def _default_llm(self):
        from llm_client import AnthropicClient
        from app_secrets import anthropic_api_key
        return AnthropicClient(api_key=anthropic_api_key())
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_balance_gauger.py -v`
Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/homiletics_engine.py tools/workbench/tests/test_balance_gauger.py
git commit -m "feat(homiletics): BalanceGauger with outline tag cache"
```

---

### Task 12: CoherenceChecker (embeddings)

**Files:**
- Modify: `tools/workbench/homiletics_engine.py`
- Test: `tools/workbench/tests/test_coherence_checker.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_coherence_checker.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from embeddings_client import CannedEmbeddingsClient
from homiletics_engine import CoherenceChecker


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    sid_a = d.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                              verse_start=21, verse_end=26, genre='epistle')
    sid_b = d.create_session(passage_ref='Ephesians 2:8-9', book=69, chapter=2,
                              verse_start=8, verse_end=9, genre='epistle')
    yield d, sid_a, sid_b
    os.unlink(path)


def _seed_outline(d, sid, contents):
    from datetime import datetime, timezone
    conn = d._conn()
    now = datetime.now(timezone.utc).isoformat()
    ids = []
    for i, c in enumerate(contents):
        cur = conn.execute(
            "INSERT INTO outline_nodes (session_id, type, content, rank, created_at, updated_at) "
            "VALUES (?, 'point', ?, ?, ?, ?)", (sid, c, i, now, now))
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def test_finds_similar_prior_claim(db):
    d, sid_a, sid_b = db
    _seed_outline(d, sid_b,
                   ["Christ propitiated God's wrath at the cross."])
    emb = CannedEmbeddingsClient(dimension=8)
    # CannedEmbeddingsClient is deterministic by text; we'll just check the
    # checker returns the prior insight (similarity 1.0 since identical text).
    checker = CoherenceChecker(embeddings=emb, similarity_threshold=0.5)
    insights = checker.run(d, claim="Christ propitiated God's wrath at the cross.",
                           session_id=sid_a)
    assert len(insights) >= 1
    assert insights[0].kind == 'coherence_flag'
    assert insights[0].metadata.get('similarity', 0) > 0.99


def test_excludes_self_session(db):
    d, sid_a, sid_b = db
    _seed_outline(d, sid_a, ["a claim in same session"])
    emb = CannedEmbeddingsClient(dimension=8)
    checker = CoherenceChecker(embeddings=emb, similarity_threshold=0.0)
    insights = checker.run(d, claim="a claim in same session", session_id=sid_a)
    assert insights == []


def test_returns_empty_when_no_prior_outlines(db):
    d, sid_a, sid_b = db
    emb = CannedEmbeddingsClient(dimension=8)
    checker = CoherenceChecker(embeddings=emb, similarity_threshold=0.5)
    assert checker.run(d, claim='novel', session_id=sid_a) == []


def test_threshold_filters_low_similarity(db):
    d, sid_a, sid_b = db
    _seed_outline(d, sid_b, ["completely unrelated topic about sheep husbandry"])
    emb = CannedEmbeddingsClient(dimension=8)
    checker = CoherenceChecker(embeddings=emb, similarity_threshold=0.99)
    insights = checker.run(d, claim='propitiation in Romans 3',
                           session_id=sid_a)
    assert insights == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_coherence_checker.py -v`
Expected: ImportError on `CoherenceChecker`.

- [ ] **Step 3: Append `CoherenceChecker` to `homiletics_engine.py`**

```python
class CoherenceChecker:
    """Find prior outline claims similar to a new claim, via embeddings."""

    name = 'check_sermon_coherence'
    SOURCE_KIND = 'outline_node'

    def __init__(self, embeddings=None, similarity_threshold: float = 0.75,
                 top_k: int = 3, model_name: str = None):
        self._emb = embeddings
        self._threshold = similarity_threshold
        self._top_k = top_k
        self._model_name_override = model_name

    def run(self, db, *, session_id: int, claim: str) -> list:
        if not claim or not claim.strip():
            return []
        emb = self._emb or self._default_embeddings()
        model_name = self._model_name_override or self._infer_model_name(emb)

        # Embed the incoming claim once.
        query_vec = emb.embed_one(claim)

        # Find all candidate outline_nodes from OTHER sessions.
        candidates = self._load_candidate_nodes(db, exclude_session_id=session_id)
        if not candidates:
            return []

        # Embed any candidates we haven't embedded yet (lazy backfill).
        cached = {r['source_id']: r['vector'] for r in db.get_claim_embeddings(
            source_kind=self.SOURCE_KIND, model=model_name,
            source_ids=[c['id'] for c in candidates],
        )}
        to_embed = [c for c in candidates if c['id'] not in cached]
        if to_embed:
            vecs = emb.embed([c['content'] for c in to_embed])
            for cand, vec in zip(to_embed, vecs):
                db.save_claim_embedding(
                    source_kind=self.SOURCE_KIND, source_id=cand['id'],
                    claim_text=cand['content'], vector=vec, model=model_name,
                )
                cached[cand['id']] = vec

        scored = []
        for c in candidates:
            v = cached.get(c['id'])
            if not v:
                continue
            sim = self._cosine(query_vec, v)
            if sim >= self._threshold:
                scored.append((sim, c))
        scored.sort(key=lambda sc: -sc[0])

        results = []
        for sim, c in scored[:self._top_k]:
            insight = Insight(
                kind='coherence_flag',
                title=f"Similar prior claim (sim {sim:.2f})",
                body=f"**This session's claim:** {claim}\n\n**Prior:** {c['content']}",
                citations=[f"session:{c['session_id']}", f"outline_node:{c['id']}"],
                confidence=float(sim),
                metadata={
                    'similarity': round(float(sim), 4),
                    'prior_session_id': c['session_id'],
                    'prior_node_id': c['id'],
                },
            )
            persist(db, insight, session_id=session_id, source_tool=self.name)
            results.append(insight)
        return results

    def _load_candidate_nodes(self, db, exclude_session_id: int) -> list:
        conn = db._conn()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT id, session_id, content FROM outline_nodes "
                "WHERE session_id != ? AND content IS NOT NULL AND length(content) > 0",
                (exclude_session_id,)).fetchall()]
        finally:
            conn.close()

    def _cosine(self, a: list, b: list) -> float:
        import math
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(x * x for x in b)) or 1.0
        return dot / (na * nb)

    def _default_embeddings(self):
        from embeddings_client import LocalEmbeddingsClient
        return LocalEmbeddingsClient()

    def _infer_model_name(self, emb) -> str:
        # LocalEmbeddingsClient exposes _model_name; CannedEmbeddingsClient has none.
        return getattr(emb, '_model_name', 'canned-test')
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_coherence_checker.py -v`
Expected: 4 passes.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/homiletics_engine.py tools/workbench/tests/test_coherence_checker.py
git commit -m "feat(homiletics): CoherenceChecker via embedding similarity"
```

---

### Task 13: Seven LLM tools + dispatch + system-prompt addendum

**Files:**
- Modify: `tools/workbench/companion_tools.py` (7 tool defs + dispatch cases + 7 impl functions)
- Modify: `tools/workbench/companion_agent.py` (system-prompt addendum naming the tools and when to call each)
- Test: `tools/workbench/tests/test_homiletics_tools.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_homiletics_tools.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from companion_tools import get_tool_names, execute_tool


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


@pytest.fixture
def session_ctx():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    sid = d.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                            verse_start=21, verse_end=26, genre='epistle')
    yield {
        'db': d, 'session_id': sid, 'passage_ref': 'Romans 3:21-26',
        'book': 66, 'chapter': 3, 'verse_start': 21, 'verse_end': 26,
        'genre': 'epistle',
    }
    os.unlink(path)


def test_all_seven_tools_registered():
    names = set(get_tool_names())
    expected = {
        'find_illustration', 'find_objections', 'find_counseling_applications',
        'find_wisdom_principles', 'suggest_christ_thread', 'gauge_outline_balance',
        'check_sermon_coherence',
    }
    assert expected.issubset(names), f"missing: {expected - names}"


def test_unknown_required_input_returns_friendly_error(session_ctx):
    r = execute_tool('find_illustration', {}, session_ctx)
    assert 'error' in r


def test_check_sermon_coherence_empty_claim_returns_error(session_ctx):
    r = execute_tool('check_sermon_coherence', {'claim': ''}, session_ctx)
    assert 'error' in r


def test_gauge_outline_balance_with_empty_outline_returns_empty(session_ctx, monkeypatch):
    # Mock the BalanceGauger so it doesn't try to call the real LLM.
    from homiletics_engine import BalanceGauger
    def _empty_run(self, db, *, session_id):
        return []
    monkeypatch.setattr(BalanceGauger, 'run', _empty_run)
    r = execute_tool('gauge_outline_balance', {}, session_ctx)
    assert 'insights' in r
    assert r['insights'] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_homiletics_tools.py -v`
Expected: first test fails because tools aren't registered yet.

- [ ] **Step 3: Add 7 tool definitions to `companion_tools.py`**

In `tools/workbench/companion_tools.py`, find the `TOOL_DEFINITIONS = [` block and the closing `]` (the last tool was `suggest_search_terms` from the previous shipping). Immediately BEFORE the closing `]`, insert these seven entries:

```python
    {
        "name": "find_illustration",
        "description": (
            "Find sermon illustrations for a theme. Searches the library for "
            "monographs and biographies, then ranks candidates by narrative "
            "concreteness using Opus. Returns 1-3 insights each with a snippet, "
            "source resource, and one-line rationale. Use when an outline point "
            "lacks a story or vivid image."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "theme": {"type": "string", "description": "Theme or topic to illustrate."},
                "count": {"type": "integer", "description": "How many illustrations to return (1-5). Default 3."},
            },
            "required": ["theme"],
        },
    },
    {
        "name": "find_objections",
        "description": (
            "Find likely objections to a claim the sermon is making. Combines a "
            "library search with an Opus pass over four categories: theological, "
            "pastoral, cultural, honest_doubt. Returns insights each with the "
            "objection in the objector's voice and a one-line rebuttal pointer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "claim": {"type": "string", "description": "The sermon claim under examination."},
                "count": {"type": "integer", "description": "Number of objections (1-6). Default 3."},
            },
            "required": ["claim"],
        },
    },
    {
        "name": "find_counseling_applications",
        "description": (
            "Map the session's passage to common pastoral-counseling situations "
            "(guilt, anxiety, marriage_conflict, etc.). Each insight names the "
            "situation, gives a one-paragraph application, and tags a tier "
            "(preventative / remedial / diagnostic). Uses the session's passage; "
            "no extra input required."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of applications (1-5). Default 3."},
            },
            "required": [],
        },
    },
    {
        "name": "find_wisdom_principles",
        "description": (
            "Distill 2-3 transferable wisdom principles from the session's passage. "
            "Each principle is a single proverbial sentence with rationale, "
            "topical tags, and 1-3 cross-references. Use when the outline needs "
            "preachable take-home sentences."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of principles (1-5). Default 3."},
            },
            "required": [],
        },
    },
    {
        "name": "suggest_christ_thread",
        "description": (
            "Propose 1-3 Christ-thread angles for the session's passage from a "
            "Reformed homiletical perspective. Each angle is tagged by category "
            "(typology / promise_fulfillment / contrast / redemptive_historical / "
            "direct_reference) with supporting refs. Optionally focus on a specific "
            "point already in the outline."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "point": {"type": "string", "description": "Optional outline point to focus the Christ-thread."},
                "count": {"type": "integer", "description": "Number of angles (1-5). Default 3."},
            },
            "required": [],
        },
    },
    {
        "name": "gauge_outline_balance",
        "description": (
            "Tag every node in the session's outline by homiletical type "
            "(exegetical_claim / application / illustration / christ_thread / etc.) "
            "and return the exegesis-vs-rest ratio plus flags for sections that "
            "are nearly pure exegesis or nearly pure application. Tag results are "
            "cached on outline_nodes; subsequent calls only re-tag changed nodes."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "check_sermon_coherence",
        "description": (
            "Find prior sermon outlines that made a similar claim to this one. "
            "Uses local embedding similarity. Useful for catching repetition "
            "('you preached this same Christ-thread 3 weeks ago') or unintentional "
            "drift from prior positions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "claim": {"type": "string", "description": "The claim or proposition to check against prior sermons."},
            },
            "required": ["claim"],
        },
    },
```

- [ ] **Step 4: Add the seven dispatch cases**

In the same file's `execute_tool()` function, find the `elif tool_name == "suggest_search_terms":` block and add immediately after it (before the `else:`):

```python
        elif tool_name == "find_illustration":
            return _find_illustration(tool_input, session_context)
        elif tool_name == "find_objections":
            return _find_objections(tool_input, session_context)
        elif tool_name == "find_counseling_applications":
            return _find_counseling_applications(tool_input, session_context)
        elif tool_name == "find_wisdom_principles":
            return _find_wisdom_principles(tool_input, session_context)
        elif tool_name == "suggest_christ_thread":
            return _suggest_christ_thread(tool_input, session_context)
        elif tool_name == "gauge_outline_balance":
            return _gauge_outline_balance(tool_input, session_context)
        elif tool_name == "check_sermon_coherence":
            return _check_sermon_coherence(tool_input, session_context)
```

- [ ] **Step 5: Add the seven impl functions at the bottom of `companion_tools.py`**

Append:

```python
# ── Homiletics Engine tools ──────────────────────────────────────────────────


def _ctx_passage(ctx):
    return {
        'book': ctx.get('book'),
        'chapter': ctx.get('chapter'),
        'verse_start': ctx.get('verse_start'),
        'verse_end': ctx.get('verse_end'),
        'passage_ref': ctx.get('passage_ref'),
    }


def _serialize_insights(insights):
    return [
        {
            'kind': i.kind,
            'title': i.title,
            'body': i.body,
            'citations': list(i.citations),
            'confidence': i.confidence,
            'metadata': dict(i.metadata),
        }
        for i in insights
    ]


def _find_illustration(tool_input, ctx):
    theme = (tool_input.get('theme') or '').strip()
    if not theme:
        return {'error': 'theme is required'}
    count = max(1, min(int(tool_input.get('count', 3)), 5))
    try:
        from homiletics_engine import IllustrationFinder
        insights = IllustrationFinder().run(
            ctx['db'], session_id=ctx['session_id'],
            theme=theme, passage=_ctx_passage(ctx), count=count,
        )
        return {'insights': _serialize_insights(insights)}
    except Exception as e:
        return {'error': f'illustration_failed: {type(e).__name__}: {e}'}


def _find_objections(tool_input, ctx):
    claim = (tool_input.get('claim') or '').strip()
    if not claim:
        return {'error': 'claim is required'}
    count = max(1, min(int(tool_input.get('count', 3)), 6))
    try:
        from homiletics_engine import ObjectionFinder
        insights = ObjectionFinder().run(
            ctx['db'], session_id=ctx['session_id'],
            claim=claim, count=count, passage=_ctx_passage(ctx),
        )
        return {'insights': _serialize_insights(insights)}
    except Exception as e:
        return {'error': f'objection_failed: {type(e).__name__}: {e}'}


def _find_counseling_applications(tool_input, ctx):
    count = max(1, min(int(tool_input.get('count', 3)), 5))
    try:
        from homiletics_engine import CounselingApplications
        insights = CounselingApplications().run(
            ctx['db'], session_id=ctx['session_id'],
            passage=_ctx_passage(ctx), count=count,
        )
        return {'insights': _serialize_insights(insights)}
    except Exception as e:
        return {'error': f'counseling_failed: {type(e).__name__}: {e}'}


def _find_wisdom_principles(tool_input, ctx):
    count = max(1, min(int(tool_input.get('count', 3)), 5))
    try:
        from homiletics_engine import WisdomPrinciples
        insights = WisdomPrinciples().run(
            ctx['db'], session_id=ctx['session_id'],
            passage=_ctx_passage(ctx), count=count,
        )
        return {'insights': _serialize_insights(insights)}
    except Exception as e:
        return {'error': f'wisdom_failed: {type(e).__name__}: {e}'}


def _suggest_christ_thread(tool_input, ctx):
    point = tool_input.get('point')
    count = max(1, min(int(tool_input.get('count', 3)), 5))
    try:
        from homiletics_engine import ChristThreadProposer
        insights = ChristThreadProposer().run(
            ctx['db'], session_id=ctx['session_id'],
            passage=_ctx_passage(ctx), point=point, count=count,
        )
        return {'insights': _serialize_insights(insights)}
    except Exception as e:
        return {'error': f'christ_thread_failed: {type(e).__name__}: {e}'}


def _gauge_outline_balance(tool_input, ctx):
    try:
        from homiletics_engine import BalanceGauger
        insights = BalanceGauger().run(ctx['db'], session_id=ctx['session_id'])
        return {'insights': _serialize_insights(insights)}
    except Exception as e:
        return {'error': f'balance_failed: {type(e).__name__}: {e}'}


def _check_sermon_coherence(tool_input, ctx):
    claim = (tool_input.get('claim') or '').strip()
    if not claim:
        return {'error': 'claim is required'}
    try:
        from homiletics_engine import CoherenceChecker
        insights = CoherenceChecker().run(
            ctx['db'], session_id=ctx['session_id'], claim=claim,
        )
        return {'insights': _serialize_insights(insights)}
    except Exception as e:
        return {'error': f'coherence_failed: {type(e).__name__}: {e}'}
```

- [ ] **Step 6: Add system-prompt addendum to `companion_agent.py`**

In `tools/workbench/companion_agent.py`, find the existing `build_study_prompt(...)` function (use `grep -n "def build_study_prompt" tools/workbench/companion_agent.py`). At the end of that function's prompt-assembly block (just before it returns the assembled prompt), append a new section:

```python
    prompt += """

# Homiletics tools (Phase 1)

Seven new tools assist with the homiletical bridge. Call them when the
moment is right — not on every turn:

- `find_illustration(theme)` — when an outline point lacks a story or vivid image.
- `find_objections(claim)` — when a claim is bold or counter-intuitive; surface
  what a thoughtful objector would say.
- `find_counseling_applications` — early in homiletics planning, to anchor the
  sermon in real pastoral situations.
- `find_wisdom_principles` — when distilling preachable take-home sentences.
- `suggest_christ_thread(point?)` — when you finish a point and want to test
  whether you can get to Christ from here.
- `gauge_outline_balance` — at end-of-prep check, to see exegesis-vs-application ratio.
- `check_sermon_coherence(claim)` — when a claim feels like you've made it before.

These tools persist their results to the homiletics_insights table for later
use by the pre-flight review and post-preach learning phases (not in Phase 1).
"""
    return prompt
```

If the build_study_prompt function returns a literal early or uses a different assembly style, append the same text in a similar idiomatic way. The key is that the string above ends up appended to the system prompt sent on study-mode turns.

- [ ] **Step 7: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_homiletics_tools.py -v`
Expected: 4 passes.

- [ ] **Step 8: Commit**

```bash
git add tools/workbench/companion_tools.py tools/workbench/companion_agent.py \
        tools/workbench/tests/test_homiletics_tools.py
git commit -m "feat(homiletics): seven LLM tools + dispatch + system-prompt addendum"
```

---

### Task 14: End-to-end test + manual verification

**Files:**
- Create: `tools/workbench/tests/test_homiletics_engine_e2e.py`

- [ ] **Step 1: Write the E2E test**

Create `tools/workbench/tests/test_homiletics_engine_e2e.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from embeddings_client import CannedEmbeddingsClient
from homiletics_engine import (
    Insight, ANALYZER_KINDS, IllustrationFinder, ChristThreadProposer,
    WisdomPrinciples, ObjectionFinder, CounselingApplications,
    BalanceGauger, CoherenceChecker,
)


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    sid = d.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                            verse_start=21, verse_end=26, genre='epistle')
    yield d, sid
    os.unlink(path)


class _CannedLLM:
    def __init__(self, output): self._output = output
    def call(self, prompt, schema, **kwargs):
        return {'output': self._output, 'input_tokens': 0,
                'output_tokens': 0, 'model': 'test'}


class _CannedSearcher:
    def __init__(self, results): self._results = results
    def search(self, q, **kw): return {'ok': True, 'results': self._results}


def test_all_seven_kinds_can_persist_to_one_session(db):
    d, sid = db
    passage = {'book': 66, 'chapter': 3, 'verse_start': 21, 'verse_end': 26,
               'passage_ref': 'Romans 3:21-26'}

    # IllustrationFinder
    IllustrationFinder(
        searcher=_CannedSearcher([{'resource_id': 'LLS:R', 'title': 't',
                                     'sections': [{'snippet': 's', 'section_offset': 0}]}]),
        llm=_CannedLLM({'ranked': [{'index': 0, 'score': 0.8, 'rationale': 'r'}]}),
    ).run(d, session_id=sid, theme='grace', count=1)

    # ChristThreadProposer
    ChristThreadProposer(llm=_CannedLLM({'angles': [
        {'category': 'typology', 'angle': 'a', 'rationale': 'r',
         'supporting_refs': ['Romans 3:25']}
    ]})).run(d, session_id=sid, passage=passage, count=1)

    # WisdomPrinciples
    WisdomPrinciples(llm=_CannedLLM({'principles': [
        {'principle': 'p', 'rationale': 'r', 'cross_refs': []}
    ]})).run(d, session_id=sid, passage=passage, count=1)

    # ObjectionFinder
    ObjectionFinder(
        searcher=_CannedSearcher([]),
        llm=_CannedLLM({'objections': [
            {'objection_class': 'theological', 'objection': 'o', 'rebuttal_pointer': 'p'}
        ]}),
    ).run(d, session_id=sid, claim='c', count=1)

    # CounselingApplications
    CounselingApplications(llm=_CannedLLM({'applications': [
        {'pastoral_situation': 'guilt', 'application': 'a', 'tier': 'remedial'}
    ]})).run(d, session_id=sid, passage=passage, count=1)

    # BalanceGauger needs an outline node first
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    d._conn().execute(
        "INSERT INTO outline_nodes (session_id, type, content, rank, created_at, updated_at) "
        "VALUES (?, 'point', 'a node', 0, ?, ?)", (sid, now, now))
    d._conn().commit()
    BalanceGauger(llm=_CannedLLM({'tags': [
        {'node_id': 1, 'homiletical_type': 'exegetical_claim'}
    ]})).run(d, session_id=sid)

    # CoherenceChecker needs a node in another session
    other = d.create_session(passage_ref='Eph 2', book=69, chapter=2,
                              verse_start=8, verse_end=9, genre='epistle')
    d._conn().execute(
        "INSERT INTO outline_nodes (session_id, type, content, rank, created_at, updated_at) "
        "VALUES (?, 'point', 'grace not works', 0, ?, ?)", (other, now, now))
    d._conn().commit()
    CoherenceChecker(embeddings=CannedEmbeddingsClient(dimension=8),
                     similarity_threshold=0.0).run(
        d, session_id=sid, claim='grace not works')

    # Assert all seven kinds persisted
    rows = d.get_insights(sid)
    kinds = {r['kind'] for r in rows}
    assert ANALYZER_KINDS.issubset(kinds), f"missing kinds: {ANALYZER_KINDS - kinds}"
```

- [ ] **Step 2: Run the test**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_homiletics_engine_e2e.py -v`
Expected: 1 pass.

- [ ] **Step 3: Run the full homiletics test suite**

Run:
```
cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest \
  tests/test_homiletics_schema.py tests/test_homiletics_db_helpers.py \
  tests/test_homiletics_engine_base.py tests/test_embeddings_client.py \
  tests/test_illustration_finder.py tests/test_christ_thread_proposer.py \
  tests/test_wisdom_principles.py tests/test_objection_finder.py \
  tests/test_counseling_applications.py tests/test_balance_gauger.py \
  tests/test_coherence_checker.py tests/test_homiletics_tools.py \
  tests/test_homiletics_engine_e2e.py -v
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tools/workbench/tests/test_homiletics_engine_e2e.py
git commit -m "test(homiletics): end-to-end persistence of all seven analyzer kinds"
```

- [ ] **Step 5: Manual verification on live session 20**

Hand back to Bryan to run:

```bash
# One-time, before first CoherenceChecker call:
/Volumes/External/Logos4/tools/setup_embeddings.sh

# Restart the workbench so it picks up the seven new tools:
pm2 restart study-companion
```

Then in a browser at `https://study.mightyoakshybrid.org/study/session/20`, ask the AI study partner something like "Find me an illustration for grace as patronage." The LLM should pick `find_illustration`, return a Jeffers/IVP BBC snippet, and persist an insight row. Verify with:

```bash
sqlite3 /Volumes/External/Logos4/tools/workbench/companion.db \
  "SELECT kind, title, source_tool, created_at FROM homiletics_insights ORDER BY created_at DESC LIMIT 5"
```

If you see the row, Phase 1 is live.

---

## Out of scope (Phase 2-4)

- Real-time hooks on `save_outline_node` / `save_card_response` to fire matching analyzers asynchronously.
- A pre-flight review page that renders all unconsumed insights as a code-review-style checklist.
- Post-preach learning loop: join `homiletics_insights.consumed_at` against transcript content to score which analyzer kinds moved the needle on evaluator scores.
- `severity` field on Insight (deferred until Phase 3 review UI needs it).
- `phase` field on Insight (deferred until Phase 4 learning needs it).
