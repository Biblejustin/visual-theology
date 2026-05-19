"""Regression tests for the four bugs caught in the 2026-05-18 review:

1. Scheduled sermon sync called _make_sermonaudio_client by name after
   the helper moved to bp_sermons. Silent failure (caught by the broad
   except + logged only). Cron quietly stopped working.

2. /api/study/session/<id>/decision crashed with AttributeError on
   typed JSON like {"key": 1, "value": "x"} because .strip() ran on a
   non-string. Caller saw a 500 instead of a clean 400.

3. app_secrets.get_secret raised when the macOS Keychain was locked or
   unavailable (PM2 daemon, SSH context). The env-var fallback never
   ran. Every secret-using route 500'd.

4. /sermons/<id>/reanalyze built the LLM client BEFORE confirming the
   sermon existed. Missing IDs surfaced unrelated secret/client errors
   instead of a clean 404.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ── #1 Scheduled sermon sync ─────────────────────────────────────────

def test_scheduled_sermon_sync_uses_bp_sermons_helpers(monkeypatch):
    """Regression: app.py:_scheduled_sermon_sync used to call
    _make_sermonaudio_client by name after the helper moved to
    bp_sermons. It now imports from bp_sermons. This test patches the
    helper there and confirms the cron body finds it."""
    import app as app_module
    import bp_sermons

    calls = []

    class FakeClient:
        pass

    monkeypatch.setattr(bp_sermons, '_make_sermonaudio_client',
                        lambda: calls.append('client') or FakeClient())
    monkeypatch.setattr(bp_sermons, '_broadcaster_id',
                        lambda: calls.append('broadcaster') or 'fake-id')

    # Stub the work — we're testing the dispatch, not the side effects.
    monkeypatch.setattr('sermonaudio_sync.run_sync',
                        lambda db, client, broadcaster_id, trigger: calls.append(f'run_sync:{trigger}'))
    monkeypatch.setattr('sermon_matcher.dispatch_matching',
                        lambda db: calls.append('dispatch_matching'))
    monkeypatch.setattr('sermon_analyzer.dispatch_pending_analyses',
                        lambda db, llm_client=None: calls.append('dispatch_pending_analyses'))

    # Make get_db a no-op (returns something truthy).
    monkeypatch.setattr('db_provider.get_db', lambda: object())

    # _scheduled_sermon_sync used to catch + log + swallow. After the
    # fix it re-raises so silent regressions like this one are loud.
    app_module._scheduled_sermon_sync()

    # Confirm the cron walked the full sync → match → analyze chain.
    assert 'client' in calls
    assert 'broadcaster' in calls
    assert 'run_sync:cron' in calls
    assert 'dispatch_matching' in calls
    assert 'dispatch_pending_analyses' in calls


# ── #2 Decision endpoint typed-JSON ──────────────────────────────────

@pytest.fixture
def client():
    import app as app_module
    from conftest import init_all_app_globals
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    init_all_app_globals(tmp.name)
    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as c:
        yield c
    os.unlink(tmp.name)


def test_decision_endpoint_returns_400_on_typed_key(client):
    """Regression: {'key': 1, 'value': 'x'} used to crash with
    AttributeError: 'int' object has no attribute 'strip'."""
    r = client.post('/api/study/session/1/decision',
                    json={'key': 1, 'value': 'something'})
    assert r.status_code == 400
    assert 'string' in r.get_json().get('error', '').lower()


def test_decision_endpoint_returns_400_on_typed_value(client):
    r = client.post('/api/study/session/1/decision',
                    json={'key': 'subject', 'value': 99})
    assert r.status_code == 400


def test_decision_endpoint_returns_400_on_missing_keys(client):
    """Empty body still gets the clean 400, not a 500."""
    r = client.post('/api/study/session/1/decision', json={})
    assert r.status_code == 400


# ── #3 Secret loader keyring failure ─────────────────────────────────

def test_get_secret_falls_back_to_env_when_keyring_raises(monkeypatch):
    """Regression: a locked/unavailable Keychain used to raise out of
    get_secret, so the env-var fallback never ran."""
    import app_secrets

    class BrokenKeyring:
        def get_password(self, service, name):
            raise RuntimeError('keychain locked')

    monkeypatch.setattr(app_secrets, '_kr', BrokenKeyring())
    monkeypatch.setenv('TEST_FALLBACK_SECRET', 'from-env')

    # Should not raise; should fall through to env.
    assert app_secrets.get_secret('whatever', 'TEST_FALLBACK_SECRET') == 'from-env'


def test_get_secret_returns_empty_when_keyring_raises_and_no_env(monkeypatch):
    import app_secrets

    class BrokenKeyring:
        def get_password(self, service, name):
            raise RuntimeError('keychain locked')

    monkeypatch.setattr(app_secrets, '_kr', BrokenKeyring())
    monkeypatch.delenv('NEVER_SET_TEST_SECRET', raising=False)

    assert app_secrets.get_secret('whatever', 'NEVER_SET_TEST_SECRET') == ''


def test_get_secret_uses_keyring_when_it_works(monkeypatch):
    """Don't regress the happy path while we're in here."""
    import app_secrets

    class WorkingKeyring:
        def get_password(self, service, name):
            return 'from-keychain'

    monkeypatch.setattr(app_secrets, '_kr', WorkingKeyring())
    assert app_secrets.get_secret('whatever', 'IGNORED_ENV') == 'from-keychain'


# ── #4 Reanalyze validates before touching secrets ───────────────────

def test_reanalyze_returns_404_for_missing_sermon_without_touching_secrets(client, monkeypatch):
    """Regression: missing-id used to build the AnthropicClient first,
    so a Keychain failure surfaced instead of the clean 404."""
    # Sabotage the secret loader. If 404 fires BEFORE secrets are
    # touched (the fix), this never gets called and the test passes.
    # If validation order is wrong (the bug), this raises and we get a
    # 500 instead of 404.
    import app_secrets
    monkeypatch.setattr(app_secrets, 'anthropic_api_key',
                        lambda: (_ for _ in ()).throw(RuntimeError('secrets exploded')))

    r = client.post('/sermons/99999/reanalyze')
    assert r.status_code == 404
