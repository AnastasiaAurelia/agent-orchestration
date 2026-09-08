# Diana Security semantic reviewer (Security Phase 4)

A thin, deterministic normalizer that turns an already-produced **semantic
review evidence artifact** into [`evidence_model.py`](../evidence_model.py)
run records. This module never runs a model, spawns a reviewer session, or
renders any judgment itself -- a fresh, independent, read-only reviewer
session does that, outside this repository (the same pattern the main
Diana roadmap's Phase 10 Independent Reviewer already established for
implementation-quality review), and hands back a JSON artifact recording
what it inspected, reasoned about, and concluded:

```
fresh, independent, read-only reviewer session   <- NOT this repository
        |
bound semantic-review evidence artifact           <- what reviewer_normalizer.py parses
        |
thin Diana normalization (this module)
        |
evidence_model.py
```

Reuses [`diana/security/adapters/adapter_base.py`](../adapters/adapter_base.py)'s
`verify_identity()`/`verify_target()`/`build_runs()`/`tool_error_runs()`/
`tool_unavailable_runs()`/`NotAuthorized`/`ArtifactError` directly -- a
code review's target is just `{repository, commit}`, the same shape
Phase 2's adapters already verify against, so this is genuine reuse, not
a third reimplementation of the same binding logic. This module is
evidence-producing, not implementation-producing: it never writes,
commits, or edits anything, and has no code path that could.

## A deliberate departure from Phase 1-3's principle

Every prior phase's normalizer *computed* a result from raw,
independently checkable facts (an evidence-item text match, an assertion
outcome, a hash comparison) and never trusted a verifier's own
self-reported conclusion. Semantic review is categorically different:
there is no deeper raw fact for this module to derive a verdict from --
reasoning about architecture, trust boundaries, and business-logic
invariants *is* the judgment being asked for. This module therefore does
accept the reviewer's own `result` field as the verdict, but only once it
survives structural gates that make an unsubstantiated or dishonest claim
mechanically difficult to submit (see below). This is a real, stated
exception to the rest of this track's "never trust self-reported
conclusions" discipline, not an accidental inconsistency.

## Independence and read-only-ness: a necessary, not sufficient, proxy

`reviewer.session_type` must be exactly `"fresh_read_only"`,
`reviewer.independent_from_implementation` must be exactly `true`, and
`execution.mutations_attempted` must be exactly `false`. **This module
cannot cryptographically prove a review was actually conducted by a
fresh, independent session that never touched a mutation approval** --
that is an operational guarantee the surrounding orchestration must
uphold (spawn a genuinely fresh reviewer session, deny any mutation
approval it requests, exactly as the main Diana roadmap's Phase 10
pattern already does for implementation review). This module only
refuses to accept an artifact that doesn't even *claim* those properties;
if a reviewer session requests write/mutation permission, the
orchestrating human/process must stop that reviewer -- this module has no
way to intervene in that decision, only to reject the resulting artifact
afterward if it's honest about what happened.

## "Looks fine" is not evidence

A self-declared `PASS` or `FAIL` must be substantiated
(`reviewer_base.load_review_envelope()`):

- At least `MIN_REASONING_LENGTH` (80) characters of combined
  `architecture_reasoning` + `result_reasoning`.
- Not composed solely of generic reassurance phrases (`VAGUE_PHRASES`:
  "looks fine", "seems secure", "no obvious issue", ...) -- these are
  stripped out and what remains must still clear the length bar.
- At least one `evidence_references` entry.
- The reasoning text must reference at least one of `files_inspected` by
  its literal path.

`UNPROVEN`/`NOT_APPLICABLE`/`ERROR` carry no such bar -- a reviewer saying
"I couldn't establish this" legitimately needs less proof than one
claiming to have established something.

## No fabricated citations

Every `evidence_references[].file` must be a member of `files_inspected`
-- a reviewer cannot cite a file it never declared having looked at.

## Requirement text is tied to the real catalog contract

`requirement` must equal one of the target control's actual
`catalog.json` `required_evidence` strings, checked against a freshly
loaded and re-validated catalog (`validate_catalog.validate()`) -- not
trusted from the artifact, and not assumed unchanged.

## Authorization is catalog-derived, not a hardcoded list

Unlike Phase 2's static adapters, a semantic reviewer is broadly
applicable, so `reviewer_normalizer._authorized_control_ids()` computes
its scope directly from `catalog.json`: any control whose
`verification.modes` includes `SEMANTIC_REVIEW` or `HUMAN`. Every
`human_judgment_required=true` control in the current catalog already
satisfies this (verified empirically). **This deliberately does not
match the Phase 4 kickoff's own illustrative "semantic families" list
one-for-one**: five of those named controls -- `SEC-040`, `SEC-062`,
`SEC-067`, `SEC-070`, `SEC-071` -- do NOT list `SEMANTIC_REVIEW`/`HUMAN`
in their actual `catalog.json` `verification.modes` (their real modes are
`INFRA_CONFIG`/`DYNAMIC_API`/`DEPENDENCY_SCANNER`/`DYNAMIC_DB`/
`PROVIDER_SANDBOX` only). The kickoff instruction itself says "do not
assume this list is exhaustive, use catalog human_judgment_required and
verification modes," and the catalog is the authoritative source per this
track's permanent SOURCE LOCK -- a semantic-review contribution for one of
those five controls is rejected here (`test-reviewer.sh` CASE 14), and
would independently be rejected by `evidence_model.py`'s own capability
check downstream regardless.

Capability is additionally checked *twice*: `verifier_type` must be
`SEMANTIC_REVIEW` or `HUMAN` structurally (`reviewer_base.py`), and
`reviewer_normalizer.py` separately pre-checks it against the *specific
target control's* real `verification.modes` before ever building a run --
an unauthorized capability is rejected here, with a specific reason, not
only caught one layer downstream by `evidence_model.py`.

## AI/LLM constitutional guard (SEC-074, SEC-075)

A `PASS` for either control must never rest on "the model refused" or
"the system prompt tells it not to." `AI_CONSTITUTION_CONTROLS` requires
the artifact to carry `ai_authorization_context.enforced_outside_model:
true` before a `PASS` is accepted -- a reviewer claiming `PASS` without
explicitly asserting that authorization is enforced by code outside the
model is rejected (`ArtifactError` -> `ERROR`), regardless of how
well-reasoned the rest of the artifact is (`test-reviewer.sh` CASE 8a
proves this; CASE 8b shows the same control passing once the flag is
present and substantiated).

## Business logic (SEC-043)

The kickoff spec asks the reviewer to explicitly map actors, trusted
state, allowed state transitions, invariants, replay/reordering
possibilities, and server-side enforcement -- this module does not (and
cannot) verify that the reviewer's reasoning actually constitutes a
correct such analysis; it only enforces the structural substantiation bar
above (non-trivial reasoning, cited files, no vague-only text).
`test-reviewer.sh` CASE 11 uses a fixture written to this standard as an
example, not as proof this module validates the analysis's *correctness*.

## Dependency trust (SEC-061)

Same principle: the reviewer is expected to evaluate provenance,
lockfile integrity, and publisher reputation, not rely on a known-
vulnerability scan alone (that's `diana/security/adapters/osv_scanner_
adapter.py`'s job, and a different control, `SEC-060`). `SEC-062`
(Dependency Confusion) is catalog-excluded from this module entirely, per
"Authorization is catalog-derived" above.

## Tenant/authorization controls

The kickoff spec's guidance ("do not accept frontend filtering as
security evidence") is guidance for what a *good* reviewer artifact's
`architecture_reasoning`/`call_chain` should actually trace (request ->
identity -> authorization -> resource/tenant selection -> DB/storage
operation) -- again, this module enforces substantiation structure, not
the semantic correctness of the trust-boundary analysis itself.

## Result semantics (no new states)

| Reviewer artifact state | Result |
|---|---|
| Substantiated `PASS`, target verified, capability authorized | `SATISFIED` -> contributes toward `PASS` |
| Substantiated `FAIL`, target verified | `VIOLATED` -> contributes toward `FAIL` |
| `NOT_APPLICABLE` | `NOT_APPLICABLE` (no penalty) |
| `UNPROVEN` (reviewer couldn't establish the requirement) | `UNPROVEN` |
| Target/commit doesn't match the caller's expectation | not attributed -> `UNPROVEN` |
| `ERROR` (reviewer self-reports it could not complete reliably) | `ERROR` |
| Malformed artifact, vague/unsubstantiated PASS/FAIL, fabricated citation, unauthorized capability, missing AI-constitution flag on a SEC-074/075 PASS, or `artifact_binding` mismatch | `ERROR` |
| No artifact available | explicit `UNPROVEN`, tagged with the requested control's own permitted capability |

## What this phase does not do

- Does not run a model, spawn a reviewer session, or judge anything
  itself -- it only normalizes a verdict a real reviewer already reached.
- Does not verify the *correctness* of a reviewer's reasoning, only its
  structural substantiation (length, citations, no vague-only text).
- Does not cryptographically prove reviewer independence or read-only-
  ness -- that remains an operational guarantee of whoever spawns the
  real reviewer session.
- Does not touch `diana/gate`, `diana/preflight`, `diana/playwright`,
  `diana/adapters` (the pre-existing AO adapter), `diana/security/
  adapters/`, or `diana/security/dynamic/`.
- Does not change `diana/security/catalog.json`, `validate_catalog.py`,
  or `evidence_model.py`.

## CLI

```
python3 diana/security/reviewer/reviewer_normalizer.py <catalog.json> <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]
```

Prints `{"version": 1, "runs": [...]}`; combine with other verifiers'
runs (Phase 2 adapters, Phase 3 dynamic scenarios) and feed to
`evidence_model.py` directly.

## Tests

`test-reviewer.sh` (20 assertions, offline, synthetic fixtures under
`fixtures/*.json`) proves every required independence property: vague
reviewer text cannot become `PASS`; missing evidence produces `UNPROVEN`;
an explicit concrete flaw produces `FAIL`; malformed output produces
`ERROR`; a wrong target commit is never attributed; an unauthorized
verifier capability can never `PASS` (both an entirely invalid
`verifier_type` value and a valid one this specific control doesn't
permit); AI-model-refusal-alone cannot prove `SEC-074`/`SEC-075`; and
semantic evidence correctly composes with a dynamic/static companion
contribution (proven against `DYNAMIC_API`, `DYNAMIC_DB`, and
`DEPENDENCY_SCANNER` companions, not just one capability) -- including
demonstrating that a `SEMANTIC_REVIEW` contribution alone satisfies the
`human_judgment_required` gate for `SEC-043`/`SEC-061` established back in
Phase 1, while the control's `dynamic_required` gate still needs its own
separate dynamic contribution.
