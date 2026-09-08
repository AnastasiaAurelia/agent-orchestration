"""Diana Security dynamic-verification scenario registry (Security Phase 3).

Each entry maps a `scenario.id` (declared inside a scenario evidence
artifact -- see `dynamic_base.py`) to exactly ONE `(control_id,
requirement)` pair from the Phase 0 catalog it is designed to establish,
plus the safety/authorization constraints `dynamic_normalizer.py` enforces
before computing a result. This is the dynamic-verification equivalent of
Phase 2's per-adapter `AUTHORIZED_EVIDENCE` mapping: a scenario cannot
establish evidence for any `(control_id, requirement)` pair outside its
own registry entry, and an artifact's self-reported `result.control_id`/
`result.requirement` are checked against this registry, never trusted
verbatim.

Each entry covers the *dynamic* required_evidence item for its control
(each of these 17 spot-check controls' catalog entry lists exactly one
requirement that is inherently a runtime/negative-test claim). The
control's other required_evidence item (typically a static/semantic
code-fact claim) is left to a different verifier capability -- Phase 2's
static adapters or a future semantic reviewer -- exactly mirroring how
Phase 2's Gitleaks adapter only ever covered part of SEC-007's contract.
This scoping is deliberate: a thin, uniform, table-driven normalizer
handling 17 controls consistently, not 17 bespoke scanner implementations.

Field meanings:

- `control_id` / `requirement`: source-derived-linked authorization target
  (must equal one of the control's real `catalog.json` `required_evidence`
  strings).
- `allowed_verifier_modes`: the artifact's declared `verifier_mode` must
  be one of these (a subset of the control's real catalog
  `verification.modes`, restricted to the dynamic-capable ones).
- `required_assertions`: assertion ids that must all be present with
  outcome `PASSED` for `SATISFIED`; any present with outcome `FAILED`
  (subject to target-identity verification) produces `VIOLATED`; any
  missing produces `UNPROVEN`.
- `required_identities`: logical role labels (from
  `dynamic_base.ALLOWED_IDENTITY_LABELS`) the scenario must declare using,
  checked for distinctness where the scenario is inherently a comparison
  between two identities (e.g. cross-account tests).
- Safety flags (`max_concurrency`, `forbid_privileged_bypass`,
  `requires_provider_sandbox`, `requires_model_attempt`,
  `requires_inert_payload_marker`): see
  `dynamic_base.validate_scenario_safety_invariants()`.
"""

from __future__ import annotations

SCENARIO_REGISTRY: dict[str, dict] = {
    "bola-cross-account-denied": {
        "control_id": "SEC-001",
        "requirement": "cross-account negative access test (user A cannot access user B's object by id)",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["cross_account_access_denied", "protected_state_unchanged"],
        "required_identities": {"USER_A", "USER_B"},
    },
    "privileged-action-denied-for-normal-user": {
        "control_id": "SEC-002",
        "requirement": "negative test proving a lower-privilege account is rejected from a privileged action",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["privileged_action_denied"],
        "required_identities": {"USER_A"},
    },
    "anonymous-protected-endpoint-denied": {
        "control_id": "SEC-003",
        "requirement": "unauthenticated request to a sensitive endpoint is rejected",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["anonymous_request_denied"],
        "required_identities": {"ANONYMOUS"},
    },
    "sql-injection-payload-no-effect": {
        "control_id": "SEC-008",
        "requirement": "negative test proving an injection payload does not alter query behavior",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["injection_payload_no_effect"],
        "required_identities": set(),
    },
    "stored-xss-payload-inert": {
        "control_id": "SEC-012",
        "requirement": "negative test proving a stored script payload does not execute in another user's session",
        "allowed_verifier_modes": {"DYNAMIC_BROWSER"},
        "required_assertions": ["stored_payload_not_executed"],
        "required_identities": {"USER_A", "USER_B"},
        "requires_inert_payload_marker": True,
    },
    "cross-origin-forged-request-rejected": {
        "control_id": "SEC-015",
        "requirement": "negative test proving a cross-origin forged request is rejected",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["forged_request_rejected"],
        "required_identities": {"USER_A"},
    },
    "executable-upload-rejected-or-inert": {
        "control_id": "SEC-038",
        "requirement": "negative test proving an executable/script file upload is rejected or rendered inert",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["executable_upload_rejected_or_inert"],
        "required_identities": {"USER_A"},
    },
    "workflow-abuse-sequence-blocked": {
        "control_id": "SEC-043",
        "requirement": "test proving an out-of-order or repeated abuse of the workflow does not bypass its business rule",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["abuse_sequence_blocked"],
        "required_identities": {"USER_A"},
    },
    "concurrent-one-time-action-invariant": {
        "control_id": "SEC-044",
        "requirement": "concurrent-request test proving repeated simultaneous submissions cannot bypass a one-time/limited-use constraint",
        "allowed_verifier_modes": {"DYNAMIC_CONCURRENCY"},
        "required_assertions": ["concurrent_invariant_held"],
        "required_identities": {"USER_A"},
        "max_concurrency": 5,
    },
    "cross-user-db-session-denied": {
        "control_id": "SEC-066",
        "requirement": "runtime test with multiple identities proving one user's database session cannot read/write another user's rows",
        "allowed_verifier_modes": {"DYNAMIC_DB"},
        "required_assertions": ["cross_user_db_access_denied"],
        "required_identities": {"USER_A", "USER_B"},
        "forbid_privileged_bypass": True,
    },
    "cross-tenant-access-denied": {
        "control_id": "SEC-067",
        "requirement": "cross-tenant negative access test proving tenant A cannot read/write tenant B's data",
        "allowed_verifier_modes": {"DYNAMIC_DB", "DYNAMIC_API"},
        "required_assertions": ["cross_tenant_access_denied"],
        "required_identities": {"TENANT_A", "TENANT_B"},
        "forbid_privileged_bypass": True,
    },
    "invalid-webhook-signature-rejected": {
        "control_id": "SEC-068",
        "requirement": "negative test proving a request with a missing/invalid signature is rejected",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["invalid_signature_rejected"],
        "required_identities": set(),
    },
    "duplicate-webhook-no-duplicate-effect": {
        "control_id": "SEC-069",
        "requirement": "replay test proving the same webhook event delivered twice does not double-apply its effect",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["replayed_event_no_duplicate_effect"],
        "required_identities": set(),
    },
    "tampered-price-rejected": {
        "control_id": "SEC-070",
        "requirement": "negative test in a payment provider sandbox proving a tampered client-side amount does not change the amount actually charged",
        "allowed_verifier_modes": {"PROVIDER_SANDBOX", "DYNAMIC_API"},
        "required_assertions": ["tampered_amount_rejected"],
        "required_identities": {"PROVIDER_TEST_ACCOUNT"},
        "requires_provider_sandbox": True,
    },
    "forged-payment-event-no-entitlement": {
        "control_id": "SEC-071",
        "requirement": "negative test in a payment provider sandbox proving a forged/failed-payment event does not grant entitlement",
        "allowed_verifier_modes": {"PROVIDER_SANDBOX", "DYNAMIC_API"},
        "required_assertions": ["forged_event_no_entitlement"],
        "required_identities": {"PROVIDER_TEST_ACCOUNT"},
        "requires_provider_sandbox": True,
    },
    "unauthorized-ai-tool-call-rejected": {
        "control_id": "SEC-074",
        "requirement": "negative test proving a tool call the model attempts outside the caller's authorization is rejected by the enforcement layer, not merely discouraged by a prompt",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["external_enforcement_rejected_call"],
        "required_identities": {"USER_A"},
        "requires_model_attempt": True,
    },
    "prompt-injection-no-unauthorized-tool-call": {
        "control_id": "SEC-075",
        "requirement": "negative test proving injected instructions embedded in untrusted content do not result in an unauthorized tool call",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["injected_instructions_no_unauthorized_call"],
        "required_identities": {"USER_A"},
        "requires_model_attempt": True,
    },
    # -- Security Track remediation round A additions -------------------
    "shell-metacharacter-payload-inert": {
        "control_id": "SEC-010",
        "requirement": "negative test proving a shell metacharacter payload does not execute unintended commands",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["shell_metacharacter_payload_no_effect"],
        "required_identities": set(),
    },
    "template-expression-payload-not-evaluated": {
        "control_id": "SEC-011",
        "requirement": "negative test proving a template-expression payload is not evaluated",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["template_expression_payload_not_evaluated"],
        "required_identities": set(),
    },
    "forged-none-algorithm-jwt-rejected": {
        "control_id": "SEC-021",
        "requirement": "negative test proving a token with a forged/none-algorithm signature is rejected",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["forged_jwt_rejected"],
        "required_identities": set(),
    },
    "ssrf-internal-address-request-rejected": {
        "control_id": "SEC-035",
        "requirement": "negative test proving a request targeting an internal address is rejected",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["internal_address_request_rejected"],
        "required_identities": set(),
    },
    "crafted-deserialization-payload-inert": {
        "control_id": "SEC-058",
        "requirement": "negative test proving a crafted serialized payload does not achieve code execution or object injection",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["crafted_payload_no_code_execution"],
        "required_identities": set(),
    },
    "sensitive-file-paths-not-fetchable": {
        "control_id": "SEC-064",
        "requirement": "negative test proving common sensitive file paths (.env, .git/config, backup archives) are not publicly fetchable",
        "allowed_verifier_modes": {"DYNAMIC_API"},
        "required_assertions": ["sensitive_file_paths_not_fetchable"],
        "required_identities": set(),
    },
}
