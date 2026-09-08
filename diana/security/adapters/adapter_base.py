"""Shared, minimal framework for Diana Security static adapters
(Security Phase 2).

An adapter normalizes a **verified scan-evidence artifact** -- not a bare
native tool report -- into evidence_model.py "run" records. It never
invokes, installs, or bundles the tool itself -- "Diana must orchestrate
tools, not reimplement them." Nothing in this module (or the adapters
built on it) makes a network call or spawns a subprocess.

## Core invariant: a clean tool report is not proof of a clean target

A syntactically valid "clean" native report (an empty Gitleaks findings
array, an osv-scanner object that merely lacked a `results` key) proves
nothing if the tool scanned the wrong repository, the wrong commit, or
only part of the target. **Positive and negative evidence are asymmetric,
and so are their identity requirements:**

- **A recognized finding (`VIOLATED`) may come from PARTIAL COVERAGE** --
  finding one real problem does not require having looked everywhere --
  **but it must still be attributed to the correct TARGET IDENTITY**
  (the same `repository` and `commit` the caller expected). A finding
  from the wrong repository, the wrong commit, or an artifact/expectation
  missing that identity entirely must never be attributed as violating
  the *expected* target -- it is simply not evidence about that target at
  all, and is dropped rather than counted.
- **A clean result (`SATISFIED`) requires both target identity AND
  scan-coverage verification** -- identity match alone is not enough; the
  scan must also have covered what the specific requirement needs (full
  repository scope for Gitleaks, full manifest coverage for osv-scanner,
  the mapped rule having actually run for Semgrep).

This module therefore exposes two separate checks, not one:

- **`verify_identity(target, expected_target)`** -- TARGET IDENTITY only:
  compares `repository` and `commit`. Both the artifact's declared target
  and the caller's expectation must supply *non-empty* values for both
  fields, or identity cannot be established. This is the gate for
  attributing a finding (`VIOLATED`) to the expected target.
- **`verify_target(target, expected_target)`** -- full verification
  (identity plus every other key the caller supplied, e.g. `scope`).
  This is the gate for trusting a clean result (`SATISFIED`).

## Artifact integrity vs. authenticity

**`load_envelope()`'s `artifact_binding.sha256` check detects corruption
or substitution of the artifact's own declared fields (`tool`,
`execution`, `target`, `config`, `scanned_inputs`, `report`) after the
binding was computed -- it does NOT authenticate who produced the
artifact.** There is no PKI, signature, or attestation service anywhere
in this module, and none should be added here; that is a different,
much larger problem this phase deliberately does not attempt to solve.
The hash only proves internal self-consistency: if any bound field
changes without the binding being recomputed, `load_envelope()` raises.

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
  "artifact_binding": {"sha256": "<sha256 of the canonical JSON of {tool, execution, target, config, scanned_inputs, report}>"}
}
```

Each adapter additionally validates `tool.name` against the exact
identity it expects (e.g. Gitleaks' adapter only accepts
`tool.name == "gitleaks"`) -- a syntactically compatible report from the
wrong tool is rejected, not guessed at.

## Integrity invariant (evidence authorization, unchanged since the first
Phase 2 implementation)

A static adapter MUST NOT be able to manufacture PASS merely by emitting
an arbitrary catalog required_evidence string as SATISFIED. Every adapter
declares an explicit `AUTHORIZED_EVIDENCE` mapping: `{control_id:
[exact required_evidence strings from catalog.json this adapter is
actually capable of establishing]}`. `build_runs()` below is the ONLY
function that constructs run evidence items, and it raises `NotAuthorized`
for any `(control_id, requirement)` pair not present in the caller-
supplied `authorized` mapping -- every adapter's `ingest()` catches this
as a defense-in-depth backstop (it should never actually fire, since each
adapter is responsible for only ever handing `build_runs()` pairs it has
already validated) and converts it to an `ERROR` run rather than letting
it escape uncaught.

## Tool-missing is an explicit result, not silence

When no artifact is available to ingest, adapters no longer simply return
no runs at all. `tool_unavailable_runs()` builds one explicit run per
requested (authorized) control with `applicability: "UNKNOWN"` and empty
evidence -- this deterministically aggregates to `UNPROVEN` in
evidence_model.py (never `NOT_APPLICABLE`, never `PASS`, and distinct from
a control simply being absent from the results). No change to
evidence_model.py was needed for this: `UNKNOWN` applicability already
routes to `UNPROVEN` ("applicability has not been established") in the
existing Phase 1 aggregation. The one limitation worth naming plainly:
evidence_model.py's aggregate result for the `UNKNOWN`-applicability path
does not surface a run's `verifier.identity` string in its output, so the
specific human-readable "why" (e.g. "no artifact provided at
<path>") is only visible by inspecting the raw run this module builds,
not in evidence_model.py's final JSON. That is an existing, unchanged
Phase 1 output-shape limitation, not something this phase works around or
needed to fix.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


class NotAuthorized(ValueError):
    """Raised when code attempts to build evidence for a (control_id,
    requirement) pair the adapter's AUTHORIZED_EVIDENCE mapping does not
    list. Every adapter's ingest() catches this as a backstop."""


class ArtifactError(ValueError):
    """The scan-evidence artifact itself is structurally invalid or fails
    its own integrity/identity checks (execution did not complete, the
    artifact binding hash doesn't match, or the declared tool identity
    doesn't match what this adapter accepts). Distinct from a target
    mismatch: an ArtifactError means nothing in the artifact -- findings
    included -- can be trusted."""


REQUIRED_ENVELOPE_FIELDS = {"tool", "execution", "target", "config", "scanned_inputs", "report", "artifact_binding"}
BOUND_FIELDS = ("tool", "execution", "target", "config", "scanned_inputs", "report")


def canonical_artifact_hash(envelope: dict[str, Any]) -> str:
    """Deterministic hash over every bound field of the envelope (tool,
    execution, target, config, scanned_inputs, report) -- NOT just the
    report. Uses a canonical (sorted-key, compact) JSON serialization so
    semantically identical content hashes identically regardless of key
    order. Detects corruption/substitution of any bound field; it does
    not authenticate who produced the artifact (see module docstring)."""
    bound = {key: envelope[key] for key in BOUND_FIELDS}
    canonical = json.dumps(bound, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_envelope(raw: Any) -> dict[str, Any]:
    """Structural + integrity validation of a scan-evidence artifact.
    Raises ArtifactError for anything that makes the artifact untrustworthy
    as a whole (missing structure, incomplete execution, tampered/mismatched
    artifact binding). Does NOT check tool identity or the declared target
    against any expectation -- see verify_tool_identity()/verify_identity()/
    verify_target() for those, none of which raise."""
    if not isinstance(raw, dict):
        raise ArtifactError("artifact must be a JSON object")

    missing = REQUIRED_ENVELOPE_FIELDS - set(raw.keys())
    if missing:
        raise ArtifactError(f"artifact missing required field(s): {sorted(missing)}")

    tool = raw["tool"]
    if (
        not isinstance(tool, dict)
        or not isinstance(tool.get("name"), str)
        or not tool.get("name", "").strip()
        or not isinstance(tool.get("version"), str)
        or not tool.get("version", "").strip()
    ):
        raise ArtifactError("artifact.tool must be an object with non-empty string name and version")

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

    artifact_binding = raw["artifact_binding"]
    if not isinstance(artifact_binding, dict) or not isinstance(artifact_binding.get("sha256"), str):
        raise ArtifactError("artifact.artifact_binding must be an object with a string sha256")

    actual_hash = canonical_artifact_hash(raw)
    if actual_hash != artifact_binding["sha256"]:
        raise ArtifactError(
            "artifact.artifact_binding.sha256 does not match the bound fields (tool/execution/target/"
            "config/scanned_inputs/report) -- the artifact may have been modified or substituted after binding"
        )

    return raw


def verify_tool_identity(tool: dict[str, Any], expected_name: str) -> None:
    """Raises ArtifactError if the artifact's declared tool name doesn't
    exactly match what this adapter accepts. A syntactically compatible
    report from the wrong tool is rejected, not guessed at. Called after
    load_envelope() has already confirmed tool.name/version are non-empty
    strings."""
    actual_name = tool["name"]
    if actual_name != expected_name:
        raise ArtifactError(f"artifact.tool.name mismatch: this adapter only accepts {expected_name!r}, got {actual_name!r}")


def verify_identity(target: dict[str, Any], expected_target: dict[str, Any] | None) -> tuple[bool, str | None]:
    """TARGET IDENTITY ONLY: compares repository and commit. Both the
    artifact's target and the caller's expectation must supply non-empty
    values for both fields. This is the gate for attributing a recognized
    finding (VIOLATED) to the expected target -- deliberately does not
    consider scope/coverage, since a finding needs only be from the right
    repository and commit, not from a complete scan of it. Never raises."""
    if not target:
        return False, "artifact declares no target context"
    if not expected_target:
        return False, "caller supplied no expected target"

    expected_repo = expected_target.get("repository")
    expected_commit = expected_target.get("commit")
    if not expected_repo or not expected_commit:
        return False, "expected_target must specify non-empty repository and commit"

    actual_repo = target.get("repository")
    actual_commit = target.get("commit")
    if not actual_repo or not actual_commit:
        return False, "artifact target must specify non-empty repository and commit"

    if actual_repo != expected_repo:
        return False, f"target.repository mismatch: expected {expected_repo!r}, artifact declares {actual_repo!r}"
    if actual_commit != expected_commit:
        return False, f"target.commit mismatch: expected {expected_commit!r}, artifact declares {actual_commit!r}"

    return True, None


def verify_target(target: dict[str, Any], expected_target: dict[str, Any] | None) -> tuple[bool, str | None]:
    """FULL verification: target identity (repository/commit) plus every
    other key the caller supplied (e.g. scope). This is the gate for
    trusting a clean result (SATISFIED) -- identity match alone is not
    sufficient; the declared scan coverage must also match what the
    caller expected. Never raises."""
    identity_ok, reason = verify_identity(target, expected_target)
    if not identity_ok:
        return False, reason

    for key, value in (expected_target or {}).items():
        if key in ("repository", "commit"):
            continue  # already checked by verify_identity
        actual = target.get(key)
        if actual != value:
            return False, f"target.{key} mismatch: expected {value!r}, artifact declares {actual!r}"

    return True, None


def build_runs(
    contributions: list[tuple[str, str, str, str, str | None]],
    authorized: dict[str, list[str]],
    capability: str,
    identity: str,
    requested_control_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build evidence_model.py-compatible run records from a list of
    (control_id, requirement, status, provenance, detail) contributions.
    Every contribution's (control_id, requirement) pair MUST be present
    in `authorized`, or this raises NotAuthorized -- every adapter's
    ingest() catches this as a backstop.

    If `requested_control_ids` is given, an explicit APPLICABLE run with
    empty evidence is also built for any requested control that received
    no contribution -- the artifact was successfully parsed (this
    function is only reached once it has been), but there was nothing to
    say about that control. This ensures a control the caller asked about
    never simply vanishes from evidence_model.py's output: an empty-
    evidence run deterministically aggregates to UNPROVEN ("missing
    required evidence"), not an absent result. Without this, a control
    that received zero contributions would never appear in
    evidence_model.py's results at all -- indistinguishable from a
    control nobody ever asked about."""
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

    for control_id in requested_control_ids or []:
        if control_id not in by_control:
            runs.append(
                {
                    "control_id": control_id,
                    "applicability": "APPLICABLE",
                    "verifier": {"type": capability, "identity": identity},
                    "evidence": [],
                    "tool_error": None,
                }
            )
    return runs


def tool_error_runs(control_ids: list[str], capability: str, identity: str, message: str) -> list[dict[str, Any]]:
    """Build one ERROR-triggering run per requested control, for a genuine
    parse/integrity/authorization failure (malformed artifact JSON, an
    ArtifactError from load_envelope/verify_tool_identity, an unauthorized
    Semgrep rule_map entry, or a stray NotAuthorized from build_runs()).
    Applicability is UNKNOWN because a failed load/parse tells us nothing
    about whether the control is applicable."""
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


def tool_unavailable_runs(control_ids: list[str], capability: str, identity: str, reason: str) -> list[dict[str, Any]]:
    """Build one explicit run per requested control for the 'no artifact
    to ingest' case (tool did not run / was never given output to parse).
    Distinct from tool_error_runs(): no tool_error is set (this is not a
    failure of a tool that ran -- it simply never ran), so this
    deterministically aggregates to UNPROVEN in evidence_model.py, not
    ERROR. The human-readable reason is embedded in the run's
    verifier.identity for anyone inspecting raw runs; evidence_model.py's
    aggregate output for the UNKNOWN-applicability path does not surface
    per-run identity strings (see module docstring)."""
    return [
        {
            "control_id": control_id,
            "applicability": "UNKNOWN",
            "verifier": {"type": capability, "identity": f"{identity} (unavailable: {reason})"},
            "evidence": [],
            "tool_error": None,
        }
        for control_id in control_ids
    ]
