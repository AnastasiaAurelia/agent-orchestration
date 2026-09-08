#!/usr/bin/env python3
"""Diana Security static adapter: Semgrep (Security Phase 2, provenance-
corrected).

Normalizes a **verified scan-evidence artifact** (see `adapter_base`
module docstring) wrapping a saved/real Semgrep JSON report into
evidence_model.py run records. Never installs, invokes, or bundles
Semgrep -- this module only parses an already-produced artifact.

## The illustrative-mapping bug this correction fixes

The first implementation hardcoded a `RULE_MAP` of illustrative example
Semgrep `check_id` strings directly into this module's production
`ingest()` path. Those rule IDs were never verified against a real
Semgrep registry or a real deployment's actual configured ruleset --
using them to authorize a real `PASS` would let an unverified guess
manufacture evidence. **`ingest()` now never consults a hardcoded rule
table.** The only rule mapping it will ever use is
`artifact["config"]["rule_map"]` -- part of the caller-constructed,
trusted envelope (see `adapter_base`), not this module's own guesswork.
If an artifact carries no `rule_map` (the default for any artifact a
caller didn't deliberately populate), no `check_id` is ever authorized,
and every mapped control this adapter is asked about stays `UNPROVEN`.

`ILLUSTRATIVE_TEST_ONLY_RULE_MAP` below still exists, but strictly as a
labeled example a *test* can choose to place into an artifact's
`config.rule_map` when it wants to exercise the "verified mapping"
pathway (see test-adapters.sh CASE S1/S2/P11). Production code never
reads it.

## Clean result meaningfulness (unchanged principle, now scope-aware too)

A Semgrep report only proves something about a control if (a) a rule
mapped to that control actually ran, per the report's `rules_run` list,
**and** (b) the artifact's declared scan target/scope matches what the
caller expected (see `adapter_base.verify_target()`) -- a rule running
against the wrong commit or a narrow subtree proves nothing about the
requirement's intended scope. Both must hold before a zero-finding result
for that control counts as `SATISFIED`. **A real finding from a mapped,
verified rule counts as `VIOLATED` regardless of scope** -- the
positive/negative evidence asymmetry applies here too.

## Mapped controls

Both fully satisfiable by STATIC_ANALYZER alone (dynamic_required=False,
human_judgment_required=False):

- `SEC-055` Weak Cryptography / Custom Crypto
- `SEC-056` Insecure Randomness
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

# TEST-ONLY. Never read by ingest(). Illustrative example rule-ID strings,
# NOT verified against Semgrep's live public registry (this environment
# makes no network calls and does not run Semgrep). A real deployment
# must supply its own verified mapping via artifact["config"]["rule_map"];
# this constant exists only so tests can construct a realistic-looking
# verified artifact without inventing a mapping ad hoc per test case.
ILLUSTRATIVE_TEST_ONLY_RULE_MAP: dict[str, list[str]] = {
    "python.lang.security.insecure-random": ["SEC-056", SEC056_REQ],
    "javascript.lang.security.insecure-random": ["SEC-056", SEC056_REQ],
    "python.lang.security.weak-crypto-cipher": ["SEC-055", SEC055_REQ],
    "javascript.lang.security.weak-crypto-cipher": ["SEC-055", SEC055_REQ],
    "generic.crypto.security.custom-crypto-implementation": ["SEC-055", SEC055_REQ],
}


class ReportParseError(ValueError):
    """The wrapped report is not a valid Semgrep native report."""


def _parse_report(raw: Any) -> tuple[list[dict[str, Any]], set[str]]:
    if not isinstance(raw, dict):
        raise ReportParseError("expected report to be a JSON object")
    if "results" not in raw:
        raise ReportParseError("report has no 'results' field")
    results = raw["results"]
    if not isinstance(results, list):
        raise ReportParseError("results must be a list")
    for entry in results:
        if not isinstance(entry, dict) or not isinstance(entry.get("check_id"), str):
            raise ReportParseError("each result must be an object with a string check_id")
    if "rules_run" not in raw:
        raise ReportParseError("report has no 'rules_run' field")
    rules_run = raw["rules_run"]
    if not isinstance(rules_run, list) or not all(isinstance(r, str) for r in rules_run):
        raise ReportParseError("rules_run must be a list of strings")
    return results, set(rules_run)


def _parse_rule_map(config: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Extracts a caller-verified rule map from the artifact's trusted
    config block. Absent entirely -> empty map (nothing authorized).
    A malformed rule_map (wrong shape) is a structural artifact problem."""
    raw_map = config.get("rule_map", {})
    if not isinstance(raw_map, dict):
        raise adapter_base.ArtifactError("artifact.config.rule_map must be an object if present")
    parsed: dict[str, tuple[str, str]] = {}
    for check_id, pair in raw_map.items():
        if (
            not isinstance(check_id, str)
            or not isinstance(pair, list)
            or len(pair) != 2
            or not all(isinstance(x, str) for x in pair)
        ):
            raise adapter_base.ArtifactError(
                f"artifact.config.rule_map entry for {check_id!r} must be a 2-element list of strings [control_id, requirement]"
            )
        parsed[check_id] = (pair[0], pair[1])
    return parsed


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
        return []

    try:
        with open(artifact_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        envelope = adapter_base.load_envelope(raw)
        results, rules_run = _parse_report(envelope["report"])
        rule_map = _parse_rule_map(envelope["config"])
    except (OSError, json.JSONDecodeError, adapter_base.ArtifactError, ReportParseError) as exc:
        return adapter_base.tool_error_runs(
            requested_authorized, CAPABILITY, identity, f"could not verify Semgrep artifact: {exc}"
        )

    target_verified, _mismatch_reason = adapter_base.verify_target(envelope["target"], expected_target)

    # Findings: for each mapped rule (per the artifact's OWN verified
    # rule_map, never the illustrative constant) that actually fired,
    # that's a VIOLATED contribution -- trusted regardless of target
    # verification. check_ids not in rule_map are silently dropped.
    violated_pairs: set[tuple[str, str]] = set()
    contributions: list[tuple[str, str, str, str, str | None]] = []
    for entry in results:
        mapped = rule_map.get(entry["check_id"])
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

    # Clean-result meaningfulness: only claim SATISFIED for a control if
    # (a) a rule_map-mapped rule for it actually ran, AND (b) the declared
    # target/scope was verified against what the caller expected.
    for control_id in requested_authorized:
        requirement = AUTHORIZED_EVIDENCE[control_id][0]
        if (control_id, requirement) in violated_pairs:
            continue
        mapped_rules_for_control = {rid for rid, (cid, _req) in rule_map.items() if cid == control_id}
        rule_ran = bool(mapped_rules_for_control & rules_run)
        if rule_ran and target_verified:
            contributions.append(
                (
                    control_id,
                    requirement,
                    "SATISFIED",
                    (
                        f"semgrep ran {sorted(mapped_rules_for_control & rules_run)} with zero findings for "
                        f"{control_id}; target verified against caller-supplied expectation"
                    ),
                    None,
                )
            )
        # else: either the mapped rule never ran, or the scan target/scope
        # couldn't be verified -- no contribution (stays UNPROVEN).

    return adapter_base.build_runs(contributions, AUTHORIZED_EVIDENCE, CAPABILITY, identity)


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(
            json.dumps(
                {
                    "version": 1,
                    "error": "usage: semgrep_adapter.py <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]",
                },
                sort_keys=True,
            )
        )
        return 1

    artifact_arg, expected_arg = argv[1], argv[2]
    control_ids = argv[3:]
    artifact_path = None if artifact_arg == "-" else artifact_arg
    identity = f"semgrep::{artifact_arg}"

    expected_target = None
    if expected_arg != "-":
        with open(expected_arg, "r", encoding="utf-8") as f:
            expected_target = json.load(f)

    runs = ingest(artifact_path, control_ids, identity, expected_target)
    print(json.dumps({"version": 1, "runs": runs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
