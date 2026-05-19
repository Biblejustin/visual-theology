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

# Total sermon-prep budget = sum of every phase's individual budget. The 16-phase
# workflow targets roughly 13.5 hours per sermon. This is the second-scale
# countdown ring (after the per-phase ring) so Bryan can see both 'time left
# in THIS phase' and 'time left in THIS sermon' at the same glance.
SERMON_BUDGET_SEC = sum(PHASE_TIMERS.values())


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
    event or the timestamp can't be parsed. Returns 0 if study_analytics
    isn't initialized (tests that don't set it up shouldn't crash).
    """
    if study_analytics is None:
        return 0
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


# Next-action phrasing pools, rotated by day-of-year so the strip doesn't
# habituate (per anti-habituation research: same notification at same time
# gets filtered out within days for ADHD brains). Each pool keys to a
# (priority, condition) and provides 3-4 variants. Order in _next_action()
# determines precedence — most urgent state wins.
_NEXT_ACTION_POOL = {
    'sermon_over': [
        "Past the 13.5h sermon budget. Whatever you have is what you preach.",
        "Sermon hours exhausted. Time to compose with what's already here.",
        "Past the sermon budget. Trust the work the Lord gave you this week.",
    ],
    'phase_over': [
        "You're past the {phase} budget. Consider moving on.",
        "{phase} budget is spent. The next phase is waiting.",
        "Past {phase} time. Land what's open and advance.",
    ],
    'sermon_warn': [
        "Sermon budget 80% spent. Time to start narrowing.",
        "Half the sermon hours left. Choose what stays in.",
        "Sermon budget thinning — Phase 13 is closer than it feels.",
    ],
    'phase_warn': [
        "Approaching the {phase} budget. Wrap this thread soon.",
        "{phase} budget nearly spent. Bring it to a landing.",
        "Almost out of {phase} time. One more push, then move.",
    ],
    'long_session': [
        "You've been in this session over 4 hours. Take a break.",
        "4+ hours in this session. Step away — your brain needs reset.",
        "Long session. A 10-min walk would compound the next hour.",
    ],
    # ── DopBoost affirmations (v2 spec §5, arxiv 2507.06864) ─────────
    # Soft 1-line surfaced when budgets are HEALTHY and meaningful work is
    # underway. Triggered only on positive patterns — anti-shame design.
    'affirmation': [
        "Good pace through {phase}. The work is real.",
        "On budget today — keep this rhythm.",
        "Solid prep happening. The Lord blesses faithful work.",
        "You're in the zone. Trust this pace.",
        "Steady week so far. This is what good sermon weeks look like.",
        "Right where you should be in {phase}. Carry on.",
    ],
}


def _rotated_phrasing(pool_key, **fmt):
    """Pick a phrasing for `pool_key` rotated by day-of-year."""
    from datetime import datetime as _dt
    pool = _NEXT_ACTION_POOL.get(pool_key, [])
    if not pool:
        return ''
    idx = _dt.now().timetuple().tm_yday % len(pool)
    try:
        return pool[idx].format(**fmt)
    except (KeyError, IndexError):
        return pool[idx]


def _next_action(phase, budget_state, total_elapsed_sec,
                 sermon_budget_state=None,
                 time_in_phase_sec=0):
    """One-line suggestion for what's next. Precedence: most urgent wins.
    When nothing is urgent and meaningful work is happening, surfaces a
    DopBoost-style affirmation per ADHD-productivity research (positive-
    pattern recognition, not always-on)."""
    if sermon_budget_state == BUDGET_OVER:
        return _rotated_phrasing('sermon_over')
    if budget_state == BUDGET_OVER:
        return _rotated_phrasing('phase_over', phase=phase)
    if sermon_budget_state == BUDGET_WARN:
        return _rotated_phrasing('sermon_warn')
    if budget_state == BUDGET_WARN:
        return _rotated_phrasing('phase_warn', phase=phase)
    if total_elapsed_sec >= 4 * 3600:
        return _rotated_phrasing('long_session')
    # DopBoost affirmation conditions (all must hold):
    # - budgets are healthy
    # - meaningful in-phase work (>=5 min)
    # - meaningful session work (>=10 min total)
    # Per arxiv 2507.06864: triggered only on positive patterns, never
    # always-on. The strip otherwise stays empty in healthy state.
    if (budget_state == BUDGET_OK
            and sermon_budget_state in (None, BUDGET_OK)
            and time_in_phase_sec >= 5 * 60
            and total_elapsed_sec >= 10 * 60):
        return _rotated_phrasing('affirmation', phase=phase)
    return ""


def _accumulate_sermon_elapsed(companion_db, session):
    """Sum total_elapsed_seconds across this session AND any series siblings.

    For a standalone session this is just its own elapsed. For a series child
    (parent_session_id set) it sums parent + every child so the sermon-scale
    ring reflects work across the whole prep arc, not just this one session.
    """
    total = int(session.get('total_elapsed_seconds') or 0)
    parent_id = session.get('parent_session_id')
    # Walk the series — pick whichever node we have, then read its children.
    if parent_id:
        try:
            parent = companion_db.get_parent(session['id'])
            if parent:
                total = int(parent.get('total_elapsed_seconds') or 0)
                for child in companion_db.get_children(parent['id']):
                    if child['id'] != session['id']:
                        total += int(child.get('total_elapsed_seconds') or 0)
                # Add this session's elapsed too (skipped above)
                total += int(session.get('total_elapsed_seconds') or 0)
        except Exception:
            pass
    else:
        try:
            for child in companion_db.get_children(session['id']):
                total += int(child.get('total_elapsed_seconds') or 0)
        except Exception:
            pass
    return total


def assess_session(companion_db, study_analytics, session_id):
    """Return a status dict the time-coach UI can render.

    Returns None if the session doesn't exist. All time values are
    integer seconds. budget_state is one of BUDGET_OK / BUDGET_WARN /
    BUDGET_OVER. next_action is a short string (may be empty).
    """
    session = companion_db.get_session(session_id)
    if not session:
        return None

    # Two paths:
    #   - Sessions where the v2 picker has been touched (v2_phase NOT NULL):
    #     use v2 metadata for budget + phase_events keyed by the v2 key.
    #   - Legacy / pre-picker sessions: stay on the original current_phase
    #     path so the existing budget semantics + tests still hold.
    explicit_v2 = session.get('v2_phase')
    if explicit_v2:
        from v2_phases import get_phase as get_v2_phase
        v2_row = get_v2_phase(explicit_v2)
        phase = v2_row.key if v2_row else explicit_v2
        budget = (v2_row.budget_min * 60) if (v2_row and v2_row.budget_min) else None
        time_in_phase = _seconds_in_current_phase(study_analytics, session_id, phase)
        # If v2 events haven't accumulated yet, fall back to legacy elapsed
        # so the Time Coach has SOMETHING to show during the first session
        # after the picker fires.
        if time_in_phase == 0 and session.get('current_phase'):
            time_in_phase = _seconds_in_current_phase(
                study_analytics, session_id, session['current_phase'])
    else:
        phase = session['current_phase']
        budget = PHASE_TIMERS.get(phase)
        time_in_phase = _seconds_in_current_phase(study_analytics, session_id, phase)
    state = _budget_state(time_in_phase, budget)
    total_elapsed = int(session.get('total_elapsed_seconds') or 0)
    sermon_elapsed = _accumulate_sermon_elapsed(companion_db, session)
    sermon_remaining = max(0, SERMON_BUDGET_SEC - sermon_elapsed)
    sermon_state = _budget_state(sermon_elapsed, SERMON_BUDGET_SEC)
    phase_remaining = None
    if budget is not None:
        phase_remaining = max(0, budget - time_in_phase)
    return {
        'current_phase': phase,
        'session_created_at': session.get('created_at'),
        'time_in_phase_sec': time_in_phase,
        'phase_budget_sec': budget,
        'phase_remaining_sec': phase_remaining,
        'budget_state': state,
        'total_elapsed_sec': total_elapsed,
        'sermon_elapsed_sec': sermon_elapsed,
        'sermon_budget_sec': SERMON_BUDGET_SEC,
        'sermon_remaining_sec': sermon_remaining,
        'sermon_budget_state': sermon_state,
        'next_action': _next_action(phase, state, total_elapsed,
                                    sermon_budget_state=sermon_state,
                                    time_in_phase_sec=time_in_phase),
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


def prep_window_position():
    """Compute Bryan's position in the sermon-prep window (Tue 00:00 → Fri 15:00).

    Returns a dict the horizon-bar partial can render:
      - state: 'in_window' | 'before_window' | 'lockout' | 'preach_day' | 'day_off'
      - fraction: 0.0–1.0 progress through the window (None outside it)
      - color: 'green' | 'amber' | 'red' | 'lockout' | 'muted'
      - label: short human-readable status

    Window boundaries (per reference_bryan_work_week):
      - Tue 00:00 local = prep window opens
      - Fri 15:00 local = hard cutoff (sermon prep window closes)
      - Sat all day + Sun before 6:30am = weekend lockout
      - Sun 6:30am+ = preach day (preaching + pastoral, not sermon prep)
      - Mon = day off
    """
    from datetime import timedelta
    now = _now().astimezone()
    wd = now.weekday()  # Mon=0 .. Tue=1 .. Sun=6
    if wd == 0:  # Monday
        return {'state': 'day_off', 'fraction': None, 'color': 'muted',
                'label': "Monday — day off"}
    if wd == 6:  # Sunday
        if now.hour >= 6 and (now.hour > 6 or now.minute >= 30):
            return {'state': 'preach_day', 'fraction': None, 'color': 'muted',
                    'label': "Sunday — preach day"}
        return {'state': 'lockout', 'fraction': None, 'color': 'lockout',
                'label': "Weekend lockout"}
    if wd == 5:  # Saturday
        return {'state': 'lockout', 'fraction': None, 'color': 'lockout',
                'label': "Weekend lockout"}
    # Tue (1) – Fri (4): inside the prep window (or past Fri 3pm)
    # Anchor Tuesday 00:00 local
    tue_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=(wd - 1))
    fri_cutoff = tue_start + timedelta(days=3, hours=15)  # Tue + 3d = Fri; +15h = 15:00
    window_total = (fri_cutoff - tue_start).total_seconds()
    elapsed = (now - tue_start).total_seconds()
    if now >= fri_cutoff:  # Fri after 3pm — past hard cutoff but still Friday
        return {'state': 'lockout', 'fraction': 1.0, 'color': 'lockout',
                'label': "Past Friday 3pm — prep window closed"}
    fraction = max(0.0, min(1.0, elapsed / window_total))
    if fraction >= 0.90:
        color = 'red'
    elif fraction >= 0.60:
        color = 'amber'
    else:
        color = 'green'
    # Friendly label includes time remaining
    remaining = (fri_cutoff - now).total_seconds()
    rem_hours = int(remaining // 3600)
    rem_min = int((remaining % 3600) // 60)
    if rem_hours >= 24:
        rem_label = f"{rem_hours // 24}d {rem_hours % 24}h to Friday 3pm"
    elif rem_hours >= 1:
        rem_label = f"{rem_hours}h {rem_min}m to Friday 3pm"
    else:
        rem_label = f"{rem_min}m to Friday 3pm"
    return {'state': 'in_window', 'fraction': fraction, 'color': color,
            'label': rem_label}
