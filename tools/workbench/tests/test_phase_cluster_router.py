"""Tests for phase_cluster_router (Cycle 3b substrate).

Canonical phase → persona mapping. No wiring yet — that lands cycle-by-cycle
as each persona's prompt is built.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from phase_cluster_router import persona_for_phase, PERSONAS, PHASE_PERSONA


# ── Personas exist ─────────────────────────────────────────────────────────

def test_four_personas_defined():
    assert set(PERSONAS) == {'silent', 'research', 'composing', 'editing'}


# ── Silent (workflow Phases 1–3) ──────────────────────────────────────────

@pytest.mark.parametrize("phase", [
    'prayer',          # /study/ + /companion/
    'read_translate',  # /study/ Phase 2
    'text_work',       # /companion/ Phase 2 (translate)
    'translation',     # observed in live DB
    'read_text',       # observed in live DB
    'digestion',       # /study/ + /companion/ Phase 3
])
def test_silent_phases_map_to_silent(phase):
    assert persona_for_phase(phase) == 'silent', \
        f"Phase {phase!r} should be silent (Phases 1-3 of workflow)"


# ── Research (workflow Phases 4–12) ───────────────────────────────────────

@pytest.mark.parametrize("phase", [
    'study_bibles',  # /study/ Phase 5 (Quick Answers)
    'observation',   # /companion/ Phase 4 (Exegesis observation)
    'word_study',    # /companion/ word-study
    'context',       # /companion/ Phase 6 (Context Questions)
    'theological',   # /companion/ Phase 9 (ST/BT)
    'commentary',    # /companion/ Phase 12 (Commentaries)
])
def test_research_phases_map_to_research(phase):
    assert persona_for_phase(phase) == 'research', \
        f"Phase {phase!r} should be research (Phases 4-12 of workflow)"


# ── Composing (workflow Phases 13–14) ─────────────────────────────────────

@pytest.mark.parametrize("phase", [
    'exegetical_point',     # /companion/ Phase 13.3 (EP/OTM)
    'fcf_homiletical',      # /companion/ Phase 13.6 (FCF) + 13.7 (HP)
    'sermon_construction',  # /companion/ Phase 14 (Sermon Writing)
])
def test_composing_phases_map_to_composing(phase):
    assert persona_for_phase(phase) == 'composing', \
        f"Phase {phase!r} should be composing (Phases 13-14)"


# ── Editing (workflow Phases 15–16) ───────────────────────────────────────

def test_edit_pray_maps_to_editing():
    assert persona_for_phase('edit_pray') == 'editing'


# ── Catch-all + unknown ───────────────────────────────────────────────────

def test_study_catch_all_maps_to_research():
    """The /study/ conversation mode uses phase='study' (not phase-scoped).
    Until persona-specific prompts ship, default to research (current
    Study TA behavior)."""
    assert persona_for_phase('study') == 'research'


def test_unknown_phase_defaults_to_research():
    """Safe default: unknown phases get research (most conservative —
    matches the current Study TA behavior, never silently goes silent)."""
    assert persona_for_phase('not_a_real_phase') == 'research'


def test_empty_string_defaults_to_research():
    assert persona_for_phase('') == 'research'


def test_none_defaults_to_research():
    assert persona_for_phase(None) == 'research'


# ── Mapping table integrity ───────────────────────────────────────────────

def test_phase_persona_values_are_valid_personas():
    """Every value in PHASE_PERSONA must be one of the four declared personas."""
    invalid = {p: v for p, v in PHASE_PERSONA.items() if v not in PERSONAS}
    assert not invalid, f"PHASE_PERSONA has invalid persona values: {invalid}"


def test_all_companion_phases_have_persona_assignment():
    """Every phase known to companion_db should be classified."""
    from companion_db import PHASES_ORDER
    missing = [p for p in PHASES_ORDER if p not in PHASE_PERSONA]
    assert not missing, f"Companion phases missing from router: {missing}"
