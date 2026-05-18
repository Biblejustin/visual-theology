"""End-to-end persona dispatch smoke test (Task 8, three-persona router plan)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from phase_cluster_router import persona_for_phase
from companion_agent import select_persona_prompt
from persona_tools import tool_defs_for_persona


@pytest.mark.parametrize("phase, expected_persona, prompt_marker", [
    ('study',               'research',  'Research'),
    ('study_bibles',        'research',  'Research'),
    ('exegetical_point',    'composing', 'Composing'),
    ('sermon_construction', 'composing', 'Composing'),
    ('edit_pray',           'editing',   'Editing'),
])
def test_dispatch_chain(phase, expected_persona, prompt_marker):
    persona = persona_for_phase(phase)
    assert persona == expected_persona

    prompt = select_persona_prompt(
        persona=persona, passage='Romans 1:18-23', genre='epistle',
        session_elapsed_seconds=0, current_phase=phase,
        outline_summary='', card_work_summary='', coaching_context='',
    )
    assert prompt_marker in prompt

    tools = tool_defs_for_persona(persona)
    assert len(tools) > 0
    names = {t['name'] for t in tools}
    assert not (names & {'find_illustration', 'find_objections',
                         'find_counseling_applications',
                         'find_wisdom_principles', 'suggest_christ_thread'})


def test_silent_phase_produces_no_prompt():
    persona = persona_for_phase('prayer')
    assert persona == 'silent'
    prompt = select_persona_prompt(
        persona='silent', passage='Romans 1:18-23', genre='epistle',
        session_elapsed_seconds=0, current_phase='prayer',
        outline_summary='', card_work_summary='', coaching_context='',
    )
    assert prompt is None
    assert tool_defs_for_persona('silent') == []
