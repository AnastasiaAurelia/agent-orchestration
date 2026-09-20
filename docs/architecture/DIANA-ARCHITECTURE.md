# Diana — Final Architecture (M1–M7 complete)

Status: **descriptive, not normative.** This document explains the system that the frozen M1–M7
specifications and their errata define. Where it appears to disagree with any frozen specification,
**the frozen specification wins and this document is the defect.**

Accepted base: `main` at `431229e` (PR #51). Milestones M1–M7 are accepted, with fourteen frozen
documents: seven specifications and seven errata.

Every claim below was read from the accepted specifications, audits and implementation rather than
recalled. Where a number appears, it was measured.

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

The Security Gate currently evaluates **75 controls** and reports **75/75 UNPROVEN**, which maps to
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

**No such rule is currently configured.** The accepted M4 audit records the repository ruleset as
`required_approving_review_count: 0`, `require_code_owner_review: false`,
`require_last_push_approval: false`. `CODEOWNERS` exists but has no merge-blocking effect without
code-owner review enabled.

> **M5-D20 remains undischarged.** `REQUIRE_HUMAN` is an advisory verdict, not a mechanical merge
> condition. Before any future milestone grants autonomous merge authority, it needs an independently
> verifiable human-approval mechanism that automation cannot self-satisfy.

**Two approvals that never convert into one another:**

- **Category A — Diana runtime approval.** "Start this exact bounded run." This is what `diana-do`
  asks for.
- **Category B — repository/deployment approval.** "This exact revision may merge or deploy."

No accumulation of A becomes B. M7 adds a human approval step for A, which is exactly what could be
misread as discharging M5-D20. It does not.

See [`DIANA-HUMAN-APPROVAL-ROADMAP.md`](DIANA-HUMAN-APPROVAL-ROADMAP.md) — **future work**.

---

## A7 — Legacy and expert surface

**Nothing has been deleted or retired.** All six slash commands, `ship.py`, `ao.py` and every CI
adapter are byte-identical to the accepted M6 base.

| Surface | Status |
|---|---|
| `/diana-ship` | Present and usable. Its **steps 2–5** (accept the goal, author the Definition of Done, classify risk, precheck) are **superseded for the certified product path only** — the M7 flow produces the same four outputs from one sentence. The command is not retired. |
| `/fix`, `/review`, `/ship`, `/orchestrate`, `/loop-audit` | Present, unchanged, expert/debug surfaces. |
| `ship.py` (10 subcommands), `ao.py` (4) | Present, unchanged. |
| Gate, Security Gate, preflight, adapters | Present, unchanged; part of the normal CI path. |
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

### Future tracks — **none of these exist today**

```
                       M1–M7 COMPLETE
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
  [FUTURE] TRACK A     [FUTURE] TRACK B     [FUTURE] TRACK D
  Security Evidence     Mechanical Human      Distribution &
   Certification           Approval          Productization
        │                    │                    │
        └─────────┬──────────┘                    │
                  ▼                               │
          [FUTURE] TRACK C ◀─────────────────────┘
       Certified Workflow Expansion
                  │
                  ▼
           broader production use
```

See [`DIANA-POST-M7-ROADMAP.md`](DIANA-POST-M7-ROADMAP.md).

---

## What is true today, in one table

| Claim | Status |
|---|---|
| Natural language starts a bounded run | **Yes** |
| Model output can grant authority | **No** — structurally |
| Approval binds an exact immutable proposal | **Yes** |
| Crash/restart resumes the same run | **Yes** |
| Builder and Reviewer under one envelope | **Yes** |
| Reviewer is read-only by enforcement | **Yes** |
| Backend switch creates authority | **No** |
| Security findings are certified | **No** — 75/75 UNPROVEN |
| Human merge approval is mechanically enforced | **No** — M5-D20 undischarged |
| Arbitrary workflows can be added by keyword | **No** — a class needs its own certification |
| Runs on macOS/Windows | **Unproven** — `/proc` and `flock` are Linux assumptions |
| The lease contains a hostile process | **No** — cooperative exclusion only |
