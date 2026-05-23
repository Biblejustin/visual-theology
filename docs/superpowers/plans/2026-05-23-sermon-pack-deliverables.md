# Sermon-Pack Deliverables Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce three sermon deliverables (manuscript, preaching outline, congregation handout) per session from a single click, with mining-percentage instrumentation that exposes how much content the agent failed to capture in structured panels.

**Architecture:** A pure passage-agnostic Python generator (`tools/sermon_pack.py`) reads `companion.db` directly (no Flask, no LLM), resolves each artifact field through a fixed source precedence (decisions → outline_nodes → notepads/cards → conversation mining), and renders three `.format()`-substituted markdown templates. A new `sermon_pack_metrics` table records per-artifact source breakdowns so the follow-on upstream-fix spec can measure mining-% trending toward zero. Three Flask routes wrap the generator; one small refactor to `tools/md_to_html.py` exposes string-based HTML rendering. The outline-sidebar export block is restructured into two clearly labeled groups: "SERMON PACK" (new) and "OUTLINE TREE ONLY" (existing PDF/Word).

**Tech Stack:** Python 3 stdlib (sqlite3, pathlib, dataclasses, re, argparse), Flask (existing), the existing `tools/md_to_html.py` renderer, plain HTML/CSS/vanilla JS.

---

## Spec

Authoritative spec: `docs/superpowers/specs/2026-05-23-sermon-pack-deliverables-design.md`. Each subagent receives a pointer to it plus this plan's task text. The hand-assembled reference outputs at `/Volumes/External/Logos4/Romans3/Romans-3-25-26_{manuscript,preaching-outline,congregation-handout}.md` are the visual targets for generated output shape (NOT byte-identical — they are the human gold standard the generator approximates from the database).

## File structure

**Create:**
- `tools/sermon_pack.py` — generator module + CLI (one file: data extraction, source resolution, mining, render orchestrator, CLI entrypoint).
- `tools/sermon_pack_templates/manuscript.md.tmpl`
- `tools/sermon_pack_templates/outline.md.tmpl`
- `tools/sermon_pack_templates/handout.md.tmpl`
- `tools/workbench/tests/test_sermon_pack.py` — unit tests against synthetic SQLite fixtures.
- `tools/workbench/tests/test_sermon_pack_routes.py` — Flask route tests.

**Modify:**
- `tools/md_to_html.py` — extract `md_to_html_string(md, stem) -> str` from `convert()` so routes can render in-memory.
- `tools/workbench/companion_db.py` — add `sermon_pack_metrics` table to `init_db()` + `save_sermon_pack_metric` / `get_latest_sermon_pack_metrics` helpers.
- `tools/workbench/app.py` — three new pack routes + one metrics endpoint for the source-breakdown pill.
- `tools/workbench/templates/study_session.html` — restructure `.outline-export-actions` block into two groups + add source-breakdown pill placeholder.
- `tools/workbench/static/study.js` — small fetch wiring the source-breakdown pill (no version bump assumed; bumped at deploy if needed).
- `tools/workbench/static/study.css` — styles for the new sermon-pack group + pill.

**Reuse (no changes beyond Task 10's small refactor):** `tools/md_to_html.py` (existing handout tight-print profile + `<!--PAGEBREAK-->` marker).

## Hard rules (enforced in every task)

- **No em-dashes (`—`) or double-hyphens (`--`)** in any generated prose, template, or comment that ships into output. Use periods, commas, semicolons, colons, parens. EN-dashes (`–`) in numeric ranges only ("Hebrews 9–10", "30–39 min").
- **No fabrication.** Missing fields render as `[missing: <field_name>]`. Mining only surfaces text the agent or Bryan actually wrote.
- **No silent fallbacks.** Errors are typed and surfaced; do not swallow exceptions to "keep going."
- **TDD strictly.** Write a failing test FIRST, watch it fail for the right reason, write minimal code, watch it pass, then commit. No batch-implement-then-test.
- **Generator runs without LogosReader.** Tests use synthetic SQLite fixtures only; no live `.logos4` dependencies.
- **Determinism.** No timestamps in artifact bodies; only in metrics rows. Mining heuristic must pick the same message every run for the same DB state.
- **`grep —` returns 0** across all template files and across every generated artifact in tests.

---

## Task 1: `sermon_pack_metrics` table + DB helpers

**Files:**
- Modify: `tools/workbench/companion_db.py:67-154` (init_db schema)
- Modify: `tools/workbench/companion_db.py:560` (helpers below `_now`)
- Test: `tools/workbench/tests/test_sermon_pack.py` (new file)

- [ ] **Step 1: Write the failing test**

```python
# tools/workbench/tests/test_sermon_pack.py
import os, sys, tempfile, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from companion_db import CompanionDB


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    cdb = CompanionDB(tmp.name)
    cdb.init_db()
    return cdb, tmp.name


def test_sermon_pack_metrics_table_exists_and_save_round_trips():
    cdb, _ = _fresh_db()
    cdb.save_sermon_pack_metric(
        session_id=1, artifact='manuscript',
        source_breakdown={'decisions': 3, 'mined': 2, 'missing': 1},
        mining_percentage=0.4,
    )
    rows = cdb.get_latest_sermon_pack_metrics(1)
    assert 'manuscript' in rows
    assert rows['manuscript']['mining_percentage'] == 0.4
    assert json.loads(rows['manuscript']['source_breakdown_json']) == {
        'decisions': 3, 'mined': 2, 'missing': 1
    }
```

- [ ] **Step 2: Run test, verify failure for right reason**

```bash
cd tools/workbench && python3 -m pytest tests/test_sermon_pack.py -v
```
Expected: FAIL — `AttributeError: 'CompanionDB' object has no attribute 'save_sermon_pack_metric'`.

- [ ] **Step 3: Add table to `init_db` and helper methods**

In `companion_db.py` `init_db()` executescript block (alongside existing `CREATE TABLE IF NOT EXISTS` calls):

```sql
CREATE TABLE IF NOT EXISTS sermon_pack_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    artifact TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    source_breakdown_json TEXT NOT NULL,
    mining_percentage REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sermon_pack_metrics_session
    ON sermon_pack_metrics(session_id, generated_at DESC);
```

Then below `_now()` add:

```python
def save_sermon_pack_metric(self, *, session_id, artifact,
                            source_breakdown, mining_percentage):
    """Insert one metric row per artifact per generation."""
    with self._safe_write() as conn:
        cur = conn.execute(
            "INSERT INTO sermon_pack_metrics "
            "(session_id, artifact, generated_at, source_breakdown_json, mining_percentage) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, artifact, self._now(),
             json.dumps(source_breakdown), float(mining_percentage)),
        )
        return cur.lastrowid


def get_latest_sermon_pack_metrics(self, session_id):
    """Return {artifact: {source_breakdown_json, mining_percentage, generated_at}}
    using only the most recent row per artifact for this session."""
    conn = self._conn()
    rows = conn.execute(
        "SELECT artifact, source_breakdown_json, mining_percentage, generated_at "
        "FROM sermon_pack_metrics WHERE session_id = ? "
        "ORDER BY generated_at DESC",
        (session_id,),
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        if r['artifact'] not in out:
            out[r['artifact']] = dict(r)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd tools/workbench && python3 -m pytest tests/test_sermon_pack.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/companion_db.py tools/workbench/tests/test_sermon_pack.py
git commit -m "feat(sermon-pack): sermon_pack_metrics table + DB helpers"
```

---

## Task 2: Generator skeleton + `FieldResult` dataclass + source enum

**Files:**
- Create: `tools/sermon_pack.py`
- Modify: `tools/workbench/tests/test_sermon_pack.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to tools/workbench/tests/test_sermon_pack.py
import importlib.util
from pathlib import Path

def _load_sermon_pack():
    """Import tools/sermon_pack.py without requiring sys.path gymnastics."""
    path = Path(__file__).parent.parent.parent / 'sermon_pack.py'
    spec = importlib.util.spec_from_file_location('sermon_pack', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_field_result_dataclass_and_sources():
    sp = _load_sermon_pack()
    fr = sp.FieldResult(value='X', source='decisions')
    assert fr.value == 'X'
    assert fr.source == 'decisions'
    assert sp.SOURCES == ('decisions', 'outline', 'notepad', 'card', 'mined', 'missing')


def test_missing_field_helper_renders_marker():
    sp = _load_sermon_pack()
    fr = sp.missing_field('subject')
    assert fr.value == '[missing: subject]'
    assert fr.source == 'missing'
```

- [ ] **Step 2: Run test, verify failure**

```bash
cd tools/workbench && python3 -m pytest tests/test_sermon_pack.py::test_field_result_dataclass_and_sources -v
```
Expected: FAIL — `FileNotFoundError` (sermon_pack.py doesn't exist).

- [ ] **Step 3: Create `tools/sermon_pack.py`**

```python
"""Sermon-pack deliverables generator.

Pure data transformation: SQLite in, three markdown files out. No Flask,
no LLM. See docs/superpowers/specs/2026-05-23-sermon-pack-deliverables-design.md.

Source precedence per field:
  1. session_decisions  (the 9 keyed homiletical commitments)
  2. outline_nodes      (the structured outline tree)
  3. card_notepads      (Bryan's per-phase notepad work)
  4. card_responses     (Bryan's per-phase card answers)
  5. conversation_messages (mined when nothing above has the field)

Each resolved field carries a `source` tag; per-artifact mining-percentage
is recorded to sermon_pack_metrics. Driving mining-% to zero is the
acceptance metric for the follow-on upstream-wiring spec.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SOURCES = ('decisions', 'outline', 'notepad', 'card', 'mined', 'missing')


@dataclass
class FieldResult:
    """One resolved artifact field.

    `value` is always a string ready for template substitution. When the
    field couldn't be resolved from any source, value is the literal
    `[missing: <field>]` marker and source is "missing"."""
    value: str
    source: str


def missing_field(name: str) -> FieldResult:
    return FieldResult(value=f'[missing: {name}]', source='missing')
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd tools/workbench && python3 -m pytest tests/test_sermon_pack.py -v
```
Expected: PASS (both new tests + Task 1's test).

- [ ] **Step 5: Commit**

```bash
git add tools/sermon_pack.py tools/workbench/tests/test_sermon_pack.py
git commit -m "feat(sermon-pack): generator skeleton + FieldResult"
```

---

## Task 3: Decisions source extractor

**Files:**
- Modify: `tools/sermon_pack.py`
- Modify: `tools/workbench/tests/test_sermon_pack.py`

- [ ] **Step 1: Write the failing test**

```python
def _seed_decisions(db_path, session_id, decisions):
    """Insert session_decisions rows. The table must exist already
    (SessionDecisionStore.init_db ran in the fixture)."""
    from session_decisions import SessionDecisionStore
    store = SessionDecisionStore(db_path)
    store.init_db()
    for k, v in decisions.items():
        store.set_decision(session_id=session_id, key=k, value=v)


def _seed_session(cdb, passage='Romans 3:25-26'):
    return cdb.create_session(
        passage_ref=passage, book=45, chapter=3,
        verse_start=25, verse_end=26, genre='epistle',
    )


def test_load_decisions_returns_keyed_dict():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    _seed_decisions(path, sid, {
        'subject': 'God is righteous.',
        'hp_tht': 'Your God has proven His righteousness.',
        'target_length_min': '28',
    })
    sp = _load_sermon_pack()
    d = sp.load_decisions(path, sid)
    assert d['subject'] == 'God is righteous.'
    assert d['hp_tht'] == 'Your God has proven His righteousness.'
    assert d['target_length_min'] == '28'
    assert 'theme' not in d


def test_load_decisions_empty_session_returns_empty_dict():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    sp = _load_sermon_pack()
    assert sp.load_decisions(path, sid) == {}
```

- [ ] **Step 2: Run test, verify failure**

```bash
cd tools/workbench && python3 -m pytest tests/test_sermon_pack.py::test_load_decisions_returns_keyed_dict -v
```
Expected: FAIL — `AttributeError: module 'sermon_pack' has no attribute 'load_decisions'`.

- [ ] **Step 3: Implement `load_decisions` + `load_session_row` helper**

Add to `tools/sermon_pack.py`:

```python
def _open_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def load_session_row(db_path: str, session_id: int) -> dict | None:
    """Return the sessions row as a dict, or None."""
    conn = _open_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def load_decisions(db_path: str, session_id: int) -> dict[str, str]:
    """Return {key: value} for every committed session_decision for this session."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT key, value FROM session_decisions WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        return {r['key']: r['value'] for r in rows}
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests**

```bash
cd tools/workbench && python3 -m pytest tests/test_sermon_pack.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/sermon_pack.py tools/workbench/tests/test_sermon_pack.py
git commit -m "feat(sermon-pack): decisions source extractor"
```

---

## Task 4: Outline-tree source extractor

**Files:**
- Modify: `tools/sermon_pack.py`
- Modify: `tools/workbench/tests/test_sermon_pack.py`

- [ ] **Step 1: Write the failing test**

```python
def test_load_outline_tree_groups_main_points_with_subs():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    root = cdb.add_outline_node(sid, 'main_point', 'God displayed Jesus as mercy seat',
                                 verse_ref='Rom 3:25')
    cdb.add_outline_node(sid, 'sub_point', 'Tabernacle was a throne room',
                          parent_id=root)
    cdb.add_outline_node(sid, 'sub_point', 'Propitiation = mercy seat',
                          parent_id=root)
    cdb.add_outline_node(sid, 'cross_ref', 'Lev 16',
                          parent_id=root, verse_ref='Lev 16')
    sp = _load_sermon_pack()
    structured = sp.load_outline_points(path, sid)
    assert len(structured) == 1
    pt = structured[0]
    assert pt['title'] == 'God displayed Jesus as mercy seat'
    assert pt['verse_ref'] == 'Rom 3:25'
    assert [s['content'] for s in pt['subs']] == [
        'Tabernacle was a throne room',
        'Propitiation = mercy seat',
    ]
    assert [c['content'] for c in pt['cross_refs']] == ['Lev 16']


def test_load_outline_tree_ignores_title_only_node():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    cdb.add_outline_node(sid, 'title', 'Romans 3:25-26')
    sp = _load_sermon_pack()
    assert sp.load_outline_points(path, sid) == []
```

- [ ] **Step 2: Run, verify failure**

Expected: `AttributeError: no attribute 'load_outline_points'`.

- [ ] **Step 3: Implement `load_outline_points`**

```python
def load_outline_points(db_path: str, session_id: int) -> list[dict]:
    """Return list of main-point dicts:
        {title, verse_ref, subs: [{content, verse_ref}], cross_refs: [...], notes: [...], illustrations: [...]}
    Title-only nodes and orphan sub-points are skipped."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id, parent_id, type, content, verse_ref, rank "
            "FROM outline_nodes WHERE session_id = ? ORDER BY rank",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()

    nodes = [dict(r) for r in rows]
    by_id = {n['id']: n for n in nodes}
    children: dict[int | None, list[dict]] = {}
    for n in nodes:
        children.setdefault(n['parent_id'], []).append(n)

    points = []
    for n in children.get(None, []):
        if n['type'] != 'main_point':
            continue
        kids = children.get(n['id'], [])
        points.append({
            'title': n['content'],
            'verse_ref': n['verse_ref'] or '',
            'subs': [{'content': k['content'], 'verse_ref': k['verse_ref'] or ''}
                     for k in kids if k['type'] == 'sub_point'],
            'cross_refs': [{'content': k['content'], 'verse_ref': k['verse_ref'] or ''}
                           for k in kids if k['type'] == 'cross_ref'],
            'notes': [{'content': k['content']}
                      for k in kids if k['type'] == 'note'],
            'illustrations': [{'content': k['content']}
                              for k in kids if k['type'] == 'illustration'],
        })
    return points
```

- [ ] **Step 4: Run tests** — Expected PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/sermon_pack.py tools/workbench/tests/test_sermon_pack.py
git commit -m "feat(sermon-pack): outline-tree source extractor"
```

---

## Task 5: Notepad + card-response source extractors

**Files:**
- Modify: `tools/sermon_pack.py`
- Modify: `tools/workbench/tests/test_sermon_pack.py`

- [ ] **Step 1: Write the failing test**

```python
def test_load_notepads_returns_phase_keyed_dict():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    cdb.save_card_notepad(sid, 'card_digestion', 'The text wrestles with...')
    cdb.save_card_notepad(sid, 'card_read_translate', 'My translation work...')
    sp = _load_sermon_pack()
    notepads = sp.load_notepads(path, sid)
    assert notepads['card_digestion'] == 'The text wrestles with...'
    assert notepads['card_read_translate'] == 'My translation work...'
    assert 'card_prayer' not in notepads


def test_load_card_responses_flattens_to_phase_keyed_lists():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    cdb.save_response = lambda *a, **kw: None  # ensure no accidental writes
    # Use raw insert because save_response signature varies.
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO card_responses (session_id, phase, question_id, content, created_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (sid, 'observation', 1, 'I notice the word propitiation.'),
    )
    conn.execute(
        "INSERT INTO card_responses (session_id, phase, question_id, content, created_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (sid, 'observation', 2, 'Verb tenses shift in v.26.'),
    )
    conn.commit(); conn.close()
    sp = _load_sermon_pack()
    by_phase = sp.load_card_responses(path, sid)
    assert by_phase['observation'] == [
        'I notice the word propitiation.',
        'Verb tenses shift in v.26.',
    ]
```

- [ ] **Step 2: Run, verify failure**

Expected: `AttributeError`.

- [ ] **Step 3: Implement both loaders**

```python
def load_notepads(db_path: str, session_id: int) -> dict[str, str]:
    """Return {phase: content} for non-empty card_notepads."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT phase, content FROM card_notepads "
            "WHERE session_id = ? AND content != ''",
            (session_id,),
        ).fetchall()
        return {r['phase']: r['content'] for r in rows}
    finally:
        conn.close()


def load_card_responses(db_path: str, session_id: int) -> dict[str, list[str]]:
    """Return {phase: [content, ...]} ordered by created_at."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT phase, content FROM card_responses "
            "WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r['phase'], []).append(r['content'])
    return out
```

- [ ] **Step 4: Run tests** — Expected PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/sermon_pack.py tools/workbench/tests/test_sermon_pack.py
git commit -m "feat(sermon-pack): notepad + card-response extractors"
```

---

## Task 6: Mining heuristic — pick the sermon-shaped assistant message

**Files:**
- Modify: `tools/sermon_pack.py`
- Modify: `tools/workbench/tests/test_sermon_pack.py`

- [ ] **Step 1: Write the failing test**

```python
def _insert_msg(path, sid, role, content, phase='study'):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO conversation_messages "
        "(session_id, phase, role, content, created_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (sid, phase, role, content),
    )
    conn.commit(); conn.close()


def test_mine_largest_outline_message_picks_richest_assistant_turn():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    _insert_msg(path, sid, 'user', 'short user note')
    _insert_msg(path, sid, 'assistant',
                'Brief acknowledgement, no headings here.')
    big = ('# Sermon outline\n\n## INTRODUCTION\n\nOpening prose.\n\n'
           '## POINT 1: God displayed Jesus\n\nBody text body text.\n\n'
           '## POINT 2: God proved His righteousness\n\nMore body.\n\n'
           '## CONCLUSION\n\nClosing prose.')
    _insert_msg(path, sid, 'assistant', big)
    _insert_msg(path, sid, 'assistant', 'Quick follow-up, no structure.')
    sp = _load_sermon_pack()
    chosen = sp.mine_outline_message(path, sid)
    assert chosen is not None
    assert chosen.startswith('# Sermon outline')


def test_mine_outline_message_returns_none_when_no_qualifying_message():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    _insert_msg(path, sid, 'assistant', 'No markdown headings here at all.')
    _insert_msg(path, sid, 'user', '## a heading from a user does not count')
    sp = _load_sermon_pack()
    assert sp.mine_outline_message(path, sid) is None


def test_mine_is_deterministic_across_calls():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    a = '# A\n\n## Heading 1\n## Heading 2\n## Heading 3\n\nBody.'
    b = '# B\n\n## Heading 1\n## Heading 2\n## Heading 3\n\nBody.'
    _insert_msg(path, sid, 'assistant', a)
    _insert_msg(path, sid, 'assistant', b)
    sp = _load_sermon_pack()
    assert sp.mine_outline_message(path, sid) == sp.mine_outline_message(path, sid)
```

- [ ] **Step 2: Run, verify failure**

Expected: `AttributeError`.

- [ ] **Step 3: Implement mining heuristic**

```python
_MIN_HEADINGS_FOR_OUTLINE = 3


def mine_outline_message(db_path: str, session_id: int) -> str | None:
    """Return the assistant message most likely to contain a full sermon outline.

    Heuristic: an assistant message in any phase that contains at least
    `_MIN_HEADINGS_FOR_OUTLINE` markdown headings (lines starting with `## `
    or higher). Among qualifying messages, pick the longest. Ties broken by
    higher message id (most recent), so the result is fully deterministic
    given the database state."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id, content FROM conversation_messages "
            "WHERE session_id = ? AND role = 'assistant' AND content IS NOT NULL "
            "ORDER BY id",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    best = None
    best_key = (-1, -1)  # (length, id)
    for r in rows:
        c = r['content']
        if len(re.findall(r'(?m)^#{2,6}\s', c)) < _MIN_HEADINGS_FOR_OUTLINE:
            continue
        key = (len(c), r['id'])
        if key > best_key:
            best_key = key
            best = c
    return best
```

- [ ] **Step 4: Run tests** — Expected PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/sermon_pack.py tools/workbench/tests/test_sermon_pack.py
git commit -m "feat(sermon-pack): conversation mining heuristic"
```

---

## Task 7: Outline-message parser — split into intro / points / conclusion

**Files:**
- Modify: `tools/sermon_pack.py`
- Modify: `tools/workbench/tests/test_sermon_pack.py`

- [ ] **Step 1: Write the failing test**

```python
def test_parse_mined_outline_extracts_sections():
    sp = _load_sermon_pack()
    md = (
        '# Sermon outline\n\n'
        '## INTRODUCTION\n\n'
        'Opening prose paragraph.\n\n'
        'Second intro paragraph.\n\n'
        '## POINT 1: God displayed Jesus as mercy seat\n\n'
        'Body for point 1.\n\n'
        '### Sub-section\n\nMore body.\n\n'
        '## POINT 2: God proved His righteousness\n\n'
        'Body for point 2.\n\n'
        '## CONCLUSION\n\nClosing prose.'
    )
    parsed = sp.parse_mined_outline(md)
    assert parsed['intro'].startswith('Opening prose paragraph.')
    assert 'Second intro paragraph.' in parsed['intro']
    assert len(parsed['points']) == 2
    assert parsed['points'][0]['title'] == 'POINT 1: God displayed Jesus as mercy seat'
    assert 'Body for point 1.' in parsed['points'][0]['body']
    assert '### Sub-section' in parsed['points'][0]['body']
    assert parsed['points'][1]['title'].startswith('POINT 2')
    assert parsed['conclusion'].startswith('Closing prose.')


def test_parse_mined_outline_missing_sections_return_empty_string():
    sp = _load_sermon_pack()
    parsed = sp.parse_mined_outline('# Title\n\nSome prose with no H2 anywhere.')
    assert parsed['intro'] == ''
    assert parsed['points'] == []
    assert parsed['conclusion'] == ''
```

- [ ] **Step 2: Run, verify failure**

- [ ] **Step 3: Implement parser**

```python
_H2_RE = re.compile(r'(?m)^##\s+(.+?)\s*$')


def parse_mined_outline(md: str) -> dict:
    """Split a mined outline message into {intro, points: [{title, body}], conclusion}.

    Sections under `## INTRODUCTION` (or `## INTRO`) → intro.
    Sections under `## POINT N: ...` (or any `## ` not labelled CONCLUSION/INTRO)
    → points list (the title is the H2 text, the body is everything until
    the next H2).
    Sections under `## CONCLUSION` → conclusion.
    All other H2s are treated as points."""
    intro_parts: list[str] = []
    points: list[dict] = []
    conclusion_parts: list[str] = []

    matches = list(_H2_RE.finditer(md))
    if not matches:
        return {'intro': '', 'points': [], 'conclusion': ''}

    for i, m in enumerate(matches):
        title = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[body_start:body_end].strip()
        upper = title.upper()
        if upper in ('INTRODUCTION', 'INTRO'):
            intro_parts.append(body)
        elif upper == 'CONCLUSION':
            conclusion_parts.append(body)
        else:
            points.append({'title': title, 'body': body})

    return {
        'intro': '\n\n'.join(p for p in intro_parts if p),
        'points': points,
        'conclusion': '\n\n'.join(p for p in conclusion_parts if p),
    }
```

- [ ] **Step 4: Run tests** — Expected PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/sermon_pack.py tools/workbench/tests/test_sermon_pack.py
git commit -m "feat(sermon-pack): outline-message parser"
```

---

## Task 8: Field resolver + per-artifact source breakdown

**Files:**
- Modify: `tools/sermon_pack.py`
- Modify: `tools/workbench/tests/test_sermon_pack.py`

- [ ] **Step 1: Write the failing test**

```python
def test_resolve_artifact_fields_uses_decision_when_present():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    _seed_decisions(path, sid, {
        'subject': 'God is righteous.',
        'hp_tht': 'Your God has proven His righteousness.',
        'target_length_min': '28',
    })
    sp = _load_sermon_pack()
    ctx = sp.resolve_artifact_fields(path, sid, artifact='manuscript')
    assert ctx['subject'].value == 'God is righteous.'
    assert ctx['subject'].source == 'decisions'
    assert ctx['take_home_truth'].value.startswith('Your God')
    assert ctx['target_length_min'].value == '28'


def test_resolve_artifact_falls_back_to_mined_for_intro_when_no_structured():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    big = ('# Sermon\n\n## INTRODUCTION\n\nMined intro paragraph.\n\n'
           '## POINT 1: A\n\nBody.\n\n## POINT 2: B\n\nBody.\n\n'
           '## CONCLUSION\n\nMined conclusion.')
    _insert_msg(path, sid, 'assistant', big)
    sp = _load_sermon_pack()
    ctx = sp.resolve_artifact_fields(path, sid, artifact='manuscript')
    assert 'Mined intro paragraph.' in ctx['intro'].value
    assert ctx['intro'].source == 'mined'
    assert ctx['points'].source == 'mined'
    assert ctx['conclusion'].source == 'mined'


def test_resolve_artifact_marks_missing_fields_with_marker():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    sp = _load_sermon_pack()
    ctx = sp.resolve_artifact_fields(path, sid, artifact='manuscript')
    assert ctx['subject'].value == '[missing: subject]'
    assert ctx['subject'].source == 'missing'
    assert ctx['intro'].source == 'missing'


def test_compute_source_breakdown_and_mining_pct():
    sp = _load_sermon_pack()
    ctx = {
        'subject': sp.FieldResult('S', 'decisions'),
        'take_home_truth': sp.FieldResult('T', 'decisions'),
        'intro': sp.FieldResult('I', 'mined'),
        'points': sp.FieldResult('P', 'outline'),
        'conclusion': sp.FieldResult('[missing: conclusion]', 'missing'),
    }
    br = sp.compute_source_breakdown(ctx)
    assert br == {'decisions': 2, 'outline': 1, 'mined': 1, 'missing': 1,
                  'notepad': 0, 'card': 0}
    assert sp.compute_mining_percentage(br) == 0.25  # 1 mined / 4 non-missing
```

- [ ] **Step 2: Run, verify failure**

Expected: AttributeError on `resolve_artifact_fields`.

- [ ] **Step 3: Implement resolver + breakdown helpers**

```python
# Decision-key → field-name mapping used by all three artifacts.
_DECISION_TO_FIELD = {
    'subject': 'subject',
    'theme': 'theme',
    'ep_otm': 'exegetical_point',
    'transition': 'transition',
    'exegetical_outline': 'exegetical_outline',
    'fcf': 'fcf',
    'hp_tht': 'take_home_truth',
    'purpose_statement': 'purpose_statement',
    'target_length_min': 'target_length_min',
}

_ARTIFACT_FIELDS = {
    'manuscript': (
        'subject', 'take_home_truth', 'target_length_min', 'passage_ref',
        'preach_date', 'intro', 'points', 'conclusion',
    ),
    'outline': (
        'subject', 'take_home_truth', 'target_length_min', 'passage_ref',
        'preach_date', 'intro', 'points', 'conclusion',
    ),
    'handout': (
        'subject', 'take_home_truth', 'passage_ref', 'preach_date',
        'points', 'conclusion',
    ),
}


def _field_from_decisions(decisions: dict, field_name: str) -> FieldResult | None:
    for dec_key, mapped in _DECISION_TO_FIELD.items():
        if mapped == field_name and dec_key in decisions:
            return FieldResult(value=decisions[dec_key], source='decisions')
    return None


def _field_from_session(session_row: dict, field_name: str) -> FieldResult | None:
    if field_name == 'passage_ref' and session_row.get('passage_ref'):
        return FieldResult(value=session_row['passage_ref'], source='decisions')
    if field_name == 'preach_date' and session_row.get('preach_date'):
        return FieldResult(value=session_row['preach_date'], source='decisions')
    return None


def resolve_artifact_fields(db_path: str, session_id: int, *,
                            artifact: str) -> dict[str, FieldResult]:
    """Resolve every field for the named artifact, returning {name: FieldResult}."""
    if artifact not in _ARTIFACT_FIELDS:
        raise ValueError(f'unknown artifact: {artifact!r}')

    session_row = load_session_row(db_path, session_id) or {}
    decisions = load_decisions(db_path, session_id)
    outline_points = load_outline_points(db_path, session_id)
    mined = mine_outline_message(db_path, session_id)
    parsed = parse_mined_outline(mined) if mined else {'intro': '', 'points': [], 'conclusion': ''}

    ctx: dict[str, FieldResult] = {}
    for name in _ARTIFACT_FIELDS[artifact]:
        # session columns (passage_ref / preach_date) are treated as 'decisions'
        # in the source taxonomy because they are committed structured fields,
        # not mined text.
        sess = _field_from_session(session_row, name)
        if sess:
            ctx[name] = sess
            continue
        dec = _field_from_decisions(decisions, name)
        if dec:
            ctx[name] = dec
            continue
        if name == 'points':
            if outline_points:
                ctx[name] = FieldResult(value=_format_points(outline_points), source='outline')
            elif parsed['points']:
                ctx[name] = FieldResult(value=_format_mined_points(parsed['points']),
                                         source='mined')
            else:
                ctx[name] = missing_field(name)
            continue
        if name == 'intro':
            if parsed['intro']:
                ctx[name] = FieldResult(value=parsed['intro'], source='mined')
            else:
                ctx[name] = missing_field(name)
            continue
        if name == 'conclusion':
            if parsed['conclusion']:
                ctx[name] = FieldResult(value=parsed['conclusion'], source='mined')
            else:
                ctx[name] = missing_field(name)
            continue
        ctx[name] = missing_field(name)
    return ctx


def _format_points(points: list[dict]) -> str:
    """Format outline_nodes points as a manuscript/outline body string."""
    parts = []
    for i, p in enumerate(points, start=1):
        title = p['title']
        ref = f' ({p["verse_ref"]})' if p.get('verse_ref') else ''
        parts.append(f'## POINT {i}: {title}{ref}')
        for s in p.get('subs', []):
            sub_ref = f' ({s["verse_ref"]})' if s.get('verse_ref') else ''
            parts.append(f'* {s["content"]}{sub_ref}')
        for x in p.get('cross_refs', []):
            ref = x.get('verse_ref') or x.get('content', '')
            parts.append(f'* **Scripture:** {ref}')
        for n in p.get('notes', []):
            parts.append(f'* {n["content"]}')
        for il in p.get('illustrations', []):
            parts.append(f'> {il["content"]}')
        parts.append('')
    return '\n'.join(parts).rstrip()


def _format_mined_points(points: list[dict]) -> str:
    parts = []
    for p in points:
        parts.append(f'## {p["title"]}')
        parts.append('')
        parts.append(p['body'])
        parts.append('')
    return '\n'.join(parts).rstrip()


def compute_source_breakdown(ctx: dict[str, FieldResult]) -> dict[str, int]:
    br = {s: 0 for s in SOURCES}
    for fr in ctx.values():
        br[fr.source] = br.get(fr.source, 0) + 1
    return br


def compute_mining_percentage(breakdown: dict[str, int]) -> float:
    """Mined / non-missing total. Returns 0.0 if no fields resolved at all."""
    denom = sum(v for k, v in breakdown.items() if k != 'missing')
    if denom == 0:
        return 0.0
    return round(breakdown.get('mined', 0) / denom, 4)
```

- [ ] **Step 4: Run tests** — Expected PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/sermon_pack.py tools/workbench/tests/test_sermon_pack.py
git commit -m "feat(sermon-pack): field resolver + source-breakdown math"
```

---

## Task 9: Manuscript template + render

**Files:**
- Create: `tools/sermon_pack_templates/manuscript.md.tmpl`
- Modify: `tools/sermon_pack.py`
- Modify: `tools/workbench/tests/test_sermon_pack.py`

- [ ] **Step 1: Write the failing test**

```python
def test_render_manuscript_substitutes_fields_and_contains_no_em_dash():
    sp = _load_sermon_pack()
    ctx = {
        'subject': sp.FieldResult('God is righteous.', 'decisions'),
        'take_home_truth': sp.FieldResult('Your God has proven His righteousness.',
                                          'decisions'),
        'target_length_min': sp.FieldResult('28', 'decisions'),
        'passage_ref': sp.FieldResult('Romans 3:25-26', 'decisions'),
        'preach_date': sp.FieldResult('2026-05-24', 'decisions'),
        'intro': sp.FieldResult('Opening prose paragraph.', 'mined'),
        'points': sp.FieldResult('## POINT 1: A\n\nBody one.', 'mined'),
        'conclusion': sp.FieldResult('Closing prose.', 'mined'),
    }
    out = sp.render('manuscript', ctx)
    assert 'Romans 3:25-26' in out
    assert 'Your God has proven His righteousness.' in out
    assert 'Opening prose paragraph.' in out
    assert '## POINT 1: A' in out
    assert '—' not in out  # em-dash
    assert '--' not in out


def test_render_manuscript_with_missing_fields_emits_markers():
    sp = _load_sermon_pack()
    ctx = {k: sp.missing_field(k) for k in
           ('subject', 'take_home_truth', 'target_length_min', 'passage_ref',
            'preach_date', 'intro', 'points', 'conclusion')}
    out = sp.render('manuscript', ctx)
    assert '[missing: subject]' in out
    assert '[missing: take_home_truth]' in out
    assert '[missing: intro]' in out
    assert '—' not in out


def test_render_manuscript_is_deterministic_byte_identical():
    sp = _load_sermon_pack()
    ctx = {
        'subject': sp.FieldResult('S', 'decisions'),
        'take_home_truth': sp.FieldResult('T', 'decisions'),
        'target_length_min': sp.FieldResult('28', 'decisions'),
        'passage_ref': sp.FieldResult('Rom 3:25-26', 'decisions'),
        'preach_date': sp.FieldResult('2026-05-24', 'decisions'),
        'intro': sp.FieldResult('I', 'mined'),
        'points': sp.FieldResult('## POINT 1\n\nbody', 'mined'),
        'conclusion': sp.FieldResult('C', 'mined'),
    }
    a = sp.render('manuscript', ctx)
    b = sp.render('manuscript', ctx)
    assert a == b
```

- [ ] **Step 2: Run, verify failure**

Expected: AttributeError or FileNotFoundError on the template file.

- [ ] **Step 3: Create template + render function**

Create `tools/sermon_pack_templates/manuscript.md.tmpl` (NO em-dashes, NO `--`):

```
# {passage_ref}: Full Sermon Manuscript

**Passage:** {passage_ref}.
**Preach date:** {preach_date}.
**Subject:** {subject}
**Take Home Truth:** {take_home_truth}
**Target length:** {target_length_min} minutes.

---

## INTRODUCTION

{intro}

**Take Home Truth:** {take_home_truth}

---

{points}

---

## CONCLUSION

{conclusion}

**Take Home Truth.** {take_home_truth}
```

Add to `tools/sermon_pack.py`:

```python
_TEMPLATE_DIR = Path(__file__).parent / 'sermon_pack_templates'


def _load_template(artifact: str) -> str:
    path = _TEMPLATE_DIR / f'{artifact}.md.tmpl'
    return path.read_text(encoding='utf-8')


def render(artifact: str, ctx: dict[str, FieldResult]) -> str:
    """Render a markdown artifact from resolved fields."""
    tmpl = _load_template(artifact)
    values = {k: v.value for k, v in ctx.items()}
    return tmpl.format(**values)
```

- [ ] **Step 4: Run tests** — Expected PASS. Also verify the template file itself has no em-dashes:

```bash
grep -n "—\|--" tools/sermon_pack_templates/manuscript.md.tmpl
```
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add tools/sermon_pack_templates/manuscript.md.tmpl tools/sermon_pack.py tools/workbench/tests/test_sermon_pack.py
git commit -m "feat(sermon-pack): manuscript template + render"
```

---

## Task 10: Preaching-outline template + render

**Files:**
- Create: `tools/sermon_pack_templates/outline.md.tmpl`
- Modify: `tools/workbench/tests/test_sermon_pack.py`

- [ ] **Step 1: Write the failing test**

```python
def test_render_outline_uses_bullet_style_and_no_em_dash():
    sp = _load_sermon_pack()
    ctx = {
        'subject': sp.FieldResult('God is righteous.', 'decisions'),
        'take_home_truth': sp.FieldResult('THT here.', 'decisions'),
        'target_length_min': sp.FieldResult('28', 'decisions'),
        'passage_ref': sp.FieldResult('Romans 3:25-26', 'decisions'),
        'preach_date': sp.FieldResult('2026-05-24', 'decisions'),
        'intro': sp.FieldResult('FCF question. Bridge. THT.', 'mined'),
        'points': sp.FieldResult('## POINT 1: A\n* sub one\n* sub two', 'outline'),
        'conclusion': sp.FieldResult('EP recap. THT. Silence.', 'mined'),
    }
    out = sp.render('outline', ctx)
    assert 'Preaching Outline' in out
    assert 'God is righteous.' in out
    assert '## POINT 1: A' in out
    assert '* sub one' in out
    assert '—' not in out


def test_render_outline_with_missing_target_length_emits_marker():
    sp = _load_sermon_pack()
    ctx = {
        'subject': sp.FieldResult('S', 'decisions'),
        'take_home_truth': sp.FieldResult('T', 'decisions'),
        'target_length_min': sp.missing_field('target_length_min'),
        'passage_ref': sp.FieldResult('Rom 3:25-26', 'decisions'),
        'preach_date': sp.FieldResult('', 'decisions'),
        'intro': sp.missing_field('intro'),
        'points': sp.missing_field('points'),
        'conclusion': sp.missing_field('conclusion'),
    }
    out = sp.render('outline', ctx)
    assert '[missing: target_length_min]' in out
    assert '[missing: intro]' in out
    assert '—' not in out
```

- [ ] **Step 2: Run, verify failure**

Expected: FileNotFoundError.

- [ ] **Step 3: Create template**

Create `tools/sermon_pack_templates/outline.md.tmpl`:

```
# {passage_ref}: Preaching Outline

**Subject:** {subject}
**Preach date:** {preach_date}. Target: {target_length_min} min.

**Take Home Truth:**
*{take_home_truth}*

---

## INTRO

{intro}

---

{points}

---

## CONCLUSION

{conclusion}

**THT:** *{take_home_truth}*
```

- [ ] **Step 4: Run tests** — Expected PASS.

Verify no em-dashes in template:

```bash
grep -n "—\|--" tools/sermon_pack_templates/outline.md.tmpl
```
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add tools/sermon_pack_templates/outline.md.tmpl tools/workbench/tests/test_sermon_pack.py
git commit -m "feat(sermon-pack): preaching-outline template"
```

---

## Task 11: Handout template + render (fill-in-blank + page break)

**Files:**
- Create: `tools/sermon_pack_templates/handout.md.tmpl`
- Modify: `tools/sermon_pack.py` (handout-specific point-format helper)
- Modify: `tools/workbench/tests/test_sermon_pack.py`

- [ ] **Step 1: Write the failing test**

```python
def test_render_handout_inserts_fill_in_blanks_and_page_break():
    sp = _load_sermon_pack()
    points_md = ('## Point 1: God ________ Jesus as your ________ ________\n'
                 '* The tabernacle was a royal throne room.\n'
                 '\n'
                 '## Point 2: God ________ His righteousness in dealing with ________ ________\n'
                 '* God dealt with their sins by His forbearance.')
    ctx = {
        'subject': sp.FieldResult('God\'s Righteousness, Proven', 'decisions'),
        'take_home_truth': sp.FieldResult('Your God has proven His righteousness.',
                                          'decisions'),
        'passage_ref': sp.FieldResult('Romans 3:25-26', 'decisions'),
        'preach_date': sp.FieldResult('2026-05-24', 'decisions'),
        'points': sp.FieldResult(points_md, 'outline'),
        'conclusion': sp.FieldResult('God is righteous.', 'mined'),
    }
    out = sp.render('handout', ctx)
    assert 'Romans 3:25-26' in out
    assert 'God ________' in out  # 4+ underscores survives substitution
    assert '<!--PAGEBREAK-->' in out
    assert '—' not in out
    assert '--' not in out.replace('<!--PAGEBREAK-->', '')


def test_handout_template_filename_triggers_tight_print_profile():
    # Smoke: md_to_html's _pick_css selects the handout profile when the
    # stem contains "handout". This test pins the filename convention.
    from md_to_html import _pick_css, _HANDOUT_CSS
    assert _pick_css('romans-3-25-26-congregation-handout') == _HANDOUT_CSS
```

- [ ] **Step 2: Run, verify failure**

Expected: FileNotFoundError.

- [ ] **Step 3: Create handout template**

Create `tools/sermon_pack_templates/handout.md.tmpl`:

```
# {subject}

**{passage_ref}.** Sunday, {preach_date}.

---

## Take Home Truth

**{take_home_truth}**

---

{points}

<!--PAGEBREAK-->

## Conclusion

{conclusion}

**{take_home_truth}**
```

(The fill-in-blank `________` runs come straight from the `points` field if the source produced them. The generator does not synthesize blanks; the resolver passes through what was authored. This is intentional: the handout's fill-in-blank shape is the agent's responsibility upstream. When mining a recent outline, blanks are absent and Bryan adds them in Pages. This will be revisited in the follow-on upstream-wiring spec.)

Verify no em-dashes:

```bash
grep -n "—" tools/sermon_pack_templates/handout.md.tmpl
```
Expected: no output. (`<!--PAGEBREAK-->` is the only legal `--` sequence; the second assertion in the test guards that.)

- [ ] **Step 4: Run tests** — Expected PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/sermon_pack_templates/handout.md.tmpl tools/workbench/tests/test_sermon_pack.py
git commit -m "feat(sermon-pack): handout template + fill-in-blank passthrough"
```

---

## Task 12: `generate()` orchestrator + metrics logging

**Files:**
- Modify: `tools/sermon_pack.py`
- Modify: `tools/workbench/tests/test_sermon_pack.py`

- [ ] **Step 1: Write the failing test**

```python
def test_generate_writes_three_files_and_logs_metrics():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    _seed_decisions(path, sid, {
        'subject': 'God is righteous.',
        'hp_tht': 'THT here.',
        'target_length_min': '28',
    })
    _insert_msg(path, sid, 'assistant',
                '# X\n\n## INTRODUCTION\n\nIntro.\n\n'
                '## POINT 1: A\n\nBody.\n\n## POINT 2: B\n\nBody.\n\n'
                '## CONCLUSION\n\nEnd.')
    sp = _load_sermon_pack()
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        result = sp.generate(sid, db_path=path, out_dir=td)
        assert set(result) == {'manuscript', 'outline', 'handout'}
        for artifact in result:
            p = Path(result[artifact]['path'])
            assert p.exists()
            assert p.read_text(encoding='utf-8') == result[artifact]['content']
            assert '—' not in result[artifact]['content']
            assert isinstance(result[artifact]['mining_percentage'], float)
            assert isinstance(result[artifact]['source_breakdown'], dict)
    # metrics row was logged
    metrics = cdb.get_latest_sermon_pack_metrics(sid)
    assert set(metrics) == {'manuscript', 'outline', 'handout'}


def test_generate_is_byte_identical_on_consecutive_runs():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    _seed_decisions(path, sid, {'subject': 'X', 'hp_tht': 'Y',
                                'target_length_min': '28'})
    sp = _load_sermon_pack()
    import tempfile
    with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
        a = sp.generate(sid, db_path=path, out_dir=td1)
        b = sp.generate(sid, db_path=path, out_dir=td2)
        for art in ('manuscript', 'outline', 'handout'):
            assert a[art]['content'] == b[art]['content']


def test_generate_subset_of_artifacts():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    sp = _load_sermon_pack()
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        result = sp.generate(sid, db_path=path, out_dir=td, artifacts=('handout',))
        assert set(result) == {'handout'}


def test_generate_raises_for_missing_session():
    cdb, path = _fresh_db()
    sp = _load_sermon_pack()
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        try:
            sp.generate(99999, db_path=path, out_dir=td)
        except sp.SermonPackError as e:
            assert '99999' in str(e)
        else:
            assert False, 'expected SermonPackError'
```

- [ ] **Step 2: Run, verify failure**

Expected: AttributeError on `generate`.

- [ ] **Step 3: Implement orchestrator**

```python
class SermonPackError(RuntimeError):
    """Raised when generation cannot proceed (e.g. session id is unknown)."""


def _safe_filename(passage: str, artifact: str) -> str:
    """Mirror the existing _safe_export_filename convention."""
    slug = re.sub(r'[^A-Za-z0-9]+', '-', passage).strip('-').lower() or 'sermon'
    suffix = {
        'manuscript': 'manuscript',
        'outline': 'preaching-outline',
        'handout': 'congregation-handout',
    }[artifact]
    return f'{slug}-{suffix}.md'


def generate(session_id: int, *, db_path: str, out_dir: str | Path,
             artifacts: tuple[str, ...] = ('manuscript', 'outline', 'handout'),
             companion_db=None) -> dict:
    """Generate the requested artifacts and write each as a .md file.

    Returns {artifact: {path, content, mining_percentage, source_breakdown}}.
    Logs one sermon_pack_metrics row per artifact when `companion_db` is
    supplied (the Flask routes pass app.companion_db; the CLI builds one
    from db_path)."""
    session_row = load_session_row(db_path, session_id)
    if session_row is None:
        raise SermonPackError(f'session {session_id} not found in {db_path}')
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    passage = session_row.get('passage_ref') or f'session-{session_id}'

    # Build companion_db handle for metrics logging if caller didn't pass one.
    if companion_db is None:
        try:
            sys.path.insert(0, str(Path(__file__).parent / 'workbench'))
            from companion_db import CompanionDB  # noqa: E402
            companion_db = CompanionDB(db_path)
        except ImportError:
            companion_db = None  # tests with no workbench package still work

    result: dict[str, dict] = {}
    for art in artifacts:
        ctx = resolve_artifact_fields(db_path, session_id, artifact=art)
        content = render(art, ctx)
        breakdown = compute_source_breakdown(ctx)
        mining_pct = compute_mining_percentage(breakdown)
        path = out_dir / _safe_filename(passage, art)
        path.write_text(content, encoding='utf-8')
        if companion_db is not None:
            companion_db.save_sermon_pack_metric(
                session_id=session_id, artifact=art,
                source_breakdown=breakdown,
                mining_percentage=mining_pct,
            )
        result[art] = {
            'path': str(path),
            'content': content,
            'mining_percentage': mining_pct,
            'source_breakdown': breakdown,
        }
    return result
```

- [ ] **Step 4: Run tests** — Expected PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/sermon_pack.py tools/workbench/tests/test_sermon_pack.py
git commit -m "feat(sermon-pack): generate() orchestrator + metrics logging"
```

---

## Task 13: CLI (positional session_id, --out, --artifact, --report)

**Files:**
- Modify: `tools/sermon_pack.py`
- Modify: `tools/workbench/tests/test_sermon_pack.py`

- [ ] **Step 1: Write the failing test**

```python
def test_cli_generates_artifacts_to_out_dir(tmp_path):
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    _seed_decisions(path, sid, {'subject': 'S', 'hp_tht': 'T',
                                'target_length_min': '28'})
    sp = _load_sermon_pack()
    rc = sp.main([str(sid), '--db', path, '--out', str(tmp_path)])
    assert rc == 0
    md_files = sorted(p.name for p in tmp_path.glob('*.md'))
    assert any('manuscript' in n for n in md_files)
    assert any('preaching-outline' in n for n in md_files)
    assert any('congregation-handout' in n for n in md_files)


def test_cli_report_prints_recent_mining_percentages(capsys):
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    cdb.save_sermon_pack_metric(
        session_id=sid, artifact='manuscript',
        source_breakdown={'decisions': 2, 'mined': 1},
        mining_percentage=0.333,
    )
    sp = _load_sermon_pack()
    rc = sp.main(['--report', '--db', path])
    assert rc == 0
    out = capsys.readouterr().out
    assert str(sid) in out
    assert 'manuscript' in out
    assert '0.333' in out or '33.3' in out
```

- [ ] **Step 2: Run, verify failure**

Expected: AttributeError on `main`.

- [ ] **Step 3: Implement CLI**

```python
def _print_report(db_path: str) -> int:
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT session_id, artifact, mining_percentage, generated_at "
            "FROM sermon_pack_metrics ORDER BY generated_at DESC LIMIT 50"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        print('no sermon_pack_metrics rows yet')
        return 0
    print(f'{"session":>7}  {"artifact":<11}  {"mining%":>7}  generated_at')
    for r in rows:
        print(f'{r["session_id"]:>7}  {r["artifact"]:<11}  '
              f'{r["mining_percentage"]:>6.3f}  {r["generated_at"]}')
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description='Generate sermon-pack deliverables from a finished study session.',
    )
    p.add_argument('session_id', nargs='?', type=int,
                   help='session id (omit when using --report)')
    p.add_argument('--db', default=None,
                   help='path to companion.db (defaults to tools/workbench/companion.db)')
    p.add_argument('--out', default='.',
                   help='output directory for generated .md files')
    p.add_argument('--artifact', action='append',
                   choices=('manuscript', 'outline', 'handout'),
                   help='emit only the named artifact (repeatable). Default: all three.')
    p.add_argument('--report', action='store_true',
                   help='print recent sermon_pack_metrics rows and exit')
    args = p.parse_args(argv)

    db_path = args.db or str(Path(__file__).parent / 'workbench' / 'companion.db')

    if args.report:
        return _print_report(db_path)

    if args.session_id is None:
        p.error('session_id is required unless --report is given')

    artifacts = tuple(args.artifact) if args.artifact else ('manuscript', 'outline', 'handout')
    result = generate(args.session_id, db_path=db_path, out_dir=args.out,
                      artifacts=artifacts)
    for art, info in result.items():
        print(f'{art}: {info["path"]}  mining={info["mining_percentage"]:.3f}  '
              f'breakdown={info["source_breakdown"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 4: Run tests** — Expected PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/sermon_pack.py tools/workbench/tests/test_sermon_pack.py
git commit -m "feat(sermon-pack): CLI with --report"
```

---

## Task 14: `md_to_html_string` refactor

**Files:**
- Modify: `tools/md_to_html.py`
- Test: `tools/workbench/tests/test_sermon_pack.py`

- [ ] **Step 1: Write the failing test**

```python
def test_md_to_html_string_picks_handout_profile_for_handout_stem():
    from md_to_html import md_to_html_string, _HANDOUT_CSS
    md = '# Title\n\nSome prose.'
    html = md_to_html_string(md, stem='romans-3-25-26-congregation-handout')
    assert '<!DOCTYPE html>' in html
    assert '<title>Title</title>' in html
    assert _HANDOUT_CSS.strip() in html


def test_md_to_html_string_default_profile():
    from md_to_html import md_to_html_string, _BASE_CSS
    html = md_to_html_string('# A', stem='manuscript')
    assert _BASE_CSS.strip() in html
```

- [ ] **Step 2: Run, verify failure**

Expected: ImportError on `md_to_html_string`.

- [ ] **Step 3: Refactor**

In `tools/md_to_html.py`, add `md_to_html_string()` and refactor `convert()` to use it:

```python
def md_to_html_string(md: str, stem: str) -> str:
    """Convert an in-memory markdown string to a standalone HTML document.
    Picks the tight handout profile when `stem` contains the handout
    keyword (same rule as file-based convert)."""
    title_match = re.search(r"^#\s+(.+)$", md, re.M)
    title = title_match.group(1).strip() if title_match else stem
    body = md_to_html_body(md)
    css = _pick_css(stem)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>{css}</style>
</head>
<body>
{body}
</body>
</html>
"""


def convert(md_path: Path) -> Path:
    md = md_path.read_text(encoding="utf-8")
    html_doc = md_to_html_string(md, md_path.stem)
    out = md_path.with_suffix(".html")
    out.write_text(html_doc, encoding="utf-8")
    return out
```

- [ ] **Step 4: Run tests** — Expected PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/md_to_html.py tools/workbench/tests/test_sermon_pack.py
git commit -m "refactor(md_to_html): expose md_to_html_string for in-memory render"
```

---

## Task 15: Three Flask pack routes (md + html)

**Files:**
- Modify: `tools/workbench/app.py` (add routes near the existing outline-export route at line 1369)
- Create: `tools/workbench/tests/test_sermon_pack_routes.py`

- [ ] **Step 1: Write the failing test**

```python
# tools/workbench/tests/test_sermon_pack_routes.py
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest


@pytest.fixture
def client():
    import app as app_module
    from conftest import init_all_app_globals
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    init_all_app_globals(tmp.name)
    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as c:
        yield c
    os.unlink(tmp.name)


def _new_session(client, passage='Romans 3:25-26'):
    r = client.post('/study/session/new', data={'passage': passage})
    return int(r.headers['Location'].rstrip('/').split('/')[-1])


def test_pack_manuscript_md_returns_attachment(client):
    sid = _new_session(client)
    r = client.get(f'/study/session/{sid}/pack/manuscript.md')
    assert r.status_code == 200
    assert r.content_type.startswith('text/markdown') or r.content_type.startswith('text/plain')
    cd = r.headers['Content-Disposition']
    assert 'attachment' in cd
    assert 'manuscript' in cd
    assert b'\xe2\x80\x94' not in r.data  # no em-dash bytes


def test_pack_outline_md(client):
    sid = _new_session(client)
    r = client.get(f'/study/session/{sid}/pack/outline.md')
    assert r.status_code == 200
    assert b'Preaching Outline' in r.data


def test_pack_handout_md(client):
    sid = _new_session(client)
    r = client.get(f'/study/session/{sid}/pack/handout.md')
    assert r.status_code == 200
    assert b'<!--PAGEBREAK-->' in r.data


def test_pack_handout_html_uses_tight_print_profile(client):
    sid = _new_session(client)
    r = client.get(f'/study/session/{sid}/pack/handout.md?format=html')
    assert r.status_code == 200
    assert r.content_type.startswith('text/html')
    assert b'<!DOCTYPE html>' in r.data
    # tight-print profile sets 10.5pt body font; default is 12pt
    assert b'10.5pt' in r.data


def test_pack_bad_artifact_returns_400(client):
    sid = _new_session(client)
    r = client.get(f'/study/session/{sid}/pack/nonsense.md')
    assert r.status_code == 404 or r.status_code == 400


def test_pack_missing_session_returns_404(client):
    r = client.get('/study/session/999999/pack/manuscript.md')
    assert r.status_code == 404
```

- [ ] **Step 2: Run, verify failure**

```bash
cd tools/workbench && python3 -m pytest tests/test_sermon_pack_routes.py -v
```
Expected: FAIL (404s for all).

- [ ] **Step 3: Add routes**

Above the existing `@app.route("/study/session/<int:session_id>/outline/export.<fmt>")` (line 1369), add:

```python
@app.route("/study/session/<int:session_id>/pack/<artifact>.md")
def study_pack_artifact(session_id, artifact):
    """Download one sermon-pack artifact (manuscript|outline|handout) as
    markdown, or render to HTML when ?format=html is set.

    See docs/superpowers/specs/2026-05-23-sermon-pack-deliverables-design.md."""
    session_row, err = require_session(companion_db, session_id)
    if err:
        return err
    if artifact not in ('manuscript', 'outline', 'handout'):
        return jsonify({'error': 'artifact must be manuscript, outline, or handout'}), 400

    # The generator imports tools/sermon_pack.py from the parent of tools/workbench/.
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).parent.parent))
    import sermon_pack  # type: ignore

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        result = sermon_pack.generate(
            session_id, db_path=companion_db.db_path,
            out_dir=td, artifacts=(artifact,),
            companion_db=companion_db,
        )
    info = result[artifact]
    fmt = (request.args.get('format') or 'md').lower()
    passage = session_row.get('passage_ref') or f'session-{session_id}'
    slug = re.sub(r'[^A-Za-z0-9]+', '-', passage).strip('-').lower() or 'sermon'
    suffix = {'manuscript': 'manuscript', 'outline': 'preaching-outline',
              'handout': 'congregation-handout'}[artifact]
    base = f'{slug}-{suffix}'

    if fmt == 'html':
        import sys as _sys2
        _sys2.path.insert(0, str(_Path(__file__).parent.parent))
        from md_to_html import md_to_html_string  # type: ignore
        html_doc = md_to_html_string(info['content'], stem=base)
        return Response(
            html_doc, mimetype='text/html',
            headers={'Content-Disposition': f'attachment; filename="{base}.html"'},
        )

    return Response(
        info['content'], mimetype='text/markdown; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{base}.md"'},
    )
```

- [ ] **Step 4: Run tests** — Expected PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/app.py tools/workbench/tests/test_sermon_pack_routes.py
git commit -m "feat(sermon-pack): three pack routes (md + ?format=html)"
```

---

## Task 16: Metrics endpoint for source-breakdown pill

**Files:**
- Modify: `tools/workbench/app.py`
- Modify: `tools/workbench/tests/test_sermon_pack_routes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_pack_metrics_returns_latest_per_artifact(client):
    import app as app_module
    sid = _new_session(client)
    app_module.companion_db.save_sermon_pack_metric(
        session_id=sid, artifact='manuscript',
        source_breakdown={'decisions': 2, 'mined': 1, 'missing': 0,
                          'outline': 0, 'notepad': 0, 'card': 0},
        mining_percentage=0.333,
    )
    app_module.companion_db.save_sermon_pack_metric(
        session_id=sid, artifact='outline',
        source_breakdown={'decisions': 4, 'mined': 0, 'missing': 0,
                          'outline': 0, 'notepad': 0, 'card': 0},
        mining_percentage=0.0,
    )
    r = client.get(f'/study/session/{sid}/pack/metrics')
    assert r.status_code == 200
    data = r.get_json()
    assert data['manuscript']['mining_percentage'] == 0.333
    assert data['outline']['mining_percentage'] == 0.0
    assert data['manuscript']['source_breakdown']['decisions'] == 2


def test_pack_metrics_empty_when_never_generated(client):
    sid = _new_session(client)
    r = client.get(f'/study/session/{sid}/pack/metrics')
    assert r.status_code == 200
    assert r.get_json() == {}
```

- [ ] **Step 2: Run, verify failure**

- [ ] **Step 3: Add endpoint**

Below the pack route from Task 15, add:

```python
@app.route("/study/session/<int:session_id>/pack/metrics")
def study_pack_metrics(session_id):
    """Latest per-artifact source-breakdown metrics for the sidebar pill.

    Returns {} when no generations recorded yet."""
    _, err = require_session(companion_db, session_id)
    if err:
        return err
    rows = companion_db.get_latest_sermon_pack_metrics(session_id)
    out = {}
    for artifact, row in rows.items():
        out[artifact] = {
            'mining_percentage': row['mining_percentage'],
            'generated_at': row['generated_at'],
            'source_breakdown': json.loads(row['source_breakdown_json']),
        }
    return jsonify(out)
```

(Verify `json` is already imported at the top of app.py — yes per the existing imports.)

- [ ] **Step 4: Run tests** — Expected PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/app.py tools/workbench/tests/test_sermon_pack_routes.py
git commit -m "feat(sermon-pack): metrics endpoint for source-breakdown pill"
```

---

## Task 17: UI restructure — two groups + pill placeholder

**Files:**
- Modify: `tools/workbench/templates/study_session.html` (line 138-141)
- Modify: `tools/workbench/static/study.css` (around line 1330)
- Modify: `tools/workbench/tests/test_study_routes.py` (line 98-99 assertion)

- [ ] **Step 1: Write the failing test**

In `test_study_routes.py`, add a new test:

```python
def test_study_session_view_has_sermon_pack_block(client):
    session_id, _ = _make_study_session(client)
    r = client.get(f'/study/session/{session_id}')
    assert r.status_code == 200
    # Two clearly-labeled groups
    assert b'SERMON PACK' in r.data
    assert b'OUTLINE TREE ONLY' in r.data
    # Six pack download links
    assert b'/pack/manuscript.md' in r.data
    assert b'/pack/manuscript.md?format=html' in r.data
    assert b'/pack/outline.md' in r.data
    assert b'/pack/outline.md?format=html' in r.data
    assert b'/pack/handout.md' in r.data
    assert b'/pack/handout.md?format=html' in r.data
    # Existing outline-tree-only links still present (relabeled)
    assert b'/outline/export.pdf' in r.data
    assert b'/outline/export.docx' in r.data
    # Pill placeholder
    assert b'sermon-pack-pill' in r.data
```

- [ ] **Step 2: Run, verify failure**

Expected: AssertionError missing SERMON PACK markup.

- [ ] **Step 3: Update template + CSS**

Replace lines 138-141 of `tools/workbench/templates/study_session.html`:

```html
                    <div class="sermon-pack-actions" aria-label="Sermon-pack deliverables">
                        <div class="pack-group">
                            <div class="pack-group-label">SERMON PACK</div>
                            <div class="pack-row">
                                <span class="pack-row-label">Manuscript</span>
                                <a href="/study/session/{{ session.id }}/pack/manuscript.md" class="outline-export-link">.md</a>
                                <a href="/study/session/{{ session.id }}/pack/manuscript.md?format=html" class="outline-export-link">.html</a>
                            </div>
                            <div class="pack-row">
                                <span class="pack-row-label">Outline</span>
                                <a href="/study/session/{{ session.id }}/pack/outline.md" class="outline-export-link">.md</a>
                                <a href="/study/session/{{ session.id }}/pack/outline.md?format=html" class="outline-export-link">.html</a>
                            </div>
                            <div class="pack-row">
                                <span class="pack-row-label">Handout</span>
                                <a href="/study/session/{{ session.id }}/pack/handout.md" class="outline-export-link">.md</a>
                                <a href="/study/session/{{ session.id }}/pack/handout.md?format=html" class="outline-export-link">.html</a>
                            </div>
                            <div id="sermon-pack-pill" class="sermon-pack-pill" title="Source breakdown of last build">
                                <span class="pill-label">Source breakdown</span>
                                <span class="pill-value" data-empty>not yet built</span>
                            </div>
                        </div>
                        <div class="pack-group">
                            <div class="pack-group-label">OUTLINE TREE ONLY</div>
                            <div class="outline-export-actions" aria-label="Export preaching outline">
                                <a href="/study/session/{{ session.id }}/outline/export.pdf" class="outline-export-link">PDF</a>
                                <a href="/study/session/{{ session.id }}/outline/export.docx" class="outline-export-link">Word</a>
                            </div>
                        </div>
                    </div>
```

Append to `tools/workbench/static/study.css` (after the existing `.outline-export-link:hover` block around line 1357):

```css
.sermon-pack-actions {
    display: flex;
    flex-direction: column;
    gap: 10px;
    width: 100%;
}

.pack-group {
    display: flex;
    flex-direction: column;
    gap: 6px;
}

.pack-group-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--text-dim);
}

.pack-row {
    display: flex;
    gap: 6px;
    align-items: center;
}

.pack-row-label {
    flex: 1;
    font-size: 12px;
    color: var(--text-muted);
}

.sermon-pack-pill {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    padding: 2px 8px;
    border-radius: 10px;
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    font-size: 11px;
    color: var(--text-muted);
}

.sermon-pack-pill .pill-label {
    font-weight: 600;
}

.sermon-pack-pill .pill-value[data-empty] {
    font-style: italic;
}
```

- [ ] **Step 4: Run tests**

```bash
cd tools/workbench && python3 -m pytest tests/test_study_routes.py::test_study_session_view_has_sermon_pack_block -v
```
Expected: PASS.

Also re-run the existing `test_study_session_view` (the old assertions on `/outline/export.pdf` should still pass since those links remain).

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/templates/study_session.html tools/workbench/static/study.css tools/workbench/tests/test_study_routes.py
git commit -m "feat(sermon-pack): sidebar UI - two groups + pill placeholder"
```

---

## Task 18: JS to populate the source-breakdown pill on load

**Files:**
- Modify: `tools/workbench/static/study.js`
- Modify: `tools/workbench/templates/study_session.html` (bump cache-buster `study.js?v=N`)
- Test: a small route-side integration test for the JS-served metric (the JS itself is verified manually in browser per `feedback_ui_verify_before_done`)

- [ ] **Step 1: Locate the cache-buster**

In `tools/workbench/templates/study_session.html:171`:
```html
<script src="/static/study.js?v=12"></script>
```
Bump to `?v=13` once JS changes land.

- [ ] **Step 2: Add JS to study.js**

Append to `tools/workbench/static/study.js`:

```javascript
// Sermon-pack source-breakdown pill ────────────────────────────────────
// Fetches the latest per-artifact metrics for this session and renders
// a one-line summary in the sidebar pill. Quiet by design: when no
// generations have happened yet, the pill shows "not yet built".
(function () {
    const pill = document.getElementById('sermon-pack-pill');
    if (!pill || !window.studySessionId) return;
    const valueEl = pill.querySelector('.pill-value');

    async function refresh() {
        try {
            const r = await fetch('/study/session/' + window.studySessionId + '/pack/metrics');
            if (!r.ok) return;
            const data = await r.json();
            if (!data || Object.keys(data).length === 0) {
                valueEl.textContent = 'not yet built';
                valueEl.setAttribute('data-empty', '');
                return;
            }
            const parts = [];
            for (const art of ['manuscript', 'outline', 'handout']) {
                if (!data[art]) continue;
                const pct = Math.round(data[art].mining_percentage * 100);
                parts.push(art[0].toUpperCase() + ': ' + pct + '%');
            }
            valueEl.textContent = parts.join('  ');
            valueEl.removeAttribute('data-empty');
            // detailed breakdown in the title for hover-tooltip
            const lines = [];
            for (const art of ['manuscript', 'outline', 'handout']) {
                if (!data[art]) continue;
                const br = data[art].source_breakdown;
                const summary = Object.entries(br)
                    .filter(([, v]) => v > 0)
                    .map(([k, v]) => k + ':' + v)
                    .join(', ');
                lines.push(art + ' (' + summary + ')');
            }
            pill.title = 'Last build source breakdown\n' + lines.join('\n');
        } catch (e) {
            // quiet: pill stays at default text
        }
    }

    refresh();
    document.addEventListener('sermon-pack-generated', refresh);
})();
```

- [ ] **Step 3: Run the existing route + study-routes tests**

```bash
cd tools/workbench && python3 -m pytest tests/test_sermon_pack_routes.py tests/test_study_routes.py -v
```
Expected: all PASS (no new tests for JS itself; manual browser verification covers it in the deploy step).

- [ ] **Step 4: Bump cache-buster**

In `templates/study_session.html:171`, change `?v=12` → `?v=13`.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/static/study.js tools/workbench/templates/study_session.html
git commit -m "feat(sermon-pack): JS for source-breakdown pill"
```

---

## Task 19: Full-suite regression + acceptance grep

**Files:** none new; this is a verification task.

- [ ] **Step 1: Run the full pytest suite**

```bash
cd tools/workbench && python3 -m pytest tests/ -q
```
Expected: 1147 + (new tests from this plan, ~30) = ~1177 PASS. Zero failures.

- [ ] **Step 2: Generate against session 22 (live acceptance)**

```bash
cd /Volumes/External/Logos4
mkdir -p /tmp/sp_acceptance
python3 tools/sermon_pack.py 22 --db tools/workbench/companion.db --out /tmp/sp_acceptance
ls -la /tmp/sp_acceptance/*.md
```
Expected: three files, each non-empty:
- `romans-3-25-26-manuscript.md`
- `romans-3-25-26-preaching-outline.md`
- `romans-3-25-26-congregation-handout.md`

- [ ] **Step 3: Style grep — no em-dashes in any output**

```bash
grep -c "—" /tmp/sp_acceptance/*.md
grep -c -- "--" /tmp/sp_acceptance/*.md   # double-hyphen, allowing the PAGEBREAK marker
grep -c "—" tools/sermon_pack_templates/*.tmpl
```
Expected: all `0`s. The handout `<!--PAGEBREAK-->` is the only legal `--` and only appears in the handout file; verify that's the only match:

```bash
grep -n -- "--" /tmp/sp_acceptance/*.md | grep -v 'PAGEBREAK'
```
Expected: no output.

- [ ] **Step 4: Mining metric inspection**

```bash
sqlite3 tools/workbench/companion.db \
  "SELECT artifact, mining_percentage, source_breakdown_json FROM sermon_pack_metrics WHERE session_id = 22 ORDER BY generated_at DESC LIMIT 3"
```
Expected: three rows with non-trivial mining_percentage values (>0) for session 22 (decisions filled most fields but intro/points/conclusion came from mining).

- [ ] **Step 5: Determinism check**

```bash
mkdir -p /tmp/sp_a /tmp/sp_b
python3 tools/sermon_pack.py 22 --db tools/workbench/companion.db --out /tmp/sp_a
python3 tools/sermon_pack.py 22 --db tools/workbench/companion.db --out /tmp/sp_b
diff -r /tmp/sp_a /tmp/sp_b
```
Expected: identical (no diff output).

- [ ] **Step 6: Commit (if any incidental formatting fixes are needed)**

If everything is green, no commit needed. Otherwise commit fixes.

---

## Task 20: Browser-verified deploy

**Files:** none.

- [ ] **Step 1: Check Bryan isn't actively using the app**

```bash
pm2 logs study-companion --lines 30 --nostream 2>&1 | grep -E "GET|POST|PATCH" | tail -8
```
If any line in the last 8 includes Bryan's IP `199.120.77.214` within the last few minutes, PAUSE. Surface to Bryan: "I'm ready to deploy; restarting would interrupt your live session. OK to restart now?"

If quiet:

- [ ] **Step 2: Restart**

```bash
pm2 restart study-companion
```

- [ ] **Step 3: Verify single reader process tree**

```bash
sleep 2
pm2 jlist | python3 -c "import json,sys; ps=json.load(sys.stdin); print([p for p in ps if p['name']=='study-companion'][0]['pid'])"
ps -ef | grep -E "LogosReader|app.py" | grep -v grep
```
Expected: one app.py PID and one LogosReader as its grandchild. No orphans.

- [ ] **Step 4: Health check**

```bash
curl -sI https://study.mightyoakshybrid.org/study/ | head -3
```
Expected: `302 Found` (login redirect for unauthenticated curl). Confirms Flask up + cookie-aware.

- [ ] **Step 5: Live browser verification**

Open https://study.mightyoakshybrid.org/study/session/22 in a browser (Bryan logged in). Visually confirm:

- Sidebar shows **SERMON PACK** label group with 3 rows × 2 links each (Manuscript .md/.html, Outline .md/.html, Handout .md/.html)
- Sidebar shows **OUTLINE TREE ONLY** label group with PDF / Word links
- Pill below SERMON PACK shows mining-% summary (after first download triggers a generation, or already populated from Task 19's CLI run since it wrote to the same companion.db)
- Click each of the six SERMON PACK links: three .md files download cleanly, three .html files render in browser

Take a quick screenshot of the sidebar; paste it in the deploy report.

- [ ] **Step 6: Final summary**

Write the deploy report to Bryan with:
- Commit list (one line each)
- Test totals (before → after)
- Live acceptance: contents of `/tmp/sp_acceptance/` + mining-% values from sermon_pack_metrics
- pm2 PID + reader tree + 302 health check
- Confirmation that sermon_pack_metrics is recording (so the follow-on upstream-fix spec has a baseline)

---

## Self-review checklist

Run mentally before kicking off subagents.

**Spec coverage:**
- §Architecture.1 pure generator + CLI → Tasks 2-13.
- §Architecture.2 source precedence + tags → Tasks 3-8.
- §Architecture.3 three routes (+ HTML render) → Task 15.
- §Architecture.4 UI restructure with pill → Tasks 17-18.
- §Architecture.5 templates in version-controlled directory → Tasks 9-11.
- §Architecture.6 sermon_pack_metrics + --report → Tasks 1, 13.
- §Architecture.7 style discipline (no em-dashes, [missing:], no fabrication) → enforced in every render task + Task 19 grep.
- §Acceptance.1 three-click reproducibility → Tasks 15, 20.
- §Acceptance.2 mining-% non-trivial → Task 19 step 4.
- §Acceptance.3 sparse-data resilience → Task 8 missing-field test + Task 12 empty-decisions test.
- §Acceptance.4 byte-identical determinism → Tasks 9, 12 explicit tests.
- §Acceptance.5 no em-dash → Task 19 step 3.
- §Acceptance.6 browser-verified → Task 20.

**No placeholders:** All code blocks shown in full. No "TBD" or "similar to Task N" references without inline code.

**Type consistency:** `FieldResult` introduced Task 2, used identically through Task 12. `SermonPackError` defined Task 12. `generate()` signature stable from Task 12 onward. Routes pass `companion_db=companion_db` kwarg in Task 15, matching Task 12's parameter.

**Risks flagged:**
- Mining heuristic is intentionally simple (`≥3 H2/H3 headings, pick longest assistant message`). Session 22's id-619 message qualifies easily; if a future session has multiple long outlines, the most recent wins. This is acceptable for the bridge phase before upstream wiring.
- The `points` field from outline_nodes loses sub-point hierarchy beyond one level. Spec defers deep nesting; only one level is observed in production today.
- JS isn't covered by automated tests. Manual browser verification in Task 20 covers it per `feedback_ui_verify_before_done`. If the pill never renders, the failure is visible — not silent — because the empty-state default text "not yet built" still shows.
