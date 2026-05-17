import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from llm_client import CannedLLMClient
from series_planner import propose_series_plan, SeriesPlanRow


# Override pytest-base-url's autouse _verify_url fixture so these unit tests
# don't trigger conftest.py's Flask-server `base_url` fixture (which hard-exits
# if port 5111 is in use by the PM2 dev server).
@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


def _msgs(*pairs):
    return [{'id': i+1, 'role': r, 'content': c} for i, (r, c) in enumerate(pairs)]


def test_returns_rows_when_llm_returns_valid_plan():
    canned = CannedLLMClient({
        # Match by prompt-prefix hash; series_planner builds the prompt
        # deterministically from messages, so we use a fixture-by-key approach
        # by registering the response under any key that the call() lookup finds.
    })
    # Simpler: build a tiny adapter client that returns a fixed plan.
    class _Fixed:
        def call(self, prompt, schema, **kwargs):
            return {'output': {'sermons': [
                {'position': 1, 'title': 'But Now', 'verse_start': 21, 'verse_end': 22},
                {'position': 2, 'title': 'All Have Sinned', 'verse_start': 23, 'verse_end': 24},
                {'position': 3, 'title': 'God Put Forward', 'verse_start': 25, 'verse_end': 26},
                {'position': 4, 'title': 'The Heart of the Gospel', 'verse_start': 21, 'verse_end': 26},
            ]}, 'input_tokens': 0, 'output_tokens': 0, 'model': 'test'}
    rows = propose_series_plan(_msgs(('user', 'split into 4')), session_chapter=3, llm=_Fixed())
    assert len(rows) == 4
    assert rows[0] == SeriesPlanRow(position=1, title='But Now', verse_start=21, verse_end=22)
    assert rows[3] == SeriesPlanRow(position=4, title='The Heart of the Gospel',
                                     verse_start=21, verse_end=26)


def test_returns_empty_when_llm_returns_error():
    class _Err:
        def call(self, prompt, schema, **kwargs):
            return {'error': 'sdk down'}
    rows = propose_series_plan(_msgs(('user', 'hi')), session_chapter=3, llm=_Err())
    assert rows == []


def test_returns_empty_when_llm_returns_no_sermons():
    class _Empty:
        def call(self, prompt, schema, **kwargs):
            return {'output': {'sermons': []}, 'input_tokens': 0, 'output_tokens': 0, 'model': 'test'}
    rows = propose_series_plan(_msgs(('user', 'hi')), session_chapter=3, llm=_Empty())
    assert rows == []


def test_skips_rows_with_invalid_verse_range():
    class _Bad:
        def call(self, prompt, schema, **kwargs):
            return {'output': {'sermons': [
                {'position': 1, 'title': 'ok', 'verse_start': 1, 'verse_end': 2},
                {'position': 2, 'title': 'bad-start', 'verse_start': -1, 'verse_end': 5},
                {'position': 3, 'title': 'bad-order', 'verse_start': 5, 'verse_end': 2},
                {'position': 4, 'title': 'missing', 'verse_start': None, 'verse_end': None},
            ]}, 'input_tokens': 0, 'output_tokens': 0, 'model': 'test'}
    rows = propose_series_plan(_msgs(('user', 'hi')), session_chapter=3, llm=_Bad())
    assert len(rows) == 1
    assert rows[0].title == 'ok'
