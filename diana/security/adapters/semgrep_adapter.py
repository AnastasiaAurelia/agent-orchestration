#!/usr/bin/env python3
"""Diana Security static adapter: Semgrep (Security Phase 2).

Normalizes a saved/real Semgrep JSON report into evidence_model.py run
records. Never installs, invokes, or bundles Semgrep -- this module only
parses an already-produced report file.

## Authorization (integrity invariant)

Unlike Gitleaks (every finding means the same thing) or osv-scanner
(every finding is a dependency vulnerability), Semgrep is a general
pattern-matching engine whose `check_id` can mean anything depending on
which rules were configured. This adapter therefore keys its
authorization on a small, explicit `RULE_MAP`: `{check_id: (control_id,
requirement)}`. A `check_id` not in this table produces NO contribution
at all -- it is silently dropped, never treated as satisfying or
violating anything (see test-adapters.sh CASE: unmapped rule).

**The exact `check_id` strings below are illustrative, not verified
against Semgrep's live public registry** (this environment makes no
network calls and does not run Semgrep). A real deployment MUST replace
or extend this table with the exact rule IDs from its actual configured
ruleset before results carry meaning for those specific rules -- an
unrecognized `check_id` correctly stays unmapped (UNPROVEN, never PASS)
by design, so an incomplete or wrong table fails closed rather than
overclaiming.

Mapped controls, both fully satisfiable by STATIC_ANALYZER alone
(dynamic_required=False, human_judgment_required=False, so a clean,
rule-covered Semgrep scan is sufficient on its own to PASS them):

- `SEC-055` Weak Cryptography / Custom Crypto -- "cryptographic operations
  use vetted standard-library/well-known algorithms and libraries, not a
  custom-designed cipher/scheme."
- `SEC-056` Insecure Randomness -- "security-relevant random values are
  generated with a cryptographically secure random source, not a
  general-purpose PRNG."

## Clean result meaningfulness (the other integrity requirement)

"Tool silence may only satisfy an evidence requirement when the adapter's
documented semantics prove that a clean result is meaningful for that
specific requirement." A Semgrep report only proves something about a
control if the rule(s) mapped to that control actually ran. This adapter
therefore requires the report to carry a `rules_run` array (the check_ids
Semgrep actually executed in that scan). For a given mapped control, this
adapter only emits `SATISFIED` (on zero matching findings) when at least
one rule mapped to that control appears in `rules_run`; if no mapped rule
ran at all, that control is left with no contribution from this adapter
(stays `UNPROVEN`, not silently passed).

## Semgrep report shape (informal)

```jsonc
{
  "results": [
    {"check_id": "python.lang.security.insecure-random", "path": "app/tokens.py", "start": {"line": 12}}
  ],
  "rules_run": ["python.lang.security.insecure-random", "python.lang.security.weak-crypto-cipher"]
}
```
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_base  # noqa: E402

CAPABILITY = "STATIC_ANALYZER"

SEC055_REQ = "cryptographic operations use vetted standard-library/well-known algorithms and libraries, not a custom-designed cipher/scheme"
SEC056_REQ = "security-relevant random values are generated with a cryptographically secure random source, not a general-purpose PRNG"

AUTHORIZED_EVIDENCE = {
    "SEC-055": [SEC055_REQ],
    "SEC-056": [SEC056_REQ],
}

# Illustrative starter mapping -- see module docstring. check_id -> (control_id, requirement).
RULE_MAP: dict[str, tuple[str, str]] = {
    "python.lang.security.insecure-random": ("SEC-056", SEC056_REQ),
    "javascript.lang.security.insecure-random": ("SEC-056", SEC056_REQ),
    "python.lang.security.weak-crypto-cipher": ("SEC-055", SEC055_REQ),
    "javascript.lang.security.weak-crypto-cipher": ("SEC-055", SEC055_REQ),
    "generic.crypto.security.custom-crypto-implementation": ("SEC-055", SEC055_REQ),
}


class ReportParseError(ValueError):
    """The given file is not a valid Semgrep JSON report."""


def _parse_report(raw: Any) -> tuple[list[dict[str, Any]], set[str]]:
    if not isinstance(raw, dict):
        raise ReportParseError("expected a JSON object")
    results = raw.get("results")
    if not isinstance(results, list):
        raise ReportParseError("results must be a list")
    for entry in results:
        if not isinstance(entry, dict) or not isinstance(entry.get("check_id"), str):
            raise ReportParseError("each result must be an object with a string check_id")
    rules_run = raw.get("rules_run")
    if not isinstance(rules_run, list) or not all(isinstance(r, str) for r in rules_run):
        raise ReportParseError("rules_run must be a list of strings")
    return results, set(rules_run)


def ingest(tool_output_path: str | None, control_ids: list[str], identity: str) -> list[dict[str, Any]]:
    requested_authorized = [c for c in control_ids if c in AUTHORIZED_EVIDENCE]
    if not requested_authorized:
        return []

    if tool_output_path is None or not Path(tool_output_path).exists():
        return []

    try:
        with open(tool_output_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        results, rules_run = _parse_report(raw)
    except (OSError, json.JSONDecodeError, ReportParseError) as exc:
        return adapter_base.tool_error_runs(
            requested_authorized, CAPABILITY, identity, f"could not parse Semgrep report: {exc}"
        )

    # Findings: for each mapped rule that actually fired, that's a VIOLATED
    # contribution for its mapped control/requirement (unmapped check_ids
    # are silently dropped -- never contribute anything).
    violated_pairs: set[tuple[str, str]] = set()
    contributions: list[tuple[str, str, str, str, str | None]] = []
    for entry in results:
        mapped = RULE_MAP.get(entry["check_id"])
        if mapped is None:
            continue
        control_id, requirement = mapped
        if control_id not in requested_authorized:
            continue
        if (control_id, requirement) in violated_pairs:
            continue
        violated_pairs.add((control_id, requirement))
        path = entry.get("path", "unknown-file")
        contributions.append(
            (
                control_id,
                requirement,
                "VIOLATED",
                f"semgrep finding: rule={entry['check_id']} file={path}",
                None,
            )
        )

    # Clean-result meaningfulness: only claim SATISFIED for a control if a
    # rule mapped to it actually ran, and no finding for that control was
    # already recorded above.
    for control_id in requested_authorized:
        if (control_id, AUTHORIZED_EVIDENCE[control_id][0]) in violated_pairs:
            continue
        requirement = AUTHORIZED_EVIDENCE[control_id][0]
        mapped_rules_for_control = {rid for rid, (cid, _req) in RULE_MAP.items() if cid == control_id}
        if mapped_rules_for_control & rules_run:
            contributions.append(
                (
                    control_id,
                    requirement,
                    "SATISFIED",
                    f"semgrep ran {sorted(mapped_rules_for_control & rules_run)} with zero findings for {control_id}",
                    None,
                )
            )
        # else: no mapped rule ran -- no contribution, stays UNPROVEN.

    return adapter_base.build_runs(contributions, AUTHORIZED_EVIDENCE, CAPABILITY, identity)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(json.dumps({"version": 1, "error": "usage: semgrep_adapter.py <report.json|-> <control_id> [control_id...]"}, sort_keys=True))
        return 1

    report_arg = argv[1]
    control_ids = argv[2:]
    tool_output_path = None if report_arg == "-" else report_arg
    identity = f"semgrep::{report_arg}"

    runs = ingest(tool_output_path, control_ids, identity)
    print(json.dumps({"version": 1, "runs": runs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
