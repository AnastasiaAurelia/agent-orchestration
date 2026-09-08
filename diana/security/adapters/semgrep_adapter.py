#!/usr/bin/env python3
"""Diana Security static adapter: Semgrep (Security Phase 2, final
trust-boundary correction).

Normalizes a **verified scan-evidence artifact** (see `adapter_base`
module docstring) wrapping a saved/real Semgrep JSON report into
evidence_model.py run records. Never installs, invokes, or bundles
Semgrep -- this module only parses an already-produced artifact.

## Rule-map authorization, not just shape validation

The only rule mapping `ingest()` will ever use is
`artifact["config"]["rule_map"]` -- part of the caller-constructed,
trusted envelope, never the module's own `ILLUSTRATIVE_TEST_ONLY_RULE_MAP`
(read only by tests, never by production code). Beyond checking the
mapping's *shape*, every entry is validated against this adapter's own
`AUTHORIZED_EVIDENCE`: the control_id must be one this adapter is
authorized for, and the requirement string must exactly equal the
authorized requirement for that control. A rule_map entry naming an
unknown control, an unauthorized control, or the wrong requirement text
is rejected as an artifact integrity problem (`ArtifactError` -> `ERROR`
for the whole artifact) -- a caller cannot smuggle authorization for a
control this adapter was never designed to prove by writing it into the
artifact's own config block. A malformed `check_id` (not a non-empty
string) is rejected the same way.

## Target identity vs. scan coverage

A recognized finding from a mapped, verified rule is trusted from partial
scope, but only when attributed to the correct TARGET IDENTITY
(`adapter_base.verify_identity()`: same repository and commit the caller
expected). A clean result additionally requires the mapped rule to have
actually run (`rules_run`) *and* the stronger `adapter_base.verify_target()`
check (identity plus scope) before it can count as `SATISFIED`.

## Mapped controls

Fully satisfiable by STATIC_ANALYZER alone (dynamic_required=False,
human_judgment_required=False):

- `SEC-055` Weak Cryptography / Custom Crypto
- `SEC-056` Insecure Randomness

Security Track remediation round A additions -- each of these controls
also has `dynamic_required=True`, so this adapter covers only ONE of the
two `required_evidence` items; a companion `DYNAMIC_API` contribution
(see `diana/security/dynamic/scenarios.py`) is still required to reach
PASS, exactly the same composition discipline as every other
`dynamic_required` control in this catalog:

- `SEC-010` OS Command Injection -- the static ("no shell command built
  via string concatenation of request-influenced input") half only.
- `SEC-011` Server-Side Template Injection -- the static ("request-
  influenced input never rendered as template syntax") half only.
- `SEC-021` JWT Signature Verification Errors -- the static ("fixed,
  expected signing algorithm enforced") half only.
- `SEC-058` Insecure Deserialization -- the static ("safe/restricted
  deserialization format, not an unrestricted native deserializer") half
  only.

`SEC-035` (Server-Side Request Forgery) was deliberately NOT added here:
its static required_evidence item ("outbound request targets are
validated/allow-listed and internal/metadata address ranges are
blocked") is a claim about actual network-egress *behavior*, not a
lexical/structural code pattern -- a static pattern match can flag "a
URL is built from request input near an HTTP call," but cannot establish
that destinations are genuinely allow-listed and that private/link-local/
loopback ranges are blocked after DNS resolution (redirect-following,
DNS rebinding, and allow-list completeness are runtime properties). Only
`SEC-035`'s dynamic negative-test item is covered (by a new scenario);
its static item remains an explicit, documented gap -- see
`diana/security/README.md`'s "Security Track remediation round A"
section.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_base  # noqa: E402

CAPABILITY = "STATIC_ANALYZER"
TOOL_NAME = "semgrep"

SEC055_REQ = "cryptographic operations use vetted standard-library/well-known algorithms and libraries, not a custom-designed cipher/scheme"
SEC056_REQ = "security-relevant random values are generated with a cryptographically secure random source, not a general-purpose PRNG"
SEC010_REQ = "no shell command is built via string concatenation/interpolation of request-influenced input"
SEC011_REQ = "request-influenced input is never rendered as template syntax (only as template data)"
SEC021_REQ = "JWT verification enforces a fixed, expected signing algorithm (no algorithm confusion, no 'none' algorithm accepted)"
SEC058_REQ = "deserialization of untrusted input uses a safe/restricted format or schema, not an unrestricted native object deserializer"

AUTHORIZED_EVIDENCE = {
    "SEC-055": [SEC055_REQ],
    "SEC-056": [SEC056_REQ],
    "SEC-010": [SEC010_REQ],
    "SEC-011": [SEC011_REQ],
    "SEC-021": [SEC021_REQ],
    "SEC-058": [SEC058_REQ],
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
    "diana.shell-injection-via-concatenation": ["SEC-010", SEC010_REQ],
    "diana.template-injection-via-render": ["SEC-011", SEC011_REQ],
    "diana.jwt-unsafe-verification": ["SEC-021", SEC021_REQ],
    "diana.insecure-deserialization": ["SEC-058", SEC058_REQ],
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
        if not isinstance(entry, dict) or not isinstance(entry.get("check_id"), str) or not entry.get("check_id", "").strip():
            raise ReportParseError("each result must be an object with a non-empty string check_id")
    if "rules_run" not in raw:
        raise ReportParseError("report has no 'rules_run' field")
    rules_run = raw["rules_run"]
    if not isinstance(rules_run, list) or not all(isinstance(r, str) for r in rules_run):
        raise ReportParseError("rules_run must be a list of strings")
    return results, set(rules_run)


def _parse_rule_map(config: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Extracts a caller-verified rule map from the artifact's trusted
    config block, and validates every entry against AUTHORIZED_EVIDENCE --
    a rule_map cannot authorize a control/requirement this adapter isn't
    itself capable of proving. Absent config.rule_map entirely -> empty
    map (nothing authorized). Any malformed shape or unauthorized
    control/requirement is an ArtifactError (-> ERROR for the artifact)."""
    raw_map = config.get("rule_map", {})
    if not isinstance(raw_map, dict):
        raise adapter_base.ArtifactError("artifact.config.rule_map must be an object if present")

    parsed: dict[str, tuple[str, str]] = {}
    for check_id, pair in raw_map.items():
        if not isinstance(check_id, str) or not check_id.strip():
            raise adapter_base.ArtifactError(f"artifact.config.rule_map has a malformed check_id: {check_id!r}")
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or not all(isinstance(x, str) for x in pair)
        ):
            raise adapter_base.ArtifactError(
                f"artifact.config.rule_map entry for {check_id!r} must be a 2-element list of strings [control_id, requirement]"
            )
        control_id, requirement = pair[0], pair[1]
        if control_id not in AUTHORIZED_EVIDENCE:
            raise adapter_base.ArtifactError(
                f"artifact.config.rule_map entry for {check_id!r} names {control_id!r}, "
                f"which this adapter is not authorized for"
            )
        if requirement not in AUTHORIZED_EVIDENCE[control_id]:
            raise adapter_base.ArtifactError(
                f"artifact.config.rule_map entry for {check_id!r} names an unauthorized requirement for "
                f"{control_id!r}: {requirement!r}"
            )
        parsed[check_id] = (control_id, requirement)
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
        return adapter_base.tool_unavailable_runs(
            requested_authorized, CAPABILITY, identity, "no artifact provided"
        )

    try:
        with open(artifact_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        envelope = adapter_base.load_envelope(raw)
        adapter_base.verify_tool_identity(envelope["tool"], TOOL_NAME)
        results, rules_run = _parse_report(envelope["report"])
        rule_map = _parse_rule_map(envelope["config"])
    except (OSError, json.JSONDecodeError, adapter_base.ArtifactError, ReportParseError) as exc:
        return adapter_base.tool_error_runs(
            requested_authorized, CAPABILITY, identity, f"could not verify Semgrep artifact: {exc}"
        )

    identity_verified, _identity_reason = adapter_base.verify_identity(envelope["target"], expected_target)
    target_verified, _target_reason = adapter_base.verify_target(envelope["target"], expected_target)

    # Findings: for each mapped rule (per the artifact's OWN verified,
    # AUTHORIZED_EVIDENCE-checked rule_map) that actually fired, that's a
    # VIOLATED contribution -- trusted from partial scope, but only when
    # attributed to the correct target identity. check_ids not in
    # rule_map are silently dropped.
    violated_pairs: set[tuple[str, str]] = set()
    contributions: list[tuple[str, str, str, str, str | None]] = []
    if identity_verified:
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
    # else: any findings present are not attributed to the expected
    # target at all -- no contribution for them.

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
        # couldn't be verified -- no contribution.

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
