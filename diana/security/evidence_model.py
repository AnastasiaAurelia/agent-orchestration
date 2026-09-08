#!/usr/bin/env python3
"""Diana Security result/evidence model (Security Phase 1).

Deterministic, Python-stdlib-only. No network, no LLM, no security-tool
invocation, no wall-clock reads. Given the Phase 0 control catalog and a
JSON array of "verification run" records (one attempted verification per
control, produced by some future static/dynamic/semantic verifier -- none
of which exist yet), computes one result per run:

    PASS            -- all of the control's required_evidence items are
                       present and SATISFIED
    FAIL            -- positive evidence that a required_evidence item is
                       VIOLATED
    NOT_APPLICABLE  -- the run declares the control irrelevant to the
                       target
    UNPROVEN        -- the control may be applicable, but required
                       evidence is incomplete (never PASS by default)
    ERROR           -- the verifier reported a tool/execution failure, or
                       the run itself is malformed

Core invariant: "no finding" is never PASS. A run with no evidence, an
unrecognized control, or a schema violation fails closed to UNPROVEN or
ERROR -- it can never be silently upgraded to PASS.

This module does not decide anything about a real repository and does not
install or call any scanner. It only defines and computes the result
contract that later phases (static adapters, dynamic verification, a
semantic reviewer) will produce runs for.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_catalog  # noqa: E402  (reuse the Phase 0 verifier-type enum + validator)

RESULT_STATES = {"PASS", "FAIL", "NOT_APPLICABLE", "UNPROVEN", "ERROR"}
APPLICABILITY_STATES = {"APPLICABLE", "NOT_APPLICABLE", "UNKNOWN"}
EVIDENCE_STATUSES = {"SATISFIED", "VIOLATED"}

ALLOWED_RUN_FIELDS = {"control_id", "applicability", "verifier", "evidence", "tool_error", "observed_at"}
ALLOWED_VERIFIER_FIELDS = {"type", "identity"}
ALLOWED_EVIDENCE_ITEM_FIELDS = {"requirement", "status", "provenance", "detail"}
ALLOWED_TOOL_ERROR_FIELDS = {"message"}


class MalformedRun(ValueError):
    """A run does not conform to the evidence-model schema."""


class CatalogError(ValueError):
    """The control catalog itself could not be loaded/validated."""


def load_controls(catalog_path: str) -> dict[str, dict[str, Any]]:
    """Load and structurally validate the Phase 0 catalog, fail closed."""
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"could not read/parse catalog: {exc}") from exc

    errors = validate_catalog.validate(catalog)
    if errors:
        raise CatalogError(f"catalog is not structurally valid: {errors}")

    return {c["id"]: c for c in catalog["controls"]}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MalformedRun(message)


def _validate_run_schema(run: Any, controls: dict[str, dict[str, Any]]) -> None:
    _require(isinstance(run, dict), "run must be an object")

    unknown = set(run.keys()) - ALLOWED_RUN_FIELDS
    _require(not unknown, f"unknown top-level field(s): {sorted(unknown)}")

    control_id = run.get("control_id")
    _require(
        isinstance(control_id, str) and control_id in controls,
        f"control_id must be a known catalog id, got {control_id!r}",
    )

    applicability = run.get("applicability")
    _require(
        applicability in APPLICABILITY_STATES,
        f"applicability must be one of {sorted(APPLICABILITY_STATES)}, got {applicability!r}",
    )

    verifier = run.get("verifier")
    _require(isinstance(verifier, dict), "verifier must be an object")
    unknown_v = set(verifier.keys()) - ALLOWED_VERIFIER_FIELDS
    _require(not unknown_v, f"verifier has unknown field(s): {sorted(unknown_v)}")
    v_type = verifier.get("type")
    _require(
        v_type in validate_catalog.ALLOWED_VERIFIER_TYPES,
        f"verifier.type must be a known verifier type, got {v_type!r}",
    )
    v_identity = verifier.get("identity")
    _require(
        isinstance(v_identity, str) and v_identity.strip(),
        "verifier.identity must be a non-empty string",
    )

    evidence = run.get("evidence")
    _require(isinstance(evidence, list), "evidence must be a list")
    required = set(controls[control_id]["required_evidence"]) if isinstance(control_id, str) and control_id in controls else set()
    seen_requirements: set[str] = set()
    for item in evidence:
        _require(isinstance(item, dict), "each evidence item must be an object")
        unknown_e = set(item.keys()) - ALLOWED_EVIDENCE_ITEM_FIELDS
        _require(not unknown_e, f"evidence item has unknown field(s): {sorted(unknown_e)}")

        req = item.get("requirement")
        _require(
            isinstance(req, str) and req in required,
            f"evidence item requirement does not match this control's required_evidence: {req!r}",
        )
        _require(req not in seen_requirements, f"duplicate evidence item for requirement: {req!r}")
        seen_requirements.add(req)

        status = item.get("status")
        _require(
            status in EVIDENCE_STATUSES,
            f"evidence item status must be one of {sorted(EVIDENCE_STATUSES)}, got {status!r}",
        )

        provenance = item.get("provenance")
        _require(
            isinstance(provenance, str) and provenance.strip(),
            "evidence item provenance must be a non-empty string",
        )

        detail = item.get("detail")
        _require(detail is None or isinstance(detail, str), "evidence item detail must be a string if present")

    tool_error = run.get("tool_error")
    if tool_error is not None:
        _require(isinstance(tool_error, dict), "tool_error must be an object or null")
        unknown_t = set(tool_error.keys()) - ALLOWED_TOOL_ERROR_FIELDS
        _require(not unknown_t, f"tool_error has unknown field(s): {sorted(unknown_t)}")
        message = tool_error.get("message")
        _require(
            isinstance(message, str) and message.strip(),
            "tool_error.message must be a non-empty string",
        )

    observed_at = run.get("observed_at")
    _require(observed_at is None or isinstance(observed_at, str), "observed_at must be a string if present")


def evaluate_run(run: Any, controls: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Evaluate one verification run against the catalog's evidence contract.

    Pure and deterministic: never reads the clock, network, or filesystem.
    Never raises -- a malformed run fails closed to an ERROR result record
    rather than propagating an exception, so a batch of many runs can
    still report on the well-formed ones.
    """
    try:
        _validate_run_schema(run, controls)
    except MalformedRun as exc:
        control_id = run.get("control_id") if isinstance(run, dict) else None
        return {
            "control_id": control_id if isinstance(control_id, str) else None,
            "result": "ERROR",
            "applicability": None,
            "verifier": None,
            "evidence": [],
            "reasons": [f"malformed evidence: {exc}"],
            "observed_at": run.get("observed_at") if isinstance(run, dict) else None,
        }

    control = controls[run["control_id"]]
    applicability = run["applicability"]
    verifier = run["verifier"]
    evidence = run["evidence"]
    tool_error = run.get("tool_error")

    if tool_error is not None:
        result = "ERROR"
        reasons = [f"verifier reported a tool/execution error: {tool_error['message']}"]
    elif applicability == "NOT_APPLICABLE":
        result = "NOT_APPLICABLE"
        reasons = ["control determined not applicable to this target"]
    elif applicability == "UNKNOWN":
        result = "UNPROVEN"
        reasons = ["applicability has not been established"]
    else:
        required: list[str] = control["required_evidence"]
        by_requirement = {item["requirement"]: item for item in evidence}

        violated = [r for r in required if r in by_requirement and by_requirement[r]["status"] == "VIOLATED"]
        if violated:
            result = "FAIL"
            reasons = [f"required evidence violated: {r}" for r in violated]
        else:
            missing = [r for r in required if r not in by_requirement]
            if missing:
                result = "UNPROVEN"
                reasons = [f"missing required evidence: {r}" for r in missing]
            else:
                result = "PASS"
                reasons = [f"all {len(required)} required evidence item(s) satisfied"]

    return {
        "control_id": run["control_id"],
        "result": result,
        "applicability": applicability,
        "verifier": verifier,
        "evidence": evidence,
        "reasons": reasons,
        "observed_at": run.get("observed_at"),
    }


def evaluate_batch(runs: list[Any], controls: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [evaluate_run(run, controls) for run in runs]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(json.dumps({"version": 1, "error": "usage: evidence_model.py <catalog.json> <runs.json>"}, sort_keys=True))
        return 1

    catalog_path, runs_path = argv[1], argv[2]

    try:
        controls = load_controls(catalog_path)
    except CatalogError as exc:
        print(json.dumps({"version": 1, "error": str(exc)}, sort_keys=True))
        return 1

    try:
        with open(runs_path, "r", encoding="utf-8") as f:
            runs = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"version": 1, "error": f"could not read/parse runs file: {exc}"}, sort_keys=True))
        return 1

    if not isinstance(runs, list):
        print(json.dumps({"version": 1, "error": "runs file must contain a JSON array"}, sort_keys=True))
        return 1

    results = evaluate_batch(runs, controls)
    print(json.dumps({"version": 1, "results": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
