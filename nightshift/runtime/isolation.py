"""VPS isolation contract for Diana Nightshift.

Documents, and deterministically validates the checkable part of, the
workspace-separation contract required before any real Claude executor is
permitted to run on a host that also serves a production deployment (the
motivating case: a Tencent VPS that also hosts production ResearchLens).

What this module can and cannot prove
--------------------------------------
This module runs as one Python process and can only validate what is
observable from inside it: whether a configured Nightshift workspace root
overlaps, contains, or is symlinked into a named production path. That is
useful and real -- it catches a misconfigured or typo'd workspace root
before anything runs -- but it is **not** OS-level isolation and must never
be described as equivalent to it. Actual isolation (a dedicated Linux user,
its own home directory, kernel-enforced file permissions that make a
production credential file genuinely unreadable to the nightshift user)
can only be created by root/sudo actions on the real host. This milestone
does not execute those actions; see docs/nightshift/VPS_SETUP.md for the
exact, supervised commands a human runs separately, and the "Adversarial
review" notes in this milestone's final report for why cwd/path checks
alone are not a substitute.

Concretely, this module's ALLOWED verdict means: "the workspace root, as
configured, does not overlap any of the production paths we were told
about, by literal path or by symlink." It does not mean: "the nightshift
Linux user cannot read those paths" -- that depends entirely on real
filesystem permissions this process does not and cannot set.
"""

from __future__ import annotations

import dataclasses
import os
from enum import Enum
from typing import Optional, Sequence


class IsolationOutcome(str, Enum):
    ALLOWED = "allowed"
    REJECTED = "rejected"


@dataclasses.dataclass(frozen=True)
class IsolationDecision:
    outcome: IsolationOutcome
    resolved_nightshift_root: Optional[str] = None
    reason: Optional[str] = None

    @property
    def allowed(self) -> bool:
        return self.outcome == IsolationOutcome.ALLOWED

    def to_json_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "resolved_nightshift_root": self.resolved_nightshift_root,
            "reason": self.reason,
        }


def validate_isolation(
    nightshift_root: str, forbidden_paths: Sequence[str]
) -> IsolationDecision:
    """Require ``nightshift_root`` to share nothing with any ``forbidden_paths`` entry.

    Both the workspace root and every forbidden path are resolved with
    os.path.realpath before comparison, so a symlink pointing from the
    workspace into a forbidden path (or vice versa) is caught the same way
    a literal overlap is -- exactly like policy.validate_working_dir's
    containment check, just checking for *absence* of overlap instead of
    presence of containment.

    Fails closed, deliberately, in cases that could otherwise look like a
    false "all clear":

    - an empty ``forbidden_paths`` list is rejected outright -- nothing was
      configured to check against, so "no overlap found" would be a
      meaningless, not a safe, answer;
    - any forbidden-path entry that doesn't exist on disk is rejected --
      most plausibly a typo in the config, and a path that silently checks
      nothing is worse than an error asking a human to fix it.
    """
    if not isinstance(nightshift_root, str) or not nightshift_root:
        return IsolationDecision(
            outcome=IsolationOutcome.REJECTED, reason="nightshift_root is empty"
        )
    if not os.path.isdir(nightshift_root):
        return IsolationDecision(
            outcome=IsolationOutcome.REJECTED,
            reason=f"nightshift_root {nightshift_root!r} is not an existing directory",
        )
    if not forbidden_paths:
        return IsolationDecision(
            outcome=IsolationOutcome.REJECTED,
            reason="no forbidden/production paths were configured to check against -- "
            "fail closed rather than assume isolation",
        )

    resolved_root = os.path.realpath(nightshift_root)

    for forbidden in forbidden_paths:
        if not isinstance(forbidden, str) or not forbidden:
            return IsolationDecision(
                outcome=IsolationOutcome.REJECTED,
                reason="a forbidden path entry is empty -- malformed isolation config",
            )
        if not os.path.exists(forbidden):
            return IsolationDecision(
                outcome=IsolationOutcome.REJECTED,
                reason=(
                    f"forbidden path {forbidden!r} does not exist -- cannot verify no "
                    "overlap, fail closed rather than silently skip the check"
                ),
            )
        resolved_forbidden = os.path.realpath(forbidden)
        if (
            resolved_root == resolved_forbidden
            or resolved_root.startswith(resolved_forbidden + os.sep)
            or resolved_forbidden.startswith(resolved_root + os.sep)
        ):
            return IsolationDecision(
                outcome=IsolationOutcome.REJECTED,
                resolved_nightshift_root=resolved_root,
                reason=(
                    f"nightshift_root {nightshift_root!r} (resolved: {resolved_root!r}) "
                    f"overlaps forbidden/production path {forbidden!r} "
                    f"(resolved: {resolved_forbidden!r})"
                ),
            )

    return IsolationDecision(
        outcome=IsolationOutcome.ALLOWED, resolved_nightshift_root=resolved_root
    )
