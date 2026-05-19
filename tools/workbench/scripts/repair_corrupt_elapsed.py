"""One-off repair for inflated total_elapsed_seconds (bug review #6).

Pre-cc9b914 (the tick-gap timer fix), the studyClock would happily
recompute elapsed = Date.now() - startTime across laptop sleeps. The
client could POST 898 years to /clock and the server (pre-cfa68f8) would
write it. This left the live DB with implausible values that distort
the Time Coach sermon ring and the patterns panel.

Repair rule: cap each session's total_elapsed_seconds to
min(now - created_at, 14 days). 14 days matches the new clock
endpoint's MAX_REASONABLE_SECONDS ceiling, and the session-age cap
covers younger sessions (you can't have spent more time in a session
than it has existed).

Usage:
    python3 scripts/repair_corrupt_elapsed.py [--dry-run] [--db <path>]

Default DB path matches the live companion.db.
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone

MAX_SECONDS = 14 * 24 * 3600  # 14 days


def repair(db_path, dry_run=False):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, passage_ref, created_at, total_elapsed_seconds "
        "FROM sessions WHERE total_elapsed_seconds IS NOT NULL"
    ).fetchall()

    now = datetime.now(timezone.utc)
    fixes = []
    for r in rows:
        elapsed = int(r['total_elapsed_seconds'] or 0)
        # Cap by both session age AND the 14-day absolute ceiling
        try:
            created = datetime.fromisoformat(r['created_at'])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age = int((now - created).total_seconds())
        except (ValueError, TypeError):
            age = MAX_SECONDS
        ceiling = min(max(0, age), MAX_SECONDS)
        if elapsed > ceiling:
            fixes.append((r['id'], r['passage_ref'], elapsed, ceiling))

    if not fixes:
        print("No corrupt rows found.")
        conn.close()
        return 0

    print(f"{'DRY RUN — ' if dry_run else ''}Repairing {len(fixes)} session(s):")
    for sid, passage, old, new in fixes:
        old_h = old / 3600.0
        new_h = new / 3600.0
        print(f"  session {sid} ({passage}): {old_h:.1f}h → {new_h:.1f}h")

    if dry_run:
        print("\nDry run — no DB changes made. Re-run without --dry-run to apply.")
        conn.close()
        return 0

    for sid, _passage, _old, new in fixes:
        conn.execute(
            "UPDATE sessions SET total_elapsed_seconds = ?, "
            "updated_at = ? WHERE id = ?",
            (new, now.isoformat(), sid))
    conn.commit()
    conn.close()
    print(f"\nRepaired {len(fixes)} session(s).")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="/Volumes/External/Logos4/tools/workbench/companion.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sys.exit(repair(args.db, dry_run=args.dry_run))
