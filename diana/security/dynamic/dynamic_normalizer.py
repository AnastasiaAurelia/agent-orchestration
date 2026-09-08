#!/usr/bin/env python3
"""Diana Security dynamic-verification normalizer (Security Phase 3).

Consumes one already-produced dynamic scenario evidence artifact (see
`dynamic_base.py` for the envelope shape and trust model) and emits
`diana/security/evidence_model.py`-compatible run records. This module
never executes an HTTP request, drives a browser, queries a database, or
calls a payment provider -- it only normalizes evidence an external
scenario runner already produced, exactly as
`diana/security/adapters/*.py` only normalize already-produced static
tool reports. Reuses `adapter_base.build_runs()` /
`adapter_base.tool_error_runs()` / `adapter_base.tool_unavailable_runs()`
/ `adapter_base.NotAuthorized` directly -- those functions only construct
evidence_model.py output records and have no static-adapter-specific
behavior, so this is genuine reuse, not duplication.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dynamic_base  # noqa: E402
from scenarios import SCENARIO_REGISTRY  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapters"))
import adapter_base  # noqa: E402


def _authorized_control_ids() -> list[str]:
    return sorted({spec["control_id"] for spec in SCENARIO_REGISTRY.values()})


def evaluate_scenario(envelope: dict[str, Any], expected_target: dict[str, Any] | None) -> tuple[str | None, str]:
    """Returns (status, provenance) where status is 'SATISFIED',
    'VIOLATED', or None (no contribution -- caller leaves this control's
    evidence empty, which evidence_model.py will surface as UNPROVEN).
    Never raises -- ArtifactError is raised earlier, by the caller, from
    envelope loading / scenario lookup / safety-invariant checks."""
    assertions_by_id = {a["id"]: a["outcome"] for a in envelope["assertions"]}
    spec = SCENARIO_REGISTRY[envelope["scenario"]["id"]]

    cleanup = envelope["cleanup"]
    cleanup_note = ""
    if cleanup["required"] and (not cleanup["performed"] or not cleanup["success"]):
        cleanup_note = f" [cleanup visibility: required={cleanup['required']} performed={cleanup['performed']} success={cleanup['success']}]"

    missing = [aid for aid in spec["required_assertions"] if aid not in assertions_by_id]
    if missing:
        return None, f"skipped required assertion(s): {missing}{cleanup_note}"

    failed = [aid for aid in spec["required_assertions"] if assertions_by_id[aid] == "FAILED"]

    if failed:
        identity_verified, reason = dynamic_base.verify_scenario_identity(envelope["target"], expected_target)
        if identity_verified:
            return "VIOLATED", f"required assertion(s) failed: {failed}{cleanup_note}"
        return None, f"assertion(s) failed but target identity not verified ({reason}) -- not attributed{cleanup_note}"

    target_verified, reason = dynamic_base.verify_scenario_target(envelope["target"], expected_target)
    if target_verified:
        return (
            "SATISFIED",
            f"all required assertion(s) passed ({spec['required_assertions']}); target verified "
            f"(repository={envelope['target'].get('repository')!r}, commit={envelope['target'].get('commit')!r}, "
            f"environment={envelope['environment']!r}){cleanup_note}",
        )
    return None, f"all required assertions passed, but target/scope not verified ({reason}){cleanup_note}"


def ingest(
    artifact_path: str | None,
    control_ids: list[str],
    identity: str,
    expected_target: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Returns evidence_model.py run records for the requested control_ids
    this normalizer's scenario registry is authorized for. Controls no
    registered scenario covers are silently skipped."""
    requested_authorized = [c for c in control_ids if c in _authorized_control_ids()]
    if not requested_authorized:
        return []

    # capability placeholder resolved per-scenario below; use a generic
    # identity/capability pair for the tool-unavailable path since we do
    # not yet know which verifier_mode an unavailable artifact would have
    # declared.
    if artifact_path is None or not Path(artifact_path).exists():
        return adapter_base.tool_unavailable_runs(
            requested_authorized, "DYNAMIC_API", identity, "no scenario artifact provided"
        )

    try:
        with open(artifact_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        envelope = dynamic_base.load_scenario_envelope(raw)

        scenario_id = envelope["scenario"]["id"]
        spec = SCENARIO_REGISTRY.get(scenario_id)
        if spec is None:
            raise dynamic_base.ArtifactError(f"unknown scenario.id {scenario_id!r} -- not in the scenario registry")

        result = envelope["result"]
        if result["control_id"] != spec["control_id"] or result["requirement"] != spec["requirement"]:
            raise dynamic_base.ArtifactError(
                f"artifact.result ({result['control_id']!r}, {result['requirement']!r}) does not match "
                f"scenario {scenario_id!r}'s registered ({spec['control_id']!r}, {spec['requirement']!r})"
            )

        if spec["control_id"] not in requested_authorized:
            return []

        if envelope["verifier_mode"] not in spec["allowed_verifier_modes"]:
            raise dynamic_base.ArtifactError(
                f"scenario {scenario_id!r} requires verifier_mode in {sorted(spec['allowed_verifier_modes'])}, "
                f"artifact declares {envelope['verifier_mode']!r}"
            )

        dynamic_base.validate_scenario_safety_invariants(envelope, spec)
    except (OSError, json.JSONDecodeError, dynamic_base.ArtifactError) as exc:
        # We may not have successfully parsed enough to know the real
        # verifier_mode; DYNAMIC_API is used as a reporting placeholder
        # for the ERROR run's verifier.type (the run's evidence is empty
        # regardless, so this does not fabricate any capability claim).
        return adapter_base.tool_error_runs(
            requested_authorized, "DYNAMIC_API", identity, f"could not verify dynamic scenario artifact: {exc}"
        )

    capability = envelope["verifier_mode"]
    control_id = spec["control_id"]
    requirement = spec["requirement"]

    status, provenance = evaluate_scenario(envelope, expected_target)

    contributions = []
    if status is not None:
        contributions.append((control_id, requirement, status, provenance, None))

    authorized = {control_id: [requirement]}
    try:
        return adapter_base.build_runs(contributions, authorized, capability, identity, [control_id])
    except adapter_base.NotAuthorized as exc:
        return adapter_base.tool_error_runs([control_id], capability, identity, str(exc))


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(
            json.dumps(
                {
                    "version": 1,
                    "error": "usage: dynamic_normalizer.py <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]",
                },
                sort_keys=True,
            )
        )
        return 1

    artifact_arg, expected_arg = argv[1], argv[2]
    control_ids = argv[3:]
    artifact_path = None if artifact_arg == "-" else artifact_arg
    identity = f"dynamic::{artifact_arg}"

    expected_target = None
    if expected_arg != "-":
        with open(expected_arg, "r", encoding="utf-8") as f:
            expected_target = json.load(f)

    runs = ingest(artifact_path, control_ids, identity, expected_target)
    print(json.dumps({"version": 1, "runs": runs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
