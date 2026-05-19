"""Passage + library Flask blueprint.

First extraction in the app.py blueprint split (polish #5b). These
routes are pure read endpoints over Logos library data — no session
state, no shared writes — so they make the cleanest first extraction.

Routes:
- GET /api/passage           — multi-translation passage display
- GET /api/interlinear       — chapter interlinear
- GET /api/resource/<filename>/toc                 — resource TOC
- GET /api/resource/<filename>/article/<int:n>     — single article body
- GET /api/library/search    — catalog search by query/type

The blueprint factory takes no state — all dependencies are
module-level (study.* helpers, CATALOG_DB constant). Routes that
require a CompanionDB session live in other blueprints.
"""

import os
import sqlite3

from flask import Blueprint, jsonify, render_template, request

from study import (
    parse_reference,
    resolve_bible_files,
    read_bible_chapter,
    extract_verses,
    clean_bible_text,
    get_interlinear_for_chapter,
    read_article_text,
    get_toc_cached,
    CATALOG_DB,
    RESOURCE_MGR_DB,
)
from validators import parse_int_arg


bp_passage = Blueprint("passage", __name__)


def _escape(text):
    """HTML-escape text for safe rendering."""
    from markupsafe import escape
    return escape(text)


@bp_passage.route("/api/passage")
def api_passage():
    """Get passage text in multiple translations."""
    ref_str = request.args.get("ref", "")
    versions = request.args.get("versions", "ESV,NASB95,KJV").split(",")

    if not ref_str:
        return jsonify({"error": "ref required"}), 400

    # Bug review #3: parse_reference raises ValueError on bad input.
    try:
        ref = parse_reference(ref_str)
    except ValueError as e:
        return jsonify({"error": f"invalid reference: {e}"}), 400
    bible_files = resolve_bible_files(versions)

    results = []
    for bf in bible_files:
        chapter_text, metadata = read_bible_chapter(bf, ref["book"], ref["chapter"])
        if chapter_text:
            if ref["verse_start"]:
                text = extract_verses(chapter_text, ref["verse_start"], ref["verse_end"])
            else:
                text = chapter_text
            text = clean_bible_text(text)
            results.append({
                "version": bf.replace(".logos4", "").replace(".lbxlls", ""),
                "text": text,
            })

    return render_template("partials/passage_display.html",
                           reference=ref_str, translations=results)


@bp_passage.route("/api/interlinear")
def api_interlinear():
    """Get interlinear data for a passage."""
    ref_str = request.args.get("ref", "")
    if not ref_str:
        return jsonify({"error": "ref required"}), 400

    try:
        ref = parse_reference(ref_str)
    except ValueError as e:
        return jsonify({"error": f"invalid reference: {e}"}), 400
    bible_files = resolve_bible_files(["ESV"])
    if not bible_files:
        return "<p>No Bible available for interlinear</p>"

    data = get_interlinear_for_chapter(bible_files[0], ref["book"], ref["chapter"])
    return render_template("partials/interlinear_display.html",
                           reference=ref_str, words=data)


@bp_passage.route("/api/resource/<filename>/toc")
def api_resource_toc(filename):
    """Get a resource's table of contents."""
    toc_entries = get_toc_cached(filename)
    return render_template("partials/resource_detail.html",
                           filename=filename, toc=toc_entries,
                           title=request.args.get("title", filename),
                           abbrev=request.args.get("abbrev", ""),
                           rtype=request.args.get("type", ""))


@bp_passage.route("/api/resource/<filename>/article/<int:article_num>")
def api_resource_article(filename, article_num):
    """Read a specific article from a resource."""
    text = read_article_text(filename, article_num, max_chars=30000)
    if not text:
        text = "(Could not read this article)"
    return f'<div class="article-text">{_escape(text)}</div>'


@bp_passage.route("/api/library/search")
def api_library_search():
    query = request.args.get("q", "").strip()
    rtype = request.args.get("type", "all")
    # Bug review #2: int() crashed on ?limit=x. Validators handle this.
    limit = parse_int_arg("limit", default=50, lo=1, hi=200)

    if not query and rtype == "all":
        return render_template("partials/library_results.html", results=[], query="")

    type_filter = ""
    if rtype != "all":
        type_map = {
            "bible": "text.monograph.bible",
            "commentary": "text.monograph.commentary%",
            "lexicon": "%lexicon%",
            "theology": "%systematic-theology%",
            "confession": "%confession%",
            "ancient": "%ancient%",
            "sermons": "%sermons%",
        }
        if rtype in type_map:
            type_filter = f"AND c.Type LIKE '{type_map[rtype]}'"

    conn = sqlite3.connect(CATALOG_DB)
    conn.row_factory = sqlite3.Row
    conn.execute(f"ATTACH '{RESOURCE_MGR_DB}' AS rm")

    if query:
        rows = conn.execute(f"""
            SELECT c.ResourceId, c.AbbreviatedTitle, c.Title, c.Type,
                   rm.Resources.Location
            FROM Records c
            INNER JOIN rm.Resources ON c.ResourceId = rm.Resources.ResourceId
            WHERE c.Availability = 2
            AND (c.Title LIKE ? OR c.AbbreviatedTitle LIKE ?)
            {type_filter}
            ORDER BY c.AbbreviatedTitle
            LIMIT ?
        """, (f"%{query}%", f"%{query}%", limit)).fetchall()
    else:
        rows = conn.execute(f"""
            SELECT c.ResourceId, c.AbbreviatedTitle, c.Title, c.Type,
                   rm.Resources.Location
            FROM Records c
            INNER JOIN rm.Resources ON c.ResourceId = rm.Resources.ResourceId
            WHERE c.Availability = 2
            {type_filter}
            ORDER BY c.AbbreviatedTitle
            LIMIT ?
        """, (limit,)).fetchall()
    conn.close()

    results = []
    for r in rows:
        loc = r["Location"] or ""
        results.append({
            "resource_id": r["ResourceId"],
            "abbrev": r["AbbreviatedTitle"] or "",
            "title": r["Title"],
            "type": r["Type"],
            "filename": os.path.basename(loc) if loc else "",
        })

    return render_template("partials/library_results.html",
                           results=results, query=query)
