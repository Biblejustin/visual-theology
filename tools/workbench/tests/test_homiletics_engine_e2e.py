import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from embeddings_client import CannedEmbeddingsClient
from homiletics_engine import (
    Insight, ANALYZER_KINDS, IllustrationFinder, ChristThreadProposer,
    WisdomPrinciples, ObjectionFinder, CounselingApplications,
    BalanceGauger, CoherenceChecker,
)


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


class _CannedLLM:
    def __init__(self, output): self._output = output
    def call(self, prompt, schema, **kwargs):
        return {'output': self._output, 'input_tokens': 0,
                'output_tokens': 0, 'model': 'test'}


class _CannedSearcher:
    def __init__(self, results): self._results = results
    def search(self, q, **kw): return {'ok': True, 'results': self._results}


def test_all_seven_kinds_can_persist_to_one_session(db):
    d, sid = db
    passage = {'book': 66, 'chapter': 3, 'verse_start': 21, 'verse_end': 26,
               'passage_ref': 'Romans 3:21-26'}

    # IllustrationFinder
    IllustrationFinder(
        searcher=_CannedSearcher([{'resource_id': 'LLS:R', 'title': 't',
                                     'sections': [{'snippet': 's', 'section_offset': 0}]}]),
        llm=_CannedLLM({'ranked': [{'index': 0, 'score': 0.8, 'rationale': 'r'}]}),
    ).run(d, session_id=sid, theme='grace', count=1)

    # ChristThreadProposer
    ChristThreadProposer(llm=_CannedLLM({'angles': [
        {'category': 'typology', 'angle': 'a', 'rationale': 'r',
         'supporting_refs': ['Romans 3:25']}
    ]})).run(d, session_id=sid, passage=passage, count=1)

    # WisdomPrinciples
    WisdomPrinciples(llm=_CannedLLM({'principles': [
        {'principle': 'p', 'rationale': 'r', 'cross_refs': []}
    ]})).run(d, session_id=sid, passage=passage, count=1)

    # ObjectionFinder
    ObjectionFinder(
        searcher=_CannedSearcher([]),
        llm=_CannedLLM({'objections': [
            {'objection_class': 'theological', 'objection': 'o', 'rebuttal_pointer': 'p'}
        ]}),
    ).run(d, session_id=sid, claim='c', count=1)

    # CounselingApplications
    CounselingApplications(llm=_CannedLLM({'applications': [
        {'pastoral_situation': 'guilt', 'application': 'a', 'tier': 'remedial'}
    ]})).run(d, session_id=sid, passage=passage, count=1)

    # BalanceGauger needs an outline node first
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn = d._conn()
    cur = conn.execute(
        "INSERT INTO outline_nodes (session_id, type, content, rank, created_at, updated_at) "
        "VALUES (?, 'point', 'a node', 0, ?, ?)", (sid, now, now))
    balance_node_id = cur.lastrowid
    conn.commit()
    conn.close()
    BalanceGauger(llm=_CannedLLM({'tags': [
        {'node_id': balance_node_id, 'homiletical_type': 'exegetical_claim'}
    ]})).run(d, session_id=sid)

    # CoherenceChecker needs a node in another session
    other = d.create_session(passage_ref='Eph 2', book=69, chapter=2,
                              verse_start=8, verse_end=9, genre='epistle')
    conn = d._conn()
    conn.execute(
        "INSERT INTO outline_nodes (session_id, type, content, rank, created_at, updated_at) "
        "VALUES (?, 'point', 'justified by faith alone', 0, ?, ?)", (other, now, now))
    conn.commit()
    conn.close()
    CoherenceChecker(embeddings=CannedEmbeddingsClient(dimension=8),
                     similarity_threshold=0.0).run(
        d, session_id=sid, claim='justified by faith alone')

    # Assert all seven kinds persisted
    rows = d.get_insights(sid)
    kinds = {r['kind'] for r in rows}
    assert ANALYZER_KINDS.issubset(kinds), f"missing kinds: {ANALYZER_KINDS - kinds}"
