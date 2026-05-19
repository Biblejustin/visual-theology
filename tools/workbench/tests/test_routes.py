"""
Integration tests for Flask companion routes.

Uses Flask's test client — does NOT start the actual server.
LogosReader/batch reader is not initialized; companion_db is injected directly.
"""

import os
import sys
import tempfile

# Add workbench and tools directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from companion_db import CompanionDB, PHASES_ORDER
from seed_questions import seed_question_bank

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


def test_clock_endpoint_404_for_missing_session(client):
    """Bug review #3 — was silently returning 200 for nonexistent sessions."""
    r = client.patch('/study/session/99999/clock', json={'elapsed': 100})
    assert r.status_code == 404


def test_clock_endpoint_accepts_elapsed_zero(client):
    """Bug review #4 — elapsed=0 was treated as missing because falsy."""
    r = client.post('/companion/session/new', data={'passage': 'Romans 1:18-23'})
    sid = int(r.headers['Location'].rstrip('/').split('/')[-1])
    r = client.patch(f'/study/session/{sid}/clock', json={'elapsed': 0})
    assert r.status_code == 200
    assert r.get_json().get('ok') is True


def test_clock_endpoint_rejects_missing_elapsed(client):
    r = client.post('/companion/session/new', data={'passage': 'Romans 1:18-23'})
    sid = int(r.headers['Location'].rstrip('/').split('/')[-1])
    r = client.patch(f'/study/session/{sid}/clock', json={})
    assert r.status_code == 400


def test_clock_endpoint_rejects_negative(client):
    r = client.post('/companion/session/new', data={'passage': 'Romans 1:18-23'})
    sid = int(r.headers['Location'].rstrip('/').split('/')[-1])
    r = client.patch(f'/study/session/{sid}/clock', json={'elapsed': -100})
    assert r.status_code == 400


def test_clock_endpoint_rejects_non_int(client):
    r = client.post('/companion/session/new', data={'passage': 'Romans 1:18-23'})
    sid = int(r.headers['Location'].rstrip('/').split('/')[-1])
    r = client.patch(f'/study/session/{sid}/clock', json={'elapsed': 'lots'})
    assert r.status_code == 400


def test_clock_endpoint_rejects_absurdly_large_values(client):
    """Bug review #6: pre-cc9b914 timer wrote 898 years to the DB."""
    r = client.post('/companion/session/new', data={'passage': 'Romans 1:18-23'})
    sid = int(r.headers['Location'].rstrip('/').split('/')[-1])
    # 14 days + 1s — just past the ceiling
    r = client.patch(f'/study/session/{sid}/clock', json={'elapsed': 14 * 24 * 3600 + 1})
    assert r.status_code == 400


def test_clock_endpoint_persists_valid_elapsed(client):
    import app as app_module
    r = client.post('/companion/session/new', data={'passage': 'Romans 1:18-23'})
    sid = int(r.headers['Location'].rstrip('/').split('/')[-1])
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


def _make_session(client, passage='Romans 1:18-23'):
    """Helper: POST /companion/session/new and return (session_id, location)."""
    r = client.post('/companion/session/new', data={'passage': passage})
    location = r.headers.get('Location', '')
    session_id = None
    if '/companion/session/' in location:
        part = location.rstrip('/').split('/')
        try:
            session_id = int(part[-1])
        except ValueError:
            pass
    return session_id, location


# ── Index ────────────────────────────────────────────────────────────────────

def test_companion_index(client):
    r = client.get('/companion/')
    assert r.status_code == 200
    assert b'Sermon Study Companion' in r.data


# ── Session creation ─────────────────────────────────────────────────────────

def test_create_session_redirects(client):
    r = client.post('/companion/session/new', data={'passage': 'Romans 1:18-23'})
    assert r.status_code == 302
    assert '/companion/session/' in r.headers['Location']


def test_create_session_valid_location(client):
    session_id, location = _make_session(client)
    assert session_id is not None, f"Could not extract session id from '{location}'"


# ── Session view ─────────────────────────────────────────────────────────────

def test_session_view(client):
    session_id, location = _make_session(client)
    r = client.get(location)
    assert r.status_code == 200
    assert b'Romans 1:18-23' in r.data


# ── Card flow ────────────────────────────────────────────────────────────────

def test_card_get(client):
    session_id, _ = _make_session(client)
    r = client.get(f'/companion/session/{session_id}/card')
    assert r.status_code == 200
    # Should contain a question text from the prayer phase
    assert b'<div class="card">' in r.data or b'card' in r.data


def test_card_respond(client):
    import app as app_module
    session_id, _ = _make_session(client)

    questions = app_module.companion_db.get_questions('prayer')
    assert questions, "No prayer questions seeded"

    r = client.post(
        f'/companion/session/{session_id}/card/respond',
        data={'question_id': questions[0]['id'], 'response': 'Test prayer response'},
    )
    assert r.status_code == 200


def test_card_respond_skip(client):
    """Submitting without a response (empty) should still advance."""
    import app as app_module
    session_id, _ = _make_session(client)

    questions = app_module.companion_db.get_questions('prayer')
    assert questions

    r = client.post(
        f'/companion/session/{session_id}/card/respond',
        data={'question_id': questions[0]['id'], 'response': ''},
    )
    assert r.status_code == 200


# ── Phase next ───────────────────────────────────────────────────────────────

def test_phase_next(client):
    session_id, _ = _make_session(client)
    r = client.post(f'/companion/session/{session_id}/phase/next')
    assert r.status_code == 200


def test_phase_next_advances_phase(client):
    import app as app_module
    session_id, _ = _make_session(client)

    # Confirm starting phase
    session_before = app_module.companion_db.get_session(session_id)
    assert session_before['current_phase'] == 'prayer'

    client.post(f'/companion/session/{session_id}/phase/next')

    session_after = app_module.companion_db.get_session(session_id)
    assert session_after['current_phase'] == 'text_work'


# ── Outline ──────────────────────────────────────────────────────────────────

def test_outline_get_empty(client):
    session_id, _ = _make_session(client)
    r = client.get(f'/companion/session/{session_id}/outline')
    assert r.status_code == 200


def test_outline_add_node(client):
    session_id, _ = _make_session(client)
    r = client.post(
        f'/companion/session/{session_id}/outline/add',
        data={'content': 'Test main point', 'node_type': 'main_point'},
    )
    assert r.status_code == 200
    assert b'Test main point' in r.data


def test_outline_add_multiple_nodes(client):
    session_id, _ = _make_session(client)
    client.post(
        f'/companion/session/{session_id}/outline/add',
        data={'content': 'First point', 'node_type': 'main_point'},
    )
    r = client.post(
        f'/companion/session/{session_id}/outline/add',
        data={'content': 'Second point', 'node_type': 'main_point'},
    )
    assert r.status_code == 200
    assert b'First point' in r.data
    assert b'Second point' in r.data


# ── Progress ─────────────────────────────────────────────────────────────────

def test_progress(client):
    session_id, _ = _make_session(client)
    r = client.get(f'/companion/session/{session_id}/progress')
    assert r.status_code == 200
    # Should contain progress dots
    assert b'progress-dot' in r.data


# ── Export ───────────────────────────────────────────────────────────────────

def test_export(client):
    session_id, _ = _make_session(client)
    r = client.get(f'/companion/session/{session_id}/export')
    assert r.status_code == 200
    assert b'Romans 1:18-23' in r.data


# ── Timer ────────────────────────────────────────────────────────────────────

def test_timer_update(client):
    session_id, _ = _make_session(client)
    r = client.patch(
        f'/companion/session/{session_id}/timer',
        json={'remaining': 500, 'paused': True},
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data.get('ok') is True


def test_timer_update_persists(client):
    import app as app_module
    session_id, _ = _make_session(client)

    client.patch(
        f'/companion/session/{session_id}/timer',
        json={'remaining': 300, 'paused': True},
    )

    session = app_module.companion_db.get_session(session_id)
    assert session['timer_remaining_seconds'] == 300
    assert session['timer_paused'] == 1


# ── Error cases ──────────────────────────────────────────────────────────────

def test_empty_passage_error(client):
    r = client.post('/companion/session/new', data={'passage': ''})
    assert r.status_code == 302
    location = r.headers.get('Location', '')
    assert 'error' in location


def test_invalid_passage_error(client):
    r = client.post('/companion/session/new', data={'passage': 'not a real reference 999'})
    assert r.status_code == 302
    location = r.headers.get('Location', '')
    assert 'error' in location


def test_discuss_no_message_returns_400(client):
    session_id, _ = _make_session(client)
    r = client.post(
        f'/companion/session/{session_id}/discuss',
        json={},
        content_type='application/json',
    )
    assert r.status_code == 400


def test_session_not_found_redirects(client):
    """Accessing a non-existent session should redirect to companion index."""
    r = client.get('/companion/session/99999')
    assert r.status_code == 302
    assert '/companion/' in r.headers['Location']
