"""Accuser-voice detection — Cycle 2a of the EF-prosthesis design (v2 spec §9).

Detects four trigger categories in Bryan's outgoing messages and logs them for
calibration. Does NOT respond to or rebut the trigger — that lands in a later
cycle once detection precision has been measured against real data.

Categories (per v2 §9.1):
  - self_condemnation       — "I cheated", "I was lazy", "wasn't mine", etc.
  - asymmetric_attribution  — "the LLM wrote X", "Claude did Y"
  - punitive_standard       — "Prutow requires…", "Micah preaches…"
  - self_disqualifying      — "but it was on the fly", "just got lucky"

Design notes:
  - Conservative regex with word boundaries, case-insensitive.
  - High precision preferred over recall during calibration; refine from logs.
  - Short messages (<10 chars) skipped to reduce noise.
  - All matches for a single message are logged (one row per category match).
"""

import re
import sqlite3
from datetime import datetime, timezone


MIN_TEXT_LEN = 10  # skip very short messages (e.g., "ok", "yes")


# Pattern groups — each is (compiled regex, category, human-readable label).
_PATTERNS = []


def _add(pattern, category, label):
    _PATTERNS.append((re.compile(pattern, re.IGNORECASE), category, label))


# ── self_condemnation ────────────────────────────────────────────────────
_add(r"\bI\s+cheated\b", "self_condemnation", "I cheated")
_add(r"\bI(?:'m|\s+am)\s+lazy\b", "self_condemnation", "I am/I'm lazy")
_add(r"\bI\s+(?:was|got|feel|felt)\s+lazy\b", "self_condemnation", "I was/got/feel/felt lazy")
_add(r"\bI\s+(?:just\s+)?phoned\s+(?:it|this|that)\s+in\b", "self_condemnation", "I phoned it in")
_add(r"\bphoned\s+in\s+the\s+\w+", "self_condemnation", "phoned in the X")
_add(r"\bwasn'?t\s+mine\b", "self_condemnation", "wasn't mine")
_add(r"\bdidn'?t\s+(?:really\s+)?earn\b", "self_condemnation", "didn't earn")
_add(r"\bI\s+failed\s+(?:at|on|in|to)\b", "self_condemnation", "I failed at/on/in/to")
_add(r"\bI\s+(?:was|am)\s+(?:shallow|fake|fraudulent)\b", "self_condemnation", "I am/was shallow|fake|fraudulent")

# ── asymmetric_attribution ───────────────────────────────────────────────
_add(r"\bthe\s+(?:LLM|AI|model|assistant|bot)\s+(?:did|wrote|made|created|gave|came\s+up\s+with)\b",
     "asymmetric_attribution", "the LLM/AI/model/assistant did/wrote/...")
_add(r"\bClaude\s+(?:did|wrote|made|created|gave|came\s+up\s+with)\b",
     "asymmetric_attribution", "Claude did/wrote/...")

# ── punitive_standard ────────────────────────────────────────────────────
_AUTHORITIES = r"Prutow|Chapell|Macartney|Carrick|York|Beeke|Robinson|Perkins"
_PUNITIVE_VERBS = r"requires|demands|expects|insists|wants|teaches|preaches|says"
_add(rf"\b(?:{_AUTHORITIES})\s+(?:{_PUNITIVE_VERBS})\b",
     "punitive_standard", "<authority> requires/demands/expects/...")
_add(rf"\bMicah\s+(?:{_PUNITIVE_VERBS})\b",
     "punitive_standard", "Micah requires/demands/...")
_add(rf"\bPastor\s+[A-Z]\w+\s+(?:{_PUNITIVE_VERBS})\b",
     "punitive_standard", "Pastor X requires/...")
_add(rf"\bDr\.?\s+[A-Z]\w+\s+(?:{_PUNITIVE_VERBS})\b",
     "punitive_standard", "Dr. X requires/...")

# ── self_disqualifying ───────────────────────────────────────────────────
_add(r"\bbut\s+it\s+was\s+on\s+the\s+fly\b", "self_disqualifying", "but it was on the fly")
_add(r"\bbut\s+it\s+was\s+accidental\b", "self_disqualifying", "but it was accidental")
_add(r"\b(?:I\s+)?just\s+got\s+lucky\b", "self_disqualifying", "(I) just got lucky")
_add(r"\bjust\s+luck(?:y)?\b", "self_disqualifying", "just luck/lucky")
_add(r"\bfelt\s+but\s+not\s+seen\b", "self_disqualifying", "felt but not seen")


class AccuserDetector:
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
            CREATE TABLE IF NOT EXISTS accuser_detections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                session_id INTEGER,
                message_id INTEGER,
                surface TEXT,
                trigger_category TEXT NOT NULL,
                matched_pattern TEXT NOT NULL,
                matched_text TEXT NOT NULL,
                full_text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_accuser_session
                ON accuser_detections(session_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_accuser_category
                ON accuser_detections(trigger_category, timestamp);
        """)
        conn.commit()
        conn.close()

    def detect(self, text):
        """Return list of {category, label, matched_text} for every pattern hit."""
        if not text or len(text) < MIN_TEXT_LEN:
            return []
        hits = []
        for rx, category, label in _PATTERNS:
            for m in rx.finditer(text):
                hits.append({
                    "category": category,
                    "label": label,
                    "matched_text": m.group(0),
                })
        return hits

    def analyze_message(self, *, session_id, message_id, surface, text):
        """Detect + log. Returns number of detections logged."""
        if not text or len(text) < MIN_TEXT_LEN:
            return 0
        hits = self.detect(text)
        if not hits:
            return 0
        now = self._now()
        conn = self._conn()
        for h in hits:
            conn.execute(
                "INSERT INTO accuser_detections "
                "(timestamp, session_id, message_id, surface, "
                "trigger_category, matched_pattern, matched_text, full_text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (now, session_id, message_id, surface,
                 h["category"], h["label"], h["matched_text"], text))
        conn.commit()
        conn.close()
        return len(hits)

    def get_detections(self, session_id=None, category=None, limit=500):
        clauses = []
        params = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if category is not None:
            clauses.append("trigger_category = ?")
            params.append(category)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        conn = self._conn()
        rows = conn.execute(
            f"SELECT * FROM accuser_detections {where} "
            f"ORDER BY timestamp DESC LIMIT ?",
            params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
