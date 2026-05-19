"""Sermons Flask blueprint.

Fourth blueprint extraction (polish #5b, 4 of 5). Holds the live sermon
upload + review + matching surface that integrates with SermonAudio.

This blueprint was already defined as `sermons_bp` inside app.py since
the original sermons feature shipped; it just lived in the same file
as the other app routes. The split moves the 18 routes to their own
module so app.py is no longer a 2500-line catch-all.

Tests still import the blueprint via `from app import app, get_db`;
`get_db()` now resolves to db_provider (imported below).

Routes (all under /sermons):
- GET  /                            sermons_list
- GET  /<id>                        sermon_detail
- GET  /<id>/status                 sermon_status
- POST /sync                        sermon_sync
- POST /backfill?limit=N            sermon_backfill
- POST /backfill-srt                sermon_backfill_srt
- POST /<id>/reanalyze              sermon_reanalyze
- POST /<id>/link/<session_id>      sermon_link
- POST /<id>/unlink                 sermon_unlink
- POST /<id>/approve-candidate/<link_id>  sermon_approve_candidate
- GET  /patterns                    sermon_patterns
- GET  /sync-log                    sermon_sync_log_page
- POST /patterns/coach/message      meta_coach_message
- GET  /patterns/coach/commitment   meta_coach_commitment
- POST /patterns/coach/commitment   meta_coach_commitment_save
- POST /patterns/insights           meta_coach_save_insight
"""

import json
import os

from flask import (
    Blueprint, abort, jsonify, render_template, request,
)

from db_provider import get_db
from app_secrets import (
    sermonaudio_api_key, sermonaudio_broadcaster_id, anthropic_api_key,
)


# ── Sermons Blueprint ────────────────────────────────────────────────────

sermons_bp = Blueprint('sermons', __name__, url_prefix='/sermons')


@sermons_bp.route('/', methods=['GET'])
def sermons_list():
    db = get_db()
    conn = db._conn()
    rows = conn.execute("""
        SELECT id, title, preach_date, duration_seconds, sync_status, match_status,
               ui_last_seen_version, source_version
        FROM sermons
        WHERE classified_as = 'sermon' AND is_remote_deleted = 0
        ORDER BY preach_date DESC
        LIMIT 100
    """).fetchall()
    sermons = [dict(
        id=r[0], title=r[1], preach_date=r[2], duration_seconds=r[3],
        sync_status=r[4], match_status=r[5],
        badge=(r[6] < r[7]),
    ) for r in rows]
    conn.close()
    return render_template('sermons/list.html', sermons=sermons)


@sermons_bp.route('/<int:sermon_id>', methods=['GET'])
def sermon_detail(sermon_id):
    db = get_db()
    conn = db._conn()
    sermon_row = conn.execute("SELECT * FROM sermons WHERE id = ?", (sermon_id,)).fetchone()
    if not sermon_row:
        conn.close()
        abort(404)
    sermon = dict(sermon_row)

    from sermon_coach_tools import get_sermon_review as _get_review_parsed
    review = _get_review_parsed(db, sermon_id)

    flags_rows = conn.execute("""
        SELECT id, flag_type, severity, transcript_start_sec, transcript_end_sec,
               section_label, excerpt, rationale
        FROM sermon_flags WHERE sermon_id = ?
        ORDER BY transcript_start_sec
    """, (sermon_id,)).fetchall()
    flags = [dict(r) for r in flags_rows]

    candidates_rows = conn.execute("""
        SELECT sl.id, sl.sermon_id, sl.session_id, sl.match_reason, s.passage_ref
        FROM sermon_links sl
        JOIN sessions s ON s.id = sl.session_id
        WHERE sl.sermon_id = ? AND sl.link_status = 'candidate'
    """, (sermon_id,)).fetchall()
    candidates = [dict(r) for r in candidates_rows]

    # Compare-and-set: only bump ui_last_seen_version if it's behind source_version
    conn.execute("""
        UPDATE sermons SET ui_last_seen_version = source_version
        WHERE id = ? AND ui_last_seen_version < source_version
    """, (sermon_id,))
    conn.commit()
    conn.close()

    return render_template(
        'sermons/detail.html',
        sermon=sermon, review=review, flags=flags, candidates=candidates,
    )


@sermons_bp.route('/<int:sermon_id>/status', methods=['GET'])
def sermon_status(sermon_id):
    db = get_db()
    conn = db._conn()
    row = conn.execute("""
        SELECT sync_status, match_status, source_version, ui_last_seen_version
        FROM sermons WHERE id = ?
    """, (sermon_id,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    return jsonify(dict(
        sync_status=row[0], match_status=row[1],
        source_version=row[2], ui_last_seen_version=row[3],
    ))


def _make_sermonaudio_client():
    """Factory for the real API client. Tests monkeypatch this."""
    from sermonaudio_sync import SermonAudioAPIClient
    return SermonAudioAPIClient(sermonaudio_api_key())


def _broadcaster_id() -> str:
    return sermonaudio_broadcaster_id()


@sermons_bp.route('/sync', methods=['POST'])
def sermon_sync():
    from sermonaudio_sync import run_sync
    from sermon_matcher import dispatch_matching
    from sermon_analyzer import dispatch_pending_analyses
    from llm_client import AnthropicClient
    db = get_db()
    result = run_sync(db, client=_make_sermonaudio_client(),
                      broadcaster_id=_broadcaster_id(), trigger='manual')
    if result is None:
        return jsonify({'error': 'sync already running'}), 409
    result['sermons_matched'] = dispatch_matching(db)
    client = AnthropicClient(api_key=anthropic_api_key())
    analyzed = dispatch_pending_analyses(db, llm_client=client)
    result['sermons_analyzed'] = analyzed
    return jsonify(result), 202


@sermons_bp.route('/backfill', methods=['POST'])
def sermon_backfill():
    from sermonaudio_sync import run_sync
    from sermon_matcher import dispatch_matching
    from sermon_analyzer import dispatch_pending_analyses
    from llm_client import AnthropicClient
    # Bug review #8: int() crashed on ?limit=x. This endpoint kicks off
    # external API calls; clamp to a reasonable ceiling so a typo doesn't
    # trigger 500 sermon-syncs.
    try:
        limit = int(request.args.get('limit', 24))
    except (TypeError, ValueError):
        limit = 24
    limit = max(1, min(limit, 500))
    db = get_db()
    result = run_sync(db, client=_make_sermonaudio_client(),
                      broadcaster_id=_broadcaster_id(), trigger='backfill', limit=limit)
    if result is None:
        return jsonify({'error': 'sync already running'}), 409
    result['sermons_matched'] = dispatch_matching(db)
    client = AnthropicClient(api_key=anthropic_api_key())
    analyzed = dispatch_pending_analyses(db, llm_client=client)
    result['sermons_analyzed'] = analyzed
    return jsonify(result), 202


@sermons_bp.route('/backfill-srt', methods=['POST'])
def sermon_backfill_srt():
    """Re-fetch SRT for all sermons and populate transcript_segments."""
    import json as _json
    from srt_parser import parse_srt_segments, validate_segments, build_canonical_transcript
    from sermonaudio_sync import _fetch_srt_raw
    db = get_db()
    conn = db._conn()
    rows = conn.execute("""
        SELECT id, sermonaudio_id, duration_seconds FROM sermons
        WHERE classified_as = 'sermon' AND transcript_segments IS NULL
    """).fetchall()

    results = {'total': len(rows), 'updated': 0, 'failed': 0, 'no_srt': 0}
    for row in rows:
        sermon_id, sa_id, duration = row[0], row[1], row[2] or 0
        try:
            from sermonaudio.node.requests import Node
            detail = Node.get_sermon(sa_id)
            caption = detail.media.caption[0] if detail.media and detail.media.caption else None
            if not caption or not caption.download_url:
                results['no_srt'] += 1
                continue
            srt_raw = _fetch_srt_raw(caption.download_url)
            if not srt_raw:
                results['no_srt'] += 1
                continue
            segments = parse_srt_segments(srt_raw)
            if not segments:
                results['failed'] += 1
                continue
            quality = validate_segments(segments, duration)
            canonical = build_canonical_transcript(segments)
            conn.execute("""
                UPDATE sermons SET transcript_segments = ?, transcript_quality = ?,
                    transcript_text = ? WHERE id = ?
            """, (_json.dumps(segments), quality, canonical, sermon_id))
            conn.commit()
            results['updated'] += 1
        except Exception as e:
            results['failed'] += 1
    conn.close()
    return jsonify(results)


@sermons_bp.route('/<int:sermon_id>/reanalyze', methods=['POST'])
def sermon_reanalyze(sermon_id):
    """Re-run sermon analysis. 404 BEFORE touching secrets/clients —
    nonexistent IDs shouldn't surface unrelated secret/client errors."""
    from sermon_analyzer import analyze_sermon
    from llm_client import AnthropicClient
    db = get_db()
    conn = db._conn()
    try:
        exists = conn.execute(
            "SELECT 1 FROM sermons WHERE id = ?", (sermon_id,)
        ).fetchone()
        if not exists:
            return jsonify({'error': 'sermon not found'}), 404
        updated = conn.execute(
            "UPDATE sermons SET sync_status = 'analysis_pending', last_state_change_at = datetime('now') "
            "WHERE id = ? AND sync_status NOT IN ('analysis_pending', 'analysis_running')",
            (sermon_id,),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    if not updated:
        return jsonify({'error': 'analysis already pending or running'}), 409
    # Secrets + client only after we've confirmed the work is real.
    client = AnthropicClient(api_key=anthropic_api_key())
    result = analyze_sermon(db, sermon_id, llm_client=client)
    return jsonify(result)


@sermons_bp.route('/<int:sermon_id>/link/<int:session_id>', methods=['POST'])
def sermon_link(sermon_id, session_id):
    # Bug review #9: was 500 with FK constraint error for missing rows.
    db = get_db()
    conn = db._conn()
    try:
        sermon_row = conn.execute("SELECT id FROM sermons WHERE id = ?", (sermon_id,)).fetchone()
        if not sermon_row:
            return jsonify({'error': 'sermon not found'}), 404
        session_row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not session_row:
            return jsonify({'error': 'session not found'}), 404
        conn.execute(
            "UPDATE sermon_links SET link_status = 'rejected' WHERE sermon_id = ? AND link_status = 'active'",
            (sermon_id,),
        )
        conn.execute("""
            INSERT INTO sermon_links
                (sermon_id, session_id, link_status, link_source, match_reason, created_at)
            VALUES (?, ?, 'active', 'manual', 'user_linked', datetime('now'))
        """, (sermon_id, session_id))
        conn.execute(
            "UPDATE sermons SET match_status = 'matched', last_match_attempt_at = datetime('now') WHERE id = ?",
            (sermon_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({'ok': True, 'link_status': 'active', 'link_source': 'manual'})


@sermons_bp.route('/<int:sermon_id>/unlink', methods=['POST'])
def sermon_unlink(sermon_id):
    # Bug review #9: was silently {ok:true} on missing sermon_id.
    db = get_db()
    conn = db._conn()
    try:
        sermon_row = conn.execute("SELECT id FROM sermons WHERE id = ?", (sermon_id,)).fetchone()
        if not sermon_row:
            return jsonify({'error': 'sermon not found'}), 404
        conn.execute("""
            UPDATE sermon_links SET link_status = 'rejected'
            WHERE sermon_id = ? AND link_status = 'active'
        """, (sermon_id,))
        conn.execute(
            "UPDATE sermons SET match_status = 'rejected_all' WHERE id = ?",
            (sermon_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({'ok': True})


@sermons_bp.route('/<int:sermon_id>/approve-candidate/<int:link_id>', methods=['POST'])
def sermon_approve_candidate(sermon_id, link_id):
    # Bug review #9: was silently {ok:true} when sermon or link didn't exist.
    db = get_db()
    conn = db._conn()
    try:
        link_row = conn.execute(
            "SELECT id FROM sermon_links WHERE id = ? AND sermon_id = ?",
            (link_id, sermon_id),
        ).fetchone()
        if not link_row:
            return jsonify({
                'error': 'candidate link not found for this sermon'
            }), 404
        conn.execute(
            "DELETE FROM sermon_links WHERE sermon_id = ? AND link_status = 'active'",
            (sermon_id,),
        )
        conn.execute("""
            UPDATE sermon_links SET link_status = 'active'
            WHERE id = ? AND sermon_id = ?
        """, (link_id, sermon_id))
        conn.execute(
            "UPDATE sermons SET match_status = 'matched' WHERE id = ?",
            (sermon_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({'ok': True})


@sermons_bp.route('/patterns', methods=['GET'])
def sermon_patterns():
    from sermon_coach_tools import get_sermon_patterns
    from meta_coach_tools import get_active_commitment
    db = get_db()
    data = get_sermon_patterns(db)
    commitment = get_active_commitment(db)
    return render_template('sermons/patterns.html', patterns=data, commitment=commitment)


@sermons_bp.route('/sync-log', methods=['GET'])
def sermon_sync_log_page():
    db = get_db()
    conn = db._conn()
    runs = conn.execute("""
        SELECT run_id, trigger, started_at, ended_at, sermons_new, sermons_updated,
               sermons_failed, status, error_summary
        FROM sermon_sync_log
        ORDER BY started_at DESC LIMIT 20
    """).fetchall()
    cost_total = conn.execute(
        "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM sermon_analysis_cost_log WHERE called_at >= date('now','-30 days')"
    ).fetchone()[0]
    conn.close()
    return render_template('sermons/sync_log.html',
                             runs=[dict(r) for r in runs], cost_30d=cost_total)


@sermons_bp.route('/patterns/coach/message', methods=['POST'])
def meta_coach_message():
    import json as _json
    from meta_coach_agent import stream_meta_coach_response
    from llm_client import AnthropicClient
    user_message = request.json.get('message') if request.is_json else request.form.get('message', '')
    try:
        conversation_id = int(request.json.get('conversation_id', 0)) if request.is_json else 0
    except (ValueError, TypeError):
        conversation_id = 0
    db = get_db()
    api_key = anthropic_api_key()
    client = AnthropicClient(api_key=api_key)

    def generate():
        for event in stream_meta_coach_response(
            db=db, conversation_id=conversation_id,
            user_message=user_message, llm_client=client,
        ):
            yield f"data: {_json.dumps(event)}\n\n"

    return Response(generate(), mimetype='text/event-stream')


@sermons_bp.route('/patterns/coach/commitment', methods=['GET'])
def get_commitment():
    from meta_coach_tools import get_active_commitment
    db = get_db()
    commitment = get_active_commitment(db)
    return jsonify(commitment or {})


@sermons_bp.route('/patterns/coach/commitment', methods=['POST'])
def create_commitment():
    # silent=True so non-JSON bodies don't 415 (bug review #1)
    data = request.get_json(silent=True) or {}
    dimension_key = data.get('dimension_key', '')
    experiment = data.get('practice_experiment', '')
    if not dimension_key or not experiment:
        return jsonify({'error': 'dimension_key and practice_experiment required'}), 400
    try:
        target = int(data.get('target_sermons', 2))
    except (ValueError, TypeError):
        return jsonify({'error': 'target_sermons must be an integer'}), 400
    db = get_db()
    conn = db._conn()
    try:
        baseline = conn.execute(
            "SELECT id FROM sermons WHERE classified_as='sermon' ORDER BY preach_date DESC LIMIT 1"
        ).fetchone()
        baseline_id = baseline[0] if baseline else None
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE coaching_commitments SET status='superseded' WHERE status='active'")
        conn.execute("""
            INSERT INTO coaching_commitments (dimension_key, practice_experiment, target_sermons,
                baseline_sermon_id, status, created_at)
            VALUES (?, ?, ?, ?, 'active', ?)
        """, (dimension_key, experiment, target, baseline_id, now))
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return jsonify({'id': new_id, 'status': 'active'})
    finally:
        conn.close()


@sermons_bp.route('/<int:sermon_id>/coach/history', methods=['GET'])
def sermon_coach_history(sermon_id):
    db = get_db()
    try:
        conversation_id = int(request.args.get('conversation_id', 1))
    except (ValueError, TypeError):
        conversation_id = 1
    conn = db._conn()
    rows = conn.execute("""
        SELECT role, content FROM sermon_coach_messages
        WHERE sermon_id = ? AND conversation_id = ? AND role IN ('user', 'assistant')
        ORDER BY id
    """, (sermon_id, conversation_id)).fetchall()
    conn.close()
    return jsonify([{'role': r[0], 'content': r[1]} for r in rows if r[1]])


@sermons_bp.route('/<int:sermon_id>/coach/message', methods=['POST'])
def sermon_coach_message(sermon_id):
    import json as _json
    from sermon_coach_agent import stream_coach_response
    from llm_client import AnthropicClient
    user_message = request.json.get('message') if request.is_json else request.form.get('message', '')
    try:
        conversation_id = int(request.json.get('conversation_id', 1)) if request.is_json else 1
    except (ValueError, TypeError):
        conversation_id = 1
    db = get_db()
    api_key = anthropic_api_key()
    client = AnthropicClient(api_key=api_key)

    def generate():
        for event in stream_coach_response(
            db=db, sermon_id=sermon_id, conversation_id=conversation_id,
            user_message=user_message, llm_client=client,
        ):
            yield f"data: {_json.dumps(event)}\n\n"

    return Response(generate(), mimetype='text/event-stream')


@sermons_bp.route('/coaching-insight', methods=['POST'])
def create_coaching_insight():
    import json as _json
    # silent=True so non-JSON bodies return 400 instead of 415 (bug review #1)
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or not (data.get('summary') or '').strip():
        return jsonify({'error': 'summary is required'}), 400
    # Validate that applies_when / avoid_when (if present) are lists of strings
    # (bug review #10). A string would silently become list-of-chars; an int
    # raises TypeError inside json.dumps(list(...)).
    for field in ('applies_when', 'avoid_when'):
        if field in data:
            v = data[field]
            if not isinstance(v, list) or not all(isinstance(s, str) for s in v):
                return jsonify({
                    'error': f'{field} must be a list of strings'
                }), 400
    db = get_db()
    conn = db._conn()
    try:
        conn.execute("""
            INSERT INTO coaching_insights
                (dimension_key, summary, applies_when, avoid_when,
                 source_sermon_id, source_conversation_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            data.get('dimension_key'),
            data['summary'].strip(),
            _json.dumps(list(data.get('applies_when', []))),
            _json.dumps(list(data.get('avoid_when', []))),
            data.get('source_sermon_id'),
            data.get('source_conversation_id'),
        ))
        insight_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return jsonify({'id': insight_id, 'status': 'saved'}), 201
    finally:
        conn.close()


