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


_OBJECTION_CLASSES = ('theological', 'pastoral', 'cultural', 'honest_doubt')

_OBJECTION_SCHEMA = {
    'type': 'object',
    'properties': {
        'objections': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'objection_class': {'type': 'string', 'enum': list(_OBJECTION_CLASSES)},
                    'objection': {'type': 'string'},
                    'rebuttal_pointer': {'type': 'string'},
                },
                'required': ['objection_class', 'objection', 'rebuttal_pointer'],
            },
        },
    },
    'required': ['objections'],
}

_OBJECTION_SYSTEM = (
    "List likely objections a congregation might raise to this claim, drawn from "
    "four categories:\n"
    "  - theological: doctrinal pushback from non-Reformed positions\n"
    "  - pastoral: 'but what if I/my situation...' (felt-need objections)\n"
    "  - cultural: modern skepticism, scientific naturalism, moral intuitions\n"
    "  - honest_doubt: thoughtful Christian wrestling that deserves charity\n"
    "For each: state the objection in the objector's voice (one sentence), then "
    "give a one-line rebuttal pointer (not a full answer — a direction to take in "
    "preaching). Distribute across categories where realistic."
)


class ObjectionFinder:
    """Find likely objections to a claim. Combines library search + LLM taxonomy."""

    name = 'find_objections'

    def __init__(self, searcher=None, llm=None):
        self._searcher = searcher
        self._llm = llm

    def run(self, db, *, session_id: int, claim: str, count: int = 3,
            passage: dict = None) -> list:
        if not claim:
            return []
        llm_objections = self._llm_objections(claim, count)
        # Reserve at least 60% for LLM-generated; allow a few from library if room.
        llm_min = max(1, int(count * 0.6))
        library_cap = max(0, count - llm_min)
        from_library = self._library_objections(claim, library_cap) if library_cap else []
        llm_take = max(llm_min, count - len(from_library))
        from_llm = llm_objections[:llm_take]
        combined = from_llm + from_library
        results = []
        for o in combined[:count]:
            insight = Insight(
                kind='objection',
                title=f"[{o.get('objection_class', '?')}] {o['objection'][:80]}",
                body=f"**Objection:** {o['objection']}\n\n**Direction:** {o['rebuttal_pointer']}",
                citations=o.get('citations', []),
                confidence=o.get('confidence', 0.75),
                metadata={
                    'objection_class': o.get('objection_class'),
                    'source': o.get('source', 'llm'),
                },
            )
            persist(db, insight, session_id=session_id, source_tool=self.name)
            results.append(insight)
        return results

    def _llm_objections(self, claim: str, count: int) -> list:
        llm = self._llm or self._default_llm()
        result = llm.call(
            prompt=f"Claim: {claim}\nProduce up to {count*2} objections.",
            schema=_OBJECTION_SCHEMA, system=_OBJECTION_SYSTEM,
            tool_name='emit_objections',
            tool_description='Emit objections by category.',
        )
        if 'error' in result or 'output' not in result:
            return []
        return [{**o, 'source': 'llm'} for o in (result['output'].get('objections') or [])]

    def _library_objections(self, claim: str, count: int) -> list:
        if count <= 0:
            return []
        searcher = self._searcher or self._default_searcher()
        try:
            response = searcher.search(
                f"objections to {claim}", limit=count * 3,
                resource_type_prefix='text.monograph',
            )
        except Exception:
            return []
        results = []
        for c in (response.get('results') or [])[:count]:
            section = (c.get('sections') or [{}])[0]
            results.append({
                'objection_class': 'theological',
                'objection': f"From {c.get('title', '?')}: {(section.get('snippet') or '')[:200]}",
                'rebuttal_pointer': 'See the cited resource for the author\'s treatment.',
                'citations': [c.get('resource_id')] if c.get('resource_id') else [],
                'source': 'library',
                'confidence': 0.6,
            })
        return results

    def _default_searcher(self):
        from logos_search import get_searcher
        return get_searcher()

    def _default_llm(self):
        from llm_client import AnthropicClient
        from app_secrets import anthropic_api_key
        return AnthropicClient(api_key=anthropic_api_key())


_COUNSELING_TIERS = ('preventative', 'remedial', 'diagnostic')

_COUNSELING_SCHEMA = {
    'type': 'object',
    'properties': {
        'applications': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'pastoral_situation': {'type': 'string'},
                    'application': {'type': 'string'},
                    'tier': {'type': 'string', 'enum': list(_COUNSELING_TIERS)},
                    'cross_refs': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['pastoral_situation', 'application', 'tier'],
            },
        },
    },
    'required': ['applications'],
}

_COUNSELING_SYSTEM = (
    "Map this passage to common pastoral-counseling situations the preacher might "
    "address. For each: name the situation in one or two words "
    "(e.g. 'anxiety', 'marriage_conflict', 'guilt', 'addiction', 'grief', 'doubt', "
    "'self_righteousness'), give a one-paragraph application targeted to someone in "
    "that situation, tag the tier:\n"
    "  - preventative: forming healthy patterns before crisis\n"
    "  - remedial: healing/restoration where there's already damage\n"
    "  - diagnostic: helping the person recognize the issue\n"
    "and give 1-2 cross-references from Scripture or biblical-counseling literature."
)


class CounselingApplications:
    """Map a passage to common pastoral-counseling situations."""

    name = 'find_counseling_applications'

    def __init__(self, llm=None):
        self._llm = llm

    def run(self, db, *, session_id: int, passage: dict, count: int = 3) -> list:
        llm = self._llm or self._default_llm()
        result = llm.call(
            prompt=f"Passage: {passage.get('passage_ref', '?')}\nRequested applications: {count}",
            schema=_COUNSELING_SCHEMA, system=_COUNSELING_SYSTEM,
            tool_name='emit_counseling_applications',
            tool_description='Emit pastoral-counseling applications.',
        )
        if 'error' in result or 'output' not in result:
            return []
        apps = result['output'].get('applications') or []
        results = []
        for a in apps[:count]:
            cross_refs = list(a.get('cross_refs') or [])
            insight = Insight(
                kind='counseling',
                title=f"[{a['tier']}] {a['pastoral_situation']}",
                body=a['application'],
                citations=cross_refs,
                confidence=0.80,
                metadata={
                    'pastoral_situation': a['pastoral_situation'],
                    'tier': a['tier'],
                },
            )
            persist(db, insight, session_id=session_id, source_tool=self.name)
            results.append(insight)
        return results

    def _default_llm(self):
        from llm_client import AnthropicClient
        from app_secrets import anthropic_api_key
        return AnthropicClient(api_key=anthropic_api_key())


_HOMILETICAL_TAGS = (
    'exegetical_claim', 'application', 'illustration', 'christ_thread',
    'question', 'transition', 'other',
)

_BALANCE_TAG_SCHEMA = {
    'type': 'object',
    'properties': {
        'tags': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'node_id': {'type': 'integer'},
                    'homiletical_type': {'type': 'string', 'enum': list(_HOMILETICAL_TAGS)},
                },
                'required': ['node_id', 'homiletical_type'],
            },
        },
    },
    'required': ['tags'],
}

_BALANCE_TAG_SYSTEM = (
    "Classify each outline node as one of: exegetical_claim (an exposition / "
    "textual observation), application (so-what for the listener), illustration "
    "(a story, image, anecdote), christ_thread (a Christ-centered angle), "
    "question (rhetorical or pastoral), transition (connective tissue), other. "
    "Use your best single label."
)


class BalanceGauger:
    """Tag outline nodes and report exegesis-vs-rest balance."""

    name = 'gauge_outline_balance'

    def __init__(self, llm=None):
        self._llm = llm

    def run(self, db, *, session_id: int) -> list:
        nodes = self._load_nodes(db, session_id)
        if not nodes:
            return []
        stale = [n for n in nodes if self._is_stale(n)]
        if stale:
            self._retag(db, stale)
            nodes = self._load_nodes(db, session_id)
        counts = self._counts(nodes)
        total = sum(counts.values()) or 1
        ratio_exegetical = counts.get('exegetical_claim', 0) / total
        summary = Insight(
            kind='balance_flag',
            title=f"Outline balance: {int(ratio_exegetical * 100)}% exegetical",
            body=self._summary_body(counts, total),
            citations=[],
            confidence=1.0,
            metadata={
                'summary': True,
                'ratio_exegetical': round(ratio_exegetical, 3),
                'total_nodes': total,
                'tag_counts': counts,
            },
        )
        persist(db, summary, session_id=session_id, source_tool=self.name)
        flags = [summary]
        if ratio_exegetical >= 0.9 and total >= 3:
            flag = Insight(
                kind='balance_flag',
                title='Outline is almost entirely exegetical',
                body=(f"{counts.get('exegetical_claim', 0)} of {total} nodes are "
                      f"exegetical. Consider adding application, illustration, or "
                      f"a Christ-thread."),
                citations=[],
                confidence=0.95,
                metadata={'flag': 'all_exegetical',
                          'ratio_exegetical': round(ratio_exegetical, 3)},
            )
            persist(db, flag, session_id=session_id, source_tool=self.name)
            flags.append(flag)
        elif ratio_exegetical <= 0.1 and total >= 3 and counts.get('application', 0) >= 1:
            flag = Insight(
                kind='balance_flag',
                title='Outline has little exegetical grounding',
                body=('Application without rooting in the text can drift. '
                      'Consider adding exegetical anchors per section.'),
                citations=[],
                confidence=0.85,
                metadata={'flag': 'thin_exegesis',
                          'ratio_exegetical': round(ratio_exegetical, 3)},
            )
            persist(db, flag, session_id=session_id, source_tool=self.name)
            flags.append(flag)
        return flags

    def _load_nodes(self, db, session_id: int) -> list:
        conn = db._conn()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT id, content, type, updated_at, homiletical_type, "
                "homiletical_type_at FROM outline_nodes WHERE session_id = ?",
                (session_id,)).fetchall()]
        finally:
            conn.close()

    def _is_stale(self, node: dict) -> bool:
        if not node.get('homiletical_type'):
            return True
        tag_at = node.get('homiletical_type_at')
        if not tag_at:
            return True
        return node.get('updated_at') and node['updated_at'] > tag_at

    def _retag(self, db, nodes: list):
        llm = self._llm or self._default_llm()
        prompt_lines = ['Outline nodes to classify:', '']
        for i, n in enumerate(nodes):
            prompt_lines.append(f"Node {i} [{n['id']}]: {n['content']}")
        prompt = "\n".join(prompt_lines)
        result = llm.call(
            prompt=prompt, schema=_BALANCE_TAG_SCHEMA, system=_BALANCE_TAG_SYSTEM,
            tool_name='emit_node_tags',
            tool_description='Classify outline nodes by homiletical type.',
        )
        if 'error' in result or 'output' not in result:
            return
        tags = result['output'].get('tags') or []
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn = db._conn()
        try:
            for t in tags:
                conn.execute(
                    "UPDATE outline_nodes SET homiletical_type = ?, "
                    "homiletical_type_at = ? WHERE id = ?",
                    (t['homiletical_type'], now, t['node_id']),
                )
            conn.commit()
        finally:
            conn.close()

    def _counts(self, nodes: list) -> dict:
        counts = {tag: 0 for tag in _HOMILETICAL_TAGS}
        for n in nodes:
            t = n.get('homiletical_type') or 'other'
            counts[t] = counts.get(t, 0) + 1
        return {k: v for k, v in counts.items() if v > 0}

    def _summary_body(self, counts: dict, total: int) -> str:
        lines = [f"Total nodes: {total}"]
        for tag, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            pct = int(100 * n / total)
            lines.append(f"  {tag}: {n} ({pct}%)")
        return "\n".join(lines)

    def _default_llm(self):
        from llm_client import AnthropicClient
        from app_secrets import anthropic_api_key
        return AnthropicClient(api_key=anthropic_api_key())


class CoherenceChecker:
    """Find prior outline claims similar to a new claim, via embeddings."""

    name = 'check_sermon_coherence'
    SOURCE_KIND = 'outline_node'

    def __init__(self, embeddings=None, similarity_threshold: float = 0.75,
                 top_k: int = 3, model_name: str = None):
        self._emb = embeddings
        self._threshold = similarity_threshold
        self._top_k = top_k
        self._model_name_override = model_name

    def run(self, db, *, session_id: int, claim: str) -> list:
        if not claim or not claim.strip():
            return []
        emb = self._emb or self._default_embeddings()
        model_name = self._model_name_override or self._infer_model_name(emb)

        # Embed the incoming claim once.
        query_vec = emb.embed_one(claim)

        # Find all candidate outline_nodes from OTHER sessions.
        candidates = self._load_candidate_nodes(db, exclude_session_id=session_id)
        if not candidates:
            return []

        # Embed any candidates we haven't embedded yet (lazy backfill).
        cached = {r['source_id']: r['vector'] for r in db.get_claim_embeddings(
            source_kind=self.SOURCE_KIND, model=model_name,
            source_ids=[c['id'] for c in candidates],
        )}
        to_embed = [c for c in candidates if c['id'] not in cached]
        if to_embed:
            vecs = emb.embed([c['content'] for c in to_embed])
            for cand, vec in zip(to_embed, vecs):
                db.save_claim_embedding(
                    source_kind=self.SOURCE_KIND, source_id=cand['id'],
                    claim_text=cand['content'], vector=vec, model=model_name,
                )
                cached[cand['id']] = vec

        scored = []
        for c in candidates:
            v = cached.get(c['id'])
            if not v:
                continue
            sim = self._cosine(query_vec, v)
            if sim >= self._threshold:
                scored.append((sim, c))
        scored.sort(key=lambda sc: -sc[0])

        results = []
        for sim, c in scored[:self._top_k]:
            insight = Insight(
                kind='coherence_flag',
                title=f"Similar prior claim (sim {sim:.2f})",
                body=f"**This session's claim:** {claim}\n\n**Prior:** {c['content']}",
                citations=[f"session:{c['session_id']}", f"outline_node:{c['id']}"],
                confidence=float(sim),
                metadata={
                    'similarity': round(float(sim), 4),
                    'prior_session_id': c['session_id'],
                    'prior_node_id': c['id'],
                },
            )
            persist(db, insight, session_id=session_id, source_tool=self.name)
            results.append(insight)
        return results

    def _load_candidate_nodes(self, db, exclude_session_id: int) -> list:
        conn = db._conn()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT id, session_id, content FROM outline_nodes "
                "WHERE session_id != ? AND content IS NOT NULL AND length(content) > 0",
                (exclude_session_id,)).fetchall()]
        finally:
            conn.close()

    def _cosine(self, a: list, b: list) -> float:
        import math
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(x * x for x in b)) or 1.0
        return dot / (na * nb)

    def _default_embeddings(self):
        from embeddings_client import LocalEmbeddingsClient
        return LocalEmbeddingsClient()

    def _infer_model_name(self, emb) -> str:
        # LocalEmbeddingsClient exposes _model_name; CannedEmbeddingsClient has none.
        return getattr(emb, '_model_name', 'canned-test')
