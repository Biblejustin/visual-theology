"""Homiletics Engine — analyzers that produce Insight objects for the companion.

Each analyzer is an independent class with a `run()` method returning
`list[Insight]`. Analyzers share no state. The Insight type is the only
contract between analyzers and the delivery layer (Phase 1: LLM tools).

Phase 2-4 (save hooks, pre-flight review, post-preach learning) plug into
the same engine without rework.
"""

from __future__ import annotations
from dataclasses import dataclass, field

ANALYZER_KINDS = frozenset({
    'illustration', 'objection', 'counseling', 'wisdom',
    'christ_thread', 'balance_flag', 'coherence_flag',
})


@dataclass(frozen=True)
class Insight:
    """A homiletics insight. Frozen so it can be safely shared across threads."""
    kind: str                              # one of ANALYZER_KINDS
    title: str                             # one-line human-readable
    body: str                              # markdown detail
    citations: list                        # resource IDs (LLS:...) or sermon IDs
    confidence: float                      # 0.0-1.0
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.kind not in ANALYZER_KINDS:
            raise ValueError(f"unknown kind: {self.kind!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence {self.confidence} out of [0, 1]")


def persist(db, insight: Insight, *, session_id: int, source_tool: str) -> int:
    """Persist an Insight to homiletics_insights. Returns the row id."""
    return db.save_insight(
        session_id=session_id,
        kind=insight.kind,
        title=insight.title,
        body=insight.body,
        citations=insight.citations,
        confidence=insight.confidence,
        metadata=insight.metadata,
        source_tool=source_tool,
    )
