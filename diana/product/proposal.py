#!/usr/bin/env python3
"""Diana M7: the Proposal, and the approval binding ERRATA-001 corrected.

A Proposal is a **prediction of the run that approval would create**. It is
built with the *same* builders the run itself uses -- `remediate.build_contract`,
`workitems.build`, `topology.build` -- so there is no second implementation of
contract semantics to drift from the first.

## What approval binds, and why it is not the contract digest

ERRATA-001 measured that `unattended.approve()` does not accept `created_at`,
that `created_at` is inside `CONTRACT_KEYS` and therefore inside the contract
digest, and that M7-REG-2 forbids adding the parameter. A proposal therefore
*cannot* predict the digest that will be persisted -- measured, not argued.

So approval binds a PROPOSAL digest over the authority-bearing objects
(M7-E1-D1): every contract field except `created_at`, plus the work-item digest
and the actor-topology digest. `created_at` is excluded because it is
provenance -- nothing reads a contract's `created_at` to decide anything -- and
its exclusion is asserted by test rather than assumed.

## Why target freshness needs no new mechanism

Also measured: every target movement changes this digest. A new commit moves
`target.git_commit`; a dirty tree moves `target.dirty`; and a **gitignored-only**
change -- which moves neither, the M5-F3 blind spot -- moves
`repo_profile.inventory`. All three fields are inside the proposal digest and
all three are rebuilt by `approve()` from the live repository, so freshness
rides on the objects M5/M6 already treat as authoritative.

## Why approval re-derives instead of trusting the stored proposal

M7-E1-D3. The stored proposal is what was *displayed*; the live repository is
what will be *acted on*. Approval rebuilds the proposal against the repository
as it is now and compares. A mismatch is refused and **no run directory is
created** -- the proposal is never silently rebuilt and executed, which is the
display -> approve -> create window closed.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("runtime", "unattended", "mutation", "multiactor", "adapters", "profile"):
    sys.path.insert(0, str(_HERE.parent / _sub))
sys.path.insert(0, str(_HERE))

import contract as _contract  # noqa: E402
import recovery as _recovery  # noqa: E402
import remediate as _remediate  # noqa: E402
import topology as _topology  # noqa: E402
import workitems as _workitems  # noqa: E402
import actors as _actors  # noqa: E402
import intent as _intent  # noqa: E402
import refusal as _ref  # noqa: E402

PROPOSALS_DIRNAME = "proposals"
DOCUMENT_VERSION = 1

# M7-E1-D1: every contract field except `created_at`.
AUTHORITY_FIELDS = tuple(k for k in _contract.CONTRACT_KEYS if k != "created_at")

DEFAULT_MAX_ATTEMPTS = 6
DEFAULT_TOTAL_SECONDS = 3600


def proposals_dir(base: str | None = None) -> Path:
    root = Path(base) if base else Path(os.path.expanduser("~/.diana"))
    directory = root / PROPOSALS_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    return directory


def authority_view(contract_block: dict) -> dict:
    """The authority-bearing half of a contract. `created_at` is excluded."""
    return {k: contract_block[k] for k in AUTHORITY_FIELDS}


def digest_of(contract_block: dict, items_doc: dict, topology_doc: dict | None) -> str:
    """The proposal digest (M7-E1-D1), over the same canonical serialization
    the contract itself is hashed with, so the two cannot disagree about bytes."""
    return _contract.digest({
        "authority": authority_view(contract_block),
        "work_items": _workitems.digest(items_doc),
        "actors": _topology.digest(topology_doc) if topology_doc else None,
    })


def _derive(intent_doc: dict, repo_root: str, run_id: str,
            max_attempts: int, total_seconds: int) -> dict:
    """Build the predicted objects with the SAME builders the run will use."""
    if intent_doc["workflow"] != "BOUNDED_REMEDIATION":
        raise _ref.Refused(
            _ref.WORKFLOW_NO_PRODUCT_PATH,
            f"understood as {intent_doc['workflow']}, which is a certified class but has "
            "no bounded-run product path in M7; only a bounded repair can be started here")
    observed = _recovery.observe_target(repo_root)
    contract_block = _remediate.build_contract(
        task=intent_doc["goal"], repo_root=repo_root,
        git_commit=observed["git_commit"], dirty=observed["dirty"],
        allowed_commands=tuple(intent_doc["commands"]),
        write_roots=tuple(str(Path(repo_root) / p) for p in intent_doc["write_paths"]),
        run_id=run_id)
    items_doc = _workitems.build(run_id=run_id, items=intent_doc["items"])
    topology_doc = _topology.build(run_id=run_id)
    return {"contract": contract_block, "items": items_doc, "topology": topology_doc,
            "observed": observed}


def build(goal: str, repo_root: str, *, base: str | None = None,
          max_attempts: int = DEFAULT_MAX_ATTEMPTS,
          total_seconds: int = DEFAULT_TOTAL_SECONDS) -> dict:
    """Natural-language goal -> a persisted, digest-identified Proposal."""
    repo_root = os.path.realpath(repo_root)
    intent_doc = _intent.classify(goal, repo_root)
    run_id = str(uuid.uuid4())
    derived = _derive(intent_doc, repo_root, run_id, max_attempts, total_seconds)
    proposal = {
        "document_version": DOCUMENT_VERSION,
        "proposal_digest": digest_of(derived["contract"], derived["items"],
                                     derived["topology"]),
        "run_id": run_id,
        "repo_root": repo_root,
        "intent": {k: v for k, v in intent_doc.items() if k != "_withheld"},
        "withheld_by_exclusion": intent_doc.get("_withheld", []),
        "budget": {"max_attempts": max_attempts, "total_seconds": total_seconds},
        "predicted_contract": derived["contract"],
        "predicted_items": derived["items"],
        "predicted_topology": derived["topology"],
    }
    path = proposals_dir(base) / f"{proposal['proposal_digest'].split(':', 1)[1]}.json"
    path.write_text(json.dumps(proposal, indent=2, sort_keys=True))
    os.chmod(path, 0o600)
    return proposal


def load(proposal_digest: str, base: str | None = None) -> dict:
    """Load a proposal BY DIGEST. Free text cannot address one (M7-E1-D5/D7)."""
    if not isinstance(proposal_digest, str) or not proposal_digest.startswith("sha256:") \
            or len(proposal_digest) != len("sha256:") + 64:
        raise _ref.Refused(
            _ref.APPROVAL_NOT_A_DIGEST,
            f"{proposal_digest!r} is not a proposal digest. Approval takes the digest "
            "shown with the proposal; agreeable words are not an approval")
    path = proposals_dir(base) / f"{proposal_digest.split(':', 1)[1]}.json"
    try:
        proposal = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise _ref.Refused(
            _ref.PROPOSAL_NOT_FOUND, f"no proposal {proposal_digest}: {exc}") from None
    if proposal.get("proposal_digest") != proposal_digest:
        raise _ref.Refused(
            _ref.PROPOSAL_MALFORMED, "the stored proposal does not carry its own digest")
    return proposal


def recompute(proposal: dict) -> str:
    """Re-derive the proposal against the LIVE repository and return its digest."""
    derived = _derive(proposal["intent"], proposal["repo_root"], proposal["run_id"],
                      proposal["budget"]["max_attempts"],
                      proposal["budget"]["total_seconds"])
    return digest_of(derived["contract"], derived["items"], derived["topology"])


def approve(proposal_digest: str, *, base: str | None = None,
            runs_base: str | None = None) -> dict:
    """Approve a proposal by digest and create the run (M7-E1-D3/D4).

    Category A only: start this exact bounded Diana run. It is not a GitHub,
    merge, deployment or any other HUMAN_ONLY approval, and no accumulation of
    it becomes one (M7-D21/D22).
    """
    proposal = load(proposal_digest, base)

    # M7-E1-D3: re-derive against the live repository BEFORE anything is created.
    current = recompute(proposal)
    if current != proposal_digest:
        raise _ref.Refused(
            _ref.PROPOSAL_STALE,
            "the repository changed since this proposal was shown, so the authority it "
            f"describes is no longer the authority that would be granted (now {current}). "
            "No run was created. Propose again and approve the new proposal")

    intent_doc = proposal["intent"]
    repo_root = proposal["repo_root"]
    result = _actors.approve(
        task=intent_doc["goal"], repo_root=repo_root,
        allowed_commands=tuple(intent_doc["commands"]),
        write_roots=tuple(str(Path(repo_root) / p) for p in intent_doc["write_paths"]),
        runs_base=runs_base, run_id=proposal["run_id"],
        max_attempts=proposal["budget"]["max_attempts"],
        total_seconds=proposal["budget"]["total_seconds"],
        items=intent_doc["items"])

    # M7-E1-D4: the prediction must have been true. `created_at` is the ONE
    # field allowed to differ; anything else means the run that was created is
    # not the run that was approved, and it must stop rather than be excused.
    persisted = result["contract"]
    predicted = proposal["predicted_contract"]
    differing = [k for k in AUTHORITY_FIELDS if persisted[k] != predicted[k]]
    if differing:
        raise _ref.Refused(
            _ref.CONTRACT_PREDICTION_FAILED,
            f"the created run differs from the approved proposal on {differing}; "
            f"run directory {result['run_directory']} exists and must not be executed")
    return {"proposal_digest": proposal_digest, "run_id": result["run_id"],
            "run_directory": result["run_directory"],
            "contract_digest": result["contract_digest"], "approved": True}
