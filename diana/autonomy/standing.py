#!/usr/bin/env python3
"""Diana autonomy: the standing approval — one human decision, bounded.

A standing approval is the object a human approves ONCE so that Diana may start
recovery runs the human did not individually approve. It is therefore the most
authority-dense document in the system, and it is built to the same rules as
every other one: closed schema, digest-bound, re-verified before every use, and
never widened.

## Why it is not a second permission system

It is not a new grant. It is a RECORDING of the grant the human already made,
plus the bounds within which Diana may re-apply it. Everything authority-bearing
in it is copied from the approved contract by `from_contract` -- not
re-specified, not re-derived, not defaulted -- so there is no second description
of authority that could drift from the first. A child run is later proven a
SUBSET of this record (`subset.py`); nothing is ever proven a subset of the
child.

## What invalidates it

Everything authority-relevant. The digest covers the repository identity, the
original goal, the whole capability envelope, the read scope, the risk and depth
class, the executor identity, the autonomy policy and its budgets, and the
human-only conditions. Change any of them and the digest changes, which means
the standing approval no longer matches and Diana refuses to act on it.

That is what makes the document non-reusable across repositories, across an
authority expansion, across a modified autonomy policy, across a broader command
set and across a broader write scope -- all of which are, mechanically, a
different digest.

## Why the executor is bound

M6-D21 froze the executor as a seam that carries no authority, and that remains
true: a backend cannot grant itself anything. But WHICH backend a human agreed
to leave running unattended is part of what they agreed to, so it is recorded
and bound. Binding it costs nothing and closes a substitution nobody approved.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "runtime"))
import contract as _contract  # noqa: E402
import escalation as _esc  # noqa: E402
import policy as _policy  # noqa: E402

STANDING_VERSION = 1
DOCUMENT_NAME = "standing-approval.json"

DOCUMENT_KEYS = (
    "standing_version",
    "root_run_id",
    "repo_root",
    "original_goal",
    "authority",
    "executor",
    "autonomy",
    "human_only_conditions",
)

# The authority-bearing half of the approved contract, copied verbatim. These
# are the axes a child is later proven not to exceed; the list is fixed so a
# later contract field cannot silently escape the subset proof by not being
# mentioned here.
AUTHORITY_KEYS = (
    "workflow",
    "risk",
    "depth",
    "read_scope",
    "capability_envelope",
)

# Conditions that ALWAYS require a human, whatever the policy says and whatever
# a supervisor recommends. This is the list the proposal renders under
# "ALWAYS ESCALATES", so the human reads the same set Diana enforces.
HUMAN_ONLY_CONDITIONS = (
    "authority_expansion",
    "network_write",
    "deploy",
    "merge",
    "credential_access",
    "git_history_rewrite",
    "policy_mutation",
    "ambiguous_workspace_provenance",
    "unproven_within_standing_approval",
)


def from_contract(contract_block: dict, *, root_run_id: str, goal: str,
                  autonomy_policy: dict, executor: str) -> dict:
    """Record the approval that was just granted. Copies; never re-derives."""
    _policy.validate(autonomy_policy)
    if not isinstance(executor, str) or not executor.strip():
        raise _esc.Escalation(
            _esc.STANDING_APPROVAL_INVALID, "executor identity must be a non-empty string")
    document = {
        "standing_version": STANDING_VERSION,
        "root_run_id": str(root_run_id),
        "repo_root": contract_block["target"]["repo_root"],
        "original_goal": contract_block["task"],
        "authority": {key: contract_block[key] for key in AUTHORITY_KEYS},
        "executor": executor.strip(),
        "autonomy": autonomy_policy,
        "human_only_conditions": list(HUMAN_ONLY_CONDITIONS),
    }
    validate(document)
    return document


def validate(document: object) -> None:
    """Closed schema, fail-closed. Raises Escalation, never returns False."""
    if not isinstance(document, dict):
        raise _esc.Escalation(
            _esc.STANDING_APPROVAL_INVALID, "standing approval is not an object")
    missing = sorted(set(DOCUMENT_KEYS) - set(document))
    extra = sorted(set(document) - set(DOCUMENT_KEYS))
    if missing or extra:
        raise _esc.Escalation(
            _esc.STANDING_APPROVAL_INVALID,
            f"standing approval fields invalid: missing={missing} unexpected={extra}")
    if document["standing_version"] != STANDING_VERSION:
        raise _esc.Escalation(
            _esc.STANDING_APPROVAL_INVALID,
            f"standing_version {document['standing_version']!r} != {STANDING_VERSION}")
    for key in ("root_run_id", "repo_root", "original_goal", "executor"):
        if not isinstance(document[key], str) or not document[key].strip():
            raise _esc.Escalation(
                _esc.STANDING_APPROVAL_INVALID, f"{key} must be a non-empty string")
    authority = document["authority"]
    if not isinstance(authority, dict) or set(authority) != set(AUTHORITY_KEYS):
        raise _esc.Escalation(
            _esc.STANDING_APPROVAL_INVALID,
            f"authority fields invalid: missing={sorted(set(AUTHORITY_KEYS) - set(authority or ()))} "
            f"unexpected={sorted(set(authority or ()) - set(AUTHORITY_KEYS))}")
    if not isinstance(authority["capability_envelope"], dict):
        raise _esc.Escalation(
            _esc.STANDING_APPROVAL_INVALID, "authority.capability_envelope must be an object")
    if not isinstance(authority["read_scope"], dict):
        raise _esc.Escalation(
            _esc.STANDING_APPROVAL_INVALID, "authority.read_scope must be an object")
    try:
        _policy.validate(document["autonomy"])
    except _policy.PolicyError as exc:
        raise _esc.Escalation(
            _esc.STANDING_APPROVAL_INVALID, f"autonomy policy invalid: {exc}") from None
    conditions = document["human_only_conditions"]
    if list(conditions) != list(HUMAN_ONLY_CONDITIONS):
        # Not merely "a list of strings": the SET is frozen. A standing approval
        # that dropped a condition would be one whose rendered "ALWAYS ESCALATES"
        # section lied to the human who approved it.
        raise _esc.Escalation(
            _esc.STANDING_APPROVAL_INVALID,
            "human_only_conditions must be exactly the frozen set "
            f"{list(HUMAN_ONLY_CONDITIONS)}")


def digest(document: dict) -> str:
    validate(document)
    return _contract.digest(document)


def require(document: object, expected_digest: str) -> dict:
    """Re-verify a standing approval before acting on it. The only entry point.

    Nothing reads a standing approval without coming through here, so a document
    that has been edited on disk cannot become authority: its digest no longer
    matches the one the approval bound, and a mismatch escalates rather than
    being repaired.
    """
    validate(document)
    actual = _contract.digest(document)
    if actual != expected_digest:
        raise _esc.Escalation(
            _esc.STANDING_APPROVAL_DIGEST_MISMATCH,
            f"the standing approval on disk hashes to {actual}, not the approved "
            f"{expected_digest}; it was changed after approval and grants nothing",
            requested={"reapproval_of": expected_digest})
    if not document["autonomy"]["enabled"]:
        raise _esc.Escalation(
            _esc.AUTONOMY_DISABLED,
            "this run was approved in manual mode; autonomous recovery was never granted")
    return document


def enabled(document: object) -> bool:
    """Is this a usable autonomous standing approval? Never raises."""
    try:
        validate(document)
    except _esc.Escalation:
        return False
    return bool(document["autonomy"]["enabled"])
