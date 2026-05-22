# Auto Phase Advance — Design Spec

**Date:** 2026-05-22
**Status:** Design approved (brainstorm). Spec pending Bryan's review → then implementation plan.
**Build timing:** After Friday/Sunday prep — requires a `study-companion` restart, do not deploy mid-prep.

## Problem

The phase dropdown in `/study/session/<id>` is not just a label — it is the **control surface** for the study partner. Changing it calls `POST /study/session/<id>/v2-phase`, which `transition_v2_phase()`s the session, fires exit/enter phase events, regenerates the cached "Next:" cue, and (via `resolve_v2_phase` → `persona_for_v2_phase`) switches the agent's **persona and posture** on the next turn.

Today that lever is fully manual. The *conversation* drifts naturally into the next phase's work (observed 2026-05-22: the chat was doing Identify-Christ work while the dropdown still said "7. Big Picture"), but Bryan has to remember to hand-crank the dropdown to resync persona/prompt. That friction is the target.

## Core principle (Bryan's autonomy rule)

**The AI proposes; Bryan disposes.** The AI never silently switches phases. It can *suggest and ask*; Bryan confirms. Manual dropdown remains the always-available override. See memory `feedback-software-response-to-ef-problem` and `feedback-llm-role-research-ga`.

## What this is — and is NOT

- **IS:** friction removal (no manual dropdown chore) + a polite, opt-in "phase transition pull."
- **IS NOT:** a hard time constraint. Per the phase-16 memory (`phase16-never-reached-adhd-time-blindness`), gentle nags habituate. This will not, by itself, fix "I never reach Phase 16." Real pull is commitment-device / external-accountability territory — separate work, deliberately out of scope here.

## Confirmation UX (Bryan's choice: "Both")

When the AI suggests advancing, Bryan can confirm **either** by tapping a chip **or** by typing "yes / let's go."

## Design

### 1. AI suggests — new tool `suggest_phase_advance(target_phase, reason)`
- Agent calls it when: (a) Bryan signals wrap-up ("I think ID Christ is done"), (b) the phase time-budget is exceeded (Time Coach data is already in the agent's context), or (c) it senses the phase's work is complete.
- `target_phase`: defaults to current + 1; may be any valid phase (Bryan can say "jump to commentaries"). Validated against `v2_phases.V2_PHASE_KEYS`.
- `reason`: short rationale the AI can fold into its spoken question, including the clock ("past the 29m budget, and it's Friday").
- Side effects: stores a **pending suggestion** on the session (`pending_phase_suggestion = target_key`) and emits an SSE event so the UI renders a chip. Does NOT change the phase.

### 2. The chip
- Renders below the AI's message: `→ Move to Phase N: <Name>` plus a quiet `stay here` dismiss.
- Follows the existing tool→UI precedent (`save_to_outline` updates the outline panel).
- The AI also asks the question in its own voice (so the suggestion reads naturally, not just as a button).

### 3a. Confirm by tap
- Chip POSTs the **existing** `/study/session/<id>/v2-phase` endpoint with the target key.
- Persona + posture + "Next:" cue switch via the existing machinery. Clears `pending_phase_suggestion`.
- On success: update the dropdown selection + persona/posture header in place from the JSON response (`persona` is already returned); no full reload required.

### 3b. Confirm by text ("yes")
- The pending suggestion is present in the agent's turn context. Prompt rule: *if a phase suggestion is pending and Bryan affirms moving, call `commit_phase_advance`; if he qualifies it ("yes, but first…") or declines, call `clear_phase_suggestion` and keep working.*
- Uses the LLM's judgment for the fuzzy "yes vs. yes-but" case instead of brittle keyword parsing.
- `commit_phase_advance()` performs the same `transition_v2_phase` path server-side and clears the pending state.

### 4. Manual dropdown — unchanged
Always-available override; nothing about its current behavior changes.

## Components touched
- `companion_tools.py` — new tools: `suggest_phase_advance`, `commit_phase_advance`, `clear_phase_suggestion`.
- `companion_agent.py` — system-prompt addendum: when/how to suggest, time-reminder framing, and the pending-suggestion confirm/decline rule.
- `companion_db.py` — `pending_phase_suggestion` column + setter/clearer (migration).
- `app.py` — SSE event for the chip; reuse `/v2-phase` for the commit (no new commit route needed if `commit_phase_advance` calls the same DB path).
- `static/study.js` + chat partial — render the chip, wire tap → `/v2-phase`, in-place dropdown/persona update, clear chip.

## Open questions for the plan
- Should a pending suggestion expire / be superseded if Bryan keeps talking about the current phase? (Lean: superseded — the AI re-evaluates each turn and re-suggests or drops it.)
- One chip at a time vs. allowing a stale chip to linger. (Lean: only the latest pending suggestion is live.)

## Out of scope
Hard time constraints, commitment devices, rescue/emergency modes (see `feedback-no-rescue-modes-choice`). Charged-language and quote-fidelity issues are tracked separately (see `docs/superpowers/ideas/2026-05-22-library-quote-fidelity-and-ancient-lit-datasets.md`).
