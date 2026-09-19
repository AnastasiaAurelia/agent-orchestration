# M6 — independent review

Reviewer: not the implementation author. Inputs treated as authoritative: the frozen
`HERMES-RUNTIME-M6.md` (`e61ba11`), `HERMES-RUNTIME-M6-ERRATA-001.md` (`8b806b5`), the accepted
M1–M5 specifications, and the code at HEAD. The author's `M6-AUDIT.md`, its finding classifications
and its test counts were **not** treated as evidence; the architecture below was reconstructed from
code first, and `M6-AUDIT.md` was read only afterwards, to compare.

---

## 1. Independent architecture reconstruction

**Modified pre-existing production files** (`git diff e61ba11`): `diana/runtime/blocking.py`,
`diana/unattended/journal.py`, `diana/unattended/report.py`, `diana/unattended/unattended.py` —
**equal to M6-E1-D1's set**, with `.gitignore` classified separately as a release manifest on M5's
own precedent. Every diff was read, not just the file list: `blocking.py` is purely additive;
`unattended.py` changes exactly `run_attempt`'s signature and its one call to `start_attempt`;
`report.py` adds one field; `journal.py` bumps the version, closes the attempt sub-schema, adds
`actor` and `actor_topology_digest`, and leaves the state machine, transition table, digest
construction, atomic-write path and M5-A1/M5-A2 checks untouched. Nothing was weakened.

**Topology.** `topology.FROZEN_ROLES = ("BUILDER", "REVIEWER")`, a module constant, written once at
approval into a digest-bound `actors.json` and re-verified on every resume in both directions
(document without a bound digest, and digest without a document, are each refused).

**Authority projection path.** `projection.derive(role, contract, topology)` builds the role's
envelope → `prove_subset` (four axes against the parent) → `actors.install_projection` re-proves the
subset, installs through the **unmodified** `hermes_patches.install_capability/install_confinement`,
then probes Hermes's real `_dispatch_authorized_once` in both directions. The M1/M4 boundary is
re-parameterised, never replaced or duplicated — confirmed by `hermes_patches.py` being byte-identical.

**Actor identity.** Written by `journal.start_attempt` in the same durable write that creates the
attempt, covered by the existing journal digest, in a closed key set. `update_attempt` refuses
`actor` outright. Nothing reads an actor from the environment, from a turn record, or from any file
outside the journal — I grepped for all three and drove the spoof cases.

**Budgets.** `_budget_state` is untouched M5 code: run-level `max_attempts` plus an absolute
deadline. There is no per-actor counter, so nothing to multiply.

**Ownership and locking.** `ownership.py` is untouched (stamp + start-time, per PID). M6 adds
`runlock.py`: an exclusive `flock` on `<run>/executor.lock`, taken in `actors.execute` before the
journal is read.

**Handoff.** One `execute()` call, strictly sequential: budget → eligibility → `ARMED` →
`install_projection` → `run_attempt(actor=…)` → reconcile → settle. Backend change happens at the
same seam (`turn_driver`) across a resume.

**Reviewer.** `{read_file, search_files}`, no `write_scope`, no `allowed_commands`, no
`command_policy`; verdict validated by a closed schema and admitted only from a journal-recorded
`REVIEWER` attempt reviewing a different, clean `BUILDER` attempt.

This reconstruction agrees with `M6-AUDIT.md`. The disagreements are below, and they are all about
what the author's **fixes did not cover**.

---

## 2. Defects found

Both implementation defects are residuals of the author's own two findings. They were found by
attacking the fixes rather than the original defects.

### R-1 — a projection was proven "within the approval", never "this role's envelope" *(implementation; fixed)*

M6-A3 added `prove_subset` to `install_projection`, closing *wider than the parent*. It does not
close *wrong shape, still within the parent* — and every role's envelope is by construction a subset
of the parent, so the subset proof cannot tell them apart.

**Measured:** a `derive` returning `{read_file, search_files, terminal}` plus the parent's
`allowed_commands` and `command_policy` for `REVIEWER` passed `prove_subset` cleanly, installed
cleanly, and the "reviewer" then **executed `python3 check.py` through the real dispatch funnel**
(`executed: True`). The author's own probe missed it because `FORBIDDEN_PROBE[REVIEWER]` is
`write_file`, which still refused for want of a `write_scope`. This is precisely the lesson
`M6-AUDIT.md` states — *a probe proves the boundary is live, never that it is the right boundary* —
applied one level up, to the subset proof.

**Fix:** `projection.prove_role_shape` compares the projection **by value** against the role's frozen
envelope (`ROLE_ENVELOPE`), and `install_projection` calls it alongside `prove_subset`.
**Regression:** `test-m6-review.sh` R-1 (6 assertions, 2 falsifiers), including the live `terminal`
call refused under `REVIEWER` and reaching its handler under `BUILDER`.

### R-3 — a failed install left whatever it had installed live *(implementation; fixed)*

`install_projection` replaced the boundary and then proved it. Every failure path after the
replacement returned by raising, leaving the **unproven** projection installed. Measured: after a
probe-refused `REVIEWER` install, the live envelope was the reviewer's; after a subset-refused one,
the previous `BUILDER` envelope remained.

Not reachable through the run loop — the exception aborts `execute()` and no turn runs — so this is
hardening, not an escape. It matters because "the boundary in force is whatever was installed last"
is the property Phase 0 F5 measured, and a failed proof should not be able to leave one in force.

**Fix:** every failure after the boundary is replaced falls through to `deny_all()`, an empty
allowed-tools set at the same guard. `uninstall()` was deliberately **not** used: it restores
Hermes's unpatched dispatch, which is fail-**open**. **Regression:** R-3 (3 assertions + falsifier),
including a real refused `read_file` under deny-all and recovery afterwards.

### R-2 — the run lock is entry-point-scoped *(implementation; NOT fixed — pinned and deferred)*

M6-A4's `flock` is taken in `actors.execute`. `unattended.execute` — M5's, still public, unmodified —
takes no lock and, on a **locked** run with a `TURN_ACTIVE` attempt, **discharged the obligation**:
quiescence proved (the peer executor is invisible to `owned_pids` by design, Phase 0 F16),
`reconciliation-001.json` written, attempt closed `reconciled: True`, state driven to `ARMED`. Only
then did it refuse with `actor-not-recorded`. So a peer reconciled a turn that was still in flight —
the reading-a-moving-object failure M5-D15 exists to prevent and M6-A4 was meant to close.

Threat model is the same one the author accepted for M6-A4 (another Diana process on the same host;
not agent-reachable, since the run directory is outside both scopes per M5-AC-29). By their own
standard it is in scope.

**Not fixed, deliberately.** The only sound fix is to take the lock where the obligation is
discharged, in `unattended.discharge_obligation` — and M6-E1-D1 permits `unattended.py` exactly one
change (`run_attempt` gains `actor`). Widening that unilaterally is the precise failure the erratum
exists to prevent, and it is the milestone owner's call, not the reviewer's. **Recommendation:
M6-ERRATA-002 widening `unattended.py`'s permitted change to include lock acquisition in
`discharge_obligation`.** Meanwhile the exact current behavior is **pinned** by a test (roadmap
invariant 6) so it cannot silently widen or silently close.

### V-1 — an attack assertion never exercised the control it named *(harness; fixed)*

`test-m6-audit.sh`'s A12 ("actor switch with an edited run policy") edited the policy of a run that
was already **terminal**, so `execute` returned `run-already-terminal` and the run-policy digest was
never consulted. The assertion accepted `in (RUN_POLICY_DIGEST_MISMATCH, RUN_ALREADY_TERMINAL)`, so
it passed while proving nothing — the exact "multiple outcomes accepted where one is frozen" shape.

The control itself is sound: on a **non-terminal** run the same edit yields exactly
`run-policy-digest-mismatch` and no attempt starts. A12 now uses a non-terminal run and asserts the
single frozen code, plus A12b that no attempt started.

---

## 3. Attacks run, and what held

All attacks drove the real dispatch funnel, edited real durable state, or killed real processes. No
result depends on a model declining to misbehave.

**Held (55 independent checks across 7 probe scripts):** Builder impersonates Reviewer; Reviewer
impersonates Builder; Builder forges a PASS through the loop's own `_accept_review`; actor-field
tampering with and without a recomputed digest; journal-version downgrade (re-digested); unknown role
smuggled into a re-digested journal; stale actor replay; environment actor-id spoof
(`DIANA_ACTOR`/`DIANA_ROLE` set during a real run); backend self-report masquerade; projection wider
than parent; projection with `read_scope: /`; projection dropping denied subpaths; projection
granting `delegate_task`; projection changed after resume; topology expanded to a third role;
`delegate_task` and `message_agent` under the widest projection; actor switch resetting attempts, the
deadline, the contract or the run_id; backend switch across a real SIGKILL; terminal run resumed for
more budget; budget raised on disk; COMPLETE item replay; item A's build reviewed as item B's; a
dependency-blocked item dispatched; handoff from `TURN_ACTIVE`; second executor on a live run;
reviewer `write_file`/`patch`/`terminal` with valid arguments; reviewer PASS over an out-of-envelope
diff; silent reviewer; four verdict-schema refusals with distinct codes; rejection ordering.

**Got through:** R-1 and R-2 (above). R-3 and V-1 were found by inspection and confirmed by probe.

---

## 4. Run lock — what `flock` proves, and what it does not

**Proves.** Mutual exclusion between processes that *take the lock*, atomically — there is no
read-then-write window two processes can both win, which a PID file would have. Release on process
death by any means, including `SIGKILL`, with no stale-lock heuristic and none of M5-D14's
PID-recycling exposure. Verified: a second executor is refused `actor-handoff-refused`; a killed
holder's lock is immediately reclaimable; a stale `executor.lock` file with no live holder does not
block.

**Does not prove.**

1. **It is not a containment primitive.** It constrains nothing about what a process *does* — only
   which process may enter `actors.execute`. It is an exclusion protocol between cooperating callers.
2. **It is entry-point-scoped, not run-scoped** (R-2). Any caller that does not take it is unaffected,
   and `unattended.execute` is such a caller today.
3. **It is inherited by `fork`.** `flock` is held per open file description, so a forked descendant
   keeps the run locked after the acquirer exits — measured: `actor-handoff-refused` persisted while
   only a grandchild lived. Fail-closed in direction, but it can wedge a run until that descendant dies.
4. **It depends on the filesystem honouring `flock`.** Some network filesystems do not. Diana state
   lives on local disk today; a deployment that moves it must re-establish this rather than assume it.
5. **It says nothing about the target repository** — only about the run directory.

---

## 5. Reviewer / Diana semantics — independently confirmed

Read-only **by enforcement**: under the `REVIEWER` projection, `write_file`, `patch` and `terminal`
driven with **valid in-envelope arguments** were each refused with the *capability* refusal ("not in
the execution contract capability envelope"), not merely an argument refusal, and the target was
byte-unchanged. No command authority: the reviewer envelope carries no `allowed_commands` and no
`command_policy`, and R-1's fix now makes acquiring them impossible without changing the frozen
`ROLE_ENVELOPE`. Diana owns verification: `verify` is a parameter of `actors.execute`, called by the
loop, never handed to a driver. The verdict is input-only: a reviewer `PASS` over an out-of-envelope
diff still yielded `BLOCKED / reconciliation-mismatch`, and the reviewer was never consulted at all,
because reconciliation failed first. Identity is digest-covered, closed-schema, and single: the only
authoritative record is the journal attempt entry; `review-verdict-*.json` and `run-report.json` also
contain an `actor` field but are **write-only** — I grepped every read site, and `_accept_review`
re-derives both roles from the journal.

---

## 6. Regression results — measured by me, not quoted

| Suite | Result |
|---|---|
| M6 acceptance (`test-m6-multiactor.sh`) | **174 passed / 0 failed / 21 falsifiers** |
| M6 attack pass (`test-m6-audit.sh`, A12 tightened) | **26 repelled / 0 got through** |
| M6 independent review (`test-m6-review.sh`, new) | **18 passed / 0 failed / 4 falsifiers** |
| M1 | 470 / 0 (9/9 suites, 13/13 criteria) |
| M2 (live provider) | 59 / 0 |
| M3 | 106 / 0 |
| M4 | 146 / 0 |
| M5 / journal / ownership | 202 / 0, 54 / 0, 29 / 0 |
| Remaining Diana suites | 22 / 22 green |
| AO legacy | `test-ao-adapter.sh` and `test-ship.sh` green; `ao.py`, its test and all of `diana/ship/` byte-identical to `e61ba11` |

All ten frozen specs (M1–M6 and the four errata) are byte-identical.

---

## 7. AO-MIG-1 — verified narrowly

The claim checked was exactly *"reviewer read-only enforcement is superseded for this certified
path"*, and it holds, in both halves, reproduced independently:

* **Legacy gap reproduces.** `ship.py reviewer-readonly-check --expected-head <head>` returned exit
  `0`, `{"ok": true}` for a reviewer that wrote a gitignored `build/artifact.bin`.
* **New path detects.** Diana's reconciliation over the same interval: `within_envelope: false`,
  `paths_outside_write_scope: ["build/artifact.bin"]`, with the path absent from both git views.
* **New path prevents.** Under the `REVIEWER` projection the same write was refused at dispatch and
  the file was never created — this is the genuinely M6-specific half.
* **Equivalence.** Tracked-modified, untracked-added and committed cases: refused by both paths.
* **Nothing retired or deleted.** `ship.py` is byte-identical and its subcommand still runs and still
  returns `ok: true` for the gitignored case — which is the point: the responsibility moved, the code
  did not. No claim of retirement or deletion is supported, and none is made.

---

## 8. Potentially vacuous assertions remaining

`vacuity_audit.py` (the author's tool, re-run by me) reports **4** in the acceptance suite, **0** in
the attack pass and **0** in the review suite. I re-derived them independently by grep and manual
read, and reached the same four; each is a static pin, labelled as such in its own assertion text,
with a named behavioral counterpart:

* the two backends are different implementations *(counterpart: distinct turn-record self-reports)*;
* the backend seam and the actor seam are the same parameters *(counterpart: both changed in one run)*;
* `journal.KNOWN_ACTORS` equals `topology.FROZEN_ROLES` *(counterpart: the unknown-actor refusals)* —
  this one is load-bearing, because `journal.py` duplicates the role vocabulary rather than importing it;
* the M6 source contains no `delegate_task(` / `acp_command` / `gh pr` / `git push` / `worktree add`
  *(counterpart: both refused through the real funnel; no `.worktrees/` after a real run)*.

I checked the harness helpers themselves, which the tool does not: `check()` requires `cond is True`
(a truthy non-`True` fails), and `code_of()` catches only `blocking.Blocked`, so an unexpected
`TypeError` surfaces as an error rather than passing as a refusal. Neither is a vacuity vector.

One assertion in the acceptance suite accepts two states —
`rec["state"] in ("TURN_ACTIVE", "RECONCILING")` after a real `SIGKILL`. That is genuine
nondeterminism in where the kill lands, not laxity, and I am satisfied it is correct.

---

## 9. Residual limitations

* **R-2 is open**, pinned, and needs an erratum. Until then, M6-D11's "at most one live actor per run"
  holds for M6's entry point and not for M5's.
* **A whole-record journal forgery remains undetected** — an attacker who rewrites `journal.json`
  *and* recomputes its digest produces a consistent record. M5's threat model, unchanged; the
  rollback case is caught by M5-A1's artifact-count check, which I re-verified.
* **Role vocabulary is duplicated** between `journal.KNOWN_ACTORS` and `topology.FROZEN_ROLES`, held
  equal by one assertion rather than by construction. That assertion is now load-bearing.
* **Per-item review scoping lives in process memory**, so a resume re-reviews rather than completes.
  Fail-closed, but repeated interruption spends budget on reviews.
* **`install_projection` is not re-entrant under concurrency** — process-global by nature (Phase 0
  F5). Safe only because M6-D11 forbids concurrent actors, which R-2 partially undermines.
* **The live reviewer's verdict text is never exercised end-to-end**; the boundary is proven live with
  a real provider, the scheduling proof uses deterministic backends.
* **M5-D20's merge-authority requirement is still not discharged**, and adding a reviewer does not
  discharge it.
* **AO equivalence is fixture-based** — no AO runtime exists on this host.

---

## 10. Verdict

M6's central claim — *more than one bounded actor participates in Diana-governed work without
multiplying or laundering authority* — is **substantially proven**, and the two defects I found were
both in the fixes rather than in the design. Authority genuinely does not multiply: one contract, one
digest-bound envelope, one run-level budget, one authoritative identity record, and a reviewer whose
verdict cannot move a state transition.

Two things keep this short of unqualified acceptance. **R-2 is open** and is a real hole in a property
the specification states flatly. And **this review is now itself un-reviewed**: I wrote fixes for R-1
and R-3, which means the code I checked is no longer entirely code I did not write. A third pass over
`prove_role_shape` and `deny_all` by someone else is the remaining honest gap.


---

## 11. Final release review — three unreviewed controls, and the regression accounting

A third reviewer examined only the controls no independent reviewer had yet seen
(`prove_role_shape`, `deny_all`, `runlease.py` and its two guards) and reconciled the suite
accounting. Verdicts:

**`prove_role_shape` — sound.** All 19 near-miss projections refused: extra tool, missing tool,
terminal, patch, write_file, delegate_task, command added, write_scope added, unknown field, altered
command policy, read_scope widened *and* narrowed, each role's envelope offered as the other's, a
builder minus/plus a tool, a wider write root, a raised timeout, and an empty envelope. Structurally
different forms are refused too — tuple-vs-list, reordered tools, duplicated tools, and (with a
multi-root scope) reordered `allowed_roots` and `denied_subpaths`. The one accepted variant is
`max_timeout_s: 300` vs `300.0`, which is the same authority written differently and grants nothing.
A projection dict with no `role` key raises `KeyError` rather than `Blocked` — before any install, so
the previous boundary stands; noted, not a defect.

**`deny_all` — sound, and now proven on the funnel the earlier passes never drove.** Both real
dispatch paths were derived from the pinned implementation: `_dispatch_authorized_once` (which 13
inline tools bypass `handle_function_call` on) and `model_tools.handle_function_call`. Every prior M6
pass drove only the first. Under deny-all, forced failures at the liveness flag, the forbidden probe
and the permitted probe each leave `allowed_tools == []`, and `write_file`, `patch`, `terminal`,
`delegate_task`, an unknown `future_tool_9000` **and** `read_file` are refused on **both** funnels
with Diana's capability refusal. `uninstall()` is never called (AST-checked), both guards stay
installed, and `handle_function_call` is still Diana's wrapper. A subsequent valid REVIEWER
projection grants exactly the frozen reviewer authority on both funnels, and a later BUILDER recovers.

**`runlease` — one defect, fixed (F-1).** The guard is the first executable statement of both
`discharge_obligation` and `run_attempt` (AST-verified). The real-process peer attack holds on all
three entry points with zero side effects. But `os.open` follows symlinks: replacing `executor.lock`
with a link to an unrelated file made the probe lock **that** file, report the run free, and a peer
was measured **writing `reconciliation-001.json` for a live run** — R-2 re-opened through the lease
file itself. This is M5-A2's shape one level over: the lease's *name* was trusted and the *file*
behind it was not checked.

*Fix:* `_open_lock` uses `O_NOFOLLOW` and requires a regular file (a planted fifo is refused too), and
this process records the `(st_dev, st_ino)` of the lease it actually locked, so an owner whose lease
file is replaced underneath it **refuses** rather than acting on a lease it can no longer prove.

*Residual, stated plainly:* a peer that **unlinks and recreates** the lease file still gets through.
That is inherent to file-based leasing and is not fixable by locking a file — the same adversary can
unlink the journal, which M5's threat model already records as undetected (M6-E2-D8). What is now
closed is redirection (symlink, non-regular file) and silent replacement under a live owner.

### The 26 → 22 accounting, reconciled exactly

The historical figure is right and the M6 reports were **under-counting**. The cause: every sweep used
the shell glob `diana/*/test-*.sh`, which matches one directory level. Four suites live two levels
deep and were therefore **never executed** in any M6 sweep:

`diana/security/adapters/test-adapters.sh`, `diana/security/dynamic/test-dynamic.sh`,
`diana/security/reviewer/test-github-review-adapter.sh`, `diana/security/reviewer/test-reviewer.sh`.

| | Total suites | Milestone suites | Pre-existing |
|---|---|---|---|
| M5 accepted base `29c5a7f` | 33 | 7 (m1, m2-live-turn, m3-runtime-verify, m4-bounded-mutation, m5-journal, m5-ownership, m5-unattended) | **26** |
| M6 HEAD | 38 | 12 (those 7 + m6-multiactor, m6-audit, m6-review, m6-lease, m6-thirdpass) | **26** |

26 is unchanged, matching M4's F8 and M5's F15. The reported "22" was 26 minus the four unmatched
suites. **No suite was dropped, renamed away, deleted or skipped by any milestone** — they were
missed by the reviewer's own enumeration. All four were then run: 64/0, 60/0, 28/0 and 35/0. Every
sweep now enumerates with `find`, not a one-level glob.

### Two count observations, investigated rather than accepted

**M2 reports 57 or 59** depending on the run. The suite emits one assertion *per corrupted tool call
actually applied*, and `ToolCallCorruptor` only substitutes when the live model emits an envelope
tool; a turn that exhausts its iteration budget applies fewer. The floor is guarded by
`M2-AC-4 non-envelope calls were forced onto the live path` (`len(applied) >= 3`) and by the
unconditional `every forced non-envelope call was refused`. This is M5's carried assumption about
M2-AC-4's provider sensitivity (M4 audit §6), it predates M6, and M2 touches none of M6's files.

**`test-playwright-prototype.sh` failed once** with `MCP request timed out: initialize` — the
Playwright MCP server not starting, under load from parallel suites. It passes on a clean retry, the
suite and its sources are byte-identical to the M6 freeze, and it imports nothing M6 changed.
Infrastructure flake, recorded rather than silently re-run.


---

## 12. Focused final review of the F-1 lease fix

Scope: `diana/runtime/runlease.py`, the two ERRATA-002 guards, and the F-1 regressions only. Nothing
was taken on the previous reviewer's word; every case below is behavioral.

**`_open_lock` — sound.** Flags are `O_RDWR | O_NOFOLLOW | (O_CREAT on acquire)`, mode `0600`;
`ELOOP`/`EMLINK` become the symlink refusal, then `fstat` + `S_ISREG` gates the fd, which is closed on
any failure. Measured against real filesystem objects: a normal file acquires; a symlink present from
the start is refused at both `probe` and `acquire`; a symlink swapped in **after** a valid lease is
refused for owner and peer alike; FIFO, directory and unix socket are refused; a hardlink to another
regular file, deletion, and an atomic `rename`-over are all caught by the owner. A device node could
not be hardlinked as an unprivileged user (`EXDEV`) and is covered by the same `S_ISREG` gate as the
FIFO and socket cases.

**Inode binding — sound, and the mechanism is stronger than it first looks.** The obvious objection
is inode-number reuse, and it is real: unlink-and-recreate with no open descriptor reused the same
inode **200/200 times** on this ext4 filesystem. It cannot reach the bound inode, though, because
**the owner's own open fd pins it** — with the fd held and the file unlinked, 500 newly created files
never collided with it, and the number only became reusable after the owner closed. So
`(st_dev, st_ino)` is a sound identity *for exactly the owner's fd lifetime*, which is exactly the
window in which it is consulted. A real owner refused after 300 replacement cycles.

**TOCTOU — what is actually guaranteed.** With the lease file untouched, 200 consecutive peer probes
all reported held: there is **no timing window a peer can hit without replacing the file**, because
the owner holds `flock` on the same inode and `EAGAIN` is not racy. The residual window is therefore
not a timing race at all — it requires unlink/replace, and that is the documented residual. The
guarantee is narrower than "mutual exclusion": it is *no process advances a run while another holds
the lease, absent the ability to remove or replace files in the run directory*.

**fd / fork / exec — one residual, bounded and pinned.** The lease fd is **not inheritable across
exec** (Python sets `CLOEXEC` by default), so a command the executor spawns cannot keep a run locked
— measured: the lease frees when the owner exits despite a live `sleep` child. A `dup()` shares the
open file description and so cannot steal the lease; a separately opened fd in the same process is
refused, which is precisely why `require_lease` probes rather than acquires. A **`fork`ed** descendant
does retain the lease after the owner exits; the wedge is fail-closed and bounded by that
descendant's lifetime, and the run frees when it dies. Pinned by test.

**Peer bypass — refused with zero side effects.** With a real owner process holding the lease on a
`TURN_ACTIVE` run, peers via `unattended.execute`, `discharge_obligation` and `run_attempt` each got
exactly `actor-handoff-refused`, and all eight required absences held: journal bytes unchanged, state
still `TURN_ACTIVE`, attempt still `OPEN`, `reconciled` still false, no reconciliation artifact, no
new snapshot, no new attempt, target unchanged. After killing the owner: the stale file did not
block, a fresh executor acquired, quiescence was proven before the diff, the obligation was
discharged exactly once, and the same `run_id`, contract bytes and budget remained in force with the
crashed attempt's journaled actor intact.

### Defect found and fixed — F-2 (implementation)

`_open_lock` converted only `ELOOP`/`EMLINK`; every other open failure was re-raised raw. `probe` and
`require_lease` wrapped their own calls, but **`RunLease.acquire` did not**, so a lease path replaced
by a directory (`EISDIR`) or made unreadable (`EACCES`) escaped as a bare `OSError` rather than a
reason code. Fail-closed in effect — no lease is granted — but it breaks M1's AC-1 discipline that an
operator reads the *code*, and a caller catching `blocking.Blocked` would not classify it.

*Fix:* the conversion moved into `_open_lock`, so all three call sites are consistent; the errno name
is kept in the detail so the failure stays diagnosable. *Regression:* six assertions covering
directory and unreadable-file leases across `probe`, `require_lease` and `acquire`, plus a falsifier
proving a normal lease still succeeds at all three.

### Vacuity — F-1/F-2 tests only

Each control was **removed and the test re-run**: dropping `O_NOFOLLOW` makes the peer succeed and
write `reconciliation-001.json` for a live run; clearing the inode binding lets the owner act on a
lease it cannot prove; the FIFO case reaches the `S_ISREG` gate. And the setup does not pre-guarantee
refusal — the identical call against a run with no lease at all succeeds, so every refusal came from
the lease guard and not from the fixture. No literal `True`, no `else True`, no empty quantifier, no
comment-matching grep, no multi-code acceptance, and no falsifier that mutates only the harness.

### The unlink/recreate residual — why it may remain

It is truly outside the inherited threat model, on all three of the required grounds. ERRATA-002
M6-E2-D8 states it. The same adversary was measured removing `journal.json`, which M5 already treats
as undetectable-but-fail-closed. And nothing in the code, the tests or the documents describes the
lease as containment — `runlease.py`'s own docstring says it "answers one question -- may this process
act on this run right now -- and constrains nothing about what a process does once admitted".
