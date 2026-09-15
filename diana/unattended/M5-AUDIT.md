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

## 8. Independent development-handoff review — acceptance proof correction

Review baseline: `1dd5a9738cc684eb042eb4634ed8a23ccc7d73a4`, independently
inspected by Codex after the original Claude implementation session. The live
remote branch matched that commit. This is a review of the acceptance harness
and its claimed observations, not a claim that the entire implementation has
received a new independent security audit. The reviewer also authored these
corrections; their verification is described explicitly below.

The historical **168/0** result in §7 is retained as history. It included
unconditional and otherwise insufficient predicates, so that number did not
prove every property its labels claimed. No frozen specification or erratum was
changed. All findings below are **proof/harness defects**: the exercised real
implementation satisfied the corrected observations, and no production code
was changed.

### Requirements and findings

| Finding | Frozen property / defect | Corrected system observation |
|---|---|---|
| **M5-IR-1** | AC-17, D7/D17, M1 D35/D36: a blocked run cannot yield an advisory deliverable. The predicate was literal `True`. | Inspect the actual blocked run's persisted JSON files; require its run report to agree with the returned result, reject advisory deliverables, require `artifact.validate()` rejection and Security Track `MALFORMED`. |
| **M5-IR-2** | AC-17, D7: `BLOCKED` must be reachable and distinct. The check accepted `COMPLETE` too and used a clean run. | A separate crashed run with a gitignored escape must yield exactly `BLOCKED/reconciliation-mismatch`; clean and exhausted runs must yield exactly `COMPLETE/work-finished` and `FAILED/attempt-budget-exhausted`. Returned outcome, journal, persisted journal, and report must agree. |
| **M5-IR-3** | AC-20, D20: no PR, push, merge, or additional Git mutation authority. `all(... for tok in [])` always passed; neighboring source-string checks could match prose and miss equivalent invocations. | Inspect executable AST call sites across all M5 production modules, resolving import aliases, and the two concrete Git helper calls. Observe real subprocess launches during approval/execution, including reconciliation; permit exactly `git -C <target> rev-parse HEAD` and `git -C <target> status --porcelain`. Intercept network connections and unexpected subprocesses before effects. Require both allowed operations to have actually occurred. |
| **M5-IR-4** | AC-18 structural rejection: missing `ALLOWED_RUN_FIELDS` took an `else True` path; any exception from `artifact.validate()` counted as rejection. | Require the real schema constant, catch only `ArtifactError`, and let unexpected exceptions fail. Remove a duplicated rejection assertion that examined the same report twice. |
| **M5-IR-5** | AC-1, D5: the pre-turn snapshot must precede mutation. An impossible substring search over `[:0]` fell back to mere path presence. | Remove that misleading duplicate. Retain the existing comparison of the actual persisted hash against the known original source bytes. |
| **M5-IR-6** | AC-4: actual process death before the turn and SIGTERM must recover correctly. The purported before-turn crash was a normal no-op run; SIGTERM was not exercised. | Kill a real child immediately after durable `ARMED`; check signal, state, empty attempts, and unchanged source, then resume to exact completion. Separately SIGTERM a child after its in-scope mutation and prove owed reconciliation and exact completion. |
| **M5-IR-7** | AC-7/19: an allowed command must execute; unexpected exceptions must not masquerade as refusals. Absence of `diana:` let a missing-script failure pass; the dispatch helper stringified every exception. | Run a fixture command that prints a canary and require exit zero plus that output. Dispatch catches only Diana's `Blocked`; other exceptions propagate. |
| **M5-IR-8** | AC-21, D11: original contract bytes and verifier integrity must survive restart; the killed turn must not have a completed turn record. The byte comparison read the same path twice; the verifier check searched one token; turn-record absence had an unrelated one-attempt escape clause. | Save bytes before the live run and compare them after resume; require actual absence of the interrupted turn record. |
| **M5-IR-9** | AC-21: adversarial attempts in the same live run remain refused. The purported adversarial section was disabled by `if False`. | Execute forced denied-tool and out-of-scope-write probes inside the resumed live attempt, then run the real model. Require two refusals and no forbidden file. Remove the disabled code and unused self-source read. |
| **M5-IR-10** | AC-25, E1-D5/D8: invalid graphs must be rejected before arming/execution. The snapshot search filtered UUID run paths for the target label `graph`, selecting nothing. Empty-graph rejection accepted any exception name. | Observe the production transition and attempt boundaries during invalid approval; require the exact self-dependency reason and zero calls. Require `work-items-malformed` for the empty graph; unexpected exceptions propagate. |
| **M5-IR-11** | AC-26, E1-D15: completed items must not replay on restart. The sole test restarted a terminal run, so run-level refusal made item-level replay unobservable. | Retain exact terminal refusal coverage. Add a child that completes P then dies in Q; resume the nonterminal run, observe only Q's driver invocation, preserve P's completion, and require exact overall completion. |
| **M5-IR-12** | AC-27, E1-D13: cancellation yields exactly `FAILED/run-cancelled`. A redundant assertion accepted any of the three terminal states. | Use the same exact outcome/journal/report observation as AC-17 for cancellation and remove the broad redundant check. |
| **M5-IR-13** | AC-4/21: reconciliation reports must actually describe the interrupted attempts. `all()` over report attempts could pass if the report omitted them. | Require the constructed scenario's exact nonzero attempt count and each attempt's `within_envelope is True`. |

M5-D17 explicitly requires a blocked-item **run report**. In these tests it is
Diana run-record data, not an `ADVISORY_SECURITY_REVIEW` or certified evidence.
The structural checks preserve that distinction; they do not reinterpret a
blocked run as an advisory success or require removal of the run-record outcome.

### Falsification evidence

The acceptance-only helper `test_m5_proofs.py` is imported by the harness and
never by production. Production-boundary mutations are temporary in-memory
patches, restored by context managers. Persistence/output mutations alter only
test fixtures and are restored. No remote command or network canary is allowed
to take effect.

| Corrected observation | Counterexample and rejection reason |
|---|---|
| Exact terminal outcomes (IR-2, IR-12) | Patch the real finalizer to return another state for each constructed COMPLETE/FAILED/BLOCKED run. The shared exact-state predicate rejects each mismatch against the journal/report. |
| Report separation (IR-1, IR-4) | Patch the report builder/writer to emit advisory type, an advisory filename, or a Security Track field; separately make the advisory validator accept the report. The same structural predicate rejects every counterexample. Unexpected validator exceptions propagate, and deleting the schema constant fails instead of taking a default-pass branch. |
| Outward operations (IR-3) | Patch `observe_target` to attempt `git push`, `git commit`, `gh pr create`, `gh pr merge`, `gh api`, an extra `git status` option, or a socket connection. The process/network canary intercepts each before effects; the actual execution still finishes, but the observed-operation predicate rejects it. Temporary production-source copies containing an aliased outward call or changing `status` to `push` fail the AST observations. |
| Command execution/refusal (IR-7) | A nonzero command result without a Diana refusal fails the success predicate. An unexpected exception containing `diana:` propagates instead of passing as a refusal. |
| Same-live-run denials (IR-9) | Temporarily bypass the production dispatcher so both denied probes return acceptance. The shared live-denial predicate rejects the bypass. |
| Byte binding and turn-record absence (IR-8) | Change the contract at the same path, alter a verifier while retaining `sys.exit(1)` in a comment, and plant a completed turn record. Saved-byte and absence predicates reject these persisted counterexamples. |
| Reconciliation completeness (IR-13) | Remove the attempts or mark one outside the envelope in an actual run's report copy. The same count/within-envelope predicate rejects both. |
| Graph ordering and error identity (IR-10) | Patch the production graph builder to invoke the intercepted attempt boundary before rejecting the graph. The no-execution predicate fails despite the correct rejection code. A `RuntimeError` from graph construction propagates instead of satisfying the empty-graph test. |
| Item replay (IR-11) | Patch the real finalizer to replay P's driver despite preserving an otherwise correct COMPLETE result. The same resumed-call/status/outcome predicate rejects the extra P invocation. |
| Removed duplicate / unexecuted crash setup (IR-5, IR-6) | IR-5's weak duplicate is removed in favor of the already-existing known-original hash equality. IR-6 replaces the non-crashing setup with observed signal exits, durable state and source bytes. A real child returning normally from a no-op turn is rejected by the same before-turn-crash predicate. |

### Entire-suite vacuity review

Reviewed every assertion and setup in `test-m5-unattended.sh`, including its
new helper, against the frozen requirements. Searched literal predicates,
empty quantifiers, default-pass branches, permissive terminal membership,
self-source reads, comment/docstring matches, setup-guaranteed assertions, and
exception helpers. Remaining `all`/`any` checks have a nonempty witness/count
check or test an actual existential/absence property. Fixture `True`/`False`
work predicates intentionally drive the run; they are not acceptance verdicts.
The AC-13/16 source inspections operate on production AST nodes and exclude
comments/docstrings. AC-22's Git comparisons remain protected by explicit
nonempty-diff and nonempty-object checks. Contract substitution's two accepted
reason codes are binding failures, not alternative terminal outcomes.

A final reread caught a redundant report-existence check that had become
setup-guaranteed after replacing the search with a known run directory; it now
observes the actual returned report path. During correction, the new AST
inventory initially misclassified the local `git()` helper as an imported Git
client. That harness failure was fixed by resolving imported names; no
production behavior or acceptance requirement was relaxed.

The AST inventory is a bounded inspection of these production call sites, not
a general Python security analyzer. Behavioral process/network observation
covers the exercised approval, execution and reconciliation path; provider
network traffic belongs to the separately bounded live driver. Neither check
claims OS isolation or proves all hypothetical dynamically generated programs.

### Fresh results — measured on committed `f4ce851`

Every suite below was rerun from the committed tree after the harness
corrections, so each count corresponds exactly to committed files. The
historical **168/0** in §7 is retained as history and is **not** reused as a
fresh result.

| Suite | Result |
|---|---|
| M5 acceptance (AC-1…29) | **202 passed, 0 failed** |
| — of which falsification checks | **30 passed, 0 failed** |
| M5 journal | **54 passed, 0 failed** |
| M5 ownership | **29 passed, 0 failed** |
| M1 | **470 passed, 0 failed**, 9/9 suites, 13/13 criteria |
| M2 | **59 passed, 0 failed**, live provider |
| M3 | **106 passed, 0 failed** |
| M4 | **146 passed, 0 failed** |
| Pre-existing Diana regression | **26/26** suites green |

The live interrupted-and-resumed case ran again on this tree: a real Hermes turn
was killed at `TURN_ACTIVE` (`rc=-9`), a fresh process discharged the interrupted
attempt's obligation, and a second attempt under the byte-identical original
contract reached `COMPLETE`/`work-finished`.

### Independent verification of the correction commit

The corrections in `f4ce851` were themselves re-verified from the committed tree
by a reviewer who did not write them, against the seven properties the handoff
required. The harness's embedded Python was extracted and inspected as an AST —
**171 acceptance predicates** — rather than as text, so a predicate could not
satisfy a check by resembling one in prose.

| Property | Result |
|---|---|
| No literal unconditional `True` predicate | none found |
| No statically-empty `all()` / `any()` quantifier | none found |
| No `else True` / `or True` acceptance fallback | none found |
| No predicate accepting multiple frozen terminal outcomes | none found |
| No self-source assertion used as system evidence | none found |
| Corrected assertions carry meaningful falsifiers | 3 helper falsifiers, 30 `FALSIFY` assertions, all passing |
| No production behavior weakened to make tests green | `f4ce851` changes exactly two files, both acceptance-only |

`test_m5_proofs.py` is imported by the harness and by **no** production module,
confirmed by reading every production source in `diana/unattended/`. The
production modules — `journal.py`, `ownership.py`, `recovery.py`, `report.py`,
`runpolicy.py`, `unattended.py`, `workitems.py` — and `diana/runtime/blocking.py`
are **byte-identical** across `1dd5a97..f4ce851`.

### Known potentially-vacuous assertions remaining

**None known.** Every defect the independent review found (M5-IR-1 … M5-IR-13)
is corrected in the committed harness, and the structural re-verification above
found no instance of the five vacuity patterns it searched for.

Two limits on that claim, stated rather than implied:

- The re-verification is **structural plus falsification-backed**, not semantic.
  A predicate can satisfy every pattern above and still be weaker than its label
  suggests. The 30 `FALSIFY` assertions are the compensating evidence: each one
  constructs a counterexample and requires the corresponding predicate to reject
  it, so the predicates that matter most are shown to fail when they should.
- The AST inventory covers predicates inside `check(...)` calls in the harness's
  embedded Python. Setup code that silently makes an assertion trivially true is
  not detectable by that method; the review in §8 addressed those cases by
  reading, and found and removed one (a report-existence check that had become
  setup-guaranteed).

### Classification

All thirteen independent-review findings are **proof/harness defects**, not
production defects. The evidence for that classification is that the exercised
real implementation satisfied every corrected observation with **no production
change**: the corrections made the harness prove what its labels already
claimed, and the system already behaved that way. Had any corrected observation
failed against the real implementation, it would have been reclassified as a
production defect and fixed as one.
