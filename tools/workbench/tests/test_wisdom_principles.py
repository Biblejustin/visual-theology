import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from homiletics_engine import WisdomPrinciples


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


class _FixedLLM:
    def __init__(self, output): self._output = output
    def call(self, prompt, schema, **kwargs):
        return {'output': self._output, 'input_tokens': 0,
                'output_tokens': 0, 'model': 'test'}


def test_distills_wisdom_principles_with_cross_refs(db):
    d, sid = db
    output = {'principles': [
        {'principle': 'Boasting in human righteousness is excluded by the gospel.',
         'rationale': 'Paul shuts down self-justification by grounding righteousness in God\'s gift.',
         'cross_refs': ['Ephesians 2:9', 'Galatians 6:14'],
         'principle_tags': ['humility', 'grace']},
        {'principle': 'God\'s justice and mercy meet in the cross.',
         'rationale': 'God is both just and the justifier through Christ\'s propitiation.',
         'cross_refs': ['Psalm 85:10'],
         'principle_tags': ['atonement']},
    ]}
    p = WisdomPrinciples(llm=_FixedLLM(output))
    passage = {'book': 66, 'chapter': 3, 'verse_start': 21, 'verse_end': 26,
               'passage_ref': 'Romans 3:21-26'}
    insights = p.run(d, session_id=sid, passage=passage, count=2)
    assert len(insights) == 2
    assert all(i.kind == 'wisdom' for i in insights)
    assert 'humility' in insights[0].metadata['principle_tags']
    assert 'Ephesians 2:9' in insights[0].citations
    rows = d.get_insights(sid, kind='wisdom')
    assert len(rows) == 2


def test_returns_empty_on_llm_error(db):
    d, sid = db
    class _Err:
        def call(self, prompt, schema, **kwargs):
            return {'error': 'sdk down'}
    p = WisdomPrinciples(llm=_Err())
    passage = {'book': 66, 'chapter': 3, 'verse_start': 21, 'verse_end': 26,
               'passage_ref': 'Romans 3:21-26'}
    assert p.run(d, session_id=sid, passage=passage) == []
