#!/usr/bin/env python3
"""Diana M6: role projections of the ONE approved envelope (M6-D12 … M6-D15).

M6 grants no capability. Every actor acts under a **projection** of the single
approved parent contract's envelope: narrowed on every authority axis, never
widened, and proven a subset BEFORE it is installed.

## Why projections rather than per-actor contracts

Measured (Phase 0, F8/F11): the contract schema is closed and carries no actor
field, so a per-actor contract needs its own `run_id`, therefore its own
journal, therefore its own `max_attempts` and its own deadline. Their
composition would exceed the single approved envelope BY CONSTRUCTION, and
M5-D12 already says a second contract is a second approval. A projection cannot
have that failure mode, because it is derived by narrowing and is checked.

## Why a non-subset projection is refused rather than clamped

M6-D13. A clamped over-broad projection is an over-broad projection that
reported green -- which is exactly the shape M4's ERRATA-001 exists to record.
`prove_subset` raises; it never repairs, intersects, or silently drops a key.

## What actually enforces this

Nothing here enforces anything. `install` hands the projected envelope to the
UNMODIFIED M1/M4 boundary (`hermes_patches.install_capability` +
`install_confinement`), which Phase 0 F15 measured refusing `write_file`,
`patch` and `terminal` under a read-only projection in a process that had just
executed `write_file` under a builder projection. The boundary is
re-parameterised, not replaced and not duplicated.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "runtime"))
sys.path.insert(0, str(_HERE.parent / "adapters"))
sys.path.insert(0, str(_HERE.parent / "mutation"))
import blocking  # noqa: E402
import read_scope as _read_scope  # noqa: E402
import topology as _topology  # noqa: E402

BUILDER = _topology.BUILDER
REVIEWER = _topology.REVIEWER

# M6-R1: the reviewer envelope, frozen. No write_scope, no allowed_commands, no
# command_policy, and therefore no terminal, no patch, no write_file.
REVIEWER_TOOLS = ("read_file", "search_files")

# Independent-review finding R-1. `prove_subset` answers "is this within the
# APPROVAL?" and nothing else. It cannot answer "is this the boundary THIS ROLE
# was frozen with", because a role's envelope is by construction a subset of the
# approval -- so a REVIEWER projection carrying `terminal` passes every subset
# axis and was measured executing a command through the real dispatch funnel.
#
# This is the same lesson M6-A3 recorded, one level up: a probe proves the
# boundary is live, a subset proof proves it is not too wide for the approval,
# and NEITHER proves it is the right boundary for the actor about to act. That
# third question is what `prove_role_shape` answers.
def _reviewer_envelope(parent_envelope: dict) -> dict:
    return {"allowed_tools": sorted(REVIEWER_TOOLS)}


def _builder_envelope(parent_envelope: dict) -> dict:
    # M6-D12: the builder projection IS the approved envelope, exactly.
    return {k: v for k, v in parent_envelope.items()}


ROLE_ENVELOPE = {BUILDER: _builder_envelope, REVIEWER: _reviewer_envelope}


def derive(role: str, contract_block: dict, topology_doc: dict) -> dict:
    """The projected envelope + read_scope for `role`, proven ⊆ the parent.

    Returns {"role", "capability_envelope", "read_scope"}. Never returns an
    unproven projection: `prove_subset` runs before the value leaves here.
    """
    role = _topology.require_role(topology_doc, role)
    parent_env = contract_block["capability_envelope"]
    parent_read = contract_block["read_scope"]

    builder = ROLE_ENVELOPE.get(role)
    if builder is None:  # pragma: no cover - require_role refused everything else
        raise blocking.Blocked(blocking.ACTOR_UNKNOWN, f"no projection for role {role!r}")

    projection = {"role": role, "capability_envelope": builder(parent_env),
                  "read_scope": dict(parent_read)}
    prove_subset(projection, contract_block)
    prove_role_shape(projection, contract_block)
    return projection


def prove_role_shape(projection: dict, contract_block: dict) -> None:
    """Raise Blocked unless the projection is EXACTLY this role's frozen envelope.

    Independent-review finding R-1. Subset-of-the-approval is necessary and not
    sufficient: every role's envelope is a subset, so "is it a subset" cannot
    distinguish the REVIEWER's frozen `{read_file, search_files}` from a
    REVIEWER that has quietly acquired `terminal`. Both are legal subsets; only
    one is the role M6-R1 froze.

    Compared by value against the frozen definition rather than by a rule about
    it, so a projection that has been built, rebuilt, wrapped or supplied from
    anywhere must still be byte-for-byte the envelope the specification names.
    """
    role = projection["role"]
    builder = ROLE_ENVELOPE.get(role)
    if builder is None:
        raise blocking.Blocked(
            blocking.ACTOR_UNKNOWN, f"no frozen envelope for role {role!r}")
    expected_env = builder(contract_block["capability_envelope"])
    actual_env = projection["capability_envelope"]
    if actual_env != expected_env:
        raise blocking.Blocked(
            blocking.ACTOR_PROJECTION_NOT_SUBSET,
            f"{role}: projection is not this role's frozen envelope; "
            f"expected tools {sorted(expected_env.get('allowed_tools') or ())} "
            f"with keys {sorted(expected_env)}, got "
            f"{sorted(actual_env.get('allowed_tools') or ())} "
            f"with keys {sorted(actual_env)}")
    expected_read = dict(contract_block["read_scope"])
    if projection["read_scope"] != expected_read:
        raise blocking.Blocked(
            blocking.ACTOR_PROJECTION_NOT_SUBSET,
            f"{role}: projection's read_scope is not the approved read_scope")


def _root_within(child_root: str, parent_scope: dict) -> tuple[bool, str]:
    """Is `child_root` inside the parent scope? Decided by the same code that
    decides a tool call, so the two can never disagree."""
    return _read_scope.decide(child_root, parent_scope)


def prove_subset(projection: dict, contract_block: dict) -> None:
    """Raise Blocked unless the projection is ⊆ the parent on EVERY axis.

    The four axes are M6-D13's, and each is checked separately so a refusal
    names which authority axis was exceeded -- an operator reading
    `actor-projection-not-subset` needs to know whether a tool, a path, a
    command or a read root was the thing that grew.
    """
    parent_env = contract_block["capability_envelope"]
    parent_read = contract_block["read_scope"]
    env = projection["capability_envelope"]
    role = projection["role"]

    if not isinstance(env, dict) or "allowed_tools" not in env:
        raise blocking.Blocked(
            blocking.ACTOR_PROJECTION_NOT_SUBSET,
            f"{role}: projected envelope has no allowed_tools")
    unknown_keys = set(env) - {"allowed_tools", "write_scope", "allowed_commands",
                               "command_policy"}
    if unknown_keys:
        raise blocking.Blocked(
            blocking.ACTOR_PROJECTION_NOT_SUBSET,
            f"{role}: projected envelope has unknown key(s) {sorted(unknown_keys)}")

    # Axis 1 -- tools.
    extra_tools = sorted(set(env["allowed_tools"]) - set(parent_env["allowed_tools"]))
    if extra_tools:
        raise blocking.Blocked(
            blocking.ACTOR_PROJECTION_NOT_SUBSET,
            f"{role}: tool(s) {extra_tools} are not in the approved envelope")

    # Axis 2 -- write scope. A projection may drop write_scope entirely (that is
    # what makes a reviewer read-only); it may never introduce one the parent
    # lacks, widen a root, or relax a denied subpath.
    child_ws = env.get("write_scope")
    parent_ws = parent_env.get("write_scope")
    if child_ws is not None:
        if parent_ws is None:
            raise blocking.Blocked(
                blocking.ACTOR_PROJECTION_NOT_SUBSET,
                f"{role}: declares a write_scope the approved envelope does not have")
        for root in child_ws.get("allowed_roots") or []:
            allowed, why = _root_within(root, parent_ws)
            if not allowed:
                raise blocking.Blocked(
                    blocking.ACTOR_PROJECTION_NOT_SUBSET,
                    f"{role}: write root {root} is outside the approved write_scope ({why})")
        missing_denied = sorted(set(parent_ws.get("denied_subpaths") or [])
                                - set(child_ws.get("denied_subpaths") or []))
        if missing_denied:
            raise blocking.Blocked(
                blocking.ACTOR_PROJECTION_NOT_SUBSET,
                f"{role}: drops denied_subpaths {missing_denied} the approval requires")

    # Axis 3 -- commands, exact-match, M4's rule unchanged.
    extra_commands = sorted(set(env.get("allowed_commands") or ())
                            - set(parent_env.get("allowed_commands") or ()))
    if extra_commands:
        raise blocking.Blocked(
            blocking.ACTOR_PROJECTION_NOT_SUBSET,
            f"{role}: command(s) {extra_commands} are not in the approved envelope")
    child_cp = env.get("command_policy") or {}
    parent_cp = parent_env.get("command_policy") or {}
    if child_cp:
        if not parent_cp:
            raise blocking.Blocked(
                blocking.ACTOR_PROJECTION_NOT_SUBSET,
                f"{role}: declares a command_policy the approved envelope does not have")
        ceiling = parent_cp.get("max_timeout_s")
        child_ceiling = child_cp.get("max_timeout_s")
        if ceiling is not None and child_ceiling is not None and child_ceiling > ceiling:
            raise blocking.Blocked(
                blocking.ACTOR_PROJECTION_NOT_SUBSET,
                f"{role}: max_timeout_s {child_ceiling} exceeds the approved {ceiling}")
        parent_roots = {"allowed_roots": list(parent_cp.get("workdir_roots") or []),
                        "denied_subpaths": []}
        for root in child_cp.get("workdir_roots") or []:
            allowed, why = _root_within(root, parent_roots)
            if not allowed:
                raise blocking.Blocked(
                    blocking.ACTOR_PROJECTION_NOT_SUBSET,
                    f"{role}: workdir root {root} is outside the approved roots ({why})")

    # Axis 4 -- read scope.
    child_rs = projection["read_scope"]
    for root in child_rs.get("allowed_roots") or []:
        allowed, why = _root_within(root, parent_read)
        if not allowed:
            raise blocking.Blocked(
                blocking.ACTOR_PROJECTION_NOT_SUBSET,
                f"{role}: read root {root} is outside the approved read_scope ({why})")
    missing_denied = sorted(set(parent_read.get("denied_subpaths") or [])
                            - set(child_rs.get("denied_subpaths") or []))
    if missing_denied:
        raise blocking.Blocked(
            blocking.ACTOR_PROJECTION_NOT_SUBSET,
            f"{role}: drops read denied_subpaths {missing_denied} the approval requires")


def prove_union_within_parent(topology_doc: dict, contract_block: dict) -> dict:
    """M6-AC-5: the UNION of every role's projection is ⊆ the parent envelope.

    Checking each projection separately is necessary and not sufficient: the
    question an approver actually asked was what the whole run may do, and that
    is the union.
    """
    parent_env = contract_block["capability_envelope"]
    union_tools: set[str] = set()
    union_commands: set[str] = set()
    for role in _topology.roles(topology_doc):
        env = derive(role, contract_block, topology_doc)["capability_envelope"]
        union_tools |= set(env["allowed_tools"])
        union_commands |= set(env.get("allowed_commands") or ())
    extra_tools = sorted(union_tools - set(parent_env["allowed_tools"]))
    extra_commands = sorted(union_commands - set(parent_env.get("allowed_commands") or ()))
    if extra_tools or extra_commands:
        raise blocking.Blocked(
            blocking.ACTOR_PROJECTION_NOT_SUBSET,
            f"the union of all projections exceeds the approved envelope: "
            f"tools={extra_tools} commands={extra_commands}")
    return {"union_tools": sorted(union_tools), "union_commands": sorted(union_commands)}
