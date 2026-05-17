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
