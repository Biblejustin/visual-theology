"""Tests for the misplacement fix (Cycle 3a): the Study TA is a research GA,
not a content vendor. The 5 HE generators (find_illustration, find_objections,
find_counseling_applications, find_wisdom_principles, suggest_christ_thread)
must not be available to the Study TA. The 2 diagnostics (gauge_outline_balance,
check_sermon_coherence) stay. The HE-13 addendum must be gone from the
Study TA prompt.

Per v2 spec §14 items 4-6 + memory `[[homiletics-engine-phase1-misplacement]]`.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# Names per v2 spec
RETIRED = {
    'find_illustration', 'find_objections', 'find_counseling_applications',
    'find_wisdom_principles', 'suggest_christ_thread',
}
DIAGNOSTICS_KEPT = {'gauge_outline_balance', 'check_sermon_coherence'}


def test_he13_addendum_removed_from_study_prompt():
    """The 'Homiletics tools (Phase 1)' addendum must not appear in the
    Study TA system prompt — that addendum is the source of the
    'pushing too fast' failure mode."""
    from companion_agent import build_study_prompt
    p = build_study_prompt(
        passage='Romans 1:18-23',
        genre='epistle',
        session_elapsed_seconds=0,
        outline_summary='',
        card_work_summary='',
        coaching_context='',
    )
    assert 'Homiletics tools (Phase 1)' not in p
    assert 'Call them when the moment is right' not in p


def test_retired_tool_names_not_mentioned_in_study_prompt():
    """The Study TA prompt must not reference the retired generator names."""
    from companion_agent import build_study_prompt
    p = build_study_prompt(
        passage='Romans 1:18-23',
        genre='epistle',
        session_elapsed_seconds=0,
        outline_summary='',
        card_work_summary='',
        coaching_context='',
    )
    for name in RETIRED:
        assert name not in p, f"Retired tool {name!r} still appears in study prompt"


def test_study_ta_tool_defs_exclude_retired_tools():
    """The function that builds the Study TA's tool list must filter the 5
    retired generators out."""
    from companion_agent import study_ta_tool_defs
    names = {t['name'] for t in study_ta_tool_defs()}
    assert not (names & RETIRED), \
        f"Study TA tool list still includes retired tools: {names & RETIRED}"


def test_study_ta_tool_defs_keep_diagnostics():
    """The 2 diagnostic tools (gauge_outline_balance + check_sermon_coherence)
    stay available to the Study TA — they are diagnostic, not generative."""
    from companion_agent import study_ta_tool_defs
    names = {t['name'] for t in study_ta_tool_defs()}
    missing = DIAGNOSTICS_KEPT - names
    assert not missing, f"Study TA lost diagnostic tools that should stay: {missing}"


def test_study_ta_tool_defs_keep_research_tools():
    """The Study TA still has the research tools it has always had —
    Bible, lexicon, grammar, commentary, library search, etc. The filter
    only removes the 5 generators."""
    from companion_agent import study_ta_tool_defs
    names = {t['name'] for t in study_ta_tool_defs()}
    # A representative sample — the filter should not have accidentally removed any of these
    expected_research_tools_present = any(
        n in names for n in ['lookup_bible', 'lookup_lexicon', 'search_library', 'lookup_commentary']
    )
    assert expected_research_tools_present, \
        f"Study TA lost research tools — got names: {sorted(names)}"


def test_global_tool_definitions_still_contains_everything():
    """The 5 generators stay in TOOL_DEFINITIONS at the module level. They
    are filtered for the Study TA only; future Pre-Flight/Composing Partner
    cycles may re-enable them with strict opt-in + labeling."""
    from companion_tools import TOOL_DEFINITIONS
    names = {t['name'] for t in TOOL_DEFINITIONS}
    for name in RETIRED:
        assert name in names, \
            f"{name!r} removed from TOOL_DEFINITIONS — should stay for future cycles"
