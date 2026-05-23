import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from persona_prompts import build_research_partner_prompt


def _research_prompt(phase='p10_patristics'):
    # Signature (persona_prompts.py:281): keyword-only —
    # passage, genre, session_elapsed_seconds, current_phase, outline_summary,
    # card_work_summary, coaching_context, elapsed_in_phase_seconds.
    return build_research_partner_prompt(
        passage="Romans 3:25-26", genre="epistle", session_elapsed_seconds=0,
        current_phase=phase, card_work_summary="", coaching_context="")


def test_research_prompt_includes_read_library_section_invocation_cue():
    p = _research_prompt('p04_exegesis')
    assert "read_library_section" in p, "research prompt must tell the LLM to call read_library_section for verbatim quotes"


def test_patristic_phase_chains_ancient_literature_to_full_text():
    p = _research_prompt('p10_patristics')
    assert "ancient_literature" in p.lower()
    assert "read_library_section" in p


def test_always_active_guardrails_forbid_fabricated_quotes():
    # The always-active guardrail block prepended to every persona prompt
    # must forbid fabricated quotations. Verify via the rendered research
    # prompt (which is wrapped by the guardrails in companion_agent's
    # select_persona_prompt flow).
    import companion_agent
    # Find the guardrails constant. companion_agent.py:24 starts the always-active
    # guardrails string. Whatever identifier holds it, it must contain the rule.
    src = open(companion_agent.__file__).read()
    assert "SOURCE FIDELITY" in src, "always-active guardrails must include the SOURCE FIDELITY rule"
    assert "verbatim" in src.lower()
    # And the rule must mention read_library_section so the LLM knows the escape from punting.
    assert "read_library_section" in src


from persona_prompts import (
    build_research_partner_prompt,
    build_composing_partner_prompt,
    build_editing_partner_prompt,
)


def _kw():
    return dict(
        passage="Romans 3:25-26", genre="epistle", session_elapsed_seconds=0,
        current_phase="p04_exegesis", card_work_summary="", coaching_context="")


def test_source_fidelity_rule_in_research_prompt():
    p = build_research_partner_prompt(**_kw())
    # Research persona gets SOURCE_FIDELITY_RULE (header "## Source Fidelity")
    # rather than the full HOMILETICAL_GUARDRAILS (which would brand it as a
    # coach — violating test_research_does_not_describe_self_as_coach).
    assert "source fidelity" in p.lower(), \
        "research persona must carry the source fidelity (anti-fabrication) rule"
    assert "verbatim" in p.lower()
    assert "read_library_section" in p


def test_source_fidelity_rule_in_composing_prompt():
    # build_composing_partner_prompt accepts the same kw set as research; if it
    # raises TypeError on extra kwargs, narrow to the supported ones.
    try:
        p = build_composing_partner_prompt(**_kw())
    except TypeError:
        # Fall back: build with whatever the function accepts.
        import inspect
        sig = inspect.signature(build_composing_partner_prompt)
        kw = {k: v for k, v in _kw().items() if k in sig.parameters}
        p = build_composing_partner_prompt(**kw)
    assert "SOURCE FIDELITY" in p


def test_source_fidelity_rule_in_editing_prompt():
    try:
        p = build_editing_partner_prompt(**_kw())
    except TypeError:
        import inspect
        sig = inspect.signature(build_editing_partner_prompt)
        kw = {k: v for k, v in _kw().items() if k in sig.parameters}
        p = build_editing_partner_prompt(**kw)
    assert "SOURCE FIDELITY" in p
