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
