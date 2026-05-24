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
