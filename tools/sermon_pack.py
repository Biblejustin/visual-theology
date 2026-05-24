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


# Decision-key to field-name mapping used by all three artifacts.
_DECISION_TO_FIELD = {
    'subject': 'subject',
    'theme': 'theme',
    'ep_otm': 'exegetical_point',
    'transition': 'transition',
    'exegetical_outline': 'exegetical_outline',
    'fcf': 'fcf',
    'hp_tht': 'take_home_truth',
    'purpose_statement': 'purpose_statement',
    'target_length_min': 'target_length_min',
}

_ARTIFACT_FIELDS = {
    'manuscript': (
        'subject', 'take_home_truth', 'target_length_min', 'passage_ref',
        'preach_date', 'intro', 'points', 'conclusion',
    ),
    'outline': (
        'subject', 'take_home_truth', 'target_length_min', 'passage_ref',
        'preach_date', 'intro', 'points', 'conclusion',
    ),
    'handout': (
        'subject', 'take_home_truth', 'passage_ref', 'preach_date',
        'points', 'conclusion',
    ),
}


def _field_from_decisions(decisions: dict, field_name: str) -> FieldResult | None:
    for dec_key, mapped in _DECISION_TO_FIELD.items():
        if mapped == field_name and dec_key in decisions:
            return FieldResult(value=decisions[dec_key], source='decisions')
    return None


def _field_from_session(session_row: dict, field_name: str) -> FieldResult | None:
    if field_name == 'passage_ref' and session_row.get('passage_ref'):
        return FieldResult(value=session_row['passage_ref'], source='decisions')
    if field_name == 'preach_date' and session_row.get('preach_date'):
        return FieldResult(value=session_row['preach_date'], source='decisions')
    return None


def resolve_artifact_fields(db_path: str, session_id: int, *,
                            artifact: str) -> dict[str, FieldResult]:
    """Resolve every field for the named artifact, returning {name: FieldResult}."""
    if artifact not in _ARTIFACT_FIELDS:
        raise ValueError(f'unknown artifact: {artifact!r}')

    session_row = load_session_row(db_path, session_id) or {}
    decisions = load_decisions(db_path, session_id)
    outline_points = load_outline_points(db_path, session_id)
    mined = mine_outline_message(db_path, session_id)
    parsed = parse_mined_outline(mined) if mined else {'intro': '', 'points': [], 'conclusion': ''}

    ctx: dict[str, FieldResult] = {}
    for name in _ARTIFACT_FIELDS[artifact]:
        # Session-column fields (passage_ref, preach_date) are tagged 'decisions'
        # in the source taxonomy because they are committed structured fields,
        # not mined text.
        sess = _field_from_session(session_row, name)
        if sess:
            ctx[name] = sess
            continue
        dec = _field_from_decisions(decisions, name)
        if dec:
            ctx[name] = dec
            continue
        if name == 'points':
            if outline_points:
                ctx[name] = FieldResult(value=_format_points(outline_points), source='outline')
            elif parsed['points']:
                ctx[name] = FieldResult(value=_format_mined_points(parsed['points']),
                                         source='mined')
            else:
                ctx[name] = missing_field(name)
            continue
        if name == 'intro':
            if parsed['intro']:
                ctx[name] = FieldResult(value=parsed['intro'], source='mined')
            else:
                ctx[name] = missing_field(name)
            continue
        if name == 'conclusion':
            if parsed['conclusion']:
                ctx[name] = FieldResult(value=parsed['conclusion'], source='mined')
            else:
                ctx[name] = missing_field(name)
            continue
        ctx[name] = missing_field(name)
    return ctx


def _format_points(points: list[dict]) -> str:
    """Format outline_nodes points as a manuscript/outline body string."""
    parts = []
    for i, p in enumerate(points, start=1):
        title = p['title']
        ref = f' ({p["verse_ref"]})' if p.get('verse_ref') else ''
        parts.append(f'## POINT {i}: {title}{ref}')
        for s in p.get('subs', []):
            sub_ref = f' ({s["verse_ref"]})' if s.get('verse_ref') else ''
            parts.append(f'* {s["content"]}{sub_ref}')
        for x in p.get('cross_refs', []):
            ref = x.get('verse_ref') or x.get('content', '')
            parts.append(f'* **Scripture:** {ref}')
        for n in p.get('notes', []):
            parts.append(f'* {n["content"]}')
        for il in p.get('illustrations', []):
            parts.append(f'> {il["content"]}')
        parts.append('')
    return '\n'.join(parts).rstrip()


def _format_mined_points(points: list[dict]) -> str:
    parts = []
    for p in points:
        parts.append(f'## {p["title"]}')
        parts.append('')
        parts.append(p['body'])
        parts.append('')
    return '\n'.join(parts).rstrip()


def compute_source_breakdown(ctx: dict[str, FieldResult]) -> dict[str, int]:
    br = {s: 0 for s in SOURCES}
    for fr in ctx.values():
        br[fr.source] = br.get(fr.source, 0) + 1
    return br


def compute_mining_percentage(breakdown: dict[str, int]) -> float:
    """Mined divided by non-missing total. Returns 0.0 if no fields resolved at all."""
    denom = sum(v for k, v in breakdown.items() if k != 'missing')
    if denom == 0:
        return 0.0
    return round(breakdown.get('mined', 0) / denom, 4)


_TEMPLATE_DIR = Path(__file__).parent / 'sermon_pack_templates'


def _load_template(artifact: str) -> str:
    path = _TEMPLATE_DIR / f'{artifact}.md.tmpl'
    return path.read_text(encoding='utf-8')


def render(artifact: str, ctx: dict[str, FieldResult]) -> str:
    """Render a markdown artifact from resolved fields."""
    tmpl = _load_template(artifact)
    values = {k: v.value for k, v in ctx.items()}
    return tmpl.format(**values)


class SermonPackError(RuntimeError):
    """Raised when generation cannot proceed (e.g. session id is unknown)."""


def _safe_filename(passage: str, artifact: str) -> str:
    """Mirror the existing _safe_export_filename convention from app.py."""
    slug = re.sub(r'[^A-Za-z0-9]+', '-', passage).strip('-').lower() or 'sermon'
    suffix = {
        'manuscript': 'manuscript',
        'outline': 'preaching-outline',
        'handout': 'congregation-handout',
    }[artifact]
    return f'{slug}-{suffix}.md'


def generate(session_id: int, *, db_path: str, out_dir: str | Path,
             artifacts: tuple[str, ...] = ('manuscript', 'outline', 'handout'),
             companion_db=None) -> dict:
    """Generate the requested artifacts and write each as a .md file.

    Returns {artifact: {path, content, mining_percentage, source_breakdown}}.
    Logs one sermon_pack_metrics row per artifact when `companion_db` is
    supplied (the Flask routes pass app.companion_db; the CLI builds one
    from db_path)."""
    session_row = load_session_row(db_path, session_id)
    if session_row is None:
        raise SermonPackError(f'session {session_id} not found in {db_path}')
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    passage = session_row.get('passage_ref') or f'session-{session_id}'

    # Build companion_db handle for metrics logging if caller did not pass one.
    if companion_db is None:
        try:
            sys.path.insert(0, str(Path(__file__).parent / 'workbench'))
            from companion_db import CompanionDB  # noqa: E402
            companion_db = CompanionDB(db_path)
        except ImportError:
            companion_db = None  # tests with no workbench package still work

    result: dict[str, dict] = {}
    for art in artifacts:
        ctx = resolve_artifact_fields(db_path, session_id, artifact=art)
        content = render(art, ctx)
        breakdown = compute_source_breakdown(ctx)
        mining_pct = compute_mining_percentage(breakdown)
        path = out_dir / _safe_filename(passage, art)
        path.write_text(content, encoding='utf-8')
        if companion_db is not None:
            companion_db.save_sermon_pack_metric(
                session_id=session_id, artifact=art,
                source_breakdown=breakdown,
                mining_percentage=mining_pct,
            )
        result[art] = {
            'path': str(path),
            'content': content,
            'mining_percentage': mining_pct,
            'source_breakdown': breakdown,
        }
    return result


def _print_report(db_path: str) -> int:
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT session_id, artifact, mining_percentage, generated_at "
            "FROM sermon_pack_metrics ORDER BY generated_at DESC LIMIT 50"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        print('no sermon_pack_metrics rows yet')
        return 0
    print(f'{"session":>7}  {"artifact":<11}  {"mining%":>7}  generated_at')
    for r in rows:
        print(f'{r["session_id"]:>7}  {r["artifact"]:<11}  '
              f'{r["mining_percentage"]:>6.3f}  {r["generated_at"]}')
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description='Generate sermon-pack deliverables from a finished study session.',
    )
    p.add_argument('session_id', nargs='?', type=int,
                   help='session id (omit when using --report)')
    p.add_argument('--db', default=None,
                   help='path to companion.db (defaults to tools/workbench/companion.db)')
    p.add_argument('--out', default='.',
                   help='output directory for generated .md files')
    p.add_argument('--artifact', action='append',
                   choices=('manuscript', 'outline', 'handout'),
                   help='emit only the named artifact (repeatable). Default: all three.')
    p.add_argument('--report', action='store_true',
                   help='print recent sermon_pack_metrics rows and exit')
    args = p.parse_args(argv)

    db_path = args.db or str(Path(__file__).parent / 'workbench' / 'companion.db')

    if args.report:
        return _print_report(db_path)

    if args.session_id is None:
        p.error('session_id is required unless --report is given')

    artifacts = tuple(args.artifact) if args.artifact else ('manuscript', 'outline', 'handout')
    result = generate(args.session_id, db_path=db_path, out_dir=args.out,
                      artifacts=artifacts)
    for art, info in result.items():
        print(f'{art}: {info["path"]}  mining={info["mining_percentage"]:.3f}  '
              f'breakdown={info["source_breakdown"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
