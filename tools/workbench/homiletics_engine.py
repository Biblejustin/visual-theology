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


_CHRIST_THREAD_CATEGORIES = (
    'typology', 'promise_fulfillment', 'contrast',
    'redemptive_historical', 'direct_reference',
)

_CHRIST_THREAD_SCHEMA = {
    'type': 'object',
    'properties': {
        'angles': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'category': {'type': 'string', 'enum': list(_CHRIST_THREAD_CATEGORIES)},
                    'angle': {'type': 'string'},
                    'rationale': {'type': 'string'},
                    'supporting_refs': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['category', 'angle', 'rationale'],
            },
        },
    },
    'required': ['angles'],
}

_CHRIST_THREAD_SYSTEM = (
    "You propose Christ-thread angles for sermon prep from a Reformed homiletical "
    "perspective. Use these five categories, in descending preference for the "
    "passage:\n"
    "  - direct_reference: the text names or describes Christ directly\n"
    "  - promise_fulfillment: a promise the text makes that Christ fulfills (or vice versa)\n"
    "  - typology: a person, event, or institution that prefigures Christ\n"
    "  - contrast: how the text shows the inability that Christ alone meets\n"
    "  - redemptive_historical: where this passage sits in salvation history with Christ at the center\n"
    "For each angle: state it in one sentence, give a one-line rationale tied to the "
    "text, and cite 1-2 supporting biblical references. Distribute across categories "
    "where the text supports it; do not force five if fewer fit."
)


class ChristThreadProposer:
    """Propose Christ-thread angles for the passage (optionally focused on a point)."""

    name = 'suggest_christ_thread'

    def __init__(self, llm=None):
        self._llm = llm

    def run(self, db, *, session_id: int, passage: dict,
            point: str = None, count: int = 3) -> list:
        llm = self._llm or self._default_llm()
        prompt = self._build_prompt(passage, point, count)
        result = llm.call(
            prompt=prompt, schema=_CHRIST_THREAD_SCHEMA,
            system=_CHRIST_THREAD_SYSTEM,
            tool_name='emit_christ_thread_angles',
            tool_description='Emit Christ-thread angles for the passage.',
        )
        if 'error' in result or 'output' not in result:
            return []
        angles = result['output'].get('angles') or []
        results = []
        for a in angles[:count]:
            insight = Insight(
                kind='christ_thread',
                title=f"{a['category']}: {a['angle'][:80]}",
                body=f"**{a['angle']}**\n\n{a['rationale']}",
                citations=list(a.get('supporting_refs') or []),
                confidence=0.85,
                metadata={
                    'category': a['category'],
                    'supporting_refs': list(a.get('supporting_refs') or []),
                },
            )
            persist(db, insight, session_id=session_id, source_tool=self.name)
            results.append(insight)
        return results

    def _build_prompt(self, passage: dict, point: str, count: int) -> str:
        ref = passage.get('passage_ref') or '?'
        lines = [f"Passage: {ref}", f"Requested angles: {count}"]
        if point:
            lines.append(f"Specific point under consideration: {point}")
        return "\n".join(lines)

    def _default_llm(self):
        from llm_client import AnthropicClient
        from app_secrets import anthropic_api_key
        return AnthropicClient(api_key=anthropic_api_key())


_WISDOM_SCHEMA = {
    'type': 'object',
    'properties': {
        'principles': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'principle': {'type': 'string'},
                    'rationale': {'type': 'string'},
                    'cross_refs': {'type': 'array', 'items': {'type': 'string'}},
                    'principle_tags': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['principle', 'rationale'],
            },
        },
    },
    'required': ['principles'],
}

_WISDOM_SYSTEM = (
    "Distill 2-3 transferable wisdom principles from the passage. Each principle "
    "should be a single proverbial sentence that carries beyond the specific "
    "situation in the text. Give a brief rationale grounded in the text, 1-3 "
    "cross-references (preferring wisdom literature — Proverbs, Job, Ecclesiastes "
    "— and Pauline epistles), and a short list of topical tags. Avoid generic "
    "moralism: each principle should be something a Reformed pastor would happily "
    "preach as a sub-point."
)


class WisdomPrinciples:
    """Extract transferable wisdom principles from the passage."""

    name = 'find_wisdom_principles'

    def __init__(self, llm=None):
        self._llm = llm

    def run(self, db, *, session_id: int, passage: dict, count: int = 3) -> list:
        llm = self._llm or self._default_llm()
        prompt = f"Passage: {passage.get('passage_ref', '?')}\nRequested principles: {count}"
        result = llm.call(
            prompt=prompt, schema=_WISDOM_SCHEMA,
            system=_WISDOM_SYSTEM,
            tool_name='emit_wisdom_principles',
            tool_description='Emit transferable wisdom principles from the passage.',
        )
        if 'error' in result or 'output' not in result:
            return []
        principles = result['output'].get('principles') or []
        results = []
        for p in principles[:count]:
            cross_refs = list(p.get('cross_refs') or [])
            insight = Insight(
                kind='wisdom',
                title=p['principle'][:100],
                body=f"**{p['principle']}**\n\n{p['rationale']}",
                citations=cross_refs,
                confidence=0.80,
                metadata={
                    'principle_tags': list(p.get('principle_tags') or []),
                    'cross_refs': cross_refs,
                },
            )
            persist(db, insight, session_id=session_id, source_tool=self.name)
            results.append(insight)
        return results

    def _default_llm(self):
        from llm_client import AnthropicClient
        from app_secrets import anthropic_api_key
        return AnthropicClient(api_key=anthropic_api_key())
