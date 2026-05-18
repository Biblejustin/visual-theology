"""Tests for persona-specific tool-list filters (Cycle 3c)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from persona_tools import (
    research_partner_tool_defs,
    composing_partner_tool_defs,
    editing_partner_tool_defs,
    tool_defs_for_persona,
    RETIRED_GENERATORS,
    DIAGNOSTICS,
)


def test_retired_generators_constant():
    assert RETIRED_GENERATORS == frozenset({
        'find_illustration', 'find_objections', 'find_counseling_applications',
        'find_wisdom_principles', 'suggest_christ_thread',
    })


def test_diagnostics_constant():
    assert DIAGNOSTICS == frozenset({
        'gauge_outline_balance', 'check_sermon_coherence',
    })


def test_research_partner_excludes_retired_generators():
    names = {t['name'] for t in research_partner_tool_defs()}
    assert not (names & RETIRED_GENERATORS)


def test_research_partner_keeps_diagnostics():
    names = {t['name'] for t in research_partner_tool_defs()}
    assert DIAGNOSTICS.issubset(names)


def test_composing_partner_excludes_retired_generators():
    names = {t['name'] for t in composing_partner_tool_defs()}
    assert not (names & RETIRED_GENERATORS)


def test_composing_partner_keeps_diagnostics():
    names = {t['name'] for t in composing_partner_tool_defs()}
    assert DIAGNOSTICS.issubset(names)


def test_editing_partner_excludes_retired_generators():
    names = {t['name'] for t in editing_partner_tool_defs()}
    assert not (names & RETIRED_GENERATORS)


def test_editing_partner_keeps_diagnostics():
    names = {t['name'] for t in editing_partner_tool_defs()}
    assert DIAGNOSTICS.issubset(names)


def test_tool_defs_for_persona_dispatches():
    research_names = {t['name'] for t in tool_defs_for_persona('research')}
    composing_names = {t['name'] for t in tool_defs_for_persona('composing')}
    editing_names = {t['name'] for t in tool_defs_for_persona('editing')}
    assert research_names == {t['name'] for t in research_partner_tool_defs()}
    assert composing_names == {t['name'] for t in composing_partner_tool_defs()}
    assert editing_names == {t['name'] for t in editing_partner_tool_defs()}


def test_tool_defs_for_silent_returns_empty():
    assert tool_defs_for_persona('silent') == []


def test_tool_defs_for_unknown_persona_returns_research_default():
    """Safe default — unknown persona gets research tool list (most conservative)."""
    research_names = {t['name'] for t in research_partner_tool_defs()}
    unknown_names = {t['name'] for t in tool_defs_for_persona('not_a_persona')}
    assert unknown_names == research_names
