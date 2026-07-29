# Supervised Real-Claude Smoke Test (Milestone 7C)

**Status: not executed.** Every command in this document is for a human to
run manually, later, on the actual VPS, after reading and understanding
what it does. Nothing in this milestone runs any of these commands, invokes
a real Claude Code session, or touches production. There is no scheduler,
no loop, and no automation implied anywhere in this document.

This is the supervised, one-shot proof that `nightshift/runtime/claude_executor.py`
can carry a real Claude Code process through exactly one bounded cycle on
the isolated `nightshift` account set up in Milestone 7B1/7B2. Read that
account's isolation contract (`docs/nightshift/VPS_SETUP.md`) first — this
document assumes the `nightshift` user, workspace, and permissions already
exist and have already been verified.

Everything below runs as the `nightshift` Linux user, entirely under
`/home/nightshift/workspace/smoke`, a throwaway project created fresh for
this test and never committed or pushed anywhere. Nothing here reads,
lists, or writes any path under `/home/ubuntu` or any ResearchLens path —
step 9 is how you prove that afterward, not something to assume.

## 1. Push this branch, then pull it into the isolated workspace

From your own local worktree (not on the VPS):

```bash
git push origin feature/nightshift-capability
```

Then, as the `nightshift` user on the VPS:

```bash
sudo -u nightshift git -C /home/nightshift/workspace/agent-orchestration fetch origin
sudo -u nightshift git -C /home/nightshift/workspace/agent-orchestration checkout feature/nightshift-capability
sudo -u nightshift git -C /home/nightshift/workspace/agent-orchestration pull --ff-only origin feature/nightshift-capability
```

## 2. Create the disposable smoke project

```bash
sudo -u nightshift mkdir -p /home/nightshift/workspace/smoke/task-001
sudo -u nightshift git -C /home/nightshift/workspace/smoke/task-001 init
sudo -u nightshift git -C /home/nightshift/workspace/smoke/task-001 config user.email "nightshift-smoke@localhost"
sudo -u nightshift git -C /home/nightshift/workspace/smoke/task-001 config user.name "Nightshift Smoke Test"
```

This directory is never committed to the `agent-orchestration` repository
and never pushed anywhere — it is scratch space for the one task below,
and it is fine to delete afterward.

## 3. Create the one-task queue

Write `/home/nightshift/state/smoke-queue.json` with exactly one pending
task (as the `nightshift` user, not copy-pasted from an admin shell so the
file ends up owned correctly):

```bash
sudo -u nightshift tee /home/nightshift/state/smoke-queue.json > /dev/null <<'EOF'
{
  "tasks": [
    {
      "id": "smoke-001",
      "status": "pending",
      "title": "Create calculator.py with an add(a, b) function that returns a + b, and test_calculator.py with a unittest TestCase that imports it and asserts add(2, 3) == 5.",
      "attempt_count": 0,
      "max_attempts": 1,
      "claimed_pid": null,
      "claimed_at": null,
      "acceptance_command": ["python3", "-m", "unittest", "discover", "-v"],
      "working_dir": "/home/nightshift/workspace/smoke/task-001",
      "timeout_seconds": 60,
      "executor_command": ["true"],
      "executor_timeout_seconds": 1,
      "approved_root": "/home/nightshift/workspace/smoke"
    }
  ]
}
EOF
```

`executor_command`/`executor_timeout_seconds` are vestigial, schema-required
fields from the generic executor (Milestone 4A) — `claude_executor.py` never
reads them; it builds the real Claude invocation entirely from `title` and
`working_dir` plus its own trusted `ClaudeConfig` (see that module's
docstring). `max_attempts: 1` keeps this a genuinely one-shot smoke test —
a failure here should be looked at, not silently retried.

## 4. Write the Claude executor configuration

```bash
sudo -u nightshift tee /home/nightshift/state/smoke-config.json > /dev/null <<'EOF'
{
  "queue_path": "/home/nightshift/state/smoke-queue.json",
  "report_dir": "/home/nightshift/reports",
  "nightshift_root": "/home/nightshift/workspace",
  "forbidden_paths": ["/home/ubuntu"],
  "claude_executable": "/home/nightshift/.local/bin/claude",
  "claude_timeout_seconds": 120,
  "run_log_path": "/home/nightshift/logs/smoke-run-log.jsonl"
}
EOF
```

`claude_timeout_seconds: 120` is a deliberately tight bound for a two-file
smoke objective — raise it only if a real, observed timeout on this
specific task justifies it, not preemptively.

## 5. Open a supervised tmux session

```bash
sudo -u nightshift tmux new-session -s nightshift-smoke-001
```

Stay attached and watch it run — this is a supervised smoke test, not an
unattended one. Do not detach and leave it running unobserved, and do not
configure tmux to auto-restart the command.

## 6. Run exactly one cycle

Inside the tmux session, as the `nightshift` user:

```bash
cd /home/nightshift/workspace/agent-orchestration
python3 -m nightshift.runtime.claude_executor run-one --config /home/nightshift/state/smoke-config.json
```

This prints one JSON `CycleResult` and exits. It does not loop, does not
poll for more work, and does not schedule a next run. If it prints
`"ran": false`, read `"message"` — that means preflight, executable
integrity, or CLI capability verification rejected the run *before*
claiming the task, exactly as designed; the task itself was never touched.

## 7. Inspect the evidence

```bash
cat /home/nightshift/state/smoke-queue.json
cat /home/nightshift/logs/smoke-run-log.jsonl
ls /home/nightshift/reports
cat /home/nightshift/reports/*.md
ls -la /home/nightshift/workspace/smoke/task-001
ps -u nightshift -f
```

Confirm: the task transitioned to `done` or `failed`/`requeued` (not stuck
`claimed`); the run log records one executor entry and one acceptance
entry; the report names `smoke-001` with the real outcome; `ps` shows no
lingering `claude` process still running under the `nightshift` user after
the cycle printed its result.

## 8. Inspect the disposable project, but do not commit or push it

```bash
sudo -u nightshift git -C /home/nightshift/workspace/smoke/task-001 status
sudo -u nightshift git -C /home/nightshift/workspace/smoke/task-001 diff
```

Look at `calculator.py`/`test_calculator.py` if they were created. Do not
run `git add`/`git commit`/`git push` in this directory — it is scratch
space for this smoke test only, never a real deliverable, and never
connected to any remote.

## 9. Prove nothing under `/home/ubuntu` or ResearchLens changed

```bash
sudo find /home/ubuntu -newer /home/nightshift/state/smoke-config.json 2>&1
```

An empty result (or only `Permission denied` lines, if `nightshift` cannot
even traverse `/home/ubuntu` — which is the isolation contract working as
intended) is the expected outcome. Any real file path printed here means
something under `/home/ubuntu` changed during this test and must be
investigated before treating the smoke test as safe, regardless of what
the cycle's own report says.

## 10. Stop

```bash
exit
```

Detach/close the tmux session and stop. Do not run a second cycle, do not
add a scheduler, cron entry, or systemd timer, and do not point this
configuration at a real project. This document proves one bounded,
supervised cycle works end-to-end — nothing here authorizes unattended or
repeated execution.

## What this smoke test does not prove

- That `--permission-mode dontAsk`'s exact runtime behavior matches its
  name under every prompt shape — this is the first real invocation that
  observes it directly; read the actual transcript/evidence rather than
  assuming.
- That Claude Code's own file-editing tools cannot be induced to write
  outside `working_dir` — the load-bearing protection against that remains
  the OS-level `nightshift` user permissions (Milestone 7B1/7B2), not
  anything this smoke test observes.
- Anything about unattended, scheduled, or multi-cycle operation — that is
  explicitly out of scope until a separate, later, explicitly approved
  milestone.

## What must never appear in any output you keep or share from this test

- The real Claude account's email address, organization ID, or
  subscription identifiers (visible in raw `claude auth status --json`
  output, which this smoke test does not print anywhere — `claude_executor.py`
  only ever records the pass/fail preflight outcome, never the raw
  payload).
- Any credential, token, or `.env` content from ResearchLens or any other
  production service.
