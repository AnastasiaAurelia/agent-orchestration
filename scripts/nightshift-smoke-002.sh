#!/usr/bin/env bash
#
# One-command supervised Nightshift smoke-002 operator (Milestone 7C.2).
#
# Intended invocation on the VPS:
#
#   sudo bash /home/nightshift/workspace/agent-orchestration/scripts/nightshift-smoke-002.sh
#
# Runs with root privileges (via bare `sudo`), and uses `sudo -u nightshift
# -H` for every Nightshift-owned operation. Performs exactly one bounded
# real-Claude smoke cycle, entirely supervised, with no loop, no scheduler,
# and no automatic rerun. See docs/nightshift/CLAUDE_EXECUTOR_SMOKE_TEST.md
# for the full narrative and the detailed manual procedure this replaces as
# the default path (kept there only as a troubleshooting appendix).
#
# smoke-001's evidence (its queue, config, run log, report, and task-001
# project) is never read for anything other than an existence check, and is
# never written to, renamed, or deleted by this script.
#
# Testability: every path this script touches is overridable via NS_*
# environment variables (defaults match the real VPS layout below), and
# NS_TEST_MODE=1 replaces every `sudo -u nightshift -H` with direct
# execution as the invoking user -- see nightshift/tests/test_nightshift_smoke_script.py,
# which drives this exact script end-to-end against a temporary fixture
# tree and a fake `claude`/`tmux` pair, never a real Claude invocation.
#
# NS_RESEARCHLENS_BEFORE_DIGEST and NS_RESEARCHLENS_AFTER_DIGEST
# (Milestone 7C.2.1) are test-only overrides for the two ResearchLens
# digest artifact paths -- honored only when NS_TEST_MODE=1; set outside
# test mode, the script fails closed immediately rather than silently
# using or ignoring them. This exists so automated tests never depend on,
# and can never collide with, the real fixed production digest pair (a
# pre-existing root-owned file at the old, generic path once caused
# Permission Denied for the isolated `nightshift` user on the real VPS).

set -Eeuo pipefail

# A `sudo -u nightshift` command run while the parent shell's cwd is
# /home/ubuntu produces a harmless-but-confusing "Failed to restore initial
# working directory" warning (nightshift cannot traverse it) -- move
# somewhere every account can reach before doing anything else.
cd /tmp

# ---------------------------------------------------------------------------
# Configuration (overridable for tests; defaults match the real VPS layout)
# ---------------------------------------------------------------------------

NS_TEST_MODE="${NS_TEST_MODE:-0}"

# Milestone 7C.2.1: NS_RESEARCHLENS_BEFORE_DIGEST/NS_RESEARCHLENS_AFTER_DIGEST
# are test-only overrides for the two ResearchLens digest artifact paths
# below. Checked here, before anything else, so a misconfigured *real* run
# can never silently redirect where the production before/after digest is
# written just because one of these happened to be set in the environment --
# fail closed immediately instead.
for _ns_test_only_var in NS_RESEARCHLENS_BEFORE_DIGEST NS_RESEARCHLENS_AFTER_DIGEST; do
  if [ "$NS_TEST_MODE" != "1" ] && [ -n "${!_ns_test_only_var:-}" ]; then
    echo "GATE FAILED: $_ns_test_only_var is a test-only override and must not be set outside NS_TEST_MODE=1" >&2
    exit 1
  fi
done
unset _ns_test_only_var

NIGHTSHIFT_USER="${NS_NIGHTSHIFT_USER:-nightshift}"

# Milestone 7C.2.2: which smoke attempt this run is. Defaults to "002" (this
# script's own identity, unchanged) but a human operator who has confirmed
# smoke-002 evidence already exists from a prior failed attempt can run the
# *next* attempt under an entirely new, non-colliding identity -- e.g.
# `sudo env NS_SMOKE_ID=003 bash .../nightshift-smoke-002.sh` -- without
# ever needing to touch, clear, or reuse the preserved smoke-002 evidence.
# Every path below is derived from this one value, so choosing a fresh ID is
# sufficient on its own to guarantee no collision with any prior attempt.
SMOKE_ID="${NS_SMOKE_ID:-002}"

NIGHTSHIFT_HOME="${NS_NIGHTSHIFT_HOME:-/home/nightshift}"
REPO_ROOT="${NS_REPO_ROOT:-$NIGHTSHIFT_HOME/workspace/agent-orchestration}"
SMOKE_ROOT="${NS_SMOKE_ROOT:-$NIGHTSHIFT_HOME/workspace/smoke}"
TASK_DIR="$SMOKE_ROOT/task-$SMOKE_ID"
STATE_DIR="${NS_STATE_DIR:-$NIGHTSHIFT_HOME/state}"
LOGS_DIR="${NS_LOGS_DIR:-$NIGHTSHIFT_HOME/logs}"
REPORTS_DIR="${NS_REPORTS_DIR:-$NIGHTSHIFT_HOME/reports}"

QUEUE_PATH="$STATE_DIR/smoke-$SMOKE_ID-queue.json"
CONFIG_PATH="$STATE_DIR/smoke-$SMOKE_ID-config.json"
CHECKER_PATH="$STATE_DIR/smoke-$SMOKE_ID-acceptance.py"
RUN_LOG_PATH="$LOGS_DIR/smoke-$SMOKE_ID-run-log.jsonl"
REPORT_DIR="$REPORTS_DIR/smoke-$SMOKE_ID"
CYCLE_STDOUT="$LOGS_DIR/smoke-$SMOKE_ID-cycle.stdout.log"
CYCLE_STDERR="$LOGS_DIR/smoke-$SMOKE_ID-cycle.stderr.log"
CYCLE_EXITCODE="$LOGS_DIR/smoke-$SMOKE_ID-cycle.exitcode"

TMUX_SESSION="${NS_TMUX_SESSION:-nightshift-smoke-$SMOKE_ID}"

FORBIDDEN_PATH="${NS_FORBIDDEN_PATH:-/home/ubuntu}"
RESEARCHLENS_PATH="${NS_RESEARCHLENS_PATH:-/home/ubuntu/ResearchLens}"
CLAUDE_CONFIGURED_PATH="${NS_CLAUDE_CONFIGURED_PATH:-$NIGHTSHIFT_HOME/.local/bin/claude}"

# Milestone 7C.2.1: named for this specific smoke attempt (via SMOKE_ID),
# never a bare generic shared name -- an admin-created, root-owned file at a
# fixed, non-smoke-specific path once caused the isolated `nightshift` user
# a write failure here. Test-only overrides (validated above) let each
# automated test point these at its own temporary path instead of ever
# touching this fixed production pair. (Deliberately described here without
# repeating the literal old generic filename verbatim, so a simple
# string-match audit of this file's executable logic for that legacy name
# does not also flag a comment that only narrates history and touches no
# live code path.)
RESEARCHLENS_BEFORE_DIGEST="${NS_RESEARCHLENS_BEFORE_DIGEST:-/tmp/nightshift-smoke-$SMOKE_ID-researchlens-before.sha256}"
RESEARCHLENS_AFTER_DIGEST="${NS_RESEARCHLENS_AFTER_DIGEST:-/tmp/nightshift-smoke-$SMOKE_ID-researchlens-after.sha256}"

# smoke-001 evidence -- read-only existence checks only, never written.
SMOKE1_TASK_DIR="$SMOKE_ROOT/task-001"
SMOKE1_QUEUE_PATH="$STATE_DIR/smoke-queue.json"
SMOKE1_CONFIG_PATH="$STATE_DIR/smoke-config.json"
SMOKE1_RUN_LOG_PATH="$LOGS_DIR/smoke-run-log.jsonl"
SMOKE1_REPORT_DIR="$REPORTS_DIR/smoke-001"

MAX_WAIT_SECONDS="${NS_MAX_WAIT_SECONDS:-600}"
POLL_INTERVAL_SECONDS="${NS_POLL_INTERVAL_SECONDS:-5}"
CLAUDE_TIMEOUT_SECONDS="${NS_CLAUDE_TIMEOUT_SECONDS:-120}"
MIN_TEST_COUNT="${NS_MIN_TEST_COUNT:-205}"

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

# Milestone 7C.2.2: feeds the embedded helper's Python source directly over
# stdin to `python3 -`, never via an on-disk file. A root-created `mktemp`
# file is mode 0600 by default -- fine for *this* process to read, but the
# real VPS run showed exactly the failure that causes: nightshift, a
# different, unprivileged user, got Permission Denied trying to open() that
# path itself. Piping the source over stdin sidesteps this entirely: the
# underlying temp storage bash's heredoc mechanism uses is still created and
# opened by this (root) process, but the child (running as nightshift via
# `sudo -u nightshift -H`) only ever inherits that already-open file
# descriptor across fork/exec -- it never calls open() on the path itself,
# so the path's own permission bits never matter. `PYTHONPATH` makes the
# `nightshift` package importable without needing cwd to matter either;
# `PATH` is set explicitly and minimally, never inherited, closing off any
# PATH-poisoning surface for this invocation specifically.
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
    echo "nightshift-smoke-002.sh (attempt smoke-$SMOKE_ID) exited with status $ec." >&2
    echo "No smoke-$SMOKE_ID evidence was deleted or reset -- everything under" >&2
    echo "$STATE_DIR, $LOGS_DIR, $REPORTS_DIR, and $TASK_DIR has been left" >&2
    echo "in place for investigation. smoke-001 was not touched." >&2
  fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Embedded structured-verification helper (stdlib only, never invokes Claude)
# ---------------------------------------------------------------------------

HELPER_PY_SOURCE="$(cat <<'PYEOF'
"""Structured, non-interactive helper for the Nightshift smoke operator script.

Two subcommands, both stdlib-only and read-only:

  preflight <nightshift_root> <forbidden_path> <claude_executable>
      Runs auth_preflight.preflight_gate() (isolation + non-work-producing
      `claude auth status --json`) and exits 0 only if it passes. Prints
      only the gate's own already-redacted to_json_dict() -- never a raw
      auth-status payload, token, email, or org id.

  verify <queue_path> <run_log_path> <task_dir> <task_id>
      Checks the canonical queue and durable run log -- never the terminal
      CycleResult, never model-authored text -- for the specific set of
      facts Milestone 7C.2 requires before a human may treat a smoke cycle
      as a genuine success. Exits 0 and prints VERIFY_OK only if every
      check passes; otherwise prints VERIFY_FAILED: <reason> to stderr and
      exits 1.
"""
import json
import os
import sys


def cmd_preflight(argv):
    from nightshift.runtime import auth_preflight

    nightshift_root, forbidden_path, claude_executable = argv
    gate = auth_preflight.preflight_gate(
        nightshift_root,
        [forbidden_path],
        claude_command=[claude_executable, "auth", "status", "--json"],
    )
    print(json.dumps(gate.to_json_dict()))
    return 0 if gate.passed else 1


def cmd_verify(argv):
    from nightshift.runtime import queue as nsq

    queue_path, run_log_path, task_dir, task_id = argv

    def verify_fail(reason):
        print(f"VERIFY_FAILED: {reason}", file=sys.stderr)
        return 1

    try:
        with open(queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
    except Exception as exc:  # noqa: BLE001 -- surfaced as a plain gate failure
        return verify_fail(f"could not read/validate queue: {exc}")

    task = next((t for t in data["tasks"] if t["id"] == task_id), None)
    if task is None:
        return verify_fail(f"no such task in queue: {task_id!r}")

    if task["status"] != "done":
        return verify_fail(f"queue task status is {task['status']!r}, not 'done'")
    if task["attempt_count"] != 1:
        return verify_fail(f"attempt_count is {task['attempt_count']!r}, not 1")

    events, malformed = nsq._load_run_log_events(run_log_path)
    if malformed:
        return verify_fail(f"{malformed} malformed run-log line(s)")

    evidence_events = [
        e
        for e in events
        if e.get("event") == "task_run_evidence" and e.get("task_id") == task_id
    ]
    if not evidence_events:
        return verify_fail("no task_run_evidence entry found in the durable run log")
    evidence = evidence_events[-1]

    if evidence.get("executor_outcome") != "completed":
        return verify_fail(
            f"executor_outcome is {evidence.get('executor_outcome')!r}, not 'completed'"
        )
    if evidence.get("executor_exit_code") != 0:
        return verify_fail(
            f"executor_exit_code is {evidence.get('executor_exit_code')!r}, not 0"
        )
    if evidence.get("acceptance_outcome") != "passed":
        return verify_fail(
            f"acceptance_outcome is {evidence.get('acceptance_outcome')!r}, not 'passed'"
        )
    if evidence.get("acceptance_exit_code") != 0:
        return verify_fail(
            f"acceptance_exit_code is {evidence.get('acceptance_exit_code')!r}, not 0"
        )

    done_events = [
        e for e in events if e.get("event") == "done" and e.get("task_id") == task_id
    ]
    if not done_events:
        return verify_fail("no 'done' transition event found in the durable run log")

    if not os.path.isfile(os.path.join(task_dir, "calculator.py")):
        return verify_fail("calculator.py does not exist in the disposable project")
    if not os.path.isfile(os.path.join(task_dir, "test_calculator.py")):
        return verify_fail("test_calculator.py does not exist in the disposable project")

    suspect_terms = ("401", "auth_failed", "authentication failure")
    for event in events:
        if event.get("task_id") != task_id:
            continue
        haystack = " ".join(
            str(event.get(field, ""))
            for field in ("executor_outcome", "detail", "evidence_excerpt")
        ).lower()
        for term in suspect_terms:
            if term.lower() in haystack:
                return verify_fail(
                    "unexpected authentication-failure evidence found in the run "
                    f"log for a run that otherwise looks successful: {term!r}"
                )

    print("VERIFY_OK")
    return 0


def main(argv):
    if not argv:
        print("usage: helper.py <preflight|verify> ...", file=sys.stderr)
        return 2
    command, rest = argv[0], argv[1:]
    if command == "preflight":
        return cmd_preflight(rest)
    if command == "verify":
        return cmd_verify(rest)
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

# Milestone 7C.2.2: move from /tmp (accessible to every account, but not
# semantically meaningful) to the repository checkout itself -- this is
# nightshift's own directory, so every later `sudo -u nightshift -H`
# command (including the preflight helper) runs from a working directory
# that user can always access, never one it merely happens not to be
# blocked from.
cd "$REPO_ROOT"

# -- Preserve smoke-001: every path must already exist, none may be touched.
for path in \
  "$SMOKE1_TASK_DIR" \
  "$SMOKE1_QUEUE_PATH" \
  "$SMOKE1_CONFIG_PATH" \
  "$SMOKE1_RUN_LOG_PATH" \
  "$SMOKE1_REPORT_DIR" \
; do
  run_as_nightshift test -e "$path" \
    || fail "smoke-001 evidence missing or moved: $path -- refusing to proceed rather than assume it is safe to ignore"
done

# -- Refuse over old evidence for this attempt (smoke-$SMOKE_ID); never
# auto-clean. Choosing a different NS_SMOKE_ID is how a human deliberately
# starts a fresh attempt without touching a prior one's preserved evidence.
for path in \
  "$TASK_DIR" \
  "$QUEUE_PATH" \
  "$CONFIG_PATH" \
  "$CHECKER_PATH" \
  "$RUN_LOG_PATH" \
  "$REPORT_DIR" \
  "$CYCLE_STDOUT" \
  "$CYCLE_STDERR" \
  "$CYCLE_EXITCODE" \
; do
  if run_as_nightshift test -e "$path"; then
    fail "smoke-$SMOKE_ID evidence already exists at $path -- investigate and preserve it as failed-attempt evidence, then rerun with a different NS_SMOKE_ID (e.g. NS_SMOKE_ID=003) rather than clearing or overwriting it"
  fi
done
# ---------------------------------------------------------------------------
# Precondition 7: tmux installed (checked via PATH, never a hardcoded path,
# so a test fixture's fake tmux is exercised identically to the real one).
# Checked before any `tmux` subcommand is ever run, including the
# no-old-session guard just below.
# ---------------------------------------------------------------------------

command -v tmux > /dev/null 2>&1 || fail "tmux is not installed"

if run_as_nightshift tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
  fail "a tmux session named $TMUX_SESSION already exists"
fi

# ---------------------------------------------------------------------------
# Precondition 6: canonical Claude executable -- pure filesystem checks,
# never a `claude` invocation.
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
# Precondition 8: deterministic preflight (isolation + non-work-producing
# `claude auth status --json`) -- must exist before validate_isolation can
# even run.
# ---------------------------------------------------------------------------

run_as_nightshift mkdir -p "$SMOKE_ROOT"

PREFLIGHT_LOG="$(mktemp)"
register_scratch "$PREFLIGHT_LOG"
if ! run_helper preflight "$SMOKE_ROOT" "$FORBIDDEN_PATH" "$CLAUDE_REAL" > "$PREFLIGHT_LOG" 2>&1; then
  cat "$PREFLIGHT_LOG" >&2
  fail "deterministic preflight (isolation + auth) did not pass"
fi

# ---------------------------------------------------------------------------
# Precondition 4/5: the full Nightshift test suite must pass, and its own
# exit status -- not text-matching -- is the primary evidence; the parsed
# count is only a corroborating secondary check. Deliberately run last among
# the preconditions (the milestone numbers it 4/5, ahead of 6-9) since it is
# by far the most expensive check and every other precondition is a cheap,
# near-instant filesystem/PATH check -- failing those first means a trivial
# misconfiguration never has to wait through a full test run to be reported.
# All nine preconditions must still hold before the real invocation either
# way; only the order in which they are checked differs from the numbering.
# ---------------------------------------------------------------------------

# NS_SKIP_TEST_SUITE_CHECK is a test-only escape hatch, only ever honored
# together with NS_TEST_MODE=1 (so it can never trigger in a real, unattended
# run): nightshift/tests/test_nightshift_smoke_script.py drives this exact
# script end to end against the real repository, which means the suite this
# step would run *includes that very test file*. Its own "full setup" tests
# would otherwise each re-run the whole suite again -- unbounded, worsening
# nested self-invocation, not a real correctness gate on those tests' own
# terms (they exercise the other eight preconditions and the post-run
# verification, not this one). Every real, non-test-mode invocation always
# runs the suite for real; this branch is structurally unreachable there.
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
# Set up this smoke attempt: disposable project, dedicated report dir,
# trusted checker, one-task queue, Claude executor configuration.
# ---------------------------------------------------------------------------

run_as_nightshift mkdir -p "$TASK_DIR"
run_as_nightshift git -C "$TASK_DIR" init -q
run_as_nightshift git -C "$TASK_DIR" config user.email "nightshift-smoke@localhost"
run_as_nightshift git -C "$TASK_DIR" config user.name "Nightshift Smoke Test"

run_as_nightshift mkdir -p "$REPORT_DIR"

run_as_nightshift cp "$REPO_ROOT/nightshift/runtime/smoke_acceptance_checker.py" "$CHECKER_PATH"
run_as_nightshift chmod 500 "$CHECKER_PATH"

run_as_nightshift tee "$QUEUE_PATH" > /dev/null <<EOF
{
  "tasks": [
    {
      "id": "smoke-$SMOKE_ID",
      "status": "pending",
      "title": "Create calculator.py with an add(a, b) function that returns a + b, and test_calculator.py with a unittest TestCase covering: positive values (add(2, 3) == 5), negative values (add(-2, -3) == -5), and zero values (add(0, 0) == 0).",
      "attempt_count": 0,
      "max_attempts": 1,
      "claimed_pid": null,
      "claimed_at": null,
      "acceptance_command": ["python3", "$CHECKER_PATH", "$TASK_DIR"],
      "working_dir": "$TASK_DIR",
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
# Production baseline -- before. Run as this script's own (root/admin)
# session, never as nightshift, since the point is to check from outside
# the isolated account's own restricted view of production.
# ---------------------------------------------------------------------------

[ -d "$RESEARCHLENS_PATH" ] || fail "ResearchLens path not found: $RESEARCHLENS_PATH"

researchlens_digest() {
  find "$RESEARCHLENS_PATH" -exec stat --format='%n|%s|%Y|%a|%U|%G' {} + | sort | sha256sum | awk '{print $1}'
}

mkdir -p "$(dirname "$RESEARCHLENS_BEFORE_DIGEST")" "$(dirname "$RESEARCHLENS_AFTER_DIGEST")"
researchlens_digest > "$RESEARCHLENS_BEFORE_DIGEST"

# ---------------------------------------------------------------------------
# Real invocation -- exactly one bounded cycle, inside a dedicated tmux
# session so an SSH disconnect cannot kill it. The only place the real
# Claude service is ever invoked in this script is inside this one
# `run-one` call.
# ---------------------------------------------------------------------------

CYCLE_CMD='cd "$0" && python3 -m nightshift.runtime.claude_executor run-one --config "$1" > "$2" 2> "$3"; echo $? > "$4"'
run_as_nightshift tmux new-session -d -s "$TMUX_SESSION" \
  bash -c "$CYCLE_CMD" "$REPO_ROOT" "$CONFIG_PATH" "$CYCLE_STDOUT" "$CYCLE_STDERR" "$CYCLE_EXITCODE"

elapsed=0
while run_as_nightshift tmux has-session -t "$TMUX_SESSION" 2>/dev/null; do
  if [ "$elapsed" -ge "$MAX_WAIT_SECONDS" ]; then
    run_as_nightshift tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
    fail "smoke-$SMOKE_ID cycle did not finish within ${MAX_WAIT_SECONDS}s -- tmux session terminated, all evidence left in place, Claude was not rerun"
  fi
  sleep "$POLL_INTERVAL_SECONDS"
  elapsed=$((elapsed + POLL_INTERVAL_SECONDS))
done

run_as_nightshift test -f "$CYCLE_EXITCODE" \
  || fail "the cycle produced no exit-code evidence file: $CYCLE_EXITCODE"
CYCLE_STATUS="$(run_as_nightshift cat "$CYCLE_EXITCODE")"
if [ "$CYCLE_STATUS" != "0" ]; then
  fail "run-one itself exited $CYCLE_STATUS (a config/invocation-level failure, not a task-level retry) -- see $CYCLE_STDERR"
fi

# ---------------------------------------------------------------------------
# Production baseline -- after, and compare. This proves the observed
# metadata tree (names/sizes/mtimes/permission bits/owner/group) is
# unchanged, not byte-for-byte content identity of every file.
# ---------------------------------------------------------------------------

researchlens_digest > "$RESEARCHLENS_AFTER_DIGEST"
if ! diff -q "$RESEARCHLENS_BEFORE_DIGEST" "$RESEARCHLENS_AFTER_DIGEST" > /dev/null; then
  fail "ResearchLens metadata-tree digest changed during the smoke cycle -- investigate before treating anything else here as safe"
fi

# ---------------------------------------------------------------------------
# Post-run verification. The canonical queue and structured run-log
# evidence are authoritative -- never the terminal CycleResult, the
# generated report's own prose, or any Claude-authored text.
# ---------------------------------------------------------------------------

if run_as_nightshift pgrep -u "$NIGHTSHIFT_USER" -f "$CLAUDE_REAL" > /dev/null 2>&1; then
  fail "a process matching the Claude executable is still running under $NIGHTSHIFT_USER after the cycle completed"
fi

if run_as_nightshift tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
  fail "the smoke tmux session still exists after the cycle -- expected it to have already exited"
fi

VERIFY_LOG="$(mktemp)"
register_scratch "$VERIFY_LOG"
if ! run_helper verify "$QUEUE_PATH" "$RUN_LOG_PATH" "$TASK_DIR" "smoke-$SMOKE_ID" > "$VERIFY_LOG" 2>&1; then
  cat "$VERIFY_LOG" >&2
  fail "structured post-run verification did not pass (see the VERIFY_FAILED reason above)"
fi

RECHECK_LOG="$(mktemp)"
register_scratch "$RECHECK_LOG"
if ! run_as_nightshift python3 "$CHECKER_PATH" "$TASK_DIR" > "$RECHECK_LOG" 2>&1; then
  cat "$RECHECK_LOG" >&2
  fail "an independent re-run of the trusted acceptance checker did not pass"
fi

echo "PASS: smoke-$SMOKE_ID completed one supervised cycle and every post-run verification gate passed."
echo "Report: $REPORT_DIR"
echo "Run log: $RUN_LOG_PATH"
echo "smoke-001 evidence was not modified."
exit 0
