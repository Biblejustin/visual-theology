import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json, tempfile, pytest
import app as app_module
import series_planner as planner_module


# Override pytest-base-url's autouse _verify_url fixture from conftest.py so
# this in-process test doesn't boot a real Flask server (which hard-exits if
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
    # Stub the planner so we don't hit the real LLM.
    fake_plan = [
        planner_module.SeriesPlanRow(position=1, title='But Now', verse_start=21, verse_end=22),
        planner_module.SeriesPlanRow(position=2, title='All Have Sinned', verse_start=23, verse_end=24),
        planner_module.SeriesPlanRow(position=3, title='God Put Forward', verse_start=25, verse_end=26),
        planner_module.SeriesPlanRow(position=4, title='The Heart of the Gospel',
                                      verse_start=21, verse_end=26),
    ]
    monkeypatch.setattr(app_module, 'propose_series_plan',
                         lambda messages, session_chapter, llm=None: fake_plan,
                         raising=False)
    with app_module.app.test_client() as c:
        yield c
    os.unlink(path)


def test_full_split_flow_for_romans_3_21_26(client):
    db = app_module.companion_db
    parent_id = db.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                                   verse_start=21, verse_end=26, genre='epistle')
    # question_id is NOT NULL — pass 0, not None (verified in Task 9).
    db.save_card_response(parent_id, 'read_translate', 0, 'Broad exegesis of Romans 3:21-26')

    # 1) Propose
    resp = client.post(f'/study/session/{parent_id}/split/propose')
    assert resp.status_code == 200
    plan = resp.get_json()['plan']
    assert len(plan) == 4
    assert plan[0]['title'] == 'But Now'

    # 2) Confirm split
    resp = client.post(f'/study/session/{parent_id}/split',
                        data=json.dumps({'plan': plan}),
                        content_type='application/json')
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body['children']) == 3

    # 3) Parent narrowed
    parent = db.get_session(parent_id)
    assert parent['verse_start'] == 21 and parent['verse_end'] == 22
    assert parent['series_title'] == 'But Now'

    # 4) Children created with right verses
    children = db.get_children(parent_id)
    assert [(c['verse_start'], c['verse_end']) for c in children] == [
        (23, 24), (25, 26), (21, 26)]

    # 5) Breadcrumb appears on parent and each child
    for sid in [parent_id] + [c['id'] for c in children]:
        resp = client.get(f'/study/session/{sid}')
        assert b'series-breadcrumb' in resp.data, f'no breadcrumb on session {sid}'

    # 6) Child has its OWN card surface (first-class session) and shows
    #    a 'Part of series' breadcrumb back to the parent for context.
    child = children[0]
    db.save_card_response(child['id'], 'read_translate', 0, 'Child working translation')
    conn = db._conn()
    conn.execute("UPDATE sessions SET current_phase='read_translate' WHERE id=?",
                  (child['id'],))
    conn.commit()
    conn.close()
    resp = client.get(f'/study/session/{child["id"]}')
    assert b'Child working translation' in resp.data
    assert b'Broad exegesis of Romans 3:21-26' not in resp.data  # parent text must not bleed in
    assert b'Part of series' in resp.data

    # 7) Re-split is rejected
    resp = client.post(f'/study/session/{parent_id}/split',
                        data=json.dumps({'plan': plan}),
                        content_type='application/json')
    assert resp.status_code == 400
