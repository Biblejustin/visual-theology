"""Session decision tracking — per codex adversarial review item #7.

ADHD work can look active while avoiding decisions. The system measures
time but not commitment. This module tracks the discrete homiletical
commitments Bryan makes across Phase 13 + key Phase 14 points so the
Composing Partner knows what's locked vs still open.

Decision keys map to Bryan's stated sub-phases:
  - Phase 13.1 Subject
  - Phase 13.2 Theme
  - Phase 13.3 EP/OTM
  - Phase 13.4 Transition
  - Phase 13.5 Exegetical Outline
  - Phase 13.6 FCF
  - Phase 13.7 HP/THT
  - Phase 13.8 Purpose Statement
  - target_length_min (the spoken-length commitment for Editing)

One row per (session_id, key). Upsert semantics — re-setting replaces
the value (and updates modified_at).
"""

import sqlite3
from datetime import datetime, timezone


DECISION_KEYS = (
    'subject',
    'theme',
    'ep_otm',
    'transition',
    'exegetical_outline',
    'fcf',
    'hp_tht',
    'purpose_statement',
    'target_length_min',
)

_DECISION_LABELS = {
    'subject':           'Subject',
    'theme':             'Theme',
    'ep_otm':            'EP / OTM',
    'transition':        'Transition',
    'exegetical_outline':'Exegetical Outline',
    'fcf':               'FCF',
    'hp_tht':            'HP / THT',
    'purpose_statement': 'Purpose Statement',
    'target_length_min': 'Target length (min)',
}


class SessionDecisionStore:
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
            CREATE TABLE IF NOT EXISTS session_decisions (
                session_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                committed_at TEXT NOT NULL,
                modified_at TEXT NOT NULL,
                PRIMARY KEY (session_id, key)
            );
            CREATE INDEX IF NOT EXISTS idx_session_decisions_session
                ON session_decisions(session_id);
        """)
        conn.commit()
        conn.close()

    def set_decision(self, *, session_id, key, value):
        if key not in DECISION_KEYS:
            raise ValueError(f"unknown decision key: {key!r}")
        now = self._now()
        conn = self._conn()
        conn.execute(
            "INSERT INTO session_decisions (session_id, key, value, committed_at, modified_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(session_id, key) DO UPDATE SET "
            "  value = excluded.value, modified_at = excluded.modified_at",
            (session_id, key, value, now, now))
        conn.commit()
        conn.close()

    def get_decision(self, session_id, key):
        conn = self._conn()
        row = conn.execute(
            "SELECT value FROM session_decisions WHERE session_id = ? AND key = ?",
            (session_id, key)).fetchone()
        conn.close()
        return row['value'] if row else None

    def list_decisions(self, session_id):
        conn = self._conn()
        rows = conn.execute(
            "SELECT key, value, committed_at, modified_at FROM session_decisions "
            "WHERE session_id = ? ORDER BY committed_at",
            (session_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def clear_decision(self, session_id, key):
        conn = self._conn()
        conn.execute(
            "DELETE FROM session_decisions WHERE session_id = ? AND key = ?",
            (session_id, key))
        conn.commit()
        conn.close()

    def open_decision_keys(self, session_id):
        committed = {r['key'] for r in self.list_decisions(session_id)}
        return [k for k in DECISION_KEYS if k not in committed]

    def format_decision_summary(self, session_id):
        """Multi-line summary suitable for the Composing Partner prompt."""
        decisions = self.list_decisions(session_id)
        if not decisions:
            return ('No decisions committed yet for this session. All open: ' +
                    ', '.join(_DECISION_LABELS[k] for k in DECISION_KEYS) + '.')
        committed_keys = {d['key'] for d in decisions}
        lines = ['Committed decisions:']
        for d in decisions:
            label = _DECISION_LABELS.get(d['key'], d['key'])
            lines.append(f"  ✓ {label}: {d['value']}")
        open_labels = [_DECISION_LABELS[k] for k in DECISION_KEYS if k not in committed_keys]
        if open_labels:
            lines.append('Open: ' + ', '.join(open_labels))
        else:
            lines.append('All decisions committed.')
        return '\n'.join(lines)
