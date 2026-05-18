"""Pastor feedback substrate (v2 §10.1).

Captures qualitative feedback from Bryan's wife (Olivia), elders, and
congregants. The capture path is currently Bryan typing what he heard
into a small form on /study/feedback/new — Olivia-direct channels
(email/SMS/Dropbox) are her separate decision and land on top of this
substrate when chosen.

The Editing Partner will read recent positive feedback as part of its
Phase 15 audit (per v2 §4 — "don't let positive evidence vanish under
the weight of pattern critique"). For now this is just the data layer.
"""

import sqlite3
from datetime import datetime, timezone


_VALID_VALENCE = {'positive', 'constructive', 'mixed'}
_VALID_CAPTURED_BY = {'bryan_typed', 'transcribed', 'pasted'}


class PastorFeedbackStore:
    def __init__(self, db_path):
        self.db_path = db_path

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def init_db(self):
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pastor_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_label TEXT NOT NULL,
                content TEXT NOT NULL,
                valence TEXT NOT NULL DEFAULT 'positive'
                    CHECK (valence IN ('positive', 'constructive', 'mixed')),
                sermon_id INTEGER,
                captured_by TEXT NOT NULL DEFAULT 'bryan_typed',
                captured_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pastor_feedback_recent
                ON pastor_feedback(captured_at DESC);
            CREATE INDEX IF NOT EXISTS idx_pastor_feedback_source
                ON pastor_feedback(source_label, captured_at DESC);
        """)
        conn.commit()
        conn.close()

    def add_feedback(self, *, source_label, content, valence='positive',
                     sermon_id=None, captured_by='bryan_typed'):
        if valence not in _VALID_VALENCE:
            raise ValueError(f"valence must be one of {_VALID_VALENCE!r}; got {valence!r}")
        if captured_by not in _VALID_CAPTURED_BY:
            raise ValueError(f"captured_by must be one of {_VALID_CAPTURED_BY!r}; got {captured_by!r}")
        conn = self._conn()
        cur = conn.execute(
            "INSERT INTO pastor_feedback "
            "(source_label, content, valence, sermon_id, captured_by, captured_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source_label, content, valence, sermon_id, captured_by, self._now()))
        conn.commit()
        fid = cur.lastrowid
        conn.close()
        return fid

    def recent_feedback(self, source_label=None, limit=20):
        clauses = []
        params = []
        if source_label is not None:
            clauses.append("source_label = ?")
            params.append(source_label)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        conn = self._conn()
        rows = conn.execute(
            f"SELECT * FROM pastor_feedback {where} "
            f"ORDER BY captured_at DESC LIMIT ?",
            params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def delete_feedback(self, feedback_id):
        conn = self._conn()
        conn.execute("DELETE FROM pastor_feedback WHERE id = ?", (feedback_id,))
        conn.commit()
        conn.close()

    def count_by_valence(self):
        conn = self._conn()
        rows = conn.execute(
            "SELECT valence, COUNT(*) AS n FROM pastor_feedback GROUP BY valence"
        ).fetchall()
        conn.close()
        return {r['valence']: r['n'] for r in rows}
