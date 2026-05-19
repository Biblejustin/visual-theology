"""Tests for persona-aware tool filtering (formerly test_study_ta_retired_generators.py).

The Study TA was renamed Research Partner in the v2 persona refactor; the
tool-filtering logic moved from companion_agent.study_ta_tool_defs() to
persona_tools.tool_defs_for_persona(). These tests assert the same
invariants against the live API:

- The 5 HE generators (find_illustration, find_objections,
  find_counseling_applications, find_wisdom_principles,
  suggest_christ_thread) must NOT be available to any of the three
  conversational personas (research / composing / editing).
- The 2 diagnostics (gauge_outline_balance, check_sermon_coherence)
  MUST stay available.
- The 5 generators must remain in TOOL_DEFINITIONS at the module level —
  filtered for personas only; future cycles may re-enable them with
  strict opt-in.

Per v2 spec §14 items 4-6 + memory `[[homiletics-engine-phase1-misplacement]]`.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


RETIRED = {
    'find_illustration', 'find_objections', 'find_counseling_applications',
    'find_wisdom_principles', 'suggest_christ_thread',
}
DIAGNOSTICS_KEPT = {'gauge_outline_balance', 'check_sermon_coherence'}
PERSONAS = ('research', 'composing', 'editing')


def test_no_persona_exposes_retired_generators():
    """The 5 HE generators must be filtered out of every persona's tool list."""
    from persona_tools import tool_defs_for_persona
    for persona in PERSONAS:
        names = {t['name'] for t in tool_defs_for_persona(persona)}
        overlap = names & RETIRED
        assert not overlap, \
            f"{persona!r} persona still exposes retired generators: {overlap}"


def test_every_persona_keeps_diagnostics():
    """gauge_outline_balance + check_sermon_coherence are diagnostic (not
    generative). They stay available to every conversational persona."""
    from persona_tools import tool_defs_for_persona
    for persona in PERSONAS:
        names = {t['name'] for t in tool_defs_for_persona(persona)}
        missing = DIAGNOSTICS_KEPT - names
        assert not missing, \
            f"{persona!r} persona lost diagnostic tools: {missing}"


def test_research_persona_keeps_research_tools():
    """Sanity check that the filter only removes the 5 generators —
    not the core research tools."""
    from persona_tools import tool_defs_for_persona
    names = {t['name'] for t in tool_defs_for_persona('research')}
    present = any(
        n in names for n in ('lookup_bible', 'lookup_lexicon', 'search_library', 'lookup_commentary')
    )
    assert present, f"Research persona lost research tools — got: {sorted(names)}"


def test_silent_persona_has_no_tools():
    """The fourth persona ('silent') renders no chat surface; tool list
    is empty so any accidental routing into a streaming agent returns
    no callable tools."""
    from persona_tools import tool_defs_for_persona
    assert tool_defs_for_persona('silent') == []


def test_unknown_persona_falls_back_to_research():
    """Defensive default: an unrecognized persona name returns the
    research tool list rather than crashing or returning nothing."""
    from persona_tools import tool_defs_for_persona
    research_names = {t['name'] for t in tool_defs_for_persona('research')}
    unknown_names = {t['name'] for t in tool_defs_for_persona('not-a-real-persona')}
    assert research_names == unknown_names


def test_global_tool_definitions_still_contains_everything():
    """The 5 generators stay in TOOL_DEFINITIONS at the module level. They
    are filtered for personas; future cycles may re-enable them with
    strict opt-in + labeling."""
    from companion_tools import TOOL_DEFINITIONS
    names = {t['name'] for t in TOOL_DEFINITIONS}
    for name in RETIRED:
        assert name in names, \
            f"{name!r} removed from TOOL_DEFINITIONS — should stay for future cycles"
