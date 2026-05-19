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
from phase_cluster_router import persona_for_phase
from persona_tools import tool_defs_for_persona


# ── Homiletical Guardrails ────────────────────────────────────────────────

HOMILETICAL_GUARDRAILS = """
## Homiletical Guardrails (Always Active)

You are a homiletical coach, not just a study tool. These guardrails are ALWAYS active:

1. **The "So What?" Gate**: When Bryan adds an exegetical observation to the outline, ask: "So what does this mean for the person in the pew?" If he can't answer, it's a lecture point, not a sermon point.

2. **The Christ Thread**: Christ must be woven throughout the sermon, not tacked on at the end. If the outline has 3+ main points without a Christological connection, flag it: "Where is Christ in point II? Your congregation needs to see Him here, not just in the conclusion."

3. **Time Estimator**: Each main point with sub-points takes roughly 7-10 minutes to deliver. An outline with 4 main points = 30-40 minutes. Bryan's wife says 25-30 is the target. If the outline is getting long, say so directly: "This outline is tracking toward 35+ minutes. What can you cut?"

4. **Congregation Awareness**: Bryan preaches to real people — families, elderly, children, new believers. If a point is too academic, note it: "This is seminary-level. How would you say this to the single mom in the third row?"

5. **The Lecture Test**: If a section explains without applying, or analyzes without moving the heart, flag it as Chapell would: "This is a lecture, not a sermon. Where's the FCF?"
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
                          manuscript='', estimated_minutes=None,
                          active_commitment='', recent_coach_one_changes='',
                          prior_overruns_summary=''):
    """Build the system prompt for the active persona. Returns None for silent.

    Imports are local to avoid a circular import: persona_prompts imports
    HOMILETICAL_GUARDRAILS + _format_time from companion_agent.
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
        )
    # default: research
    return build_research_partner_prompt(
        passage=passage, genre=genre,
        session_elapsed_seconds=session_elapsed_seconds,
        current_phase=current_phase,
        outline_summary=outline_summary,
        card_work_summary=card_work_summary,
        coaching_context=coaching_context,
    )


def stream_study_response(session_id, user_message, db, analytics=None,
                          model='claude-sonnet-4-20250514', accuser=None):
    """Generator yielding SSE events for conversation-first study streaming.

    Uses full conversation history (not phase-scoped) for natural multi-session flow.
    """
    try:
        import anthropic
    except ImportError:
        yield _sse_event('error', {'message': 'Anthropic SDK not installed. Run: pip install anthropic'})
        return

    api_key = anthropic_api_key()
    if not api_key:
        yield _sse_event('error', {'message': 'ANTHROPIC_API_KEY not set'})
        return

    client = anthropic.Anthropic(api_key=api_key)

    # Load session
    session = db.get_session(session_id)
    if not session:
        yield _sse_event('error', {'message': 'Session not found'})
        return

    # Build outline summary
    outline_tree = db.get_outline_tree(session_id)
    outline_summary = _format_outline_summary(outline_tree) if outline_tree else ''

    # Load Bryan's card-phase work for the system prompt
    card_responses = db.get_card_responses(session_id)
    card_annotations = db.get_card_annotations(session_id)
    card_notepad = db.get_card_notepad(session_id, "study_bibles")

    card_parts = []
    for resp in card_responses:
        if resp.get("content"):
            card_parts.append(f"[{resp['phase']}] {resp['content']}")
    if card_annotations:
        card_parts.append("\n[Study Bible Stars]")
        for a in card_annotations:
            line = f"  \u2605 {a['source']}: \"{a['starred_text']}\""
            if a.get("note"):
                line += f" \u2192 {a['note']}"
            card_parts.append(line)
    if card_notepad:
        card_parts.append(f"\n[Study Bible Notepad]\n{card_notepad}")
    card_work_summary = "\n".join(card_parts) if card_parts else ""

    # Load coaching context for the study prompt
    from coaching_bridge import load_active_commitment, load_coaching_insights, build_coaching_prompt_section
    commitment = load_active_commitment(db)
    insights = load_coaching_insights(db)
    coaching_context = build_coaching_prompt_section(commitment, insights)

    # Build system prompt via the phase-cluster router. Cross-agent data layer
    # (agent_data_provider) populates the composing/editing-specific context
    # fields (rut history, manuscript, length estimate, active commitment, etc.)
    # from companion.db. Research persona uses defaults.
    from agent_data_provider import context_for_persona
    persona = persona_for_phase(session['current_phase'])
    # pastor_feedback lives in app.py as a module global; import lazily to
    # avoid circular import and degrade gracefully if not initialized.
    try:
        from app import pastor_feedback as _pf, session_decisions as _sd
    except ImportError:
        _pf = _sd = None
    extra_context = context_for_persona(db, session_id, persona,
                                        pastor_feedback_store=_pf,
                                        session_decision_store=_sd)
    system_prompt = select_persona_prompt(
        persona=persona,
        passage=session['passage_ref'],
        genre=session['genre'],
        session_elapsed_seconds=session['total_elapsed_seconds'],
        current_phase=session['current_phase'],
        outline_summary=outline_summary,
        card_work_summary=card_work_summary,
        coaching_context=coaching_context,
        **extra_context,
    )
    if system_prompt is None:
        # Silent persona — forward-compat guard. Today /study/'s catch-all
        # 'study' phase routes to research; this branch is unreachable in
        # current data but protects against future silent-phase routing
        # into stream_study_response without a UI guard upstream.
        yield _sse_event('error', {'message': 'No chat surface in silent phase.'})
        return

    # Build messages from FULL conversation history (not phase-scoped)
    all_messages = db.get_messages(session_id, limit=100)
    messages = []
    for m in all_messages:
        if m['role'] == 'user' and m.get('content'):
            messages.append({'role': 'user', 'content': m['content']})
        elif m['role'] == 'assistant' and m.get('content'):
            messages.append({'role': 'assistant', 'content': m['content']})

    # Add current user message
    messages.append({'role': 'user', 'content': user_message})

    # Save user message + accuser-voice detection (Cycle 2a: log-only)
    mid = db.save_message(session_id, 'study', 'user', user_message)
    if accuser is not None:
        try:
            accuser.analyze_message(
                session_id=session_id, message_id=mid,
                surface='study', text=user_message)
        except Exception:
            pass  # detection is observational; never block the user flow

    # Session context for tool execution
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

    # Streaming loop with tool use
    full_response_text = ''

    while True:
        tool_use_blocks = []
        current_tool = None

        try:
            with client.messages.stream(
                model=model,
                max_tokens=4096,
                system=system_prompt,
                messages=messages,
                tools=tool_defs,
            ) as stream:
                for event in stream:
                    if event.type == 'content_block_start':
                        if hasattr(event.content_block, 'type') and event.content_block.type == 'tool_use':
                            current_tool = {
                                'id': event.content_block.id,
                                'name': event.content_block.name,
                                'input_json': ''
                            }
                            yield _sse_event('tool_start', {'name': event.content_block.name})
                    elif event.type == 'content_block_delta':
                        if hasattr(event.delta, 'text'):
                            full_response_text += event.delta.text
                            yield _sse_event('text_delta', {'text': event.delta.text})
                        elif hasattr(event.delta, 'partial_json'):
                            if current_tool:
                                current_tool['input_json'] += event.delta.partial_json
                    elif event.type == 'content_block_stop':
                        if current_tool:
                            try:
                                tool_input = json.loads(current_tool['input_json'])
                            except json.JSONDecodeError:
                                tool_input = {}
                            result = execute_tool(current_tool['name'], tool_input, session_context)
                            tool_use_blocks.append((current_tool, result))
                            yield _sse_event('tool_result', {
                                'name': current_tool['name'],
                                'result': result
                            })
                            # Track outline saves
                            if current_tool['name'] == 'save_to_outline' and analytics:
                                analytics.record_outline_event(session_id, 'add',
                                                              tool_input.get('node_type', 'note'))
                            current_tool = None

                response = stream.get_final_message()

        except Exception as e:
            yield _sse_event('error', {'message': str(e)})
            break

        if response.stop_reason == 'tool_use':
            messages.append({'role': 'assistant', 'content': response.content})
            tool_results = []
            for tool, result in tool_use_blocks:
                tool_results.append({
                    'type': 'tool_result',
                    'tool_use_id': tool['id'],
                    'content': json.dumps(result) if isinstance(result, dict) else str(result)
                })
            messages.append({'role': 'user', 'content': tool_results})
        else:
            break

    # Save assistant response
    if full_response_text:
        db.save_message(session_id, 'study', 'assistant', full_response_text)
        if analytics:
            analytics.record_message(session_id, 'assistant', len(full_response_text))

    yield _sse_event('done', {})
