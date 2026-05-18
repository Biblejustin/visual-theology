"""Phase cluster router (Cycle 3b substrate).

Maps each known phase string to one of four personas. Used by the
upcoming Research/Composing/Editing Partner refactor — system prompts and
tool lists will swap based on persona_for_phase(session.current_phase).

For now this is foundation only. Existing /study/ behavior is unchanged
(catch-all phase 'study' → 'research', matching current Study TA behavior).

Personas:
  - silent      → workflow Phases 1-3 (Pray / Translate / Digest). No
                  AI presence; chat box not rendered.
  - research    → workflow Phases 4-12 (exegesis through commentaries).
                  Research-GA + interlocutor + manuscript-margin reviewer.
  - composing   → workflow Phases 13-14 (Sermon Work, Sermon Writing).
                  Socratic interview-driven composition.
  - editing     → workflow Phases 15-16 (Finishing Questions, Ruthless Edit).
                  Auto-audit + 4-pass ruthless edit.

See `docs/superpowers/specs/2026-05-18-sermon-prep-agent-architecture-design-v2.md`
§3 + §4 + memories [[architecture-four-agents]] and [[reference-sermon-prep-workflow]].
"""

PERSONAS = frozenset({'silent', 'research', 'composing', 'editing'})

PHASE_PERSONA = {
    # ── Silent: heart / wrestling / translation phases ──────────────────
    'prayer':         'silent',  # /study/ + /companion/ (workflow Ph 1)
    'read_translate': 'silent',  # /study/ (workflow Ph 2)
    'text_work':      'silent',  # /companion/ (workflow Ph 2 translate)
    'translation':    'silent',  # observed in live DB
    'read_text':      'silent',  # observed in live DB
    'digestion':      'silent',  # /study/ + /companion/ (workflow Ph 3)

    # ── Research: exegesis through commentaries ─────────────────────────
    'study_bibles':   'research',  # /study/ Ph 5 (Quick Answers)
    'observation':    'research',  # /companion/ Ph 4 (Exegesis)
    'word_study':     'research',  # /companion/ word-study
    'context':        'research',  # /companion/ Ph 6 (Context Questions)
    'theological':    'research',  # /companion/ Ph 9 (ST/BT)
    'commentary':     'research',  # /companion/ Ph 12 (Commentaries)

    # ── Composing: sermon work + sermon writing ─────────────────────────
    'exegetical_point':    'composing',  # /companion/ Ph 13.3 (EP/OTM)
    'fcf_homiletical':     'composing',  # /companion/ Ph 13.6 + 13.7
    'sermon_construction': 'composing',  # /companion/ Ph 14 (Sermon Writing)

    # ── Editing: finishing + ruthless edit ──────────────────────────────
    'edit_pray':      'editing',         # /companion/ Ph 16 (Ruthless Edit)

    # ── /study/ conversation-mode catch-all ─────────────────────────────
    # The /study/ surface uses a single 'study' phase for the conversation
    # mode. Until per-phase tracking is introduced inside conversation,
    # this defaults to research (matches the current Study TA behavior).
    'study':          'research',
}

# Safe default — matches the current pre-router Study TA behavior so that
# unknown phase strings don't accidentally silence the AI.
DEFAULT_PERSONA = 'research'


def persona_for_phase(phase):
    """Return the persona for a given phase string. Unknown or empty inputs
    fall back to DEFAULT_PERSONA (research) — never accidentally silent."""
    if not phase:
        return DEFAULT_PERSONA
    return PHASE_PERSONA.get(phase, DEFAULT_PERSONA)
