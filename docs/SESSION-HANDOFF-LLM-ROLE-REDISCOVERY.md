# Session Handoff — LLM Role Rediscovery (2026-05-18)

This handoff captures the architectural reframe that happened in the 2026-05-18 session. A future session should start here.

## What happened today

The session opened intending to design Phase 2 of the Homiletics Engine (save hooks → real-time insight surfacing). It quickly surfaced that **Phase 1's wiring is wrong, not the engine itself**. The 7 analyzer tools were added to the Study TA's tool list and system-prompt addendum, which violated Bryan's longstanding mental model: *the LLM in `/study/` is a research grad assistant, not an author and not a coach.*

This is now codified in two repo docs and four memory entries.

## Canonical references created

In the repo:
- **`docs/sermon-prep-workflow.md`** — the full 16-phase "ADHD - Christ Centered Sermon Prep" workflow, extracted from Bryan's Logos workflow screenshots. ~13.5 hours per sermon. Every LLM/tool/UI must respect this structure.
- **`docs/llm-role-in-sermon-prep.md`** — the rules for what the LLM is and isn't. Hard rule: *"It is NOT a way for an LLM to write or create my sermons."* Default mode is questions; producing content requires explicit invitation + clear labeling. Phase-aware silence: silent in prayer/digest, silent-unless-invited in research phases, discussion partner + manuscript reviewer in sermon-construction phases.

In persistent memory (`~/.claude/projects/-Volumes-External-Logos4/memory/`):
- **`feedback_llm_role_research_ga.md`** — the research-GA model + the hard rule
- **`reference_sermon_prep_workflow.md`** — pointer to the workflow doc with the 16-phase summary
- **`architecture_four_agents.md`** — Study TA / Sermon Coach / Meta Coach / (planned) Pre-Flight Coach
- **`project_homiletics_engine_phase1_misplacement.md`** — diagnosis + corrective design

`MEMORY.md` index updated with all four.

## The 4-agent architecture (the key reframe)

| Agent | Status | Module | Role |
|-------|--------|--------|------|
| **Study TA** | shipped | `companion_agent.py` | Research GA + interlocutor + manuscript reviewer DURING prep |
| **Sermon Coach** | shipped | `sermon_coach_agent.py` | Per-sermon retrospective review AFTER preaching |
| **Meta Coach** | shipped | `meta_coach_agent.py` | Corpus-scoped coaching across many sermons |
| **Pre-Flight Coach** | NOT BUILT | TBD | Generative help on demand BEFORE preaching — owns the 5 generator tools |

Phase 1 of the Homiletics Engine put the Pre-Flight Coach's tools into the Study TA. That mistake is what Bryan caught.

## The 7 Homiletics Engine tools — corrective design

| Tool | Stays in Study TA? | Reason |
|------|--------------------|--------|
| `gauge_outline_balance` | ✅ Yes | Diagnostic — reviews Bryan's existing outline, produces questions |
| `check_sermon_coherence` | ✅ Yes | Diagnostic — surfaces prior similar claims |
| `find_illustration` | ❌ Move to Pre-Flight Coach | Generator — picks "the best" for Bryan |
| `find_objections` | ❌ Move to Pre-Flight Coach | Generator — invents objections |
| `find_wisdom_principles` | ❌ Move to Pre-Flight Coach | Pure generation, no library |
| `find_counseling_applications` | ❌ Move to Pre-Flight Coach | Pure generation, no library |
| `suggest_christ_thread` | ❌ Move to Pre-Flight Coach | Generator — proposes angles for Bryan |

**The engine code is reusable as-is.** `homiletics_engine.py`, the `Insight` dataclass, the `homiletics_insights` table, the 7 analyzer classes are all sound. Only the wiring (which agent calls them, on what trigger, with what default behavior) was wrong.

## The 3-part corrective program (agreed, not yet built)

| | Change | Cost | Effect |
|---|--------|------|--------|
| **A** | Split the 7 tools: keep 2 diagnostics in Study TA, remove 5 generators from Study TA's `TOOL_DEFINITIONS` and system-prompt addendum | Small | Study TA stops pushing illustrations/applications |
| **B** | Add an explicit Socratic clause to Study TA prompt: *"When Bryan reaches a homiletical decision (illustration, objection, Christ angle, application), your first move is always a question that invites him to attempt it. Use a tool only if he asks for help, or if he attempts and asks for sharpening."* | Small | The structural gap that allowed HE-13 to dominate is closed |
| **C** | Design + build the Pre-Flight Coach — new agent, new UI surface, with the 5 generators on explicit invitation + clear labeling | Medium-large | The 5 generators have a proper home |

## Where the conversation ended

We were starting to brainstorm the Pre-Flight Coach design (which scope, which UI surface, when accessible, etc.) when Bryan asked me to save this work for future sessions. **The Pre-Flight Coach brainstorm is the natural next step** for a future session.

## Suggested next session prompt

> Read `docs/llm-role-in-sermon-prep.md` and `docs/sermon-prep-workflow.md`. Then check memory `[[homiletics-engine-phase1-misplacement]]` for context. We want to do A + B + C from that doc. Start with A + B as a small clean fix, then brainstorm the Pre-Flight Coach (C) as its own design.

## Things Bryan said that future sessions should NOT forget

- *"It is NOT a way for an LLM to write or create my sermons."* — the hard rule, repeat back to back if a feature seems to drift.
- The LLM model = his old graduate-school RA work for professors: research → discussion → manuscript review, but the work was the professor's.
- ADHD-driven failure modes the tool exists to address: overwhelmed by commentaries → procrastinate → sermons end up too long, lacking application, with Christ tacked on at the end.
- *"Somewhere between [questions only] and [labeled starter material on explicit invitation]"* — default mode is questions; production requires invitation + label.
- *"There's not supposed to be any coaching in the first cards. It's my prayer and translation and wrestling phase."* — phases 1-3 (and arguably parts of 4) are off-limits to AI coaching.
- The study TA "has been pushing a little too fast in actually providing a little too much instead of provoking me to think and provide my own answers" — the failure mode A + B above are designed to fix.
