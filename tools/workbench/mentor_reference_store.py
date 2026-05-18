"""Mentor reference store (Cycle 2c) — v2 spec §6.2.

Read-only store of actual stated positions from homileticians Bryan invokes
(Prutow, Chapell, Robinson, Macartney, Carrick, York, Beeke, Perkins, plus
local mentors like Micah Ramsey). Future accuser-voice rebuttal (v2 §9)
cites these to test the invoked-version of an authority against the
actual-version when Bryan's memory has drifted toward a phantom more punitive
than the source actually warrants.

Design constraints:
  - NO paraphrased-from-training-data entries. Every row carries an explicit
    source title + locatable citation (chapter/page/section). Entries start
    `verified=0` until Bryan confirms against his actual library copy.
  - The seed contains only high-confidence positions widely documented in
    published pastoral literature. Anything less certain is left for Bryan
    (or a future Logos-library integration tool) to populate.
"""

import sqlite3
from datetime import datetime, timezone


class MentorReferenceStore:
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
            CREATE TABLE IF NOT EXISTS mentor_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mentor_key TEXT NOT NULL,
                source_title TEXT NOT NULL,
                topic TEXT NOT NULL,
                position_text TEXT NOT NULL,
                citation TEXT NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(mentor_key, source_title, topic, position_text)
            );
            CREATE INDEX IF NOT EXISTS idx_mentor_references_mentor
                ON mentor_references(mentor_key);
            CREATE INDEX IF NOT EXISTS idx_mentor_references_topic
                ON mentor_references(topic);
        """)
        conn.commit()
        conn.close()

    def add_reference(self, *, mentor_key, source_title, topic,
                      position_text, citation, verified=False, notes=None):
        conn = self._conn()
        cur = conn.execute(
            "INSERT OR IGNORE INTO mentor_references "
            "(mentor_key, source_title, topic, position_text, citation, "
            " verified, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (mentor_key, source_title, topic, position_text, citation,
             1 if verified else 0, notes, self._now()))
        conn.commit()
        rid = cur.lastrowid
        conn.close()
        return rid

    def get_references(self, mentor_key=None, topic=None, verified_only=False):
        clauses = []
        params = []
        if mentor_key is not None:
            clauses.append("mentor_key = ?")
            params.append(mentor_key)
        if topic is not None:
            clauses.append("topic = ?")
            params.append(topic)
        if verified_only:
            clauses.append("verified = 1")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self._conn()
        rows = conn.execute(
            f"SELECT * FROM mentor_references {where} "
            f"ORDER BY mentor_key, topic, id",
            params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def mark_verified(self, reference_id):
        conn = self._conn()
        conn.execute(
            "UPDATE mentor_references SET verified = 1 WHERE id = ?",
            (reference_id,))
        conn.commit()
        conn.close()

    def delete_reference(self, reference_id):
        conn = self._conn()
        conn.execute(
            "DELETE FROM mentor_references WHERE id = ?", (reference_id,))
        conn.commit()
        conn.close()

    def list_mentors(self):
        conn = self._conn()
        rows = conn.execute(
            "SELECT DISTINCT mentor_key FROM mentor_references ORDER BY mentor_key"
        ).fetchall()
        conn.close()
        return [r["mentor_key"] for r in rows]

    def seed_examples(self):
        """Insert a small set of high-confidence starter entries.

        Each entry includes an explicit citation Bryan can verify in his
        library. All start unverified — Bryan flips the verified flag after
        checking the actual source. Returns the count of entries currently
        present after seeding (de-duped by UNIQUE constraint).

        The starter set is deliberately small. Real population happens (a)
        as Bryan annotates positions while reading, or (b) via a future
        Logos-library extraction tool that pulls actual indexed quotes.
        """
        # ONE starter that's about as well-documented as homiletics positions
        # get: Prutow's central thesis is the title of his book.
        starters = [
            dict(
                mentor_key="prutow",
                source_title="So, Pastor, What's Your Point?",
                topic="main_point",
                position_text=(
                    "A sermon should have one main point — a single, "
                    "comprehensive, clear, brief, memorable statement of the "
                    "burden of the text. (Verify exact wording and any "
                    "stated word-count guidance against the actual text.)"
                ),
                citation="Prutow, D. M., So, Pastor, What's Your Point? — "
                         "see the central-thesis discussion in the opening "
                         "chapters (verify exact pages).",
                notes=(
                    "Seeded as a starter example for the reference store. "
                    "Marked unverified pending Bryan's confirmation against "
                    "his actual library copy. Future Logos-library extraction "
                    "should replace the paraphrase with the actual quoted text."
                ),
            ),
        ]
        for s in starters:
            self.add_reference(**s)
        return len(self.get_references())
