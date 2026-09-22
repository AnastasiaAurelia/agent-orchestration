#!/usr/bin/env python3
"""Diana autonomy: proving a child run is inside the standing approval.

This is the whole authority mechanism of autonomous recovery. A supervisor may
recommend anything; a child run starts only if THIS module proves, on every
axis, that it asks for nothing the human did not already grant.

## Why it re-uses M6's projection reasoning rather than inventing one

`multiactor/projection.prove_subset` already answers "is this envelope inside
that envelope?" on four axes, and it decides containment with
`read_scope.decide` -- the same function that decides a live tool call, so the
two can never disagree about what "inside" means. The same technique is used
here, against the standing approval instead of against a single run's contract.

The axes are checked SEPARATELY and each names itself in its refusal, because
an operator reading `write-scope-not-subset` needs to know whether a tool, a
path, a command or a read root was the thing that grew.

## Why "subset" is necessary and not sufficient

Being inside the approval does not make a child legitimate. A child must also
be a RECOVERY of the root goal, be within every cumulative budget, and leave the
workspace provably safe. Those are proven elsewhere and all of them must hold.
This module answers one question completely and no other question at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "runtime"))
import escalation as _esc  # noqa: E402
import read_scope as _read_scope  # noqa: E402
import standing as _standing  # noqa: E402

# Envelope keys a child may carry at all. A key outside this set is refused
# rather than ignored: an unknown authority axis is exactly the thing a subset
# proof cannot reason about, so it must never reach one.
KNOWN_ENVELOPE_KEYS = frozenset(
    {"allowed_tools", "write_scope", "allowed_commands", "command_policy"})


def _within(path: str, scope: dict) -> tuple[bool, str]:
    """Decided by the SAME code that decides a live tool call."""
    return _read_scope.decide(path, scope)


def prove_repository(child_contract: dict, standing_doc: dict) -> None:
    """Axis 0 -- the same repository. A standing approval never crosses repos."""
    child_root = child_contract["target"]["repo_root"]
    if child_root != standing_doc["repo_root"]:
        raise _esc.Escalation(
            _esc.REPOSITORY_MISMATCH,
            f"the child names repository {child_root}, but this standing approval was "
            f"granted for {standing_doc['repo_root']}; an approval is never reusable "
            "across repositories",
            requested={"repository": child_root})


def prove_class(child_contract: dict, standing_doc: dict) -> None:
    """Axis 1 -- workflow, risk and depth are the approved ones, exactly.

    Equality rather than "no worse than": risk and depth are DERIVED (M1 D12/D13)
    and a child that derived a different class is a child doing a different kind
    of work, which is a new approval however the numbers compare.
    """
    authority = standing_doc["authority"]
    for key in ("workflow", "risk", "depth"):
        if child_contract[key] != authority[key]:
            raise _esc.Escalation(
                _esc.AUTHORITY_EXPANSION_REQUESTED,
                f"the child's {key} is {child_contract[key]!r}, and this standing "
                f"approval covers {authority[key]!r}",
                requested={key: child_contract[key]})


def prove_tools(child_env: dict, parent_env: dict) -> None:
    """Axis 2 -- tools."""
    unknown = sorted(set(child_env) - KNOWN_ENVELOPE_KEYS)
    if unknown:
        raise _esc.Escalation(
            _esc.AUTHORITY_EXPANSION_REQUESTED,
            f"the child envelope carries unknown authority axis/axes {unknown}, which "
            "no subset proof covers",
            requested={"envelope_keys": unknown})
    extra = sorted(set(child_env.get("allowed_tools") or ())
                   - set(parent_env.get("allowed_tools") or ()))
    if extra:
        raise _esc.Escalation(
            _esc.AUTHORITY_EXPANSION_REQUESTED,
            f"the child asks for tool(s) {extra}, which the standing approval does not grant",
            requested={"allowed_tools": extra})


def prove_write_scope(child_env: dict, parent_env: dict) -> None:
    """Axis 3 -- write scope. A child may drop it entirely; never widen it."""
    child_ws = child_env.get("write_scope")
    parent_ws = parent_env.get("write_scope")
    if child_ws is None:
        return
    if parent_ws is None:
        raise _esc.Escalation(
            _esc.WRITE_SCOPE_NOT_SUBSET,
            "the child declares a write scope the standing approval does not have",
            requested={"write_scope": (child_ws or {}).get("allowed_roots")})
    for root in child_ws.get("allowed_roots") or []:
        allowed, why = _within(root, parent_ws)
        if not allowed:
            raise _esc.Escalation(
                _esc.WRITE_SCOPE_NOT_SUBSET,
                f"the child asks to write {root}, which is outside the approved write "
                f"scope ({why})",
                requested={"write_scope": [root]})
    dropped = sorted(set(parent_ws.get("denied_subpaths") or ())
                     - set(child_ws.get("denied_subpaths") or ()))
    if dropped:
        raise _esc.Escalation(
            _esc.WRITE_SCOPE_NOT_SUBSET,
            f"the child drops denied_subpaths {dropped} the standing approval requires",
            requested={"denied_subpaths_dropped": dropped})


def prove_commands(child_env: dict, parent_env: dict) -> None:
    """Axis 4 -- commands, exact match. M4's rule, unchanged."""
    extra = sorted(set(child_env.get("allowed_commands") or ())
                   - set(parent_env.get("allowed_commands") or ()))
    if extra:
        raise _esc.Escalation(
            _esc.COMMAND_NOT_SUBSET,
            f"the child asks to run {extra}, which the standing approval does not grant; "
            "a command is matched exactly and never approximately",
            requested={"allowed_commands": extra})
    child_cp = child_env.get("command_policy") or {}
    parent_cp = parent_env.get("command_policy") or {}
    if not child_cp:
        return
    if not parent_cp:
        raise _esc.Escalation(
            _esc.COMMAND_NOT_SUBSET,
            "the child declares a command policy the standing approval does not have")
    ceiling, child_ceiling = parent_cp.get("max_timeout_s"), child_cp.get("max_timeout_s")
    if ceiling is not None and child_ceiling is not None and child_ceiling > ceiling:
        raise _esc.Escalation(
            _esc.COMMAND_NOT_SUBSET,
            f"the child's max_timeout_s {child_ceiling} exceeds the approved {ceiling}",
            requested={"max_timeout_s": child_ceiling})
    parent_roots = {"allowed_roots": list(parent_cp.get("workdir_roots") or []),
                    "denied_subpaths": []}
    for root in child_cp.get("workdir_roots") or []:
        allowed, why = _within(root, parent_roots)
        if not allowed:
            raise _esc.Escalation(
                _esc.COMMAND_NOT_SUBSET,
                f"the child's workdir root {root} is outside the approved roots ({why})",
                requested={"workdir_roots": [root]})


def prove_read_scope(child_contract: dict, standing_doc: dict) -> None:
    """Axis 5 -- read scope. Egress-by-transcript is an authority axis (D17)."""
    parent_read = standing_doc["authority"]["read_scope"]
    child_read = child_contract["read_scope"]
    for root in child_read.get("allowed_roots") or []:
        allowed, why = _within(root, parent_read)
        if not allowed:
            raise _esc.Escalation(
                _esc.AUTHORITY_EXPANSION_REQUESTED,
                f"the child asks to read {root}, which is outside the approved read "
                f"scope ({why})",
                requested={"read_scope": [root]})
    dropped = sorted(set(parent_read.get("denied_subpaths") or ())
                     - set(child_read.get("denied_subpaths") or ()))
    if dropped:
        raise _esc.Escalation(
            _esc.AUTHORITY_EXPANSION_REQUESTED,
            f"the child drops read denied_subpaths {dropped} the approval requires",
            requested={"read_denied_subpaths_dropped": dropped})


def prove_within_standing(child_contract: dict, standing_doc: dict) -> dict:
    """Prove a child contract is inside the standing approval on EVERY axis.

    Returns a record of what was proven, for the audit trail. Raises Escalation
    naming the axis that grew. There is no partial success and no clamping: a
    child that exceeds the approval is refused, never narrowed to fit, because a
    clamped over-broad child is an over-broad child that reported green.
    """
    _standing.validate(standing_doc)
    prove_repository(child_contract, standing_doc)
    prove_class(child_contract, standing_doc)
    parent_env = standing_doc["authority"]["capability_envelope"]
    child_env = child_contract["capability_envelope"]
    if not isinstance(child_env, dict):
        raise _esc.Escalation(
            _esc.AUTHORITY_EXPANSION_REQUESTED, "the child has no capability envelope")
    prove_tools(child_env, parent_env)
    prove_write_scope(child_env, parent_env)
    prove_commands(child_env, parent_env)
    prove_read_scope(child_contract, standing_doc)
    return {
        "repository": standing_doc["repo_root"],
        "workflow": child_contract["workflow"],
        "risk": child_contract["risk"],
        "depth": child_contract["depth"],
        "allowed_tools": sorted(child_env.get("allowed_tools") or ()),
        "write_roots": list((child_env.get("write_scope") or {}).get("allowed_roots") or ()),
        "allowed_commands": sorted(child_env.get("allowed_commands") or ()),
    }
