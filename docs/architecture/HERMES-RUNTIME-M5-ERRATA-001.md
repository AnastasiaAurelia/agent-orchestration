# HERMES-RUNTIME-M5 — ERRATA 001

Status: **normative erratum** to [`HERMES-RUNTIME-M5.md`](HERMES-RUNTIME-M5.md).
Scope: **adds a minimal work-item, dependency and cancellation model to M5.** Every frozen
M5 decision (M5-D1 … M5-D21) and every acceptance criterion M5-AC-1 … M5-AC-22 remains in
force, unmodified, and this erratum weakens none of them.

`HERMES-RUNTIME-M5.md` remains **byte-identical**. It is not edited in place, and this
erratum does not rewrite it. Where this erratum and the frozen text appear to disagree about
the *shape of a run*, this erratum governs; where they appear to disagree about **authority**,
the frozen text wins and this erratum is the defect.

M1's D1–D38, M2's D1–D14, M3's D1–D16, M4's D1–D16 (as corrected by `ERRATA-001` and
`ERRATA-002`) remain frozen and unmodified.

**This erratum adds orchestration, not capability.** It grants no tool, no command, no scope,
no network, no subagent, no role, and no repository operation. M5's envelope remains M4's
`BOUNDED_REMEDIATION` envelope byte-for-byte.

---

## 1. When and how this was discovered

Found **after M5 was implementation-complete and its acceptance suite was green (125/0), and
after the post-implementation audit — but before any push, any pull request, and any merge.**

The omission was identified by comparing the delivered implementation against the M5
acceptance target as originally stated. The implementer reported the gap explicitly rather
than letting a green suite stand as completeness, and the governance decision is that these
semantics were **part of intended M5 behavior all along**, not M6 work.

So this is not a deferral being reversed. It is a **specification omission**: the frozen M5
text describes a run as one envelope with bounded attempts and never decomposes the work,
while the behavior M5 was meant to prove requires that a single approved envelope can carry
several bounded work items whose ordering is deterministic and Diana-owned.

**The frozen text is the defect here, and the remedy is this erratum rather than a silent
implementation extension.** M4's `ERRATA-001` exists because exactly that kind of silent
widening once reported green.

## 2. What the frozen specification says, and what it omits

`M5-D7` gives a run three terminal outcomes. `M5-D8` gives the **run** a state machine.
`M5-D16` gives the **run** an attempt budget. Nothing in the frozen text says what happens
when one approved envelope covers more than one unit of work, which leaves three questions
unanswered and therefore unprovable:

- whether a second unit may proceed when a first has failed;
- what stops a unit that logically depends on a failed one from running anyway;
- how an operator stops an unattended run that is behaving correctly but is no longer wanted.

Left unspecified, the first two would be answered by whatever the turn driver happened to do
— which is to say, by the model. That is the channel `M1 D14` denies it.

## 3. Frozen decisions

**M5-E1-D1 — A run carries an ordered set of WORK ITEMS, declared at approval and never
after.** Items are fixed when the envelope is approved. Neither Hermes nor a resumed process
may add, remove, rename or re-parent one. "Approve once" (M5-D12) extends to the shape of the
work, not only to the envelope: a run that could grow new items after approval would be a run
whose scope the approver never saw.

**M5-E1-D2 — Items live in an M5-owned document, digest-bound, exactly as the run policy
does.** `work-items.json` in the run directory, covered by `work_items_digest` in the journal
and re-verified on every resume alongside the contract and the run policy (M5-D11). No frozen
schema gains a field (M5-D2, M2-D5, M3-D4). A tampered item set is refused with its own reason
code.

**M5-E1-D3 — Each item has a stable Diana-owned identity.** A non-empty `id`, unique within
the run, assigned at approval. Identity is never derived from item text, ordering, or anything
Hermes emits, because an identity that can be recomputed from mutable input is not an identity.

**M5-E1-D4 — Item status is Diana-owned and durable, with exactly four values.**
`PENDING`, `RUNNING`, `COMPLETE`, `BLOCKED`. `COMPLETE` and `BLOCKED` are **terminal for the
item** and are never re-entered (M5-D8's rule, one level down). Status lives in the journal,
is written through the same crash-atomic path (M5-D4), and is **never inferred from Hermes
text**: an item becomes `COMPLETE` only after its required reconciliation has been discharged
and recorded.

**M5-E1-D5 — Dependencies are explicit, deterministic, and an acyclic graph.** Each item
declares `depends_on`, a possibly empty list of item ids in the same run. At approval the
graph is validated and **rejected fail-closed** for: a self-dependency, an unknown dependency
id, a duplicate item id, and any cycle. Validation happens before the run is armed, so an
invalid graph can never have executed anything.

**M5-E1-D6 — Eligibility is graph-derived, never model-asserted.** An item may start **iff**
its status is `PENDING` and **every** item in `depends_on` is `COMPLETE`. There is no other
route to execution, no override, and no channel by which Hermes can declare itself unblocked.
Dependency truth comes from Diana-owned durable state and from nothing else.

**M5-E1-D7 — `BLOCKED` propagates transitively, and independence is proven by the graph.**
When an item becomes `BLOCKED`, every item that depends on it **directly or transitively**
becomes `BLOCKED` with reason `dependency-blocked`, computed by walking the declared graph.
An item not reachable from a blocked item along that graph is **independent** and continues
normally, provided its own dependencies are `COMPLETE`.

This is the decision that makes "continue the independent work" safe. Independence is a
property of a Diana-owned DAG, computed the same way every time, and never a judgement about
what looks unrelated.

**M5-E1-D8 — A dependency-blocked item must not execute, and the proof is the driver.** The
turn driver is **never invoked** for an item that is not eligible. Acceptance asserts
non-invocation directly rather than asserting that the item "did nothing" after the fact:
M4-D13's rule — enforcement precedes the effect, proven by a canary rather than a log.

**M5-E1-D9 — A `COMPLETE` item is never re-entered on resume.** Completion is durable, and a
resumed process skips every `COMPLETE` item without calling its driver. The prohibition is on
**re-execution**, not merely on re-reporting: acceptance proves the driver is not called again.

**M5-E1-D10 — The attempt budget stays a property of the RUN, not of each item.** M5-D16's
`max_attempts` continues to bound the whole run. Attempts are additionally recorded per item
for the report, but items do **not** each receive a fresh budget — that would multiply the one
bound that stops an unattended run retrying indefinitely, which is precisely the reset the M5
audit found and fixed as `M5-A1`.

**M5-E1-D11 — Cancellation is Diana-owned, durable, and one-way.** A cancellation is recorded
in the journal through the crash-atomic path before it takes effect, and it survives restart.
Once recorded: **no new item may start and no new turn may begin.** Nothing Hermes does can
clear it — it is Diana-owned state in a directory outside `read_scope` and `write_scope`
entirely — and no resumed process may treat its absence-after-tampering as permission
(M5-D9: a tampered journal is refused, never re-interpreted).

**M5-E1-D12 — Cancellation never discharges an owed reconciliation.** M5-D6 is unchanged and
outranks it: a run with an outstanding obligation discharges that obligation **before**
terminating, even when cancelled mid-flight. Cancelling a run stops future work; it does not
declare the work already done to be unexamined. A cancellation that skipped the audit would
be a way to launder an out-of-envelope mutation into a clean stop.

**M5-E1-D13 — Cancellation terminates the run `FAILED`, with reason code `run-cancelled`.**
Stated explicitly rather than invented casually, and **no fourth top-level outcome is added**:
M5-D7's three terminal states remain exactly three. `FAILED` is the correct class by M5-D7's
own definition — *"the work did not finish for a reason that is **not** an envelope
violation"* — and cancellation is exactly that. `BLOCKED` would be wrong, because `BLOCKED`
means a Diana control refused or could not prove itself, and a cancelled run proved everything
it was asked to.

Ordering is deterministic and decides the edge case: if every item reached `COMPLETE` before
the cancellation was durably recorded, the run already terminated `COMPLETE` and is terminal
(M5-D8) — cancellation cannot reach back into it. If the cancellation was recorded first, the
run terminates `FAILED` / `run-cancelled`. The journal's own ordering is the arbiter, and it
is the only arbiter.

**M5-E1-D14 — The run's terminal outcome is derived from item state, and `COMPLETE` requires
every item.** The run is `COMPLETE` **iff every declared item is `COMPLETE`**. It is therefore
structurally impossible for a run to report `COMPLETE` while required work is unfinished. If a
cancellation is recorded, the run is `FAILED` / `run-cancelled`. If the budget or deadline is
exhausted, `FAILED` per M5-D13/D16. Otherwise, if no item is eligible and at least one item is
`BLOCKED`, the run is `BLOCKED`, carrying the reason of the first item that blocked.

**M5-E1-D15 — Terminal item and run states cannot be resurrected.** Already frozen for the run
(M5-D8); extended here to items, and enforced at the single place item status changes rather
than at each caller.

**M5-E1-D16 — This erratum adds no capability.** No tool, command, scope, network access,
subagent, actor or reviewer role, PR operation, deployment, credential access, or AO
migration. `M5-D20` is unchanged: M5 grants no merge authority. `M5-D1` is unchanged: the
envelope is M4's, byte-for-byte.

## 4. Correction to M5-AC-4

`M5-AC-4`'s "crash after the turn but before reconciliation" case asserted only that the run
reached *some* terminal state. That is too weak: it passes whenever the system terminates,
including with the wrong terminal class, which is the kind of assertion M4's audit criticised
for passing for the wrong reason.

> **M5-AC-4 (as corrected by ERRATA-001)** — a crash after the turn but before reconciliation
> leaves the same outstanding obligation, and a resumed process discharges it and reaches the
> **exact** terminal outcome the constructed scenario requires. For the scenario as
> constructed — a single in-scope mutation, no out-of-envelope write, and a work predicate
> that reports the work finished — that outcome is **`COMPLETE`** with reason `work-finished`,
> and the assertion must fail if any other terminal class is returned.

The corrected assertion is falsification-tested: forcing each other terminal class must make
it fail.

## 5. Acceptance additions

`M5-AC-23` … `M5-AC-29`, in addition to M5-AC-1 … M5-AC-22, all of which remain in force.

| # | Criterion |
|---|---|
| **M5-AC-23** | **Blocked independent item.** Item `A` becomes `BLOCKED`; independent item `C` still executes and reaches `COMPLETE`. |
| **M5-AC-24** | **Dependency blocked.** `B` depends on `A`; `A` is `BLOCKED`; `B`'s turn driver is **provably never invoked**, and `B` is recorded `BLOCKED` with reason `dependency-blocked`. Transitive blocking is asserted through at least one intermediate item. |
| **M5-AC-25** | **Cycle and corrupt graph.** A self-dependency, an unknown dependency id, a duplicate item id and a cycle are each **rejected fail-closed before anything executes**, each with its own reason code; a tampered `work-items.json` is refused by digest. |
| **M5-AC-26** | **Completed item is not replayed.** An item completes; the process restarts; the item's driver is **not called again**, and its status is still `COMPLETE`. |
| **M5-AC-27** | **Durable cancellation.** A run is cancelled; the process restarts; **no new turn executes**, cancellation survives, and the run terminates `FAILED` with `run-cancelled`. |
| **M5-AC-28** | **Cancellation does not skip reconciliation.** A run cancelled while an obligation is outstanding still discharges that obligation, and an out-of-envelope mutation left by the interrupted turn is still detected. |
| **M5-AC-29** | **Hermes cannot alter item, dependency or cancellation state.** Writes to `work-items.json` and the journal through the real dispatch path are refused, the files are byte-unchanged, and no item's status, dependency set or cancellation flag can be changed by anything the agent can reach. |

## 6. What this erratum does not do

- It does not modify `HERMES-RUNTIME-M5.md` or any M1–M4 specification or erratum.
- It does not amend, rebase or rewrite any existing commit. M5's implementation commits stand.
- It does not relax any enforcement control, acceptance criterion, or boundary.
- It does not add a fourth top-level run outcome (M5-E1-D13).
- It does not grant capability of any kind (M5-E1-D16), and it is not M6: there is no
  multi-actor orchestration, no reviewer role, no `delegate_task`, and no AO migration.
