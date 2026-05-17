"""I2 — server-side 403 guard on child card-write endpoints + children start
at the 'context' (conversation) phase rather than 'prayer'.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json, tempfile, pytest
import app as app_module


# Override pytest-base-url's autouse _verify_url fixture from conftest.py so
# these endpoint tests don't boot a real Flask server.
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


def _split_into_one_child(client):
    """Create a parent + split into a single child, return (parent_id, child_id)."""
    db = app_module.companion_db
    parent_id = db.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                                   verse_start=21, verse_end=26, genre='epistle')
    payload = {'plan': [
        {'position': 1, 'title': 'But Now', 'verse_start': 21, 'verse_end': 22},
        {'position': 2, 'title': 'All Have Sinned', 'verse_start': 23, 'verse_end': 24},
    ]}
    resp = client.post(f'/study/session/{parent_id}/split',
                        data=json.dumps(payload), content_type='application/json')
    assert resp.status_code == 200, resp.data
    children = db.get_children(parent_id)
    return parent_id, children[0]['id']


def test_child_card_next_returns_403(client):
    _, child_id = _split_into_one_child(client)
    resp = client.post(f'/study/session/{child_id}/card/next',
                        data={'response': 'should not save'})
    assert resp.status_code == 403, resp.data
    # And nothing should have been written to card_responses for the child.
    rows = app_module.companion_db.get_card_responses(child_id)
    assert rows == []


def test_child_card_autosave_returns_403(client):
    _, child_id = _split_into_one_child(client)
    resp = client.post(f'/study/session/{child_id}/card/autosave',
                        data=json.dumps({'content': 'should not save'}),
                        content_type='application/json')
    assert resp.status_code == 403, resp.data
    body = resp.get_json()
    assert body and body.get('error') == 'child_session_read_only'


def test_parent_card_next_still_works(client):
    db = app_module.companion_db
    sid = db.create_session(passage_ref='Romans 3:1-8', book=66, chapter=3,
                             verse_start=1, verse_end=8, genre='epistle')
    resp = client.post(f'/study/session/{sid}/card/next',
                        data={'response': 'A real prayer'})
    # Existing behavior: 302 redirect back to the session view.
    assert resp.status_code == 302, resp.data
    rows = db.get_card_responses(sid)
    assert any(r['content'] == 'A real prayer' for r in rows)


def test_child_session_starts_at_context_phase_not_prayer(client):
    _, child_id = _split_into_one_child(client)
    child = app_module.companion_db.get_session(child_id)
    assert child['current_phase'] == 'context', \
        f"child should start at context, got {child['current_phase']!r}"
