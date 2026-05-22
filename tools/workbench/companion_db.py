import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timezone

PHASE_TIMERS = {
    'prayer': 900,
    'text_work': 7200,
    'digestion': 3600,
    'observation': 3600,
    'word_study': 1800,
    'context': 2700,
    'theological': 7200,
    'commentary': 1800,
    'exegetical_point': 1800,
    'fcf_homiletical': 1800,
    'sermon_construction': 10800,
    'edit_pray': 5400,
}

PHASES_ORDER = list(PHASE_TIMERS.keys())

class CompanionDB:
    def __init__(self, db_path):
        self.db_path = db_path

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _safe_write(self):
        """Connection context manager for write paths.

        Polish #3 (project assessment). Replaces the
        try/except/rollback/finally boilerplate that was applied
        unevenly across write helpers — some had it (post bug review
        #5), some didn't, some had rollback but no close discipline.

        Usage:
            with self._safe_write() as conn:
                conn.execute("INSERT ...", (...))
                return cur.lastrowid    # commit on normal exit
                                        # rollback on exception
                                        # close always

        Read helpers can stay as plain try/finally — bug review #5
        only flagged the write path because that's where rollbacks
        matter and connection leaks block subsequent writers.
        """
        conn = self._conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def init_db(self):
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                passage_ref TEXT NOT NULL,
                book INTEGER NOT NULL,
                chapter INTEGER NOT NULL,
                verse_start INTEGER,
                verse_end INTEGER,
                genre TEXT NOT NULL,
                current_phase TEXT NOT NULL DEFAULT 'prayer',
                current_question_id INTEGER,
                timer_remaining_seconds INTEGER NOT NULL DEFAULT 900,
                timer_paused INTEGER NOT NULL DEFAULT 0,
                total_elapsed_seconds INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS card_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id),
                phase TEXT NOT NULL,
                question_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                saved_to_outline INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id),
                phase TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_name TEXT,
                tool_input TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outline_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id),
                parent_id INTEGER REFERENCES outline_nodes(id),
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                verse_ref TEXT,
                rank INTEGER NOT NULL DEFAULT 0,
                flags TEXT DEFAULT '{}',
                source_type TEXT,
                source_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS question_bank (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phase TEXT NOT NULL,
                question_text TEXT NOT NULL,
                guidance_text TEXT,
                genre_tags TEXT NOT NULL DEFAULT '[]',
                priority TEXT NOT NULL DEFAULT 'core',
                resource_type TEXT DEFAULT 'none',
                rank INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS card_annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id),
                phase TEXT NOT NULL,
                source TEXT,
                starred_text TEXT,
                note TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS card_notepads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id),
                phase TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(session_id, phase)
            );
            CREATE INDEX IF NOT EXISTS idx_card_responses_session ON card_responses(session_id, phase);
            CREATE INDEX IF NOT EXISTS idx_conversation_session ON conversation_messages(session_id, phase);
            CREATE INDEX IF NOT EXISTS idx_outline_session ON outline_nodes(session_id);
            CREATE INDEX IF NOT EXISTS idx_outline_parent ON outline_nodes(parent_id);
            CREATE INDEX IF NOT EXISTS idx_questions_phase ON question_bank(phase, priority);
            CREATE INDEX IF NOT EXISTS idx_card_annotations_session ON card_annotations(session_id, phase);
            CREATE INDEX IF NOT EXISTS idx_card_notepads_session ON card_notepads(session_id, phase);
        """)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sermons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sermonaudio_id TEXT UNIQUE NOT NULL,
                broadcaster_id TEXT NOT NULL,
                title TEXT NOT NULL,
                speaker_name TEXT,
                event_type TEXT,
                series TEXT,
                preach_date TEXT,
                publish_date TEXT,
                duration_seconds INTEGER,
                bible_text_raw TEXT,
                book INTEGER,
                chapter INTEGER,
                verse_start INTEGER,
                verse_end INTEGER,
                audio_url TEXT,
                transcript_text TEXT,
                transcript_source TEXT DEFAULT 'sermonaudio',
                sermon_type TEXT NOT NULL DEFAULT 'expository' CHECK (sermon_type IN ('expository','topical')),
                classified_as TEXT NOT NULL CHECK (classified_as IN ('sermon','skipped')),
                classification_reason TEXT,
                metadata_hash TEXT,
                transcript_hash TEXT,
                source_version INTEGER NOT NULL DEFAULT 1,
                remote_updated_at TEXT,
                sync_status TEXT NOT NULL DEFAULT 'pending_sync' CHECK (sync_status IN (
                    'pending_sync','synced_metadata','transcript_ready',
                    'analysis_pending','analysis_running','review_ready',
                    'sync_failed','analysis_failed','analysis_skipped','permanent_failure'
                )),
                sync_error TEXT,
                failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
                last_failure_at TEXT,
                last_state_change_at TEXT NOT NULL,
                last_match_attempt_at TEXT,
                match_status TEXT NOT NULL DEFAULT 'unmatched' CHECK (match_status IN (
                    'unmatched','matched','awaiting_candidates',
                    'unparseable_passage','topical_no_match','rejected_all'
                )),
                is_remote_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_remote_deleted IN (0, 1)),
                deleted_at TEXT,
                ui_last_seen_version INTEGER NOT NULL DEFAULT 0,
                first_synced_at TEXT NOT NULL,
                last_synced_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sermons_sermonaudio_id ON sermons(sermonaudio_id);
            CREATE INDEX IF NOT EXISTS idx_sermons_preach_date ON sermons(preach_date DESC);
            CREATE INDEX IF NOT EXISTS idx_sermons_sync_status ON sermons(sync_status);
            CREATE INDEX IF NOT EXISTS idx_sermons_classified ON sermons(classified_as);
            CREATE INDEX IF NOT EXISTS idx_sermons_match_status ON sermons(match_status);
            CREATE INDEX IF NOT EXISTS idx_sermons_book_chapter ON sermons(book, chapter);

            CREATE TABLE IF NOT EXISTS sermon_passages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sermon_id INTEGER NOT NULL REFERENCES sermons(id) ON DELETE CASCADE,
                rank INTEGER NOT NULL,
                book INTEGER NOT NULL,
                chapter_start INTEGER NOT NULL,
                verse_start INTEGER,
                chapter_end INTEGER NOT NULL,
                verse_end INTEGER,
                raw_text TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(sermon_id, rank)
            );
            CREATE INDEX IF NOT EXISTS idx_sermon_passages_sermon ON sermon_passages(sermon_id);
            CREATE INDEX IF NOT EXISTS idx_sermon_passages_lookup ON sermon_passages(book, chapter_start);

            CREATE TABLE IF NOT EXISTS sermon_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sermon_id INTEGER NOT NULL REFERENCES sermons(id) ON DELETE CASCADE,
                session_id INTEGER NOT NULL REFERENCES sessions(id),
                link_status TEXT NOT NULL CHECK (link_status IN ('active','candidate','rejected')),
                link_source TEXT NOT NULL CHECK (link_source IN ('auto','manual')),
                match_reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(sermon_id, session_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sermon_links_one_active
                ON sermon_links(sermon_id) WHERE link_status = 'active';
            CREATE INDEX IF NOT EXISTS idx_sermon_links_sermon ON sermon_links(sermon_id);
            CREATE INDEX IF NOT EXISTS idx_sermon_links_session ON sermon_links(session_id);

            CREATE TABLE IF NOT EXISTS sermon_reviews (
                sermon_id INTEGER PRIMARY KEY REFERENCES sermons(id) ON DELETE CASCADE,
                analyzer_version TEXT NOT NULL,
                homiletics_core_version TEXT NOT NULL,
                model_version TEXT,
                analyzed_transcript_hash TEXT NOT NULL,
                source_version_at_analysis INTEGER NOT NULL,
                burden_clarity TEXT CHECK (burden_clarity IN ('crisp','clear','implied','muddled','absent')),
                burden_statement_excerpt TEXT,
                burden_first_stated_at_sec INTEGER,
                movement_clarity TEXT CHECK (movement_clarity IN ('river','mostly_river','uneven','lake')),
                movement_rationale TEXT,
                application_specificity TEXT CHECK (application_specificity IN ('localized','concrete','abstract','absent')),
                application_first_arrived_at_sec INTEGER,
                application_excerpts TEXT,
                ethos_rating TEXT CHECK (ethos_rating IN ('seized','engaged','professional','detached')),
                ethos_markers TEXT,
                concreteness_score INTEGER CHECK (concreteness_score BETWEEN 1 AND 5),
                imagery_density_per_10min REAL,
                narrative_moments TEXT,
                christ_thread_score TEXT CHECK (christ_thread_score IN ('explicit','gestured','absent')),
                christ_thread_excerpts TEXT,
                exegetical_grounding TEXT CHECK (exegetical_grounding IN ('grounded','partial','pretext')),
                exegetical_grounding_notes TEXT,
                actual_duration_seconds INTEGER NOT NULL,
                planned_duration_seconds INTEGER,
                duration_delta_seconds INTEGER,
                section_timings TEXT,
                length_delta_commentary TEXT,
                density_hotspots TEXT,
                late_application_note TEXT,
                outline_coverage_pct REAL,
                outline_additions TEXT,
                outline_omissions TEXT,
                outline_drift_note TEXT,
                top_impact_helpers TEXT NOT NULL,
                top_impact_hurters TEXT NOT NULL,
                faithfulness_note TEXT,
                one_change_for_next_sunday TEXT NOT NULL,
                per_dimension_growth_edges TEXT,
                application_landing TEXT CHECK (application_landing IN ('pressed','touched','gestured','missed')),
                opening_tension TEXT CHECK (opening_tension IN ('strong','adequate','weak','absent')),
                opening_tension_note TEXT,
                computed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sermon_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sermon_id INTEGER NOT NULL REFERENCES sermons(id) ON DELETE CASCADE,
                flag_type TEXT NOT NULL,
                severity TEXT NOT NULL CHECK (severity IN ('info','note','warn','concern')),
                transcript_start_sec INTEGER,
                transcript_end_sec INTEGER,
                section_label TEXT,
                excerpt TEXT,
                rationale TEXT NOT NULL,
                analyzer_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sermon_flags_sermon ON sermon_flags(sermon_id);
            CREATE INDEX IF NOT EXISTS idx_sermon_flags_type ON sermon_flags(flag_type);

            CREATE TABLE IF NOT EXISTS sermon_coach_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sermon_id INTEGER REFERENCES sermons(id) ON DELETE CASCADE,
                session_id INTEGER REFERENCES sessions(id),
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user','assistant','tool','tool_result')),
                content TEXT,
                tool_name TEXT,
                tool_input TEXT,
                tool_result TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_coach_messages_sermon ON sermon_coach_messages(sermon_id);
            CREATE INDEX IF NOT EXISTS idx_coach_messages_session ON sermon_coach_messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_coach_messages_conv ON sermon_coach_messages(conversation_id);

            CREATE TABLE IF NOT EXISTS sermon_sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                trigger TEXT NOT NULL CHECK (trigger IN ('cron','manual','backfill')),
                started_at TEXT NOT NULL,
                ended_at TEXT,
                sermons_fetched INTEGER DEFAULT 0,
                sermons_new INTEGER DEFAULT 0,
                sermons_updated INTEGER DEFAULT 0,
                sermons_noop INTEGER DEFAULT 0,
                sermons_skipped INTEGER DEFAULT 0,
                sermons_failed INTEGER DEFAULT 0,
                error_summary TEXT,
                status TEXT NOT NULL CHECK (status IN ('running','completed','failed'))
            );

            CREATE TABLE IF NOT EXISTS sermon_analysis_cost_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sermon_id INTEGER NOT NULL REFERENCES sermons(id) ON DELETE CASCADE,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                estimated_cost_usd REAL NOT NULL,
                called_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cost_log_sermon ON sermon_analysis_cost_log(sermon_id);
            CREATE INDEX IF NOT EXISTS idx_cost_log_called ON sermon_analysis_cost_log(called_at DESC);
        """)
        existing_session_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if 'last_homiletical_activity_at' not in existing_session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN last_homiletical_activity_at TEXT")
        # Series split: parent/child sessions for one-prep-many-sermons
        if 'parent_session_id' not in existing_session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN parent_session_id INTEGER REFERENCES sessions(id)")
        if 'series_position' not in existing_session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN series_position INTEGER")
        if 'series_title' not in existing_session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN series_title TEXT")
        # v2 16-phase model (review #4): explicit canonical phase pointer
        # alongside the legacy current_phase. Backfill from current_phase
        # via v2_phases._LEGACY_TO_V2 on read; nothing to do at schema
        # time except create the column.
        if 'v2_phase' not in existing_session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN v2_phase TEXT")
        # Cached EF-prosthesis next-action sentence (point-of-performance UI).
        # Set when v2 phase changes; consumed by the session page top-of-thread
        # line + the /study/ resume card. _set_for_v2_phase lets us detect
        # staleness without comparing strings.
        if 'next_action_text' not in existing_session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN next_action_text TEXT")
        if 'next_action_set_for_v2_phase' not in existing_session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN next_action_set_for_v2_phase TEXT")
        # Per-session preach date — when Bryan is delivering THIS sermon.
        # Used by Time Coach + horizon countdown to scope "Friday 3pm
        # cutoff" to the correct week; manually editable in the topbar.
        # Stored as ISO YYYY-MM-DD; NULL means "not set" (default
        # falls back to "next Sunday" elsewhere).
        if 'preach_date' not in existing_session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN preach_date TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id)")
        # Homiletics engine: insight storage + claim embeddings + outline tag cache
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS homiletics_insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK (kind IN (
                    'illustration', 'objection', 'counseling', 'wisdom',
                    'christ_thread', 'balance_flag', 'coherence_flag'
                )),
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                citations TEXT NOT NULL,
                confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
                metadata TEXT,
                created_at TEXT NOT NULL,
                consumed_at TEXT,
                source_tool TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_insights_session_kind
                ON homiletics_insights(session_id, kind);
            CREATE INDEX IF NOT EXISTS idx_insights_session_created
                ON homiletics_insights(session_id, created_at);

            CREATE TABLE IF NOT EXISTS claim_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_kind TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                claim_text TEXT NOT NULL,
                embedding BLOB NOT NULL,
                embedding_model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(source_kind, source_id, embedding_model)
            );
            CREATE INDEX IF NOT EXISTS idx_claim_emb_source
                ON claim_embeddings(source_kind, source_id);
        """)
        existing_outline_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(outline_nodes)").fetchall()}
        if 'homiletical_type' not in existing_outline_cols:
            conn.execute(
                "ALTER TABLE outline_nodes ADD COLUMN homiletical_type TEXT "
                "CHECK (homiletical_type IS NULL OR homiletical_type IN ("
                "  'exegetical_claim', 'application', 'illustration', 'christ_thread',"
                "  'question', 'transition', 'other'))"
            )
        if 'homiletical_type_at' not in existing_outline_cols:
            conn.execute("ALTER TABLE outline_nodes ADD COLUMN homiletical_type_at TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_outline_nodes_session_type "
            "ON outline_nodes(session_id, homiletical_type)"
        )
        existing_sermon_cols = {r[1] for r in conn.execute("PRAGMA table_info(sermons)").fetchall()}
        if 'transcript_segments' not in existing_sermon_cols:
            conn.execute("ALTER TABLE sermons ADD COLUMN transcript_segments TEXT")
        if 'transcript_quality' not in existing_sermon_cols:
            conn.execute("ALTER TABLE sermons ADD COLUMN transcript_quality TEXT CHECK (transcript_quality IN ('good', 'degraded'))")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS analysis_runs (
                id TEXT PRIMARY KEY,
                sermon_id INTEGER NOT NULL REFERENCES sermons(id) ON DELETE CASCADE,
                run_type TEXT NOT NULL CHECK (run_type IN ('review', 'tagging')),
                review_run_id TEXT REFERENCES analysis_runs(id),
                prompt_version TEXT NOT NULL,
                model_name TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_analysis_runs_sermon ON analysis_runs(sermon_id, run_type, is_active);

            CREATE TABLE IF NOT EXISTS sermon_moments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sermon_id INTEGER NOT NULL REFERENCES sermons(id) ON DELETE CASCADE,
                analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
                start_segment_index INTEGER NOT NULL,
                end_segment_index INTEGER NOT NULL,
                start_ms INTEGER NOT NULL,
                end_ms INTEGER NOT NULL,
                sermon_position_pct REAL NOT NULL CHECK (sermon_position_pct >= 0.0 AND sermon_position_pct <= 1.0),
                excerpt_text TEXT NOT NULL,
                context_text TEXT,
                dimension_key TEXT NOT NULL CHECK (dimension_key IN (
                    'burden_clarity', 'movement_clarity', 'application_specificity',
                    'ethos_rating', 'concreteness_score', 'christ_thread_score', 'exegetical_grounding'
                )),
                section_role TEXT NOT NULL CHECK (section_role IN (
                    'intro', 'setup', 'exposition', 'illustration_section', 'application',
                    'transition', 'recap', 'appeal', 'conclusion', 'prayer', 'reading'
                )),
                homiletic_move TEXT CHECK (homiletic_move IS NULL OR homiletic_move IN (
                    'big_idea_statement', 'structure_signpost', 'textual_observation', 'doctrinal_claim',
                    'illustration', 'exhortation', 'application', 'christ_connection', 'gospel_implication',
                    'objection_handling', 'direct_address', 'diagnostic_question', 'pastoral_comfort',
                    'warning', 'summary_restatement', 'contextualization'
                )),
                valence TEXT NOT NULL CHECK (valence IN ('positive', 'negative')),
                confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
                impact TEXT NOT NULL CHECK (impact IN ('minor', 'moderate', 'major')),
                moment_rank INTEGER NOT NULL,
                rationale TEXT NOT NULL,
                review_source_ref TEXT,
                prompt_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sermon_moments_sermon_run ON sermon_moments(sermon_id, analysis_run_id);
            CREATE INDEX IF NOT EXISTS idx_sermon_moments_dimension ON sermon_moments(sermon_id, dimension_key, valence);
            CREATE INDEX IF NOT EXISTS idx_sermon_moments_position ON sermon_moments(sermon_id, start_segment_index, end_segment_index);
        """)
        # Unique index — separate statement since IF NOT EXISTS behavior varies
        try:
            conn.execute("""
                CREATE UNIQUE INDEX uq_sermon_moment_span ON sermon_moments(
                    sermon_id, analysis_run_id, dimension_key, valence,
                    start_segment_index, end_segment_index
                )
            """)
        except Exception:
            pass  # Already exists
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS coaching_commitments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dimension_key TEXT NOT NULL,
                practice_experiment TEXT NOT NULL,
                target_sermons INTEGER NOT NULL DEFAULT 2,
                baseline_sermon_id INTEGER REFERENCES sermons(id),
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'superseded')),
                created_at TEXT NOT NULL,
                reviewed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_coaching_commitments_active ON coaching_commitments(status);
        """)
        try:
            conn.execute("""
                CREATE UNIQUE INDEX uq_coaching_one_active ON coaching_commitments(status) WHERE status = 'active'
            """)
        except Exception:
            pass  # Already exists
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS coaching_insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dimension_key TEXT,
                summary TEXT NOT NULL,
                applies_when TEXT NOT NULL,
                avoid_when TEXT NOT NULL,
                source_sermon_id INTEGER REFERENCES sermons(id),
                source_conversation_id INTEGER,
                superseded_by INTEGER REFERENCES coaching_insights(id),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audio_recordings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                phase TEXT NOT NULL,
                file_path TEXT NOT NULL,
                content_type TEXT,
                byte_size INTEGER,
                duration_sec REAL,
                transcript_excerpt TEXT,
                quality TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audio_recordings_session
                ON audio_recordings(session_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS session_coaching_exposure (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                dimension_key TEXT NOT NULL,
                escalation_level INTEGER NOT NULL CHECK (escalation_level BETWEEN 1 AND 5),
                response TEXT NOT NULL DEFAULT 'pending' CHECK (response IN ('accepted', 'declined', 'deferred', 'pending')),
                created_at TEXT NOT NULL,
                UNIQUE(session_id, dimension_key)
            );
        """)
        conn.commit()
        conn.close()

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def create_session(self, passage_ref, book, chapter, verse_start, verse_end, genre):
        now = self._now()
        with self._safe_write() as conn:
            cur = conn.execute("""
                INSERT INTO sessions (passage_ref, book, chapter, verse_start, verse_end, genre,
                                      current_phase, timer_remaining_seconds, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'prayer', ?, ?, ?)
            """, (passage_ref, book, chapter, verse_start, verse_end, genre,
                  PHASE_TIMERS['prayer'], now, now))
            return cur.lastrowid

    def get_session(self, session_id):
        conn = self._conn()
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_children(self, parent_id):
        """Return child sessions of `parent_id` ordered by series_position."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE parent_session_id = ? "
                "ORDER BY series_position",
                (parent_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_parent(self, child_id):
        """Return the parent session row for `child_id`, or None if standalone."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT p.* FROM sessions c "
                "JOIN sessions p ON p.id = c.parent_session_id "
                "WHERE c.id = ?",
                (child_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_series(self, session_id):
        """Return parent + all children ordered by position, for any member id.

        Returns [] if the session is standalone (no parent, no children).
        """
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT parent_session_id FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return []
            parent_id = row['parent_session_id'] if row['parent_session_id'] else session_id
            # Check that parent_id is actually a parent (has children) — otherwise standalone.
            children = conn.execute(
                "SELECT 1 FROM sessions WHERE parent_session_id = ? LIMIT 1",
                (parent_id,),
            ).fetchone()
            if not children:
                return []
            rows = conn.execute(
                "SELECT * FROM sessions WHERE id = ? OR parent_session_id = ? "
                "ORDER BY series_position",
                (parent_id, parent_id),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def save_insight(self, *, session_id, kind, title, body, citations,
                     confidence, metadata, source_tool):
        """Insert a homiletics_insights row. Returns the new row id."""
        with self._safe_write() as conn:
            cur = conn.execute(
                "INSERT INTO homiletics_insights "
                "(session_id, kind, title, body, citations, confidence, metadata, "
                " created_at, source_tool) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, kind, title, body,
                 json.dumps(citations or []),
                 float(confidence),
                 json.dumps(metadata or {}),
                 self._now(),
                 source_tool),
            )
            return cur.lastrowid

    def get_insights(self, session_id, kind=None):
        """Return homiletics_insights rows for a session, optionally filtered by kind.

        citations and metadata are returned as raw JSON strings — callers decode.
        """
        conn = self._conn()
        try:
            if kind:
                rows = conn.execute(
                    "SELECT * FROM homiletics_insights "
                    "WHERE session_id = ? AND kind = ? ORDER BY created_at DESC",
                    (session_id, kind),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM homiletics_insights "
                    "WHERE session_id = ? ORDER BY created_at DESC",
                    (session_id,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def save_claim_embedding(self, *, source_kind, source_id, claim_text,
                              vector, model):
        """Insert/replace a claim_embeddings row. Vector is packed as float32 bytes."""
        import struct
        blob = struct.pack(f'{len(vector)}f', *vector)
        with self._safe_write() as conn:
            cur = conn.execute(
                "INSERT INTO claim_embeddings "
                "(source_kind, source_id, claim_text, embedding, embedding_model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_kind, source_id, embedding_model) DO UPDATE SET "
                "  claim_text = excluded.claim_text, "
                "  embedding = excluded.embedding, "
                "  created_at = excluded.created_at",
                (source_kind, source_id, claim_text, blob, model,
                 self._now()),
            )
            return cur.lastrowid

    def get_claim_embeddings(self, *, source_kind, model, source_ids=None):
        """Return claim_embeddings rows with the vector unpacked to list[float].

        If source_ids is provided, returns only embeddings for those ids.
        """
        import struct
        conn = self._conn()
        try:
            if source_ids is not None:
                if not source_ids:
                    return []
                placeholders = ",".join("?" for _ in source_ids)
                rows = conn.execute(
                    f"SELECT * FROM claim_embeddings "
                    f"WHERE source_kind = ? AND embedding_model = ? "
                    f"  AND source_id IN ({placeholders})",
                    [source_kind, model, *source_ids],
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM claim_embeddings "
                    "WHERE source_kind = ? AND embedding_model = ?",
                    (source_kind, model),
                ).fetchall()
            out = []
            for r in rows:
                blob = r['embedding']
                n = len(blob) // 4   # float32 = 4 bytes
                vec = list(struct.unpack(f'{n}f', blob))
                out.append({
                    'id': r['id'],
                    'source_kind': r['source_kind'],
                    'source_id': r['source_id'],
                    'claim_text': r['claim_text'],
                    'vector': vec,
                    'model': r['embedding_model'],
                })
            return out
        finally:
            conn.close()

    def list_sessions(self, status=None):
        conn = self._conn()
        if status:
            rows = conn.execute("SELECT * FROM sessions WHERE status = ? ORDER BY updated_at DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_phase(self, session_id, phase, timer_seconds=None):
        if timer_seconds is None:
            timer_seconds = PHASE_TIMERS.get(phase, 1800)
        with self._safe_write() as conn:
            conn.execute("""
                UPDATE sessions SET current_phase = ?, timer_remaining_seconds = ?,
                                   timer_paused = 0, updated_at = ?
                WHERE id = ?
            """, (phase, timer_seconds, self._now(), session_id))

    def set_preach_date(self, session_id, preach_date):
        """Persist the date Bryan will preach this sermon (ISO YYYY-MM-DD
        or None to clear). Returns affected rowcount so endpoint callers
        can detect a missing session_id."""
        with self._safe_write() as conn:
            cur = conn.execute(
                "UPDATE sessions SET preach_date = ?, updated_at = ? WHERE id = ?",
                (preach_date, self._now(), session_id))
            return cur.rowcount

    def set_next_action(self, session_id, text, v2_phase):
        """Cache an EF-prosthesis next-action sentence + the v2 phase it
        was generated for. Caller regenerates whenever
        next_action_set_for_v2_phase != current session.v2_phase.
        Returns rowcount so missing-id callers can detect no-op."""
        with self._safe_write() as conn:
            cur = conn.execute(
                "UPDATE sessions SET next_action_text = ?, "
                "next_action_set_for_v2_phase = ?, updated_at = ? "
                "WHERE id = ?",
                (text, v2_phase, self._now(), session_id))
            return cur.rowcount

    def update_v2_phase(self, session_id, v2_phase):
        """Set the canonical v2 phase pointer (review #4).

        Returns affected row count so callers can detect missing
        session_id (returns 0). Caller is responsible for validating
        that v2_phase is a known key; we don't gate that here so
        backfill scripts can write whatever the v2_phases module knows.

        NOTE: Prefer transition_v2_phase() in user-facing flows — it's
        race-safe (single transaction) and logs phase_events atomically.
        update_v2_phase remains for the backfill script.
        """
        with self._safe_write() as conn:
            cur = conn.execute(
                "UPDATE sessions SET v2_phase = ?, updated_at = ? WHERE id = ?",
                (v2_phase, self._now(), session_id))
            return cur.rowcount

    def transition_v2_phase(self, session_id, v2_phase):
        """Atomic v2-phase transition: SELECT old + conditional UPDATE +
        log exit/enter events, all inside a single BEGIN IMMEDIATE
        transaction.

        Also syncs legacy current_phase so the UI machinery (card vs
        conversation mode, which card template renders, prayer/translation/
        digestion textareas) lands on the right screen. Previously the
        picker only updated v2_phase, leaving legacy stale — Bryan saw
        the picker say 'Phase 2 Read & Translate' but the Greek text +
        translation textarea never appeared because legacy phase was
        still on a non-card key.

        Returns (status, old_v2_phase):
          ('missing', None)   — session row doesn't exist
          ('no_change', None) — session exists but already at v2_phase
          ('changed', old)    — transitioned, events logged
        """
        from v2_phases import legacy_phase_for_v2
        new_legacy = legacy_phase_for_v2(v2_phase)
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")  # writer lock → serializes concurrent calls
            row = conn.execute(
                "SELECT v2_phase, current_phase FROM sessions WHERE id = ?",
                (session_id,)
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return ('missing', None)
            old = row['v2_phase']
            if old == v2_phase:
                conn.execute("ROLLBACK")
                return ('no_change', None)
            now = self._now()
            # Reset the timer to the budget for the new phase. Bryan's
            # phase ring depends on this; without the reset, the picker
            # would put him in a phase whose ring already showed depleted.
            new_timer = PHASE_TIMERS.get(new_legacy or row['current_phase'], 1800)
            conn.execute(
                "UPDATE sessions SET v2_phase = ?, current_phase = COALESCE(?, current_phase), "
                "timer_remaining_seconds = ?, timer_paused = 0, updated_at = ? "
                "WHERE id = ?",
                (v2_phase, new_legacy, new_timer, now, session_id))
            # Log the exit/enter events under the same transaction so a
            # crash between them can't leave the log unbalanced.
            if old:
                conn.execute(
                    "INSERT INTO phase_events (session_id, phase, event_type, timestamp) "
                    "VALUES (?, ?, 'exit', ?)",
                    (session_id, old, now))
            conn.execute(
                "INSERT INTO phase_events (session_id, phase, event_type, timestamp) "
                "VALUES (?, ?, 'enter', ?)",
                (session_id, v2_phase, now))
            conn.execute("COMMIT")
            return ('changed', old)
        except Exception:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            raise
        finally:
            conn.close()

    def save_card_response(self, session_id, phase, question_id, content):
        now = self._now()
        with self._safe_write() as conn:
            cur = conn.execute("""
                INSERT INTO card_responses (session_id, phase, question_id, content, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (session_id, phase, question_id, content, now))
            if phase in ('exegetical_point', 'fcf_homiletical', 'sermon_construction', 'edit_pray'):
                conn.execute(
                    "UPDATE sessions SET last_homiletical_activity_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, session_id),
                )
            return cur.lastrowid

    def get_card_responses(self, session_id, phase=None):
        conn = self._conn()
        if phase:
            rows = conn.execute("SELECT * FROM card_responses WHERE session_id = ? AND phase = ? ORDER BY created_at", (session_id, phase)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM card_responses WHERE session_id = ? ORDER BY created_at", (session_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def save_message(self, session_id, phase, role, content, tool_name=None, tool_input=None):
        now = self._now()
        with self._safe_write() as conn:
            cur = conn.execute("""
                INSERT INTO conversation_messages (session_id, phase, role, content, tool_name, tool_input, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (session_id, phase, role, content, tool_name, tool_input, now))
            if phase in ('exegetical_point', 'fcf_homiletical', 'sermon_construction', 'edit_pray'):
                conn.execute(
                    "UPDATE sessions SET last_homiletical_activity_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, session_id),
                )
            return cur.lastrowid

    def get_messages(self, session_id, phase=None, limit=50):
        conn = self._conn()
        if phase:
            rows = conn.execute("""
                SELECT * FROM conversation_messages
                WHERE session_id = ? AND phase = ?
                ORDER BY created_at DESC LIMIT ?
            """, (session_id, phase, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM conversation_messages
                WHERE session_id = ?
                ORDER BY created_at DESC LIMIT ?
            """, (session_id, limit)).fetchall()
        conn.close()
        return [dict(r) for r in reversed(rows)]

    def add_outline_node(self, session_id, node_type, content, parent_id=None,
                         verse_ref=None, rank=0, flags=None, source_type=None, source_id=None):
        now = self._now()
        with self._safe_write() as conn:
            if rank == 0 and parent_id is not None:
                row = conn.execute("SELECT MAX(rank) as max_rank FROM outline_nodes WHERE session_id = ? AND parent_id = ?",
                                   (session_id, parent_id)).fetchone()
                rank = (row['max_rank'] or 0) + 1
            elif rank == 0 and parent_id is None:
                row = conn.execute("SELECT MAX(rank) as max_rank FROM outline_nodes WHERE session_id = ? AND parent_id IS NULL",
                                   (session_id,)).fetchone()
                rank = (row['max_rank'] or 0) + 1
            cur = conn.execute("""
                INSERT INTO outline_nodes (session_id, parent_id, type, content, verse_ref, rank,
                                           flags, source_type, source_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (session_id, parent_id, node_type, content, verse_ref, rank,
                  json.dumps(flags or {}), source_type, source_id, now, now))
            return cur.lastrowid

    def get_outline_tree(self, session_id):
        conn = self._conn()
        rows = conn.execute("""
            SELECT * FROM outline_nodes WHERE session_id = ? ORDER BY rank
        """, (session_id,)).fetchall()
        conn.close()
        nodes = [dict(r) for r in rows]
        for n in nodes:
            n['flags'] = json.loads(n['flags']) if n['flags'] else {}
        return self._build_tree(nodes)

    def _build_tree(self, nodes):
        by_id = {n['id']: {**n, 'children': []} for n in nodes}
        roots = []
        for n in nodes:
            node = by_id[n['id']]
            if n['parent_id'] and n['parent_id'] in by_id:
                by_id[n['parent_id']]['children'].append(node)
            else:
                roots.append(node)
        return roots

    def update_outline_node(self, node_id, content=None, node_type=None, rank=None, flags=None):
        """Returns affected row count so callers can detect missing-id no-ops."""
        updates = []
        params = []
        if content is not None:
            updates.append("content = ?")
            params.append(content)
        if node_type is not None:
            updates.append("type = ?")
            params.append(node_type)
        if rank is not None:
            updates.append("rank = ?")
            params.append(rank)
        if flags is not None:
            updates.append("flags = ?")
            params.append(json.dumps(flags))
        updates.append("updated_at = ?")
        params.append(self._now())
        params.append(node_id)
        with self._safe_write() as conn:
            cur = conn.execute(
                f"UPDATE outline_nodes SET {', '.join(updates)} WHERE id = ?", params)
            return cur.rowcount

    def delete_outline_node(self, node_id):
        """Returns affected row count for the node itself (children cascade)."""
        with self._safe_write() as conn:
            conn.execute("DELETE FROM outline_nodes WHERE parent_id = ?", (node_id,))
            cur = conn.execute("DELETE FROM outline_nodes WHERE id = ?", (node_id,))
            return cur.rowcount

    def delete_session(self, session_id):
        with self._safe_write() as conn:
            conn.execute("DELETE FROM card_notepads WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM card_annotations WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM outline_nodes WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM conversation_messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM card_responses WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def get_questions(self, phase, genre=None, priority=None):
        conn = self._conn()
        query = "SELECT * FROM question_bank WHERE phase = ?"
        params = [phase]
        if priority:
            query += " AND priority = ?"
            params.append(priority)
        query += " ORDER BY rank"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        questions = [dict(r) for r in rows]
        if genre:
            questions = [q for q in questions
                         if genre in json.loads(q['genre_tags']) or not json.loads(q['genre_tags'])]
        return questions

    def get_question(self, question_id):
        conn = self._conn()
        row = conn.execute("SELECT * FROM question_bank WHERE id = ?", (question_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def save_audio_recording(self, *, session_id, phase, file_path,
                              content_type, byte_size, duration_sec,
                              transcript_excerpt, quality):
        """Persist a row pointing to an on-disk audio file.

        Bryan keeps the raw audio so a junk Whisper transcript can be
        re-run later (better model, manual transcription, or simply
        listened back as a prayer he didn't want transcribed at all).
        """
        with self._safe_write() as conn:
            cur = conn.execute("""
                INSERT INTO audio_recordings
                    (session_id, phase, file_path, content_type, byte_size,
                     duration_sec, transcript_excerpt, quality, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (session_id, phase, file_path, content_type, byte_size,
                  duration_sec, transcript_excerpt, quality, self._now()))
            return cur.lastrowid

    def list_audio_recordings(self, session_id, limit=50):
        conn = self._conn()
        rows = conn.execute("""
            SELECT * FROM audio_recordings
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (session_id, limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_timer(self, session_id, remaining_seconds, paused=None, elapsed_delta=0):
        with self._safe_write() as conn:
            if paused is not None:
                conn.execute("""
                    UPDATE sessions SET timer_remaining_seconds = ?, timer_paused = ?,
                           total_elapsed_seconds = total_elapsed_seconds + ?, updated_at = ?
                    WHERE id = ?
                """, (remaining_seconds, int(paused), elapsed_delta, self._now(), session_id))
            else:
                conn.execute("""
                    UPDATE sessions SET timer_remaining_seconds = ?,
                           total_elapsed_seconds = total_elapsed_seconds + ?, updated_at = ?
                    WHERE id = ?
                """, (remaining_seconds, elapsed_delta, self._now(), session_id))

    def complete_session(self, session_id):
        with self._safe_write() as conn:
            conn.execute("UPDATE sessions SET status = 'completed', updated_at = ? WHERE id = ?",
                         (self._now(), session_id))

    def save_card_annotation(self, session_id, phase, source, starred_text, note=""):
        with self._safe_write() as conn:
            cur = conn.execute(
                "INSERT INTO card_annotations (session_id, phase, source, starred_text, note) VALUES (?, ?, ?, ?, ?)",
                (session_id, phase, source, starred_text, note))
            return cur.lastrowid

    def get_card_annotations(self, session_id, phase=None):
        conn = self._conn()
        if phase:
            rows = conn.execute(
                "SELECT id, source, starred_text, note, created_at FROM card_annotations WHERE session_id = ? AND phase = ? ORDER BY created_at",
                (session_id, phase)).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, source, starred_text, note, created_at FROM card_annotations WHERE session_id = ? ORDER BY created_at",
                (session_id,)).fetchall()
        conn.close()
        return [{"id": r[0], "source": r[1], "starred_text": r[2], "note": r[3], "created_at": r[4]} for r in rows]

    def save_card_notepad(self, session_id, phase, content):
        with self._safe_write() as conn:
            conn.execute(
                "INSERT INTO card_notepads (session_id, phase, content) VALUES (?, ?, ?) ON CONFLICT(session_id, phase) DO UPDATE SET content = ?, updated_at = datetime('now')",
                (session_id, phase, content, content))

    def get_card_notepad(self, session_id, phase):
        conn = self._conn()
        row = conn.execute(
            "SELECT content FROM card_notepads WHERE session_id = ? AND phase = ?",
            (session_id, phase)).fetchone()
        conn.close()
        return row[0] if row else ""
