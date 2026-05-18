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


# ── Analyzers ───────────────────────────────────────────────────────────────


_ILLUSTRATION_RANK_SCHEMA = {
    'type': 'object',
    'properties': {
        'ranked': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'index': {'type': 'integer', 'minimum': 0},
                    'score': {'type': 'number', 'minimum': 0, 'maximum': 1},
                    'rationale': {'type': 'string'},
                },
                'required': ['index', 'score', 'rationale'],
            },
        },
    },
    'required': ['ranked'],
}

_ILLUSTRATION_RANK_SYSTEM = (
    "You rank passages as sermon illustrations. Score each on narrative "
    "concreteness (named people, dates, places, story arc) and vividness "
    "(0=abstract/theoretical, 1=vivid and immediately preachable). Return "
    "the indexes in descending score order with a one-line rationale."
)


class IllustrationFinder:
    """Find sermon illustrations for a theme. Uses search_library + Opus ranking."""

    name = 'find_illustration'

    def __init__(self, searcher=None, llm=None, candidate_multiplier: int = 5):
        self._searcher = searcher           # logos_search.LogosSearcher
        self._llm = llm                     # llm_client.AnthropicClient
        self._mult = candidate_multiplier

    def run(self, db, *, session_id: int, theme: str, count: int = 3,
            passage: dict = None) -> list:
        if not theme:
            return []
        searcher = self._searcher or self._default_searcher()
        candidates = self._search(searcher, theme, count)
        if not candidates:
            return []
        llm = self._llm or self._default_llm()
        ranked = self._rank(llm, theme, candidates)
        results = []
        for entry in ranked[:count]:
            cand = candidates[entry['index']]
            section = (cand.get('sections') or [{}])[0]
            insight = Insight(
                kind='illustration',
                title=f"{cand.get('title', '?')[:80]} — score {entry['score']:.2f}",
                body=section.get('snippet') or '',
                citations=[cand.get('resource_id')] if cand.get('resource_id') else [],
                confidence=float(entry['score']),
                metadata={
                    'rationale': entry['rationale'],
                    'author': cand.get('authors'),
                    'section_offset': section.get('section_offset'),
                },
            )
            persist(db, insight, session_id=session_id, source_tool=self.name)
            results.append(insight)
        return results

    def _search(self, searcher, theme: str, count: int) -> list:
        try:
            response = searcher.search(
                theme, limit=count * self._mult,
                resource_type_prefix='text.monograph',
            )
        except Exception:
            return []
        if not response.get('ok'):
            return []
        return response.get('results') or []

    def _rank(self, llm, theme: str, candidates: list) -> list:
        prompt_lines = [f"Theme: {theme}", "", "Candidates:"]
        for i, c in enumerate(candidates):
            section = (c.get('sections') or [{}])[0]
            prompt_lines.append(f"[{i}] {c.get('title', '?')}")
            prompt_lines.append(f"    {section.get('snippet', '')[:600]}")
        prompt = "\n".join(prompt_lines)
        result = llm.call(
            prompt=prompt, schema=_ILLUSTRATION_RANK_SCHEMA,
            system=_ILLUSTRATION_RANK_SYSTEM,
            tool_name='rank_illustrations',
            tool_description='Rank illustrations by narrative concreteness.',
        )
        if 'error' in result or 'output' not in result:
            return []
        return result['output'].get('ranked') or []

    def _default_searcher(self):
        from logos_search import get_searcher
        return get_searcher()

    def _default_llm(self):
        from llm_client import AnthropicClient
        from app_secrets import anthropic_api_key
        return AnthropicClient(api_key=anthropic_api_key())
