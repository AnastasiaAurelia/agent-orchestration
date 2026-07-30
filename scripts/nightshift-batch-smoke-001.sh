#!/usr/bin/env bash
#
# One-command supervised Nightshift batch-smoke-001 operator (Milestone 7D).
#
# Intended invocation on the VPS, run exactly once, only after this branch
# has been reviewed, pushed, pulled, and the full test suite has been
# retested on the VPS:
#
#   sudo bash /home/nightshift/workspace/agent-orchestration/scripts/nightshift-batch-smoke-001.sh
#
# Verifies the bounded multi-task batch runner (nightshift.runtime.
# claude_executor.run_batch / `run-batch` CLI subcommand) end to end with
# two independent, real Claude cycles processed by a single `run-batch`
# invocation -- never a scheduler, never a loop, never rerun automatically.
# Follows the exact same supervised, fail-closed, evidence-preserving shape
# as scripts/nightshift-smoke-002.sh (which validates the single-task
# `run-one` path); see that script and
# docs/nightshift/CLAUDE_EXECUTOR_SMOKE_TEST.md for the shared narrative.
#
# smoke-001/002/003 evidence (and any other prior smoke-* evidence) is never
# read for anything other than an unrelated, pre-existing fact on disk, and
# is never written to, renamed, or deleted by this script.
#
# Testability: every path this script touches is overridable via NS_*
# environment variables (defaults match the real VPS layout below), and
# NS_TEST_MODE=1 replaces every `sudo -u nightshift -H` with direct
# execution as the invoking user, matching nightshift-smoke-002.sh's own
# convention.
#
# NS_RESEARCHLENS_BEFORE_DIGEST and NS_RESEARCHLENS_AFTER_DIGEST are
# test-only overrides for the two ResearchLens digest artifact paths --
# honored only when NS_TEST_MODE=1; set outside test mode, the script fails
# closed immediately rather than silently using or ignoring them (same
# rationale as nightshift-smoke-002.sh).
#
# THIS SCRIPT IS NOT EXECUTED AS PART OF THIS MILESTONE'S IMPLEMENTATION --
# only `bash -n` (a syntax check) is run against it. A human operator runs
# it for real, exactly once, later.

set -Eeuo pipefail

cd /tmp

# ---------------------------------------------------------------------------
# Configuration (overridable for tests; defaults match the real VPS layout)
# ---------------------------------------------------------------------------

NS_TEST_MODE="${NS_TEST_MODE:-0}"

for _ns_test_only_var in NS_RESEARCHLENS_BEFORE_DIGEST NS_RESEARCHLENS_AFTER_DIGEST; do
  if [ "$NS_TEST_MODE" != "1" ] && [ -n "${!_ns_test_only_var:-}" ]; then
    echo "GATE FAILED: $_ns_test_only_var is a test-only override and must not be set outside NS_TEST_MODE=1" >&2
    exit 1
  fi
done
unset _ns_test_only_var

NIGHTSHIFT_USER="${NS_NIGHTSHIFT_USER:-nightshift}"

# Which batch-smoke attempt this run is. Defaults to "001" (this script's
# own identity) but a human operator who has confirmed batch-smoke-001
# evidence already exists from a prior failed attempt can run the *next*
# attempt under an entirely new, non-colliding identity (e.g.
# NS_BATCH_SMOKE_ID=002) without ever touching, clearing, or reusing the
# preserved batch-smoke-001 evidence. Every path below is derived from this
# one value.
BATCH_SMOKE_ID="${NS_BATCH_SMOKE_ID:-001}"

NIGHTSHIFT_HOME="${NS_NIGHTSHIFT_HOME:-/home/nightshift}"
REPO_ROOT="${NS_REPO_ROOT:-$NIGHTSHIFT_HOME/workspace/agent-orchestration}"
SMOKE_ROOT="${NS_SMOKE_ROOT:-$NIGHTSHIFT_HOME/workspace/smoke}"
TASK_DIR="$SMOKE_ROOT/batch-smoke-$BATCH_SMOKE_ID"
TASK1_DIR="$TASK_DIR/task-1"
TASK2_DIR="$TASK_DIR/task-2"
STATE_DIR="${NS_STATE_DIR:-$NIGHTSHIFT_HOME/state}"
LOGS_DIR="${NS_LOGS_DIR:-$NIGHTSHIFT_HOME/logs}"
REPORTS_DIR="${NS_REPORTS_DIR:-$NIGHTSHIFT_HOME/reports}"

QUEUE_PATH="$STATE_DIR/batch-smoke-$BATCH_SMOKE_ID-queue.json"
CONFIG_PATH="$STATE_DIR/batch-smoke-$BATCH_SMOKE_ID-config.json"
CHECKER1_PATH="$STATE_DIR/batch-smoke-$BATCH_SMOKE_ID-acceptance-1.py"
CHECKER2_PATH="$STATE_DIR/batch-smoke-$BATCH_SMOKE_ID-acceptance-2.py"
RUN_LOG_PATH="$LOGS_DIR/batch-smoke-$BATCH_SMOKE_ID-run-log.jsonl"
REPORT_DIR="$REPORTS_DIR/batch-smoke-$BATCH_SMOKE_ID"
CYCLE_STDOUT="$LOGS_DIR/batch-smoke-$BATCH_SMOKE_ID-cycle.stdout.log"
CYCLE_STDERR="$LOGS_DIR/batch-smoke-$BATCH_SMOKE_ID-cycle.stderr.log"
CYCLE_EXITCODE="$LOGS_DIR/batch-smoke-$BATCH_SMOKE_ID-cycle.exitcode"

TMUX_SESSION="${NS_TMUX_SESSION:-nightshift-batch-smoke-$BATCH_SMOKE_ID}"

FORBIDDEN_PATH="${NS_FORBIDDEN_PATH:-/home/ubuntu}"
RESEARCHLENS_PATH="${NS_RESEARCHLENS_PATH:-/home/ubuntu/ResearchLens}"
CLAUDE_CONFIGURED_PATH="${NS_CLAUDE_CONFIGURED_PATH:-$NIGHTSHIFT_HOME/.local/bin/claude}"

RESEARCHLENS_BEFORE_DIGEST="${NS_RESEARCHLENS_BEFORE_DIGEST:-/tmp/nightshift-batch-smoke-$BATCH_SMOKE_ID-researchlens-before.sha256}"
RESEARCHLENS_AFTER_DIGEST="${NS_RESEARCHLENS_AFTER_DIGEST:-/tmp/nightshift-batch-smoke-$BATCH_SMOKE_ID-researchlens-after.sha256}"

MAX_WAIT_SECONDS="${NS_MAX_WAIT_SECONDS:-1200}"
POLL_INTERVAL_SECONDS="${NS_POLL_INTERVAL_SECONDS:-5}"
CLAUDE_TIMEOUT_SECONDS="${NS_CLAUDE_TIMEOUT_SECONDS:-120}"
MIN_TEST_COUNT="${NS_MIN_TEST_COUNT:-205}"

# Batch limits for the one `run-batch` invocation this script performs.
# max-tasks=3 (with exactly two pending tasks in the queue) so the runner
# processes both, then observes the queue empty and stops on its own --
# never because a limit was hit. Runtime/consecutive-failure limits are
# left at run-batch's own production defaults (never overridden here),
# since the point of this smoke test is to validate the real default
# configuration, not a permissive test-only one.
BATCH_MAX_TASKS="${NS_BATCH_MAX_TASKS:-3}"

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

fail() {
  echo "GATE FAILED: $1" >&2
  exit 1
}

run_as_nightshift() {
  if [ "$NS_TEST_MODE" = "1" ]; then
    "$@"
  else
    sudo -u "$NIGHTSHIFT_USER" -H "$@"
  fi
}

run_helper() {
  if [ "$NS_TEST_MODE" = "1" ]; then
    env PYTHONPATH="$REPO_ROOT" PATH="/usr/bin:/bin" python3 - "$@" <<< "$HELPER_PY_SOURCE"
  else
    sudo -u "$NIGHTSHIFT_USER" -H env PYTHONPATH="$REPO_ROOT" PATH="/usr/bin:/bin" python3 - "$@" \
      <<< "$HELPER_PY_SOURCE"
  fi
}

SCRATCH_FILES=()
register_scratch() { SCRATCH_FILES+=("$1"); }

cleanup() {
  local ec=$?
  for f in ${SCRATCH_FILES[@]+"${SCRATCH_FILES[@]}"}; do
    [ -n "$f" ] && rm -f "$f" 2>/dev/null || true
  done
  if [ "$ec" -ne 0 ]; then
    echo "nightshift-batch-smoke-001.sh (attempt batch-smoke-$BATCH_SMOKE_ID) exited with status $ec." >&2
    echo "No batch-smoke-$BATCH_SMOKE_ID evidence was deleted or reset -- everything under" >&2
    echo "$STATE_DIR, $LOGS_DIR, $REPORTS_DIR, and $TASK_DIR has been left" >&2
    echo "in place for investigation. No other smoke-*/batch-smoke-* evidence was touched." >&2
  fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Embedded structured-verification helper (stdlib only, never invokes Claude)
# ---------------------------------------------------------------------------

HELPER_PY_SOURCE="$(cat <<'PYEOF'
"""Structured, non-interactive helper for the Nightshift batch-smoke operator.

Three subcommands, all stdlib-only and read-only:

  preflight <nightshift_root> <forbidden_path> <claude_executable>
      Runs auth_preflight.preflight_gate() (isolation + non-work-producing
      `claude auth status --json`), then verify_cli_capabilities() against
      the same executable. Exits 0 only if both pass. Prints only the
      gate's own already-redacted to_json_dict() plus a CLI-capability
      summary -- never a raw auth-status payload, token, email, or org id.

  verify_batch <queue_path> <run_log_path> <task1_id> <task1_dir> <task2_id> <task2_dir>
      Checks the canonical queue and durable run log -- never the terminal
      BatchResult printed by run-batch, never model-authored text -- for
      the specific set of facts Milestone 7D requires before a human may
      treat a two-task batch cycle as a genuine success. Exits 0 and
      prints VERIFY_OK only if every check passes for *both* tasks;
      otherwise prints VERIFY_FAILED: <reason> to stderr and exits 1.
"""
import json
import os
import sys


def cmd_preflight(argv):
    from nightshift.runtime import auth_preflight
    from nightshift.runtime import claude_executor as ce

    nightshift_root, forbidden_path, claude_executable = argv
    gate = auth_preflight.preflight_gate(
        nightshift_root,
        [forbidden_path],
        claude_command=[claude_executable, "auth", "status", "--json"],
    )
    result = {"gate": gate.to_json_dict()}
    if not gate.passed:
        print(json.dumps(result))
        return 1

    cap_ok, missing, cap_error = ce.verify_cli_capabilities(claude_executable)
    result["cli_capability_ok"] = cap_ok
    result["cli_capability_missing"] = missing
    result["cli_capability_error"] = cap_error
    print(json.dumps(result))
    return 0 if cap_ok else 1


def _verify_one_task(events, queue_tasks, task_id, task_dir):
    task = next((t for t in queue_tasks if t["id"] == task_id), None)
    if task is None:
        return f"no such task in queue: {task_id!r}"
    if task["status"] != "done":
        return f"queue task {task_id!r} status is {task['status']!r}, not 'done'"
    if task["attempt_count"] != 1:
        return f"queue task {task_id!r} attempt_count is {task['attempt_count']!r}, not 1"

    evidence_events = [
        e for e in events if e.get("event") == "task_run_evidence" and e.get("task_id") == task_id
    ]
    if not evidence_events:
        return f"no task_run_evidence entry found in the durable run log for {task_id!r}"
    evidence = evidence_events[-1]

    # A nonempty permission_denials list always forces executor_outcome to
    # "permission_denied" (Milestone 7C.2.3's Completion Invariant fix,
    # unchanged and reused here) -- so "completed" alone already proves no
    # tool call was denied for this task, with no separate raw field to
    # check.
    if evidence.get("executor_outcome") != "completed":
        return (
            f"{task_id!r} executor_outcome is {evidence.get('executor_outcome')!r}, "
            "not 'completed' (this also covers a nonempty permission_denials list, "
            "which always forces this outcome away from 'completed')"
        )
    if evidence.get("executor_exit_code") != 0:
        return f"{task_id!r} executor_exit_code is {evidence.get('executor_exit_code')!r}, not 0"
    if evidence.get("acceptance_outcome") != "passed":
        return f"{task_id!r} acceptance_outcome is {evidence.get('acceptance_outcome')!r}, not 'passed'"
    if evidence.get("acceptance_exit_code") != 0:
        return f"{task_id!r} acceptance_exit_code is {evidence.get('acceptance_exit_code')!r}, not 0"

    done_events = [e for e in events if e.get("event") == "done" and e.get("task_id") == task_id]
    if not done_events:
        return f"no 'done' transition event found in the durable run log for {task_id!r}"

    if not os.path.isfile(os.path.join(task_dir, "calculator.py")):
        return f"calculator.py does not exist in the disposable project for {task_id!r}"
    if not os.path.isfile(os.path.join(task_dir, "test_calculator.py")):
        return f"test_calculator.py does not exist in the disposable project for {task_id!r}"

    suspect_terms = ("401", "auth_failed", "authentication failure")
    for event in events:
        if event.get("task_id") != task_id:
            continue
        haystack = " ".join(
            str(event.get(field, "")) for field in ("executor_outcome", "detail", "evidence_excerpt")
        ).lower()
        for term in suspect_terms:
            if term.lower() in haystack:
                return (
                    f"unexpected authentication-failure evidence found in the run log "
                    f"for {task_id!r}, which otherwise looks successful: {term!r}"
                )
    return None


def cmd_verify_batch(argv):
    from nightshift.runtime import queue as nsq

    queue_path, run_log_path, task1_id, task1_dir, task2_id, task2_dir = argv

    def verify_fail(reason):
        print(f"VERIFY_FAILED: {reason}", file=sys.stderr)
        return 1

    try:
        with open(queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
    except Exception as exc:  # noqa: BLE001 -- surfaced as a plain gate failure
        return verify_fail(f"could not read/validate queue: {exc}")

    events, malformed = nsq._load_run_log_events(run_log_path)
    if malformed:
        return verify_fail(f"{malformed} malformed run-log line(s)")

    for task_id, task_dir in ((task1_id, task1_dir), (task2_id, task2_dir)):
        reason = _verify_one_task(events, data["tasks"], task_id, task_dir)
        if reason is not None:
            return verify_fail(reason)

    # Two distinct executor cycles: one task_run_evidence event per task id,
    # each carrying its own independent evidence -- already implied by the
    # per-task checks above succeeding for two different task ids, but
    # confirmed explicitly here as its own check.
    evidence_task_ids = {
        e.get("task_id") for e in events if e.get("event") == "task_run_evidence"
    }
    if not {task1_id, task2_id}.issubset(evidence_task_ids):
        return verify_fail("did not find two distinct task_run_evidence entries, one per task")

    print("VERIFY_OK")
    return 0


def main(argv):
    if not argv:
        print("usage: helper.py <preflight|verify_batch> ...", file=sys.stderr)
        return 2
    command, rest = argv[0], argv[1:]
    if command == "preflight":
        return cmd_preflight(rest)
    if command == "verify_batch":
        return cmd_verify_batch(rest)
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
PYEOF
)"

# ---------------------------------------------------------------------------
# Cheap preconditions first (fail fast before the expensive test-suite run)
# ---------------------------------------------------------------------------

[ "$(uname -s)" = "Linux" ] || fail "not running on Linux"

if [ "$NS_TEST_MODE" != "1" ]; then
  [ "${EUID:-$(id -u)}" -eq 0 ] || fail "must be run via sudo (effective UID is not 0)"
fi

[ -d "$REPO_ROOT" ] || fail "repository not found at $REPO_ROOT"
[ -f "$REPO_ROOT/nightshift/runtime/claude_executor.py" ] \
  || fail "$REPO_ROOT does not look like the agent-orchestration checkout"

cd "$REPO_ROOT"

# -- Refuse over old evidence for this attempt (batch-smoke-$BATCH_SMOKE_ID);
# never auto-clean. Choosing a different NS_BATCH_SMOKE_ID is how a human
# deliberately starts a fresh attempt without touching a prior one's
# preserved evidence.
for path in \
  "$TASK_DIR" \
  "$QUEUE_PATH" \
  "$CONFIG_PATH" \
  "$CHECKER1_PATH" \
  "$CHECKER2_PATH" \
  "$RUN_LOG_PATH" \
  "$REPORT_DIR" \
  "$CYCLE_STDOUT" \
  "$CYCLE_STDERR" \
  "$CYCLE_EXITCODE" \
; do
  if run_as_nightshift test -e "$path"; then
    fail "batch-smoke-$BATCH_SMOKE_ID evidence already exists at $path -- investigate and preserve it as failed-attempt evidence, then rerun with a different NS_BATCH_SMOKE_ID (e.g. NS_BATCH_SMOKE_ID=002) rather than clearing or overwriting it"
  fi
done

# ---------------------------------------------------------------------------
# tmux
# ---------------------------------------------------------------------------

command -v tmux > /dev/null 2>&1 || fail "tmux is not installed"

if run_as_nightshift tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
  fail "a tmux session named $TMUX_SESSION already exists"
fi

# ---------------------------------------------------------------------------
# Canonical Claude executable -- pure filesystem checks, never a `claude`
# invocation.
# ---------------------------------------------------------------------------

CLAUDE_REAL="$(run_as_nightshift readlink -f "$CLAUDE_CONFIGURED_PATH" || true)"
[ -n "$CLAUDE_REAL" ] || fail "could not resolve a canonical path for $CLAUDE_CONFIGURED_PATH"
case "$CLAUDE_REAL" in
  /*) ;;
  *) fail "resolved Claude executable path is not absolute: $CLAUDE_REAL" ;;
esac
run_as_nightshift test -e "$CLAUDE_REAL" || fail "resolved Claude executable does not exist: $CLAUDE_REAL"
run_as_nightshift test -f "$CLAUDE_REAL" || fail "resolved Claude executable is not a regular file: $CLAUDE_REAL"
run_as_nightshift test -x "$CLAUDE_REAL" || fail "resolved Claude executable is not executable: $CLAUDE_REAL"

CLAUDE_MODE="$(run_as_nightshift stat --format='%a' "$CLAUDE_REAL")"
GROUP_DIGIT="${CLAUDE_MODE: -2:1}"
OTHER_DIGIT="${CLAUDE_MODE: -1:1}"
case "$GROUP_DIGIT" in
  2|3|6|7) fail "resolved Claude executable is group-writable (mode $CLAUDE_MODE): $CLAUDE_REAL" ;;
esac
case "$OTHER_DIGIT" in
  2|3|6|7) fail "resolved Claude executable is world-writable (mode $CLAUDE_MODE): $CLAUDE_REAL" ;;
esac

# ---------------------------------------------------------------------------
# Deterministic preflight: isolation + non-work-producing
# `claude auth status --json` + CLI-capability check -- all three, up front,
# before the expensive test-suite run.
# ---------------------------------------------------------------------------

run_as_nightshift mkdir -p "$SMOKE_ROOT"

PREFLIGHT_LOG="$(mktemp)"
register_scratch "$PREFLIGHT_LOG"
if ! run_helper preflight "$SMOKE_ROOT" "$FORBIDDEN_PATH" "$CLAUDE_REAL" > "$PREFLIGHT_LOG" 2>&1; then
  cat "$PREFLIGHT_LOG" >&2
  fail "deterministic preflight (isolation + auth + CLI capability) did not pass"
fi

# ---------------------------------------------------------------------------
# The full Nightshift test suite must pass, exit status first, parsed count
# only as a corroborating secondary check.
# ---------------------------------------------------------------------------

if [ "$NS_TEST_MODE" = "1" ] && [ "${NS_SKIP_TEST_SUITE_CHECK:-0}" = "1" ]; then
  echo "NS_SKIP_TEST_SUITE_CHECK=1: skipping the test-suite precondition (test mode only)." >&2
else
  TEST_LOG="$(mktemp)"
  register_scratch "$TEST_LOG"
  if ! run_as_nightshift bash -c 'cd "$0" && python3 -m unittest discover -s nightshift/tests -v' \
      "$REPO_ROOT" > "$TEST_LOG" 2>&1; then
    cat "$TEST_LOG" >&2
    fail "the Nightshift test suite did not pass"
  fi
  TEST_COUNT="$(grep -Eo 'Ran [0-9]+ test' "$TEST_LOG" | tail -1 | grep -Eo '[0-9]+' || true)"
  [ -n "$TEST_COUNT" ] || fail "could not determine the test count from a passing test run (unexpected output shape)"
  [ "$TEST_COUNT" -ge "$MIN_TEST_COUNT" ] \
    || fail "test count $TEST_COUNT is below the required minimum $MIN_TEST_COUNT"
fi

# ---------------------------------------------------------------------------
# Set up this batch-smoke attempt: two disposable, independent projects,
# each with their own trusted checker copy and their own queued task.
# ---------------------------------------------------------------------------

TASK1_ID="batch-smoke-$BATCH_SMOKE_ID-1"
TASK2_ID="batch-smoke-$BATCH_SMOKE_ID-2"

run_as_nightshift mkdir -p "$TASK1_DIR" "$TASK2_DIR"
for d in "$TASK1_DIR" "$TASK2_DIR"; do
  run_as_nightshift git -C "$d" init -q
  run_as_nightshift git -C "$d" config user.email "nightshift-smoke@localhost"
  run_as_nightshift git -C "$d" config user.name "Nightshift Batch Smoke Test"
done

run_as_nightshift mkdir -p "$REPORT_DIR"

run_as_nightshift cp "$REPO_ROOT/nightshift/runtime/smoke_acceptance_checker.py" "$CHECKER1_PATH"
run_as_nightshift cp "$REPO_ROOT/nightshift/runtime/smoke_acceptance_checker.py" "$CHECKER2_PATH"
run_as_nightshift chmod 500 "$CHECKER1_PATH" "$CHECKER2_PATH"

run_as_nightshift tee "$QUEUE_PATH" > /dev/null <<EOF
{
  "tasks": [
    {
      "id": "$TASK1_ID",
      "status": "pending",
      "title": "Create calculator.py with an add(a, b) function that returns a + b, and test_calculator.py with a unittest TestCase covering: positive values (add(2, 3) == 5), negative values (add(-2, -3) == -5), and zero values (add(0, 0) == 0).",
      "attempt_count": 0,
      "max_attempts": 1,
      "claimed_pid": null,
      "claimed_at": null,
      "acceptance_command": ["python3", "$CHECKER1_PATH", "$TASK1_DIR"],
      "working_dir": "$TASK1_DIR",
      "timeout_seconds": 60,
      "executor_command": ["true"],
      "executor_timeout_seconds": 1,
      "approved_root": "$SMOKE_ROOT"
    },
    {
      "id": "$TASK2_ID",
      "status": "pending",
      "title": "Create calculator.py with a multiply(a, b) function that returns a * b, and test_calculator.py with a unittest TestCase covering: positive values (multiply(2, 3) == 6), negative values (multiply(-2, 3) == -6), and zero values (multiply(0, 5) == 0).",
      "attempt_count": 0,
      "max_attempts": 1,
      "claimed_pid": null,
      "claimed_at": null,
      "acceptance_command": ["python3", "$CHECKER2_PATH", "$TASK2_DIR"],
      "working_dir": "$TASK2_DIR",
      "timeout_seconds": 60,
      "executor_command": ["true"],
      "executor_timeout_seconds": 1,
      "approved_root": "$SMOKE_ROOT"
    }
  ]
}
EOF

run_as_nightshift tee "$CONFIG_PATH" > /dev/null <<EOF
{
  "queue_path": "$QUEUE_PATH",
  "report_dir": "$REPORT_DIR",
  "nightshift_root": "$SMOKE_ROOT",
  "forbidden_paths": ["$FORBIDDEN_PATH"],
  "claude_executable": "$CLAUDE_REAL",
  "claude_timeout_seconds": $CLAUDE_TIMEOUT_SECONDS,
  "run_log_path": "$RUN_LOG_PATH"
}
EOF

# ---------------------------------------------------------------------------
# Production baseline -- before.
# ---------------------------------------------------------------------------

[ -d "$RESEARCHLENS_PATH" ] || fail "ResearchLens path not found: $RESEARCHLENS_PATH"

researchlens_digest() {
  find "$RESEARCHLENS_PATH" -exec stat --format='%n|%s|%Y|%a|%U|%G' {} + | sort | sha256sum | awk '{print $1}'
}

mkdir -p "$(dirname "$RESEARCHLENS_BEFORE_DIGEST")" "$(dirname "$RESEARCHLENS_AFTER_DIGEST")"
researchlens_digest > "$RESEARCHLENS_BEFORE_DIGEST"

# ---------------------------------------------------------------------------
# Real invocation -- exactly one `run-batch` call, inside a dedicated tmux
# session so an SSH disconnect cannot kill it. The only place the real
# Claude service is ever invoked in this script is inside the two cycles
# this one `run-batch` call performs on its own. max-tasks=3 with exactly
# two pending tasks means the runner is expected to process both and then
# stop because it observed the queue empty -- never because a limit forced
# it to stop early. Runtime and consecutive-failure limits are left at
# run-batch's own production defaults.
# ---------------------------------------------------------------------------

CYCLE_CMD='cd "$0" && python3 -m nightshift.runtime.claude_executor run-batch --config "$1" --max-tasks "$2" > "$3" 2> "$4"; echo $? > "$5"'
run_as_nightshift tmux new-session -d -s "$TMUX_SESSION" \
  bash -c "$CYCLE_CMD" "$REPO_ROOT" "$CONFIG_PATH" "$BATCH_MAX_TASKS" "$CYCLE_STDOUT" "$CYCLE_STDERR" "$CYCLE_EXITCODE"

elapsed=0
while run_as_nightshift tmux has-session -t "$TMUX_SESSION" 2>/dev/null; do
  if [ "$elapsed" -ge "$MAX_WAIT_SECONDS" ]; then
    run_as_nightshift tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
    fail "batch-smoke-$BATCH_SMOKE_ID cycle did not finish within ${MAX_WAIT_SECONDS}s -- tmux session terminated, all evidence left in place, Claude was not rerun"
  fi
  sleep "$POLL_INTERVAL_SECONDS"
  elapsed=$((elapsed + POLL_INTERVAL_SECONDS))
done

run_as_nightshift test -f "$CYCLE_EXITCODE" \
  || fail "the cycle produced no exit-code evidence file: $CYCLE_EXITCODE"
CYCLE_STATUS="$(run_as_nightshift cat "$CYCLE_EXITCODE")"
# run-batch's own exit-code contract: 0 means the queue was observed empty
# and every attempted cycle succeeded -- exactly the outcome a two-task
# smoke expects. Any other exit code (1: a cycle failed or a blocking
# preflight/auth/policy/isolation/internal error; 2: stopped within limits
# but drainage unproven) is a genuine smoke failure here, not tolerated.
if [ "$CYCLE_STATUS" != "0" ]; then
  fail "run-batch exited $CYCLE_STATUS (expected 0: queue drained, both cycles succeeded) -- see $CYCLE_STDOUT / $CYCLE_STDERR"
fi

# ---------------------------------------------------------------------------
# Production baseline -- after, and compare.
# ---------------------------------------------------------------------------

researchlens_digest > "$RESEARCHLENS_AFTER_DIGEST"
if ! diff -q "$RESEARCHLENS_BEFORE_DIGEST" "$RESEARCHLENS_AFTER_DIGEST" > /dev/null; then
  fail "ResearchLens metadata-tree digest changed during the batch-smoke cycle -- investigate before treating anything else here as safe"
fi

# ---------------------------------------------------------------------------
# Post-run verification. The canonical queue and structured run-log
# evidence are authoritative -- never the terminal BatchResult JSON, the
# generated report's own prose, or any Claude-authored text.
# ---------------------------------------------------------------------------

if run_as_nightshift pgrep -u "$NIGHTSHIFT_USER" -f "$CLAUDE_REAL" > /dev/null 2>&1; then
  fail "a process matching the Claude executable is still running under $NIGHTSHIFT_USER after the batch completed"
fi

if run_as_nightshift pgrep -u "$NIGHTSHIFT_USER" -f "python3 -m nightshift.runtime.claude_executor" > /dev/null 2>&1; then
  fail "a lingering Nightshift Python process is still running under $NIGHTSHIFT_USER after the batch completed"
fi

if run_as_nightshift tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
  fail "the batch-smoke tmux session still exists after the cycle -- expected it to have already exited"
fi

VERIFY_LOG="$(mktemp)"
register_scratch "$VERIFY_LOG"
if ! run_helper verify_batch "$QUEUE_PATH" "$RUN_LOG_PATH" "$TASK1_ID" "$TASK1_DIR" "$TASK2_ID" "$TASK2_DIR" \
    > "$VERIFY_LOG" 2>&1; then
  cat "$VERIFY_LOG" >&2
  fail "structured post-run verification did not pass for both tasks (see the VERIFY_FAILED reason above)"
fi

RECHECK1_LOG="$(mktemp)"
RECHECK2_LOG="$(mktemp)"
register_scratch "$RECHECK1_LOG"
register_scratch "$RECHECK2_LOG"
if ! run_as_nightshift python3 "$CHECKER1_PATH" "$TASK1_DIR" > "$RECHECK1_LOG" 2>&1; then
  cat "$RECHECK1_LOG" >&2
  fail "an independent re-run of the trusted acceptance checker did not pass for $TASK1_ID"
fi
if ! run_as_nightshift python3 "$CHECKER2_PATH" "$TASK2_DIR" > "$RECHECK2_LOG" 2>&1; then
  cat "$RECHECK2_LOG" >&2
  fail "an independent re-run of the trusted acceptance checker did not pass for $TASK2_ID"
fi

echo "PASS: batch-smoke-$BATCH_SMOKE_ID completed one supervised two-task batch cycle and every post-run verification gate passed."
echo "Report: $REPORT_DIR"
echo "Run log: $RUN_LOG_PATH"
echo "No other smoke-*/batch-smoke-* evidence was modified."
exit 0
