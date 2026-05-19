"""Tests for time_coach — the always-on 4th role that watches the clock."""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from companion_db import CompanionDB
from session_analytics import SessionAnalytics
from time_coach import (
    assess_session,
    week_context,
    BUDGET_OK, BUDGET_WARN, BUDGET_OVER,
)


@pytest.fixture
def stack():
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    cdb = CompanionDB(tmp.name)
    cdb.init_db()
    sa = SessionAnalytics(tmp.name)
    sa.init_db()
    yield {'db': cdb, 'sa': sa, 'path': tmp.name}
    os.unlink(tmp.name)


# ── assess_session ────────────────────────────────────────────────────────

def test_assess_session_returns_basic_fields(stack):
    sid = stack['db'].create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    out = assess_session(stack['db'], stack['sa'], sid)
    for key in ('current_phase', 'session_created_at',
                'time_in_phase_sec', 'phase_budget_sec', 'phase_remaining_sec',
                'budget_state', 'total_elapsed_sec',
                'sermon_elapsed_sec', 'sermon_budget_sec',
                'sermon_remaining_sec', 'sermon_budget_state'):
        assert key in out, f"missing field {key!r}"


def test_assess_session_sermon_budget_matches_phase_timer_sum(stack):
    from companion_db import PHASE_TIMERS
    sid = stack['db'].create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    out = assess_session(stack['db'], stack['sa'], sid)
    assert out['sermon_budget_sec'] == sum(PHASE_TIMERS.values())


def test_assess_session_sermon_remaining_reflects_elapsed(stack):
    sid = stack['db'].create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    # Synthesize 3 hours of elapsed
    conn = stack['db']._conn()
    conn.execute("UPDATE sessions SET total_elapsed_seconds = ? WHERE id = ?",
                 (3 * 3600, sid))
    conn.commit()
    conn.close()
    out = assess_session(stack['db'], stack['sa'], sid)
    assert out['sermon_elapsed_sec'] == 3 * 3600
    assert out['sermon_remaining_sec'] == out['sermon_budget_sec'] - 3 * 3600


def test_assess_session_phase_remaining_when_budget_set(stack):
    sid = stack['db'].create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    stack['db'].update_phase(sid, 'word_study')  # 1800s budget
    long_ago = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    conn = stack['sa']._conn()
    conn.execute("INSERT INTO phase_events (session_id, phase, event_type, timestamp) "
                 "VALUES (?, 'word_study', 'enter', ?)", (sid, long_ago))
    conn.commit()
    conn.close()
    out = assess_session(stack['db'], stack['sa'], sid)
    # ~600s elapsed of 1800s budget → ~1200s remaining
    assert 1100 <= out['phase_remaining_sec'] <= 1300


def test_assess_session_phase_remaining_none_when_no_budget(stack):
    sid = stack['db'].create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    stack['db'].update_phase(sid, 'study')  # catch-all, no budget
    out = assess_session(stack['db'], stack['sa'], sid)
    assert out['phase_remaining_sec'] is None


def test_assess_session_returns_none_for_missing_session(stack):
    assert assess_session(stack['db'], stack['sa'], 99999) is None


def test_assess_session_budget_ok_when_under(stack):
    sid = stack['db'].create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    # Default phase 'prayer' budget = 900s. No phase_events → 0 elapsed.
    out = assess_session(stack['db'], stack['sa'], sid)
    assert out['budget_state'] == BUDGET_OK


def test_assess_session_budget_warn_at_80_pct(stack):
    sid = stack['db'].create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    stack['db'].update_phase(sid, 'word_study')  # budget 1800s
    # Synthesize a phase_event enter 25 min ago (1500s; > 80% of 1800).
    long_ago = (datetime.now(timezone.utc) - timedelta(seconds=1500)).isoformat()
    conn = stack['sa']._conn()
    conn.execute(
        "INSERT INTO phase_events (session_id, phase, event_type, timestamp) "
        "VALUES (?, 'word_study', 'enter', ?)", (sid, long_ago))
    conn.commit()
    conn.close()
    out = assess_session(stack['db'], stack['sa'], sid)
    assert out['budget_state'] == BUDGET_WARN


def test_assess_session_budget_over_when_past(stack):
    sid = stack['db'].create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    stack['db'].update_phase(sid, 'word_study')  # budget 1800s
    long_ago = (datetime.now(timezone.utc) - timedelta(seconds=2400)).isoformat()
    conn = stack['sa']._conn()
    conn.execute(
        "INSERT INTO phase_events (session_id, phase, event_type, timestamp) "
        "VALUES (?, 'word_study', 'enter', ?)", (sid, long_ago))
    conn.commit()
    conn.close()
    out = assess_session(stack['db'], stack['sa'], sid)
    assert out['budget_state'] == BUDGET_OVER


def test_assess_session_handles_phase_without_budget(stack):
    sid = stack['db'].create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    # The /study/ catch-all 'study' phase has no PHASE_TIMERS entry.
    stack['db'].update_phase(sid, 'study')
    out = assess_session(stack['db'], stack['sa'], sid)
    assert out['phase_budget_sec'] is None
    # No budget → no budget_state to flag (defaults to ok)
    assert out['budget_state'] == BUDGET_OK


def test_assess_session_next_action_suggests_phase_advance_when_over(stack):
    sid = stack['db'].create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    stack['db'].update_phase(sid, 'word_study')
    long_ago = (datetime.now(timezone.utc) - timedelta(seconds=2400)).isoformat()
    conn = stack['sa']._conn()
    conn.execute(
        "INSERT INTO phase_events (session_id, phase, event_type, timestamp) "
        "VALUES (?, 'word_study', 'enter', ?)", (sid, long_ago))
    conn.commit()
    conn.close()
    out = assess_session(stack['db'], stack['sa'], sid)
    assert out['next_action']  # non-empty — rotation guarantees some advice fires


# ── week_context ──────────────────────────────────────────────────────────

def test_week_context_counts_active_sessions(stack):
    stack['db'].create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    stack['db'].create_session('Romans 1:24-32', 66, 1, 24, 32, 'epistle')
    ctx = week_context(stack['db'], stack['sa'])
    assert ctx['sessions_this_week'] >= 2


def test_week_context_day_name(stack):
    ctx = week_context(stack['db'], stack['sa'])
    # Day name is a string like 'Monday' etc.
    assert ctx['day_name'] in (
        'Monday', 'Tuesday', 'Wednesday', 'Thursday',
        'Friday', 'Saturday', 'Sunday',
    )


def test_week_context_empty_db(stack):
    ctx = week_context(stack['db'], stack['sa'])
    assert ctx['sessions_this_week'] == 0


# ── prep_window_position ──────────────────────────────────────────────────

def test_prep_window_position_returns_required_fields():
    from time_coach import prep_window_position
    out = prep_window_position()
    for key in ('state', 'fraction', 'color', 'label'):
        assert key in out
    assert out['state'] in ('in_window', 'before_window', 'lockout',
                            'preach_day', 'day_off')


def test_prep_window_position_color_is_valid():
    from time_coach import prep_window_position
    out = prep_window_position()
    assert out['color'] in ('green', 'amber', 'red', 'lockout', 'muted')


# ── Next-action rotation ──────────────────────────────────────────────────

def test_next_action_phrasing_rotates_per_pool_key():
    """Each pool should have multiple variants so consecutive days differ."""
    from time_coach import _NEXT_ACTION_POOL
    for key, variants in _NEXT_ACTION_POOL.items():
        assert len(variants) >= 2, f"pool {key!r} has only {len(variants)} variant(s); need ≥2 for rotation"


def test_next_action_sermon_over_wins_over_phase_over():
    from time_coach import _next_action, BUDGET_OVER
    out = _next_action('word_study', BUDGET_OVER, 100, sermon_budget_state=BUDGET_OVER)
    assert 'sermon' in out.lower() or '13.5h' in out


def test_next_action_phase_over_when_sermon_ok():
    from time_coach import _next_action, BUDGET_OVER, BUDGET_OK
    out = _next_action('word_study', BUDGET_OVER, 100, sermon_budget_state=BUDGET_OK)
    assert 'word_study' in out or 'phase' in out.lower()


def test_next_action_empty_when_all_ok_and_session_young():
    from time_coach import _next_action, BUDGET_OK
    out = _next_action('word_study', BUDGET_OK, 60, sermon_budget_state=BUDGET_OK)
    assert out == ''
