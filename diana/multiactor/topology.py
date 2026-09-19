#!/usr/bin/env python3
"""Diana M6: the frozen actor topology (M6-D1, M6-D10, M6-E1-D4).

The topology is an M6-owned document written once by the approval path, covered
by its own digest, and re-verified on every resume -- the `run-policy.json` /
`work-items.json` precedent exactly (M5-D2, M5-E1-D2), because no frozen schema
may gain a field.

## What this document is and is not

It records which roles **exist** for a run. It does NOT record which role
**acted** in an attempt -- that lives in the journal's attempt entry and nowhere
else (M6-D7). The two cannot disagree about the same thing because they do not
state the same thing, which is why this is not the "second authoritative
actor-identity record" M6-E1-D4 forbids.

## Why the role set is closed here rather than configurable

M6-D1 freezes exactly two roles. A topology naming anything else is refused
rather than recorded: an unknown role in an accepted document would be a channel
by which a run acquires an actor nobody approved, and M6-D10 exists to close
exactly that. The set is a module constant, not a parameter.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "runtime"))
import blocking  # noqa: E402
import contract as _contract  # noqa: E402

DOCUMENT_VERSION = 1
DOCUMENT_NAME = "actors.json"

BUILDER = "BUILDER"
REVIEWER = "REVIEWER"

# M6-D1: the frozen topology. Closed, and not widenable by configuration.
FROZEN_ROLES = (BUILDER, REVIEWER)

DOCUMENT_KEYS = ("document_version", "run_id", "roles")


def build(*, run_id: str, roles=FROZEN_ROLES) -> dict:
    """Build the topology document for a run, or raise Blocked."""
    document = {
        "document_version": DOCUMENT_VERSION,
        "run_id": run_id,
        "roles": list(roles),
    }
    validate(document)
    return document


def validate(document: object) -> None:
    """Closed schema, fail-closed. An unknown key or role is a refusal."""
    if not isinstance(document, dict):
        raise blocking.Blocked(
            blocking.ACTOR_TOPOLOGY_MALFORMED, "topology document is not an object")
    missing = sorted(set(DOCUMENT_KEYS) - set(document))
    extra = sorted(set(document) - set(DOCUMENT_KEYS))
    if missing or extra:
        raise blocking.Blocked(
            blocking.ACTOR_TOPOLOGY_MALFORMED, f"missing={missing} unexpected={extra}")
    if document["document_version"] != DOCUMENT_VERSION:
        raise blocking.Blocked(
            blocking.ACTOR_TOPOLOGY_MALFORMED,
            f"document_version {document['document_version']!r} != {DOCUMENT_VERSION}")
    if not isinstance(document["run_id"], str) or not document["run_id"]:
        raise blocking.Blocked(
            blocking.ACTOR_TOPOLOGY_MALFORMED, "run_id must be a non-empty string")
    roles = document["roles"]
    if not isinstance(roles, list) or not roles:
        raise blocking.Blocked(
            blocking.ACTOR_TOPOLOGY_MALFORMED, "roles must be a non-empty list")
    if len(set(roles)) != len(roles):
        raise blocking.Blocked(
            blocking.ACTOR_TOPOLOGY_MALFORMED, f"duplicate role in {roles}")
    unknown = [r for r in roles if r not in FROZEN_ROLES]
    if unknown:
        # M6-D10: the topology cannot be model-expanded, and an unapproved role
        # is refused at the document boundary rather than at first use.
        raise blocking.Blocked(
            blocking.ACTOR_TOPOLOGY_MALFORMED,
            f"role(s) {unknown} are outside the frozen topology {list(FROZEN_ROLES)}")


def digest(document: dict) -> str:
    return _contract.digest(document)


def roles(document: dict) -> list[str]:
    return list(document["roles"])


def require_role(document: dict, role: object) -> str:
    """The one place a role name is admitted. Anything else is Blocked."""
    if not isinstance(role, str) or role not in FROZEN_ROLES:
        raise blocking.Blocked(
            blocking.ACTOR_UNKNOWN,
            f"{role!r} is not a role in the frozen topology {list(FROZEN_ROLES)}")
    if role not in document["roles"]:
        raise blocking.Blocked(
            blocking.ACTOR_UNKNOWN,
            f"role {role!r} is not declared by this run's topology {document['roles']}")
    return role
