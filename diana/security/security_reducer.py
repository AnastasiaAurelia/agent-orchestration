#!/usr/bin/env python3
"""Diana Security reducer (Security Phase 5).

The smallest deterministic reducer from a complete, validated
`security_bundle.py` bundle to one merge-boundary-relevant decision:

    PASS | REQUIRE_HUMAN | FAIL

## Policy

Per-control, by severity and result:

    CRITICAL FAIL           -> FAIL
    HIGH     FAIL           -> FAIL
    MEDIUM   FAIL           -> REQUIRE_HUMAN
    LOW      FAIL           -> REQUIRE_HUMAN  (never silently clean --
                                the catalog has no LOW-severity control
                                today, but the policy still commits to a
                                non-PASS decision if one is ever added)
    any      UNPROVEN       -> REQUIRE_HUMAN  (regardless of severity)
    any      verifier ERROR -> REQUIRE_HUMAN  (regardless of severity)
    any      NOT_APPLICABLE -> no penalty
    any      PASS           -> no penalty

The final decision is the WORST decision implied by any single control
(`PASS < REQUIRE_HUMAN < FAIL`). Positive findings are never hidden: a
trusted `FAIL` is never suppressed, averaged away, or outvoted by other
controls' `PASS`/`NOT_APPLICABLE`/errored/unavailable state -- every
control is evaluated independently and only the worst outcome survives.

## Malformed input vs. a genuine verifier ERROR (important distinction)

A **verifier-level `ERROR`** (a specific control's own aggregate result,
computed by `evidence_model.py`, because e.g. a submitted run was
malformed or used an unauthorized capability) means "we have SOME
evidence about this control but can't trust it" -- expected, ordinary,
and mapped to `REQUIRE_HUMAN`, exactly like `UNPROVEN`.

A **malformed/mismatched bundle** (wrong schema, wrong repository/base/
target SHA, wrong catalog version/hash, duplicate or unknown control,
missing canonical control) means the SECURITY DECISION INPUT ITSELF is
untrustworthy -- not one control's evidence, the whole bundle. That is
categorically worse and maps to `FAIL`, handled by `evaluate()` below
(which validates via `security_bundle.validate_bundle()` before ever
calling `reduce_bundle()`) -- `reduce_bundle()` itself is only ever
called with an already-validated bundle and never needs to make this
distinction internally.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import security_bundle  # noqa: E402

EXIT = {"PASS": 0, "REQUIRE_HUMAN": 2, "FAIL": 1}

_ORDER = {"PASS": 0, "REQUIRE_HUMAN": 1, "FAIL": 2}

SEVERITY_FAIL_DECISION = {
    "CRITICAL": "FAIL",
    "HIGH": "FAIL",
    "MEDIUM": "REQUIRE_HUMAN",
    "LOW": "REQUIRE_HUMAN",
}


def reduce_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Reduce an ALREADY-VALIDATED bundle (security_bundle.validate_bundle)
    to {"decision": ..., "reasons": [...]}. Never averages, never lets a
    clean/PASS/NOT_APPLICABLE/absent control suppress a worse one found
    elsewhere in the same bundle."""
    decision = "PASS"
    reasons: list[str] = []

    def escalate(candidate: str, reason: str) -> None:
        nonlocal decision
        reasons.append(reason)
        if _ORDER[candidate] > _ORDER[decision]:
            decision = candidate

    for item in bundle["results"]:
        control_id = item["control_id"]
        severity = item["severity"]
        result = item["result"]
        detail = "; ".join(item["reasons"]) if item["reasons"] else "(no reason recorded)"

        if result in ("PASS", "NOT_APPLICABLE"):
            continue
        if result == "FAIL":
            escalate(
                SEVERITY_FAIL_DECISION.get(severity, "FAIL"),
                f"{control_id} ({severity}) FAIL: {detail}",
            )
        elif result == "UNPROVEN":
            escalate("REQUIRE_HUMAN", f"{control_id} ({severity}) UNPROVEN: {detail}")
        elif result == "ERROR":
            escalate("REQUIRE_HUMAN", f"{control_id} ({severity}) verifier ERROR: {detail}")
        else:
            # Unreachable for an already-validated bundle; fail closed
            # defensively rather than silently ignoring an unknown state.
            escalate("FAIL", f"{control_id} has an unrecognized result state {result!r}")

    return {"decision": decision, "reasons": reasons}


def evaluate(
    bundle_raw: Any,
    catalog: dict[str, Any],
    *,
    expected_repository: str,
    expected_base_sha: str,
    expected_target_sha: str,
) -> dict[str, Any]:
    """Validate then reduce. A malformed/mismatched bundle is untrustworthy
    input -> FAIL closed, distinguished (see module docstring) from a
    validly-reported per-control verifier ERROR -> REQUIRE_HUMAN."""
    try:
        bundle = security_bundle.validate_bundle(
            bundle_raw,
            catalog,
            expected_repository=expected_repository,
            expected_base_sha=expected_base_sha,
            expected_target_sha=expected_target_sha,
        )
    except security_bundle.BundleError as exc:
        return {"decision": "FAIL", "reasons": [f"security bundle is untrustworthy: {exc}"]}
    return reduce_bundle(bundle)


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        result = {
            "decision": "FAIL",
            "reasons": [
                "usage: security_reducer.py <catalog.json> <bundle.json> "
                "<expected_repository> <expected_base_sha> <expected_target_sha>"
            ],
        }
        print(json.dumps(result, sort_keys=True))
        return EXIT["FAIL"]

    catalog_path, bundle_path, expected_repository, expected_base_sha, expected_target_sha = argv[1:]
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
        with open(bundle_path, "r", encoding="utf-8") as f:
            bundle_raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        result = {"decision": "FAIL", "reasons": [f"could not read catalog/bundle: {exc}"]}
        print(json.dumps(result, sort_keys=True))
        return EXIT["FAIL"]

    result = evaluate(
        bundle_raw,
        catalog,
        expected_repository=expected_repository,
        expected_base_sha=expected_base_sha,
        expected_target_sha=expected_target_sha,
    )
    print(json.dumps(result, sort_keys=True))
    return EXIT[result["decision"]]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
