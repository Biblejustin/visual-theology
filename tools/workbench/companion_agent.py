"""Companion Agent — streaming Claude conversation loop for sermon study.

The companion is an engaged study partner + homiletical coach.
It knows what phase Bryan is in, what he's already done, and what time remains.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from companion_tools import execute_tool
from app_secrets import anthropic_api_key
from phase_cluster_router import persona_for_phase, persona_for_v2_phase
from persona_tools import tool_defs_for_persona


# ── Source Fidelity Rule (applies to ALL personas, including research) ────

SOURCE_FIDELITY_RULE = """## Source Fidelity (Always Active)

Never present a direct quotation — from a Church father, commentator, theologian, grammar, or confession — unless a retrieval tool returned it verbatim in this turn. If you don't have the exact text, say so plainly and describe the idea in your own words; do NOT invent attributed quotes. To quote a source found via search_library, first call read_library_section with its filename + article_num."""


# ── Language Discipline Rule (applies to ALL personas) ────────────────────
# Sibling to SOURCE_FIDELITY_RULE — both are "do not fabricate confidence"
# rules. Anti-sycophancy is epistemology, not tone. Per-persona augments
# live in persona_prompts.py as _LANGUAGE_AUGMENTS_{RESEARCH,COMPOSING,EDITING}.

LANGUAGE_DISCIPLINE_RULE = """## Language Discipline (Always Active)

Sycophancy is a truthfulness failure under social pressure — not a vocabulary
problem. Bryan built this tool to get a sharp colleague, not a yes-man. False
validation breaks the trust that makes your pushback worth anything.

### Three categories of acknowledgment

1. **Behavior acknowledgment** (neutral, structural). "Captured." "Saved."
   "Next?" Use when Bryan completes a unit.
2. **Process/effort validation** (cognitive labor with observable evidence).
   "You stayed in 13.6 for 18 minutes — the FCF you usually skip." "Three
   drafts of the Subject; this one names the agent and the assertion." Use
   when there is concrete work worth marking.
3. **Substantive agreement** — only after naming the basis (textual,
   confessional, homiletical). Never bare "you're right." Always "the text
   supports it because…" or "Wallace's section on objective genitive backs
   that." If you cannot name the basis, ask a question instead of agreeing.

### Banned: unearned global evaluation

Never use: brilliant, masterful, perfect, exactly right, spot-on, crystal
clear, golden, beautifully done, theological gold, you nailed it, that's
it!, YES! NOW you've got it, you've got it locked down.

Diagnostic adjectives are allowed when tied to a specific checkable claim:
"clear," "complete," "supported," "tracks with the text," "covers v.25 but
not v.26." These are diagnostic, not evaluative.

### Challenge-trigger protocol

When Bryan pushes back, do not immediately concede AND do not double down.
Re-evaluate. If he gives new evidence, stronger reasoning, or identifies a
missed source, revise plainly. If not, maintain the concern and explain
the narrow reason. Pattern:

    "I'd revise if X is true; otherwise my concern remains Y."

Steady is not stubborn. Mirroring Bryan's emotional certainty is sycophancy
in another costume — match confidence to evidence, not to his tone.

### Tool truth

Never say "saved," "locked," "committed," or "captured" unless a tool
actually returned success in this turn. If the tool failed or you didn't
call it, say so: "I haven't saved that — want me to?"

### Disagreement is a feature

If Bryan's claim is weak, name what is weak and ask one next question.
Withholding disagreement to keep the room warm is not warmth — it is
abandonment of the work he is paying you to do."""


# ── Homiletical Guardrails ────────────────────────────────────────────────
# NOTE: rule 6 below intentionally duplicates the body of SOURCE_FIDELITY_RULE.
# The duplication is deliberate so the coach-framed prompt keeps its existing
# all-caps "SOURCE FIDELITY" header (composing/editing tests + LLM-facing text
# stay unchanged). If you edit one, edit the other.

HOMILETICAL_GUARDRAILS = """
## Homiletical Guardrails (Always Active)

You are a homiletical coach, not just a study tool. These guardrails are ALWAYS active:

1. **The "So What?" Gate**: When Bryan adds an exegetical observation to the outline, ask: "So what does this mean for the person in the pew?" If he can't answer, it's a lecture point, not a sermon point.

2. **The Christ Thread**: Christ must be woven throughout the sermon, not tacked on at the end. If the outline has 3+ main points without a Christological connection, flag it: "Where is Christ in point II? Your congregation needs to see Him here, not just in the conclusion."

3. **Time Estimator**: Each main point with sub-points takes roughly 7-10 minutes to deliver. An outline with 4 main points = 30-40 minutes. Bryan's wife says 25-30 is the target. If the outline is getting long, say so directly: "This outline is tracking toward 35+ minutes. What can you cut?"

4. **Congregation Awareness**: Bryan preaches to real people — families, elderly, children, new believers. If a point is too academic, note it: "This is seminary-level. How would you say this to the single mom in the third row?"

5. **The Lecture Test**: If a section explains without applying, or analyzes without moving the heart, flag it as Chapell would: "This is a lecture, not a sermon. Where's the FCF?"

6. **SOURCE FIDELITY (always)**: Never present a direct quotation — from a Church father, commentator, theologian, grammar, or confession — unless a retrieval tool returned it verbatim in this turn. If you don't have the exact text, say so plainly and describe the idea in your own words; do NOT invent attributed quotes. To quote a source found via search_library, first call read_library_section with its filename + article_num.
"""


def _format_time(seconds):
    """Format seconds as human-readable time string."""
    if seconds <= 0:
        return "no time remaining"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours} hour{'s' if hours != 1 else ''} and {minutes} minute{'s' if minutes != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"




def _sse_event(event_type, data):
    """Format a server-sent event."""
    return f"data: {json.dumps({'type': event_type, **data})}\n\n"




def _format_outline_summary(tree, indent=0):
    """Format outline tree as indented text summary."""
    lines = []
    for node in tree:
        prefix = '  ' * indent
        type_marker = {
            'title': '#',
            'theme': '##',
            'main_point': 'I.',
            'sub_point': '-',
            'bullet': '*',
            'cross_ref': '[ref]',
            'note': '(note)',
        }.get(node.get('type', ''), '-')

        content = node.get('content', '')
        verse = f" ({node['verse_ref']})" if node.get('verse_ref') else ''
        lines.append(f"{prefix}{type_marker} {content}{verse}")

        if node.get('children'):
            lines.append(_format_outline_summary(node['children'], indent + 1))

    return '\n'.join(lines)






def select_persona_prompt(*, persona, passage, genre, session_elapsed_seconds,
                          current_phase, outline_summary, card_work_summary,
                          coaching_context,
                          prior_phase_summary='',
                          sermon_coach_pattern_summary='',
                          decision_summary='',
                          manuscript='', estimated_minutes=None,
                          active_commitment='', recent_coach_one_changes='',
                          prior_overruns_summary='',
                          recent_positive_feedback='',
                          target_length_committed=None,
                          elapsed_in_phase_seconds=0):
    """Build the system prompt for the active persona. Returns None for silent.

    Imports are local to avoid a circular import: persona_prompts imports
    HOMILETICAL_GUARDRAILS + _format_time from companion_agent.

    Every kwarg the cross-agent data layer (agent_data_provider) can
    emit must appear here, otherwise **extra_context unpacking will
    crash composing/editing prompt construction. See test_persona_kwarg_contract.
    """
    if persona == 'silent':
        return None
    from persona_prompts import (
        build_research_partner_prompt,
        build_composing_partner_prompt,
        build_editing_partner_prompt,
    )
    if persona == 'composing':
        return build_composing_partner_prompt(
            passage=passage, genre=genre,
            session_elapsed_seconds=session_elapsed_seconds,
            current_phase=current_phase,
            outline_summary=outline_summary,
            card_work_summary=card_work_summary,
            coaching_context=coaching_context,
            prior_phase_summary=prior_phase_summary,
            sermon_coach_pattern_summary=sermon_coach_pattern_summary,
            decision_summary=decision_summary,
            elapsed_in_phase_seconds=elapsed_in_phase_seconds,
        )
    if persona == 'editing':
        return build_editing_partner_prompt(
            passage=passage, genre=genre,
            session_elapsed_seconds=session_elapsed_seconds,
            current_phase=current_phase,
            manuscript=manuscript,
            estimated_minutes=estimated_minutes,
            active_commitment=active_commitment,
            recent_coach_one_changes=recent_coach_one_changes,
            prior_overruns_summary=prior_overruns_summary,
            recent_positive_feedback=recent_positive_feedback,
            target_length_committed=target_length_committed,
            elapsed_in_phase_seconds=elapsed_in_phase_seconds,
        )
    # default: research
    return build_research_partner_prompt(
        passage=passage, genre=genre,
        session_elapsed_seconds=session_elapsed_seconds,
        current_phase=current_phase,
        outline_summary=outline_summary,
        card_work_summary=card_work_summary,
        coaching_context=coaching_context,
        elapsed_in_phase_seconds=elapsed_in_phase_seconds,
    )



def _load_card_work_summary(db, session_id):
    """Concatenate Bryan's card responses, study-bible stars, and notepad
    into a single summary block for the system prompt."""
    parts = []
    for resp in db.get_card_responses(session_id):
        if resp.get("content"):
            parts.append(f"[{resp['phase']}] {resp['content']}")
    annotations = db.get_card_annotations(session_id)
    if annotations:
        parts.append("\n[Study Bible Stars]")
        for a in annotations:
            line = f"  ★ {a['source']}: \"{a['starred_text']}\""
            if a.get("note"):
                line += f" → {a['note']}"
            parts.append(line)
    notepad = db.get_card_notepad(session_id, "study_bibles")
    if notepad:
        parts.append(f"\n[Study Bible Notepad]\n{notepad}")
    return "\n".join(parts) if parts else ""


def stream_study_response(session_id, user_message, db, analytics=None,
                          model='claude-sonnet-4-20250514', accuser=None,
                          llm_client=None):
    """Generator yielding SSE events for conversation-first study streaming.

    Uses full conversation history (not phase-scoped) for natural multi-session flow.
    Streaming goes through llm_client.AnthropicClient so the tool-use loop is
    the SAME loop used by sermon_coach_agent + meta_coach_agent. Pass
    `llm_client` to inject a CannedLLMClient in tests.
    """
    api_key = anthropic_api_key()
    if api_key is None and llm_client is None:
        yield _sse_event('error', {'message': 'ANTHROPIC_API_KEY not set'})
        return

    if llm_client is None:
        from llm_client import AnthropicClient
        llm_client = AnthropicClient(api_key=api_key, model=model)

    session = db.get_session(session_id)
    if not session:
        yield _sse_event('error', {'message': 'Session not found'})
        return

    outline_tree = db.get_outline_tree(session_id)
    outline_summary = _format_outline_summary(outline_tree) if outline_tree else ''
    card_work_summary = _load_card_work_summary(db, session_id)

    # Coaching context for the system prompt (sermon coach memory bridge).
    from coaching_bridge import (
        load_active_commitment, load_coaching_insights,
        build_coaching_prompt_section,
    )
    coaching_context = build_coaching_prompt_section(
        load_active_commitment(db), load_coaching_insights(db))

    # Cross-agent data layer populates composing/editing-specific context.
    # pastor_feedback + session_decisions live on the app module; import
    # lazily so tests that don't initialize the app still work.
    from agent_data_provider import context_for_persona
    # v2_phase wins when set (review #4 picker); else fall back to legacy
    # current_phase routing for sessions created before the picker landed.
    from v2_phases import resolve_v2_phase
    v2_phase_row = resolve_v2_phase(session.get('v2_phase'),
                                    session.get('current_phase'))
    persona = persona_for_v2_phase(v2_phase_row.key)
    try:
        from app import pastor_feedback as _pf, session_decisions as _sd
    except ImportError:
        _pf = _sd = None
    extra_context = context_for_persona(db, session_id, persona,
                                        pastor_feedback_store=_pf,
                                        session_decision_store=_sd)

    # Elapsed-in-phase fuels the per-phase time gauge in the active-focus
    # block. Picker changes fire phase_events keyed by the v2 key, and
    # /card/next fires events keyed by the legacy current_phase. Look up
    # by the v2 key first (the picker is the more recent signal), fall
    # back to legacy if no v2 events have accumulated yet.
    elapsed_in_phase = 0
    if analytics:
        try:
            elapsed_in_phase = int(
                analytics.get_phase_time_seconds(session_id, v2_phase_row.key))
        except Exception:
            elapsed_in_phase = 0
        if elapsed_in_phase == 0 and session.get('current_phase'):
            try:
                elapsed_in_phase = int(
                    analytics.get_phase_time_seconds(session_id, session['current_phase']))
            except Exception:
                elapsed_in_phase = 0

    system_prompt = select_persona_prompt(
        persona=persona,
        passage=session['passage_ref'],
        genre=session['genre'],
        session_elapsed_seconds=session['total_elapsed_seconds'],
        current_phase=v2_phase_row.key,
        outline_summary=outline_summary,
        card_work_summary=card_work_summary,
        coaching_context=coaching_context,
        elapsed_in_phase_seconds=elapsed_in_phase,
        **extra_context,
    )
    if system_prompt is None:
        # Silent persona — forward-compat guard for future silent-phase
        # routing without a UI guard upstream.
        yield _sse_event('error', {'message': 'No chat surface in silent phase.'})
        return

    # Build messages from FULL conversation history (not phase-scoped).
    messages = []
    for m in db.get_messages(session_id, limit=100):
        if m['role'] in ('user', 'assistant') and m.get('content'):
            messages.append({'role': m['role'], 'content': m['content']})
    messages.append({'role': 'user', 'content': user_message})

    # Save user message + accuser-voice detection (Cycle 2a: log-only).
    mid = db.save_message(session_id, 'study', 'user', user_message)
    if accuser is not None:
        try:
            accuser.analyze_message(
                session_id=session_id, message_id=mid,
                surface='study', text=user_message)
        except Exception:
            pass  # detection is observational; never block the user flow

    session_context = {
        'session_id': session_id,
        'passage_ref': session['passage_ref'],
        'book': session['book'],
        'chapter': session['chapter'],
        'verse_start': session['verse_start'],
        'verse_end': session['verse_end'],
        'genre': session['genre'],
        'phase': session['current_phase'],
        'db': db,
    }

    tool_defs = tool_defs_for_persona(persona)
    full_response_text = ''

    # Tool-use loop. Same shape as sermon_coach_agent + meta_coach_agent —
    # llm_client.stream_message normalizes the SDK's content_block_*
    # events into typed dicts so callers don't repeat the SDK plumbing.
    MAX_TOOL_ROUNDS = 8
    for _round in range(MAX_TOOL_ROUNDS):
        tool_use_blocks = []
        stop_reason = 'end_turn'
        content = []
        try:
            for event in llm_client.stream_message(
                    system=system_prompt, messages=messages, tools=tool_defs):
                if event['type'] == 'text_delta':
                    full_response_text += event.get('text', '')
                    yield _sse_event('text_delta', {'text': event['text']})
                elif event['type'] == 'tool_use':
                    yield _sse_event('tool_start', {'name': event['tool_name']})
                    result = execute_tool(event['tool_name'], event['tool_input'],
                                          session_context)
                    tool_use_blocks.append({'tool': event, 'result': result})
                    yield _sse_event('tool_result', {
                        'name': event['tool_name'], 'result': result,
                    })
                    if event['tool_name'] == 'save_to_outline' and analytics:
                        analytics.record_outline_event(
                            session_id, 'add',
                            event['tool_input'].get('node_type', 'note'))
                elif event['type'] == 'message_complete':
                    stop_reason = event.get('stop_reason', 'end_turn')
                    content = event.get('content', [])
                elif event['type'] == 'error':
                    yield _sse_event('error', {'message': event.get('error', 'unknown')})
                    return
        except Exception as e:
            yield _sse_event('error', {'message': str(e)})
            break

        if stop_reason != 'tool_use' or not tool_use_blocks:
            break

        messages.append({'role': 'assistant', 'content': content})
        tool_results = []
        for block in tool_use_blocks:
            tool_results.append({
                'type': 'tool_result',
                'tool_use_id': block['tool']['tool_use_id'],
                'content': (json.dumps(block['result'])
                            if isinstance(block['result'], dict)
                            else str(block['result'])),
            })
        messages.append({'role': 'user', 'content': tool_results})

    if full_response_text:
        db.save_message(session_id, 'study', 'assistant', full_response_text)
        if analytics:
            analytics.record_message(session_id, 'assistant', len(full_response_text))

    yield _sse_event('done', {})
