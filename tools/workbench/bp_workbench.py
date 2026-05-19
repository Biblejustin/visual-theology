"""Workbench Flask blueprint.

Third blueprint extraction (polish #5b, 3 of 5). Holds the legacy
project-workbench UI that predates /study/ — kept active because the
chat-with-Claude project flow still works and is occasionally useful
for ad-hoc passage research outside the 16-phase workflow.

Routes:
- GET  /                     — project list
- GET  /project/<project_id>
- GET  /library              — Logos library browser page
- POST /api/projects                          — create
- POST /api/projects/<id>/phase               — update phase
- DELETE /api/projects/<id>
- GET  /api/projects/<id>/notes
- POST /api/projects/<id>/notes
- GET  /api/projects/<id>/research
- POST /api/chat                              — SSE stream
"""

from flask import (
    Blueprint, Response, jsonify, redirect, render_template, request, url_for,
)

import workbench_db as db
from workbench_agent import stream_agent_response, PHASES
from study import parse_reference
from validators import json_body


bp_workbench = Blueprint("workbench", __name__)


@bp_workbench.route("/")
def index():
    projects = db.list_projects()
    return render_template("projects_list.html", projects=projects)


@bp_workbench.route("/project/<project_id>")
def project_view(project_id):
    project = db.get_project(project_id)
    if not project:
        return redirect(url_for("workbench.index"))
    conversation = db.get_conversation(project_id)
    research_items = db.get_research_items(project_id)
    notes = db.get_notes(project_id)
    return render_template(
        "project.html",
        project=project,
        conversation=conversation,
        research_items=research_items,
        notes=notes,
        phases=PHASES,
    )


@bp_workbench.route("/library")
def library_view():
    return render_template("library.html")


# ── API: Projects ────────────────────────────────────────────────────────

@bp_workbench.route("/api/projects", methods=["POST"])
def api_create_project():
    passage = request.form.get("passage", "").strip()
    theme = request.form.get("theme", "").strip() or None
    if not passage:
        return redirect(url_for("workbench.index"))

    try:
        parsed = parse_reference(passage)
    except Exception:
        parsed = None

    project = db.create_project(passage, theme, parsed)
    return redirect(url_for("workbench.project_view", project_id=project["id"]))


@bp_workbench.route("/api/projects/<project_id>/phase", methods=["POST"])
def api_update_phase(project_id):
    # Accept either form or JSON. silent=True so non-JSON bodies don't 415
    # before our validation runs (bug review #1).
    data, err = json_body()
    if err: return err
    phase = request.form.get("phase") or data.get("phase")
    if not phase:
        return jsonify({"error": "phase required"}), 400
    db.update_project_phase(project_id, phase)
    return jsonify({"ok": True})


@bp_workbench.route("/api/projects/<project_id>", methods=["DELETE"])
def api_delete_project(project_id):
    db.delete_project(project_id)
    return jsonify({"ok": True})


# ── API: Chat ────────────────────────────────────────────────────────────

@bp_workbench.route("/api/chat", methods=["POST"])
def api_chat():
    # silent=True so a non-JSON body returns our 400 instead of Flask's 415
    # (bug review #1). Coerce to {} so .get calls are safe.
    data, err = json_body()
    if err: return err
    project_id = data.get("project_id")
    message = (data.get("message") or "").strip()
    model = data.get("model", "claude-haiku-4-5-20251001")

    if not project_id or not message:
        return jsonify({"error": "project_id and message required"}), 400

    return Response(
        stream_agent_response(project_id, message, model=model),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── API: Notes ───────────────────────────────────────────────────────────

@bp_workbench.route("/api/projects/<project_id>/notes", methods=["GET"])
def api_get_notes(project_id):
    section = request.args.get("section")
    notes = db.get_notes(project_id, section)
    return render_template("partials/notes_editor.html",
                           project_id=project_id, notes=notes)


@bp_workbench.route("/api/projects/<project_id>/notes", methods=["POST"])
def api_save_notes(project_id):
    data = request.get_json(silent=True) or request.form
    section = data.get("section", "general")
    content = data.get("content", "")
    db.save_note(project_id, content, section)
    return jsonify({"ok": True})


# ── API: Research Items ──────────────────────────────────────────────────

@bp_workbench.route("/api/projects/<project_id>/research")
def api_get_research(project_id):
    item_type = request.args.get("type")
    items = db.get_research_items(project_id, item_type)
    return render_template("partials/research_panel.html",
                           project_id=project_id, items=items)
