"""Tests for per-phase ACTIVE FOCUS injection (review #4 deepening).

Each persona prompt now includes an ACTIVE FOCUS section at the top
that surfaces the SPECIFIC posture for the current v2 phase — tools
to reach for first, what to avoid, time budget. These tests pin the
content so the prompt doesn't silently regress to generic.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ── ACTIVE FOCUS block injection ─────────────────────────────────────

def test_research_p06_context_focus_mentions_cultural_concepts():
    """Phase 6 should tell the LLM to reach for cultural-context tools."""
    from persona_prompts import build_research_partner_prompt
    p = build_research_partner_prompt(
        passage='Rom 1:18-23', genre='epistle',
        session_elapsed_seconds=0,
        current_phase='p06_context',
    )
    assert 'ACTIVE FOCUS' in p
    assert 'Phase 6' in p
    assert 'Cultural' in p or 'cultural' in p
    assert 'PROACTIVE' in p or 'proactive' in p
    # Should NOT default to generic verse-level exegesis cues
    assert 'get_passage_context' in p or 'cultural_concepts' in p


def test_research_p09_systematics_focus_mentions_anti_paralysis():
    """Phase 9 is the anti-paralysis curator."""
    from persona_prompts import build_research_partner_prompt
    p = build_research_partner_prompt(
        passage='Rom 1:18-23', genre='epistle',
        session_elapsed_seconds=0,
        current_phase='p09_systematics',
    )
    assert 'ACTIVE FOCUS' in p
    assert 'Phase 9' in p
    assert 'Anti-paralysis' in p or 'anti-paralysis' in p
    assert 'cross-tradition' in p.lower() or 'reformed-only' in p.lower()


def test_research_p12_commentaries_focus_mentions_curator():
    """Phase 12 is the aggressive curator."""
    from persona_prompts import build_research_partner_prompt
    p = build_research_partner_prompt(
        passage='Rom 1:18-23', genre='epistle',
        session_elapsed_seconds=0,
        current_phase='p12_commentaries',
    )
    assert 'ACTIVE FOCUS' in p
    assert 'Phase 12' in p
    assert '5' in p and 'distinctive' in p.lower()
    # Distinctive ≠ Reformed-only echo chamber
    assert 'Reformed conservatives' in p or 'cross-tradition' in p.lower()


def test_composing_p13_focus_mentions_procrastination():
    """Phase 13 active focus must name the procrastination zone."""
    from persona_prompts import build_composing_partner_prompt
    p = build_composing_partner_prompt(
        passage='Rom 1', genre='epistle',
        session_elapsed_seconds=0,
        current_phase='p13_sermon_work',
    )
    assert 'ACTIVE FOCUS' in p
    assert 'Phase 13' in p
    assert 'PROCRASTINATION' in p or 'procrastination' in p.lower()


def test_editing_p16_focus_mentions_4_pass():
    """Phase 16 is the 4-pass evidence-based edit."""
    from persona_prompts import build_editing_partner_prompt
    p = build_editing_partner_prompt(
        passage='Rom 1', genre='epistle',
        session_elapsed_seconds=0,
        current_phase='p16_ruthless_edit',
    )
    assert 'ACTIVE FOCUS' in p
    assert 'Phase 16' in p
    assert '4-pass' in p.lower() or '4 pass' in p.lower() or 'four-pass' in p.lower() or 'four pass' in p.lower()


# ── Phase number / name appears correctly ────────────────────────────

@pytest.mark.parametrize('v2_key,expected_num', [
    ('p06_context',         '6'),
    ('p09_systematics',     '9'),
    ('p12_commentaries',    '12'),
])
def test_active_focus_names_the_right_phase_number(v2_key, expected_num):
    from persona_prompts import build_research_partner_prompt
    p = build_research_partner_prompt(
        passage='Rom 1', genre='epistle',
        session_elapsed_seconds=0,
        current_phase=v2_key,
    )
    # ACTIVE FOCUS block header should include the phase number
    assert f'Phase {expected_num}' in p


# ── Time budget appears in the active focus ──────────────────────────

def test_active_focus_includes_time_gauge_when_elapsed():
    from persona_prompts import build_research_partner_prompt
    p = build_research_partner_prompt(
        passage='Rom 1', genre='epistle',
        session_elapsed_seconds=300,
        current_phase='p12_commentaries',
        elapsed_in_phase_seconds=1500,  # 25 min into a 30-min budget
    )
    assert '25 min' in p
    assert '30 min' in p
    # Color cue should pick green / yellow / red
    assert any(c in p for c in ('green', 'yellow', 'red'))


def test_active_focus_marks_over_budget_red():
    from persona_prompts import build_research_partner_prompt
    p = build_research_partner_prompt(
        passage='Rom 1', genre='epistle',
        session_elapsed_seconds=300,
        current_phase='p12_commentaries',
        elapsed_in_phase_seconds=3600,  # 60 min in a 30-min phase
    )
    assert 'red' in p.lower()


# ── Unknown / silent phases degrade gracefully ───────────────────────

def test_active_focus_omitted_for_silent_phases():
    """p01_prayer is silent — no ACTIVE FOCUS block since the persona
    won't render anyway, but the builder shouldn't crash either."""
    from persona_prompts import build_research_partner_prompt
    p = build_research_partner_prompt(
        passage='Rom 1', genre='epistle',
        session_elapsed_seconds=0,
        current_phase='p01_prayer',  # silent — no focus metadata
    )
    # Silent phases don't have an _ACTIVE_FOCUS entry, so the block is empty
    # but the surrounding prompt still renders.
    assert 'Research Partner' in p
    assert 'ACTIVE FOCUS' not in p


def test_active_focus_omitted_for_unknown_phase_key():
    """Stale picker value shouldn't break the prompt."""
    from persona_prompts import build_research_partner_prompt
    p = build_research_partner_prompt(
        passage='Rom 1', genre='epistle',
        session_elapsed_seconds=0,
        current_phase='p99_nonexistent',
    )
    assert 'Research Partner' in p
    assert 'ACTIVE FOCUS' not in p
