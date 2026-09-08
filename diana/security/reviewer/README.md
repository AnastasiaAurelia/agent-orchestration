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

A self-declared `PASS`, `FAIL`, or `NOT_APPLICABLE` must be substantiated
(`reviewer_base.load_review_envelope()`):

- At least `MIN_REASONING_LENGTH` (80) characters of combined
  `architecture_reasoning` + `result_reasoning`.
- Not composed solely of generic reassurance phrases (`VAGUE_PHRASES`:
  "looks fine", "seems secure", "no obvious issue", ...) -- these are
  stripped out and what remains must still clear the length bar.
- At least one `evidence_references` entry.
- The reasoning text must reference at least one of `files_inspected` by
  its literal path.

Only `UNPROVEN`/`ERROR` carry no such bar -- a reviewer saying "I
couldn't establish this" legitimately needs less proof than one claiming
to have established (or ruled out) something. `NOT_APPLICABLE` used to
carry a much weaker bar than `PASS`/`FAIL`; it no longer does (see
"NOT_APPLICABLE must be proven" below).

## Citation self-consistency, not citation authenticity

Every `evidence_references[].file` must be a member of `files_inspected`
-- a reviewer cannot cite a file it never declared having looked at.
**This is a self-consistency check on the artifact's own declarations,
not independent proof.** It does not verify that the cited file actually
exists in the reviewed repository at that commit, and it does not verify
that the reviewer genuinely opened it -- both remain declarations from
the artifact's producer. Independently attesting either would require
checking against the real Git object store, which Phase 4 deliberately
does not build (see "Trust boundary" below). Earlier documentation for
this phase overclaimed this as "no fabricated citations"; that language
has been corrected here and in `reviewer_base.py`'s error messages.

## NOT_APPLICABLE must be proven

`NOT_APPLICABLE` means explicit evidence establishes that the control is
*irrelevant* to this target -- not:

- "the reviewer chose not to investigate,"
- "probably not applicable,"
- evidence was unavailable, or
- generic uncertainty.

All four of those are `UNPROVEN`, not `NOT_APPLICABLE`. Concretely,
`NOT_APPLICABLE` requires (`reviewer_base.load_review_envelope()`):

- Target identity verified (the existing `target`/`expected_target`
  check applies identically regardless of result).
- The same structural substantiation bar as `PASS`/`FAIL`: substantive,
  non-vague reasoning; at least one `evidence_references` entry; the
  reasoning must name an inspected file -- i.e. applicability must be
  *shown*, tied to the actual inspected scope, not merely asserted.
- No unresolved applicability assumption: `reviewer_normalizer.py`
  downgrades a `NOT_APPLICABLE` with a non-empty `unresolved_assumptions`
  to `UNPROVEN` (see below) -- an open question that could make the
  control applicable after all means applicability was never actually
  established.

This module does not programmatically cross-check the reviewer's stated
reasoning against `catalog.json`'s `applicability.signals` for the
control -- doing so would require semantically parsing free-text
reasoning against a signal list, a materially larger and more speculative
addition than Phase 4's scope. It relies on the structural substantiation
bar plus the requirement that the reviewer's own prose name which
applicability signal or feature class is absent or irrelevant, as with
every other semantic verdict in this phase: substantiation, not
correctness, is what this module verifies.

## Unresolved assumptions block PASS (and NOT_APPLICABLE)

`result=PASS` (or `NOT_APPLICABLE`) with a non-empty
`unresolved_assumptions` is inconsistent with what those results are
supposed to mean: the requirement (or its irrelevance) has not actually
been established while something material remains unresolved.
`reviewer_normalizer.ingest()` downgrades such an artifact to `UNPROVEN`
before it reaches evidence-model aggregation. `FAIL` is deliberately
**not** downgraded this way: a concrete, cited flaw must stay visible as
`FAIL` even if unrelated assumptions remain open elsewhere in the
review -- downgrading a real flaw to `UNPROVEN` merely because some
other, unrelated assumption exists would hide a genuine problem.

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

`ai_authorization_context` is an explicitly schema-defined, optional
top-level field (`reviewer_base.OPTIONAL_ENVELOPE_FIELDS`), structurally
validated (must be an object with a boolean `enforced_outside_model`)
whenever present, required/relevant only for a `PASS` on
`AI_CONSTITUTION_CONTROLS`, and irrelevant/optional for every other
control. Critically, it is covered by `artifact_binding` exactly like
every other allowed field (see "Artifact integrity" below) -- it cannot
be flipped from `false` to `true`, or added after the fact, without
invalidating the hash.

## Artifact integrity: `artifact_binding` covers every allowed field

`artifact_binding.sha256` is computed over the canonical JSON of **every
allowed top-level field except `artifact_binding` itself** --
`reviewer_base.canonical_artifact_hash()` builds this set dynamically
from `ALLOWED_ENVELOPE_FIELDS` rather than a fixed, hand-maintained field
list, so any current or future optional field (including
`ai_authorization_context`) is automatically covered without a code
change to the hash function itself. An unknown top-level field is
rejected outright (`ArtifactError`), not silently dropped from the hash.
This closes the gap where a field like `ai_authorization_context` could
previously be added or tampered with after the artifact was hashed
without detection: `reviewer.session_type`/`independent_from_implementation`,
`execution.mutations_attempted`, `evidence_references`, and
`ai_authorization_context` are all now bound.

**What this proves, and what it doesn't:** `artifact_binding` proves
internal artifact integrity only -- that the artifact has not been
edited since the hash was computed. It does **not** authenticate who
produced the artifact (no signature, no PKI -- deliberately out of scope
for Phase 4) and does not independently prove the reviewer session was
genuinely fresh, independent, or read-only. Real reviewer orchestration
must still enforce those properties operationally (spawn a genuinely
fresh, read-only session; deny any mutation approval it requests) -- this
module can only refuse an artifact that doesn't even claim them, or that
was edited after being bound.

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
| Substantiated `PASS`, target verified, capability authorized, no unresolved assumptions | `SATISFIED` -> contributes toward `PASS` |
| Substantiated `FAIL`, target verified | `VIOLATED` -> contributes toward `FAIL` (unaffected by unresolved assumptions) |
| Substantiated `NOT_APPLICABLE`, target verified, no unresolved assumptions | `NOT_APPLICABLE` (no penalty) |
| `PASS` or `NOT_APPLICABLE` with a non-empty `unresolved_assumptions` | downgraded to `UNPROVEN` |
| `UNPROVEN` (reviewer couldn't establish the requirement) | `UNPROVEN` |
| Target/commit doesn't match the caller's expectation | not attributed -> `UNPROVEN` |
| `ERROR` (reviewer self-reports it could not complete reliably) | `ERROR` |
| Malformed artifact, vague/unsubstantiated PASS/FAIL/NOT_APPLICABLE, citation inconsistent with `files_inspected`, unknown top-level field, unauthorized capability, missing AI-constitution flag on a SEC-074/075 PASS, or `artifact_binding` mismatch | `ERROR` |
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
- Does not independently verify that a cited file exists in the reviewed
  repository at the reviewed commit, or that the reviewer actually opened
  it -- only that the citation is self-consistent with the artifact's own
  `files_inspected` declaration (see "Citation self-consistency" above).
- Does not sign or cryptographically authenticate artifact provenance --
  `artifact_binding` proves internal integrity (untampered-since-hashed),
  not producer identity.

## Forward-looking note for Security Phase 5 (Gate + CI Integration)

The trust boundary documented above -- an artifact proves internal
consistency but not reviewer identity, and does not independently prove a
claimed inspected path was actually opened -- must carry into Phase 5's
design rather than be silently assumed away. Real reviewer orchestration
(spawning the fresh, read-only session that produces these artifacts, and
wiring its output into CI) must enforce fresh/read-only access as an
operational control outside this module; Phase 5 should not treat a
structurally valid, hash-bound artifact as proof that the underlying
review process itself was trustworthy, only as proof that the artifact
it receives hasn't been altered since whatever process produced it
finished.

## CLI

```
python3 diana/security/reviewer/reviewer_normalizer.py <catalog.json> <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]
```

Prints `{"version": 1, "runs": [...]}`; combine with other verifiers'
runs (Phase 2 adapters, Phase 3 dynamic scenarios) and feed to
`evidence_model.py` directly.

## Tests

`test-reviewer.sh` (35 assertions, offline, synthetic fixtures under
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

The final semantic evidence integrity correction (human review) added:

- **A1-A5**: a valid artifact with `ai_authorization_context` is accepted
  and correctly bound (A1); tampering `ai_authorization_context`,
  `reviewer` session metadata, or `evidence_references` after the binding
  was computed is caught as a hash mismatch (A2, A3, A5); an unknown
  top-level field is rejected outright (A4).
- **N1-N5** (N1 = CASE 9): a well-substantiated explicit `NOT_APPLICABLE`
  is accepted (N1); hedged/uncertain reasoning ("probably not
  applicable") and zero evidence references are never accepted as
  `NOT_APPLICABLE` (N2, N3); an otherwise-substantiated `NOT_APPLICABLE`
  with an unresolved applicability assumption, or for the wrong target
  commit, downgrades/fails-closed to `UNPROVEN` (N4, N5).
- **U1-U5**: `PASS` with no unresolved assumptions stays `PASS` (U1); a
  `PASS` or `NOT_APPLICABLE` with a non-empty `unresolved_assumptions` is
  downgraded to `UNPROVEN`, including when a full companion contribution
  would otherwise complete the control's evidence (U2, U4, U5); a
  concrete, cited `FAIL` remains visible even with an unrelated unresolved
  assumption present (U3).
