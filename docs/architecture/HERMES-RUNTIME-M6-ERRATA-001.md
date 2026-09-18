# HERMES-RUNTIME-M6 — ERRATA 001

Status: **normative erratum** to [`HERMES-RUNTIME-M6.md`](HERMES-RUNTIME-M6.md).
Scope: **enumerates, by set equality, the exact pre-existing production-code files M6 is permitted to
replace**, and freezes the two schema changes that make that set necessary.

`HERMES-RUNTIME-M6.md` remains **byte-identical**. It is not edited in place. Every frozen M6
decision (M6-D1 … M6-D24, M6-R1 … M6-R7, M6-REG-1 … M6-REG-5) and every acceptance criterion
M6-AC-1 … M6-AC-24 remains in force, unmodified, and this erratum weakens none of them. Where this
erratum and the frozen text appear to disagree about **authority**, the frozen text wins and this
erratum is the defect.

M1's D1–D38, M2's D1–D14, M3's D1–D16, M4's D1–D16 (as corrected by its `ERRATA-001` and
`ERRATA-002`) and M5's D1–D21 (as extended by its `ERRATA-001`) remain frozen and unmodified.

**This erratum grants no capability.** It adds no tool, no command, no scope, no network, no
subagent, no role, and no repository operation.

---

## 1. Why this erratum exists

M6-REG-2 as frozen says the permitted-replacement set "is stated by **set equality** at
implementation time and justified file-by-file from a frozen M6 decision", and then bounds it only by
**exclusion**. That is a deferral, and M5-REG-2 does not permit a deferral: it permits a replacement
of pre-existing behavior **only where a future milestone explicitly freezes and proves that
replacement**. A set that is to be written down during implementation has not been frozen; it has
been promised. Implementing against a promised set is how a replacement set grows by one file at a
time, each addition locally reasonable, with nothing to check the total against.

**A second defect, in M6's own reasoning, is corrected here.** Finding F18 argued that M6 "may evolve
M5's own modules" because `git diff --name-status 69f5569..HEAD` compares trees, so a file M5 first
added still classifies as `A` however often M6 modifies it. That observation is **true about git and
false as authority**. It describes what one acceptance assertion happens to measure, not what
M5-REG-2 permits. `diana/unattended/journal.py` is a frozen, proven M5 production module; from M6's
standpoint it is pre-existing, and replacing it requires an explicit freeze regardless of how git
classifies it. M6's implementation is bound by this section, not by F18's classification argument.

This is the same failure shape M4's `ERRATA-001` records: a milestone reaching into a neighbouring
module on a locally plausible justification, and reporting green. The remedy is the same — say
exactly which files, say why, and assert the set by equality.

---

## 2. The permitted replacement set — frozen, by set equality

**M6-E1-D1 — M6's permitted-replacement set of pre-existing production-code files is exactly these
four files, and no others.**

| File | Frozen decision requiring it | What M6 changes, and nothing else |
|---|---|---|
| `diana/runtime/blocking.py` | M6-D10, M6-D13, M6-R5, and failure-semantics rule 5 | **Adds** reason codes only. No existing code is removed, renamed, or given a new meaning. Already M5's declared replacement file, for the same additive reason. |
| `diana/unattended/journal.py` | **M6-D7**, which names this change explicitly: bump `JOURNAL_VERSION` to `3`, close the attempt-entry sub-schema, add `actor` to it | The record gains exactly one key (`actor_topology_digest`); the attempt entry becomes a closed key set including `actor`; `start_attempt` gains the `actor` parameter. The state machine, the transition table, the digest construction, the atomic-write path, M5-A1's rollback check and M5-A2's artifact-path check are **unchanged**. |
| `diana/unattended/unattended.py` | **M6-D16** (every actor turn is an attempt in the one list) and **M6-D20** (handoff at an attempt boundary) | `run_attempt` gains an `actor` parameter and passes it to `start_attempt`; `start_attempt`'s new attempt entry is initialised with `reconciliation_file: None` so the closed key set holds from creation. M5-D5's write-ahead ordering, the budget computation, `discharge_obligation`, item settlement and every terminal transition are **unchanged**. |
| `diana/unattended/report.py` | **M6-D4** with **M5-D17** (a blocked-item report must be legible to someone who was not watching) | `_attempt_summary` reports the attempt's `actor`. A multi-actor run whose report cannot say which actor produced which attempt does not satisfy M5-D17. Nothing else in the report changes, and it still carries no Hermes-proposed severity, risk or depth. |

**M6-E1-D2 — Everything else is excluded, and the exclusions that matter are named.** M6 modifies
**none** of: `diana/adapters/hermes_patches.py`, `diana/adapters/hermes.py`,
`diana/adapters/hermes_live.py`, `diana/adapters/selftest.py`, `diana/adapters/ao.py`,
`diana/runtime/contract.py`, `diana/runtime/read_scope.py`, `diana/mutation/mutation_policy.py`
(M5-D19, carried), `diana/mutation/reconcile.py`, `diana/mutation/remediate.py`,
`diana/mutation/remediation_driver.py`, `diana/unattended/ownership.py`,
`diana/unattended/recovery.py`, `diana/unattended/runpolicy.py`, `diana/unattended/workitems.py`,
or **anything** under `diana/ship/`, `diana/gate/`, `diana/security/`, `diana/preflight/`,
`diana/ci/`, `diana/playwright/`, `diana/hooks/`, `diana/skills/`, `diana/commands/`,
`diana/advisory/`, `diana/profile/` or `diana/runtime_verify/`.

Two exclusions are load-bearing and are stated as such:

- **`hermes_patches.py` is excluded, and F15 is why it can be.** Per-actor projection was measured
  working through the existing `install_capability(tools, MutationPolicy(envelope))` API. An
  implementation that finds itself needing to edit the boundary to make projection work has
  misunderstood M6-D14 and must stop, not widen this set.
- **`recovery.py` is excluded.** Topology verification on resume (M6-D10) is performed by M6's own
  module immediately after `recovery.load_run`, not inside it. A run with no declared topology has
  nothing to verify (M6-E1-D4), so no path skips a check that applies to it.

**M6-E1-D3 — Existing M5 test files are not modified.** `test-m5-unattended.sh`,
`test-m5-journal.sh`, `test-m5-ownership.sh` and `test_m5_proofs.py` stay byte-identical, and their
counts must be met or exceeded unchanged. The schema changes above are therefore constrained to be
backward-compatible with M5's own direct calls into the journal API — which is the point of
M6-E1-D5's sole-actor rule, not a convenience discovered afterwards.

---

## 3. The two schema changes, frozen

**M6-E1-D4 — The actor topology is an M6-owned document, `actors.json`, digest-bound in the journal.**
It follows the `run-policy.json` / `work-items.json` precedent exactly (M5-D2, M5-E1-D2): written once
by the approval path, covered by its own digest, with that digest recorded in the journal record as
`actor_topology_digest` and re-verified on every resume. A run that declares no topology carries
`actor_topology_digest: null` and **is** M5's single-actor run, unchanged in every respect.

This is **not** a second authoritative record of actor identity. It records which roles *exist* for a
run — a different fact from which role *acted* in an attempt, which lives in the journal attempt entry
and nowhere else (M6-D7). The two cannot disagree about the same thing because they do not state the
same thing.

**M6-E1-D5 — The journal attempt entry is closed, carries `actor`, and the default is permitted only
where it is unambiguous.** The attempt entry's key set is exactly:

```
attempt, state, started_at, snapshot_file, ended_at,
reconciled, within_envelope, turn_error, reconciliation_file, actor
```

`actor` must be a role named by the run's frozen topology. `start_attempt` accepts `actor=None`, and
resolves it as follows:

- `actor_topology_digest is None` — the run has one implicit actor, `BUILDER`, whose projection **is**
  the approved parent envelope. `None` resolves to `BUILDER`. This is M5's behavior described
  accurately, not a new grant: no M5 run ever had a second role to be confused with.
- `actor_topology_digest` is set — the run has a declared topology, so there is no unambiguous sole
  actor. `actor=None` is **`BLOCKED`** with its own reason code.

The default therefore exists exactly where ambiguity cannot, and is refused exactly where risk begins.
Two further properties keep it from becoming a soft edge: the label never confers authority — the
projection Diana installs does (M6-D14) — and the label's only authority-adjacent reader, verdict
acceptance (M6-R7), fails **closed** under a mistaken default, because a `BUILDER`-labelled attempt
can never produce a verdict.

---

## 4. Acceptance criteria added by this erratum

In force alongside M6-AC-1 … M6-AC-24, all of which remain unmodified.

| # | Criterion |
|---|---|
| **M6-E1-AC-1** | The set of modified pre-existing production-code files, computed from the repository, **equals** M6-E1-D1's four files exactly — asserted by set equality, not by containment, and failing if the set is either larger or smaller than declared. |
| **M6-E1-AC-2** | Each of `hermes_patches.py`, `mutation_policy.py`, `contract.py`, `ao.py`, `recovery.py`, `ownership.py`, `workitems.py` and every file under `diana/ship/` is proven **absent** from that set and byte-identical to `e61ba11`. |
| **M6-E1-AC-3** | The four M5 test files of M6-E1-D3 are byte-identical to `e61ba11`, and their suites pass at or above the counts finding F20 recorded. |
| **M6-E1-AC-4** | `actors.json` is digest-bound: a tampered topology document, a wrong `actor_topology_digest`, and a topology naming a role outside `{BUILDER, REVIEWER}` are each refused on resume with their **own** reason code. |
| **M6-E1-AC-5** | The attempt entry is closed: an attempt carrying an unknown key is refused as malformed, and an attempt missing `actor` is refused — neither is recorded and neither is repaired. |
| **M6-E1-AC-6** | The sole-actor rule holds in both directions: with `actor_topology_digest: null`, `start_attempt` without an actor yields `BUILDER`; with a topology declared, `start_attempt` without an actor is `BLOCKED`. |
| **M6-E1-AC-7** | A journal-version downgrade is refused: a record claiming `journal_version: 2` is refused as malformed by the version check, and the refusal is proven to be load-bearing by a falsifier. |

---

## 5. What this erratum does not do

- It does not widen any envelope, add any tool, or change `risk` or `depth`.
- It does not permit any additional file to be modified later. A file not in M6-E1-D1 requires its
  own erratum, with its own justification from a frozen decision.
- It does not retire, delete or modify any AO path (M6-D23, M6-D24 unchanged).
- It does not discharge M5-D20's carried merge-authority requirement.
