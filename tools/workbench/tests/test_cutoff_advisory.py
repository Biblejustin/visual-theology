"""Tests for Cycle 1: Daily 5pm advisory cutoff.

Covers:
- Pure time logic (is_past_daily_cutoff with DST handling)
- DB schema + helpers (log_cutoff_event, mark_cutoff_dismissed, get_recent_cutoff_event)
- Flask integration (banner context on sermon-work routes, dismiss endpoint, throttling)

Mode: advisory only — notification + logging, no enforcement.
"""

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from session_analytics import SessionAnalytics
from companion_db import CompanionDB
from seed_questions import seed_question_bank

# Bryan is in Central time. Tests pass tz explicitly so they're deterministic
# regardless of the test machine's local timezone.
CENTRAL = ZoneInfo("America/Chicago")


@pytest.fixture
def sa():
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    a = SessionAnalytics(tmp.name)
    a.init_db()
    yield a
    os.unlink(tmp.name)


# ── Pure time logic (DST-aware) ─────────────────────────────────────────────

def test_is_past_daily_cutoff_summer_4pm_central_is_false(sa):
    # May 18, 2026 = CDT (UTC-5). 4pm CDT = 21:00 UTC.
    now_utc = datetime(2026, 5, 18, 21, 0, tzinfo=timezone.utc)
    assert sa.is_past_daily_cutoff(now_utc=now_utc, tz=CENTRAL) is False


def test_is_past_daily_cutoff_summer_5pm_central_is_true(sa):
    # 5pm CDT = 22:00 UTC.
    now_utc = datetime(2026, 5, 18, 22, 0, tzinfo=timezone.utc)
    assert sa.is_past_daily_cutoff(now_utc=now_utc, tz=CENTRAL) is True


def test_is_past_daily_cutoff_summer_6pm_central_is_true(sa):
    now_utc = datetime(2026, 5, 18, 23, 0, tzinfo=timezone.utc)
    assert sa.is_past_daily_cutoff(now_utc=now_utc, tz=CENTRAL) is True


def test_is_past_daily_cutoff_winter_4pm_central_is_false(sa):
    # January 15, 2026 = CST (UTC-6). 4pm CST = 22:00 UTC.
    now_utc = datetime(2026, 1, 15, 22, 0, tzinfo=timezone.utc)
    assert sa.is_past_daily_cutoff(now_utc=now_utc, tz=CENTRAL) is False


def test_is_past_daily_cutoff_winter_5pm_central_is_true(sa):
    # 5pm CST = 23:00 UTC.
    now_utc = datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc)
    assert sa.is_past_daily_cutoff(now_utc=now_utc, tz=CENTRAL) is True


def test_is_past_daily_cutoff_morning_is_false(sa):
    now_utc = datetime(2026, 5, 18, 14, 0, tzinfo=timezone.utc)  # 9am CDT
    assert sa.is_past_daily_cutoff(now_utc=now_utc, tz=CENTRAL) is False


def test_is_past_daily_cutoff_respects_env_override_tz(sa, monkeypatch):
    """LOGOS_LOCAL_TZ env var lets the system override the auto-detected TZ."""
    now_utc = datetime(2026, 5, 18, 22, 0, tzinfo=timezone.utc)
    assert sa.is_past_daily_cutoff(now_utc=now_utc, tz=ZoneInfo("America/Los_Angeles")) is False
    assert sa.is_past_daily_cutoff(now_utc=now_utc, tz=ZoneInfo("America/New_York")) is True


# ── Friday 3pm cutoff ─────────────────────────────────────────────────────

def test_friday_3pm_thursday_evening_is_false(sa):
    now_utc = datetime(2026, 5, 22, 1, 0, tzinfo=timezone.utc)  # Thu 8pm CDT
    assert sa.is_past_friday_3pm(now_utc=now_utc, tz=CENTRAL) is False


def test_friday_3pm_friday_at_2pm_is_false(sa):
    # 2026-05-22 is Friday. 2pm CDT = 19:00 UTC
    now_utc = datetime(2026, 5, 22, 19, 0, tzinfo=timezone.utc)
    assert sa.is_past_friday_3pm(now_utc=now_utc, tz=CENTRAL) is False


def test_friday_3pm_friday_at_3pm_is_true(sa):
    now_utc = datetime(2026, 5, 22, 20, 0, tzinfo=timezone.utc)  # Fri 3pm CDT
    assert sa.is_past_friday_3pm(now_utc=now_utc, tz=CENTRAL) is True


def test_friday_3pm_friday_at_8pm_is_true(sa):
    now_utc = datetime(2026, 5, 23, 1, 0, tzinfo=timezone.utc)  # Fri 8pm CDT
    assert sa.is_past_friday_3pm(now_utc=now_utc, tz=CENTRAL) is True


def test_friday_3pm_saturday_is_false(sa):
    # Saturday gets weekend_lockout precedence; Friday-3pm is Friday-only.
    now_utc = datetime(2026, 5, 23, 22, 0, tzinfo=timezone.utc)  # Sat 5pm CDT
    assert sa.is_past_friday_3pm(now_utc=now_utc, tz=CENTRAL) is False


# ── Weekend lockout ───────────────────────────────────────────────────────

def test_weekend_lockout_saturday_morning_is_true(sa):
    now_utc = datetime(2026, 5, 23, 14, 0, tzinfo=timezone.utc)  # Sat 9am CDT
    assert sa.is_weekend_lockout(now_utc=now_utc, tz=CENTRAL) is True


def test_weekend_lockout_saturday_night_is_true(sa):
    now_utc = datetime(2026, 5, 24, 4, 0, tzinfo=timezone.utc)  # Sat 11pm CDT
    assert sa.is_weekend_lockout(now_utc=now_utc, tz=CENTRAL) is True


def test_weekend_lockout_sunday_6am_is_true(sa):
    now_utc = datetime(2026, 5, 24, 11, 0, tzinfo=timezone.utc)  # Sun 6am CDT
    assert sa.is_weekend_lockout(now_utc=now_utc, tz=CENTRAL) is True


def test_weekend_lockout_sunday_630am_is_false(sa):
    # Sunday 6:30am CDT = lockout lifts for read-through.
    now_utc = datetime(2026, 5, 24, 11, 30, tzinfo=timezone.utc)
    assert sa.is_weekend_lockout(now_utc=now_utc, tz=CENTRAL) is False


def test_weekend_lockout_sunday_10am_is_false(sa):
    now_utc = datetime(2026, 5, 24, 15, 0, tzinfo=timezone.utc)  # Sun 10am CDT
    assert sa.is_weekend_lockout(now_utc=now_utc, tz=CENTRAL) is False


def test_weekend_lockout_friday_evening_is_false(sa):
    now_utc = datetime(2026, 5, 23, 1, 0, tzinfo=timezone.utc)  # Fri 8pm CDT
    assert sa.is_weekend_lockout(now_utc=now_utc, tz=CENTRAL) is False


def test_weekend_lockout_monday_is_false(sa):
    now_utc = datetime(2026, 5, 25, 14, 0, tzinfo=timezone.utc)  # Mon 9am CDT
    assert sa.is_weekend_lockout(now_utc=now_utc, tz=CENTRAL) is False


# ── active_cutoff_type precedence ─────────────────────────────────────────

def test_active_cutoff_type_weekend_wins_over_5pm(sa):
    now_utc = datetime(2026, 5, 23, 23, 0, tzinfo=timezone.utc)  # Sat 6pm CDT
    assert sa.active_cutoff_type(now_utc=now_utc, tz=CENTRAL) == 'weekend_lockout'


def test_active_cutoff_type_friday_3pm_wins_over_5pm(sa):
    now_utc = datetime(2026, 5, 22, 23, 0, tzinfo=timezone.utc)  # Fri 6pm CDT
    assert sa.active_cutoff_type(now_utc=now_utc, tz=CENTRAL) == 'friday_3pm'


def test_active_cutoff_type_daily_5pm_on_tuesday(sa):
    now_utc = datetime(2026, 5, 19, 23, 0, tzinfo=timezone.utc)  # Tue 6pm CDT
    assert sa.active_cutoff_type(now_utc=now_utc, tz=CENTRAL) == 'daily_5pm'


def test_active_cutoff_type_returns_none_when_no_cutoff(sa):
    now_utc = datetime(2026, 5, 19, 15, 0, tzinfo=timezone.utc)  # Tue 10am CDT
    assert sa.active_cutoff_type(now_utc=now_utc, tz=CENTRAL) is None


# ── Schema ──────────────────────────────────────────────────────────────────

def test_cutoff_events_table_created_by_init_db(sa):
    conn = sa._conn()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='cutoff_events'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


# ── DB helpers ──────────────────────────────────────────────────────────────

def test_log_cutoff_event_inserts_row_with_session(sa):
    event_id = sa.log_cutoff_event('/companion/session/5', 'daily_5pm', session_id=5)
    assert event_id > 0
    conn = sa._conn()
    row = conn.execute(
        "SELECT route, cutoff_type, session_id, dismissed FROM cutoff_events WHERE id = ?",
        (event_id,)
    ).fetchone()
    conn.close()
    assert row['route'] == '/companion/session/5'
    assert row['cutoff_type'] == 'daily_5pm'
    assert row['session_id'] == 5
    assert row['dismissed'] == 0


def test_log_cutoff_event_inserts_row_without_session(sa):
    event_id = sa.log_cutoff_event('/study/', 'daily_5pm')
    assert event_id > 0
    conn = sa._conn()
    row = conn.execute(
        "SELECT route, session_id FROM cutoff_events WHERE id = ?", (event_id,)
    ).fetchone()
    conn.close()
    assert row['route'] == '/study/'
    assert row['session_id'] is None


def test_mark_cutoff_dismissed_updates_row(sa):
    event_id = sa.log_cutoff_event('/companion/', 'daily_5pm')
    sa.mark_cutoff_dismissed(event_id)
    conn = sa._conn()
    row = conn.execute(
        "SELECT dismissed FROM cutoff_events WHERE id = ?", (event_id,)
    ).fetchone()
    conn.close()
    assert row['dismissed'] == 1


def test_get_recent_cutoff_event_returns_recent_match(sa):
    event_id = sa.log_cutoff_event('/study/session/3', 'daily_5pm')
    recent = sa.get_recent_cutoff_event('/study/session/3', within_minutes=15)
    assert recent is not None
    assert recent['id'] == event_id
    assert recent['route'] == '/study/session/3'


def test_get_recent_cutoff_event_filters_by_route(sa):
    sa.log_cutoff_event('/companion/', 'daily_5pm')
    assert sa.get_recent_cutoff_event('/study/', within_minutes=15) is None


def test_get_recent_cutoff_event_excludes_old_events(sa):
    old_iso = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    conn = sa._conn()
    conn.execute(
        "INSERT INTO cutoff_events (timestamp, route, cutoff_type, dismissed) "
        "VALUES (?, ?, ?, 0)",
        (old_iso, '/companion/', 'daily_5pm')
    )
    conn.commit()
    conn.close()
    assert sa.get_recent_cutoff_event('/companion/', within_minutes=15) is None


def test_get_recent_cutoff_event_returns_dismissed_state(sa):
    event_id = sa.log_cutoff_event('/companion/', 'daily_5pm')
    sa.mark_cutoff_dismissed(event_id)
    recent = sa.get_recent_cutoff_event('/companion/', within_minutes=15)
    assert recent is not None
    assert recent['dismissed'] == 1


# ── Flask integration ──────────────────────────────────────────────────────

@pytest.fixture
def client():
    import app as app_module

    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    cdb = CompanionDB(tmp.name)
    cdb.init_db()
    seed_question_bank(cdb)
    analytics = SessionAnalytics(tmp.name)
    analytics.init_db()

    app_module.companion_db = cdb
    app_module.study_analytics = analytics
    app_module.app.config['TESTING'] = True

    with app_module.app.test_client() as c:
        yield c, analytics

    os.unlink(tmp.name)


def _make_companion_session(c, passage='Romans 1:18-23'):
    r = c.post('/companion/session/new', data={'passage': passage})
    loc = r.headers.get('Location', '')
    return int(loc.rstrip('/').split('/')[-1])


def test_companion_session_past_cutoff_logs_event(client, monkeypatch):
    c, sa = client
    monkeypatch.setattr(sa, 'active_cutoff_type', lambda now_utc=None, tz=None: 'daily_5pm')
    sid = _make_companion_session(c)
    r = c.get(f'/companion/session/{sid}')
    assert r.status_code == 200
    conn = sa._conn()
    events = conn.execute("SELECT * FROM cutoff_events").fetchall()
    conn.close()
    assert len(events) == 1
    assert events[0]['cutoff_type'] == 'daily_5pm'
    assert events[0]['route'] == f'/companion/session/{sid}'


def test_companion_session_before_cutoff_does_not_log(client, monkeypatch):
    c, sa = client
    monkeypatch.setattr(sa, 'active_cutoff_type', lambda now_utc=None, tz=None: None)
    sid = _make_companion_session(c)
    c.get(f'/companion/session/{sid}')
    conn = sa._conn()
    events = conn.execute("SELECT * FROM cutoff_events").fetchall()
    conn.close()
    assert len(events) == 0


def test_study_index_past_cutoff_logs_event(client, monkeypatch):
    c, sa = client
    monkeypatch.setattr(sa, 'active_cutoff_type', lambda now_utc=None, tz=None: 'daily_5pm')
    r = c.get('/study/')
    assert r.status_code == 200
    conn = sa._conn()
    events = conn.execute("SELECT * FROM cutoff_events").fetchall()
    conn.close()
    assert len(events) == 1
    assert events[0]['route'] == '/study/'


def test_non_sermon_route_does_not_log(client, monkeypatch):
    c, sa = client
    monkeypatch.setattr(sa, 'active_cutoff_type', lambda now_utc=None, tz=None: 'daily_5pm')
    c.get('/library')
    conn = sa._conn()
    events = conn.execute("SELECT * FROM cutoff_events").fetchall()
    conn.close()
    assert len(events) == 0


def test_htmx_fragment_request_does_not_log(client, monkeypatch):
    c, sa = client
    monkeypatch.setattr(sa, 'active_cutoff_type', lambda now_utc=None, tz=None: 'daily_5pm')
    sid = _make_companion_session(c)
    # Drain any event from the page-load make_session itself
    conn = sa._conn()
    conn.execute("DELETE FROM cutoff_events")
    conn.commit()
    conn.close()
    # HX-Request header means this is an HTMX fragment, not a full page load
    c.get(f'/companion/session/{sid}/card', headers={'HX-Request': 'true'})
    conn = sa._conn()
    events = conn.execute("SELECT * FROM cutoff_events").fetchall()
    conn.close()
    assert len(events) == 0


def test_throttle_prevents_duplicate_logs_within_window(client, monkeypatch):
    c, sa = client
    monkeypatch.setattr(sa, 'active_cutoff_type', lambda now_utc=None, tz=None: 'daily_5pm')
    sid = _make_companion_session(c)
    c.get(f'/companion/session/{sid}')
    c.get(f'/companion/session/{sid}')
    conn = sa._conn()
    events = conn.execute(
        "SELECT * FROM cutoff_events WHERE route = ?",
        (f'/companion/session/{sid}',)
    ).fetchall()
    conn.close()
    assert len(events) == 1


def test_dismiss_endpoint_marks_event(client):
    c, sa = client
    event_id = sa.log_cutoff_event('/companion/', 'daily_5pm')
    r = c.post(f'/api/cutoff-events/{event_id}/dismiss')
    assert r.status_code in (200, 204)
    conn = sa._conn()
    row = conn.execute(
        "SELECT dismissed FROM cutoff_events WHERE id = ?", (event_id,)
    ).fetchone()
    conn.close()
    assert row['dismissed'] == 1


def test_dismiss_endpoint_404_for_unknown_event(client):
    c, _ = client
    r = c.post('/api/cutoff-events/99999/dismiss')
    assert r.status_code == 404
