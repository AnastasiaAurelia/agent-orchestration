"""Shared, minimal framework for Diana Security static adapters
(Security Phase 2).

An adapter normalizes a **verified scan-evidence artifact** -- not a bare
native tool report -- into evidence_model.py "run" records. It never
invokes, installs, or bundles the tool itself -- "Diana must orchestrate
tools, not reimplement them." Nothing in this module (or the adapters
built on it) makes a network call or spawns a subprocess.

## Core invariant: a clean tool report is not proof of a clean target

Human review of the first Phase 2 implementation found that a
syntactically valid "clean" native report (an empty Gitleaks findings
array, an osv-scanner object that merely lacked a `results` key) was
treated as sufficient to emit `SATISFIED` evidence, without proving the
tool actually scanned the intended repository, commit, and scope. A clean
result from an empty directory, a stale commit, or a narrow subtree looks
byte-for-byte identical to a clean result from a complete, current scan --
"no finding" from the wrong or incomplete target proves nothing.

**Positive and negative evidence are asymmetric.** A recognized finding
(`VIOLATED`) is trustworthy even under partial/incomplete scope -- finding
one real problem does not require having looked everywhere. A clean
result (`SATISFIED`) is only trustworthy when the adapter can additionally
prove the scan actually covered what the requirement needs it to have
covered. This module's two trust layers exist specifically to make that
distinction structural, not adapter-by-adapter folklore:

1. **The scan-evidence artifact** (`load_envelope()`) -- a caller-
   constructed envelope wrapping the raw native tool report with
   independently-asserted, trusted metadata: which tool, whether it
   completed, what target it declares, and a hash binding the wrapped
   report to that declaration (so the native report can't be swapped for
   a different one after the fact without detection). Missing required
   structure, `execution.completed` not `true`, or a `report_binding`
   hash mismatch are integrity failures -- `ERROR`, because at that point
   nothing in the artifact (findings included) can be trusted.
2. **Target verification** (`verify_target()`) -- compares the artifact's
   *declared* target against what the caller *expected* to be scanned.
   A mismatch (wrong commit, missing target context entirely, narrower
   scope than the requirement needs) is not an integrity failure -- it's
   simply insufficient to prove a clean *result* means a clean *target*.
   It never invalidates findings already present in the report, only
   whether an absence of findings can be trusted as `SATISFIED`.

## Scan-evidence artifact envelope (informal)

```jsonc
{
  "tool": {"name": "gitleaks", "version": "8.18.1"},
  "execution": {"completed": true},
  "target": {
    "repository": "AnastasiaAurelia/agent-orchestration",
    "commit": "c51ce15a301df2d7804b36d59e9d508b8653249b",
    "root": ".",
    "scope": "full-repo"
  },
  "config": {},
  "scanned_inputs": [],
  "report": { /* raw native tool output, tool-specific shape */ },
  "report_binding": {"sha256": "<sha256 of json.dumps(report, sort_keys=True, separators=(',', ':'))>"}
}
```

`target`, `config`, and `scanned_inputs` are caller-asserted, trusted
metadata -- they describe what the caller *did*, not something derived
from the (semi-trusted) native `report`. `report_binding.sha256` ties the
wrapped `report` to that trusted description so it cannot be substituted
independently of it.

## Integrity invariant (unchanged from the first Phase 2 implementation)

A static adapter MUST NOT be able to manufacture PASS merely by emitting
an arbitrary catalog required_evidence string as SATISFIED. Every adapter
declares an explicit `AUTHORIZED_EVIDENCE` mapping: `{control_id:
[exact required_evidence strings from catalog.json this adapter is
actually capable of establishing]}`. `build_runs()` below is the ONLY
function that constructs run evidence items, and it refuses (raises
`NotAuthorized`) to build a contribution for any `(control_id,
requirement)` pair not present in the caller-supplied `authorized`
mapping.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


class NotAuthorized(ValueError):
    """Raised when code attempts to build evidence for a (control_id,
    requirement) pair the adapter's AUTHORIZED_EVIDENCE mapping does not
    list."""


class ArtifactError(ValueError):
    """The scan-evidence artifact itself is structurally invalid or fails
    its own integrity check (execution did not complete, or the wrapped
    native report does not match its declared binding hash). Distinct
    from a target mismatch: an ArtifactError means nothing in the
    artifact -- findings included -- can be trusted."""


REQUIRED_ENVELOPE_FIELDS = {"tool", "execution", "target", "config", "scanned_inputs", "report", "report_binding"}


def canonical_report_hash(report: Any) -> str:
    """Deterministic hash binding a native report to its envelope. Uses a
    canonical (sorted-key, compact) JSON serialization so semantically
    identical reports hash identically regardless of key order."""
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_envelope(raw: Any) -> dict[str, Any]:
    """Structural + integrity validation of a scan-evidence artifact.
    Raises ArtifactError for anything that makes the artifact untrustworthy
    as a whole (missing structure, incomplete execution, tampered/mismatched
    report binding). Does NOT check the declared target against any
    expectation -- see verify_target() for that, which never raises."""
    if not isinstance(raw, dict):
        raise ArtifactError("artifact must be a JSON object")

    missing = REQUIRED_ENVELOPE_FIELDS - set(raw.keys())
    if missing:
        raise ArtifactError(f"artifact missing required field(s): {sorted(missing)}")

    tool = raw["tool"]
    if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not isinstance(tool.get("version"), str):
        raise ArtifactError("artifact.tool must be an object with string name and version")

    execution = raw["execution"]
    if not isinstance(execution, dict) or execution.get("completed") is not True:
        raise ArtifactError("artifact.execution.completed must be true -- tool did not report successful completion")

    target = raw["target"]
    if not isinstance(target, dict):
        raise ArtifactError("artifact.target must be an object")

    config = raw["config"]
    if not isinstance(config, dict):
        raise ArtifactError("artifact.config must be an object")

    scanned_inputs = raw["scanned_inputs"]
    if not isinstance(scanned_inputs, list) or not all(isinstance(x, str) for x in scanned_inputs):
        raise ArtifactError("artifact.scanned_inputs must be a list of strings")

    report_binding = raw["report_binding"]
    if not isinstance(report_binding, dict) or not isinstance(report_binding.get("sha256"), str):
        raise ArtifactError("artifact.report_binding must be an object with a string sha256")

    actual_hash = canonical_report_hash(raw["report"])
    if actual_hash != report_binding["sha256"]:
        raise ArtifactError(
            "artifact.report_binding.sha256 does not match the wrapped report -- "
            "the native report may have been substituted or corrupted after binding"
        )

    return raw


def verify_target(target: dict[str, Any], expected_target: dict[str, Any] | None) -> tuple[bool, str | None]:
    """Compares the artifact's declared target against what the caller
    expected. Only keys present in `expected_target` are compared. Never
    raises -- returns (True, None) when verified, else (False, reason).
    A missing/empty target block, or no expected_target supplied at all,
    both fail verification (there is nothing to compare)."""
    if not target:
        return False, "artifact declares no target/scan-scope context"
    if not expected_target:
        return False, "caller supplied no expected target to verify the scan against"
    for key, value in expected_target.items():
        actual = target.get(key)
        if actual != value:
            return False, f"target.{key} mismatch: expected {value!r}, artifact declares {actual!r}"
    return True, None


def build_runs(
    contributions: list[tuple[str, str, str, str, str | None]],
    authorized: dict[str, list[str]],
    capability: str,
    identity: str,
) -> list[dict[str, Any]]:
    """Build evidence_model.py-compatible run records, one per control_id,
    from a list of (control_id, requirement, status, provenance, detail)
    contributions. Every contribution's (control_id, requirement) pair
    MUST be present in `authorized`, or this raises NotAuthorized."""
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
    """Build one ERROR-triggering run per requested control, for a genuine
    parse/integrity failure (malformed artifact JSON, or an ArtifactError
    from load_envelope). Applicability is UNKNOWN because a failed
    load/parse tells us nothing about whether the control is applicable."""
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
