"""Tests for agent_data_provider — the cross-agent data layer."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from companion_db import CompanionDB
from agent_data_provider import (
    format_active_commitment,
    fetch_recent_one_changes,
    fetch_overrun_summary,
    fetch_sermon_coach_patterns,
    fetch_prior_phase_summary,
    fetch_manuscript_from_outline,
    estimate_spoken_minutes_from_outline,
    context_for_persona,
)


@pytest.fixture
def db():
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    d = CompanionDB(tmp.name)
    d.init_db()
    yield d
    os.unlink(tmp.name)


def _seed_sermon(db, *, sermon_id=1, preach_date='2026-05-10',
                 actual_seconds=2200, planned_seconds=1680,
                 one_change='Move application earlier.', title='Test sermon'):
    """Insert a sermon + a sermon_review row."""
    conn = db._conn()
    now = '2026-05-18T00:00:00+00:00'
    conn.execute(
        "INSERT INTO sermons (id, sermonaudio_id, broadcaster_id, title, "
        "preach_date, sermon_type, classified_as, last_state_change_at, "
        "first_synced_at, last_synced_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'expository', 'sermon', ?, ?, ?, ?, ?)",
        (sermon_id, f'sa_{sermon_id}', 'bcid', title, preach_date,
         now, now, now, now, now))
    conn.execute(
        "INSERT INTO sermon_reviews (sermon_id, analyzer_version, "
        "homiletics_core_version, analyzed_transcript_hash, "
        "source_version_at_analysis, actual_duration_seconds, "
        "planned_duration_seconds, duration_delta_seconds, "
        "top_impact_helpers, top_impact_hurters, one_change_for_next_sunday, "
        "computed_at) "
        "VALUES (?, 'v1', 'v1', 'h', 1, ?, ?, ?, '[]', '[]', ?, ?)",
        (sermon_id, actual_seconds, planned_seconds,
         actual_seconds - planned_seconds, one_change, now))
    conn.commit()
    conn.close()


def _seed_active_commitment(db, text='Tuesday at 10am Test: name a time/setting before the 60% mark.'):
    conn = db._conn()
    conn.execute(
        "INSERT INTO coaching_commitments (dimension_key, practice_experiment, "
        "status, created_at) VALUES ('application', ?, 'active', ?)",
        (text, '2026-05-18T00:00:00+00:00'))
    conn.commit()
    conn.close()


# ── format_active_commitment ──────────────────────────────────────────────

def test_format_active_commitment_returns_text(db):
    _seed_active_commitment(db, text='Move application earlier.')
    from coaching_bridge import load_active_commitment
    out = format_active_commitment(load_active_commitment(db))
    assert 'Move application earlier' in out


def test_format_active_commitment_handles_none():
    assert format_active_commitment(None) == ''


# ── fetch_recent_one_changes ──────────────────────────────────────────────

def test_fetch_recent_one_changes_returns_list(db):
    _seed_sermon(db, sermon_id=1, preach_date='2026-05-10',
                 one_change='Cut the second conviction pass.')
    _seed_sermon(db, sermon_id=2, preach_date='2026-05-17',
                 one_change='Land the application before minute 12.')
    out = fetch_recent_one_changes(db, n=6)
    assert '2026-05-17' in out
    assert 'Land the application' in out
    assert '2026-05-10' in out
    assert 'Cut the second conviction pass' in out


def test_fetch_recent_one_changes_orders_newest_first(db):
    _seed_sermon(db, sermon_id=1, preach_date='2026-04-01', one_change='OLD')
    _seed_sermon(db, sermon_id=2, preach_date='2026-05-15', one_change='NEW')
    out = fetch_recent_one_changes(db, n=6)
    assert out.index('NEW') < out.index('OLD')


def test_fetch_recent_one_changes_respects_limit(db):
    for i in range(8):
        _seed_sermon(db, sermon_id=i + 1,
                     preach_date=f'2026-04-{i + 1:02d}',
                     one_change=f'change-{i}')
    out = fetch_recent_one_changes(db, n=3)
    assert out.count('change-') == 3


def test_fetch_recent_one_changes_empty_when_no_reviews(db):
    assert fetch_recent_one_changes(db) == ''


# ── fetch_overrun_summary ─────────────────────────────────────────────────

def test_fetch_overrun_summary_reports_stats(db):
    _seed_sermon(db, sermon_id=1, actual_seconds=2400, planned_seconds=1680)
    _seed_sermon(db, sermon_id=2, actual_seconds=2520, planned_seconds=1680)
    out = fetch_overrun_summary(db)
    assert 'reviewed sermons' in out
    assert '2' in out


def test_fetch_overrun_summary_empty_when_no_reviews(db):
    assert fetch_overrun_summary(db) == ''


# ── fetch_sermon_coach_patterns ───────────────────────────────────────────

def test_fetch_sermon_coach_patterns_returns_dimensions(db):
    # Need sermon_moments with valence=negative
    _seed_sermon(db, sermon_id=1)
    conn = db._conn()
    # analysis_runs is FK target; insert one (per its actual schema)
    conn.execute(
        "INSERT INTO analysis_runs (id, sermon_id, run_type, prompt_version, "
        "model_name, status, created_at) "
        "VALUES ('run-1', 1, 'review', 'v1', 'claude', 'completed', ?)",
        ('2026-05-18T00:00:00+00:00',))
    conn.execute(
        "INSERT INTO sermon_moments (sermon_id, analysis_run_id, "
        "start_segment_index, end_segment_index, start_ms, end_ms, "
        "sermon_position_pct, excerpt_text, dimension_key, section_role, "
        "valence, confidence, impact, moment_rank, rationale, "
        "prompt_version, created_at) "
        "VALUES (1, 'run-1', 0, 1, 0, 100, 0.5, 'ex', "
        "'christ_thread_score', 'exposition', 'negative', 0.8, 'major', "
        "1, 'r', 'v1', ?)",
        ('2026-05-18T00:00:00+00:00',))
    conn.commit()
    conn.close()
    out = fetch_sermon_coach_patterns(db, n_sermons=6)
    assert 'christ_thread_score' in out


def test_fetch_sermon_coach_patterns_empty_when_no_moments(db):
    assert fetch_sermon_coach_patterns(db) == ''


# ── fetch_prior_phase_summary ─────────────────────────────────────────────

def test_fetch_prior_phase_summary_returns_card_responses(db):
    sid = db.create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    db.save_card_response(sid, 'prayer', 1, 'Lord, open my eyes.')
    db.save_card_response(sid, 'read_translate', 2, 'My working translation of v.18.')
    out = fetch_prior_phase_summary(db, sid)
    assert 'prayer' in out
    assert 'open my eyes' in out
    assert 'read_translate' in out
    assert 'working translation' in out


def test_fetch_prior_phase_summary_empty_when_no_responses(db):
    sid = db.create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    assert fetch_prior_phase_summary(db, sid) == ''


# ── manuscript + estimate ─────────────────────────────────────────────────

def test_fetch_manuscript_from_outline_renders_tree(db):
    sid = db.create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    root = db.add_outline_node(sid, 'title', 'God Reveals His Wrath')
    db.add_outline_node(sid, 'main_point', 'I. The wrath is being revealed (v.18)',
                        parent_id=root)
    out = fetch_manuscript_from_outline(db, sid)
    assert 'God Reveals His Wrath' in out
    assert 'wrath is being revealed' in out


def test_fetch_manuscript_empty_when_no_outline(db):
    sid = db.create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    assert fetch_manuscript_from_outline(db, sid) == ''


def test_estimate_spoken_minutes_from_outline_counts_words(db):
    sid = db.create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    # 300 words of content → ~2 min at 150 wpm
    long_text = ' '.join(['word'] * 300)
    db.add_outline_node(sid, 'main_point', long_text)
    minutes = estimate_spoken_minutes_from_outline(db, sid)
    assert minutes is not None
    assert 1.5 <= minutes <= 2.5


def test_estimate_spoken_minutes_none_when_empty(db):
    sid = db.create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    assert estimate_spoken_minutes_from_outline(db, sid) is None


# ── context_for_persona dispatcher ────────────────────────────────────────

def test_context_for_persona_research_returns_empty_dict(db):
    sid = db.create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    out = context_for_persona(db, sid, 'research')
    assert out == {}


def test_context_for_persona_composing_has_two_fields(db):
    sid = db.create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    out = context_for_persona(db, sid, 'composing')
    assert set(out.keys()) == {'prior_phase_summary', 'sermon_coach_pattern_summary'}


def test_context_for_persona_editing_has_five_fields(db):
    sid = db.create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    out = context_for_persona(db, sid, 'editing')
    assert set(out.keys()) == {
        'manuscript', 'estimated_minutes', 'active_commitment',
        'recent_coach_one_changes', 'prior_overruns_summary',
    }


def test_context_for_persona_silent_returns_empty(db):
    sid = db.create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    assert context_for_persona(db, sid, 'silent') == {}


def test_context_for_persona_unknown_returns_empty(db):
    sid = db.create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    assert context_for_persona(db, sid, 'nope') == {}
