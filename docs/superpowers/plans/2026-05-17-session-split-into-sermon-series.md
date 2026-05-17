# Session Split into Sermon Series — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a study session split into N sibling sessions for a multi-sermon series — shared exegesis (parent cards rendered read-only in children), per-sermon homiletics in each child, AI-extracted plan from the conversation, and a sermon matcher that targets children once the parent has been split.

**Architecture:** Three new columns on `sessions` (`parent_session_id`, `series_position`, `series_title`) + helpers in `companion_db.py`. New pure-function `series_planner.py` extracts the plan from `conversation_messages` via the existing `LLMClient` protocol (Opus 4.6 in prod, `CannedLLMClient` in tests). Two new Flask endpoints (`/study/session/<id>/split/propose` and `/study/session/<id>/split`) wire the modal flow. Matcher excludes parents in the candidate query. Q1 candidates-widget fix folded in (separate session-side and sermon-side partials, corrected query, drop `LIMIT 1` on linked sermons).

**Tech Stack:** Python 3 + Flask, SQLite via `sqlite3`, Jinja2 templates, vanilla JS (no framework), `anthropic` SDK via `llm_client.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-05-17-session-split-into-sermon-series-design.md`

---

### Task 1: Schema migration — three columns + index on `sessions`

**Files:**
- Modify: `tools/workbench/companion_db.py` (extend the inline migration block around lines 313-321)
- Test: `tools/workbench/tests/test_session_split_schema.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_session_split_schema.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile
import pytest
from companion_db import CompanionDB


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    yield d
    os.unlink(path)


def test_sessions_has_parent_session_id_column(db):
    cols = {r[1] for r in db._conn().execute("PRAGMA table_info(sessions)").fetchall()}
    assert 'parent_session_id' in cols
    assert 'series_position' in cols
    assert 'series_title' in cols


def test_sessions_has_parent_index(db):
    rows = db._conn().execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sessions'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert 'idx_sessions_parent' in names


def test_init_db_is_idempotent(db):
    db.init_db()  # second call must not raise
    cols = {r[1] for r in db._conn().execute("PRAGMA table_info(sessions)").fetchall()}
    assert 'parent_session_id' in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_session_split_schema.py -v`
Expected: 2 failures (columns missing, index missing). `test_init_db_is_idempotent` may also fail.

- [ ] **Step 3: Add the migration in `companion_db.py`**

In `tools/workbench/companion_db.py`, immediately after the existing block at line 314-321 that handles `last_homiletical_activity_at` and `transcript_*` columns, add:

```python
        # Series split: parent/child sessions for one-prep-many-sermons
        if 'parent_session_id' not in existing_session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN parent_session_id INTEGER REFERENCES sessions(id)")
        if 'series_position' not in existing_session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN series_position INTEGER")
        if 'series_title' not in existing_session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN series_title TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id)")
```

The `existing_session_cols` set is already computed at line 314. The index uses `IF NOT EXISTS` so a second init is safe.

- [ ] **Step 4: Run the tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_session_split_schema.py -v`
Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/companion_db.py tools/workbench/tests/test_session_split_schema.py
git commit -m "feat(db): session series schema — parent_session_id, series_position, series_title"
```

---

### Task 2: DB helpers — `get_children`, `get_parent`, `get_series`

**Files:**
- Modify: `tools/workbench/companion_db.py` (add three methods to the `CompanionDB` class)
- Test: `tools/workbench/tests/test_session_split_helpers.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_session_split_helpers.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    yield d
    os.unlink(path)


def _seed_series(db):
    """Parent (id=1, pos=1) + two children (ids=2,3 pos=2,3)."""
    parent_id = db.create_session(
        passage_ref='Romans 3:21-22', book=66, chapter=3,
        verse_start=21, verse_end=22, genre='epistle',
    )
    conn = db._conn()
    conn.execute(
        "UPDATE sessions SET series_position=1, series_title=? WHERE id=?",
        ('But Now', parent_id),
    )
    conn.commit()
    child_a = db.create_session(
        passage_ref='Romans 3:23-24', book=66, chapter=3,
        verse_start=23, verse_end=24, genre='epistle',
    )
    child_b = db.create_session(
        passage_ref='Romans 3:25-26', book=66, chapter=3,
        verse_start=25, verse_end=26, genre='epistle',
    )
    conn.execute(
        "UPDATE sessions SET parent_session_id=?, series_position=2, series_title=? WHERE id=?",
        (parent_id, 'All Have Sinned', child_a),
    )
    conn.execute(
        "UPDATE sessions SET parent_session_id=?, series_position=3, series_title=? WHERE id=?",
        (parent_id, 'God Put Forward', child_b),
    )
    conn.commit()
    return parent_id, child_a, child_b


def test_get_children_returns_children_in_position_order(db):
    parent_id, child_a, child_b = _seed_series(db)
    children = db.get_children(parent_id)
    assert [c['id'] for c in children] == [child_a, child_b]
    assert [c['series_position'] for c in children] == [2, 3]


def test_get_children_empty_for_standalone_session(db):
    sid = db.create_session(passage_ref='Acts 1:1', book=44, chapter=1,
                            verse_start=1, verse_end=1, genre='narrative')
    assert db.get_children(sid) == []


def test_get_parent_returns_parent_row_for_child(db):
    parent_id, child_a, _ = _seed_series(db)
    parent = db.get_parent(child_a)
    assert parent['id'] == parent_id


def test_get_parent_returns_none_for_standalone(db):
    sid = db.create_session(passage_ref='Acts 1:1', book=44, chapter=1,
                            verse_start=1, verse_end=1, genre='narrative')
    assert db.get_parent(sid) is None


def test_get_series_returns_parent_plus_children_ordered_for_any_member(db):
    parent_id, child_a, child_b = _seed_series(db)
    for sid in (parent_id, child_a, child_b):
        members = db.get_series(sid)
        assert [m['id'] for m in members] == [parent_id, child_a, child_b]
        assert [m['series_position'] for m in members] == [1, 2, 3]


def test_get_series_returns_empty_for_standalone(db):
    sid = db.create_session(passage_ref='Acts 1:1', book=44, chapter=1,
                            verse_start=1, verse_end=1, genre='narrative')
    assert db.get_series(sid) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_session_split_helpers.py -v`
Expected: 6 failures with `AttributeError: 'CompanionDB' object has no attribute 'get_children'` (and similar for the other helpers).

- [ ] **Step 3: Add the helpers to `CompanionDB`**

In `tools/workbench/companion_db.py`, add these three methods inside the `CompanionDB` class. Place them after the existing `get_session` method (find it with `grep -n "def get_session" tools/workbench/companion_db.py`).

```python
    def get_children(self, parent_id):
        """Return child sessions of `parent_id` ordered by series_position."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE parent_session_id = ? "
                "ORDER BY series_position",
                (parent_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_parent(self, child_id):
        """Return the parent session row for `child_id`, or None if standalone."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT p.* FROM sessions c "
                "JOIN sessions p ON p.id = c.parent_session_id "
                "WHERE c.id = ?",
                (child_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_series(self, session_id):
        """Return parent + all children ordered by position, for any member id.

        Returns [] if the session is standalone (no parent, no children).
        """
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT parent_session_id FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return []
            parent_id = row['parent_session_id'] if row['parent_session_id'] else session_id
            # Check that parent_id is actually a parent (has children) — otherwise standalone.
            children = conn.execute(
                "SELECT 1 FROM sessions WHERE parent_session_id = ? LIMIT 1",
                (parent_id,),
            ).fetchone()
            if not children:
                return []
            rows = conn.execute(
                "SELECT * FROM sessions WHERE id = ? OR parent_session_id = ? "
                "ORDER BY series_position",
                (parent_id, parent_id),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_session_split_helpers.py -v`
Expected: 6 passes.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/companion_db.py tools/workbench/tests/test_session_split_helpers.py
git commit -m "feat(db): series helpers get_children, get_parent, get_series"
```

---

### Task 3: Parameterize `tool_name` in `AnthropicClient.call`

The current `AnthropicClient.call` hardcodes the tool name `emit_review` (`llm_client.py:77,86`). The series planner needs a different tool name (`emit_series_plan`) without that semantic mismatch. Add an optional `tool_name` parameter that defaults to `emit_review` — fully backwards-compatible.

**Files:**
- Modify: `tools/workbench/llm_client.py:61-107`
- Test: `tools/workbench/tests/test_llm_client_tool_name.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_llm_client_tool_name.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from llm_client import AnthropicClient


class _FakeResponse:
    def __init__(self, tool_name, tool_input):
        class _Block:
            type = 'tool_use'
            input = tool_input
            name = tool_name
        class _Usage:
            input_tokens = 1
            output_tokens = 1
        self.content = [_Block()]
        self.usage = _Usage()


class _FakeMessages:
    def __init__(self):
        self.captured_kwargs = None
    def create(self, **kwargs):
        self.captured_kwargs = kwargs
        return _FakeResponse(kwargs['tools'][0]['name'], {'ok': True})


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def test_call_uses_custom_tool_name_when_provided():
    c = AnthropicClient(api_key='x')
    c._client = _FakeClient()
    out = c.call(prompt='hi', schema={'type': 'object'}, tool_name='emit_series_plan')
    assert out['output'] == {'ok': True}
    captured = c._client.messages.captured_kwargs
    assert captured['tools'][0]['name'] == 'emit_series_plan'
    assert captured['tool_choice'] == {'type': 'tool', 'name': 'emit_series_plan'}


def test_call_defaults_to_emit_review_when_no_tool_name():
    c = AnthropicClient(api_key='x')
    c._client = _FakeClient()
    out = c.call(prompt='hi', schema={'type': 'object'})
    assert out['output'] == {'ok': True}
    captured = c._client.messages.captured_kwargs
    assert captured['tools'][0]['name'] == 'emit_review'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_llm_client_tool_name.py -v`
Expected: `test_call_uses_custom_tool_name_when_provided` fails — the tool name will still be `emit_review`. (The default-case test will already pass.)

- [ ] **Step 3: Edit `llm_client.py`**

In `tools/workbench/llm_client.py`, change the `call` signature and the two hardcoded `'emit_review'` strings:

Replace lines 61-87 (the start of `call` through the `kwargs = dict(...)` block, including the tools/tool_choice fields) with:

```python
    def call(self, prompt: str, schema: dict, *,
             max_tokens: int = 4096,
             system: Optional[str] = None,
             track_usage: bool = True,
             tool_name: str = 'emit_review',
             tool_description: str = 'Emit the structured sermon review following the schema.') -> dict:
        """Call the model with a tool-use-enforced schema.

        Returns {output: dict, input_tokens: int, output_tokens: int, model: str}.
        On error, returns {error: str}.
        """
        try:
            client = self._get_client()
        except Exception as e:
            return {'error': f'sdk_import_failed: {type(e).__name__}: {e}'}
        try:
            messages = [{'role': 'user', 'content': prompt}]
            tools = [{
                'name': tool_name,
                'description': tool_description,
                'input_schema': schema,
            }]
            kwargs = dict(
                model=self.model,
                max_tokens=max_tokens,
                messages=messages,
                tools=tools,
                tool_choice={'type': 'tool', 'name': tool_name},
            )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_llm_client_tool_name.py tests/ -k "llm_client or analyzer or tagger" -v`
Expected: new tests pass; existing analyzer/tagger tests still pass (they use the default tool name).

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/llm_client.py tools/workbench/tests/test_llm_client_tool_name.py
git commit -m "refactor(llm): parameterize AnthropicClient.call tool_name (default emit_review)"
```

---

### Task 4: `series_planner.propose_series_plan` — extract plan from messages

**Files:**
- Create: `tools/workbench/series_planner.py`
- Create: `tools/workbench/tests/test_series_planner.py`

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_series_planner.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from llm_client import CannedLLMClient
from series_planner import propose_series_plan, SeriesPlanRow


def _msgs(*pairs):
    return [{'id': i+1, 'role': r, 'content': c} for i, (r, c) in enumerate(pairs)]


def test_returns_rows_when_llm_returns_valid_plan():
    canned = CannedLLMClient({
        # Match by prompt-prefix hash; series_planner builds the prompt
        # deterministically from messages, so we use a fixture-by-key approach
        # by registering the response under any key that the call() lookup finds.
    })
    # Simpler: build a tiny adapter client that returns a fixed plan.
    class _Fixed:
        def call(self, prompt, schema, **kwargs):
            return {'output': {'sermons': [
                {'position': 1, 'title': 'But Now', 'verse_start': 21, 'verse_end': 22},
                {'position': 2, 'title': 'All Have Sinned', 'verse_start': 23, 'verse_end': 24},
                {'position': 3, 'title': 'God Put Forward', 'verse_start': 25, 'verse_end': 26},
                {'position': 4, 'title': 'The Heart of the Gospel', 'verse_start': 21, 'verse_end': 26},
            ]}, 'input_tokens': 0, 'output_tokens': 0, 'model': 'test'}
    rows = propose_series_plan(_msgs(('user', 'split into 4')), session_chapter=3, llm=_Fixed())
    assert len(rows) == 4
    assert rows[0] == SeriesPlanRow(position=1, title='But Now', verse_start=21, verse_end=22)
    assert rows[3] == SeriesPlanRow(position=4, title='The Heart of the Gospel',
                                     verse_start=21, verse_end=26)


def test_returns_empty_when_llm_returns_error():
    class _Err:
        def call(self, prompt, schema, **kwargs):
            return {'error': 'sdk down'}
    rows = propose_series_plan(_msgs(('user', 'hi')), session_chapter=3, llm=_Err())
    assert rows == []


def test_returns_empty_when_llm_returns_no_sermons():
    class _Empty:
        def call(self, prompt, schema, **kwargs):
            return {'output': {'sermons': []}, 'input_tokens': 0, 'output_tokens': 0, 'model': 'test'}
    rows = propose_series_plan(_msgs(('user', 'hi')), session_chapter=3, llm=_Empty())
    assert rows == []


def test_skips_rows_with_invalid_verse_range():
    class _Bad:
        def call(self, prompt, schema, **kwargs):
            return {'output': {'sermons': [
                {'position': 1, 'title': 'ok', 'verse_start': 1, 'verse_end': 2},
                {'position': 2, 'title': 'bad-start', 'verse_start': -1, 'verse_end': 5},
                {'position': 3, 'title': 'bad-order', 'verse_start': 5, 'verse_end': 2},
                {'position': 4, 'title': 'missing', 'verse_start': None, 'verse_end': None},
            ]}, 'input_tokens': 0, 'output_tokens': 0, 'model': 'test'}
    rows = propose_series_plan(_msgs(('user', 'hi')), session_chapter=3, llm=_Bad())
    assert len(rows) == 1
    assert rows[0].title == 'ok'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_series_planner.py -v`
Expected: import error — `series_planner` does not exist yet.

- [ ] **Step 3: Create `series_planner.py`**

Create `tools/workbench/series_planner.py`:

```python
"""Extract a multi-sermon series plan from a session's conversation messages.

Pure function with an injectable LLMClient. Returns a list of SeriesPlanRow
or an empty list on any error / no-plan-found. The Flask route uses the result
to pre-populate the split modal; users can always edit before confirming.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol, Optional


@dataclass(frozen=True)
class SeriesPlanRow:
    position: int
    title: str
    verse_start: int
    verse_end: int


class _Client(Protocol):
    def call(self, prompt: str, schema: dict, **kwargs) -> dict: ...


_SCHEMA = {
    'type': 'object',
    'properties': {
        'sermons': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'position': {'type': 'integer', 'minimum': 1},
                    'title': {'type': 'string'},
                    'verse_start': {'type': 'integer', 'minimum': 1},
                    'verse_end': {'type': 'integer', 'minimum': 1},
                },
                'required': ['position', 'title', 'verse_start', 'verse_end'],
            },
        },
    },
    'required': ['sermons'],
}


_SYSTEM = (
    "You read a pastor's sermon-prep conversation and extract any plan to "
    "split the passage into a multi-sermon series. Return one row per sermon "
    "in preaching order with the title and verse range the pastor chose. "
    "If no series plan is discussed, return an empty list."
)


def _build_prompt(messages: list, session_chapter: int) -> str:
    convo = '\n'.join(
        f"[{m['id']}] {m['role']}: {m['content']}" for m in messages
    )
    return (
        f"Chapter under study: {session_chapter}\n\n"
        f"Conversation:\n{convo}\n\n"
        "Extract the sermon series plan if present. If the pastor decided "
        "to split this passage into multiple sermons, list each sermon with "
        "its position (1-based, preaching order), title (in quotes if given), "
        "and verse range."
    )


def propose_series_plan(messages: list, session_chapter: int,
                        llm: _Client) -> list:
    """Return a list of SeriesPlanRow extracted from `messages`.

    Returns [] on any LLM error, empty output, or if all rows fail validation.
    """
    if not messages:
        return []
    result = llm.call(
        prompt=_build_prompt(messages, session_chapter),
        schema=_SCHEMA,
        system=_SYSTEM,
        tool_name='emit_series_plan',
        tool_description='Emit the proposed sermon-series plan extracted from the conversation.',
    )
    if 'error' in result or 'output' not in result:
        return []
    raw = result['output'].get('sermons') or []
    rows = []
    for r in raw:
        try:
            vs = int(r.get('verse_start'))
            ve = int(r.get('verse_end'))
            pos = int(r.get('position'))
            title = str(r.get('title') or '').strip()
        except (TypeError, ValueError):
            continue
        if vs < 1 or ve < 1 or vs > ve or not title or pos < 1:
            continue
        rows.append(SeriesPlanRow(position=pos, title=title,
                                  verse_start=vs, verse_end=ve))
    rows.sort(key=lambda r: r.position)
    return rows
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_series_planner.py -v`
Expected: 4 passes.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/series_planner.py tools/workbench/tests/test_series_planner.py
git commit -m "feat(planner): series_planner.propose_series_plan extracts plan from chat"
```

---

### Task 5: Matcher excludes sessions that have children

**Files:**
- Modify: `tools/workbench/sermon_matcher.py:219-224` (the session query in `apply_match_decision_for_sermon`)
- Test: `tools/workbench/tests/test_sermon_matcher_parent_excluded.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_sermon_matcher_parent_excluded.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from sermon_matcher import apply_match_decision_for_sermon


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    yield d
    os.unlink(path)


def _insert_sermon(db, *, title, vs, ve, preach_date):
    conn = db._conn()
    cur = conn.execute(
        "INSERT INTO sermons (title, bible_text_raw, book, chapter, verse_start, "
        "verse_end, preach_date, sermon_type, classified_as, match_status, "
        "source_version, ui_last_seen_version, sync_status) "
        "VALUES (?, ?, 66, 3, ?, ?, ?, 'expository', 'sermon', 'unmatched', 0, 0, 'ok')",
        (title, f"Romans 3:{vs}-{ve}", vs, ve, preach_date),
    )
    sermon_id = cur.lastrowid
    conn.execute("INSERT INTO sermon_passages (sermon_id, rank, book, chapter_start, "
                  "verse_start, chapter_end, verse_end) VALUES (?, 1, 66, 3, ?, 3, ?)",
                  (sermon_id, vs, ve))
    conn.commit()
    return sermon_id


def test_parent_with_children_is_not_a_match_candidate(db):
    parent_id = db.create_session(passage_ref='Romans 3:21-22', book=66, chapter=3,
                                   verse_start=21, verse_end=22, genre='epistle')
    child_id = db.create_session(passage_ref='Romans 3:23-24', book=66, chapter=3,
                                  verse_start=23, verse_end=24, genre='epistle')
    conn = db._conn()
    conn.execute("UPDATE sessions SET series_position=1, series_title='But Now', "
                  "last_homiletical_activity_at='2026-05-08 12:00:00' WHERE id=?",
                  (parent_id,))
    conn.execute("UPDATE sessions SET parent_session_id=?, series_position=2, "
                  "series_title='All Have Sinned', "
                  "last_homiletical_activity_at='2026-05-15 12:00:00' WHERE id=?",
                  (parent_id, child_id))
    conn.commit()

    sermon_id = _insert_sermon(db, title='All Have Sinned', vs=23, ve=24,
                                preach_date='2026-05-17')
    decision = apply_match_decision_for_sermon(db, sermon_id)
    assert decision.action == 'auto_link'
    assert decision.auto_link_target.session_id == child_id


def test_standalone_session_still_matches_when_no_children_exist(db):
    sid = db.create_session(passage_ref='Romans 3:1-8', book=66, chapter=3,
                             verse_start=1, verse_end=8, genre='epistle')
    db._conn().execute(
        "UPDATE sessions SET last_homiletical_activity_at='2026-05-08 12:00:00' WHERE id=?",
        (sid,),
    )
    db._conn().commit()
    sermon_id = _insert_sermon(db, title='Not Performance', vs=1, ve=8,
                                preach_date='2026-05-10')
    decision = apply_match_decision_for_sermon(db, sermon_id)
    assert decision.action == 'auto_link'
    assert decision.auto_link_target.session_id == sid
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_sermon_matcher_parent_excluded.py -v`
Expected: `test_parent_with_children_is_not_a_match_candidate` fails — the matcher currently considers the parent (verse 21-22) AND the child (verse 23-24); with sermon range 23-24 the parent gets an "overlap" tier2 match and the child gets an "exact" match. Depending on tier outcomes you may get `surface_candidates` or auto-link to the wrong one.

- [ ] **Step 3: Edit the matcher query**

In `tools/workbench/sermon_matcher.py`, find the block at lines 219-224:

```python
        session_rows = conn.execute("""
            SELECT id, book, chapter, verse_start, verse_end,
                   created_at, last_homiletical_activity_at
            FROM sessions
            WHERE book = ? AND chapter = ?
        """, (s_row[1], s_row[2])).fetchall()
```

Replace with:

```python
        session_rows = conn.execute("""
            SELECT id, book, chapter, verse_start, verse_end,
                   created_at, last_homiletical_activity_at
            FROM sessions
            WHERE book = ? AND chapter = ?
              AND id NOT IN (
                  SELECT parent_session_id FROM sessions
                  WHERE parent_session_id IS NOT NULL
              )
        """, (s_row[1], s_row[2])).fetchall()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_sermon_matcher_parent_excluded.py tests/test_sermon_matcher.py -v`
Expected: 2 new tests pass; existing `test_sermon_matcher.py` cases continue to pass (they use pure-function `match_sermon_to_sessions`, which is unchanged).

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/sermon_matcher.py tools/workbench/tests/test_sermon_matcher_parent_excluded.py
git commit -m "feat(matcher): exclude parent sessions once they have children"
```

---

### Task 6: Split endpoint — `POST /study/session/<id>/split`

**Files:**
- Modify: `tools/workbench/app.py` (add route near the other `/study/session/<int:session_id>/...` handlers around line 1100)
- Test: `tools/workbench/tests/test_session_split_endpoint.py` (create)

The endpoint accepts a confirmed plan as JSON, validates it, runs the split in a single transaction, then re-runs the matcher for every sermon currently linked to the parent.

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_session_split_endpoint.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json, tempfile, pytest
import app as app_module


@pytest.fixture
def client(monkeypatch):
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    monkeypatch.setenv('COMPANION_DB_PATH', path)
    app_module.app.config['TESTING'] = True
    app_module.startup()
    with app_module.app.test_client() as c:
        yield c
    os.unlink(path)


def _create_parent(db):
    sid = db.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                             verse_start=21, verse_end=26, genre='epistle')
    return sid


def test_split_creates_children_and_narrows_parent(client):
    db = app_module.companion_db
    parent_id = _create_parent(db)

    payload = {'plan': [
        {'position': 1, 'title': 'But Now', 'verse_start': 21, 'verse_end': 22},
        {'position': 2, 'title': 'All Have Sinned', 'verse_start': 23, 'verse_end': 24},
        {'position': 3, 'title': 'God Put Forward', 'verse_start': 25, 'verse_end': 26},
        {'position': 4, 'title': 'The Heart of the Gospel', 'verse_start': 21, 'verse_end': 26},
    ]}
    resp = client.post(f'/study/session/{parent_id}/split',
                        data=json.dumps(payload), content_type='application/json')
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body['parent_id'] == parent_id
    assert len(body['children']) == 3

    parent = db.get_session(parent_id)
    assert parent['verse_start'] == 21 and parent['verse_end'] == 22
    assert parent['series_position'] == 1
    assert parent['series_title'] == 'But Now'

    children = db.get_children(parent_id)
    assert [c['series_position'] for c in children] == [2, 3, 4]
    assert [c['verse_start'] for c in children] == [23, 25, 21]
    assert [c['verse_end'] for c in children] == [24, 26, 26]
    assert [c['series_title'] for c in children] == [
        'All Have Sinned', 'God Put Forward', 'The Heart of the Gospel']


def test_split_rejects_when_session_already_split(client):
    db = app_module.companion_db
    parent_id = _create_parent(db)
    payload = {'plan': [
        {'position': 1, 'title': 'a', 'verse_start': 21, 'verse_end': 22},
        {'position': 2, 'title': 'b', 'verse_start': 23, 'verse_end': 24},
    ]}
    resp1 = client.post(f'/study/session/{parent_id}/split',
                         data=json.dumps(payload), content_type='application/json')
    assert resp1.status_code == 200
    resp2 = client.post(f'/study/session/{parent_id}/split',
                         data=json.dumps(payload), content_type='application/json')
    assert resp2.status_code == 400
    assert 'already_split' in resp2.get_json()['error']


def test_split_rejects_invalid_plan(client):
    db = app_module.companion_db
    parent_id = _create_parent(db)
    # Position 1 missing
    payload = {'plan': [{'position': 2, 'title': 'b', 'verse_start': 23, 'verse_end': 24}]}
    resp = client.post(f'/study/session/{parent_id}/split',
                        data=json.dumps(payload), content_type='application/json')
    assert resp.status_code == 400
    assert 'missing_position_1' in resp.get_json()['error']
```

`app.config['TESTING'] = True` bypasses the `@app.before_request` auth check at `app.py:164-176` — no session-key tweaking needed.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_session_split_endpoint.py -v`
Expected: 404 on the POST — the route does not exist yet.

- [ ] **Step 3: Add the route to `app.py`**

In `tools/workbench/app.py`, add this route. Place it next to the existing `/study/session/<int:session_id>/...` handlers (the existing handlers start around line 1083 — put this one after the last `study/session/...` route, around line 1410):

```python
@app.route("/study/session/<int:session_id>/split", methods=["POST"])
def study_session_split(session_id):
    """Split a session into a sermon series.

    Body: {"plan": [{"position": 1, "title": "...", "verse_start": int, "verse_end": int}, ...]}

    Position 1 narrows the parent. Positions 2..N create children.
    All sermons currently linked to the parent are re-matched after the split.
    """
    payload = request.get_json(silent=True) or {}
    plan = payload.get("plan") or []
    if not plan:
        return jsonify({"error": "empty_plan"}), 400

    parent = companion_db.get_session(session_id)
    if not parent:
        return jsonify({"error": "session_not_found"}), 404
    if companion_db.get_children(session_id):
        return jsonify({"error": "already_split"}), 400
    if parent.get("parent_session_id"):
        return jsonify({"error": "cannot_split_a_child"}), 400

    # Normalize + validate plan
    try:
        rows = []
        for r in plan:
            rows.append({
                "position": int(r["position"]),
                "title": str(r["title"]).strip(),
                "verse_start": int(r["verse_start"]),
                "verse_end": int(r["verse_end"]),
            })
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "malformed_plan_row"}), 400
    rows.sort(key=lambda r: r["position"])
    positions = [r["position"] for r in rows]
    if positions[0] != 1:
        return jsonify({"error": "missing_position_1"}), 400
    if positions != list(range(1, len(rows) + 1)):
        return jsonify({"error": "non_contiguous_positions"}), 400
    for r in rows:
        if not r["title"] or r["verse_start"] < 1 or r["verse_end"] < r["verse_start"]:
            return jsonify({"error": "invalid_row"}), 400

    # Derive the book/chapter prefix from the parent's existing passage_ref
    # (everything before the colon), so we don't need a book-id-to-name map.
    book_chap_prefix = parent["passage_ref"].split(":")[0].rsplit(" ", 1)[0] + f" {parent['chapter']}"

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()

    conn = companion_db._conn()
    conn.execute("BEGIN IMMEDIATE")
    try:
        first = rows[0]
        conn.execute(
            "UPDATE sessions SET verse_start=?, verse_end=?, "
            "series_position=1, series_title=?, passage_ref=?, updated_at=? WHERE id=?",
            (first["verse_start"], first["verse_end"], first["title"],
             f"{book_chap_prefix}:{first['verse_start']}-{first['verse_end']}",
             now_iso, session_id),
        )
        child_ids = []
        for r in rows[1:]:
            cur = conn.execute(
                "INSERT INTO sessions (passage_ref, book, chapter, verse_start, "
                "verse_end, genre, parent_session_id, series_position, series_title, "
                "current_phase, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'prayer', 'active', ?, ?)",
                (f"{book_chap_prefix}:{r['verse_start']}-{r['verse_end']}",
                 parent["book"], parent["chapter"], r["verse_start"], r["verse_end"],
                 parent["genre"], session_id, r["position"], r["title"],
                 now_iso, now_iso),
            )
            child_ids.append(cur.lastrowid)

        # Find sermons currently linked to the parent and re-match them
        linked_sermons = [row[0] for row in conn.execute(
            "SELECT sermon_id FROM sermon_links WHERE session_id=? "
            "AND link_status IN ('active','candidate')", (session_id,)
        ).fetchall()]
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        conn.close()
        raise
    finally:
        conn.close()

    # Re-match outside the txn — each call opens its own BEGIN IMMEDIATE
    from sermon_matcher import apply_match_decision_for_sermon
    for sid in linked_sermons:
        apply_match_decision_for_sermon(companion_db, sid)

    children = companion_db.get_children(session_id)
    return jsonify({
        "parent_id": session_id,
        "children": [{"id": c["id"], "position": c["series_position"],
                       "title": c["series_title"]} for c in children],
    })
```

No external helpers are needed — `now_iso` is built inline from `datetime`, and the book/chapter prefix is derived from the parent's existing `passage_ref` (which always has the form `"Romans 3:21-26"` etc.).

The test's `_make_sermon_with_candidate_link`-style INSERTs in Task 5 confirm the `sermons` table already exists in test DBs after `init_db`. No additional schema work needed here.

- [ ] **Step 4: Run the tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_session_split_endpoint.py -v`
Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/app.py tools/workbench/tests/test_session_split_endpoint.py
git commit -m "feat(study): POST /study/session/<id>/split — narrow parent, create children, re-match"
```

---

### Task 7: Propose endpoint — `POST /study/session/<id>/split/propose`

**Files:**
- Modify: `tools/workbench/app.py` (add route adjacent to the split route)
- Test: `tools/workbench/tests/test_split_propose_endpoint.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_split_propose_endpoint.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
import app as app_module
import series_planner as planner_module


@pytest.fixture
def client(monkeypatch):
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    monkeypatch.setenv('COMPANION_DB_PATH', path)
    app_module.app.config['TESTING'] = True
    app_module.startup()

    def _fake_propose(messages, session_chapter, llm=None):
        return [
            planner_module.SeriesPlanRow(position=1, title='But Now',
                                          verse_start=21, verse_end=22),
            planner_module.SeriesPlanRow(position=2, title='All Have Sinned',
                                          verse_start=23, verse_end=24),
        ]
    monkeypatch.setattr(app_module, 'propose_series_plan', _fake_propose, raising=False)
    with app_module.app.test_client() as c:
        yield c
    os.unlink(path)


def test_propose_returns_plan_from_planner(client):
    db = app_module.companion_db
    sid = db.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                             verse_start=21, verse_end=26, genre='epistle')
    resp = client.post(f'/study/session/{sid}/split/propose')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['plan'][0]['title'] == 'But Now'
    assert body['plan'][1]['verse_start'] == 23


def test_propose_returns_empty_plan_when_no_session(client):
    resp = client.post('/study/session/9999/split/propose')
    assert resp.status_code == 404
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_split_propose_endpoint.py -v`
Expected: 404 (route missing).

- [ ] **Step 3: Add the route and import**

At the top of `tools/workbench/app.py`, add the import next to the other companion imports (around line 48-50):

```python
from series_planner import propose_series_plan
```

Add the route next to the split route from Task 6:

```python
@app.route("/study/session/<int:session_id>/split/propose", methods=["POST"])
def study_session_split_propose(session_id):
    """Read the session's conversation and return an AI-extracted series plan."""
    session = companion_db.get_session(session_id)
    if not session:
        return jsonify({"error": "session_not_found"}), 404
    messages = companion_db.get_messages(session_id, limit=500)

    from llm_client import AnthropicClient
    from app_secrets import anthropic_api_key
    llm = AnthropicClient(api_key=anthropic_api_key())
    rows = propose_series_plan(messages, session_chapter=session["chapter"], llm=llm)
    return jsonify({"plan": [
        {"position": r.position, "title": r.title,
         "verse_start": r.verse_start, "verse_end": r.verse_end}
        for r in rows
    ]})
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_split_propose_endpoint.py -v`
Expected: 2 passes.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/app.py tools/workbench/tests/test_split_propose_endpoint.py
git commit -m "feat(study): POST /study/session/<id>/split/propose — AI-extracted plan"
```

---

### Task 8: Fix Q1 — split the candidates partial and correct the session-side query

**Files:**
- Create: `tools/workbench/templates/partials/sermon_candidates_for_session.html`
- Modify: `tools/workbench/templates/partials/sermon_candidates_list.html` → rename or repurpose as the sermon-side partial
- Modify: `tools/workbench/templates/sermons/detail.html:20`
- Modify: `tools/workbench/templates/partials/sermon_review_tab.html:9,28`
- Modify: `tools/workbench/templates/study_session.html` (the `{% include 'partials/sermon_candidates_list.html' %}` reference, if any)
- Modify: `tools/workbench/app.py:1006-1028` (the linked-sermon + candidates queries in `study_session_view`)
- Test: `tools/workbench/tests/test_candidates_widget_session_view.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_candidates_widget_session_view.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
import app as app_module


@pytest.fixture
def client(monkeypatch):
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    monkeypatch.setenv('COMPANION_DB_PATH', path)
    app_module.app.config['TESTING'] = True
    app_module.startup()
    with app_module.app.test_client() as c:
        yield c
    os.unlink(path)


def _make_sermon_with_candidate_link(db, *, session_id, title, vs, ve,
                                       preach_date, reason='tier2:overlap+no_hom'):
    conn = db._conn()
    cur = conn.execute(
        "INSERT INTO sermons (title, bible_text_raw, book, chapter, verse_start, "
        "verse_end, preach_date, sermon_type, classified_as, match_status, "
        "source_version, ui_last_seen_version, sync_status) "
        "VALUES (?, ?, 66, 3, ?, ?, ?, 'expository', 'sermon', 'awaiting_candidates', 0, 0, 'ok')",
        (title, f"Romans 3:{vs}-{ve}", vs, ve, preach_date),
    )
    sermon_id = cur.lastrowid
    conn.execute(
        "INSERT INTO sermon_links (sermon_id, session_id, link_status, "
        "link_source, match_reason, created_at) "
        "VALUES (?, ?, 'candidate', 'auto', ?, ?)",
        (sermon_id, session_id, reason, '2026-05-17T00:00:00'),
    )
    conn.commit()
    return sermon_id


def test_session_view_shows_only_candidates_for_this_session(client):
    db = app_module.companion_db
    target_session = db.create_session(
        passage_ref='Romans 3:21-26', book=66, chapter=3,
        verse_start=21, verse_end=26, genre='epistle')
    other_session = db.create_session(
        passage_ref='Romans 3:1-8', book=66, chapter=3,
        verse_start=1, verse_end=8, genre='epistle')

    # Sermon with candidate links to BOTH sessions.
    sermon_id = _make_sermon_with_candidate_link(
        db, session_id=target_session, title='But Now', vs=20, ve=22,
        preach_date='2026-05-10')
    conn = db._conn()
    conn.execute(
        "INSERT INTO sermon_links (sermon_id, session_id, link_status, "
        "link_source, match_reason, created_at) "
        "VALUES (?, ?, 'candidate', 'auto', 'tier2:overlap+no_hom', '2026-05-17T00:00:00')",
        (sermon_id, other_session),
    )
    conn.commit()

    resp = client.get(f'/study/session/{target_session}')
    html = resp.get_data(as_text=True)
    # Header copy must be session-side
    assert 'Sermons that may have been prepped in this session' in html
    # Sermon title must appear once for this session, with preach date
    assert html.count('But Now') >= 1
    assert '2026-05-10' in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_candidates_widget_session_view.py -v`
Expected: assertion failure on the header copy (current partial says "Not sure which session you prepped for this sermon").

- [ ] **Step 3: Rename the existing partial and create the new one**

Rename:

```bash
git mv tools/workbench/templates/partials/sermon_candidates_list.html \
        tools/workbench/templates/partials/sermon_candidates_for_sermon.html
```

The renamed file is the sermon-side variant — keep its content (it already does the right thing for sermon detail).

Create `tools/workbench/templates/partials/sermon_candidates_for_session.html`:

```html
{% if candidates %}
<div class="sermon-card sermon-card-candidates">
  <h2>Sermons that may have been prepped in this session</h2>
  <p>Pick the sermon that matches this prep, and we'll link it.</p>
  <ul>
    {% for c in candidates %}
      <li>
        <form method="post" action="/sermons/{{ c.sermon_id }}/approve-candidate/{{ c.id }}" style="display:inline">
          <strong>{{ c.title or '(untitled sermon)' }}</strong>
          <span class="passage">{{ c.bible_text_raw }}</span>
          {% if c.preach_date %}<span class="preach-date">preached {{ c.preach_date }}</span>{% endif %}
          <span class="match-reason">{{ c.match_reason }}</span>
          <button type="submit">Approve</button>
        </form>
      </li>
    {% endfor %}
  </ul>
</div>
{% endif %}
```

- [ ] **Step 4: Update the templates that include the old partial**

In `tools/workbench/templates/sermons/detail.html:20`, change:

```jinja
{% include 'partials/sermon_candidates_list.html' %}
```
to:
```jinja
{% include 'partials/sermon_candidates_for_sermon.html' %}
```

In `tools/workbench/templates/partials/sermon_review_tab.html:9` and `:28`, do the same replacement.

In `tools/workbench/templates/study_session.html`, find the include (`grep -n "sermon_candidates" tools/workbench/templates/study_session.html`). Replace with:

```jinja
{% include 'partials/sermon_candidates_for_session.html' %}
```

If no include exists in `study_session.html`, add one in the appropriate panel area (search for where `linked_sermon` is rendered) and pass the `candidates` already in scope.

- [ ] **Step 5: Fix the session-side query in `app.py`**

In `tools/workbench/app.py`, replace lines 1006-1028 (the `try:` block in `study_session_view` that loads `linked_sermon`, `review`, and `candidates`) with:

```python
    linked_sermons = []
    review = None
    candidates = []
    conn = companion_db._conn()
    try:
        link_rows = conn.execute("""
            SELECT s.*
            FROM sermons s
            JOIN sermon_links sl ON sl.sermon_id = s.id
            WHERE sl.session_id = ? AND sl.link_status = 'active'
            ORDER BY s.preach_date DESC
        """, (session_id,)).fetchall()
        linked_sermons = [dict(r) for r in link_rows]
        if linked_sermons:
            from sermon_coach_tools import get_sermon_review as _get_review_parsed
            review = _get_review_parsed(companion_db, linked_sermons[0]['id'])
        candidate_rows = conn.execute("""
            SELECT sl.id, sl.sermon_id, sl.match_reason,
                   s.title, s.bible_text_raw, s.preach_date
            FROM sermon_links sl
            JOIN sermons s ON s.id = sl.sermon_id
            WHERE sl.link_status = 'candidate' AND sl.session_id = ?
            ORDER BY s.preach_date DESC
        """, (session_id,)).fetchall()
        candidates = [dict(c) for c in candidate_rows]
    finally:
        conn.close()
```

Then update the two `render_template` calls in `study_session_view` (around lines 1050-1080) to pass `linked_sermons=linked_sermons` instead of `linked_sermon=linked_sermon`. Update `templates/study_session.html` accordingly to iterate `linked_sermons` instead of treating `linked_sermon` as a single object — show the sermon list in whatever section currently renders the single sermon. (`grep -n "linked_sermon" tools/workbench/templates/study_session.html` to find the spots.)

- [ ] **Step 6: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_candidates_widget_session_view.py -v`
Expected: pass.

Also run the broader sermon-UI smoke test:

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_e2e_sermon_ui.py -v`
Expected: pass — the sermon-side partial rename should not break it (templates were updated).

- [ ] **Step 7: Commit**

```bash
git add tools/workbench/app.py \
        tools/workbench/templates/partials/sermon_candidates_for_session.html \
        tools/workbench/templates/partials/sermon_candidates_for_sermon.html \
        tools/workbench/templates/sermons/detail.html \
        tools/workbench/templates/partials/sermon_review_tab.html \
        tools/workbench/templates/study_session.html \
        tools/workbench/tests/test_candidates_widget_session_view.py
git commit -m "fix(study): session-side candidates widget — correct query, dedicated partial, multi-sermon link list"
```

---

### Task 9: Series breadcrumb + read-only parent cards banner

**Files:**
- Modify: `tools/workbench/templates/study_session.html`
- Modify: `tools/workbench/app.py` — `study_session_view` to load `series_members`, `is_child`, and inherit cards from parent
- Modify: `tools/workbench/static/companion.css` — minimal styles for breadcrumb + banner
- Test: `tools/workbench/tests/test_series_breadcrumb_view.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tools/workbench/tests/test_series_breadcrumb_view.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
import app as app_module


@pytest.fixture
def client(monkeypatch):
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    monkeypatch.setenv('COMPANION_DB_PATH', path)
    app_module.app.config['TESTING'] = True
    app_module.startup()
    with app_module.app.test_client() as c:
        yield c
    os.unlink(path)


def _seed_split(db):
    parent_id = db.create_session(passage_ref='Romans 3:21-22', book=66, chapter=3,
                                   verse_start=21, verse_end=22, genre='epistle')
    db._conn().execute(
        "UPDATE sessions SET series_position=1, series_title='But Now' WHERE id=?",
        (parent_id,))
    db._conn().commit()
    db.save_card_response(parent_id, 'read_translate', None, 'Broad exegesis text…')
    child_id = db.create_session(passage_ref='Romans 3:23-24', book=66, chapter=3,
                                  verse_start=23, verse_end=24, genre='epistle')
    db._conn().execute(
        "UPDATE sessions SET parent_session_id=?, series_position=2, "
        "series_title='All Have Sinned' WHERE id=?", (parent_id, child_id))
    db._conn().commit()
    return parent_id, child_id


def test_breadcrumb_renders_siblings_on_parent_view(client):
    parent_id, child_id = _seed_split(app_module.companion_db)
    resp = client.get(f'/study/session/{parent_id}')
    html = resp.get_data(as_text=True)
    assert 'But Now' in html and 'All Have Sinned' in html
    assert 'series-breadcrumb' in html  # CSS hook


def test_child_view_inherits_parent_cards_read_only(client):
    parent_id, child_id = _seed_split(app_module.companion_db)
    # Child is at phase 'read_translate'
    app_module.companion_db._conn().execute(
        "UPDATE sessions SET current_phase='read_translate' WHERE id=?", (child_id,))
    app_module.companion_db._conn().commit()
    resp = client.get(f'/study/session/{child_id}')
    html = resp.get_data(as_text=True)
    assert 'Broad exegesis text' in html
    assert 'From parent session' in html
```

`save_card_response` is defined as `save_card_response(session_id, phase, question_id, content)` — the test passes `None` for `question_id`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_series_breadcrumb_view.py -v`
Expected: 2 failures (no breadcrumb, no parent-card inheritance).

- [ ] **Step 3: Wire series context into `study_session_view`**

In `tools/workbench/app.py`, inside `study_session_view` after loading `session` (around line 996), add:

```python
    series_members = companion_db.get_series(session_id)
    parent_session = None
    if session.get('parent_session_id'):
        parent_session = companion_db.get_parent(session_id)
```

In the card-mode branch (around line 1037-1042), the existing `resource_text` and `study_bible_notes` come from helpers keyed on the current session. Override to read from the parent if this is a child:

```python
        card_session_id = parent_session['id'] if parent_session else session_id
        if card_def["auto_resource"] == "original_language":
            resource_text = _get_original_language_text(
                parent_session if parent_session else session)
        elif card_def["auto_resource"] == "study_bibles":
            study_bible_notes = _get_study_bible_notes(
                parent_session if parent_session else session)
        responses = companion_db.get_card_responses(card_session_id, phase)
        prefilled = companion_db.get_card_notepad(card_session_id, f"card_{phase}")
        if not prefilled:
            prefilled = responses[-1]["content"] if responses else ""
```

(Keep `annotations` and `notepad` keyed on the child's own id — those are per-child.)

Pass `series_members=series_members`, `parent_session=parent_session`, and a derived `is_child=bool(parent_session)` to both `render_template` calls.

- [ ] **Step 4: Render the breadcrumb and banner in `study_session.html`**

At the top of the session view in `tools/workbench/templates/study_session.html` (find the first `<div>` after the page header — `grep -n "session-header\|<h1\|<header" tools/workbench/templates/study_session.html | head`), add:

```jinja
{% if series_members %}
<nav class="series-breadcrumb" aria-label="Sermon series">
  <span class="label">Series:</span>
  {% for m in series_members %}
    <a href="/study/session/{{ m.id }}" class="{% if m.id == session.id %}active{% endif %}">
      {{ m.series_position }}. {{ m.series_title or m.passage_ref }}
    </a>{% if not loop.last %} · {% endif %}
  {% endfor %}
</nav>
{% endif %}
```

Inside the card-mode area, where the prefilled/auto-resource content renders, add (somewhere visible above the card content):

```jinja
{% if is_child %}
<div class="parent-card-banner">
  From parent session: <a href="/study/session/{{ parent_session.id }}">{{ parent_session.passage_ref }}</a>
  (broad exegesis, read-only)
</div>
{% endif %}
```

If the child should not be able to edit the card, also disable the submit button when `is_child` is true — find the card form's submit button and add `{% if is_child %}disabled{% endif %}` to it.

- [ ] **Step 5: Add minimal styles**

Append to `tools/workbench/static/companion.css`:

```css
.series-breadcrumb {
  padding: 8px 12px;
  background: var(--surface-2, #1b1d22);
  border-radius: 6px;
  margin-bottom: 12px;
  font-size: 13px;
}
.series-breadcrumb .label { opacity: 0.7; margin-right: 6px; }
.series-breadcrumb a { color: var(--text-2, #b8bcc4); text-decoration: none; }
.series-breadcrumb a.active { color: var(--accent, #7aa2f7); font-weight: 600; }

.parent-card-banner {
  padding: 6px 10px;
  background: var(--surface-3, #23262d);
  border-left: 3px solid var(--accent, #7aa2f7);
  font-size: 13px;
  margin-bottom: 10px;
}
```

- [ ] **Step 6: Run tests to verify pass**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_series_breadcrumb_view.py -v`
Expected: 2 passes.

- [ ] **Step 7: Commit**

```bash
git add tools/workbench/app.py tools/workbench/templates/study_session.html \
        tools/workbench/static/companion.css \
        tools/workbench/tests/test_series_breadcrumb_view.py
git commit -m "feat(study): series breadcrumb + read-only parent cards in children"
```

---

### Task 10: Split-modal UI — button, propose call, edit, confirm

**Files:**
- Create: `tools/workbench/templates/partials/series_split_modal.html`
- Modify: `tools/workbench/templates/study_session.html` — include the modal and add the "Split into series" button
- Modify: `tools/workbench/static/companion.js` — modal open/close, AJAX wiring
- Modify: `tools/workbench/static/companion.css` — modal styles
- Test: manual smoke (no new automated test in this task — modal interaction is covered by Task 11's E2E)

- [ ] **Step 1: Create the modal partial**

Create `tools/workbench/templates/partials/series_split_modal.html`:

```html
<dialog id="series-split-modal" class="series-split-modal">
  <form method="dialog" id="series-split-form">
    <h2>Split into sermon series</h2>
    <p class="hint">Edit the proposed rows. Position 1 narrows this session; positions 2+ create new child sessions.</p>
    <div id="series-split-loading" hidden>Reading the conversation…</div>
    <table id="series-split-rows">
      <thead>
        <tr><th>#</th><th>Title</th><th>Verses</th><th></th></tr>
      </thead>
      <tbody></tbody>
    </table>
    <button type="button" id="series-split-add">+ Add sermon</button>
    <div id="series-split-error" class="error" hidden></div>
    <menu>
      <button value="cancel">Cancel</button>
      <button value="confirm" id="series-split-confirm">Confirm split</button>
    </menu>
  </form>
</dialog>
```

- [ ] **Step 2: Add the trigger button + include in `study_session.html`**

In `tools/workbench/templates/study_session.html`, add near the session header (only when not already split and not a child):

```jinja
{% if not series_members and not is_child %}
<button id="open-split-modal" class="secondary"
        data-session-id="{{ session.id }}">Split into series</button>
{% endif %}
{% include 'partials/series_split_modal.html' %}
```

- [ ] **Step 3: Add the JS in `companion.js`**

Append to `tools/workbench/static/companion.js`:

```javascript
(function () {
  const openBtn = document.getElementById('open-split-modal');
  if (!openBtn) return;
  const modal = document.getElementById('series-split-modal');
  const tbody = modal.querySelector('#series-split-rows tbody');
  const errBox = modal.querySelector('#series-split-error');
  const loading = modal.querySelector('#series-split-loading');
  const addBtn = modal.querySelector('#series-split-add');
  const confirmBtn = modal.querySelector('#series-split-confirm');
  const sessionId = openBtn.dataset.sessionId;

  function renderRow(row) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><input type="number" min="1" value="${row.position}" class="pos" style="width:3em"></td>
      <td><input type="text" value="${row.title.replace(/"/g, '&quot;')}" class="title"></td>
      <td>
        <input type="number" min="1" value="${row.verse_start}" class="vs" style="width:4em"> –
        <input type="number" min="1" value="${row.verse_end}" class="ve" style="width:4em">
      </td>
      <td><button type="button" class="del">×</button></td>`;
    tr.querySelector('.del').onclick = () => tr.remove();
    return tr;
  }

  function readRows() {
    return Array.from(tbody.querySelectorAll('tr')).map(tr => ({
      position: parseInt(tr.querySelector('.pos').value, 10),
      title: tr.querySelector('.title').value.trim(),
      verse_start: parseInt(tr.querySelector('.vs').value, 10),
      verse_end: parseInt(tr.querySelector('.ve').value, 10),
    }));
  }

  openBtn.addEventListener('click', async () => {
    tbody.innerHTML = '';
    errBox.hidden = true;
    loading.hidden = false;
    modal.showModal();
    try {
      const resp = await fetch(`/study/session/${sessionId}/split/propose`, {method: 'POST'});
      const body = await resp.json();
      (body.plan || []).forEach(r => tbody.appendChild(renderRow(r)));
      if (!body.plan || body.plan.length === 0) {
        // Seed with one empty row
        tbody.appendChild(renderRow({position: 1, title: '', verse_start: 1, verse_end: 1}));
      }
    } catch (e) {
      errBox.textContent = 'Could not load proposal: ' + e.message;
      errBox.hidden = false;
      tbody.appendChild(renderRow({position: 1, title: '', verse_start: 1, verse_end: 1}));
    } finally {
      loading.hidden = true;
    }
  });

  addBtn.addEventListener('click', () => {
    const next = tbody.querySelectorAll('tr').length + 1;
    tbody.appendChild(renderRow({position: next, title: '', verse_start: 1, verse_end: 1}));
  });

  confirmBtn.addEventListener('click', async (ev) => {
    ev.preventDefault();
    errBox.hidden = true;
    const plan = readRows();
    const resp = await fetch(`/study/session/${sessionId}/split`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({plan}),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({error: 'unknown'}));
      errBox.textContent = 'Split failed: ' + body.error;
      errBox.hidden = false;
      return;
    }
    modal.close();
    window.location.reload();
  });
})();
```

- [ ] **Step 4: Add minimal modal styles**

Append to `tools/workbench/static/companion.css`:

```css
.series-split-modal { background: var(--surface-1, #15171c); color: var(--text-1, #e6e7ea); border: 1px solid var(--border, #2a2d35); border-radius: 8px; min-width: 480px; }
.series-split-modal table { width: 100%; border-collapse: collapse; margin: 8px 0; }
.series-split-modal th, .series-split-modal td { padding: 4px 6px; text-align: left; }
.series-split-modal input { background: var(--surface-2, #1b1d22); color: inherit; border: 1px solid var(--border, #2a2d35); padding: 4px 6px; border-radius: 4px; }
.series-split-modal .error { color: #f7768e; padding: 6px 0; }
.series-split-modal menu { display: flex; justify-content: flex-end; gap: 8px; padding: 0; margin-top: 12px; }
.series-split-modal .hint { font-size: 13px; opacity: 0.75; }
```

- [ ] **Step 5: Manual smoke test**

```bash
cd /Volumes/External/Logos4/tools/workbench
pkill -f "python3 app.py" 2>/dev/null; sleep 1
python3 app.py &
sleep 2
```

Open `http://localhost:5111/study/session/20` in a browser. Click "Split into series". Verify:
- Modal opens with rows pre-populated from the AI plan (or empty if extraction failed).
- You can edit titles and verse ranges.
- Clicking "+ Add sermon" adds a new row.
- Clicking the × button removes a row.
- Clicking "Confirm split" closes the modal and reloads — verify the parent now shows the breadcrumb with all sibling sermons.

Stop the server when done: `pkill -f "python3 app.py"`

- [ ] **Step 6: Commit**

```bash
git add tools/workbench/templates/partials/series_split_modal.html \
        tools/workbench/templates/study_session.html \
        tools/workbench/static/companion.js \
        tools/workbench/static/companion.css
git commit -m "feat(study): split-into-series modal — AI proposal, editable rows, confirm"
```

---

### Task 11: E2E test — full split flow against the real session 20 fixture

**Files:**
- Create: `tools/workbench/tests/test_session_split_e2e.py`

- [ ] **Step 1: Write the E2E test**

Create `tools/workbench/tests/test_session_split_e2e.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json, tempfile, pytest
import app as app_module
import series_planner as planner_module


@pytest.fixture
def client(monkeypatch):
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    monkeypatch.setenv('COMPANION_DB_PATH', path)
    app_module.app.config['TESTING'] = True
    app_module.startup()
    # Stub the planner so we don't hit the real LLM.
    fake_plan = [
        planner_module.SeriesPlanRow(position=1, title='But Now', verse_start=21, verse_end=22),
        planner_module.SeriesPlanRow(position=2, title='All Have Sinned', verse_start=23, verse_end=24),
        planner_module.SeriesPlanRow(position=3, title='God Put Forward', verse_start=25, verse_end=26),
        planner_module.SeriesPlanRow(position=4, title='The Heart of the Gospel',
                                      verse_start=21, verse_end=26),
    ]
    monkeypatch.setattr(app_module, 'propose_series_plan',
                         lambda messages, session_chapter, llm=None: fake_plan)
    with app_module.app.test_client() as c:
        yield c
    os.unlink(path)


def test_full_split_flow_for_romans_3_21_26(client):
    db = app_module.companion_db
    parent_id = db.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                                   verse_start=21, verse_end=26, genre='epistle')
    db.save_card_response(parent_id, 'read_translate', None, 'Broad exegesis of Romans 3:21-26')

    # 1) Propose
    resp = client.post(f'/study/session/{parent_id}/split/propose')
    assert resp.status_code == 200
    plan = resp.get_json()['plan']
    assert len(plan) == 4
    assert plan[0]['title'] == 'But Now'

    # 2) Confirm split
    resp = client.post(f'/study/session/{parent_id}/split',
                        data=json.dumps({'plan': plan}),
                        content_type='application/json')
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body['children']) == 3

    # 3) Parent narrowed
    parent = db.get_session(parent_id)
    assert parent['verse_start'] == 21 and parent['verse_end'] == 22
    assert parent['series_title'] == 'But Now'

    # 4) Children created with right verses
    children = db.get_children(parent_id)
    assert [(c['verse_start'], c['verse_end']) for c in children] == [
        (23, 24), (25, 26), (21, 26)]

    # 5) Breadcrumb appears on parent and each child
    for sid in [parent_id] + [c['id'] for c in children]:
        resp = client.get(f'/study/session/{sid}')
        assert b'series-breadcrumb' in resp.data, f'no breadcrumb on session {sid}'

    # 6) Child inherits parent's read_translate card
    child = children[0]
    db._conn().execute("UPDATE sessions SET current_phase='read_translate' WHERE id=?",
                        (child['id'],))
    db._conn().commit()
    resp = client.get(f'/study/session/{child["id"]}')
    assert b'Broad exegesis of Romans 3:21-26' in resp.data
    assert b'From parent session' in resp.data

    # 7) Re-split is rejected
    resp = client.post(f'/study/session/{parent_id}/split',
                        data=json.dumps({'plan': plan}),
                        content_type='application/json')
    assert resp.status_code == 400
```

- [ ] **Step 2: Run the test**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/test_session_split_e2e.py -v`
Expected: pass.

- [ ] **Step 3: Run the full test suite**

Run: `cd /Volumes/External/Logos4/tools/workbench && python3 -m pytest tests/ -v`
Expected: all green, no regressions.

- [ ] **Step 4: Commit**

```bash
git add tools/workbench/tests/test_session_split_e2e.py
git commit -m "test(study): end-to-end split flow for Romans 3:21-26"
```

---

### Task 12: Manual verification against the live `companion.db` fixture

This task is manual — verifies the spec's primary user story against the actual data.

- [ ] **Step 1: Snapshot the live DB**

```bash
cp /Volumes/External/Logos4/tools/workbench/companion.db \
   /Volumes/External/Logos4/tools/workbench/companion.db.pre-split-bak
```

- [ ] **Step 2: Restart the Flask app**

```bash
cd /Volumes/External/Logos4/tools/workbench
pkill -f "python3 app.py" 2>/dev/null; sleep 1
python3 app.py &
sleep 2
```

Or if managed by PM2:

```bash
pm2 restart study-companion
```

- [ ] **Step 3: Walk the flow in a browser**

Open `https://study.mightyoakshybrid.org/study/session/20` (or `http://localhost:5111/study/session/20` if hitting locally).

Verify:
- "Split into series" button is visible.
- Click it. Modal opens with 4 rows extracted from the conversation: But Now (21-22), All Have Sinned (23-24), God Put Forward (25-26), The Heart of the Gospel (21-26).
- Confirm. Page reloads, breadcrumb shows all 4 sermons.
- Click "All Have Sinned" in the breadcrumb → new child session opens at phase prayer.
- Navigate to the `read_translate` card on the child → see the parent's broad exegesis text with the "From parent session" banner.
- Back at parent (session 20) → linked sermon "But Now" is still shown; no candidate-widget weirdness.

- [ ] **Step 4: Roll back if anything looks off**

```bash
pm2 stop study-companion 2>/dev/null || pkill -f "python3 app.py"
mv /Volumes/External/Logos4/tools/workbench/companion.db.pre-split-bak \
   /Volumes/External/Logos4/tools/workbench/companion.db
pm2 restart study-companion 2>/dev/null || (cd /Volumes/External/Logos4/tools/workbench && python3 app.py &)
```

- [ ] **Step 5: If verified, remove backup and commit a note**

```bash
rm /Volumes/External/Logos4/tools/workbench/companion.db.pre-split-bak
```

(No commit needed for this task — verification only.)

---

## Out of scope (deferred)

- Per-child card overrides (children currently always render the parent's cards read-only; no way to override per sermon).
- Sermon-detail page breadcrumb when the linked session is part of a series.
- Deletion safeguards for children that have any conversation messages.
- Auto-detect splits on historical sessions other than session 20.
