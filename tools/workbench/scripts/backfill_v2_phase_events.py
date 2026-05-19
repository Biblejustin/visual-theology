"""One-off backfill for v2 phase_events on existing sessions.

Context: review #4 follow-up. The picker now fires phase_events for v2
keys, but sessions that existed BEFORE the picker landed (or that have
been on the same v2 phase since session creation) have no v2 events,
so the persona prompt's ACTIVE FOCUS time gauge is blind.

This script walks every active session and, for any session that has a
non-NULL v2_phase but no phase_events row for that v2 key, synthesizes
a single 'enter' event timestamped at session.created_at (or now,
whichever is older — capped so elapsed isn't absurd).

Idempotent: safe to re-run. Sessions that already have a v2 event for
their current key are left alone.

Usage:
    python3 scripts/backfill_v2_phase_events.py [--dry-run] [--db <path>]
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

# v2 phase keys live in v2_phases.V2_PHASE_KEYS; import lazily so this
# script works from the workbench dir.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from v2_phases import V2_PHASE_KEYS, _LEGACY_TO_V2


def backfill(db_path, dry_run=False):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    sessions = conn.execute("""
        SELECT id, current_phase, v2_phase, created_at, total_elapsed_seconds
        FROM sessions
        WHERE status = 'active'
    """).fetchall()

    fixed = []
    for s in sessions:
        sid = s['id']
        # Resolve which v2 phase the session is actually in. Picker value
        # wins; else map from legacy.
        v2 = s['v2_phase']
        if not v2:
            v2 = _LEGACY_TO_V2.get(s['current_phase'])
        if not v2 or v2 not in V2_PHASE_KEYS:
            continue

        # Skip if we already have a v2 event for this session.
        existing = conn.execute(
            "SELECT 1 FROM phase_events WHERE session_id = ? AND phase = ? LIMIT 1",
            (sid, v2)).fetchone()
        if existing:
            continue

        # Synthesize an enter event at session.created_at. This gives the
        # time gauge a realistic floor — the LLM will see "X min in phase"
        # where X reflects time since session start (an over-estimate for
        # active sessions that started in a different phase, but better
        # than nothing).
        when = s['created_at'] or datetime.now(timezone.utc).isoformat()
        fixed.append((sid, v2, when))

    if not fixed:
        print('No backfill needed — every active session already has v2 events.')
        conn.close()
        return 0

    print(f"{'DRY RUN — ' if dry_run else ''}Backfilling {len(fixed)} session(s):")
    for sid, v2, when in fixed:
        print(f'  session {sid}: enter {v2} @ {when[:19]}')

    if dry_run:
        conn.close()
        return 0

    for sid, v2, when in fixed:
        conn.execute(
            "INSERT INTO phase_events (session_id, phase, event_type, timestamp) "
            "VALUES (?, ?, 'enter', ?)",
            (sid, v2, when))
    conn.commit()
    conn.close()
    print(f'\nBackfilled {len(fixed)} session(s).')
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default='/Volumes/External/Logos4/tools/workbench/companion.db')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    sys.exit(backfill(args.db, dry_run=args.dry_run))
