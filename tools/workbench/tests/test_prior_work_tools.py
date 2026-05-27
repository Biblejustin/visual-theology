"""Tests for prior-work recall tools (sessions + sermons + coach review).

Spec: docs/superpowers/specs/2026-05-27-prior-work-recall-design.md

Tools under test:
  list_prior_sessions / search_prior_sessions / read_prior_session
  list_prior_sermons  / search_prior_sermons  / read_prior_sermon

All six accept scope='series' (default), 'passage', or 'all'. Series uses
db.get_series(); sermons are matched via sermon_links.link_status='active'
joined to series-member sessions.
"""

import os
import sys
import json
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from companion_db import CompanionDB
from session_decisions import SessionDecisionStore
import prior_work_tools as pwt


# ─────────────────────────── Fixtures ─────────────────────────────


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


@pytest.fixture
def db(db_path):
    d = CompanionDB(db_path)
    d.init_db()
    # SessionDecisionStore creates its own table — ensure it exists.
    sd = SessionDecisionStore(db_path)
    sd.init_db()
    return d


def _now():
    return "2026-05-27T10:00:00"


def _insert_session(conn, *, sid, passage_ref, book, chapter, verse_start, verse_end,
                     parent=None, position=None, series_title=None, phase='exegesis'):
    conn.execute(
        "INSERT INTO sessions (id, passage_ref, book, chapter, verse_start, verse_end, "
        "  genre, current_phase, timer_remaining_seconds, status, created_at, updated_at, "
        "  parent_session_id, series_position, series_title) "
        "VALUES (?,?,?,?,?,?, 'epistle', ?, 900, 'active', ?, ?, ?, ?, ?)",
        (sid, passage_ref, book, chapter, verse_start, verse_end,
         phase, _now(), _now(), parent, position, series_title),
    )


def _insert_message(conn, *, sid, role, content, phase='exegesis'):
    conn.execute(
        "INSERT INTO conversation_messages (session_id, phase, role, content, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (sid, phase, role, content, _now()),
    )


def _insert_outline(conn, *, sid, type_, content, verse_ref=None):
    conn.execute(
        "INSERT INTO outline_nodes (session_id, type, content, verse_ref, "
        "  created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (sid, type_, content, verse_ref, _now(), _now()),
    )


def _insert_notepad(conn, *, sid, phase, content):
    conn.execute(
        "INSERT INTO card_notepads (session_id, phase, content, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (sid, phase, content, _now()),
    )


def _insert_annotation(conn, *, sid, phase, source, starred_text, note):
    conn.execute(
        "INSERT INTO card_annotations (session_id, phase, source, starred_text, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (sid, phase, source, starred_text, note),
    )


def _insert_decision(conn, *, sid, key, value):
    conn.execute(
        "INSERT INTO session_decisions (session_id, key, value, committed_at, modified_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (sid, key, value, _now(), _now()),
    )


def _insert_sermon(conn, *, sid, title, preach_date, book, chapter,
                    verse_start, verse_end, series='Romans Sermons',
                    sync_status='review_ready', transcript='', duration=2160):
    conn.execute(
        "INSERT INTO sermons (id, sermonaudio_id, broadcaster_id, title, "
        "  series, preach_date, duration_seconds, book, chapter, verse_start, verse_end, "
        "  transcript_text, classified_as, sync_status, match_status, "
        "  last_state_change_at, first_synced_at, last_synced_at, "
        "  created_at, updated_at) "
        "VALUES (?, ?, 'bx', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sermon', ?, 'matched', "
        "        ?, ?, ?, ?, ?)",
        (sid, f"sa-{sid}", title, series, preach_date, duration,
         book, chapter, verse_start, verse_end, transcript, sync_status,
         _now(), _now(), _now(), _now(), _now()),
    )


def _insert_sermon_link(conn, *, sermon_id, session_id, status='active', source='auto'):
    conn.execute(
        "INSERT INTO sermon_links (sermon_id, session_id, link_status, "
        "  link_source, match_reason, created_at) "
        "VALUES (?, ?, ?, ?, 'passage_overlap', ?)",
        (sermon_id, session_id, status, source, _now()),
    )


def _insert_review(conn, *, sermon_id, one_change='Cut the Hebrews 9 excursus by 4 min',
                    helpers='blood-imagery metaphor;',
                    hurters='6-min Hebrews 9 excursus;',
                    transcript_hash='abc'):
    conn.execute(
        "INSERT INTO sermon_reviews (sermon_id, analyzer_version, homiletics_core_version, "
        "  analyzed_transcript_hash, source_version_at_analysis, "
        "  movement_clarity, movement_rationale, "
        "  application_landing, christ_thread_score, exegetical_grounding, "
        "  actual_duration_seconds, "
        "  top_impact_helpers, top_impact_hurters, "
        "  one_change_for_next_sunday, computed_at) "
        "VALUES (?, 'v1', 'v1', ?, 1, "
        "        'river', 'Argument flowed cleanly.', "
        "        'pressed', 'explicit', 'grounded', "
        "        2160, ?, ?, ?, ?)",
        (sermon_id, transcript_hash, helpers, hurters, one_change, _now()),
    )


def _ensure_analysis_run(conn, sermon_id):
    """Insert an analysis_runs row for `sermon_id` if none exists, return its id."""
    rid = f'run-{sermon_id}'
    existing = conn.execute(
        "SELECT id FROM analysis_runs WHERE id = ?", (rid,),
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO analysis_runs (id, sermon_id, run_type, prompt_version, "
            "  model_name, is_active, status, created_at) "
            "VALUES (?, ?, 'review', 'v1', 'opus', 1, 'completed', ?)",
            (rid, sermon_id, _now()),
        )
    return rid


def _insert_moment(conn, *, sermon_id, dimension='application_specificity',
                    valence='positive',
                    excerpt='Concrete Tuesday-morning example',
                    rationale='Pressed application onto a real situation',
                    section_role='application',
                    start_seg=0, end_seg=0, moment_rank=1):
    rid = _ensure_analysis_run(conn, sermon_id)
    conn.execute(
        "INSERT INTO sermon_moments (sermon_id, analysis_run_id, start_segment_index, "
        "  end_segment_index, start_ms, end_ms, sermon_position_pct, excerpt_text, "
        "  dimension_key, section_role, valence, confidence, impact, moment_rank, "
        "  rationale, prompt_version, created_at) "
        "VALUES (?, ?, ?, ?, 0, 1000, 0.1, ?, ?, ?, ?, 0.9, 'moderate', ?, ?, 'v1', ?)",
        (sermon_id, rid, start_seg, end_seg, excerpt, dimension,
         section_role, valence, moment_rank, rationale, _now()),
    )


def _insert_flag(conn, *, sermon_id, flag_type='length_drift', severity='note',
                  excerpt='Hebrews 9 excursus', rationale='Long detour from Romans 3'):
    conn.execute(
        "INSERT INTO sermon_flags (sermon_id, flag_type, severity, excerpt, "
        "  rationale, analyzer_version, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'v1', ?)",
        (sermon_id, flag_type, severity, excerpt, rationale, _now()),
    )


def _seed_romans_series(db):
    """Sessions 20-23 = Romans 3 wrap-up series. Session 99 = unrelated standalone.

    Sermons 205-207 are the preached sermons for sessions 20-22. Sermon 205
    has an extra noisy 'candidate' link to session 23 (mirrors real DB state).
    Sermon 205 is analyzed; sermon 207 is not.
    """
    with db._safe_write() as conn:
        # Parent (session 20) + 3 children
        _insert_session(conn, sid=20, passage_ref='Romans 3:21-22', book=66, chapter=3,
                        verse_start=21, verse_end=22, position=1,
                        series_title='But Now')
        _insert_session(conn, sid=21, passage_ref='Romans 3:23-24', book=66, chapter=3,
                        verse_start=23, verse_end=24, parent=20, position=2,
                        series_title='All Have Sinned')
        _insert_session(conn, sid=22, passage_ref='Romans 3:25-26', book=66, chapter=3,
                        verse_start=25, verse_end=26, parent=20, position=3,
                        series_title='God Put Forward')
        _insert_session(conn, sid=23, passage_ref='Romans 3:21-26', book=66, chapter=3,
                        verse_start=21, verse_end=26, parent=20, position=4,
                        series_title='The Heart of the Gospel', phase='observation')
        # Standalone — different passage
        _insert_session(conn, sid=99, passage_ref='Romans 8:1-4', book=66, chapter=8,
                        verse_start=1, verse_end=4)

        # Prep work for session 20 (rich)
        _insert_message(conn, sid=20, role='user', content='What is the force of νυνὶ δέ?')
        _insert_message(conn, sid=20, role='assistant',
                        content='The temporal-logical pivot from indictment to gospel.')
        _insert_outline(conn, sid=20, type_='main_point',
                        content='But now — the eschatological pivot has arrived')
        _insert_decision(conn, sid=20, key='subject',
                         value='The eschatological hinge of Romans')
        _insert_decision(conn, sid=20, key='fcf',
                         value='We live as if still under condemnation')
        _insert_notepad(conn, sid=20, phase='exegesis',
                        content='νυνὶ δέ — strong adversative + temporal force')
        _insert_annotation(conn, sid=20, phase='exegesis', source='BDAG',
                           starred_text='νυνί', note='temporal-logical hinge per Moo')

        # Prep work for session 21
        _insert_outline(conn, sid=21, type_='main_point',
                        content='All have sinned — universal scope')

        # Prep work for session 22
        _insert_message(conn, sid=22, role='user',
                        content='Is ἱλαστήριον propitiation or mercy seat?')
        _insert_outline(conn, sid=22, type_='main_point',
                        content='God publicly displays Christ as propitiation')

        # Sermons
        _insert_sermon(conn, sid=205, title='But Now', preach_date='2026-05-10',
                       book=66, chapter=3, verse_start=20, verse_end=22,
                       transcript='Brothers and sisters, what a pivot. But now. The hinge. '
                                  'Universal indictment behind us; righteousness ahead. '
                                  'God did not delay forever. He acted in Christ.',
                       sync_status='review_ready')
        _insert_sermon(conn, sid=206, title='Justified Freely by Grace',
                       preach_date='2026-05-17',
                       book=66, chapter=3, verse_start=23, verse_end=24,
                       transcript='All have sinned. All. No exception. And yet — gift.',
                       sync_status='analysis_pending')
        _insert_sermon(conn, sid=207, title='God Put Forward',
                       preach_date='2026-05-24',
                       book=66, chapter=3, verse_start=25, verse_end=26,
                       transcript='Propitiation — not divine child abuse but covenant '
                                  'love satisfying covenant justice. Christ propitiates.',
                       sync_status='review_ready')

        # Links — mostly clean, with one noisy candidate link.
        _insert_sermon_link(conn, sermon_id=205, session_id=20, status='active')
        _insert_sermon_link(conn, sermon_id=206, session_id=21, status='active')
        _insert_sermon_link(conn, sermon_id=207, session_id=22, status='active')
        # Noise: an extra candidate link to the current wrap-up session.
        _insert_sermon_link(conn, sermon_id=205, session_id=23, status='candidate')

        # Coach reviews — sermon 205 analyzed, sermon 207 NOT yet, sermon 206 pending
        _insert_review(conn, sermon_id=205,
                       one_change='Open with a congregant question, not a Greek paradigm',
                       helpers='strong opening tension; vivid courtroom imagery',
                       hurters='abstract exegetical aside in the middle')
        _insert_review(conn, sermon_id=207,
                       one_change='Cut the Hebrews 9 excursus to 2 minutes',
                       helpers='blood-imagery extended metaphor',
                       hurters='6-min Hebrews 9 excursus broke movement')
        _insert_moment(conn, sermon_id=205, dimension='burden_clarity',
                       valence='positive', excerpt='Have you ever stood at the bottom?',
                       rationale='Strong tension-setter — congregational hook',
                       section_role='intro', start_seg=0, end_seg=2, moment_rank=1)
        _insert_moment(conn, sermon_id=207, dimension='christ_thread_score',
                       valence='positive', excerpt='Christ as propitiation',
                       rationale='Explicit Christ thread',
                       section_role='exposition', start_seg=10, end_seg=12, moment_rank=1)
        _insert_flag(conn, sermon_id=207, flag_type='length_drift', severity='warn',
                     excerpt='Hebrews 9:11-14 excursus runs 6 minutes',
                     rationale='Detour from Romans 3 — breaks movement')


# ─────────────────────────── Tests: sessions ─────────────────────────────


def test_list_prior_sessions_series_scope_returns_siblings(db):
    _seed_romans_series(db)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.list_prior_sessions({'scope': 'series'}, ctx)
    assert out['scope_used'] == 'series'
    ids = [s['session_id'] for s in out['sessions']]
    assert ids == [20, 21, 22]  # series_position order, current excluded
    assert all('passage_ref' in s and 'series_position' in s for s in out['sessions'])
    # The richest session (20) should report has_decisions and has_outline.
    s20 = next(s for s in out['sessions'] if s['session_id'] == 20)
    assert s20['has_decisions'] is True
    assert s20['has_outline'] is True
    assert s20['message_count'] >= 2


def test_list_prior_sessions_passage_scope_when_no_series(db):
    """Standalone session (99) has no series — passage scope should hit nothing
    by passage (it's Romans 8, no overlapping prior). 'all' scope works."""
    _seed_romans_series(db)
    ctx = {'session_id': 99, 'book': 66, 'chapter': 8,
           'verse_start': 1, 'verse_end': 4, 'db': db}
    series_out = pwt.list_prior_sessions({'scope': 'series'}, ctx)
    assert series_out['sessions'] == []
    assert 'hint' in series_out
    all_out = pwt.list_prior_sessions({'scope': 'all'}, ctx)
    # Should include sessions 20-23 (any other sessions exist), not 99.
    ids = {s['session_id'] for s in all_out['sessions']}
    assert ids == {20, 21, 22, 23}


def test_list_prior_sessions_passage_scope_requires_verse_overlap(db):
    """From session 23 (Rom 3:21-26), passage scope must only return sessions
    that actually OVERLAP the verse range, not anything in the same chapter."""
    _seed_romans_series(db)
    with db._safe_write() as conn:
        # 3:10-18 — same chapter, NO verse overlap with 3:21-26 → excluded
        _insert_session(conn, sid=200, passage_ref='Romans 3:10-18',
                        book=66, chapter=3, verse_start=10, verse_end=18)
        # 3:24-28 — same chapter, overlaps 3:21-26 on 24-26 → included
        _insert_session(conn, sid=201, passage_ref='Romans 3:24-28',
                        book=66, chapter=3, verse_start=24, verse_end=28)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.list_prior_sessions({'scope': 'passage'}, ctx)
    ids = {s['session_id'] for s in out['sessions']}
    # Series members (20,21,22) excluded; 200 non-overlapping excluded;
    # 201 overlapping included.
    assert 201 in ids
    assert 200 not in ids
    assert 20 not in ids and 21 not in ids and 22 not in ids


def test_search_prior_sessions_finds_in_each_surface(db):
    _seed_romans_series(db)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}

    out = pwt.search_prior_sessions(
        {'query': 'νυνὶ', 'scope': 'series',
         'search_in': ['decisions', 'outline', 'chat', 'notepads', 'annotations']}, ctx)
    surfaces = {h['surface'] for h in out['hits']}
    # 'νυνὶ' appears in chat (msg) and notepad — at least those should be present.
    assert 'chat' in surfaces or 'notepads' in surfaces

    # Different query — should hit decisions.
    out2 = pwt.search_prior_sessions(
        {'query': 'eschatological', 'scope': 'series'}, ctx)
    assert any(h['surface'] == 'decisions' for h in out2['hits'])


def test_search_prior_sessions_drops_unknown_surface(db):
    _seed_romans_series(db)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    # Mixed valid + invalid surfaces — valid surfaces still searched.
    out = pwt.search_prior_sessions(
        {'query': 'eschatological', 'scope': 'series',
         'search_in': ['decisions', 'NOT_A_REAL_SURFACE']}, ctx)
    assert 'error' not in out
    assert any(h['surface'] == 'decisions' for h in out['hits'])


def test_search_prior_sessions_all_invalid_surfaces_errors(db):
    """If every requested surface is invalid, refuse rather than silently
    broaden to all surfaces (which would mask agent typos)."""
    _seed_romans_series(db)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.search_prior_sessions(
        {'query': 'eschatological', 'scope': 'series',
         'search_in': ['typo', 'also_bad']}, ctx)
    assert out['error'] == 'no_valid_search_in_surfaces'
    assert 'valid_surfaces' in out


def test_search_prior_sessions_insights_surface(db):
    """homiletics_insights is searchable as a session-side surface (moved from
    sermon-side per codex review — avoids noisy-active-link contamination)."""
    _seed_romans_series(db)
    with db._safe_write() as conn:
        conn.execute(
            "INSERT INTO homiletics_insights (session_id, kind, title, body, "
            "  citations, confidence, metadata, created_at, source_tool) "
            "VALUES (20, 'christ_thread', 'νυνί force', "
            "        'The pivot is temporal-logical, per Moo.', "
            "        '[]', 0.9, '{}', ?, 'lookup_lexicon')",
            (_now(),),
        )
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.search_prior_sessions(
        {'query': 'temporal-logical', 'scope': 'series',
         'search_in': ['insights']}, ctx)
    assert any(h['surface'] == 'insights' and h['session_id'] == 20
               for h in out['hits'])


def test_read_prior_session_pulls_requested_sections(db):
    _seed_romans_series(db)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.read_prior_session(
        {'session_id': 20, 'sections': ['decisions', 'outline', 'notepads']}, ctx)
    assert out['session_id'] == 20
    assert out['passage_ref'] == 'Romans 3:21-22'
    assert out['decisions']['subject'] == 'The eschatological hinge of Romans'
    assert any('eschatological pivot' in n['content'] for n in out['outline_tree'])
    assert 'exegesis' in out['notepads_by_phase']
    # Did NOT request chat → no chat_summary in payload.
    assert 'chat_summary' not in out


def test_read_prior_session_chat_summary_truncates(db):
    _seed_romans_series(db)
    # Pile up >20 messages in session 20.
    with db._safe_write() as conn:
        for i in range(25):
            _insert_message(conn, sid=20, role='user', content=f'filler {i}')
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.read_prior_session(
        {'session_id': 20, 'sections': ['chat']}, ctx)
    assert len(out['chat_summary']) == 20
    # ...and chat_full mode returns everything.
    out_full = pwt.read_prior_session(
        {'session_id': 20, 'sections': ['chat_full']}, ctx)
    assert len(out_full['chat_summary']) >= 27


def test_read_prior_session_unknown_id(db):
    _seed_romans_series(db)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.read_prior_session({'session_id': 9999}, ctx)
    assert out.get('error') == 'not_found'


# ─────────────────────────── Tests: sermons ─────────────────────────────


def test_list_prior_sermons_series_uses_only_active_links(db):
    """Sermon 205 has an active link to session 20 AND a noisy candidate link
    to session 23. Series scope from session 23 must use only the active path."""
    _seed_romans_series(db)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.list_prior_sermons({'scope': 'series'}, ctx)
    ids = [s['sermon_id'] for s in out['sermons']]
    # All three series sermons present; sermon 205 should appear via session 20,
    # not double-counted from the candidate link to session 23.
    assert sorted(ids) == [205, 206, 207]
    s205 = next(s for s in out['sermons'] if s['sermon_id'] == 205)
    assert s205['has_review'] is True
    assert 'congregant question' in (s205.get('one_change_for_next_sunday') or '')
    s206 = next(s for s in out['sermons'] if s['sermon_id'] == 206)
    assert s206['has_review'] is False


def test_list_prior_sermons_passage_scope_excludes_series(db):
    """Passage scope must exclude sermons already in the series scope AND
    require verse-range overlap with the current session, so the two scopes
    are complementary and non-overlapping in the chapter-level case."""
    _seed_romans_series(db)
    with db._safe_write() as conn:
        # 3:1-8 — same chapter but NO overlap with 3:21-26 → excluded
        _insert_sermon(conn, sid=300, title='Earlier Romans 3 Sermon',
                       preach_date='2025-12-01',
                       book=66, chapter=3, verse_start=1, verse_end=8,
                       transcript='Earlier coverage of Romans 3.',
                       sync_status='review_ready')
        # 3:23-26 — overlaps, not in series → included
        _insert_sermon(conn, sid=301, title='Standalone Rom 3:23-26',
                       preach_date='2025-11-01',
                       book=66, chapter=3, verse_start=23, verse_end=26,
                       transcript='Overlapping coverage of 3:23-26.',
                       sync_status='review_ready')
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.list_prior_sermons({'scope': 'passage'}, ctx)
    ids = {s['sermon_id'] for s in out['sermons']}
    # 301 overlaps, included; 300 doesn't overlap, excluded; series sermons excluded.
    assert 301 in ids
    assert 300 not in ids
    assert 205 not in ids and 206 not in ids and 207 not in ids


def test_search_prior_sermons_transcript_substring(db):
    _seed_romans_series(db)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.search_prior_sermons(
        {'query': 'covenant justice', 'scope': 'series',
         'search_in': ['transcript']}, ctx)
    sermon_ids = {h['sermon_id'] for h in out['hits']}
    assert 207 in sermon_ids
    hit = next(h for h in out['hits'] if h['sermon_id'] == 207)
    assert '«covenant justice»' in hit['snippet']  # highlight markers present


def test_search_prior_sermons_review_surface(db):
    _seed_romans_series(db)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.search_prior_sermons(
        {'query': 'Hebrews 9', 'scope': 'series', 'search_in': ['review']}, ctx)
    assert any(h['sermon_id'] == 207 and h['surface'] == 'review' for h in out['hits'])


def test_search_prior_sermons_moments_and_flags(db):
    _seed_romans_series(db)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.search_prior_sermons(
        {'query': 'tension', 'scope': 'series', 'search_in': ['moments']}, ctx)
    assert any(h['sermon_id'] == 205 and h['surface'] == 'moments' for h in out['hits'])

    out2 = pwt.search_prior_sermons(
        {'query': 'excursus', 'scope': 'series', 'search_in': ['flags']}, ctx)
    assert any(h['sermon_id'] == 207 and h['surface'] == 'flags' for h in out2['hits'])


def test_read_prior_sermon_sections(db):
    _seed_romans_series(db)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.read_prior_sermon(
        {'sermon_id': 207,
         'sections': ['review', 'transcript', 'moments', 'flags']}, ctx)
    assert out['sermon_id'] == 207
    assert out['review']['one_change_for_next_sunday'].startswith('Cut the Hebrews 9')
    assert 'Propitiation' in out['transcript']
    assert any(m['dimension_key'] == 'christ_thread_score' for m in out['moments'])
    assert any(f['flag_type'] == 'length_drift' for f in out['flags'])


def test_read_prior_sermon_review_none_when_unanalyzed(db):
    _seed_romans_series(db)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.read_prior_sermon(
        {'sermon_id': 206, 'sections': ['review']}, ctx)
    assert out['review'] is None  # analysis_pending — no review row


def test_read_prior_sermon_unknown_id(db):
    _seed_romans_series(db)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.read_prior_sermon({'sermon_id': 9999}, ctx)
    assert out.get('error') == 'not_found'


# ─────────────────────────── Tests: scope semantics ─────────────────────────


def test_scope_used_echoed(db):
    _seed_romans_series(db)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    for tool, args in [
        (pwt.list_prior_sessions, {'scope': 'passage'}),
        (pwt.list_prior_sermons, {'scope': 'all'}),
        (pwt.search_prior_sessions, {'query': 'foo', 'scope': 'all'}),
        (pwt.search_prior_sermons, {'query': 'foo', 'scope': 'passage'}),
    ]:
        out = tool(args, ctx)
        assert out['scope_used'] == args['scope'], (tool.__name__, out)


def test_empty_series_returns_hint(db):
    """Session 99 is standalone — series scope yields nothing + a hint."""
    _seed_romans_series(db)
    ctx = {'session_id': 99, 'book': 66, 'chapter': 8,
           'verse_start': 1, 'verse_end': 4, 'db': db}
    out = pwt.list_prior_sermons({'scope': 'series'}, ctx)
    assert out['sermons'] == []
    assert 'hint' in out


def test_search_cap_25_hits(db):
    """Pile up many transcript hits and verify the cap."""
    _seed_romans_series(db)
    with db._safe_write() as conn:
        for i in range(40):
            _insert_sermon(conn, sid=1000 + i, title=f'Filler {i}',
                           preach_date='2025-01-01',
                           book=66, chapter=3, verse_start=21, verse_end=26,
                           transcript='match keyword here ' * 3,
                           sync_status='review_ready')
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.search_prior_sermons(
        {'query': 'match keyword', 'scope': 'all',
         'search_in': ['transcript']}, ctx)
    assert len(out['hits']) <= 25


def test_list_cap_50_rows(db):
    _seed_romans_series(db)
    with db._safe_write() as conn:
        for i in range(80):
            _insert_session(conn, sid=2000 + i, passage_ref=f'Filler {i}',
                            book=66, chapter=3, verse_start=1, verse_end=8)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.list_prior_sessions({'scope': 'all'}, ctx)
    assert len(out['sessions']) <= 50


def test_default_scope_is_series(db):
    _seed_romans_series(db)
    ctx = {'session_id': 23, 'book': 66, 'chapter': 3,
           'verse_start': 21, 'verse_end': 26, 'db': db}
    out = pwt.list_prior_sessions({}, ctx)
    assert out['scope_used'] == 'series'
