"""Canonical 16-phase workflow metadata (review #4 substrate).

The Christ-Centered Sermon Prep workflow is 16 distinct phases. Each has
its own persona, time budget, and posture (how the LLM should behave).
This module is the single source of truth for that metadata — the router,
the UI picker, the persona prompts, and the Time Coach all key off these
constants.

Old phase strings ('prayer', 'text_work', 'context', etc.) still exist
in the DB for backwards compatibility. _LEGACY_TO_V2 maps old→new so
existing sessions keep routing correctly.

Source: docs/superpowers/specs/2026-05-18-sermon-prep-agent-architecture-design-v2.md
"""

from collections import namedtuple


V2Phase = namedtuple('V2Phase', [
    'key',           # canonical id, e.g. 'p06_context'
    'number',        # 1..16
    'name',          # human-readable
    'persona',       # silent | research | composing | editing
    'budget_min',    # time budget in minutes (None = open-ended)
    'posture',       # one-line cue for the LLM
])


V2_PHASES = (
    # Phase 1-3 — silent (no LLM presence)
    V2Phase('p01_prayer',          1,  'Prayer',                        'silent',    15,  'Heart preparation. No LLM presence.'),
    V2Phase('p02_read_translate',  2,  'Read & Translate',              'silent',    120, 'Wrestle with the original text alone.'),
    V2Phase('p03_digestion',       3,  'Digestion',                     'silent',    60,  'Pray through phrase by phrase. No analysis yet.'),

    # Phase 4-12 — Research Partner
    V2Phase('p04_exegesis',        4,  'Exegesis',                      'research',  60,  'Full RA + interlocutor mode. Doctoral RA, not BA sophomore.'),
    V2Phase('p05_quick_answers',   5,  'Quick Answers (Study Bibles)',  'research',  30,  'Silent unless invited. Dump notes with citations. Stop.'),
    V2Phase('p06_context',         6,  'Context Questions',             'research',  15,  'Proactive on cultural / geographic backgrounds.'),
    V2Phase('p07_big_picture',     7,  'Big Picture',                   'research',  30,  'Canonical / structural observations. Wrestle with argument flow.'),
    V2Phase('p08_christ',          8,  'Identify Christ',               'research',  30,  'Provide + provoke after Bryan goes first. 7 lenses.'),
    V2Phase('p09_systematics',     9,  'Systematic & Biblical Theology','research',  30,  'Anti-paralysis curator. Cross-tradition + heresy guard.'),
    V2Phase('p10_patristics',      10, "Don't Live in a Historical Vacuum", 'research', 30, 'Sanity check + bolster + rabbit-hole guard.'),
    V2Phase('p11_confessions',     11, 'Confessions',                   'research',  15,  'WCF / WLC / WSC proactive. Flag tensions.'),
    V2Phase('p12_commentaries',    12, 'Commentaries',                  'research',  30,  'Aggressive curator. 5 distinctive voices, not 5 Reformed conservatives.'),

    # Phase 13-14 — Composing Partner
    V2Phase('p13_sermon_work',     13, 'Sermon Work',                   'composing', 90,  'Procrastination zone. Pull Bryan into the work via questions.'),
    V2Phase('p14_sermon_writing',  14, 'Formal Sermon Writing',         'composing', 240, 'Socratic interview. Never composes; elicits.'),

    # Phase 15-16 — Editing Partner
    V2Phase('p15_finishing',       15, 'Finishing Questions',           'editing',   45,  'Auto-audit on entry. Surface findings with affirmation alongside.'),
    V2Phase('p16_ruthless_edit',   16, 'Ruthless Edit',                 'editing',   60,  '4-pass evidence-based edit. Surface candidate cuts; Bryan decides.'),
)


V2_PHASE_KEYS = tuple(p.key for p in V2_PHASES)
_BY_KEY = {p.key: p for p in V2_PHASES}
_BY_NUMBER = {p.number: p for p in V2_PHASES}


def get_phase(key_or_number):
    """Look up by key string OR by integer number 1..16. Returns None if
    not found — callers should treat absence as 'no v2 phase set'."""
    if isinstance(key_or_number, int):
        return _BY_NUMBER.get(key_or_number)
    if isinstance(key_or_number, str):
        return _BY_KEY.get(key_or_number)
    return None


# ── Legacy → v2 mapping ───────────────────────────────────────────────
#
# Old DB phase strings → v2 phase keys. Many-to-one in places (e.g. the
# old 'theological' phase covered v2 Phases 8-11). When we map old→v2 we
# pick the most representative phase; the user can refine via the UI
# picker. This mapping powers (a) the schema backfill at startup, (b)
# the runtime fallback inside resolve_v2_phase().

_LEGACY_TO_V2 = {
    'prayer':              'p01_prayer',
    'read_translate':      'p02_read_translate',
    'text_work':           'p02_read_translate',   # /companion/ legacy name
    'translation':         'p02_read_translate',
    'read_text':           'p02_read_translate',
    'digestion':           'p03_digestion',
    'observation':         'p04_exegesis',
    'word_study':          'p04_exegesis',         # word study lives under exegesis
    'study_bibles':        'p05_quick_answers',
    'context':             'p06_context',
    'theological':         'p09_systematics',      # covers 8-11; ST/BT is the typical first stop
    'commentary':          'p12_commentaries',
    'exegetical_point':    'p13_sermon_work',
    'fcf_homiletical':     'p13_sermon_work',
    'sermon_construction': 'p14_sermon_writing',
    'edit_pray':           'p16_ruthless_edit',
    'study':               'p04_exegesis',         # /study/ catch-all → exegesis default
}


def resolve_v2_phase(v2_phase, legacy_phase):
    """Pick the v2 phase to use. Explicit v2_phase wins; else map from
    legacy; else default to p04_exegesis (matches the old router's
    research-leaning catch-all)."""
    if v2_phase and v2_phase in _BY_KEY:
        return _BY_KEY[v2_phase]
    if legacy_phase and legacy_phase in _LEGACY_TO_V2:
        return _BY_KEY[_LEGACY_TO_V2[legacy_phase]]
    return _BY_KEY['p04_exegesis']


def phases_by_persona():
    """Return {persona: [V2Phase, ...]} for UI grouping in the picker."""
    out = {'silent': [], 'research': [], 'composing': [], 'editing': []}
    for p in V2_PHASES:
        out[p.persona].append(p)
    return out
