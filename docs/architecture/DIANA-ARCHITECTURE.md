# Diana — Final Architecture (M1–M7 complete)

Status: **descriptive, not normative.** This document explains the system that the frozen M1–M7
specifications and their errata define. Where it appears to disagree with any frozen specification,
**the frozen specification wins and this document is the defect.**

Accepted base: `main` at `431229e` (PR #51). Milestones M1–M7 are accepted, with fourteen frozen
documents: seven specifications and seven errata.

Every claim below was read from the accepted specifications, audits and implementation rather than
recalled. Where a number appears, it was measured.

> **Snapshot boundary.** This document is the canonical architecture record for the **accepted M1–M7
> system** at `431229e` and for the governance updates documented since. Sections A1–A7 and B are
> kept deliberately as an **acceptance snapshot**. They are not rewritten as the code evolves, and a
> statement in them is a statement about the accepted revision unless it is labeled **current
> `main`**.
>
> Current `main` also contains **post-M7 changes** that were accepted separately and are **not**
> part of the M1–M7 acceptance: generic write-scope derivation, the Hermes Builder/Reviewer
> production-path repair, CI gate-input hardening, mechanical human merge approval (Track B), and
> one-approval autonomous recovery. They are summarized in
> [Post-M7 deltas on current `main`](#post-m7-deltas-on-current-main) and documented in:
>
> | Source | Role |
> |---|---|
> | [`README.md`](../../README.md) | Entry point for current `main`: the product surface and runtime behaviour |
> | This document | Accepted M1–M7 architecture, plus clearly marked post-M7 deltas |
> | `HERMES-RUNTIME-M1` … `M7` and their errata | Frozen accepted specifications; they win over this document |
> | [`DIANA-POST-M7-ERRATA-001.md`](DIANA-POST-M7-ERRATA-001.md) | The authoritative account of which pre-existing production files accepted post-M7 work replaced |
> | [`DIANA-POST-M7-ROADMAP.md`](DIANA-POST-M7-ROADMAP.md) | The original post-M7 plan, with explicit current-status labels |
> | [`DIANA-HUMAN-APPROVAL-B0.md`](DIANA-HUMAN-APPROVAL-B0.md) … [`B5`](DIANA-HUMAN-APPROVAL-B5.md) | Evidence for mechanical human merge approval |

---

## The one-sentence version

> Diana is a deterministic authority and governance layer around nondeterministic coding agents:
> a model may propose and act, but permissions, durable state, verification, recovery and approval
> live outside the model.

**Intelligence proposes. Diana governs.**

Hermes is the reasoning and execution backend. Diana is not tied to trusting one model session, one
provider, or one backend — the executor is a replaceable seam, and replacing it grants nothing.

---

## The layers

```
        ┌─────────────────────────────────────────────────────────┐
  A1    │  PRODUCT      Goal → Plan → Authority → Approval →       │
        │               Progress → Result / Blocked               │
        ├─────────────────────────────────────────────────────────┤
  A2    │  AUTHORITY    contract · run policy · work items ·       │
        │               projections · actor topology · digests    │
        ├─────────────────────────────────────────────────────────┤
  A3    │  EXECUTION    Builder → Reviewer, sequential, one budget │
        ├─────────────────────────────────────────────────────────┤
  A4    │  DURABILITY   journal · reconciliation · lease · resume  │
        ├─────────────────────────────────────────────────────────┤
  A5    │  EVIDENCE     advisory · UNPROVEN · (future) certified   │
        ├─────────────────────────────────────────────────────────┤
  A6    │  GOVERNANCE   Gate · Security Gate · human merge         │
        └─────────────────────────────────────────────────────────┘
```

---

## A1 — Product layer

A user starts from a sentence, not a command vocabulary:

```
diana-do "Fix the failing tests in this repo, but don't touch auth or deployment"
```

They receive a **Goal**, a **Plan**, the exact **Authority** the run would hold, and a digest to
approve. Nothing has run and no run exists until they approve.

**Natural language may propose intent. It never grants authority.** `Intent` is a closed schema with
no `risk` field and no `depth` field at all — there is no channel in which either could be proposed.
Depth comes from the certified workflow class; risk derives from the granted envelope. Fields the
schema does not know (`allowed_tools`, `actors`, `network_policy`, `merge`, `deploy`) are refused,
not recorded.

**Classification is deterministic.** Routing is a keyword rule over a fixed vocabulary with a
disqualifying-word veto, so a "fix this" cannot quietly become a "look at this". No model decides
what a request *is*.

**A proposal is concrete and digest-bound.** It carries the full contract, work-item document and
actor topology that approval would create, built by the *same* builders the run uses — there is no
second implementation of contract semantics. Its identity is a digest over every authority-bearing
field plus the work-item and actor-topology digests.

**Approval is approval of that exact proposal.** The approval path takes a digest by type and has no
free-text branch: `yes`, `do it`, `go ahead`, `continue` and an approving model paraphrase cannot
become approval, because agreeable text is not the input type. Approval re-derives against the live
repository before creating anything; a stale proposal is refused with **no run directory created**,
never rebuilt and run.

**A user exclusion is authority, not prose.** "don't touch auth" removes that path from the write
roots. A run that writes there is blocked by reconciliation.

**Progress derives from durable Diana state** — the journal, and nothing else. There is no parallel
progress store, so a crash cannot produce a conflicting view. **Result derives from the run report.**
**Blocked is a first-class outcome**, not an error screen: it names what was attempted, which control
refused it, why, what decision a human must make, and whether granting it would widen the approved
envelope.

---

## A2 — Authority layer

> **Model output is never authority.**

One user-approved **parent envelope** governs a run. Everything else is derived from it or narrower
than it.

| Object | What it fixes | Bound by |
|---|---|---|
| **Contract** | tools, write scope, denied subpaths, exact command allowlist, read scope, risk, depth, target | its own digest |
| **Run policy** | attempt cap, absolute deadline, quiescence grace | its own digest |
| **Work items** | the unit graph, validated acyclic before arming | its own digest |
| **Actor topology** | the closed role set for the run | its own digest |
| **Proposal** | all of the above, predicted before anything exists | the proposal digest |

- **Risk and depth are derived, never proposed.** Depth comes from the certified workflow class; risk
  from the granted tool envelope.
- **Commands are an exact-match allowlist**, and a command may only enter it by appearing verbatim in
  the frozen catalogue. Shell is never parsed for safety.
- **Projections** narrow the parent envelope per role and are proven a subset on every authority axis
  *and* proven equal by value to that role's frozen shape. A non-subset projection is refused, never
  clamped — a clamped projection is one the user did not approve.
- **Target freshness** rides inside the contract: a new commit moves `target.git_commit`, a dirty tree
  moves `target.dirty`, and a gitignored-only change moves `repo_profile.inventory`. All three are
  inside the proposal digest, so a moved target invalidates an approval without a separate mechanism.

---

## A3 — Execution layer

**Hermes reasons and acts. Diana decides what may be acted upon.**

Two actors, one approved envelope:

- **Builder** — the bounded-remediation projection: read, search, write, patch, and the exact allowed
  commands, confined to the approved write scope.
- **Reviewer** — read and search only. No write scope, no commands, no terminal. Read-only **by
  enforcement**: under the reviewer projection those tools are refused at the real dispatch boundary
  with valid arguments, and the target is byte-unchanged.

The reviewer runs **no verification commands** — Diana does. A test command executes repository code
and can therefore mutate, so granting it to a read-only role would return the authority the role
exists to withhold.

**Actors are strictly sequential.** Concurrency is not declined by preference; it is foreclosed by two
measured properties — enforcement is installed once per process, and two processes sharing a run stamp
are indistinguishable to the ownership check.

**A reviewer verdict is an input to a Diana decision, never a state transition.** A `PASS` over a diff
that left the envelope still blocks.

**Backends are replaceable and carry no authority.** Switching executor or model creates no new
envelope and no new budget: the contract stays byte-identical, the run id is unchanged, and attempt
numbering continues. There is **one run-level budget** — every actor turn spends it.

---

## A4 — Durability and recovery layer

- **The run journal is the only authoritative record**, written crash-atomically (temp file, fsync,
  atomic rename). A torn record reads as absent, and absence fails closed.
- **Write-ahead ordering**: no mutation begins until the audit's inputs are durable.
- **`TURN_ACTIVE` in a journal at startup is an obligation**, not an invitation to continue. The next
  process must discharge it before doing anything else.
- **Quiescence is proven before reconciliation**, never assumed — reconciling a target another process
  is still writing is a reading of a moving object.
- **Reconciliation is detection, never prevention.** It uses two views that fail differently: a hash
  snapshot of every regular file, and git status. The hash view is what catches gitignored changes
  that git cannot see.
- **The retry budget is run-level and actor-neutral**; exhausting it is `FAILED`, never a partial pass.
- **Work items form a validated DAG**; a blocked item propagates to its dependents.
- **Cancellation is durable and one-way.**
- **The run lease** is an exclusive `flock` checked at the effects — before reconciliation, before an
  attempt is closed, before the journal advances.

### Residual limitations, stated accurately

- **The run lease is cooperative exclusion, not containment.** It answers one question — may this
  process act on this run right now — and constrains nothing about what a process does once admitted.
- A hostile same-user process that **unlinks and recreates** the lease file is outside the inherited
  threat model; the same actor can unlink the journal, which is likewise undetected.
- A **whole-record journal forgery** (rewrite *and* re-digest) is not detected. The digest proves
  authorship, not intent.
- Per-PID ownership and quiescence depend on **`/proc`**; the lease depends on **`flock`**. Both are
  Linux assumptions.
- Quiescence bounds only processes Diana can see. Work handed to a service, container or remote host
  leaves no stamped descendant.
- A `fork`ed descendant retains the lease after its parent exits — fail-closed, bounded by that
  descendant's lifetime.

---

## A5 — Security and evidence layer

Three states, and they are **not** the same thing:

| | Meaning |
|---|---|
| **ADVISORY** | A deterministic scanner's finding. Structurally incapable of being read as Security Track evidence — it carries none of the evidence model's allowed run fields, and is rejected by the artifact validator. |
| **UNPROVEN** | The Security Track's honest default. Either applicability was never established, or no verification run was submitted for that control. |
| **CERTIFIED** | **Does not exist today.** No control is certified by any part of M1–M7. |

At the accepted revision the Security Gate evaluates **75 controls** and reports **75/75 UNPROVEN**
(`diana/security/` is unchanged on current `main`), which maps to
`REQUIRE_HUMAN`. That is the standard being met, not 75 failures — and the distinction matters,
because "we have no evidence" and "we found a violation" are different facts.

**M1–M7 do not make any security finding certified.** Advisory findings may only become certified
evidence by satisfying the Security Track's own requirements — verifier provenance, target binding,
evidence composition — never by relaxing them. See
[`DIANA-CERTIFICATION-ROADMAP.md`](DIANA-CERTIFICATION-ROADMAP.md), which is **future work**.

---

## A6 — Governance layer

Two CI gates run on every pull request. Both currently decide `REQUIRE_HUMAN` for this repository's
own changes.

**`REQUIRE_HUMAN` maps to a *passing* GitHub check.** That is deliberate: a required status check that
failed on `REQUIRE_HUMAN` would deadlock the pull request against its own required check. The run log
states that "merge stays blocked by the independent required human/code-owner review rule."

**Historical baseline (pre-Track-B): no such rule was configured.** The accepted M4 audit records the
repository ruleset as `required_approving_review_count: 0`, `require_code_owner_review: false`,
`require_last_push_approval: false`. `CODEOWNERS` existed but had no merge-blocking effect without
code-owner review enabled. The current state follows.

> **M5-D20 is DISCHARGED** (Track B, phases B0–B5). The values above describe the pre-Track-B
> baseline and are retained for context. The live `diana-main-protection` ruleset now carries
> `required_approving_review_count: 1`, `require_code_owner_review: true`,
> `require_last_push_approval: true` and `dismiss_stale_reviews_on_push: true`.
>
> Measured on live pull requests, not inferred from configuration: an automation-authored pull
> request with zero approvals is **blocked with required checks green**; automation **cannot**
> approve its own pull request (GitHub returns `422` on both REST and GraphQL); a human code-owner
> approval opens the merge path for **that exact revision**; and a diff-affecting push **dismisses**
> the approval.
>
> **The discharge is conditional.** It rests on credential separation — the automation runtime must
> be unable to authenticate as the human owner — which **no GitHub rule enforces or detects**, and
> which failed twice during Track B before being held by provider-side credential revocation. See
> [`DIANA-HUMAN-APPROVAL-B5.md`](DIANA-HUMAN-APPROVAL-B5.md) §13.3.

**Two approvals that never convert into one another:**

- **Category A — Diana runtime approval.** "Start this exact bounded run." This is what `diana-do`
  asks for.
- **Category B — repository/deployment approval.** "This exact revision may merge or deploy."

No accumulation of A becomes B. M7 adds a human approval step for A, which is exactly what could be
misread as discharging M5-D20 — it did not, and Track B discharged B separately. The separation is
architectural: the M7 product surface has **no code path to GitHub at all**, so a Category A approval
cannot create a review or change merge eligibility.

See [`DIANA-HUMAN-APPROVAL-B5.md`](DIANA-HUMAN-APPROVAL-B5.md) for the discharge and its standing
conditions; [`DIANA-HUMAN-APPROVAL-ROADMAP.md`](DIANA-HUMAN-APPROVAL-ROADMAP.md) is the original
plan, retained as history.

---

## A7 — Legacy and expert surface

**Nothing has been deleted or retired.** *Accepted M1–M7 baseline (`431229e`):* all six slash
commands, `ship.py`, `ao.py` and every CI adapter were byte-identical to the accepted M6 base. That
statement remains true for the accepted revision.

*Current `main`:* it is not true of every file. Accepted post-M7 work replaced four of these surfaces:
`diana/ci/build-gate-input.py`, `diana/ci/write-summary.py` and `diana/gate/diana-gate.py` (`0f121ce`,
CI gate-input hardening), and `diana/adapters/hermes_live.py` (`0828801`, `f724038`, the production
Builder/Reviewer repair). These are authorized, documented changes. They are enumerated by set
equality in [`DIANA-POST-M7-ERRATA-001.md`](DIANA-POST-M7-ERRATA-001.md) (POST-M7-E1-D1) and
re-asserted by `diana/ci/test-post-m7-replacement-set.sh`. The slash commands, `ship.py`, `ao.py`,
preflight and `diana/security/` remain byte-identical to `431229e` on current `main`.

| Surface | Status (accepted baseline → current `main`) |
|---|---|
| `/diana-ship` | Present and usable. Its **steps 2–5** (accept the goal, author the Definition of Done, classify risk, precheck) are **superseded for the certified product path only** — the M7 flow produces the same four outputs from one sentence. The command is not retired. |
| `/fix`, `/review`, `/ship`, `/orchestrate`, `/loop-audit` | Present, unchanged, expert/debug surfaces. |
| `ship.py` (10 subcommands), `ao.py` (4) | Present, unchanged. |
| Security Gate, preflight | Present, unchanged; part of the normal CI path. |
| Diana Gate, gate-input adapter, CI summary | Present; unchanged at acceptance. **Current `main`:** hardened post-M7 by `0f121ce` — strictly more is refused, nothing is newly admitted (POST-M7-E1-D1). |
| Hermes live adapter (`hermes_live.py`) | Unchanged at acceptance. **Current `main`:** replaced post-M7 by `0828801`/`f724038`; contract, `allowed_tools` and `read_scope` unchanged (POST-M7-E1-D1). |
| Expert inspection | The existing inspectors — journal read, run report, authority re-binding — surface contract, journal, actor, reconciliation, reason codes and raw evidence. |

The product path and the expert path **converge on the same runtime**: the same goal driven through
both reaches the same contract, byte for byte, and the product CLI calls the accepted entry points
rather than reimplementing them.

---

## B — Architecture map

### The governed path

```
                          USER
                            │
                    natural language
                            │
                            ▼
                     PRODUCT UX  (M7)
                            │
                intent → concrete proposal
                            │
                            ▼
                         DIANA
        authority · policy · durable state · verification
                            │
                exact approved envelope
                            │
                ┌───────────┴───────────┐
                ▼                       ▼
             BUILDER                 REVIEWER
          write · patch ·            read · search
        bounded commands             (read-only)
                │                       │
                └───────────┬───────────┘
                            │
                    Hermes / executor backend
                        (replaceable)
                            │
                            ▼
                  runtime / target repository
```

### The run lifecycle

```
  proposal ──approve──▶ contract + run policy + work items + topology
      │                            │
   (digest)                        ▼
      │                        run journal
      │                            │
      │                    ┌───────┴────────┐
      │                    ▼                ▼
      │                 attempt ─────▶ reconciliation
      │                    │                │
      │              (builder/reviewer)     │
      │                    │                ▼
      │                    └──────▶ COMPLETE / FAILED / BLOCKED
      │
   stale? ──▶ refused, no run created
```

### Post-M7 tracks — Track B complete; A, C and D are planning only

```
                       M1–M7 COMPLETE
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
  [PLANNED] TRACK A    [COMPLETE] TRACK B   [PLANNED] TRACK D
  Security Evidence     Mechanical Human      Distribution &
   Certification       Merge Approval —      Productization
                      M5-D20 DISCHARGED
                   (conditional: credential
                        separation)
        │                    │                    │
        │                    ▼                    │
        │          merge ≠ deploy: deployment     │
        │          approval still unaddressed     │
        │                                         │
        └─────────┬───────────────────────────────┘
                  ▼
          [PLANNED] TRACK C
       Certified Workflow Expansion
                  │
                  ▼
           broader production use
```

- **Track B is complete for merge.** M5-D20 is discharged, measured on live pull requests
  ([`DIANA-HUMAN-APPROVAL-B5.md`](DIANA-HUMAN-APPROVAL-B5.md)). The discharge is **conditional on
  credential separation**, an operational assumption that no GitHub rule enforces or detects
  (B5 §13.3). It did not address deployment, and merge approval is not deployment approval.
- **Tracks A, C and D are planning only.** Their roadmaps state that nothing in them is implemented;
  no security control is certified and no workflow class has been added.

This diagram was previously titled "Future tracks — none of these exist today" and showed Track B as
future. That was the pre-Track-B plan. The original dependency map is kept unedited in
[`DIANA-POST-M7-ROADMAP.md`](DIANA-POST-M7-ROADMAP.md), and the original Track B plan is kept in
[`DIANA-HUMAN-APPROVAL-ROADMAP.md`](DIANA-HUMAN-APPROVAL-ROADMAP.md).

---

## Post-M7 deltas on current `main`

*Post-M7 delta* — accepted after `431229e`, **not** part of the M1–M7 acceptance, and not folded into
A1–A7 above. Each row was checked against the commit and the current source. None of them adds a
tool, a network capability, merge authority or deploy authority.

| Delta | Where | What changed on current `main` | Stated limits |
|---|---|---|---|
| **Generic write-scope derivation** | `3e248fd` (#66); `diana/product/intent.py`, `catalogue.py` | A goal that names its own paths gets exactly those paths as write roots. A named path policy will not grant — absolute, `..`, a symlink escaping the repository, `.git`, `.github/…`, `.env*`, Diana's enforcement surface — is **refused**, not replaced. Only a goal naming no path falls back to the frozen roots `src`, `lib`, `tests`, `test`. | A named path is a *request* inside the intent; the contract is still built by the same builder and enforced at the same dispatch boundary. Directories must be written with `/` or `./`. |
| **Hermes Builder/Reviewer production-path repair** | `0828801`, `f724038` (#65, `97f718a`) | OAuth-backed provider resolution (fail-closed); the reviewer is *presented* only `projection.REVIEWER_TOOLS`; verdict extraction is a fail-closed parser over the complete final text; reviewer evidence is bound to the exact build under review; the Builder is given the approved task; reviewer turns get an explicit wall-clock bound. One controlled real dogfood run completed end to end. | **A validated repair, not a production-hardening claim.** Verdicts over 2,000 characters are proven by the deterministic suite only. `verdict.py`'s closed schema is untouched. |
| **CI gate-input hardening** | `0f121ce` (in #65) | Per-cause refusal reasons and annotations; a **deleted** review-sensitive path is now visible to the Gate (`--diff-filter=ACMRD`); the Gate recognises and quotes the adapter's failure envelope; the summary renders `FAIL` instead of a traceback. | The closed input schema, every decision path and every exit code are unchanged. Strictly more is refused; nothing is newly admitted (POST-M7-E1-D1). |
| **Mechanical human merge approval** | Track B (#64, `aec0820`); [`B5`](DIANA-HUMAN-APPROVAL-B5.md) | M5-D20 **discharged**: a zero-approval automation pull request is blocked with checks green; automation cannot approve its own pull request; a code-owner approval opens the merge path for that exact revision; a diff-affecting push dismisses it. See [A6](#a6--governance-layer). | **Conditional on credential separation**, an operational assumption no GitHub rule enforces or detects (B5 §13.3). Merge approval is **not** deployment approval; deployment approval remains unaddressed. |
| **One-approval autonomous recovery** | `2f3559f` (#67, `c8d6ecf`); `diana/autonomy/`, `diana/supervisors/` | Opt-in (`diana-do --autonomy`); off by default, and a manual proposal hashes to the same bytes as before. A digest-bound **standing approval** (the envelope, the executor and a closed autonomy policy with a fixed deny list and finite limits) is approved once with the root run. On a failure classified as recoverable, Diana may start **child recovery runs**, each with **its own run id**, each proven a subset of the standing approval on every axis, recorded in a digest-bound lineage, and all drawing on budgets that are **cumulative across the lineage**. The session ends `COMPLETE` or `BLOCKED_FOR_HUMAN`. | One approval authorizes one bounded session, rooted at one approved run — not permanent autonomy. A supervisor's recommendation is never authority. No session-level resume if the loop's own process is interrupted. Evidence is the deterministic `diana/autonomy/test-autonomy-*.sh` suites. |

**Two recovery mechanisms that must not be conflated:**

```text
crash recovery (M5, A4)                 autonomous recovery (post-M7, opt-in)
  same run id                             standing digest-bound approval
  same approved contract                        ↓
  same remaining budget                   bounded session lineage
  target moved → BLOCKED                        ↓
                                          root run + child recovery runs
                                                ↓
                                          each child has its own run id
                                                ↓
                                          aggregate budget across the lineage
```

---

## What is true on current `main`, in one table

Each row was re-checked against current `main`. Rows marked *post-M7* describe behaviour that did
not exist at the accepted revision.

| Claim | Status |
|---|---|
| Natural language starts a bounded run | **Yes** |
| Model output can grant authority | **No** — structurally |
| Approval binds an exact immutable proposal | **Yes** |
| Crash/restart resumes the same run | **Yes** |
| Autonomous recovery keeps the same run id | **No** (*post-M7*, opt-in) — child recovery runs get their own run ids under one standing approval |
| Builder and Reviewer under one envelope | **Yes** |
| Reviewer is read-only by enforcement | **Yes** |
| Backend switch creates authority | **No** |
| Security findings are certified | **No** — 75/75 UNPROVEN |
| Human merge approval is mechanically enforced | **Yes** (*post-M7*) — M5-D20 discharged (Track B); conditional on credential separation |
| Deployment approval is enforced | **No** — there is no deployment; merge approval is not deploy approval |
| Arbitrary workflows can be added by keyword | **No** — a class needs its own certification |
| Runs on macOS/Windows | **Unproven** — `/proc` and `flock` are Linux assumptions |
| The lease contains a hostile process | **No** — cooperative exclusion only |
