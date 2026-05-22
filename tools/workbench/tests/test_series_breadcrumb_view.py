import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
import app as app_module


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    """Override pytest-base-url's autouse _verify_url to avoid starting the
    Flask subprocess (and port-5111 collision check) for in-process tests."""
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


def _seed_split(db):
    parent_id = db.create_session(passage_ref='Romans 3:21-22', book=66, chapter=3,
                                   verse_start=21, verse_end=22, genre='epistle')
    conn = db._conn()
    conn.execute(
        "UPDATE sessions SET series_position=1, series_title='But Now' WHERE id=?",
        (parent_id,))
    conn.commit()
    conn.close()
    db.save_card_response(parent_id, 'read_translate', 0, 'Broad exegesis text…')
    child_id = db.create_session(passage_ref='Romans 3:23-24', book=66, chapter=3,
                                  verse_start=23, verse_end=24, genre='epistle')
    conn = db._conn()
    conn.execute(
        "UPDATE sessions SET parent_session_id=?, series_position=2, "
        "series_title='All Have Sinned' WHERE id=?", (parent_id, child_id))
    conn.commit()
    conn.close()
    return parent_id, child_id


def test_breadcrumb_renders_siblings_on_parent_view(client):
    parent_id, child_id = _seed_split(app_module.companion_db)
    resp = client.get(f'/study/session/{parent_id}')
    html = resp.get_data(as_text=True)
    assert 'But Now' in html and 'All Have Sinned' in html
    assert 'series-breadcrumb' in html  # CSS hook


def test_child_view_uses_own_cards_and_shows_series_breadcrumb(client):
    """Children are first-class: their card view loads their OWN data
    (not the parent's) and shows a 'Part of series' link back to the
    parent for context. Previously children inherited the parent's
    card text read-only — that broke Bryan's actual weekly workflow
    where each week's preached sermon is a child."""
    parent_id, child_id = _seed_split(app_module.companion_db)
    db = app_module.companion_db
    db.save_card_response(child_id, 'read_translate', 0, 'Child-own translation')
    conn = db._conn()
    conn.execute(
        "UPDATE sessions SET current_phase='read_translate' WHERE id=?", (child_id,))
    conn.commit()
    conn.close()
    resp = client.get(f'/study/session/{child_id}')
    html = resp.get_data(as_text=True)
    assert 'Child-own translation' in html
    assert 'Broad exegesis text' not in html  # parent's text must NOT bleed in
    assert 'Part of series' in html
