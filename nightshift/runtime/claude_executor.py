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
                           built-in set" -- replaces the tool set entirely,
                           unlike --allowedTools/--disallowedTools, which
                           read as permission-layer allow/deny on top of
                           whatever else is available. --tools is the
                           stronger, more explicit mechanism and is used
                           alone; --allowedTools/--disallowedTools are not
                           used here.
  --permission-mode <mode> choices: acceptEdits, auto, bypassPermissions,
                           manual, dontAsk, plan. bypassPermissions is
                           explicitly forbidden by this milestone; dontAsk
                           is used as the best-effort correct choice for
                           "no interactive prompt without bypassing
                           permission checks" -- its exact runtime
                           semantics were NOT independently verified in
                           this implementation session (no real Claude
                           invocation is permitted here); this is left for
                           the supervised VPS smoke test to confirm, not
                           assumed to be proven.
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
from typing import Optional, Sequence

from nightshift.runtime import auth_preflight as _auth_preflight
from nightshift.runtime import isolation as _isolation
from nightshift.runtime import policy as _policy
from nightshift.runtime import queue as _queue

REQUIRED_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep")
DEFAULT_PERMISSION_MODE = "dontAsk"
DEFAULT_CLAUDE_TIMEOUT_SECONDS = 300.0
SIGTERM_GRACE_SECONDS = 5.0
AUTH_STATUS_TIMEOUT_SECONDS = 10.0
HELP_CHECK_TIMEOUT_SECONDS = 10.0
VERSION_CHECK_TIMEOUT_SECONDS = 10.0

REQUIRED_HELP_MARKERS = (
    "--print",
    "--tools",
    "--permission-mode",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--no-session-persistence",
    "--output-format",
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
        return _queue.ExecutorResult(
            outcome=_queue.ExecutorOutcome.COMPLETED,
            command=tuple(argv),
            pid=pid,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            started_at=started_at,
            ended_at=_utcnow().isoformat(),
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
        config.queue_path, task_id, lock_path=config.lock_path, run_log_path=config.run_log_path
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

    def to_json_dict(self) -> dict:
        return {
            "ran": self.ran,
            "claim": self.claim.to_json_dict() if self.claim is not None else None,
            "task_run": self.task_run.to_json_dict() if self.task_run is not None else None,
            "report_path": self.report_path,
            "message": self.message,
        }


def _report_only_cycle_result(
    config: "ClaudeConfig", claim: Optional["_queue.ClaimResult"] = None, message: Optional[str] = None
) -> CycleResult:
    report_path = _queue.write_report(config.queue_path, config.report_dir, run_log_path=config.run_log_path)
    return CycleResult(ran=False, claim=claim, task_run=None, report_path=report_path, message=message)


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
        return _report_only_cycle_result(config, message=f"preflight failed before claim: {reason}")

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

    return 1


if __name__ == "__main__":
    sys.exit(main())
