# Diana Nightshift

> **Experimental**
>
> **Supervised use only.**
>
> **Unattended scheduling is not yet supported.**

Nightshift is an optional companion to Diana for running one narrowly defined
task through a deterministic controller. It is not a deployment service, a
scheduler, or approval for overnight unattended use.

For the design analysis and exact smoke history, see
[RESEARCH.md](RESEARCH.md) and
[CLAUDE_EXECUTOR_SMOKE_TEST.md](CLAUDE_EXECUTOR_SMOKE_TEST.md).

## Status

| Capability | Current status |
|---|---|
| Deterministic queue | **Implemented and unit-tested.** JSON validation, atomic claim/transition writes, non-blocking file locking, retry caps, and abandoned-claim recovery exist in `nightshift/runtime/queue.py`. |
| Isolated Linux user | **Operator-managed and exercised during VPS smoke attempts.** The repository documents the dedicated `nightshift` account and restrictive permissions, but application code cannot create or prove this OS boundary. |
| Authentication preflight | **Implemented and unit-tested; validated end-to-end on the VPS.** A real attempt initially failed before the combined preflight completed because of a cross-user helper-file permission defect. That defect was fixed, and `smoke-003` recorded a clean, corrected preflight pass (see [Smoke validation history](#smoke-validation-history)). |
| Policy and working-directory validation | **Implemented and unit-tested.** Commands, child environment, executable resolution, and realpath containment are checked in code. These checks are not a filesystem sandbox. |
| One-shot Claude executor | **Implemented and unit-tested; validated end-to-end on the VPS.** A real VPS invocation initially exposed an authentication false-success defect, now fixed. `smoke-003` recorded a clean end-to-end pass (see [Smoke validation history](#smoke-validation-history)). |
| Independent acceptance | **Implemented and unit-tested.** Completion requires a successful executor and a passing external acceptance command. The smoke checker also rejects zero or too few discovered tests. |
| Durable run evidence | **Implemented and unit-tested; failure evidence has been observed on the VPS.** Queue state, JSONL evidence, and deterministic Markdown reports are written outside Claude's working directory. |
| Bounded multi-task runner | **Not implemented.** `run-one` handles at most one task and stops. |
| Scheduler | **Not implemented.** There is no cron, systemd timer, daemon, or polling loop. |
| Automatic push/deploy | **Not implemented and out of scope.** Push, merge, deployment, and production credentials remain human-controlled. |

Most automated tests use fake local executables and temporary directories.
That proves deterministic behavior without spending Claude budget, but it is
not the same as a successful real-Claude VPS smoke test.

## Smoke validation history

### smoke-003 (passed)

- VPS full Nightshift test suite: **205/205 passed**.
- The supervised operator completed **one** supervised cycle and reported:
  `PASS: smoke-003 completed one supervised cycle and every post-run
  verification gate passed.`
- Every structured post-run verification gate passed.
- Evidence (preserved on the VPS; not copied into this repository):
  - Report: `/home/nightshift/reports/smoke-003`
  - Run log: `/home/nightshift/logs/smoke-003-run-log.jsonl`
- `smoke-003` must not be rerun. `smoke-001`, `smoke-002`, and `smoke-003`
  evidence must all be preserved and are treated as immutable.
- **Scope of this result:** it validates one supervised task cycle. It does
  not validate, and must not be described as validating, scheduling or
  unattended overnight execution.
- **Effect on the roadmap:** Milestone 7D (bounded multi-task queue runner)
  is now unblocked. Milestone 7E (scheduler) remains blocked until a
  supervised two-task batch smoke passes.

## Nightshift in one sentence

Nightshift is a deterministic controller that gives a fresh Claude process one
bounded task, independently verifies the result, records evidence, and stops.

## Nightshift vs Project Loop

### Diana Project Loop

- A repository-native workflow for advancing an approved backlog.
- Model-driven inside an active coding session.
- Uses Diana's `LOOP.md`, `STATE.md`, `RUN_LOG.md`, and `BUDGET.md` conventions.
- Does not schedule, deploy, push, merge, or create an external runtime.

### Nightshift

- A deterministic Python runtime/controller separate from Diana's prompt
  workflow.
- Owns the canonical queue, claim lock, attempt counter, retry transition,
  acceptance result, run log, and report.
- Launches a fresh, bounded Claude process for one task.
- Is intended to explore eventual unattended operation, but is not yet
  approved or equipped for unattended scheduling.

Project Loop helps a supervised model follow a disciplined workflow.
Nightshift puts the state transitions and completion decision in code outside
the model. They are complementary and must not be treated as the same system.

## Architecture

```text
queue
  -> preflight
  -> claim one task
  -> fresh Claude process
  -> deterministic acceptance
  -> transition
  -> evidence/report
  -> stop
```

The Claude process receives the bounded objective and a working directory. It
does **not** own the canonical queue, retry count, acceptance verdict, run log,
or report state. `run-one` performs no second claim, automatic retry, polling,
or scheduling.

## Safety model

Nightshift currently combines several boundaries:

- A dedicated `nightshift` Linux user with its own home and restrictive
  filesystem permissions.
- A narrow project-specific `approved_root` and executor
  `nightshift_root`, with symlinks resolved before containment checks.
- Non-empty `forbidden_paths` checked for overlap with the Nightshift root.
- A child environment built from an allowlist containing only `PATH`, `LANG`,
  `LC_ALL`, and `TMPDIR` when present. Credential-shaped variables fail the
  authentication preflight.
- A Claude tool set restricted to `Read`, `Write`, `Edit`, `Glob`, and `Grep`;
  Bash, MCP servers, slash commands, and session persistence are disabled.
- A fresh process for each task, with a wall-clock timeout and process-group
  termination.
- An acceptance command launched separately after Claude exits.
- No API-key fallback, push, merge, deployment, or production credentials.
- No direct use of a production repository as Claude's working directory.

There are two different kinds of isolation:

**Application-level path validation** canonicalizes configured paths and
rejects a working directory outside the approved root or a Nightshift root
that overlaps a configured forbidden path. It bounds configuration mistakes,
but it cannot prevent a process from using an absolute path that the OS user
can access.

**OS-level isolation** uses Linux ownership and permissions so the
`nightshift` user cannot read or modify protected production paths. This is
the load-bearing filesystem boundary. The Python validators do not create it,
and prompt instructions are not a substitute for it. Containers or stronger
per-project OS isolation are not currently implemented.

See [VPS_SETUP.md](VPS_SETUP.md) for the supervised host setup and permission
checks.

## VPS layout

The intended layout is:

```text
/home/nightshift/
├── workspace/
│   ├── agent-orchestration/
│   └── projects/
├── state/
├── logs/
└── reports/
```

`workspace/agent-orchestration` is the controller repository. Target
repositories belong under `workspace/projects/<project-name>` or another
explicitly approved, equally narrow location. Queue/config files belong in
`state`, JSONL execution evidence in `logs`, and generated Markdown reports in
`reports`. Do not use `/home/nightshift/workspace` as a convenient broad
project boundary when a project-specific directory will do.

## Current supported workflow

The only supported real-Claude workflow today is the single supervised smoke
operator. It creates a disposable calculator task, runs exactly one
`run-one` cycle, verifies its evidence, and stops. It does not onboard a real
project.

### Laptop

Run the Nightshift tests before transferring or reviewing a candidate commit:

```bash
cd /path/to/agent-orchestration
python3 -m unittest discover -s nightshift/tests -v
git status --short
```

These tests use fakes and temporary fixtures; they do not invoke real Claude.

### VPS admin shell

Confirm the isolated checkout is present and run the same code tests as the
`nightshift` user:

```bash
test -d /home/nightshift/workspace/agent-orchestration
sudo -u nightshift -H bash -lc '
  cd /home/nightshift/workspace/agent-orchestration &&
  python3 -m unittest discover -s nightshift/tests -v
'
```

Before a new smoke identity, inspect whether the default identity already has
preserved evidence:

```bash
sudo -u nightshift -H test -e /home/nightshift/workspace/smoke/task-002 \
  && echo "smoke-002 evidence exists"
sudo -u nightshift -H test -e /home/nightshift/state/smoke-002-queue.json \
  && echo "smoke-002 evidence exists"
```

Do not delete or overwrite existing evidence.

### `nightshift` user

The operator runs Nightshift-owned operations as this account. For read-only
CLI confirmation without launching Claude:

```bash
cd /home/nightshift/workspace/agent-orchestration
python3 -m nightshift.runtime.claude_executor --help
python3 -m nightshift.runtime.claude_executor run-one --help
```

Do not run `run-one` directly as an exploratory command: it is the work-
producing boundary and requires a reviewed queue/config plus supervision.

### Supervised operator

After reading
[CLAUDE_EXECUTOR_SMOKE_TEST.md](CLAUDE_EXECUTOR_SMOKE_TEST.md), run the
one-command operator from a human-controlled VPS admin shell:

```bash
sudo bash /home/nightshift/workspace/agent-orchestration/scripts/nightshift-smoke-002.sh
```

Stay available to inspect the result. The script uses a bounded detached
`tmux` session so an SSH disconnect does not kill the cycle; that resilience
does not turn the run into approved unattended operation.

If prior `smoke-002` evidence exists, preserve it and deliberately choose a
new identity:

```bash
sudo env NS_SMOKE_ID=003 \
  bash /home/nightshift/workspace/agent-orchestration/scripts/nightshift-smoke-002.sh
```

Never rerun automatically after a failure.

## Task and configuration concepts

The canonical queue is a JSON object with exactly one `tasks` array. Each task
has exactly these fields:

- `id`: unique, non-empty task identifier.
- `title`: the bounded objective passed to Claude.
- `status`: `pending`, `claimed`, `done`, or `failed`.
- `attempt_count`: claims already consumed; incremented at claim time.
- `max_attempts`: fixed positive retry ceiling.
- `claimed_pid` and `claimed_at`: persistent claim owner and timestamp.
- `working_dir`: existing directory where executor and acceptance start.
- `approved_root`: existing root that must contain `working_dir` after
  resolving symlinks. The generic acceptance boundary uses this field.
- `acceptance_command`: non-empty argv array, executed without a shell.
- `timeout_seconds`: acceptance-command timeout.
- `executor_command` and `executor_timeout_seconds`: required generic-executor
  fields. The Claude adapter does not trust or execute `executor_command`; it
  builds its own Claude argv and uses the separate config timeout.

Example task contract only:

```json
{
  "tasks": [
    {
      "id": "example-001",
      "status": "pending",
      "title": "Add the already-approved bounded change and its focused tests.",
      "attempt_count": 0,
      "max_attempts": 1,
      "claimed_pid": null,
      "claimed_at": null,
      "acceptance_command": ["python3", "-m", "unittest", "tests.test_feature"],
      "working_dir": "/home/nightshift/workspace/projects/example-project",
      "timeout_seconds": 120,
      "executor_command": ["true"],
      "executor_timeout_seconds": 1,
      "approved_root": "/home/nightshift/workspace/projects/example-project"
    }
  ]
}
```

This is illustrative JSON, not generated production configuration. A trusted
human must author the objective and acceptance command.

The Claude adapter loads a separate JSON configuration:

- `queue_path`: canonical queue file.
- `report_dir`: destination for dated Markdown reports.
- `nightshift_root`: narrow root used to contain Claude's `working_dir`.
- `forbidden_paths`: non-empty list of existing protected paths that must not
  overlap `nightshift_root`.
- `claude_executable`: absolute, canonical, non-group/world-writable executable
  path.
- `claude_timeout_seconds`: Claude wall-clock timeout; defaults to 300 seconds.
- `run_log_path`: optional JSONL evidence path.
- `lock_path`: optional queue lock path.
- `stale_threshold_seconds`: optional abandoned-claim age; defaults to 300
  seconds.

Example configuration only:

```json
{
  "queue_path": "/home/nightshift/state/example-project-queue.json",
  "report_dir": "/home/nightshift/reports/example-project",
  "nightshift_root": "/home/nightshift/workspace/projects/example-project",
  "forbidden_paths": ["/srv/production-app"],
  "claude_executable": "/absolute/canonical/path/to/claude",
  "claude_timeout_seconds": 300,
  "run_log_path": "/home/nightshift/logs/example-project-run-log.jsonl"
}
```

The forbidden path must be replaced with a real, existing protected path on
the actual host. An empty list fails closed.

## Evidence and result interpretation

For a configured project, inspect the paths named in its config rather than
guessing global defaults:

```bash
python3 -m json.tool /home/nightshift/state/example-project-queue.json
sed -n '1,240p' /home/nightshift/logs/example-project-run-log.jsonl
ls -la /home/nightshift/reports/example-project
sed -n '1,260p' /home/nightshift/reports/example-project/*.md
ps -u nightshift -o pid,ppid,stat,etime,comm
```

In the queue, check `status`, `attempt_count`, `claimed_pid`, and `claimed_at`.
In the JSONL run log, find the latest `task_run_evidence` event for the task,
then the transition event that follows it:

- `executor_outcome` and `executor_exit_code` show whether Claude completed,
  timed out, failed authentication, or exited non-zero.
- `evidence_excerpt` contains a short redacted failure excerpt when available;
  permission or policy denials are also represented by failed outcomes and
  transition details.
- `acceptance_outcome` and `acceptance_exit_code` show what the independent
  checker decided.
- `done`, `requeued`, or `failed_permanently` records the state transition.
- The report summarizes the current queue and run history. It is derived from
  those files; it is not a model-authored verdict.
- The process listing should show no remaining Claude or task process after
  the bounded run finishes.

**Claude exit code 0 alone does not mean the task succeeded.** A task becomes
`done` only when the executor outcome is `completed`, the executor exit code
is exactly `0`, independent acceptance passes, its exit code is `0`, and the
owned transition succeeds. A CLI `run-one` exit code of `0` means the cycle
was operationally processed; the queue and evidence contain the task result.

Preserve raw stdout/stderr files from the supervised operator as protected
evidence. They may contain sensitive command output even though durable JSONL
excerpts are redacted.

## Adding another project

General project onboarding is **planned, not currently supported by an
operator command**. The safe intended pattern is:

```text
clone into /home/nightshift/workspace/projects/<project>
  -> dedicate that project directory as approved_root/nightshift_root
  -> use project-specific queue, log, and report paths
  -> provide no GitHub or deployment credentials
  -> run deterministic build/tests as acceptance
  -> human reviews the diff
  -> human performs any branch push and merge
```

For example, the public portfolio repository
`AnastasiaAurelia/AnastasiaAurelia.github.io` could later be cloned into an
isolated directory such as
`/home/nightshift/workspace/projects/anastasia-portfolio`. Its queue, run log,
and reports would use distinct portfolio-specific paths, and its
`approved_root`/`nightshift_root` would be that project directory—not all of
`/home/nightshift/workspace` and never a live deployment checkout.

Nightshift would run a pre-agreed deterministic build or test command. A human
would inspect the resulting diff and separately decide whether to commit,
push, merge, or deploy. This example describes an onboarding pattern only; it
does not modify or authorize work on the real portfolio.

## What Nightshift must never receive

Do not place any of these in the `nightshift` user's environment, home,
project, queue, config, or prompt:

- GitHub personal access token.
- SSH private key or forwarded SSH agent.
- Sanity write token.
- Cloudflare token.
- Production `.env`.
- Database credentials or connection URLs.
- Deployment secrets of any kind.

Public build variables and private write credentials are different things. A
value intentionally published to browser code may be suitable for a public
build; a token that can mutate content, infrastructure, data, or repository
state is not.

Nightshift deliberately leaves push, merge, and deployment to a human because
those actions cross the boundary from a disposable working copy into shared
external state. Keeping credentials absent makes that boundary enforceable
instead of merely instructional.

## Troubleshooting

Do not automatically rerun a failed attempt. Preserve its queue, config, run
log, report, stdout/stderr captures, working directory, and smoke identity.

### Authentication expired or rejected

**Meaning:** preflight may reject before claim, or the executor may record
`auth_failed`/a non-zero exit if authentication fails after launch.

**Inspect:** the operator output path, config-specific run log, queue, and
report. Do not capture or share raw `claude auth status --json`, which contains
account metadata.

**Action:** preserve evidence, repair authentication manually as the isolated
user, then use a fresh smoke identity only after review.

### Permission mode denies Write or Edit

**Meaning:** the real CLI's `dontAsk` behavior rejected a requested edit. This
is a failed supervised-validation result, not a reason to weaken permissions.

**Inspect:** protected operator stdout/stderr, run-log executor outcome, queue
transition, and unchanged working tree.

**Action:** preserve the evidence and stop. Do not switch to
`bypassPermissions`.

### Acceptance reports zero tests

**Meaning:** a generic test command can exit successfully without finding real
tests. The trusted smoke checker rejects fewer than three structurally
discovered calculator tests.

**Inspect:** `acceptance_outcome`, `acceptance_exit_code`, its logged detail,
and the acceptance checker output.

**Action:** preserve evidence and fix the task/checker contract deliberately;
do not treat executor success as completion.

### Executor exits non-zero

**Meaning:** Claude did not satisfy the completion invariant, even if
acceptance happens to pass.

**Inspect:** `executor_outcome`, `executor_exit_code`, redacted evidence
excerpt, transition event, and report.

**Action:** keep the failed attempt and investigate before authoring another
task or smoke identity.

### Working-directory or isolation rejection

**Meaning:** a path is missing, resolves outside its approved root, overlaps a
forbidden path, or the forbidden-path configuration is incomplete.

**Inspect:** the preflight/config error and resolved paths. Application-level
rejection does not itself prove Linux permissions.

**Action:** correct the explicit narrow paths; do not broaden the root to make
the error disappear.

### Stale smoke evidence already exists

**Meaning:** the requested smoke identity was already used or partially
created.

**Inspect:** its project, queue, config, checker, run log, report, and captured
cycle files.

**Action:** preserve them. After review, choose a new `NS_SMOKE_ID`; never
delete evidence just to pass the guard.

### VPS SSH disconnects while `tmux` continues

**Meaning:** the bounded cycle may still be running in the operator-created
session.

**Inspect:** reconnect, inspect the named `tmux` session and evidence paths,
and wait only within the documented bound. The operator itself checks for a
lingering session/process at completion.

**Action:** do not start a duplicate run.

### Full tests differ between laptop and VPS

**Meaning:** a host-dependent fixture, permission, executable path, or
pre-existing file may differ. Real VPS defects have previously exposed
cross-user and `/tmp` ownership assumptions that local tests missed.

**Inspect:** the exact failing test and preserved fixture/evidence paths on
each host.

**Action:** diagnose the environmental difference. Do not lower the test
count, skip the failure, or rerun the real smoke until the suite is green.

## Current limitations and roadmap

Current boundaries:

- One `run-one` invocation processes at most one task.
- No batch runner, polling worker, scheduler, timer, daemon, or unattended
  overnight mode exists.
- Real corrected end-to-end supervised smoke validation passed on `smoke-003`
  (see [Smoke validation history](#smoke-validation-history)). This proves one
  supervised task cycle, not scheduling or unattended overnight execution.
- The command policy is denylist-based and assumes a trusted task author; it
  is not a sandbox for arbitrary commands.
- Path checks do not replace Linux permissions, containers, namespaces,
  network isolation, or resource limits.
- The report is a deterministic snapshot, not an approval or notification
  system.
- Project onboarding has no supported automation yet.

Planned milestones, without promised timelines:

- Bounded multi-task batch runner (Milestone 7D) — unblocked by the
  `smoke-003` pass.
- Supervised two-task batch smoke.
- Scheduler/timer (Milestone 7E) — remains blocked until a supervised
  two-task batch smoke passes.
- Project onboarding workflow.
- Optional stronger per-project OS isolation.

None of these planned items should be described or operated as implemented.

## Operator checklist

Before:

- [ ] Repository and VPS Nightshift tests are green.
- [ ] Authentication preflight passes without API credentials.
- [ ] Project, state, log, report, approved-root, and forbidden paths are
      explicit and narrow.
- [ ] The evidence/smoke identity is unused.
- [ ] The protected production baseline is recorded where applicable.

After:

- [ ] Executor outcome is `completed` with exit code `0`.
- [ ] Independent acceptance passed with exit code `0`.
- [ ] Expected files exist and the human reviewed their contents/diff.
- [ ] No Claude, task, or `tmux` process remains.
- [ ] Queue transition, JSONL evidence, and report exist and agree.
- [ ] Protected paths and baselines are unchanged.
- [ ] No automatic push, merge, or deployment occurred.
