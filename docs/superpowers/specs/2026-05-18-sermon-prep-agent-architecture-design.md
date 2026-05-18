# Sermon Prep Agent Architecture — Design Synthesis

**Date:** 2026-05-18
**Status:** All items below are CONFIRMED by Bryan in the 2026-05-18 walkthrough session unless explicitly marked otherwise.
**Supersedes:** `docs/llm-role-in-sermon-prep.md` (which mixed Bryan's words with inferences). The 3-part A+B+C corrective program in `docs/SESSION-HANDOFF-LLM-ROLE-REDISCOVERY.md` is also superseded by this doc.
**Source:** Phase-by-phase walkthrough of the 16-phase "ADHD - Christ Centered Sermon Prep" workflow. Bryan provided the posture for each phase from his stated needs; this doc synthesizes those answers + the system-level decisions that emerged.

---

## 1. The hard rule (load-bearing for every other decision)

> *"It is NOT a way for an LLM to write or create my sermons."*

The LLM in `/study/` is a research grad assistant + Socratic interlocutor + manuscript reviewer + time partner. **The sermon's words are Bryan's.** Every persona, every intervention, every tool, every emergency mode preserves this rule. Compression and rescue modes compress the *pace* of elicitation, never substitute for it.

This rule is the kill switch for any feature that drifts toward generating Bryan's content. If a feature would write a sentence Bryan should write, it must be redesigned or removed — no matter how convenient.

---

## 2. The diagnosed root causes (data-validated, 2026-05-18)

These shape every design choice that follows.

**Root cause 1 — Phase 13 procrastination.** Bryan spends days on Phases 1-12 (research, intellectually rewarding) and crashes into Phase 13 (sermon work) with hours left in the week. Result: rushed homiletical scaffolding → 14 leans on what he knows well (exegesis) → sermons land 75% exegetical. The BalanceGauger measures the symptom; this is the cause. See memory `[[phase13-procrastination-root-cause]]`.

**Root cause 2 — Phase 16 has never run.** In months of using the system, Bryan has never reached the Ruthless Edit phase. The 38 reviewed sermons average +12.4 min over target (+44%), with zero ever under target — exactly what happens when Ruthless Edit is theoretical. The Sermon Coach has asked for specific cuts every week after the fact; the cuts that would happen in Phase 16 don't happen. See memory `[[phase16-never-reached-adhd-time-blindness]]`.

**Implication:** The architecture must be **time-aware**, not just phase-aware. ADHD time-blindness is the real failure mode.

---

## 3. The 4-role architecture

One codebase (`companion_agent.py` and its scaffolding stay). Four roles selected by a phase-cluster router. The first three are personas (mutually exclusive — one at a time per phase). The fourth is always-on alongside whichever persona is active.

| Role | Active in | Mode |
|------|-----------|------|
| **Research Partner** | Phases 4–12 | Research-GA + interlocutor + manuscript-margin reviewer. Persona-bound. |
| **Composing Partner** | Phases 13–14 | Socratic interview-driven composition. Persona-bound. |
| **Editing Partner** | Phases 15–16 | Auto-audit + 4-pass ruthless edit + longitudinal pattern awareness. Persona-bound. |
| **Time Coach** | All phases (always-on) | Time budget awareness, transition pull, hyperfocus interruption, compression, emergency, soft front-loading. Not persona-bound. |

Naming is final. Bryan confirmed in the synthesis-decision question (2026-05-18).

### 3.1 Shared scaffolding

- Streaming Claude loop
- Tool dispatch
- Message persistence
- Session/card state
- Cross-agent data access layer (see §6)

The differences between personas are: **system prompt + tool list + UI cue (visual transition marker when cluster changes).** All three personas share the same underlying agent code.

### 3.2 Why split at all

Three reasons (in priority order):

1. **The system prompt is already strained.** Encoding "silent in 1-3, reactive in 5-12, Socratic in 13-14, ruthless in 15-16" all in one prompt is what allowed the HE-13 addendum to dominate and overstep. Splitting fixes the structural cause, not just the symptom.
2. **Mental model honesty.** The kind of partner Bryan needs in Phase 6 (cultural backgrounds researcher) is genuinely different from Phase 13 (interview-driven composer) is genuinely different from Phase 16 (ruthless editor). Pretending they're one role obscures the differences.
3. **Evolvability.** Tuning the Composing Partner's elicitation style without breaking Research Partner's pushback behavior is much easier with separate prompts.

### 3.3 Phase-cluster router

```
Phase 1-3   → silent (no persona active; chat box not rendered)
Phase 4-12  → Research Partner
Phase 13-14 → Composing Partner
Phase 15-16 → Editing Partner
ALL phases  → Time Coach (overlay)
```

Transitions show a soft visual cue (color/label/avatar shift) so Bryan feels the role change as a phase transition, not a system glitch.

---

## 4. Per-phase postures (the walkthrough output)

Each phase has a confirmed posture. Quoted text is Bryan's verbatim from the 2026-05-18 walkthrough; un-quoted is paraphrased from his answers.

### Phases 1, 2, 3 — Prayer, Translate, Digest (Research Partner inactive)
**Fully silent. Chat box not rendered.** *"There's not supposed to be any coaching in the first cards. It's my prayer and translation and wrestling phase."*

### Phase 4 — Exegesis (Research Partner: full mode)
*"Full LLM RA interlocutor mode."* Reads Bryan's exegetical questions and challenges logged during translation and prayer. Pulls best resources from the library, returns direct quotes (not info dumps). Actively challenges what Bryan has missed, gotten wrong, or needs nuance on. Doctoral-level RA, not BA sophomore. Fresh eyes; doesn't take the driver's wheel.

### Phase 5 — Get Quick Answers / Study Bibles (Research Partner: reactive)
Silent unless invited. When invited, dumps relevant notes from the 7 study bibles with citations and stops. Plus soft timer awareness — warns if past 30 min or going deep on a tangent.

### Phase 6 — Context Questions (Research Partner: proactive)
*"Full engagement, bringing things up, wrestling through, maybe even showing me things I've never learned or haven't considered."* This is where assumption-laden cultural backgrounds hide. LLM proactively surfaces cultural/geographic context from Atlas, Cultural Concepts, Biblical Places/Things, Media Resources.

### Phase 7 — Big Picture (Research Partner: proactive + discussion)
Proactively surfaces canonical/structural observations Bryan might miss AND pushes back as a discussion partner when invited.

### Phase 8 — Identify Christ (Research Partner: conditional proposals)
*"Start with what I've already done, but then feels free to bring in what other sources have said (or using their methods the LLM can propose other angles I've missed). It's a researcher and conversation partner who's providing and provoking but not driving."* First phase where the LLM is allowed to *propose new content* (Christ angles), conditioned on Bryan having gone first. Uses Greidanus / Clowney / Beeke / Calvin / Edwards / etc. — multi-tradition.

### Phase 9 — Systematic & Biblical Theology (Research Partner: anti-paralysis + heresy guard + cross-tradition)
*"This tool is meant to help save me time and this is an area I can get bogged down by or it's a mountain of research I can get paralyzed by. Yet it's in these sections that heresy can be stepped into if not understood. I also don't want the LLM to only read [Reformed] sources… While I am a Westminsterian reformed pastor I don't want to be ignorant of how other traditions have interpreted and wrestled with things."* Cross-tradition survey. Proactive proposals from ST/BT loci. Heresy-guard role.

**This establishes a cross-phase principle for ALL research phases:** save time + prevent paralysis + catch heresy + broad cross-tradition survey. Applies retroactively to Phases 4, 7, 10, 12.

### Phase 10 — Don't Live in a Historical Vacuum (Research Partner: divergence flag + rabbit-hole guard)
Two specific use cases Bryan named:
1. **Sanity check:** is my interpretation/application *totally foreign* to any patristic understanding? If so, reconsider.
2. **Bolster/clarify:** are there patristic writings that bolster or clarify my interpretation?
Plus rabbit-hole guard (patristics goes deep fast) and divergence flag (names where ancient reading diverges from Bryan's Reformed reading without driving change). Uses Logos's Ancient Literature module (broad, not hand-picked list).

### Phase 11 — Confessions (Research Partner: confessional surface + WCF-lean)
Proactive surface across WCF/WLC/WSC + Heidelberg / 3FU / 39 Articles parallel sections, plus tension-flag where Bryan's reading sits against the Westminster Standards. **Lean heavy on Westminster Standards** (not equal weight across confessions — Bryan is RP).

### Phase 12 — Commentaries (Research Partner: aggressive curator)
The worst paralysis trap. 1,025 commentaries, 30 min, workflow doc explicitly warns "don't drown." LLM does the survey:
- Reads Bryan's prior phase cards (4, 7, 8, 9, 10, 11) to know his open questions
- Picks 5 distinctive voices (not 5 conservatives saying the same thing — cross-tradition)
- Returns 1 paragraph per voice, weighted toward Bryan's open questions
- Soft 30-min drown-guard timer

### Phase 13 — Sermon Work (Composing Partner activates)

**Critical context:** Phase 13 is where Bryan procrastinates. The Composing Partner must actively pull him into the work, not sit back. *"I need your help as a system whole."*

**Sub-phase posture map** (all 8 share Socratic-interview foundation, with the Composing Partner refusing to write any of Bryan's words):

| Sub-phase | Posture |
|-----------|---------|
| 13.1 Subject | Socratic only (no candidates) + week-clock aware. |
| 13.2 Theme | Socratic + LLM pushes back / clarifies / corrects, never drives. |
| 13.3 EP/OTM | Socratic + manuscript review + cross-agent rut awareness + multi-tradition synthesis-aware critique. Never proposes; only critiques drafts. |
| 13.4 Transition | Same as 13.3. |
| 13.5 Exegetical Outline | Same as 13.2. |
| 13.6 FCF | Same as 13.3. Heightened rut-awareness (FCF is where Christ-tacked-on starts). |
| 13.7 HP/THT | Same as 13.3. |
| 13.8 Purpose Statement | Same as 13.3. |

The Composing Partner is **not just a manuscript reviewer** — it's a Socratic interviewer whose questioning becomes the manuscript content. *"It compiles study, provokes questions to get me to think and compose, it then interacts to get to a good point for actually preaching."* The words come out of Bryan via good questioning, not from the LLM.

### Phase 14 — Formal Sermon Writing (Composing Partner continues)
All 4 sub-phases (broad outline, amplify divisions, intro, conclusion) inherit Group B posture from Phase 13: Socratic interview-driven composition + cross-agent rut awareness + multi-tradition synthesis-aware critique. Never composes; elicits. The LLM is more active during 14.2 (Amplify Divisions, 2h) — proactively watching for drift into pure exegesis, missing application, no Christ thread in a division, transition gaps. Intro and conclusion get targeted length/clarity push-back.

### Phase 15 — Finishing Questions (Editing Partner activates)
**Auto-audit on entry + longitudinal pattern awareness.** When Phase 15 opens, the LLM has already run a full audit of the manuscript against Bryan's known weaknesses (length estimate, application count, Christ thread coherence, gospel landing, congregation address) AND weighted findings by the last 6 sermons' Sermon Coach reports. *"Your last 4 sermons all had absent-Christ in middle divisions — same pattern here."* Bryan decides what to do with each finding.

### Phase 16 — Ruthless Edit (Editing Partner: 4-pass evidence-based)

Designed from the 38-sermon corpus (see §2 root cause 2 + companion.db analysis). Each pass targets a specific recurring negative pattern in the data:

- **Pass 0 (10s, automatic):** length gauge. Estimate spoken length, show delta against 28-min target, color-code.
- **Pass 1 (15m):** exposition over-run. 74 of Bryan's negative moments live in exposition (5× any other section). Default surfacing here first. For each long expository paragraph: *"Does this drive the burden, or is it explanation-for-explanation's-sake? Past sermons cut similar passages [cite]."*
- **Pass 2 (10m):** application placement check, not just count. Coach has asked 4+ times to move application earlier. LLM identifies latest application moment, asks if it can move forward. References active coaching commitment (currently "Tuesday at 10am Test") and checks for it before 60% mark.
- **Pass 3 (10m):** Christ-thread + pastoral concreteness sweep. Christ explicit in each division? Named-people moments embedded?
- **Pass 4 (10m):** redundancy + transition tightening. Repeated points, weak transitions, conviction passes already made.

**Cross-cutting:** Editing Partner can cite specific past sermons. *"Your 4/26 sermon had a vv.13-18 expository over-run the coach flagged — your current Div 2 has the same shape."* Pulls from `sermon_reviews.length_delta_commentary` + `sermon_moments` for precedent.

**Hard rule preserved:** Bryan keeps or cuts every line. LLM never deletes.

---

## 5. The Time Coach (4th role, always-on overlay)

### 5.1 Why it exists

Phase 16 never runs. Phase 13 starts Saturday. The 3-persona router is phase-bound and won't fix either — both are time-budget problems, not phase-content problems. ADHD time-blindness is the failure mode.

### 5.2 Visibility (Choice A — confirmed)

Visible persona. Own UI strip (timer + budget + intervention text when active). Bryan trusts what he can see; invisible meta-wrappers create distrust when the active persona suddenly shifts behavior without explanation.

### 5.3 Interruption timing (Choice B — confirmed)

**Status strip always-on + active mid-card intervention at severe budget breaches, with smart escalation so it doesn't become a predictable alarm.** Bryan's framing: *"something annoying that doesn't become an alarm I just ignore but the system is actively moving me forward toward Sunday morning."*

The escalation pattern (sketch — refine in implementation):
- Strip is ambient (color shift) at small overruns.
- Strip text changes ("you're 2× over budget for Phase 6") at moderate overruns.
- A single chat message ("you've been in research for 4h on Wednesday — want a Phase 13 prep ping when you wrap?") at severe overruns.
- Vary the message phrasing/cadence so it doesn't become predictable noise.

### 5.4 The 5 interventions (all confirmed)

1. **Phase-transition pull** — when phase budget exceeded, active persona's next message ends with a momentum bridge: *"You've been in Phase 6 for 47 min on a 15-min budget. The cultural-context work has 6 captures so far. Ready to move to Phase 7, or is there a specific question still open?"*
2. **Hyperfocus interruption** — after N continuous minutes in a research phase without phase change, status strip surfaces above the active card: *"You've been in research 4h today. Phase 13 needs ~2h and Sunday is in 3 days."* Visual not blocking.
3. **Compressed-time mode (Saturday rescue)** — when calendar shows ≤24h to preach AND Phase 13 hasn't started: optional sub-phases visually de-emphasized; Composing Partner front-loads inheritance from Phase 4-12 captures; Editing Partner pre-flights manuscript inline rather than only as final pass.
4. **Sunday-morning emergency mode** — last resort: 5-min "three longest paragraphs" pass, one question per paragraph: *"Can this be cut by 50%? Y/N/Maybe — go."* Even one acted cut moves the needle.
5. **Front-loading** — see §5.5; the prevention play with the safeguards Bryan flagged.

### 5.5 Front-loading — the soft/reversible/lazy-fire design

Bryan flagged that aggressive front-loading would short-circuit his cognitive arc:

> **orientation → discovery → processing → challenging → synthesizing → editing down**

The arc itself is the work. Three governing principles, locked in:

1. **Tentative language, never categorical.** Not "cut candidate" — "long passage, flag for Phase 16 revisit *if it doesn't earn its keep*." Not "composing candidate" — "potentially useful in Phase 13, see if it still resonates." Tags are for future-Bryan, not verdicts from current Time Coach.
2. **Lazy-fire on strong signals only.** Default is silence. Only flags on (a) clearly long passages (>4-5 sentences without a turn), or (b) moments Bryan explicitly bookmarks ("this might preach"). The Time Coach does not second-guess every card.
3. **Reversible and Bryan-visible.** Every tag shown to Bryan, removable one-click. When Phase 13 activates, Bryan sees the inherited stash and re-tags, removes, merges freely. Scaffolding, not commitments.

**Persona-mode map for the arc:**
- Orientation, Discovery → Research Partner Phases 4-12. Don't interrupt. Don't pre-judge.
- Processing, Challenging → Research Partner pushback (Phases 4, 7, 9, 10).
- Synthesizing → Composing Partner Phases 13-14, front-loaded stash surfaces as inheritance, not verdict.
- Editing down → Editing Partner Phases 15-16, tags get evaluated by Bryan with Editing Partner asking questions.

The Time Coach itself doesn't drive the arc. It protects the time available for the arc to complete.

---

## 6. Cross-agent data wiring

The Composing Partner and Editing Partner read from the Sermon Coach's tables in `companion.db`:

- `sermon_reviews` — for length deltas, density hotspots, late-application notes, one-change-for-next-sunday history
- `sermon_moments` — for negative-major moments by section/dimension (where ruts live)
- `coaching_commitments` — active commitment Bryan is working on
- `coaching_insights` — accumulated longitudinal patterns

The Research Partner does NOT need this data — it serves Phase 4-12 which is text-and-resource-bound, not Bryan-pattern-bound.

The Editing Partner additionally reads `sermons.transcript_text` for the last 6 sermons to enable "your 4/26 sermon had this same shape" precedent citations.

The Time Coach reads `phase_events` (timestamps per phase transition per session) to compute per-session, per-day, per-week budget consumption.

---

## 7. Homiletical fluency requirement

The Composing and Editing Partners (and the Sermon Coach, retroactively) must speak fluently across Bryan's homiletical synthesis — **not** Chapell only. The named sources:

- **Chapell** — Christ-Centered Preaching (FCF, Christ-centered lens, redemptive arc)
- **Dr. Barry York** — Bryan's RPTS preaching professor
- **Dr. Haddon Robinson** — Biblical Preaching (Big Idea)
- **Denis Prutow** — *So Pastor, What's Your Point?* (RP tradition)
- **Dr. Joel Beeke** — Reformed Preaching (Puritan + experiential)
- **William Perkins** — *The Art of Prophesying* (classical Reformed homiletics)
- Plus others in his library and outside

**Anti-pattern:** defaulting to "as Chapell teaches…" for every homiletical move. When critiquing a Big Idea, Robinson's framework is primary. When the issue is doctrine/use balance, Perkins/Beeke. When the question is Reformed-pastoral, York/Prutow. See `[[bryan-homiletical-synthesis]]`.

---

## 8. Hard dependencies (must land before / alongside)

### 8.1 Timer must become activity-aware (BLOCKS Time Coach)

Current timer requires manual click. No tab-focus detection, no idle detection, no recovery from phone interruptions. The Time Coach cannot function on this timer. Needed:

- Tab-focus / Page Visibility API auto-pause
- Activity detection inside cards (keystrokes, scrolls, clicks) with idle threshold
- Idle confirmation when Bryan returns ("resume from pause, or count the gap?")
- Legitimate over-budget recognition (active typing + budget exceeded ≠ stalled; soft prompt not hard cut)
- Synergy with audio: mic-input level signals "still here" even with no keys/clicks

### 8.2 Audio recording UX (independent, but improves Phase 1)

Current audio is broken UX. Bryan can't see if it's recording. Prayer captures are unusable. Needed:

- Real-time waveform/level meter
- Live transcription preview (rough is fine)
- Per-phase recording defaults
- Post-capture quality indicator
- Easy retry without losing phase progress

Both tracked as separate tasks (#53, #54). The Time Coach task (#52) is now formally `blockedBy` the timer fix (#53).

---

## 9. Migration from current state

**Current code state (shipped, but architecturally misplaced):**
- `homiletics_engine.py` — 7 analyzers, sound engine
- `homiletics_insights` + `claim_embeddings` tables — sound infrastructure
- 7 HE tools wired into Study TA's `TOOL_DEFINITIONS` — **wrong agent**
- HE-13 system-prompt addendum in `companion_agent.py:build_study_prompt` — **wrong location**

**Migration plan (delegated to Task #50 — implementation plan):**
1. Introduce `phase_cluster_router.py`. Determines current phase cluster from active session's phase.
2. Refactor `companion_agent.py:build_study_prompt` into three persona-specific prompt builders.
3. Refactor `companion_tools.py:TOOL_DEFINITIONS` into three persona-specific tool lists.
4. Remove the HE-13 addendum from Research Partner's prompt.
5. Move `gauge_outline_balance` + `check_sermon_coherence` to Editing Partner's tool list (they are diagnostic, fit the Editing Partner).
6. The 5 generators (`find_illustration`, `find_objections`, `find_counseling_applications`, `find_wisdom_principles`, `suggest_christ_thread`) — provisionally retired. The Composing Partner's elicitation pattern is intended to replace the need for generators. If specific gaps emerge in practice, individual generators can be added back to Composing Partner's tool list with strict opt-in + labeling.
7. UI: visual transition cue between clusters.
8. Time Coach: deferred until timer fix (#53) lands.
9. Front-loading: lazy-fire, soft tags, full Bryan visibility/reversibility.
10. Cross-agent data layer: read-only accessors for Composing/Editing Partners.

---

## 10. What this design does NOT do

For honesty about scope:

- Does not change `/sermons/` (Sermon Coach) or `/sermons/patterns/` (Meta Coach). Both stay.
- Does not redesign the 16-phase workflow itself (sound; Bryan's synthesis).
- Does not change `companion.db` schema except possibly adding a Time Coach tags table and `phase_events` indexing.
- Does not implement audio fixes — separately tracked (#54).
- Does not implement Time Coach itself — blocked on timer fix (#53).
- Does not predict whether the Composing Partner's elicitation will fully replace the 5 retired generators. That's an empirical question for after migration.

---

## 11. Open questions for implementation

- Per-phase budget defaults: workflow doc has minutes per phase; treat those as defaults or learn from Bryan's actual averages?
- Hyperfocus interruption threshold: 4h continuous research is a guess. Calibrate from `phase_events` history.
- Phase-transition visual cue: which exact UI affordance — color border, persona avatar, banner? Implementation-time choice.
- Front-loading tag storage: new table or extend `card_annotations`? Implementation-time choice.

---

## 12. Memory references

Memories written or updated during the 2026-05-18 rediscovery:

- `feedback_llm_role_research_ga.md` — the hard rule + research-GA model
- `reference_sermon_prep_workflow.md` — pointer to `docs/sermon-prep-workflow.md`
- `architecture_four_agents.md` — (needs update to reflect 3-persona-router + Time Coach in `/study/`; see Task #49)
- `project_homiletics_engine_phase1_misplacement.md` — diagnosis of the misplaced HE-13
- `project_phase13_procrastination_root_cause.md` — root cause 1
- `project_phase16_never_reached_adhd_time_blindness.md` — root cause 2
- `project_bryan_homiletical_synthesis.md` — multi-tradition fluency requirement
