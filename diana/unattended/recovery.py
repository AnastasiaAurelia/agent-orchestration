#!/usr/bin/env python3
"""Diana M5: resume -- authority re-binding and target freshness (M5-D10..D12).

## The order is the design

M5-D10: a resumed process has NO enforcement until it reinstalls it, because the
capability and confinement patches are a module global that died with the
previous process. So enforcement is re-established before anything is touched --
M1's ordering rule, applied to a resume.

M5-D11: re-binding needs TWO independent proofs and both are mandatory.

  1. DOCUMENT INTEGRITY -- the contract and the run policy re-verified by digest
     and run_id. Answers "is this the approved envelope?"
  2. TARGET FRESHNESS -- the repository still being the one the envelope was
     approved against. Answers "is this still the world it was approved for?"

Phase 0 F9 is why the first is not sufficient: `load_and_verify` proves only
that the document is the one Diana wrote, `target.git_commit` is compared to the
live repository by NO code path, and there is no TTL -- a six-hour-old contract
verifies exactly as a six-second-old one. Phase 0 F13 is why the second exists:
a third party committed to the target while the run was down and the
reconciliation blamed the run, because a snapshot diff knows only THAT a file
changed, never WHO changed it.

## Why a drifted target is never re-approved here

M5-D12: when freshness fails the run terminates BLOCKED and reports what
drifted. Nothing in this module builds a contract, re-derives a scope, or adopts
the new state. "Approve once" means the approval is REUSED, never regenerated --
a system that manufactures its own successor approval has no approval at all.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("runtime", "adapters", "mutation", "profile", "advisory"):
    sys.path.insert(0, str(_HERE.parent / _sub))
sys.path.insert(0, str(_HERE))

import blocking  # noqa: E402
import contract as _contract  # noqa: E402
import hermes_patches as _patches  # noqa: E402
import journal as _journal  # noqa: E402
import mutation_policy as _policy  # noqa: E402
import ownership as _ownership  # noqa: E402
import runpolicy as _runpolicy  # noqa: E402
import workitems as _workitems  # noqa: E402

CONTRACT_NAME = "contract.json"
POLICY_NAME = "run-policy.json"
WORK_ITEMS_NAME = "work-items.json"


# --- target identity ------------------------------------------------------

def observe_target(repo_root: str) -> dict:
    """Diana's own view of the target's git identity. Never Hermes's.

    A repository that cannot be observed is `TARGET_UNREADABLE` rather than
    "assume unchanged": M5-D9's direction, applied to the freshness input.
    """
    def git(*args):
        return subprocess.run(["git", "-C", repo_root, *args],
                              capture_output=True, text=True, timeout=60, check=False)
    try:
        head = git("rev-parse", "HEAD")
        status = git("status", "--porcelain")
    except (OSError, subprocess.SubprocessError) as exc:
        raise blocking.Blocked(
            blocking.TARGET_UNREADABLE, f"{type(exc).__name__}: {exc}") from None
    if head.returncode != 0:
        raise blocking.Blocked(
            blocking.TARGET_UNREADABLE, f"git rev-parse failed: {head.stderr.strip()[:200]}")
    if status.returncode != 0:
        raise blocking.Blocked(
            blocking.TARGET_UNREADABLE, f"git status failed: {status.stderr.strip()[:200]}")
    return {"git_commit": head.stdout.strip(), "dirty": bool(status.stdout.strip())}


def require_fresh(record: dict, *, allow_dirty_drift: bool) -> dict:
    """Prove the target is still the world the envelope was approved for.

    `allow_dirty_drift` exists for one legitimate reason: the run's OWN mutations
    make the tree dirty, so after a first attempt the target IS expected to be
    dirty relative to approval. The COMMIT is never allowed to drift, because a
    moved HEAD means work the run did not do and cannot be held responsible for
    (F13). Dirtiness is bounded by reconciliation; a changed commit is not.
    """
    binding = record["target_binding"]
    observed = observe_target(binding["repo_root"])
    if observed["git_commit"] != binding["git_commit"]:
        raise blocking.Blocked(
            blocking.TARGET_MOVED,
            f"target HEAD is {observed['git_commit'][:12]}; the envelope was approved "
            f"against {binding['git_commit'][:12]}")
    if not allow_dirty_drift and observed["dirty"] != binding["dirty"]:
        raise blocking.Blocked(
            blocking.TARGET_MOVED,
            f"target dirty state changed: approved dirty={binding['dirty']}, "
            f"observed dirty={observed['dirty']}")
    return observed


# --- authority re-binding -------------------------------------------------

def load_authority(run_directory, record: dict) -> tuple[dict, dict, dict]:
    """Re-verify BOTH documents against the digests the journal binds.

    The journal names the digests; the files must match them. That is what makes
    contract substitution detectable: swapping in a different but individually
    valid contract changes its digest, and the journal still names the old one.
    """
    directory = _journal.open_dir(run_directory)
    for name in (CONTRACT_NAME, POLICY_NAME, WORK_ITEMS_NAME):
        path = directory / name
        if path.is_symlink():
            raise blocking.Blocked(
                blocking.JOURNAL_PATH_UNSAFE, f"{name} is a symlink: {path}")

    contract_block = _contract.load_and_verify(
        directory / CONTRACT_NAME, record["run_id"], record["contract_digest"],
        accept=(_contract.M4_CLASS,))

    try:
        policy = json.loads((directory / POLICY_NAME).read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise blocking.Blocked(
            blocking.RUN_POLICY_MALFORMED, f"unreadable run policy: {exc}") from None
    _runpolicy.validate(policy)
    actual = _runpolicy.digest(policy)
    if actual != record["run_policy_digest"]:
        raise blocking.Blocked(
            blocking.RUN_POLICY_DIGEST_MISMATCH, f"{actual} != {record['run_policy_digest']}")
    if policy["run_id"] != record["run_id"]:
        raise blocking.Blocked(
            blocking.RUN_ID_MISMATCH,
            f"run policy names run {policy['run_id']!r}, journal names {record['run_id']!r}")

    # ERRATA-001 M5-E1-D2: the item set is re-verified exactly as the other two
    # approved documents are. A tampered graph would otherwise be the one way to
    # change what may run without touching the envelope at all.
    try:
        items_doc = json.loads((directory / WORK_ITEMS_NAME).read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise blocking.Blocked(
            blocking.WORK_ITEMS_MALFORMED, f"unreadable work items: {exc}") from None
    _workitems.validate(items_doc)
    actual_items = _workitems.digest(items_doc)
    if actual_items != record["work_items_digest"]:
        raise blocking.Blocked(
            blocking.WORK_ITEMS_DIGEST_MISMATCH,
            f"{actual_items} != {record['work_items_digest']}")
    if items_doc["run_id"] != record["run_id"]:
        raise blocking.Blocked(
            blocking.RUN_ID_MISMATCH,
            f"work items name run {items_doc['run_id']!r}, journal names {record['run_id']!r}")
    if set(items_doc and _workitems.ids(items_doc)) != set(record["items"]):
        raise blocking.Blocked(
            blocking.WORK_ITEMS_MALFORMED,
            "the journal's item status set does not match the approved item set")
    return contract_block, policy, items_doc


def reestablish_enforcement(contract_block: dict) -> object:
    """Reinstall M1's confinement and M4's argument policy, then PROVE them live.

    M5-D10. The proof is behavioral rather than a flag read: `capability_live()`
    checks both dispatch entries are the guarded ones, and `confinement_live()`
    checks the resolver is Diana's. A resume that cannot prove them is BLOCKED
    rather than run with a boundary nobody verified.
    """
    envelope = contract_block["capability_envelope"]
    policy = _policy.MutationPolicy(envelope)
    _patches.install_confinement(contract_block["read_scope"])
    _patches.install_capability(envelope["allowed_tools"], policy=policy)
    if not _patches.confinement_live():
        raise blocking.Blocked(
            blocking.ENFORCEMENT_NOT_REESTABLISHED, "confinement patch is not live after resume")
    if not _patches.capability_live():
        raise blocking.Blocked(
            blocking.ENFORCEMENT_NOT_REESTABLISHED, "capability patch is not live after resume")
    return policy


# --- the resume entry point ----------------------------------------------

def load_run(run_directory, *, expected_run_id: str | None = None) -> dict:
    """Load and fully re-bind a run, refusing anything unprovable.

    Returns the verified pieces. Performs NO mutation and starts NO turn: a
    caller decides what to do, and every path it may take is a journaled
    transition.
    """
    record = _journal.read(run_directory)

    # A stale or foreign journal must not be adopted just because it parses.
    if expected_run_id is not None and record["run_id"] != expected_run_id:
        raise blocking.Blocked(
            blocking.RUN_ID_MISMATCH,
            f"journal names run {record['run_id']!r}, expected {expected_run_id!r}")
    # The directory name is itself a binding: M1 persists per-run paths so a
    # stale contract from an earlier run is unusable.
    directory_name = Path(run_directory).name
    if directory_name != record["run_id"]:
        raise blocking.Blocked(
            blocking.RUN_ID_MISMATCH,
            f"journal names run {record['run_id']!r} but lives in {directory_name!r}")

    if record["state"] in _journal.TERMINAL_STATES:
        raise blocking.Blocked(
            blocking.RUN_ALREADY_TERMINAL,
            f"run is {record['state']} ({record['terminal']['reason_code']}); "
            "a terminal run is read, never resumed")

    contract_block, policy, items_doc = load_authority(run_directory, record)
    return {"record": record, "contract": contract_block, "policy": policy,
            "items": items_doc}
