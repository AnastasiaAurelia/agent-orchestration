# Diana Security Track

The Security Track is an **optional** track that starts only after the main
Diana roadmap (Phases 0-11, plus the AO-approval-boundary repair) is
complete. It is not a phase of that roadmap and does not redesign Diana's
existing architecture. Diana remains a thin, provider-neutral owner of
policy, workflow, Definition of Done, memory, governance/risk, and
deterministic quality/security orchestration -- not a security scanner
implementation, browser engine, model router, worktree manager, hosted
cloud service, provider SDK, or pentesting engine.

**Security Phase 0 was catalog/specification only.** It answered "what
security controls may Diana need to prove?", not "does this repository pass
those controls?". No scanner integration, no Diana Gate schema change, no
dynamic testing, and no external security tool installation happened in that
phase.

**Security Phase 1 adds the result/evidence model.** It defines *how* a
control's `required_evidence` contract maps to an actual result, and
computes that result deterministically from a caller-supplied verification
run. It still does not run, install, or call any scanner, static analyzer,
dynamic test, or LLM reviewer -- those are later phases. Phase 1 only
defines and computes the contract that those future verifiers will produce
evidence against.

## What's here

- `catalog.json` -- 75 vulnerability-class controls (`SEC-001`..`SEC-075`),
  extracted from the handbook *75 Common Vibe-Coded Web App Vulnerabilities*.
- `validate_catalog.py` -- a deterministic, Python-stdlib-only structural
  validator for `catalog.json`. No network, no LLM, no security-tool
  invocation.
- `test-catalog.sh` -- a fixture-free regression suite (CASE A-J) that
  proves the validator both accepts the real catalog and fails closed on
  mutated copies of it.
- `evidence_model.py` -- a deterministic, Python-stdlib-only evaluator that
  takes the catalog plus a JSON array of "verification run" records and
  computes one `PASS`/`FAIL`/`NOT_APPLICABLE`/`UNPROVEN`/`ERROR` result per
  run against that control's `required_evidence` contract. No network, no
  LLM, no security-tool invocation, no wall-clock reads.
- `test-evidence-model.sh` -- a fixture-free regression suite (CASE 1-13,
  with CASE 6 split into three malformed-evidence sub-cases: invalid
  status enum, unknown control id, and an evidence item whose requirement
  text isn't part of that control's contract) proving every result state
  and that a malformed run inside a batch fails closed to `ERROR` without
  blocking reporting on the other, well-formed runs in that same batch.

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

`evidence_model.py` implements exactly five result states:

```
PASS | FAIL | NOT_APPLICABLE | UNPROVEN | ERROR
```

A control becomes `PASS` **only** when every one of its catalog
`required_evidence` items is present in the run and marked `SATISFIED`.
Anything short of that -- no evidence at all, some but not all items
present, an unrecognized control, a malformed run, applicability that
hasn't been established -- fails closed to `UNPROVEN` or `ERROR`, never
`PASS`. `FAIL` requires positive evidence: at least one required item
explicitly marked `VIOLATED`. This is enforced in code
(`evaluate_run()`), not just documented, and `test-evidence-model.sh`
proves it for every state plus three distinct malformed-input shapes.

### What Phase 1 does not do

- It does not run, install, or invoke any static analyzer, dependency
  scanner, secret scanner, dynamic tester, or LLM reviewer. There is
  nothing yet that *produces* a verification run for a real repository --
  `evidence_model.py` only evaluates one once it exists.
- It does not touch `diana/gate` or `diana/preflight`. Security evidence
  does not yet feed into any merge decision -- that is Security Phase 5.
- It does not compute or store applicability automatically. A run's
  `applicability` (`APPLICABLE`/`NOT_APPLICABLE`/`UNKNOWN`) is still an
  input the caller supplies, not something this phase derives from a
  control's `applicability.signals`.
- It does not read the clock, network, or filesystem beyond the two input
  files it is given. An optional `observed_at` string on a run is passed
  through unchanged for future storage/display use; it plays no role in
  computing the result, which keeps the whole model deterministic and
  testable without freezing time.

### Evidence-run schema (informal)

```jsonc
{
  "control_id": "SEC-001",                 // must be a real SEC-NNN id
  "applicability": "APPLICABLE",            // APPLICABLE | NOT_APPLICABLE | UNKNOWN
  "verifier": {
    "type": "DYNAMIC_API",                  // must be in catalog.json's verifier_types enum
    "identity": "pytest::test_bola_cross_account"
  },
  "evidence": [
    {
      "requirement": "<must exactly match one of the control's catalog required_evidence strings>",
      "status": "SATISFIED",                // SATISFIED | VIOLATED
      "provenance": "negative test tests/test_bola.py::test_cross_account_denied",
      "detail": "optional free text"
    }
  ],
  "tool_error": null,                       // or {"message": "..."} -> forces result ERROR
  "observed_at": null                       // optional opaque string, never read by the logic
}
```

An evidence item's `requirement` must be one of the exact strings in that
control's `catalog.json` `required_evidence` array -- this deliberately
ties every PASS/FAIL determination back to the Phase 0 catalog contract
rather than to free-form claims a verifier could invent.

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

## What this track does not yet claim

- Diana cannot yet detect or prove any of these 75 controls against a real
  repository end to end. Phase 0 is a catalog; Phase 1 is a result
  calculator that something else must feed. Neither is an implementation
  that inspects real code.
- No security tools have been installed in either phase.
- `diana/gate` and `diana/preflight` are completely unchanged -- their
  schemas, behavior, and test suites are untouched by this track so far.

## Future phases (not started here)

- **Security Phase 2 -- Static Security Adapters**: thin adapters that
  normalize real static analyzer/secret-scanner/dependency-scanner output
  into Phase 1 evidence runs.
- **Security Phase 3 -- Dynamic Verification**: bounded runtime tests
  (authorization, injection, webhook, race-condition, payment-sandbox,
  etc.) that also produce Phase 1 evidence runs.
- **Security Phase 4 -- Semantic Security Reviewer**: a fresh, read-only
  reviewer for controls that can't be settled by static/dynamic tooling
  alone.
- **Security Phase 5 -- Security Gate + CI**: feed Phase 1 results into
  Diana's merge decision without collapsing the existing Preflight/Gate
  separation.
- **Security Phase 6 -- Prove 75/75 Coverage**: an executable coverage
  matrix showing every catalog control has a defined, testable
  verification path.
- This README does not promise the exact shape of that work ahead of each
  phase actually landing.

## Running the validator and tests

```
python3 diana/security/validate_catalog.py diana/security/catalog.json
bash diana/security/test-catalog.sh
bash diana/security/test-evidence-model.sh
```

All of the above are deterministic, offline, and make no changes to this
repository. `evidence_model.py` itself takes two arguments (a catalog path
and a runs-file path) and is normally invoked directly for ad hoc checks:

```
python3 diana/security/evidence_model.py diana/security/catalog.json <runs.json>
```
