"""Supervised real-Claude executor adapter for Diana Nightshift (Milestone 7C).

Connects the already-built deterministic queue/policy/isolation/auth-preflight
runtime to exactly one real Claude Code process per cycle. This module is
deliberately separate from nightshift/runtime/queue.py: queue.py already owns
the generic deterministic queue, transition, execution, acceptance, recovery,
and reporting behavior (Milestones 1-6), and a Claude invocation needs
trusted, infrastructure-controlled CLI flags that must never come from
task-author-supplied argv the way the generic executor's executor_command
field does. This is a focused adapter, not a provider abstraction or plugin
system -- there is exactly one thing it launches: Claude, one way, via one
trusted configuration.

CLI capabilities this module relies on, verified live against the installed
CLI (`claude --version` -> "2.1.220 (Claude Code)") during this milestone's
implementation, not assumed from memory -- see `claude --help`:

  -p, --print              non-interactive: print response and exit
  --tools <tools...>       "Specify the list of available tools from the
                           built-in set" -- replaces the tool set entirely.
                           This is the authoritative surface restriction:
                           Bash, WebFetch, WebSearch, subagents, and
                           deployment/git-push tools are absent from this
                           list, not merely "not pre-approved" -- they are
                           not part of the tool set Claude has access to at
                           all, regardless of --allowedTools below.
  --allowedTools <tools...> Milestone 7C.2.3: the *same* bounded set as
                           --tools (Read,Write,Edit,Glob,Grep), passed
                           explicitly so acceptEdits pre-approves exactly
                           those tools without prompting -- this never
                           widens the tool surface (--tools already fixed
                           that, and always takes effect first/independently);
                           it only removes the interactive-approval step
                           for tools already on the one authoritative list.
  --permission-mode <mode> choices: acceptEdits, auto, bypassPermissions,
                           manual, dontAsk, plan. bypassPermissions remains
                           explicitly forbidden. dontAsk was this
                           milestone's original best-effort choice, but the
                           first real supervised smoke run showed it
                           actually blocks Write calls outright (Claude's
                           own final text explained the active mode was
                           refusing them; both Write attempts appeared in
                           its structured result's own permission_denials,
                           exit code 0, subtype "success", zero files ever
                           created) -- not "no prompt, but still permitted"
                           as assumed. Milestone 7C.2.3 switches to
                           acceptEdits (auto-accept file edit operations
                           specifically, still not a full permission
                           bypass) plus --allowedTools below, and adds
                           post-hoc detection of permission_denials in the
                           captured result as a second, independent safety
                           net: even if a future CLI version or mode
                           reintroduces silent denials, this module does
                           not rely solely on assuming the flag works as
                           documented.
  --strict-mcp-config      "Only use MCP servers from --mcp-config,
                           ignoring all other MCP configurations" -- used
                           with no --mcp-config supplied at all, so no MCP
                           server loads. This closes a real gap --tools
                           alone does not: --tools governs "the built-in
                           set", and the help text does not say MCP-
                           provided tools are part of that set. Without
                           --strict-mcp-config, an ambient MCP server
                           already configured for the invoking user could
                           hand the model tools entirely outside the
                           --tools restriction.
  --disable-slash-commands "Disable all skills" -- a skill could reference
                           behavior (e.g. assuming Bash) that has no
                           business being reachable for this smoke task.
  --no-session-persistence "(only works with --print)" -- no resumable
                           session state left behind for a one-shot task.
  --output-format json     structured, machine-parseable evidence capture
                           (single JSON result), rather than free text.

There is no CLI-native turn/step-count limit in this version (not present
anywhere in `claude --help`). Bounded execution is therefore enforced
entirely by this module's own wall-clock timeout + process-group kill
(SIGTERM, bounded grace period, then SIGKILL fallback) -- the existing
mechanism this codebase already uses for the generic executor
(queue.run_executor), extended here with the SIGTERM grace step since a
real Claude process may benefit from a chance to exit cleanly, unlike a
throwaway test fixture.

Threat model, stated as plainly as nightshift/runtime/policy.py's own:
this is safe only for a supervised, trusted-task-author smoke test on an
already OS-level-isolated host (see nightshift/runtime/isolation.py and
docs/nightshift/VPS_SETUP.md). It is not a sandbox. Whether Claude Code's
own file-editing tools would refuse an absolute path outside the launch
cwd is not verified here and is not this module's job to guarantee --
the actual load-bearing protection against reaching production paths is
the OS-level Linux-user permission boundary already proven in Milestones
7B1/7B2, not anything this module or Claude Code's own tool layer does.
See this milestone's adversarial review for the complete list of what
remains unproven or out of scope.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import signal
import stat
import subprocess
import sys
import time
from enum import Enum
from typing import Optional, Sequence

from nightshift.runtime import auth_preflight as _auth_preflight
from nightshift.runtime import isolation as _isolation
from nightshift.runtime import policy as _policy
from nightshift.runtime import queue as _queue

REQUIRED_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep")
# Milestone 7C.2.3: dontAsk turned out to block Write outright on a real
# supervised smoke run (see the module docstring) -- acceptEdits is the
# corrected choice, still not bypassPermissions, still bounded to exactly
# REQUIRED_TOOLS via --tools/--allowedTools together.
DEFAULT_PERMISSION_MODE = "acceptEdits"
DEFAULT_CLAUDE_TIMEOUT_SECONDS = 300.0
SIGTERM_GRACE_SECONDS = 5.0
AUTH_STATUS_TIMEOUT_SECONDS = 10.0
HELP_CHECK_TIMEOUT_SECONDS = 10.0
VERSION_CHECK_TIMEOUT_SECONDS = 10.0

REQUIRED_HELP_MARKERS = (
    "--print",
    "--tools",
    "--allowedTools",
    "--permission-mode",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--no-session-persistence",
    "--output-format",
)

# Milestone 7C.1: conservative, safe-to-check-in-cleartext substrings that
# indicate a real Claude invocation exited non-zero because it could not
# authenticate, not because the task itself failed. None of these are
# secrets -- they are the kind of short status words/codes an API error
# response or CLI error message uses, never a token or account identifier.
# Deliberately broad and lowercase-matched: a false positive here only
# yields a more specific, still-correctly-failing outcome label
# (AUTH_FAILED instead of a generic nonzero exit); a false negative still
# correctly fails the attempt via the ordinary nonzero-exit path below --
# this classification only sharpens *why*, it never loosens the Completion
# Invariant itself (see queue.run_acceptance_and_record()).
_AUTH_FAILURE_MARKERS = (
    "401",
    "access token has expired",
    "access token expired",
    "re-authenticate",
    "reauthenticate",
    "authentication_error",
    "unauthorized",
    "invalid_api_key",
    "invalid x-api-key",
)


def _looks_like_auth_failure(exit_code: Optional[int], stdout: str, stderr: str) -> bool:
    """Best-effort detection of a Claude authentication failure in captured output.

    Only ever consulted when the process already exited non-zero. Never
    stores or returns the matched text itself -- callers only get a bool.
    """
    if not exit_code:
        return False
    combined = f"{stdout}\n{stderr}".lower()
    return any(marker in combined for marker in _AUTH_FAILURE_MARKERS)


# Milestone 7C.2.3: a real supervised smoke run showed Claude exiting 0,
# reporting subtype "success", after a 3-turn session -- while its own
# structured result listed both Write calls it attempted under
# permission_denials, and zero files were ever actually created. Exit code
# and self-reported subtype are not sufficient evidence of a genuine
# success; the structured result's own permission_denials list is checked
# explicitly, regardless of exit code, as a second, independent signal.
_MAX_DESCRIBED_DENIALS = 5


def _parse_permission_denials(stdout: str) -> Optional[list]:
    """Best-effort parse of Claude's own JSON result for a permission_denials list.

    Returns None if stdout is not valid JSON, is not a JSON object, or has
    no ``permission_denials`` list at all -- callers treat None exactly
    like "no denials found", never as a reason to assume anything else.
    """
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    denials = data.get("permission_denials")
    if not isinstance(denials, list):
        return None
    return denials


def _sanitize_permission_denial(denial) -> dict:
    """Extract only a tool name and a bounded file-path string from one raw
    denial entry -- never the full raw entry, never any other tool_input
    field (e.g. file content for a Write call), never any other part of
    Claude's own JSON result.
    """
    if not isinstance(denial, dict):
        return {"tool_name": None, "path": None}
    tool_name = denial.get("tool_name")
    if not isinstance(tool_name, str):
        raw_tool = denial.get("tool")
        tool_name = raw_tool if isinstance(raw_tool, str) else None
    path = None
    tool_input = denial.get("tool_input")
    if isinstance(tool_input, dict):
        raw_path = tool_input.get("file_path")
        if not isinstance(raw_path, str):
            raw_path = tool_input.get("path")
        if isinstance(raw_path, str):
            path = raw_path[:300]
    return {"tool_name": tool_name, "path": path}


def _build_permission_denial_message(denials: list) -> str:
    """Build a short, sanitized summary from raw permission_denials.

    Only ever includes a tool name and a bounded path per denial (see
    _sanitize_permission_denial) -- never raw credentials, never
    unrestricted model output, never the full raw JSON result. This is the
    only text this module derives from a permission denial that is ever
    allowed into durable evidence -- see queue._safe_log_excerpt(), which
    deliberately never falls back to raw stdout/stderr for this outcome.
    """
    sanitized = [_sanitize_permission_denial(d) for d in denials[:_MAX_DESCRIBED_DENIALS]]
    parts = []
    for entry in sanitized:
        tool = entry["tool_name"] or "unknown-tool"
        parts.append(f"{tool}({entry['path']})" if entry["path"] else tool)
    summary = ", ".join(parts) if parts else "no further detail available"
    omitted = len(denials) - len(sanitized)
    if omitted > 0:
        summary += f", and {omitted} more"
    return (
        f"Claude reported {len(denials)} permission denial(s) for its own tool calls "
        f"({summary}) -- classified as a permission failure regardless of exit code or "
        "self-reported subtype"
    )


# Same "never ran" concept queue.run_task() established in Milestone 7A --
# duplicated here deliberately (queue._EXECUTOR_NEVER_RAN_OUTCOMES is
# private, and this module only ever produces a subset of it anyway: a
# Claude invocation has no MALFORMED_CONTRACT concept of its own).
_NEVER_RAN_OUTCOMES = frozenset(
    {_queue.ExecutorOutcome.MISSING_EXECUTABLE, _queue.ExecutorOutcome.POLICY_REJECTED}
)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Executable integrity
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ExecutableIntegrityResult:
    ok: bool
    reason: Optional[str] = None
    canonical_path: Optional[str] = None
    owner_uid: Optional[int] = None
    mode_octal: Optional[str] = None
    version: Optional[str] = None

    def to_json_dict(self) -> dict:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "canonical_path": self.canonical_path,
            "owner_uid": self.owner_uid,
            "mode_octal": self.mode_octal,
            "version": self.version,
        }


def verify_claude_executable(configured_path: Optional[str]) -> ExecutableIntegrityResult:
    """Verify the configured Claude executable before it is ever launched.

    Requires: an absolute path; existence; a regular, executable file;
    that the path IS its own canonical form (os.path.realpath(path) ==
    path -- no symlink indirection tolerated for the trusted entry point
    itself); and that it is neither group- nor world-writable. Records
    only safe metadata (canonical path, owner uid, permission bits, the
    plain version string) -- never credential files or auth tokens, and
    this proves only that the inspected path is what gets launched and
    that an unprivileged third party cannot trivially overwrite it, not
    software supply-chain authenticity.
    """
    if not isinstance(configured_path, str) or not configured_path:
        return ExecutableIntegrityResult(ok=False, reason="claude_executable is empty")
    if not os.path.isabs(configured_path):
        return ExecutableIntegrityResult(
            ok=False,
            reason=f"claude_executable must be an absolute path, got {configured_path!r}",
        )
    if not os.path.exists(configured_path):
        return ExecutableIntegrityResult(
            ok=False, reason=f"claude_executable {configured_path!r} does not exist"
        )
    if not os.path.isfile(configured_path):
        return ExecutableIntegrityResult(
            ok=False, reason=f"claude_executable {configured_path!r} is not a regular file"
        )
    if not os.access(configured_path, os.X_OK):
        return ExecutableIntegrityResult(
            ok=False, reason=f"claude_executable {configured_path!r} is not executable"
        )

    canonical = os.path.realpath(configured_path)
    if canonical != configured_path:
        return ExecutableIntegrityResult(
            ok=False,
            reason=(
                f"claude_executable {configured_path!r} is not its own canonical path "
                f"(resolves to {canonical!r}) -- configure the canonical path directly, "
                "e.g. via `readlink -f`"
            ),
            canonical_path=canonical,
        )

    st = os.stat(configured_path)
    mode_octal = oct(stat.S_IMODE(st.st_mode))
    if st.st_mode & stat.S_IWGRP:
        return ExecutableIntegrityResult(
            ok=False,
            reason=f"claude_executable {configured_path!r} is group-writable (mode {mode_octal})",
            canonical_path=canonical,
            owner_uid=st.st_uid,
            mode_octal=mode_octal,
        )
    if st.st_mode & stat.S_IWOTH:
        return ExecutableIntegrityResult(
            ok=False,
            reason=f"claude_executable {configured_path!r} is world-writable (mode {mode_octal})",
            canonical_path=canonical,
            owner_uid=st.st_uid,
            mode_octal=mode_octal,
        )

    version = None
    try:
        completed = subprocess.run(
            [configured_path, "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_CHECK_TIMEOUT_SECONDS,
            shell=False,
            env=_policy.build_allowed_env(),
        )
        if completed.returncode == 0:
            version = completed.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        version = None

    return ExecutableIntegrityResult(
        ok=True, canonical_path=canonical, owner_uid=st.st_uid, mode_octal=mode_octal, version=version
    )


def verify_cli_capabilities(claude_executable: str):
    """Confirm the installed CLI's --help output names every required flag.

    Returns (ok, missing_markers, error). Fails closed rather than
    weakening the invocation: a missing capability must never fall back to
    an assumed flag or a looser permission mode.
    """
    try:
        completed = subprocess.run(
            [claude_executable, "--help"],
            capture_output=True,
            text=True,
            timeout=HELP_CHECK_TIMEOUT_SECONDS,
            shell=False,
            env=_policy.build_allowed_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, list(REQUIRED_HELP_MARKERS), str(exc)

    help_text = completed.stdout
    missing = [marker for marker in REQUIRED_HELP_MARKERS if marker not in help_text]
    return (not missing), missing, None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Raised by ClaudeConfig.load() on any invalid, missing, or unsafe configuration."""


_REQUIRED_CONFIG_FIELDS = frozenset(
    {"queue_path", "report_dir", "nightshift_root", "forbidden_paths", "claude_executable"}
)
_KNOWN_CONFIG_FIELDS = _REQUIRED_CONFIG_FIELDS | frozenset(
    {
        "claude_timeout_seconds",
        "run_log_path",
        "lock_path",
        "stale_threshold_seconds",
    }
)


@dataclasses.dataclass(frozen=True)
class ClaudeConfig:
    """Small, explicit, trusted configuration for one Nightshift Claude cycle.

    Deliberately not a general configuration framework -- only what this
    milestone needs. ``nightshift_root`` should be the *disposable* smoke
    workspace specifically (e.g. /home/nightshift/workspace/smoke), not the
    broader Nightshift install directory that also contains this runtime's
    own checkout -- containment is enforced against exactly this value, so
    setting it too broadly would widen, not narrow, what a task's
    working_dir is allowed to be.
    """

    queue_path: str
    report_dir: str
    nightshift_root: str
    forbidden_paths: tuple
    claude_executable: str
    claude_timeout_seconds: float = DEFAULT_CLAUDE_TIMEOUT_SECONDS
    run_log_path: Optional[str] = None
    lock_path: Optional[str] = None
    stale_threshold_seconds: int = _queue.DEFAULT_STALE_THRESHOLD_SECONDS

    @staticmethod
    def load(data: dict) -> "ClaudeConfig":
        if not isinstance(data, dict):
            raise ConfigError("configuration must be a JSON object")

        unknown = set(data.keys()) - _KNOWN_CONFIG_FIELDS
        if unknown:
            raise ConfigError(f"unknown configuration field(s): {sorted(unknown)}")

        missing = _REQUIRED_CONFIG_FIELDS - set(data.keys())
        if missing:
            raise ConfigError(f"missing required configuration field(s): {sorted(missing)}")

        queue_path = data["queue_path"]
        report_dir = data["report_dir"]
        nightshift_root = data["nightshift_root"]
        forbidden_paths = data["forbidden_paths"]
        claude_executable = data["claude_executable"]
        claude_timeout_seconds = data.get("claude_timeout_seconds", DEFAULT_CLAUDE_TIMEOUT_SECONDS)
        run_log_path = data.get("run_log_path")
        lock_path = data.get("lock_path")
        stale_threshold_seconds = data.get(
            "stale_threshold_seconds", _queue.DEFAULT_STALE_THRESHOLD_SECONDS
        )

        for name, value in (
            ("queue_path", queue_path),
            ("report_dir", report_dir),
            ("nightshift_root", nightshift_root),
            ("claude_executable", claude_executable),
        ):
            if not isinstance(value, str) or not value:
                raise ConfigError(f"{name} must be a non-empty string")

        if not os.path.isabs(claude_executable):
            raise ConfigError(
                f"claude_executable must be an absolute path, got {claude_executable!r}"
            )

        if not isinstance(forbidden_paths, (list, tuple)) or not forbidden_paths:
            raise ConfigError("forbidden_paths must be a non-empty list")
        if not all(isinstance(p, str) and p for p in forbidden_paths):
            raise ConfigError("forbidden_paths entries must all be non-empty strings")

        if (
            isinstance(claude_timeout_seconds, bool)
            or not isinstance(claude_timeout_seconds, (int, float))
            or claude_timeout_seconds <= 0
        ):
            raise ConfigError("claude_timeout_seconds must be a positive number")

        if not os.path.isdir(nightshift_root):
            raise ConfigError(f"nightshift_root {nightshift_root!r} is not an existing directory")

        isolation_decision = _isolation.validate_isolation(nightshift_root, list(forbidden_paths))
        if not isolation_decision.allowed:
            raise ConfigError(
                f"nightshift_root overlaps a forbidden path: {isolation_decision.reason}"
            )

        return ClaudeConfig(
            queue_path=queue_path,
            report_dir=report_dir,
            nightshift_root=nightshift_root,
            forbidden_paths=tuple(forbidden_paths),
            claude_executable=claude_executable,
            claude_timeout_seconds=float(claude_timeout_seconds),
            run_log_path=run_log_path,
            lock_path=lock_path,
            stale_threshold_seconds=stale_threshold_seconds,
        )


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _build_prompt(task: dict, config: "ClaudeConfig") -> str:
    """Build the Claude prompt entirely from trusted task-contract fields.

    Never interpolated into a shell command -- passed as one argv element.
    States exactly what the Prompt Contract requires: the bounded
    objective, the working directory, the allowed file scope, forbidden
    paths, that no network/external action is allowed, that Claude must
    not run tests itself, that independent acceptance runs afterward, that
    Claude must not touch canonical Nightshift state, and that its final
    text is not proof of completion.
    """
    objective = task.get("title", "")
    working_dir = task.get("working_dir", "")
    forbidden = ", ".join(config.forbidden_paths)

    return (
        f"Bounded objective: {objective}\n\n"
        f"You are working inside exactly this directory and nothing else: {working_dir}\n"
        f"Allowed file scope: read, write, and edit files only within {working_dir} and its "
        "subdirectories.\n"
        f"Forbidden paths -- do not read or write under any circumstance: {forbidden}\n"
        "No network access, no external messaging, and no outbound action of any kind is "
        "allowed or available in this session.\n"
        "Do not run tests yourself for this task, and do not attempt to execute any shell "
        "or test command -- you have no Bash or shell tool available in this session "
        "regardless of what you might try to invoke.\n"
        "After you exit, a separate, independent, deterministic process will run the real "
        "acceptance check outside of this session, without trusting anything you report. "
        "Your own process exit code and your final text response are not evidence of "
        "completion -- only that independent acceptance check decides whether this task "
        "succeeded.\n"
        "Do not create, modify, or delete any Nightshift queue, state, evidence, lock, or "
        "report file. Those live outside your working directory and are never yours to "
        "touch.\n"
    )


def _build_claude_argv(claude_executable: str, prompt: str) -> list:
    return [
        claude_executable,
        "-p",
        "--output-format",
        "json",
        "--tools",
        ",".join(REQUIRED_TOOLS),
        "--allowedTools",
        ",".join(REQUIRED_TOOLS),
        "--permission-mode",
        DEFAULT_PERMISSION_MODE,
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
        prompt,
    ]


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


def _rejected_executor_result(
    outcome: "_queue.ExecutorOutcome", message: str
) -> "_queue.ExecutorResult":
    return _queue.ExecutorResult(
        outcome=outcome,
        command=(),
        pid=None,
        exit_code=None,
        stdout="",
        stderr="",
        started_at=None,
        ended_at=None,
        message=message,
    )


def run_claude_executor(
    task: dict, config: "ClaudeConfig", env: Optional[dict] = None
) -> "_queue.ExecutorResult":
    """Launch exactly one real (or, in tests, fake) Claude process for ``task``.

    Never trusts task["executor_command"]/task["executor_timeout_seconds"]
    -- those are the generic executor's fields (Milestone 4A); a Claude
    invocation is built entirely from trusted ``config`` plus the task's
    title/working_dir, never from task-author-supplied argv.

    Performs its own full set of checks (working-directory containment,
    executable integrity, CLI capability, preflight gate) every time it is
    called, regardless of whether an outer caller (run_one) already
    checked some of these before claiming -- so this function is safe to
    call directly and can never be tricked into launching without full
    verification just because some other code path already checked once.

    ``env`` is the environment the preflight's sensitive-variable check
    inspects -- defaults to this process's real os.environ (production
    behavior), exactly like auth_preflight.run_auth_preflight()'s own
    ``env`` parameter, which this is threaded through to. Tests pass an
    explicit, clean env so a real, benign variable already present in the
    developer's own shell (e.g. SSH_AUTH_SOCK from a normal SSH agent)
    doesn't produce a fail-closed result that has nothing to do with the
    behavior under test -- the fail-closed behavior itself is exactly what
    Milestone 7B1 already covers directly.
    """
    working_dir = task.get("working_dir")

    wd_decision = _policy.validate_working_dir(working_dir, config.nightshift_root)
    if not wd_decision.allowed:
        return _rejected_executor_result(
            _queue.ExecutorOutcome.POLICY_REJECTED, f"working_dir rejected: {wd_decision.reason}"
        )

    integrity = verify_claude_executable(config.claude_executable)
    if not integrity.ok:
        outcome = (
            _queue.ExecutorOutcome.MISSING_EXECUTABLE
            if integrity.reason and "does not exist" in integrity.reason
            else _queue.ExecutorOutcome.POLICY_REJECTED
        )
        return _rejected_executor_result(
            outcome, f"executable integrity check failed: {integrity.reason}"
        )

    cap_ok, missing, cap_error = verify_cli_capabilities(config.claude_executable)
    if not cap_ok:
        return _rejected_executor_result(
            _queue.ExecutorOutcome.POLICY_REJECTED,
            f"unsupported Claude CLI capabilities: missing={missing} error={cap_error}",
        )

    gate = _auth_preflight.preflight_gate(
        config.nightshift_root,
        list(config.forbidden_paths),
        claude_command=[config.claude_executable, "auth", "status", "--json"],
        env=env,
    )
    if not gate.passed:
        reason = gate.auth.reason if gate.auth is not None else gate.isolation.reason
        return _rejected_executor_result(
            _queue.ExecutorOutcome.POLICY_REJECTED, f"preflight failed: {reason}"
        )

    prompt = _build_prompt(task, config)
    argv = _build_claude_argv(config.claude_executable, prompt)
    # Deliberately not the ``env`` parameter above: that one only ever feeds
    # the preflight's sensitive-variable check (see docstring). The actual
    # child process always gets the strict, hardcoded-safe allowlist below,
    # built from this process's real os.environ, never from a test override.
    launch_env = _policy.build_allowed_env()

    started_at = _utcnow().isoformat()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=working_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            start_new_session=True,
            env=launch_env,
        )
    except OSError as exc:
        return _queue.ExecutorResult(
            outcome=_queue.ExecutorOutcome.MISSING_EXECUTABLE,
            command=tuple(argv),
            pid=None,
            exit_code=None,
            stdout="",
            stderr="",
            started_at=started_at,
            ended_at=_utcnow().isoformat(),
            message=f"could not launch resolved executable: {exc}",
        )

    pid = proc.pid
    try:
        stdout, stderr = proc.communicate(timeout=config.claude_timeout_seconds)
        outcome = _queue.ExecutorOutcome.COMPLETED
        message = None
        # Milestone 7C.1: a nonzero exit here previously still produced
        # COMPLETED, which -- combined with a lenient acceptance command --
        # is exactly how a real-world Claude authentication failure was
        # once wrongly reported as a successful task. This reclassification
        # never changes exit_code/stdout/stderr, only the outcome label and
        # message; the Completion Invariant in
        # queue.run_acceptance_and_record() is what actually prevents a
        # "done" transition, for this and for any other nonzero exit.
        #
        # Milestone 7C.2.3: checked first, and regardless of exit code --
        # the real observed failure was exit 0 with subtype "success", so
        # gating this behind a nonzero exit check (like the auth-failure
        # check below) would have missed it entirely.
        denials = _parse_permission_denials(stdout)
        if denials:
            outcome = _queue.ExecutorOutcome.PERMISSION_DENIED
            message = _build_permission_denial_message(denials)
        elif proc.returncode != 0 and _looks_like_auth_failure(proc.returncode, stdout, stderr):
            outcome = _queue.ExecutorOutcome.AUTH_FAILED
            message = (
                "Claude authentication failure detected: nonzero exit code plus "
                "recognized authentication-failure evidence in captured output"
            )
        return _queue.ExecutorResult(
            outcome=outcome,
            command=tuple(argv),
            pid=pid,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            started_at=started_at,
            ended_at=_utcnow().isoformat(),
            message=message,
        )
    except subprocess.TimeoutExpired:
        pass

    # Timeout: SIGTERM first, bounded grace period, then SIGKILL fallback --
    # deliberately different from the generic executor's SIGKILL-only
    # behavior, since a real Claude process may benefit from a chance to
    # exit cleanly. Either way the whole process group is targeted, so a
    # child Claude itself spawned is cleaned up too.
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        stdout, stderr = proc.communicate(timeout=SIGTERM_GRACE_SECONDS)
        return _queue.ExecutorResult(
            outcome=_queue.ExecutorOutcome.TIMED_OUT,
            command=tuple(argv),
            pid=pid,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            started_at=started_at,
            ended_at=_utcnow().isoformat(),
            message=f"exceeded {config.claude_timeout_seconds}s timeout, terminated via SIGTERM",
        )
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    stdout, stderr = proc.communicate()  # SIGKILL guarantees termination; no timeout needed
    return _queue.ExecutorResult(
        outcome=_queue.ExecutorOutcome.TIMED_OUT,
        command=tuple(argv),
        pid=pid,
        exit_code=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        started_at=started_at,
        ended_at=_utcnow().isoformat(),
        message=(
            f"exceeded {config.claude_timeout_seconds}s timeout, required SIGKILL "
            "after SIGTERM grace period"
        ),
    )


def run_claude_task(
    config: "ClaudeConfig", task_id: str, env: Optional[dict] = None
) -> "_queue.TaskRun":
    """Run one claimed task's Claude executor, then acceptance if it actually ran.

    Mirrors queue.run_task()'s shape (Milestone 4A/7A), substituting
    run_claude_executor() for the generic queue.run_executor() -- see this
    module's docstring for why these stay separate. If the executor never
    ran at all (MISSING_EXECUTABLE or POLICY_REJECTED), fails the attempt
    directly via fail_task() instead of running acceptance, for the exact
    reason queue.run_task() already does: a lenient acceptance command must
    not mark a never-executed or policy-rejected task "done".

    For every other executor outcome (COMPLETED, TIMED_OUT, AUTH_FAILED),
    acceptance still runs, but its result is passed to
    queue.run_acceptance_and_record() together with the executor result
    itself so that function's Completion Invariant (Milestone 7C.1) can
    enforce that a passing acceptance result only drives a "done" transition
    when the executor also actually succeeded (COMPLETED, exit_code 0) --
    this is the fix for the real-world false-success case where a Claude
    session failed authentication (nonzero exit) but a lenient acceptance
    command still passed.

    ``env`` is forwarded to run_claude_executor()'s own preflight check --
    see that function's docstring.
    """
    task = _queue.get_task(config.queue_path, task_id)
    if task is None:
        transition = _queue.TransitionResult(
            outcome=_queue.TransitionOutcome.TASK_NOT_FOUND, task_id=task_id
        )
        acceptance = _queue.AcceptanceResult(
            passed=False,
            reason=_queue.AcceptanceOutcome.MALFORMED_CONTRACT,
            command=(),
            exit_code=None,
            stdout="",
            stderr="",
            started_at=None,
            ended_at=None,
            message=f"no such task after claim: {task_id!r}",
        )
        executor = _rejected_executor_result(
            _queue.ExecutorOutcome.MALFORMED_CONTRACT, f"no such task after claim: {task_id!r}"
        )
        return _queue.TaskRun(
            executor=executor, acceptance_run=_queue.AcceptanceRun(acceptance=acceptance, transition=transition)
        )

    executor_result = run_claude_executor(task, config, env=env)

    if executor_result.outcome in _NEVER_RAN_OUTCOMES:
        transition = _queue.fail_task(
            config.queue_path,
            task_id,
            lock_path=config.lock_path,
            run_log_path=config.run_log_path,
            detail=f"executor_{executor_result.outcome.value}",
        )
        acceptance = _queue.AcceptanceResult(
            passed=False,
            reason=_queue.AcceptanceOutcome.MALFORMED_CONTRACT,
            command=(),
            exit_code=None,
            stdout="",
            stderr="",
            started_at=None,
            ended_at=None,
            message="acceptance skipped: the executor never ran",
        )
        return _queue.TaskRun(
            executor=executor_result,
            acceptance_run=_queue.AcceptanceRun(acceptance=acceptance, transition=transition),
        )

    acceptance_run = _queue.run_acceptance_and_record(
        config.queue_path,
        task_id,
        lock_path=config.lock_path,
        run_log_path=config.run_log_path,
        executor_result=executor_result,
    )
    return _queue.TaskRun(executor=executor_result, acceptance_run=acceptance_run)


# ---------------------------------------------------------------------------
# One-shot cycle
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class CycleResult:
    """The full outcome of one run_one() cycle. Never represents more than one task."""

    ran: bool
    claim: Optional["_queue.ClaimResult"]
    task_run: Optional["_queue.TaskRun"]
    report_path: Optional[str]
    message: Optional[str] = None
    # Milestone 7D: populated only when the pre-claim isolation/auth gate is
    # *why* this cycle produced no claim -- None in every other case
    # (success, executable-integrity failure, CLI-capability failure, or any
    # claim outcome). This lets a caller (the batch runner below) distinguish
    # an isolation failure (gate.auth is None -- preflight_gate() rejects
    # isolation before auth ever runs) from an authentication failure
    # (gate.auth is not None and not passed) without re-running or
    # re-implementing the check itself -- reusing this one shared cycle
    # function's own already-performed check, never a second call to it.
    # Already fully sanitized by auth_preflight.py's own GateResult -- never
    # a raw auth-status payload, token, email, or org id.
    gate: Optional["_auth_preflight.GateResult"] = None

    def to_json_dict(self) -> dict:
        return {
            "ran": self.ran,
            "claim": self.claim.to_json_dict() if self.claim is not None else None,
            "task_run": self.task_run.to_json_dict() if self.task_run is not None else None,
            "report_path": self.report_path,
            "message": self.message,
            "gate": self.gate.to_json_dict() if self.gate is not None else None,
        }


def _report_only_cycle_result(
    config: "ClaudeConfig",
    claim: Optional["_queue.ClaimResult"] = None,
    message: Optional[str] = None,
    gate: Optional["_auth_preflight.GateResult"] = None,
) -> CycleResult:
    report_path = _queue.write_report(config.queue_path, config.report_dir, run_log_path=config.run_log_path)
    return CycleResult(
        ran=False, claim=claim, task_run=None, report_path=report_path, message=message, gate=gate
    )


def run_one(config: "ClaudeConfig", env: Optional[dict] = None) -> CycleResult:
    """Run at most one Nightshift Claude cycle: preflight -> claim -> execute
    -> acceptance -> transition -> report -> stop.

    Never loops, never polls, never sleeps waiting for more work, never
    processes a second task, and never automatically retries within this
    call beyond whatever single deterministic transition queue.py's own
    state machine performs for the one claimed task. A failed preflight,
    a failed executable-integrity check, or an unsupported CLI capability
    all occur *before* claim_next() is ever called, so none of them can
    consume a task attempt.

    ``env`` defaults to this process's real os.environ (production
    behavior) and is forwarded to every preflight check this function and
    run_claude_task()/run_claude_executor() perform -- see
    run_claude_executor()'s docstring for why this exists.
    """
    gate = _auth_preflight.preflight_gate(
        config.nightshift_root,
        list(config.forbidden_paths),
        claude_command=[config.claude_executable, "auth", "status", "--json"],
        env=env,
    )
    if not gate.passed:
        reason = gate.auth.reason if gate.auth is not None else gate.isolation.reason
        return _report_only_cycle_result(
            config, message=f"preflight failed before claim: {reason}", gate=gate
        )

    integrity = verify_claude_executable(config.claude_executable)
    if not integrity.ok:
        return _report_only_cycle_result(
            config, message=f"executable integrity failed before claim: {integrity.reason}"
        )

    cap_ok, missing, cap_error = verify_cli_capabilities(config.claude_executable)
    if not cap_ok:
        return _report_only_cycle_result(
            config,
            message=(
                f"unsupported Claude CLI capabilities before claim: missing={missing} "
                f"error={cap_error}"
            ),
        )

    claim = _queue.claim_next(
        config.queue_path,
        stale_threshold_seconds=config.stale_threshold_seconds,
        lock_path=config.lock_path,
        run_log_path=config.run_log_path,
    )
    if claim.outcome != _queue.ClaimOutcome.CLAIMED:
        return _report_only_cycle_result(config, claim=claim, message="no task claimed this cycle")

    task_run = run_claude_task(config, claim.task_id, env=env)

    report_path = _queue.write_report(config.queue_path, config.report_dir, run_log_path=config.run_log_path)
    return CycleResult(ran=True, claim=claim, task_run=task_run, report_path=report_path)


# ---------------------------------------------------------------------------
# Bounded batch runner (Milestone 7D)
#
# Repeatedly calls run_one() -- the exact same shared, already-proven
# one-cycle implementation the `run-one` CLI command uses, unmodified in
# its own behavior -- until a bounded stop condition is reached. This is a
# bounded batch command, never a scheduler: it never polls, never sleeps
# waiting for work, never watches the filesystem, and never runs
# indefinitely. Every cycle still goes through run_one()'s own preflight,
# claim, fresh-process executor launch, independent acceptance, and
# Completion Invariant -- none of that is duplicated here.
# ---------------------------------------------------------------------------


class BatchStopReason(str, Enum):
    """Why run_batch() stopped. The evidence that produced it, not model
    confidence, always determines which value is used -- see run_batch()'s
    own docstring for the exact decision order.
    """

    QUEUE_EMPTY = "queue_empty"
    MAX_TASKS_REACHED = "max_tasks_reached"
    MAX_RUNTIME_REACHED = "max_runtime_reached"
    MAX_CONSECUTIVE_FAILURES_REACHED = "max_consecutive_failures_reached"
    AUTHENTICATION_FAILURE = "authentication_failure"
    PREFLIGHT_FAILURE = "preflight_failure"
    POLICY_FAILURE = "policy_failure"
    ISOLATION_FAILURE = "isolation_failure"
    INTERNAL_ERROR = "internal_error"


DEFAULT_MAX_TASKS = 3
DEFAULT_MAX_RUNTIME_SECONDS = 7200
DEFAULT_MAX_CONSECUTIVE_FAILURES = 1

# Conservative hard validation bounds -- never silently clamped to, always
# enforced as an explicit rejection of anything outside them.
MAX_TASKS_HARD_CAP = 10
MAX_RUNTIME_SECONDS_HARD_CAP = 28800


def _validate_batch_limits(max_tasks, max_runtime_seconds, max_consecutive_failures) -> None:
    """Reject invalid limits explicitly. Never an unlimited sentinel, never
    zero-as-unlimited, never a negative value, never silent clamping.
    """
    for name, value in (
        ("max_tasks", max_tasks),
        ("max_runtime_seconds", max_runtime_seconds),
        ("max_consecutive_failures", max_consecutive_failures),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{name} must be an integer, got {value!r}")

    if not (1 <= max_tasks <= MAX_TASKS_HARD_CAP):
        raise ConfigError(
            f"max_tasks must be between 1 and {MAX_TASKS_HARD_CAP}, got {max_tasks}"
        )
    if not (1 <= max_runtime_seconds <= MAX_RUNTIME_SECONDS_HARD_CAP):
        raise ConfigError(
            "max_runtime_seconds must be between 1 and "
            f"{MAX_RUNTIME_SECONDS_HARD_CAP}, got {max_runtime_seconds}"
        )
    if not (1 <= max_consecutive_failures <= max_tasks):
        raise ConfigError(
            "max_consecutive_failures must be between 1 and max_tasks "
            f"({max_tasks}), got {max_consecutive_failures}"
        )


@dataclasses.dataclass(frozen=True)
class CycleOutcome:
    """One sanitized, structured summary of a single attempted (claimed) cycle.

    Only ever built from fields queue.py's own dataclasses already expose --
    never raw stdout/stderr, never a raw Claude JSON result, never
    credentials or auth identity. ``evidence_excerpt`` mirrors exactly what
    the durable run log itself would have recorded for this cycle (see
    queue._safe_log_excerpt()) -- the same sanitization boundary, reused,
    never a second one.
    """

    task_id: str
    executor_outcome: str
    executor_exit_code: Optional[int]
    acceptance_outcome: str
    acceptance_exit_code: Optional[int]
    transition_outcome: str
    succeeded: bool
    evidence_excerpt: Optional[str] = None

    def to_json_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "executor_outcome": self.executor_outcome,
            "executor_exit_code": self.executor_exit_code,
            "acceptance_outcome": self.acceptance_outcome,
            "acceptance_exit_code": self.acceptance_exit_code,
            "transition_outcome": self.transition_outcome,
            "succeeded": self.succeeded,
            "evidence_excerpt": self.evidence_excerpt,
        }


@dataclasses.dataclass(frozen=True)
class BatchResult:
    """The full, structured, deterministic outcome of one run_batch() call."""

    started_at: str
    ended_at: str
    elapsed_seconds: float
    max_tasks: int
    max_runtime_seconds: int
    max_consecutive_failures: int
    attempted_cycles: int
    done_count: int
    failed_count: int
    final_consecutive_failures: int
    task_ids_attempted: tuple
    cycle_outcomes: tuple
    stop_reason: BatchStopReason
    queue_observed_empty: bool
    message: Optional[str] = None

    def to_json_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "elapsed_seconds": self.elapsed_seconds,
            "limits": {
                "max_tasks": self.max_tasks,
                "max_runtime_seconds": self.max_runtime_seconds,
                "max_consecutive_failures": self.max_consecutive_failures,
            },
            "attempted_cycles": self.attempted_cycles,
            "done_count": self.done_count,
            "failed_count": self.failed_count,
            "final_consecutive_failures": self.final_consecutive_failures,
            "task_ids_attempted": list(self.task_ids_attempted),
            "cycle_outcomes": [c.to_json_dict() for c in self.cycle_outcomes],
            "stop_reason": self.stop_reason.value,
            "queue_observed_empty": self.queue_observed_empty,
            "message": self.message,
        }


def batch_exit_code(result: "BatchResult") -> int:
    """Map a BatchResult to the CLI exit-code contract (Milestone 7D).

    0: the queue was explicitly observed empty and every attempted cycle
       completed successfully -- a genuinely clean, fully drained batch.
    2: execution stopped safely on max_tasks or max_runtime, with no
       task-cycle failure and no blocking error, but queue drainage was
       never proven (more pending work may or may not remain).
    1: any task-cycle failure, the consecutive-failure limit, or a
       preflight/auth/policy/isolation/internal blocking failure.

    Never a generic success message or exit code merely because the
    controller stopped within its configured limits -- exit 2 exists
    precisely to keep "stopped within bounds" distinct from "drained".
    """
    if result.stop_reason == BatchStopReason.QUEUE_EMPTY and result.failed_count == 0:
        return 0
    if (
        result.stop_reason in (BatchStopReason.MAX_TASKS_REACHED, BatchStopReason.MAX_RUNTIME_REACHED)
        and result.failed_count == 0
    ):
        return 2
    return 1


def run_batch(
    config: "ClaudeConfig",
    max_tasks: int = DEFAULT_MAX_TASKS,
    max_runtime_seconds: int = DEFAULT_MAX_RUNTIME_SECONDS,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
    env: Optional[dict] = None,
    clock=time.monotonic,
) -> BatchResult:
    """Process multiple queued tasks by repeatedly calling run_one().

    Terminology (see the module's own milestone notes for the full
    rationale): max_tasks limits *claimed execution attempts*, not unique
    task IDs -- a retry attempt on a previously-attempted task consumes one
    more unit exactly like a first attempt would. A cycle is successful
    only when the existing Completion Invariant transitions that task to
    "done"; anything else (requeued, permanently failed, wrong owner,
    invalid state, a lock/queue-level rejection) counts as a failed cycle
    and increments consecutive_failures. A successful "done" cycle resets
    consecutive_failures to zero. An empty queue is never counted as a
    failure.

    Limits are enforced independently, all three checked before every
    claim, in this fixed order: remaining runtime, then max_tasks, then
    consecutive_failures. The total-runtime deadline is captured with
    ``clock()`` (a monotonic clock by default -- never wall-clock
    timestamps) before the very first cycle's own preflight begins, so
    preflight time is included in the budget from the start.

    Before every claim, this function computes the batch's remaining
    runtime and refuses to claim if none remains. For the cycle it does
    attempt, the *executor's* own timeout is capped to
    min(config.claude_timeout_seconds, remaining_runtime) -- never widening
    an existing smaller per-cycle timeout, only ever shrinking it to fit
    what's left of the batch budget. If that capped timeout is what
    actually expires during execution, run_claude_executor()'s own already-
    proven SIGTERM-then-bounded-SIGKILL process-group cleanup is what
    handles it -- unchanged, not duplicated here -- and the existing
    Completion Invariant guarantees a timed-out executor can never become
    "done" merely because acceptance happened to pass. The very next loop
    iteration then re-checks remaining runtime (now exhausted) before ever
    considering another claim.

    A pre-claim gate failure (isolation or authentication, surfaced via
    CycleResult.gate) or a pre-claim executable-integrity/CLI-capability
    failure (CycleResult.claim is None with no gate attached) stops the
    batch immediately and consumes no max_tasks unit at all, since nothing
    was ever claimed -- a failure before claim must never mutate a pending
    task, exactly like a single run_one() call already guarantees. Any
    claim outcome other than CLAIMED/NO_PENDING_TASK (a busy or held lock,
    a malformed queue, an internal failure) is treated as a blocking
    internal error and also stops the batch immediately.

    When a claimed cycle's own failure was specifically an
    ExecutorOutcome/AcceptanceOutcome.POLICY_REJECTED, and that failure is
    what pushes consecutive_failures to the configured limit, the more
    specific BatchStopReason.POLICY_FAILURE is used instead of the generic
    MAX_CONSECUTIVE_FAILURES_REACHED -- reusing the existing, already more
    specific outcome enum rather than inventing a parallel classification.

    Never polls, never sleeps waiting for work, never watches the
    filesystem: every iteration either claims and processes exactly one
    task via run_one() or stops immediately on an observed empty queue or a
    limit/failure condition.
    """
    _validate_batch_limits(max_tasks, max_runtime_seconds, max_consecutive_failures)

    batch_start_monotonic = clock()
    batch_start_wall = _utcnow().isoformat()

    attempted_cycles = 0
    done_count = 0
    failed_count = 0
    consecutive_failures = 0
    last_failure_was_policy = False
    task_ids_attempted = []
    cycle_outcomes = []
    stop_reason = None
    queue_observed_empty = False
    message = None

    while True:
        elapsed = clock() - batch_start_monotonic
        remaining_runtime = max_runtime_seconds - elapsed

        if remaining_runtime <= 0:
            stop_reason = BatchStopReason.MAX_RUNTIME_REACHED
            break
        if attempted_cycles >= max_tasks:
            stop_reason = BatchStopReason.MAX_TASKS_REACHED
            break
        if consecutive_failures >= max_consecutive_failures:
            stop_reason = (
                BatchStopReason.POLICY_FAILURE
                if last_failure_was_policy
                else BatchStopReason.MAX_CONSECUTIVE_FAILURES_REACHED
            )
            break

        cycle_config = dataclasses.replace(
            config, claude_timeout_seconds=min(config.claude_timeout_seconds, remaining_runtime)
        )

        result = run_one(cycle_config, env=env)

        if result.gate is not None and not result.gate.passed:
            # Pre-claim isolation/auth failure -- never consumes a max_tasks
            # unit; nothing was ever claimed.
            stop_reason = (
                BatchStopReason.ISOLATION_FAILURE
                if result.gate.auth is None
                else BatchStopReason.AUTHENTICATION_FAILURE
            )
            message = result.message
            break

        if result.claim is None:
            # Executable-integrity or CLI-capability failure -- also
            # pre-claim, also consumes no max_tasks unit.
            stop_reason = BatchStopReason.PREFLIGHT_FAILURE
            message = result.message
            break

        if result.claim.outcome == _queue.ClaimOutcome.NO_PENDING_TASK:
            queue_observed_empty = True
            stop_reason = BatchStopReason.QUEUE_EMPTY
            break

        if result.claim.outcome != _queue.ClaimOutcome.CLAIMED:
            # LOCK_HELD, LOCK_BUSY, MALFORMED_QUEUE, or INTERNAL_FAILURE.
            stop_reason = BatchStopReason.INTERNAL_ERROR
            message = result.claim.message
            break

        # A task was genuinely claimed -- this consumes exactly one
        # max_tasks unit, whether it succeeds or not, and whether it is a
        # first attempt or a retry of a previously-attempted task.
        attempted_cycles += 1
        task_id = result.claim.task_id
        task_ids_attempted.append(task_id)

        task_run = result.task_run
        executor = task_run.executor
        acceptance = task_run.acceptance_run.acceptance
        transition = task_run.acceptance_run.transition
        succeeded = transition.outcome == _queue.TransitionOutcome.DONE

        cycle_outcomes.append(
            CycleOutcome(
                task_id=task_id,
                executor_outcome=executor.outcome.value,
                executor_exit_code=executor.exit_code,
                acceptance_outcome=acceptance.reason.value,
                acceptance_exit_code=acceptance.exit_code,
                transition_outcome=transition.outcome.value,
                succeeded=succeeded,
                evidence_excerpt=_queue._safe_log_excerpt(executor),
            )
        )

        if succeeded:
            done_count += 1
            consecutive_failures = 0
            last_failure_was_policy = False
        else:
            failed_count += 1
            consecutive_failures += 1
            last_failure_was_policy = (
                executor.outcome == _queue.ExecutorOutcome.POLICY_REJECTED
                or acceptance.reason == _queue.AcceptanceOutcome.POLICY_REJECTED
            )

    ended_at = _utcnow().isoformat()
    elapsed_seconds = clock() - batch_start_monotonic

    return BatchResult(
        started_at=batch_start_wall,
        ended_at=ended_at,
        elapsed_seconds=elapsed_seconds,
        max_tasks=max_tasks,
        max_runtime_seconds=max_runtime_seconds,
        max_consecutive_failures=max_consecutive_failures,
        attempted_cycles=attempted_cycles,
        done_count=done_count,
        failed_count=failed_count,
        final_consecutive_failures=consecutive_failures,
        task_ids_attempted=tuple(task_ids_attempted),
        cycle_outcomes=tuple(cycle_outcomes),
        stop_reason=stop_reason,
        queue_observed_empty=queue_observed_empty,
        message=message,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point: `python3 -m nightshift.runtime.claude_executor run-one --config <path>`.

    Runs at most one Nightshift Claude cycle then exits -- no loop, no
    polling, no scheduler, no daemon mode. Exit code 0 means the cycle
    itself completed (even if the task ended up retried/failed -- that is
    a normal, expected outcome, not an operational error). Exit code 1
    means the configuration file or its contents were themselves invalid.
    """
    parser = argparse.ArgumentParser(prog="nightshift.runtime.claude_executor")
    sub = parser.add_subparsers(dest="command", required=True)

    run_one_parser = sub.add_parser("run-one")
    run_one_parser.add_argument("--config", required=True, help="path to a JSON ClaudeConfig file")

    run_batch_parser = sub.add_parser("run-batch")
    run_batch_parser.add_argument("--config", required=True, help="path to a JSON ClaudeConfig file")
    run_batch_parser.add_argument("--max-tasks", type=int, default=DEFAULT_MAX_TASKS)
    run_batch_parser.add_argument(
        "--max-runtime-seconds", type=int, default=DEFAULT_MAX_RUNTIME_SECONDS
    )
    run_batch_parser.add_argument(
        "--max-consecutive-failures", type=int, default=DEFAULT_MAX_CONSECUTIVE_FAILURES
    )

    args = parser.parse_args(argv)

    if args.command == "run-one":
        try:
            with open(args.config, "r", encoding="utf-8") as f:
                raw_config = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"ran": False, "message": f"could not read config: {exc}"}))
            return 1

        try:
            config = ClaudeConfig.load(raw_config)
        except ConfigError as exc:
            print(json.dumps({"ran": False, "message": f"invalid configuration: {exc}"}))
            return 1

        result = run_one(config)
        print(json.dumps(result.to_json_dict(), indent=2))
        return 0

    if args.command == "run-batch":
        try:
            with open(args.config, "r", encoding="utf-8") as f:
                raw_config = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"stop_reason": "internal_error", "message": f"could not read config: {exc}"}))
            return 1

        try:
            config = ClaudeConfig.load(raw_config)
        except ConfigError as exc:
            print(json.dumps({"stop_reason": "internal_error", "message": f"invalid configuration: {exc}"}))
            return 1

        try:
            result = run_batch(
                config,
                max_tasks=args.max_tasks,
                max_runtime_seconds=args.max_runtime_seconds,
                max_consecutive_failures=args.max_consecutive_failures,
            )
        except ConfigError as exc:
            print(json.dumps({"stop_reason": "internal_error", "message": f"invalid batch limits: {exc}"}))
            return 1

        print(json.dumps(result.to_json_dict(), indent=2))
        return batch_exit_code(result)

    return 1


if __name__ == "__main__":
    sys.exit(main())
