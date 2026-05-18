# Session Handoff — LLM Role Rediscovery (2026-05-18, updated)

This handoff captures the architectural reframe completed across two related sessions on 2026-05-18. **It supersedes the earlier version of this file** (which proposed an unconfirmed A+B+C corrective program).

## Where to start (canonical doc)

**The full confirmed design lives in:**
`docs/superpowers/specs/2026-05-18-sermon-prep-agent-architecture-design.md`

Read that doc first. Everything below is context.

## What happened today

The first half of the session intended to design Phase 2 of the Homiletics Engine (save hooks → real-time insight surfacing). That surfaced a deeper problem: **Phase 1's wiring was wrong** — 7 analyzer tools added to the Study TA's tool list, including 5 generators that violated Bryan's research-GA model.

The second half was a phase-by-phase walkthrough of Bryan's 16-phase "ADHD - Christ Centered Sermon Prep" workflow. Bryan named the LLM's posture for each phase. Two root causes emerged:

1. **Phase 13 procrastination** — Bryan spends days on Phases 1-12, crashes into Phase 13 with hours left. The 75% exegetical sermon symptom traces back to this time imbalance, not outline composition.
2. **Phase 16 has never run** — in months of using the system, the Ruthless Edit phase has not happened once. The 38 reviewed sermons average +12.4 min over target with zero ever under, confirming the gap.

These root causes forced an architectural reframe: from phase-aware to **time-aware**. ADHD time-blindness is the actual failure mode.

## Canonical references created/updated

In the repo:
- **`docs/sermon-prep-workflow.md`** — full 16-phase workflow (unchanged from earlier in the session).
- **`docs/superpowers/specs/2026-05-18-sermon-prep-agent-architecture-design.md`** — the confirmed design synthesis. Authoritative.
- **`docs/llm-role-in-sermon-prep.md`** — **superseded** by the design synthesis above. Task #48 will revise it to either redirect or rewrite-with-marked-confirmed-vs-inferred.
- **This file** — handoff pointer.

In persistent memory (`~/.claude/projects/-Volumes-External-Logos4/memory/`):
- `feedback_llm_role_research_ga.md` — hard rule + research-GA model
- `reference_sermon_prep_workflow.md` — workflow pointer
- `architecture_four_agents.md` — **needs Task #49 update**; current content references the old "Pre-Flight Coach" plan which has been replaced
- `project_homiletics_engine_phase1_misplacement.md` — HE-13 misplacement diagnosis
- `project_phase13_procrastination_root_cause.md` — root cause 1
- `project_phase16_never_reached_adhd_time_blindness.md` — root cause 2
- `project_bryan_homiletical_synthesis.md` — multi-tradition fluency requirement

`MEMORY.md` index has all of the above.

## The confirmed architecture (one-screen summary)

| Role | Active in | Mode |
|------|-----------|------|
| **Research Partner** | Phases 4–12 | Research-GA + interlocutor + manuscript-margin reviewer |
| **Composing Partner** | Phases 13–14 | Socratic interview-driven composition |
| **Editing Partner** | Phases 15–16 | Auto-audit + 4-pass ruthless edit + longitudinal awareness |
| **Time Coach** | All phases (always-on overlay) | Time budget, transition pull, hyperfocus interrupt, compression, emergency, soft front-loading |
| **Sermon Coach** | `/sermons/` (unchanged) | Retrospective per-sermon review |
| **Meta Coach** | `/sermons/patterns/` (unchanged) | Corpus-scoped coaching |

The first 3 personas + Time Coach all live in `/study/`. One codebase, phase-cluster router swaps system prompt + tool list.

The previously-proposed **Pre-Flight Coach** is RETIRED — its work is absorbed into the Composing Partner's elicitation pattern + Editing Partner's audit. The 5 HE generators (`find_illustration`, `find_objections`, `find_counseling_applications`, `find_wisdom_principles`, `suggest_christ_thread`) are provisionally retired; can be added to Composing Partner's tool list later if empirical gaps emerge.

## What was DELIBERATELY rejected from the earlier handoff

- The **A+B+C corrective program** — superseded. A and B happen as a natural consequence of the 3-persona refactor (no need to track separately). C (Pre-Flight Coach) is retired.

## Hard dependencies

| Task | Blocks | Status |
|------|--------|--------|
| #53 Timer fix (activity detection) | Time Coach | Pending — not in this session |
| #54 Audio recording UX fix | Phase 1 quality | Pending — not in this session |

The Time Coach (Task #52) is formally `blockedBy` the timer fix. Audio fix is independent.

## Suggested next session prompt

> Read `docs/superpowers/specs/2026-05-18-sermon-prep-agent-architecture-design.md`. Then check memory `[[phase13-procrastination-root-cause]]` and `[[phase16-never-reached-adhd-time-blindness]]` for the diagnostic context. Next step: write the implementation plan (Task #50) for the three-persona phase-cluster router, deferring the Time Coach until the timer fix lands.

## Things future sessions should NOT forget

- *"It is NOT a way for an LLM to write or create my sermons."* — the hard rule. Repeat back if a feature seems to drift.
- The LLM model = grad-school RA work for professors: research → discussion → manuscript review, but the work was the professor's.
- ADHD-driven failure modes the tool exists to address: overwhelmed by commentaries → procrastinate → crash into Phase 13 → sermon too long, lacking application, Christ tacked on at end, Phase 16 never runs.
- The walkthrough's per-phase postures (in the design synthesis doc, §4) are CONFIRMED by Bryan in his own words. Don't re-derive — read.
- The orientation→discovery→processing→challenging→synthesizing→editing arc is the cognitive work Bryan does across the 16 phases. Don't short-circuit it. Front-loading must be soft/reversible/lazy-fire.
- Multi-tradition homiletical fluency. NOT Chapell-only. Named sources in `[[bryan-homiletical-synthesis]]`.
- Cross-tradition theological survey (not Reformed-only) in research phases — `[[feedback-llm-role-research-ga]]`.
- The data (companion.db) shows Phase 16 has never run; the Sermon Coach asks for cuts every week; nothing acts on those cuts. The Time Coach exists to break this cycle.
