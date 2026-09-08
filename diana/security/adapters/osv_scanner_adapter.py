#!/usr/bin/env python3
"""Diana Security static adapter: osv-scanner (Security Phase 2, final
trust-boundary correction).

Normalizes a **verified scan-evidence artifact** (see `adapter_base`
module docstring) wrapping a saved/real osv-scanner JSON report into
evidence_model.py run records. Never installs, invokes, or bundles
osv-scanner -- this module only parses an already-produced artifact.

## The fail-open bug this correction fixes

The first implementation read vulnerabilities via
`raw.get("results", [])`, so an artifact with no `results` key at all
(e.g. `{}`) silently fell through to "zero vulnerabilities found" and was
treated as a clean, PASS-worthy scan. `_iter_vulnerabilities()` requires
`results` to actually be present (and a list); its absence is a
structural failure (`ERROR`), not silence.

## Target identity vs. scan coverage

A real CRITICAL/HIGH finding is trusted from partial manifest coverage --
finding one real vulnerable dependency doesn't require having scanned
every manifest in the repository -- but it must be attributed to the
correct TARGET IDENTITY (`adapter_base.verify_identity()`: same repository
and commit the caller expected). A finding from the wrong repository, the
wrong commit, or an artifact/expectation missing that identity entirely
is dropped, not counted as violating the *expected* target.

A clean result requires the stronger `adapter_base.verify_target()` check
(target identity plus every other expectation the caller supplied) *and*
`scanned_inputs` covering every manifest/lockfile the caller expects --
SEC-060's requirement is "no unresolved critical/high finding" across the
*relevant* manifests, which a clean result can only prove if those
manifests were actually part of the scan.

## Native vs. normalized report shape -- kept explicit, not blurred

This adapter parses a real osv-scanner-shaped report faithfully: nested
`results[].packages[].vulnerabilities[]`, each vulnerability requiring an
explicit `severity` string field (`CRITICAL`/`HIGH`/`MEDIUM`/`LOW`). A
vulnerability entry without a recognized `severity` string fails closed
to a structural error for the whole artifact, rather than silently being
excluded from the CRITICAL/HIGH check. If a real deployment's osv-scanner
output nests severity differently, a separate, explicit upstream
normalization step -- not this adapter -- is responsible for producing
the flat `severity` field this adapter requires.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_base  # noqa: E402

CAPABILITY = "DEPENDENCY_SCANNER"
TOOL_NAME = "osv-scanner"

SEC060_REQ_NO_CRITICAL_HIGH = (
    "dependency manifest/lockfile is scanned against a known-vulnerability "
    "database with no unresolved critical/high finding"
)

AUTHORIZED_EVIDENCE = {
    "SEC-060": [SEC060_REQ_NO_CRITICAL_HIGH],
}

VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
UNRESOLVED_SEVERITIES = {"CRITICAL", "HIGH"}


class ReportParseError(ValueError):
    """The wrapped report is not a valid, complete osv-scanner native
    report."""


def _iter_vulnerabilities(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ReportParseError("expected report to be a JSON object")
    if "results" not in raw:
        raise ReportParseError("report has no 'results' field -- not a completed osv-scanner report")
    results = raw["results"]
    if not isinstance(results, list):
        raise ReportParseError("results must be a list")

    vulns = []
    for result in results:
        if not isinstance(result, dict):
            raise ReportParseError("each result must be an object")
        if "packages" not in result:
            raise ReportParseError("each result must have a 'packages' field")
        packages = result["packages"]
        if not isinstance(packages, list):
            raise ReportParseError("packages must be a list")
        for pkg in packages:
            if not isinstance(pkg, dict):
                raise ReportParseError("each package must be an object")
            if "vulnerabilities" not in pkg:
                raise ReportParseError("each package must have a 'vulnerabilities' field")
            pkg_vulns = pkg["vulnerabilities"]
            if not isinstance(pkg_vulns, list):
                raise ReportParseError("vulnerabilities must be a list")
            for v in pkg_vulns:
                if not isinstance(v, dict):
                    raise ReportParseError("each vulnerability must be an object")
                severity = v.get("severity")
                if severity not in VALID_SEVERITIES:
                    raise ReportParseError(
                        f"vulnerability {v.get('id', '<unknown>')!r} has an unrecognized severity {severity!r} "
                        f"(expected one of {sorted(VALID_SEVERITIES)})"
                    )
                vulns.append({"package": pkg.get("package", {}), "vuln": v})
    return vulns


def ingest(
    artifact_path: str | None,
    control_ids: list[str],
    identity: str,
    expected_target: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    requested_authorized = [c for c in control_ids if c in AUTHORIZED_EVIDENCE]
    if not requested_authorized:
        return []

    if artifact_path is None or not Path(artifact_path).exists():
        return adapter_base.tool_unavailable_runs(
            requested_authorized, CAPABILITY, identity, "no artifact provided"
        )

    try:
        with open(artifact_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        envelope = adapter_base.load_envelope(raw)
        adapter_base.verify_tool_identity(envelope["tool"], TOOL_NAME)
        vulns = _iter_vulnerabilities(envelope["report"])
    except (OSError, json.JSONDecodeError, adapter_base.ArtifactError, ReportParseError) as exc:
        return adapter_base.tool_error_runs(
            requested_authorized, CAPABILITY, identity, f"could not verify osv-scanner artifact: {exc}"
        )

    identity_verified, _identity_reason = adapter_base.verify_identity(envelope["target"], expected_target)

    # "manifests" is not a target-identity field -- it's this adapter's
    # own coverage expectation, checked separately below against
    # scanned_inputs, not passed to the generic target comparison.
    expected_target_only = {k: v for k, v in (expected_target or {}).items() if k != "manifests"}
    target_verified, _target_reason = adapter_base.verify_target(envelope["target"], expected_target_only)

    expected_manifests = set((expected_target or {}).get("manifests", []))
    scanned_inputs = set(envelope["scanned_inputs"])
    manifests_covered = expected_manifests <= scanned_inputs if expected_manifests else False

    contributions = []
    if "SEC-060" in requested_authorized:
        unresolved = [v for v in vulns if v["vuln"]["severity"] in UNRESOLVED_SEVERITIES]
        if unresolved:
            if identity_verified:
                # Trusted from partial coverage, but only when attributed
                # to the correct repository + commit.
                names = sorted({v["package"].get("name", "unknown") for v in unresolved})
                contributions.append(
                    (
                        "SEC-060",
                        SEC060_REQ_NO_CRITICAL_HIGH,
                        "VIOLATED",
                        f"osv-scanner found {len(unresolved)} unresolved CRITICAL/HIGH finding(s) in: {', '.join(names)}",
                        None,
                    )
                )
            # else: finding(s) present, but target identity could not be
            # verified -- not attributed to the expected target.
        elif target_verified and manifests_covered:
            contributions.append(
                (
                    "SEC-060",
                    SEC060_REQ_NO_CRITICAL_HIGH,
                    "SATISFIED",
                    (
                        f"osv-scanner completed with no unresolved CRITICAL/HIGH finding; expected manifests "
                        f"{sorted(expected_manifests)} confirmed within scanned_inputs {sorted(scanned_inputs)}"
                    ),
                    None,
                )
            )
        # else: clean result, but target/manifest coverage could not be
        # verified as complete -- no contribution.

    try:
        return adapter_base.build_runs(contributions, AUTHORIZED_EVIDENCE, CAPABILITY, identity, requested_authorized)
    except adapter_base.NotAuthorized as exc:
        return adapter_base.tool_error_runs(requested_authorized, CAPABILITY, identity, str(exc))


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(
            json.dumps(
                {
                    "version": 1,
                    "error": "usage: osv_scanner_adapter.py <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]",
                },
                sort_keys=True,
            )
        )
        return 1

    artifact_arg, expected_arg = argv[1], argv[2]
    control_ids = argv[3:]
    artifact_path = None if artifact_arg == "-" else artifact_arg
    identity = f"osv-scanner::{artifact_arg}"

    expected_target = None
    if expected_arg != "-":
        with open(expected_arg, "r", encoding="utf-8") as f:
            expected_target = json.load(f)

    runs = ingest(artifact_path, control_ids, identity, expected_target)
    print(json.dumps({"version": 1, "runs": runs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
