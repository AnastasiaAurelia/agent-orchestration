#!/usr/bin/env python3
"""Diana autonomy: the bounded autonomy policy, as a closed schema.

## Why this is a document and not a set of flags

An autonomy policy decides whether Diana may start work a human did not
individually approve. That makes it authority-relevant, and roadmap invariant 4
requires a closed schema wherever a decision reads one: an unknown field
recorded rather than refused is a side channel, and here the side channel would
be "autonomy quietly did more than the approval described".

So the policy is a document with a fixed key set, it is digest-bound into the
proposal the human approves, and changing ANY field changes that digest.

## Why the deny list cannot be switched off

`deny` is not configuration. Every entry is a capability Diana never grants
autonomously, and a policy that set one to `False` would be an authority
expansion expressed as a setting -- exactly the shape this whole mechanism
exists to make impossible. `validate` therefore refuses a policy whose deny
entries are anything but `True`. They are written down rather than implied so
the human approving a standing approval can SEE what autonomy will never do.

## Why disabled is the default, and byte-identical to today

`manual()` returns a policy with `enabled=False`, and the proposal builder omits
the autonomy digest entirely for it. A manual proposal therefore hashes to
exactly the bytes it hashed to before this module existed -- backward
compatibility proven by construction rather than by inspection.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "runtime"))
import contract as _contract  # noqa: E402

POLICY_VERSION = 1

POLICY_KEYS = ("policy_version", "enabled", "allow", "deny", "limits")

# What autonomy MAY do, each independently approvable. Every entry is a boolean
# and every one of them is narrower than "do whatever recovers the run".
ALLOW_KEYS = (
    "local_code_edits",
    "tests",
    "same_scope_retries",
    "narrower_child_runs",
    "task_splitting",
    "preserve_verified_changes",
    "revert_owned_unverified_changes",
    "dependency_changes",
)

# What autonomy NEVER does. Fixed at True; see the module docstring.
DENY_KEYS = (
    "deploy",
    "merge",
    "credentials",
    "network_writes",
    "git_history_rewrite",
    "policy_mutation",
    "authority_expansion",
)

# Every bound is finite and every bound is mandatory. An absent limit would be
# an unbounded recovery loop, which is the failure mode a recovery loop has.
LIMIT_KEYS = (
    "max_child_runs",
    "max_child_depth",
    "max_total_attempts",
    "max_wall_clock_seconds",
    "max_changed_files",
    "max_supervisor_calls",
)

# Conservative. A first autonomous run should finish or stop, not explore.
DEFAULT_ALLOW = {
    "local_code_edits": True,
    "tests": True,
    "same_scope_retries": True,
    "narrower_child_runs": True,
    "task_splitting": True,
    "preserve_verified_changes": True,
    "revert_owned_unverified_changes": True,
    # Off by default: a dependency change reaches outside the repository's own
    # source, and authorising it is a decision a human makes once, explicitly.
    "dependency_changes": False,
}
DEFAULT_LIMITS = {
    "max_child_runs": 8,
    "max_child_depth": 3,
    "max_total_attempts": 24,
    "max_wall_clock_seconds": 7200,
    "max_changed_files": 50,
    "max_supervisor_calls": 12,
}


class PolicyError(ValueError):
    """A policy that cannot be interpreted deterministically."""


def manual() -> dict:
    """The policy that means "autonomy is off". Today's behaviour, exactly."""
    return build(enabled=False)


def build(*, enabled: bool = True, allow=None, limits=None) -> dict:
    """A validated policy document, or raise PolicyError."""
    document = {
        "policy_version": POLICY_VERSION,
        "enabled": bool(enabled),
        "allow": dict(DEFAULT_ALLOW) | dict(allow or {}),
        "deny": {key: True for key in DENY_KEYS},
        "limits": dict(DEFAULT_LIMITS) | dict(limits or {}),
    }
    validate(document)
    return document


def validate(document: object) -> None:
    """Closed schema, fail-closed. An unknown key or value is a refusal."""
    if not isinstance(document, dict):
        raise PolicyError("autonomy policy is not an object")
    missing = sorted(set(POLICY_KEYS) - set(document))
    extra = sorted(set(document) - set(POLICY_KEYS))
    if missing or extra:
        raise PolicyError(f"autonomy policy fields invalid: missing={missing} unexpected={extra}")
    if document["policy_version"] != POLICY_VERSION:
        raise PolicyError(
            f"policy_version {document['policy_version']!r} != {POLICY_VERSION}")
    if not isinstance(document["enabled"], bool):
        raise PolicyError("enabled must be a boolean")

    for name, keys in (("allow", ALLOW_KEYS), ("deny", DENY_KEYS)):
        block = document[name]
        if not isinstance(block, dict) or set(block) != set(keys):
            raise PolicyError(
                f"{name} fields invalid: missing={sorted(set(keys) - set(block or ()))} "
                f"unexpected={sorted(set(block or ()) - set(keys))}")
        for key in keys:
            if not isinstance(block[key], bool):
                raise PolicyError(f"{name}.{key} must be a boolean")
    # The whole point of the deny block: it is a statement, not a switch.
    disabled = sorted(k for k in DENY_KEYS if document["deny"][k] is not True)
    if disabled:
        raise PolicyError(
            f"deny entries {disabled} are not True; these capabilities are never granted "
            "autonomously and cannot be switched off by configuration")

    limits = document["limits"]
    if not isinstance(limits, dict) or set(limits) != set(LIMIT_KEYS):
        raise PolicyError(
            f"limits fields invalid: missing={sorted(set(LIMIT_KEYS) - set(limits or ()))} "
            f"unexpected={sorted(set(limits or ()) - set(LIMIT_KEYS))}")
    for key in LIMIT_KEYS:
        value = limits[key]
        if type(value) is not int or value < 1:
            raise PolicyError(
                f"limits.{key} must be a positive integer, not {value!r}; an absent or "
                "non-finite bound is an unbounded recovery loop")


def digest(document: dict) -> str:
    """The policy's identity, over the same canonical serialization contracts use."""
    validate(document)
    return _contract.digest(document)


def allows(document: dict, capability: str) -> bool:
    """Is `capability` permitted? Unknown capability is False, never True."""
    validate(document)
    if not document["enabled"]:
        return False
    if capability in DENY_KEYS:
        return False
    return bool(document["allow"].get(capability, False))
