import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json, tempfile, pytest
import app as app_module


# Override pytest-base-url's autouse _verify_url fixture from conftest.py so
# these endpoint tests don't boot a real Flask server (which hard-exits if
# port 5111 is in use). We use Flask's in-process test client instead.
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


def _create_parent(db):
    sid = db.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                             verse_start=21, verse_end=26, genre='epistle')
    return sid


def test_split_creates_children_and_narrows_parent(client):
    db = app_module.companion_db
    parent_id = _create_parent(db)

    payload = {'plan': [
        {'position': 1, 'title': 'But Now', 'verse_start': 21, 'verse_end': 22},
        {'position': 2, 'title': 'All Have Sinned', 'verse_start': 23, 'verse_end': 24},
        {'position': 3, 'title': 'God Put Forward', 'verse_start': 25, 'verse_end': 26},
        {'position': 4, 'title': 'The Heart of the Gospel', 'verse_start': 21, 'verse_end': 26},
    ]}
    resp = client.post(f'/study/session/{parent_id}/split',
                        data=json.dumps(payload), content_type='application/json')
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body['parent_id'] == parent_id
    assert len(body['children']) == 3

    parent = db.get_session(parent_id)
    assert parent['verse_start'] == 21 and parent['verse_end'] == 22
    assert parent['series_position'] == 1
    assert parent['series_title'] == 'But Now'

    children = db.get_children(parent_id)
    assert [c['series_position'] for c in children] == [2, 3, 4]
    assert [c['verse_start'] for c in children] == [23, 25, 21]
    assert [c['verse_end'] for c in children] == [24, 26, 26]
    assert [c['series_title'] for c in children] == [
        'All Have Sinned', 'God Put Forward', 'The Heart of the Gospel']


def test_split_rejects_when_session_already_split(client):
    db = app_module.companion_db
    parent_id = _create_parent(db)
    payload = {'plan': [
        {'position': 1, 'title': 'a', 'verse_start': 21, 'verse_end': 22},
        {'position': 2, 'title': 'b', 'verse_start': 23, 'verse_end': 24},
    ]}
    resp1 = client.post(f'/study/session/{parent_id}/split',
                         data=json.dumps(payload), content_type='application/json')
    assert resp1.status_code == 200
    resp2 = client.post(f'/study/session/{parent_id}/split',
                         data=json.dumps(payload), content_type='application/json')
    assert resp2.status_code == 400
    assert 'already_split' in resp2.get_json()['error']


def test_split_rejects_invalid_plan(client):
    db = app_module.companion_db
    parent_id = _create_parent(db)
    # Position 1 missing
    payload = {'plan': [{'position': 2, 'title': 'b', 'verse_start': 23, 'verse_end': 24}]}
    resp = client.post(f'/study/session/{parent_id}/split',
                        data=json.dumps(payload), content_type='application/json')
    assert resp.status_code == 400
    assert 'missing_position_1' in resp.get_json()['error']
