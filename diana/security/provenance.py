#!/usr/bin/env python3
"""Diana Security verifier provenance manifest (Security Phase 7.5).

A canonical, tool-agnostic record of "what ran, which version, against
what target, with which rules/templates, when, and what artifact proves
it" -- shared by every trusted external-verifier family this track wires
live (Semgrep, Gitleaks, the deterministic repo scan, the dynamic
sensitive-path-fetch scenario, osv-scanner, Trivy, the bounded ZAP
verifier, the curated Nuclei verifier). This module is deliberately
verifier-agnostic and pure: it builds/validates a manifest dict from
already-known facts, it never invokes a tool, reads the network, or reads
the clock itself. Every field it records comes from the caller.

## What this is not

This is NOT a replacement for `adapters/adapter_base.py`'s
`artifact_binding` (tool/execution/target/config/scanned_inputs/report
hash) or `dynamic/dynamic_base.py`'s equivalent -- those remain the
per-family integrity bindings evidence_model.py's adapters already trust.
`build_manifest()` below is a SEPARATE, additive record most useful for
humans/audits asking "what verifiers ran in this CI invocation, with
which tool/template versions" -- it is not consumed by evidence_model.py
and does not change any PASS/FAIL/UNPROVEN/ERROR determination.

## No secrets

Nothing in `build_manifest()` accepts or stores a credential, token, or
API key -- REQUIRED_FIELDS below is a closed set and `build_manifest()`
raises `ValueError` for any unknown top-level key, so a caller cannot
smuggle an extra field (e.g. an accidental credential) into a manifest
that then gets persisted/printed.

## Deterministic serialization + mutation detection

`canonical_hash()` hashes a fixed, sorted-key, compact JSON serialization
of the manifest's bound fields (`BOUND_FIELDS`) -- semantically identical
content always hashes identically regardless of key order, and mutating
ANY bound field is detected by `verify_manifest_digest()`. Exactly the
same shape of guarantee `adapter_base.canonical_artifact_hash()` already
gives the scan-evidence envelope, extended to the provenance record
itself.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA_VERSION = 1

# Bounded, closed set of recognized verifier_type values a manifest may
# declare -- deliberately narrower than catalog.json's full verifier_types
# enum, since a provenance manifest only ever describes an EXTERNAL TOOL
# invocation (never a semantic-review or deterministic-Diana-internal
# check, which have no separate "tool version"/"ruleset digest" to record).
RECOGNIZED_VERIFIER_TYPES = {
    "STATIC_ANALYZER",
    "SECRET_SCANNER",
    "DEPENDENCY_SCANNER",
    "DYNAMIC_API",
    "DYNAMIC_BROWSER",
}

REQUIRED_FIELDS = {
    "schema_version",
    "verifier_type",
    "tool_name",
    "tool_version",
    "repository",
    "commit",
    "environment",
    "target",
    "started_at",
    "finished_at",
    "command_identity",
    "ruleset_identity",
    "artifact_digest",
    "exit_status",
    "coverage_description",
}

# Fields NEVER accepted -- a defense-in-depth denylist, so an accidental
# caller-supplied credential-shaped key is rejected outright rather than
# silently persisted. Checked in addition to (not instead of) the closed
# REQUIRED_FIELDS allowlist above.
FORBIDDEN_FIELD_SUBSTRINGS = ("token", "secret", "password", "credential", "api_key", "apikey")

BOUND_FIELDS = tuple(sorted(REQUIRED_FIELDS))


class ProvenanceError(ValueError):
    """The manifest is structurally invalid, contains a forbidden field,
    or fails its own integrity binding."""


def build_manifest(
    *,
    verifier_type: str,
    tool_name: str,
    tool_version: str,
    repository: str,
    commit: str,
    environment: str,
    target: str,
    started_at: str,
    finished_at: str,
    command_identity: str,
    ruleset_identity: str,
    artifact_digest: str,
    exit_status: str,
    coverage_description: str,
) -> dict[str, Any]:
    """Builds one canonical provenance manifest record. Every argument is
    a plain string the caller already computed/observed -- this function
    never invokes a tool, reads the network, or reads the clock. Raises
    ProvenanceError for any non-string / empty-string value (fail closed,
    never silently accept a placeholder) or an unrecognized
    `verifier_type`."""
    fields = {
        "schema_version": SCHEMA_VERSION,
        "verifier_type": verifier_type,
        "tool_name": tool_name,
        "tool_version": tool_version,
        "repository": repository,
        "commit": commit,
        "environment": environment,
        "target": target,
        "started_at": started_at,
        "finished_at": finished_at,
        "command_identity": command_identity,
        "ruleset_identity": ruleset_identity,
        "artifact_digest": artifact_digest,
        "exit_status": exit_status,
        "coverage_description": coverage_description,
    }
    return _finalize(fields)


def _finalize(fields: dict[str, Any]) -> dict[str, Any]:
    if set(fields.keys()) != REQUIRED_FIELDS:
        raise ProvenanceError(
            f"manifest must declare exactly {sorted(REQUIRED_FIELDS)}, got {sorted(fields.keys())}"
        )
    for key, value in fields.items():
        if key == "schema_version":
            continue
        if not isinstance(value, str) or not value.strip():
            raise ProvenanceError(f"manifest field {key!r} must be a non-empty string, got {value!r}")
        lowered = key.lower()
        if any(bad in lowered for bad in FORBIDDEN_FIELD_SUBSTRINGS):
            raise ProvenanceError(f"manifest field name {key!r} looks credential-shaped -- refused")
    if fields["verifier_type"] not in RECOGNIZED_VERIFIER_TYPES:
        raise ProvenanceError(
            f"verifier_type {fields['verifier_type']!r} is not recognized "
            f"({sorted(RECOGNIZED_VERIFIER_TYPES)})"
        )
    if fields["schema_version"] != SCHEMA_VERSION:
        raise ProvenanceError(f"schema_version must be {SCHEMA_VERSION}, got {fields['schema_version']!r}")

    manifest = dict(fields)
    manifest["manifest_binding"] = {"sha256": canonical_hash(manifest)}
    return manifest


def canonical_hash(manifest: dict[str, Any]) -> str:
    """Deterministic hash over every bound field (BOUND_FIELDS) -- NOT
    including any existing `manifest_binding` key on the input, so this
    is safe to call both when building (no binding present yet) and when
    re-verifying (binding present, ignored)."""
    bound = {key: manifest[key] for key in BOUND_FIELDS}
    canonical = json.dumps(bound, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_manifest(raw: Any) -> dict[str, Any]:
    """Structural + integrity validation of an already-serialized
    manifest (e.g. read back from a JSON file). Raises ProvenanceError
    for anything structurally wrong or a binding mismatch. Never raises
    for a manifest this module itself produced and did not mutate."""
    if not isinstance(raw, dict):
        raise ProvenanceError("manifest must be a JSON object")
    missing = REQUIRED_FIELDS - set(raw.keys())
    if missing:
        raise ProvenanceError(f"manifest missing required field(s): {sorted(missing)}")
    binding = raw.get("manifest_binding")
    if not isinstance(binding, dict) or not isinstance(binding.get("sha256"), str):
        raise ProvenanceError("manifest.manifest_binding must be an object with a string sha256")

    fields = {key: raw[key] for key in REQUIRED_FIELDS}
    validated = _finalize(fields)  # re-runs every structural/content check above
    actual_hash = canonical_hash(raw)
    if actual_hash != binding["sha256"]:
        raise ProvenanceError(
            "manifest.manifest_binding.sha256 does not match the bound fields -- "
            "the manifest may have been modified or substituted after binding"
        )
    return validated


def verify_manifest_digest(manifest: dict[str, Any]) -> bool:
    """Returns True iff `manifest`'s own manifest_binding.sha256 matches a
    freshly recomputed hash of its bound fields -- never raises. Prefer
    `load_manifest()` when you want a hard failure with a reason;
    this is for a caller that only wants a boolean check."""
    binding = manifest.get("manifest_binding")
    if not isinstance(binding, dict):
        return False
    return canonical_hash(manifest) == binding.get("sha256")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"version": SCHEMA_VERSION, "error": "usage: provenance.py <manifest.json>"}, sort_keys=True))
        return 1
    import sys as _sys

    try:
        with open(argv[1], "r", encoding="utf-8") as f:
            raw = json.load(f)
        manifest = load_manifest(raw)
    except (OSError, json.JSONDecodeError, ProvenanceError) as exc:
        print(json.dumps({"version": SCHEMA_VERSION, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"version": SCHEMA_VERSION, "manifest": manifest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv))
