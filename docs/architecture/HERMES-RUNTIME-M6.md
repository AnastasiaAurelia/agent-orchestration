# Hermes Runtime — Milestone 6: Multi-Actor / Reviewer, and gradual AO replacement (FROZEN)

Status: **normative and frozen**. Source of truth for M6; remains normative after implementation.

Architectural decisions **M6-D1 … M6-D24, M6-R1 … M6-R7 and M6-REG-1 … M6-REG-5 are frozen**. M1's D1–D38, M2's D1–D14, M3's D1–D16, M4's
D1–D16 (as corrected by [`HERMES-RUNTIME-M4-ERRATA-001.md`](HERMES-RUNTIME-M4-ERRATA-001.md) and
[`HERMES-RUNTIME-M4-ERRATA-002.md`](HERMES-RUNTIME-M4-ERRATA-002.md)) and M5's D1–D21 (as extended by
[`HERMES-RUNTIME-M5-ERRATA-001.md`](HERMES-RUNTIME-M5-ERRATA-001.md)) remain frozen and unmodified;
where M6 appears to disagree with any of them, the earlier milestone wins and the M6 text is the defect.

Branch: `feature/hermes-multi-actor`, based on accepted M1+M2+M3+M4+M5 `main` (`29c5a7f`).
Roadmap: [`HERMES-ROADMAP.md`](HERMES-ROADMAP.md).
Prior: [`HERMES-RUNTIME-M1.md`](HERMES-RUNTIME-M1.md), [`HERMES-RUNTIME-M2.md`](HERMES-RUNTIME-M2.md),
[`HERMES-RUNTIME-M3.md`](HERMES-RUNTIME-M3.md), [`HERMES-RUNTIME-M4.md`](HERMES-RUNTIME-M4.md),
[`HERMES-RUNTIME-M5.md`](HERMES-RUNTIME-M5.md).

---

## Thesis

> More than one bounded actor participates in the same Diana-governed work, and **no authority is
> created, multiplied, or laundered** by the fact that there is more than one.

M5 established the chain *one approved envelope → one bounded unattended executor → durable
Diana-owned state → crash/restart/resume*. M6 asks whether a second actor can join that chain
without adding a second source of authority.

**M6 grants no capability.** The approved envelope is M4's `BOUNDED_REMEDIATION` envelope, unchanged
and unwidened: same `allowed_tools`, same `write_scope`, same `allowed_commands`, same
`risk = ELEVATED`, same `depth = D2`, one `run_id`, one contract, one digest. What M6 adds is that
the *single* approved envelope may be **projected** — narrowed, never widened — onto a small fixed
set of actor roles, and that Diana records which role acted, before it acts.

The hazard M6 exists to refuse is specific and has a name: **model-to-model trust becoming
authority**. A reviewer that says "this is fine" must not thereby be able to make it fine; a second
model backend must not arrive with a fresh budget in its hand; a builder must not be able to become
a reviewer by saying so.

---

## Non-goals

M6 adds **none** of the following, and a later milestone that wants one needs its own design round:

- arbitrary network access;
- autonomous `git push`, autonomous PR creation, autonomous PR merge (M5-D20 restated, not discharged);
- deployment, credential access, repository-settings mutation;
- unrestricted shell;
- Hermes `delegate_task`, subagents, ACP children, or any Hermes-owned delegation facility (M6-D5);
- concurrent actors (M6-D11);
- a Planner actor (M6-D2);
- natural-language product UX — M7 remains Product UX;
- any relaxation of Security Track evidence requirements;
- deletion or modification of any existing AO path (M6-D23, M6-D24).

---

## Phase 0 — empirical findings

Established against the real repository at `29c5a7f` and the pinned Hermes `0.21.1`
(`b8e8639445bd6f05a8141abcea7ae2aa8279f2b7`), by execution rather than from documentation. Each
finding below was **measured**; the probe that produced it is named in prose so it can be re-run.

| # | Finding |
|---|---|
| **F1** | **The AO runtime is absent on this host; the AO regression surface is fully alive.** `ao` is on no `PATH`, `~/.ao` does not exist, and `python3 diana/adapters/ao.py check` returns `{"ok": false, "error": "ao_missing"}`. Yet `diana/adapters/test-ao-adapter.sh` passes 9/9 and `diana/ship/test-ship.sh` passes 35 assertions, both entirely on the `diana/adapters/fixtures/fake-ao-*` doubles. So AO's *behavior* is legacy-but-specified, and AO's *proof surface* is deterministic and reproducible without the runtime. Every M6 migration claim must be made against that fixture surface, because that is the only AO surface that can actually be run. |
| **F2** | **AO's responsibilities, inventoried from code rather than from the roadmap.** Two modules own all of it. `diana/adapters/ao.py` (294 lines, the sole AO boundary) owns *delegation and task ownership*: `check`, `spawn` (hardcoded `--harness claude-code`), `status`, `stop`. `diana/ship/ship.py` (10 subcommands) owns everything else: **planning** — `plan-validate` (exactly two workers, disjoint `allowed_files`); **scope enforcement** — `verify-diff`, `cross-worker-check`, plus a hardcoded `FORBIDDEN_PREFIXES` list; **integration** — `integrate` (git worktree + `git merge --no-ff` between disposable branches, never `main`); **review** — `review-verdict`, `reviewer-readonly-check`, `correction-prompt`; **gating and publication** — `gate`, `open-pr`. `diana/commands/*.md` carry the prose protocols a live session follows. Nothing else in the repository constructs an `ao` command. |
| **F3** | **Diana already has a multi-actor pipeline, and it is *attended* by design.** `diana/ship/README.md` states that resolving an AO worker's tool-call approvals "requires a live human in the loop" and that "a deterministic, testable script structurally cannot provide that and must not fake it"; the correction cycle is bounded "by convention in the command's instructions, not by any counter or state `ship.py` tracks". So M6 is not a port of the AO pipeline into the runtime. It is the **first unattended** multi-actor execution, and it inherits none of the AO pipeline's human-in-the-loop safety. |
| **F4** | **Hermes's own delegation facility exists on the pinned version and is substantial.** `delegate_task` is registered; `toolsets.TOOLSETS["delegation"]` presents it; `delegation.max_concurrent_children` is **10**, `max_spawn_depth` **1**, `worktree_isolation` **false** by default; `DELEGATE_BLOCKED_TOOLS` is exactly `{clarify, cronjob_manage, delegate_task, memory, present_wisdom_consent, send_message}`. `tools/delegate_tool.py` spawns child `AIAgent` instances with "the parent's toolsets minus child-blocked tools", and `_build_child_agent`'s own docstring says children "can run on a different provider:model pair". Cross-model execution is a *native* Hermes capability, configured by `delegation.*`, not by the model. |
| **F5** | **In-process enforcement is a property of the PROCESS, not of the actor — measured.** Diana's capability guard closes over exactly `(allowed, original, policy)`; the dispatch guard's signature is `(agent_, state, ref, *, execute, **kwargs)` and Diana ignores `agent_`. Two distinct agent objects were driven through the real guard and received **identical** verdicts: `write_file` refused for both, `read_file` executed for both. Consequence, in both directions: an in-process child **cannot widen** the envelope, and it equally **cannot be narrowed** — it inherits whatever the single process-global install says. A role that is "read-only" because someone said so, in a process where the install says otherwise, is read-only in name only. |
| **F6** | **An ACP child is out-of-process and is a total bypass of Diana's boundary.** `_resolve_child_runtime` accepts `override_acp_command`; `agent/copilot_acp_client.py:310` starts it with `subprocess.Popen([command] + args, stdin=PIPE, stdout=PIPE, stderr=PIPE)`; `hermes_cli/tips.py:264` documents `delegate_task with acp_command: 'claude'` spawning Claude Code as a child agent. Diana's boundary is two in-process monkeypatches (`hermes_patches._STATE`, module globals). Nothing in another process is patched. A cross-model handoff routed through Hermes's own delegation would therefore execute **entirely outside** every M1–M5 control. |
| **F7** | **Worktree isolation mutates the target repository without ever reaching the dispatch boundary.** `tools/subagent_worktree.py:88` runs `git worktree add <repo>/.worktrees/subagent-<id> -b hermes-subagent/<id> HEAD` by direct `subprocess.run`, called from `_create_isolated_worktree` during child seeding. It is not a tool call, so `MutationPolicy.decide` never sees it and `write_scope` never adjudicates it. Diana's hash-snapshot reconciliation would detect the resulting paths **post hoc** — detection, not prevention, and exactly the asymmetry M4-D14 already names. |
| **F8** | **Actor identity is absent from every authoritative Diana document.** A real bounded run was driven to `COMPLETE` and its run directory enumerated: exactly eight files — `contract.json`, `run-policy.json`, `work-items.json`, `journal.json`, `pre-turn-snapshot-001.json`, `reconciliation-001.json`, `turn-record-001.json`, `run-report.json`. None of the seven authoritative ones carries an actor, role, producer or author field, and both `contract.CONTRACT_KEYS` and `journal.RECORD_KEYS` are **closed** schemas whose `validate()` rejects an unknown key outright. M5 durable state cannot today distinguish **who** produced a turn or a result. |
| **F9** | **The one artifact that can name a producer is the one Diana does not validate.** `unattended.run_attempt` writes `getattr(turn_driver, "record", None)` verbatim to `turn-record-<n>.json` with no schema, no digest, and no reference from the journal. Measured: drivers were given `{"actor": "scripted"}` and `{"backend_self_report": "hermes-deepseek"}` and Diana persisted both unaltered. M2-D13 already says the turn record is observability and never a control. **A self-named actor is not an identity**, and M6 must not be tempted to read one there. |
| **F10** | **Journal attempt entries are an OPEN sub-schema.** `journal.validate()` requires only that each attempt entry be a dict containing `attempt` and `state`; `update_attempt(**fields)` writes arbitrary keys through the atomic path. Nothing agent-reachable writes there today, so this is a latent robustness gap rather than a live side channel — but it is precisely the place M6 wants to put authority-relevant data, and roadmap invariant 4 requires closed schemas wherever a decision reads them. |
| **F11** | **Retry budget is run-level and actor-neutral by construction — and would multiply under a naive actor split.** `_budget_state` is exactly `runpolicy.expired(policy)` or `len(record["attempts"]) >= policy["max_attempts"]`. There is no per-actor counter to multiply. Symmetrically: any M6 design that gave an actor its own journal, its own run policy, or its own `run_id` would give it its own `max_attempts` and its own deadline, and the composition would exceed what was approved once. |
| **F12** | **Two actor processes cannot share one Diana run stamp — measured.** Two processes were started in separate sessions, both carrying `DIANA_RUN_ID=<one run>`. `ownership.owned_pids` returned **both**. `require_quiescent(terminate=False)` raised `BLOCKED[quiescence-not-proven]` naming both pids. `require_quiescent(terminate=True)` — which M5-D15 makes **mandatory before reconciliation** — **terminated both**, and the peer was measured dead afterwards. A concurrent peer actor is therefore killed by its peer's ordinary resume path. Concurrency between actors sharing a run is not a preference M6 declines; it is a property M5 already forecloses. |
| **F13** | **M5's `execute()` is a single-executor loop with no pre-terminal return point — measured.** Given a driver that stayed inside the envelope but never finished the work, **one** call consumed all three attempts and returned `FAILED / attempt-budget-exhausted`. Given a run already terminal, a second, different backend was refused `BLOCKED[run-already-terminal]`, having made zero driver calls, with the `COMPLETE` item untouched. So today a backend switch is reachable **only** through a new contract — which by M5-D12 is a new approval with a new `run_id`, a new `max_attempts` and a new deadline. That is precisely the fresh-budget, fresh-envelope outcome M6 must forbid. |
| **F14** | **The executor seam already exists, one level below the loop.** `execute(run_directory, *, turn_driver, is_work_finished, expected_run_id=None, require_quiescence=True)` and `run_attempt(run_directory, record, contract_block, policy, turn_driver, item_id=None)` both take the executor as an injected callable. What is missing is not an abstraction — it is a **Diana-owned selection** of which executor acts, made before the turn and written down. An `AgentExecutor` class hierarchy would add a name, not a bound. |
| **F15** | **Per-actor envelope projection is implementable with the M1/M4 boundary UNMODIFIED — measured.** `hermes_patches.install_capability(tools, MutationPolicy(envelope))` re-wraps `_diana_original_handle` rather than the already-installed guard, so re-installing is single-layer and idempotent (`co_freevars` stayed `('allowed','original','policy')`, `_diana_original_handle` preserved). Under a Builder projection `write_file` succeeded and wrote the file. Re-projecting to `{read_file, search_files}` in the **same process** then refused `write_file`, `patch` **and** `terminal` through the real dispatch path, and the file was byte-unchanged. A role projection is therefore just a **projected envelope dict** through the existing API. |
| **F16** | **A reviewer cannot fetch Diana's evidence for itself.** M5-AC-29 is green today and asserts that the run directory lies outside **both** `read_scope` and `write_scope`, that `read_file` on `journal.json` is refused, and that a `terminal` command aimed at the run directory is refused. Whatever evidence a reviewer sees, Diana must **hand** it to the reviewer as Diana-owned context; the reviewer has no tool path to the journal, the reconciliation records or the run report. |
| **F17** | **Diana's reconciliation is strictly stronger than `ship.py reviewer-readonly-check` — measured, with a falsifying case.** A "reviewer" wrote one gitignored file, `build/artifact.bin`. `ship.py reviewer-readonly-check --expected-head <head>` returned exit `0` and `{"ok": true}` — `HEAD` was unchanged and `git status --porcelain` was empty. Diana's `reconcile.reconcile()` over the same interval reported `paths_touched: ["build/artifact.bin"]`, `within_envelope: false`, `git_status_changed: false`. This is M5's F3 (git alone cannot see a gitignored mutation) applied to the reviewer role, and it is the measured justification for M6's AO migration candidate. |
| **F18** | **The frozen regression assertions determine where M6 may write, and were re-derived rather than inherited.** `test-m2-live-turn.sh` asserts on **every future HEAD** that nothing under `diana/adapters/ao.py`, `diana/adapters/test-ao-adapter.sh`, `diana/gate/`, `diana/ship/`, `diana/security/`, `diana/preflight/`, `diana/ci/`, `diana/playwright/`, `diana/hooks/`, `diana/skills/` is modified. `test-m5-unattended.sh` asserts that `git diff --name-status 69f5569..HEAD` yields a modified-production set exactly equal to `{diana/runtime/blocking.py}`, classifying `docs/` and `.gitignore` separately. Measured on `29c5a7f`: modified = `.gitignore`, `diana/runtime/blocking.py`. Because that range compares **trees**, files first added by M5 (all of `diana/unattended/`) still appear as `A` however often M6 touches them. So M6 may evolve M5's own modules and `blocking.py`, and may modify **nothing else that existed at `69f5569`**. |
| **F19** | **Dispatch surface re-derived (roadmap invariant 2).** The registry saturates at **92** tools on this environment after `import model_tools`. `INLINE_TOOL_EXECUTORS` holds exactly **13** names: `annotate_preview, clarify, delegate_task, desktop_preview, drive_preview, gui_tour, memory, message_agent, read_terminal, read_window_below, session_search, setup_mcp, todo_list`. **Two** of them are second-actor surfaces — `delegate_task` (spawn a child agent) and `message_agent` (`tools/bot_mode_dm.py`, direct-message another agent). Both are denied today by set membership alone, with no dedicated control, and M6 keeps them denied. Per M5-D18 the count 92 is a property of this environment and is asserted nowhere. |
| **F20** | **Pre-M6 regression baseline on `29c5a7f`, before any M6 code.** All **29** Diana suites green. M1 **470** assertions / 0 (9/9 suites, 13/13 criteria); M2 **59** / 0 with a live provider (`deepseek` / `deepseek-v4-flash`); M3 **106** / 0; M4 **146** / 0; M5 **202** / 0, plus journal **54** / 0 and ownership **29** / 0; `ship` **35** PASS / 0 FAIL; AO adapter 9/9. |

### What F5, F12, F13 and F15 together determine

They converge, independently, on the same three-part shape, and none of the three is a taste.

F5 says enforcement is installed once per process, so an in-process actor cannot carry its own
narrower envelope unless the install is **re-projected** for it. F15 says re-projection works through
the existing API and is behaviorally real. F12 says two actors sharing one run cannot be alive at the
same time, because the quiescence proof M5 already requires would kill the peer. F13 says the current
loop never returns anywhere a different executor could be handed the work.

So: **actors are sequential, one at a time, each acting under a re-projected install, selected by
Diana at an attempt boundary.** That is not a design preference arrived at by reasoning — it is the
only shape the measured system admits.

---

## Actor topology

**M6-D1 — The frozen topology is exactly two roles: `BUILDER` and `REVIEWER`.** The set is closed.
It is declared once, at approval, in an M6-owned document, and it is digest-bound. No role may be
added, renamed, removed, split or duplicated during a run, and nothing Hermes emits is an input to
the topology.

**M6-D2 — There is no Planner, and its absence is a finding rather than a deferral.** The artifact a
planner would produce already exists and is already Diana-owned: `work-items.json`, built by
`unattended.approve`, validated as an acyclic dependency graph **before** the run is armed, digest-bound,
and re-verified on every resume (M5-E1-D1/D2/D5). Letting an actor author that document would hand the
agent the one channel M1 D14 denies it, because the item graph determines what work is eligible, what
counts as finished, and how the shared budget is spent. F2 records that AO reached the same conclusion
from the other direction: `ship.py plan-validate` **validates** a plan supplied to it and never produces
one. A planning *actor* would be authority wearing a role's clothes. The plan stays an approval-time
input.

**M6-D3 — An actor is a Diana-constructed agent instance for one attempt, not a long-lived entity.**
M4's `remediation_driver.build_agent` already constructs a fresh `AIAgent` per turn. M6 changes what
envelope is installed before that construction and what Diana writes down about it — nothing else.
An actor has no session, no memory across attempts, no identity the model holds, and nothing to
resume.

---

## Actor identity semantics

**M6-D4 — Actor identity is Diana-owned, assigned before the turn, and durable.** For every attempt,
Diana decides the role, writes it into authoritative durable state, and only then runs the turn. The
ordering is M5-D5's write-ahead rule applied to identity: an attempt whose role was not durable before
it began has no provable actor, and by M5-D9 that is `BLOCKED`, not a guess.

**M6-D5 — M6 uses no Hermes delegation facility, and `delegate_task` stays denied.** Per F4, F6 and
F7 the pinned version can spawn children that run on a different provider, in another process, in
their own git worktree, writing through a path no Diana control adjudicates. M6's actors are
Diana-constructed (M6-D3), sequential (M6-D11), and each gets its envelope from Diana. `delegate_task`
and `message_agent` remain outside `allowed_tools` and are refused by set membership, with no
dedicated control and no exception (M1 D30's reasoning, unchanged).

**M6-D6 — The agent has no channel to name its own actor, and the turn record is not one.** Per F9
`turn-record-<n>.json` is driver-supplied, unvalidated and not digest-bound; a driver was measured
writing an arbitrary `actor` string that Diana persisted verbatim. M6 therefore reads the actor from
**Diana's journal and nowhere else**. An implementation that resolves an actor by reading a turn
record, a model response, a tool argument, or any Hermes-owned state has reintroduced self-naming and
is a defect regardless of what its tests report.

**M6-D7 — Actor identity lives in the journal's attempt entry, and M6 closes that sub-schema.** Per
F10 attempt entries currently accept any key. M6 bumps `JOURNAL_VERSION` to `3`, fixes the attempt
entry's key set exactly, and adds `actor` to it. Two properties follow and both are the point: the
actor becomes covered by the existing journal digest, so tampering is detectable by the same check
that already protects the state machine; and it becomes ordered with the attempt by construction,
because `start_attempt` is the one place an attempt comes into existence. This is chosen over M5-D2's
"write your own file" precedent deliberately — a second document naming actors would have to be kept
in agreement with the journal's attempts, and a disagreement surface between two records of the same
fact is exactly what M5-A1 exploited. Per F18 `diana/unattended/journal.py` remains an **addition**
against every frozen baseline range, so no earlier milestone's regression assertion is disturbed.

**M6-D8 — The per-PID actor stamp is attribution only, is explicitly forgeable, and no authority
depends on it.** M5-D14's ownership stamp `DIANA_RUN_ID` is unchanged and remains the **sole** basis
for ownership and quiescence. M6 additionally stamps the acting role so a surviving command tree can
be *described* in the quiescence observation. A descendant shell can export a different value, so the
stamp is forgeable by the very processes it describes — it is therefore recorded as observation and is
never read to decide anything. Stating this plainly is the point: an attribution field that is quietly
trusted later is worse than no field.

---

## Actor authority semantics

**M6-D9 — Diana remains the only authority source, and an actor may change nothing about its own
constraints.** An actor may not define or choose its role, promote itself, add a tool, widen a path,
add a command, reset or extend a budget, change `risk` or `depth`, certify its own output, or cause
another actor to exist. This is M1 D14 carried forward verbatim into a world with more than one agent
in it; multiplying the agents does not multiply the channels.

**M6-D10 — The actor topology cannot be model-expanded.** The role set is fixed at approval and
digest-bound (M6-D1). A run whose durable topology does not verify, or which names a role outside the
frozen set, is `BLOCKED` with its own reason code. There is no path — configuration, prompt, tool
argument, or journal write — by which a run acquires a third actor.

**M6-D11 — Actors are strictly sequential; at most one actor is live at any instant in a run.** Per
F12 two processes sharing a `DIANA_RUN_ID` are indistinguishable to `ownership.owned_pids`, and
`require_quiescent` — mandatory before every reconciliation (M5-D15) — terminated both. Per F5 the
enforcement install is process-global, so two concurrent in-process actors would necessarily share one
envelope and the last install would silently win. Concurrency is therefore refused by two independent
measured mechanisms, not by preference. Sequencing also keeps every M5 guarantee intact unchanged: one
attempt, one snapshot, one reconciliation, one obligation.

---

## Contract and projection model

**M6-D12 — One parent contract with Diana-owned role projections. Not separate contracts, and the
alternative is refused on evidence.** Separate actor contracts would each need their own `run_id`
(the contract schema is closed and carries no actor field, F8), therefore their own journal, therefore
their own `max_attempts` and their own deadline (F11) — so their composition would exceed the single
approved envelope **by construction**, and M5-D12 already says a second contract is a second approval.
A projection cannot have that failure mode, because it is derived from the approved envelope by
narrowing and is checked to be a subset before it is installed.

**M6-D13 — A projection is a subset, proven by containment, and a non-subset projection is refused
before installation.** For every role, all four must hold against the parent contract's envelope:

- `allowed_tools` ⊆ parent `allowed_tools`;
- `write_scope.allowed_roots` each within a parent allowed root, and `denied_subpaths` ⊇ the parent's;
- `allowed_commands` ⊆ parent `allowed_commands` (exact-match, M4's rule unchanged);
- `read_scope` within the parent's `read_scope`.

The union of every role's projection must also be ⊆ the parent envelope. A projection that adds a
tool, widens a root, adds a command, or removes a denied subpath is `BLOCKED` — it is not clamped,
not intersected, and not repaired, because a silently repaired over-broad projection is an
over-broad projection that reported green (ERRATA-001 §3's discipline).

**M6-D14 — Projection is enforced by re-installing the existing M1/M4 boundary, which M6 does not
modify.** Per F15 `install_capability(projected_tools, MutationPolicy(projected_envelope))` re-wraps
the preserved original, is single-layer and idempotent, and was measured refusing `write_file`,
`patch` and `terminal` under a read-only projection in a process that had just executed `write_file`
under a builder projection. `hermes_patches.py`, `mutation_policy.py` and `contract.py` are untouched
by M6 (F18, M6-D20). The boundary is not replaced and is not duplicated; it is re-parameterised, at
the same choke point, by Diana, before the actor exists.

**M6-D15 — Enforcement is re-established and behaviorally proven before every actor turn, not once
per process.** M5-D10 requires a resumed process to prove confinement and capability live before
touching anything. M6 extends that to every **actor transition**, for the reason F5 measured: the
install that is live is the last one installed, so an actor turn that begins without its own proven
projection is running under its predecessor's envelope. The self-test is M1 D26's — real tools through
the real dispatch path — and a projection that cannot be proven live is `BLOCKED`.

---

## Budget sharing semantics

**M6-D16 — There is exactly one budget for a run, and every actor turn spends it.** The run policy's
`max_attempts` and absolute deadline are unchanged and remain run-level (F11). Every actor turn —
builder or reviewer — is an attempt, appended to the one `attempts` list, with its own durable
pre-turn snapshot (M5-D16) and its own reconciliation obligation (M5-D6). There is no per-actor
budget, no per-role allowance, and no review cycle counter that is not simply the shared budget being
spent. A review that rejects and a rebuild that follows cost two attempts, and when the shared budget
is exhausted the run is `FAILED` (M5-D13) — never a partial pass, and never a fresh allowance for
whichever actor has not had its turn.

**M6-D17 — Changing actor or backend never resets budget, envelope, obligations or item state.** The
run policy, the contract, the work-item document and the journal are all unchanged by an actor
transition; only the acting role changes. Concretely and testably, across a transition: `max_attempts`
and `deadline_at` are the same values, `contract.json` is byte-identical with the same digest, a
`COMPLETE` item is never re-attempted, a dependency-blocked item stays blocked, and an attempt whose
predecessor is unreconciled is still refused.

---

## Process ownership semantics

**M6-D18 — M5-D14 and M5-D15 are unchanged, and ownership stays keyed on `DIANA_RUN_ID` alone.**
Ownership is the stamp plus PID start time; a process-group kill and a name-pattern kill remain
prohibited; quiescence is proven by observation before every reconciliation. M6 adds no second
ownership key, because F12 showed a shared stamp already makes every process of the run ownable and
M6-D11 makes concurrency impossible — so there is never a peer that needs protecting from its peer's
quiescence proof.

**M6-D19 — A handoff may not occur while any stamped process of the run is alive.** The acting actor
changes only at an attempt boundary, and an attempt boundary already requires the run to pass through
reconciliation, which already requires quiescence (M5-D15). Stated as its own decision because the
failure it prevents is silent: handing work to a second actor while the first actor's command tree is
still writing to the target produces a second actor reasoning about, and reconciling against, a moving
object.

---

## Actor handoff semantics

**M6-D20 — Handoff is an intra-run, pre-terminal event at an attempt boundary. A terminal run is
never handed off.** Per F13 a terminal run refuses every executor with `run-already-terminal`, and the
only way past it is a new contract — a new approval, with a new budget (M5-D12). So M6's handoff must
happen while the run is still live, from `ARMED`, after the previous attempt's obligation has been
discharged. The sequence, in order, is fixed:

```
  [ ARMED ]
     │  Diana selects the next actor from the frozen topology
     │  and journals it in the new attempt entry            (M6-D4, M6-D7)
     ▼
  quiescence proven for the run                             (M5-D15, M6-D19)
     │
     ▼
  projection derived from the parent contract, proven ⊆     (M6-D13)
     │
     ▼
  boundary re-installed and proven live behaviorally        (M6-D14, M6-D15)
     │
     ▼
  [ TURN_ACTIVE ]  the actor's turn runs
     │
     ▼
  reconciliation discharged, item outcome decided by Diana  (M5-D6, M5-E1-D4)
```

No step may be skipped and none may be reordered: this is M1's invariant 1 — a control is evaluated
before the risk it guards — applied to the moment an actor changes.

**M6-D21 — Backend replaceability is a property of the seam, not of a vendor, and needs no new
abstraction.** Per F14 the executor is already an injected callable at both `execute` and
`run_attempt`. What M6 adds is the **Diana-owned selection** of which callable acts and the durable
record of that choice; an `AgentExecutor` class hierarchy would add a name and no bound. A replacement
backend is therefore any callable satisfying the same seam, and it receives exactly what M5 already
provides: the same approved contract, the same target binding, the same work-item state, the same
remaining budget, the same outstanding obligations. It is a **fresh actor given durable run state** —
never a session, a transcript, or a continuation of another model's thoughts. **M6 implements no
Codex/OpenAI integration**; the frozen scope does not justify one, and the property under test is
replaceability, which is proven with a second in-repository executor and not with a vendor.

**M6-D22 — A backend or actor switch may never create authority.** Restated as the list an
implementation is checked against, because this is the milestone's whole thesis: a switch must not
create a fresh retry budget, create or regenerate an authority envelope, forget a reconciliation
obligation, replay `COMPLETE` work, bypass a blocked dependency, or inherit anything from another
model's session. Each is an acceptance criterion below, and each is falsification-tested.

---

## Reviewer semantics

**M6-R1 — `REVIEWER` is read-only, and that is a measured property, not a declaration.** Its
projection is exactly `{read_file, search_files}` with no `write_scope`, no `allowed_commands` and no
`command_policy`. Per F15 that projection was measured refusing `write_file`, `patch` and `terminal`
through the real dispatch path while `read_file` succeeded and the target was byte-unchanged. A
reviewer that "does not mutate" because it was asked not to is the thing M2-D7 already refused to
accept as evidence.

**M6-R2 — The reviewer runs no verification commands; Diana does.** A test command executes repository
code and can therefore mutate, so granting `terminal` to a read-only role would return the mutation
authority the role exists to withhold, one layer down. Verification remains Diana-side and
deterministic — M1's scanner and M3's runtime verifier are the same discipline — and its **results** are
handed to the reviewer as evidence.

**M6-R3 — Reviewer evidence is Diana-supplied, and its composition is fixed.** Per F16 the reviewer
has no tool path to the run directory at all. Diana supplies exactly: the attempt's reconciliation
record (paths touched, paths outside `write_scope`, `within_envelope`), the `git status --porcelain`
before/after pair, the diff of the attempt under review, the deterministic verification result, the
item's declared task, and the envelope in force at that moment. It does **not** receive the journal,
the contract digest, the run policy, another actor's prompt, or anything under `~/.hermes/`
(M5-D3, unchanged). What the reviewer may read of the target itself is its own `read_scope`, which is
a projection of the parent's and no wider.

**M6-R4 — A reviewer verdict is an INPUT to a Diana decision and is never a state transition.** M5's
`is_work_finished` remains the Diana predicate that decides completion, and it still runs only after
reconciliation has been discharged (M5-E1-D4). A reviewer `PASS` is **necessary and not sufficient**:
an attempt whose diff left the envelope is `BLOCKED` no matter what the reviewer said, and no reviewer
output can move an item to `COMPLETE`, move the run to a terminal state, extend a budget, or unblock a
dependency. Diana owns every transition, exactly as before.

**M6-R5 — The verdict is a closed schema, validated for shape and for self-consistency, and absence
is failure.** The contract is `{decision, summary, findings[], dod_checks[]}` with `decision ∈
{PASS, FAIL}`. Refused, each with its own reason code: no output at all — a crashed, stalled or
refusing reviewer must look **identical** to an explicit failure and must never read as a pass;
unknown or missing keys; an invalid `decision`; a `FAIL` carrying neither a finding nor a failing
check; and a `PASS` that coexists with any `dod_checks` entry marked `FAIL`. That last one is not
resolved in either direction — a verdict that contradicts itself is refused, not interpreted. This is
`ship.py cmd_review_verdict`'s discipline, reimplemented Diana-side because M6 needs it unattended and
F18 forbids modifying the original.

**M6-R6 — A rejection is a normal, budgeted outcome, and the builder may respond within the same
run.** A reviewer `FAIL` leaves the item `PENDING` — not `BLOCKED` — provided the attempt's
reconciliation was clean, because "the work is not right yet" and "the boundary failed" are different
facts and only the second is a statement about authority (M5-D7's distinction, carried down to the item).
The next builder attempt receives the verdict's findings verbatim as scoped context. It spends the shared
budget (M6-D16), and when that budget is exhausted the run is `FAILED` with the outstanding findings in
the blocked-item report. There is no separate correction-cycle counter, because there is no separate
budget to count against.

**M6-R7 — A reviewer cannot review its own output, and a builder cannot forge a rejection.** The role
of every attempt is journaled before the turn (M6-D4/D7), so an attempt has exactly one role and it
was chosen by Diana. A verdict is accepted only when it is the output of an attempt Diana journaled
as `REVIEWER`; the output of a `BUILDER` attempt is never readable as a verdict, whatever it contains.
An item's reviewing attempt must not be the same attempt that built it, and Diana refuses a verdict
that reviews an attempt with the same actor as itself. Identity confusion is closed at the source: the
identity was never the agent's to state.

---

## Failure and retry semantics

Unchanged from M5 except where an actor makes a distinction necessary. Stated as the rules an
implementation is checked against:

1. **Every actor turn is an attempt**, with its own durable pre-turn snapshot and its own
   reconciliation obligation (M5-D5, M5-D6, M5-D16).
2. **An envelope violation is a run-level fail-closed event** regardless of which actor caused it, and
   regardless of any reviewer verdict. `BLOCKED`, reason `reconciliation-mismatch`, no deliverable.
3. **A failed turn with a clean reconciliation blocks that item and its dependents**, leaving
   independent items free — M5-E1-D7, unchanged, and it does not matter which actor failed.
4. **A reviewer rejection with a clean reconciliation re-arms the item** (M6-R6). It is not a boundary
   failure and must not be reported as one.
5. **An unprovable actor is `BLOCKED`.** A journal whose attempt entry has no actor, an actor outside
   the frozen topology, a projection that is not a subset, or a projection that cannot be proven live
   all fail closed in their own direction, with their own reason codes (M5-D9's rule, unchanged).
6. **Terminal is terminal, across actors.** A terminal run is read, never handed to another actor
   (M6-D20).

---

## AO migration scope

**M6-D23 — M6 migrates exactly one AO responsibility, and migration means replacement-in-use, not
deletion.** Per F18 the frozen M2-REG-2 assertion forbids modifying anything under `diana/ship/`,
`diana/adapters/ao.py` or seven other pre-existing trees on **every future HEAD**, and M5-AC-22 pins
the modified-production set to `{diana/runtime/blocking.py}`. So the old path is kept **byte-identical
and green** — which is exactly the "no big-bang deletion, old path keeps working" requirement — and the
new path is additive. What retires is the **responsibility**, recorded here.

**AO-MIG-1 — reviewer read-only enforcement.**

| | |
|---|---|
| **Old path** | `diana/ship/ship.py cmd_reviewer_readonly_check` — compares the reviewer worktree's `HEAD` to an expected commit and requires `git status --porcelain` to be empty. |
| **New bounded path** | Diana's M5 reconciliation over the reviewer attempt's own durable pre-turn snapshot — the SHA-256 hash view of every regular file plus the git view (M4-D14) — adjudicated against the reviewer's projected (empty) `write_scope`, plus M6-R1's prevention at the dispatch boundary. |
| **Behavioral equivalence** | Proven by case, not asserted: every case the old path refuses, the new path refuses. |
| **Strict improvement, measured** | F17. A reviewer writing a gitignored `build/artifact.bin` is `{"ok": true}`, exit `0` under the old path and `within_envelope: false` under the new one. The old path is blind to exactly the class of mutation M5's F3 identified, applied to the reviewer role. |
| **Regression coverage** | `diana/ship/test-ship.sh` stays green, byte-unchanged, including its two reviewer-mutation cases; the new path gets its own behavioral suite including the falsifying gitignored case. |
| **Retirement** | The reviewer read-only guarantee is, from M6 onward, owned by Diana's reconciliation and by M6-R1's projection. `ship.py`'s subcommand remains present, tested and callable for the attended AO pipeline; no new work relies on it. |

**M6-D24 — Everything else in F2's AO inventory is explicitly NOT migrated by M6, and is listed so
the boundary is legible.** Not migrated: AO worker lifecycle (`ao.py check/spawn/status/stop`),
`plan-validate`, `verify-diff`, `cross-worker-check`, `integrate`, `correction-prompt`, `gate`,
`open-pr`, and every `diana/commands/*.md` protocol. Each needs its own old-path → new-path →
equivalence → coverage → retirement cycle, and M6 claims one. Reviewer **verdict validation**
(M6-R5) is new Diana-owned capability that M6 needs in order to function at all, not a migration
claim — the AO subcommand it resembles keeps its responsibility until a later milestone retires it
properly.

---

## Regression invariants

**M6-REG-1** — The frozen M1, M2, M3, M4 and M5 specifications, and
`HERMES-RUNTIME-M4-ERRATA-001.md`, `HERMES-RUNTIME-M4-ERRATA-002.md` and
`HERMES-RUNTIME-M5-ERRATA-001.md`, remain **byte-identical**.

**M6-REG-2** — M6's permitted-replacement set of pre-existing **production-code** files is stated by
**set equality** at implementation time and justified file-by-file from a frozen M6 decision. Per F18
it must not include any file that existed at `69f5569` other than `diana/runtime/blocking.py`, and it
must not include `diana/mutation/mutation_policy.py` (M5-D19, carried), `diana/adapters/hermes_patches.py`,
`diana/runtime/contract.py`, `diana/adapters/ao.py`, or anything under `diana/ship/`, `diana/gate/`,
`diana/security/`, `diana/preflight/`, `diana/ci/`, `diana/playwright/`, `diana/hooks/` or `diana/skills/`.

**M6-REG-3** — Nothing is deleted.

**M6-REG-4** — All reusable M1, M2, M3, M4 and M5 behavioral acceptance tests remain green, and the
pre-existing Diana regression remains green: **29/29 suites**, at or above F20's per-suite assertion
counts.

**M6-REG-5** — The AO path remains behaviorally unchanged: `diana/adapters/ao.py`,
`diana/adapters/test-ao-adapter.sh` and every file under `diana/ship/` are byte-identical, and
`test-ao-adapter.sh` and `test-ship.sh` pass with the same counts F20 recorded.

---

## Acceptance criteria

M6 is accepted only if **all** hold, and **all** M1 (13), M2 (13), M3 (16), M4 (18) and M5 (22 +
ERRATA-001) criteria remain green. Every criterion below is behavioral and falsifiable; a criterion
satisfied only by a model declining to misbehave is **not** satisfied (M2-D7, restated — model
refusal is not enforcement evidence).

| # | Criterion |
|---|---|
| **M6-AC-1** | **Actor A cannot impersonate actor B.** The acting role of every attempt is read from the journal alone; a turn record, model output, or tool argument naming a different actor changes no verdict, and a probe that plants one is proven not to. |
| **M6-AC-2** | **The Builder cannot gain Reviewer authority and the Reviewer cannot gain Builder authority.** Under the reviewer projection, `write_file`, `patch` and `terminal` are each refused through the **real dispatch path** with their own refusal text, and the target is byte-unchanged; under the builder projection the reviewer's verdict path is unreachable. |
| **M6-AC-3** | **The reviewer is read-only by measurement, not by instruction.** A reviewer turn that is *forced* — through M2's `ToolCallCorruptor` seam, upstream of every Diana control — to emit `write_file`, `patch` and `terminal` is refused at each, and reconciliation over the reviewer attempt shows `paths_touched == []`. |
| **M6-AC-4** | **A projection is proven a subset, and a non-subset projection is refused.** Composition of all role projections ⊆ the parent envelope is asserted by set containment; a projection adding a tool, widening a `write_scope` root, adding a command, or dropping a denied subpath is `BLOCKED` with its own reason code — not clamped, not intersected. |
| **M6-AC-5** | **Separate actor envelopes cannot compose into authority greater than the approved parent envelope**, asserted over the union of every projection and every attempt actually run, against the byte-identical `contract.json`. |
| **M6-AC-6** | **Changing actor does not reset the retry budget.** Across a builder → reviewer → builder sequence, `attempts` increases monotonically in the one list, `max_attempts` and `deadline_at` are unchanged, and the run terminates `FAILED / attempt-budget-exhausted` at exactly the declared cap — never one attempt later. |
| **M6-AC-7** | **Changing model backend does not create a new envelope.** A second, in-repository executor backend takes over mid-run; `contract.json` is byte-identical with the same digest, the run policy digest is unchanged, and no new `run_id` or run directory is created. **No vendor integration is added** (M6-D21). |
| **M6-AC-8** | **Reconciliation obligations survive actor handoff.** A handoff attempted while the previous attempt is unreconciled is **refused**; an uncatchable kill during actor A's turn leaves an obligation that actor B's process must discharge before any new work, and cannot skip. |
| **M6-AC-9** | **`COMPLETE` work is not replayed by another actor.** After an item reaches `COMPLETE`, a different actor's turn driver is never invoked for it — asserted on the recorded driver-call list, not on the absence of a diff. |
| **M6-AC-10** | **Blocked dependencies remain blocked across actors.** A dependency-blocked item is never dispatched to any actor, and a second actor cannot make it eligible. |
| **M6-AC-11** | **Actor identity is durable across crash and restart.** SIGKILL mid-turn; a fresh process reads the acting role from the journal, and a journal whose attempt entry lacks an actor, or names one outside the frozen topology, is `BLOCKED` with its own reason code. |
| **M6-AC-12** | **Stale actor state is refused.** A rolled-back journal that rewinds the actor sequence is refused by M5-A1's artifact-count check; a tampered actor value fails the journal digest; both carry their own reason codes. |
| **M6-AC-13** | **The actor topology cannot be model-expanded.** The topology document is digest-bound and re-verified on every resume; a run naming a third role, or whose topology digest mismatches, is `BLOCKED`. `delegate_task` and `message_agent` are refused through the real dispatch path by set membership (F19), asserted as membership with **no count assertion anywhere** (M5-D18). |
| **M6-AC-14** | **Reviewer rejection cannot be forged by the Builder.** A `FAIL` verdict emitted by a `BUILDER` attempt is not readable as a verdict; and a verdict whose reviewing attempt has the same actor as the attempt under review is refused (M6-R7). |
| **M6-AC-15** | **Reviewer approval alone cannot bypass Diana's state transitions.** A reviewer `PASS` over an attempt whose diff left the envelope still yields `BLOCKED / reconciliation-mismatch` and no deliverable; a `PASS` never by itself moves an item to `COMPLETE`, terminates the run, or extends a budget. |
| **M6-AC-16** | **The verdict schema fails closed in every direction.** No output, unknown key, missing key, invalid `decision`, a bare `FAIL` with no stated reason, and a self-contradictory `PASS` alongside a failing `dod_check` are each refused with their **own** reason code — one over-broad check passing all six proves nothing about any of them (M1 AC-1's reasoning). |
| **M6-AC-17** | **A rejection re-arms rather than blocks, and spends the shared budget.** After a clean-reconciliation `FAIL`, the item is `PENDING`, the findings reach the next builder attempt verbatim, and the attempt count has advanced by the number of turns actually taken. |
| **M6-AC-18** | **Enforcement is re-proven at every actor transition.** In each actor's turn, confinement and capability are proven live **behaviorally** through the real dispatch path before any tool call, and a transition that cannot prove them is `BLOCKED`. An implementation that installs once per process fails this criterion. |
| **M6-AC-19** | **Handoff requires quiescence.** A handoff attempted while a stamped process of the run is alive is refused; the peer-termination hazard F12 measured is proven absent because no concurrent actor is ever created. |
| **M6-AC-20** | **AO-MIG-1 is replaced behaviorally without breaking its old regression surface.** Every case `ship.py reviewer-readonly-check` refuses, the Diana path refuses; the gitignored case of F17 is `ok: true` under the old path and detected under the new one, asserted on both in the same test; and `test-ship.sh` passes byte-unchanged with F20's count. |
| **M6-AC-21** | **The AO path is untouched.** `diana/adapters/ao.py`, `diana/adapters/test-ao-adapter.sh` and every file under `diana/ship/` are byte-identical to `29c5a7f` (M6-REG-5). |
| **M6-AC-22** | **End-to-end, unattended:** an approved run decomposed into items is built by `BUILDER`, reviewed by `REVIEWER`, rejected once, rebuilt, approved, reconciled and returned `COMPLETE` — interrupted by an uncatchable kill at one actor transition and resumed by a fresh process under the original contract — with the whole diff across every attempt and both actors proven inside the one envelope, and adversarial attempts in the same run still refused. |
| **M6-AC-23** | **M6-REG-1…5 hold**, with the permitted-replacement set asserted by **equality**, `mutation_policy.py`, `hermes_patches.py`, `contract.py`, `ao.py` and `diana/ship/` each proven absent from it, and 29/29 suites green at or above F20's counts. |
| **M6-AC-24** | **No non-goal was added:** the run opens no pull request, performs no merge, makes no network call outside the provider transport, spawns no `git`/`gh` mutation subprocess beyond what M4 permits, creates no `.worktrees/` path (F7), and starts no out-of-process agent (F6). |

---

## Acceptance quality requirements

M5's independent review found vacuous assertions that had reported green, and M4's audit found two
defects a self-audit had signed off. M6 plans for that from the start rather than after it.

1. **Every criterion above must have at least one falsification test** — a test that fails when the
   control is removed. A criterion whose assertion passes with the control uninstalled is vacuous and
   does not count as satisfied.
2. **No assertion may be satisfied by a model declining to act.** Adversarial cases use the
   `ToolCallCorruptor` seam or drive the dispatch path directly, as M2-D7 requires.
3. **No count assertions** on the Hermes tool registry, anywhere in the suite (M5-D18).
4. **An independent actor-boundary audit is required**, conducted as if by someone who did not write
   the implementation, with a stated verdict, covering at minimum: role forgery, projection widening,
   budget multiplication, verdict forgery, handoff-without-quiescence, and stale-actor replay.
5. **A fresh-context review** of the acceptance suite specifically for vacuity, following the M5
   development-handoff precedent recorded in `diana/unattended/M5-AUDIT.md` §8.

---

## Unresolved decisions — deliberately left open

Recorded so that a later reader can tell what M6 decided from what it declined to decide.

- **Whether a reviewer should ever run verification commands.** M6-R2 says no, and Diana runs them.
  If a future milestone wants reviewer-executed verification, it needs a design for a command that
  cannot mutate — which M4 already found to be an unwinnable parsing game — not an exception here.
- **Whether a third role ever earns its place.** M6-D2 refuses a Planner on evidence available now.
  A later milestone wanting one must show a repository-derived need and a channel by which its output
  does not become an agent-authored constraint.
- **How a reviewer's *semantic* judgment is bounded.** M6 bounds what a reviewer may *do* and what its
  verdict may *cause*. It does not bound whether the judgment is good, and nothing here should be read
  as claiming it does.
- **Concurrency.** M6-D11 forecloses it for this milestone on measured grounds (F12, F5). A future
  milestone wanting concurrent actors must first re-open M5-D14/D15's ownership model, which is frozen.

---

## Carried assumptions — stated, not proven

- **The whole of M5's carried assumptions carry forward unchanged**, including: per-PID ownership
  rests on `/proc`; quiescence bounds only processes Diana can see; reconciliation remains detection
  and never prevention; the command allowlist is only as good as what Diana declares; and a live
  provider is required for the end-to-end criteria.
- **M5-D20's merge-authority requirement is restated and again not discharged.** M6 grants no merge
  authority and opens no pull request. `REQUIRE_HUMAN` still has no independently verifiable
  human-approval mechanism that automation cannot self-satisfy, and M6 must not be read as having
  supplied one — a milestone that adds a *reviewer* is exactly the milestone most likely to be
  misread that way.
- **The actor stamp is forgeable by descendants** (M6-D8) and is attribution only. An implementation
  that begins reading it to decide anything has introduced a defect the acceptance suite must catch.
- **AO behavioral equivalence is proven against fixtures, not against a live AO runtime**, because
  per F1 no AO runtime exists on this host. The equivalence claim in AO-MIG-1 is therefore a claim
  about the specified and tested behavior of `reviewer-readonly-check`, not about an observed live
  AO session.
- **Hermes's delegation facilities remain present and unpatched.** M6 denies them by set membership
  (M6-D5), which is the same control M1 relied on; it does not disable them inside Hermes, and a
  future milestone that grants any tool near them must re-derive the dispatch surface again
  (roadmap invariant 2).
- **This specification has not been independently audited.** It is frozen on the same terms as M4 and
  M5 and carries the same exposure.
