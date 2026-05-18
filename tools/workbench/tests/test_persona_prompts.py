"""Tests for persona_prompts.

Each persona prompt is asserted against the load-bearing pieces of v2 spec §4.
False-positive tests verify the prompts don't re-introduce HE-13 generator
language or coach-vs-researcher framing.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ── Research Partner ──────────────────────────────────────────────────────

from persona_prompts import build_research_partner_prompt


def _make_research(**overrides):
    defaults = dict(
        passage='Romans 1:18-23', genre='epistle',
        session_elapsed_seconds=0, current_phase='study',
        outline_summary='', card_work_summary='', coaching_context='',
    )
    defaults.update(overrides)
    return build_research_partner_prompt(**defaults)


def test_research_identifies_as_research_grad_assistant():
    p = _make_research()
    assert 'research grad assistant' in p.lower() or 'research graduate assistant' in p.lower()


def test_research_states_the_hard_rule():
    p = _make_research()
    lowered = p.lower()
    assert 'not' in lowered and 'sermon' in lowered and 'write' in lowered


def test_research_does_not_describe_self_as_coach():
    """Research Partner is RA + interlocutor + reviewer, never coach."""
    p = _make_research()
    lowered = p.lower()
    assert 'i am a coach' not in lowered
    assert 'homiletical coach' not in lowered
    assert 'your coach' not in lowered


def test_research_invokes_cross_tradition_principle():
    p = _make_research()
    assert 'tradition' in p.lower()


def test_research_invokes_anti_paralysis_for_research_phases():
    p = _make_research()
    lowered = p.lower()
    assert 'paralys' in lowered or 'drown' in lowered or 'overwhelm' in lowered


def test_research_does_not_reference_retired_generators():
    p = _make_research()
    for name in ['find_illustration', 'find_objections', 'find_counseling_applications',
                 'find_wisdom_principles', 'suggest_christ_thread']:
        assert name not in p, f"Research Partner prompt re-introduces {name}"


def test_research_does_not_have_he13_addendum():
    p = _make_research()
    assert 'Homiletics tools (Phase 1)' not in p


def test_research_includes_diagnostics_in_tool_reference():
    p = _make_research()
    assert 'gauge_outline_balance' in p or 'check_sermon_coherence' in p


def test_research_phase_8_christ_identification_conditional_framing():
    p = _make_research()
    lowered = p.lower()
    assert 'after' in lowered


def test_research_includes_outline_summary_when_provided():
    p = _make_research(outline_summary='I. Wrath revealed (v.18)\nII. Knowing the unknown God (v.19-23)')
    assert 'Wrath revealed' in p


def test_research_includes_card_work_when_provided():
    p = _make_research(card_work_summary='[prayer] Lord, open my eyes.\n[translation] The wrath is being revealed (present passive).')
    assert 'wrath is being revealed' in p


# ── Composing Partner ─────────────────────────────────────────────────────

from persona_prompts import build_composing_partner_prompt


def _make_composing(**overrides):
    defaults = dict(
        passage='Romans 1:18-23', genre='epistle',
        session_elapsed_seconds=0, current_phase='exegetical_point',
        outline_summary='', card_work_summary='', coaching_context='',
        prior_phase_summary='', sermon_coach_pattern_summary='',
    )
    defaults.update(overrides)
    return build_composing_partner_prompt(**defaults)


def test_composing_states_elicitation_not_composition():
    p = _make_composing()
    lowered = p.lower()
    assert 'elicit' in lowered or 'socratic' in lowered or 'interview' in lowered


def test_composing_states_hard_rule():
    p = _make_composing()
    lowered = p.lower()
    assert 'sermon' in lowered and 'write' in lowered and 'not' in lowered


def test_composing_names_phase_13_sub_phases():
    p = _make_composing()
    for sub in ['Subject', 'Theme', 'EP', 'FCF', 'HP', 'Purpose']:
        assert sub in p, f"Composing prompt missing sub-phase {sub}"


def test_composing_invokes_homiletical_synthesis():
    p = _make_composing()
    for source in ['Robinson', 'Prutow', 'Chapell']:
        assert source in p


def test_composing_references_prior_phase_summary_when_provided():
    p = _make_composing(prior_phase_summary='Phase 4 exegetical work: aorist passive in v.18 = ongoing revelation of wrath')
    assert 'aorist passive' in p


def test_composing_references_coach_patterns_when_provided():
    p = _make_composing(sermon_coach_pattern_summary='Last 3 sermons: Christ-tacked-on-at-end pattern flagged.')
    assert 'Christ-tacked-on-at-end' in p


def test_composing_does_not_have_he13_addendum():
    p = _make_composing()
    assert 'Homiletics tools (Phase 1)' not in p


# ── Editing Partner ───────────────────────────────────────────────────────

from persona_prompts import build_editing_partner_prompt


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


def test_editing_states_data_not_verdict_framing():
    p = _make_editing()
    assert 'data' in p.lower() and 'verdict' in p.lower()


def test_editing_states_bryan_keeps_or_cuts():
    p = _make_editing()
    lowered = p.lower()
    assert 'never delet' in lowered or ('bryan' in lowered and 'cut' in lowered)


def test_editing_names_four_passes():
    p = _make_editing()
    assert 'Pass 1' in p and 'Pass 2' in p and 'Pass 3' in p and 'Pass 4' in p


def test_editing_pass_1_targets_exposition():
    p = _make_editing()
    assert 'exposition' in p.lower()


def test_editing_pass_2_targets_application_placement():
    p = _make_editing()
    lowered = p.lower()
    assert 'application' in lowered and ('placement' in lowered or 'earlier' in lowered)


def test_editing_includes_active_commitment_when_provided():
    p = _make_editing(active_commitment='Tuesday at 10am Test: name a time/setting before the 60% mark.')
    assert 'Tuesday at 10am Test' in p


def test_editing_references_overrun_history_when_provided():
    p = _make_editing(prior_overruns_summary='38 sermons, +12.4 min avg overrun, zero under target.')
    assert '+12.4 min' in p or '12.4' in p


def test_editing_includes_length_estimate_when_provided():
    p = _make_editing(estimated_minutes=37.5)
    assert '37.5' in p
