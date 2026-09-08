#!/usr/bin/env python3
"""Diana Security 75/75 coverage matrix (Security Phase 6).

Answers, for every canonical control `SEC-001`..`SEC-075`, one honest
question: **"Can Diana truthfully and reproducibly evaluate this control
today?"** -- and proves the answer executably rather than asserting it in
prose. Deterministic, Python-stdlib-only, no network, no LLM.

This module does NOT invent new evidence, does NOT run any tool/scenario/
reviewer session, and does NOT fabricate a PASS/FAIL for any control. It
does two genuinely different, clearly separated things:

1. **`resulting_state`** -- the REAL, LIVE result, computed by actually
   running `security_bundle.build_bundle()` with the REAL, unmodified
   `ci_verifier_runs.py` output (today: `[]`, since no live verifier
   execution is wired into CI -- Security Phase 5's own documented,
   honest limitation) against the real catalog. As of today this is
   `UNPROVEN` for all 75 controls, uniformly -- not because this module
   says so, but because that is what the real, already-shipped pipeline
   actually computes when asked. "No finding" is never treated as PASS.

2. **`capability_coverage`** -- a STATIC, code-level fact, computed by
   cross-referencing each control's real `catalog.json` `required_evidence`
   contract (plus its `dynamic_required`/`human_judgment_required` gates)
   against the REAL authorization tables already shipped in Phase 2-4:
   `adapters/{gitleaks,osv_scanner,semgrep}_adapter.py`'s `AUTHORIZED_
   EVIDENCE` dicts (imported directly, never re-typed here), `dynamic/
   scenarios.py`'s `SCENARIO_REGISTRY` (imported directly), and the
   reviewer's catalog-derived authorization rule (any control whose
   `verification.modes` includes `SEMANTIC_REVIEW`/`HUMAN`, mirroring
   `reviewer_normalizer._authorized_control_ids()` exactly). This answers
   "if a human/operator produced a real, valid artifact and fed it
   through the existing normalizers today, could this control's evidence
   contract be satisfied at all?" -- entirely independent of whether that
   has actually happened (it hasn't, for any control, today).

`capability_coverage` is never substituted for `resulting_state`, and
`resulting_state` is never upgraded because `capability_coverage` looks
good. A control can be `FULLY_COVERED` (every required_evidence item plus
gate has an implemented path) and still show `resulting_state: UNPROVEN`
-- that is not a contradiction, it is the honest state of a track that
has built normalizers but has not yet wired live verifier execution into
CI (Security Phase 5's `ci_verifier_runs.py` extension point, still
empty).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SEC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SEC_DIR))
import evidence_model  # noqa: E402
import security_bundle  # noqa: E402
import ci_verifier_runs  # noqa: E402
import validate_catalog  # noqa: E402

sys.path.insert(0, str(SEC_DIR / "adapters"))
import gitleaks_adapter  # noqa: E402
import osv_scanner_adapter  # noqa: E402
import semgrep_adapter  # noqa: E402
import deterministic_repo_adapter  # noqa: E402

sys.path.insert(0, str(SEC_DIR / "dynamic"))
import scenarios as dynamic_scenarios  # noqa: E402

sys.path.insert(0, str(SEC_DIR / "reviewer"))
import reviewer_base  # noqa: E402

DYNAMIC_FAMILY_MODES = {
    "DYNAMIC_API",
    "DYNAMIC_BROWSER",
    "DYNAMIC_DB",
    "DYNAMIC_CONCURRENCY",
    "PROVIDER_SANDBOX",
}

STATIC_ADAPTERS = {
    "gitleaks_adapter": gitleaks_adapter,
    "osv_scanner_adapter": osv_scanner_adapter,
    "semgrep_adapter": semgrep_adapter,
    "deterministic_repo_adapter": deterministic_repo_adapter,
}


def _static_adapter_coverage() -> dict[str, list[tuple[str, str]]]:
    """control_id -> [(adapter_module_name, requirement), ...] for every
    (control, requirement) pair a real, shipped Phase 2 adapter is
    actually authorized to prove, read directly from each adapter's own
    AUTHORIZED_EVIDENCE dict (never re-typed/duplicated here)."""
    coverage: dict[str, list[tuple[str, str]]] = {}
    for name, module in STATIC_ADAPTERS.items():
        for control_id, requirements in module.AUTHORIZED_EVIDENCE.items():
            coverage.setdefault(control_id, [])
            for requirement in requirements:
                coverage[control_id].append((name, requirement))
    return coverage


def _dynamic_scenario_coverage() -> dict[str, list[tuple[str, str, frozenset[str]]]]:
    """control_id -> [(scenario_id, requirement, allowed_verifier_modes), ...]
    read directly from dynamic/scenarios.py's real registry."""
    coverage: dict[str, list[tuple[str, str, frozenset[str]]]] = {}
    for scenario_id, spec in dynamic_scenarios.SCENARIO_REGISTRY.items():
        control_id = spec["control_id"]
        coverage.setdefault(control_id, [])
        coverage[control_id].append((scenario_id, spec["requirement"], frozenset(spec["allowed_verifier_modes"])))
    return coverage


def _reviewer_authorized_controls(controls: dict[str, dict[str, Any]]) -> set[str]:
    """Mirrors reviewer_normalizer._authorized_control_ids() exactly:
    any control whose real catalog verification.modes intersects
    SEMANTIC_REVIEW/HUMAN. A reviewer contribution, once a real session
    produces one, can target ANY of that control's required_evidence
    items (the specific requirement is caller-supplied per submission,
    checked against the real catalog contract) -- so reviewer coverage
    is control-level, not tied to one specific requirement string."""
    return {
        control_id
        for control_id, control in controls.items()
        if set(control["verification"]["modes"]) & reviewer_base.ALLOWED_REVIEWER_VERIFIER_TYPES
    }


def build_matrix(
    catalog: dict[str, Any],
    repository: str,
    base_sha: str,
    target_sha: str,
    runs: list[Any] | None = None,
) -> dict[str, Any]:
    """`runs`, when given, is used verbatim instead of calling the real
    (network-dependent, since Security Track remediation round A)
    `ci_verifier_runs.collect_trusted_runs()`. This exists so tests can
    exercise this function deterministically and offline with a FIXED
    runs list -- the CLI (`main()` below) never passes `runs`, so the
    real command-line tool always uses genuine, live-collected evidence,
    exactly as `diana-security-gate.yml` does."""
    errors = validate_catalog.validate(catalog)
    if errors:
        raise ValueError(f"catalog is not structurally valid: {errors}")

    controls = {c["id"]: c for c in catalog["controls"]}
    static_cov = _static_adapter_coverage()
    dynamic_cov = _dynamic_scenario_coverage()
    reviewer_authorized = _reviewer_authorized_controls(controls)

    # The REAL, live resulting state -- computed by actually running the
    # real, unmodified, already-shipped pipeline with the real trusted
    # verifier runs (unless a fixed `runs` list was injected for testing).
    # Never fabricated, never simulated.
    real_runs = runs if runs is not None else ci_verifier_runs.collect_trusted_runs()
    real_bundle = security_bundle.build_bundle(catalog, real_runs, repository, base_sha, target_sha)
    real_results = {r["control_id"]: r for r in real_bundle["results"]}
    # Distinct from real_results: build_bundle() fills an UNPROVEN
    # placeholder into its OWN "results" for every canonical control, so
    # every control_id always appears there regardless of whether a real
    # run was submitted. live_execution_exists must instead reflect the
    # RAW `real_runs` input actually received -- the set of control_ids
    # that genuinely got a submitted run this invocation.
    controls_with_real_runs = {r.get("control_id") for r in real_runs if isinstance(r, dict)}

    rows = []
    for control_id in sorted(controls.keys()):
        control = controls[control_id]
        required_evidence: list[str] = control["required_evidence"]
        modes = control["verification"]["modes"]
        dynamic_required = control["verification"]["dynamic_required"]
        human_judgment_required = control["verification"]["human_judgment_required"]

        per_requirement = []
        for requirement in required_evidence:
            paths = []
            for adapter_name, req in static_cov.get(control_id, []):
                if req == requirement:
                    paths.append({"kind": "static_adapter", "module": adapter_name})
            for scenario_id, req, allowed_modes in dynamic_cov.get(control_id, []):
                if req == requirement:
                    paths.append({"kind": "dynamic_scenario", "module": scenario_id})
            if control_id in reviewer_authorized:
                paths.append({"kind": "semantic_reviewer", "module": "reviewer_normalizer"})
            per_requirement.append({"requirement": requirement, "implemented_paths": paths})

        all_items_covered = all(item["implemented_paths"] for item in per_requirement)
        any_items_covered = any(item["implemented_paths"] for item in per_requirement)

        dynamic_gate_satisfiable = (not dynamic_required) or (control_id in dynamic_cov)
        human_gate_satisfiable = (not human_judgment_required) or (control_id in reviewer_authorized)

        if all_items_covered and dynamic_gate_satisfiable and human_gate_satisfiable:
            capability_coverage = "FULLY_COVERED"
        elif any_items_covered or (dynamic_required and control_id in dynamic_cov) or (human_judgment_required and control_id in reviewer_authorized):
            capability_coverage = "PARTIALLY_COVERED"
        else:
            capability_coverage = "NOT_COVERED"

        gaps = []
        for item in per_requirement:
            if not item["implemented_paths"]:
                gaps.append(f"no implemented capability for required_evidence: {item['requirement']!r}")
        if dynamic_required and control_id not in dynamic_cov:
            gaps.append("dynamic_required=true but no dynamic scenario is registered for this control")
        if human_judgment_required and control_id not in reviewer_authorized:
            gaps.append("human_judgment_required=true but no SEMANTIC_REVIEW/HUMAN mode is permitted for this control (catalog gap, not expected to occur)")
        if capability_coverage == "FULLY_COVERED":
            gaps.append("capability exists but is not yet wired into live CI: ci_verifier_runs.py returns [] today (Security Phase 5's documented extension point)")

        real_result = real_results.get(control_id, {})

        rows.append(
            {
                "control_id": control_id,
                "title": control["title"],
                "severity": control["severity"],
                "applicability_signals": control["applicability"]["signals"],
                "permitted_verifier_modes": modes,
                "dynamic_required": dynamic_required,
                "human_judgment_required": human_judgment_required,
                "required_evidence": per_requirement,
                "capability_coverage": capability_coverage,
                # Derived, never asserted: a control has live_execution_exists
                # = True iff at least one REAL run was actually submitted for
                # it in THIS bundle (control_id appears in the real,
                # already-computed evidence_model.py output) -- distinct from
                # capability_coverage, which only says a path COULD exist.
                "live_execution_exists": control_id in controls_with_real_runs,
                "evidence_can_be_produced_today_offline": capability_coverage == "FULLY_COVERED",
                "resulting_state": real_result.get("result", "UNPROVEN"),
                "explicit_reason": "; ".join(real_result.get("reasons", ["no verification runs submitted for this control"])),
                "evidence_reference": (
                    f"security_bundle.build_bundle(catalog, ci_verifier_runs.collect_trusted_runs(), "
                    f"{repository!r}, {base_sha!r}, {target_sha!r}) -> results[{control_id!r}]"
                ),
                "remaining_gap": gaps if gaps else ["none -- fully covered and live (not expected today; see resulting_state)"],
            }
        )

    live_wired_count = sum(1 for r in rows if r["live_execution_exists"])
    coverage_counts = {"FULLY_COVERED": 0, "PARTIALLY_COVERED": 0, "NOT_COVERED": 0}
    result_counts = {"PASS": 0, "FAIL": 0, "NOT_APPLICABLE": 0, "UNPROVEN": 0, "ERROR": 0}
    for row in rows:
        coverage_counts[row["capability_coverage"]] += 1
        result_counts[row["resulting_state"]] += 1

    return {
        "version": 1,
        "repository": repository,
        "base_sha": base_sha,
        "target_sha": target_sha,
        "catalog_version": catalog["catalog_version"],
        "catalog_sha256": security_bundle.catalog_fingerprint(catalog),
        "total_controls": len(rows),
        "live_execution_wired_count": live_wired_count,
        "capability_coverage_counts": coverage_counts,
        "resulting_state_counts": result_counts,
        "rows": rows,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(
            json.dumps(
                {
                    "version": 1,
                    "error": "usage: coverage_matrix.py <catalog.json> <repository> <base_sha> <target_sha>",
                },
                sort_keys=True,
            )
        )
        return 1

    catalog_path, repository, base_sha, target_sha = argv[1:]
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
        matrix = build_matrix(catalog, repository, base_sha, target_sha)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"version": 1, "error": str(exc)}, sort_keys=True))
        return 1

    print(json.dumps(matrix, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
