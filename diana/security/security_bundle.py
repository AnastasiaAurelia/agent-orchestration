#!/usr/bin/env python3
"""Diana Security result bundle (Security Phase 5).

Defines the smallest deterministic "complete Security result bundle" --
one aggregate result for every canonical catalog control (`SEC-001`..
`SEC-075`), bound to an exact `(repository, base_sha, target_sha,
catalog_version, catalog_sha256)` tuple so a bundle generated for one
target/catalog can never be mistaken for another:

    trusted verifier execution (diana/security/ci_verifier_runs.py)
            |
    normalized Phase 1 runs
            |
    evidence_model.py
            |
    complete security result bundle (this module)
            |
    security_reducer.py

`build_bundle()` is the only producer: it runs `evidence_model.evaluate()`
over the supplied runs and then explicitly fills in `UNPROVEN` for every
canonical control that received zero runs, so "no verification run
submitted" is never silently absent from the bundle -- it is a literal,
visible `UNPROVEN` result, exactly like every other Security Track phase's
"no finding != PASS" invariant.

`validate_bundle()` is the only consumer-side gate: it fails closed on any
structural, identity, or catalog mismatch (malformed bundle, wrong
repository/base_sha/target_sha, wrong catalog version/hash, unknown or
duplicate control, missing canonical control, invalid result state, or a
per-control severity that disagrees with the TRUSTED catalog passed in --
an "impossible severity mismatch"). The trusted catalog is always supplied
by the caller (see `security_reducer.py` / `diana/ci/run-security-gate.py`
for how the caller obtains it from the pull request's PROTECTED BASE SHA,
never the PR head) -- this module never reads a catalog file itself, so it
cannot be tricked into trusting a PR-head-supplied catalog.

## Producer trust boundary (important, read before extending this)

A bundle's own JSON shape and internal SHA-256 hash agreement prove
nothing about who produced it or whether its `runs` came from a genuine,
CI-controlled trusted verifier. `build_bundle()` performs no independent
verification of its `runs` argument's provenance -- that responsibility
belongs entirely to the CALLER: only `diana/ci/run-security-gate.py`
invoking `diana/security/ci_verifier_runs.py` (which currently always
returns an empty list -- no live verifier execution is wired into CI yet)
is a legitimate, trusted caller. A PR body claim, a checked-in JSON file,
or any other PR-author-controlled input must never be passed to
`build_bundle()` as if it were trusted `runs`. See
`diana/security/README.md`'s "Security Phase 5" section for the full
trust-boundary writeup.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence_model  # noqa: E402  (reuse the Phase 1 aggregation engine directly)
import validate_catalog  # noqa: E402  (reuse the Phase 0 structural validator)


class BundleError(ValueError):
    pass


ALLOWED_RESULT_STATES = {"PASS", "FAIL", "NOT_APPLICABLE", "UNPROVEN", "ERROR"}
ALLOWED_APPLICABILITY_STATES = {"APPLICABLE", "NOT_APPLICABLE", "UNKNOWN", None}

REQUIRED_BUNDLE_FIELDS = {
    "version",
    "repository",
    "base_sha",
    "target_sha",
    "catalog_version",
    "catalog_sha256",
    "generated_count",
    "results",
}
REQUIRED_RESULT_FIELDS = {
    "control_id",
    "severity",
    "result",
    "applicability",
    "reasons",
    "evidence",
    "run_issues",
}


def catalog_fingerprint(catalog: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON of the whole trusted catalog -- the
    "catalog integrity identifier" a bundle is bound to. Any change to
    catalog.json (severity, applicability, required_evidence, anything)
    changes this fingerprint, so a bundle generated against one catalog
    content can never silently validate against a different one."""
    canonical = json.dumps(catalog, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_control_ids(catalog: dict[str, Any]) -> list[str]:
    return sorted(c["id"] for c in catalog["controls"])


def _load_and_check_catalog(catalog: Any) -> dict[str, Any]:
    if not isinstance(catalog, dict):
        raise BundleError("catalog must be an object")
    errors = validate_catalog.validate(catalog)
    if errors:
        raise BundleError(f"trusted catalog is not structurally valid: {errors}")
    return catalog


def build_bundle(
    catalog: dict[str, Any],
    runs: list[Any],
    repository: str,
    base_sha: str,
    target_sha: str,
) -> dict[str, Any]:
    """Build the complete 75-control security bundle for one target.
    `catalog` must already be the TRUSTED (protected-base) catalog --
    this function does not know or care where it came from, only that the
    caller asserts it is the one to bind results against."""
    catalog = _load_and_check_catalog(catalog)
    controls = {c["id"]: c for c in catalog["controls"]}

    raw_results = {
        r["control_id"]: r
        for r in evidence_model.evaluate(runs, controls)
        if isinstance(r.get("control_id"), str)
    }

    results = []
    for control_id in canonical_control_ids(catalog):
        control = controls[control_id]
        raw = raw_results.get(control_id)
        if raw is None:
            raw = {
                "result": "UNPROVEN",
                "applicability": None,
                "reasons": ["no verification runs submitted for this control"],
                "evidence": [],
                "run_issues": [],
            }
        results.append(
            {
                "control_id": control_id,
                "severity": control["severity"],
                "result": raw["result"],
                "applicability": raw["applicability"],
                "reasons": raw["reasons"],
                "evidence": raw["evidence"],
                "run_issues": raw["run_issues"],
            }
        )

    return {
        "version": 1,
        "repository": repository,
        "base_sha": base_sha,
        "target_sha": target_sha,
        "catalog_version": catalog["catalog_version"],
        "catalog_sha256": catalog_fingerprint(catalog),
        "generated_count": len(runs),
        "results": results,
    }


def validate_bundle(
    bundle: Any,
    catalog: dict[str, Any],
    *,
    expected_repository: str,
    expected_base_sha: str,
    expected_target_sha: str,
) -> dict[str, Any]:
    """Fail-closed structural + identity + catalog-authority validation.
    `catalog` must be the TRUSTED (protected-base) catalog -- results are
    checked against ITS severities, never the bundle's own claims. Raises
    BundleError with a specific reason for the first problem found;
    returns the bundle unchanged (not a copy) on success."""
    catalog = _load_and_check_catalog(catalog)
    controls = {c["id"]: c for c in catalog["controls"]}
    expected_catalog_version = catalog["catalog_version"]
    expected_catalog_sha256 = catalog_fingerprint(catalog)

    if not isinstance(bundle, dict):
        raise BundleError("security bundle must be a JSON object")

    if set(bundle.keys()) != REQUIRED_BUNDLE_FIELDS:
        raise BundleError(
            f"security bundle top-level fields are invalid: got {sorted(bundle.keys())}, "
            f"expected exactly {sorted(REQUIRED_BUNDLE_FIELDS)}"
        )

    if bundle["version"] != 1:
        raise BundleError(f"unsupported security bundle version: {bundle['version']!r}")

    if not isinstance(bundle["repository"], str) or bundle["repository"] != expected_repository:
        raise BundleError(
            f"security bundle repository mismatch: expected {expected_repository!r}, "
            f"got {bundle.get('repository')!r}"
        )
    if not isinstance(bundle["base_sha"], str) or bundle["base_sha"] != expected_base_sha:
        raise BundleError(
            f"security bundle base_sha mismatch: expected {expected_base_sha!r}, "
            f"got {bundle.get('base_sha')!r}"
        )
    if not isinstance(bundle["target_sha"], str) or bundle["target_sha"] != expected_target_sha:
        raise BundleError(
            f"security bundle target_sha mismatch: expected {expected_target_sha!r}, "
            f"got {bundle.get('target_sha')!r} -- a bundle generated for one target head "
            f"cannot prove a different one"
        )

    if bundle["catalog_version"] != expected_catalog_version or bundle["catalog_sha256"] != expected_catalog_sha256:
        raise BundleError(
            "security bundle catalog mismatch: this bundle was generated against a different "
            "catalog than the trusted protected-base catalog.json -- fail closed rather than "
            "silently applying a mismatched policy"
        )

    if (
        not isinstance(bundle["generated_count"], int)
        or isinstance(bundle["generated_count"], bool)
        or bundle["generated_count"] < 0
    ):
        raise BundleError("security bundle generated_count must be a non-negative integer")

    results = bundle["results"]
    if not isinstance(results, list):
        raise BundleError("security bundle results must be a list")

    expected_ids = set(controls.keys())
    seen_ids: set[str] = set()
    for index, item in enumerate(results):
        if not isinstance(item, dict) or set(item.keys()) != REQUIRED_RESULT_FIELDS:
            raise BundleError(f"security bundle results[{index}] fields are invalid")

        control_id = item["control_id"]
        if not isinstance(control_id, str) or control_id not in expected_ids:
            raise BundleError(f"security bundle results[{index}] has an unknown control_id: {control_id!r}")
        if control_id in seen_ids:
            raise BundleError(f"security bundle has a duplicate control_id: {control_id!r}")
        seen_ids.add(control_id)

        if item["result"] not in ALLOWED_RESULT_STATES:
            raise BundleError(
                f"security bundle results for {control_id} has an invalid result state: {item['result']!r}"
            )
        if item["applicability"] not in ALLOWED_APPLICABILITY_STATES:
            raise BundleError(
                f"security bundle results for {control_id} has an invalid applicability state: "
                f"{item['applicability']!r}"
            )

        expected_severity = controls[control_id]["severity"]
        if item["severity"] != expected_severity:
            raise BundleError(
                f"security bundle results for {control_id} has an impossible severity mismatch: "
                f"trusted catalog says {expected_severity!r}, bundle claims {item['severity']!r}"
            )

        if not isinstance(item["reasons"], list) or any(not isinstance(r, str) for r in item["reasons"]):
            raise BundleError(f"security bundle results for {control_id}: reasons must be a list of strings")
        if not isinstance(item["evidence"], list):
            raise BundleError(f"security bundle results for {control_id}: evidence must be a list")
        if not isinstance(item["run_issues"], list):
            raise BundleError(f"security bundle results for {control_id}: run_issues must be a list")

    missing = expected_ids - seen_ids
    if missing:
        raise BundleError(f"security bundle is missing canonical control(s): {sorted(missing)}")

    return bundle


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        print(
            json.dumps(
                {
                    "version": 1,
                    "error": "usage: security_bundle.py <catalog.json> <runs.json> <repository> <base_sha> <target_sha>",
                },
                sort_keys=True,
            )
        )
        return 1

    catalog_path, runs_path, repository, base_sha, target_sha = argv[1:]
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
        with open(runs_path, "r", encoding="utf-8") as f:
            runs = json.load(f)
        if not isinstance(runs, list):
            raise BundleError("runs file must contain a JSON array")
        bundle = build_bundle(catalog, runs, repository, base_sha, target_sha)
    except (OSError, json.JSONDecodeError, BundleError) as exc:
        print(json.dumps({"version": 1, "error": str(exc)}, sort_keys=True))
        return 1

    print(json.dumps(bundle, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
