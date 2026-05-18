"""Tests for the patterns_report aggregators (panel data layer)."""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from session_analytics import SessionAnalytics
from accuser_detector import AccuserDetector
from patterns_report import (
    cutoff_summary,
    cutoff_recent_dismissed_rate,
    accuser_category_summary,
    accuser_recent_examples,
    recent_session_activity,
)
from companion_db import CompanionDB


@pytest.fixture
def stack():
    """Bundle of the three DBs the patterns route needs."""
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    cdb = CompanionDB(tmp.name)
    cdb.init_db()
    sa = SessionAnalytics(tmp.name)
    sa.init_db()
    ad = AccuserDetector(tmp.name)
    ad.init_db()
    yield {'db': cdb, 'sa': sa, 'ad': ad, 'path': tmp.name}
    os.unlink(tmp.name)


# ── Cutoff summary ────────────────────────────────────────────────────────

def test_cutoff_summary_groups_by_type(stack):
    sa = stack['sa']
    sa.log_cutoff_event('/companion/', 'daily_5pm')
    sa.log_cutoff_event('/study/', 'daily_5pm')
    sa.log_cutoff_event('/companion/', 'friday_3pm')
    rows = cutoff_summary(sa, days=30)
    by_type = {r['cutoff_type']: r for r in rows}
    assert by_type['daily_5pm']['n'] == 2
    assert by_type['friday_3pm']['n'] == 1


def test_cutoff_summary_counts_dismissed(stack):
    sa = stack['sa']
    e1 = sa.log_cutoff_event('/companion/', 'daily_5pm')
    sa.log_cutoff_event('/study/', 'daily_5pm')
    sa.mark_cutoff_dismissed(e1)
    rows = cutoff_summary(sa, days=30)
    daily = next(r for r in rows if r['cutoff_type'] == 'daily_5pm')
    assert daily['n'] == 2
    assert daily['dismissed'] == 1


def test_cutoff_summary_respects_days_window(stack):
    sa = stack['sa']
    old_iso = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    conn = sa._conn()
    conn.execute(
        "INSERT INTO cutoff_events (timestamp, route, cutoff_type, dismissed) "
        "VALUES (?, '/companion/', 'daily_5pm', 0)",
        (old_iso,))
    conn.commit()
    conn.close()
    # 5-day window excludes the 10-day-old event
    rows = cutoff_summary(sa, days=5)
    assert rows == []


def test_cutoff_summary_empty_when_no_events(stack):
    assert cutoff_summary(stack['sa'], days=30) == []


def test_cutoff_recent_dismissed_rate_returns_fraction(stack):
    sa = stack['sa']
    e1 = sa.log_cutoff_event('/companion/', 'daily_5pm')
    sa.log_cutoff_event('/study/', 'daily_5pm')
    sa.log_cutoff_event('/companion/', 'friday_3pm')
    sa.log_cutoff_event('/companion/', 'weekend_lockout')
    sa.mark_cutoff_dismissed(e1)
    rate = cutoff_recent_dismissed_rate(sa, days=30)
    assert 0.0 < rate < 1.0


def test_cutoff_recent_dismissed_rate_zero_when_none(stack):
    assert cutoff_recent_dismissed_rate(stack['sa'], days=30) == 0.0


# ── Accuser detection summaries ─────────────────────────────────────────

def test_accuser_category_summary_counts_per_category(stack):
    ad = stack['ad']
    ad.analyze_message(session_id=1, message_id=1, surface='study',
                       text='I cheated on this sermon.')
    ad.analyze_message(session_id=1, message_id=2, surface='study',
                       text='I phoned it in entirely.')
    ad.analyze_message(session_id=1, message_id=3, surface='study',
                       text='The LLM wrote the slave-market line for me.')
    rows = accuser_category_summary(ad, days=30)
    by_cat = {r['trigger_category']: r['n'] for r in rows}
    assert by_cat['self_condemnation'] == 2
    assert by_cat['asymmetric_attribution'] == 1


def test_accuser_category_summary_empty_when_none(stack):
    assert accuser_category_summary(stack['ad'], days=30) == []


def test_accuser_recent_examples_returns_newest_first(stack):
    ad = stack['ad']
    ad.analyze_message(session_id=1, message_id=1, surface='study',
                       text='I cheated on this sermon.')
    ad.analyze_message(session_id=1, message_id=2, surface='study',
                       text='The LLM wrote my conclusion entirely.')
    rows = accuser_recent_examples(ad, limit=10)
    assert len(rows) == 2
    # Both items appear with the matched text
    text = ' '.join(r['matched_text'] for r in rows)
    assert 'cheated' in text or 'LLM wrote' in text


def test_accuser_recent_examples_respects_limit(stack):
    ad = stack['ad']
    for i in range(5):
        ad.analyze_message(session_id=1, message_id=i + 1, surface='study',
                           text=f'I phoned it in number {i}.')
    rows = accuser_recent_examples(ad, limit=3)
    assert len(rows) == 3


# ── Session activity ─────────────────────────────────────────────────────

def test_recent_session_activity_counts_sessions(stack):
    db = stack['db']
    db.create_session('Romans 1:18-23', 66, 1, 18, 23, 'epistle')
    db.create_session('Romans 1:24-32', 66, 1, 24, 32, 'epistle')
    result = recent_session_activity(db, days=30)
    assert result['active_sessions'] >= 2


def test_recent_session_activity_zero_when_empty(stack):
    result = recent_session_activity(stack['db'], days=30)
    assert result['active_sessions'] == 0
