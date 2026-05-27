"""Tests for the LANGUAGE_DISCIPLINE_RULE and per-persona language augments.

Anti-sycophancy is epistemology, not tone — it lives alongside SOURCE_FIDELITY_RULE
as a global "do not fabricate confidence" rule, with persona-local augments for
the highest-risk surface (composing) and a lighter touch on the best-postured
one (editing).

These are prompt-shape tests (Level 1). They catch silent deletion, refactor
drift, and missing pieces. They do NOT exercise the LLM — Level 2 fixtures do
that in a separate file.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from persona_prompts import (
    build_research_partner_prompt,
    build_composing_partner_prompt,
    build_editing_partner_prompt,
)


def _make_research(**overrides):
    defaults = dict(
        passage='Romans 1:18-23', genre='epistle',
        session_elapsed_seconds=0, current_phase='study',
        outline_summary='', card_work_summary='', coaching_context='',
    )
    defaults.update(overrides)
    return build_research_partner_prompt(**defaults)


def _make_composing(**overrides):
    defaults = dict(
        passage='Romans 1:18-23', genre='epistle',
        session_elapsed_seconds=0, current_phase='exegetical_point',
        outline_summary='', card_work_summary='', coaching_context='',
        prior_phase_summary='', sermon_coach_pattern_summary='',
    )
    defaults.update(overrides)
    return build_composing_partner_prompt(**defaults)


def _make_editing(**overrides):
    defaults = dict(
        passage='Romans 1:18-23', genre='epistle',
        session_elapsed_seconds=0, current_phase='edit_pray',
        manuscript='', estimated_minutes=None,
        active_commitment='', recent_coach_one_changes='',
        prior_overruns_summary='',
    )
    defaults.update(overrides)
    return build_editing_partner_prompt(**defaults)


_PERSONAS = [
    ('research', _make_research),
    ('composing', _make_composing),
    ('editing', _make_editing),
]


# ── Global LANGUAGE_DISCIPLINE_RULE present in every persona ──────────────


def test_language_discipline_block_present_in_every_persona():
    for name, make in _PERSONAS:
        p = make()
        assert 'Language Discipline' in p, f"{name} missing Language Discipline block"


def test_truthfulness_failure_frame_present():
    """The conceptual anchor — not 'banned words,' epistemology."""
    for name, make in _PERSONAS:
        p = make()
        assert 'truthfulness failure under social pressure' in p.lower(), (
            f"{name} missing the truthfulness-failure framing"
        )


# ── Three-category model ──────────────────────────────────────────────────


def test_three_categories_named_in_every_persona():
    for name, make in _PERSONAS:
        p = make().lower()
        assert 'behavior acknowledgment' in p, f"{name} missing category 1"
        assert 'process' in p and 'validation' in p, f"{name} missing category 2"
        assert 'substantive agreement' in p, f"{name} missing category 3"


def test_substantive_agreement_requires_basis():
    """Agreement only after naming textual/confessional/homiletical basis."""
    for name, make in _PERSONAS:
        p = make().lower()
        assert 'name' in p and 'basis' in p, (
            f"{name} missing 'name the basis' requirement for agreement"
        )


# ── Banned vocabulary ─────────────────────────────────────────────────────


_BANNED_SAMPLES = ['brilliant', 'masterful', 'perfect', 'spot-on', 'crystal clear']


def test_banned_vocabulary_list_intact():
    """Regression against silent deletion of the banned-list contents."""
    import re
    for name, make in _PERSONAS:
        p = re.sub(r'\s+', ' ', make().lower())
        for word in _BANNED_SAMPLES:
            assert word in p, f"{name} prompt no longer lists '{word}' as banned"


def test_diagnostic_adjectives_explicitly_allowed():
    """'clear,' 'complete,' 'supported' must be carved out — not a blanket ban."""
    for name, make in _PERSONAS:
        p = make().lower()
        # The carve-out lives in the global rule; assert the specific phrase
        # rather than just the word "diagnostic" (which appears elsewhere).
        assert 'diagnostic adjectives are allowed' in p, (
            f"{name} missing diagnostic-adjective carve-out from the language rule"
        )


# ── Challenge-trigger protocol ────────────────────────────────────────────


def test_challenge_protocol_pattern_present():
    """The 'I'd revise if X; otherwise concern remains Y' pattern."""
    for name, make in _PERSONAS:
        p = make()
        assert "I'd revise if" in p, f"{name} missing challenge-protocol pattern"


def test_no_immediate_capitulation_rule():
    for name, make in _PERSONAS:
        p = make().lower()
        assert 'do not immediately concede' in p, (
            f"{name} missing no-capitulation rule"
        )


# ── Tool truth ────────────────────────────────────────────────────────────


def test_tool_truth_rule_present():
    for name, make in _PERSONAS:
        p = make().lower()
        assert 'tool truth' in p, f"{name} missing tool-truth block"
        assert 'unless a tool' in p, (
            f"{name} missing 'unless a tool actually returned success' clause"
        )


# ── Per-persona augments ──────────────────────────────────────────────────


def test_research_augment_truth_over_rapport():
    p = _make_research()
    assert 'Truth over rapport' in p


def test_composing_augment_no_coining_one_liners():
    p = _make_composing()
    assert 'No coining of memorable phrases' in p


def test_composing_augment_bryans_confidence_not_evidence():
    p = _make_composing()
    assert "Bryan's confidence is not your evidence" in p


def test_editing_augment_no_retroactive_flattery():
    p = _make_editing()
    assert 'No retroactive flattery' in p


# ── Tone: sharp colleague, not warm friend ────────────────────────────────


def test_research_voice_is_sharp_not_warm():
    """Voice line should not list 'Warm' as a positive descriptor of tone.
    A meta-disclaim ('warmth is in the work, not the adjectives') is allowed."""
    p = _make_research()
    voice_section = p.split('Voice:')[1].split('\n\n')[0] if 'Voice:' in p else ''
    # The old prompt opened with "Warm, direct, intellectually curious" — the
    # comma after Warm was the giveaway that it was listed as a tonal adjective.
    assert 'Warm,' not in voice_section, (
        f"Research voice still lists 'Warm' as a tonal descriptor: {voice_section!r}"
    )
    assert 'sharp' in voice_section.lower() or 'rigorous' in voice_section.lower(), (
        f"Research voice missing 'sharp' or 'rigorous': {voice_section!r}"
    )


# ── Existing-language leaks (codex Q4) ────────────────────────────────────


def test_pattern_critique_shame_leak_removed_from_phase_cue():
    """persona_prompts.py:157 originally pressured the model to manufacture affirmation."""
    p = _make_editing(current_phase='p15_finishing')
    assert 'Pattern critique without affirmation' not in p
    assert 'produces shame, not change' not in p


def test_positive_feedback_does_not_manufacture_affirmation():
    """persona_prompts.py:662–666 originally said to surface affirmation alongside critique unconditionally."""
    p = _make_editing(
        current_phase='edit_pray',
        recent_positive_feedback='Olivia said the application was clearer last week.',
    )
    assert 'Pattern critique without affirmation produces shame' not in p
    assert 'Do not manufacture affirmation' in p, (
        "Positive-feedback block should explicitly forbid manufactured affirmation"
    )


def test_research_save_rule_uses_evidence_not_importance():
    """Old wording: 'save when Bryan lands something important' — model would infer importance and flatter."""
    p = _make_research()
    assert 'lands something important' not in p
    assert 'complete decision with text support' in p


def test_composing_save_rule_does_not_say_sharp():
    """Old wording at line 445 said 'when Bryan lands a sharp Subject/Theme.' Sharp is content evaluation."""
    p = _make_composing()
    assert 'sharp Subject' not in p
    assert 'complete homiletical decision' in p
