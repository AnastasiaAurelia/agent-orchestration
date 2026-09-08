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

## No shared verifier-capability placeholder

The first implementation reported "tool unavailable" and "artifact
malformed before scenario identity is known" using a hardcoded
`"DYNAMIC_API"` capability for every requested control -- unsound for a
control like SEC-012 (only permits `DYNAMIC_BROWSER`), SEC-044 (only
`DYNAMIC_CONCURRENCY`), or SEC-066 (only `DYNAMIC_DB`): a missing verifier
must never manufacture a capability violation merely because the
normalizer guessed wrong. `_capability_for_control()` looks up each
requested control's own registered `allowed_verifier_modes` and
deterministically picks one (alphabetically first) -- both error paths
below now build one run *per control*, each tagged with that control's
own real, registry-permitted capability, never a single shared guess
applied to a whole batch.
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


def _spec_for_control(control_id: str) -> dict[str, Any]:
    for spec in SCENARIO_REGISTRY.values():
        if spec["control_id"] == control_id:
            return spec
    raise KeyError(control_id)


def _capability_for_control(control_id: str) -> str:
    """Deterministic policy for choosing one verifier capability to report
    an ERROR/UNPROVEN run under, when a control's actual scenario artifact
    isn't available or hasn't been identified yet: the alphabetically
    first of that control's own registered allowed_verifier_modes. Never a
    capability the control doesn't actually permit."""
    return sorted(_spec_for_control(control_id)["allowed_verifier_modes"])[0]


def evaluate_scenario(envelope: dict[str, Any], expected_context: dict[str, Any] | None) -> tuple[str | None, str]:
    """Returns (status, provenance) where status is 'SATISFIED',
    'VIOLATED', 'ERROR', or None (no contribution -- caller leaves this
    control's evidence empty, which evidence_model.py will surface as
    UNPROVEN). Never raises -- ArtifactError is raised earlier, by the
    caller, from envelope loading / scenario lookup / safety-invariant
    checks."""
    assertions_by_id = {a["id"]: a["outcome"] for a in envelope["assertions"]}
    spec = SCENARIO_REGISTRY[envelope["scenario"]["id"]]
    execution_context = dynamic_base.build_execution_context(envelope)

    cleanup = envelope["cleanup"]
    cleanup_unresolved = cleanup["required"] and (not cleanup["performed"] or not cleanup["success"])
    cleanup_note = ""
    if cleanup["required"]:
        cleanup_note = f" [cleanup: required={cleanup['required']} performed={cleanup['performed']} success={cleanup['success']}]"

    missing = [aid for aid in spec["required_assertions"] if aid not in assertions_by_id]
    if missing:
        return None, f"skipped required assertion(s): {missing}{cleanup_note}"

    failed = [aid for aid in spec["required_assertions"] if assertions_by_id[aid] == "FAILED"]

    if failed:
        # A real observed violation is never suppressed by cleanup state --
        # the security finding is reported regardless.
        identity_verified, reason = dynamic_base.verify_scenario_identity(execution_context, expected_context)
        if identity_verified:
            return "VIOLATED", f"required assertion(s) failed: {failed}{cleanup_note}"
        return None, f"assertion(s) failed but execution context not verified ({reason}) -- not attributed{cleanup_note}"

    # All required assertions passed. PASS requires no unresolved
    # execution error -- a mutating scenario whose required cleanup did
    # not complete successfully did not run safely/reliably, so it cannot
    # be trusted as a clean SATISFIED, regardless of how the assertions
    # themselves came out.
    if cleanup_unresolved:
        return (
            "ERROR",
            "all required assertion(s) passed, but required cleanup did not complete successfully "
            f"(performed={cleanup['performed']}, success={cleanup['success']}) -- the mutating verification "
            f"run did not complete safely/reliably{cleanup_note}",
        )

    target_verified, reason = dynamic_base.verify_scenario_target(execution_context, expected_context)
    if target_verified:
        return (
            "SATISFIED",
            f"all required assertion(s) passed ({spec['required_assertions']}); execution context verified "
            f"(environment={execution_context.get('environment')!r}, repository={execution_context.get('repository')!r}, "
            f"commit={execution_context.get('commit')!r}){cleanup_note}",
        )
    return None, f"all required assertions passed, but execution context not verified ({reason}){cleanup_note}"


def ingest(
    artifact_path: str | None,
    control_ids: list[str],
    identity: str,
    expected_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Returns evidence_model.py run records for the requested control_ids
    this normalizer's scenario registry is authorized for. Controls no
    registered scenario covers are silently skipped."""
    requested_authorized = [c for c in control_ids if c in _authorized_control_ids()]
    if not requested_authorized:
        return []

    if artifact_path is None or not Path(artifact_path).exists():
        runs: list[dict[str, Any]] = []
        for control_id in requested_authorized:
            capability = _capability_for_control(control_id)
            runs.extend(
                adapter_base.tool_unavailable_runs([control_id], capability, identity, "no scenario artifact provided")
            )
        return runs

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
        # Scenario identity may not be known yet (e.g. the artifact never
        # even parsed) -- report per requested control using each
        # control's own registry-permitted capability, never a shared
        # guess.
        runs = []
        for control_id in requested_authorized:
            capability = _capability_for_control(control_id)
            runs.extend(
                adapter_base.tool_error_runs(
                    [control_id], capability, identity, f"could not verify dynamic scenario artifact: {exc}"
                )
            )
        return runs

    capability = envelope["verifier_mode"]
    control_id = spec["control_id"]
    requirement = spec["requirement"]

    status, provenance = evaluate_scenario(envelope, expected_context)

    if status == "ERROR":
        return adapter_base.tool_error_runs([control_id], capability, identity, provenance)

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
                    "error": "usage: dynamic_normalizer.py <artifact.json|-> <expected_context.json|-> <control_id> [control_id...]",
                },
                sort_keys=True,
            )
        )
        return 1

    artifact_arg, expected_arg = argv[1], argv[2]
    control_ids = argv[3:]
    artifact_path = None if artifact_arg == "-" else artifact_arg
    identity = f"dynamic::{artifact_arg}"

    expected_context = None
    if expected_arg != "-":
        with open(expected_arg, "r", encoding="utf-8") as f:
            expected_context = json.load(f)

    runs = ingest(artifact_path, control_ids, identity, expected_context)
    print(json.dumps({"version": 1, "runs": runs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
