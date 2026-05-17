"""Extract a multi-sermon series plan from a session's conversation messages.

Pure function with an injectable LLMClient. Returns a list of SeriesPlanRow
or an empty list on any error / no-plan-found. The Flask route uses the result
to pre-populate the split modal; users can always edit before confirming.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol, Optional


@dataclass(frozen=True)
class SeriesPlanRow:
    position: int
    title: str
    verse_start: int
    verse_end: int


class _Client(Protocol):
    def call(self, prompt: str, schema: dict, **kwargs) -> dict: ...


_SCHEMA = {
    'type': 'object',
    'properties': {
        'sermons': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'position': {'type': 'integer', 'minimum': 1},
                    'title': {'type': 'string'},
                    'verse_start': {'type': 'integer', 'minimum': 1},
                    'verse_end': {'type': 'integer', 'minimum': 1},
                },
                'required': ['position', 'title', 'verse_start', 'verse_end'],
            },
        },
    },
    'required': ['sermons'],
}


_SYSTEM = (
    "You read a pastor's sermon-prep conversation and extract any plan to "
    "split the passage into a multi-sermon series. Return one row per sermon "
    "in preaching order with the title and verse range the pastor chose. "
    "If no series plan is discussed, return an empty list."
)


def _build_prompt(messages: list, session_chapter: int) -> str:
    convo = '\n'.join(
        f"[{m['id']}] {m['role']}: {m['content']}" for m in messages
    )
    return (
        f"Chapter under study: {session_chapter}\n\n"
        f"Conversation:\n{convo}\n\n"
        "Extract the sermon series plan if present. If the pastor decided "
        "to split this passage into multiple sermons, list each sermon with "
        "its position (1-based, preaching order), title (in quotes if given), "
        "and verse range."
    )


def propose_series_plan(messages: list, session_chapter: int,
                        llm: _Client) -> list:
    """Return a list of SeriesPlanRow extracted from `messages`.

    Returns [] on any LLM error, empty output, or if all rows fail validation.
    """
    if not messages:
        return []
    result = llm.call(
        prompt=_build_prompt(messages, session_chapter),
        schema=_SCHEMA,
        system=_SYSTEM,
        tool_name='emit_series_plan',
        tool_description='Emit the proposed sermon-series plan extracted from the conversation.',
    )
    if 'error' in result or 'output' not in result:
        return []
    raw = result['output'].get('sermons') or []
    rows = []
    for r in raw:
        try:
            vs = int(r.get('verse_start'))
            ve = int(r.get('verse_end'))
            pos = int(r.get('position'))
            title = str(r.get('title') or '').strip()
        except (TypeError, ValueError):
            continue
        if vs < 1 or ve < 1 or vs > ve or not title or pos < 1:
            continue
        rows.append(SeriesPlanRow(position=pos, title=title,
                                  verse_start=vs, verse_end=ve))
    rows.sort(key=lambda r: r.position)
    return rows
