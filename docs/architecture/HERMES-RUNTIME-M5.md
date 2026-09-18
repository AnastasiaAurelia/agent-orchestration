# Hermes Runtime — Milestone 5: Unattended Bounded Execution (FROZEN)

Status: **normative and frozen**. Source of truth for M5; remains normative after implementation.

Architectural decisions **M5-D1 … M5-D21 are frozen**. M1's D1–D38, M2's D1–D14, M3's D1–D16 and
M4's D1–D16 (as corrected by [`HERMES-RUNTIME-M4-ERRATA-001.md`](HERMES-RUNTIME-M4-ERRATA-001.md))
remain frozen and unmodified; where M5 appears to disagree with any of them, the earlier milestone
wins and the M5 text is the defect.

Branch: `feature/hermes-unattended`, based on accepted M1+M2+M3+M4 `main` (`fb1a78f`).
Roadmap: [`HERMES-ROADMAP.md`](HERMES-ROADMAP.md).
Prior: [`HERMES-RUNTIME-M1.md`](HERMES-RUNTIME-M1.md), [`HERMES-RUNTIME-M2.md`](HERMES-RUNTIME-M2.md),
[`HERMES-RUNTIME-M3.md`](HERMES-RUNTIME-M3.md), [`HERMES-RUNTIME-M4.md`](HERMES-RUNTIME-M4.md).

---

## Thesis

> A user approves **one** execution envelope, leaves, and returns to `COMPLETE`, `FAILED` or
> `BLOCKED` — with the envelope approved at hour zero still **provably** the envelope in force at
> hour six, across process death and resumption.

**M5 extends duration and orchestration. It grants no capability.** The envelope is M4's
`BOUNDED_REMEDIATION` envelope, unchanged: same `allowed_tools`, same `write_scope`, same
`allowed_commands`, same `risk = ELEVATED`, same `depth = D2`. No tool is added. No scope is widened.

What M5 adds is the thing M1–M4 never needed, because they assumed a fact M5 removes: **that the
Diana process is alive for the whole run**. Every guarantee M4 proved is a guarantee of a live
process. M5's subject is what remains true when that process dies.

Explicitly **not** in M5: new tools, `execute_code`, subagents/`delegate_task`, arbitrary network,
multi-actor or reviewer roles, AO replacement, natural-language routing beyond M1's deterministic
keyword rule, and autonomous PR-merge authority (see M5-D20).

---

## Phase 0 — empirical findings

Established against the pinned install (`hermes-agent 0.21.1` @ `b8e8639`) on the accepted M4 base
(`fb1a78f`), by execution rather than from documentation. Each finding below was **measured**; the
probe that produced it is named in prose so it can be re-run.

| # | Finding |
|---|---|
| **F1** | **The M4 mutation path persists nothing.** `remediate.execute()` never calls `contract.persist`. A completed bounded-remediation run produced **no** run directory, **no** `contract.json`, **no** env binding (`DIANA_RUN_ID` / `DIANA_CONTRACT_DIGEST` both unset) and **no** persisted reconciliation report. The contract, the pre-turn snapshot and the turn record exist **only in process memory**. M1's advisory path does persist (`advisory/run.py:202-210`); the *mutating* path — the only one that can damage a target — does not. |
| **F2** | **SIGTERM and SIGKILL skip reconciliation entirely.** Three death modes were measured against the same fixture, with an out-of-envelope file planted mid-turn. In-process exception → audit finding F-A5's guarantee holds and `reconciliation-mismatch` is raised correctly. **SIGTERM (rc 143) and SIGKILL (rc 137) → reconciliation never runs**, the out-of-envelope file survives undetected, and nothing on disk records that a run ever happened. F-A5 lifted reconciliation from "only `Blocked`" to "any exception"; it is still bounded by **the process's own survival**. |
| **F3** | **git alone cannot recover an ignored mutation.** After a SIGKILL, `git status --porcelain` reported only ` M src/calc.py`. The out-of-envelope mutation of a **gitignored** path (`build/artifact.bin`) was invisible to git and was recorded only by the hash snapshot — whose "before" half died with the process. M4-D14 uses two views precisely because they fail differently; a crash destroys exactly the view that covers gitignored paths. |
| **F4** | **Hermes owns durable process state that survives restarts.** `~/.hermes/processes.json` (`process_registry.py:41 CHECKPOINT_PATH`) is written by `_write_checkpoint()` at three production sites and read by `recover_from_checkpoint()`, which **adopts host PIDs across a restart** (correctly refusing a recycled PID by comparing start time). It is invoked from `gateway/run_startup.py:921` — the **gateway**, not `AIAgent.chat()`, so it is not on Diana's embedding path. A live gateway (`hermes_cli.main gateway run`, uptime ~6.3 h) is nevertheless running on this host and shares `~/.hermes/` with every Diana run. |
| **F5** | **Foreground commands leave a different Hermes-side residue.** A foreground `terminal` call does **not** enter `processes.json` (contents stayed `[]`, mtime unmoved) and appears in neither `_running` nor `_finished`. It does write `~/.hermes/cache/terminal/hermes-snap-<id>.sh` carrying the command text, plus `logs/agent.log`. That is durable state, outside the contract, outside any run directory, and outside Diana's control. |
| **F6** | **A command tree survives Diana's death and finishes its work.** Hermes spawns with `start_new_session=True` (`process_registry.py:946`), so the command gets its own session and process group. Measured: Diana (pid 30372) was SIGKILLed 4 s into an allowed command; the tree (pgid 30414) was reparented to the user's systemd (pid 1680), **survived, and wrote its marker 30 s later**. M4-D10 refused `background: true` because "a bound on the tool call is not a bound on the work it started" — F6 shows the **same property holds for foreground commands** once Diana dies. M4's real bound was Diana's liveness, which is exactly the assumption M5 removes. |
| **F7** | **The absent-`workdir` asymmetry — an M4 defect, found by M5's Phase 0.** `decide_command` checks `workdir` only `if workdir is not None`. Measured on the real M4 shape (`write_scope = <root>/src`): an explicit `workdir=<repo_root>` is **refused**, while **omitting** `workdir` is **allowed** and the command actually executed in `<repo_root>` — outside `workdir_roots`. The in-code comment at `mutation_policy.py:219-220`, *"An absent `workdir` is safe to default because the session cwd is still scope-checked"*, is **false**: nothing scope-checks the session cwd. This is the exact shape of audit finding F-A2 (an omitted argument reaching a value the declared bound refuses), one argument over. See M5-D19. |
| **F8** | **Diana has no retry; Hermes has one Diana does not configure.** No retry, backoff or attempt loop exists anywhere under `diana/`. Hermes runs its own API retry (`api_max_retries`, default **3**, `config_defaults.py:109`; SDK `max_retries=0` because "retry belongs to hermes's outer loop (honors Retry-After)"). Diana never sets it. Today Diana's only bound on retry is **transitive**: the wall-clock deadline and iteration cap on the whole turn (M2-D12; 420 s / 24 iterations in M4). M5 grows that deadline, and the transitive bound weakens exactly as duration grows. |
| **F9** | **Authority binding proves document integrity, not world freshness.** `load_and_verify` checks digest and `run_id`. `target.git_commit` is recorded and is compared to the live repository by **no code path** (verified by exhaustive grep). There is **no** TTL, expiry or deadline field. Measured: after the target advanced by a commit, the contract re-verified as valid and nothing noticed. The `DIANA_RUN_ID` / `DIANA_CONTRACT_DIGEST` binding is **env-carried and therefore process-local** — a resumed process inherits neither. A six-hour-old contract verifies exactly as a six-second-old one. |
| **F10** | **Diana cannot observe the identity of a process Hermes spawns.** A foreground `terminal` call returns exactly `{output, exit_code, error}`; the process registry is **empty** for foreground calls; and `MutationPolicy.decide()` receives only the tool **name and arguments** — no pid, no pgid, no session handle. So at resume there is no record, on either side, naming the process tree a previous run started, while F6 proves such a tree can outlive the run. |
| **F11** | **Diana's own `contract.persist` is not crash-atomic.** It is a plain `open(path,"wb").write(payload)` with no `fsync` and no atomic rename. That is adequate for M1 — written once, before the run, and a torn contract fails its digest and therefore fails closed — but it is **not** adequate for state written or updated while a run is in flight. Hermes's own `utils.atomic_json_write` (temp file + `fsync` + `os.replace`) is the pattern already available in the pinned tree. |
| **F12** | **Post-mortem reconciliation from durable state works, and needs no new mechanism.** A fresh process, given only a contract and a pre-turn snapshot that had been written **atomically before the turn began**, re-verified the contract's digest/`run_id` binding and then reconciled the crashed run — **detecting the gitignored out-of-envelope mutation that `git status` alone could not see**. The missing ingredient in M4 is **durability and ordering**, not a new control. |
| **F13** | **A snapshot diff cannot attribute, and nothing detects target drift.** Between a crash and a resume, a third party committed `THIRD-PARTY-EDIT.md` to the target. The reconciliation blamed it on the run, because a snapshot diff knows only *that* a file changed, never *who* changed it. Symmetrically, a human could revert a run's escape and hide it. Combined with F9 (nothing compares `target.git_commit` to the live repo), this is why resume needs a freshness precondition that is **distinct from** contract integrity. |
| **F14** | **Dispatch surface re-derived from code (roadmap invariant 2), and the count is not a stable property.** The saturated registry holds **92** tools after `import model_tools` (**0** before it; importing all 46 discovered tool modules adds none). **80 of the 92 carry a `check_fn`** — availability-gated on environment, config and credentials. M4's F1 recorded **77** and M1's own docstring **88**, on the *same* pinned version: the number tracks the environment, not the pin. `INLINE_TOOL_EXECUTORS` still holds exactly **13** names, and **none** of the five M4-granted tools is among them. `get_tool_definitions(["file","terminal"])` presents 8, including `process_manage` — presented, and denied by set membership. |
| **F15** | **Pre-M5 regression baseline on `fb1a78f`, before any M5 code:** M1 **470** assertions passed / 0 failed (9/9 suites, 13/13 criteria); M2 **59** / 0 with a live provider; M3 **106** / 0; M4 **128** / 0; pre-existing Diana regression **26/26** suites green. These match the M4 audit §7 figures exactly. The root `verify.sh` reports 17 `FAIL`, investigated rather than assumed: it is an **install** verifier, invoked by **no** acceptance suite, and it fails identically on the pristine accepted base because Diana's own source repository is not an install target. Pre-existing, not a regression. |
| **F16** | **Per-PID run ownership is provable, and survives Diana's death.** With `DIANA_RUN_ID` stamped into the environment before the turn, **every** process in the Hermes-spawned command tree inherits it and carries it readably at `/proc/<pid>/environ` — measured: the wrapper `bash` (pid 61676) and its `sleep` child (pid 61678) both carried the stamp, in their own pgid (61676), distinct from Diana's (61645). Two properties follow. Ownership is provable **per PID**, so termination never needs a process-group kill. And `/proc/<pid>/environ` reflects the environment **at exec time**, so a value set through `os.environ` after start marks **descendants only** and never the setter — which is exactly the marker semantics wanted. |

### What F1, F2, F6 and F12 together determine

M4's reconciliation is correct and its guarantee is real — for a process that lives. F1 and F2 show
the guarantee evaporates on process death; F6 shows the *mutation* does not, because the work outlives
the run that authorized it; and F12 shows the fix needs no new control, only durability written in the
right order. So M5's entire mechanism is: **write the audit's inputs down, atomically, before the
mutation starts, and make reconciliation an obligation of the run rather than of the process.**

---

## The four kinds of state

M5 is about state that outlives a process, so the categories are named before any decision uses them.
Conflating them is how an unattended system quietly widens its own authority.

**1. Hermes-owned durable state.** `~/.hermes/processes.json` (adopted across restarts by the gateway,
F4), `cache/terminal/hermes-snap-*.sh` (F5), `logs/`, `state/gateway.heartbeat`, `cron/`, `config.yaml`,
`.env`. Diana neither writes nor controls any of it, and a **live gateway process shares it**. It is
**never** an input to a Diana decision.

**2. Diana-owned authoritative durable state.** Today: `~/.diana/runs/<run_id>/` on the M1 advisory
path only, and **nothing whatsoever on the M4 mutation path** (F1). This is the category M5 creates.
Everything M5 decides with must live here, be written by Diana, and be covered by a digest.

**3. Target-repository state.** The working tree and git state: mutated by the run, by an allowed
command, and possibly by a third party while the run is down (F13). Two independent views are required
because they fail differently (F3).

**4. Process / runtime state.** The enforcement monkeypatches (`hermes_patches._STATE`, a module global
and therefore process-local), the env binding (F9), the agent and turn threads, and command process
trees that live in their own sessions (F6) and that Diana cannot name from the tool result (F10) but
**can** name by stamp (F16).

---

## Frozen decisions

**M5-D1 — M5 adds no capability, and the envelope is M4's, byte-for-byte.** Same `allowed_tools`,
same `write_scope` semantics, same `allowed_commands` exact-match rule, `risk = ELEVATED` derived from
the envelope (M1 D12 / M4-D3), `depth = D2` from the certified `BOUNDED_REMEDIATION` class (M1 D13 /
M4-D5). M5 certifies **no new workflow class** and adds **no tool**. A milestone that extended duration
*and* authority in one step could not attribute a failure to either.

**M5-D2 — Neither the `ExecutionContract` nor any frozen document schema gains a field.** M4-D2 froze
`capability_envelope`'s optional keys as exactly `{write_scope, allowed_commands, command_policy}` and
`validate()` rejects unknown envelope keys. M5's duration budget is therefore **not** an envelope key.
It lives in an M5-owned document in the run directory, following the M2-D5 and M3-D4 precedent: when a
milestone needs to record something new, it writes its own file rather than editing a frozen schema.

**M5-D3 — The run journal is Diana-owned authoritative durable state, and it is the only thing M5
decides with.** It extends M1's existing `~/.diana/runs/<run_id>/` (dir `0700`, files `0600`) rather
than inventing a second location. Nothing under `~/.hermes/` is ever read as an input to a Diana
decision (F4, F5): Hermes-owned state is observed and reported, never trusted. The agent contributes
no input to its own audit — M4-D14's rule, carried forward unchanged into the durable case.

**M5-D4 — Every journal write is crash-atomic: temp file, `fsync`, `os.replace`.** Per F11, Diana's
existing `persist` is a plain write, which is safe only because M1 writes it once before the run and a
torn contract fails its digest. State that is updated *during* a run has no such luxury: a half-written
journal must be either the old state or the new state, never a third thing. A journal record that
cannot be read and verified is treated as absent, and absence fails closed (M5-D9).

**M5-D5 — Write-ahead ordering: no mutation may begin until the audit's inputs are durable.** Before
the first tool call of a mutating turn, the contract, the run policy and the **pre-turn snapshot**
(hash view **and** `git status --porcelain`, both computed by Diana per M4-D14) must be written and
`fsync`ed. This is the single ordering rule that makes F2's gap closable, and F12 proved it sufficient.
It is M1's own invariant-1 discipline — *a control must be evaluated before the risk it guards* —
applied to a control that is an obligation rather than a check.

**M5-D6 — Reconciliation is owed by the **run**, not by the process.** Audit finding F-A5 lifted
reconciliation from "only on `Blocked`" to "on any exception"; M5 lifts it again, from "any exception"
to **"any termination, including one the process cannot observe"** (F2). Concretely: a run whose journal
records a started-and-unreconciled turn is an **outstanding obligation**, and the next process that
encounters it must discharge that obligation before doing anything else with that run. A process that
cannot discharge it does not get to ignore it (M5-D9).

**M5-D7 — Three terminal outcomes for a run: `COMPLETE`, `FAILED`, `BLOCKED` — and they are journal
states, never document values.** M1 D36 is unchanged and unweakened: `BLOCKED` is never a value inside
a produced document, and a blocked run yields a run record and no deliverable. These three are the
states of the **run journal**, which is a Diana run record, and they are what the returning user reads.
`COMPLETE` means the work finished and reconciliation proved the diff stayed inside the envelope.
`FAILED` means the work did not finish for a reason that is **not** an envelope violation — the turn
errored, the budget was exhausted, the provider never came back. `BLOCKED` means a Diana control
refused: an envelope violation, a failed authority re-binding, a failed freshness check, or an
obligation that could not be discharged. `FAILED` and `BLOCKED` are kept distinct because "the work did
not get done" and "the system could not prove itself" are different facts, and only the second is a
statement about the boundary.

**M5-D8 — The run is a state machine, and every transition is journaled before it is acted on.**
The states and the only permitted transitions:

```
                 approve              (M5-D5 write-ahead)
   [ APPROVED ] ──────────▶ [ ARMED ] ──────────▶ [ TURN_ACTIVE ]
        │                       │                        │
        │                       │                        │ turn ends, or the process dies
        │                       │                        ▼
        │                       │                 [ RECONCILING ] ◀─── resume finds an
        │                       │                        │              outstanding obligation
        │                       │        ┌───────────────┼───────────────┐
        │                       │        ▼               ▼               ▼
        │                       │   within envelope   not within     cannot prove
        │                       │        │            envelope           │
        │                       │        ▼               ▼               ▼
        │                       │  [ RECONCILED ]   [ BLOCKED ]     [ BLOCKED ]
        │                       │        │
        │                       │        ├──▶ budget remains and work is unfinished ──▶ [ ARMED ]
        │                       │        └──▶ work finished ──▶ [ COMPLETE ]
        │                       │
        └───────────────────────┴──▶ budget exhausted / turn error ──▶ [ FAILED ]
```

`ARMED` is the state in which the audit's inputs are durable and no mutation has yet been attempted; it
is the only state from which a turn may start, and the only state a resume may re-enter. `TURN_ACTIVE`
is the only state in which a mutation may be in flight, and finding it in the journal at startup is
exactly the outstanding obligation of M5-D6. `COMPLETE`, `FAILED` and `BLOCKED` are terminal: a
terminal run is never resumed, only read.

**M5-D9 — Absence, ambiguity and unprovability all fail closed, in the same direction.** A journal
that is missing, unreadable, digest-mismatched, or in a state the state machine does not permit is not
a reason to start fresh — it is `BLOCKED`. The temptation an unattended system creates is precisely to
treat "I cannot tell what happened" as "nothing happened", and F2 is the proof that those are different:
the crash that destroys the evidence is the same crash that is most likely to have left something behind.

**M5-D10 — Resume re-establishes enforcement before it re-establishes anything else.** The
capability and confinement patches are a module global (`hermes_patches._STATE`) and therefore die with
the process. A resumed process has **no** enforcement until it reinstalls it. So the resume sequence is:
verify the journal → re-verify the contract → reinstall confinement and capability policy → prove them
live behaviorally, as M1 D26's self-test does → only then touch anything. M1's ordering rule again: the
control precedes the risk it guards.

**M5-D11 — Authority re-binding on resume requires two independent proofs, and both are mandatory.**
Per F9 the existing binding proves only that the document is the one Diana wrote.

1. **Document integrity** — `contract.json` re-verified by digest and `run_id`, plus the M5-owned run
   policy re-verified by its own digest. This answers *"is this the approved envelope?"*
2. **Target freshness** — the target's `git_commit` and `dirty` state, and the durable pre-turn
   snapshot, still describe the repository the envelope was approved against. This answers *"is this
   still the world it was approved for?"*

Either failing is `BLOCKED`, with its own reason code. F13 is why the second exists: without it, a
resumed reconciliation blames the run for a third party's commit, or lets a third party's revert conceal
the run's own escape. A contract that verifies against a repository that moved is a valid document about
a world that no longer exists.

**M5-D12 — A drifted target is never silently re-approved.** When freshness fails, the run terminates
`BLOCKED` and reports what drifted. It does **not** rebuild a contract, re-derive a scope, or adopt the
new state — re-approval is a **new** contract with a new `run_id`, created by the same approval path
that created the first one. "Approve once" means the approval is reused, never regenerated: a system
that manufactures its own successor approval has no approval at all.

**M5-D13 — The envelope has a declared, digest-bound expiry, and expiry is not an error.** Per F9 there
is no TTL today and a six-hour-old contract verifies exactly as a six-second-old one. M5's run policy
declares an absolute deadline for the whole run, covered by the run-policy digest, and reaching it is a
**normal** terminal outcome (`FAILED`, budget exhausted) with a legible report — not a crash and not a
`BLOCKED`. An envelope that cannot expire is an envelope that was approved once and holds forever, which
is the opposite of a bounded one.

**M5-D14 — Process ownership is proven per PID, by a Diana-owned stamp, and never by process group.**
Per F16, `DIANA_RUN_ID` stamped into the environment before the turn is inherited by every process in
the command tree and is readable at `/proc/<pid>/environ`. Ownership is established by **that stamp plus
the PID's start time**, the same recycling defence Hermes's own `_host_pid_is_ours` uses (F4) — a PID
alone is not an identity. A process-group or name-pattern kill is **prohibited**: F6's tree sits in its
own pgid, so per-PID action is always sufficient, and a PGID kill can reach processes that merely share
a group. That hazard is not hypothetical — M4's audit §4 records a cleanup that killed the supervising
session because an MCP server shared its process group.

**M5-D15 — Quiescence is proven before reconciliation, never assumed.** Reconciling a target that a
previous run's command tree is still writing to produces a reading of a moving object. So before a
resumed process reconciles, it must show that **no** process bearing this run's stamp is alive (M5-D14).
If any is, the run waits within its bound and then terminates it per M5-D14; if it cannot be shown gone,
the run is `BLOCKED` (M5-D9). This is M3-D13's discipline — *termination proven by observation, not by
having requested it* — applied to a tree Diana did not spawn.

**M5-D16 — Retry is Diana-owned, bounded, and may never re-enter a turn whose predecessor was not
reconciled.** Per F8 the only retry today is Hermes's, which Diana does not configure and which is
bounded only transitively by a wall clock that M5 necessarily grows. M5 therefore declares its own
budget in the run policy — a maximum number of turn attempts **and** an absolute wall-clock deadline for
the whole run — and enforces two rules. **Retry is not resumption:** a new attempt may start only from
`ARMED`, which by M5-D5 means a fresh pre-turn snapshot is already durable, so each attempt is
independently attributable. **A mutating turn is not idempotent:** an attempt whose predecessor is
unreconciled is refused, because re-running a partially applied mutation over an unaudited tree
compounds the damage it was meant to detect. Exhausting the budget is `FAILED` (M5-D13), never a partial
pass.

**M5-D17 — The blocked-item report is a first-class deliverable, written for someone who was not
watching.** The roadmap's requirement is that returning to a blocked item is *cheap*. The report is
Diana-owned, in the run directory, and states for each item what was attempted, which control refused
it and with which reason code, what the envelope permitted at that moment, and what a human would have
to decide to unblock it. It carries no severity, no risk and no depth proposed by Hermes (M1 D14), and
it is **not** an `ADVISORY_SECURITY_REVIEW` and must not be readable as one (M1 D35's structural rule).

**M5-D18 — No milestone may assert a tool count, and M5 asserts the allowlist property instead.** Per
F14 the registry saturates at 92 on this environment while M4 recorded 77 and M1 88 on the *same* pinned
commit, because 80 of 92 registrations are `check_fn`-gated on environment and credentials. A count is
therefore not a property of the pin and must never be an acceptance criterion. What holds regardless is
set membership: a tool absent from `allowed_tools` is refused whether the registry holds 77 entries or
770. This matters more at M5 than anywhere before it, because an unattended run spans hours during which
gated tools can *become* available — and under an allowlist that changes nothing.

**M5-D19 — F7 is an M4 defect. M5 does not silently fix it, and does not proceed past it.** The
absent-`workdir` asymmetry falsifies the stated reasoning of `mutation_policy.py:219-220` and lets a
run execute in a directory the same policy refuses when it is named. It was found by M5's Phase 0, but
it belongs to M4's frozen `M4-D11`. Two things follow, and both are required. It must be corrected
through **M4's own audit trail** — an erratum and an audit finding against M4, with its own acceptance
assertion — and **not** by M5 quietly widening its permitted-replacement set to reach into
`mutation_policy.py`; M4's ERRATA-001 exists because exactly that kind of silent widening reported green
once already. And it must be closed **before** M5 implementation begins, because the session cwd is
precisely the process state that does not survive a resume: a run that is re-armed in a new process
inherits a default working directory that nothing has ever checked.

**M5-D20 — Unattended execution does not confer merge authority, and M4's governance limitation
becomes binding here.** M4's audit §8 records that `REQUIRE_HUMAN` is an advisory verdict with no
mechanical enforcement (`required_approving_review_count: 0`, `require_code_owner_review: false`), and
carries forward the requirement that *before any future milestone grants autonomous PR-merge authority,
`REQUIRE_HUMAN` needs an independently verifiable human-approval mechanism that automation cannot
self-satisfy*. M5 is the first milestone where an agent is, by design, the only party present when work
completes. M5 therefore grants **no** merge authority, opens **no** pull request, and its terminal
states are reports a human acts on. The carried requirement is restated rather than discharged: M5 does
not satisfy it, and must not be read as having done so.

**M5-D21 — M5 carries its own later-milestone regression invariant**, per the M2-D14 pattern:

- **M5-REG-1** — the frozen M1, M2, M3 and M4 specifications, and `HERMES-RUNTIME-M4-ERRATA-001.md`,
  remain **byte-identical**.
- **M5-REG-2** — pre-existing Diana/AO modules and behavior remain unchanged, **except where a future
  milestone explicitly freezes and proves a replacement**. M5's permitted-replacement set of
  pre-existing **production-code** files is to be stated by **set equality** at implementation time and
  justified file-by-file from a frozen M5 decision, following ERRATA-001 §3's discipline. It does
  **not** include `diana/mutation/mutation_policy.py` (M5-D19).
- **M5-REG-3** — nothing is deleted by later work.
- **M5-REG-4** — all reusable M1, M2, M3 and M4 behavioral acceptance tests remain green.

---

## Crash and recovery semantics

Stated as the rules a reader can check an implementation against.

1. **A crash is not a state.** It is the *absence* of a transition. The journal's last durable state is
   what the system knows; everything after it is unproven and therefore owed (M5-D6, M5-D9).
2. **Mutation never precedes durability.** If the journal does not say `ARMED`, no tool call that can
   mutate has been made (M5-D5).
3. **`TURN_ACTIVE` in a journal at startup means a turn was in flight when the process stopped.** It is
   an obligation, not an invitation to continue: quiescence first (M5-D15), then reconciliation
   (M5-D6), then — only if the envelope held and budget remains — a new attempt from `ARMED` (M5-D16).
4. **The audit survives the auditor.** Because the pre-turn snapshot is durable before the mutation
   (M5-D5), a process that never saw the turn can still compute the same diff the dead process would
   have (F12), including the gitignored paths git cannot see (F3).
5. **What the audit cannot do is attribute.** A diff says what changed, never who changed it (F13).
   Freshness (M5-D11) is what keeps the diff meaningful; without it a post-mortem reconciliation is a
   confident statement about the wrong actor.
6. **Terminal is terminal.** `COMPLETE`, `FAILED` and `BLOCKED` are never resumed. A run that needs to
   continue past a terminal state needs a new approval (M5-D12).

---

## Acceptance criteria

M5 is accepted only if **all** hold, and **all** M1 (13), M2 (13), M3 (16) and M4 (18) criteria remain
green.

| # | Criterion |
|---|---|
| **M5-AC-1** | A bounded remediation writes a durable run journal: contract, run policy and pre-turn snapshot are on disk and `fsync`ed **before** the first mutating tool call, proven by killing the process immediately after `ARMED` and reading them back. |
| **M5-AC-2** | Every journal write is crash-atomic: a write interrupted at an arbitrary point leaves either the previous record or the new one, never a partial or unparseable one, and a torn record is treated as absent and fails closed. |
| **M5-AC-3** | **SIGKILL during a mutating turn is reconciled.** The run is killed with an out-of-envelope mutation on disk; a fresh process discharges the obligation and reports `reconciliation-mismatch`. This is the case F2 measured as silently undetected today. |
| **M5-AC-4** | The same holds for SIGTERM and for an uncatchable death at three distinct points: before the turn, mid-turn, and after the turn but before reconciliation. |
| **M5-AC-5** | A **gitignored** out-of-envelope mutation is detected post-mortem, where `git status --porcelain` alone reports nothing (F3). |
| **M5-AC-6** | A crashed run's obligation cannot be skipped: a process that finds `TURN_ACTIVE` refuses to start new work until reconciliation completes, and refuses outright if it cannot complete it. |
| **M5-AC-7** | Resume re-establishes enforcement before anything else: in the resumed process, confinement and capability are proven live **behaviorally** through the real dispatch path before any tool call, and a resume that cannot prove them is `BLOCKED`. |
| **M5-AC-8** | Authority re-binding is enforced in both halves: a tampered `contract.json`, a tampered run policy, a wrong `run_id`, and a digest mismatch are each refused with their own reason code. |
| **M5-AC-9** | **Target freshness is enforced.** A resume whose target has moved — a new commit, or a dirty state that disagrees with the durable snapshot — is `BLOCKED` with its own reason code, and the third-party file of F13 is **not** attributed to the run. |
| **M5-AC-10** | A drifted target is never silently re-approved: no new contract is built, no scope re-derived, and the run terminates reporting exactly what drifted (M5-D12). |
| **M5-AC-11** | The envelope is unchanged across the whole run: the contract in force at the last attempt is **byte-identical** to the one approved at hour zero, digest included, and every attempt's enforcement is proven against that same document. |
| **M5-AC-12** | The declared absolute deadline is honoured: a run that reaches it terminates `FAILED` with a legible report, not a crash and not a `BLOCKED`, and no attempt starts after it. |
| **M5-AC-13** | Process ownership is proven per PID: every process of a command tree carries the run stamp and is identified by stamp **plus** start time; a recycled PID is **not** adopted; and the acceptance harness performs **no** process-group kill and no name-pattern kill. |
| **M5-AC-14** | **Quiescence precedes reconciliation:** a command tree deliberately left alive after the parent dies (the F6 case) is observed, terminated per M5-D14, and proven gone before the diff is computed; a tree that cannot be proven gone yields `BLOCKED`. |
| **M5-AC-15** | Retry is bounded and attributable: the attempt cap and the wall-clock deadline are both enforced, each attempt has its **own** durable pre-turn snapshot, and an attempt whose predecessor is unreconciled is **refused**. |
| **M5-AC-16** | Nothing under `~/.hermes/` is an input to a Diana decision: with `processes.json` and the terminal cache adversarially populated, every M5 verdict is unchanged, and Hermes-owned state appears in the report as observation only. |
| **M5-AC-17** | The three terminal outcomes are produced and are distinguishable: a run that finishes within its envelope is `COMPLETE`; one that exhausts its budget is `FAILED`; one that violates its envelope or fails a binding is `BLOCKED` — and `BLOCKED` appears in **no** produced document, per M1 D36. |
| **M5-AC-18** | The blocked-item report is legible and structurally safe: each item names what was attempted, the refusing control, its reason code and what a human must decide; it carries no Hermes-proposed severity, risk or depth; it is rejected by `artifact.validate()` and classifies `MALFORMED` under `evidence_model` (M1 D35 / M3-D15). |
| **M5-AC-19** | Capability is unchanged: `allowed_tools`, `write_scope` and `allowed_commands` are identical to M4's, `risk` derives `ELEVATED` and `depth` `D2` from the same certified class, M5 certifies no new workflow class, and every non-granted tool is still refused through the real dispatch path — asserted as **set membership**, with **no** count assertion anywhere in the suite (M5-D18). |
| **M5-AC-20** | An unattended run opens no pull request, performs no merge, and spawns no `git`/`gh` mutation subprocess beyond what M4 already permits (M5-D20). |
| **M5-AC-21** | **End-to-end:** a real bounded remediation is interrupted by an uncatchable kill mid-turn, resumed by a fresh process under the original contract, completes the work, and returns `COMPLETE` with a reconciliation proving the whole diff — across both attempts — stayed inside the envelope, while adversarial attempts in the same run are still refused. |
| **M5-AC-22** | M5-REG-1…4 hold, with the permitted-replacement set asserted by **equality** and `mutation_policy.py` absent from it (M5-D19, M5-REG-2). |

---

## Carried assumptions — stated, not proven

- **F7 must be closed first.** M5 implementation may not begin until the absent-`workdir` defect is
  corrected through M4's audit trail (M5-D19). A resumed run inherits a default working directory that
  nothing checks, so this is a precondition, not a parallel task.
- **Per-PID ownership rests on `/proc`.** M5-D14's mechanism is Linux-specific and assumes `/proc` is
  mounted and readable. On a platform without it, the quiescence proof of M5-D15 is unavailable and the
  correct behavior is `BLOCKED`, not a weaker check.
- **The stamp marks descendants, not arbitrary relatives.** A process that Hermes spawns *before* Diana
  sets the stamp would not carry it. M5's ordering (stamp before arming, M5-D5) is what makes the marker
  complete, and an implementation that stamps later has a hole the acceptance suite must catch.
- **Quiescence is about processes Diana can see.** A command that hands work to a system service, a
  container, or a remote host leaves no stamped descendant. M5 bounds the process tree it spawned; it
  does not bound delegation off the machine, and M4's carried assumption — that the command allowlist is
  only as good as what Diana declares — is unchanged and now matters for longer.
- **Reconciliation remains detection, never prevention** (M4-D14). Durability makes the detection
  survive a crash; it does not make it a control.
- **A live provider is required** for the end-to-end criteria, as in M2 and M4, and `M2-AC-4`'s known
  sensitivity to live-provider behavior (M4 audit §6) is unchanged.
- **This specification has not been independently audited.** M4's audit is explicit that a self-audit is
  not a substitute for independent review, and that the first independent reviewer to examine M4 found
  two further defects in a control the self-audit had signed off. M5 is frozen on the same terms and
  carries the same exposure.
