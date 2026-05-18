"""Pattern review aggregators for the /study/patterns panel.

Each function returns small dicts/lists ready to render in a Jinja template.
No HTML, no Flask — pure data so they're trivially testable in isolation.
"""

from datetime import datetime, timedelta, timezone


def _cutoff_iso(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ── Cutoff (daily_5pm / friday_3pm / weekend_lockout) ──────────────────

def cutoff_summary(study_analytics, days=30):
    """Per-type cutoff event counts + dismissed counts in the window."""
    since = _cutoff_iso(days)
    conn = study_analytics._conn()
    try:
        rows = conn.execute("""
            SELECT cutoff_type,
                   COUNT(*) AS n,
                   SUM(dismissed) AS dismissed,
                   COUNT(DISTINCT route) AS unique_routes
            FROM cutoff_events
            WHERE timestamp >= ?
            GROUP BY cutoff_type
            ORDER BY n DESC
        """, (since,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def cutoff_recent_dismissed_rate(study_analytics, days=30):
    """Fraction of cutoff events in the window that Bryan dismissed
    (i.e., overrode by clicking through). 0.0 if no events."""
    since = _cutoff_iso(days)
    conn = study_analytics._conn()
    try:
        row = conn.execute("""
            SELECT COUNT(*) AS n, SUM(dismissed) AS d
            FROM cutoff_events
            WHERE timestamp >= ?
        """, (since,)).fetchone()
    finally:
        conn.close()
    if not row or not row['n']:
        return 0.0
    return float(row['d'] or 0) / float(row['n'])


# ── Accuser-voice detection ────────────────────────────────────────────

def accuser_category_summary(accuser_detector, days=30):
    """Per-category accuser-detection counts in the window."""
    since = _cutoff_iso(days)
    conn = accuser_detector._conn()
    try:
        rows = conn.execute("""
            SELECT trigger_category, COUNT(*) AS n
            FROM accuser_detections
            WHERE timestamp >= ?
            GROUP BY trigger_category
            ORDER BY n DESC
        """, (since,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def accuser_recent_examples(accuser_detector, limit=10):
    """Most-recent accuser detections with the matched text + a context excerpt."""
    conn = accuser_detector._conn()
    try:
        rows = conn.execute("""
            SELECT id, timestamp, session_id, surface,
                   trigger_category, matched_pattern, matched_text,
                   substr(full_text, 1, 200) AS excerpt
            FROM accuser_detections
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ── Session activity ──────────────────────────────────────────────────

def recent_session_activity(companion_db, days=30):
    """Lightweight session activity stats — count of active sessions in the window."""
    since = _cutoff_iso(days)
    conn = companion_db._conn()
    try:
        row = conn.execute("""
            SELECT COUNT(*) AS active
            FROM sessions
            WHERE created_at >= ? AND status = 'active'
        """, (since,)).fetchone()
    finally:
        conn.close()
    return {'active_sessions': (row['active'] if row else 0)}
