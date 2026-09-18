#!/usr/bin/env python3
"""Diana M5: the run policy -- M5's own duration budget (M5-D2, M5-D13, M5-D16).

## Why this is a separate document and not an envelope key

M4-D2 froze `capability_envelope`'s optional keys as exactly `write_scope`,
`allowed_commands` and `command_policy`, and `contract.validate()` rejects any
unknown envelope key. M5 must therefore not put its budget there. It follows the
M2-D5 / M3-D4 precedent instead: when a milestone needs to record something new,
it writes its own file rather than editing a frozen schema.

## Why the budget is authority-shaped even though it is not capability

The envelope says what a run MAY DO. The policy says how long it may keep doing
it and how many times it may try. Per M5-D13 an envelope that cannot expire is
one that was approved once and holds forever, which is the opposite of bounded.
So the policy is digest-covered exactly like the contract, and M5-D11's resume
re-binding verifies BOTH digests: approval covers the pair.

The policy may only ever NARROW. It grants nothing, names no tool, and has no
channel by which Hermes can reach it (M1 D14).
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runtime"))
import blocking  # noqa: E402
import contract as _contract  # noqa: E402

POLICY_VERSION = 1

POLICY_KEYS = (
    "policy_version",
    "run_id",
    "created_at",
    "max_attempts",
    "deadline_at",
    "quiescence_grace_seconds",
)

# Deliberately small defaults. A bound nobody chose is still a bound, and an
# unattended run that inherits a generous default is the failure M5-D13 names.
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_QUIESCENCE_GRACE_SECONDS = 20


def _utc(ts: _dt.datetime) -> str:
    return ts.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> _dt.datetime:
    """Parse one of our own timestamps. Any deviation raises, never guesses."""
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"not a UTC timestamp: {value!r}")
    return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=_dt.timezone.utc
    )


def build(*, run_id: str, max_attempts: int = DEFAULT_MAX_ATTEMPTS,
          total_seconds: int, created_at: str | None = None,
          quiescence_grace_seconds: int = DEFAULT_QUIESCENCE_GRACE_SECONDS) -> dict:
    """Build the run policy. `deadline_at` is ABSOLUTE, not a duration.

    Absolute because a duration has to be added to something, and the thing it
    would be added to is process start time -- which resets on every resume. A
    run whose deadline restarts with the process has no deadline at all.
    """
    now = parse_iso(created_at) if created_at else _dt.datetime.now(_dt.timezone.utc)
    policy = {
        "policy_version": POLICY_VERSION,
        "run_id": run_id,
        "created_at": _utc(now),
        "max_attempts": int(max_attempts),
        "deadline_at": _utc(now + _dt.timedelta(seconds=int(total_seconds))),
        "quiescence_grace_seconds": int(quiescence_grace_seconds),
    }
    validate(policy)
    return policy


def validate(policy: object) -> None:
    """Raise Blocked unless the policy is well-formed. Closed schema (M1 D14)."""
    if not isinstance(policy, dict):
        raise blocking.Blocked(blocking.RUN_POLICY_MALFORMED, "policy is not an object")
    missing = sorted(set(POLICY_KEYS) - set(policy))
    extra = sorted(set(policy) - set(POLICY_KEYS))
    if missing or extra:
        raise blocking.Blocked(
            blocking.RUN_POLICY_MALFORMED, f"missing={missing} unexpected={extra}")
    if policy["policy_version"] != POLICY_VERSION:
        raise blocking.Blocked(
            blocking.RUN_POLICY_MALFORMED,
            f"policy_version {policy['policy_version']!r} != {POLICY_VERSION}")
    if not isinstance(policy["run_id"], str) or not policy["run_id"]:
        raise blocking.Blocked(blocking.RUN_POLICY_MALFORMED, "run_id must be a non-empty string")
    for key in ("max_attempts", "quiescence_grace_seconds"):
        value = policy[key]
        # `bool` is an `int` in Python; a boolean attempt cap is a defect, not a 1.
        if not isinstance(value, int) or isinstance(value, bool):
            raise blocking.Blocked(blocking.RUN_POLICY_MALFORMED, f"{key} must be an integer")
    if policy["max_attempts"] < 1:
        raise blocking.Blocked(blocking.RUN_POLICY_MALFORMED, "max_attempts must be >= 1")
    if policy["quiescence_grace_seconds"] < 0:
        raise blocking.Blocked(
            blocking.RUN_POLICY_MALFORMED, "quiescence_grace_seconds must be >= 0")
    try:
        created = parse_iso(policy["created_at"])
        deadline = parse_iso(policy["deadline_at"])
    except ValueError as exc:
        raise blocking.Blocked(blocking.RUN_POLICY_MALFORMED, str(exc)) from None
    if deadline <= created:
        raise blocking.Blocked(
            blocking.RUN_POLICY_MALFORMED, "deadline_at must be after created_at")


def digest(policy: dict) -> str:
    """Same canonical serialization the contract uses, so one rule governs both."""
    return _contract.digest(policy)


def expired(policy: dict, now: _dt.datetime | None = None) -> bool:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return now >= parse_iso(policy["deadline_at"])


def seconds_remaining(policy: dict, now: _dt.datetime | None = None) -> float:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return (parse_iso(policy["deadline_at"]) - now).total_seconds()
