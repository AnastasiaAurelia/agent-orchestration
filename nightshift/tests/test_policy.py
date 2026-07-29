"""Focused unit tests for nightshift.runtime.policy.

These test the policy module in isolation (executable resolution, deny
rules, environment allowlist, working-directory containment) without going
through the queue/executor/acceptance machinery -- see test_queue.py for
the integration tests proving policy rejection actually prevents a launch
and feeds the retry/failure state machine.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from nightshift.runtime import policy  # noqa: E402


class PolicyTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nightshift-policy-test-")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- Executable resolution -----------------------------------------------

    def test_resolve_executable_finds_a_real_binary_on_path(self):
        resolved = policy.resolve_executable(sys.executable, self.tmpdir)
        self.assertIsNotNone(resolved)
        self.assertTrue(os.path.isabs(resolved))
        self.assertTrue(os.path.isfile(resolved))

    def test_resolve_executable_returns_none_for_nonexistent_command(self):
        resolved = policy.resolve_executable("nightshift-fixture-does-not-exist", self.tmpdir)
        self.assertIsNone(resolved)

    # -- Minimal allowed command ----------------------------------------------

    def test_minimal_allowed_local_command_is_allowed(self):
        decision = policy.check_command(
            [sys.executable, "-c", "pass"], self.tmpdir, policy.EXECUTOR_POLICY
        )
        self.assertEqual(decision.outcome, policy.PolicyOutcome.ALLOWED)
        self.assertIsNotNone(decision.resolved_executable)

    # -- Executor vs. acceptance policies differ ------------------------------

    def test_executor_and_acceptance_policies_differ_on_plain_git(self):
        argv = ["git", "status"]

        executor_decision = policy.check_command(argv, self.tmpdir, policy.EXECUTOR_POLICY)
        acceptance_decision = policy.check_command(argv, self.tmpdir, policy.ACCEPTANCE_POLICY)

        self.assertEqual(executor_decision.outcome, policy.PolicyOutcome.ALLOWED)
        self.assertEqual(acceptance_decision.outcome, policy.PolicyOutcome.REJECTED)
        self.assertIn("git", acceptance_decision.reason.lower())

    # -- Shell interpreters ----------------------------------------------------

    def test_installed_shell_interpreters_are_rejected(self):
        for shell in ("sh", "bash", "dash"):
            if shutil.which(shell) is None:
                continue
            with self.subTest(shell=shell):
                decision = policy.check_command(
                    [shell, "-c", "echo hi"], self.tmpdir, policy.EXECUTOR_POLICY
                )
                self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)
                self.assertIn("shell interpreter", decision.reason)

    def test_all_enumerated_shell_interpreters_are_never_allowed(self):
        for shell in ("sh", "bash", "zsh", "fish", "dash", "powershell", "pwsh"):
            with self.subTest(shell=shell):
                decision = policy.check_command(
                    [shell, "-c", "echo hi"], self.tmpdir, policy.EXECUTOR_POLICY
                )
                self.assertEqual(
                    decision.outcome,
                    policy.PolicyOutcome.REJECTED,
                    f"{shell!r} must never be allowed, whether by explicit deny or by "
                    "being absent from this system entirely",
                )

    # -- Enumerated dangerous operations ---------------------------------------

    def test_git_push_rejected(self):
        for argv in (["git", "push"], ["git", "push", "--force"], ["git", "push", "origin", "main"]):
            with self.subTest(argv=argv):
                decision = policy.check_command(argv, self.tmpdir, policy.EXECUTOR_POLICY)
                self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)

    def test_git_reset_hard_and_clean_rejected(self):
        decision = policy.check_command(
            ["git", "reset", "--hard"], self.tmpdir, policy.EXECUTOR_POLICY
        )
        self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)
        decision = policy.check_command(["git", "clean", "-fd"], self.tmpdir, policy.EXECUTOR_POLICY)
        self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)

    def test_package_installation_rejected(self):
        cases = [
            ["pip", "install", "requests"],
            ["pip3", "install", "requests"],
            ["uv", "add", "requests"],
            ["npm", "install"],
            ["pnpm", "install"],
            ["yarn", "add", "left-pad"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                decision = policy.check_command(argv, self.tmpdir, policy.EXECUTOR_POLICY)
                self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)

    def test_network_utilities_rejected(self):
        for argv in (
            ["curl", "https://example.com"],
            ["wget", "https://example.com"],
            ["nc", "example.com", "80"],
            ["netcat", "example.com", "80"],
            ["ssh", "user@example.com"],
            ["scp", "file", "user@example.com:/tmp"],
        ):
            with self.subTest(argv=argv):
                decision = policy.check_command(argv, self.tmpdir, policy.EXECUTOR_POLICY)
                self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)

    def test_rsync_to_remote_target_rejected_but_local_rsync_not_specifically_denied(self):
        remote_decision = policy.check_command(
            ["rsync", "-a", "src/", "user@host:/dest/"], self.tmpdir, policy.EXECUTOR_POLICY
        )
        self.assertEqual(remote_decision.outcome, policy.PolicyOutcome.REJECTED)

    def test_sudo_and_su_rejected(self):
        for argv in (["sudo", "ls"], ["su", "-c", "ls"]):
            with self.subTest(argv=argv):
                decision = policy.check_command(argv, self.tmpdir, policy.EXECUTOR_POLICY)
                self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)

    def test_deployment_and_orchestration_utilities_rejected(self):
        for argv in (
            ["docker", "run", "x"],
            ["kubectl", "apply", "-f", "x.yaml"],
            ["systemctl", "restart", "x"],
            ["crontab", "-l"],
        ):
            with self.subTest(argv=argv):
                decision = policy.check_command(argv, self.tmpdir, policy.EXECUTOR_POLICY)
                self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)

    def test_apt_rejected(self):
        for argv in (["apt", "install", "x"], ["apt-get", "install", "x"]):
            with self.subTest(argv=argv):
                decision = policy.check_command(argv, self.tmpdir, policy.EXECUTOR_POLICY)
                self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)

    def test_rm_rejected(self):
        decision = policy.check_command(["rm", "-rf", "x"], self.tmpdir, policy.EXECUTOR_POLICY)
        self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)

    def test_gh_pr_merge_and_gh_api_write_rejected(self):
        decision = policy.check_command(
            ["gh", "pr", "merge", "1"], self.tmpdir, policy.EXECUTOR_POLICY
        )
        self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)

        decision = policy.check_command(
            ["gh", "api", "-X", "POST", "repos/x/y/issues"], self.tmpdir, policy.EXECUTOR_POLICY
        )
        self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)

        # A read-only gh api call (no -X/--method, defaults to GET) is not
        # one of the enumerated write operations.
        decision = policy.check_command(
            ["gh", "api", "repos/x/y"], self.tmpdir, policy.EXECUTOR_POLICY
        )
        self.assertEqual(decision.outcome, policy.PolicyOutcome.ALLOWED)

    def test_env_wrapper_rejected(self):
        decision = policy.check_command(
            ["env", "FOO=bar", sys.executable, "-c", "pass"], self.tmpdir, policy.EXECUTOR_POLICY
        )
        self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)

    # -- Environment allowlist --------------------------------------------------

    def test_sensitive_environment_variables_are_excluded(self):
        parent_env = {
            "PATH": "/usr/bin:/bin",
            "ANTHROPIC_API_KEY": "sk-should-not-appear",
            "ANTHROPIC_AUTH_TOKEN": "should-not-appear",
            "AWS_ACCESS_KEY_ID": "should-not-appear",
            "AWS_SECRET_ACCESS_KEY": "should-not-appear",
            "GOOGLE_APPLICATION_CREDENTIALS": "/should/not/appear",
            "GITHUB_TOKEN": "should-not-appear",
            "GH_TOKEN": "should-not-appear",
            "SSH_AUTH_SOCK": "/should/not/appear",
            "DATABASE_URL": "postgres://user:pw@host/db",
            "MY_APP_SECRET": "should-not-appear",
            "SOME_PASSWORD": "should-not-appear",
            "RANDOM_TOKEN_THING": "should-not-appear",
        }

        env = policy.build_allowed_env(parent_env=parent_env)

        for name in parent_env:
            if name == "PATH":
                continue
            self.assertNotIn(name, env, f"{name!r} must never be passed to a child process")

    def test_explicitly_allowed_harmless_variables_are_present(self):
        parent_env = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "LC_ALL": "C", "TMPDIR": "/tmp"}

        env = policy.build_allowed_env(parent_env=parent_env)

        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        self.assertEqual(env["LANG"], "en_US.UTF-8")
        self.assertEqual(env["LC_ALL"], "C")
        self.assertEqual(env["TMPDIR"], "/tmp")

    def test_home_is_excluded_by_default(self):
        parent_env = {"PATH": "/usr/bin", "HOME": "/home/someone"}
        env = policy.build_allowed_env(parent_env=parent_env)
        self.assertNotIn("HOME", env)

    def test_path_falls_back_to_safe_default_when_parent_has_none(self):
        env = policy.build_allowed_env(parent_env={})
        self.assertEqual(env["PATH"], policy.DEFAULT_SAFE_PATH)

    def test_extra_env_still_screens_sensitive_names(self):
        env = policy.build_allowed_env(
            parent_env={"PATH": "/usr/bin"},
            extra={"NIGHTSHIFT_TASK_ID": "t1", "SNEAKY_SECRET": "nope"},
        )
        self.assertEqual(env["NIGHTSHIFT_TASK_ID"], "t1")
        self.assertNotIn("SNEAKY_SECRET", env)

    # -- Working-directory boundary ---------------------------------------------

    def test_working_dir_inside_approved_root_is_allowed(self):
        sub = os.path.join(self.tmpdir, "sub")
        os.makedirs(sub)
        decision = policy.validate_working_dir(sub, self.tmpdir)
        self.assertEqual(decision.outcome, policy.PolicyOutcome.ALLOWED)

    def test_working_dir_outside_approved_root_is_rejected(self):
        outside = tempfile.mkdtemp(prefix="nightshift-outside-")
        try:
            decision = policy.validate_working_dir(outside, self.tmpdir)
            self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_dot_dot_path_escape_is_rejected(self):
        sub = os.path.join(self.tmpdir, "sub")
        os.makedirs(sub)
        escaping = os.path.join(sub, "..", "..")  # climbs above approved_root
        decision = policy.validate_working_dir(escaping, sub)
        self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)

    def test_symlink_escape_is_rejected(self):
        root = os.path.join(self.tmpdir, "root")
        os.makedirs(root)
        outside = os.path.join(self.tmpdir, "outside")
        os.makedirs(outside)
        symlink_path = os.path.join(root, "escape")
        os.symlink(outside, symlink_path)

        decision = policy.validate_working_dir(symlink_path, root)

        self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)
        self.assertIn("outside approved_root", decision.reason)

    def test_malformed_working_dir_config_fails_closed(self):
        for working_dir, approved_root in (
            ("", self.tmpdir),
            (self.tmpdir, ""),
            (None, self.tmpdir),
            (self.tmpdir, None),
            (self.tmpdir, "/path/does/not/exist"),
        ):
            with self.subTest(working_dir=working_dir, approved_root=approved_root):
                decision = policy.validate_working_dir(working_dir, approved_root)
                self.assertEqual(decision.outcome, policy.PolicyOutcome.REJECTED)


if __name__ == "__main__":
    unittest.main()
