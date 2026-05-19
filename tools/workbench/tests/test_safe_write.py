"""Tests for CompanionDB._safe_write context manager (polish #3).

The helper consolidates the try/except/rollback/finally boilerplate that
was applied unevenly across write helpers. These tests pin the
semantics so future refactors don't quietly break commit/rollback/close
guarantees.
"""

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from companion_db import CompanionDB


@pytest.fixture
def db():
    """Fresh DB per test, deleted on cleanup."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    yield d
    # WAL companions
    for suffix in ("", "-shm", "-wal"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


def _open_count_proxy(db):
    """Count how many connections the helper closes by intercepting _conn."""
    opens = []
    real = db._conn

    def tracker():
        c = real()
        opens.append(c)
        return c
    db._conn = tracker
    return opens


def test_safe_write_commits_on_success(db):
    sid = db.create_session("Rom 1:1", 45, 1, 1, 1, "epistle")
    # If commit didn't happen, get_session would return None
    assert db.get_session(sid) is not None


def test_safe_write_rolls_back_on_exception(db):
    # Create a session, then attempt a write that fails — verify state unchanged
    sid = db.create_session("Rom 1:1", 45, 1, 1, 1, "epistle")
    before = db.get_session(sid)['current_phase']

    # update_phase + intentionally bad SQL inside the with block via
    # a custom helper that simulates a mid-transaction failure
    with pytest.raises(Exception):
        with db._safe_write() as conn:
            conn.execute("UPDATE sessions SET current_phase = ? WHERE id = ?",
                         ('badphase', sid))
            # Force an error AFTER the partial write
            conn.execute("SELECT * FROM nonexistent_table_xyz")

    after = db.get_session(sid)['current_phase']
    assert after == before, "rollback should undo the in-flight phase update"


def test_safe_write_closes_connection_on_success(db):
    opens = _open_count_proxy(db)
    with db._safe_write() as conn:
        conn.execute("SELECT 1")
    assert len(opens) == 1
    # Sanity: closed connections raise on .execute
    with pytest.raises(sqlite3.ProgrammingError):
        opens[0].execute("SELECT 1")


def test_safe_write_closes_connection_on_failure(db):
    opens = _open_count_proxy(db)
    with pytest.raises(Exception):
        with db._safe_write() as conn:
            conn.execute("SELECT * FROM nonexistent_table_xyz")
    assert len(opens) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opens[0].execute("SELECT 1")


def test_migrated_helpers_still_work(db):
    """Smoke test the helpers we migrated — they should behave identically."""
    sid = db.create_session("Rom 8:1", 45, 8, 1, 1, "epistle")
    assert sid is not None

    # update_phase
    db.update_phase(sid, "text_work")
    assert db.get_session(sid)['current_phase'] == 'text_work'

    # save_card_response
    rid = db.save_card_response(sid, "text_work", 1, "test response")
    assert rid is not None

    # save_message
    mid = db.save_message(sid, "text_work", "user", "hello")
    assert mid is not None

    # add_outline_node + update_outline_node + delete_outline_node
    nid = db.add_outline_node(sid, "point", "Main point")
    assert nid is not None
    updated = db.update_outline_node(nid, content="Revised main point")
    assert updated == 1
    deleted = db.delete_outline_node(nid)
    assert deleted == 1

    # save_card_annotation + save_card_notepad
    aid = db.save_card_annotation(sid, "text_work", "bdag", "logos", "key word")
    assert aid is not None
    db.save_card_notepad(sid, "text_work", "scratch")
    assert db.get_card_notepad(sid, "text_work") == "scratch"

    # update_timer + complete_session + delete_session
    db.update_timer(sid, 1500, paused=True, elapsed_delta=60)
    db.complete_session(sid)
    assert db.get_session(sid)['status'] == 'completed'
    db.delete_session(sid)
    assert db.get_session(sid) is None


def test_update_outline_node_returns_rowcount_for_missing(db):
    """Regression: bug review #7 — missing-id mutations must return 0."""
    sid = db.create_session("Rom 1", 45, 1, 1, 1, "epistle")
    db.add_outline_node(sid, "point", "real node")
    assert db.update_outline_node(99999, content="nope") == 0
    assert db.delete_outline_node(99999) == 0
