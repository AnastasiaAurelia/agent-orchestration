"""Deterministic Claude authentication preflight for Diana Nightshift.

Runs only a non-work-producing status command (``claude auth status
--json``) and decides, deterministically, whether it is safe to proceed to
a real executor -- never itself launches, invokes, or produces any actual
Claude work. See docs/nightshift/RESEARCH.md and this milestone's own
adversarial review for what this preflight does and does not prove.

What "passing" means here, precisely
-------------------------------------
Observed once, live, directly against this machine's real Claude Code
installation (not assumed): ``claude auth status`` (default ``--json``)
returns ``{"loggedIn": bool, "authMethod": "claude.ai", "apiProvider":
"firstParty", "email": ..., "orgId": ..., "orgName": ..., "subscriptionType":
"pro"}`` for a genuine, logged-in subscription session. This preflight
requires ``loggedIn is True``, ``authMethod == "claude.ai"``, ``apiProvider
== "firstParty"``, and a non-empty ``subscriptionType`` -- all four,
together, are the evidence bar for "subscription-backed, not
ANTHROPIC_API_KEY-backed."

The exact ``authMethod``/``apiProvider`` strings an API-key-backed session
would report were **not** empirically observed for this module (doing so
would require actually logging out or configuring a real API key against
this machine, which this milestone does not do). The safety property does
not depend on knowing that string precisely: any ``authMethod`` other than
the one confirmed-good value fails closed. A best-effort
``API_KEY_BACKED`` vs. generic ``UNEXPECTED_PROVIDER`` sub-label exists
purely for evidence readability, not for the pass/fail decision itself.

Why excluding HOME from the child environment is safe (empirically checked)
-----------------------------------------------------------------------------
policy.build_allowed_env() does not include HOME by default, so the auth
status child process launches with HOME entirely absent, not empty or
wrong. This was verified directly against this machine's real Claude Code
installation, not assumed: with HOME unset (``env -i PATH=... claude auth
status``), the CLI still correctly reports the real logged-in session --
it falls back to resolving the home directory some other way (most likely
the OS user/passwd database for the running UID) when HOME is absent.
Separately verified: if HOME *is* explicitly set to a wrong path, the CLI
reports ``loggedIn: false`` instead of falling back -- so leaving HOME
unset is not merely harmless here, it is safer than passing through a
value that could be wrong or attacker-influenced. This is a real, checked
fact about this specific installed version, not a guarantee about every
Claude Code version or platform.

What this preflight does NOT prove
------------------------------------
It does not prove that an unattended, scheduler-triggered invocation, in a
different (stripped) environment, will refresh an expiring OAuth token
successfully. It only proves that, *at the moment it was run, in the
environment it was given*, the CLI reports a logged-in, first-party,
subscription-backed session. See this milestone's adversarial review for
the scheduler-environment and token-refresh gaps this does not close.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from enum import Enum
from typing import Optional, Sequence

from nightshift.runtime import isolation as _isolation
from nightshift.runtime import policy as _policy

DEFAULT_AUTH_STATUS_COMMAND = ("claude", "auth", "status", "--json")
DEFAULT_TIMEOUT_SECONDS = 10.0


class PreflightOutcome(str, Enum):
    """Every non-PASSED value is a fail-closed outcome; nothing here is a warning."""

    PASSED = "passed"
    SENSITIVE_ENV_PRESENT = "sensitive_env_present"
    MISSING_EXECUTABLE = "missing_executable"
    TIMED_OUT = "timed_out"
    NON_ZERO_EXIT = "non_zero_exit"
    MALFORMED_OUTPUT = "malformed_output"
    LOGGED_OUT = "logged_out"
    API_KEY_BACKED = "api_key_backed"
    UNEXPECTED_PROVIDER = "unexpected_provider"
    SUBSCRIPTION_UNVERIFIED = "subscription_unverified"


@dataclasses.dataclass(frozen=True)
class PreflightResult:
    outcome: PreflightOutcome
    passed: bool
    reason: Optional[str] = None
    # Classification evidence only -- these three fields, and nothing else
    # in this dataclass, ever holds real CLI output. No field here is ever
    # a token, a refresh token, an org id used as a secret, or raw
    # stdout/stderr. See the module docstring's safety claim.
    auth_method: Optional[str] = None
    api_provider: Optional[str] = None
    subscription_type: Optional[str] = None

    def to_json_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "passed": self.passed,
            "reason": self.reason,
            "auth_method": self.auth_method,
            "api_provider": self.api_provider,
            "subscription_type": self.subscription_type,
        }


def _find_present_sensitive_names(env: dict) -> list:
    return sorted(name for name in env if _policy.is_sensitive_env_name(name))


def run_auth_preflight(
    claude_command: Optional[Sequence[str]] = None,
    env: Optional[dict] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> PreflightResult:
    """Run the auth-status command and decide pass/fail. Never launches real work.

    ``env`` is the environment to *check and launch from* -- defaults to
    this process's own os.environ. The sensitive-variable check runs before
    anything else: if any credential-shaped variable (see
    policy.is_sensitive_env_name) is present, this fails closed
    immediately, regardless of what the auth-status command would
    otherwise report -- a stray ANTHROPIC_API_KEY sitting in the
    environment is exactly the "silent metered-API-billing" risk this
    preflight exists to catch before it can matter.

    The child process itself is launched with an allowlisted environment
    (policy.build_allowed_env), never this function's own full ``env`` --
    consistent with every other subprocess launch in this codebase.
    """
    check_env = env if env is not None else dict(os.environ)

    present_sensitive = _find_present_sensitive_names(check_env)
    if present_sensitive:
        return PreflightResult(
            outcome=PreflightOutcome.SENSITIVE_ENV_PRESENT,
            passed=False,
            reason=(
                "sensitive credential-shaped environment variable(s) present: "
                + ", ".join(present_sensitive)
                + " -- refusing to proceed rather than risk a metered API route "
                "or credential exposure"
            ),
        )

    command = list(claude_command) if claude_command is not None else list(DEFAULT_AUTH_STATUS_COMMAND)
    if not command or not isinstance(command[0], str) or not command[0]:
        return PreflightResult(
            outcome=PreflightOutcome.MISSING_EXECUTABLE,
            passed=False,
            reason="empty or invalid claude_command",
        )

    resolved = _policy.resolve_executable(command[0], os.getcwd())
    if resolved is None:
        return PreflightResult(
            outcome=PreflightOutcome.MISSING_EXECUTABLE,
            passed=False,
            reason=f"could not resolve {command[0]!r} on PATH",
        )

    child_env = _policy.build_allowed_env(parent_env=check_env)

    try:
        completed = subprocess.run(
            [resolved] + command[1:],
            timeout=timeout_seconds,
            capture_output=True,
            text=True,
            shell=False,
            env=child_env,
        )
    except subprocess.TimeoutExpired:
        return PreflightResult(
            outcome=PreflightOutcome.TIMED_OUT,
            passed=False,
            reason=f"auth status command exceeded {timeout_seconds}s timeout",
        )
    except OSError as exc:
        return PreflightResult(
            outcome=PreflightOutcome.MISSING_EXECUTABLE,
            passed=False,
            reason=f"could not launch resolved executable: {exc}",
        )

    if completed.returncode != 0:
        return PreflightResult(
            outcome=PreflightOutcome.NON_ZERO_EXIT,
            passed=False,
            reason=f"auth status command exited with code {completed.returncode}",
        )

    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return PreflightResult(
            outcome=PreflightOutcome.MALFORMED_OUTPUT,
            passed=False,
            reason="could not parse JSON from auth status output",
        )
    if not isinstance(parsed, dict):
        return PreflightResult(
            outcome=PreflightOutcome.MALFORMED_OUTPUT,
            passed=False,
            reason="auth status output is not a JSON object",
        )

    logged_in = parsed.get("loggedIn")
    auth_method = parsed.get("authMethod")
    api_provider = parsed.get("apiProvider")
    subscription_type = parsed.get("subscriptionType")

    if not isinstance(auth_method, (str, type(None))) or not isinstance(
        api_provider, (str, type(None))
    ):
        return PreflightResult(
            outcome=PreflightOutcome.MALFORMED_OUTPUT,
            passed=False,
            reason="auth status output has non-string authMethod/apiProvider",
        )

    if logged_in is not True:
        return PreflightResult(
            outcome=PreflightOutcome.LOGGED_OUT,
            passed=False,
            reason="not logged in",
            auth_method=auth_method,
            api_provider=api_provider,
        )

    if auth_method != "claude.ai" or api_provider != "firstParty":
        looks_like_api_key = bool(auth_method) and any(
            marker in auth_method.lower() for marker in ("api", "key")
        )
        outcome = (
            PreflightOutcome.API_KEY_BACKED
            if looks_like_api_key
            else PreflightOutcome.UNEXPECTED_PROVIDER
        )
        return PreflightResult(
            outcome=outcome,
            passed=False,
            reason=(
                f"unexpected authMethod={auth_method!r} / apiProvider={api_provider!r} "
                "(required: authMethod='claude.ai', apiProvider='firstParty')"
            ),
            auth_method=auth_method,
            api_provider=api_provider,
        )

    if not isinstance(subscription_type, str) or not subscription_type:
        return PreflightResult(
            outcome=PreflightOutcome.SUBSCRIPTION_UNVERIFIED,
            passed=False,
            reason="subscriptionType is missing or empty -- cannot verify subscription-backed auth",
            auth_method=auth_method,
            api_provider=api_provider,
        )

    return PreflightResult(
        outcome=PreflightOutcome.PASSED,
        passed=True,
        auth_method=auth_method,
        api_provider=api_provider,
        subscription_type=subscription_type,
    )


@dataclasses.dataclass(frozen=True)
class GateResult:
    """The combined isolation + auth verdict a real-executor caller must check.

    This is the fail-closed integration point this milestone establishes:
    a future, separately-approved milestone that actually launches a real
    Claude executor must call preflight_gate() first and refuse to proceed
    unless ``passed`` is True. No real executor exists yet in this
    codebase to wire this into -- see the module docstring.
    """

    passed: bool
    isolation: _isolation.IsolationDecision
    auth: Optional[PreflightResult]

    def to_json_dict(self) -> dict:
        return {
            "passed": self.passed,
            "isolation": self.isolation.to_json_dict(),
            "auth": self.auth.to_json_dict() if self.auth is not None else None,
        }


def preflight_gate(
    nightshift_root: str,
    forbidden_paths: Sequence[str],
    claude_command: Optional[Sequence[str]] = None,
    env: Optional[dict] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> GateResult:
    """Check isolation, then auth. Both must pass. Fails closed on either.

    Isolation is checked first since it never spawns a subprocess and is
    strictly cheaper; a failed isolation check skips the auth check
    entirely (``auth`` is None in that case) rather than needlessly running
    it.
    """
    isolation_decision = _isolation.validate_isolation(nightshift_root, forbidden_paths)
    if not isolation_decision.allowed:
        return GateResult(passed=False, isolation=isolation_decision, auth=None)

    auth_result = run_auth_preflight(
        claude_command=claude_command, env=env, timeout_seconds=timeout_seconds
    )
    return GateResult(
        passed=isolation_decision.allowed and auth_result.passed,
        isolation=isolation_decision,
        auth=auth_result,
    )
