import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from homiletics_engine import ChristThreadProposer, Insight


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
    def __init__(self, output):
        self._output = output
    def call(self, prompt, schema, **kwargs):
        return {'output': self._output, 'input_tokens': 0,
                'output_tokens': 0, 'model': 'test'}


def test_proposes_christ_threads_with_categories(db):
    d, sid = db
    output = {'angles': [
        {'category': 'direct_reference', 'angle': 'God set forth Christ as a propitiation',
         'rationale': 'The verse names Christ directly as hilastērion.',
         'supporting_refs': ['Romans 3:25', 'Hebrews 9:5']},
        {'category': 'typology',
         'angle': 'Mercy seat in the OT prefigures Christ',
         'rationale': 'hilastērion echoes Exodus mercy seat.',
         'supporting_refs': ['Exodus 25:17', 'Leviticus 16:14']},
    ]}
    proposer = ChristThreadProposer(llm=_FixedLLM(output))
    passage = {'book': 66, 'chapter': 3, 'verse_start': 25, 'verse_end': 25,
               'passage_ref': 'Romans 3:25'}
    insights = proposer.run(d, session_id=sid, passage=passage, count=2)
    assert len(insights) == 2
    assert all(i.kind == 'christ_thread' for i in insights)
    kinds = {i.metadata['category'] for i in insights}
    assert kinds == {'direct_reference', 'typology'}
    # Persisted
    rows = d.get_insights(sid, kind='christ_thread')
    assert len(rows) == 2


def test_returns_empty_on_llm_error(db):
    d, sid = db
    class _Err:
        def call(self, prompt, schema, **kwargs):
            return {'error': 'sdk down'}
    proposer = ChristThreadProposer(llm=_Err())
    passage = {'book': 66, 'chapter': 3, 'verse_start': 25, 'verse_end': 25,
               'passage_ref': 'Romans 3:25'}
    insights = proposer.run(d, session_id=sid, passage=passage)
    assert insights == []


def test_passes_point_into_prompt_when_given(db):
    d, sid = db
    captured = {}
    class _Capture:
        def call(self, prompt, schema, **kwargs):
            captured['prompt'] = prompt
            return {'output': {'angles': []}, 'input_tokens': 0,
                    'output_tokens': 0, 'model': 'test'}
    proposer = ChristThreadProposer(llm=_Capture())
    passage = {'book': 66, 'chapter': 3, 'verse_start': 25, 'verse_end': 25,
               'passage_ref': 'Romans 3:25'}
    proposer.run(d, session_id=sid, passage=passage, point='God\'s wrath is satisfied')
    assert 'wrath is satisfied' in captured['prompt']
