"""I1 — remap_parent_links_after_split: move sermon_links from a split parent
to the best-matching child (or leave on parent if it matches better/none does).
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import tempfile, pytest
from companion_db import CompanionDB
from sermon_matcher import remap_parent_links_after_split


# Override pytest-base-url's autouse _verify_url fixture from conftest.py so
# these unit tests don't boot a real Flask server.
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


def _insert_sermon(db, *, title, vs, ve, preach_date, book=66, chapter=3):
    """Insert a sermon row with NOT NULL columns satisfied. Mirrors the helper
    in test_sermon_matcher_parent_excluded.py.
    """
    conn = db._conn()
    cur = conn.execute(
        "INSERT INTO sermons (sermonaudio_id, broadcaster_id, title, bible_text_raw, "
        "book, chapter, verse_start, verse_end, preach_date, sermon_type, "
        "classified_as, match_status, source_version, ui_last_seen_version, "
        "sync_status, last_state_change_at, first_synced_at, last_synced_at, "
        "created_at, updated_at) "
        "VALUES (?, 'bcast', ?, ?, ?, ?, ?, ?, ?, 'expository', 'sermon', "
        "'unmatched', 0, 0, 'review_ready', datetime('now'), datetime('now'), "
        "datetime('now'), datetime('now'), datetime('now'))",
        (f"sa-{title}-{vs}-{ve}", title, f"Romans {chapter}:{vs}-{ve}",
         book, chapter, vs, ve, preach_date),
    )
    sermon_id = cur.lastrowid
    conn.commit()
    conn.close()
    return sermon_id


def _link(db, sermon_id, session_id, status='active', source='auto',
          reason='tier1:exact+0d'):
    conn = db._conn()
    conn.execute(
        "INSERT INTO sermon_links (sermon_id, session_id, link_status, link_source, "
        "match_reason, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (sermon_id, session_id, status, source, reason),
    )
    conn.commit()
    conn.close()


def _links_for(db, sermon_id):
    conn = db._conn()
    rows = conn.execute(
        "SELECT session_id, link_status, link_source, match_reason "
        "FROM sermon_links WHERE sermon_id = ?",
        (sermon_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _split_parent_into(db, parent_id, narrowed_vs, narrowed_ve, child_ranges):
    """Mimic the split-endpoint mutation: narrow the parent, insert children."""
    conn = db._conn()
    conn.execute(
        "UPDATE sessions SET verse_start=?, verse_end=?, series_position=1, "
        "series_title='Part 1' WHERE id=?",
        (narrowed_vs, narrowed_ve, parent_id),
    )
    parent = conn.execute("SELECT * FROM sessions WHERE id=?", (parent_id,)).fetchone()
    child_ids = []
    for pos, (vs, ve) in enumerate(child_ranges, start=2):
        cur = conn.execute(
            "INSERT INTO sessions (passage_ref, book, chapter, verse_start, "
            "verse_end, genre, parent_session_id, series_position, series_title, "
            "current_phase, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'context', 'active', "
            "datetime('now'), datetime('now'))",
            (f"Romans {parent['chapter']}:{vs}-{ve}", parent['book'],
             parent['chapter'], vs, ve, parent['genre'], parent_id,
             pos, f'Part {pos}'),
        )
        child_ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    return child_ids


def test_link_to_sermon_matching_child_range_moves_to_child(db):
    parent_id = db.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                                   verse_start=21, verse_end=26, genre='epistle')
    sermon_id = _insert_sermon(db, title='All Have Sinned', vs=23, ve=24,
                                preach_date='2026-05-17')
    _link(db, sermon_id, parent_id, status='active')
    # Split: parent narrows to 21-22, child covers 23-24.
    child_ids = _split_parent_into(db, parent_id, 21, 22, [(23, 24), (25, 26)])

    remap_parent_links_after_split(db, parent_id)

    links = _links_for(db, sermon_id)
    assert len(links) == 1, links
    assert links[0]['session_id'] == child_ids[0]
    assert links[0]['link_status'] == 'active'
    assert 'remapped from parent after split' in links[0]['match_reason']


def test_link_to_sermon_matching_parent_narrowed_range_stays_on_parent(db):
    parent_id = db.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                                   verse_start=21, verse_end=26, genre='epistle')
    # Sermon's range matches the parent's NEW narrowed range.
    sermon_id = _insert_sermon(db, title='But Now', vs=21, ve=22,
                                preach_date='2026-05-17')
    _link(db, sermon_id, parent_id, status='active')
    _split_parent_into(db, parent_id, 21, 22, [(23, 24), (25, 26)])

    remap_parent_links_after_split(db, parent_id)

    links = _links_for(db, sermon_id)
    assert len(links) == 1
    assert links[0]['session_id'] == parent_id
    # Match reason should be unchanged (no remap suffix appended).
    assert 'remapped' not in links[0]['match_reason']


def test_link_to_sermon_not_matching_any_member_stays_on_parent(db):
    parent_id = db.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                                   verse_start=21, verse_end=26, genre='epistle')
    # Sermon is on a completely different chapter — no overlap with anyone.
    sermon_id = _insert_sermon(db, title='Mystery', vs=1, ve=8,
                                preach_date='2026-05-17', chapter=8)
    _link(db, sermon_id, parent_id, status='active')
    _split_parent_into(db, parent_id, 21, 22, [(23, 24)])

    remap_parent_links_after_split(db, parent_id)

    links = _links_for(db, sermon_id)
    assert len(links) == 1
    assert links[0]['session_id'] == parent_id
    assert 'remapped' not in links[0]['match_reason']


def test_candidate_links_are_remapped_too(db):
    parent_id = db.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                                   verse_start=21, verse_end=26, genre='epistle')
    sermon_id = _insert_sermon(db, title='All Have Sinned', vs=23, ve=24,
                                preach_date='2026-05-17')
    _link(db, sermon_id, parent_id, status='candidate', source='auto',
          reason='tier2:overlap+0d')
    child_ids = _split_parent_into(db, parent_id, 21, 22, [(23, 24)])

    remap_parent_links_after_split(db, parent_id)

    links = _links_for(db, sermon_id)
    assert len(links) == 1
    assert links[0]['session_id'] == child_ids[0]
    assert links[0]['link_status'] == 'candidate'
    assert 'remapped from parent after split' in links[0]['match_reason']


def test_unique_constraint_handled_when_link_already_exists_on_target_child(db):
    parent_id = db.create_session(passage_ref='Romans 3:21-26', book=66, chapter=3,
                                   verse_start=21, verse_end=26, genre='epistle')
    sermon_id = _insert_sermon(db, title='All Have Sinned', vs=23, ve=24,
                                preach_date='2026-05-17')
    _link(db, sermon_id, parent_id, status='active', reason='parent-existing')
    child_ids = _split_parent_into(db, parent_id, 21, 22, [(23, 24)])
    # Pre-existing rejected link on the child — UNIQUE(sermon_id, session_id)
    # means INSERT OR IGNORE should silently no-op.
    _link(db, sermon_id, child_ids[0], status='rejected', source='manual',
          reason='child-preexisting-rejected')

    remap_parent_links_after_split(db, parent_id)

    links = _links_for(db, sermon_id)
    # Parent link should be deleted; the existing child row should be untouched.
    session_ids = {l['session_id']: l for l in links}
    assert parent_id not in session_ids, \
        f"parent row should have been deleted, got: {links}"
    assert child_ids[0] in session_ids
    # The pre-existing child row was preserved (INSERT OR IGNORE didn't overwrite).
    assert session_ids[child_ids[0]]['link_status'] == 'rejected'
    assert session_ids[child_ids[0]]['match_reason'] == 'child-preexisting-rejected'
