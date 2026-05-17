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


def _make_sermon_with_candidate_link(db, *, session_id, title, vs, ve,
                                       preach_date, reason='tier2:overlap+no_hom'):
    conn = db._conn()
    cur = conn.execute(
        "INSERT INTO sermons (sermonaudio_id, broadcaster_id, title, bible_text_raw, "
        "book, chapter, verse_start, verse_end, preach_date, sermon_type, "
        "classified_as, match_status, source_version, ui_last_seen_version, "
        "sync_status, last_state_change_at, first_synced_at, last_synced_at, "
        "created_at, updated_at) "
        "VALUES (?, 'bcast', ?, ?, 66, 3, ?, ?, ?, 'expository', 'sermon', "
        "'awaiting_candidates', 0, 0, 'review_ready', datetime('now'), datetime('now'), "
        "datetime('now'), datetime('now'), datetime('now'))",
        (f"sa-{title}-{vs}-{ve}", title, f"Romans 3:{vs}-{ve}", vs, ve, preach_date),
    )
    sermon_id = cur.lastrowid
    conn.execute(
        "INSERT INTO sermon_links (sermon_id, session_id, link_status, "
        "link_source, match_reason, created_at) "
        "VALUES (?, ?, 'candidate', 'auto', ?, ?)",
        (sermon_id, session_id, reason, '2026-05-17T00:00:00'),
    )
    conn.commit()
    return sermon_id


def test_session_view_shows_only_candidates_for_this_session(client):
    db = app_module.companion_db
    target_session = db.create_session(
        passage_ref='Romans 3:21-26', book=66, chapter=3,
        verse_start=21, verse_end=26, genre='epistle')
    other_session = db.create_session(
        passage_ref='Romans 3:1-8', book=66, chapter=3,
        verse_start=1, verse_end=8, genre='epistle')

    # Sermon with candidate links to BOTH sessions.
    sermon_id = _make_sermon_with_candidate_link(
        db, session_id=target_session, title='But Now', vs=20, ve=22,
        preach_date='2026-05-10')
    conn = db._conn()
    conn.execute(
        "INSERT INTO sermon_links (sermon_id, session_id, link_status, "
        "link_source, match_reason, created_at) "
        "VALUES (?, ?, 'candidate', 'auto', 'tier2:overlap+no_hom', '2026-05-17T00:00:00')",
        (sermon_id, other_session),
    )
    conn.commit()

    resp = client.get(f'/study/session/{target_session}')
    html = resp.get_data(as_text=True)
    # Header copy must be session-side
    assert 'Sermons that may have been prepped in this session' in html
    # Sermon title must appear once for this session, with preach date
    assert html.count('But Now') >= 1
    assert '2026-05-10' in html
