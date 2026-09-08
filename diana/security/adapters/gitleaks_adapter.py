#!/usr/bin/env python3
"""Diana Security static adapter: Gitleaks (Security Phase 2).

Normalizes a saved/real Gitleaks JSON report into evidence_model.py run
records. Never installs, invokes, or bundles Gitleaks -- this module only
parses an already-produced report file.

## Authorization (integrity invariant)

Every Gitleaks finding is, by the tool's own design, a detected
credential-like literal in source -- Gitleaks has no other kind of
finding. That maps cleanly and completely to exactly ONE catalog
requirement: SEC-007's "no credential/token/private-key literal is
committed in source". This adapter is deliberately NOT authorized for
SEC-007's second requirement ("secrets are loaded from environment/
secret-manager configuration, not source") -- the absence of a detected
literal does not prove secrets are loaded from config; that needs SEC-007's
other permitted capability, STATIC_ANALYZER (SEC-007's verification.modes
is exactly [SECRET_SCANNER, STATIC_ANALYZER] -- SEMANTIC_REVIEW is not
permitted for this control), contributing separately. A Gitleaks-only
evidence set for SEC-007 is therefore UNPROVEN by evidence_model.py's own
aggregation, not PASS -- this is the expected, correct behavior, not a
bug: see test-adapters.sh CASE G1/G2.

This adapter is also deliberately NOT authorized for SEC-006 (frontend
secret exposure) or SEC-065 (cloud/service-role key exposure), even
though both are secret-adjacent: both controls' required_evidence makes a
claim about a specific SCOPE (a built frontend bundle; "only used
server-side") that a plain source-tree Gitleaks scan cannot establish on
its own without knowing what was actually scanned. Extending this
adapter's authorization to those controls is left to a future change that
threads a verified scan-target scope through the mapping -- not guessed
here.

## Gitleaks report shape (informal)

Gitleaks' JSON report is a flat array of finding objects (empty array `[]`
for a clean scan):

```jsonc
[
  {
    "Description": "AWS Access Key",
    "File": "config/settings.py",
    "RuleID": "aws-access-key-id",
    "Secret": "AKIA..."
  }
]
```

This adapter only checks that the top-level value is a JSON array whose
entries (if any) are objects -- it does not depend on any specific field
beyond that, since every finding is treated identically (see
"Authorization" above).
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
    """The given file is not a valid Gitleaks JSON report."""


def parse_report(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ReportParseError("expected a JSON array of findings")
    for entry in raw:
        if not isinstance(entry, dict):
            raise ReportParseError("expected each finding to be an object")
    return raw


def ingest(tool_output_path: str | None, control_ids: list[str], identity: str) -> list[dict[str, Any]]:
    """Returns evidence_model.py run records for the requested control_ids
    this adapter is authorized for. Controls this adapter is not
    authorized for are silently skipped (never fabricated evidence)."""
    requested_authorized = [c for c in control_ids if c in AUTHORIZED_EVIDENCE]
    if not requested_authorized:
        return []

    if tool_output_path is None or not Path(tool_output_path).exists():
        # Tool did not run -- emit nothing, letting evidence_model.py's
        # "no runs submitted" default (UNPROVEN) apply.
        return []

    try:
        with open(tool_output_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        findings = parse_report(raw)
    except (OSError, json.JSONDecodeError, ReportParseError) as exc:
        return adapter_base.tool_error_runs(
            requested_authorized, CAPABILITY, identity, f"could not parse Gitleaks report: {exc}"
        )

    contributions = []
    if "SEC-007" in requested_authorized:
        if findings:
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
        else:
            contributions.append(
                (
                    "SEC-007",
                    SEC007_REQ_NO_LITERAL,
                    "SATISFIED",
                    "gitleaks scan completed with zero findings",
                    None,
                )
            )

    return adapter_base.build_runs(contributions, AUTHORIZED_EVIDENCE, CAPABILITY, identity)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(json.dumps({"version": 1, "error": "usage: gitleaks_adapter.py <report.json|-> <control_id> [control_id...]"}, sort_keys=True))
        return 1

    report_arg = argv[1]
    control_ids = argv[2:]
    tool_output_path = None if report_arg == "-" else report_arg
    identity = f"gitleaks::{report_arg}"

    runs = ingest(tool_output_path, control_ids, identity)
    print(json.dumps({"version": 1, "runs": runs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
