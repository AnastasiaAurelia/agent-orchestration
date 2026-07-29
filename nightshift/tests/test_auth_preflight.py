"""Focused unit tests for nightshift.runtime.auth_preflight.

Uses only fake local Python executables as stand-ins for the real `claude`
CLI -- never invokes a real Claude session, never touches this machine's
actual Claude authentication. See test_queue.py / test_policy.py for the
sibling modules' own test conventions this file follows.
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

from nightshift.runtime import auth_preflight  # noqa: E402
from nightshift.runtime import isolation  # noqa: E402


def _fake_claude_auth_status(payload, exit_code=0):
    """Build a [python3, -c, ...] command that prints `payload` as JSON and exits."""
    script = (
        "import sys, json\n"
        f"sys.stdout.write(json.dumps({payload!r}))\n"
        f"sys.exit({exit_code})\n"
    )
    return [sys.executable, "-c", script]


def _fake_claude_raw_output(raw_text, exit_code=0):
    script = f"import sys\nsys.stdout.write({raw_text!r})\nsys.exit({exit_code})\n"
    return [sys.executable, "-c", script]


def _fake_claude_hang(seconds=30):
    script = f"import time\ntime.sleep({seconds})\n"
    return [sys.executable, "-c", script]


_SUBSCRIPTION_OK_PAYLOAD = {
    "loggedIn": True,
    "authMethod": "claude.ai",
    "apiProvider": "firstParty",
    "email": "user@example.com",
    "orgId": "org-fake",
    "orgName": "fake org",
    "subscriptionType": "pro",
}


class AuthPreflightTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nightshift-auth-preflight-test-")
        # A minimal env with no sensitive names, so tests can reach the
        # subprocess-launch step unless they specifically want to test the
        # sensitive-env fail-closed gate.
        self.clean_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- Passing case ------------------------------------------------------

    def test_valid_first_party_subscription_auth_passes(self):
        result = auth_preflight.run_auth_preflight(
            claude_command=_fake_claude_auth_status(_SUBSCRIPTION_OK_PAYLOAD),
            env=self.clean_env,
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.outcome, auth_preflight.PreflightOutcome.PASSED)
        self.assertEqual(result.auth_method, "claude.ai")
        self.assertEqual(result.api_provider, "firstParty")
        self.assertEqual(result.subscription_type, "pro")

    # -- Fail-closed classification cases ------------------------------------

    def test_logged_out_status_fails(self):
        payload = dict(_SUBSCRIPTION_OK_PAYLOAD, loggedIn=False)

        result = auth_preflight.run_auth_preflight(
            claude_command=_fake_claude_auth_status(payload), env=self.clean_env
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.outcome, auth_preflight.PreflightOutcome.LOGGED_OUT)

    def test_malformed_json_output_fails(self):
        result = auth_preflight.run_auth_preflight(
            claude_command=_fake_claude_raw_output("{not valid json"), env=self.clean_env
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.outcome, auth_preflight.PreflightOutcome.MALFORMED_OUTPUT)

    def test_non_object_json_output_fails(self):
        result = auth_preflight.run_auth_preflight(
            claude_command=_fake_claude_raw_output(json.dumps([1, 2, 3])), env=self.clean_env
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.outcome, auth_preflight.PreflightOutcome.MALFORMED_OUTPUT)

    def test_unexpected_provider_fails(self):
        payload = dict(_SUBSCRIPTION_OK_PAYLOAD, authMethod="enterprise-sso", apiProvider="bedrock")

        result = auth_preflight.run_auth_preflight(
            claude_command=_fake_claude_auth_status(payload), env=self.clean_env
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.outcome, auth_preflight.PreflightOutcome.UNEXPECTED_PROVIDER)

    def test_api_key_backed_auth_fails(self):
        payload = dict(
            _SUBSCRIPTION_OK_PAYLOAD, authMethod="api-key", apiProvider="firstParty", subscriptionType=None
        )

        result = auth_preflight.run_auth_preflight(
            claude_command=_fake_claude_auth_status(payload), env=self.clean_env
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.outcome, auth_preflight.PreflightOutcome.API_KEY_BACKED)

    def test_missing_subscription_type_fails(self):
        payload = dict(_SUBSCRIPTION_OK_PAYLOAD, subscriptionType=None)

        result = auth_preflight.run_auth_preflight(
            claude_command=_fake_claude_auth_status(payload), env=self.clean_env
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.outcome, auth_preflight.PreflightOutcome.SUBSCRIPTION_UNVERIFIED)

    def test_missing_executable_fails(self):
        result = auth_preflight.run_auth_preflight(
            claude_command=["nightshift-fixture-claude-does-not-exist", "auth", "status"],
            env=self.clean_env,
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.outcome, auth_preflight.PreflightOutcome.MISSING_EXECUTABLE)

    def test_timeout_fails(self):
        result = auth_preflight.run_auth_preflight(
            claude_command=_fake_claude_hang(30), env=self.clean_env, timeout_seconds=1
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.outcome, auth_preflight.PreflightOutcome.TIMED_OUT)

    def test_non_zero_exit_fails(self):
        result = auth_preflight.run_auth_preflight(
            claude_command=_fake_claude_auth_status(_SUBSCRIPTION_OK_PAYLOAD, exit_code=1),
            env=self.clean_env,
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.outcome, auth_preflight.PreflightOutcome.NON_ZERO_EXIT)

    # -- Environment safety ---------------------------------------------------

    def test_sensitive_variables_cause_fail_closed_behavior(self):
        env_with_secret = dict(self.clean_env, ANTHROPIC_API_KEY="sk-should-block-everything")

        result = auth_preflight.run_auth_preflight(
            claude_command=_fake_claude_auth_status(_SUBSCRIPTION_OK_PAYLOAD), env=env_with_secret
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.outcome, auth_preflight.PreflightOutcome.SENSITIVE_ENV_PRESENT)
        self.assertIn("ANTHROPIC_API_KEY", result.reason)
        self.assertNotIn("sk-should-block-everything", result.reason)

    def test_sensitive_parent_variables_do_not_reach_the_child(self):
        # Even a variable that IS allowed to proceed (not sensitive by name)
        # must not reach the child unless it is on the explicit allowlist --
        # proving the child only ever gets PATH/LANG/LC_ALL/TMPDIR, nothing
        # else from the parent, sensitive or not.
        env_dump_path = os.path.join(self.tmpdir, "env_dump.json")
        script = (
            "import os, json\n"
            f"open({env_dump_path!r}, 'w').write(json.dumps(dict(os.environ)))\n"
            f"print(json.dumps({_SUBSCRIPTION_OK_PAYLOAD!r}))\n"
        )
        env_with_extra = dict(self.clean_env, NOT_ALLOWLISTED_HARMLESS_VAR="should-not-reach-child")

        result = auth_preflight.run_auth_preflight(
            claude_command=[sys.executable, "-c", script], env=env_with_extra
        )

        self.assertTrue(result.passed)
        with open(env_dump_path, encoding="utf-8") as f:
            child_env = json.load(f)
        self.assertNotIn("NOT_ALLOWLISTED_HARMLESS_VAR", child_env)

    def test_credential_values_never_appear_in_evidence(self):
        payload = dict(_SUBSCRIPTION_OK_PAYLOAD, accessToken="sk-super-secret-should-never-appear")

        result = auth_preflight.run_auth_preflight(
            claude_command=_fake_claude_auth_status(payload), env=self.clean_env
        )

        evidence = json.dumps(result.to_json_dict())
        self.assertNotIn("sk-super-secret-should-never-appear", evidence)

    # -- Gate integration -------------------------------------------------------

    def test_preflight_failure_prevents_executor_launch_via_gate(self):
        nightshift_root = os.path.join(self.tmpdir, "workspace")
        production_root = os.path.join(self.tmpdir, "production")
        os.makedirs(nightshift_root)
        os.makedirs(production_root)

        payload = dict(_SUBSCRIPTION_OK_PAYLOAD, loggedIn=False)
        gate_result = auth_preflight.preflight_gate(
            nightshift_root,
            [production_root],
            claude_command=_fake_claude_auth_status(payload),
            env=self.clean_env,
        )

        self.assertFalse(gate_result.passed)
        self.assertTrue(gate_result.isolation.allowed)
        self.assertIsNotNone(gate_result.auth)
        self.assertEqual(gate_result.auth.outcome, auth_preflight.PreflightOutcome.LOGGED_OUT)

    def test_gate_short_circuits_on_isolation_failure_without_running_auth(self):
        nightshift_root = os.path.join(self.tmpdir, "workspace")
        production_root = os.path.join(self.tmpdir, "production")
        os.makedirs(production_root)
        nested = os.path.join(production_root, "workspace")
        os.makedirs(nested)

        gate_result = auth_preflight.preflight_gate(
            nested,
            [production_root],
            claude_command=_fake_claude_auth_status(_SUBSCRIPTION_OK_PAYLOAD),
            env=self.clean_env,
        )

        self.assertFalse(gate_result.passed)
        self.assertFalse(gate_result.isolation.allowed)
        self.assertIsNone(gate_result.auth, "auth must not even be attempted when isolation fails")

    def test_gate_passes_when_both_isolation_and_auth_are_satisfied(self):
        nightshift_root = os.path.join(self.tmpdir, "workspace")
        production_root = os.path.join(self.tmpdir, "production")
        os.makedirs(nightshift_root)
        os.makedirs(production_root)

        gate_result = auth_preflight.preflight_gate(
            nightshift_root,
            [production_root],
            claude_command=_fake_claude_auth_status(_SUBSCRIPTION_OK_PAYLOAD),
            env=self.clean_env,
        )

        self.assertTrue(gate_result.passed)
        self.assertTrue(gate_result.isolation.allowed)
        self.assertTrue(gate_result.auth.passed)


if __name__ == "__main__":
    unittest.main()
