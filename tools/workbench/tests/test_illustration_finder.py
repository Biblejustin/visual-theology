import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from homiletics_engine import IllustrationFinder, Insight


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
    """Stub LogosSearcher returning a fixed result list."""
    def __init__(self, results):
        self._results = results
    def search(self, query, **kwargs):
        return {'ok': True, 'results': self._results}


class _FixedLLM:
    """Stub LLM returning a fixed ranking."""
    def __init__(self, output):
        self._output = output
    def call(self, prompt, schema, **kwargs):
        return {'output': self._output, 'input_tokens': 0,
                'output_tokens': 0, 'model': 'test'}


def test_illustration_finder_happy_path(db):
    d, sid = db
    candidates = [
        {'resource_id': 'LLS:GRECOROMWRLD', 'title': 'Greco-Roman World',
         'authors': 'Jeffers, James S.', 'sections': [{
             'snippet': 'Pliny wrote to Trajan in AD 112 about Christians refusing to sacrifice.',
             'section_offset': 100}]},
        {'resource_id': 'LLS:IRRELEVANT', 'title': 'Boring Book',
         'authors': 'Anon', 'sections': [{'snippet': 'theory of patronage abstract',
                                            'section_offset': 50}]},
    ]
    llm = _FixedLLM({'ranked': [
        {'index': 0, 'score': 0.92, 'rationale': 'Concrete: names Pliny and Trajan with date AD 112.'},
        {'index': 1, 'score': 0.21, 'rationale': 'Abstract; no narrative.'},
    ]})
    finder = IllustrationFinder(searcher=_FixedSearcher(candidates), llm=llm)
    insights = finder.run(d, session_id=sid, theme='Greco-Roman patronage', count=1)
    assert len(insights) == 1
    assert insights[0].kind == 'illustration'
    assert 'Pliny' in insights[0].body
    assert insights[0].citations == ['LLS:GRECOROMWRLD']
    assert insights[0].confidence == 0.92
    assert insights[0].metadata['rationale'].startswith('Concrete')
    # Persisted
    rows = d.get_insights(sid, kind='illustration')
    assert len(rows) == 1


def test_illustration_finder_returns_empty_when_search_fails(db):
    d, sid = db
    class _Fail:
        def search(self, q, **kw): return {'ok': False, 'error': 'search_down'}
    finder = IllustrationFinder(searcher=_Fail(), llm=None)
    insights = finder.run(d, session_id=sid, theme='anything', count=3)
    assert insights == []


def test_illustration_finder_returns_empty_when_no_candidates(db):
    d, sid = db
    finder = IllustrationFinder(searcher=_FixedSearcher([]), llm=None)
    insights = finder.run(d, session_id=sid, theme='nothing matches', count=3)
    assert insights == []


def test_illustration_finder_caps_to_count(db):
    d, sid = db
    candidates = [{'resource_id': f'LLS:R{i}', 'title': f't{i}',
                    'authors': 'a', 'sections': [{'snippet': f's{i}', 'section_offset': i}]}
                   for i in range(5)]
    llm = _FixedLLM({'ranked': [
        {'index': i, 'score': 0.9 - i * 0.1, 'rationale': f'r{i}'}
        for i in range(5)
    ]})
    finder = IllustrationFinder(searcher=_FixedSearcher(candidates), llm=llm)
    insights = finder.run(d, session_id=sid, theme='x', count=2)
    assert len(insights) == 2
    assert insights[0].confidence > insights[1].confidence
