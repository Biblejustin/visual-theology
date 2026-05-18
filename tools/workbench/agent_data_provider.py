"""Cross-agent data layer — populates persona prompt context fields from
companion.db.

The persona prompts in persona_prompts.py accept several context fields
(prior_phase_summary, sermon_coach_pattern_summary, manuscript,
estimated_minutes, active_commitment, recent_coach_one_changes,
prior_overruns_summary). This module is the single source of truth that
fills those fields from the Sermon Coach data + this session's prior work.

Per v2 spec §6 (Cross-agent data wiring). Reuses coaching_bridge for the
active-commitment + coaching-insights queries that already exist.
"""

from datetime import datetime, timedelta, timezone

from coaching_bridge import load_active_commitment


# ── Coaching commitment ─────────────────────────────────────────────────

def format_active_commitment(commitment_dict):
    """Format an active commitment dict (from coaching_bridge.load_active_commitment)
    as a single readable line. Empty if no active commitment."""
    if not commitment_dict:
        return ''
    text = (commitment_dict.get('practice_experiment') or '').strip()
    return text


# ── Sermon-coach history ────────────────────────────────────────────────

def fetch_recent_one_changes(db, n=6):
    """Last `n` sermons' coach 'one change for next Sunday' notes,
    newest first, formatted as a bullet list."""
    conn = db._conn()
    try:
        rows = conn.execute("""
            SELECT s.preach_date, sr.one_change_for_next_sunday
            FROM sermon_reviews sr
            JOIN sermons s ON s.id = sr.sermon_id
            WHERE sr.one_change_for_next_sunday IS NOT NULL
              AND length(sr.one_change_for_next_sunday) > 0
            ORDER BY s.preach_date DESC
            LIMIT ?
        """, (n,)).fetchall()
    finally:
        conn.close()
    if not rows:
        return ''
    return '\n'.join(
        f"- {r['preach_date']}: {(r['one_change_for_next_sunday'] or '').strip()}"
        for r in rows
    )


def fetch_overrun_summary(db):
    """Aggregate length-overrun stats across all reviewed sermons."""
    conn = db._conn()
    try:
        row = conn.execute("""
            SELECT COUNT(*)                                       AS n,
                   AVG(duration_delta_seconds) / 60.0             AS avg_delta_min,
                   SUM(CASE WHEN duration_delta_seconds <= 0
                            THEN 1 ELSE 0 END)                    AS under_count,
                   MAX(duration_delta_seconds) / 60.0             AS max_delta_min
            FROM sermon_reviews
            WHERE duration_delta_seconds IS NOT NULL
        """).fetchone()
    finally:
        conn.close()
    if not row or row['n'] == 0:
        return ''
    return (
        f"{row['n']} reviewed sermons, "
        f"{row['avg_delta_min']:+.1f} min avg vs target, "
        f"{row['under_count']} ever under target, "
        f"worst {row['max_delta_min']:+.1f} min over."
    )


def fetch_sermon_coach_patterns(db, n_sermons=6):
    """Recurring negative-dimension patterns from the last `n_sermons` reviewed
    sermons. Returns a short multi-line summary, top 5 dimensions by major
    impact then total."""
    conn = db._conn()
    try:
        rows = conn.execute("""
            SELECT sm.dimension_key,
                   COUNT(*) AS neg_count,
                   SUM(CASE WHEN sm.impact = 'major' THEN 1 ELSE 0 END) AS major_count
            FROM sermon_moments sm
            WHERE sm.valence = 'negative'
              AND sm.sermon_id IN (
                  SELECT id FROM sermons
                  WHERE classified_as = 'sermon'
                  ORDER BY preach_date DESC
                  LIMIT ?
              )
            GROUP BY sm.dimension_key
            ORDER BY major_count DESC, neg_count DESC
            LIMIT 5
        """, (n_sermons,)).fetchall()
    finally:
        conn.close()
    if not rows:
        return ''
    lines = [f"Patterns from last {n_sermons} sermons (top negative dimensions):"]
    for r in rows:
        lines.append(
            f"- {r['dimension_key']}: {r['neg_count']} negative moments "
            f"({r['major_count']} major)"
        )
    return '\n'.join(lines)


# ── This session's prior phase work ─────────────────────────────────────

def fetch_prior_phase_summary(db, session_id, max_chars_per_entry=240):
    """Concatenate Bryan's card responses for this session — what he has
    captured in the research phases so far. Used by the Composing Partner
    to anchor its questioning in Bryan's own prior wrestling.

    Each entry is truncated to keep the system prompt bounded.
    """
    responses = db.get_card_responses(session_id)
    parts = []
    for r in responses:
        content = (r.get('content') or '').strip()
        if not content:
            continue
        if len(content) > max_chars_per_entry:
            content = content[:max_chars_per_entry].rstrip() + '…'
        parts.append(f"[{r['phase']}] {content}")
    return '\n'.join(parts)


# ── Manuscript reconstruction from outline ──────────────────────────────

def fetch_manuscript_from_outline(db, session_id):
    """Render the outline tree as a readable manuscript-like text block.
    The Editing Partner gets this as `manuscript` to audit cuts against."""
    tree = db.get_outline_tree(session_id)
    if not tree:
        return ''
    lines = []

    def _render(nodes, depth=0):
        for n in nodes:
            indent = '  ' * depth
            node_type = n.get('node_type') or ''
            content = n.get('content') or ''
            tag = f"[{node_type}] " if node_type else ''
            lines.append(f"{indent}- {tag}{content}")
            children = n.get('children') or []
            if children:
                _render(children, depth + 1)

    _render(tree)
    return '\n'.join(lines)


def fetch_recent_positive_feedback(pastor_feedback_store, days=28, limit=6):
    """Olivia / elder / congregant positive feedback from the last `days`.
    Used by the Editing Partner's Phase 15 audit so positive evidence
    doesn't vanish under the weight of pattern critique (v2 §4)."""
    if pastor_feedback_store is None:
        return ''
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = pastor_feedback_store._conn()
    try:
        rows = conn.execute("""
            SELECT captured_at, source_label, content
            FROM pastor_feedback
            WHERE valence = 'positive' AND captured_at >= ?
            ORDER BY captured_at DESC
            LIMIT ?
        """, (since, limit)).fetchall()
    finally:
        conn.close()
    if not rows:
        return ''
    lines = [f"Positive feedback from the last {days} days:"]
    for r in rows:
        lines.append(
            f"- {r['captured_at'][:10]} · {r['source_label']}: {r['content'].strip()}"
        )
    return '\n'.join(lines)


def estimate_spoken_minutes_from_outline(db, session_id, words_per_min=150):
    """Estimate spoken sermon length from the total word count of outline
    content. ~150 wpm is the standard spoken-prose estimate. Returns a
    float rounded to one decimal, or None if the outline is empty."""
    tree = db.get_outline_tree(session_id)
    if not tree:
        return None

    total_words = [0]

    def _count(nodes):
        for n in nodes:
            content = n.get('content') or ''
            total_words[0] += len(content.split())
            children = n.get('children') or []
            if children:
                _count(children)

    _count(tree)
    if total_words[0] == 0:
        return None
    return round(total_words[0] / float(words_per_min), 1)


# ── Persona dispatcher ──────────────────────────────────────────────────

def context_for_persona(db, session_id, persona, pastor_feedback_store=None):
    """Return the extra-context kwargs the right persona prompt needs.

    `pastor_feedback_store` is optional — when present, the Editing Partner
    additionally gets positive-feedback context for the Phase 15 audit so
    that affirmation stays visible alongside pattern critique.

    Research persona doesn't need any extra context fields.
    Silent persona has no prompt at all.
    Unknown persona returns {} (safe — defaults take over).
    """
    if persona == 'composing':
        return {
            'prior_phase_summary': fetch_prior_phase_summary(db, session_id),
            'sermon_coach_pattern_summary': fetch_sermon_coach_patterns(db),
        }
    if persona == 'editing':
        commitment = load_active_commitment(db)
        ctx = {
            'manuscript': fetch_manuscript_from_outline(db, session_id),
            'estimated_minutes': estimate_spoken_minutes_from_outline(db, session_id),
            'active_commitment': format_active_commitment(commitment),
            'recent_coach_one_changes': fetch_recent_one_changes(db),
            'prior_overruns_summary': fetch_overrun_summary(db),
        }
        positive = fetch_recent_positive_feedback(pastor_feedback_store)
        if positive:
            ctx['recent_positive_feedback'] = positive
        return ctx
    return {}
