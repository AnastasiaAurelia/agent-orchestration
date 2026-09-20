# Diana

**Diana is a deterministic authority and governance layer around nondeterministic coding agents.**
A model may propose and act, but permissions, durable state, verification, recovery and approval live
outside the model.

> Intelligence proposes. Diana governs.

**Hermes** is the reasoning and execution backend — the thing that reads, writes and runs commands.
Diana is not tied to trusting one model session: the executor is a replaceable seam, and replacing it
creates no new authority and no new budget.

---

## What problem this solves

A coding agent that can edit your repository is useful and unaccountable. The usual answers are to
trust the model, or to watch it. Diana takes a third: **put the permissions outside it.**

Before anything runs, Diana derives a concrete envelope — which files may be read, which may be
written, which exact commands may run — shows it to you, and requires you to approve that exact
object. During the run it owns the durable state, the verification and the evidence. Afterwards it
reports what actually happened from its own records, not from what the model says happened.

The model never grants itself anything. That is the whole design.

---

## What it looks like

```console
$ diana-do "Fix the failing tests in this repo, but don't touch auth"

GOAL
  Fix the failing tests in this repo, but don't touch auth

PLAN
  1. Fix the failing tests in this repo, but don't touch auth
  then: builder then reviewer

AUTHORITY
  Can read    . (this repository)
  Can edit    src/core, tests
  Cannot edit .git/, .env, .env.*, src/auth  (you excluded this)
  Can run     'python3 check.py', 'python3 -m pytest -q'
  Cannot      anything not listed above — no network, no deploy, no merge, no credentials
  Limits      6 attempts, 3600 seconds from run creation

APPROVAL REQUIRED
  Risk ELEVATED · needs your explicit approval before anything runs.
  This approves starting this run only. It is not a merge, deploy or review approval.

  sha256:ceb793210bfa803ca31da2e082c552079b1053a25765a245abcbd67380697741

  To start it:   /path/to/diana-do approve sha256:ceb7932... --repo /path/to/repo ...
```

Approving it runs the work and reports:

```console
PROGRESS
  run ed101244-…   state complete
  ✓ item-1   (2 attempts)
  active role: reviewer   attempt 2   closed

RESULT
  COMPLETE
  files changed        1   src/core/calc.py
  attempts             2   (2 stayed inside the approved area)
  who worked           builder → reviewer
  budget               2 of 6 attempts
```

`"don't touch auth"` is not a note in the prompt. It removed `src/auth` from the write scope, and a
run that writes there is stopped by reconciliation.

### The commands

```
diana-do "<goal>"            propose; show Goal / Plan / Authority
diana-do approve <digest>    approve that exact proposal, then run it
diana-do show <digest>       show a proposal again
diana-do status <run-id>     progress, from Diana's own journal
diana-do result <run-id>     result, from Diana's own run report
```

Shared flags: `--repo`, `--proposals-base`, `--executor`. Exit codes: `0` ok, `2` refused, `3` blocked.

`diana-do` lives at the repository root and is **not installed on `PATH`** — the command Diana prints
carries its own absolute path and context so it can be pasted and run from anywhere. Packaging is
[Track D](docs/architecture/DIANA-DISTRIBUTION-ROADMAP.md), and is not done.

---

## What approval means

Approval binds a **proposal digest** — an identity over the exact contract, work items, actor topology
and run budget that approval would create.

- The approval API takes a digest **by type**. `yes`, `do it`, `go ahead` and an approving paraphrase
  cannot become approval, because agreeable text is not the input type.
- Approval **re-derives against the live repository first**. If the repository moved — a new commit, a
  dirty tree, or even a gitignored file — the digest differs and the approval is refused with **no run
  created**. It is never silently rebuilt and run.
- It approves **starting one bounded run**. It is **not** a merge, deploy, or code-review approval, and
  no accumulation of it becomes one.

## What happens on crash or restart

The run journal is the only authoritative record and is written crash-atomically. A killed process
leaves an obligation, not a mystery: the next process must prove no process of that run is still
alive, reconcile what changed on disk, and only then continue — under the same contract, the same run
id, the same remaining budget. Rendering a run after a restart shows the same run, because the view is
a projection of that journal and there is no second copy to disagree with it.

## What Builder and Reviewer mean

Two actors under **one** approved envelope, run strictly one at a time.

- **Builder** may read, write, patch and run the exact approved commands, inside the approved paths.
- **Reviewer** may read and search. Nothing else — no write scope, no commands, no terminal. It is
  read-only *by enforcement*, not by instruction: those tools are refused at the real dispatch
  boundary.
- The reviewer runs **no verification commands** — Diana does. A test command executes repository code
  and could mutate it, which is exactly the authority the role exists to withhold.
- A reviewer verdict is an **input to a Diana decision**, never a state transition. A `PASS` over a
  diff that left the approved area still blocks.

---

## Current, and not yet

| | |
|---|---|
| **Current** | Natural-language goal → bounded proposal → exact approval → Builder/Reviewer run → progress → `COMPLETE` / `FAILED` / `BLOCKED`, on Linux, for one certified workflow class (bounded repair). |
| **Roadmap** | Security evidence certification · mechanical human merge approval · more certified workflow classes · packaging. See the [post-M7 roadmap](docs/architecture/DIANA-POST-M7-ROADMAP.md). |

### Limitations worth knowing before you rely on it

These are real, current, and documented in the specifications rather than softened here.

- **Security findings are not certified.** The Security Gate reports **75/75 `UNPROVEN`** — that is an
  honest "no evidence", not 75 failures, and not proof of anything either.
  ([Track A](docs/architecture/DIANA-CERTIFICATION-ROADMAP.md))
- **Deployment approval is still unaddressed.** Merge approval is now mechanically enforced
  (M5-D20 **discharged** — see below), but there is no deployment gate because there is no
  deployment: zero environments, and Diana holds no deploy authority. **Merge approval must never be
  read as deployment approval.**
  ([Track B](docs/architecture/DIANA-HUMAN-APPROVAL-B5.md))
- **Linux only, in practice.** Process ownership and quiescence need `/proc`; the run lease needs
  `flock`. macOS and Windows are **unproven**, not merely untested.
- **The run lease is cooperative exclusion, not containment.** It decides whether a process may act on
  a run; it constrains nothing about what a process does once admitted.
- **Same-user hostile mutation is outside parts of the threat model.** A process able to unlink and
  recreate the lease file — or rewrite and re-digest the journal — is not defended against.
- **Workflow classes are finite and certified**, not arbitrary. Adding one is a certification, not a
  routing change. ([Track C](docs/architecture/DIANA-WORKFLOW-ROADMAP.md))
- **Live-model behaviour introduces variance.** One acceptance suite's assertion count legitimately
  varies with provider behaviour, and that is recorded rather than smoothed over.
- **Legacy expert commands remain.** Nothing was deleted or retired.

---

## For experts

- **[Architecture](docs/architecture/DIANA-ARCHITECTURE.md)** — the whole system in seven layers.
- **Frozen specifications** — [M1](docs/architecture/HERMES-RUNTIME-M1.md) enforcement boundary ·
  [M2](docs/architecture/HERMES-RUNTIME-M2.md) real LLM turn ·
  [M3](docs/architecture/HERMES-RUNTIME-M3.md) runtime verification ·
  [M4](docs/architecture/HERMES-RUNTIME-M4.md) bounded mutation ·
  [M5](docs/architecture/HERMES-RUNTIME-M5.md) unattended execution ·
  [M6](docs/architecture/HERMES-RUNTIME-M6.md) multi-actor/reviewer ·
  [M7](docs/architecture/HERMES-RUNTIME-M7.md) product UX — plus seven errata in the same directory.
- **[Post-M7 roadmap](docs/architecture/DIANA-POST-M7-ROADMAP.md)** — the four future tracks.
- **Expert surfaces** — the slash commands, `ship.py`, `ao.py` and the CI gates are all still here and
  unchanged; see the operator guide below.

Everything below this line is the **operator guide** for the surrounding pipeline — policy, preflight,
the gates, the AO-based `/diana-ship` workflow, and installation of the portable Diana layer. It
remains accurate and in use.

---

## What Diana is today

Diana on `main` is **two things at once**, and conflating them is the easiest way to misread this
repository.

**1. The governed runtime (M1–M7, accepted).** The natural-language product flow described at the top
of this file: proposal, approval, bounded execution, Builder and Reviewer, durable journal,
reconciliation, recovery. Diana owns the authority and the evidence; Hermes executes. This is the path
`diana-do` drives, and it is the subject of the fourteen frozen documents in `docs/architecture/`.

**2. The surrounding pipeline (pre-M1, still in use).** Policy, Definition of Done, canonical memory,
risk classification, deterministic Preflight, the Diana Gate, the Security Track, and the AO-based
`/diana-ship` workflow. None of it was deleted or retired by M1–M7; `/diana-ship` steps 2–5 are
*superseded for the certified product path only*, and the command still works.

The rest of this section, and everything after it, documents the second.

Current main is a thin control layer that owns:

- provider-neutral engineering policy (`AGENTS.md`)
- Definition of Done discipline
- canonical project memory (`MEMORY.md`, `diana/memory/*`)
- risk classification / governance (`diana/governance/risk-tiers.md`)
- deterministic Preflight (`diana/preflight/preflight.py`)
- deterministic Diana Gate (`diana/gate/diana-gate.py`)
- security evidence normalization and reduction (`diana/security/*`)
- workflow orchestration around external execution systems

Current main intentionally delegates execution and platform ownership to external systems:

- Agent Orchestrator (AO): worker/session/worktree lifecycle
- Playwright MCP: browser automation and browser verification
- GitHub / `gh`: PR, CI, review, and merge state
- Claude / the coding model provider: inference and implementation work

## What Diana is not

Diana is not:

- an LLM
- a model host or router
- a mandatory inference proxy
- a provider SDK
- a worktree manager in its own right
- a browser engine
- a custom merge engine
- a worker-fleet scheduler
- a hosted AgentOps cloud
- a billing platform
- a security scanner implementation
- an autonomous merge bot

Diana is deliberately narrow. It enforces policy, evidence, scope, and human review boundaries; it does not replace the runtime permissions, branch protection, or external tools that actually do the work.

## Core engineering lifecycle

The original Diana lifecycle remains a useful first mental model:

```text
plan → inspect → implement/fix → test → review → ship
```

Current main does not literally implement every stage as a slash command, but the lifecycle still maps cleanly to the current repository:

- `plan` → the `plan-review` skill
- `inspect` → the `research-first` skill
- `implement / fix` → `/fix` plus the `minimal-solution` skill
- `test` → verification discipline embedded in Diana workflows; there is currently no standalone `/test` command
- `review` → `/review`
- `ship` → `/ship`

That is the simple, human-readable engineering loop. It is a good starting model for ordinary work, even though the repository also contains a larger, more structured orchestration layer for AO-based, reviewer-gated, and gate-checked execution.

## Full Diana orchestration and governance pipeline

The larger current Diana pipeline is not the same thing as the simple loop above. It is the attended, governed workflow that runs around the engineering lifecycle:

```text
goal / DoD / risk
    ↓
plan + inspect
    ↓
actor implementation
    ↓
tests / verification
    ↓
independent reviewer
    ↓
verification and scope checks
    ↓
Preflight
    ↓
Diana Gate
    ↓
Security Gate
    ↓
PR
    ↓
human merge
```

In current main, this is the role of `diana/commands/diana-ship.md` and `diana/ship/ship.py`:

- `goal / DoD / risk` are supplied as workflow inputs, not invented by the pipeline
- `plan + inspect` map to the reusable skills and the operator's preparation work
- `actor implementation` is performed by AO-backed actor sessions
- `tests / verification` are the independent checks that prove the change is correct and in scope
- `independent reviewer` is a separate read-only reviewer session, not the actor itself
- `verification and scope checks` include `verify-diff`, reviewer read-only enforcement, and the guardrail checks in `ship.py`
- `Preflight` and `Diana Gate` are deterministic evidence gates
- `Security Gate` is the separate GitHub-run pipeline that evaluates trusted security evidence
- `PR` is opened only after the required evidence is present
- `human merge` remains the protected-branch floor, with no autonomous protected-branch merge in Diana

This distinction matters:

1. The simple loop is the reader's first mental model for how Diana thinks about engineering work.
2. The orchestration pipeline is the current, larger control system for AO-backed, multi-stage, reviewer-guarded, gate-checked execution.

## Repository layout

```text
.
├── AGENTS.md
├── MEMORY.md
├── README.md
├── install.sh
├── verify.sh
├── uninstall.sh
├── diana/
│   ├── CLAUDE.md
│   ├── adapters/
│   │   ├── ao.py
│   │   ├── README.md
│   │   └── fixtures/
│   ├── ci/
│   ├── commands/
│   │   ├── diana-ship.md
│   │   ├── fix.md
│   │   ├── loop-audit.md
│   │   ├── orchestrate.md
│   │   ├── review.md
│   │   └── ship.md
│   ├── gate/
│   │   ├── diana-gate.py
│   │   ├── README.md
│   │   └── test-gate.sh
│   ├── governance/
│   │   └── risk-tiers.md
│   ├── hooks/
│   │   ├── check-careful.sh
│   │   ├── cost-report.md
│   │   ├── hooks.json
│   │   └── README.md
│   ├── mcp/
│   │   └── playwright.json
│   ├── memory/
│   ├── playwright/
│   ├── preflight/
│   ├── security/
│   ├── ship/
│   ├── skills/
│   └── templates/
└── .github/
    ├── CODEOWNERS
    └── workflows/
        ├── diana-gate.yml
        └── diana-security-gate.yml
```

## Canonical policy and memory

The source-of-truth documents for current Diana are:

- `AGENTS.md` — the canonical provider-neutral engineering constitution. This is the top-level policy file that defines the working method, the human merge floor, and the boundary between policy and external execution.
- `MEMORY.md` — current architecture memory and handoff notes. This is the place to read the current rationale for the AO, Playwright, and safety boundaries.
- `diana/CLAUDE.md` — Claude Code-specific integration details only. It does not replace `AGENTS.md`.
- `diana/governance/risk-tiers.md` — the current risk classification model.

If you are trying to understand the current design, start with those files before reading any older docs or blog posts.

## Portable install: what `install.sh` actually does

`install.sh` is the portable layer. It installs Diana into another project by creating or refreshing the project-local `.claude/` assets and by merging Diana-managed sections into the target project's root `AGENTS.md` and `CLAUDE.md`.

What it installs into the target project:

- `.claude/commands/*`
- `.claude/skills/*`
- `.claude/hooks/check-careful.sh`
- `.claude/templates/diana/*`
- `.claude/settings.json` with the portable pre-tool-use hook
- `.claude/settings.local.json` with the optional Stop-hook cost logging entry
- `.mcp.json` with Diana's Playwright MCP entry
- managed, marked sections in `AGENTS.md` and `CLAUDE.md`

Important current behavior:

- It does not create live root-level `LOOP.md`, `STATE.md`, `RUN_LOG.md`, or `BUDGET.md` automatically. Those are project state files, not install-time artifacts.
- It backs up overwritten files using `.bak.<timestamp>` before replacing them.
- It is idempotent and safe to rerun.
- It will not overwrite a non-Diana `playwright` entry in `.mcp.json`.

### Install

```bash
./install.sh /path/to/your/project
```

Or install into the current directory:

```bash
cd /path/to/your/project
/path/to/agent-orchestration/install.sh
```

### Verify

```bash
./verify.sh /path/to/your/project
```

The verifier checks:

- required `.claude/commands`, `.claude/skills`, and templates exist
- `check-careful.sh` is executable
- `.claude/settings.json` includes the portable hook
- `.claude/settings.local.json` includes the optional cost hook
- `.mcp.json` includes the Diana Playwright MCP entry
- the target `AGENTS.md` and `CLAUDE.md` contain the managed Diana sections

### Uninstall

```bash
./uninstall.sh /path/to/your/project
```

The uninstaller removes only Diana-managed files and settings entries. It does not delete unrelated user content.

## Portable Diana versus full Diana

This repository contains the full current Diana implementation on `main`. The `install.sh` script installs only the portable project-facing layer into another repository:

- `.claude/commands/*`
- `.claude/skills/*`
- `.claude/hooks/check-careful.sh`
- `.claude/templates/diana/*`
- `.claude/settings.json` with the portable safety hook
- `.claude/settings.local.json` with the optional Stop-hook cost logging entry
- `.mcp.json` with the Diana Playwright MCP entry
- managed sections inserted into the target repo's `AGENTS.md` and `CLAUDE.md`

The full Diana repository is the place where the policy, adapter, ship pipeline, preflight, gate, security track, and workflows all live together. Installing Diana into another repo does not install the full control center or security track machinery; it installs the portable operating layer that makes a target project behave like a Diana-aware project.

## Current command and skill surface

The repository currently provides the following command/skill assets.

### Core commands

- `diana/commands/fix.md` — inspection + root-cause + minimal patch + verification loop
- `diana/commands/review.md` — read-only review of the current diff
- `diana/commands/ship.md` — final handoff/report for work already completed in-session
- `diana/commands/orchestrate.md` — one iteration of a registered loop, report-only unless the loop explicitly allows more
- `diana/commands/loop-audit.md` — read-only audit of a registered loop's readiness
- `diana/commands/diana-ship.md` — the attended end-to-end pipeline for one or two AO workers plus an independent reviewer

### Skills

- `diana/skills/plan-review.md`
- `diana/skills/research-first.md`
- `diana/skills/minimal-solution.md`
- `diana/skills/loop-design.md`

### Hook assets

- `diana/hooks/check-careful.sh` — conservative destructive-command guardrail
- `diana/hooks/cost-report.md` — local cost/log reporting entry point
- `diana/hooks/hooks.json` — local hook registrations used during install

## How to start AO and open the Agent Orchestrator GUI safely

The AO runtime is an external dependency, not a Diana-owned subsystem. Diana's current contract is implemented by `diana/adapters/ao.py`, and the adapter knows how to:

- run `ao version`
- run `ao doctor --json`
- call `ao spawn ...`
- call `ao session get --json`
- call `ao session kill ...`

Do not bypass the adapter and construct raw `ao` commands elsewhere in Diana.

Current AO state matters here:

- AO binary resolution order is `--ao-bin`, then `DIANA_AO_BIN`, then `PATH`.
- AO home resolution is `--ao-home`, then `DIANA_AO_HOME`, then `~/.ao`.
- The adapter pins AO to version `0.12.10`.
- `spawn` hardcodes `--harness claude-code`.
- AO + Codex autonomous writes are not certified in current main.

For operator use, the safe production pattern is:

1. Confirm AO is present and version-compatible with `python3 diana/adapters/ao.py check`.
2. Use AO's own UI for attended approvals. The adapter can detect that a session needs attention via `ao session get --json`, but it does not resolve approvals itself.
3. If the worker account has no usable display, use the documented loopback-only VNC bridge to the worker-owned private display (`:50`) rather than exposing anything publicly.
4. Never rely on private AO daemon APIs or private SQLite storage for session metadata in Diana code paths.

This repository's current memory files explicitly document the operational constraints around AO's Electron runtime and the fact that `diana-worker` and the operator's own account are separate execution identities.

## Why `anas` and `diana-worker` are separate

The current repository treats the local operator identity (`anas`) and the AO worker identity (`diana-worker`) as separate concerns.

- `anas` is the human/operator environment used for local verification, Git operations, browser checks, and direct interaction with the repository.
- `diana-worker` is the AO worker runtime account. It may have a private display, a restricted environment, or different PATH / system-level prerequisites.
- The AO adapter intentionally works through public `ao` CLI commands only, and it treats AO's private daemon storage as off-limits.
- The known-issues notes document the operational reality that `diana-worker` has no usable display and therefore requires Xvfb or a comparable private virtual display in order to run AO's Electron-based runtime.

The point is not abstraction for its own sake: it is to keep AO execution, UI approval paths, and local operator workflows distinct and auditable.

## One actor, Agent A + Agent B, and reviewer safety

The current `diana/ship/ship.py` pipeline deliberately separates roles:

- Actor A / first worker: the implementation worker that makes the actual repo changes in an AO session.
- Agent B / second worker: a second actor in the bounded Phase 11 multi-worker flow, with disjoint `allowed_files` and a separate worktree.
- Independent reviewer: a fresh, read-only AO Claude session that must never receive write approval.

Important current semantics:

- Agent B is not the independent reviewer. It is an additional actor, and its file scopes must be validated before spawning anything.
- The reviewer is intentionally separate from the actor(s). It receives only the goal, the DoD, the base SHA, the actor commit, and the allowed/prohibited scope.
- Reviewer safety is enforced in two ways:
  - `review-verdict` validates the reviewer's final JSON structure and rejects self-contradictory results.
  - `reviewer-readonly-check` proves the reviewer worktree made no commit and left no uncommitted changes.
- If the reviewer ever enters a waiting-input state, that is treated as a reviewer-safety defect. The current workflow tells you to stop the reviewer session immediately rather than trying to resolve or inspect it.

This is exactly the current safety model for `/diana-ship`: actor(s) may work, but parallel reviewer isolation and read-only enforcement remain separate and mandatory.

## How `/diana-ship` works

`diana/commands/diana-ship.md` is the current attended end-to-end operator workflow for one or two AO workers plus an independent reviewer.

Its current high-level flow is:

1. Validate repository cleanliness and branch state.
2. Read `MEMORY.md`, `AGENTS.md`, `risk-tiers.md`, and relevant memory files before proposing changes.
3. Restate the goal and write an explicit Definition of Done.
4. Classify the task as `SAFE`, `CONSEQUENTIAL`, `DANGEROUS`, or `HUMAN_ONLY`.
5. Run `ship.py precheck`.
6. Spawn exactly one actor through `diana/adapters/ao.py spawn`.
7. Poll the actor session until it reaches a stable state, then verify the resulting diff with `verify-diff`.
8. Spawn a separate read-only reviewer session.
9. Validate the review verdict with `review-verdict` and confirm read-only status with `reviewer-readonly-check`.
10. Optionally run one correction cycle if the reviewer fails.
11. Run `gate` and `open-pr` only after the required evidence is present.

The important practical point is that `/diana-ship` is a deterministic pipeline that composes existing Diana components; it does not replace human judgment, and it never performs an autonomous protected-branch merge.

## Preflight, Diana Gate, and browser verification

The current preflight system is in `diana/preflight/preflight.py`. It is deterministic, applicability-aware, and does not call LLMs or make network requests.

`diana/preflight/reduce_for_gate.py` transforms the richer preflight output into the exact four-field shape that `diana/gate/diana-gate.py` expects.

The main gate is `diana/gate/diana-gate.py`, which evaluates:

- DoD evidence presence
- verification evidence presence
- blocker preflight findings
- diff risk and file scope
- `human_only_conditions`
- sensitive-path escalation (review-sensitive paths and workflows)

Current gate outcomes are:

- `PASS` -> exit `0`
- `FAIL` -> exit `1`
- `REQUIRE_HUMAN` -> exit `2`

Browser verification is separate from the gate: `diana/playwright/browser_applicable.py` provides a deterministic browser-facing signal, and the live `/review` workflow can use Playwright MCP to gather real browser evidence when a diff is browser-facing. Browser applicability is a signal, not a replacement for human judgment.

## Security Track: current honest state

The Security Track under `diana/security/` is optional and separate from the main Diana architecture. It is not a replacement for the core policy layer.

Current main includes:

- `catalog.json` with the 75-control catalog
- `evidence_model.py` for deterministic aggregation of verification runs
- `security_bundle.py` for full bundle construction
- `security_reducer.py` for final `PASS` / `REQUIRE_HUMAN` / `FAIL` reduction
- `ci_verifier_runs.py` as the live-wired extension point for trusted verifier execution
- `coverage_matrix.py` for comparing live resulting states with static capability coverage
- reviewer and dynamic normalizers for semantic-review and dynamic artifact pipelines

Current live-wired Security Gate state is important to understand:

- Semgrep is wired live for a specific set of controls.
- Gitleaks is wired live for secret scanning.
- A deterministic repository/deployment-tree scan is wired live for the relevant control path.
- A local dynamic negative-fetch scenario is wired live for `SEC-064` when a real web root is detected.
- Human-review evidence via GitHub PR review artifacts exists as a capability-only normalizer, but it is not yet wired into `ci_verifier_runs.py` in current main. That is a deliberate scope boundary, not a hidden failure.

Security Gate behavior on GitHub is intentionally split:

- `diana-gate.yml` uses `pull_request`
- `diana-security-gate.yml` uses `pull_request_target`

This separation matters. The Security Gate's workflow definition and evaluator scripts are resolved from the protected base, not from the PR head. The PR head SHA is used only as bound data for the bundle, not as executable code.

## How to interpret Security Gate results

A green Security Gate is a real signal, but it is not a blanket statement that every security control is proven end-to-end.

Current main's honest semantics are:

- `PASS` means the bundle/reducer produced a clean result using the evidence currently available.
- `REQUIRE_HUMAN` means the current evidence is incomplete, unresolved, or review is still needed.
- `FAIL` means a positive failing control result or a structural problem was found.
- Unproven controls stay `UNPROVEN` rather than being silently treated as `PASS`.

A Security Gate result should therefore be read as: "what the currently available trusted evidence proves today," not as a statement that the entire repository is now fully secure.

## What Diana can currently do

Current main can do the following with real confidence:

- start a bounded, governed run from a natural-language goal, with an approval bound to an exact
  proposal, Builder and Reviewer under one envelope, and a durable record that survives a crash
  (`diana-do` — see the top of this file)
- install the portable Diana layer into another project
- provide a canonical engineering policy and reusable Claude Code workflow assets
- run deterministic Preflight and Diana Gate checks locally
- run AO-aware ship workflows through the adapter boundary
- perform scoped verification and reviewer isolation using `ship.py`
- use Playwright MCP for browser-facing verification evidence
- produce and interpret security bundles in the optional Security Track

## What Diana explicitly does not do

Current main explicitly does not:

- certify any security control — the Security Gate reports 75/75 `UNPROVEN`
- gate deployment — there is no deployment, and merge approval is not deploy approval
- claim macOS or Windows support — `/proc` and `flock` are Linux assumptions
- automatically merge protected branches
- certify AO + Codex autonomous writes
- expose a private AO daemon API path as a supported interface
- silently turn `UNPROVEN` security controls into `PASS`
- hide preflight or gate failures behind a weaker risk classification
- replace human review with a self-certifying agent
- assume browser applicability without evidence

## Current limitations and experimental areas

Current main also contains intentionally narrow or capability-only areas:

- Playwright MCP is a prototype integration, not a full browser engine replacement.
- `browser_applicable.py` is a heuristic signal for browser-facing diffs; it is not a full app-detection system.
- GitHub-backed human review evidence exists as a normalizer, but it is not yet wired into the live Security Gate workflow in current main.
- The Safety Track's live verifier execution is explicit and bounded; many controls remain unproven until real evidence is supplied.
- AO's current Electron runtime may require a private virtual display for some environments.

That is the current honest state of the repository and should be treated as the source of truth for operators.

## Troubleshooting

Use this checklist when current main behaves unexpectedly:

- Confirm you are on `main` and not on the experimental `feature/nightshift-capability` branch.
- Re-read `AGENTS.md`, `MEMORY.md`, and `diana/CLAUDE.md` before changing architecture assumptions.
- Confirm AO compatibility with `python3 diana/adapters/ao.py check`.
- If a worker session is waiting for input, surface it to the human and do not treat the session as ready.
- If the reviewer stalls or produces a malformed verdict, stop the reviewer session and treat the run as failed rather than trying to infer intent from an incomplete artifact.
- If a gate fails, inspect the exact gate reasons and the preflight evidence; do not weaken the declared risk or human-only conditions to force a pass.
- If browser verification is required but Playwright is unavailable, check Node version and MCP availability rather than guessing.

## Recommended reading order

If you are returning to this repo after some time, the most useful order is:

1. `AGENTS.md`
2. `MEMORY.md`
3. `diana/CLAUDE.md`
4. `diana/adapters/README.md`
5. `diana/ship/README.md`
6. `diana/preflight/README.md`
7. `diana/gate/README.md`
8. `diana/playwright/README.md`
9. `diana/security/README.md`
10. `diana/commands/diana-ship.md`

That path reflects the actual current design of the repository and is the most reliable way to understand Diana as it exists on current `main`.

## Current operator boundary summary

`diana/adapters/ao.py` is Diana's only AO integration boundary. No other Diana file should construct a raw `ao` command.

Current supported AO surface:

- `check` — verifies AO is present and version-compatible
- `spawn` — spawns a `claude-code` worker session
- `status` — fetches session status via AO's public session CLI
- `stop` — terminates a session

Current AO code details that matter to operators:

- AO binary resolution order is `--ao-bin`, then `DIANA_AO_BIN`, then `PATH`.
- AO home resolution is `--ao-home`, then `DIANA_AO_HOME`, then `~/.ao`.
- The adapter pins AO to version `0.12.10`.
- `spawn` hardcodes `--harness claude-code`.
- AO + Codex autonomous writes are explicitly not certified in this repo's current state.

## Current workflow summary

The deterministic ship pipeline lives in `diana/ship/ship.py` and is documented in `diana/ship/README.md`. It composes the existing Diana components rather than reimplementing them:

- `diana/adapters/ao.py`
- `diana/preflight/preflight.py`
- `diana/preflight/reduce_for_gate.py`
- `diana/gate/diana-gate.py`
- `diana/playwright/browser_applicable.py`
- `gh`

The practical end-to-end workflow for operators is `diana/commands/diana-ship.md`. In current main, `ship.py` validates scope/risk/DoD, runs preflight and the gate, enforces reviewer read-only behavior, and opens a PR with the `DIANA:EVIDENCE` block. It does not itself approve live AO mutations, decide risk, or merge protected branches.

## Current gate and security model

Current main uses:

- deterministic preflight and applicability checks in `diana/preflight/preflight.py`
- a deterministic gate in `diana/gate/diana-gate.py` with outcomes `PASS`, `FAIL`, and `REQUIRE_HUMAN`
- the optional Security Track in `diana/security/` for evidence normalization, reduction, and CI-time verification

Current workflow separation is intentional:

- `.github/workflows/diana-gate.yml` uses `pull_request`
- `.github/workflows/diana-security-gate.yml` uses `pull_request_target`

This means the Security Gate evaluates evidence from the protected base while binding the PR head SHA as data only; it does not execute PR code.

## Practical operator checklist

Before claiming completion, always verify:

- the relevant code paths were inspected
- the root cause was stated clearly
- the smallest safe change was made
- the right tests/checks were run
- the diff obeyed the intended scope
- the gate/preflight outcome was actually observed
- no protected-branch merge happened without human approval
