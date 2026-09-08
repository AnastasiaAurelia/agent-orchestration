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
  empty) extension point for real trusted verifier execution;
  `diana/ci/run-security-gate.py` extracts and runs this trusted
  evaluator from the pull request's PROTECTED BASE SHA, never the PR
  head, so a PR cannot weaken its own current evaluation; and
  `diana-gate.py` gained one small, additive `combine_with_security()`
  function (its existing `evaluate()` and CLI behavior are completely
  unchanged) that combines the existing Gate decision with the Security
  decision conservatively. See "Security Phase 5" below for the full
  trust-boundary design, the PR-body-is-untrusted-for-security rule, and
  the Phase 5 PR's own bootstrap limitation.

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
  `diana/gate/diana-gate.py` gained one small, additive Security Phase 5
  function (`combine_with_security()`) and a `combine` CLI mode; its
  existing `evaluate()` function, input schema, and every pre-existing
  behavior/test are byte-for-byte unchanged -- see "Security Phase 5"
  below.
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
decision, without collapsing the existing Preflight/Gate separation:

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
existing Diana Gate (diana-gate.py, unmodified evaluate())
        |
diana-gate.py combine (new, additive combine_with_security())
        |
required CI check (map-gate-result.py, unmodified)
        |
human review
        |
merge
```

Preserved throughout: **no finding != PASS**, **missing evidence !=
PASS**, **UNPROVEN != PASS**, **ERROR != PASS**. Human review remains the
final merge authority -- Security Phase 5 can force `REQUIRE_HUMAN` or
`FAIL`, but never bypasses or replaces the required human/code-owner
review rule.

### Security evidence must not come from the PR body

The existing `<!-- DIANA:EVIDENCE -->` PR-body block
(`diana/ci/build-gate-input.py`) stays scoped to exactly its five
pre-existing fields (`dod`, `verification`, `preflight`, `risk`,
`human_only_conditions`) -- unchanged by this phase. A PR body claiming
`"SEC-001": "PASS"` or similar has zero authority over the Security
reducer: the strict `set(value) != EVIDENCE_FIELDS` check rejects any
extra field outright (proven by `test-security-gate.sh` T1). The real
Security evidence pipeline (`ci_verifier_runs.py`) takes no arguments and
reads no PR-supplied content at all -- not the PR body, not an arbitrary
checked-in JSON file, even one with a structurally valid Phase 2/3/4
`artifact_binding` hash (proven by T2: a fake, correctly-hashed Phase 4
semantic-review artifact planted in the working tree does not change
`ci_verifier_runs.py`'s output). `artifact_binding` proves internal
artifact integrity only, never who produced an artifact or that it came
through a genuine CI-controlled verifier path (Phase 2/3/4's own stated
limitation, not erased here).

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

### Preventing security self-certification: the protected-base evaluator

A PR may modify `diana/security/*`, `diana/gate/*`, `diana/ci/*`, or
`.github/workflows/*` -- those changes must never be able to weaken their
own current evaluation. `diana/ci/run-security-gate.py` extracts
`diana/security/{catalog.json, validate_catalog.py, evidence_model.py,
security_bundle.py, security_reducer.py, ci_verifier_runs.py}` from the
pull request's **protected base SHA** (`git archive <base_sha> --
diana/security`, extracted into an isolated temp directory and executed
from there) and runs the ENTIRE trusted pipeline from that extracted
copy -- never from the PR head's working tree. A PR that weakens a
control's severity in `catalog.json`, or replaces `security_reducer.py`
with one that always returns `PASS`, has zero effect on its own
evaluation: the base's real severity and real reducer are what get
executed (proven end-to-end against a real, ephemeral git repository by
`test-security-gate.sh`'s S2+S3 test).

**Bootstrap exception, stated explicitly**: this very PR is the one that
first introduces `security_bundle.py`/`security_reducer.py`/
`ci_verifier_runs.py` -- they do not exist at this PR's own base SHA,
so there is nothing yet to extract. `run-security-gate.py` detects this
(any `TRUSTED_FILES` path missing after extraction, or `git archive`
reporting the pathspec matched nothing at all) and returns
`{"decision": "SKIPPED_BOOTSTRAP", ...}`, which `combine_with_security()`
treats as a pure pass-through: zero penalty, zero escalation, the
existing Gate decision unchanged. **This PR does not claim Security
Phase 5 retroactively protects itself** -- it remains protected only by
the pre-existing Diana Gate (DoD/verification/Preflight/diff-risk/
human_only_conditions) plus the required human/code-owner review, exactly
as before this phase existed. After this PR merges, every subsequent
PR's base includes these files, so the bootstrap branch is never taken
again under ordinary operation.

### Catalog authority

Severity/policy decisions use the protected-base canonical
`catalog.json`, never a PR-head catalog. A `security_bundle.py` bundle
binds `catalog_version` (the catalog's own version integer) AND
`catalog_sha256` (a SHA-256 fingerprint of the whole trusted catalog
content) -- either mismatching the trusted catalog fails the bundle
closed (`security_bundle.validate_bundle`), so a bundle generated against
one catalog content can never silently validate against a different one.
A PR that changes `diana/security/catalog.json` is itself forced to
`REQUIRE_HUMAN` by the Gate's existing sensitive-path mechanism (see
"Security-control changes require human review" below) and, per the
protected-base design above, cannot use its own changed catalog to weaken
its own current evaluation; the new catalog only becomes authoritative
for evaluations run against a base that includes it, i.e. after human
merge.

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
sensitive prefix). This reuses the existing, already-tested mechanism
rather than inventing a second one -- proven by `test-security-gate.sh`
S1/S1b.

### Combining with the existing Diana Gate

`diana-gate.py`'s `evaluate()` function, its 6-field input schema, and
its whole pre-existing CLI behavior are **completely unchanged** -- every
pre-existing test (`test-gate.sh`, `test-gate-integration.sh`,
`test-ship.sh`) passes unmodified. A new, purely additive
`combine_with_security(gate_decision, security_decision)` function and a
`diana-gate.py combine GATE_RESULT.json SECURITY_RESULT.json` CLI mode
implement the stated combination policy:

    existing FAIL or security FAIL              -> final FAIL
    otherwise existing or security REQUIRE_HUMAN -> final REQUIRE_HUMAN
    otherwise                                    -> final PASS

(`security_decision == "SKIPPED_BOOTSTRAP"` is a pure pass-through --
see "bootstrap exception" above.) `write-summary.py` and
`map-gate-result.py` are both completely unchanged; they are simply fed
the combined result instead of the original Gate-only result, so the
human-merge-floor mapping (`REQUIRE_HUMAN` -> check succeeds, merge still
blocked by required review) is unaffected regardless of whether the
`REQUIRE_HUMAN` came from the existing Gate or the new Security axis
(proven C1-C6).

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
- Does not claim to protect its own PR via the Security axis (see
  "bootstrap exception" above) -- that PR's protection is the
  pre-existing Diana Gate + required human review, unchanged.
- Does not fabricate successful dynamic tests, semantic-review PASS, or
  provider-sandbox evidence; when actual verifier evidence is
  unavailable, it emits `UNPROVEN`, never a synthetic `PASS`. Synthetic
  fixtures exist only inside `test-security-gate.sh`, never in the real
  CI path.

## Future phases (not started here)

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
bash diana/security/adapters/test-adapters.sh
bash diana/security/dynamic/test-dynamic.sh
bash diana/security/reviewer/test-reviewer.sh
bash diana/security/test-security-gate.sh
```

All of the above are deterministic, offline, and make no changes to this
repository. `test-security-gate.sh` additionally creates and destroys
small, ephemeral, local-only `git init` scratch repositories under a
`mktemp -d` directory (to prove the protected-base extraction end to end,
S2+S3) -- no network access and no changes to this repository. `evidence_model.py` itself takes two arguments (a catalog path
and a runs-file path) and is normally invoked directly for ad hoc checks:

```
python3 diana/security/evidence_model.py diana/security/catalog.json <runs.json>
```
