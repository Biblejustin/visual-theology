"""EF-prosthesis next-action sentence generation.

Per the 2026-05-19 ADHD UI research doc + my opinion on what to build
first: a single one-sentence cue surfaced at the point of work that
answers 'what do I do RIGHT NOW?' — Barkley's point-of-performance
EF support, minimal version.

Design constraints:
  - One call per v2 phase change, NOT per page load. Cost matters; the
    user re-enters /study/ multiple times per day.
  - Cached on the session row; regenerate only when v2_phase changes
    or the user clicks Refresh.
  - Haiku, not Opus. Short. ~50-token output max.
  - Failure is invisible: if the API key is missing or the call errors,
    we return None and the UI falls back to the static v2_phases posture
    cue.
  - The result is shame-resistant (the doc's #8 recommendation) — it
    names what to do, not what was missed.
"""

import os
import re

from v2_phases import get_phase


_MAX_TOKENS = 80


def _system_prompt():
    return (
        "You write a single short next-action cue for a Reformed Presbyterian "
        "pastor doing weekly sermon prep with ADHD. The cue lands at the top "
        "of the current sermon study screen — it answers 'what's the one "
        "useful thing to do RIGHT NOW?' "
        "\n\n"
        "Rules:\n"
        "- Exactly ONE sentence, ≤ 18 words.\n"
        "- Imperative voice. 'Surface…', 'Answer…', 'Pick…', 'Save…'.\n"
        "- Behaviorally small. The user should be able to start in seconds.\n"
        "- Specific to the current phase + passage. No phase numbers in output.\n"
        "- No 'should'. No 'overdue'. No 'behind'. No moralizing.\n"
        "- No quotes around the sentence."
    )


def _user_prompt(passage, genre, v2_phase, card_work_summary, outline_summary):
    posture = v2_phase.posture if v2_phase else 'Sermon prep.'
    phase_name = v2_phase.name if v2_phase else 'Sermon prep'
    parts = [
        f"Passage: {passage}",
        f"Genre: {genre or 'unspecified'}",
        f"Phase: {phase_name} — {posture}",
    ]
    if card_work_summary:
        parts.append(f"Bryan's work so far (card phases): {card_work_summary[:600]}")
    if outline_summary:
        parts.append(f"Current outline: {outline_summary[:400]}")
    parts.append("Write the next-action sentence now.")
    return "\n\n".join(parts)


def _sanitize(text):
    if not text:
        return None
    # Single line, no surrounding quotes/whitespace, drop common prefixes.
    s = text.strip().strip('"').strip("'").strip()
    s = re.split(r'[\r\n]', s)[0].strip()
    # Order matters: longer prefixes first so 'Next action:' doesn't match the
    # bare 'Next:?' branch and leave ' action: …' behind.
    s = re.sub(r'^(Next action:?|Action:?|Next:?)\s*', '', s, flags=re.I)
    # Cap length defensively — model is told ≤18 words but we'll
    # display a soft cap of ~160 chars.
    if len(s) > 200:
        s = s[:200].rsplit(' ', 1)[0] + '…'
    return s or None


def generate_next_action(*, passage, genre, v2_phase_key,
                        card_work_summary='', outline_summary='',
                        llm_client=None):
    """Return a one-sentence next-action string for the given session
    state, or None if generation can't proceed (no API key, no client,
    error). Never raises — caller's UI falls back to the static phase
    posture cue."""
    if not v2_phase_key:
        return None
    v2 = get_phase(v2_phase_key)
    if not v2:
        return None
    # Silent phases (1-3) intentionally have no LLM cue.
    if v2.persona == 'silent':
        return None

    if llm_client is None:
        try:
            from app_secrets import anthropic_api_key
            from llm_client import AnthropicClient
        except Exception:
            return None
        api_key = anthropic_api_key()
        if not api_key:
            return None
        # Haiku is plenty for one short sentence; Opus would be wasteful
        # on a call that fires every phase transition.
        llm_client = AnthropicClient(api_key=api_key,
                                     model='claude-haiku-4-5-20251001')

    messages = [{'role': 'user',
                 'content': _user_prompt(passage, genre, v2,
                                         card_work_summary, outline_summary)}]
    try:
        # stream_message yields events; collect text deltas.
        text_parts = []
        for event in llm_client.stream_message(
                system=_system_prompt(), messages=messages, tools=[],
                max_tokens=_MAX_TOKENS):
            if event.get('type') == 'text_delta':
                text_parts.append(event.get('text', ''))
            elif event.get('type') == 'error':
                return None
        return _sanitize(''.join(text_parts))
    except Exception:
        return None
