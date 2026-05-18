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
