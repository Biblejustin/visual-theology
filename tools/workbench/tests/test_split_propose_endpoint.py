import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
import app as app_module
import series_planner as planner_module


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

    def _fake_propose(messages, session_chapter, llm=None):
        return [
            planner_module.SeriesPlanRow(position=1, title='But Now',
                                          verse_start=21, verse_end=22),
            planner_module.SeriesPlanRow(position=2, title='All Have Sinned',
                                          verse_start=23, verse_end=24),
        ]
    monkeypatch.setattr(app_module, 'propose_series_plan', _fake_propose, raising=False)
    with app_module.app.test_client() as c:
        yield c
    os.unlink(path)


def test_propose_returns_plan_from_planner(client):
    db = app_module.companion_db
    sid = db.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                             verse_start=21, verse_end=26, genre='epistle')
    resp = client.post(f'/study/session/{sid}/split/propose')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['plan'][0]['title'] == 'But Now'
    assert body['plan'][1]['verse_start'] == 23


def test_propose_returns_empty_plan_when_no_session(client):
    resp = client.post('/study/session/9999/split/propose')
    assert resp.status_code == 404
