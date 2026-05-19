"""Tests for cutoff ENFORCEMENT (review #3, after Bryan's design call).

Friday 3pm + weekend cutoffs now block writes/LLM with 423 Locked.
Daily 5pm stays advisory-only (softer family-time signal).
GET endpoints stay open at every cutoff so Bryan can read existing
work into Apple Pages for delivery.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def client(monkeypatch):
    import app as app_module
    from conftest import init_all_app_globals
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    init_all_app_globals(tmp.name)
    app_module.app.config['TESTING'] = True
    # Disable any host-set override that would mask the test.
    monkeypatch.delenv('LOGOS_CUTOFF_DISABLE', raising=False)
    monkeypatch.delenv('LOGOS_CUTOFF_ENFORCE_5PM', raising=False)
    with app_module.app.test_client() as c:
        yield c
    os.unlink(tmp.name)


def _force_cutoff(monkeypatch, app_mod, cutoff_type):
    """Make active_cutoff_type return the given cutoff regardless of clock."""
    monkeypatch.setattr(
        app_mod.study_analytics, 'active_cutoff_type',
        lambda now_utc=None, tz=None: cutoff_type,
    )


def _create_session(client):
    r = client.post('/study/session/new', data={'passage': 'Romans 1:18-23'})
    return int(r.headers['Location'].rstrip('/').split('/')[-1])


# ── Friday 3pm + weekend → 423 on writes ─────────────────────────────

@pytest.mark.parametrize('cutoff', ['friday_3pm', 'weekend_lockout'])
@pytest.mark.parametrize('method,path,body', [
    ('POST',   '/study/session/{sid}/discuss',            {'message': 'hi'}),
    ('POST',   '/study/session/{sid}/card/autosave',      {'content': 'x'}),
    ('POST',   '/study/session/{sid}/annotate',           {'source': 'a', 'starred_text': 'x'}),
    ('POST',   '/study/session/{sid}/notepad',            {'content': 'x'}),
    ('POST',   '/study/session/{sid}/outline/add',        {'content': 'x'}),
    ('POST',   '/study/session/{sid}/word-info',          {'word': 'logos'}),
    ('POST',   '/api/study/session/{sid}/decision',       {'key': 'subject', 'value': 'grace'}),
    ('PATCH',  '/study/session/{sid}/clock',              {'elapsed': 100}),
])
def test_writes_blocked_with_423(client, monkeypatch, cutoff, method, path, body):
    import app as app_module
    sid = _create_session(client)
    _force_cutoff(monkeypatch, app_module, cutoff)
    full = path.format(sid=sid)
    if method == 'POST':
        r = client.post(full, json=body)
    elif method == 'PATCH':
        r = client.patch(full, json=body)
    else:
        r = client.open(full, method=method, json=body)
    assert r.status_code == 423, \
        f'{method} {full} during {cutoff}: got {r.status_code} (expected 423)'
    data = r.get_json()
    assert data['error'] == 'cutoff_active'
    assert data['cutoff_type'] == cutoff


def test_session_creation_blocked_during_cutoff(client, monkeypatch):
    import app as app_module
    _create_session(client)  # baseline session before cutoff fires
    _force_cutoff(monkeypatch, app_module, 'weekend_lockout')
    r = client.post('/study/session/new', data={'passage': 'John 1:1-5'})
    assert r.status_code == 423


# ── Reads stay open (the Apple Pages copy-paste requirement) ─────────

def test_session_view_open_during_cutoff(client, monkeypatch):
    """Bryan must be able to read existing work to copy into Apple Pages."""
    import app as app_module
    sid = _create_session(client)
    _force_cutoff(monkeypatch, app_module, 'weekend_lockout')
    r = client.get(f'/study/session/{sid}')
    assert r.status_code == 200


def test_outline_get_open_during_cutoff(client, monkeypatch):
    import app as app_module
    sid = _create_session(client)
    _force_cutoff(monkeypatch, app_module, 'weekend_lockout')
    r = client.get(f'/study/session/{sid}/outline')
    assert r.status_code == 200


def test_study_index_open_during_cutoff(client, monkeypatch):
    import app as app_module
    _force_cutoff(monkeypatch, app_module, 'weekend_lockout')
    r = client.get('/study/')
    assert r.status_code == 200


def test_patterns_page_open_during_cutoff(client, monkeypatch):
    import app as app_module
    _force_cutoff(monkeypatch, app_module, 'weekend_lockout')
    r = client.get('/study/patterns')
    assert r.status_code == 200


def test_cutoff_dismiss_open_during_cutoff(client, monkeypatch):
    """The dismiss button must work — else the banner can't be cleared."""
    import app as app_module
    _force_cutoff(monkeypatch, app_module, 'weekend_lockout')
    # The event has to exist first. Force-insert one.
    event_id = app_module.study_analytics.log_cutoff_event(
        '/study/', 'weekend_lockout')
    r = client.post(f'/api/cutoff-events/{event_id}/dismiss')
    assert r.status_code in (200, 204)


# ── Daily 5pm stays advisory by default ──────────────────────────────

def test_daily_5pm_does_not_block_by_default(client, monkeypatch):
    import app as app_module
    sid = _create_session(client)
    _force_cutoff(monkeypatch, app_module, 'daily_5pm')
    r = client.patch(f'/study/session/{sid}/clock', json={'elapsed': 100})
    # 200, not 423 — daily 5pm is advisory only by default.
    assert r.status_code == 200


def test_daily_5pm_blocks_when_enforce_env_set(client, monkeypatch):
    import app as app_module
    sid = _create_session(client)
    monkeypatch.setenv('LOGOS_CUTOFF_ENFORCE_5PM', '1')
    _force_cutoff(monkeypatch, app_module, 'daily_5pm')
    r = client.patch(f'/study/session/{sid}/clock', json={'elapsed': 100})
    assert r.status_code == 423


# ── LOGOS_CUTOFF_DISABLE escape hatch ────────────────────────────────

def test_disable_env_bypasses_enforcement(client, monkeypatch):
    import app as app_module
    sid = _create_session(client)
    monkeypatch.setenv('LOGOS_CUTOFF_DISABLE', '1')
    _force_cutoff(monkeypatch, app_module, 'weekend_lockout')
    r = client.patch(f'/study/session/{sid}/clock', json={'elapsed': 100})
    assert r.status_code == 200  # bypassed


# ── No cutoff → no blocking ──────────────────────────────────────────

def test_no_active_cutoff_allows_writes(client, monkeypatch):
    """Sanity: when no cutoff is active, writes work as normal."""
    import app as app_module
    sid = _create_session(client)
    monkeypatch.setattr(
        app_module.study_analytics, 'active_cutoff_type',
        lambda now_utc=None, tz=None: None,
    )
    r = client.patch(f'/study/session/{sid}/clock', json={'elapsed': 100})
    assert r.status_code == 200
