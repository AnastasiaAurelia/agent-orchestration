# M5 Audit Record — Unattended Bounded Execution

Spec: [`HERMES-RUNTIME-M5.md`](../../docs/architecture/HERMES-RUNTIME-M5.md).
Branch: `feature/hermes-unattended`, based on accepted M1–M4 `main` (`69f5569`, which
includes `ERRATA-002`). Frozen in its own commit `5bfbb34` before any implementation.

## 1. Process

M5 restores the M1–M3 discipline that M4's audit §1 recorded as broken: the specification
was frozen in a **dedicated commit** (`5bfbb34`) before any implementation existed, so
"the spec predates the code" rests on a git boundary rather than on filesystem mtimes.

## 2. Nature of this audit — stated plainly

This is a **fresh-pass adversarial audit written against the finished implementation**,
attacking through real code paths rather than reading the code's claims about itself. It
found two real defects.

It is **not** an independent third-party audit. M4's §7 is the standing evidence for why
that distinction matters: the first genuinely independent reviewer to examine M4 found two
further defects in a control the self-audit had already signed off. The same exposure
applies here, and more sharply, because the audit fixes below are the newest code in the
milestone.

## 3. Attack surface exercised

Sixteen attack classes were driven through real implementation paths. Model cooperation was
never treated as evidence: every adversarial case forces the call itself.

| Attack | Result |
|---|---|
| Illegal state transitions | refused — `TURN_ACTIVE` reaches only `RECONCILING`, so no path skips the audit |
| Terminal-state resurrection | refused at the single transition choke point (`run-already-terminal`) |
| Contract substitution | refused (`contract-digest-mismatch` / `contract-run-id-mismatch`) |
| Digest / run-id substitution | refused; a foreign journal placed in the directory is refused by `run-id-mismatch` |
| Target freshness bypass | editing `target_binding` is refused by `journal-digest-mismatch` |
| Freshness ordering | `target-moved` is raised with **zero** driver invocations |
| Stale-state replay / rollback | **DEFECT M5-A1**, fixed below |
| Tampered journal | schema-invalid tamper caught by the closed schema, schema-valid tamper by digest |
| Retry reset by policy edit | refused (`run-policy-digest-mismatch`) |
| Crash between effect / reconciliation / persistence | reconciled on resume at three distinct crash points |
| Crash in the `start_attempt` → `TURN_ACTIVE` window | recovers cleanly; no mutation is possible before `TURN_ACTIVE` |
| Process leakage | stragglers observed, terminated, and proven gone |
| PID ownership spoofing | start-time pairing defeats recycling; see the NOTE in §5 |
| Model-written state masquerading as Diana state | every Hermes write to the run directory refused, journal byte-unchanged |
| Symlink / state-path attacks | run directory and journal covered; per-attempt artifacts were **DEFECT M5-A2**, fixed below |
| Authority drift after resume | every attempt sees the byte-identical approved contract |

## 4. Findings

### M5-A1 — a rolled-back journal passed every integrity check

The digest proves a record is one Diana **wrote**; it does not prove it is the **latest**
one Diana wrote. Restoring a journal saved earlier in the same run therefore verified
cleanly while rewinding `attempts` — resetting the retry budget of M5-D16, which is the
bound that stops an unattended run retrying indefinitely.

Measured against the pre-fix module: `attempts` went **2 → 1** with a valid digest, while
attempt 2's artifacts were still on disk.

**Fix.** `journal.read()` now refuses a record that the artifacts contradict: a
reconciliation record for attempt *N* proves attempt *N* was journaled, so a journal
claiming fewer attempts is stale (`journal-stale`). The snapshot bound is one higher,
because M5-D5 writes the pre-turn snapshot **before** the attempt is journaled — exactly one
un-journaled snapshot is legitimate, and a test pins that window so the check cannot become
a false positive that makes every crashed run unresumable.

### M5-A2 — a per-attempt artifact was read through a symlink

`discharge_obligation` joined the journal's `snapshot_file` onto the run directory and
opened it. The **name** is digest-protected; the **file** is not. Replacing
`pre-turn-snapshot-001.json` with a symlink requires no digest change, and Diana would then
reconcile against a "before" state chosen by whoever planted the link — the audit reading a
baseline the attacker supplied. `open_dir` already applied this reasoning to the directory
and the journal; it had not been applied one level down.

**Fix.** `journal.read_artifact()` refuses anything that is not a plain basename naming a
regular, non-symlink file inside the run directory, and the refusal drives the run to a
terminal `BLOCKED` state carrying `journal-path-unsafe` rather than propagating raw.

### Two flagged findings that were audit-harness bugs, not defects

Recorded because the failure mode matters more than the count. Both "freshness bypass" and
"freshness ordering" initially reported as defects. The fixture used
`git commit --allow-empty -qm ... -c user.email=...`, where `-c` **after** the subcommand
means *reuse commit message*, not *set config*: the commit failed, the target never moved,
and the control correctly did nothing. With the invocation corrected, both controls fire.
An audit that does not verify its own setup manufactures findings as readily as it misses
them.

## 5. Residual limitations

- **The stamp is not a containment boundary.** A declared command can shed it by re-exec'ing
  a child with a scrubbed environment. A process cannot disown itself in place, and both
  halves are asserted separately so the limitation cannot silently change. This sits inside
  M4's carried assumptions: exact-match allowlist, only as good as what Diana declares, not
  a sandbox.
- **PID ownership spoofing (NOTE, not a defect).** A same-user process that sets
  `DIANA_RUN_ID` to this run's id will be treated as owned and terminated. It gains no
  authority by doing so — it can only invite its own termination — and a different user's
  process cannot be read, so it is never claimed. Recorded rather than fixed because the
  frozen mechanism is the stamp.
- **Journal integrity is not authentication.** The digest defends against corruption,
  truncation, staleness and rollback. It does not defend against an adversary who can
  already write arbitrary bytes to the run directory and recompute it. What keeps Hermes out
  is that the run directory lies outside `read_scope` and `write_scope` entirely — asserted
  behaviorally, not assumed.
- **Quiescence covers processes Diana can see.** A command that hands work to a system
  service, container, or remote host leaves no stamped descendant.
- **`/proc`-dependent.** On a platform without it, quiescence is unprovable and the correct
  behavior is `BLOCKED`, which is what the code does.
- **No independent third-party review**, per §2.

## 6. Results

| Suite | Result |
|---|---|
| M5 acceptance | **125 passed, 0 failed** (incl. the live interrupted-and-resumed run) |
| M5 journal | **42 passed, 0 failed** |
| M5 ownership | **29 passed, 0 failed** |
| M1 | **470 passed, 0 failed**, 9/9 suites, 13/13 criteria |
| M2 | **59 passed, 0 failed**, live provider |
| M3 | **106 passed, 0 failed** |
| M4 | **146 passed, 0 failed** |
| Pre-existing Diana regression | **26/26** suites green |

`M2-AC-4`'s known live-provider sensitivity (M4 audit §6) was observed again: one run
reported 57 assertions and another 59, both with **zero failures**. The assertion measures
enforcement *coverage* — how many of five forced corruptions the model's tool calls allowed
to be injected — not enforcement itself.


## 7. ERRATA-001 round — work items, dependencies, cancellation

Scope: [`HERMES-RUNTIME-M5-ERRATA-001.md`](../../docs/architecture/HERMES-RUNTIME-M5-ERRATA-001.md),
frozen in its own commit before implementation, per the M1–M3 discipline.

### Two semantics the implementation had to settle

Recorded because both were resolved toward the frozen text rather than toward whichever
reading was easier to build.

**An envelope violation still terminates the run immediately.** ERRATA-001 says independent
items may continue when one item blocks. Frozen `M5-D8` draws `RECONCILING -> BLOCKED` for a
mismatch, and `M4-D15` says a mismatch blocks and yields no deliverable. The erratum's own
precedence rule is that where it appears to disagree with the frozen text about **authority**,
the frozen text wins — so independent items do **not** continue past a mismatch. A **turn
failure with a clean reconciliation** is a different kind of event: the work failed, nothing
escaped. That blocks only its own item and its transitive dependents, and independent work
proceeds. This distinction is what makes "continue the independent work" safe rather than
reckless: the run keeps going only when nothing left the envelope.

**Budget exhaustion is derived before the blocked-item derivation.** Frozen `M5-D13`/`M5-D16`
make budget exhaustion `FAILED`. Deriving it after the blocked-item rule let one unfinished
item make an out-of-budget run report `BLOCKED` instead — found by the acceptance suite, fixed
by following `M5-E1-D14`'s stated order exactly. An unfinished item at budget exhaustion is
re-armed rather than blocked, because "the work did not finish" is a fact about the run's
budget, not a boundary failure of that item.

### Focused audit — eleven attack classes, zero defects

| Attack | Result |
|---|---|
| Dependency bypass by forging item status | refused (`journal-digest-mismatch`) |
| Cycle injected into `work-items.json` after approval | refused (`work-item-cycle`) |
| Foreign item set substituted | refused (`work-items-digest-mismatch`) |
| Forged replay of a completed item | refused; driver not re-invoked |
| Cancellation erased from the journal | refused (`journal-digest-mismatch`) |
| Cancellation skipping an owed reconciliation | reconciliation still ran; the escape was still detected |
| Blocked item wrongly blocking independent work | independent item still completed |
| Independent work wrongly bypassing a real dependency | dependent item provably never ran |
| Retry budget reset per item | budget stayed per-RUN (3 turns across 2 items) |
| Authority drift through durable item state | refused (`journal-digest-mismatch`) |
| Journal item set exceeding the approved graph | refused (`work-items-malformed`) |

### M5-AC-4 correction, falsification-tested

The old form accepted any terminal state and so could pass for the wrong reason. The corrected
criterion names the exact expected outcome (`COMPLETE`/`work-finished`) for the constructed
scenario. Forcing the run to `FAILED` and to `BLOCKED` each makes it fail, as required by
ERRATA-001 §4.

### One vacuous assertion written and removed during this round

A journal assertion was first written as `raises(...) is False or True`, which is always true —
the exact accidental-pass pattern M3's audit had already found once, reproduced here by the
same author who cited it. It was caught before commit and replaced with a real check against a
fresh `PENDING` item, plus a positive counterpart proving `PENDING -> BLOCKED` is legal.

### Results

| Suite | Result |
|---|---|
| M5 acceptance (AC-1..29) | **168 passed, 0 failed** |
| M5 journal | **54 passed, 0 failed** |
| M5 ownership | **29 passed, 0 failed** |
| M1 | **470 passed, 0 failed** |
| M2 | **59 passed, 0 failed**, live provider |
| M3 | **106 passed, 0 failed** |
| M4 | **146 passed, 0 failed** |
| Diana regression | **26/26** green |

### Residual limitations added by this round

- **Independent continuation is bounded by the run budget, not by item count.** With
  `max_attempts` shared across items, a run with many items can exhaust its budget before
  reaching later ones. That is deliberate (`M5-E1-D10`) — a per-item budget would multiply the
  bound that audit finding `M5-A1` existed to protect — but it means item ordering within the
  declaration affects which items get attempted when budget is scarce.
- **Cancellation is cooperative with respect to a turn already in flight.** It prevents the
  *next* turn from starting; it does not interrupt a running one. A turn in flight is bounded
  by the wall-clock deadline that `M2-D12` and `M5-D13` already impose.
