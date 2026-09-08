# Diana Security Track -- Phase 0: Canonical Control Catalog

The Security Track is an **optional** track that starts only after the main
Diana roadmap (Phases 0-10, plus the AO-approval-boundary repair) is
complete. It is not a phase of that roadmap and does not redesign Diana's
existing architecture. Diana remains a thin, provider-neutral owner of
policy, workflow, Definition of Done, memory, governance/risk, and
deterministic quality/security orchestration -- not a security scanner
implementation, browser engine, model router, worktree manager, hosted
cloud service, provider SDK, or pentesting engine.

**Security Phase 0 is catalog/specification only.** It answers "what
security controls may Diana need to prove?", not "does this repository pass
those controls?". No scanner integration, no Diana Gate schema change, no
dynamic testing, and no external security tool installation happens in this
phase.

## What's here

- `catalog.json` -- 75 vulnerability-class controls (`SEC-001`..`SEC-075`),
  extracted from the handbook *75 Common Vibe-Coded Web App Vulnerabilities*.
- `validate_catalog.py` -- a deterministic, Python-stdlib-only structural
  validator for `catalog.json`. No network, no LLM, no security-tool
  invocation.
- `test-catalog.sh` -- a fixture-free regression suite (CASE A-J) that
  proves the validator both accepts the real catalog and fails closed on
  mutated copies of it.

## Source-derived vs. Diana-designed fields

Every control mixes two kinds of data, and the distinction is load-bearing:

**Source-derived** (taken from the handbook, not reinterpreted to fit
Diana's architecture):

- `source_number`
- `title`
- `severity`
- `source_summary` (`what_it_is`, `usual_cause`, `potential_impact`,
  `prevention`)

**Diana-designed** (Diana's own interpretation of how each source control
could eventually be proven -- the handbook does not specify any of this):

- `slug`
- `applicability` (signals, and an optional `notes` field)
- `verification.modes`, `verification.dynamic_required`,
  `verification.human_judgment_required`
- `required_evidence`

The handbook also contains a "copy-paste LLM prevention prompt" per entry.
Those prompts are source material only -- they were read during extraction
but are deliberately **not** stored in `catalog.json` or anywhere else in
this repository, both to avoid unnecessary duplication of the source
document's text and because **an LLM prompt is not proof of a passing
security control**. A model being told not to have a vulnerability is not
evidence that it doesn't.

## Verifier types are capabilities, not vendors

`catalog.json`'s `verifier_types` enum is a bounded, explicit list of
*capabilities* (`STATIC_ANALYZER`, `DYNAMIC_API`, `DEPENDENCY_SCANNER`,
`PROVIDER_SANDBOX`, `SEMANTIC_REVIEW`, `HUMAN`, etc.), not vendor names.
Nothing in this phase names or installs Semgrep, Gitleaks, Trivy,
osv-scanner, CodeQL, Dependabot, or any other specific tool. A later phase
may build adapters that implement a given capability with a specific tool;
Phase 0 only specifies what capability each control would need.

## Applicability is not yet automated

Each control's `applicability.signals` are concise, human-readable
conditions under which the control is relevant (e.g. "app exposes a
GraphQL server"). Phase 0 does **not** implement executable applicability
detection -- no code in this repository evaluates these signals against a
real project. That is future work, analogous to how `diana/preflight`
already does executable applicability detection for its own (much smaller,
unrelated) check catalog.

## "No finding" is not PASS

This catalog does not implement or claim any result semantics yet. A
future result model is expected to distinguish:

```
PASS | FAIL | NOT_APPLICABLE | UNPROVEN | ERROR
```

A control can only eventually become `PASS` once its `required_evidence`
contract is actually satisfied and checked. Until that result/evidence
model exists (Security Phase 1), the absence of a finding for a given
control means nothing -- it is not a substitute for `PASS`, and nothing in
this repository should be read as claiming otherwise.

## Conservative evidence mapping

Per-control verification mapping follows one governing principle: a static
finding (or a semantic/LLM review) alone is not adequate proof for a
control whose correctness depends on runtime identities, authorization,
concurrency, provider behavior, or business logic. Where that is true, the
control's `verification.modes` includes at least one dynamic verifier type
and `verification.dynamic_required` is `true`. Notably:

- Authentication/authorization controls require negative identity tests,
  not just code review.
- Database-isolation and multi-tenant controls require a runtime test with
  multiple identities/tenants, not just a policy read.
- Payment controls require provider-sandbox evidence of a trusted
  server-side amount/entitlement, not just code inspection.
- Webhook controls require signature-rejection and replay/idempotency
  evidence.
- Race-condition controls require actual concurrent-execution evidence
  (`DYNAMIC_CONCURRENCY`) -- a static read of locking code is not accepted
  as sufficient on its own.
- **AI tool authorization and prompt injection (`SEC-074`, `SEC-075`) are
  constitutional to Diana Security**: model output is untrusted, prompts
  are not a security boundary, and authorization must be enforced by code
  outside the model. Both controls require evidence that an unauthorized
  tool call is actually rejected by an enforcement layer, not merely
  discouraged by a system prompt.

A small number of controls (`SEC-043` Business Logic Abuse, `SEC-048`
Excessive Data Exposure, `SEC-061` Malicious or Compromised Dependency) are
also marked `human_judgment_required: true`, because defining "abuse",
"excessive", or "malicious intent" for a given product is not something a
scanner or a fixed rule can fully settle on its own.

## What this phase does not claim

- Diana cannot yet detect or prove any of these 75 controls end to end.
  This is a catalog, not an implementation.
- No security tools were installed in this phase.
- `diana/gate` and `diana/preflight` are completely unchanged -- their
  schemas, behavior, and test suites are untouched by this phase.

## Future phases (not started here)

- **Security Phase 1 -- Result/Evidence Model**: define how a control's
  `required_evidence` maps to an actual `PASS`/`FAIL`/`NOT_APPLICABLE`/
  `UNPROVEN`/`ERROR` result, and how evidence is captured and stored.
- Later phases are expected to add static-verifier adapters, dynamic
  verification, a semantic reviewer, and eventual Gate/CI integration --
  but this README does not promise the exact shape of that work.

## Running the validator and tests

```
python3 diana/security/validate_catalog.py diana/security/catalog.json
bash diana/security/test-catalog.sh
```

Both are deterministic, offline, and make no changes to this repository.
