#!/usr/bin/env python3
"""Diana Security static adapter: Gitleaks (Security Phase 2, final
trust-boundary correction).

Normalizes a **verified scan-evidence artifact** (see `adapter_base`
module docstring) wrapping a saved/real Gitleaks JSON report into
evidence_model.py run records. Never installs, invokes, or bundles
Gitleaks -- this module only parses an already-produced artifact.

## Target identity vs. scan coverage

A recognized finding is trusted from PARTIAL coverage (a scan of only
part of the repository can still legitimately find a real committed
secret), but it must be attributed to the correct TARGET IDENTITY -- the
same repository and commit the caller expected
(`adapter_base.verify_identity()`). A finding from the wrong repository,
the wrong commit, or an artifact/expectation missing that identity
entirely is dropped, not counted as violating the *expected* target.

A clean (zero-finding) result requires the stronger
`adapter_base.verify_target()` check: target identity *and* every other
expectation the caller supplied, plus `target.scope == "full-repo"`
(SEC-007's requirement is inherently repository-wide).

## Authorization (integrity invariant, unchanged)

Every Gitleaks finding is, by the tool's own design, a detected
credential-like literal in source -- Gitleaks has no other kind of
finding. That maps cleanly and completely to exactly ONE catalog
requirement: SEC-007's "no credential/token/private-key literal is
committed in source". This adapter is deliberately NOT authorized for
SEC-007's second requirement ("secrets are loaded from environment/
secret-manager configuration, not source") -- that needs SEC-007's other
permitted capability, STATIC_ANALYZER (SEC-007's verification.modes is
exactly [SECRET_SCANNER, STATIC_ANALYZER] -- SEMANTIC_REVIEW is not
permitted for this control), contributing separately.

This adapter is also deliberately NOT authorized for SEC-006 or SEC-065 --
both make a claim about a specific SCOPE this adapter's target model does
not yet represent.

## Artifact shape

See `adapter_base` for the envelope shape. This adapter requires
`tool.name == "gitleaks"`. `report` must be Gitleaks' native flat array of
finding objects (`[]` for a clean scan).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_base  # noqa: E402

CAPABILITY = "SECRET_SCANNER"
TOOL_NAME = "gitleaks"

SEC007_REQ_NO_LITERAL = "no credential/token/private-key literal is committed in source"

AUTHORIZED_EVIDENCE = {
    "SEC-007": [SEC007_REQ_NO_LITERAL],
}


class ReportParseError(ValueError):
    """The wrapped report is not a valid Gitleaks native report."""


def parse_report(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ReportParseError("expected report to be a JSON array of findings")
    for entry in raw:
        if not isinstance(entry, dict):
            raise ReportParseError("expected each finding to be an object")
    return raw


def ingest(
    artifact_path: str | None,
    control_ids: list[str],
    identity: str,
    expected_target: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Returns evidence_model.py run records for the requested control_ids
    this adapter is authorized for. Controls this adapter is not
    authorized for are silently skipped (never fabricated evidence)."""
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
        findings = parse_report(envelope["report"])
    except (OSError, json.JSONDecodeError, adapter_base.ArtifactError, ReportParseError) as exc:
        return adapter_base.tool_error_runs(
            requested_authorized, CAPABILITY, identity, f"could not verify Gitleaks artifact: {exc}"
        )

    identity_verified, _identity_reason = adapter_base.verify_identity(envelope["target"], expected_target)
    target_verified, _target_reason = adapter_base.verify_target(envelope["target"], expected_target)

    contributions = []
    if "SEC-007" in requested_authorized:
        if findings:
            if identity_verified:
                # Trusted from partial coverage, but only when attributed
                # to the correct repository + commit.
                for finding in findings:
                    rule = finding.get("RuleID", "unknown-rule")
                    path = finding.get("File", "unknown-file")
                    contributions.append(
                        (
                            "SEC-007",
                            SEC007_REQ_NO_LITERAL,
                            "VIOLATED",
                            f"gitleaks finding: rule={rule} file={path}",
                            None,
                        )
                    )
            # else: finding(s) present, but target identity could not be
            # verified against the caller's expectation -- not attributed
            # to the expected target, no contribution at all.
        elif target_verified and envelope["target"].get("scope") == "full-repo":
            contributions.append(
                (
                    "SEC-007",
                    SEC007_REQ_NO_LITERAL,
                    "SATISFIED",
                    (
                        "gitleaks scan completed with zero findings; target verified "
                        f"(repository={envelope['target'].get('repository')!r}, "
                        f"commit={envelope['target'].get('commit')!r}, scope=full-repo)"
                    ),
                    None,
                )
            )
        # else: clean result, but target/scope could not be verified as a
        # complete repository-wide scan -- no contribution at all.

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
                    "error": "usage: gitleaks_adapter.py <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]",
                },
                sort_keys=True,
            )
        )
        return 1

    artifact_arg, expected_arg = argv[1], argv[2]
    control_ids = argv[3:]
    artifact_path = None if artifact_arg == "-" else artifact_arg
    identity = f"gitleaks::{artifact_arg}"

    expected_target = None
    if expected_arg != "-":
        with open(expected_arg, "r", encoding="utf-8") as f:
            expected_target = json.load(f)

    runs = ingest(artifact_path, control_ids, identity, expected_target)
    print(json.dumps({"version": 1, "runs": runs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
