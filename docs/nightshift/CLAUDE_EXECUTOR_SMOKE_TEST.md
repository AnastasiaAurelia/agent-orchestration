# Supervised Real-Claude Smoke Test (Milestone 7C, corrected by 7C.1, automated by 7C.2, isolated by 7C.2.1, made permission-safe by 7C.2.2)

**Status: not executed.** Nothing in this milestone series runs the command
below, invokes a real Claude Code session, or touches production. There is
no scheduler, no loop, and no automation installed anywhere by this
document — running the command below performs exactly one supervised
cycle and then stops.

## The primary procedure: one command

```bash
sudo bash /home/nightshift/workspace/agent-orchestration/scripts/nightshift-smoke-002.sh
```

Run this on the VPS, as a human with `sudo` access, after reading this
document once. The script (`scripts/nightshift-smoke-002.sh`,
already-tested — see `nightshift/tests/test_nightshift_smoke_script.py`)
replaces the many individual commands the first two smoke runs required
with a single supervised operator that:

1. Verifies nine preconditions and fails closed, printing the exact failed
   gate, if any of them do not hold — including that `smoke-001`'s evidence
   still exists untouched, that no `smoke-002` evidence already exists, that
   the full Nightshift test suite passes, that the configured Claude
   executable resolves to a safe canonical path, that `tmux` is installed,
   and that the deterministic isolation + auth preflight passes.
2. Sets up a fresh, disposable `smoke-002` project, report directory, and
   trusted acceptance checker (a copy of the already-tested
   `nightshift/runtime/smoke_acceptance_checker.py`, installed outside
   Claude's editable working directory).
3. Records a ResearchLens metadata-tree digest immediately before the cycle.
4. Runs **exactly one** bounded `run-one` cycle — the only point anywhere in
   this procedure that invokes the real Claude service — inside a dedicated,
   detached `tmux` session (so an SSH disconnect cannot kill it), and waits
   for it to finish with a finite, bounded timeout (never an unbounded poll).
5. Records the ResearchLens digest again immediately after, and fails hard
   on any difference.
6. Verifies the outcome against the canonical queue and the durable,
   structured run-log evidence — never the terminal output, the generated
   report's prose, or anything Claude itself wrote — checking task status,
   attempt count, executor/acceptance outcome and exit codes, the presence
   of both project files, an independent re-run of the trusted checker, the
   absence of any lingering process or tmux session, and that no
   authentication-failure evidence appears where a clean success is
   expected.
7. Prints `PASS` and exits `0` only if every one of those checks holds.

On any failure, it prints exactly which gate failed, exits non-zero, and
**leaves every `smoke-002` artifact in place for investigation** — it never
deletes, resets, or silently reruns anything, and it never touches
`smoke-001` in any code path, success or failure.

`smoke-001`'s own evidence (its queue, config, run log, report, and
`task-001` project) is preserved exactly as it was by every path through
this script — it is only ever read (to confirm it still exists), never
written to, renamed, or deleted.

Read the script itself (`scripts/nightshift-smoke-002.sh`) for the exact,
literal commands it runs — this document does not duplicate them line by
line for the primary path; see the appendix below only if you need to run
the procedure by hand for troubleshooting.

## Before rerunning: check for preserved evidence from a prior attempt

The first real supervised run of this operator failed at the preflight
gate itself (Milestone 7C.2.2 — see below), leaving `smoke-002` evidence
(whatever it had already created) preserved on the VPS. **Before running
the command above again, inspect whether `smoke-002` evidence already
exists** (the same paths the script itself checks — see the preconditions
above):

```bash
sudo -u nightshift -H test -e /home/nightshift/workspace/smoke/task-002 && echo "smoke-002 evidence exists"
sudo -u nightshift -H test -e /home/nightshift/state/smoke-002-queue.json && echo "smoke-002 evidence exists"
```

- **If `smoke-002` evidence exists:** do not clear or overwrite it — it is
  the failed attempt's own evidence. Run the *next* attempt under a new,
  non-colliding identity instead:

  ```bash
  sudo env NS_SMOKE_ID=003 bash /home/nightshift/workspace/agent-orchestration/scripts/nightshift-smoke-002.sh
  ```

  Every path the script touches (task directory, queue, config, checker,
  run log, reports, tmux session name, and the task's own `id` field) is
  derived from `NS_SMOKE_ID`, so this alone guarantees no collision with
  the preserved `smoke-002` evidence — nothing further needs to change.
  `sudo env VAR=value cmd` (rather than `sudo VAR=value cmd`) is used
  deliberately: it sets the variable inside the already-root-elevated
  process's own exec, so it works regardless of the local sudoers
  `env_reset`/`env_keep` policy.

- **If inspection shows no `smoke-002` evidence was actually created**
  (for example, if the failure happened before the script ever reached
  its setup step): rerunning with the default identity (plain `sudo bash
  .../nightshift-smoke-002.sh`, no `NS_SMOKE_ID` override) is fine. Record
  which case applied and why before proceeding either way — never decide
  silently.

## Milestone 7C.2.2: the preflight permission bug and its fix

The first real run got past Claude login but failed here:

```
python3: can't open file '/tmp/tmp.<random>': [Errno 13] Permission denied
GATE FAILED: deterministic preflight (isolation + auth) did not pass
```

**Root cause:** the operator (running as root) wrote its embedded
verification helper's Python source to a `mktemp` file, then asked
`nightshift` (a different, unprivileged user, via `sudo -u nightshift -H`)
to run `python3 <that file>`. A root-created `mktemp` file is mode `0600`
by default — `nightshift` could not `open()` it by path. This was an
operator handoff bug, not evidence that isolation or authentication
actually failed; the preflight was never actually evaluated.

**Fix:** the helper's source is now fed directly over stdin to `python3 -`,
run as `nightshift` via the same `sudo -u nightshift -H` — never via an
on-disk file. The child process only ever inherits an already-open file
descriptor across `fork`/`exec`; it never calls `open()` on a path itself,
so no file's on-disk permission bits can ever block it. The preflight
Python process runs as `nightshift`, with `HOME=/home/nightshift` (from
`sudo`'s own `-H` flag) and an explicit, minimal `PATH`, from the
repository checkout itself (`cd`'d into before any `nightshift`-targeted
command runs) — never from `/tmp` or another directory `nightshift` merely
happens not to be blocked from entering.

This closes off a real class of bug: nothing else in the script asks
`nightshift` to `open()` a path a different user created, since every
other cross-user handoff already only ever redirected a command's
stdout/stderr into a file (inherited as an already-open descriptor, never
opened by path) rather than passing a path as an argument to be opened.

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
  and after the cycle — the before/after digest is a metadata-tree
  comparison (name, size, mtime, permission bits, owner, group), not a
  cryptographic hash of every file's actual byte content; it would not, in
  principle, catch a content edit that happened to preserve a file's exact
  size and mtime.
- Anything about unattended, scheduled, or multi-cycle operation — that is
  explicitly out of scope until a separate, later, explicitly approved
  milestone. The operator script itself refuses to run a second cycle and
  installs no scheduler.

## What must never appear in any output you keep or share from this test

- The real Claude account's email address, organization ID, or
  subscription identifiers (visible in raw `claude auth status --json`
  output, which this smoke test does not print anywhere — `claude_executor.py`
  only ever records the pass/fail preflight outcome, never the raw
  payload).
- Any credential, token, or `.env` content from ResearchLens or any other
  production service.
- A raw, unredacted access token, even if a future auth failure's captured
  output happened to contain one — the durable run log's
  `task_run_evidence` entries only ever carry a short, token-redacted
  excerpt (see `queue._safe_log_excerpt`/`queue._redact`, Milestone 7C.1),
  and the operator script itself never prints the cycle's raw stdout/stderr
  capture files, only their paths. The full, unredacted capture still lands
  on disk under `/home/nightshift/logs/` (as evidence, protected by the
  same OS-level isolation as everything else there) and should be treated
  with the same care as any other command output that might contain one.

## Appendix: detailed manual procedure (troubleshooting only)

This step-by-step walkthrough is what `scripts/nightshift-smoke-002.sh`
automates. It is kept here only for troubleshooting a failed run or for
understanding exactly what the script does — **it is no longer the default
way to run this smoke test.** Everything below is still `smoke-002`-scoped
and still leaves `smoke-001` untouched; the same cautions from the primary
procedure apply if you ever need to run these commands by hand instead of
through the script.

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

**Before you start:** run `cd /tmp` in your own admin shell (the one you'll
type `sudo -u nightshift ...` from). A prior smoke run produced `Failed to
restore initial working directory: /home/ubuntu: Permission denied` — this
is `bash` complaining, when it starts as the `nightshift` user, that it
inherited a current directory (`/home/ubuntu`) that `nightshift` cannot
stat. It is otherwise harmless (the command still runs correctly), but it
is easy to mistake for a real failure, and it is fully avoidable: it only
happens because the *invoking* admin shell itself was sitting inside
`/home/ubuntu` when it ran `sudo`. `/tmp` is traversable by every account on
the box (mode `1777`) regardless of user, so parking your own shell there
before running anything below avoids the warning entirely — **this changes
nothing about `/home/ubuntu`'s own permissions**, it only moves where your
own shell happens to be sitting. As additional defense, the multi-step
`nightshift` command blocks below also explicitly `cd` into
`/home/nightshift` themselves via `bash -lc`, so each one's own effective
working directory is always somewhere `nightshift` can actually use.

### 1. Push this branch, then pull it into the isolated workspace

From your own local worktree (not on the VPS):

```bash
git push origin feature/nightshift-capability
```

Then, as the `nightshift` user on the VPS:

```bash
sudo -u nightshift -H bash -lc '
  cd /home/nightshift &&
  git -C /home/nightshift/workspace/agent-orchestration fetch origin &&
  git -C /home/nightshift/workspace/agent-orchestration checkout feature/nightshift-capability &&
  git -C /home/nightshift/workspace/agent-orchestration pull --ff-only origin feature/nightshift-capability
'
```

### 2. Pre-run guards — refuse to proceed over old evidence

This smoke test must start from a clean slate — for `smoke-002` specifically;
`smoke-001`'s own evidence is deliberately not part of this check and is
never touched. Run this before creating anything, and stop if it stops
you — do not delete or overwrite previous smoke evidence just to get past
this check; look at what is already there first and decide on purpose.

```bash
for path in \
  /home/nightshift/workspace/smoke/task-002 \
  /home/nightshift/state/smoke-002-queue.json \
  /home/nightshift/state/smoke-002-config.json \
  /home/nightshift/state/smoke-002-acceptance.py \
  /home/nightshift/logs/smoke-002-run-log.jsonl \
  /home/nightshift/reports/smoke-002 \
; do
  if sudo -u nightshift -H test -e "$path"; then
    echo "REFUSING TO PROCEED: $path already exists -- inspect and deliberately clear prior smoke-002 evidence before rerunning this test, do not overwrite it silently." >&2
    exit 1
  fi
done
echo "Pre-run guard passed: no prior smoke-002 evidence found."
```

### 3. Resolve and validate the real Claude executable path

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

Step 8 below writes `$CLAUDE_REAL` (the resolved canonical path) into
`smoke-002-config.json` — never the original, possibly-symlinked
`/home/nightshift/.local/bin/claude` path.

### 4. Create the disposable smoke project

```bash
sudo -u nightshift -H bash -lc '
  cd /home/nightshift &&
  mkdir -p /home/nightshift/workspace/smoke/task-002 &&
  git -C /home/nightshift/workspace/smoke/task-002 init &&
  git -C /home/nightshift/workspace/smoke/task-002 config user.email "nightshift-smoke@localhost" &&
  git -C /home/nightshift/workspace/smoke/task-002 config user.name "Nightshift Smoke Test"
'
```

This directory is never committed to the `agent-orchestration` repository
and never pushed anywhere — it is scratch space for the one task below,
and it is fine to delete afterward (it will trip the pre-run guard on any
later rerun until it is deliberately cleared).

### 5. Create the dedicated report directory

```bash
sudo -u nightshift -H mkdir -p /home/nightshift/reports/smoke-002
```

Using a dedicated `smoke-002` subdirectory, rather than the shared
`/home/nightshift/reports` root (which also still holds `smoke-001`'s own
report, untouched), keeps this test's report isolated — step 13 below only
ever looks inside `smoke-002`, never the broader directory or `smoke-001`'s
own report.

### 6. Install the trusted smoke acceptance checker

The first smoke run's acceptance command was a plain
`python3 -m unittest discover -v`, which exits `0` — "Ran 0 tests / OK" —
even when neither project file was ever created. This is the second,
independent defect Milestone 7C.1 fixed (the first was the completion
invariant itself, which no longer lets *any* passing acceptance result
override a Claude session that didn't actually succeed).

The replacement is `nightshift/runtime/smoke_acceptance_checker.py` — an
already-tested (see `nightshift/tests/test_smoke_acceptance_checker.py`),
stdlib-only script that requires both `calculator.py` and
`test_calculator.py` to exist, requires at least three tests to be
*discovered* (counted structurally, never by parsing "Ran N tests" text),
and only exits `0` if all of them pass. Install it as a copy owned by
`nightshift`, outside the Claude-editable `task-002` directory entirely, so
a Claude session has no way to read or tamper with its own grader:

```bash
sudo -u nightshift -H bash -lc '
  cd /home/nightshift &&
  cp /home/nightshift/workspace/agent-orchestration/nightshift/runtime/smoke_acceptance_checker.py \
     /home/nightshift/state/smoke-002-acceptance.py &&
  chmod 500 /home/nightshift/state/smoke-002-acceptance.py
'
```

`chmod 500` (owner read+execute only, nothing for group or other, not even
owner-write) means even the `nightshift` account itself cannot accidentally
edit this copy once installed — if you need to update the checker, remove
this file deliberately and re-copy it, rather than editing it in place.

### 7. Create the one-task queue

Write `/home/nightshift/state/smoke-002-queue.json` with exactly one
pending task (as the `nightshift` user, not copy-pasted from an admin shell
so the file ends up owned correctly). Its `acceptance_command` invokes the
trusted checker installed in step 6, passing the disposable project
directory as its one argument — never the generic `unittest discover`
command `smoke-001` used:

```bash
sudo -u nightshift -H tee /home/nightshift/state/smoke-002-queue.json > /dev/null <<'EOF'
{
  "tasks": [
    {
      "id": "smoke-002",
      "status": "pending",
      "title": "Create calculator.py with an add(a, b) function that returns a + b, and test_calculator.py with a unittest TestCase covering: positive values (add(2, 3) == 5), negative values (add(-2, -3) == -5), and zero values (add(0, 0) == 0).",
      "attempt_count": 0,
      "max_attempts": 1,
      "claimed_pid": null,
      "claimed_at": null,
      "acceptance_command": ["python3", "/home/nightshift/state/smoke-002-acceptance.py", "/home/nightshift/workspace/smoke/task-002"],
      "working_dir": "/home/nightshift/workspace/smoke/task-002",
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
stays the broader `smoke` directory (not `task-002` itself) since that
field governs the generic queue's own acceptance-command containment check
independently of `claude_executor.py`'s own boundary in step 8 below.

### 8. Write the Claude executor configuration

```bash
sudo -u nightshift -H tee /home/nightshift/state/smoke-002-config.json > /dev/null <<EOF
{
  "queue_path": "/home/nightshift/state/smoke-002-queue.json",
  "report_dir": "/home/nightshift/reports/smoke-002",
  "nightshift_root": "/home/nightshift/workspace/smoke",
  "forbidden_paths": ["/home/ubuntu"],
  "claude_executable": "$CLAUDE_REAL",
  "claude_timeout_seconds": 120,
  "run_log_path": "/home/nightshift/logs/smoke-002-run-log.jsonl"
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

### 9. Confirm tmux is available

```bash
if ! command -v tmux > /dev/null 2>&1; then
  echo "REFUSING TO PROCEED: tmux is not installed. Do not install it as part of this run -- install and verify it deliberately first, then restart from step 9." >&2
  exit 1
fi
```

### 10. Record the ResearchLens metadata-tree hash — before

Run this as your own admin session (`ubuntu`/root) — not as `nightshift`,
and not inside the tmux session opened in step 11:

```bash
sudo find /home/ubuntu/ResearchLens -exec stat --format='%n|%s|%Y|%a|%U|%G' {} + \
  | sort | sha256sum | sudo tee /tmp/nightshift-smoke-002-researchlens-before.sha256 > /dev/null
```

This hashes each file's name, size, mtime, permission bits, owner, and
group — sorted for a deterministic combined digest regardless of directory
traversal order — into a single line written to
`/tmp/nightshift-smoke-002-researchlens-before.sha256`. The name is
specific to this smoke run, not a generic shared path (Milestone 7C.2.1) —
an unrelated, pre-existing root-owned file at a generic name once caused a
`Permission Denied` for the isolated `nightshift` user.

### 11. Open a supervised tmux session

```bash
sudo -u nightshift -H bash -lc 'cd /home/nightshift && tmux new-session -s nightshift-smoke-002'
```

Stay attached and watch it run — this is a supervised smoke test, not an
unattended one. Do not detach and leave it running unobserved, and do not
configure tmux to auto-restart the command.

### 12. Run exactly one cycle

Inside the tmux session, you are already running as the `nightshift` user
(that is who opened it in step 11), so the commands below do not need a
`sudo -u nightshift` prefix:

```bash
cd /home/nightshift/workspace/agent-orchestration
python3 -m nightshift.runtime.claude_executor run-one --config /home/nightshift/state/smoke-002-config.json
```

This prints one JSON `CycleResult` and exits. It does not loop, does not
poll for more work, and does not schedule a next run. This is the one and
only point in this entire document where a real Claude Code process is
launched. If it prints `"ran": false`, read `"message"` — that means
preflight, executable integrity, or CLI capability verification rejected
the run *before* claiming the task, exactly as designed; the task itself
was never touched.

### 13. Inspect the evidence

```bash
cat /home/nightshift/state/smoke-002-queue.json
cat /home/nightshift/logs/smoke-002-run-log.jsonl
ls /home/nightshift/reports/smoke-002
cat /home/nightshift/reports/smoke-002/*.md
ls -la /home/nightshift/workspace/smoke/task-002
ps -u nightshift -o pid,ppid,stat,etime,comm
```

Confirm: the task transitioned to `done` or `failed`/`requeued` (not stuck
`claimed`); the run log contains a `task_run_evidence` entry (executor
outcome/exit code, acceptance outcome/exit code, and — only if the executor
did not cleanly succeed — a short redacted excerpt) recorded *before* the
final transition event, and that entry alone is enough to tell whether this
was a genuine success, an authentication failure, a timeout, or an
acceptance rejection, without needing the terminal `CycleResult` output;
the report names `smoke-002` with the real outcome and must not claim
`Done: 1` unless the checker in step 6 genuinely saw three passing tests;
the `ps` metadata listing shows no lingering `claude`/Python process still
running under the `nightshift` user after the cycle printed its result (a
stale row here, not a hung terminal, is the actual signal to look for —
`etime`/`stat` make a leftover process obvious without needing full
command-line detail).

### 14. Inspect the disposable project, but do not commit or push it

```bash
sudo -u nightshift -H git -C /home/nightshift/workspace/smoke/task-002 status --short
sudo -u nightshift -H sed -n '1,200p' /home/nightshift/workspace/smoke/task-002/calculator.py
sudo -u nightshift -H sed -n '1,240p' /home/nightshift/workspace/smoke/task-002/test_calculator.py
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

### 15. Record the ResearchLens metadata-tree hash — after, and compare

Run this as your own admin session (`ubuntu`/root), the same as step 10:

```bash
sudo find /home/ubuntu/ResearchLens -exec stat --format='%n|%s|%Y|%a|%U|%G' {} + \
  | sort | sha256sum | sudo tee /tmp/nightshift-smoke-002-researchlens-after.sha256 > /dev/null

sudo diff -u /tmp/nightshift-smoke-002-researchlens-before.sha256 /tmp/nightshift-smoke-002-researchlens-after.sha256
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

### 16. Stop

```bash
exit
```

Detach/close the tmux session and stop. Do not run a second cycle, do not
add a scheduler, cron entry, or systemd timer, and do not point this
configuration at a real project. This document proves one bounded,
supervised cycle works end-to-end — nothing here authorizes unattended or
repeated execution.
