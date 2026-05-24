"""Sermon-pack deliverables generator.

Pure data transformation: SQLite in, three markdown files out. No Flask,
no LLM. See docs/superpowers/specs/2026-05-23-sermon-pack-deliverables-design.md.

Source precedence per field:
  1. session_decisions  (the 9 keyed homiletical commitments)
  2. outline_nodes      (the structured outline tree)
  3. card_notepads      (Bryan's per-phase notepad work)
  4. card_responses     (Bryan's per-phase card answers)
  5. conversation_messages (mined when nothing above has the field)

Each resolved field carries a `source` tag; per-artifact mining-percentage
is recorded to sermon_pack_metrics. Driving mining-% to zero is the
acceptance metric for the follow-on upstream-wiring spec.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SOURCES = ('decisions', 'outline', 'notepad', 'card', 'mined', 'missing')


@dataclass
class FieldResult:
    """One resolved artifact field.

    `value` is always a string ready for template substitution. When the
    field couldn't be resolved from any source, value is the literal
    `[missing: <field>]` marker and source is "missing"."""
    value: str
    source: str


def missing_field(name: str) -> FieldResult:
    return FieldResult(value=f'[missing: {name}]', source='missing')


def _open_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def load_session_row(db_path: str, session_id: int) -> dict | None:
    """Return the sessions row as a dict, or None."""
    conn = _open_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def load_decisions(db_path: str, session_id: int) -> dict[str, str]:
    """Return {key: value} for every committed session_decision for this session."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT key, value FROM session_decisions WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        return {r['key']: r['value'] for r in rows}
    finally:
        conn.close()


def load_outline_points(db_path: str, session_id: int) -> list[dict]:
    """Return list of main-point dicts:
        {title, verse_ref, subs: [{content, verse_ref}], cross_refs: [...], notes: [...], illustrations: [...]}
    Title-only nodes and orphan sub-points are skipped."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id, parent_id, type, content, verse_ref, rank "
            "FROM outline_nodes WHERE session_id = ? ORDER BY rank",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()

    nodes = [dict(r) for r in rows]
    children: dict[int | None, list[dict]] = {}
    for n in nodes:
        children.setdefault(n['parent_id'], []).append(n)

    points = []
    for n in children.get(None, []):
        if n['type'] != 'main_point':
            continue
        kids = children.get(n['id'], [])
        points.append({
            'title': n['content'],
            'verse_ref': n['verse_ref'] or '',
            'subs': [{'content': k['content'], 'verse_ref': k['verse_ref'] or ''}
                     for k in kids if k['type'] == 'sub_point'],
            'cross_refs': [{'content': k['content'], 'verse_ref': k['verse_ref'] or ''}
                           for k in kids if k['type'] == 'cross_ref'],
            'notes': [{'content': k['content']}
                      for k in kids if k['type'] == 'note'],
            'illustrations': [{'content': k['content']}
                              for k in kids if k['type'] == 'illustration'],
        })
    return points


def load_notepads(db_path: str, session_id: int) -> dict[str, str]:
    """Return {phase: content} for non-empty card_notepads."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT phase, content FROM card_notepads "
            "WHERE session_id = ? AND content != ''",
            (session_id,),
        ).fetchall()
        return {r['phase']: r['content'] for r in rows}
    finally:
        conn.close()


def load_card_responses(db_path: str, session_id: int) -> dict[str, list[str]]:
    """Return {phase: [content, ...]} ordered by created_at."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT phase, content FROM card_responses "
            "WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r['phase'], []).append(r['content'])
    return out


_MIN_HEADINGS_FOR_OUTLINE = 3


def mine_outline_message(db_path: str, session_id: int) -> str | None:
    """Return the assistant message most likely to contain a full sermon outline.

    Heuristic: an assistant message in any phase that contains at least
    `_MIN_HEADINGS_FOR_OUTLINE` markdown headings (lines starting with `## `
    or higher). Among qualifying messages, pick the longest. Ties broken by
    higher message id (most recent), so the result is fully deterministic
    given the database state."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id, content FROM conversation_messages "
            "WHERE session_id = ? AND role = 'assistant' AND content IS NOT NULL "
            "ORDER BY id",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    best = None
    best_key = (-1, -1)  # (length, id)
    for r in rows:
        c = r['content']
        if len(re.findall(r'(?m)^#{2,6}\s', c)) < _MIN_HEADINGS_FOR_OUTLINE:
            continue
        key = (len(c), r['id'])
        if key > best_key:
            best_key = key
            best = c
    return best


_H2_RE = re.compile(r'(?m)^##\s+(.+?)\s*$')


def parse_mined_outline(md: str) -> dict:
    """Split a mined outline message into {intro, points: [{title, body}], conclusion}.

    Sections under `## INTRODUCTION` (or `## INTRO`) -> intro.
    Sections under `## POINT N: ...` (or any `## ` not labelled CONCLUSION/INTRO)
    -> points list (the title is the H2 text, the body is everything until
    the next H2).
    Sections under `## CONCLUSION` -> conclusion.
    All other H2s are treated as points."""
    intro_parts: list[str] = []
    points: list[dict] = []
    conclusion_parts: list[str] = []

    matches = list(_H2_RE.finditer(md))
    if not matches:
        return {'intro': '', 'points': [], 'conclusion': ''}

    for i, m in enumerate(matches):
        title = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[body_start:body_end].strip()
        upper = title.upper()
        if upper in ('INTRODUCTION', 'INTRO'):
            intro_parts.append(body)
        elif upper == 'CONCLUSION':
            conclusion_parts.append(body)
        else:
            points.append({'title': title, 'body': body})

    return {
        'intro': '\n\n'.join(p for p in intro_parts if p),
        'points': points,
        'conclusion': '\n\n'.join(p for p in conclusion_parts if p),
    }
