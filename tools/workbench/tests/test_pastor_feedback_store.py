"""Tests for the pastor_feedback substrate (v2 §10.1)."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pastor_feedback_store import PastorFeedbackStore


@pytest.fixture
def store():
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    s = PastorFeedbackStore(tmp.name)
    s.init_db()
    yield s
    os.unlink(tmp.name)


def test_init_db_creates_table(store):
    conn = store._conn()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pastor_feedback'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_add_feedback_with_all_fields(store):
    fid = store.add_feedback(
        source_label='olivia',
        content='Sermon felt too long today — application landed though.',
        valence='constructive',
        sermon_id=42,
        captured_by='bryan_typed',
    )
    assert fid > 0
    rows = store.recent_feedback()
    assert len(rows) == 1
    r = rows[0]
    assert r['source_label'] == 'olivia'
    assert r['content'].startswith('Sermon felt too long')
    assert r['valence'] == 'constructive'
    assert r['sermon_id'] == 42
    assert r['captured_by'] == 'bryan_typed'


def test_add_feedback_minimal(store):
    """Only source_label + content are required; the rest default sensibly."""
    fid = store.add_feedback(source_label='congregant', content='Loved the gospel close.')
    rows = store.recent_feedback()
    assert len(rows) == 1
    assert rows[0]['valence'] == 'positive'  # default sensible
    assert rows[0]['sermon_id'] is None
    assert rows[0]['captured_by'] == 'bryan_typed'  # default


def test_recent_feedback_orders_newest_first(store):
    store.add_feedback(source_label='olivia', content='first')
    store.add_feedback(source_label='congregant', content='second')
    store.add_feedback(source_label='elder', content='third')
    rows = store.recent_feedback()
    assert [r['content'] for r in rows] == ['third', 'second', 'first']


def test_recent_feedback_respects_limit(store):
    for i in range(8):
        store.add_feedback(source_label='olivia', content=f'feedback {i}')
    rows = store.recent_feedback(limit=3)
    assert len(rows) == 3


def test_recent_feedback_filters_by_source(store):
    store.add_feedback(source_label='olivia', content='from spouse')
    store.add_feedback(source_label='congregant', content='from member')
    rows = store.recent_feedback(source_label='olivia')
    assert len(rows) == 1
    assert rows[0]['source_label'] == 'olivia'


def test_delete_feedback_removes_row(store):
    fid = store.add_feedback(source_label='olivia', content='oops')
    store.delete_feedback(fid)
    assert store.recent_feedback() == []


def test_count_by_valence_groups_results(store):
    store.add_feedback(source_label='olivia', content='good', valence='positive')
    store.add_feedback(source_label='olivia', content='good 2', valence='positive')
    store.add_feedback(source_label='congregant', content='cut it', valence='constructive')
    counts = store.count_by_valence()
    assert counts.get('positive') == 2
    assert counts.get('constructive') == 1


def test_count_by_valence_empty(store):
    assert store.count_by_valence() == {}


def test_valence_constraint_rejects_bad_value(store):
    with pytest.raises(Exception):
        store.add_feedback(source_label='olivia', content='x', valence='bogus')
