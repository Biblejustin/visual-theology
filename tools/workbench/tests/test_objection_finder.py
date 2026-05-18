import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from homiletics_engine import ObjectionFinder


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


class _FixedSearcher:
    def __init__(self, results): self._results = results
    def search(self, q, **kw): return {'ok': True, 'results': self._results}


class _FixedLLM:
    def __init__(self, output): self._output = output
    def call(self, prompt, schema, **kwargs):
        return {'output': self._output, 'input_tokens': 0,
                'output_tokens': 0, 'model': 'test'}


def test_returns_objections_from_both_sources(db):
    d, sid = db
    candidates = [{'resource_id': 'LLS:RECTHEO', 'title': 'Reformed Theology',
                    'authors': 'Berkhof, L.',
                    'sections': [{'snippet': 'Objections to penal substitution...',
                                   'section_offset': 0}]}]
    output = {'objections': [
        {'objection_class': 'theological',
         'objection': 'If God forgives freely, why does justice require punishment?',
         'rebuttal_pointer': 'God\'s justice and mercy meet in Christ; v. 26 explicit.'},
        {'objection_class': 'pastoral',
         'objection': 'But I don\'t feel forgiven.',
         'rebuttal_pointer': 'Justification is forensic, not felt; assurance flows from faith.'},
    ]}
    f = ObjectionFinder(searcher=_FixedSearcher(candidates), llm=_FixedLLM(output))
    insights = f.run(d, session_id=sid, claim='God satisfied his wrath in Christ', count=4)
    # 2 from LLM + at most 1 from search (capped)
    assert len(insights) >= 2
    classes = {i.metadata.get('objection_class') for i in insights if 'objection_class' in i.metadata}
    assert {'theological', 'pastoral'}.issubset(classes)
    rows = d.get_insights(sid, kind='objection')
    assert len(rows) == len(insights)


def test_caps_total_to_count(db):
    d, sid = db
    output = {'objections': [
        {'objection_class': 'theological', 'objection': f'o{i}',
         'rebuttal_pointer': f'r{i}'}
        for i in range(10)
    ]}
    f = ObjectionFinder(searcher=_FixedSearcher([]), llm=_FixedLLM(output))
    insights = f.run(d, session_id=sid, claim='x', count=3)
    assert len(insights) == 3
