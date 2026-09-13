#!/usr/bin/env python3
"""Diana advisory: ADVISORY_SECURITY_REVIEW v1 (spec step 5, D34-D37).

This document is deliberately NOT Diana Gate evidence, NOT a Security Track
bundle, NOT release certification, and NOT PR evidence. It is a separate,
proportional advisory artifact, and the separation is enforced STRUCTURALLY
rather than by naming convention (D35).

`diana/security/evidence_model.py:163` defines `ALLOWED_RUN_FIELDS` as an
allowlist -- `control_id`, `applicability`, `verifier`, `evidence`, `tool_error`,
`observed_at` -- and anything it cannot resolve classifies as MALFORMED. So the
anti-masquerade property is a testable one: this document must be REJECTED if
anyone ever feeds it to the Security Track. `validate()` enforces the same thing
from this side by refusing those keys at any nesting depth, so the two halves
cannot drift apart.

## Shape

`checks_performed` and `skipped` are absent: `coverage[]` already carries both
directions with reasons, and a third array that must stay consistent with two
others is a drift surface. A top-level `evidence` array is absent too -- evidence
is a property OF a finding, not a peer of it, and hoisting it would invent a join
key and a way for a finding to cite evidence that does not describe it.

There is no `advisory_only: true` disclaimer. A prose flag inside JSON is read by
nobody and enforced by nothing; `document_type` plus the structural rejection
test is the actual mechanism.

## Outcome (D36)

`COMPLETE` iff `scan_issues[]` and `unsupported_constructs[]` are BOTH empty.
`INCOMPLETE` when the trusted run completed correctly but one or more concrete
in-scope items could not be analyzed. `BLOCKED` is never a value here: a blocked
run produces no document at all, only a Diana run record (D37).

## unverified_observations (D1)

Whatever Hermes said. It carries NO severity field -- not "INFO", not null, the
key does not exist -- so it cannot be mistaken for a finding, and it is ungraded:
nothing in the acceptance suite passes or fails on its contents.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runtime"))
import blocking  # noqa: E402
import contract as _contract  # noqa: E402

DOCUMENT_TYPE = "ADVISORY_SECURITY_REVIEW"
SCHEMA_VERSION = 1

OUTCOME_COMPLETE = "COMPLETE"
OUTCOME_INCOMPLETE = "INCOMPLETE"

ARTIFACT_KEYS = (
    "document_type",
    "schema_version",
    "run_id",
    "outcome",
    "contract",
    "contract_digest",
    "findings",
    "suppressed",
    "coverage",
    "unsupported_constructs",
    "scan_issues",
    "limitations",
    "unverified_observations",
)

# evidence_model.ALLOWED_RUN_FIELDS, mirrored here so this document can be
# proven structurally incompatible from both directions.
SECURITY_TRACK_FIELDS = frozenset(
    {"control_id", "applicability", "verifier", "evidence", "tool_error", "observed_at"}
)

SCAN_ISSUE_KINDS = frozenset({"READ_FAILED", "DECODE_FAILED", "FILE_TOO_LARGE"})
COVERAGE_STATUSES = frozenset(
    {"CHECKED", "NOT_CHECKED", "NOT_APPLICABLE", "APPLICABILITY_UNKNOWN"}
)

# Closed schema for one Hermes observation (D14): unknown fields fail validation
# rather than being recorded, because an anomaly log is itself a side channel.
OBSERVATION_KEYS = frozenset({"note", "file"})


class ArtifactError(ValueError):
    """The document does not satisfy its own schema."""


def _forbidden_keys(node, path="$"):
    """Every Security-Track key found at any depth. Empty list is the goal."""
    hits = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in SECURITY_TRACK_FIELDS:
                hits.append(path + "." + str(key))
            hits += _forbidden_keys(value, path + "." + str(key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            hits += _forbidden_keys(value, path + "[" + str(i) + "]")
    return hits


def derive_outcome(scan_issues, unsupported_constructs):
    """D36, stated once so no caller can reinvent it."""
    if not scan_issues and not unsupported_constructs:
        return OUTCOME_COMPLETE
    return OUTCOME_INCOMPLETE


def validate_observations(observations):
    """Closed schema for Hermes output. Unknown field => Blocked, not recorded."""
    if not isinstance(observations, list):
        raise blocking.Blocked(
            blocking.HERMES_OUTPUT_SCHEMA_VIOLATION, "observations must be a list"
        )
    for item in observations:
        if not isinstance(item, dict):
            raise blocking.Blocked(
                blocking.HERMES_OUTPUT_SCHEMA_VIOLATION, "observation must be an object"
            )
        unknown = sorted(set(item) - OBSERVATION_KEYS)
        if unknown:
            # Includes any attempt to speak about risk, depth or severity:
            # Hermes has no proposal channel for those (D14), and no severity
            # field exists on an observation at all (D1).
            raise blocking.Blocked(
                blocking.HERMES_OUTPUT_SCHEMA_VIOLATION,
                "unknown observation field(s): " + str(unknown),
            )
        note = item.get("note")
        if not isinstance(note, str) or not note.strip():
            raise blocking.Blocked(
                blocking.HERMES_OUTPUT_SCHEMA_VIOLATION,
                "observation.note must be a non-empty string",
            )
        if "file" in item and not isinstance(item["file"], str):
            raise blocking.Blocked(
                blocking.HERMES_OUTPUT_SCHEMA_VIOLATION, "observation.file must be a string"
            )
    return [dict(item) for item in observations]


def build(contract_block, scan_result, coverage, observations=None):
    """Assemble the document. Diana builds it; Hermes never writes it (D9)."""
    _contract.validate(contract_block)
    document = {
        "document_type": DOCUMENT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "run_id": contract_block["run_id"],
        "outcome": derive_outcome(
            scan_result["scan_issues"], scan_result["unsupported_constructs"]
        ),
        "contract": contract_block,
        "contract_digest": _contract.digest(contract_block),
        "findings": list(scan_result["findings"]),
        "suppressed": list(scan_result["suppressed"]),
        "coverage": list(coverage),
        "unsupported_constructs": list(scan_result["unsupported_constructs"]),
        "scan_issues": list(scan_result["scan_issues"]),
        "limitations": list(scan_result["limitations"]),
        "unverified_observations": validate_observations(observations or []),
    }
    validate(document)
    return document


def validate(document):
    """Raise ArtifactError unless the document satisfies schema v1."""
    if not isinstance(document, dict):
        raise ArtifactError("document must be an object")
    keys = set(document)
    missing = sorted(set(ARTIFACT_KEYS) - keys)
    extra = sorted(keys - set(ARTIFACT_KEYS))
    if missing or extra:
        raise ArtifactError("missing=" + str(missing) + " unexpected=" + str(extra))
    if document["document_type"] != DOCUMENT_TYPE:
        raise ArtifactError("document_type must be " + DOCUMENT_TYPE)
    if document["schema_version"] != SCHEMA_VERSION:
        raise ArtifactError("schema_version must be 1")
    if document["outcome"] not in (OUTCOME_COMPLETE, OUTCOME_INCOMPLETE):
        # BLOCKED is never a document value (D36).
        raise ArtifactError("outcome must be COMPLETE or INCOMPLETE")

    hits = _forbidden_keys(document)
    if hits:
        raise ArtifactError("Security Track field(s) present, masquerade risk: " + str(hits))

    expected = derive_outcome(document["scan_issues"], document["unsupported_constructs"])
    if document["outcome"] != expected:
        raise ArtifactError(
            "outcome " + document["outcome"] + " disagrees with its own arrays (expected "
            + expected + ")"
        )

    _contract.validate(document["contract"])
    if document["run_id"] != document["contract"]["run_id"]:
        raise ArtifactError("run_id disagrees with the embedded contract")
    actual = _contract.digest(document["contract"])
    if document["contract_digest"] != actual:
        raise ArtifactError("contract_digest " + str(document["contract_digest"]) + " != " + actual)

    for finding in document["findings"]:
        _validate_finding(finding)
    for entry in document["suppressed"]:
        for key in ("file", "line", "rule_id", "reason"):
            if key not in entry:
                raise ArtifactError("suppressed entry missing " + key)
    for entry in document["unsupported_constructs"]:
        for key in ("file", "line", "construct", "reason"):
            if key not in entry:
                raise ArtifactError("unsupported_constructs entry missing " + key)
    for entry in document["scan_issues"]:
        if entry.get("kind") not in SCAN_ISSUE_KINDS:
            raise ArtifactError("scan_issues kind must be one of " + str(sorted(SCAN_ISSUE_KINDS)))
    for entry in document["coverage"]:
        if entry.get("status") not in COVERAGE_STATUSES:
            raise ArtifactError("coverage status must be one of " + str(sorted(COVERAGE_STATUSES)))
        if "reason" not in entry and "rule_ids" not in entry:
            raise ArtifactError("coverage entry must carry a reason or rule_ids")
    for entry in document["unverified_observations"]:
        if "severity" in entry:
            raise ArtifactError("unverified_observations must carry no severity field")


def _validate_finding(finding):
    if not isinstance(finding, dict):
        raise ArtifactError("finding must be an object")
    required = {"rule_id", "severity", "file", "line", "source", "sink", "flow", "code_excerpt"}
    missing = sorted(required - set(finding))
    if missing:
        raise ArtifactError("finding missing " + str(missing))
    if finding["severity"] not in ("HIGH", "MEDIUM"):
        raise ArtifactError("finding severity must be HIGH or MEDIUM")
    if finding["flow"] != "DIRECT":
        raise ArtifactError("M1 emits only DIRECT flows")
    for side in ("source", "sink"):
        for key in ("kind", "line", "text"):
            if key not in finding[side]:
                raise ArtifactError("finding." + side + " missing " + key)
    if not isinstance(finding["line"], int) or finding["line"] < 1:
        raise ArtifactError("finding.line must be a 1-indexed integer")


def persist(document, run_directory):
    """Write the document into the Diana run directory, OUTSIDE the target repo.

    Hermes never writes this file (D9); the target repository must be byte- and
    git-state-identical before and after the run, which is a property a test can
    actually check.
    """
    validate(document)
    directory = Path(run_directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "advisory-security-review.json"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
