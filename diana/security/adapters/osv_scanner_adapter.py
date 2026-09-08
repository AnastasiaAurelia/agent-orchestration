#!/usr/bin/env python3
"""Diana Security static adapter: osv-scanner (Security Phase 2).

Normalizes a saved/real osv-scanner JSON report into evidence_model.py run
records. Never installs, invokes, or bundles osv-scanner -- this module
only parses an already-produced report file.

## Authorization (integrity invariant)

This adapter is authorized for exactly one requirement: SEC-060's
"dependency manifest/lockfile is scanned against a known-vulnerability
database with no unresolved critical/high finding" -- this is SEC-060's
only required_evidence item and its verification.modes is exactly
[DEPENDENCY_SCANNER], so a clean, successfully-completed scan is
sufficient on its own to PASS this control (no other capability is
needed). Note the exact wording match: the requirement says "no
unresolved critical/high finding", not "zero findings" -- a scan that
finds only LOW/MEDIUM-severity vulnerabilities still SATISFIES this
requirement, matching the catalog text precisely rather than being more
conservative than the control actually asks for.

This adapter is NOT authorized for SEC-061 (Malicious or Compromised
Dependency) or SEC-062 (Dependency Confusion), even though both also list
DEPENDENCY_SCANNER among their permitted modes: osv-scanner's known-
vulnerability database lookup does not establish "provenance/integrity"
(SEC-061) or "internal package names are reserved on a private registry"
(SEC-062) -- those need a different kind of dependency-scanning evidence
this adapter does not produce. Extending coverage to those controls is
left to a future, dedicated adapter that actually implements that check,
not guessed here.

## osv-scanner report shape (informal)

osv-scanner's JSON output nests vulnerabilities under `results[].packages[]
.vulnerabilities[]`, each vulnerability carrying a `severity` list (each
entry `{"type": ..., "score": ...}`) or, more simply, an OSV-format
`database_specific.severity` string in many real-world exports. To stay
robust without depending on either exact shape, this adapter accepts a
`severity` string field per vulnerability entry (`CRITICAL` / `HIGH` /
`MEDIUM` / `LOW`) -- a real ingestion pipeline is expected to normalize
osv-scanner's native nested severity representation to this flat field
before handing the report to this adapter; that normalization step is
intentionally out of scope here (this adapter does not reimplement a
vulnerability database or CVSS scoring, only asks whether a "critical" or
"high" severity is present):

```jsonc
{
  "results": [
    {
      "source": {"path": "requirements.txt"},
      "packages": [
        {
          "package": {"name": "example-pkg", "version": "1.0.0"},
          "vulnerabilities": [
            {"id": "GHSA-xxxx", "severity": "HIGH"}
          ]
        }
      ]
    }
  ]
}
```

A clean scan is `{"results": []}` (or `"results"` absent entirely).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_base  # noqa: E402

CAPABILITY = "DEPENDENCY_SCANNER"

SEC060_REQ_NO_CRITICAL_HIGH = (
    "dependency manifest/lockfile is scanned against a known-vulnerability "
    "database with no unresolved critical/high finding"
)

AUTHORIZED_EVIDENCE = {
    "SEC-060": [SEC060_REQ_NO_CRITICAL_HIGH],
}

UNRESOLVED_SEVERITIES = {"CRITICAL", "HIGH"}


class ReportParseError(ValueError):
    """The given file is not a valid osv-scanner JSON report."""


def _iter_vulnerabilities(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ReportParseError("expected a JSON object")
    results = raw.get("results", [])
    if not isinstance(results, list):
        raise ReportParseError("results must be a list")

    vulns = []
    for result in results:
        if not isinstance(result, dict):
            raise ReportParseError("each result must be an object")
        packages = result.get("packages", [])
        if not isinstance(packages, list):
            raise ReportParseError("packages must be a list")
        for pkg in packages:
            if not isinstance(pkg, dict):
                raise ReportParseError("each package must be an object")
            pkg_vulns = pkg.get("vulnerabilities", [])
            if not isinstance(pkg_vulns, list):
                raise ReportParseError("vulnerabilities must be a list")
            for v in pkg_vulns:
                if not isinstance(v, dict):
                    raise ReportParseError("each vulnerability must be an object")
                vulns.append({"package": pkg.get("package", {}), "vuln": v})
    return vulns


def ingest(tool_output_path: str | None, control_ids: list[str], identity: str) -> list[dict[str, Any]]:
    requested_authorized = [c for c in control_ids if c in AUTHORIZED_EVIDENCE]
    if not requested_authorized:
        return []

    if tool_output_path is None or not Path(tool_output_path).exists():
        return []

    try:
        with open(tool_output_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        vulns = _iter_vulnerabilities(raw)
    except (OSError, json.JSONDecodeError, ReportParseError) as exc:
        return adapter_base.tool_error_runs(
            requested_authorized, CAPABILITY, identity, f"could not parse osv-scanner report: {exc}"
        )

    contributions = []
    if "SEC-060" in requested_authorized:
        unresolved = [v for v in vulns if v["vuln"].get("severity") in UNRESOLVED_SEVERITIES]
        if unresolved:
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
        else:
            contributions.append(
                (
                    "SEC-060",
                    SEC060_REQ_NO_CRITICAL_HIGH,
                    "SATISFIED",
                    f"osv-scanner completed with no unresolved CRITICAL/HIGH finding ({len(vulns)} lower-severity finding(s) present)"
                    if vulns
                    else "osv-scanner completed with zero findings",
                    None,
                )
            )

    return adapter_base.build_runs(contributions, AUTHORIZED_EVIDENCE, CAPABILITY, identity)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(json.dumps({"version": 1, "error": "usage: osv_scanner_adapter.py <report.json|-> <control_id> [control_id...]"}, sort_keys=True))
        return 1

    report_arg = argv[1]
    control_ids = argv[2:]
    tool_output_path = None if report_arg == "-" else report_arg
    identity = f"osv-scanner::{report_arg}"

    runs = ingest(tool_output_path, control_ids, identity)
    print(json.dumps({"version": 1, "runs": runs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
