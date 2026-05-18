"""Persona-specific tool-list filters (Cycle 3c).

The three personas — Research / Composing / Editing — currently share the
same tool inventory: research tools + the 2 diagnostics (gauge_outline_balance,
check_sermon_coherence), with the 5 HE generators filtered out.

The differentiation between personas is in their system prompts (how and when
the tools are used), not in which tools exist. Each persona has its own
function here so future divergence (e.g., generators returning to the
Composing Partner with strict opt-in) lands in one obvious place.
"""

from companion_tools import TOOL_DEFINITIONS


RETIRED_GENERATORS = frozenset({
    'find_illustration',
    'find_objections',
    'find_counseling_applications',
    'find_wisdom_principles',
    'suggest_christ_thread',
})


DIAGNOSTICS = frozenset({
    'gauge_outline_balance',
    'check_sermon_coherence',
})


def _filter(excluded):
    return [
        {'name': t['name'], 'description': t['description'], 'input_schema': t['input_schema']}
        for t in TOOL_DEFINITIONS
        if t['name'] not in excluded
    ]


def research_partner_tool_defs():
    """Tools available to the Research Partner (Phases 4-12)."""
    return _filter(RETIRED_GENERATORS)


def composing_partner_tool_defs():
    """Tools available to the Composing Partner (Phases 13-14).

    Currently identical to Research. The Composing Partner elicits content
    via Socratic questioning; it does not generate, so the 5 generators
    stay excluded.
    """
    return _filter(RETIRED_GENERATORS)


def editing_partner_tool_defs():
    """Tools available to the Editing Partner (Phases 15-16).

    Diagnostics are PRIMARY here — gauge_outline_balance and
    check_sermon_coherence are the natural tools for an auto-audit pass.
    Research tools stay for citation work during cuts.
    """
    return _filter(RETIRED_GENERATORS)


_PERSONA_TOOL_FUNCS = {
    'research': research_partner_tool_defs,
    'composing': composing_partner_tool_defs,
    'editing': editing_partner_tool_defs,
}


def tool_defs_for_persona(persona):
    """Dispatch to the right tool-list function. Silent → no tools.
    Unknown → research (safe default matching current Study TA behavior)."""
    if persona == 'silent':
        return []
    func = _PERSONA_TOOL_FUNCS.get(persona, research_partner_tool_defs)
    return func()
