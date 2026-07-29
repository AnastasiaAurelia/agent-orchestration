"""Tests for nightshift.runtime.claude_executor.

Uses only fake local executables standing in for `claude` -- never invokes
a real Claude Code session, never touches this machine's actual Claude
authentication or credentials. Every test operates inside a fresh
tempfile.mkdtemp() directory.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from nightshift.runtime import claude_executor as ce  # noqa: E402
from nightshift.runtime import queue as nsq  # noqa: E402

_DEFAULT_HELP_TEXT = (
    "Usage: claude [options] [prompt]\n"
    "  -p, --print\n"
    "  --tools <tools...>\n"
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

# A clean, minimal environment for the preflight's sensitive-variable check.
# Without this, a real, benign variable already present in the developer's
# own shell (e.g. SSH_AUTH_SOCK from a normal SSH agent) would make the
# preflight correctly fail closed -- exactly as designed -- for a reason
# that has nothing to do with whatever this test is actually exercising.
# That fail-closed behavior itself is already covered directly in
# test_auth_preflight.py; these tests use a clean env so they test one
# thing at a time.
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

# The "real" -p invocation.
dump_path = {dump_path!r}
if dump_path:
    with open(dump_path, "w") as f:
        json.dump({{"argv": argv, "env": dict(os.environ), "cwd": os.getcwd()}}, f)

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
    """Write a fake `claude` stand-in that dispatches on argv shape.

    Responds to --version, --help, and `auth status --json` the way the
    real CLI does (configurably), and to the real -p invocation with
    whatever behavior the test needs -- exit code, output text, a sleep
    (for timeout tests), a grandchild process (for cleanup tests), and/or
    dumping its own argv/env/cwd to `dump_path` for the test to inspect.
    Uses this exact interpreter as its shebang target (not `/usr/bin/env
    python3`) so it never depends on PATH resolution succeeding inside
    whatever sanitized environment the code under test builds.
    """
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
    title="Bounded smoke objective",
    working_dir=None,
    approved_root=None,
    attempt_count=0,
    max_attempts=3,
    status="pending",
    claimed_pid=None,
    claimed_at=None,
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
        "claimed_pid": claimed_pid,
        "claimed_at": claimed_at,
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


class ClaudeExecutorTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nightshift-claude-executor-test-")
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
        working_dir = os.path.join(self.nightshift_root, "smoke")
        os.makedirs(working_dir, exist_ok=True)
        return working_dir

    # -- 1/2/3: preflight gating ------------------------------------------------

    def test_passing_preflight_allows_the_adapter_to_proceed(self):
        working_dir = self._task_working_dir()
        _write_raw_queue(self.queue_path, [_task(working_dir=working_dir, approved_root=working_dir)])
        config = self._config()

        result = ce.run_one(config, env=_CLEAN_ENV)

        self.assertTrue(result.ran)
        self.assertEqual(result.claim.outcome, nsq.ClaimOutcome.CLAIMED)

    def test_failed_preflight_prevents_claim_and_prevents_spawn(self):
        working_dir = self._task_working_dir()
        _write_raw_queue(self.queue_path, [_task(working_dir=working_dir, approved_root=working_dir)])
        logged_out_payload = dict(_DEFAULT_AUTH_PAYLOAD, loggedIn=False)
        claude_executable = _write_fake_claude(self.fake_bin_dir, auth_payload=logged_out_payload)
        config = self._config(claude_executable=claude_executable)
        before = _read_raw(self.queue_path)

        result = ce.run_one(config, env=_CLEAN_ENV)

        self.assertFalse(result.ran)
        self.assertIsNone(result.claim)
        after = _read_raw(self.queue_path)
        self.assertEqual(before, after, "a failed preflight must never touch the queue")

    def test_preflight_failure_does_not_increment_attempt_count(self):
        working_dir = self._task_working_dir()
        _write_raw_queue(
            self.queue_path, [_task(working_dir=working_dir, approved_root=working_dir, attempt_count=0)]
        )
        logged_out_payload = dict(_DEFAULT_AUTH_PAYLOAD, loggedIn=False)
        claude_executable = _write_fake_claude(self.fake_bin_dir, auth_payload=logged_out_payload)
        config = self._config(claude_executable=claude_executable)

        ce.run_one(config, env=_CLEAN_ENV)

        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        self.assertEqual(data["tasks"][0]["attempt_count"], 0)

    # -- 4/5/6: executable integrity -----------------------------------------

    def test_explicit_claude_executable_is_required(self):
        for bad_value in (None, "", 123):
            with self.subTest(bad_value=bad_value):
                result = ce.verify_claude_executable(bad_value)
                self.assertFalse(result.ok)

    def test_relative_claude_executable_is_rejected(self):
        result = ce.verify_claude_executable("claude")
        self.assertFalse(result.ok)
        self.assertIn("absolute", result.reason)

        with self.assertRaises(ce.ConfigError):
            self._config(claude_executable="claude")

    def test_missing_executable_is_rejected(self):
        missing = os.path.join(self.fake_bin_dir, "does-not-exist")
        result = ce.verify_claude_executable(missing)
        self.assertFalse(result.ok)
        self.assertIn("does not exist", result.reason)

    def test_group_writable_executable_is_rejected(self):
        path = _write_fake_claude(self.fake_bin_dir)
        os.chmod(path, 0o775)
        result = ce.verify_claude_executable(path)
        self.assertFalse(result.ok)
        self.assertIn("group-writable", result.reason)

    def test_world_writable_executable_is_rejected(self):
        path = _write_fake_claude(self.fake_bin_dir)
        os.chmod(path, 0o757)
        result = ce.verify_claude_executable(path)
        self.assertFalse(result.ok)
        self.assertIn("world-writable", result.reason)

    def test_canonical_executable_mismatch_is_rejected(self):
        real_path = _write_fake_claude(self.fake_bin_dir, name="real-claude")
        symlink_path = os.path.join(self.fake_bin_dir, "claude-symlink")
        os.symlink(real_path, symlink_path)

        result = ce.verify_claude_executable(symlink_path)

        self.assertFalse(result.ok)
        self.assertIn("canonical", result.reason)

    def test_verified_executable_records_only_safe_metadata(self):
        path = _write_fake_claude(self.fake_bin_dir)
        result = ce.verify_claude_executable(path)

        self.assertTrue(result.ok)
        self.assertEqual(result.canonical_path, path)
        self.assertIsInstance(result.owner_uid, int)
        self.assertTrue(result.mode_octal.startswith("0o"))
        self.assertIn("Fake Claude", result.version)

    # -- 10: CLI capability verification ---------------------------------------

    def test_unsupported_required_cli_capability_fails_closed(self):
        incomplete_help = "Usage: claude [options]\n  -p, --print\n  --tools <tools...>\n"
        claude_executable = _write_fake_claude(self.fake_bin_dir, help_text=incomplete_help)

        ok, missing, error = ce.verify_cli_capabilities(claude_executable)

        self.assertFalse(ok)
        self.assertIn("--permission-mode", missing)

        # And the integration path fails closed rather than proceeding.
        working_dir = self._task_working_dir()
        _write_raw_queue(self.queue_path, [_task(working_dir=working_dir, approved_root=working_dir)])
        config = self._config(claude_executable=claude_executable)
        result = ce.run_one(config, env=_CLEAN_ENV)
        self.assertFalse(result.ran)
        self.assertIsNone(result.claim)

    # -- 11/12/13/14: argv, environment, working directory ----------------------

    def test_fake_claude_receives_the_prompt_as_one_safe_argument(self):
        working_dir = self._task_working_dir()
        dump_path = os.path.join(self.tmpdir, "dump.json")
        claude_executable = _write_fake_claude(self.fake_bin_dir, dump_path=dump_path)
        task = _task(working_dir=working_dir, approved_root=working_dir, title="Fix the thing; do not `rm -rf`")
        config = self._config(claude_executable=claude_executable)

        result = ce.run_claude_executor(task, config, env=_CLEAN_ENV)

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.COMPLETED)
        with open(dump_path, encoding="utf-8") as f:
            dumped = json.load(f)
        argv = dumped["argv"]
        # The prompt is the final argv element, containing the FULL
        # objective text verbatim, intact, as exactly one element -- not
        # split apart, not shell re-tokenized (the semicolon/backtick above
        # would have been meaningful to a shell; here they are inert text
        # inside a single argument).
        # dumped["argv"] is sys.argv[1:] from the fake script's own point of
        # view (it never sees its own executable path as an element), so it
        # is exactly one shorter than the full built argv (which does
        # include the executable path as element 0).
        self.assertEqual(len(argv), len(ce._build_claude_argv("x", "placeholder")) - 1)
        self.assertIn("Bounded objective: Fix the thing; do not `rm -rf`", argv[-1])
        self.assertNotIn("Fix the thing", argv[-2], "the objective must not have leaked into a separate argv element")

    def test_fake_claude_receives_only_the_sanitized_environment(self):
        working_dir = self._task_working_dir()
        dump_path = os.path.join(self.tmpdir, "dump.json")
        claude_executable = _write_fake_claude(self.fake_bin_dir, dump_path=dump_path)
        task = _task(working_dir=working_dir, approved_root=working_dir)
        config = self._config(claude_executable=claude_executable)

        os.environ["NIGHTSHIFT_TEST_NOT_ALLOWLISTED"] = "should-not-reach-child"
        try:
            result = ce.run_claude_executor(task, config, env=_CLEAN_ENV)
        finally:
            del os.environ["NIGHTSHIFT_TEST_NOT_ALLOWLISTED"]

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.COMPLETED)
        with open(dump_path, encoding="utf-8") as f:
            dumped = json.load(f)
        self.assertNotIn("NIGHTSHIFT_TEST_NOT_ALLOWLISTED", dumped["env"])
        self.assertIn("PATH", dumped["env"])

    def test_sensitive_variables_do_not_reach_the_fake_claude_process(self):
        working_dir = self._task_working_dir()
        dump_path = os.path.join(self.tmpdir, "dump.json")
        claude_executable = _write_fake_claude(self.fake_bin_dir, dump_path=dump_path)
        task = _task(working_dir=working_dir, approved_root=working_dir)
        config = self._config(claude_executable=claude_executable)

        os.environ["ANTHROPIC_API_KEY"] = "sk-should-never-reach-child"
        try:
            result = ce.run_claude_executor(task, config, env=_CLEAN_ENV)
        finally:
            del os.environ["ANTHROPIC_API_KEY"]

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.COMPLETED)
        with open(dump_path, encoding="utf-8") as f:
            dumped = json.load(f)
        self.assertNotIn("ANTHROPIC_API_KEY", dumped["env"])
        evidence = json.dumps(result.to_json_dict())
        self.assertNotIn("sk-should-never-reach-child", evidence)

    def test_fake_claude_runs_in_the_requested_disposable_working_directory(self):
        working_dir = self._task_working_dir()
        dump_path = os.path.join(self.tmpdir, "dump.json")
        claude_executable = _write_fake_claude(self.fake_bin_dir, dump_path=dump_path)
        task = _task(working_dir=working_dir, approved_root=working_dir)
        config = self._config(claude_executable=claude_executable)

        result = ce.run_claude_executor(task, config, env=_CLEAN_ENV)

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.COMPLETED)
        with open(dump_path, encoding="utf-8") as f:
            dumped = json.load(f)
        self.assertEqual(os.path.realpath(dumped["cwd"]), os.path.realpath(working_dir))

    # -- 15/16: working-directory boundary ---------------------------------------

    def test_working_directory_outside_approved_root_is_rejected(self):
        outside = os.path.join(self.production_root, "escape")
        os.makedirs(outside)
        task = _task(working_dir=outside, approved_root=outside)
        config = self._config()

        result = ce.run_claude_executor(task, config, env=_CLEAN_ENV)

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.POLICY_REJECTED)
        self.assertIn("working_dir rejected", result.message)

    def test_symlink_escape_working_directory_is_rejected(self):
        escape_link = os.path.join(self.nightshift_root, "escape-link")
        os.symlink(self.production_root, escape_link)
        task = _task(working_dir=escape_link, approved_root=self.nightshift_root)
        config = self._config()

        result = ce.run_claude_executor(task, config, env=_CLEAN_ENV)

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.POLICY_REJECTED)

    # -- 17/18/19/20: transition outcomes ----------------------------------------

    def test_successful_fake_claude_plus_passing_acceptance_marks_task_done(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir)
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    working_dir=working_dir,
                    approved_root=working_dir,
                    acceptance_command=[sys.executable, "-c", "pass"],
                )
            ],
        )
        config = self._config(claude_executable=claude_executable)
        nsq.claim_next(self.queue_path, claimant_pid=os.getpid())

        task_run = ce.run_claude_task(config, "t1", env=_CLEAN_ENV)

        self.assertEqual(task_run.executor.outcome, nsq.ExecutorOutcome.COMPLETED)
        self.assertTrue(task_run.acceptance_run.acceptance.passed)
        self.assertEqual(task_run.acceptance_run.transition.outcome, nsq.TransitionOutcome.DONE)

    def test_successful_fake_claude_plus_failing_acceptance_enters_retry_flow(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir)
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    working_dir=working_dir,
                    approved_root=working_dir,
                    max_attempts=3,
                    acceptance_command=[sys.executable, "-c", "import sys; sys.exit(1)"],
                )
            ],
        )
        config = self._config(claude_executable=claude_executable)
        nsq.claim_next(self.queue_path, claimant_pid=os.getpid())

        task_run = ce.run_claude_task(config, "t1", env=_CLEAN_ENV)

        self.assertEqual(task_run.acceptance_run.transition.outcome, nsq.TransitionOutcome.REQUEUED)

    def test_non_zero_fake_claude_exit_still_runs_acceptance_and_can_enter_retry_flow(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir, exit_code=1)
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    working_dir=working_dir,
                    approved_root=working_dir,
                    max_attempts=3,
                    acceptance_command=[sys.executable, "-c", "import sys; sys.exit(1)"],
                )
            ],
        )
        config = self._config(claude_executable=claude_executable)
        nsq.claim_next(self.queue_path, claimant_pid=os.getpid())

        task_run = ce.run_claude_task(config, "t1", env=_CLEAN_ENV)

        self.assertEqual(task_run.executor.outcome, nsq.ExecutorOutcome.COMPLETED)
        self.assertEqual(task_run.executor.exit_code, 1)
        self.assertEqual(task_run.acceptance_run.transition.outcome, nsq.TransitionOutcome.REQUEUED)

    def test_fake_claude_timeout_enters_retry_flow(self):
        # A trivially-passing acceptance command would mark this "done"
        # regardless of the executor's own outcome (acceptance alone
        # decides -- see test_claude_final_text_cannot_override_failed_
        # acceptance for that principle tested directly). This test needs
        # an acceptance command that actually fails, so a timed-out
        # executor is provably what drives the retry here, not a
        # coincidence of the fixture's default.
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir, sleep_seconds=30)
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    working_dir=working_dir,
                    approved_root=working_dir,
                    max_attempts=3,
                    acceptance_command=[sys.executable, "-c", "import sys; sys.exit(1)"],
                )
            ],
        )
        config = self._config(claude_executable=claude_executable, claude_timeout_seconds=1)
        nsq.claim_next(self.queue_path, claimant_pid=os.getpid())

        task_run = ce.run_claude_task(config, "t1", env=_CLEAN_ENV)

        self.assertEqual(task_run.executor.outcome, nsq.ExecutorOutcome.TIMED_OUT)
        self.assertEqual(task_run.acceptance_run.transition.outcome, nsq.TransitionOutcome.REQUEUED)

    # -- 21: child process cleanup ------------------------------------------------

    def test_child_process_spawned_by_fake_claude_is_cleaned_up_after_timeout(self):
        working_dir = self._task_working_dir()
        grandchild_pid_file = os.path.join(self.tmpdir, "grandchild_pid.txt")
        claude_executable = _write_fake_claude(
            self.fake_bin_dir, sleep_seconds=30, spawn_grandchild_pid_file=grandchild_pid_file
        )
        task = _task(working_dir=working_dir, approved_root=working_dir)
        config = self._config(claude_executable=claude_executable, claude_timeout_seconds=1)

        result = ce.run_claude_executor(task, config, env=_CLEAN_ENV)

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.TIMED_OUT)
        self.assertFalse(nsq._is_pid_alive(result.pid), "the direct fake-claude process must be dead")
        with open(grandchild_pid_file, encoding="utf-8") as f:
            grandchild_pid = int(f.read().strip())
        self.assertTrue(
            _wait_until_pid_dead(grandchild_pid),
            "a grandchild spawned by fake claude must also die from the process-group kill",
        )

    # -- 22: stdout/stderr capture ------------------------------------------------

    def test_claude_stdout_and_stderr_are_captured(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(
            self.fake_bin_dir, stdout_text="fake claude stdout line", stderr_text="fake claude stderr line"
        )
        task = _task(working_dir=working_dir, approved_root=working_dir)
        config = self._config(claude_executable=claude_executable)

        result = ce.run_claude_executor(task, config, env=_CLEAN_ENV)

        self.assertEqual(result.outcome, nsq.ExecutorOutcome.COMPLETED)
        self.assertIn("fake claude stdout line", result.stdout)
        self.assertIn("fake claude stderr line", result.stderr)

    # -- 23/24: no self-reported success ------------------------------------------

    def test_claude_final_text_cannot_override_failed_acceptance(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(
            self.fake_bin_dir, stdout_text="SUCCESS! The task is complete and all tests pass."
        )
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    working_dir=working_dir,
                    approved_root=working_dir,
                    max_attempts=3,
                    acceptance_command=[sys.executable, "-c", "import sys; sys.exit(1)"],
                )
            ],
        )
        config = self._config(claude_executable=claude_executable)
        nsq.claim_next(self.queue_path, claimant_pid=os.getpid())

        task_run = ce.run_claude_task(config, "t1", env=_CLEAN_ENV)

        self.assertIn("SUCCESS", task_run.executor.stdout)
        self.assertNotEqual(task_run.acceptance_run.transition.outcome, nsq.TransitionOutcome.DONE)

    def test_passing_acceptance_cannot_mask_an_executor_that_was_never_launched(self):
        working_dir = self._task_working_dir()
        missing_executable = os.path.join(self.fake_bin_dir, "does-not-exist")
        _write_raw_queue(
            self.queue_path,
            [
                _task(
                    working_dir=working_dir,
                    approved_root=working_dir,
                    max_attempts=3,
                    acceptance_command=[sys.executable, "-c", "pass"],  # would trivially pass
                )
            ],
        )
        config = self._config(claude_executable=missing_executable)
        nsq.claim_next(self.queue_path, claimant_pid=os.getpid())

        task_run = ce.run_claude_task(config, "t1", env=_CLEAN_ENV)

        self.assertEqual(task_run.executor.outcome, nsq.ExecutorOutcome.MISSING_EXECUTABLE)
        self.assertEqual(task_run.acceptance_run.acceptance.message, "acceptance skipped: the executor never ran")
        self.assertNotEqual(task_run.acceptance_run.transition.outcome, nsq.TransitionOutcome.DONE)
        self.assertEqual(task_run.acceptance_run.transition.outcome, nsq.TransitionOutcome.REQUEUED)

    # -- 25/26: run-one cycle bounds -----------------------------------------------

    def test_run_one_processes_no_more_than_one_task_even_when_several_are_pending(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir)
        _write_raw_queue(
            self.queue_path,
            [
                _task(task_id="t1", working_dir=working_dir, approved_root=working_dir),
                _task(task_id="t2", working_dir=working_dir, approved_root=working_dir),
                _task(task_id="t3", working_dir=working_dir, approved_root=working_dir),
            ],
        )
        config = self._config(claude_executable=claude_executable)

        result = ce.run_one(config, env=_CLEAN_ENV)

        self.assertTrue(result.ran)
        with open(self.queue_path, encoding="utf-8") as f:
            data = nsq.validate_queue(json.load(f))
        statuses = [t["status"] for t in data["tasks"]]
        self.assertEqual(statuses.count("done"), 1)
        self.assertEqual(statuses.count("pending"), 2)

    def test_empty_queue_returns_cleanly_without_invoking_claude(self):
        dump_path = os.path.join(self.tmpdir, "dump.json")
        claude_executable = _write_fake_claude(self.fake_bin_dir, dump_path=dump_path)
        _write_raw_queue(self.queue_path, [])
        config = self._config(claude_executable=claude_executable)

        result = ce.run_one(config, env=_CLEAN_ENV)

        self.assertFalse(result.ran)
        self.assertEqual(result.claim.outcome, nsq.ClaimOutcome.NO_PENDING_TASK)
        self.assertFalse(os.path.exists(dump_path), "claude must never be invoked with -p when there is no task")

    # -- 27: report reflects real evidence ------------------------------------------

    def test_report_generation_reflects_the_real_transition_evidence(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir)
        run_log_path = os.path.join(self.tmpdir, "run_log.jsonl")
        _write_raw_queue(
            self.queue_path, [_task(working_dir=working_dir, approved_root=working_dir)]
        )
        config = self._config(claude_executable=claude_executable, run_log_path=run_log_path)

        result = ce.run_one(config, env=_CLEAN_ENV)

        self.assertTrue(result.ran)
        self.assertTrue(os.path.exists(result.report_path))
        with open(result.report_path, encoding="utf-8") as f:
            report_text = f.read()
        self.assertIn("Done: 1", report_text)
        self.assertIn("`t1`", report_text)

    # -- 28 is the full-suite run in the completion checklist, not a single test here.

    # -- 29: repeated execution does not leak state -----------------------------------

    def test_repeated_execution_does_not_leak_processes_or_temp_files(self):
        working_dir = self._task_working_dir()
        claude_executable = _write_fake_claude(self.fake_bin_dir)
        _write_raw_queue(
            self.queue_path,
            [_task(task_id=f"t{i}", working_dir=working_dir, approved_root=working_dir) for i in range(3)],
        )
        config = self._config(claude_executable=claude_executable)

        before_listing = set(os.listdir(self.tmpdir))
        for _ in range(3):
            result = ce.run_one(config, env=_CLEAN_ENV)
            self.assertTrue(result.ran)
        after_listing = set(os.listdir(self.tmpdir))

        # Only the expected, already-accounted-for artifacts may appear --
        # no stray leftover temp files from repeated cycles.
        unexpected = after_listing - before_listing
        for name in unexpected:
            self.assertIn(name, ("queue.json.lock",), f"unexpected leftover artifact: {name}")


def _read_raw(path):
    with open(path, "rb") as f:
        return f.read()


def _wait_until_pid_dead(pid, timeout=2.0):
    import time

    deadline = time.monotonic() + timeout
    while nsq._is_pid_alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


if __name__ == "__main__":
    unittest.main()
