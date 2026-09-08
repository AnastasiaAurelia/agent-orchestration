"""Shared, minimal framework for Diana Security dynamic-verification
scenario normalization (Security Phase 3).

A dynamic scenario evidence artifact is produced by an EXTERNAL runtime
scenario runner -- Diana does not execute HTTP requests, drive a browser,
touch a database, or call a payment provider. This module only parses an
already-produced artifact recording what a scenario runner did and
observed, exactly as diana/security/adapters/adapter_base.py only parses
an already-produced static tool report:

    approved local/test/sandbox target
            |
    external/runtime scenario runner        <- NOT this repository
            |
    bound dynamic evidence artifact          <- what this module parses
            |
    thin Diana normalization (this module)
            |
    evidence_model.py

## Carrying the Phase 2 lesson forward

"Evidence for one target/build must not prove another." Every artifact
declares a target (repository, commit, base_url) and an environment class;
callers supply an *expected* target the same way Phase 2 adapters do, and
`verify_scenario_identity()`/`verify_scenario_target()` mirror Phase 2's
`verify_identity()`/`verify_target()` split exactly: a scenario that
observed a real security violation (a FAILED required assertion) is
trusted from partial coverage but must still be attributed to the correct
repository+commit; a scenario that observed everything working correctly
(all required assertions PASSED) additionally needs the full target/scope
proof before it can count as SATISFIED.

## Environment classification is explicit, never inferred

`ALLOWED_ENVIRONMENTS` is a small, closed set: `LOCAL`, `TEST`, `SANDBOX`.
Anything else -- `PROD`, `PRODUCTION`, `STAGING`, a typo, an environment
label this module has never seen -- is refused outright. There is no
"probably staging" heuristic; an unrecognized environment string is
treated exactly the same as a known-dangerous one (`ArtifactError`).

## Identities are bounded role labels, never credentials

`ALLOWED_IDENTITY_LABELS` is a small, closed set of logical role labels
(`ANONYMOUS`, `USER_A`, `USER_B`, ...). Runtime credentials (passwords,
cookies, session tokens, API keys) are injected by the external scenario
runner and never appear in, or are accepted by, this module -- an
identities list is validated against this bounded enum, so nothing
credential-shaped can pass structural validation even by accident.

## Scenario authorization mirrors Phase 2's AUTHORIZED_EVIDENCE

`scenarios.SCENARIO_REGISTRY` maps a `scenario.id` to exactly one
`(control_id, requirement)` pair this scenario is designed to establish,
plus the assertions/identities/verifier-mode it requires. An artifact's
own self-reported `result.control_id`/`result.requirement` must match the
registry exactly (never trusted verbatim) -- and, critically, the
resulting `SATISFIED`/`VIOLATED` status is *computed* by this module from
the raw `assertions` outcomes against the registry's `required_assertions`,
never read directly from the artifact. An artifact cannot claim its own
conclusion; it can only report what actually happened.

## Result computation

- `execution.completed != true` -> `ERROR` (nothing in the artifact can be
  trusted).
- A `required_assertions` entry missing from the artifact's `assertions`
  list -> `UNPROVEN` ("skipped assertion").
- Any `required_assertions` entry with outcome `FAILED` -> the security
  property under test did not hold. Trusted (`VIOLATED`) as long as
  `verify_scenario_identity()` passes -- partial coverage is fine for a
  real, observed violation.
- All `required_assertions` `PASSED` -> `SATISFIED` only if
  `verify_scenario_target()` (identity + environment + build) passes.
- Any FAILED assertion, or any PASSED-but-unverified-target case, that
  cannot be attributed to the expected target -> no contribution at all
  (deterministically `UNPROVEN` once combined with `build_runs()`'s
  requested-control fill-in, exactly as in Phase 2).

## Cleanup visibility

A mutating scenario's `cleanup.success == false` never changes the
computed result, but is always appended to the emitted evidence item's
`provenance` text -- "do not hide it" is implemented as "always say it,"
not as a separate silent failure mode.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapters"))
import adapter_base  # noqa: E402  (reuse build_runs/tool_error_runs/tool_unavailable_runs/NotAuthorized)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import validate_catalog  # noqa: E402  (reuse the Phase 0 DYNAMIC_VERIFIER_TYPES enum)


class ArtifactError(ValueError):
    """The scenario evidence artifact itself is structurally invalid,
    declares a refused environment, fails its integrity binding, or
    violates one of this module's scenario-safety invariants (e.g. an
    unbounded concurrency request, a privileged DB bypass, a live
    payment mode, or an AI scenario that never actually exercised the
    model). Distinct from a target mismatch: an ArtifactError means
    nothing in the artifact -- including any observed assertion outcome
    -- can be trusted."""


ALLOWED_ENVIRONMENTS = {"LOCAL", "TEST", "SANDBOX"}

# Bounded, closed set of logical test-identity role labels. Nothing
# credential-shaped can pass this check even by accident.
ALLOWED_IDENTITY_LABELS = {
    "ANONYMOUS",
    "USER_A",
    "USER_B",
    "USER_C",
    "TENANT_A",
    "TENANT_B",
    "PRIVILEGED_TEST_USER",
    "PROVIDER_TEST_ACCOUNT",
}

ALLOWED_ASSERTION_OUTCOMES = {"PASSED", "FAILED"}

# Inert, non-exfiltrating browser probe markers a scenario may use.
# Anything else (a real external callback URL, destructive JS, etc.) is
# rejected structurally.
ALLOWED_PAYLOAD_MARKERS = {"DIANA_XSS_PROBE_MARKER"}

MAX_CONCURRENCY = 5  # hard ceiling -- bounded invariant testing only, never load testing

REQUIRED_ENVELOPE_FIELDS = {
    "environment",
    "target",
    "scenario",
    "verifier_mode",
    "identities",
    "execution",
    "assertions",
    "cleanup",
    "result",
    "artifact_binding",
}
BOUND_FIELDS = (
    "environment",
    "target",
    "scenario",
    "verifier_mode",
    "identities",
    "execution",
    "assertions",
    "cleanup",
    "result",
)


def canonical_artifact_hash(envelope: dict[str, Any]) -> str:
    bound = {key: envelope[key] for key in BOUND_FIELDS}
    canonical = json.dumps(bound, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_scenario_envelope(raw: Any) -> dict[str, Any]:
    """Structural + integrity + safety-invariant validation. Raises
    ArtifactError for anything that makes the artifact untrustworthy or
    unsafe as a whole. Does not check the declared target against any
    expectation, or compute a result -- see verify_scenario_identity()/
    verify_scenario_target() and dynamic_normalizer.evaluate_scenario()."""
    if not isinstance(raw, dict):
        raise ArtifactError("artifact must be a JSON object")

    missing = REQUIRED_ENVELOPE_FIELDS - set(raw.keys())
    if missing:
        raise ArtifactError(f"artifact missing required field(s): {sorted(missing)}")

    environment = raw["environment"]
    if not isinstance(environment, str) or environment not in ALLOWED_ENVIRONMENTS:
        raise ArtifactError(
            f"artifact.environment {environment!r} is not an allowed environment "
            f"({sorted(ALLOWED_ENVIRONMENTS)}) -- refused explicitly, never inferred"
        )

    target = raw["target"]
    if (
        not isinstance(target, dict)
        or not isinstance(target.get("repository"), str)
        or not target.get("repository", "").strip()
        or not isinstance(target.get("commit"), str)
        or not target.get("commit", "").strip()
    ):
        raise ArtifactError("artifact.target must be an object with non-empty string repository and commit")

    scenario = raw["scenario"]
    if (
        not isinstance(scenario, dict)
        or not isinstance(scenario.get("id"), str)
        or not scenario.get("id", "").strip()
        or not isinstance(scenario.get("version"), str)
        or not scenario.get("version", "").strip()
    ):
        raise ArtifactError("artifact.scenario must be an object with non-empty string id and version")

    verifier_mode = raw["verifier_mode"]
    if verifier_mode not in validate_catalog.DYNAMIC_VERIFIER_TYPES:
        raise ArtifactError(
            f"artifact.verifier_mode {verifier_mode!r} is not a known dynamic verifier type "
            f"({sorted(validate_catalog.DYNAMIC_VERIFIER_TYPES)})"
        )

    identities = raw["identities"]
    if not isinstance(identities, list) or not all(isinstance(x, str) for x in identities):
        raise ArtifactError("artifact.identities must be a list of strings")
    unknown_identities = set(identities) - ALLOWED_IDENTITY_LABELS
    if unknown_identities:
        raise ArtifactError(
            f"artifact.identities contains unrecognized label(s) {sorted(unknown_identities)} -- "
            f"only bounded logical role labels are accepted ({sorted(ALLOWED_IDENTITY_LABELS)}), "
            f"never credentials"
        )

    execution = raw["execution"]
    if not isinstance(execution, dict) or execution.get("completed") is not True:
        raise ArtifactError("artifact.execution.completed must be true -- scenario did not report successful completion")

    assertions = raw["assertions"]
    if not isinstance(assertions, list):
        raise ArtifactError("artifact.assertions must be a list")
    seen_assertion_ids: set[str] = set()
    for a in assertions:
        if (
            not isinstance(a, dict)
            or not isinstance(a.get("id"), str)
            or not a.get("id", "").strip()
            or a.get("outcome") not in ALLOWED_ASSERTION_OUTCOMES
        ):
            raise ArtifactError(
                f"each assertion must be an object with a non-empty string id and outcome in "
                f"{sorted(ALLOWED_ASSERTION_OUTCOMES)}, got {a!r}"
            )
        if a["id"] in seen_assertion_ids:
            raise ArtifactError(f"duplicate assertion id: {a['id']!r}")
        seen_assertion_ids.add(a["id"])

    cleanup = raw["cleanup"]
    if (
        not isinstance(cleanup, dict)
        or not isinstance(cleanup.get("required"), bool)
        or not isinstance(cleanup.get("performed"), bool)
        or not isinstance(cleanup.get("success"), bool)
    ):
        raise ArtifactError("artifact.cleanup must be an object with boolean required/performed/success")

    result = raw["result"]
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("control_id"), str)
        or not isinstance(result.get("requirement"), str)
        or not result.get("requirement", "").strip()
    ):
        raise ArtifactError("artifact.result must be an object with a string control_id and non-empty requirement")

    artifact_binding = raw["artifact_binding"]
    if not isinstance(artifact_binding, dict) or not isinstance(artifact_binding.get("sha256"), str):
        raise ArtifactError("artifact.artifact_binding must be an object with a string sha256")

    actual_hash = canonical_artifact_hash(raw)
    if actual_hash != artifact_binding["sha256"]:
        raise ArtifactError(
            "artifact.artifact_binding.sha256 does not match the bound fields -- "
            "the artifact may have been modified or substituted after binding"
        )

    return raw


def verify_scenario_identity(target: dict[str, Any], expected_target: dict[str, Any] | None) -> tuple[bool, str | None]:
    """TARGET IDENTITY ONLY: repository + commit. Gates whether a FAILED
    (violation) assertion can be attributed to the expected target."""
    if not target:
        return False, "artifact declares no target context"
    if not expected_target:
        return False, "caller supplied no expected target"

    expected_repo = expected_target.get("repository")
    expected_commit = expected_target.get("commit")
    if not expected_repo or not expected_commit:
        return False, "expected_target must specify non-empty repository and commit"

    actual_repo = target.get("repository")
    actual_commit = target.get("commit")
    if not actual_repo or not actual_commit:
        return False, "artifact target must specify non-empty repository and commit"

    if actual_repo != expected_repo:
        return False, f"target.repository mismatch: expected {expected_repo!r}, artifact declares {actual_repo!r}"
    if actual_commit != expected_commit:
        return False, f"target.commit mismatch: expected {expected_commit!r}, artifact declares {actual_commit!r}"

    return True, None


def verify_scenario_target(target: dict[str, Any], expected_target: dict[str, Any] | None) -> tuple[bool, str | None]:
    """FULL verification: identity plus every other key the caller
    supplied (e.g. base_url). Gates whether a clean (all-PASSED) scenario
    can count as SATISFIED."""
    identity_ok, reason = verify_scenario_identity(target, expected_target)
    if not identity_ok:
        return False, reason

    for key, value in (expected_target or {}).items():
        if key in ("repository", "commit"):
            continue
        actual = target.get(key)
        if actual != value:
            return False, f"target.{key} mismatch: expected {value!r}, artifact declares {actual!r}"

    return True, None


def validate_scenario_safety_invariants(envelope: dict[str, Any], spec: dict[str, Any]) -> None:
    """Scenario-family-specific safety checks declared by the scenario's
    registry entry. Raises ArtifactError (never silently passes/ignores)
    when a safety invariant this framework enforces is violated."""
    if spec.get("max_concurrency") is not None:
        concurrency = envelope.get("concurrency", {})
        requests = concurrency.get("simultaneous_requests") if isinstance(concurrency, dict) else None
        if not isinstance(requests, int) or requests < 2:
            raise ArtifactError(
                "concurrency scenario must declare concurrency.simultaneous_requests as an integer >= 2"
            )
        if requests > MAX_CONCURRENCY:
            raise ArtifactError(
                f"concurrency.simultaneous_requests={requests} exceeds the hard ceiling of {MAX_CONCURRENCY} "
                f"-- bounded invariant testing only, never load testing"
            )

    if spec.get("forbid_privileged_bypass"):
        if envelope.get("used_privileged_bypass", False) is not False:
            raise ArtifactError(
                "negative access requests must not use service/admin bypass credentials "
                "(artifact declares used_privileged_bypass=true)"
            )

    if spec.get("requires_provider_sandbox"):
        provider_mode = envelope.get("provider_mode")
        if provider_mode != "sandbox":
            raise ArtifactError(
                f"payment/provider scenario requires provider_mode == 'sandbox', got {provider_mode!r} "
                f"-- never live charges"
            )

    if spec.get("requires_model_attempt"):
        ai_context = envelope.get("ai_context", {})
        attempted = ai_context.get("model_attempted_action") if isinstance(ai_context, dict) else None
        if attempted is not True:
            raise ArtifactError(
                "AI/LLM scenario must record ai_context.model_attempted_action=true -- the model must "
                "actually have attempted the forbidden action for an external-layer rejection to be "
                "meaningful evidence; a model that merely refused on its own proves nothing about the "
                "enforcement layer (model refusal alone is never sufficient evidence)"
            )

    if spec.get("requires_inert_payload_marker"):
        marker = envelope.get("payload_marker")
        if marker not in ALLOWED_PAYLOAD_MARKERS:
            raise ArtifactError(
                f"browser scenario must declare payload_marker as one of {sorted(ALLOWED_PAYLOAD_MARKERS)} "
                f"(inert, non-exfiltrating probe only), got {marker!r}"
            )

    required_identities = spec.get("required_identities", set())
    if required_identities:
        declared = set(envelope.get("identities", []))
        if len(declared) < len(required_identities) or not required_identities <= declared:
            raise ArtifactError(
                f"scenario requires distinct identities {sorted(required_identities)}, "
                f"artifact declares {sorted(declared)}"
            )
