"""Shared, minimal framework for Diana Security static adapters
(Security Phase 2).

An adapter normalizes ALREADY-PRODUCED tool output (a saved/real JSON
report from Gitleaks, osv-scanner, Semgrep, etc.) into evidence_model.py
"run" records. It never invokes, installs, or bundles the tool itself --
"Diana must orchestrate tools, not reimplement them." Nothing in this
module (or the adapters built on it) makes a network call or spawns a
subprocess.

## Integrity invariant (this is the whole point of this module)

A static adapter MUST NOT be able to manufacture PASS merely by emitting
an arbitrary catalog required_evidence string as SATISFIED. Every adapter
declares an explicit `AUTHORIZED_EVIDENCE` mapping: `{control_id:
[exact required_evidence strings from catalog.json this adapter is
actually capable of establishing]}`. `build_runs()` below is the ONLY
function that constructs run evidence items, and it refuses (raises
`NotAuthorized`) to build a contribution for any `(control_id,
requirement)` pair not present in the caller-supplied `authorized`
mapping. A finding/output type an adapter's own mapping doesn't recognize
must never reach `build_runs()` at all -- the per-adapter modules are
responsible for silently dropping (not fabricating evidence for) any tool
output that doesn't match one of their own recognized finding-type ->
control/requirement rules.

## Result semantics an adapter must produce

- **Clean scan, meaningfully covering an authorized requirement** ->
  `SATISFIED`. "Meaningfully covering" is adapter-specific and must be
  proven, not assumed -- see each adapter module for how it establishes
  this (e.g. Semgrep's adapter requires the specific mapped rule to have
  actually run before treating its absence-of-findings as evidence).
- **A recognized finding for an authorized requirement** -> `VIOLATED`.
- **Unknown/unmapped tool output** -> no contribution at all for that
  requirement. Left unaddressed, it becomes `UNPROVEN` via
  evidence_model.py's existing "missing required evidence" handling --
  never treated as satisfying anything.
- **Tool did not run at all** (no output artifact to ingest) -> no runs
  are emitted for the requested controls. Evidence_model.py's existing
  "no verification runs submitted for this control" default applies ->
  `UNPROVEN`, never `PASS`.
- **Tool output exists but cannot be parsed as this tool's expected
  format** (a genuine execution/parse failure, distinct from "didn't
  run") -> `tool_error_runs()` emits one run per requested control with
  `tool_error` set, which evidence_model.py turns into `ERROR`.
"""

from __future__ import annotations

from typing import Any


class NotAuthorized(ValueError):
    """Raised when code attempts to build evidence for a (control_id,
    requirement) pair the adapter's AUTHORIZED_EVIDENCE mapping does not
    list. This should never happen in practice -- each adapter module is
    responsible for only ever calling build_runs() with pairs from its own
    mapping -- but it fails loud rather than silently fabricating
    evidence if a bug ever tries to."""


def build_runs(
    contributions: list[tuple[str, str, str, str, str | None]],
    authorized: dict[str, list[str]],
    capability: str,
    identity: str,
) -> list[dict[str, Any]]:
    """Build evidence_model.py-compatible run records, one per control_id,
    from a list of (control_id, requirement, status, provenance, detail)
    contributions. Every contribution's (control_id, requirement) pair
    MUST be present in `authorized`, or this raises NotAuthorized -- this
    is the enforcement point for "an adapter may only emit evidence for
    requirements explicitly authorized by its mapping."
    """
    by_control: dict[str, list[dict[str, Any]]] = {}
    for control_id, requirement, status, provenance, detail in contributions:
        allowed = authorized.get(control_id, [])
        if requirement not in allowed:
            raise NotAuthorized(
                f"{capability} adapter is not authorized to establish "
                f"{control_id} requirement {requirement!r}"
            )
        item: dict[str, Any] = {
            "requirement": requirement,
            "status": status,
            "provenance": provenance,
        }
        if detail is not None:
            item["detail"] = detail
        by_control.setdefault(control_id, []).append(item)

    runs = []
    for control_id, items in by_control.items():
        runs.append(
            {
                "control_id": control_id,
                "applicability": "APPLICABLE",
                "verifier": {"type": capability, "identity": identity},
                "evidence": items,
                "tool_error": None,
            }
        )
    return runs


def tool_error_runs(control_ids: list[str], capability: str, identity: str, message: str) -> list[dict[str, Any]]:
    """Build one ERROR-triggering run per requested control, for the
    'tool output exists but could not be parsed' (execution failure)
    case. Applicability is UNKNOWN because a failed parse tells us
    nothing about whether the control is even applicable."""
    return [
        {
            "control_id": control_id,
            "applicability": "UNKNOWN",
            "verifier": {"type": capability, "identity": identity},
            "evidence": [],
            "tool_error": {"message": message},
        }
        for control_id in control_ids
    ]
