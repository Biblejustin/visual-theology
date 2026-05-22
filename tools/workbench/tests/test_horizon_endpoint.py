"""The 'to Friday 3pm' countdown was server-rendered once and never ticked
(study.js had no ticker for it), so it froze until a page reload. This
endpoint lets the client refresh the horizon live on its existing 60s timer.
Bug reported 2026-05-22.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import app as app_module


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


@pytest.fixture
def client(monkeypatch):
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    monkeypatch.setenv('COMPANION_DB_PATH', path)
    app_module.app.config['TESTING'] = True
    app_module.startup()
    with app_module.app.test_client() as c:
        yield c
    os.unlink(path)


def test_horizon_endpoint_returns_live_horizon(client):
    sid = app_module.companion_db.create_session(
        'Romans 3:25-26', 66, 3, 25, 26, 'epistle')
    r = client.get(f'/study/session/{sid}/horizon')
    assert r.status_code == 200
    data = r.get_json()
    for key in ('state', 'fraction', 'color', 'label'):
        assert key in data
    assert data['state'] in (
        'in_window', 'before_window', 'lockout', 'preach_day', 'day_off')
    assert data['color'] in ('green', 'amber', 'red', 'lockout', 'muted')
