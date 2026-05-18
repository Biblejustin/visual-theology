import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from companion_tools import get_tool_names, execute_tool


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


@pytest.fixture
def session_ctx():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    sid = d.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                            verse_start=21, verse_end=26, genre='epistle')
    yield {
        'db': d, 'session_id': sid, 'passage_ref': 'Romans 3:21-26',
        'book': 66, 'chapter': 3, 'verse_start': 21, 'verse_end': 26,
        'genre': 'epistle',
    }
    os.unlink(path)


def test_all_seven_tools_registered():
    names = set(get_tool_names())
    expected = {
        'find_illustration', 'find_objections', 'find_counseling_applications',
        'find_wisdom_principles', 'suggest_christ_thread', 'gauge_outline_balance',
        'check_sermon_coherence',
    }
    assert expected.issubset(names), f"missing: {expected - names}"


def test_unknown_required_input_returns_friendly_error(session_ctx):
    r = execute_tool('find_illustration', {}, session_ctx)
    assert 'error' in r


def test_check_sermon_coherence_empty_claim_returns_error(session_ctx):
    r = execute_tool('check_sermon_coherence', {'claim': ''}, session_ctx)
    assert 'error' in r


def test_gauge_outline_balance_with_empty_outline_returns_empty(session_ctx, monkeypatch):
    # Mock the BalanceGauger so it doesn't try to call the real LLM.
    from homiletics_engine import BalanceGauger
    def _empty_run(self, db, *, session_id):
        return []
    monkeypatch.setattr(BalanceGauger, 'run', _empty_run)
    r = execute_tool('gauge_outline_balance', {}, session_ctx)
    assert 'insights' in r
    assert r['insights'] == []
