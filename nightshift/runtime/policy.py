"""Deterministic command-policy security boundary for Diana Nightshift.

Milestones 3-4A let a task's executor_command/acceptance_command run as an
argv list (never a shell string) in a bounded working directory with a
timeout -- but placed no restriction on *what* those commands could do, and
inherited this process's *entire* environment into the child, credentials
included. This module closes that gap for generic executor/acceptance
commands. It does not, and cannot, make task-contract commands safe to run
from an untrusted author -- see the module-level "Threat model" note below.

This is intentionally denylist-based, not an allowlist of specific
binaries: the whole Nightshift test suite (and this milestone's own
required tests) depends on running `python3 -c ...` / `node -e ...` for
ordinary bounded work, and neither is a shell interpreter in the sense the
enumerated denylist means (sh/bash/zsh/fish/dash/powershell). "Default
deny" is enforced structurally instead: executable-resolution failure
denies, a malformed policy config denies, and the enumerated dangerous
executables/operations deny by name -- not "deny everything not on an
allowlist of binaries".

Threat model
------------
This is a boundary for a **trusted task author** running **generic**
commands unattended, not a sandbox for an **untrusted** task. It reduces
the blast radius of an honest mistake or a lazily-broad command, and it
blocks the specific dangerous operations enumerated in this milestone's
scope. It is not equivalent to container isolation or a Linux namespace
sandbox: it does not prevent reading arbitrary files the OS-level user can
read, does not prevent all indirect network access (e.g. a package
manager's own transitive network calls once its *invocation* passes this
policy accidentally, or a language-level eval such as `python3 -c
"urllib.request.urlopen(...)"`), does not resolve Git aliases before
checking argv for "push", and does not defend against PATH poisoning by an
attacker who already has write access to a directory earlier in PATH than
the resolved executable. See the module's own test suite's adversarial
review notes for the complete, explicit list of what remains possible.
"""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
from enum import Enum
from typing import Optional, Sequence


class PolicyOutcome(str, Enum):
    ALLOWED = "allowed"
    REJECTED = "rejected"


@dataclasses.dataclass(frozen=True)
class PolicyDecision:
    outcome: PolicyOutcome
    resolved_executable: Optional[str] = None
    reason: Optional[str] = None

    @property
    def allowed(self) -> bool:
        return self.outcome == PolicyOutcome.ALLOWED

    def to_json_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "resolved_executable": self.resolved_executable,
            "reason": self.reason,
        }


def resolve_executable(argv0: str, cwd: str) -> Optional[str]:
    """Resolve argv[0] to the actual absolute path that would be exec'd.

    Mirrors what the OS/subprocess would do: a name containing a path
    separator is resolved relative to ``cwd`` (or used as-is if already
    absolute); a bare name is looked up on PATH via shutil.which. The
    result is canonicalized with os.path.realpath, which also resolves any
    symlink in the resolved path. Returns None if resolution fails --
    callers must treat that as a deny, not "try to launch anyway".
    """
    if not argv0:
        return None
    if os.sep in argv0 or (os.altsep and os.altsep in argv0):
        candidate = argv0 if os.path.isabs(argv0) else os.path.join(cwd, argv0)
        if not os.path.isfile(candidate):
            return None
        return os.path.realpath(candidate)
    found = shutil.which(argv0)
    if found is None:
        return None
    return os.path.realpath(found)


# ---------------------------------------------------------------------------
# Denylist rules. Each rule is a predicate(argv, basename) -> str | None: it
# returns a human-readable rejection reason, or None to let evaluation fall
# through to the next rule.
# ---------------------------------------------------------------------------

_SHELL_INTERPRETER_NAMES = frozenset({"sh", "bash", "zsh", "fish", "dash", "powershell", "pwsh"})

# `env` can rewrite a child's environment (or chain to another executable)
# independent of whatever env dict this module builds and passes to
# subprocess -- treated the same as a shell wrapper for this reason.
_ENV_WRAPPER_NAMES = frozenset({"env"})

_ALWAYS_DENIED_NAMES = frozenset(
    {
        "sudo",
        "su",
        "ssh",
        "scp",
        "curl",
        "wget",
        "nc",
        "netcat",
        "ncat",
        "apt",
        "apt-get",
        "docker",
        "kubectl",
        "systemctl",
        "crontab",
        "rm",
    }
)

_REMOTE_RSYNC_TARGET_RE = re.compile(r"(^[^/@]+@[^:/]+:)|(^[a-zA-Z0-9_.-]+::)|(^rsync://)")


def _rule_shell_interpreter(argv: Sequence[str], basename: str) -> Optional[str]:
    if basename in _SHELL_INTERPRETER_NAMES:
        return f"shell interpreter {basename!r} is denied for MVP"
    return None


def _rule_env_wrapper(argv: Sequence[str], basename: str) -> Optional[str]:
    if basename in _ENV_WRAPPER_NAMES:
        return f"{basename!r} can rewrite a child's environment and is denied"
    return None


def _rule_always_denied(argv: Sequence[str], basename: str) -> Optional[str]:
    if basename in _ALWAYS_DENIED_NAMES:
        return f"executable {basename!r} is denied by policy"
    return None


def _rule_rsync_remote(argv: Sequence[str], basename: str) -> Optional[str]:
    if basename != "rsync":
        return None
    for arg in argv[1:]:
        if _REMOTE_RSYNC_TARGET_RE.search(arg):
            return "rsync to a remote target is denied"
    return None


def _rule_pip_install(argv: Sequence[str], basename: str) -> Optional[str]:
    if basename in ("pip", "pip3") and "install" in argv[1:]:
        return f"{basename!r} install is denied (package installation is out of scope)"
    return None


def _rule_uv_install(argv: Sequence[str], basename: str) -> Optional[str]:
    if basename == "uv" and ("add" in argv[1:] or "install" in argv[1:]):
        return "'uv add'/'uv pip install' is denied (package installation is out of scope)"
    return None


def _rule_npm_like_install(argv: Sequence[str], basename: str) -> Optional[str]:
    if basename in ("npm", "pnpm") and any(a in ("install", "i", "add") for a in argv[1:]):
        return f"{basename!r} install is denied (package installation is out of scope)"
    if basename == "yarn" and any(a in ("add", "install") for a in argv[1:]):
        return "'yarn add'/'yarn install' is denied (package installation is out of scope)"
    return None


def _rule_gh_write_operations(argv: Sequence[str], basename: str) -> Optional[str]:
    if basename != "gh":
        return None
    rest = argv[1:]
    if "pr" in rest and "merge" in rest:
        return "'gh pr merge' is denied"
    if "api" in rest:
        for flag in ("-X", "--method"):
            if flag in rest:
                idx = rest.index(flag)
                if idx + 1 < len(rest) and rest[idx + 1].upper() in ("POST", "PUT", "PATCH", "DELETE"):
                    return "'gh api' write operations are denied"
    return None


_GENERIC_DENY_RULES = (
    _rule_shell_interpreter,
    _rule_env_wrapper,
    _rule_always_denied,
    _rule_rsync_remote,
    _rule_pip_install,
    _rule_uv_install,
    _rule_npm_like_install,
    _rule_gh_write_operations,
)


def _rule_git_push(argv: Sequence[str], basename: str) -> Optional[str]:
    if basename == "git" and "push" in argv[1:]:
        return "'git push' (including --force) is denied"
    return None


def _rule_git_reset_hard(argv: Sequence[str], basename: str) -> Optional[str]:
    if basename == "git" and "reset" in argv[1:] and "--hard" in argv[1:]:
        return "'git reset --hard' is denied"
    return None


def _rule_git_clean(argv: Sequence[str], basename: str) -> Optional[str]:
    if basename == "git" and "clean" in argv[1:]:
        return "'git clean' is denied"
    return None


def _rule_git_denied_entirely(argv: Sequence[str], basename: str) -> Optional[str]:
    if basename == "git":
        return "git is not permitted for acceptance checks"
    return None


@dataclasses.dataclass(frozen=True)
class CommandPolicy:
    """A named set of deny rules applied to one phase's command.

    Executor and acceptance intentionally get different CommandPolicy
    instances -- see EXECUTOR_POLICY / ACCEPTANCE_POLICY below.
    """

    name: str
    deny_rules: tuple

    def evaluate(self, argv: Sequence[str], cwd: str) -> PolicyDecision:
        if not argv or not isinstance(argv[0], str) or not argv[0]:
            return PolicyDecision(outcome=PolicyOutcome.REJECTED, reason="empty or invalid argv[0]")

        resolved = resolve_executable(argv[0], cwd)
        if resolved is None:
            return PolicyDecision(
                outcome=PolicyOutcome.REJECTED,
                reason=f"could not resolve executable {argv[0]!r} (default deny)",
            )

        # Judge on both the literal argv[0] and the resolved basename: a
        # relative/aliased invocation and its resolved target should not be
        # able to disagree about identity for the purpose of these checks.
        candidates = {os.path.basename(argv[0]).lower(), os.path.basename(resolved).lower()}
        for basename in candidates:
            for rule in self.deny_rules:
                reason = rule(argv, basename)
                if reason is not None:
                    return PolicyDecision(
                        outcome=PolicyOutcome.REJECTED, resolved_executable=resolved, reason=reason
                    )

        return PolicyDecision(outcome=PolicyOutcome.ALLOWED, resolved_executable=resolved)


EXECUTOR_POLICY = CommandPolicy(
    name="executor",
    deny_rules=_GENERIC_DENY_RULES + (_rule_git_push, _rule_git_reset_hard, _rule_git_clean),
)

ACCEPTANCE_POLICY = CommandPolicy(
    name="acceptance",
    # Acceptance is a deterministic check command (tests/lint/build); it has
    # no legitimate reason to touch version control at all, so git is
    # denied outright here rather than only its dangerous subcommands.
    deny_rules=_GENERIC_DENY_RULES + (_rule_git_denied_entirely,),
)


def check_command(argv: Sequence[str], cwd: str, policy: CommandPolicy) -> PolicyDecision:
    """Evaluate ``argv`` against ``policy``. Never launches anything itself."""
    return policy.evaluate(argv, cwd)


# ---------------------------------------------------------------------------
# Environment allowlist
# ---------------------------------------------------------------------------

DEFAULT_SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

_ENV_ALLOWLIST_NAMES = ("PATH", "LANG", "LC_ALL", "TMPDIR")

_SENSITIVE_ENV_EXACT = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_API_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        # Not caught by the substring rules below (no TOKEN/SECRET/PASSWORD/
        # KEY/CREDENTIAL marker in the name) but a database URL routinely
        # embeds a plaintext username/password -- e.g. postgres://user:pw@host/db.
        "DATABASE_URL",
    }
)

_SENSITIVE_ENV_SUBSTRINGS = ("TOKEN", "SECRET", "PASSWORD", "KEY", "CREDENTIAL")


def is_sensitive_env_name(name: str) -> bool:
    if name in _SENSITIVE_ENV_EXACT:
        return True
    upper = name.upper()
    return any(marker in upper for marker in _SENSITIVE_ENV_SUBSTRINGS)


def build_allowed_env(
    parent_env: Optional[dict] = None, extra: Optional[dict] = None
) -> dict:
    """Build a child environment by allowlist, from empty -- never a filtered copy.

    Only PATH, LANG, LC_ALL, TMPDIR are carried over from ``parent_env``
    (defaulting to os.environ) when present; PATH falls back to a fixed,
    minimal safe default if the parent doesn't have one. HOME is
    deliberately excluded by default -- it is the root of ~/.aws, ~/.ssh,
    ~/.config/gh, ~/.netrc, and a bounded command rarely needs it.
    ``extra`` (explicit, trusted, task-specific variables) is applied last
    and is itself still screened: anything matching is_sensitive_env_name()
    is dropped even if the caller passed it, since "explicitly harmless"
    must be judged by name, not merely by the caller's intent.
    """
    if parent_env is None:
        parent_env = os.environ

    env = {}
    for name in _ENV_ALLOWLIST_NAMES:
        if name in parent_env:
            env[name] = parent_env[name]
    env.setdefault("PATH", DEFAULT_SAFE_PATH)

    if extra:
        for name, value in extra.items():
            if is_sensitive_env_name(name):
                continue
            env[name] = value

    return env


# ---------------------------------------------------------------------------
# Working-directory boundary
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class WorkingDirDecision:
    outcome: PolicyOutcome
    resolved_working_dir: Optional[str] = None
    reason: Optional[str] = None

    @property
    def allowed(self) -> bool:
        return self.outcome == PolicyOutcome.ALLOWED

    def to_json_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "resolved_working_dir": self.resolved_working_dir,
            "reason": self.reason,
        }


def validate_working_dir(working_dir: str, approved_root: str) -> WorkingDirDecision:
    """Canonicalize ``working_dir`` and require it to stay within ``approved_root``.

    Both paths are resolved with os.path.realpath, which also resolves
    symlinks -- so a working_dir that is, or contains, a symlink pointing
    outside approved_root is caught the same way a literal ``..`` escape
    is: the *resolved* path is what is checked, never the as-written one.

    This bounds where the *command we launch* starts and what argv-relative
    paths naturally resolve against -- it is not a filesystem sandbox. A
    command given an absolute path elsewhere on the filesystem (or one the
    OS-level user can read/write at all) is not prevented from touching it
    by this check alone; see the module docstring's threat model.
    """
    if not isinstance(working_dir, str) or not working_dir:
        return WorkingDirDecision(outcome=PolicyOutcome.REJECTED, reason="working_dir is empty")
    if not isinstance(approved_root, str) or not approved_root:
        return WorkingDirDecision(outcome=PolicyOutcome.REJECTED, reason="approved_root is empty")
    if not os.path.isdir(approved_root):
        return WorkingDirDecision(
            outcome=PolicyOutcome.REJECTED,
            reason=f"approved_root {approved_root!r} is not an existing directory",
        )
    if not os.path.isdir(working_dir):
        return WorkingDirDecision(
            outcome=PolicyOutcome.REJECTED,
            reason=f"working_dir {working_dir!r} is not an existing directory",
        )

    resolved_root = os.path.realpath(approved_root)
    resolved_working_dir = os.path.realpath(working_dir)

    if resolved_working_dir != resolved_root and not resolved_working_dir.startswith(
        resolved_root + os.sep
    ):
        return WorkingDirDecision(
            outcome=PolicyOutcome.REJECTED,
            resolved_working_dir=resolved_working_dir,
            reason=(
                f"working_dir {working_dir!r} resolves to {resolved_working_dir!r}, "
                f"which is outside approved_root {approved_root!r} (resolved: {resolved_root!r})"
            ),
        )

    return WorkingDirDecision(outcome=PolicyOutcome.ALLOWED, resolved_working_dir=resolved_working_dir)
