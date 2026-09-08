#!/usr/bin/env python3
"""Deterministic Diana merge-boundary gate."""

from __future__ import annotations

import json
import sys
from pathlib import PurePosixPath
from typing import Any

EXIT = {"PASS": 0, "FAIL": 1, "REQUIRE_HUMAN": 2}
RISKS = {"SAFE", "CONSEQUENTIAL", "DANGEROUS", "HUMAN_ONLY"}
PREFLIGHT_RESULTS = {"PASS", "FAIL", "SKIP"}
HUMAN_ONLY_CONDITIONS = {
    "production_deploy",
    "destructive_database_operation",
    "production_data_deletion",
    "credential_change",
    "payment_billing_infrastructure",
    "security_control_change",
    "destructive_shared_git",
    "external_publication",
    "consequential_outbound_communication",
    "branch_ci_protection_change",
}

# Exact repository paths/prefixes that deserve human review. Substring matching
# is deliberately avoided so nearby fixture/test names do not false-positive.
#
# Security Phase 5 addition: the Security Track's own enforcement surface
# (catalog, evidence model, bundle/reducer policy, and the Gate/CI wiring
# that runs them) is deterministically sensitive -- a change to any of
# these paths always requires human review, regardless of the PR author's
# self-declared diff.risk or human_only_conditions (Security Phase 5,
# section 10: "do not rely only on a PR author's self-declared risk=SAFE").
REVIEW_PATHS = {
    ".github/CODEOWNERS",
    ".github/dependabot.yml",
    "diana/security/catalog.json",
    "diana/security/evidence_model.py",
    "diana/security/security_bundle.py",
    "diana/security/security_reducer.py",
    "diana/security/ci_verifier_runs.py",
    "diana/gate/diana-gate.py",
    "diana/ci/build-gate-input.py",
    "diana/ci/run-security-gate.py",
    "diana/ci/map-gate-result.py",
}
REVIEW_PREFIXES = (
    ".github/workflows/",
    "infra/production/",
    "migrations/production/",
)


class InvalidInput(ValueError):
    pass


def require_dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidInput(f"{name} must be an object")
    return value


def evidence_present(value: Any, name: str) -> bool:
    obj = require_dict(value, name)
    if set(obj) != {"present", "evidence"}:
        raise InvalidInput(f"{name} must contain only present and evidence")
    if not isinstance(obj["present"], bool) or not isinstance(obj["evidence"], list):
        raise InvalidInput(f"{name} has invalid field types")
    if any(not isinstance(item, str) or not item.strip() for item in obj["evidence"]):
        raise InvalidInput(f"{name}.evidence must contain non-empty strings")
    return obj["present"] and bool(obj["evidence"])


def normalize_path(raw: Any) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise InvalidInput("diff.files must contain non-empty POSIX paths")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise InvalidInput("diff.files must stay repository-relative")
    return str(path)


def evaluate(data: Any) -> dict[str, Any]:
    root = require_dict(data, "input")
    expected = {"version", "dod", "verification", "preflight", "diff", "human_only_conditions"}
    if set(root) != expected or root["version"] != 1:
        raise InvalidInput("input fields or version are invalid")

    reasons: list[str] = []
    checks: list[dict[str, str]] = []
    failed = False
    require_human = False

    if not evidence_present(root["dod"], "dod"):
        failed = True
        reasons.append("Definition-of-Done evidence is missing")
    if not evidence_present(root["verification"], "verification"):
        failed = True
        reasons.append("verification evidence is missing")

    preflight = root["preflight"]
    if not isinstance(preflight, list):
        raise InvalidInput("preflight must be an array")
    seen_ids: set[str] = set()
    for index, item in enumerate(preflight):
        check = require_dict(item, f"preflight[{index}]")
        if set(check) != {"id", "applicable", "severity", "result"}:
            raise InvalidInput(f"preflight[{index}] fields are invalid")
        check_id = check["id"]
        if not isinstance(check_id, str) or not check_id or check_id in seen_ids:
            raise InvalidInput("preflight ids must be unique non-empty strings")
        seen_ids.add(check_id)
        if not isinstance(check["applicable"], bool):
            raise InvalidInput(f"preflight {check_id} applicable must be boolean")
        if check["severity"] not in {"BLOCKER", "WARNING"}:
            raise InvalidInput(f"preflight {check_id} severity is invalid")
        if check["result"] not in PREFLIGHT_RESULTS:
            raise InvalidInput(f"preflight {check_id} result is invalid")
        if not check["applicable"] and check["result"] != "SKIP":
            raise InvalidInput(f"non-applicable preflight {check_id} must SKIP")
        if check["applicable"] and check["result"] == "SKIP":
            raise InvalidInput(f"applicable preflight {check_id} cannot SKIP")
        checks.append({"id": check_id, "result": check["result"]})
        if check["applicable"] and check["severity"] == "BLOCKER" and check["result"] == "FAIL":
            failed = True
            reasons.append(f"blocker preflight failed: {check_id}")

    diff = require_dict(root["diff"], "diff")
    if set(diff) != {"risk", "files"} or diff["risk"] not in RISKS or not isinstance(diff["files"], list):
        raise InvalidInput("diff fields are invalid")
    files = [normalize_path(path) for path in diff["files"]]
    if not files:
        raise InvalidInput("diff.files must not be empty")
    sensitive = [path for path in files if path in REVIEW_PATHS or path.startswith(REVIEW_PREFIXES)]
    if diff["risk"] != "SAFE" or sensitive:
        require_human = True
        reasons.append(f"diff requires human review ({diff['risk']})")
        if sensitive:
            reasons.append("review-sensitive path changed: " + ", ".join(sensitive))

    conditions = root["human_only_conditions"]
    if not isinstance(conditions, list) or any(not isinstance(item, str) for item in conditions):
        raise InvalidInput("human_only_conditions must be an array of strings")
    unknown = sorted(set(conditions) - HUMAN_ONLY_CONDITIONS)
    if unknown:
        raise InvalidInput("unknown human_only_conditions: " + ", ".join(unknown))
    if conditions:
        require_human = True
        reasons.append("human-only condition: " + ", ".join(sorted(set(conditions))))

    decision = "FAIL" if failed else ("REQUIRE_HUMAN" if require_human else "PASS")
    return {"decision": decision, "checks": checks, "reasons": reasons}


def combine_with_security(gate_decision: str, security_decision: str) -> str:
    """Security Phase 5: combine the EXISTING (unmodified) Diana Gate
    decision with an independently-computed Security decision,
    conservatively -- this function never changes what evaluate() itself
    computes, only how its result composes with a separately-computed
    security decision:

        existing FAIL           -> final FAIL
        security FAIL           -> final FAIL
        otherwise existing REQUIRE_HUMAN or security REQUIRE_HUMAN
                                 -> final REQUIRE_HUMAN
        otherwise                -> final PASS

    A security decision of "SKIPPED_BOOTSTRAP" (the Security Phase 5 PR's
    own bootstrap case -- see diana/ci/run-security-gate.py) contributes
    NO penalty and NO escalation: the existing gate_decision passes
    through unchanged. That PR remains protected only by the pre-existing
    Diana Gate + required human/code-owner review, exactly as before this
    phase existed -- Security Phase 5 does not claim to retroactively
    protect itself.
    """
    if gate_decision not in EXIT:
        raise InvalidInput(f"unknown gate decision state: {gate_decision!r}")
    if security_decision == "SKIPPED_BOOTSTRAP":
        return gate_decision
    if security_decision not in EXIT:
        raise InvalidInput(f"unknown security decision state: {security_decision!r}")
    if gate_decision == "FAIL" or security_decision == "FAIL":
        return "FAIL"
    if gate_decision == "REQUIRE_HUMAN" or security_decision == "REQUIRE_HUMAN":
        return "REQUIRE_HUMAN"
    return "PASS"


def _main_combine(argv: list[str]) -> int:
    if len(argv) != 2:
        result = {
            "decision": "FAIL",
            "reasons": ["usage: diana-gate.py combine GATE_RESULT.json SECURITY_RESULT.json"],
        }
        print(json.dumps(result, sort_keys=True))
        return EXIT["FAIL"]
    try:
        with open(argv[0], encoding="utf-8") as handle:
            gate_result = require_dict(json.load(handle), "gate result")
        with open(argv[1], encoding="utf-8") as handle:
            security_result = require_dict(json.load(handle), "security result")
        gate_decision = gate_result["decision"]
        security_decision = security_result["decision"]
        final = combine_with_security(gate_decision, security_decision)
    except (OSError, json.JSONDecodeError, InvalidInput, KeyError) as exc:
        result = {"decision": "FAIL", "reasons": [f"could not combine gate/security results: {exc}"]}
        print(json.dumps(result, sort_keys=True))
        return EXIT["FAIL"]

    reasons = list(gate_result.get("reasons", [])) + list(security_result.get("reasons", []))
    result = {"decision": final, "reasons": reasons}
    print(json.dumps(result, sort_keys=True))
    return EXIT[final]


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "combine":
        return _main_combine(sys.argv[2:])
    if len(sys.argv) != 2:
        result = {"decision": "FAIL", "checks": [], "reasons": ["usage: diana-gate.py INPUT.json"]}
        print(json.dumps(result, sort_keys=True))
        return EXIT["FAIL"]
    try:
        with open(sys.argv[1], encoding="utf-8") as handle:
            data = json.load(handle)
        result = evaluate(data)
    except (OSError, json.JSONDecodeError, InvalidInput) as exc:
        result = {"decision": "FAIL", "checks": [], "reasons": [f"malformed input: {exc}"]}
    print(json.dumps(result, sort_keys=True))
    return EXIT[result["decision"]]


if __name__ == "__main__":
    raise SystemExit(main())
