#!/usr/bin/env python3
"""Diana Security static adapter: Gitleaks (Security Phase 2, provenance-
corrected).

Normalizes a **verified scan-evidence artifact** (see
`adapter_base` module docstring) wrapping a saved/real Gitleaks JSON
report into evidence_model.py run records. Never installs, invokes, or
bundles Gitleaks -- this module only parses an already-produced artifact.

## A clean report is not proof of a clean repository

Human review of the first implementation found that a bare `[]` Gitleaks
report was treated as sufficient to emit `SATISFIED`, without proving the
scan actually covered the intended repository, commit, and full source
tree. An empty findings array from an empty directory, a stale commit, or
a narrow subdirectory looks identical to a clean scan of the real, current,
whole repository. This adapter now requires the artifact to declare its
target (repository, commit, scan root, scope), and the caller to supply
the *expected* target -- only when they match, and the declared `scope` is
`"full-repo"` (SEC-007's requirement is inherently repository-wide: "no
credential ... literal is committed in source", not "in the part we
happened to look at"), does an empty findings array count as evidence.

**A recognized finding is trusted even under partial/mismatched scope** --
finding one real committed secret does not require having scanned the
whole repository, and is `VIOLATED` regardless of target verification.

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
permitted for this control), contributing separately. A Gitleaks-only
evidence set for SEC-007 is therefore UNPROVEN by evidence_model.py's own
aggregation, not PASS -- this is the expected, correct behavior, not a
bug: see test-adapters.sh CASE G1/G2.

This adapter is also deliberately NOT authorized for SEC-006 (frontend
secret exposure) or SEC-065 (cloud/service-role key exposure) -- both
controls' required_evidence makes a claim about a specific SCOPE (a built
frontend bundle; "only used server-side") this adapter's target model
does not yet represent. Extending authorization to those controls is left
to a future, dedicated change.

## Artifact shape

See `adapter_base` for the envelope shape. `report` must be Gitleaks'
native flat array of finding objects (`[]` for a clean scan):

```jsonc
{"Description": "AWS Access Key", "File": "config/settings.py", "RuleID": "aws-access-key-id", "Secret": "AKIA..."}
```
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_base  # noqa: E402

CAPABILITY = "SECRET_SCANNER"

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
        # Tool did not run -- emit nothing, letting evidence_model.py's
        # "no runs submitted" default (UNPROVEN) apply.
        return []

    try:
        with open(artifact_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        envelope = adapter_base.load_envelope(raw)
        findings = parse_report(envelope["report"])
    except (OSError, json.JSONDecodeError, adapter_base.ArtifactError, ReportParseError) as exc:
        return adapter_base.tool_error_runs(
            requested_authorized, CAPABILITY, identity, f"could not verify Gitleaks artifact: {exc}"
        )

    target_verified, mismatch_reason = adapter_base.verify_target(envelope["target"], expected_target)

    contributions = []
    if "SEC-007" in requested_authorized:
        if findings:
            # Positive findings are trusted regardless of target verification.
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
        # complete repository-wide scan -- no contribution at all (stays
        # UNPROVEN, never silently PASS). mismatch_reason is intentionally
        # not surfaced as an error here; it just means "not proven."

    return adapter_base.build_runs(contributions, AUTHORIZED_EVIDENCE, CAPABILITY, identity)


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
