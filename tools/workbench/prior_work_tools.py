"""Prior-work recall tools — Study TA agent reaches back into:

  • Prior STUDY sessions (Bryan's prep work — decisions, outline, chat, notepads)
  • Prior SERMONS (preached text + analyzer review + tagged moments + flags)

Six tools, organized as a graduated toolkit per data source:

    list_prior_sessions   / search_prior_sessions   / read_prior_session
    list_prior_sermons    / search_prior_sermons    / read_prior_sermon

The agent picks the lightest tool that answers the current question and
escalates only on miss (per feedback_ai_agency_over_depth). Every list/search
tool accepts `scope` ∈ {series, passage, all}, defaulting to 'series'. The
agent passes a wider scope when the tighter one returned nothing relevant.

Spec: docs/superpowers/specs/2026-05-27-prior-work-recall-design.md
"""

from __future__ import annotations

import json
from typing import Iterable


# ─────────────────────────── Constants ─────────────────────────────

VALID_SCOPES = ('series', 'passage', 'all')
LIST_CAP = 50
SEARCH_CAP = 25
SNIPPET_WINDOW = 200
CHAT_SUMMARY_LIMIT = 20

VALID_SESSION_SURFACES = ('decisions', 'outline', 'chat', 'notepads',
                          'annotations', 'insights')
VALID_SERMON_SURFACES = ('transcript', 'review', 'moments', 'flags')
SESSION_SECTIONS_DEFAULT = ('decisions', 'outline', 'notepads')
SERMON_SECTIONS_DEFAULT = ('review',)

# Rubric fields searched by surface='review' (string columns only).
_REVIEW_TEXT_COLUMNS = (
    'burden_statement_excerpt', 'movement_rationale',
    'application_excerpts', 'ethos_markers',
    'narrative_moments', 'christ_thread_excerpts',
    'exegetical_grounding_notes', 'section_timings',
    'length_delta_commentary', 'density_hotspots',
    'late_application_note', 'outline_additions',
    'outline_omissions', 'outline_drift_note',
    'top_impact_helpers', 'top_impact_hurters',
    'faithfulness_note', 'one_change_for_next_sunday',
    'per_dimension_growth_edges', 'opening_tension_note',
)


# ─────────────────────────── Helpers ─────────────────────────────


def _scope(args) -> str:
    s = (args or {}).get('scope', 'series')
    return s if s in VALID_SCOPES else 'series'


def _ctx_db(ctx):
    db = (ctx or {}).get('db')
    if db is None:
        raise ValueError("session_context missing 'db' — agent dispatch bug")
    return db


def _snippet(text: str, query: str, window: int = SNIPPET_WINDOW) -> str:
    if not text or not query:
        return ''
    needle = query.lower()
    hay = text.lower()
    i = hay.find(needle)
    if i < 0:
        return ''
    start = max(0, i - window // 2)
    end = min(len(text), i + len(query) + window // 2)
    raw = text[start:end]
    # Highlight the matched span using the ORIGINAL casing.
    matched = text[i:i + len(query)]
    highlighted = raw.replace(matched, f'«{matched}»', 1)
    prefix = '…' if start > 0 else ''
    suffix = '…' if end < len(text) else ''
    return f'{prefix}{highlighted}{suffix}'


def _series_member_ids(db, session_id: int) -> list[int]:
    """All session ids in the same series as session_id, excluding session_id itself."""
    members = db.get_series(session_id) or []
    return [m['id'] for m in members if m['id'] != session_id]


def _passage_session_ids(db, ctx, exclude_ids: Iterable[int]) -> list[int]:
    """Sessions whose verse range overlaps current session's verse range
    (same book + chapter), excluding given ids.

    Overlap = `verse_start <= current_end AND verse_end >= current_start`.
    Sessions with NULL verse_start/verse_end fall back to chapter-only matching
    (because verseless chapter sessions cover the whole chapter)."""
    book = ctx.get('book')
    chapter = ctx.get('chapter')
    if book is None or chapter is None:
        return []
    cur_vs = ctx.get('verse_start')
    cur_ve = ctx.get('verse_end')
    conn = db._conn()
    try:
        if cur_vs is None or cur_ve is None:
            rows = conn.execute(
                "SELECT id FROM sessions WHERE book = ? AND chapter = ? "
                "ORDER BY updated_at DESC",
                (book, chapter),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM sessions WHERE book = ? AND chapter = ? "
                "  AND (verse_start IS NULL OR verse_end IS NULL OR "
                "       (verse_start <= ? AND verse_end >= ?)) "
                "ORDER BY updated_at DESC",
                (book, chapter, cur_ve, cur_vs),
            ).fetchall()
        excl = set(exclude_ids)
        return [r['id'] for r in rows if r['id'] not in excl]
    finally:
        conn.close()


def _all_session_ids(db, exclude_ids: Iterable[int], cap: int = LIST_CAP) -> list[int]:
    conn = db._conn()
    try:
        rows = conn.execute(
            "SELECT id FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (cap + len(list(exclude_ids)),),
        ).fetchall()
        excl = set(exclude_ids)
        return [r['id'] for r in rows if r['id'] not in excl][:cap]
    finally:
        conn.close()


def _sermon_ids_linked_to_sessions(db, session_ids: Iterable[int]) -> list[int]:
    """Sermons with an ACTIVE sermon_link to any of the given session ids,
    de-duped and ordered by preach_date."""
    session_ids = list(session_ids)
    if not session_ids:
        return []
    placeholders = ','.join('?' for _ in session_ids)
    conn = db._conn()
    try:
        rows = conn.execute(
            f"SELECT DISTINCT s.id, s.preach_date FROM sermons s "
            f"JOIN sermon_links sl ON sl.sermon_id = s.id "
            f"WHERE sl.link_status = 'active' AND sl.session_id IN ({placeholders}) "
            f"ORDER BY s.preach_date ASC",
            session_ids,
        ).fetchall()
        return [r['id'] for r in rows]
    finally:
        conn.close()


def _passage_sermon_ids(db, ctx, exclude_ids: Iterable[int]) -> list[int]:
    """Sermons whose verse range overlaps current session's verse range,
    excluding given ids. Same overlap semantics as `_passage_session_ids`."""
    book = ctx.get('book')
    chapter = ctx.get('chapter')
    if book is None or chapter is None:
        return []
    cur_vs = ctx.get('verse_start')
    cur_ve = ctx.get('verse_end')
    conn = db._conn()
    try:
        if cur_vs is None or cur_ve is None:
            rows = conn.execute(
                "SELECT id FROM sermons WHERE book = ? AND chapter = ? "
                "ORDER BY preach_date ASC",
                (book, chapter),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM sermons WHERE book = ? AND chapter = ? "
                "  AND (verse_start IS NULL OR verse_end IS NULL OR "
                "       (verse_start <= ? AND verse_end >= ?)) "
                "ORDER BY preach_date ASC",
                (book, chapter, cur_ve, cur_vs),
            ).fetchall()
        excl = set(exclude_ids)
        return [r['id'] for r in rows if r['id'] not in excl]
    finally:
        conn.close()


def _all_sermon_ids(db, exclude_ids: Iterable[int], cap: int = LIST_CAP) -> list[int]:
    conn = db._conn()
    try:
        rows = conn.execute(
            "SELECT id FROM sermons ORDER BY preach_date DESC LIMIT ?",
            (cap + len(list(exclude_ids)),),
        ).fetchall()
        excl = set(exclude_ids)
        return [r['id'] for r in rows if r['id'] not in excl][:cap]
    finally:
        conn.close()


def _resolve_scope_sessions(db, ctx, scope: str) -> tuple[list[int], list[int]]:
    """Return (target_ids, series_member_ids) for the given session scope.

    series_member_ids is exposed so callers can dedup when scope='passage'
    needs to exclude both the current session AND its series siblings.
    """
    current = ctx['session_id']
    series = _series_member_ids(db, current)
    if scope == 'series':
        return series, series
    if scope == 'passage':
        exclude = {current, *series}
        return _passage_session_ids(db, ctx, exclude), series
    # all
    exclude = {current}
    return _all_session_ids(db, exclude, cap=LIST_CAP), series


def _resolve_scope_sermons(db, ctx, scope: str) -> list[int]:
    current = ctx['session_id']
    series_session_ids = _series_member_ids(db, current)
    if scope == 'series':
        return _sermon_ids_linked_to_sessions(db, series_session_ids)
    if scope == 'passage':
        series_sermons = set(_sermon_ids_linked_to_sessions(db, series_session_ids))
        return _passage_sermon_ids(db, ctx, series_sermons)
    return _all_sermon_ids(db, exclude_ids=set(), cap=LIST_CAP)


def _hint_for_empty(scope: str, kind: str) -> str:
    if scope == 'series':
        return f"No prior {kind} found in this series. Try scope='passage' or scope='all'."
    if scope == 'passage':
        return f"No prior {kind} found at this passage. Try scope='all'."
    return f"No prior {kind} found."


# ─────────────────────────── list_prior_sessions ─────────────────────────


def list_prior_sessions(args, ctx):
    db = _ctx_db(ctx)
    scope = _scope(args)
    target_ids, _series = _resolve_scope_sessions(db, ctx, scope)
    if not target_ids:
        return {'sessions': [], 'scope_used': scope, 'hint': _hint_for_empty(scope, 'sessions')}

    placeholders = ','.join('?' for _ in target_ids)
    conn = db._conn()
    try:
        rows = conn.execute(
            f"SELECT s.id, s.passage_ref, s.series_title, s.series_position, "
            f"  s.current_phase, s.status, s.created_at, s.updated_at, "
            f"  (SELECT COUNT(*) FROM session_decisions sd WHERE sd.session_id = s.id) AS dcount, "
            f"  (SELECT COUNT(*) FROM outline_nodes o WHERE o.session_id = s.id) AS ocount, "
            f"  (SELECT COUNT(*) FROM conversation_messages m WHERE m.session_id = s.id) AS mcount "
            f"FROM sessions s WHERE s.id IN ({placeholders}) "
            f"ORDER BY COALESCE(s.series_position, 999), s.created_at",
            target_ids,
        ).fetchall()
    finally:
        conn.close()

    sessions = []
    for r in rows:
        sessions.append({
            'session_id': r['id'],
            'passage_ref': r['passage_ref'],
            'series_title': r['series_title'],
            'series_position': r['series_position'],
            'current_phase': r['current_phase'],
            'status': r['status'],
            'created_at': r['created_at'],
            'updated_at': r['updated_at'],
            'has_decisions': r['dcount'] > 0,
            'has_outline': r['ocount'] > 0,
            'message_count': r['mcount'],
        })
    return {'sessions': sessions, 'scope_used': scope}


# ─────────────────────────── search_prior_sessions ─────────────────────────


def search_prior_sessions(args, ctx):
    db = _ctx_db(ctx)
    query = (args or {}).get('query', '').strip()
    if not query:
        return {'error': 'query is required', 'hits': []}
    scope = _scope(args)
    raw_surfaces = args.get('search_in') or list(VALID_SESSION_SURFACES)
    surfaces = [s for s in raw_surfaces if s in VALID_SESSION_SURFACES]
    if args.get('search_in') and not surfaces:
        # Agent passed search_in but nothing valid — refuse to silently broaden.
        return {'error': 'no_valid_search_in_surfaces',
                'requested': args.get('search_in'),
                'valid_surfaces': list(VALID_SESSION_SURFACES)}
    if not surfaces:
        surfaces = list(VALID_SESSION_SURFACES)

    target_ids, _series = _resolve_scope_sessions(db, ctx, scope)
    if not target_ids:
        return {'hits': [], 'scope_used': scope, 'hint': _hint_for_empty(scope, 'sessions')}

    placeholders = ','.join('?' for _ in target_ids)
    like = f'%{query}%'
    hits = []
    conn = db._conn()
    try:
        if 'decisions' in surfaces:
            rows = conn.execute(
                f"SELECT session_id, key, value FROM session_decisions "
                f"WHERE session_id IN ({placeholders}) AND value LIKE ? LIMIT ?",
                [*target_ids, like, SEARCH_CAP],
            ).fetchall()
            for r in rows:
                hits.append({
                    'session_id': r['session_id'], 'surface': 'decisions',
                    'snippet': _snippet(r['value'], query),
                    'hit_context': {'key': r['key']},
                })
        if 'outline' in surfaces:
            rows = conn.execute(
                f"SELECT session_id, type, content, verse_ref FROM outline_nodes "
                f"WHERE session_id IN ({placeholders}) AND content LIKE ? LIMIT ?",
                [*target_ids, like, SEARCH_CAP],
            ).fetchall()
            for r in rows:
                hits.append({
                    'session_id': r['session_id'], 'surface': 'outline',
                    'snippet': _snippet(r['content'], query),
                    'hit_context': {'type': r['type'], 'verse_ref': r['verse_ref']},
                })
        if 'chat' in surfaces:
            rows = conn.execute(
                f"SELECT session_id, phase, role, content FROM conversation_messages "
                f"WHERE session_id IN ({placeholders}) AND content LIKE ? "
                f"ORDER BY created_at DESC LIMIT ?",
                [*target_ids, like, SEARCH_CAP],
            ).fetchall()
            for r in rows:
                hits.append({
                    'session_id': r['session_id'], 'surface': 'chat',
                    'snippet': _snippet(r['content'], query),
                    'hit_context': {'phase': r['phase'], 'role': r['role']},
                })
        if 'notepads' in surfaces:
            rows = conn.execute(
                f"SELECT session_id, phase, content FROM card_notepads "
                f"WHERE session_id IN ({placeholders}) AND content LIKE ? LIMIT ?",
                [*target_ids, like, SEARCH_CAP],
            ).fetchall()
            for r in rows:
                hits.append({
                    'session_id': r['session_id'], 'surface': 'notepads',
                    'snippet': _snippet(r['content'], query),
                    'hit_context': {'phase': r['phase']},
                })
        if 'annotations' in surfaces:
            rows = conn.execute(
                f"SELECT session_id, phase, source, starred_text, note FROM card_annotations "
                f"WHERE session_id IN ({placeholders}) "
                f"  AND (note LIKE ? OR starred_text LIKE ?) LIMIT ?",
                [*target_ids, like, like, SEARCH_CAP],
            ).fetchall()
            for r in rows:
                blob = ' / '.join(filter(None, [r['starred_text'], r['note']]))
                hits.append({
                    'session_id': r['session_id'], 'surface': 'annotations',
                    'snippet': _snippet(blob, query),
                    'hit_context': {'phase': r['phase'], 'source': r['source']},
                })
        if 'insights' in surfaces:
            # homiletics_insights are keyed by session_id (distilled findings
            # from the agent's prep work). Constrained here to the resolved
            # scope so 'series' insights never leak from off-topic linked
            # sessions; this surface lives on the session-side to avoid the
            # sermon→session noisy-active-link contamination codex flagged.
            rows = conn.execute(
                f"SELECT session_id, kind, title, body FROM homiletics_insights "
                f"WHERE session_id IN ({placeholders}) "
                f"  AND (title LIKE ? OR body LIKE ?) LIMIT ?",
                [*target_ids, like, like, SEARCH_CAP],
            ).fetchall()
            for r in rows:
                blob = ' / '.join(filter(None, [r['title'], r['body']]))
                hits.append({
                    'session_id': r['session_id'], 'surface': 'insights',
                    'snippet': _snippet(blob, query),
                    'hit_context': {'kind': r['kind']},
                })
    finally:
        conn.close()

    return {'hits': hits[:SEARCH_CAP], 'scope_used': scope}


# ─────────────────────────── read_prior_session ─────────────────────────


def read_prior_session(args, ctx):
    db = _ctx_db(ctx)
    sid = (args or {}).get('session_id')
    if sid is None:
        return {'error': 'session_id is required'}
    sections = args.get('sections') or list(SESSION_SECTIONS_DEFAULT)
    sections = set(sections)  # for fast membership

    session = db.get_session(sid)
    if session is None:
        return {'error': 'not_found', 'id': sid}

    out = {
        'session_id': session['id'],
        'passage_ref': session['passage_ref'],
        'series_title': session.get('series_title'),
        'series_position': session.get('series_position'),
        'current_phase': session['current_phase'],
    }

    conn = db._conn()
    try:
        if 'decisions' in sections:
            rows = conn.execute(
                "SELECT key, value FROM session_decisions WHERE session_id = ?",
                (sid,),
            ).fetchall()
            out['decisions'] = {r['key']: r['value'] for r in rows}
        if 'outline' in sections:
            rows = conn.execute(
                "SELECT id, parent_id, type, content, verse_ref, rank "
                "FROM outline_nodes WHERE session_id = ? "
                "ORDER BY COALESCE(parent_id, 0), rank, id",
                (sid,),
            ).fetchall()
            out['outline_tree'] = [
                {'id': r['id'], 'parent_id': r['parent_id'],
                 'type': r['type'], 'content': r['content'],
                 'verse_ref': r['verse_ref'], 'rank': r['rank']}
                for r in rows
            ]
        if 'notepads' in sections:
            rows = conn.execute(
                "SELECT phase, content FROM card_notepads WHERE session_id = ?",
                (sid,),
            ).fetchall()
            out['notepads_by_phase'] = {r['phase']: r['content'] for r in rows}
        if 'annotations' in sections:
            rows = conn.execute(
                "SELECT phase, source, starred_text, note FROM card_annotations "
                "WHERE session_id = ? ORDER BY created_at",
                (sid,),
            ).fetchall()
            out['annotations'] = [
                {'phase': r['phase'], 'source': r['source'],
                 'starred_text': r['starred_text'], 'note': r['note']}
                for r in rows
            ]
        if 'chat' in sections or 'chat_full' in sections:
            limit_clause = '' if 'chat_full' in sections else f' LIMIT {CHAT_SUMMARY_LIMIT}'
            rows = conn.execute(
                "SELECT phase, role, content FROM conversation_messages "
                f"WHERE session_id = ? AND content IS NOT NULL "
                f"ORDER BY created_at DESC{limit_clause}",
                (sid,),
            ).fetchall()
            out['chat_summary'] = [
                {'phase': r['phase'], 'role': r['role'], 'content': r['content']}
                for r in rows
            ]
    finally:
        conn.close()

    return out


# ─────────────────────────── list_prior_sermons ─────────────────────────


def list_prior_sermons(args, ctx):
    db = _ctx_db(ctx)
    scope = _scope(args)
    sermon_ids = _resolve_scope_sermons(db, ctx, scope)
    if not sermon_ids:
        return {'sermons': [], 'scope_used': scope, 'hint': _hint_for_empty(scope, 'sermons')}

    placeholders = ','.join('?' for _ in sermon_ids)
    conn = db._conn()
    try:
        rows = conn.execute(
            f"SELECT s.id, s.title, s.preach_date, s.book, s.chapter, "
            f"  s.verse_start, s.verse_end, s.series, s.duration_seconds, "
            f"  s.sync_status, "
            f"  r.one_change_for_next_sunday, r.movement_clarity, "
            f"  r.application_landing, r.christ_thread_score, "
            f"  r.actual_duration_seconds, "
            f"  CASE WHEN r.sermon_id IS NULL THEN 0 ELSE 1 END AS has_review "
            f"FROM sermons s LEFT JOIN sermon_reviews r ON r.sermon_id = s.id "
            f"WHERE s.id IN ({placeholders}) "
            f"ORDER BY s.preach_date ASC",
            sermon_ids,
        ).fetchall()
    finally:
        conn.close()

    sermons = []
    for r in rows:
        passage_ref = _format_passage_ref(r['book'], r['chapter'],
                                          r['verse_start'], r['verse_end'])
        sermons.append({
            'sermon_id': r['id'],
            'title': r['title'],
            'preach_date': r['preach_date'],
            'passage_ref': passage_ref,
            'series': r['series'],
            'duration_seconds': r['duration_seconds'],
            'sync_status': r['sync_status'],
            'has_review': bool(r['has_review']),
            'one_change_for_next_sunday': r['one_change_for_next_sunday'],
            'movement_clarity': r['movement_clarity'],
            'application_landing': r['application_landing'],
            'christ_thread_score': r['christ_thread_score'],
        })
    return {'sermons': sermons, 'scope_used': scope}


# ─────────────────────────── search_prior_sermons ─────────────────────────


def search_prior_sermons(args, ctx):
    db = _ctx_db(ctx)
    query = (args or {}).get('query', '').strip()
    if not query:
        return {'error': 'query is required', 'hits': []}
    scope = _scope(args)
    raw_surfaces = args.get('search_in') or list(VALID_SERMON_SURFACES)
    surfaces = [s for s in raw_surfaces if s in VALID_SERMON_SURFACES]
    if args.get('search_in') and not surfaces:
        return {'error': 'no_valid_search_in_surfaces',
                'requested': args.get('search_in'),
                'valid_surfaces': list(VALID_SERMON_SURFACES)}
    if not surfaces:
        surfaces = list(VALID_SERMON_SURFACES)

    sermon_ids = _resolve_scope_sermons(db, ctx, scope)
    if not sermon_ids:
        return {'hits': [], 'scope_used': scope, 'hint': _hint_for_empty(scope, 'sermons')}

    placeholders = ','.join('?' for _ in sermon_ids)
    like = f'%{query}%'
    hits = []
    conn = db._conn()
    try:
        if 'transcript' in surfaces:
            rows = conn.execute(
                f"SELECT id, title, transcript_text FROM sermons "
                f"WHERE id IN ({placeholders}) AND transcript_text LIKE ? LIMIT ?",
                [*sermon_ids, like, SEARCH_CAP],
            ).fetchall()
            for r in rows:
                hits.append({
                    'sermon_id': r['id'], 'title': r['title'],
                    'surface': 'transcript',
                    'snippet': _snippet(r['transcript_text'] or '', query),
                    'hit_context': {},
                })
        if 'review' in surfaces:
            cols_sql = ' OR '.join(f"{c} LIKE ?" for c in _REVIEW_TEXT_COLUMNS)
            select_sql = ', '.join(_REVIEW_TEXT_COLUMNS)
            rows = conn.execute(
                f"SELECT r.sermon_id, s.title, {select_sql} "
                f"FROM sermon_reviews r JOIN sermons s ON s.id = r.sermon_id "
                f"WHERE r.sermon_id IN ({placeholders}) AND ({cols_sql}) LIMIT ?",
                [*sermon_ids, *([like] * len(_REVIEW_TEXT_COLUMNS)), SEARCH_CAP],
            ).fetchall()
            for r in rows:
                for col in _REVIEW_TEXT_COLUMNS:
                    val = r[col]
                    if val and query.lower() in val.lower():
                        hits.append({
                            'sermon_id': r['sermon_id'], 'title': r['title'],
                            'surface': 'review',
                            'snippet': _snippet(val, query),
                            'hit_context': {'field': col},
                        })
                        break
        if 'moments' in surfaces:
            rows = conn.execute(
                f"SELECT m.sermon_id, s.title, m.dimension_key, m.valence, "
                f"  m.excerpt_text, m.rationale "
                f"FROM sermon_moments m JOIN sermons s ON s.id = m.sermon_id "
                f"WHERE m.sermon_id IN ({placeholders}) "
                f"  AND (m.excerpt_text LIKE ? OR m.rationale LIKE ?) LIMIT ?",
                [*sermon_ids, like, like, SEARCH_CAP],
            ).fetchall()
            for r in rows:
                blob = ' / '.join(filter(None, [r['excerpt_text'], r['rationale']]))
                hits.append({
                    'sermon_id': r['sermon_id'], 'title': r['title'],
                    'surface': 'moments',
                    'snippet': _snippet(blob, query),
                    'hit_context': {'dimension_key': r['dimension_key'],
                                    'valence': r['valence']},
                })
        if 'flags' in surfaces:
            rows = conn.execute(
                f"SELECT f.sermon_id, s.title, f.flag_type, f.severity, "
                f"  f.excerpt, f.rationale "
                f"FROM sermon_flags f JOIN sermons s ON s.id = f.sermon_id "
                f"WHERE f.sermon_id IN ({placeholders}) "
                f"  AND (f.excerpt LIKE ? OR f.rationale LIKE ? OR f.flag_type LIKE ?) "
                f"LIMIT ?",
                [*sermon_ids, like, like, like, SEARCH_CAP],
            ).fetchall()
            for r in rows:
                blob = ' / '.join(filter(None,
                                         [r['flag_type'], r['excerpt'], r['rationale']]))
                hits.append({
                    'sermon_id': r['sermon_id'], 'title': r['title'],
                    'surface': 'flags',
                    'snippet': _snippet(blob, query),
                    'hit_context': {'flag_type': r['flag_type'],
                                    'severity': r['severity']},
                })
    finally:
        conn.close()

    return {'hits': hits[:SEARCH_CAP], 'scope_used': scope}


# ─────────────────────────── read_prior_sermon ─────────────────────────


def read_prior_sermon(args, ctx):
    db = _ctx_db(ctx)
    sid = (args or {}).get('sermon_id')
    if sid is None:
        return {'error': 'sermon_id is required'}
    sections = set(args.get('sections') or list(SERMON_SECTIONS_DEFAULT))

    conn = db._conn()
    try:
        row = conn.execute(
            "SELECT id, title, preach_date, book, chapter, verse_start, verse_end, "
            "  series, duration_seconds, sync_status, transcript_text "
            "FROM sermons WHERE id = ?",
            (sid,),
        ).fetchone()
        if row is None:
            return {'error': 'not_found', 'id': sid}

        out = {
            'sermon_id': row['id'],
            'title': row['title'],
            'preach_date': row['preach_date'],
            'passage_ref': _format_passage_ref(row['book'], row['chapter'],
                                                row['verse_start'], row['verse_end']),
            'series': row['series'],
            'duration_seconds': row['duration_seconds'],
            'sync_status': row['sync_status'],
        }

        if 'review' in sections:
            rrow = conn.execute(
                "SELECT * FROM sermon_reviews WHERE sermon_id = ?", (sid,),
            ).fetchone()
            out['review'] = dict(rrow) if rrow else None
        if 'transcript' in sections:
            out['transcript'] = row['transcript_text'] or ''
        if 'moments' in sections:
            mrows = conn.execute(
                "SELECT dimension_key, valence, excerpt_text, rationale, "
                "  start_segment_index, end_segment_index, section_role, impact "
                "FROM sermon_moments WHERE sermon_id = ? "
                "ORDER BY moment_rank, start_segment_index",
                (sid,),
            ).fetchall()
            out['moments'] = [dict(r) for r in mrows]
        if 'flags' in sections:
            frows = conn.execute(
                "SELECT flag_type, severity, excerpt, rationale, "
                "  section_label, transcript_start_sec, transcript_end_sec "
                "FROM sermon_flags WHERE sermon_id = ? ORDER BY created_at",
                (sid,),
            ).fetchall()
            out['flags'] = [dict(r) for r in frows]
    finally:
        conn.close()

    return out


# ─────────────────────────── Passage formatting ─────────────────────────


# Logos uses 1-66 Protestant numbering for sermons (see screenshot showing
# book=66 for Romans). The bp_sermons display layer already maps book IDs to
# names; here we render a compact "Book chapter:verse" using English names
# for the most-touched books. Unknown → "Book66 3:21".
_BOOK_NAMES = {
    1: 'Genesis', 2: 'Exodus', 3: 'Leviticus', 4: 'Numbers', 5: 'Deuteronomy',
    6: 'Joshua', 7: 'Judges', 8: 'Ruth',
    19: 'Psalms', 20: 'Proverbs', 21: 'Ecclesiastes', 22: 'Song of Songs',
    23: 'Isaiah', 24: 'Jeremiah', 26: 'Ezekiel', 27: 'Daniel',
    40: 'Matthew', 41: 'Mark', 42: 'Luke', 43: 'John', 44: 'Acts',
    45: 'Romans',  # standard Protestant numbering for legacy code
    46: '1 Corinthians', 47: '2 Corinthians', 48: 'Galatians',
    49: 'Ephesians', 50: 'Philippians', 51: 'Colossians',
    58: 'Hebrews', 59: 'James', 60: '1 Peter', 61: '2 Peter',
    62: '1 John', 63: '2 John', 64: '3 John', 65: 'Jude', 66: 'Revelation',
    # Logos 1-66 numbering used in this project's sermons table:
    # Bryan's data shows Romans=66 (per the sermons SQL output captured during
    # design). Treating 66 as 'Romans' for display when other markers (series
    # text) make it clear; the field is informational only.
}


def _format_passage_ref(book, chapter, vs, ve):
    if not (book and chapter):
        return ''
    name = _BOOK_NAMES.get(book, f'Book{book}')
    if vs and ve and vs != ve:
        return f'{name} {chapter}:{vs}-{ve}'
    if vs:
        return f'{name} {chapter}:{vs}'
    return f'{name} {chapter}'


# ─────────────────────────── Tool definitions (for companion_tools.py) ─────


TOOL_DEFINITIONS = [
    {
        'name': 'list_prior_sessions',
        'description': (
            "Skim list of Bryan's prior STUDY sessions (his prep work — decisions, "
            "outline, chat). Use this first to see what's already there before "
            "searching or reading. Returns session_id, passage_ref, series_title, "
            "series_position, has_decisions, has_outline, message_count. "
            "scope: 'series' (siblings of current session, default), 'passage' "
            "(same book+chapter outside the series), 'all' (recency-ordered, capped "
            "at 50). Escalate scope only when the tighter scope returned nothing."
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'scope': {'type': 'string', 'enum': list(VALID_SCOPES),
                          'description': "Default 'series'."},
            },
        },
    },
    {
        'name': 'search_prior_sessions',
        'description': (
            "Search Bryan's prior session prep work for a substring. Targets: "
            "decisions (subject/theme/FCF/etc.), outline content, chat messages, "
            "phase notepads, card annotations, and homiletics insights (distilled "
            "findings from the agent's research). Use after list_prior_sessions "
            "to find a specific term across many sessions without reading each."
        ),
        'input_schema': {
            'type': 'object',
            'required': ['query'],
            'properties': {
                'query': {'type': 'string'},
                'scope': {'type': 'string', 'enum': list(VALID_SCOPES)},
                'search_in': {
                    'type': 'array',
                    'items': {'type': 'string', 'enum': list(VALID_SESSION_SURFACES)},
                    'description': "Default: all surfaces.",
                },
            },
        },
    },
    {
        'name': 'read_prior_session',
        'description': (
            "Read a specific prior session in depth. Pass `sections` to pull only "
            "what you need: 'decisions' (the 9-key panel state), 'outline' "
            "(outline tree), 'notepads' (per-phase notes), 'annotations' (starred "
            "text + notes), 'chat' (last 20 messages), 'chat_full' (everything). "
            "Default sections: ['decisions','outline','notepads']."
        ),
        'input_schema': {
            'type': 'object',
            'required': ['session_id'],
            'properties': {
                'session_id': {'type': 'integer'},
                'sections': {
                    'type': 'array',
                    'items': {'type': 'string',
                              'enum': ['decisions', 'outline', 'notepads',
                                       'annotations', 'chat', 'chat_full']},
                },
            },
        },
    },
    {
        'name': 'list_prior_sermons',
        'description': (
            "Skim list of Bryan's prior PREACHED sermons (transcript + coach "
            "review). Returns sermon_id, title, preach_date, passage_ref, "
            "series, has_review, plus the coach's one_change_for_next_sunday and "
            "top rubric ratings when available. Use first to see what's there. "
            "scope: 'series' (linked to current session's series via active "
            "sermon_links, default), 'passage' (same book+chapter), 'all'. "
            "Coach review for a sermon is exposed via read_prior_sermon(section='review')."
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'scope': {'type': 'string', 'enum': list(VALID_SCOPES)},
            },
        },
    },
    {
        'name': 'search_prior_sermons',
        'description': (
            "Search prior sermons by substring. Surfaces: 'transcript' (preached "
            "text), 'review' (analyzer rubric — helpers/hurters, one-change, "
            "exegetical grounding notes, etc.), 'moments' (tagged sermon moments "
            "with dimension/valence), 'flags' (length drift, late application, "
            "etc.). Use this when you need a specific term across many sermons "
            "without reading each transcript. For homiletics insights distilled "
            "from prep work, use search_prior_sessions(search_in=['insights'])."
        ),
        'input_schema': {
            'type': 'object',
            'required': ['query'],
            'properties': {
                'query': {'type': 'string'},
                'scope': {'type': 'string', 'enum': list(VALID_SCOPES)},
                'search_in': {
                    'type': 'array',
                    'items': {'type': 'string', 'enum': list(VALID_SERMON_SURFACES)},
                },
            },
        },
    },
    {
        'name': 'read_prior_sermon',
        'description': (
            "Read a specific prior sermon in depth. Sections: 'review' (the coach "
            "analyzer's full rubric row — clarity, application landing, helpers/"
            "hurters, one-change, etc.; None when not yet analyzed), 'transcript' "
            "(full preached text — heavy, use only when needed), 'moments' (tagged "
            "spans), 'flags' (analyzer flags). Default: ['review']. The coach's "
            "feedback lives in 'review' — use it when you want to know what worked "
            "and what flopped on a prior sermon."
        ),
        'input_schema': {
            'type': 'object',
            'required': ['sermon_id'],
            'properties': {
                'sermon_id': {'type': 'integer'},
                'sections': {
                    'type': 'array',
                    'items': {'type': 'string',
                              'enum': ['review', 'transcript', 'moments', 'flags']},
                },
            },
        },
    },
]


# ─────────────────────────── Dispatch ─────────────────────────


_DISPATCH = {
    'list_prior_sessions': list_prior_sessions,
    'search_prior_sessions': search_prior_sessions,
    'read_prior_session': read_prior_session,
    'list_prior_sermons': list_prior_sermons,
    'search_prior_sermons': search_prior_sermons,
    'read_prior_sermon': read_prior_sermon,
}


def execute(tool_name, tool_input, session_context):
    """Dispatch a prior-work tool call. Returns the tool's structured result.

    Called from companion_tools.execute_tool when tool_name is one of ours.
    """
    fn = _DISPATCH.get(tool_name)
    if fn is None:
        return {'error': f'unknown_tool: {tool_name}'}
    return fn(tool_input or {}, session_context or {})


def tool_names():
    return list(_DISPATCH.keys())
