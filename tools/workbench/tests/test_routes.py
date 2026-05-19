"""
Integration tests for /study/ clock + patterns endpoints.

Uses Flask's test client — does NOT start the actual server.
LogosReader/batch reader is not initialized; companion_db is injected directly.

Prior to /companion/ removal this file held 28 tests including 21 for
the legacy /companion/ card UI; those have been removed along with the
routes themselves. The /study/ session-creation endpoint
(/study/session/new) replaces /companion/session/new as the fixture
entry point.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest


@pytest.fixture
def client():
    """Create Flask test client with a temp companion DB.

    Per bug review #7-#8: initializes ALL module-global stores via the
    shared init_all_app_globals helper so routes that touch session_decisions
    / pastor_feedback / accuser_detector / mentor_store don't crash with
    'no such table' from a stale or partial app-module state.
    """
    import app as app_module
    from conftest import init_all_app_globals

    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    init_all_app_globals(tmp.name)
    app_module.app.config['TESTING'] = True

    with app_module.app.test_client() as c:
        yield c

    os.unlink(tmp.name)


def _new_session(client, passage='Romans 1:18-23'):
    """POST /study/session/new and return the int session id from the Location."""
    r = client.post('/study/session/new', data={'passage': passage})
    return int(r.headers['Location'].rstrip('/').split('/')[-1])


def test_clock_endpoint_404_for_missing_session(client):
    """Bug review #3 — was silently returning 200 for nonexistent sessions."""
    r = client.patch('/study/session/99999/clock', json={'elapsed': 100})
    assert r.status_code == 404


def test_clock_endpoint_accepts_elapsed_zero(client):
    """Bug review #4 — elapsed=0 was treated as missing because falsy."""
    sid = _new_session(client)
    r = client.patch(f'/study/session/{sid}/clock', json={'elapsed': 0})
    assert r.status_code == 200
    assert r.get_json().get('ok') is True


def test_clock_endpoint_rejects_missing_elapsed(client):
    sid = _new_session(client)
    r = client.patch(f'/study/session/{sid}/clock', json={})
    assert r.status_code == 400


def test_clock_endpoint_rejects_negative(client):
    sid = _new_session(client)
    r = client.patch(f'/study/session/{sid}/clock', json={'elapsed': -100})
    assert r.status_code == 400


def test_clock_endpoint_rejects_non_int(client):
    sid = _new_session(client)
    r = client.patch(f'/study/session/{sid}/clock', json={'elapsed': 'lots'})
    assert r.status_code == 400


def test_clock_endpoint_rejects_absurdly_large_values(client):
    """Bug review #6: pre-cc9b914 timer wrote 898 years to the DB."""
    sid = _new_session(client)
    # 14 days + 1s — just past the ceiling
    r = client.patch(f'/study/session/{sid}/clock', json={'elapsed': 14 * 24 * 3600 + 1})
    assert r.status_code == 400


def test_clock_endpoint_persists_valid_elapsed(client):
    import app as app_module
    sid = _new_session(client)
    r = client.patch(f'/study/session/{sid}/clock', json={'elapsed': 1234})
    assert r.status_code == 200
    s = app_module.companion_db.get_session(sid)
    assert s['total_elapsed_seconds'] == 1234


def test_study_patterns_handles_garbage_days_param(client):
    """Per bug review #2 — invalid days param previously crashed (500)."""
    r = client.get('/study/patterns?days=x')
    assert r.status_code == 200
    r = client.get('/study/patterns?days=-50')
    assert r.status_code == 200
    r = client.get('/study/patterns?days=99999')
    assert r.status_code == 200
    r = client.get('/study/patterns?days=')
    assert r.status_code == 200
