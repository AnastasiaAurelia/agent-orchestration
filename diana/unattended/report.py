#!/usr/bin/env python3
"""Diana M5: the run report and blocked-item report (M5-D17).

M5-D17's requirement is that returning to a blocked item is CHEAP for someone
who was not watching. So the report is reconstructed entirely from Diana-owned
durable state -- the journal, the contract, the run policy, the per-attempt
reconciliation records -- and never from anything Hermes said.

Two structural rules, both inherited rather than invented:

  * It carries no severity, risk or depth proposed by Hermes (M1 D14). The agent
    has no channel into its own report.
  * It must be structurally incapable of being read as certified evidence
    (M1 D35, M3-D15): it carries none of `evidence_model`'s allowed run fields
    and must classify MALFORMED there, and `artifact.validate()` must reject it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "runtime"))
sys.path.insert(0, str(_HERE))
import journal as _journal  # noqa: E402

REPORT_NAME = "run-report.json"
DOCUMENT_TYPE = "UNATTENDED_RUN_REPORT"
SCHEMA_VERSION = 1


def _attempt_summary(attempt: dict, run_directory: Path) -> dict:
    recon_name = attempt.get("reconciliation_file")
    outside, touched, within = [], [], attempt.get("within_envelope")
    if recon_name:
        try:
            data = json.loads((run_directory / recon_name).read_bytes().decode("utf-8"))
            outside = [entry["path"] for entry in data.get("paths_outside_write_scope", [])]
            touched = data.get("paths_touched", [])
            within = data.get("within_envelope", within)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
            # A missing or damaged reconciliation record is REPORTED as such.
            # Silently reporting "nothing changed" would be the worst possible
            # failure mode for an audit record (M5-D9's direction).
            outside, touched, within = ["<reconciliation record unreadable>"], [], None
    return {
        "attempt": attempt.get("attempt"),
        "state": attempt.get("state"),
        "started_at": attempt.get("started_at"),
        "ended_at": attempt.get("ended_at"),
        "reconciled": bool(attempt.get("reconciled")),
        "within_envelope": within,
        "paths_touched": touched,
        "paths_outside_write_scope": outside,
        "turn_error": attempt.get("turn_error"),
    }


def build(record: dict, contract_block: dict, policy: dict, run_directory) -> dict:
    """Reconstruct the whole run from Diana-owned durable state alone."""
    run_directory = Path(run_directory)
    terminal = record.get("terminal") or {}
    attempts = [_attempt_summary(a, run_directory) for a in record.get("attempts", [])]

    blocked_items = []
    if record["state"] == _journal.BLOCKED:
        # What was attempted, which control refused it, with which reason code,
        # what the envelope permitted at that moment, and what a human must decide.
        envelope = contract_block["capability_envelope"]
        blocked_items.append({
            "what_was_attempted": contract_block.get("task"),
            "refused_by": "diana",
            "reason_code": terminal.get("reason_code"),
            "detail": terminal.get("detail"),
            "envelope_at_the_time": {
                "allowed_tools": envelope.get("allowed_tools"),
                "write_scope": (envelope.get("write_scope") or {}).get("allowed_roots"),
                "allowed_commands": envelope.get("allowed_commands"),
                "risk": contract_block.get("risk"),
                "depth": contract_block.get("depth"),
            },
            "paths_outside_write_scope": sorted(
                {p for a in attempts for p in a["paths_outside_write_scope"]}),
            "human_decision_required":
                _human_decision_for(terminal.get("reason_code")),
        })

    return {
        "document_type": DOCUMENT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "run_id": record["run_id"],
        "outcome": record["state"],
        "terminal": terminal or None,
        "contract_digest": record["contract_digest"],
        "run_policy_digest": record["run_policy_digest"],
        "target_binding": record["target_binding"],
        "budget": {
            "max_attempts": policy["max_attempts"],
            "attempts_used": len(record.get("attempts", [])),
            "deadline_at": policy["deadline_at"],
        },
        "attempts": attempts,
        "blocked_items": blocked_items,
        "state_history": record.get("history", []),
    }


def _human_decision_for(reason_code: str | None) -> str:
    """Plain-language next step. Deliberately a closed mapping, not free text."""
    return {
        "reconciliation-mismatch":
            "Inspect the paths changed outside write_scope and decide whether to keep or revert "
            "them, then approve a new run if the work should continue.",
        "target-moved":
            "The repository moved while the run was down. Decide whether the run's remaining work "
            "is still wanted against the new commit, then approve a NEW run; the old envelope is "
            "not reused against a different target.",
        "quiescence-not-proven":
            "A process belonging to this run could not be proven stopped. Identify it and stop it "
            "before approving any further run against this target.",
        "run-already-terminal":
            "This run already finished. Read its report; start a new run if more work is wanted.",
        "journal-digest-mismatch":
            "Diana-owned run state failed its integrity check. Treat the target as unaudited and "
            "inspect it manually before approving further automated work.",
        "journal-malformed":
            "Diana-owned run state is unreadable. Treat the target as unaudited and inspect it "
            "manually before approving further automated work.",
    }.get(reason_code or "", "Review the run report and decide whether to approve a new run.")


def persist(report: dict, run_directory) -> Path:
    path = Path(run_directory) / REPORT_NAME
    _journal.atomic_write(
        path, (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return path
