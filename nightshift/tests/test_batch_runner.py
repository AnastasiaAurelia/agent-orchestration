"""Tests for the Milestone 7D bounded batch runner (nightshift.runtime.claude_executor.run_batch).

Uses only fake local executables standing in for `claude` -- never invokes a
real Claude Code session, never touches this machine's actual Claude
authentication or credentials. Every test operates inside a fresh
tempfile.mkdtemp() directory. Mirrors the fixture conventions already used by
test_claude_executor.py (duplicated here rather than imported, matching how
every other test module in this package builds its own fixtures).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from nightshift.runtime import claude_executor as ce  # noqa: E402
from nightshift.runtime import queue as nsq  # noqa: E402

_DEFAULT_HELP_TEXT = (
    "Usage: claude [options] [prompt]\n"
    "  -p, --print\n"
    "  --tools <tools...>\n"
    "  --allowedTools <tools...>\n"
    "  --permission-mode <mode>\n"
    "  --strict-mcp-config\n"
    "  --disable-slash-commands\n"
    "  --no-session-persistence\n"
    "  --output-format <format>\n"
)

_DEFAULT_AUTH_PAYLOAD = {
    "loggedIn": True,
    "authMethod": "claude.ai",
    "apiProvider": "firstParty",
    "subscriptionType": "pro",
}

# Same rationale as test_claude_executor.py's own _CLEAN_ENV: isolate these
# tests from whatever benign variables happen to already be in the
# developer's own shell.
_CLEAN_ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}

_FAKE_CLAUDE_TEMPLATE = """{shebang}
import sys, json, os, time

argv = sys.argv[1:]

if argv[:1] == ["--version"]:
    sys.stdout.write({version!r})
    sys.exit(0)

if argv[:1] == ["--help"]:
    sys.stdout.write({help_text!r})
    sys.exit(0)

if len(argv) >= 2 and argv[0] == "auth" and argv[1] == "status":
    sys.stdout.write(json.dumps({auth_payload!r}))
    sys.exit({auth_exit_code!r})

dump_path = {dump_path!r}
if dump_path:
    with open(dump_path, "a") as f:
        f.write(json.dumps({{"argv": argv, "pid": os.getpid()}}) + "\\n")

{spawn_grandchild_snippet}

sleep_seconds = {sleep_seconds!r}
if sleep_seconds:
    time.sleep(sleep_seconds)

sys.stdout.write({stdout_text!r})
sys.stderr.write({stderr_text!r})
sys.exit({exit_code!r})
"""


def _write_fake_claude(
    directory,
    name="fake-claude",
    version="9.9.9 (Fake Claude)",
    help_text=None,
    auth_payload=None,
    auth_exit_code=0,
    dump_path=None,
    sleep_seconds=0,
    stdout_text="",
    stderr_text="",
    exit_code=0,
    spawn_grandchild_pid_file=None,
):
    if help_text is None:
        help_text = _DEFAULT_HELP_TEXT
    if auth_payload is None:
        auth_payload = _DEFAULT_AUTH_PAYLOAD

    spawn_snippet = ""
    if spawn_grandchild_pid_file:
        spawn_snippet = (
            "import subprocess\n"
            f"gc = subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(30)'])\n"
            f"with open({spawn_grandchild_pid_file!r}, 'w') as f:\n"
            "    f.write(str(gc.pid))\n"
        )

    script = _FAKE_CLAUDE_TEMPLATE.format(
        shebang=f"#!{sys.executable}",
        version=version,
        help_text=help_text,
        auth_payload=auth_payload,
        auth_exit_code=auth_exit_code,
        dump_path=dump_path,
        spawn_grandchild_snippet=spawn_snippet,
        sleep_seconds=sleep_seconds,
        stdout_text=stdout_text,
        stderr_text=stderr_text,
        exit_code=exit_code,
    )
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(script)
    os.chmod(path, 0o755)
    return os.path.realpath(path)


def _task(
    task_id="t1",
    title="Bounded batch objective",
    working_dir=None,
    approved_root=None,
    attempt_count=0,
    max_attempts=1,
    status="pending",
    acceptance_command=None,
):
    if acceptance_command is None:
        acceptance_command = [sys.executable, "-c", "pass"]
    return {
        "id": task_id,
        "status": status,
        "title": title,
        "attempt_count": attempt_count,
        "max_attempts": max_attempts,
        "claimed_pid": None,
        "claimed_at": None,
        "executor_command": [sys.executable, "-c", "pass"],
        "executor_timeout_seconds": 5,
        "acceptance_command": acceptance_command,
        "working_dir": working_dir,
        "timeout_seconds": 5,
        "approved_root": approved_root,
    }


def _write_raw_queue(path, tasks):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks}, f, indent=2, sort_keys=True)
        f.write("\n")


def _wait_until_pid_dead(pid, timeout=5.0):
    deadline = time.monotonic() + timeout
    while nsq._is_pid_alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


class _FakeClock:
    """A test-injectable monotonic clock: returns a fixed sequence of
    values, then repeats the final value forever once exhausted (so a test
    only needs to encode the elapsed-time deltas that matter at each
    conceptual checkpoint, not the exact number of times run_batch() happens
    to call the clock).
    """

    def __init__(self, values):
        self._values = list(values)
        self._index = 0

    def __call__(self):
        if self._index < len(self._values):
            value = self._values[self._index]
            self._index += 1
            return value
        return self._values[-1]


class BatchRunnerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nightshift-batch-runner-test-")
        self.nightshift_root = os.path.join(self.tmpdir, "nightshift-root")
        self.production_root = os.path.join(self.tmpdir, "production")
        self.fake_bin_dir = os.path.join(self.tmpdir, "fake-bin")
        os.makedirs(self.nightshift_root)
        os.makedirs(self.production_root)
        os.makedirs(self.fake_bin_dir)
        self.queue_path = os.path.join(self.nightshift_root, "queue.json")
        self.report_dir = os.path.join(self.nightshift_root, "reports")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self, claude_executable=None, **overrides):
        if claude_executable is None:
            claude_executable = _write_fake_claude(self.fake_bin_dir)
        data = {
            "queue_path": self.queue_path,
            "report_dir": self.report_dir,
            "nightshift_root": self.nightshift_root,
            "forbidden_paths": [self.production_root],
            "claude_executable": claude_executable,
        }
        data.update(overrides)
        return ce.ClaudeConfig.load(data)

    def _task_working_dir(self):
        working_dir = os.path.join(self.nightshift_root, "work")
        os.makedirs(working_dir, exist_ok=True)
        return working_dir

    # -- 1: empty queue --------------------------------------------------------

    def test_empty_queue_stops_immediately_with_queue_empty(self):
        _write_raw_queue(self.queue_path, [])
        config = self._config()

        result = ce.run_batch(config, env=_CLEAN_ENV)

        self.assertEqual(result.stop_reason, ce.BatchStopReason.QUEUE_EMPTY)
        self.assertTrue(result.queue_observed_empty)
        self.assertEqual(result.attempted_cycles, 0)
        self.assertEqual(result.done_count, 0)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(ce.batch_exit_code(result), 0)

    # -- 2: two successful pending tasks processed in order, then queue_empty --

    def test_two_successful_tasks_are_processed_in_order_then_queue_empty(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir)
        _write_raw_queue(
            self.queue_path,
            [
                _task(task_id="t1", working_dir=working_dir, approved_root=working_dir),
                _task(task_id="t2", working_dir=working_dir, approved_root=working_dir),
            ],
        )
        config = self._config(claude_executable=claude_executable)

        result = ce.run_batch(config, max_tasks=5, env=_CLEAN_ENV)

        self.assertEqual(result.task_ids_attempted, ("t1", "t2"))
        self.assertEqual(result.attempted_cycles, 2)
        self.assertEqual(result.done_count, 2)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(result.stop_reason, ce.BatchStopReason.QUEUE_EMPTY)
        self.assertTrue(result.queue_observed_empty)
        self.assertEqual(ce.batch_exit_code(result), 0)
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        self.assertEqual([t["status"] for t in data["tasks"]], ["done", "done"])

    # -- 3: max_tasks enforcement -----------------------------------------------

    def test_max_tasks_stops_the_batch_leaving_work_pending(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir)
        _write_raw_queue(
            self.queue_path,
            [
                _task(task_id=f"t{i}", working_dir=working_dir, approved_root=working_dir)
                for i in range(5)
            ],
        )
        config = self._config(claude_executable=claude_executable)

        result = ce.run_batch(config, max_tasks=3, env=_CLEAN_ENV)

        self.assertEqual(result.attempted_cycles, 3)
        self.assertEqual(result.done_count, 3)
        self.assertEqual(result.stop_reason, ce.BatchStopReason.MAX_TASKS_REACHED)
        self.assertFalse(result.queue_observed_empty)
        self.assertEqual(ce.batch_exit_code(result), 2)
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        statuses = [t["status"] for t in data["tasks"]]
        self.assertEqual(statuses.count("done"), 3)
        self.assertEqual(statuses.count("pending"), 2)

    # -- 4: runtime exhausted before the first claim -----------------------------

    def test_runtime_exhausted_before_first_claim_claims_nothing(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir)
        _write_raw_queue(
            self.queue_path, [_task(working_dir=working_dir, approved_root=working_dir)]
        )
        config = self._config(claude_executable=claude_executable)
        clock = _FakeClock([0, 1000])

        result = ce.run_batch(config, max_runtime_seconds=1, env=_CLEAN_ENV, clock=clock)

        self.assertEqual(result.attempted_cycles, 0)
        self.assertEqual(result.stop_reason, ce.BatchStopReason.MAX_RUNTIME_REACHED)
        self.assertEqual(ce.batch_exit_code(result), 2)
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        self.assertEqual(data["tasks"][0]["status"], "pending")

    # -- 5: runtime exhausted between tasks --------------------------------------

    def test_runtime_exhausted_between_tasks_stops_before_second_claim(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir)
        _write_raw_queue(
            self.queue_path,
            [
                _task(task_id="t1", working_dir=working_dir, approved_root=working_dir),
                _task(task_id="t2", working_dir=working_dir, approved_root=working_dir),
            ],
        )
        config = self._config(claude_executable=claude_executable)
        clock = _FakeClock([0, 0, 1000])

        result = ce.run_batch(config, max_tasks=5, max_runtime_seconds=10, env=_CLEAN_ENV, clock=clock)

        self.assertEqual(result.attempted_cycles, 1)
        self.assertEqual(result.task_ids_attempted, ("t1",))
        self.assertEqual(result.done_count, 1)
        self.assertEqual(result.stop_reason, ce.BatchStopReason.MAX_RUNTIME_REACHED)
        self.assertEqual(ce.batch_exit_code(result), 2)
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        self.assertEqual(data["tasks"][1]["status"], "pending")

    # -- 6: runtime budget caps a claimed task's own executor timeout ------------

    def test_runtime_budget_caps_the_per_task_executor_timeout(self):
        # The task's own configured timeout (300s) is far larger than the
        # tiny batch runtime budget (2s). If capping did not happen, the
        # fake claude's 10s sleep would never be interrupted by the batch
        # budget within this test's patience. Real (default) monotonic
        # clock is used here deliberately -- this proves the real, live
        # capping behavior end-to-end, not just the arithmetic.
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir, sleep_seconds=10)
        _write_raw_queue(
            self.queue_path, [_task(working_dir=working_dir, approved_root=working_dir)]
        )
        config = self._config(claude_executable=claude_executable, claude_timeout_seconds=300)

        result = ce.run_batch(config, max_runtime_seconds=2, env=_CLEAN_ENV)

        self.assertEqual(result.attempted_cycles, 1)
        self.assertEqual(result.cycle_outcomes[0].executor_outcome, nsq.ExecutorOutcome.TIMED_OUT.value)
        self.assertEqual(result.failed_count, 1)

    # -- 7: deadline during execution is cleaned up and never leaves a claimed --
    #      task or a lingering process

    def test_deadline_during_execution_cleans_up_and_stops_the_batch(self):
        working_dir = self._task_working_dir()
        grandchild_pid_file = os.path.join(self.tmpdir, "grandchild_pid.txt")
        claude_executable = _write_fake_claude(
            self.fake_bin_dir, sleep_seconds=10, spawn_grandchild_pid_file=grandchild_pid_file
        )
        _write_raw_queue(
            self.queue_path, [_task(working_dir=working_dir, approved_root=working_dir)]
        )
        config = self._config(claude_executable=claude_executable, claude_timeout_seconds=300)

        result = ce.run_batch(config, max_tasks=5, max_runtime_seconds=2, env=_CLEAN_ENV)

        self.assertEqual(result.attempted_cycles, 1)
        self.assertEqual(result.stop_reason, ce.BatchStopReason.MAX_RUNTIME_REACHED)
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        # Never left "claimed" -- the existing retry/failure logic already
        # transitioned it (to "pending" for a retry or "failed" permanently).
        self.assertIn(data["tasks"][0]["status"], ("pending", "failed"))
        with open(grandchild_pid_file, encoding="utf-8") as f:
            grandchild_pid = int(f.read().strip())
        self.assertTrue(
            _wait_until_pid_dead(grandchild_pid),
            "a grandchild spawned by the timed-out fake claude must not survive the batch",
        )

    # -- 8: default consecutive-failure behavior (first failure stops batch) ----

    def test_default_consecutive_failures_stops_after_first_failure(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir, exit_code=1)
        _write_raw_queue(
            self.queue_path,
            [
                _task(task_id="t1", working_dir=working_dir, approved_root=working_dir),
                _task(task_id="t2", working_dir=working_dir, approved_root=working_dir),
            ],
        )
        config = self._config(claude_executable=claude_executable)

        result = ce.run_batch(config, max_tasks=5, env=_CLEAN_ENV)

        self.assertEqual(result.attempted_cycles, 1)
        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.final_consecutive_failures, 1)
        self.assertEqual(result.stop_reason, ce.BatchStopReason.MAX_CONSECUTIVE_FAILURES_REACHED)
        self.assertEqual(ce.batch_exit_code(result), 1)

    # -- 8b: a policy-rejected cycle reaching the consecutive-failure limit --
    #        is reported via the more specific POLICY_FAILURE stop reason.

    def test_policy_rejected_cycle_reports_policy_failure_stop_reason(self):
        working_dir = self._task_working_dir()
        outside = os.path.join(self.production_root, "escape")
        os.makedirs(outside)
        claude_executable = _write_fake_claude(self.fake_bin_dir)
        # working_dir/approved_root both point outside the approved root
        # (self.production_root, which is itself a forbidden_paths entry)
        # -- run_claude_executor()'s own working-directory policy check
        # rejects this before ever launching the fake claude, producing
        # ExecutorOutcome.POLICY_REJECTED for this cycle.
        _write_raw_queue(self.queue_path, [_task(working_dir=outside, approved_root=outside)])
        config = self._config(claude_executable=claude_executable)

        result = ce.run_batch(config, env=_CLEAN_ENV)

        self.assertEqual(result.attempted_cycles, 1)
        self.assertEqual(result.cycle_outcomes[0].executor_outcome, nsq.ExecutorOutcome.POLICY_REJECTED.value)
        self.assertEqual(result.stop_reason, ce.BatchStopReason.POLICY_FAILURE)
        self.assertEqual(ce.batch_exit_code(result), 1)

    # -- 9: consecutive-failure reset (failure, success, failure -> 1, 0, 1) ----

    def test_consecutive_failures_reset_on_a_success(self):
        working_dir = self._task_working_dir()
        fail_claude = _write_fake_claude(self.fake_bin_dir, name="fail-claude", exit_code=1)
        ok_claude = _write_fake_claude(self.fake_bin_dir, name="ok-claude", exit_code=0)

        # Route each task to a distinct fake claude via distinct queue.json
        # entries is not possible (one config -> one claude_executable), so
        # this test instead uses acceptance_command to control pass/fail
        # per task against a single, always-exit-0 fake claude -- the
        # Completion Invariant's transition outcome is what actually drives
        # done vs. failed, exactly as in production.
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    task_id="t1",
                    working_dir=working_dir,
                    approved_root=working_dir,
                    acceptance_command=[sys.executable, "-c", "import sys; sys.exit(1)"],
                ),
                _task(
                    task_id="t2",
                    working_dir=working_dir,
                    approved_root=working_dir,
                    acceptance_command=[sys.executable, "-c", "pass"],
                ),
                _task(
                    task_id="t3",
                    working_dir=working_dir,
                    approved_root=working_dir,
                    acceptance_command=[sys.executable, "-c", "import sys; sys.exit(1)"],
                ),
            ],
        )
        config = self._config(claude_executable=ok_claude)

        result = ce.run_batch(
            config, max_tasks=3, max_consecutive_failures=2, env=_CLEAN_ENV
        )

        self.assertEqual(result.attempted_cycles, 3)
        self.assertEqual([c.succeeded for c in result.cycle_outcomes], [False, True, False])
        self.assertEqual(result.done_count, 1)
        self.assertEqual(result.failed_count, 2)
        self.assertEqual(result.final_consecutive_failures, 1)
        self.assertEqual(result.stop_reason, ce.BatchStopReason.MAX_TASKS_REACHED)
        del fail_claude  # unused stand-in kept only to document intent above

    # -- 10: authentication failure before claim ---------------------------------

    def test_authentication_failure_stops_before_any_claim(self):
        working_dir = self._task_working_dir()
        logged_out_payload = dict(_DEFAULT_AUTH_PAYLOAD, loggedIn=False)
        claude_executable = _write_fake_claude(self.fake_bin_dir, auth_payload=logged_out_payload)
        _write_raw_queue(
            self.queue_path, [_task(working_dir=working_dir, approved_root=working_dir)]
        )
        config = self._config(claude_executable=claude_executable)
        before = _read_raw(self.queue_path)

        result = ce.run_batch(config, env=_CLEAN_ENV)

        self.assertEqual(result.attempted_cycles, 0)
        self.assertEqual(result.stop_reason, ce.BatchStopReason.AUTHENTICATION_FAILURE)
        self.assertEqual(ce.batch_exit_code(result), 1)
        after = _read_raw(self.queue_path)
        self.assertEqual(before, after, "an auth failure before claim must never touch the queue")

    # -- 11a: isolation failure before claim --------------------------------------

    def test_isolation_failure_stops_before_any_claim(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir)
        _write_raw_queue(
            self.queue_path, [_task(working_dir=working_dir, approved_root=working_dir)]
        )
        config = self._config(claude_executable=claude_executable)
        # The forbidden path existed at config-load time (isolation passed
        # then); removing it now reproduces exactly the real-world case this
        # milestone's per-cycle preflight re-check exists for -- an
        # isolation condition that changes between tasks must be caught
        # before the next claim, not assumed still valid.
        shutil.rmtree(self.production_root)

        result = ce.run_batch(config, env=_CLEAN_ENV)

        self.assertEqual(result.attempted_cycles, 0)
        self.assertEqual(result.stop_reason, ce.BatchStopReason.ISOLATION_FAILURE)
        self.assertEqual(ce.batch_exit_code(result), 1)

    # -- 11b: executable-integrity (preflight) failure before claim --------------

    def test_preflight_integrity_failure_stops_before_any_claim(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir)
        _write_raw_queue(
            self.queue_path, [_task(working_dir=working_dir, approved_root=working_dir)]
        )
        config = self._config(claude_executable=claude_executable)
        # Group-writable -- still fully able to answer `auth status` (so the
        # gate itself passes), but verify_claude_executable() must reject it
        # as an executable-integrity failure, still strictly before claim.
        os.chmod(claude_executable, 0o775)

        result = ce.run_batch(config, env=_CLEAN_ENV)

        self.assertEqual(result.attempted_cycles, 0)
        self.assertEqual(result.stop_reason, ce.BatchStopReason.PREFLIGHT_FAILURE)
        self.assertEqual(ce.batch_exit_code(result), 1)

    # -- 12: blocking internal error (malformed queue) ---------------------------

    def test_malformed_queue_is_a_blocking_internal_error(self):
        claude_executable = _write_fake_claude(self.fake_bin_dir)
        with open(self.queue_path, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json")
        config = self._config(claude_executable=claude_executable)

        result = ce.run_batch(config, env=_CLEAN_ENV)

        self.assertEqual(result.attempted_cycles, 0)
        self.assertEqual(result.stop_reason, ce.BatchStopReason.INTERNAL_ERROR)
        self.assertEqual(ce.batch_exit_code(result), 1)

    # -- 13: invalid limits are all rejected, explicitly, with no side effects ---

    def test_invalid_limits_are_all_rejected(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir)
        _write_raw_queue(
            self.queue_path, [_task(working_dir=working_dir, approved_root=working_dir)]
        )
        config = self._config(claude_executable=claude_executable)

        invalid_cases = [
            {"max_tasks": 0},
            {"max_tasks": -1},
            {"max_tasks": 1.5},
            {"max_tasks": True},
            {"max_tasks": ce.MAX_TASKS_HARD_CAP + 1},
            {"max_runtime_seconds": 0},
            {"max_runtime_seconds": -5},
            {"max_runtime_seconds": ce.MAX_RUNTIME_SECONDS_HARD_CAP + 1},
            {"max_runtime_seconds": 1.5},
            {"max_consecutive_failures": 0},
            {"max_consecutive_failures": -1},
            {"max_tasks": 2, "max_consecutive_failures": 3},
        ]
        for overrides in invalid_cases:
            with self.subTest(overrides=overrides):
                kwargs = {
                    "max_tasks": ce.DEFAULT_MAX_TASKS,
                    "max_runtime_seconds": ce.DEFAULT_MAX_RUNTIME_SECONDS,
                    "max_consecutive_failures": ce.DEFAULT_MAX_CONSECUTIVE_FAILURES,
                }
                kwargs.update(overrides)
                before = _read_raw(self.queue_path)
                with self.assertRaises(ce.ConfigError):
                    ce.run_batch(config, env=_CLEAN_ENV, **kwargs)
                after = _read_raw(self.queue_path)
                self.assertEqual(before, after, "invalid limits must be rejected before touching the queue")

    # -- 14: no polling -- no sleep call anywhere, empty queue returns immediately

    def test_empty_queue_never_sleeps(self):
        _write_raw_queue(self.queue_path, [])
        config = self._config()

        with mock.patch("time.sleep", side_effect=AssertionError("run_batch must never sleep")):
            result = ce.run_batch(config, env=_CLEAN_ENV)

        self.assertEqual(result.stop_reason, ce.BatchStopReason.QUEUE_EMPTY)

    # -- 15: structured report determinism ----------------------------------------

    def test_batch_result_structure_is_deterministic_across_equivalent_runs(self):
        def _fresh_queue():
            working_dir = self._task_working_dir()
            _write_raw_queue(
                self.queue_path,
                [
                    _task(task_id="t1", working_dir=working_dir, approved_root=working_dir),
                    _task(task_id="t2", working_dir=working_dir, approved_root=working_dir),
                ],
            )

        claude_executable = _write_fake_claude(self.fake_bin_dir)
        config = self._config(claude_executable=claude_executable)

        _fresh_queue()
        result1 = ce.run_batch(config, max_tasks=5, env=_CLEAN_ENV)
        _fresh_queue()
        result2 = ce.run_batch(config, max_tasks=5, env=_CLEAN_ENV)

        d1 = result1.to_json_dict()
        d2 = result2.to_json_dict()
        for key in ("started_at", "ended_at", "elapsed_seconds", "message"):
            del d1[key], d2[key]
        for outcome in d1["cycle_outcomes"] + d2["cycle_outcomes"]:
            outcome.pop("evidence_excerpt", None)
        self.assertEqual(d1, d2)
        # Required keys are all present, per the Milestone 7D structured
        # report requirement.
        for key in (
            "started_at",
            "ended_at",
            "elapsed_seconds",
            "limits",
            "attempted_cycles",
            "done_count",
            "failed_count",
            "final_consecutive_failures",
            "task_ids_attempted",
            "cycle_outcomes",
            "stop_reason",
            "queue_observed_empty",
        ):
            self.assertIn(key, result1.to_json_dict())

    # -- 16: evidence sanitization --------------------------------------------------

    def test_evidence_is_sanitized_in_the_batch_result(self):
        working_dir = self._task_working_dir()
        fake_token = "sk-ant-api03-" + ("a" * 40)
        claude_executable = _write_fake_claude(
            self.fake_bin_dir,
            exit_code=1,
            stderr_text=f"API Error: 401 Unauthorized. Bearer {fake_token} rejected.",
        )
        _write_raw_queue(
            self.queue_path, [_task(working_dir=working_dir, approved_root=working_dir)]
        )
        config = self._config(claude_executable=claude_executable)

        result = ce.run_batch(config, env=_CLEAN_ENV)

        serialized = json.dumps(result.to_json_dict())
        self.assertNotIn(fake_token, serialized)
        self.assertIn("[REDACTED]", result.cycle_outcomes[0].evidence_excerpt or "")

    # -- 17: tool-policy regression at the batch level (Bash unavailable) --------

    def test_batch_cycle_argv_never_includes_bash(self):
        working_dir = self._task_working_dir()
        dump_path = os.path.join(self.tmpdir, "dump.jsonl")
        claude_executable = _write_fake_claude(self.fake_bin_dir, dump_path=dump_path)
        _write_raw_queue(
            self.queue_path, [_task(working_dir=working_dir, approved_root=working_dir)]
        )
        config = self._config(claude_executable=claude_executable)

        ce.run_batch(config, env=_CLEAN_ENV)

        with open(dump_path, encoding="utf-8") as f:
            dumped = json.loads(f.readline())
        argv = dumped["argv"]
        tools_index = argv.index("--tools")
        allowed_index = argv.index("--allowedTools")
        self.assertEqual(argv[tools_index + 1], "Read,Write,Edit,Glob,Grep")
        self.assertEqual(argv[allowed_index + 1], "Read,Write,Edit,Glob,Grep")

    # -- 18: existing run-one CLI regression --------------------------------------

    def test_run_one_cli_subcommand_still_behaves_as_before(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir)
        _write_raw_queue(
            self.queue_path, [_task(working_dir=working_dir, approved_root=working_dir)]
        )
        config_path = os.path.join(self.tmpdir, "config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "queue_path": self.queue_path,
                    "report_dir": self.report_dir,
                    "nightshift_root": self.nightshift_root,
                    "forbidden_paths": [self.production_root],
                    "claude_executable": claude_executable,
                },
                f,
            )

        buf = io.StringIO()
        with mock.patch.dict(os.environ, _CLEAN_ENV, clear=True):
            with redirect_stdout(buf):
                exit_code = ce.main(["run-one", "--config", config_path])

        self.assertEqual(exit_code, 0)
        printed = json.loads(buf.getvalue())
        self.assertTrue(printed["ran"])

    # -- 19: fresh-process behavior -- one process per claimed task --------------

    def test_each_claimed_task_gets_a_distinct_fresh_process(self):
        working_dir = self._task_working_dir()
        dump_path = os.path.join(self.tmpdir, "dump.jsonl")
        claude_executable = _write_fake_claude(self.fake_bin_dir, dump_path=dump_path)
        _write_raw_queue(
            self.queue_path,
            [
                _task(task_id="t1", working_dir=working_dir, approved_root=working_dir),
                _task(task_id="t2", working_dir=working_dir, approved_root=working_dir),
            ],
        )
        config = self._config(claude_executable=claude_executable)

        ce.run_batch(config, max_tasks=5, env=_CLEAN_ENV)

        with open(dump_path, encoding="utf-8") as f:
            dumped = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(dumped), 2)
        self.assertNotEqual(dumped[0]["pid"], dumped[1]["pid"])

    # -- 20: run-batch CLI exit-code contract --------------------------------------

    def test_run_batch_cli_exit_codes(self):
        working_dir = self._task_working_dir()

        def _run(claude_executable, expected_exit, **cli_overrides):
            _write_raw_queue(
                self.queue_path, [_task(working_dir=working_dir, approved_root=working_dir)]
            )
            config_path = os.path.join(self.tmpdir, "config.json")
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "queue_path": self.queue_path,
                        "report_dir": self.report_dir,
                        "nightshift_root": self.nightshift_root,
                        "forbidden_paths": [self.production_root],
                        "claude_executable": claude_executable,
                    },
                    f,
                )
            argv = ["run-batch", "--config", config_path]
            for key, value in cli_overrides.items():
                argv += [f"--{key.replace('_', '-')}", str(value)]
            buf = io.StringIO()
            with mock.patch.dict(os.environ, _CLEAN_ENV, clear=True):
                with redirect_stdout(buf):
                    exit_code = ce.main(argv)
            self.assertEqual(exit_code, expected_exit, buf.getvalue())

        # Success: queue drains fully, no failures -> exit 0.
        ok_claude = _write_fake_claude(self.fake_bin_dir, name="ok")
        _run(ok_claude, 0, max_tasks=3)

        # Stopped within bounds but queue not proven drained -> exit 2.
        _run(ok_claude, 2, max_tasks=1)

        # A task-cycle failure -> exit 1.
        failing_claude = _write_fake_claude(self.fake_bin_dir, name="failing", exit_code=1)
        _run(failing_claude, 1, max_tasks=3)


def _read_raw(path):
    with open(path, "rb") as f:
        return f.read()


if __name__ == "__main__":
    unittest.main()
