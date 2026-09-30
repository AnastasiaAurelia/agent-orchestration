#!/usr/bin/env python3
"""Diana Security static adapter: Trivy (Security Phase 7.2).

Normalizes a **verified scan-evidence artifact** (see `adapter_base`
module docstring) wrapping a saved/real Trivy JSON report into
evidence_model.py run records. Never installs, invokes, or bundles
Trivy -- this module only parses an already-produced artifact, exactly
like `gitleaks_adapter.py`/`osv_scanner_adapter.py`.

## Scope: deliberately narrow, not "wrap the whole tool"

Trivy is capable of many scan modes (dependency vulnerabilities, OS
package vulnerabilities, container images, IaC/config misconfiguration,
secrets, licenses). This adapter is authorized for exactly ONE catalog
claim this round: `SEC-060` ("dependency manifest/lockfile is scanned
against a known-vulnerability database with no unresolved critical/high
finding"), via Trivy's filesystem/dependency scan mode
(`trivy fs`/`trivy repo`, `Class == "lang-pkgs"` results).

This is deliberately NOT wired to authorize:

- Gitleaks' secret-scanning claims (`SEC-007`/`SEC-006`/`SEC-065`) --
  Trivy's own secret-detection mode overlaps Gitleaks, but this round
  does not duplicate that authority; see the Phase 7 report for why.
- Any container-image claim -- no catalog control currently authorizes
  a "container image is free of known vulnerabilities" claim with an
  exact `required_evidence` string this adapter could honestly satisfy,
  and scanning an externally-supplied image would violate the "no
  arbitrary remote targets" invariant. A locally-built, CI-produced
  image could be added in a future round if a catalog control is
  extended to ask for it explicitly.
- Any IaC/misconfiguration claim -- same reasoning: no existing catalog
  `required_evidence` string names Trivy's `Class == "config"` results,
  so authorizing them here would mean inventing new evidence claims
  outside the frozen Phase 0 catalog contract, which this phase must not
  do.

`SEC-060` already permits an independent `DEPENDENCY_SCANNER` capability
contribution from `osv_scanner_adapter.py`; Trivy contributing the SAME
control via the SAME capability type is intentional -- two independently
maintained scanners each proving the identical, narrow claim
strengthens confidence without inventing new coverage. This does not
change `SEC-060`'s FULLY_COVERED status in the coverage matrix (it was
already `FULLY_COVERED` via osv-scanner); it only gives evidence_model.py
a second, independent trusted contributor for the same requirement.

## Native report shape

This adapter parses Trivy's native JSON report format:
`{"Results": [{"Target": "...", "Class": "lang-pkgs", "Type": "...",
"Vulnerabilities": [{"VulnerabilityID": "...", "PkgName": "...",
"Severity": "CRITICAL"|"HIGH"|"MEDIUM"|"LOW"|"UNKNOWN", ...}, ...]},
...]}`. Only `Class == "lang-pkgs"` results are inspected for `SEC-060`
-- a `Class == "config"`/`"secret"` result is present in the same report
but never treated as evidence for anything this adapter is authorized
for (defense in depth: even if a caller mistakenly fed a full/unscoped
report, this adapter cannot manufacture evidence outside its own
AUTHORIZED_EVIDENCE mapping, enforced again by `adapter_base.build_runs()`).
A report with no `Results` key at all (or a non-list value) is a
structural failure (`ERROR`), never treated as "zero vulnerabilities" --
the same fail-closed fix `osv_scanner_adapter.py` already applies.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_base  # noqa: E402

CAPABILITY = "DEPENDENCY_SCANNER"
TOOL_NAME = "trivy"

# The exact required_evidence string this adapter shares with
# osv_scanner_adapter.py -- SEC-060's catalog entry lists a single
# requirement, and DEPENDENCY_SCANNER is a capability, not a vendor, so
# multiple independently-maintained tools may each contribute a
# SATISFIED/VIOLATED for it (evidence_model.py aggregates all of them).
SEC060_REQ_NO_CRITICAL_HIGH = (
    "dependency manifest/lockfile is scanned against a known-vulnerability "
    "database with no unresolved critical/high finding"
)

AUTHORIZED_EVIDENCE = {
    "SEC-060": [SEC060_REQ_NO_CRITICAL_HIGH],
}

VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"}
UNRESOLVED_SEVERITIES = {"CRITICAL", "HIGH"}

# Only this Trivy result Class counts as dependency/lockfile evidence for
# SEC-060 -- "config" (IaC misconfiguration) and "secret" results in the
# same native report are never inspected here (see module docstring).
DEPENDENCY_RESULT_CLASS = "lang-pkgs"


class ReportParseError(ValueError):
    """The wrapped report is not a valid, complete Trivy native report."""


def _iter_dependency_vulnerabilities(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ReportParseError("expected report to be a JSON object")
    if "Results" not in raw:
        raise ReportParseError("report has no 'Results' field -- not a completed Trivy report")
    results = raw["Results"]
    if results is None:
        # Trivy legitimately prints a null Results for a target with zero
        # applicable targets found (e.g. no manifest files at all) -- this
        # is a genuinely clean, complete scan, not a structural failure.
        return []
    if not isinstance(results, list):
        raise ReportParseError("Results must be a list or null")

    vulns: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            raise ReportParseError("each result must be an object")
        result_class = result.get("Class")
        if result_class != DEPENDENCY_RESULT_CLASS:
            continue  # config/secret/os-pkgs results: not this adapter's authorized claim
        target = result.get("Target", "unknown-target")
        vulnerabilities = result.get("Vulnerabilities", [])
        if vulnerabilities is None:
            continue  # this target had zero findings; a real, valid outcome
        if not isinstance(vulnerabilities, list):
            raise ReportParseError(f"Vulnerabilities for target {target!r} must be a list or null")
        for v in vulnerabilities:
            if not isinstance(v, dict):
                raise ReportParseError("each vulnerability must be an object")
            severity = v.get("Severity")
            if severity not in VALID_SEVERITIES:
                raise ReportParseError(
                    f"vulnerability {v.get('VulnerabilityID', '<unknown>')!r} has an unrecognized "
                    f"severity {severity!r} (expected one of {sorted(VALID_SEVERITIES)})"
                )
            vulns.append({"target": target, "vuln": v})
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
        vulns = _iter_dependency_vulnerabilities(envelope["report"])
    except (OSError, json.JSONDecodeError, adapter_base.ArtifactError, ReportParseError) as exc:
        return adapter_base.tool_error_runs(
            requested_authorized, CAPABILITY, identity, f"could not verify Trivy artifact: {exc}"
        )

    identity_verified, _identity_reason = adapter_base.verify_identity(envelope["target"], expected_target)

    # "manifests" is not a target-identity field -- it's this adapter's
    # own coverage expectation, checked separately below against
    # scanned_inputs (mirrors osv_scanner_adapter.py exactly).
    expected_target_only = {k: v for k, v in (expected_target or {}).items() if k != "manifests"}
    target_verified, _target_reason = adapter_base.verify_target(envelope["target"], expected_target_only)

    expected_manifests = set((expected_target or {}).get("manifests", []))
    scanned_inputs = set(envelope["scanned_inputs"])
    manifests_covered = expected_manifests <= scanned_inputs if expected_manifests else False

    contributions = []
    if "SEC-060" in requested_authorized:
        unresolved = [v for v in vulns if v["vuln"]["Severity"] in UNRESOLVED_SEVERITIES]
        if unresolved:
            if identity_verified:
                ids = sorted({v["vuln"].get("VulnerabilityID", "unknown") for v in unresolved})
                contributions.append(
                    (
                        "SEC-060",
                        SEC060_REQ_NO_CRITICAL_HIGH,
                        "VIOLATED",
                        f"trivy found {len(unresolved)} unresolved CRITICAL/HIGH dependency finding(s): {', '.join(ids)}",
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
                        f"trivy dependency scan completed with no unresolved CRITICAL/HIGH finding; "
                        f"expected manifests {sorted(expected_manifests)} confirmed within "
                        f"scanned_inputs {sorted(scanned_inputs)}"
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
                    "error": "usage: trivy_adapter.py <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]",
                },
                sort_keys=True,
            )
        )
        return 1

    artifact_arg, expected_arg = argv[1], argv[2]
    control_ids = argv[3:]
    artifact_path = None if artifact_arg == "-" else artifact_arg
    identity = f"trivy::{artifact_arg}"

    expected_target = None
    if expected_arg != "-":
        with open(expected_arg, "r", encoding="utf-8") as f:
            expected_target = json.load(f)

    runs = ingest(artifact_path, control_ids, identity, expected_target)
    print(json.dumps({"version": 1, "runs": runs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
