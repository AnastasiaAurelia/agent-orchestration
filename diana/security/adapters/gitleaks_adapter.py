#!/usr/bin/env python3
"""Diana Security static adapter: Gitleaks (Security Phase 2, final
trust-boundary correction; scope model extended in Security Track
remediation round B).

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
expectation the caller supplied, plus `target.scope` matching the
control's own REQUIRED scope (see "Scope model" below) -- each control's
claim is only as broad as the surface a caller actually asserts they
scanned.

## Scope model (round B: the real fix, not a shortcut)

Phase 2 originally recognized exactly one scope, `"full-repo"`, and
deliberately excluded `SEC-006`/`SEC-065` because both make a claim
about a specific surface this adapter's target model didn't yet
represent: a frontend/client build's shipped output is typically NOT
committed to source at all (built and deployed separately, often
`.gitignore`d), so a `"full-repo"` scan of committed source cannot
establish anything about it either way.

This round adds that missing surface explicitly, rather than loosening
`AUTHORIZED_EVIDENCE` to cover it with an inadequate scope model:

- `SCOPE_FULL_REPO` (`"full-repo"`) -- the entire repository source, as
  before. Required for `SEC-007` ("no credential/token/private-key
  literal is committed in source" -- a claim about SOURCE, full stop).
- `SCOPE_FRONTEND_BUNDLE` (`"frontend-bundle"`) -- exactly the shipped,
  built frontend/client bundle output, NOT general source. Required for
  `SEC-006` ("no third-party or backend secret key is present in shipped
  frontend/client bundle source" -- a claim specifically about that
  built output).
- `SCOPE_FULL_REPO_AND_FRONTEND_BUNDLE`
  (`"full-repo-and-frontend-bundle"`) -- BOTH surfaces scanned together
  in one artifact. Required for `SEC-065` ("service-role/elevated keys
  are never shipped to a client build OR committed to source" -- a
  conjunction of both claims. `evidence_model.py`'s aggregation
  (Phase 1, not modified here or anywhere in this round) resolves
  `SATISFIED` for one `(control_id, requirement)` pair from ANY single
  accepted contribution -- it does not itself support requiring two
  independent scoped contributions to jointly satisfy one requirement.
  Rather than touch Phase 1 aggregation semantics (out of bounds for
  this round) or accept a WEAKER, partially-scoped claim as if it proved
  the full conjunction (fabrication), `SEC-065`'s single required_
  evidence string is satisfied only by ONE artifact whose scope
  explicitly asserts BOTH surfaces were covered together. The artifact
  PRODUCER is responsible for genuinely having scanned the union of both
  file sets and truthfully declaring this scope -- this adapter checks
  it structurally, exactly as it already trusted `"full-repo"` for
  `SEC-007`; it does not (and, given only a native Gitleaks JSON report
  is available, cannot) independently reconstruct which files were
  actually scanned beyond what `verify_target()`'s caller-supplied
  expectation already checks.

A finding's relevance for `VIOLATED` is scope-aware too, not just the
identity check: a finding from a scan scoped to `SCOPE_FULL_REPO` alone
is NOT treated as evidence for `SEC-006`/`SEC-065`, because (per the
reasoning above) a full-repo scan of committed source does not
necessarily cover the frontend bundle surface those controls are
actually about -- see `RELEVANT_SCOPES_FOR_VIOLATION` below. `SEC-007`
remains scope-independent for `VIOLATED` (its claim is "nowhere in
source", so any scope is meaningful), unchanged from the original
design.

## Authorization (integrity invariant, unchanged)

Every Gitleaks finding is, by the tool's own design, a detected
credential-like literal in source -- Gitleaks has no other kind of
finding. `AUTHORIZED_EVIDENCE` below maps each authorized control to
exactly the one required_evidence string this adapter can help prove,
scope-gated as described above. This adapter remains deliberately NOT
authorized for `SEC-007`'s second requirement ("secrets are loaded from
environment/secret-manager configuration, not source") -- that needs
`SEC-007`'s other permitted capability, `STATIC_ANALYZER`, contributing
separately. It also remains NOT authorized for `SEC-006`'s second
requirement ("frontend build only contains publishable/public keys") --
determining whether a specific found key is a legitimately publishable
key (e.g. a Stripe `pk_...` vs `sk_...` prefix, a Supabase anon vs
service_role JWT) is a provider-specific classification problem a
generic secret-shaped-literal scanner does not solve; building a safe,
general, multi-provider key-classification capability remains explicitly
out of scope for this round too.

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
SEC006_REQ_NO_THIRD_PARTY_SECRET = (
    "no third-party or backend secret key is present in shipped frontend/client bundle source"
)
SEC065_REQ_NO_ELEVATED_KEY = (
    "service-role/elevated keys are never shipped to a client build or committed to source, only used server-side"
)

AUTHORIZED_EVIDENCE = {
    "SEC-007": [SEC007_REQ_NO_LITERAL],
    "SEC-006": [SEC006_REQ_NO_THIRD_PARTY_SECRET],
    "SEC-065": [SEC065_REQ_NO_ELEVATED_KEY],
}

SCOPE_FULL_REPO = "full-repo"
SCOPE_FRONTEND_BUNDLE = "frontend-bundle"
SCOPE_FULL_REPO_AND_FRONTEND_BUNDLE = "full-repo-and-frontend-bundle"

ALLOWED_SCOPES = {SCOPE_FULL_REPO, SCOPE_FRONTEND_BUNDLE, SCOPE_FULL_REPO_AND_FRONTEND_BUNDLE}

# The single scope value a clean (zero-finding) scan must declare for a
# SATISFIED contribution to this control -- deterministic, explicit,
# never inferred/guessed at.
REQUIRED_SCOPE_FOR_SATISFIED = {
    "SEC-007": SCOPE_FULL_REPO,
    "SEC-006": SCOPE_FRONTEND_BUNDLE,
    "SEC-065": SCOPE_FULL_REPO_AND_FRONTEND_BUNDLE,
}

# Which declared scopes make a FINDING meaningful evidence for this
# control. `None` means "scope-independent, any declared scope value
# counts" (SEC-007's claim is "nowhere in source", true regardless of
# exactly what subset was scanned -- this was the original, unmodified
# Phase 2 behavior, and is deliberately NOT restricted to the new
# ALLOWED_SCOPES enum, since a caller may legitimately declare a scope
# string this adapter has no specific opinion about, e.g. "partial").
# SEC-006/SEC-065 are about specific surfaces, so a finding from a scan
# that didn't plausibly cover that surface is not evidence for them --
# these two ARE restricted to their real, enumerated relevant scopes.
RELEVANT_SCOPES_FOR_VIOLATION: dict[str, set[str] | None] = {
    "SEC-007": None,
    "SEC-006": {SCOPE_FRONTEND_BUNDLE, SCOPE_FULL_REPO_AND_FRONTEND_BUNDLE},
    "SEC-065": {SCOPE_FULL_REPO_AND_FRONTEND_BUNDLE},
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
    declared_scope = envelope["target"].get("scope")

    contributions = []
    for control_id in requested_authorized:
        requirement = AUTHORIZED_EVIDENCE[control_id][0]
        relevant_scopes = RELEVANT_SCOPES_FOR_VIOLATION[control_id]
        scope_relevant = relevant_scopes is None or declared_scope in relevant_scopes
        if findings:
            if identity_verified and scope_relevant:
                # Trusted from partial coverage, but only when attributed
                # to the correct repository + commit, AND only when the
                # declared scope plausibly covers the surface this
                # control is actually about.
                for finding in findings:
                    rule = finding.get("RuleID", "unknown-rule")
                    path = finding.get("File", "unknown-file")
                    contributions.append(
                        (
                            control_id,
                            requirement,
                            "VIOLATED",
                            f"gitleaks finding: rule={rule} file={path} (scope={declared_scope})",
                            None,
                        )
                    )
            # else: finding(s) present, but either target identity could
            # not be verified, or the declared scope doesn't plausibly
            # cover this control's surface -- no contribution at all.
        elif target_verified and declared_scope == REQUIRED_SCOPE_FOR_SATISFIED[control_id]:
            contributions.append(
                (
                    control_id,
                    requirement,
                    "SATISFIED",
                    (
                        "gitleaks scan completed with zero findings; target verified "
                        f"(repository={envelope['target'].get('repository')!r}, "
                        f"commit={envelope['target'].get('commit')!r}, scope={declared_scope!r})"
                    ),
                    None,
                )
            )
        # else: clean result, but target/scope could not be verified as
        # matching this control's required scope -- no contribution.

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
