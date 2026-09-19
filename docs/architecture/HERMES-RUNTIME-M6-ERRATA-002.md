# HERMES-RUNTIME-M6 — ERRATA 002

Status: **normative erratum** to [`HERMES-RUNTIME-M6.md`](HERMES-RUNTIME-M6.md).
Scope: **narrowly corrects the run-lock guarantee of M6-D11 and audit finding M6-A4.**

`HERMES-RUNTIME-M6.md` and [`HERMES-RUNTIME-M6-ERRATA-001.md`](HERMES-RUNTIME-M6-ERRATA-001.md)
remain **byte-identical**. Neither is edited in place. Every frozen M6 decision (M6-D1 … M6-D24,
M6-R1 … M6-R7, M6-REG-1 … M6-REG-5), every M6-E1 decision, and every acceptance criterion remains in
force and unweakened. Where this erratum and the frozen text appear to disagree about **authority**,
the frozen text wins and this erratum is the defect.

M1's D1–D38, M2's D1–D14, M3's D1–D16, M4's D1–D16 (as corrected by its errata) and M5's D1–D21 (as
extended by its erratum) remain frozen and unmodified. **All unrelated M5 semantics stay frozen.**

**This erratum grants no capability.** No tool, no command, no scope, no network, no subagent, no
role, no repository operation. It removes an authority path; it adds none.

---

## 1. The defect, as measured

M6-D11 states that at most one actor is live at any instant in a run, and audit finding M6-A4 claimed
to establish it with an exclusive `flock` taken in `actors.execute`.

**The lock is entry-point-scoped, and the exclusion it establishes is therefore only between callers
that take it.** `diana/unattended/unattended.py` exports `execute`, `discharge_obligation` and
`run_attempt` publicly and unchanged from M5. A second process that calls M5's `execute` against the
same run takes no lock at all.

Measured on a real run whose journal was durably `TURN_ACTIVE` with the lease held by another
process: the peer proved quiescence (the owning executor is invisible to `owned_pids` by design —
Phase 0 F16, the stamp marks descendants and never the setter), **computed the reconciliation, wrote
`reconciliation-001.json`, closed the attempt with `reconciled: true`, and drove the journal to
`ARMED`** — restoring retry eligibility — before finally refusing with `actor-not-recorded` at the
actor check inside `start_attempt`.

So a peer reconciled an effect while the owning actor was still alive and its command tree could
still be writing. That is a reading of a moving object: the precise failure M5-D15's quiescence
premise exists to prevent, and the exclusion M6-A4 intended to establish. **A refusal that arrives
after reconciliation, after attempt closure and after the journal has advanced is too late.**

---

## 2. The choke point, derived from call paths rather than assumed

The independent reviewer proposed `discharge_obligation`. That was verified against the code before
anything was edited, by enumerating every path that can advance a run:

| Public entry in `unattended.py` | Can it advance an M6 run? | Guarded by this erratum |
|---|---|---|
| `discharge_obligation` | **Yes** — reconciles, closes the attempt, transitions `TURN_ACTIVE → RECONCILING → RECONCILED`, restoring retry eligibility. This is the load-bearing path. | **Yes** |
| `run_attempt` | **Yes** — begins a new turn. Its first effect is writing `pre-turn-snapshot-<n>.json`, which happens **before** the actor check inside `start_attempt` refuses an M6 run. | **Yes** |
| `execute` | Only through the two above; it is also where M6's own lease is taken. | Transitively |
| `approve` | Creates a run; there is no live lease to violate. | No |
| `cancel` | Advances state, but M5-E1-D11 makes cancellation deliberately safe concurrently: it starts no turn, and M5-E1-D12 still requires the obligation to be discharged first. Guarding it would break operator cancellation of a wedged run, which is the one action that must remain available. | **No, deliberately** |
| `read_result`, `_`-prefixed helpers | Read-only, or reached only through the above. | No |

`diana/multiactor/actors.py` reaches both guarded functions and holds the lease around them, so M6's
own path is unaffected except that it must now identify itself.

---

## 3. Authorized replacement scope — narrow, and named

**M6-E2-D1 — The permitted pre-existing production FILE SET is unchanged: exactly the four files of
M6-E1-D1.** This erratum widens it by nothing. `diana/adapters/hermes_patches.py`,
`diana/mutation/mutation_policy.py`, `diana/runtime/contract.py`, `diana/unattended/recovery.py`,
`diana/unattended/ownership.py`, `diana/unattended/workitems.py`, `diana/adapters/ao.py` and
everything under `diana/ship/` remain excluded and byte-identical.

**M6-E2-D2 — Inside `diana/unattended/unattended.py`, exactly two functions gain exactly one
behavior.** "The file is already permitted" is **not** blanket authority, and this decision exists so
that it cannot be read as such. The authorized change is:

| Function | Authorized addition | Explicitly not authorized |
|---|---|---|
| `discharge_obligation` | A lease check as its **first** statement, before `open_attempt`, before any transition, before quiescence, before the snapshot is read and before any reconciliation is computed. | Any change to quiescence, the diff, the snapshot handling, the attempt update, the transitions, the return shape, or M5-A2's artifact-path check. |
| `run_attempt` | The same lease check as its **first** statement, before the pre-turn snapshot is written. | Any change to M5-D5's write-ahead ordering, the stamp, the budget, the driver call, the turn record or the obligation discharge that follows. |

Nothing else in `unattended.py` changes. `_budget_state`, `_settle_item`, `_block_dependents`,
`_terminate`, `_finish`, `execute`, `approve`, `cancel` and `read_result` are untouched.

**M6-E2-D3 — The lease primitive is a new file, `diana/runtime/runlease.py`.** An addition, not a
replacement. It lives under `diana/runtime/` because both `unattended.py` and `diana/multiactor/`
already depend on that package, so the guard introduces no dependency from an M5 module onto an M6
one, and no import that could fail and be tempted into a fail-open fallback.
`diana/multiactor/runlock.py` becomes a thin caller of it rather than a second implementation of the
same thing.

---

## 4. The required property

**M6-E2-D4 — No process may discharge, reconcile, retry or otherwise advance a run while another
process holds that run's live lease, and the refusal must precede every effect.**

Concretely, a refused peer must leave **no** trace: no `reconciliation-<n>.json`, no
`pre-turn-snapshot-<n>.json`, no attempt closed, no `reconciled` flipped from `false` to `true`, no
transition toward `ARMED`, no new attempt, no mutation of the target. It receives exactly one frozen
reason code: **`actor-handoff-refused`**.

This holds whichever entry point the peer uses. It is not a property of `actors.execute`; it is a
property of the run.

---

## 5. Lease design, from measured `flock` semantics

The semantics were **measured before the design was chosen**, not assumed:

| Measured | Result | Consequence for the design |
|---|---|---|
| Second `flock(LOCK_EX\|LOCK_NB)` on a **different fd in the same process** | **`EAGAIN`** | A guard that *acquires* would **deadlock the legitimate owner**, which already holds the lease on another descriptor. The guard must therefore **probe, never acquire**. |
| Closing a separate probe fd | Owner's lock still held | Probing is non-destructive and safe to do on every call. |
| Re-`flock` on the **same** fd | Succeeds (idempotent) | Not usable: the guard does not have the owner's descriptor. |
| Another **process** | `EAGAIN` | Cross-process exclusion is real. |
| Owner `SIGKILL`ed | Lock released | No stale-lock heuristic, and none of M5-D14's PID-recycling exposure. |
| Lock **file** after release | Persists | Liveness is the `flock`, never the file's existence. A stale file must never block recovery. |
| `fork`, parent exits, child lives | **Still held** | `flock` follows the open file description, so a descendant retains the lease. Documented and tested, not wished away. |

**M6-E2-D5 — The lease is proven by probing, and ownership is the recorded holder identity.**
`require_lease(run_directory)`:

1. No lock file → no lease exists → **proceed**. This is M5's own standalone behavior, unchanged, and
   is why every M5 run and every M5 test keeps working untouched.
2. Probe `flock(LOCK_EX | LOCK_NB)` on a **fresh descriptor**, then release and close it.
   Acquired → **no live owner** → proceed.
3. Refused → a live owner exists. Read the holder record written at acquisition. If it names **this
   exact process** — pid **and** `/proc` start time, the same recycling defence as M5-D14 — the
   caller *is* the owner → proceed. Otherwise → `Blocked(actor-handoff-refused)`.

No token is passed through the call signatures, so M5's own callers are unchanged and no new argument
can be forgotten at a call site.

**M6-E2-D6 — The lease is not authority, and is not containment.** It answers exactly one question:
*may this process act on this run right now?* It grants nothing, widens nothing, and constrains
nothing about what a process does once admitted. It is an exclusion protocol, and this erratum does
not claim it is more.

**M6-E2-D7 — Environment stamps are never authority.** `DIANA_RUN_ID` keeps its M5-D14 meaning —
per-PID ownership of *descendants*, for quiescence — and is not read by the lease. No `DIANA_ACTOR`
or equivalent is introduced, read, or trusted.

**M6-E2-D8 — The threat model is stated, not overstated.** The holder record is a file readable and
writable by the same user, so this is not a defence against a hostile same-user process — such a
process can already rewrite the journal, which M5's threat model records as undetected. What it does
close completely is the defect actually measured: a **legitimate older entry point silently advancing
a live run**. Claiming more would be the kind of overstatement M4's audit warns against.

---

## 6. Acceptance criteria

In force alongside all M6 and M6-E1 criteria, none of which is modified.

| # | Criterion |
|---|---|
| **M6-E2-AC-1** | With executor A holding the lease and an attempt durably `TURN_ACTIVE`, a peer process calling **M5's public `unattended.execute`** is refused with exactly `actor-handoff-refused`. |
| **M6-E2-AC-2** | The refused peer leaves **no effect**, asserted item by item: no `reconciliation-<n>.json`, no new `pre-turn-snapshot-<n>.json`, the attempt still `state: OPEN` with `reconciled: false`, the journal still `TURN_ACTIVE`, no new attempt, and the target byte-identical. |
| **M6-E2-AC-3** | The same refusal holds for a peer calling `discharge_obligation` directly and for one calling `run_attempt` directly. |
| **M6-E2-AC-4** | The legitimate owner is **not** deadlocked: A, holding the lease, completes its own run through both guarded functions. |
| **M6-E2-AC-5** | After A dies, a fresh executor acquires the lease, proves quiescence, and discharges the obligation **exactly once** — one reconciliation record for that attempt, and the run proceeds. |
| **M6-E2-AC-6** | A stale lock **file** with no live `flock` does not block recovery. |
| **M6-E2-AC-7** | A crash releases the lease, proven by killing a real holder process. |
| **M6-E2-AC-8** | `fork` inheritance is tested and documented: a descendant retains the lease after the acquirer exits, and the run is refused rather than silently advanced. |
| **M6-E2-AC-9** | No process-group, name-pattern or broad PID action is introduced anywhere by this erratum (M5-D14). |
| **M6-E2-AC-10** | A run with **no** lease file behaves exactly as M5 always did, and M5's acceptance, journal and ownership suites remain green at or above their recorded counts, byte-unmodified. |
| **M6-E2-AC-11** | The modified pre-existing production file set still equals M6-E1-D1's four files exactly, and `diana/runtime/runlease.py` is an addition. |
| **M6-E2-AC-12** | `HERMES-RUNTIME-M6.md` and `HERMES-RUNTIME-M6-ERRATA-001.md` are byte-identical, as are all M1–M5 specifications and errata. |

---

## 7. What this erratum does not do

- It does not widen the permitted file set, or authorize any change to `unattended.py` beyond the two
  named functions.
- It does not change quiescence, reconciliation, budgets, item semantics, cancellation, or any
  terminal-state rule.
- It does not make the lease a containment primitive, or a defence against a hostile same-user
  process.
- It does not retire, delete or modify any AO path.
- It does not discharge M5-D20's carried merge-authority requirement.
