"""Time Coach — the always-on 4th role (v2 spec §5).

NOT a separate LLM persona. A small data-layer + template strip that
shows Bryan honest time signals:
  - Where in the week are we?
  - How long has this session been open?
  - How long has the current phase run vs its budget?
  - What's the next-most-important action right now?

This MVP version focuses on visible status only — no proactive
interruptions yet. Phase-transition pull and hyperfocus interruption
land in follow-ups once we've calibrated against real usage.

Reads from:
  - companion_db.sessions (current_phase, created_at, total_elapsed_seconds)
  - session_analytics.phase_events (enter timestamps → time-in-current-phase)
  - companion_db.PHASE_TIMERS (budget per phase)

Honest time signals require the activity-aware timer (task #53) to have
landed; the previous timer would have over-reported elapsed for any
session where Bryan switched tabs or walked away.
"""

from datetime import datetime, timezone

from companion_db import PHASE_TIMERS


BUDGET_OK   = 'ok'       # under 80% of budget (or no budget defined)
BUDGET_WARN = 'warn'     # 80% – 100% of budget
BUDGET_OVER = 'over'     # past budget


def _now():
    return datetime.now(timezone.utc)


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _seconds_in_current_phase(study_analytics, session_id, phase):
    """How long has the session been in `phase` since the most recent enter?

    Reads phase_events for this session+phase, finds the latest 'enter' event,
    returns now - that timestamp in seconds. Returns 0 if there's no enter
    event or the timestamp can't be parsed.
    """
    conn = study_analytics._conn()
    try:
        row = conn.execute(
            "SELECT timestamp FROM phase_events "
            "WHERE session_id = ? AND phase = ? AND event_type = 'enter' "
            "ORDER BY timestamp DESC LIMIT 1",
            (session_id, phase)).fetchone()
    finally:
        conn.close()
    if not row:
        return 0
    ts = _parse_iso(row['timestamp'])
    if ts is None:
        return 0
    delta = (_now() - ts).total_seconds()
    return max(0, int(delta))


def _budget_state(time_in_phase, budget):
    if budget is None or budget <= 0:
        return BUDGET_OK
    ratio = time_in_phase / float(budget)
    if ratio >= 1.0:
        return BUDGET_OVER
    if ratio >= 0.8:
        return BUDGET_WARN
    return BUDGET_OK


def _next_action(phase, budget_state, total_elapsed_sec):
    """One-line suggestion for what's next. MVP heuristic — refine later."""
    if budget_state == BUDGET_OVER:
        return f"You're past the budget for {phase}. Consider moving to the next phase."
    if budget_state == BUDGET_WARN:
        return f"Approaching the budget for {phase}. Wrap up this thread soon."
    if total_elapsed_sec >= 4 * 3600:
        return "You've been in this session over 4 hours. Take a break before the next phase."
    return ""  # nothing pressing — render empty


def assess_session(companion_db, study_analytics, session_id):
    """Return a status dict the time-coach UI can render.

    Returns None if the session doesn't exist. All time values are
    integer seconds. budget_state is one of BUDGET_OK / BUDGET_WARN /
    BUDGET_OVER. next_action is a short string (may be empty).
    """
    session = companion_db.get_session(session_id)
    if not session:
        return None
    phase = session['current_phase']
    budget = PHASE_TIMERS.get(phase)
    time_in_phase = _seconds_in_current_phase(study_analytics, session_id, phase)
    state = _budget_state(time_in_phase, budget)
    total_elapsed = int(session.get('total_elapsed_seconds') or 0)
    return {
        'current_phase': phase,
        'session_created_at': session.get('created_at'),
        'time_in_phase_sec': time_in_phase,
        'phase_budget_sec': budget,
        'budget_state': state,
        'total_elapsed_sec': total_elapsed,
        'next_action': _next_action(phase, state, total_elapsed),
    }


def _start_of_week_iso():
    """ISO timestamp of the start of Bryan's work week (Tuesday 00:00 local)."""
    now = _now().astimezone()  # local timezone
    # Bryan's work week starts Tuesday (per reference_bryan_work_week.md).
    # Python weekday(): Mon=0 .. Tue=1 .. Sun=6.
    days_since_tuesday = (now.weekday() - 1) % 7
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    start = start - timedelta(days=days_since_tuesday)
    return start.astimezone(timezone.utc).isoformat()


def week_context(companion_db, study_analytics):
    """Day name + sessions touched this week. Cheap, refreshed per page load."""
    now_local = _now().astimezone()
    day_name = now_local.strftime('%A')
    since = _start_of_week_iso()
    conn = companion_db._conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions "
            "WHERE created_at >= ? AND status = 'active'",
            (since,)).fetchone()
    finally:
        conn.close()
    return {
        'day_name': day_name,
        'sessions_this_week': int(row['n'] if row else 0),
    }
