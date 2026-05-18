# The LLM's Role in Sermon Prep — Architectural Constants

> **SUPERSEDED 2026-05-18.** This document originally encoded the rules from a partial rediscovery conversation. It mixed Bryan's verbatim words with inferences I had made on his behalf. After a full phase-by-phase walkthrough on 2026-05-18, the design has been synthesized into a single canonical doc:
>
> **`docs/superpowers/specs/2026-05-18-sermon-prep-agent-architecture-design.md`**
>
> Read that doc instead. It includes:
> - §1 — The hard rule
> - §2 — Diagnosed root causes (Phase 13 procrastination + Phase 16 never reached)
> - §3 — The 4-role architecture (Research Partner / Composing Partner / Editing Partner / Time Coach)
> - §4 — Confirmed per-phase postures for all 16 phases (Bryan's verbatim or paraphrased answers)
> - §5 — Time Coach design (5 interventions + visibility + escalation)
> - §6 — Cross-agent data wiring
> - §7 — Homiletical fluency requirement (multi-tradition, not Chapell-only)
> - §8 — Hard dependencies (timer fix blocks Time Coach; audio fix)
> - §9 — Migration plan from current Phase 1 misplacement
>
> What was wrong here:
> - §4 phase-silence table contained my inferences that differed from Bryan's confirmed answers (e.g., Phase 6 was listed as "silent unless invited" but is actually proactive engagement; Phase 13/14 were listed as "manuscript reviewer" but are actually Socratic interview-driven composition).
> - §5 referenced a "Pre-Flight Coach" plan that has been RETIRED. The 3-persona phase-cluster router replaces it.
> - §5 "the fix" (A+B+C corrective program) was never confirmed and is superseded by the synthesis doc.
>
> What still holds (and is restated in the synthesis doc, §1):
> - The grad-school RA metaphor is Bryan's framing, verbatim.
> - The hard rule (*"It is NOT a way for an LLM to write or create my sermons"*) is verbatim.
> - The default-questions / invited-generation rule (*"somewhere between [questions only] and [labeled starter material on explicit invitation]"*) is from Bryan.

For session handoff context: see `docs/SESSION-HANDOFF-LLM-ROLE-REDISCOVERY.md`.
