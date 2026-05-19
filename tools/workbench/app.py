#!/usr/bin/env python3
"""
Sermon Research Workbench — Flask application.

Run:
    cd /Volumes/External/Logos4/tools/workbench
    python3 app.py
    # Open http://localhost:5111
"""

import atexit
import json
import os
import sys
from pathlib import Path

# Add tools directory to path
TOOLS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(Path(__file__).parent))

# Load .env file if present (simple key=value parsing)
_env_file = TOOLS_DIR / "workbench" / ".env"
if not _env_file.exists():
    _env_file = TOOLS_DIR.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k.strip(), v)

# Pin embedding model + framework caches to the external drive so we don't
# bloat the system disk. Set BEFORE any import that might pull in torch
# or sentence-transformers.
_EXT = "/Volumes/External/Logos4/Data"
os.environ.setdefault("HF_HOME", f"{_EXT}/huggingface")
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", f"{_EXT}/huggingface/sentence-transformers")
os.environ.setdefault("TORCH_HOME", f"{_EXT}/torch")
# Extend sys.path with the externally-installed dependencies so workbench
# imports of sentence_transformers/torch/numpy resolve to the external copy.
_DEPS = f"{_EXT}/python-deps"
if os.path.isdir(_DEPS) and _DEPS not in sys.path:
    sys.path.insert(0, _DEPS)

import sqlite3
from functools import wraps
from flask import Flask, Response, render_template, request, jsonify, redirect, url_for, session, make_response, Blueprint, abort

import workbench_db as db
from workbench_agent import stream_agent_response, PHASES
from study import (
    init_batch_reader, shutdown_batch_reader, parse_reference,
    find_bibles, find_commentaries_for_ref, find_lexicons,
    read_bible_chapter, extract_verses, clean_bible_text, read_annotations,
    get_interlinear_for_chapter, run_reader, read_article_text,
    resolve_bible_files, find_study_bible_notes, CATALOG_DB, RESOURCE_MGR_DB,
)
from companion_db import CompanionDB, PHASES_ORDER, PHASE_TIMERS
from companion_agent import build_study_prompt, stream_study_response
from session_analytics import SessionAnalytics
from accuser_detector import AccuserDetector
from mentor_reference_store import MentorReferenceStore
from pastor_feedback_store import PastorFeedbackStore
from session_decisions import SessionDecisionStore, DECISION_KEYS


_DECISION_LABEL_MAP = {
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
from genre_map import get_genre
from seed_questions import seed_question_bank
from series_planner import propose_series_plan
from app_secrets import sermonaudio_api_key, sermonaudio_broadcaster_id, anthropic_api_key
from validators import (
    parse_int_arg, json_body, require_string, require_int,
    require_list_of_strings, require_session,
)

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    _APSCHEDULER_AVAILABLE = True
except ImportError:
    _APSCHEDULER_AVAILABLE = False

_scheduler = None


def _scheduled_sermon_sync():
    """The 4h cron body. Sync → match → analyze."""
    try:
        from sermonaudio_sync import run_sync
        from sermon_matcher import dispatch_matching
        from sermon_analyzer import dispatch_pending_analyses
        from llm_client import AnthropicClient
        db = get_db()
        run_sync(db, client=_make_sermonaudio_client(),
                 broadcaster_id=_broadcaster_id(), trigger='cron')
        dispatch_matching(db)
        client = AnthropicClient(api_key=anthropic_api_key())
        dispatch_pending_analyses(db, llm_client=client)
    except Exception as e:
        app.logger.error(f'Scheduled sermon sync failed: {e}')


def get_scheduler():
    """Return the singleton background scheduler. Lazy-initializes on first call.

    NOTE: Calling this starts a daemon thread. Production code should call this
    from the __main__ block. Tests can call it to inspect job registration.
    """
    global _scheduler
    if not _APSCHEDULER_AVAILABLE:
        raise RuntimeError('apscheduler not installed')
    if _scheduler is None:
        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.add_job(
            _scheduled_sermon_sync,
            IntervalTrigger(hours=4),
            id='sermon_sync_cron',
            replace_existing=True,
        )
        _scheduler.start()
    return _scheduler


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(32).hex())

# ── Auth moved to bp_auth ────────────────────────────────────────────────
from bp_auth import bp_auth
app.register_blueprint(bp_auth)


# ── Jinja Filters ────────────────────────────────────────────────────────

@app.template_filter('safe_assistant')
def safe_assistant_filter(content):
    """Render assistant message content, which may be JSON with tool_use blocks."""
    from markupsafe import escape, Markup
    try:
        blocks = json.loads(content)
        if isinstance(blocks, list):
            parts = []
            for block in blocks:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(str(escape(block.get("text", ""))))
                    elif block.get("type") == "tool_use":
                        name = escape(block.get("name", ""))
                        parts.append(f'<span class="ri-type">{name}</span>')
            return Markup("<br>".join(parts)) if parts else escape(content)
    except (json.JSONDecodeError, TypeError):
        pass
    return escape(content)


# ── Lifecycle ─────────────────────────────────────────────────────────────

companion_db = None
study_analytics = None
accuser_detector = None
mentor_store = None
pastor_feedback = None
session_decisions = None

# get_db() moved to db_provider during the blueprint split (avoid circular
# import from bp_sermons). Re-export for backwards compatibility with tests
# that import `from app import get_db`.
from db_provider import get_db


def startup():
    db.init_db()
    init_batch_reader()
    # Initialize companion DB
    global companion_db, study_analytics, accuser_detector, mentor_store, pastor_feedback, session_decisions
    db_path = os.environ.get("COMPANION_DB_PATH", str(TOOLS_DIR / "workbench" / "companion.db"))
    companion_db = CompanionDB(db_path)
    companion_db.init_db()
    # Initialize analytics DB
    study_analytics = SessionAnalytics(db_path)
    study_analytics.init_db()
    # Initialize accuser-voice detector (Cycle 2a: detection-only)
    accuser_detector = AccuserDetector(db_path)
    accuser_detector.init_db()
    # Initialize mentor reference store (Cycle 2c: substrate)
    mentor_store = MentorReferenceStore(db_path)
    mentor_store.init_db()
    # One-shot seed of the starter example if the table is empty so
    # lookup_mentor_position returns something on the first sermon cycle.
    if not mentor_store.list_mentors():
        mentor_store.seed_examples()
    # Initialize pastor feedback substrate (v2 §10.1)
    pastor_feedback = PastorFeedbackStore(db_path)
    pastor_feedback.init_db()
    # Initialize session decisions tracker (codex review item #7)
    session_decisions = SessionDecisionStore(db_path)
    session_decisions.init_db()
    # Seed question bank if empty
    if not companion_db.get_questions('prayer'):
        seed_question_bank(companion_db)

def shutdown():
    shutdown_batch_reader()

atexit.register(shutdown)

# ── Template Helpers ─────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    return {"phases": PHASES}


def _is_sermon_work_page_load(req):
    """True for full GET page loads of sermon-work surfaces (not HTMX fragments)."""
    if req.method != "GET":
        return False
    if req.headers.get("HX-Request"):
        return False
    return req.path.startswith("/study/")


# Variable phrasings per cutoff type — rotate by day-of-year so the same
# message doesn't fire two days running and habituate to background noise.
# Per v2 §8.3 EF principle: predictable alarms get tuned out within 2 weeks.
_CUTOFF_MESSAGES = {
    'daily_5pm': [
        "It's past 5pm. Family time begins now. Sermon work can wait until tomorrow.",
        "Past 5pm. Olivia and the kids first. The work will be here tomorrow.",
        "5pm. The family is more important than the manuscript right now.",
    ],
    'friday_3pm': [
        "It's past Friday 3pm. The sermon prep window is closed for the week. Saturday belongs to family and rest.",
        "Friday 3pm. Whatever is written is what you'll preach. Saturday is for the family.",
        "Past Friday cutoff. Trust the work the Lord gave you this week.",
    ],
    'weekend_lockout': [
        "It's the weekend. Sermon work waits for Tuesday — the work the Lord provided this past week is enough for this Sunday.",
        "Saturday. The Lord made this day for rest. Sunday's sermon is what it is.",
        "Weekend lockout. Open the manuscript Sunday morning at 6:30am for read-through — not before.",
    ],
}


def _pick_cutoff_message(cutoff_type):
    msgs = _CUTOFF_MESSAGES.get(cutoff_type)
    if not msgs:
        return None
    # Rotate by day-of-year so adjacent days get different phrasings.
    from datetime import datetime
    idx = datetime.now().timetuple().tm_yday % len(msgs)
    return msgs[idx]


@app.context_processor
def inject_cutoff_banner():
    """Advisory cutoff banner — notification + logging, no enforcement.

    Three cutoff types in precedence order: weekend_lockout > friday_3pm > daily_5pm.
    The most-specific active cutoff wins so the banner copy matches what's actually
    closing. Throttled to one event per (route, cutoff_type) per 15 min.
    """
    if not study_analytics:
        return {}
    if not _is_sermon_work_page_load(request):
        return {}
    cutoff_type = study_analytics.active_cutoff_type()
    if cutoff_type is None:
        return {}
    # Defensive: if the cutoff_events table is missing (e.g., a test DB that
    # wasn't init'd), degrade silently rather than 500-ing every page render.
    try:
        recent = study_analytics.get_recent_cutoff_event(request.path, within_minutes=15)
    except Exception:
        return {}
    # Re-throttling: if a recent event exists AND it's the same cutoff type,
    # honor dismiss; otherwise log a new event so transitioning between types
    # (e.g. Friday 3pm → Saturday weekend) re-surfaces the correct banner.
    if recent and recent.get("cutoff_type") == cutoff_type and recent["dismissed"]:
        return {}
    if recent and recent.get("cutoff_type") == cutoff_type:
        event_id = recent["id"]
    else:
        event_id = study_analytics.log_cutoff_event(request.path, cutoff_type)
    return {
        "cutoff_banner_active": True,
        "cutoff_event_id": event_id,
        "cutoff_event_type": cutoff_type,
        "cutoff_banner_message": _pick_cutoff_message(cutoff_type),
    }


@app.route("/api/cutoff-events/<int:event_id>/dismiss", methods=["POST"])
def api_dismiss_cutoff_event(event_id):
    """Mark a cutoff event as dismissed so its banner stops rendering."""
    if not study_analytics:
        abort(503)
    conn = study_analytics._conn()
    row = conn.execute(
        "SELECT id FROM cutoff_events WHERE id = ?", (event_id,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    study_analytics.mark_cutoff_dismissed(event_id)
    return ("", 204)


# ── Page Routes ──────────────────────────────────────────────────────────

# ── Workbench routes (/, /project/, /library, /api/projects/*, /api/chat)
# moved to bp_workbench. /api/passage, /api/interlinear, /api/library/*,
# /api/resource/* live in bp_passage.


# ── /companion/ legacy routes REMOVED (polish #5b) ──────────────────────
# The original card-based UI at /companion/* (15 routes, 5 templates) was
# superseded by /study/* and Bryan confirmed he never visits it. Deleted
# along with its tests, templates, and CSS/JS. card_responses table +
# save_card_response/get_card_responses helpers are retained — /study/
# and the persona agents still read them for context. Git history holds
# the deleted routes if a future need arises.





# ── Card Phases (Study phases 1-5 use cards, 6+ use conversation) ──────

CARD_PHASES = [
    {"key": "prayer", "label": "Prayer", "prompt": "Pray for your study of this passage.",
     "guidance": "Ask the Spirit to open your eyes to see wonderful things in His word. Pray for your congregation.",
     "has_textarea": True, "auto_resource": None, "layout": "single"},
    {"key": "read_translate", "label": "Read & Translate", "prompt": "Read the original text and write your working translation.",
     "guidance": "Work from the Greek/Hebrew. Don't reach for a published translation — wrestle with the words yourself.",
     "has_textarea": True, "auto_resource": "original_language", "layout": "side_by_side"},
    {"key": "digestion", "label": "Digestion", "prompt": "Pray through the text phrase by phrase.",
     "guidance": "Take each phrase and turn it into prayer. What is God saying? What stirs in you?",
     "has_textarea": True, "auto_resource": None, "layout": "single"},
    {"key": "study_bibles", "label": "Study Bible Consultation", "prompt": "Review notes from your study bibles.",
     "guidance": "Star passages that catch your eye. Use the notepad to capture your thinking. These feed into the conversation.",
     "has_textarea": False, "auto_resource": "study_bibles", "layout": "single"},
]


def _get_original_language_text(session):
    """Get THGNT (NT) or BHS (OT) text for the session's passage."""
    try:
        ref = parse_reference(session["passage_ref"])
        if not ref:
            return None
        version = "THGNTCROSSWAY" if ref["book"] >= 40 else "BHS"
        files = resolve_bible_files([version])
        if not files:
            return None
        text, _ = read_bible_chapter(files[0], ref["book"], ref["chapter"])
        if text and ref.get("verse_start"):
            text = extract_verses(text, ref["verse_start"], ref.get("verse_end"))
        return text
    except Exception:
        return None


def _get_study_bible_notes(session):
    """Get study bible notes for the session's passage."""
    try:
        ref = parse_reference(session["passage_ref"])
        if not ref:
            return []
        return find_study_bible_notes(ref, max_chars=5000)
    except Exception:
        return []


def _save_card_summary_for_ai(session_id):
    """Save a system message summarizing Bryan's card work for the AI."""
    responses = companion_db.get_card_responses(session_id)
    annotations = companion_db.get_card_annotations(session_id)
    notepad_text = companion_db.get_card_notepad(session_id, "study_bibles")
    parts = []
    for resp in responses:
        if resp.get("content"):
            parts.append(f"[{resp['phase']}] {resp['content']}")
    if annotations:
        parts.append("\n[Study Bible Stars]")
        for a in annotations:
            line = f"  \u2605 {a['source']}: \"{a['starred_text']}\""
            if a.get("note"):
                line += f" \u2192 {a['note']}"
            parts.append(line)
    if notepad_text:
        parts.append(f"\n[Study Bible Notepad]\n{notepad_text}")
    if parts:
        summary = "Bryan's card-phase work:\n" + "\n".join(parts)
        companion_db.save_message(session_id, "context", "system", summary)


# ── Study Routes (Conversation-First UI) ────────────────────────────────

@app.route("/study/")
def study_index():
    """Start page — enter a passage or continue a session."""
    sessions = companion_db.list_sessions(status='active')
    error = request.args.get('error')
    return render_template("study_start.html", sessions=sessions, error=error)


@app.route("/api/study/session/<int:session_id>/decision", methods=["POST"])
def api_set_session_decision(session_id):
    """Mark a homiletical decision committed for this session. Per codex
    review item #7: ADHD work can look active while avoiding decisions —
    explicit commits give the Composing Partner real signal."""
    if not session_decisions:
        abort(503)
    data = request.get_json(silent=True) or request.form
    key = (data.get("key") or "").strip()
    value = (data.get("value") or "").strip()
    if not key or not value:
        return jsonify({"error": "key and value required"}), 400
    try:
        session_decisions.set_decision(session_id=session_id, key=key, value=value)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "key": key, "value": value})


@app.route("/api/study/session/<int:session_id>/decision/<string:key>", methods=["DELETE"])
def api_clear_session_decision(session_id, key):
    if not session_decisions:
        abort(503)
    if key not in DECISION_KEYS:
        return jsonify({"error": f"unknown decision key: {key}"}), 400
    session_decisions.clear_decision(session_id, key)
    return ("", 204)


@app.route("/api/study/session/<int:session_id>/decisions", methods=["GET"])
def api_list_session_decisions(session_id):
    if not session_decisions:
        return jsonify({"decisions": [], "open": list(DECISION_KEYS)})
    return jsonify({
        "decisions": session_decisions.list_decisions(session_id),
        "open": session_decisions.open_decision_keys(session_id),
    })


@app.route("/study/session/<int:session_id>/decisions/panel", methods=["GET"])
def study_decisions_panel(session_id):
    """Render-only fragment for HTMX swap after a decision update."""
    if not session_decisions:
        return ""
    return render_template(
        "partials/decisions_panel.html",
        session=companion_db.get_session(session_id),
        committed_decisions=session_decisions.list_decisions(session_id),
        open_decision_keys=session_decisions.open_decision_keys(session_id),
        decision_labels=_DECISION_LABEL_MAP,
    )


@app.route("/study/session/<int:session_id>/decisions/set", methods=["POST"])
def study_decisions_set(session_id):
    """HTMX form post — set a decision and swap back the panel."""
    if not session_decisions:
        abort(503)
    key = (request.form.get("key") or "").strip()
    value = (request.form.get("value") or "").strip()
    if key and value:
        try:
            session_decisions.set_decision(session_id=session_id, key=key, value=value)
        except ValueError:
            pass
    return study_decisions_panel(session_id)


@app.route("/study/session/<int:session_id>/decisions/clear", methods=["POST"])
def study_decisions_clear(session_id):
    """HTMX form post — clear a decision and swap back the panel."""
    if not session_decisions:
        abort(503)
    key = (request.form.get("key") or "").strip()
    if key in DECISION_KEYS:
        session_decisions.clear_decision(session_id, key)
    return study_decisions_panel(session_id)


@app.route("/study/feedback/new", methods=["GET"])
def study_feedback_new():
    """Form for Bryan to capture feedback he received in person — Olivia,
    elders, congregants. Olivia-direct channels layer on top of the same
    substrate later (her choice of email/SMS/Dropbox/dashboard)."""
    return render_template("study_feedback_new.html")


@app.route("/study/feedback", methods=["POST"])
def study_feedback_create():
    """Persist a captured-feedback entry. Redirect back to the form with a
    flash-style query param (no full flash infra used here)."""
    if not pastor_feedback:
        abort(503)
    source = request.form.get("source_label", "").strip()
    content = request.form.get("content", "").strip()
    valence = request.form.get("valence", "positive").strip() or "positive"
    sermon_id_raw = (request.form.get("sermon_id") or "").strip()
    sermon_id = int(sermon_id_raw) if sermon_id_raw.isdigit() else None
    if not source or not content:
        return redirect(url_for("study_feedback_new", error="missing"))
    try:
        pastor_feedback.add_feedback(
            source_label=source, content=content, valence=valence,
            sermon_id=sermon_id, captured_by="bryan_typed",
        )
    except ValueError:
        return redirect(url_for("study_feedback_new", error="invalid"))
    return redirect(url_for("study_feedback_new", saved="1"))


@app.route("/study/patterns")
def study_patterns():
    """EF-prosthesis pattern review panel — cutoff overrides, accuser-voice
    detection frequency, recent matched examples, session activity. Designed
    for Bryan's weekly/monthly calibration review."""
    from patterns_report import (
        cutoff_summary, cutoff_recent_dismissed_rate,
        accuser_category_summary, accuser_recent_examples,
        recent_session_activity, decision_velocity_by_session,
    )
    days = parse_int_arg('days', default=30, lo=1, hi=365)
    cutoffs = cutoff_summary(study_analytics, days=days) if study_analytics else []
    dismiss_rate = cutoff_recent_dismissed_rate(study_analytics, days=days) if study_analytics else 0.0
    accuser_cats = accuser_category_summary(accuser_detector, days=days) if accuser_detector else []
    accuser_recent = accuser_recent_examples(accuser_detector, limit=10) if accuser_detector else []
    session_stats = recent_session_activity(companion_db, days=days) if companion_db else {'active_sessions': 0}
    feedback_recent = pastor_feedback.recent_feedback(limit=8) if pastor_feedback else []
    feedback_counts = pastor_feedback.count_by_valence() if pastor_feedback else {}
    decision_velocity = decision_velocity_by_session(companion_db, session_decisions, days=days) if companion_db else []
    return render_template(
        "study_patterns.html",
        days=days,
        cutoffs=cutoffs,
        dismiss_rate=dismiss_rate,
        accuser_cats=accuser_cats,
        accuser_recent=accuser_recent,
        session_stats=session_stats,
        feedback_recent=feedback_recent,
        feedback_counts=feedback_counts,
        decision_velocity=decision_velocity,
    )


@app.route("/study/session/<int:session_id>/delete", methods=["POST"])
def study_delete_session(session_id):
    """Delete a study session and all its data."""
    companion_db.delete_session(session_id)
    return redirect(url_for("study_index"))


@app.route("/study/session/new", methods=["POST"])
def study_new_session():
    """Create a new study session."""
    passage = request.form.get("passage", "").strip()
    if not passage:
        return redirect(url_for("study_index", error="Please enter a passage"))

    try:
        ref = parse_reference(passage)
    except ValueError as e:
        return redirect(url_for("study_index", error=str(e)))

    genre = get_genre(ref["book"])
    session_id = companion_db.create_session(
        passage_ref=passage,
        book=ref["book"],
        chapter=ref["chapter"],
        verse_start=ref["verse_start"],
        verse_end=ref["verse_end"],
        genre=genre,
    )
    # Record initial phase entry
    if study_analytics:
        study_analytics.record_phase_enter(session_id, 'prayer')
    return redirect(url_for("study_session_view", session_id=session_id))


@app.route("/study/session/<int:session_id>")
def study_session_view(session_id):
    """Main session view — card UI for phases 1-5, conversation for 6+."""
    session = companion_db.get_session(session_id)
    if not session:
        return redirect(url_for("study_index"))

    # Series context — empty list when standalone.
    series_members = companion_db.get_series(session_id)
    parent_session = None
    if session.get('parent_session_id'):
        parent_session = companion_db.get_parent(session_id)
    is_child = bool(parent_session)

    # Sermon-coach integration: which sermons are linked to this session?
    linked_sermons = []
    review = None
    candidates = []
    conn = companion_db._conn()
    try:
        link_rows = conn.execute("""
            SELECT s.*
            FROM sermons s
            JOIN sermon_links sl ON sl.sermon_id = s.id
            WHERE sl.session_id = ? AND sl.link_status = 'active'
            ORDER BY s.preach_date DESC
        """, (session_id,)).fetchall()
        linked_sermons = [dict(r) for r in link_rows]
        if linked_sermons:
            from sermon_coach_tools import get_sermon_review as _get_review_parsed
            review = _get_review_parsed(companion_db, linked_sermons[0]['id'])
        candidate_rows = conn.execute("""
            SELECT sl.id, sl.sermon_id, sl.match_reason,
                   s.title, s.bible_text_raw, s.preach_date
            FROM sermon_links sl
            JOIN sermons s ON s.id = sl.sermon_id
            WHERE sl.link_status = 'candidate' AND sl.session_id = ?
            ORDER BY s.preach_date DESC
        """, (session_id,)).fetchall()
        candidates = [dict(c) for c in candidate_rows]
    finally:
        conn.close()

    phase = session.get("current_phase", "prayer")
    card_phase_keys = [p["key"] for p in CARD_PHASES]

    if phase in card_phase_keys:
        # Card mode (phases 1-5).
        # Children inherit parent card content (read-only) per v1 spec: no
        # per-child card overrides yet. Auto-resources, card responses, and the
        # card-draft notepad all key off the parent when this is a child.
        phase_index = card_phase_keys.index(phase)
        card_def = CARD_PHASES[phase_index]
        card_session = parent_session if is_child else session
        card_session_id = card_session['id']
        resource_text = None
        study_bible_notes = None
        if card_def["auto_resource"] == "original_language":
            resource_text = _get_original_language_text(card_session)
        elif card_def["auto_resource"] == "study_bibles":
            study_bible_notes = _get_study_bible_notes(card_session)
        responses = companion_db.get_card_responses(card_session_id, phase)
        # Check auto-saved draft first, then last submitted response
        prefilled = companion_db.get_card_notepad(card_session_id, f"card_{phase}")
        if not prefilled:
            prefilled = responses[-1]["content"] if responses else ""
        # Annotations + study-bible notepad stay per-child.
        annotations = companion_db.get_card_annotations(session_id, phase) if phase == "study_bibles" else []
        notepad = companion_db.get_card_notepad(session_id, phase) if phase == "study_bibles" else ""
        from phase_cluster_router import persona_for_phase as _persona_for_phase
        from time_coach import assess_session as _tc_assess, week_context as _tc_week, prep_window_position as _tc_horizon
        return render_template("study_session.html",
                               session=session, mode="card",
                               persona=_persona_for_phase(phase),
                               time_coach=_tc_assess(companion_db, study_analytics, session_id),
                               time_coach_week=_tc_week(companion_db, study_analytics),
                               horizon=_tc_horizon(),
                               card=card_def, phase_index=phase_index,
                               total_phases=len(CARD_PHASES),
                               resource_text=resource_text,
                               study_bible_notes=study_bible_notes,
                               prefilled=prefilled,
                               annotations=annotations,
                               notepad=notepad,
                               messages=[],
                               outline=companion_db.get_outline_tree(session_id),
                               linked_sermons=linked_sermons,
                               review=review,
                               candidates=candidates,
                               series_members=series_members,
                               parent_session=parent_session,
                               is_child=is_child)
    else:
        # Conversation mode (phase 6+)
        messages = companion_db.get_messages(session_id, limit=200)
        from phase_cluster_router import persona_for_phase as _persona_for_phase
        from time_coach import assess_session as _tc_assess, week_context as _tc_week, prep_window_position as _tc_horizon
        committed_decisions = session_decisions.list_decisions(session_id) if session_decisions else []
        open_decision_keys_list = session_decisions.open_decision_keys(session_id) if session_decisions else []
        return render_template("study_session.html",
                               session=session, mode="conversation",
                               persona=_persona_for_phase(phase),
                               time_coach=_tc_assess(companion_db, study_analytics, session_id),
                               time_coach_week=_tc_week(companion_db, study_analytics),
                               horizon=_tc_horizon(),
                               card=None, phase_index=5,
                               total_phases=len(CARD_PHASES),
                               resource_text=None,
                               study_bible_notes=None,
                               prefilled="",
                               annotations=[],
                               notepad="",
                               messages=messages,
                               outline=companion_db.get_outline_tree(session_id),
                               linked_sermons=linked_sermons,
                               review=review,
                               candidates=candidates,
                               committed_decisions=committed_decisions,
                               open_decision_keys=open_decision_keys_list,
                               decision_labels=_DECISION_LABEL_MAP,
                               series_members=series_members,
                               parent_session=parent_session,
                               is_child=is_child)


@app.route("/study/session/<int:session_id>/discuss", methods=["POST"])
def study_discuss(session_id):
    """Stream a study response via SSE."""
    data, err = json_body()
    if err: return err
    message, err = require_string(data, "message", min_length=1)
    if err: return err

    # Record message analytics
    if study_analytics:
        study_analytics.record_message(session_id, 'user', len(message))

    return Response(
        stream_study_response(session_id, message, companion_db, study_analytics, accuser=accuser_detector),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _is_child_session(session_id):
    """True if this session has a parent (i.e., it's a child in a series)."""
    sess = companion_db.get_session(session_id)
    return bool(sess and sess.get("parent_session_id"))


@app.route("/study/session/<int:session_id>/card/next", methods=["POST"])
def study_card_next(session_id):
    """Advance to the next card phase, saving any response."""
    session = companion_db.get_session(session_id)
    if not session:
        return "Session not found", 404
    if session.get("parent_session_id"):
        return ("Card writes are not allowed on child sessions; "
                "their cards are inherited from the parent.", 403)
    phase = session.get("current_phase", "prayer")
    response_text = request.form.get("response", "").strip()
    if response_text:
        companion_db.save_card_response(session_id, phase, question_id=0, content=response_text)
    card_phase_keys = [p["key"] for p in CARD_PHASES]
    if phase in card_phase_keys:
        idx = card_phase_keys.index(phase)
        if idx < len(card_phase_keys) - 1:
            next_phase = card_phase_keys[idx + 1]
            companion_db.update_phase(session_id, next_phase)
        else:
            companion_db.update_phase(session_id, "context")
            _save_card_summary_for_ai(session_id)
    return redirect(url_for("study_session_view", session_id=session_id))


@app.route("/study/session/<int:session_id>/card/back", methods=["POST"])
def study_card_back(session_id):
    """Go back to the previous card phase."""
    session = companion_db.get_session(session_id)
    if not session:
        return "Session not found", 404
    phase = session.get("current_phase", "prayer")
    card_phase_keys = [p["key"] for p in CARD_PHASES]
    if phase in card_phase_keys:
        idx = card_phase_keys.index(phase)
        if idx > 0:
            companion_db.update_phase(session_id, card_phase_keys[idx - 1])
    return redirect(url_for("study_session_view", session_id=session_id))


@app.route("/study/session/<int:session_id>/study-bibles")
def study_bible_notes_route(session_id):
    """Return study bible notes for the session's passage as JSON."""
    session = companion_db.get_session(session_id)
    if not session:
        return jsonify({"error": "not found"}), 404
    notes = _get_study_bible_notes(session)
    return jsonify(notes)


@app.route("/study/session/<int:session_id>/card/autosave", methods=["POST"])
def study_card_autosave(session_id):
    """Auto-save card textarea content without advancing phase."""
    data, err = json_body()
    if err: return err
    content = data.get("content", "")
    session, err = require_session(companion_db, session_id)
    if err: return err
    if session.get("parent_session_id"):
        return jsonify({"error": "child_session_read_only"}), 403
    phase = session.get("current_phase", "prayer")
    # Use notepad table for auto-save (upsert by session+phase)
    companion_db.save_card_notepad(session_id, phase=f"card_{phase}", content=content)
    return jsonify({"ok": True})


_TRANSCRIBE_MAX_BYTES = 25 * 1024 * 1024


_AUDIO_RECORDINGS_DIR = os.environ.get(
    "AUDIO_RECORDINGS_DIR",
    str(Path("/Volumes/External/Logos4/Data/audio_recordings"))
)


def _save_audio_blob_to_disk(audio_bytes, session_id, content_type):
    """Write the raw audio to /Volumes/External/Logos4/Data/audio_recordings/
    session_<id>/<utc-iso>.<ext>. Returns the absolute file path."""
    ext_map = {
        'audio/webm': '.webm', 'audio/ogg': '.ogg',
        'audio/mp4': '.mp4', 'audio/m4a': '.m4a',
        'audio/wav': '.wav', 'audio/x-wav': '.wav',
        'audio/mpeg': '.mp3', 'audio/mp3': '.mp3',
    }
    ct = (content_type or 'audio/webm').lower().split(';')[0].strip()
    ext = ext_map.get(ct, '.webm')
    base = Path(_AUDIO_RECORDINGS_DIR) / f"session_{session_id}"
    base.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Add a short random suffix in case two recordings land in the same second
    import secrets
    suffix = secrets.token_hex(2)
    path = base / f"{stamp}_{suffix}{ext}"
    path.write_bytes(audio_bytes)
    return str(path)


@app.route("/study/session/<int:session_id>/transcribe", methods=["POST"])
def study_transcribe(session_id):
    """Run voice-to-text on an uploaded audio blob and return the transcript.

    Also persists the raw audio to disk so Bryan can re-transcribe later
    (better model, manual review, or listen back to a prayer he didn't want
    transcribed at all). Storage is on the External drive; never uploaded.
    """
    session, err = require_session(companion_db, session_id)
    if err: return err

    audio_file = request.files.get("audio")
    if audio_file is None:
        return jsonify({"error": "missing 'audio' field"}), 400

    audio_bytes = audio_file.read()
    if not audio_bytes:
        return jsonify({"error": "empty audio"}), 400
    if len(audio_bytes) > _TRANSCRIBE_MAX_BYTES:
        return jsonify({"error": "audio exceeds 25 MB limit"}), 413

    content_type = audio_file.mimetype or "audio/webm"

    try:
        import whisper_service
        result = whisper_service.transcribe_audio(audio_bytes, content_type=content_type)
    except Exception as e:
        app.logger.exception("transcription failed")
        return jsonify({"error": f"transcription failed: {e}"}), 500

    # Persist the audio file + DB row. Save AFTER transcription succeeded so
    # we don't keep blobs for failed runs (the user retries those). If the
    # save fails for any reason, log it but still return the transcript —
    # the dictation flow should not break because of disk-write issues.
    try:
        audio_path = _save_audio_blob_to_disk(audio_bytes, session_id, content_type)
        companion_db.save_audio_recording(
            session_id=session_id,
            phase=session.get('current_phase') or '',
            file_path=audio_path,
            content_type=content_type,
            byte_size=len(audio_bytes),
            duration_sec=float(result.get('duration_sec') or 0.0),
            transcript_excerpt=(result.get('text') or '')[:500],
            quality=result.get('quality') or 'unknown',
        )
    except Exception:
        app.logger.exception("audio archive write failed (transcript still returned)")

    return jsonify(result)


def _lookup_lexicon_gloss(lemma, is_nt=True):
    """Look up a short English gloss from BDAG (NT) or BDB/HALOT (OT).

    Tries the lemma as-is, then with middle→active form for Greek deponents.
    Caches the result in the MorphGNT glosses table for future lookups.
    Returns the gloss string or None.
    """
    from resource_index import ResourceIndex
    from morphgnt_cache import _extract_gloss_from_bdag, _normalize_greek

    idx_path = os.path.join(os.path.dirname(__file__), '..', 'resource_index.db')
    idx = ResourceIndex(idx_path)
    lemma_nfc = _normalize_greek(lemma)

    if is_nt:
        resources = [("BDAG.logos4", _extract_gloss_from_bdag)]
    else:
        resources = [
            ("BDB.logos4", _extract_gloss_from_bdag),
            ("HAL.logos4", _extract_gloss_from_bdag),
        ]

    for res_file, extractor in resources:
        # Try exact lemma
        results = idx.lookup(lemma_nfc, res_file, limit=1)

        # For Greek: try active form if middle/passive lemma didn't match
        if not results and lemma_nfc.endswith("ομαι"):
            results = idx.lookup(lemma_nfc[:-4] + "ω", res_file, limit=1)
        if not results and lemma_nfc.endswith("μαι"):
            results = idx.lookup(lemma_nfc[:-3] + "μι", res_file, limit=1)

        if not results or results[0].get("score", 0) < 80:
            continue

        text = read_article_text(res_file, results[0]["article_num"], max_chars=800)
        gloss = extractor(text)
        if gloss:
            # Cache for next time
            try:
                from morphgnt_cache import get_cache, _normalize_greek as _nfcg
                import sqlite3
                cache = get_cache()
                conn = cache._get_conn()
                if conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO glosses VALUES (?,?,?)",
                        (_nfcg(lemma), gloss, "bdag-live"),
                    )
                    conn.commit()
            except Exception:
                pass
            return gloss

    return None


@app.route("/study/session/<int:session_id>/word-info", methods=["POST"])
def study_word_info(session_id):
    """Get parsing info for a Greek/Hebrew word.

    Uses MorphGNT cache for NT words (authoritative parsing).
    Looks up BDAG/BDB/HALOT from the library for glosses.
    Falls back to Claude Haiku only as last resort.
    """
    data, err = json_body()
    if err: return err
    word, err = require_string(data, "word", min_length=1)
    if err: return err

    session, err = require_session(companion_db, session_id)
    if err: return err

    book = session.get("book", 66)
    is_nt = book >= 40

    morphgnt_result = None

    # Try MorphGNT cache first for NT words (authoritative parsing)
    if is_nt:
        from morphgnt_cache import get_cache
        cache = get_cache()
        morphgnt_result = cache.lookup_word(
            word,
            book=book,
            chapter=session.get("chapter"),
            verse=session.get("verse_start"),
        )
        if not morphgnt_result:
            morphgnt_result = cache.lookup_word(word, book=book)
        if not morphgnt_result:
            morphgnt_result = cache.lookup_word(word)

    if morphgnt_result:
        gloss = morphgnt_result.get("gloss", "")
        lemma = morphgnt_result["lemma"]

        # If no cached gloss, look it up live from BDAG
        if not gloss:
            gloss = _lookup_lexicon_gloss(lemma, is_nt=True) or ""

        return jsonify({
            "lemma": lemma,
            "gloss": gloss,
            "parsing": morphgnt_result["parsing_human"],
            "root": lemma,
            "source": "morphgnt",
        })

    # Fall back to Haiku for OT words or MorphGNT misses
    lang = "Greek" if is_nt else "Hebrew"
    try:
        import anthropic
        api_key = anthropic_api_key()
        if not api_key:
            return jsonify({"error": "API key not set"}), 500
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": f"Parse this {lang} word from the Bible: {word}\n\nReturn ONLY a JSON object with these fields:\n- \"lemma\": the dictionary form\n- \"gloss\": brief English meaning (2-4 words)\n- \"parsing\": full morphological parsing (e.g. \"Verb, Aorist Active Indicative, 3rd Person Singular\")\n- \"root\": the root/stem if different from lemma, otherwise same as lemma\n\nJSON only, no explanation."}],
        )
        import re
        text = resp.content[0].text.strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            info = json.loads(match.group())
            info["source"] = "haiku"
            return jsonify(info)
        return jsonify({"error": "parse failed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/study/session/<int:session_id>/annotate", methods=["POST"])
def study_annotate(session_id):
    """Save a star annotation from the study bible card."""
    # Bug review #1 + #4: silent JSON + session existence check
    _, err = require_session(companion_db, session_id)
    if err: return err
    data, err = json_body()
    if err: return err
    aid = companion_db.save_card_annotation(
        session_id, phase="study_bibles",
        source=data.get("source", ""),
        starred_text=data.get("starred_text", ""),
        note=data.get("note", ""))
    return jsonify({"id": aid})


@app.route("/study/session/<int:session_id>/notepad", methods=["POST"])
def study_notepad(session_id):
    """Save notepad content for the current phase."""
    # Bug review #1 + #4: silent JSON + session existence check
    _, err = require_session(companion_db, session_id)
    if err: return err
    data, err = json_body()
    if err: return err
    companion_db.save_card_notepad(session_id, phase=data.get("phase", "study_bibles"),
                                   content=data.get("content", ""))
    return jsonify({"ok": True})


@app.route("/study/session/<int:session_id>/outline")
def study_outline(session_id):
    """Return outline tree as JSON."""
    tree = companion_db.get_outline_tree(session_id)
    return jsonify(tree)


@app.route("/study/session/<int:session_id>/outline/add", methods=["POST"])
def study_outline_add(session_id):
    """Add a node to the outline."""
    # Bug review #4: 404 for missing session (avoids FK 500 in the helper).
    _, err = require_session(companion_db, session_id)
    if err: return err
    data, err = json_body()
    if err: return err
    # Bug review #6: content must be a string. {"content": 12}.strip() crashed.
    content, err = require_string(data, "content", allow_missing=True, default="")
    if err: return err
    node_type, err = require_string(data, "node_type", allow_missing=True, default="note")
    if err: return err

    if content:
        node_id = companion_db.add_outline_node(
            session_id=session_id,
            node_type=node_type,
            content=content,
        )
        if study_analytics:
            study_analytics.record_outline_event(session_id, 'add', node_type)
        return jsonify({"ok": True, "id": node_id})

    return jsonify({"ok": False}), 400


@app.route("/study/session/<int:session_id>/outline/<int:node_id>", methods=["PATCH"])
def study_outline_update(session_id, node_id):
    """Update an outline node."""
    data, err = json_body()
    if err: return err
    rowcount = companion_db.update_outline_node(
        node_id=node_id,
        content=data.get("content"),
        node_type=data.get("node_type"),
        rank=data.get("rank"),
    )
    # Bug review #7: was reporting success on no-op (missing node_id).
    if not rowcount:
        return jsonify({"error": "outline node not found"}), 404
    return jsonify({"ok": True})


@app.route("/study/session/<int:session_id>/outline/<int:node_id>", methods=["DELETE"])
def study_outline_delete(session_id, node_id):
    """Delete an outline node."""
    rowcount = companion_db.delete_outline_node(node_id)
    # Bug review #7: was reporting success on no-op (missing node_id).
    if not rowcount:
        return jsonify({"error": "outline node not found"}), 404
    return jsonify({"ok": True})


@app.route("/study/session/<int:session_id>/clock", methods=["PATCH"])
def study_clock_update(session_id):
    """Update elapsed time from client (absolute, not delta).

    Hardened per bug review #3-#6:
      - 404 when the session doesn't exist (was silently 200)
      - elapsed=0 is a valid reset (was treated as missing because falsy)
      - rejects non-int, negative, and absurdly-large values (was the
        source of the 898-year corruption — the tab-suspend bug used to
        let clients post any garbage number)
    """
    _, err = require_session(companion_db, session_id)
    if err: return err

    data, err = json_body()
    if err: return err
    # 14 days is the absolute ceiling — no real sermon-prep session is
    # longer than that, and clamping here prevents future tab-suspend
    # bugs from poisoning the DB the way the pre-cc9b914 timer did.
    MAX_REASONABLE_SECONDS = 14 * 24 * 3600
    elapsed, err = require_int(data, "elapsed", lo=0, hi=MAX_REASONABLE_SECONDS)
    if err: return err

    conn = companion_db._conn()
    conn.execute(
        "UPDATE sessions SET total_elapsed_seconds = ?, updated_at = ? WHERE id = ?",
        (elapsed, companion_db._now(), session_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "elapsed": elapsed})


@app.route("/study/session/<int:session_id>/split", methods=["POST"])
def study_session_split(session_id):
    """Split a session into a sermon series.

    Body: {"plan": [{"position": 1, "title": "...", "verse_start": int, "verse_end": int}, ...]}

    Position 1 narrows the parent. Positions 2..N create children.
    All sermons currently linked to the parent are re-matched after the split.
    """
    payload, err = json_body()
    if err: return err
    plan = payload.get("plan") or []
    if not plan:
        return jsonify({"error": "empty_plan"}), 400

    parent, err = require_session(companion_db, session_id)
    if err:
        # Override 'session not found' shape to match historical 'session_not_found'
        # that JS clients on the split modal already handle.
        return jsonify({"error": "session_not_found"}), 404
    if companion_db.get_children(session_id):
        return jsonify({"error": "already_split"}), 400
    if parent.get("parent_session_id"):
        return jsonify({"error": "cannot_split_a_child"}), 400

    # Normalize + validate plan
    try:
        rows = []
        for r in plan:
            rows.append({
                "position": int(r["position"]),
                "title": str(r["title"]).strip(),
                "verse_start": int(r["verse_start"]),
                "verse_end": int(r["verse_end"]),
            })
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "malformed_plan_row"}), 400
    rows.sort(key=lambda r: r["position"])
    positions = [r["position"] for r in rows]
    if positions[0] != 1:
        return jsonify({"error": "missing_position_1"}), 400
    if positions != list(range(1, len(rows) + 1)):
        return jsonify({"error": "non_contiguous_positions"}), 400
    for r in rows:
        if not r["title"] or r["verse_start"] < 1 or r["verse_end"] < r["verse_start"]:
            return jsonify({"error": "invalid_row"}), 400

    # Derive the book/chapter prefix from the parent's existing passage_ref
    # (everything before the colon), so we don't need a book-id-to-name map.
    book_chap_prefix = parent["passage_ref"].split(":")[0].rsplit(" ", 1)[0] + f" {parent['chapter']}"

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()

    conn = companion_db._conn()
    conn.execute("BEGIN IMMEDIATE")
    try:
        first = rows[0]
        conn.execute(
            "UPDATE sessions SET verse_start=?, verse_end=?, "
            "series_position=1, series_title=?, passage_ref=?, updated_at=? WHERE id=?",
            (first["verse_start"], first["verse_end"], first["title"],
             f"{book_chap_prefix}:{first['verse_start']}-{first['verse_end']}",
             now_iso, session_id),
        )
        child_ids = []
        for r in rows[1:]:
            cur = conn.execute(
                "INSERT INTO sessions (passage_ref, book, chapter, verse_start, "
                "verse_end, genre, parent_session_id, series_position, series_title, "
                "current_phase, status, created_at, updated_at) "
                # Children start at 'context' (first conversation phase) — they
                # inherit the parent's card-phase work and the UI renders parent
                # cards read-only, so card phases would be meaningless on them.
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'context', 'active', ?, ?)",
                (f"{book_chap_prefix}:{r['verse_start']}-{r['verse_end']}",
                 parent["book"], parent["chapter"], r["verse_start"], r["verse_end"],
                 parent["genre"], session_id, r["position"], r["title"],
                 now_iso, now_iso),
            )
            child_ids.append(cur.lastrowid)

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        conn.close()
        raise
    finally:
        conn.close()

    # Remap parent's sermon_links to the best-matching member (parent or
    # one of the new children). Opens its own BEGIN IMMEDIATE transaction.
    from sermon_matcher import remap_parent_links_after_split
    remap_parent_links_after_split(companion_db, session_id)

    children = companion_db.get_children(session_id)
    return jsonify({
        "parent_id": session_id,
        "children": [{"id": c["id"], "position": c["series_position"],
                       "title": c["series_title"]} for c in children],
    })


@app.route("/study/session/<int:session_id>/split/propose", methods=["POST"])
def study_session_split_propose(session_id):
    """Read the session's conversation and return an AI-extracted series plan."""
    session = companion_db.get_session(session_id)
    if not session:
        return jsonify({"error": "session_not_found"}), 404
    messages = companion_db.get_messages(session_id, limit=500)

    from llm_client import AnthropicClient
    llm = AnthropicClient(api_key=anthropic_api_key())
    rows = propose_series_plan(messages, session_chapter=session["chapter"], llm=llm)
    return jsonify({"plan": [
        {"position": r.position, "title": r.title,
         "verse_start": r.verse_start, "verse_end": r.verse_end}
        for r in rows
    ]})


from bp_sermons import sermons_bp
app.register_blueprint(sermons_bp)
from bp_passage import bp_passage
app.register_blueprint(bp_passage)
from bp_workbench import bp_workbench
app.register_blueprint(bp_workbench)


# ── Main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    startup()
    if _APSCHEDULER_AVAILABLE:
        get_scheduler()
    port = int(os.environ.get("FLASK_PORT", "5111"))
    print(f"Sermon Research Workbench starting on http://localhost:{port}", file=sys.stderr)
    print(f"  Study at http://localhost:{port}/study/", file=sys.stderr)
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
