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
        # Parse 'node_id=N: <content>' lines from the prompt and assign tags from the map.
        import re
        tags = []
        for m in re.finditer(r'^node_id=(\d+): (.+)$', prompt, re.MULTILINE):
            node_id = int(m.group(1))
            content = m.group(2)
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
