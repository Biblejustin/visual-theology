import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from homiletics_engine import CounselingApplications


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


def test_maps_passage_to_pastoral_situations(db):
    d, sid = db
    output = {'applications': [
        {'pastoral_situation': 'guilt',
         'application': 'The believer who feels their sin disqualifies them needs to hear...',
         'tier': 'remedial',
         'cross_refs': ['1 John 1:9']},
        {'pastoral_situation': 'self_righteousness',
         'application': 'The believer who quietly trusts their own goodness needs...',
         'tier': 'preventative',
         'cross_refs': ['Luke 18:9-14']},
    ]}
    c = CounselingApplications(llm=_FixedLLM(output))
    passage = {'book': 66, 'chapter': 3, 'verse_start': 21, 'verse_end': 26,
               'passage_ref': 'Romans 3:21-26'}
    insights = c.run(d, session_id=sid, passage=passage, count=2)
    assert len(insights) == 2
    sits = {i.metadata['pastoral_situation'] for i in insights}
    assert sits == {'guilt', 'self_righteousness'}
    rows = d.get_insights(sid, kind='counseling')
    assert len(rows) == 2


def test_returns_empty_on_llm_error(db):
    d, sid = db
    class _Err:
        def call(self, prompt, schema, **kwargs):
            return {'error': 'down'}
    c = CounselingApplications(llm=_Err())
    passage = {'book': 66, 'chapter': 3, 'verse_start': 21, 'verse_end': 26,
               'passage_ref': 'Romans 3:21-26'}
    assert c.run(d, session_id=sid, passage=passage) == []
