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
