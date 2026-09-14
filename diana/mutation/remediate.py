#!/usr/bin/env python3
"""Diana M4: the bounded remediation run (spec: HERMES-RUNTIME-M4.md).

Wires the M4 path: build an ELEVATED/D2 contract carrying the richer envelope,
install M1's confinement and M1's capability guard *with* M4's argument policy,
snapshot the target, let a real Hermes turn fix code and run the one authorized
verification command, then reconcile what actually changed against the envelope.

## What is new here, and what deliberately is not

New: `write_scope`, `allowed_commands`, and the reconciliation step.

Not new: the boundary. Policy is consulted at the same two entries M1 proved and
M2 exercised with a live model. M1's confinement is installed unchanged and does
the same job it always did -- M4 F4 showed it already refuses every out-of-scope
write, including every V4A patch header, with no modification at all.

## Why reconciliation is not the control

`write_scope` bounds the file tools. It does not bound `python3 check.py`, which
can write wherever the Diana process can. Reconciliation is how the run finds
out. It runs after the fact, it cannot prevent anything, and M4-D14 says so in
as many words. Prevention is the control; this is the audit.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("runtime", "adapters", "profile", "advisory"):
    sys.path.insert(0, str(_HERE.parent / _sub))
sys.path.insert(0, str(_HERE))

import blocking  # noqa: E402
import contract as _contract  # noqa: E402
import hermes as _hermes  # noqa: E402
import hermes_patches as _patches  # noqa: E402
import mutation_policy as _policy  # noqa: E402
import reconcile as _reconcile  # noqa: E402
import repo_profile as _repo_profile  # noqa: E402

WORKFLOW = "BOUNDED_REMEDIATION"
ALLOWED_TOOLS = ("read_file", "search_files", "write_file", "patch", "terminal")


def build_contract(*, task: str, repo_root: str, git_commit: str, dirty: bool,
                   allowed_commands, write_roots=None, max_timeout_s: int = 300,
                   run_id: str | None = None, created_at: str | None = None) -> dict:
    """An ELEVATED/D2 contract. The class is named explicitly (M4-D4)."""
    repo_root = os.path.realpath(repo_root)
    read_scope_block = {
        "allowed_roots": [repo_root],
        "denied_subpaths": list(_contract.DEFAULT_DENIED_SUBPATHS),
    }
    profile_block = _repo_profile.profile(repo_root, read_scope_block)
    roots = tuple(write_roots or (repo_root,))
    return _contract.build(
        task=task, repo_root=repo_root, git_commit=git_commit, dirty=dirty,
        repo_profile=profile_block, workflow=WORKFLOW, allowed_tools=ALLOWED_TOOLS,
        write_roots=roots, allowed_commands=tuple(allowed_commands),
        command_policy={"max_timeout_s": max_timeout_s,
                        "workdir_roots": [os.path.realpath(r) for r in roots]},
        run_id=run_id, created_at=created_at,
        accept=(_contract.M4_CLASS,),
    )


def install(contract_block: dict, *, env=None, config=None, hermes_home=None,
            require_preflight: bool = True):
    """Install both boundaries, with M4's argument policy on the capability one."""
    repo_root = contract_block["target"]["repo_root"]
    envelope = contract_block["capability_envelope"]

    # Everything provable without importing Hermes is proven FIRST (M1's
    # ordering rule: a control evaluated after the import it guards is useless).
    if require_preflight:
        _hermes.check_pre_import(repo_root=repo_root, env=env, hermes_home=hermes_home)

    _patches.install_confinement(contract_block["read_scope"])
    _patches.install_capability(envelope["allowed_tools"], policy=_policy.MutationPolicy(envelope))
    return _policy.MutationPolicy(envelope)


def snapshot_target(contract_block: dict) -> dict:
    root = contract_block["target"]["repo_root"]
    return {"files": _reconcile.snapshot(root), "git": _reconcile.git_status(root)}


def reconcile_target(contract_block: dict, before: dict) -> dict:
    root = contract_block["target"]["repo_root"]
    after = {"files": _reconcile.snapshot(root), "git": _reconcile.git_status(root)}
    return _reconcile.reconcile(
        root=root, before=before["files"], after=after["files"],
        before_git=before["git"], after_git=after["git"],
        write_scope=contract_block["capability_envelope"]["write_scope"],
    )


def execute(*, task: str, repo_root: str, allowed_commands, turn_driver,
            write_roots=None, env=None, config=None, hermes_home=None,
            git_commit: str = "unknown", dirty: bool = False,
            require_preflight: bool = True, run_id: str | None = None) -> dict:
    """Run one bounded remediation. Raises Blocked; never returns a partial pass."""
    contract_block = build_contract(
        task=task, repo_root=repo_root, git_commit=git_commit, dirty=dirty,
        allowed_commands=allowed_commands, write_roots=write_roots, run_id=run_id)
    install(contract_block, env=env, config=config, hermes_home=hermes_home,
            require_preflight=require_preflight)

    before = snapshot_target(contract_block)
    turn_error: BaseException | None = None
    try:
        turn_driver(contract_block)
    except BaseException as exc:  # noqa: BLE001 - deliberate; see below
        # M4-D14/D15 (audit finding F-A5). This caught only `Blocked`, which made
        # the audit depend on the failure having ALREADY been normalised: a driver
        # raising anything else -- a RuntimeError from a bug, a TypeError from a
        # changed Hermes signature, an interrupt -- propagated straight out and
        # skipped reconciliation entirely. That is exactly the case reconciliation
        # exists for, because a mutating turn that died half-way is the one most
        # likely to have left something behind.
        #
        # So every BaseException is caught, and none is lost: it is held here and
        # re-raised below once reconciliation has had its say.
        turn_error = exc

    # ALWAYS reconcile. Not in a `finally`, because the report must be able to
    # raise a Blocked that takes precedence over `turn_error`, and a `finally`
    # that raises would discard the original without a cause chain.
    try:
        report = reconcile_target(contract_block, before)
    except BaseException as rec_exc:
        # Reconciliation itself failed. It must not hide the turn's failure.
        if turn_error is not None:
            raise rec_exc from turn_error
        raise

    try:
        _reconcile.require_within_envelope(report)
    except blocking.Blocked as mismatch:
        # A mismatch OUTRANKS the turn's own error (M4-D15): either way the run
        # yields no deliverable, and "something landed outside the envelope" is
        # the more serious fact about the system. The original failure is kept as
        # the cause rather than discarded, so diagnosis loses nothing.
        if turn_error is not None:
            raise mismatch from turn_error
        raise

    if turn_error is not None:
        raise turn_error
    return {"contract": contract_block, "reconciliation": report}
