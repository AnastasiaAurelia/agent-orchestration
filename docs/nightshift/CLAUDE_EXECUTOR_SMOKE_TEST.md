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

Everything below runs as the `nightshift` Linux user (via `sudo -u nightshift
-H`, consistently, so each command gets that user's own `$HOME` rather than
whatever the invoking admin session happens to have set), entirely under
`/home/nightshift/workspace/smoke`, a throwaway project created fresh for
this test and never committed or pushed anywhere. The two steps that
inspect `/home/ubuntu`/ResearchLens (recorded immediately before and
immediately after the one real cycle, further below) are the exception —
those run as your own admin session (`ubuntu`/root), never as `nightshift`,
since the whole point is to check the production tree from outside the
isolated account's own restricted view of it.

## 1. Push this branch, then pull it into the isolated workspace

From your own local worktree (not on the VPS):

```bash
git push origin feature/nightshift-capability
```

Then, as the `nightshift` user on the VPS:

```bash
sudo -u nightshift -H git -C /home/nightshift/workspace/agent-orchestration fetch origin
sudo -u nightshift -H git -C /home/nightshift/workspace/agent-orchestration checkout feature/nightshift-capability
sudo -u nightshift -H git -C /home/nightshift/workspace/agent-orchestration pull --ff-only origin feature/nightshift-capability
```

## 2. Pre-run guards — refuse to proceed over old evidence

This smoke test must start from a clean slate. Run this before creating
anything, and stop if it stops you — do not delete or overwrite previous
smoke evidence just to get past this check; look at what is already there
first and decide on purpose.

```bash
for path in \
  /home/nightshift/workspace/smoke/task-001 \
  /home/nightshift/state/smoke-queue.json \
  /home/nightshift/state/smoke-config.json \
  /home/nightshift/logs/smoke-run-log.jsonl \
  /home/nightshift/reports/smoke-001 \
; do
  if sudo -u nightshift -H test -e "$path"; then
    echo "REFUSING TO PROCEED: $path already exists -- inspect and deliberately clear prior smoke evidence before rerunning this test, do not overwrite it silently." >&2
    exit 1
  fi
done
echo "Pre-run guard passed: no prior smoke evidence found."
```

## 3. Resolve and validate the real Claude executable path

`verify_claude_executable()` in `claude_executor.py` requires the
*canonical* executable path and rejects a symlink entry point outright (a
typical `~/.local/bin/claude` install is often a symlink into an npm/nvm
tree). Resolve it once, here, as the `nightshift` user, and validate it
before it ever goes into a config file:

```bash
CLAUDE_REAL="$(sudo -u nightshift -H readlink -f /home/nightshift/.local/bin/claude)"

if [ -z "$CLAUDE_REAL" ] || [[ "$CLAUDE_REAL" != /* ]]; then
  echo "REFUSING TO PROCEED: could not resolve an absolute canonical path for the Claude executable" >&2
  exit 1
fi
if ! sudo -u nightshift -H test -e "$CLAUDE_REAL"; then
  echo "REFUSING TO PROCEED: $CLAUDE_REAL does not exist" >&2
  exit 1
fi
if ! sudo -u nightshift -H test -x "$CLAUDE_REAL"; then
  echo "REFUSING TO PROCEED: $CLAUDE_REAL is not executable" >&2
  exit 1
fi
echo "Resolved canonical Claude executable: $CLAUDE_REAL"
```

Step 7 below writes `$CLAUDE_REAL` (the resolved canonical path) into
`smoke-config.json` — never the original, possibly-symlinked
`/home/nightshift/.local/bin/claude` path.

## 4. Create the disposable smoke project

```bash
sudo -u nightshift -H mkdir -p /home/nightshift/workspace/smoke/task-001
sudo -u nightshift -H git -C /home/nightshift/workspace/smoke/task-001 init
sudo -u nightshift -H git -C /home/nightshift/workspace/smoke/task-001 config user.email "nightshift-smoke@localhost"
sudo -u nightshift -H git -C /home/nightshift/workspace/smoke/task-001 config user.name "Nightshift Smoke Test"
```

This directory is never committed to the `agent-orchestration` repository
and never pushed anywhere — it is scratch space for the one task below,
and it is fine to delete afterward (it will trip the pre-run guard on any
later rerun until it is deliberately cleared).

## 5. Create the dedicated report directory

```bash
sudo -u nightshift -H mkdir -p /home/nightshift/reports/smoke-001
```

Using a dedicated `smoke-001` subdirectory, rather than the shared
`/home/nightshift/reports` root, keeps this test's report isolated from
any other report history that directory may accumulate — step 12 below
only ever looks inside `smoke-001`, never the broader directory.

## 6. Create the one-task queue

Write `/home/nightshift/state/smoke-queue.json` with exactly one pending
task (as the `nightshift` user, not copy-pasted from an admin shell so the
file ends up owned correctly):

```bash
sudo -u nightshift -H tee /home/nightshift/state/smoke-queue.json > /dev/null <<'EOF'
{
  "tasks": [
    {
      "id": "smoke-001",
      "status": "pending",
      "title": "Create calculator.py with an add(a, b) function that returns a + b, and test_calculator.py with a unittest TestCase covering: positive values (add(2, 3) == 5), negative values (add(-2, -3) == -5), and zero values (add(0, 0) == 0).",
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
a failure here should be looked at, not silently retried. `approved_root`
stays the broader `smoke` directory (not `task-001` itself) since that
field governs the generic queue's own acceptance-command containment check
independently of `claude_executor.py`'s own boundary in step 7 below.

## 7. Write the Claude executor configuration

```bash
sudo -u nightshift -H tee /home/nightshift/state/smoke-config.json > /dev/null <<EOF
{
  "queue_path": "/home/nightshift/state/smoke-queue.json",
  "report_dir": "/home/nightshift/reports/smoke-001",
  "nightshift_root": "/home/nightshift/workspace/smoke",
  "forbidden_paths": ["/home/ubuntu"],
  "claude_executable": "$CLAUDE_REAL",
  "claude_timeout_seconds": 120,
  "run_log_path": "/home/nightshift/logs/smoke-run-log.jsonl"
}
EOF
```

Note the unquoted heredoc delimiter (`<<EOF`, not `<<'EOF'`) — this is
deliberate and required so `$CLAUDE_REAL` from step 3 is substituted by
your own current shell before the content is written; it is still your
shell doing the substitution, not the `nightshift` user's, so this is safe
regardless of which user's shell eventually runs `tee`.

`nightshift_root` is scoped to `/home/nightshift/workspace/smoke`
specifically, not the broader `/home/nightshift/workspace` (which also
contains this runtime's own `agent-orchestration` checkout) — containment
in `claude_executor.py` is enforced against exactly this value, so the
disposable task tree is the *only* thing a Claude invocation launched
under this configuration is allowed to touch.

`claude_timeout_seconds: 120` is a deliberately tight bound for a
three-case calculator smoke objective — raise it only if a real, observed
timeout on this specific task justifies it, not preemptively.

## 8. Confirm tmux is available

```bash
if ! command -v tmux > /dev/null 2>&1; then
  echo "REFUSING TO PROCEED: tmux is not installed. Do not install it as part of this run -- install and verify it deliberately first, then restart from step 8." >&2
  exit 1
fi
```

## 9. Record the ResearchLens metadata-tree hash — before

Run this as your own admin session (`ubuntu`/root) — not as `nightshift`,
and not inside the tmux session opened in step 10:

```bash
sudo find /home/ubuntu/ResearchLens -exec stat --format='%n|%s|%Y|%a|%U|%G' {} + \
  | sort | sha256sum | sudo tee /tmp/researchlens-before.sha256 > /dev/null
```

This hashes each file's name, size, mtime, permission bits, owner, and
group — sorted for a deterministic combined digest regardless of directory
traversal order — into a single line written to
`/tmp/researchlens-before.sha256`.

## 10. Open a supervised tmux session

```bash
sudo -u nightshift -H tmux new-session -s nightshift-smoke-001
```

Stay attached and watch it run — this is a supervised smoke test, not an
unattended one. Do not detach and leave it running unobserved, and do not
configure tmux to auto-restart the command.

## 11. Run exactly one cycle

Inside the tmux session, you are already running as the `nightshift` user
(that is who opened it in step 10), so the commands below do not need a
`sudo -u nightshift` prefix:

```bash
cd /home/nightshift/workspace/agent-orchestration
python3 -m nightshift.runtime.claude_executor run-one --config /home/nightshift/state/smoke-config.json
```

This prints one JSON `CycleResult` and exits. It does not loop, does not
poll for more work, and does not schedule a next run. This is the one and
only point in this entire document where a real Claude Code process is
launched. If it prints `"ran": false`, read `"message"` — that means
preflight, executable integrity, or CLI capability verification rejected
the run *before* claiming the task, exactly as designed; the task itself
was never touched.

## 12. Inspect the evidence

```bash
cat /home/nightshift/state/smoke-queue.json
cat /home/nightshift/logs/smoke-run-log.jsonl
ls /home/nightshift/reports/smoke-001
cat /home/nightshift/reports/smoke-001/*.md
ls -la /home/nightshift/workspace/smoke/task-001
ps -u nightshift -o pid,ppid,stat,etime,comm
```

Confirm: the task transitioned to `done` or `failed`/`requeued` (not stuck
`claimed`); the run log records one executor entry and one acceptance
entry; the report names `smoke-001` with the real outcome; the `ps`
metadata listing shows no lingering `claude`/Python process still running
under the `nightshift` user after the cycle printed its result (a stale
row here, not a hung terminal, is the actual signal to look for —
`etime`/`stat` make a leftover process obvious without needing full
command-line detail).

## 13. Inspect the disposable project, but do not commit or push it

```bash
sudo -u nightshift -H git -C /home/nightshift/workspace/smoke/task-001 status --short
sudo -u nightshift -H sed -n '1,200p' /home/nightshift/workspace/smoke/task-001/calculator.py
sudo -u nightshift -H sed -n '1,240p' /home/nightshift/workspace/smoke/task-001/test_calculator.py
```

Use `git status --short`, not plain `git diff` — this project's git repo
was just initialized in step 4 with nothing committed yet, so
`calculator.py`/`test_calculator.py` are untracked, and untracked files
never show up in an ordinary `git diff` (diff only compares tracked
content against the index/HEAD). `git status --short` lists them (`??
calculator.py`); the `sed` calls above then let you actually read what
Claude wrote, since there is no committed baseline to diff against yet.

Do not run `git add`/`git commit`/`git push` in this directory — it is
scratch space for this smoke test only, never a real deliverable, and
never connected to any remote.

## 14. Record the ResearchLens metadata-tree hash — after, and compare

Run this as your own admin session (`ubuntu`/root), the same as step 9:

```bash
sudo find /home/ubuntu/ResearchLens -exec stat --format='%n|%s|%Y|%a|%U|%G' {} + \
  | sort | sha256sum | sudo tee /tmp/researchlens-after.sha256 > /dev/null

sudo diff -u /tmp/researchlens-before.sha256 /tmp/researchlens-after.sha256
```

An empty diff means the two combined digests are identical: the observed
file names, sizes, mtimes, permission bits, ownership, and group across
the entire ResearchLens tree are unchanged from immediately before the
cycle to immediately after it. Be precise about what this does and does
not prove: it is a metadata-tree comparison, not a cryptographic hash of
every file's actual byte content — it would not, in principle, catch a
content edit that happened to preserve a file's exact size and mtime.
Any non-empty diff output means something in that tree changed and must
be investigated before treating this smoke test as safe, regardless of
what the cycle's own report says.

## 15. Stop

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
- Byte-for-byte content identity of every file under ResearchLens before
  and after the cycle — see step 14's caveat; this test only proves the
  observed metadata tree is unchanged.
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
