import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile
import pytest
from companion_db import CompanionDB


# Override pytest-base-url's autouse _verify_url fixture so these DB-only
# tests don't trigger conftest.py's Flask-server `base_url` fixture (which
# hard-exits if port 5111 is in use).
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
