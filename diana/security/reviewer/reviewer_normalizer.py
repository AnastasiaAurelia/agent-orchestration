#!/usr/bin/env python3
"""Diana Security semantic-review normalizer (Security Phase 4).

Consumes one already-produced semantic-review evidence artifact (see
`reviewer_base.py` for the envelope shape and trust model) and emits
`diana/security/evidence_model.py`-compatible run records. This module
never runs a model, spawns a reviewer session, or renders any judgment
itself -- it only normalizes a verdict an external, fresh, independent,
read-only reviewer session already reached, exactly as
`diana/security/adapters/*.py` and `diana/security/dynamic/*.py`
normalize already-produced static/dynamic evidence.

## Authorization is catalog-derived, not a hardcoded list

Unlike Phase 2's static adapters (each authorized for a small, fixed set
of controls), a semantic reviewer is broadly applicable -- so this module
computes its authorized controls directly from `catalog.json`: any
control whose `verification.modes` includes `SEMANTIC_REVIEW` or `HUMAN`.
(Every `human_judgment_required=true` control in the current catalog
already satisfies this -- verified empirically, not assumed -- so modes
alone is a sufficient and structurally simpler authorization source than
also treating `human_judgment_required` as an independent signal.) This
deliberately does not match the Phase 4 kickoff's own illustrative
"semantic families" list one-for-one: five of those named controls
(SEC-040, SEC-062, SEC-067, SEC-070, SEC-071) do NOT list
`SEMANTIC_REVIEW`/`HUMAN` in their actual `catalog.json`
`verification.modes` (their real modes are
`INFRA_CONFIG`/`DYNAMIC_API`/`DEPENDENCY_SCANNER`/`DYNAMIC_DB`/
`PROVIDER_SANDBOX` only) -- the kickoff instruction itself says "do not
assume this list is exhaustive, use catalog human_judgment_required and
verification modes," and the catalog is the authoritative source per this
track's permanent SOURCE LOCK. A semantic-review contribution for one of
those five controls is rejected here (and would independently be rejected
by `evidence_model.py`'s own capability check downstream regardless) --
this is not an oversight, it is the catalog being followed rather than
the illustrative list.

## AI/LLM constitutional guard (SEC-074, SEC-075)

A `PASS` for either control must never rest on "the model refused" or
"the system prompt tells it not to." `AI_CONSTITUTION_CONTROLS` requires
the artifact to carry `ai_authorization_context.enforced_outside_model:
true` before a `PASS` is accepted -- a reviewer claiming PASS without
explicitly asserting that authorization is enforced by code outside the
model is rejected, regardless of how well-reasoned the rest of the
artifact is.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_base  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapters"))
import adapter_base  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import validate_catalog  # noqa: E402  (reuse the Phase 0 validator + verifier-type enum)

AI_CONSTITUTION_CONTROLS = {"SEC-074", "SEC-075"}


def _load_catalog_controls(catalog_path: str) -> dict[str, dict[str, Any]]:
    with open(catalog_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    errors = validate_catalog.validate(catalog)
    if errors:
        raise reviewer_base.ArtifactError(f"catalog is not structurally valid: {errors}")
    return {c["id"]: c for c in catalog["controls"]}


def _authorized_control_ids(catalog_controls: dict[str, dict[str, Any]]) -> set[str]:
    """A control is authorized for semantic review iff its real catalog
    verification.modes actually lists SEMANTIC_REVIEW or HUMAN. Every
    human_judgment_required=true control in the current catalog already
    satisfies this (verified empirically, not assumed); computing
    authorization from modes alone -- rather than also treating
    human_judgment_required as an independent signal -- keeps this
    guarantee structural: _capability_for_control() can never be asked
    for a control with no judgment-capable mode to report under."""
    return {
        control["id"]
        for control in catalog_controls.values()
        if set(control["verification"]["modes"]) & reviewer_base.ALLOWED_REVIEWER_VERIFIER_TYPES
    }


def ingest(
    catalog_controls: dict[str, dict[str, Any]],
    artifact_path: str | None,
    control_ids: list[str],
    identity: str,
    expected_target: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Returns evidence_model.py run records for the requested control_ids
    this reviewer is catalog-authorized for. Controls the catalog doesn't
    permit a judgment capability for are silently skipped."""
    authorized_ids = _authorized_control_ids(catalog_controls)
    requested_authorized = [c for c in control_ids if c in authorized_ids]
    if not requested_authorized:
        return []

    if artifact_path is None or not Path(artifact_path).exists():
        runs: list[dict[str, Any]] = []
        for control_id in requested_authorized:
            capability = _capability_for_control(catalog_controls, control_id)
            runs.extend(
                adapter_base.tool_unavailable_runs([control_id], capability, identity, "no review artifact provided")
            )
        return runs

    try:
        with open(artifact_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        envelope = reviewer_base.load_review_envelope(raw)

        control_id = envelope["control_id"]
        if control_id not in requested_authorized:
            return []

        control = catalog_controls.get(control_id)
        if control is None:
            raise reviewer_base.ArtifactError(f"unknown control_id {control_id!r} -- not in the catalog")

        if envelope["requirement"] not in control["required_evidence"]:
            raise reviewer_base.ArtifactError(
                f"artifact.requirement {envelope['requirement']!r} does not match any of "
                f"{control_id}'s real catalog required_evidence strings"
            )

        allowed_modes = set(control["verification"]["modes"])
        if envelope["verifier_type"] not in allowed_modes:
            raise reviewer_base.ArtifactError(
                f"{control_id}'s catalog verification.modes ({sorted(allowed_modes)}) does not permit "
                f"verifier_type {envelope['verifier_type']!r} -- this control cannot be reviewed via "
                f"semantic/human judgment capability"
            )

        if envelope["result"] == "PASS" and control_id in AI_CONSTITUTION_CONTROLS:
            ai_context = raw.get("ai_authorization_context", {})
            if not isinstance(ai_context, dict) or ai_context.get("enforced_outside_model") is not True:
                raise reviewer_base.ArtifactError(
                    f"{control_id} requires ai_authorization_context.enforced_outside_model=true for a PASS "
                    f"verdict -- model refusal alone, or a system prompt instruction alone, is never sufficient "
                    f"evidence that authorization is enforced outside the model"
                )
    except (OSError, json.JSONDecodeError, reviewer_base.ArtifactError) as exc:
        runs = []
        for control_id in requested_authorized:
            capability = _capability_for_control(catalog_controls, control_id)
            runs.extend(
                adapter_base.tool_error_runs(
                    [control_id], capability, identity, f"could not verify semantic-review artifact: {exc}"
                )
            )
        return runs

    verifier_type = envelope["verifier_type"]
    requirement = envelope["requirement"]

    target_verified, reason = adapter_base.verify_target(envelope["target"], expected_target)

    if not target_verified:
        # Not attributed to the expected target -- explicit, empty-evidence
        # run so the control is never silently absent from results (same
        # lesson as Phase 2/3: always emit a run once the artifact parsed).
        return adapter_base.build_runs([], {control_id: [requirement]}, verifier_type, identity, [control_id])

    result = envelope["result"]

    # PASS/NOT_APPLICABLE cannot coexist with an unresolved security
    # assumption: if the reviewer itself flagged something it could not
    # resolve, the requirement has not actually been established (or
    # applicability has not actually been ruled in/out), regardless of how
    # well-substantiated the rest of the artifact is. FAIL is deliberately
    # NOT downgraded here -- a concrete, cited flaw must stay visible even
    # if unrelated assumptions remain open elsewhere in the review.
    if result in ("PASS", "NOT_APPLICABLE") and envelope["unresolved_assumptions"]:
        result = "UNPROVEN"

    if result == "ERROR":
        return adapter_base.tool_error_runs([control_id], verifier_type, identity, envelope["result_reasoning"])

    if result == "NOT_APPLICABLE":
        return [
            {
                "control_id": control_id,
                "applicability": "NOT_APPLICABLE",
                "verifier": {"type": verifier_type, "identity": identity},
                "evidence": [],
                "tool_error": None,
            }
        ]

    if result == "UNPROVEN":
        # Explicit empty-evidence run -- deterministically UNPROVEN via
        # evidence_model.py's "missing required evidence" path.
        return adapter_base.build_runs([], {control_id: [requirement]}, verifier_type, identity, [control_id])

    # PASS or FAIL: the reviewer's own (now structurally substantiated)
    # verdict becomes the evidence status.
    status = "SATISFIED" if result == "PASS" else "VIOLATED"
    provenance = (
        f"semantic review by {envelope['reviewer']['session_id']}: {envelope['result_reasoning']} "
        f"[files_inspected={envelope['files_inspected']}]"
    )
    contributions = [(control_id, requirement, status, provenance, None)]

    try:
        return adapter_base.build_runs(contributions, {control_id: [requirement]}, verifier_type, identity, [control_id])
    except adapter_base.NotAuthorized as exc:
        return adapter_base.tool_error_runs([control_id], verifier_type, identity, str(exc))


def _capability_for_control(catalog_controls: dict[str, dict[str, Any]], control_id: str) -> str:
    """Deterministic policy for choosing one verifier capability to report
    an ERROR/UNPROVEN run under: the alphabetically first of this
    control's real catalog modes that this module could plausibly have
    used (SEMANTIC_REVIEW or HUMAN), never a fabricated capability."""
    modes = set(catalog_controls[control_id]["verification"]["modes"]) & reviewer_base.ALLOWED_REVIEWER_VERIFIER_TYPES
    return sorted(modes)[0]


def main(argv: list[str]) -> int:
    if len(argv) < 5:
        print(
            json.dumps(
                {
                    "version": 1,
                    "error": "usage: reviewer_normalizer.py <catalog.json> <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]",
                },
                sort_keys=True,
            )
        )
        return 1

    catalog_path, artifact_arg, expected_arg = argv[1], argv[2], argv[3]
    control_ids = argv[4:]
    artifact_path = None if artifact_arg == "-" else artifact_arg
    identity = f"reviewer::{artifact_arg}"

    try:
        catalog_controls = _load_catalog_controls(catalog_path)
    except (OSError, json.JSONDecodeError, reviewer_base.ArtifactError) as exc:
        print(json.dumps({"version": 1, "error": f"could not load catalog: {exc}"}, sort_keys=True))
        return 1

    expected_target = None
    if expected_arg != "-":
        with open(expected_arg, "r", encoding="utf-8") as f:
            expected_target = json.load(f)

    runs = ingest(catalog_controls, artifact_path, control_ids, identity, expected_target)
    print(json.dumps({"version": 1, "runs": runs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
