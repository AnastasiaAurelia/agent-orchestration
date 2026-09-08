# Diana Security dynamic verification (Security Phase 3)

A thin, deterministic normalizer that turns an already-produced **dynamic
scenario evidence artifact** into
[`evidence_model.py`](../evidence_model.py) run records. This module never
sends an HTTP request, drives a browser, queries a database, or calls a
payment provider -- an external runtime scenario runner does that, outside
this repository, and hands back a JSON artifact recording what it did and
observed:

```
approved local/test/sandbox target
        |
external/runtime scenario runner        <- NOT this repository
        |
bound dynamic evidence artifact          <- what dynamic_normalizer.py parses
        |
thin Diana normalization (this module)
        |
evidence_model.py
```

Not a monolithic dynamic security engine: one small envelope schema, one
table-driven scenario registry, one normalizer. Reuses
[`diana/security/adapters/adapter_base.py`](../adapters/adapter_base.py)'s
`build_runs()`/`tool_error_runs()`/`tool_unavailable_runs()`/
`NotAuthorized` directly -- those functions only construct
evidence_model.py output records, so this is genuine reuse of Phase 2
code, not duplication. Reuses [`validate_catalog.py`](../validate_catalog.py)'s
`DYNAMIC_VERIFIER_TYPES` enum rather than re-declaring it. Reuses the
existing `diana/playwright/` capability for browser-driven scenarios (that
capability is not modified by this phase).

## Carrying the Phase 2 lesson forward

**"Evidence for one target/build must not prove another."** Every artifact
declares a `target` (repository, commit, base_url); callers supply an
*expected* target the same way Phase 2 adapters do, and
`verify_scenario_identity()`/`verify_scenario_target()` mirror Phase 2's
`verify_identity()`/`verify_target()` split: a scenario that observed a
real violation (a `FAILED` required assertion) is trusted from partial
coverage but must still be attributed to the correct repository+commit; a
scenario where everything held (all required assertions `PASSED`)
additionally needs the full target/scope proof before it counts as
`SATISFIED`.

## Environment classification is explicit, never inferred

`dynamic_base.ALLOWED_ENVIRONMENTS = {"LOCAL", "TEST", "SANDBOX"}`.
Anything else -- `PROD`, `PRODUCTION`, `STAGING`, a typo -- is refused
outright (`ArtifactError` -> `ERROR`). There is no "probably staging"
heuristic.

## Identities are bounded role labels, never credentials

`dynamic_base.ALLOWED_IDENTITY_LABELS` is a small, closed set (`ANONYMOUS`,
`USER_A`, `USER_B`, `USER_C`, `TENANT_A`, `TENANT_B`,
`PRIVILEGED_TEST_USER`, `PROVIDER_TEST_ACCOUNT`). Runtime credentials
(passwords, cookies, session tokens, API keys) are injected by the
external scenario runner and are structurally rejected by this module --
an identities list containing anything outside this enum fails closed.

## Scenario authorization mirrors Phase 2's AUTHORIZED_EVIDENCE

[`scenarios.py`](scenarios.py)'s `SCENARIO_REGISTRY` maps a `scenario.id`
to exactly one `(control_id, requirement)` pair, plus the assertions/
identities/verifier-mode/safety constraints it requires. An artifact's
self-reported `result.control_id`/`result.requirement` are checked against
the registry, never trusted verbatim -- and the `SATISFIED`/`VIOLATED`
status is *computed* by `dynamic_normalizer.evaluate_scenario()` from the
raw `assertions` outcomes, never read directly from the artifact. An
artifact can only report what happened, not its own conclusion.

## Scope: 17 controls, the dynamic half of each contract

Each of the 17 registered scenarios covers only the *dynamic* required_
evidence item of its control -- every one of these controls' catalog
entries has exactly one requirement that is inherently a runtime/
negative-test claim, and one that is typically a static/semantic code-fact
claim. This mirrors Phase 2's Gitleaks adapter, which only ever covered
part of SEC-007's contract: a thin, uniform, table-driven normalizer
handling 17 controls consistently is preferred over 17 bespoke
implementations, and a single scenario's evidence composes with a
different verifier's contribution for the other requirement through
`evidence_model.py`'s existing multi-run aggregation -- no new machinery
needed. `test-dynamic.sh`'s PASS-expecting cases demonstrate this
composition explicitly with a synthetic companion contribution, the same
way Phase 2's Gitleaks+static-analyzer test did for SEC-007.

Registered scenarios (`scenario.id` -> control):

| scenario.id | control | verifier_mode(s) |
|---|---|---|
| `bola-cross-account-denied` | SEC-001 | DYNAMIC_API |
| `privileged-action-denied-for-normal-user` | SEC-002 | DYNAMIC_API |
| `anonymous-protected-endpoint-denied` | SEC-003 | DYNAMIC_API |
| `sql-injection-payload-no-effect` | SEC-008 | DYNAMIC_API |
| `stored-xss-payload-inert` | SEC-012 | DYNAMIC_BROWSER |
| `cross-origin-forged-request-rejected` | SEC-015 | DYNAMIC_API |
| `executable-upload-rejected-or-inert` | SEC-038 | DYNAMIC_API |
| `workflow-abuse-sequence-blocked` | SEC-043 | DYNAMIC_API |
| `concurrent-one-time-action-invariant` | SEC-044 | DYNAMIC_CONCURRENCY |
| `cross-user-db-session-denied` | SEC-066 | DYNAMIC_DB |
| `cross-tenant-access-denied` | SEC-067 | DYNAMIC_DB, DYNAMIC_API |
| `invalid-webhook-signature-rejected` | SEC-068 | DYNAMIC_API |
| `duplicate-webhook-no-duplicate-effect` | SEC-069 | DYNAMIC_API |
| `tampered-price-rejected` | SEC-070 | PROVIDER_SANDBOX, DYNAMIC_API |
| `forged-payment-event-no-entitlement` | SEC-071 | PROVIDER_SANDBOX, DYNAMIC_API |
| `unauthorized-ai-tool-call-rejected` | SEC-074 | DYNAMIC_API |
| `prompt-injection-no-unauthorized-tool-call` | SEC-075 | DYNAMIC_API |

Most of the catalog's 75 controls still have no scenario at all -- this is
not claimed as broader coverage than it is.

## Safety invariants enforced structurally, not by convention

- **Concurrency is bounded, never load testing.** `concurrency.
  simultaneous_requests` must be an integer in `[2, 5]`
  (`dynamic_base.MAX_CONCURRENCY = 5`); anything higher is rejected.
- **No privileged/admin bypass for negative DB access tests.** A DB
  scenario declaring `used_privileged_bypass: true` is rejected -- a
  negative access test that used elevated credentials to "prove" denial
  proves nothing about the actual access-control boundary.
- **Payment/webhook-sandbox scenarios must declare `provider_mode:
  "sandbox"`.** Anything else (including omission) is rejected -- never
  live charges.
- **AI/LLM scenarios must record that the model actually attempted the
  forbidden action** (`ai_context.model_attempted_action: true`) before an
  external-layer rejection is accepted as evidence. **A model that merely
  refused on its own proves nothing about the enforcement layer** -- this
  is the constitutional guard against treating model refusal as security
  proof, enforced in code, not just policy prose (`test-dynamic.sh` CASE
  Z1 proves an artifact without this flag fails closed).
- **Browser scenarios must use an inert, non-exfiltrating payload marker**
  from a bounded set (`dynamic_base.ALLOWED_PAYLOAD_MARKERS`) -- no real
  external callback domain, no destructive JS.
- **Cross-account/cross-tenant scenarios require genuinely distinct
  identities** -- an artifact claiming a "cross-account" test while
  reusing the same identity for both sides is rejected (`test-dynamic.sh`
  CASE I).

## Artifact integrity

Same pattern as Phase 2's `adapter_base.py`: `artifact_binding.sha256`
covers every bound envelope field (`environment`, `target`, `scenario`,
`verifier_mode`, `identities`, `execution`, `assertions`, `cleanup`,
`result`). Mutating any one without recomputing the hash is detected
(`ArtifactError` -> `ERROR`). This detects corruption/substitution of the
artifact's own declared fields -- it does not authenticate who produced
it; no PKI, signatures, or attestation were added.

## Cleanup visibility

A mutating scenario's `cleanup.success == false` never changes the
computed result, but is always appended to the emitted evidence item's
`provenance` text -- visible on inspection, never a silent separate
failure mode.

## Result semantics (no new states)

| Scenario/artifact state | Result |
|---|---|
| All required assertions PASSED, target verified | `SATISFIED` -> contributes toward `PASS` |
| A required assertion FAILED, target identity verified (coverage may be partial) | `VIOLATED` -> contributes toward `FAIL` |
| A required assertion FAILED, but target identity not verified | not attributed -> `UNPROVEN` |
| A required assertion missing from the artifact | `UNPROVEN` ("skipped assertion") |
| No artifact available | explicit `UNPROVEN` (`tool_unavailable_runs`) |
| Refused environment, execution incomplete, unknown scenario.id, artifact_binding mismatch, or a violated safety invariant | `ERROR` |

## What this phase does not do

- Does not execute anything against a real application -- fixtures are
  synthetic, hand-authored JSON, never a live scan.
- Does not implement another browser engine, HTTP client, database
  driver, or payment SDK.
- Does not touch `diana/gate`, `diana/preflight`, `diana/playwright`, or
  `diana/adapters` (the pre-existing AO adapter).
- Does not change `diana/security/catalog.json`, `validate_catalog.py`,
  `evidence_model.py`, or anything under `diana/security/adapters/`.
- Does not claim broader coverage than the 17 controls above, and does
  not claim to fully prove even those (only their dynamic-evidence half).

## CLI

```
python3 diana/security/dynamic/dynamic_normalizer.py <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]
```

Prints `{"version": 1, "runs": [...]}`; combine with other verifiers'
runs (Phase 2 adapters, a future semantic reviewer) and feed to
`evidence_model.py` directly.

## Tests

`test-dynamic.sh` (32 assertions, offline, synthetic fixtures under
`fixtures/*.json`) covers CASE A-Y (the required set: environment
refusal/acceptance, wrong target, skipped assertion, execution crash,
authorization-denied-with-state-unchanged, unauthorized-action-succeeds,
distinct-identity enforcement, anonymous-endpoint test, browser
hostile-input PASS/FAIL, DB cross-tenant PASS/FAIL, webhook signature/
replay PASS/FAIL, payment tampering/entitlement PASS, bounded-concurrency
PASS/FAIL, sandbox-unavailable, artifact corruption, cleanup visibility,
AI tool authorization, prompt injection) plus CASE Z1-Z5 proving each
safety invariant this framework enforces.
