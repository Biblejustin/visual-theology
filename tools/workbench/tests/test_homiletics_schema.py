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
