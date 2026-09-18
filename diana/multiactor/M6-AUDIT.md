# M6 — implementation audit record

Scope: `HERMES-RUNTIME-M6.md` (frozen `e61ba11`) as narrowed by
`HERMES-RUNTIME-M6-ERRATA-001.md` (`8b806b5`). Written during implementation, not after it.

---

## 1. Preflight, before any code

The instruction was to verify that the frozen spec **enumerates** the pre-existing production files
M6 may replace. It does not: `M6-REG-2` says the set "is stated by **set equality** at implementation
time" and then bounds it only by exclusion. That is a promise, not a freeze, and M5-REG-2 permits a
replacement only where a milestone **explicitly freezes and proves** it.

Implementation therefore stopped and `ERRATA-001` was written first. It also corrects a defect in M6's
own reasoning: **finding F18 argued that git's `A`/`M` classification against an old baseline licenses
M6 to evolve M5's modules.** That is true about git and false as authority — it describes what one
assertion happens to measure, not what M5-REG-2 permits. The permitted set is now four files, each
justified from a frozen decision, asserted by **equality** in `M6-E1-AC-1`, and computed against the
**working tree** rather than `HEAD` so it is a check on the implementation rather than on the commit.

---

## 2. What was built

| Added | Purpose |
|---|---|
| `diana/multiactor/topology.py` | The frozen two-role topology as a digest-bound M6-owned document (M6-D1, M6-E1-D4). |
| `diana/multiactor/projection.py` | Role projections of the ONE approved envelope, proven ⊆ on four axes (M6-D12…D14). |
| `diana/multiactor/verdict.py` | The closed reviewer-verdict schema and its provenance check (M6-R5, M6-R7). |
| `diana/multiactor/actors.py` | Diana-owned actor selection, projection installation with a live proof, and the run loop (M6-D15, M6-D20). |
| `diana/multiactor/executors.py` | Backends at the seam M5 already had, including two deterministic ones and the real-model pair (M6-D21). |
| `diana/multiactor/runlock.py` | One live executor per run (audit finding M6-A4). |
| `diana/multiactor/vacuity_audit.py` | An AST vacuity auditor for acceptance suites. |
| `diana/multiactor/test-m6-multiactor.sh` | Acceptance: **174 passed / 0 failed / 21 falsifiers**. |
| `diana/multiactor/test-m6-audit.sh` | Independent attack pass: **25 repelled / 0 through**. |
| `diana/multiactor/test_m6_child.py` | A killable child so crash cases are real kills of real processes. |

**Modified — exactly the four frozen files**, and nothing else: `diana/runtime/blocking.py` (11 reason
codes added, nothing altered), `diana/unattended/journal.py` (version 3, closed attempt entry,
`actor`), `diana/unattended/unattended.py` (`run_attempt` gains `actor`), `diana/unattended/report.py`
(the report names each attempt's actor). Asserted by set equality; `hermes_patches.py`,
`mutation_policy.py`, `contract.py`, `recovery.py`, `ao.py` and all of `diana/ship/` are asserted
byte-identical to the freeze.

---

## 3. Findings

Four concrete defects were found and fixed. Two were found by attacking the implementation before the
formal audit pass; two by the audit pass itself. All four are in M6's own code.

### M6-A1 — the scheduling ledger was on the authority path

`actors.py` kept `review-state.json` recording which attempt last built an item, and its docstring
claimed nothing authority-relevant was read from it. **The claim was false**: `_accept_review` took the
reviewed attempt number *from the ledger*, which was not digest-bound. A ledger edited to name an older
build would have had a genuine reviewer verdict bless work the reviewer never saw, and nothing would
have noticed.

*Fix:* the ledger is **deleted**, not authenticated. Every scheduling input is derived from the
digest-covered journal, and the only per-run memory is held for one `execute()` call. A resume loses
it and re-reviews — one attempt out of the shared budget, no authority granted.
*Regression:* `M6-AC-14 [M6-A1]`, and `A15` in the attack pass.

### M6-A2 — one item's build was reviewable as another item's work

With the ledger gone, the reviewed attempt was derived as "the most recent clean BUILDER attempt of the
run". Journal attempt entries carry no item id (`M6-E1-D5` froze the key set), so that is a statement
about the **run**, not about an item. Measured: in a two-item run where item A's fix made Diana's
verification pass globally, item B — whose builder raised on every attempt — was marked **COMPLETE** on
the strength of a reviewer PASS about item A's build.

*Fix:* scoping is per item, held by the loop for one call, and every remembered attempt number is
re-validated against the journal (`clean_build_by_number`) so a number that has come to mean something
else resolves to nothing.
*Regression:* `M6-AC-9 [M6-A2 regression]` (two assertions), and `A15`.

### M6-A3 — `install_projection` trusted its supplier

`derive()` proves the subset property, and `install_projection` relied on that alone — so a `derive`
that had been replaced or changed installed whatever it returned. The behavioral probe did not catch
it: a probe covers **one** forbidden tool, and the attack's widened envelope satisfied it (`write_file`
still refused, for want of a `write_scope`) while granting `delegate_task` and a `read_scope` of `/`.

*Fix:* `install_projection` calls `prove_subset` against the contract itself, immediately before
installing. **This is the general lesson of the round: a behavioral probe proves the boundary is live,
never that it is the right boundary.** Both proofs are required and neither substitutes for the other.
*Regression:* `M6-AC-4 [M6-A3 regression]` plus its falsifier, and `A9`.

### M6-A4 — a second executor process could take over a live run

M6-D11 promises at most one live actor per run. A second executor process started on the same run
directory while the first was inside its turn, found `TURN_ACTIVE`, **discharged the obligation and
carried the run on** — while the first was alive and could still write to the target.

M5-D15's quiescence does not close this, and that is not a bug in it: Phase 0 F16 established that the
ownership stamp marks descendants and never the setter, so a peer executor is invisible to
`owned_pids` **by design**. Making Diana stamp itself would close this hole and open the one Phase 0
F12 measured — one executor's quiescence proof terminating its peer.

*Fix:* `runlock.py`, an exclusive `flock` on the run directory taken before the journal is read. The
kernel's lock is atomic (no read-then-write window two processes can both win) and is released by
process death however it dies (no stale-lock heuristic, and none of M5-D14's PID-recycling exposure).
*Regression:* `M6-AC-19 [M6-A4]` (four assertions plus a falsifier), and `A17`/`A17b`.

### M6-A5 — the run lease was entry-point-scoped *(found by independent review as R-2; fixed under ERRATA-002)*

Appended after the fact. **Nothing above this line has been rewritten**: the record of what M6-A4 was
believed to have established stands as it was written, because the point of this entry is that it was
believed too broadly.

M6-A4 closed M6's own entry point. The independent reviewer attacked the **fix** rather than the
original defect and found that `unattended.execute`, `discharge_obligation` and `run_attempt` are
public and unchanged from M5, and take no lease. Measured on a run durably `TURN_ACTIVE` with the
lease held by another process: the peer proved quiescence — the owning executor is invisible to
`owned_pids` by design (Phase 0 F16) — wrote `reconciliation-001.json`, closed the attempt with
`reconciled: true`, and drove the journal to `ARMED`, restoring retry eligibility, before refusing on
`actor-not-recorded`. A refusal that arrives after those effects is not a refusal.

*Why M6-A4 read as sufficient:* it was tested by starting a second **M6 executor**, which does take
the lease. The case that mattered was a peer using the older, lower entry point — which the M6 suite
never exercised because M6's own loop never calls it that way.

*Fix (M6-ERRATA-002):* the lease moved from the entry point to the **effects**. The choke point was
derived from call paths rather than assumed: `discharge_obligation` and `run_attempt` each gain a
lease check as their first statement, before any transition, snapshot write or diff. `cancel` is
deliberately left unguarded (M5-E1-D11/D12 make it safe concurrently, and it must stay available on
a wedged run). The primitive is a new file, `diana/runtime/runlease.py`, placed where both packages
already depend so no M5 module imports an M6 one.

*The measurement that decided the design:* a second `flock(LOCK_EX | LOCK_NB)` on a different
descriptor **in the same process** returns `EAGAIN`, so a guard that acquired would have deadlocked
the legitimate owner. The guard therefore **probes and never acquires**; ownership is the recorded
holder's pid plus `/proc` start time.

*Regression:* `test-m6-lease.sh` (32/0, 3 falsifiers) asserts the absence of every effect
individually, plus crash release, stale-file recovery, `fork` retention and the unchanged M5 path;
`test-m6-thirdpass.sh` re-attacks it from a real second process.

### The general lesson of both review findings

R-1 and M6-A5 are the same shape: **a fix was checked against the case that produced it, not against
the property it claimed to establish.** M6-A3 proved "not wider than the approval" and was read as
"is the right boundary". M6-A4 proved "no second M6 executor" and was read as "one live actor per
run". Both gaps were found by attacking the fix.

### Two harness errors, recorded because they were nearly reported as findings

`A8` asserted that a REVIEWER projection naming `terminal` must be refused — but `terminal` **is** in
the approved parent envelope, so that projection is a legal subset; per-role shape is `derive`'s job,
which `A9` attacks. `A14` asserted a dependency-blocked item was dispatched, using a fixture where the
item failed on its own merits rather than on its dependency. Both were attack-script defects; the
controls were correct.

### A fixture hazard caught before it entered the suite

The first end-to-end run took three BUILDER attempts where two sufficed. Cause: the fixture's fix was
the **same byte length** as the broken source and landed within the same mtime second, so Python served
stale bytecode from `__pycache__` and Diana's verification read a result that was no longer true. The
suite now verifies with `-B` and the fixture differs in length. This is worth recording because it is
the failure mode a flaky acceptance suite is made of, and because it is a small argument for M6-R2:
verification that an agent could not have influenced is still only as good as its own determinism.

---

## 4. Residual limitations — stated, not proven away

* **A whole-record journal forgery is not detected, and is not claimed to be.** An attacker who can
  rewrite `journal.json` *and* recompute its digest produces an internally consistent record; `A5`
  records this explicitly. The digest proves authorship, not intent, and M5-A1's artifact-count check
  is what catches the rollback case. This is M5's threat model, carried forward unchanged.
* **Reviewer judgment is unbounded.** M6 bounds what a reviewer may **do** and what its verdict may
  **cause**. Nothing here bounds whether the judgment is any good, and nothing in this record should be
  read as claiming otherwise.
* **The live reviewer's verdict text is not exercised end-to-end.** `HermesReviewer` exists and a live
  model turn under the REVIEWER projection is proven (the forced-mutation case, with a real provider).
  The reject→rebuild→approve *scheduling* proof uses deterministic backends, because a live model's
  verdict text is not reproducible. The boundary is proven live; the verdict text is not.
* **The run lock is advisory across filesystems that do not honour `flock`** (some network mounts).
  Diana-owned state lives under `~/.diana` on local disk; a deployment that moves it must re-establish
  this property rather than assume it.
* **Per-item scoping lives in process memory.** It is deliberately not durable (M6-A1's lesson), so a
  resume re-reviews rather than completes. That costs budget and grants nothing, but it does mean a run
  interrupted repeatedly can spend its budget on reviews.
* **M5-D20's merge-authority requirement is again restated and not discharged.** M6 adds a *reviewer*,
  which is exactly the milestone most likely to be misread as human approval. It is not: `REQUIRE_HUMAN`
  still has no independently verifiable mechanism automation cannot self-satisfy.
* **AO equivalence is proven against fixtures**, because no AO runtime exists on this host (Phase 0 F1).

---

## 5. Potentially vacuous assertions remaining

`vacuity_audit.py` reports **4** findings against the acceptance suite and **0** against the attack
pass. All four are static pins, each labelled `[static pin]` or `[static manifest]` in its own text and
each paired with a named behavioral counterpart:

| Assertion | Why it is static | Behavioral counterpart |
|---|---|---|
| the two backends are different implementations | both reach the same target state by different routes, so the difference is not observable in the target | the distinct turn-record self-reports in the same run |
| the backend seam and the actor seam are the same parameters | introspects the real production signature | both changed within one run (M6-AC-7) |
| `journal.KNOWN_ACTORS` equals the frozen topology | a drift pin at a location (roadmap invariant 6) | the unknown-actor refusals (`M6-AC-13`, `A7`) |
| M6 source contains no `delegate_task(`/`acp_command`/`gh pr`/`git push`/`worktree add` | a source grep proves an author did not type something | `delegate_task` and `message_agent` refused through the real funnel; no `.worktrees/` after a real run |

A fifth static check (`fcntl.flock` present in `runlock.py`) is labelled the same way; its behavioral
counterpart is the concurrent-process attack.

**No assertion in either suite is satisfied by a model declining to misbehave.** The live adversarial
case rewrites a tool call at `_parse_tool_call`, upstream of every Diana control, so the model really
produces the turn and its good behavior is not what passes the test.

---

## 6. Results

| Suite | Result |
|---|---|
| M6 acceptance | **174 passed / 0 failed / 21 falsifiers** |
| M6 independent attack pass | **25 repelled / 0 got through** |
| M1 | 470 / 0 (9/9 suites, 13/13 criteria) |
| M2 (live provider) | 59 / 0 |
| M3 | 106 / 0 |
| M4 | 146 / 0 |
| M5 | 202 / 0 |
| M5 journal | 54 / 0 |
| M5 ownership | 29 / 0 |
| Pre-existing Diana regression | 22 / 22 suites green (29 / 29 including the milestone suites) |
| AO legacy (`test-ao-adapter.sh`, `test-ship.sh`) | green, sources byte-identical to `e61ba11` |

Every count meets or exceeds Phase 0 finding F20's recorded baseline. No M5 test file was modified.

---

## 7. Nature of this audit

The attack pass in `test-m6-audit.sh` was written against M6 as an adversary and found two defects the
acceptance suite had passed over (`M6-A3`, `M6-A4`) — which is the argument for running it at all. It
is **not** an independent review: the same author wrote the implementation, the acceptance suite, the
attack pass and this record. M4's audit is explicit that a self-audit is not a substitute for
independent review, and that the first genuinely independent reviewer to examine M4 found two further
defects in a control the self-audit had signed off. M6 carries the same exposure.
