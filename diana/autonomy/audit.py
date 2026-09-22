#!/usr/bin/env python3
"""Diana autonomy: the supervisor decision audit trail.

Every autonomous child exists because a recommendation was made and Diana
validated it. The audit record is what makes that reconstructable afterwards:
what was asked, what was answered, what Diana proved, and what it started.

It is EVIDENCE, never an input. Nothing reads an audit record back to decide
anything -- the same rule `turn-record-<n>.json` follows (M2-D13) and for the
same reason: a file that is read to make a decision is on the authority path,
and this one is deliberately not.

Secrets never reach it. The evidence it stores is the digest of the evidence
record rather than the record itself, so an audit is small, comparable, and
incapable of leaking a verification transcript.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "runtime"))
sys.path.insert(0, str(_HERE.parent / "unattended"))
import contract as _contract  # noqa: E402
import journal as _journal  # noqa: E402

AUDIT_DIRNAME = "supervisor"

RECORD_KEYS = (
    "decision_id", "at", "root_run_id", "parent_run_id",
    "evidence_digest", "provider", "model",
    "decision", "reason", "next_goal", "children",
    "requested_write_scope", "requested_commands",
    "preserve_changes", "revert_changes", "confidence", "human_required",
    "accepted", "rejection_code", "rejection_detail",
    "proof", "child_run_ids", "budget_before", "budget_after",
)


def build(*, decision_id, root_run_id, parent_run_id, evidence, recommendation,
          provider, model, accepted, rejection_code=None, rejection_detail="",
          proof=None, child_run_ids=(), budget_before=None, budget_after=None,
          at=None) -> dict:
    """One decision, complete enough to reconstruct why a child was allowed."""
    return {
        "decision_id": str(decision_id),
        "at": at or _journal._utc_now(),
        "root_run_id": str(root_run_id),
        "parent_run_id": str(parent_run_id),
        # The digest, not the evidence: comparable, small, and unable to carry a
        # transcript into an artifact.
        "evidence_digest": _contract.digest(evidence),
        "provider": str(provider),
        "model": str(model) if model else None,
        "decision": recommendation["decision"],
        "reason": recommendation["reason"],
        "next_goal": recommendation["next_goal"],
        "children": list(recommendation["children"]),
        "requested_write_scope": list(recommendation["requested_write_scope"]),
        "requested_commands": list(recommendation["requested_commands"]),
        "preserve_changes": list(recommendation["preserve_changes"]),
        "revert_changes": list(recommendation["revert_changes"]),
        "confidence": recommendation["confidence"],
        "human_required": recommendation["human_required"],
        "accepted": bool(accepted),
        "rejection_code": rejection_code,
        "rejection_detail": str(rejection_detail or "")[:500],
        "proof": proof or {},
        "child_run_ids": [str(r) for r in child_run_ids],
        "budget_before": dict(budget_before or {}),
        "budget_after": dict(budget_after or {}),
    }


def rejected(*, decision_id, root_run_id, parent_run_id, evidence, provider, model,
             code, detail, budget_before=None) -> dict:
    """An audit record for a recommendation that never became a recommendation.

    A provider that timed out or answered malformed JSON produced no validated
    recommendation at all, and the audit must still say so -- otherwise the only
    trace of a failed consultation is its absence.
    """
    return {
        "decision_id": str(decision_id),
        "at": _journal._utc_now(),
        "root_run_id": str(root_run_id),
        "parent_run_id": str(parent_run_id),
        "evidence_digest": _contract.digest(evidence) if isinstance(evidence, dict) else None,
        "provider": str(provider), "model": str(model) if model else None,
        "decision": None, "reason": "", "next_goal": None, "children": [],
        "requested_write_scope": [], "requested_commands": [],
        "preserve_changes": [], "revert_changes": [],
        "confidence": None, "human_required": True,
        "accepted": False, "rejection_code": code,
        "rejection_detail": str(detail or "")[:500],
        "proof": {}, "child_run_ids": [],
        "budget_before": dict(budget_before or {}), "budget_after": {},
    }


def write(directory, record: dict) -> Path:
    audit_dir = Path(directory) / AUDIT_DIRNAME
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / f"decision-{record['decision_id']}.json"
    _journal.atomic_write(
        path, json.dumps(record, indent=2, sort_keys=True).encode("utf-8"))
    return path


def read_all(directory) -> list[dict]:
    audit_dir = Path(directory) / AUDIT_DIRNAME
    if not audit_dir.is_dir():
        return []
    out = []
    for path in sorted(audit_dir.glob("decision-*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(out, key=lambda r: (r.get("at") or "", r.get("decision_id") or ""))
