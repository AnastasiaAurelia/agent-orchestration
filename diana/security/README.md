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
computes that result deterministically by aggregating every caller-supplied
verification run submitted for a control. It still does not run, install,
or call any scanner, static analyzer, dynamic test, or LLM reviewer --
those are later phases. Phase 1 only defines and computes the contract
that those future verifiers will produce evidence against.

A human review of the first Phase 1 implementation found an integrity gap:
evaluating one run in isolation let a single verifier of *any* capability
-- even one the control's catalog entry doesn't list at all -- manufacture
`PASS` merely by writing `SATISFIED` next to every required_evidence
string. That has been corrected: `evidence_model.py` now aggregates every
run submitted for a control and enforces that each evidence contribution
came from a verifier capability the control actually permits, plus its
specific dynamic/human-judgment requirements. See "Evidence provenance and
multi-verifier aggregation" below for the exact rules.

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
  takes the catalog plus a JSON array of "verification run" records,
  groups them by `control_id`, and computes one aggregate
  `PASS`/`FAIL`/`NOT_APPLICABLE`/`UNPROVEN`/`ERROR` result per control
  against that control's `required_evidence` contract *and* its permitted
  verifier capabilities. No network, no LLM, no security-tool invocation,
  no wall-clock reads.
- `test-evidence-model.sh` -- a fixture-free regression suite (CASE 1-13
  preserved/adapted from the original single-run design, plus CASE A-L
  added by the provenance-integrity correction) proving every result
  state, that an out-of-capability or otherwise-invalid verifier can never
  manufacture `PASS`, and that a problem run for one control never affects
  a different control's result in the same batch.
- `adapters/` -- Security Phase 2's static adapters (Gitleaks, osv-scanner,
  Semgrep): deterministic normalizers that turn a **verified scan-evidence
  artifact** (a tool report bound to a caller-checked target/commit/scope,
  not a bare report) into `evidence_model.py` run records. Each adapter
  declares an explicit authorization mapping so a tool can never
  manufacture `PASS` for a control/requirement it isn't actually capable
  of proving, and a clean result can only become `SATISFIED` when the
  adapter can prove the scan actually covered what the requirement needs
  -- "no finding" from the wrong or incomplete target proves nothing. See
  `adapters/README.md` for the full design and per-adapter scope.
- `dynamic/` -- Security Phase 3's dynamic-verification normalizer:
  consumes an already-produced **dynamic scenario evidence artifact**
  (produced by an external runtime scenario runner -- Diana never sends
  requests, drives a browser, or touches a database itself) and computes
  `evidence_model.py` runs for 17 controls' runtime/negative-test
  requirement, reusing Phase 2's `adapter_base.py` output-construction
  helpers and the same target-identity-vs-coverage split. See
  `dynamic/README.md` for the full design, scenario registry, and safety
  invariants (bounded environments, bounded identity labels, bounded
  concurrency, sandbox-only payments, no privileged DB bypass, no
  model-refusal-as-proof).
- `reviewer/` -- Security Phase 4's semantic-review normalizer: consumes
  an already-produced **semantic review evidence artifact** (produced by
  a fresh, independent, read-only reviewer session -- Diana never runs a
  model or judges anything itself) and computes `evidence_model.py` runs
  for any catalog control whose `verification.modes` actually includes
  `SEMANTIC_REVIEW`/`HUMAN`. Unlike every earlier phase, this normalizer
  trusts the reviewer's own PASS/FAIL/NOT_APPLICABLE verdict -- but only
  once it survives strict structural substantiation gates (no vague
  reasoning, cited files, citations self-consistent with the artifact's
  own declared inspection scope), an integrity binding covering every
  allowed artifact field (not a fixed subset), a rule that any unresolved
  security assumption downgrades a PASS/NOT_APPLICABLE to UNPROVEN, and an
  explicit constitutional guard for `SEC-074`/`SEC-075` (a PASS can never
  rest on model refusal alone). See `reviewer/README.md` for the full
  design, the stated departure from Phase 1-3's "never trust self-reported
  conclusions" principle, and the explicit trust-boundary documentation
  (artifact integrity is not producer authenticity).
- **Security Phase 5 -- Security Gate + CI integration**:
  `security_bundle.py` builds the complete, deterministic 75-control
  result bundle (running `evidence_model.py` and explicitly filling
  `UNPROVEN` for every control with zero submitted runs -- "no run
  submitted" is never silently absent); `security_reducer.py` reduces a
  validated bundle to one `PASS`/`REQUIRE_HUMAN`/`FAIL` decision by
  severity policy; `ci_verifier_runs.py` is the single, explicit (today
  empty) extension point for real trusted verifier execution. A
  **separate** required CI check, `.github/workflows/diana-security-
  gate.yml`, evaluates this using `pull_request_target` -- its workflow
  *definition itself*, not just the scripts it runs, is resolved from the
  protected base, never the PR head, so a PR cannot weaken its own
  current evaluation by editing either the evaluator code or the
  orchestrating workflow. `diana-gate.py` (the ordinary, pre-existing
  Gate) is untouched beyond one narrow `REVIEW_PATHS` addition -- the two
  checks stay fully separate; GitHub branch protection requiring both
  reproduces the intended combination policy. See "Security Phase 5"
  below for the full trust-boundary design, the PR-body-is-untrusted-
  for-security rule, and the post-merge activation requirement.
- **Security Phase 6 -- Prove 75/75 Coverage**: `coverage_matrix.py`
  computes, for every canonical control, two never-conflated facts: the
  REAL live `resulting_state` (today: `UNPROVEN` for all 75, proven by
  actually running the real pipeline, not asserted) and a static
  `capability_coverage` classification (`FULLY_COVERED`/
  `PARTIALLY_COVERED`/`NOT_COVERED`, read directly from Phase 2-4's real
  authorization tables). Current honest finding: 21 `FULLY_COVERED`, 25
  `PARTIALLY_COVERED`, 29 `NOT_COVERED` (including 8 `CRITICAL`-severity
  controls with zero implemented capability). See "Security Phase 6"
  below for the full breakdown and what remains.

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

A control becomes `PASS` **only** when, aggregating every run submitted
for it: applicability resolves to `APPLICABLE`, every one of its catalog
`required_evidence` items is `SATISFIED` by an accepted contribution, that
contribution came from a verifier capability the control actually
permits, its dynamic requirement is met by an accepted dynamic-capability
contribution when `dynamic_required` is true, and its human-judgment
requirement is met by an accepted `HUMAN`/`SEMANTIC_REVIEW` contribution
when `human_judgment_required` is true. Anything short of that -- no
evidence at all, some but not all items present, an unrecognized control,
a malformed run, an out-of-capability verifier, applicability that hasn't
been established -- fails closed to `UNPROVEN` or `ERROR`, never `PASS`.
`FAIL` requires positive evidence: at least one required item explicitly
marked `VIOLATED` by a permitted-capability run. This is enforced in code
(`evaluate()` / `_aggregate_group()`), not just documented, and
`test-evidence-model.sh` proves it for every state, every malformed-input
shape, and -- specifically -- that no out-of-capability or unknown
verifier type can ever produce `PASS` regardless of what its evidence text
claims.

## Evidence provenance and multi-verifier aggregation

**Evidence text alone is not proof.** A `SATISFIED` claim only counts if
the verifier making it is a capability the control's catalog entry
actually lists. `verification.modes` is documented as a **permitted set**
(an evidence-contributing verifier's type must belong to it), not a
checklist where every listed mode must separately contribute -- see the
long design-rationale comment at the top of `evidence_model.py` for why
this is the narrowest interpretation actually supported by how Phase 0
designed the catalog (many controls pair two modes as *alternative*
techniques for the same code-level fact, not as independently mandatory
proofs), and for why "every mode is mandatory" would have silently broken
a legitimate, non-superseded Phase 1 test case (a single comprehensive
`DYNAMIC_API` run fully satisfying `SEC-001`, which lists `SEMANTIC_REVIEW`
as a permitted alternative, not a second mandatory proof).

On top of the permitted-set rule, two capability requirements are enforced
exactly as the catalog schema expresses them:

- `dynamic_required: true` -- at least one accepted `SATISFIED`
  contribution must come from a verifier whose type is one of
  `DYNAMIC_API` / `DYNAMIC_BROWSER` / `DYNAMIC_DB` / `DYNAMIC_CONCURRENCY`
  / `PROVIDER_SANDBOX`.
- `human_judgment_required: true` -- at least one accepted `SATISFIED`
  contribution must come from a verifier whose type is `HUMAN` or
  `SEMANTIC_REVIEW` (the only two catalog verifier types representing a
  human/semantic judgment capability). Every `human_judgment_required`
  control in the current catalog (`SEC-043`, `SEC-048`, `SEC-061`) already
  lists `SEMANTIC_REVIEW` among its permitted modes, so this gate is
  satisfiable catalog-wide today.

Because one run can only carry one verifier identity, and several
controls legitimately need evidence from more than one capability (e.g.
`SEC-001`'s code-level ownership check versus its cross-account dynamic
negative test), `evidence_model.py` aggregates **every run submitted for a
given `control_id`** before deciding a result, rather than evaluating runs
in isolation:

1. Each run is classified `CLEAN`, `MALFORMED` (schema violation, unknown
   control, or an evidence item whose requirement text isn't part of that
   control's real contract), `CAPABILITY_VIOLATION` (schema-valid, but an
   out-of-permitted-set verifier type), or `TOOL_ERROR` (schema-valid,
   permitted, but the run reports a tool/execution failure).
2. Trusted violation evidence from any `CLEAN`, `APPLICABLE` run always
   surfaces as `FAIL`, even alongside other problem runs -- a real finding
   should never be hidden behind an unrelated verifier failure.
3. Otherwise, **any** non-`CLEAN` run for that control forces the whole
   aggregate to `ERROR`. An out-of-capability or broken contribution
   anywhere in the batch makes the result unreliable; it is reported, not
   silently discarded.
4. Otherwise, applicability is resolved across the clean runs (conflicting
   `APPLICABLE`/`NOT_APPLICABLE` calls -> `ERROR`; all-`NOT_APPLICABLE` ->
   `NOT_APPLICABLE`; only `UNKNOWN` -> `UNPROVEN`), and only then are
   `SATISFIED` evidence items from `APPLICABLE` clean runs collected and
   checked against `required_evidence` plus the dynamic/human-judgment
   gates above.

A control with zero submitted runs is `UNPROVEN` ("no verification runs
submitted"), distinct from runs that were attempted and rejected
(`ERROR`).

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

### Evidence-run schema (informal, input)

The input format is unchanged from Phase 1's first draft -- a flat JSON
array of runs, each carrying exactly one verifier's contribution to one
control. Submitting more than one run with the same `control_id` is how a
multi-capability control's evidence is represented; `evidence_model.py`
groups them, not the caller.

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
  "tool_error": null,                       // or {"message": "..."} -> forces this run to TOOL_ERROR
  "observed_at": null                       // optional opaque string, never read by the logic
}
```

An evidence item's `requirement` must be one of the exact strings in that
control's `catalog.json` `required_evidence` array -- this deliberately
ties every PASS/FAIL determination back to the Phase 0 catalog contract
rather than to free-form claims a verifier could invent. As of the
provenance correction, the run's `verifier.type` must *also* be one of
the control's real `verification.modes`, or the run is rejected as a
capability violation (see above) and can never contribute to `PASS`.

### Result schema (informal, output)

`evidence_model.py` prints `{"version": 1, "results": [...]}`, one entry
per distinct `control_id` encountered (in order of first appearance):

```jsonc
{
  "control_id": "SEC-001",
  "result": "PASS",                         // PASS | FAIL | NOT_APPLICABLE | UNPROVEN | ERROR
  "applicability": "APPLICABLE",             // resolved value, or null if undetermined/conflicting
  "reasons": ["all 2 required evidence item(s) satisfied by permitted verifier capabilities: ['DYNAMIC_API', 'SEMANTIC_REVIEW']"],
  "evidence": [ /* flattened SATISFIED/VIOLATED items from every CLEAN run, each tagged with contributed_by + observed_at */ ],
  "run_issues": [ /* {verifier_type, status, reason} for any MALFORMED/CAPABILITY_VIOLATION/TOOL_ERROR run in this control's batch */ ]
}
```

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
  calculator that something else must feed; Phase 2 adds 3 static
  normalizers for a handful of controls; Phase 3 adds 17 dynamic
  scenario registrations, each covering only the runtime half of its
  control's contract; Phase 4 adds a semantic-review normalizer for any
  control whose catalog modes permit `SEMANTIC_REVIEW`/`HUMAN` -- none of
  this inspects a live repository, invokes a scanner, executes a real
  request/browser/DB/payment action, or runs a model itself.
- No security tools, models, or reviewer sessions have been installed or
  run in any phase so far. Phase 2's adapters parse documented tool
  *output formats*; Phase 3's normalizer parses an already-produced
  scenario artifact; Phase 4's normalizer parses an already-produced
  review artifact. None runs, ships, or depends on Gitleaks/osv-scanner/
  Semgrep/Trivy/a browser/a database/a payment provider/an LLM being
  present anywhere in this repository.
- `diana/preflight`, `diana/playwright`, and `diana/adapters` (the
  pre-existing AO adapter) remain completely unchanged by this track.
  `diana/gate/diana-gate.py` gained only one narrow, additive `REVIEW_PATHS`
  entry set (Security Phase 5); its `evaluate()` function, input schema,
  and every pre-existing behavior/test are byte-for-byte unchanged -- see
  "Security Phase 5" below. Security evidence is evaluated by a wholly
  separate CI check (`diana-security-gate.yml`), never by code inside
  `diana-gate.py` itself.
- Phase 2 covers 3 verifier capabilities (`SECRET_SCANNER`,
  `DEPENDENCY_SCANNER`, `STATIC_ANALYZER`) for a handful of controls
  (`SEC-007`; `SEC-060`; `SEC-055`/`SEC-056`). Phase 3 covers the dynamic
  half of 17 controls' contracts. Phase 4 can be invoked for any
  catalog-authorized control, but its correctness depends entirely on the
  quality of a real reviewer session this repository doesn't run --
  passing a substantiation bar is not the same as being right. Most of
  the catalog's 75 controls still have no verifier at all, and
  applicability automation doesn't exist yet. **Phase 5 does not change
  any of this**: `ci_verifier_runs.py` currently always returns zero
  trusted runs (no live verifier execution is wired into CI), so every
  catalog control is honestly `UNPROVEN` and the Security Gate honestly
  returns `REQUIRE_HUMAN` for every PR today -- see "Security Phase 5"
  below for why that is the correct, intended state, not a bug.

## Security Phase 5 -- Security Gate + CI integration

Integrates the Security Track's results into Diana's merge-boundary
decision, without collapsing the existing Preflight/Gate separation, as
a **separate, independently required CI check**:

```
trusted verifier execution (ci_verifier_runs.py -- today: none, so [])
        |
normalized Phase 1 runs
        |
evidence_model.py
        |
complete security result bundle (security_bundle.py)
        |
deterministic security reducer (security_reducer.py)
        |
required "Diana Security Gate" check (map-gate-result.py, unmodified)
        |                                    (runs alongside, not through,
human review  <-------------------------------  the unmodified "Diana Gate" check)
        |
merge
```

Preserved throughout: **no finding != PASS**, **missing evidence !=
PASS**, **UNPROVEN != PASS**, **ERROR != PASS**. Human review remains the
final merge authority -- Security Phase 5 can force `REQUIRE_HUMAN` or
`FAIL` on its own check, but never bypasses or replaces the required
human/code-owner review rule.

### The trust root is the WORKFLOW, not just the evaluator code

A first version of this phase ran the trusted evaluator scripts via
`git archive`-based extraction from a PR's protected base SHA, but did so
*inside a step of the ordinary, plain-`pull_request`-triggered*
`diana-gate.yml`. That protected the evaluator **scripts** from
PR-head tampering, but not the **workflow orchestration itself** -- for a
plain `pull_request` trigger, GitHub resolves and runs the workflow
*file* from the PR head, so a malicious PR could simply delete or edit
the step that invoked the extraction, with nothing to stop it. That is
not a sound trust root.

The corrected design uses a **separate** workflow,
`.github/workflows/diana-security-gate.yml`, triggered by
`pull_request_target` instead of `pull_request`. For `pull_request_target`,
GitHub resolves and runs the **workflow definition itself** from the
repository's default branch, never the PR head -- a PR that edits this
workflow file has zero effect on what actually executes against it. Its
`actions/checkout` step deliberately specifies no `ref:` override, so it
also defaults to the base branch's tip (never the PR head, never a merge
commit): the entire checked-out tree, including
`diana/security/{ci_verifier_runs.py, security_bundle.py,
security_reducer.py}`, is already the protected base -- no extraction
step is needed, those scripts run directly from that checkout. The
workflow declares `permissions: contents: read` (no write scopes) and
references no `secrets.*` anywhere; it never checks out, fetches, or
executes anything from the PR head ref (`test-security-gate.sh` W1-W6
check these properties structurally).

`diana/ci/run-security-gate.py` (the original git-archive-extraction
script) remains in the repository, but is **not** what the live CI check
invokes -- it is kept as a local/offline dry-run tool and as the
reference implementation `test-security-gate.sh`'s S2+S3 test uses to
prove the general "protected base beats PR head" property against a real
ephemeral git repository (a technique still useful for other trigger
types that don't hand you a base-rooted checkout for free). See its own
module docstring for the full explanation.

### Security evidence must not come from the PR body

The existing `DIANA:EVIDENCE` PR-body HTML-comment block
(`diana/ci/build-gate-input.py`, consumed only by the separate, ordinary
"Diana Gate" check) stays scoped to exactly its five pre-existing fields
(`dod`, `verification`, `preflight`, `risk`, `human_only_conditions`) --
unchanged by this phase. A PR body claiming `"SEC-001": "PASS"` or
similar has zero authority over the Security reducer: the strict
`set(value) != EVIDENCE_FIELDS` check rejects any extra field outright
(proven by `test-security-gate.sh` T1). The real Security evidence
pipeline (`ci_verifier_runs.py`) takes no arguments and reads no
PR-supplied content at all -- not the PR body, not an arbitrary
checked-in JSON file, even one with a structurally valid Phase 2/3/4
`artifact_binding` hash (proven by T2: a fake, correctly-hashed Phase 4
semantic-review artifact planted in the working tree does not change
`ci_verifier_runs.py`'s output). `artifact_binding` proves internal
artifact integrity only, never who produced an artifact or that it came
through a genuine CI-controlled verifier path (Phase 2/3/4's own stated
limitation, not erased here). The Security Gate workflow does not even
parse the PR body at all -- there is no code path in it that could read a
security claim out of it.

### Producer trust: why every control is UNPROVEN today

`ci_verifier_runs.py` always returns `[]`. No live static analyzer,
dependency scanner, secret scanner, dynamic scenario runner, or semantic
reviewer session is wired into CI yet -- Phase 2/3/4 built normalizers
for already-produced evidence artifacts, not live execution. This means
`security_bundle.build_bundle()` marks all 75 controls `UNPROVEN`, and
`security_reducer.py` maps `UNPROVEN` to `REQUIRE_HUMAN` -- so the
Security Gate currently returns `REQUIRE_HUMAN` for every PR. **This is
the correct, honest, fail-closed state**, not a placeholder bug: Diana
has not yet wired any trusted verifier into CI, so it correctly refuses
to claim any control is proven. `ci_verifier_runs.py` is the single,
explicit extension point for later wiring in real trusted verifier
execution; nothing else in this pipeline needs to change when that
happens (proven by T3/T4/T5).

### Preventing security self-certification

A PR may modify `diana/security/*`, `diana/gate/*`, `diana/ci/*`, or
`.github/workflows/*` -- those changes must never be able to weaken their
own current evaluation. As described above, `pull_request_target`
resolves BOTH the workflow definition AND (via the default checkout) the
evaluator code from the protected base -- a PR that weakens a control's
severity in `catalog.json`, or replaces `security_reducer.py` with one
that always returns `PASS`, has zero effect on its own evaluation. The
underlying "protected base wins over PR head" extraction property is
additionally proven end-to-end against a real, ephemeral git repository
by `test-security-gate.sh`'s S2+S3 test (using the reference
`run-security-gate.py` tool, since a live `pull_request_target` run can't
be simulated locally).

**Bootstrap limitation, stated explicitly**: `pull_request_target`
workflows only run when the workflow FILE already exists on the default
branch. `diana-security-gate.yml` is introduced by this very PR, so it
does not exist on the default branch yet, and **does not fire at all**
against this PR -- there is no `SKIPPED_BOOTSTRAP` decision to combine
with anything, because there is no run at all. **This PR does not claim
Security Phase 5 retroactively protects itself** -- it remains protected
only by the pre-existing Diana Gate (DoD/verification/Preflight/
diff-risk/human_only_conditions) plus the required human/code-owner
review, exactly as before this phase existed. After this PR merges, the
**next** pull request opened against this repository is the first real
opportunity to confirm the workflow actually fires and produces a
sensible result -- **Security Phase 6 must not begin until that
post-merge activation has been observed** (a probe: open or push to any
subsequent PR and confirm the "Diana Security Gate" check appears and
completes).

### Catalog authority

Severity/policy decisions use the protected-base canonical
`catalog.json`, never a PR-head catalog (guaranteed by the same
`pull_request_target` checkout described above, not by any manual
extraction). A `security_bundle.py` bundle binds `catalog_version` (the
catalog's own version integer) AND `catalog_sha256` (a SHA-256
fingerprint of the whole trusted catalog content) -- either mismatching
the trusted catalog fails the bundle closed (`security_bundle.
validate_bundle`), so a bundle generated against one catalog content can
never silently validate against a different one. A PR that changes
`diana/security/catalog.json` is itself forced to `REQUIRE_HUMAN` by the
Gate's existing sensitive-path mechanism (see "Security-control changes
require human review" below) and cannot use its own changed catalog to
weaken its own current evaluation; the new catalog only becomes
authoritative for evaluations run against a base that includes it, i.e.
after human merge.

### Security result bundle

`security_bundle.build_bundle()` produces exactly one aggregate result
for every canonical control `SEC-001`..`SEC-075` (never an arbitrary
self-authored status string): it runs `evidence_model.evaluate()` over
the supplied runs, then explicitly fills `UNPROVEN` (`"no verification
runs submitted for this control"`) for every canonical control that
received zero runs, so a zero-run control is never silently absent from
the bundle. Each bundle carries `version`, `repository`, `base_sha`,
`target_sha`, `catalog_version`, `catalog_sha256`, `generated_count`
(the number of raw runs consumed), and `results` (75 entries, each with
`control_id`, `severity`, `result`, `applicability`, `reasons`,
`evidence`, `run_issues`).

`security_bundle.validate_bundle()` fails closed on: malformed bundle
(wrong/missing top-level or per-result fields), wrong repository, wrong
target/head SHA, wrong base SHA, wrong catalog version/hash, unknown
control, duplicate control, missing canonical control, invalid result
state, and an "impossible" per-control severity mismatch against the
trusted catalog (proven by `test-security-gate.sh` I1-I8 plus a bonus
severity-mismatch case).

### Security reducer policy

`security_reducer.reduce_bundle()` (called only on an already-validated
bundle):

| Severity | Result | Decision |
|---|---|---|
| CRITICAL | FAIL | `FAIL` |
| HIGH | FAIL | `FAIL` |
| MEDIUM | FAIL | `REQUIRE_HUMAN` |
| LOW | FAIL | `REQUIRE_HUMAN` (never silently clean) |
| any | UNPROVEN | `REQUIRE_HUMAN` |
| any | verifier ERROR | `REQUIRE_HUMAN` |
| any | NOT_APPLICABLE | no penalty |
| any | PASS | no penalty |

The final decision is the worst decision implied by any single control
(`PASS < REQUIRE_HUMAN < FAIL`) -- never averaged, never majority-voted; a
trusted `FAIL` is never suppressed by other controls' `PASS`/
`NOT_APPLICABLE`/errored/unavailable state (proven G1-G9 plus a dedicated
"FAIL not hidden" case). A **malformed/mismatched bundle** (the input
itself is untrustworthy) is a categorically different, worse failure than
a **per-control verifier `ERROR`** (some evidence exists but can't be
trusted) -- `security_reducer.evaluate()` maps the former to `FAIL`
(via `validate_bundle` raising `BundleError`) and the latter to
`REQUIRE_HUMAN` (via the table above); `reduce_bundle()` itself is only
ever called on an already-validated bundle and never needs to make this
distinction internally.

### Security-control changes require human review

`diana-gate.py`'s existing, unmodified sensitive-path mechanism
(`REVIEW_PATHS`/`REVIEW_PREFIXES` -> forces `REQUIRE_HUMAN` regardless of
self-declared `risk`/`human_only_conditions`) now also lists
`diana/security/catalog.json`, `diana/security/evidence_model.py`,
`diana/security/security_bundle.py`, `diana/security/security_reducer.py`,
`diana/security/ci_verifier_runs.py`, `diana/gate/diana-gate.py`,
`diana/ci/build-gate-input.py`, `diana/ci/run-security-gate.py`, and
`diana/ci/map-gate-result.py` (`.github/workflows/` was already a
sensitive prefix, so `diana-security-gate.yml` is already covered). This
reuses the existing, already-tested mechanism rather than inventing a
second one -- proven by `test-security-gate.sh` S1/S1b.

### Separation from the existing Diana Gate (no in-process combination)

`diana-gate.py`'s `evaluate()` function, its 6-field input schema, and
its whole pre-existing CLI behavior are **completely unchanged** -- every
pre-existing test (`test-gate.sh`, `test-gate-integration.sh`,
`test-ship.sh`) passes unmodified, and `diana-gate.yml` itself is
byte-for-byte unchanged from before this phase. There is deliberately
**no in-process combination**: "Diana Gate" and "Diana Security Gate" are
two separate, independently required GitHub status checks. Both map
their own decision through the identical, unmodified
`map-gate-result.py` convention (`PASS`/`REQUIRE_HUMAN` -> check
succeeds, `FAIL` -> check fails); GitHub branch protection requiring
BOTH checks reproduces the intended combination policy purely through
platform-level AND-of-required-checks semantics:

    either check FAILs                    -> merge blocked
    either check is REQUIRE_HUMAN         -> that check still succeeds,
                                              but the SEPARATE required-
                                              review rule still blocks
                                              merge until reviewed
    both checks clean                     -> merge allowed (subject to
                                              the same required-review
                                              rule as always)

No code anywhere needs to read both decisions at once for this to work
correctly (proven C1-C4).

### What Security Phase 5 does not do

- Does not run any live scanner, dynamic tester, or reviewer session --
  `ci_verifier_runs.py` always returns `[]` today (see "Producer trust"
  above); wiring in real trusted verifier execution is future work, and
  belongs entirely inside that one file.
- Does not change `evidence_model.py`'s result semantics, `catalog.json`,
  or any Phase 1-4 file.
- Does not change `diana/preflight`, `diana/playwright`, or
  `diana/adapters` (the pre-existing AO adapter) at all.
- Does not weaken, bypass, or replace the required human/code-owner
  review rule -- `REQUIRE_HUMAN` still means "check succeeds, merge stays
  blocked by that independent rule," exactly as before this phase.
- Does not claim to protect its own PR (see "bootstrap limitation"
  above) -- that PR's protection is the pre-existing Diana Gate +
  required human review, unchanged.
- Does not fabricate successful dynamic tests, semantic-review PASS, or
  provider-sandbox evidence; when actual verifier evidence is
  unavailable, it emits `UNPROVEN`, never a synthetic `PASS`. Synthetic
  fixtures exist only inside `test-security-gate.sh`, never in the real
  CI path.
- Does not claim Security Phase 6 can begin before the post-merge
  activation probe above has been observed.

## Security Phase 6 -- Prove 75/75 Coverage

Answers, executably rather than in prose, one honest question for every
canonical control: **"Can Diana truthfully and reproducibly evaluate
this control today?"** `coverage_matrix.py` computes a deterministic
matrix over all 75 controls; `test-coverage-matrix.sh` proves its core
invariants hold.

### Two axes, never conflated

- **`resulting_state`** -- the REAL, LIVE result. Computed by actually
  running `security_bundle.build_bundle()` with the real, unmodified
  `ci_verifier_runs.py` output (today: `[]`) against the real catalog.
  As of this phase, `UNPROVEN` for **all 75 controls, uniformly** -- not
  asserted, but proven by literally invoking the same pipeline
  `diana-security-gate.yml` runs in CI (`test-coverage-matrix.sh` M12
  cross-checks this against an independent, direct
  `security_bundle.py` invocation).
- **`capability_coverage`** -- a static, code-level fact: for each
  required_evidence item, is there an implemented normalizer/adapter
  path that COULD produce that evidence if a human/operator supplied a
  real artifact? Computed by importing and reading the REAL,
  already-shipped authorization tables directly (never re-typed):
  Phase 2's three adapters' `AUTHORIZED_EVIDENCE` dicts, Phase 3's
  `dynamic/scenarios.py` `SCENARIO_REGISTRY`, and Phase 4's
  catalog-derived reviewer authorization rule (mirrors
  `reviewer_normalizer._authorized_control_ids()` exactly). One of
  `FULLY_COVERED` (every required_evidence item plus `dynamic_required`/
  `human_judgment_required` gate has an implemented path),
  `PARTIALLY_COVERED`, or `NOT_COVERED`.

A control can be `FULLY_COVERED` and still show `resulting_state:
UNPROVEN` -- that is not a bug, it is the honest state of a track that
has built normalizers but has not yet wired live verifier execution into
CI (`test-coverage-matrix.sh` M8 proves this explicitly for `SEC-001`).
**`capability_coverage` is never substituted for `resulting_state`, and
`resulting_state` is never upgraded because coverage looks good** --
"no finding" is never treated as PASS, anywhere in this matrix.

### Current honest coverage (this phase's actual finding)

As of this phase, against the real catalog and the real Phase 2-4
implementation:

| capability_coverage | count |
|---|---|
| `FULLY_COVERED` | 21 |
| `PARTIALLY_COVERED` | 25 |
| `NOT_COVERED` | 29 |

| resulting_state | count |
|---|---|
| `UNPROVEN` | 75 |
| `PASS` / `FAIL` / `NOT_APPLICABLE` / `ERROR` | 0 |

29 controls (including 8 `CRITICAL`-severity ones) have **zero**
implemented capability today -- no adapter, no dynamic scenario, and no
`SEMANTIC_REVIEW`/`HUMAN` mode permitted by the catalog. These are
explicit, named gaps (`remaining_gap` per row), not silently absent.
Even the 21 `FULLY_COVERED` controls are `UNPROVEN` live, because
`ci_verifier_runs.py` still returns `[]` -- Security Phase 5's own
documented, honest limitation, unchanged by this phase.

### What this phase does not do

- Does not fabricate a PASS, FAIL, or NOT_APPLICABLE for any control.
  Every `resulting_state` is computed by the real, unmodified pipeline.
- Does not wire any live verifier execution into CI --
  `ci_verifier_runs.py` is untouched; that remains explicitly future
  work, confined to that one file.
- Does not change `catalog.json`, `evidence_model.py`,
  `validate_catalog.py`, `security_bundle.py`, `security_reducer.py`, or
  any Phase 2-5 adapter/scenario/reviewer file -- it only reads their
  real, already-shipped authorization tables.
- Does not claim `NOT_COVERED` controls are `NOT_APPLICABLE` -- capability
  absence and applicability are different questions; every `NOT_COVERED`
  control's `resulting_state` is `UNPROVEN`, exactly like every other
  control with zero trusted runs.

## Security Track remediation round A

Not a new architecture phase -- a focused round reducing the real gaps
Security Phase 6 proved (21 `FULLY_COVERED` / 25 `PARTIALLY_COVERED` /
29 `NOT_COVERED`, `resulting_state` `UNPROVEN` for all 75, zero live
verifier execution). Two priorities, addressed honestly rather than by
loosening any catalog mode or inventing authorization the catalog
doesn't permit.

### Priority 1: the 8 CRITICAL `NOT_COVERED` controls

| Control | Outcome | How |
|---|---|---|
| `SEC-010` OS Command Injection | now `FULLY_COVERED` | `semgrep_adapter.py` (static half) + new dynamic scenario (dynamic half) |
| `SEC-011` Server-Side Template Injection | now `FULLY_COVERED` | same pattern |
| `SEC-021` JWT Signature Verification Errors | now `FULLY_COVERED` | same pattern |
| `SEC-058` Insecure Deserialization | now `FULLY_COVERED` | same pattern |
| `SEC-064` Exposed `.env`/Git/Backup/Config Files | now `FULLY_COVERED` | new `deterministic_repo_adapter.py` (static half, `DETERMINISTIC_REPO` mode) + new dynamic scenario (dynamic half) |
| `SEC-035` Server-Side Request Forgery (SSRF) | now `PARTIALLY_COVERED` | dynamic scenario added; static half explicitly BLOCKED (see below) |
| `SEC-006` Exposed API Keys/Secrets in Frontend | unchanged, explicitly BLOCKED | see below |
| `SEC-065` Cloud/DB Service Role Key Exposure | unchanged, explicitly BLOCKED | see below |

**`SEC-006`/`SEC-065` remain explicitly blocked, not silently skipped.**
`gitleaks_adapter.py`'s module docstring has said since Phase 2 that it
is "deliberately NOT authorized for `SEC-006` or `SEC-065` -- both make a
claim about a specific SCOPE this adapter's target model does not yet
represent" (its `target.scope` model only distinguishes `"full-repo"`
today; these two controls need a genuinely different scope concept --
"this is exactly the shipped client bundle" for `SEC-006`, "this is
everywhere including the client bundle" for `SEC-065`). This round
confirmed that reasoning is still correct and did not attempt a rushed
fix: extending the adapter's scope model is real, non-trivial,
adapter-level work deserving its own careful round, not a shortcut taken
under this round's time pressure. **Not loosening `AUTHORIZED_EVIDENCE`
to cover these without solving the underlying scope-representation gap
was a deliberate choice**, consistent with "do not invent authorization
the catalog/adapter model doesn't actually support."

**`SEC-035`'s static item is explicitly blocked for a different, sharper
reason**: its wording ("outbound request targets are validated/allow-
listed and internal/metadata address ranges are blocked") is a claim
about actual network-egress *behavior* (DNS-rebinding resistance,
redirect-following, allow-list completeness), not a lexical/structural
code pattern -- a static analyzer can flag "a URL is built from request
input near an HTTP call," but cannot establish the stronger claim the
catalog requires. See `semgrep_adapter.py`'s own module docstring for the
full reasoning. Its dynamic item (a real negative test) is not subject to
this limitation and was added normally.

### Priority 2: real trusted live verifier execution

`ci_verifier_runs.py` no longer always returns `[]`. It now genuinely
installs (into an ephemeral venv, on demand, no workflow-file changes
needed) and runs **Semgrep** -- a real, independently-maintained static
analyzer -- against the actual checked-out repository, using this
repository's own PINNED, committed rule file
(`diana/security/verifiers/semgrep-rules.yml`), never a live external
rule registry (`--config=auto`/`p/...` would depend on a remote registry
at scan time, which is neither deterministic nor reproducible).

- **Target identity is self-determined from real git state**
  (`git rev-parse HEAD`, `git remote get-url origin`), never from an
  argument or environment variable a PR could influence.
- **Unavailable tool != PASS; tool error != PASS; no finding != PASS.**
  Any failure at any step (git identity, venv/pip install, Semgrep
  execution, JSON parsing) degrades to the exact same `tool_unavailable`
  path Phase 2 already built and tested -- explicit `UNPROVEN`, never a
  crash, never fabricated evidence. A top-level guard additionally
  ensures `collect_trusted_runs()` itself can never raise.
  `test-ci-verifier-runs.sh` proves this degradation path directly
  (mocking tool unavailability) without depending on network access.
- **No PR-head executable code.** `diana-security-gate.yml`'s
  `pull_request_target` trust root (Security Phase 5's correction) means
  this script always runs from the protected base; Semgrep itself reads
  files as pattern-matching DATA, never executes/imports/evaluates them.
- **No secrets, no synthetic evidence outside tests.** The artifact this
  module builds always wraps Semgrep's own real, just-produced JSON
  output; synthetic reports exist only in the test suite.

**Real, honest result on this repository today**: Semgrep finds zero
matches for its 4 pinned rules against this repository's own code, so
`SEC-010`/`SEC-011`/`SEC-021`/`SEC-058` each gain a genuine `SATISFIED`
contribution for their static half -- their `resulting_state` remains
`UNPROVEN` (they are `dynamic_required=true` and no live dynamic
execution exists yet), but the REASON is now substantively different:
"missing required evidence: `<the specific dynamic negative-test item>`"
instead of the blanket "no verification runs submitted for this
control." This is real, if partial, progress -- not yet PASS anywhere,
because reaching PASS also needs the dynamic half, which remains
capability-only (a registered scenario, not live execution) after this
round.

### New totals after this round

| capability_coverage | before | after |
|---|---|---|
| `FULLY_COVERED` | 21 | 26 |
| `PARTIALLY_COVERED` | 25 | 26 |
| `NOT_COVERED` | 29 | 23 |

`resulting_state_counts` remains `{PASS: 0, FAIL: 0, NOT_APPLICABLE: 0,
UNPROVEN: 75, ERROR: 0}` -- unchanged in aggregate shape, though 4
controls' `UNPROVEN` reason is now substantively different (real partial
evidence, not blanket absence) as described above.
`live_execution_wired_count` is 4, up from 0.

### What this round does not do

- Does not wire live DYNAMIC_API/DYNAMIC_BROWSER/DYNAMIC_DB/
  DYNAMIC_CONCURRENCY/PROVIDER_SANDBOX execution, or any live semantic
  reviewer session, into CI -- only Semgrep (`STATIC_ANALYZER`) runs for
  real. Every dynamic scenario and every reviewer authorization remains
  capability-only.
- Does not wire Gitleaks or the new `deterministic_repo_adapter.py` into
  live execution -- both remain capability-only; a future round adding
  them would extend `ci_verifier_runs.py` in the same pattern this round
  established for Semgrep, without needing to change `security_bundle.py`,
  `security_reducer.py`, or the Gate integration at all.
- Does not claim PASS for any control -- `resulting_state_counts` still
  has zero `PASS` after this round; see the exact counts in this round's
  PR description / final report.
- Does not build a generic scanner platform -- `ci_verifier_runs.py`
  wires in exactly one real verifier family, using this repository's own
  pinned rules, not a configurable multi-tool framework.
- Does not change `catalog.json`'s modes, or authorize any adapter for a
  control/requirement its catalog `verification.modes` doesn't already
  structurally permit.

## Security Track remediation round B

A second focused round, starting from round A's real baseline (26
`FULLY_COVERED` / 26 `PARTIALLY_COVERED` / 23 `NOT_COVERED`,
`live_execution_wired_count` 4, `resulting_state` `UNPROVEN` for all 75).
Three priorities, again addressed honestly rather than by loosening any
catalog mode, evidence-model aggregation semantics, or inventing
authorization the catalog doesn't permit.

### Priority 1: close SEC-006/SEC-065's zero-capability gap properly

Round A left `SEC-006`/`SEC-065` explicitly blocked because
`gitleaks_adapter.py`'s `target.scope` model only recognized
`"full-repo"` -- neither control's claim ("no third-party secret in the
shipped frontend bundle" / "elevated keys never shipped to a client
build, conjoined with never committed to source") could be honestly
represented. This round fixes the underlying representation instead of
merely adding the controls to `AUTHORIZED_EVIDENCE`:

- `SCOPE_FRONTEND_BUNDLE` ("exactly the shipped/built frontend bundle
  output") for `SEC-006`.
- `SCOPE_FULL_REPO_AND_FRONTEND_BUNDLE` (both surfaces scanned together
  in ONE artifact) for `SEC-065` -- its required_evidence string is a
  conjunction across two surfaces, and `evidence_model.py`'s Phase 1
  aggregation only supports OR-semantics across contributions to one
  `(control_id, requirement)` pair. Rather than change that shared
  aggregation (out of bounds for an adapter-level round), the conjunction
  is pushed onto the artifact producer: a `SATISFIED` claim under this
  scope asserts both surfaces were genuinely scanned together, exactly
  the same trust model `"full-repo"` already uses for SEC-007.
- Each control now has an explicit `REQUIRED_SCOPE_FOR_SATISFIED` (the
  exact scope a clean scan needs to count as `SATISFIED`) and
  `RELEVANT_SCOPES_FOR_VIOLATION` (which scopes make a finding count as
  evidence at all) -- `SEC-007` keeps its original scope-independent
  `VIOLATED` behavior (`None` sentinel) so a finding under any scope
  string still counts, unchanged from Phase 2.
- Scope is never fabricated: `ci_verifier_runs.py` only ever declares
  `SCOPE_FRONTEND_BUNDLE`/`SCOPE_FULL_REPO_AND_FRONTEND_BUNDLE` when
  `_detect_frontend_bundle_dir()` finds a REAL, existing conventional
  build-output directory (`dist`, `build`, `out`, `.next`,
  `public/build`) at the repository root -- deterministic trusted
  filesystem inspection, never a model guess. This repository (Diana's
  own tooling, not a deployed web app) has no such directory, so those
  two scopes are correctly never attempted here; `SEC-006`/`SEC-065`
  stay honestly `UNPROVEN` rather than a scope being invented.

**Gitleaks is now wired into live execution.** A version-pinned
(`8.30.1`), SHA256-checksum-verified (checksum pinned in
`ci_verifier_runs.py`'s own source, from Gitleaks' published
`_checksums.txt`) release binary is downloaded on demand -- never
`@latest`, never unverified -- using the same "no workflow-file changes
needed" pattern round A established for Semgrep. It runs against a
pinned, committed config (`diana/security/verifiers/gitleaks-config.toml`)
that extends Gitleaks' own default ruleset (`[extend] useDefault = true`)
with an allowlist for this Security Track's own synthetic test fixtures
(fake secret-shaped strings that exist specifically to test the
adapter's parsing logic -- the standard, honest way to handle intentional
fixtures with a real scanner, not evidence-hiding; see that file's own
comments). Always scans `SCOPE_FULL_REPO` for `SEC-007`; additionally
scans a detected frontend-bundle directory for `SEC-006`/`SEC-065` only
when one is actually found.

### Priority 2: deterministic-repo-scan wired into live execution

`deterministic_repo_adapter.py`'s `SEC-064` capability (added in round A)
was capability-only until this round. `ci_verifier_runs.py` now performs
a real, deterministic filesystem traversal (`_list_served_paths` --
sorted, relative, forward-slash paths, no execution of anything found)
of the SAME detected public deployment web root Gitleaks' frontend-bundle
scope uses, and wraps the result as the `{"served_paths": [...]}` report
`deterministic_repo_adapter.py` expects. Protected-base checkout only,
inspected as data; explicit producer identity
(`diana-deterministic-repo-scan`, this module's own internal version
string, never a fabricated third-party name); exact repository+commit
target binding. If no web root directory is detected (true for this
repository today), `SEC-064`'s static half correctly stays `UNPROVEN`
rather than a served-path listing being fabricated for a deployment
surface that does not exist.

### Priority 3: one dynamic verifier family brought live

The `sensitive-file-paths-not-fetchable` scenario (`SEC-064`'s dynamic
half) is now genuinely live. `ci_verifier_runs.py` starts a real HTTP
server bound ONLY to `127.0.0.1` on an OS-assigned ephemeral port
(`http.server.SimpleHTTPRequestHandler`, this process's own, torn down
in a `finally` block), serving the SAME detected web root as Priority 2,
and issues real negative-fetch GET requests for a small, fixed,
non-PR-influenced set of well-known sensitive paths
(`SENSITIVE_FETCH_CANDIDATE_PATHS`: `.env`, `.git/config`, `backup.sql`,
`backup.zip`). No production target, no public network exposure, no
PR-provided paths or commands, no PR-head code executed (the server only
serves static file bytes, exactly like Gitleaks/Semgrep read files as
data). Every probe has a short, bounded timeout
(`_LOCAL_FETCH_TIMEOUT_SECONDS`). The result is normalized through the
real, unmodified `dynamic_normalizer.py`/`dynamic_base.py` -- environment
declared as `LOCAL` (never inferred), execution context (environment +
repository + commit + `base_url`) verified exactly like every other
dynamic scenario. Only attempted when a real web root directory is
detected; otherwise `SEC-064`'s dynamic half correctly stays `UNPROVEN`.

This was chosen over the other 22 registered dynamic families because it
required no external application, no test identities, no payment-
provider sandbox, and no model-attempt harness to run honestly -- it is
the smallest scenario that can produce genuinely truthful evidence using
only what this round already had (a detected web root, a bounded local
HTTP server) without becoming a general dynamic execution platform.

### New totals after this round

| capability_coverage | before (round A) | after (round B) |
|---|---|---|
| `FULLY_COVERED` | 26 | 27 |
| `PARTIALLY_COVERED` | 26 | 27 |
| `NOT_COVERED` | 23 | 21 |

Zero `CRITICAL` controls remain `NOT_COVERED` (16 `HIGH` + 5 `MEDIUM`
remain `NOT_COVERED`, unchanged in severity mix from round A --
`SEC-006`/`SEC-065` moved out of `NOT_COVERED` this round). \
`live_execution_wired_count` is 6, up from 4 (`SEC-007`, `SEC-010`,
`SEC-011`, `SEC-021`, `SEC-058`, `SEC-064` each received at least one
genuinely submitted live run this invocation -- some of which stayed
`UNKNOWN`/`UNPROVEN` because this repository lacks a frontend-bundle
directory, which is itself honest, not a gap in the wiring).

`resulting_state_counts` remains `{PASS: 0, FAIL: 0, NOT_APPLICABLE: 0,
UNPROVEN: 75, ERROR: 0}` on this repository today -- still zero `PASS`
anywhere. This is expected, not a shortfall in this round's wiring:
every one of the 75 controls' `required_evidence` is an AND across
(typically) a static and a dynamic (or semantic-reviewer) claim, and no
single control yet has ALL of its required items live-satisfied at once
(e.g. `SEC-007` also needs a semantic "secrets loaded from
environment/secret-manager configuration" claim no live semantic
reviewer session yet produces; `SEC-010`/`SEC-011`/`SEC-021`/`SEC-058`
have live static evidence but their dynamic half, while now a
capability, isn't the one family this round brought live). Reaching
`PASS` anywhere is future work, and reaching PASS honestly requires that
AND to be genuinely, individually earned -- exactly what "no finding !=
PASS" has meant since Phase 1.

### What this round does not do

- Does not wire live semantic-reviewer (`SEMANTIC_REVIEW`/`HUMAN`)
  sessions into CI -- every control needing
  `human_judgment_required=true` remains capability-only.
- Does not bring any dynamic family live besides
  `sensitive-file-paths-not-fetchable` -- the other 22 registered
  scenarios (cross-account access, SQL injection, stored XSS, webhook
  replay, payment-provider sandboxes, AI tool-call enforcement, etc.)
  remain capability-only; each would need its own real test
  identities/fixtures/sandbox this round did not build.
- Does not modify `evidence_model.py`'s aggregation semantics -- SEC-065's
  conjunction is represented via a single combined-scope artifact, not a
  change to how multiple contributions to one requirement are combined.
- Does not claim PASS for any control -- see `resulting_state_counts`
  above.
- Does not change `catalog.json`'s modes, or authorize any adapter for a
  control/requirement its catalog `verification.modes` doesn't already
  structurally permit.
- Does not build a generic scanner or dynamic-execution platform --
  `ci_verifier_runs.py` wires in exactly four real verifier
  families (Semgrep, Gitleaks, the deterministic-repo-scan, and one
  dynamic scenario), each using this repository's own pinned
  rules/config/candidate lists, not a configurable multi-tool framework.

## Security Track remediation round C

Round B's goal was capability breadth (27/27/21 `FULLY/PARTIALLY/NOT_
COVERED`, zero `CRITICAL` `NOT_COVERED`, 6 controls with live execution
wired). Its `resulting_state_counts` were still `{PASS:0, ..., UNPROVEN:
75}` for every control on this repository. Round C's goal was different:
attack the causes of `UNPROVEN` directly, and find out whether this
repository can honestly reach a real `PASS` anywhere -- not just increase
capability percentages.

### Priority 1 -- the first genuine PASS: an executable proof of why none exists today

Ran the real, unmodified pipeline end to end (`ci_verifier_runs.
collect_trusted_runs()` -> `evidence_model.evaluate()`, zero synthetic
input) against this repository's actual current state. Result: **0 PASS**,
7 runs collected, 5 controls with at least one item satisfied (SEC-007,
010, 011, 021, 058), none complete.

For each of those 5 near-misses, the SPECIFIC missing item was
identified precisely -- not "capability missing," but the exact
`required_evidence` string still unproven:

| Control | Item satisfied | Item still UNPROVEN |
|---|---|---|
| SEC-007 | no credential literal committed (Gitleaks, live) | secrets loaded from environment/secret-manager config (a SEMANTIC claim -- see Priority 3) |
| SEC-010 | no shell command built via string concat (Semgrep, live) | negative test proving a metacharacter payload is inert |
| SEC-011 | request input never rendered as template syntax (Semgrep, live) | negative test proving a template-expression payload is inert |
| SEC-021 | JWT verification enforces a fixed algorithm (Semgrep, live) | negative test proving a forged/none-alg token is rejected |
| SEC-058 | deserialization uses a safe/restricted format (Semgrep, live) | negative test proving a crafted payload achieves nothing |

The four dynamic-negative-test items (SEC-010/011/021/058) all need a
REAL code path performing the underlying operation (building a shell
command from input, rendering a template, verifying a JWT, deserializing
untrusted data) to test against -- a negative test against a mechanism
that doesn't exist would be either impossible (nothing to invoke) or
vacuous (testing nothing), and this round's instructions explicitly
forbid synthetic/fabricated evidence. An exhaustive code search across
this entire repository (`grep -rn` for `shell=True`/string-built shell
commands, `jwt`/`JWT`, `jinja2`/`Template(`, `pickle.loads`/`yaml.load`/
`eval(`, outbound `requests.get`/`urlopen` on caller-supplied URLs, any
dependency manifest/lockfile, any frontend/client build tooling, any
database access library, any real webhook receiver, any auth/session/
login code, any HTTP server framework) found **zero matches for every
one of these**, plus zero matches for the mechanisms behind every other
near-candidate control checked (SEC-035 SSRF, SEC-060 dependency
scanning, SEC-065 combined-scope secret exposure, SEC-074/075 AI tool
authorization against Diana's own `diana/adapters/ao.py`, which is a CLI
subprocess wrapper with no model-facing authorization boundary of its
own).

**Root cause, stated plainly: this repository (Diana's own security-
tooling/CLI-orchestration codebase) has none of the application features
the "vibe coder security" catalog assumes** -- no HTTP server, no
frontend, no database, no dependency manifest, no user accounts, no
webhook receiver. This is an architectural fact about THIS repository,
not a live-wiring gap in Diana. **No canonical SEC control can honestly
reach PASS in this repository today** -- verified by exhaustive search,
not asserted. (Priority 3 below independently produces a genuine,
mechanism-level PASS proof using synthetic test fixtures -- clearly
distinguished there from a real-repository claim.)

### Priority 2 -- expand live dynamic verification: same root cause, same conclusion

The round's suggested targets (SEC-010, 011, 021, 035, 058, 064) were
evaluated individually. SEC-010/011/021/058 have no real subject (see
above). SEC-035 (SSRF) needs outbound-fetch-of-caller-supplied-URL code --
zero matches found. SEC-064's dynamic half is ALREADY genuinely live
(round B) and correctly blocked by the same real, verified absence: this
repository has no directory matching any conventional public-deployment
web-root name (`dist`/`build`/`out`/`.next`/`public/build`), confirmed
again this round (`ls -d */` shows only `diana/`; no `docs/`/`site/`/
GitHub Pages config exists either). Building a new dynamic scenario
against a nonexistent target would require fabricating a toy vulnerable
code sample purely to have something to test -- explicitly forbidden
("no synthetic production evidence"). **No additional dynamic family can
be brought genuinely live against a real subject in this repository as
it exists today.** This is not a capacity limit of the scenario registry
(23 scenarios already exist, capability-only) -- it is the same
architectural fact from Priority 1.

### Priority 3 -- trusted human/semantic evidence: architecture decision

**Decision: GitHub-backed human review is preferred over wiring an LLM,
exactly as the round's investigation instruction anticipated it might
be.** Built as a genuinely new, capability-only normalizer --
`diana/security/reviewer/github_review_adapter.py` -- deliberately
NOT layered onto `reviewer_normalizer.py`/`reviewer_base.py` (Phase 4),
because that contract is shaped for an AI/agent review SESSION
specifically (`session_type` is a closed one-value enum,
`"fresh_read_only"`; `files_inspected`/`architecture_reasoning`/
`call_chain` read as a structured session log a human clicking "Approve"
does not naturally produce). Forcing GitHub review data into that shape
would mean either requiring reviewers to hand-author an AI-review-level
document, or silently relaxing a bar that module was never designed to
relax. `github_review_adapter.py` is a parallel, independently-scoped
module with its own honestly-different structural checks, following the
exact same architecture (parse an already-produced artifact; reuse
`adapter_base.verify_identity/verify_target/build_runs/tool_error_runs/
tool_unavailable_runs/NotAuthorized` directly; never call the GitHub API
itself) every prior Phase 2-4 normalizer already established.

Trust properties (mapped onto every one of Round C's explicit
requirements):

- **Reviewer identity from GitHub, never the PR body**: `reviewer.login`/
  `reviewer.review_id` are expected to be copied verbatim from a real
  `gh api .../pulls/<n>/reviews` response by whatever trusted script
  produces the artifact (see live-wiring status below) -- never typed
  into a PR-body evidence block.
- **Independence is a structural gate, not a self-declared courtesy**:
  `independence.reviewer_is_pr_author`/`reviewer_is_diana_agent` must
  BOTH be `false` or the module raises `ArtifactError` -> an explicit
  `ERROR` run, never a silent downgrade -- a non-independent "review" can
  never become authorization evidence.
- **Judgment bound to the exact reviewed commit SHA; staleness handled by
  the SAME exact-match binding every adapter already uses** --
  `adapter_base.verify_identity()`/`verify_target()` compare
  `target.commit` against the currently-evaluated commit; a review
  against a superseded commit contributes nothing. No new staleness
  mechanism was invented.
- **Evidence records control_id, reviewer identity, target commit SHA,
  judgment, rationale, timestamp** -- every one is a required, checked
  envelope field.
- **Repository governance decides reviewer eligibility** -- this module
  enforces only the two independence facts any design needs regardless
  (not the author, not the Diana worker account); a live-wiring script
  MAY apply a stricter eligibility filter (e.g. CODEOWNERS membership)
  before ever producing an artifact -- a deployment/governance choice,
  not this normalizer's to make.
- **Judgment is per-control, not a blanket PR approval** -- `rationale`
  must literally name the `control_id` it judges, be non-trivial length,
  and not be composed solely of a small vague-reassurance blocklist
  (`VAGUE_RATIONALE_PHRASES`) -- a generic "LGTM" cannot become evidence
  for any specific control.
- **No PR-body self-certification, no worker self-approval** -- enforced
  structurally, proven by test (`reviewer_is_diana_agent=true` ->
  `ERROR`).

**Mechanism proof (not a real-repository claim)**: `test-github-review-
adapter.sh` feeds two independent, substantiated `APPROVE` artifacts
(different reviewer logins, one per SEC-016 `required_evidence` item)
through this module and `evidence_model.evaluate()` UNCHANGED, and gets a
genuine `PASS` back -- proving the mechanism is real and correctly wired
to the existing aggregation, not asserted in prose. A companion test
proves one `REQUEST_CHANGES` item keeps the whole control from `PASS`
even when the other item is cleanly satisfied (never averaged away).

**AI/semantic review remains available as a documented, provider-neutral,
BYOK-only fallback** for judgment calls that genuinely exceed what a
structural human sign-off can determine -- never a Diana-owned or shared
provider key, never Diana Cloud, never a secret introduced into the
Security Gate trust root, and AI judgment can never become trusted
authorization merely because a model said so (the EXISTING
`reviewer_normalizer.py`/`reviewer_base.py` structural-substantiation
gates already enforce this, unchanged, for whenever that path is used).
This round did not need to build or change that fallback path -- GitHub-
backed human review satisfies the catalog and evidence model without it.

**Live-wiring status: capability-only, not yet wired into
`ci_verifier_runs.py`, for one precise, verified reason.** Unlike
Semgrep/Gitleaks/the deterministic-repo-scan/the one live dynamic
scenario (all wired with zero `.github/workflows/*.yml` changes),
fetching real PR review data via `gh api` from inside
`diana-security-gate.yml` needs a `pull-requests: read` permission that
workflow's `permissions:` block does not currently grant -- verified
directly against the live file (`permissions: {contents: read}` only;
GitHub Actions treats any explicit `permissions:` block as authoritative,
so an unlisted scope is `none`). Adding that scope is a
`.github/workflows/diana-security-gate.yml` edit, which this session's
`DIANA-AGENT` credential cannot push (missing the `workflow` OAuth
scope -- the same constraint documented since Security Phase 5). This is
a precisely-scoped, ready-to-implement follow-up requiring one human-
pushed permission change, not attempted or half-built this round.

### Priority 4 -- classification of the 21 remaining NOT_COVERED controls

Recomputed from source (`coverage_matrix.py`, unchanged capability
totals: this priority is classification, not new implementation --
"do not implement all remaining controls just to inflate coverage" was
followed literally). Every remaining `NOT_COVERED` control's TWO
`required_evidence` items were independently checked against this
repository's actual code (same exhaustive search as Priority 1) and
against the existing scenario registry.

**Zero fall into category B** (an existing dynamic scenario already
sufficient) -- none of the 23 registered scenarios target any of these
21 controls. **Zero fall into category D** (trusted human judgment
needed) -- all 21 have `human_judgment_required=false`; every control
that DOES need judgment was already `FULLY_COVERED`/`PARTIALLY_COVERED`
via the reviewer path (Phase 4 + Priority 3 above). The real split is
between **A** (a new Semgrep-style static rule is genuinely feasible) for
most static halves, and **E/F** for both dynamic halves and a handful of
INFRA_CONFIG-heavy static halves -- because this repository has neither
the underlying code pattern (F) nor a deployed environment to inspect (E).

| Control | Sev | Static item | Dynamic item | Primary blocker |
|---|---|---|---|---|
| SEC-009 nosql-injection | HIGH | A -- Semgrep-feasible (operator-dict built from request input) | C, needs a real DB | F -- no database access code exists in this repo |
| SEC-013 reflected-xss | HIGH | A -- Semgrep-feasible (unescaped output of request input) | C, needs DYNAMIC_BROWSER + running app | F -- no web server/render surface |
| SEC-014 dom-based-xss | HIGH | A -- Semgrep-feasible (unsafe DOM sink from URL-derived value) | C, needs a browser + app | F -- no frontend code at all |
| SEC-030 insecure-cookie-attributes | HIGH | A -- Semgrep-feasible (Set-Cookie flags) | E, needs live response inspection | F -- no cookie-setting code exists |
| SEC-031 cors-misconfiguration | HIGH | A -- Semgrep-feasible (wildcard ACAO + credentials) | E/C, needs a live cross-origin request | F -- no CORS config code exists |
| SEC-036 path-traversal | HIGH | A -- Semgrep-feasible (unvalidated path join from request input) | C -- same negative-fetch pattern as SEC-064's live scenario | F -- no file-serving-from-request-path code exists |
| SEC-037 local-file-inclusion | HIGH | A -- Semgrep-feasible (dynamic import/include from request input) | C | F -- no dynamic file-inclusion code exists |
| SEC-039 upload-content-type-confusion | HIGH | A -- Semgrep-feasible (trusting client Content-Type alone) | C, needs a file-upload endpoint | F -- no file-upload code exists |
| SEC-040 insecure-file-permissions | HIGH | E -- real cloud storage config | E, needs a real deployed bucket | E -- no cloud storage deployment in this repo |
| SEC-041 mass-assignment | HIGH | A -- Semgrep-feasible (bulk-assign from request body) | C, needs a mutation endpoint | F -- no request-body-binding code exists |
| SEC-052 debug-mode-in-production | HIGH | A/E -- needs a web framework or deployment config | n/a (`dynamic_required=false`) | F -- no web framework/production deployment config exists |
| SEC-054 tls-https-misconfiguration | HIGH | E -- real deployment TLS config | E, live TLS handshake inspection | E -- no deployed endpoint to inspect |
| SEC-057 improper-certificate-validation | HIGH | A -- Semgrep-feasible (`verify=False`/`rejectUnauthorized:false`) | C, needs an outbound TLS client + bad-cert endpoint | F -- no outbound TLS client code exists |
| SEC-059 prototype-pollution | HIGH | A -- Semgrep-feasible (unguarded object merge) | C, needs a JS object-merge endpoint | F -- no JS object-merge code (repo is Python/shell) |
| SEC-062 dependency-confusion | HIGH | E -- real registry config | n/a (`dynamic_required=false`) | E/F -- no packages published from this repo |
| SEC-072 insecure-api-key-authentication | HIGH | F -- needs an API-key-issuing backend | C, needs a live API | F -- no API-key auth system exists |
| SEC-032 missing-or-weak-csp | MEDIUM | A -- Semgrep-feasible if a framework existed | E, live header inspection | E -- no deployed web server to inspect |
| SEC-033 clickjacking | MEDIUM | A | E | E -- no deployed web server to inspect |
| SEC-034 open-redirect | MEDIUM | A -- Semgrep-feasible (redirect target from request input) | C -- negative-fetch style scenario | F -- no redirect-issuing code exists |
| SEC-053 security-misconfigured-http-headers | MEDIUM | A | E | E -- no deployed web server to inspect |
| SEC-063 exposed-source-maps | MEDIUM | A/E -- needs a real build/frontend surface | C -- same pattern as SEC-064's live scenario | E/F -- no build/frontend surface exists |

Deliberately not implemented: building Semgrep rules for 12+ controls
whose only real subject in THIS repository would be hypothetical code,
or new dynamic scenarios with no real endpoint to point them at, is
exactly the "implement to inflate coverage" this priority explicitly
says not to do. The classification itself -- precise, per-item, sourced
from real code search rather than guessed -- is the deliverable.

### Priority 5 -- live-verifier reliability, checked empirically this round

| Family | AVAILABLE | EXECUTED | RUN PRODUCED | EVIDENCE ACCEPTED | CONTROL CONTRIBUTION ACCEPTED |
|---|---|---|---|---|---|
| Semgrep | yes (venv-installed, v1.176.1) | yes (0 findings) | 4 runs | yes | SEC-010/011/021/058 SATISFIED |
| Gitleaks | yes (binary downloaded, checksum-verified) | yes (0 findings) | 1 run | yes | SEC-007 SATISFIED |
| deterministic-repo-scan | no (`_detect_frontend_bundle_dir` finds nothing) | n/a | 1 run (explicit UNKNOWN) | n/a | none (honest, not fabricated) |
| `sensitive-file-paths-not-fetchable` | no (same reason) | n/a | 1 run (explicit UNKNOWN) | n/a | none (honest, not fabricated) |

Downloaded-tool integrity, verified directly against source: Semgrep is
installed from PyPI into an ephemeral venv on demand (no pinned exact
version string enforced beyond whatever PyPI currently resolves --
this is a real, narrower reliability gap worth a future round's
attention: unlike Gitleaks, Semgrep's install is not yet version-pinned/
checksum-verified). Gitleaks is a fixed, version-pinned (`8.30.1`)
release binary with a SHA256 checksum PINNED IN SOURCE, verified before
extraction -- no mutable `@latest` URL. Both degrade to explicit
`UNKNOWN`/`UNPROVEN` (never `PASS`) on any unavailability, exactly as
designed. **A real, observed environment-dependent reliability gap**:
PR #34's live CI run showed Gitleaks producing NO run at all for SEC-007
(`"no verification runs submitted for this control"`), while this same
code produces a clean SATISFIED run in this session's sandbox -- the
degradation worked correctly (no crash, no fabrication) in both places,
but the GitHub Actions runner environment apparently could not complete
a Gitleaks install/run that succeeds locally. Root cause not yet
diagnosed (candidate causes: runner network egress restrictions,
transient GitHub releases rate-limiting) -- flagged here as a concrete
reliability item for a future round, not silently glossed over.

### New totals after this round

Capability coverage is UNCHANGED from round B (27/27/21) -- this round's
new capability (`github_review_adapter.py`) authorizes the exact same
36-control set `reviewer_normalizer.py` already covered, so it adds a
second `implemented_paths` entry (visible in `coverage_matrix.py`'s
`"kind": "github_review"` rows) without moving any control between
`FULLY_COVERED`/`PARTIALLY_COVERED`/`NOT_COVERED`. `resulting_state_
counts` remains `{PASS:0, FAIL:0, NOT_APPLICABLE:0, UNPROVEN:75,
ERROR:0}` on this repository -- Priority 1's executable proof is exactly
why, and that proof, not a capability number, is this round's real
deliverable.

### What this round does not do

- Does not fabricate a PASS, a NOT_APPLICABLE, or evidence for any
  control -- the near-miss analysis and the 21-control classification
  are both sourced from real, exhaustive code search, not asserted.
- Does not build new Semgrep rules or dynamic scenarios for controls with
  no real subject in this repository -- doing so was explicitly weighed
  and explicitly rejected as either impossible or vacuous.
- Does not wire `github_review_adapter.py` into live CI -- blocked on a
  verified, human-pushable `.github/workflows/diana-security-gate.yml`
  permission change (`pull-requests: read`), not attempted this round.
- Does not introduce an AI/LLM reviewer, a Diana-owned provider key, or
  Diana Cloud -- GitHub-backed human review satisfied the catalog and
  evidence model without needing to.
- Does not change `catalog.json`, `evidence_model.py`'s aggregation
  semantics, or any existing adapter's `AUTHORIZED_EVIDENCE` mapping.
- Does not diagnose the Gitleaks CI-vs-local reliability discrepancy
  found by Priority 5 -- flagged precisely for a future round, not
  silently accepted or hidden.

## Future phases (not started here)

- This README does not promise the exact shape of future work ahead of
  each phase actually landing.

## Running the validator and tests

```
python3 diana/security/validate_catalog.py diana/security/catalog.json
bash diana/security/test-catalog.sh
bash diana/security/test-evidence-model.sh
bash diana/security/adapters/test-adapters.sh
bash diana/security/dynamic/test-dynamic.sh
bash diana/security/reviewer/test-reviewer.sh
bash diana/security/test-security-gate.sh
bash diana/security/test-coverage-matrix.sh
bash diana/security/test-ci-verifier-runs.sh
```

Every suite above is deterministic and offline for its CORE assertions.
`test-ci-verifier-runs.sh` and (since Security Track remediation round A)
`test-security-gate.sh`/`test-coverage-matrix.sh` additionally attempt
ONE real, network-dependent end-to-end check each (real Semgrep
installation and execution) -- these SKIP gracefully (never fail the
suite) if network/pip install is unavailable in the current environment,
since the deterministic checks alongside them already prove the
underlying logic offline. `test-security-gate.sh` additionally creates and destroys
small, ephemeral, local-only `git init` scratch repositories under a
`mktemp -d` directory (to prove the protected-base extraction end to end,
S2+S3) -- no network access and no changes to this repository.

`evidence_model.py` itself takes two arguments (a catalog path
and a runs-file path) and is normally invoked directly for ad hoc checks:

```
python3 diana/security/evidence_model.py diana/security/catalog.json <runs.json>
```

`coverage_matrix.py` (Security Phase 6) takes a catalog path plus the
target-identity triple and prints the full 75-control matrix:

```
python3 diana/security/coverage_matrix.py diana/security/catalog.json <repository> <base_sha> <target_sha>
```
