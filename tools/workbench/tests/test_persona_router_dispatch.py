"""Tests for the persona router dispatch in stream_study_response (Task 5)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_select_persona_prompt_research():
    from companion_agent import select_persona_prompt
    out = select_persona_prompt(
        persona='research', passage='Rom 1:18', genre='epistle',
        session_elapsed_seconds=0, current_phase='study',
        outline_summary='', card_work_summary='', coaching_context='',
    )
    assert 'research grad assistant' in out.lower() or 'research graduate assistant' in out.lower()


def test_select_persona_prompt_composing():
    from companion_agent import select_persona_prompt
    out = select_persona_prompt(
        persona='composing', passage='Rom 1:18', genre='epistle',
        session_elapsed_seconds=0, current_phase='exegetical_point',
        outline_summary='', card_work_summary='', coaching_context='',
    )
    assert 'Composing Partner' in out or 'composing partner' in out.lower()


def test_select_persona_prompt_editing():
    from companion_agent import select_persona_prompt
    out = select_persona_prompt(
        persona='editing', passage='Rom 1:18', genre='epistle',
        session_elapsed_seconds=0, current_phase='edit_pray',
        outline_summary='', card_work_summary='', coaching_context='',
    )
    assert 'Editing Partner' in out or 'editing partner' in out.lower()


def test_select_persona_prompt_silent_returns_none():
    from companion_agent import select_persona_prompt
    out = select_persona_prompt(
        persona='silent', passage='Rom 1:18', genre='epistle',
        session_elapsed_seconds=0, current_phase='prayer',
        outline_summary='', card_work_summary='', coaching_context='',
    )
    assert out is None
