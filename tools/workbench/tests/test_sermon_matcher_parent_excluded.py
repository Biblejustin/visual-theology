import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from sermon_matcher import apply_match_decision_for_sermon


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    yield d
    os.unlink(path)


def _insert_sermon(db, *, title, vs, ve, preach_date):
    conn = db._conn()
    cur = conn.execute(
        "INSERT INTO sermons (sermonaudio_id, broadcaster_id, title, bible_text_raw, "
        "book, chapter, verse_start, verse_end, preach_date, sermon_type, "
        "classified_as, match_status, source_version, ui_last_seen_version, "
        "sync_status, last_state_change_at, first_synced_at, last_synced_at, "
        "created_at, updated_at) "
        "VALUES (?, 'bcast', ?, ?, 66, 3, ?, ?, ?, 'expository', 'sermon', "
        "'unmatched', 0, 0, 'review_ready', datetime('now'), datetime('now'), "
        "datetime('now'), datetime('now'), datetime('now'))",
        (f"sa-{title}-{vs}-{ve}", title, f"Romans 3:{vs}-{ve}", vs, ve, preach_date),
    )
    sermon_id = cur.lastrowid
    conn.execute("INSERT INTO sermon_passages (sermon_id, rank, book, chapter_start, "
                  "verse_start, chapter_end, verse_end, created_at) "
                  "VALUES (?, 1, 66, 3, ?, 3, ?, datetime('now'))",
                  (sermon_id, vs, ve))
    conn.commit()
    return sermon_id


def test_parent_with_children_is_not_a_match_candidate(db):
    parent_id = db.create_session(passage_ref='Romans 3:21-22', book=66, chapter=3,
                                   verse_start=21, verse_end=22, genre='epistle')
    child_id = db.create_session(passage_ref='Romans 3:23-24', book=66, chapter=3,
                                  verse_start=23, verse_end=24, genre='epistle')
    conn = db._conn()
    conn.execute("UPDATE sessions SET created_at='2026-05-01 09:00:00', "
                  "series_position=1, series_title='But Now', "
                  "last_homiletical_activity_at='2026-05-08 12:00:00' WHERE id=?",
                  (parent_id,))
    conn.execute("UPDATE sessions SET created_at='2026-05-14 09:00:00', "
                  "parent_session_id=?, series_position=2, "
                  "series_title='All Have Sinned', "
                  "last_homiletical_activity_at='2026-05-15 12:00:00' WHERE id=?",
                  (parent_id, child_id))
    conn.commit()

    sermon_id = _insert_sermon(db, title='All Have Sinned', vs=23, ve=24,
                                preach_date='2026-05-17')
    decision = apply_match_decision_for_sermon(db, sermon_id)
    assert decision.action == 'auto_link'
    assert decision.auto_link_target.session_id == child_id
    # Parent must not appear anywhere in the decision (auto_link or candidates).
    candidate_ids = {c.session_id for c in decision.candidates}
    assert parent_id not in candidate_ids
    assert decision.auto_link_target.session_id != parent_id


def test_standalone_session_still_matches_when_no_children_exist(db):
    sid = db.create_session(passage_ref='Romans 3:1-8', book=66, chapter=3,
                             verse_start=1, verse_end=8, genre='epistle')
    conn = db._conn()
    conn.execute(
        "UPDATE sessions SET created_at='2026-05-01 09:00:00', "
        "last_homiletical_activity_at='2026-05-08 12:00:00' WHERE id=?",
        (sid,),
    )
    conn.commit()
    sermon_id = _insert_sermon(db, title='Not Performance', vs=1, ve=8,
                                preach_date='2026-05-10')
    decision = apply_match_decision_for_sermon(db, sermon_id)
    assert decision.action == 'auto_link'
    assert decision.auto_link_target.session_id == sid
