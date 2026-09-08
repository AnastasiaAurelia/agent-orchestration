# Diana Security static adapters (Security Phase 2)

Thin, deterministic normalizers that turn a **verified scan-evidence
artifact** wrapping an already-produced tool report (a saved JSON file --
real or fixture) into [`evidence_model.py`](../evidence_model.py) run
records. None of these adapters install, invoke, or bundle the underlying
tool, make a network call, or spawn a subprocess -- **Diana orchestrates
tools, it does not reimplement them.** No scanner (Semgrep, Gitleaks,
osv-scanner, Trivy, or any other) is installed anywhere in this repository
by this phase.

## Human review correction: a clean tool report is not proof of a clean target

The first implementation of this phase treated a syntactically valid
"clean" native tool report -- an empty Gitleaks findings array, an
osv-scanner object that merely lacked a `results` key, a Semgrep report
with zero findings -- as sufficient on its own to emit `SATISFIED`
evidence. Human review found this doesn't prove anything about the actual
target: an empty findings array from an empty directory, a stale commit,
a narrow subtree, or a scan that never ran the relevant rule looks
byte-for-byte identical to a clean result from a complete, current,
correctly-configured scan. Three concrete gaps were found and fixed:

1. **No target/commit/scope binding.** Nothing tied a report to the
   repository, commit, or scan scope it claimed to cover, so a clean
   report from the wrong (or no) target was indistinguishable from a
   clean report of the real one.
2. **osv-scanner's parser fell through on missing structure.**
   `raw.get("results", [])` treated an artifact with no `results` key at
   all (`{}`) as "zero vulnerabilities found" -- a fail-*open* default on
   a completely absent field, not a genuine clean scan.
3. **Semgrep's rule table was hardcoded and unverified.** Illustrative
   example `check_id` strings, never checked against a real Semgrep
   registry or a real deployment's ruleset, lived directly in the
   production `ingest()` path and could authorize a real `PASS`.

**Core invariant now enforced: a clean tool report is not proof of a clean
target.** Positive and negative evidence are treated asymmetrically:

- **A recognized finding (`VIOLATED`) is trusted even under partial or
  mismatched scope.** Finding one real, concrete problem does not require
  having looked everywhere first.
- **A clean result (`SATISFIED`) is only trustworthy when the adapter can
  additionally prove** the scan actually completed, is bound to the
  expected target, and covered what the requirement needs covered.
  Absence of findings never implies completeness by itself.

## The verified scan-evidence artifact

Adapters no longer accept a bare native tool report. They require an
**envelope** (see [`adapter_base.py`](adapter_base.py) for the full
schema and validation) wrapping the native report with caller-asserted,
trusted context:

```jsonc
{
  "tool": {"name": "gitleaks", "version": "8.18.1"},
  "execution": {"completed": true},
  "target": {
    "repository": "AnastasiaAurelia/agent-orchestration",
    "commit": "c51ce15a301df2d7804b36d59e9d508b8653249b",
    "root": ".",
    "scope": "full-repo"
  },
  "config": {},
  "scanned_inputs": [],
  "report": { /* raw native tool output */ },
  "report_binding": {"sha256": "<sha256 of the canonical JSON of report>"}
}
```

`target`/`config`/`scanned_inputs` describe what the caller *did* --
they are not derived from the (semi-trusted) native `report`.
`report_binding.sha256` ties the wrapped `report` to that trusted
description so it can't be substituted independently after the fact.

Two layers of checking, both in [`adapter_base.py`](adapter_base.py):

- **`load_envelope()`** -- structural + integrity validation. Raises
  `ArtifactError` (→ `ERROR`) for missing envelope structure,
  `execution.completed` not `true`, or a `report_binding` hash mismatch
  (tampering/substitution). At that point *nothing* in the artifact --
  findings included -- can be trusted.
- **`verify_target(target, expected_target)`** -- compares the artifact's
  declared target against what the *caller* expected (supplied
  separately, never inferred from the artifact itself). Never raises;
  returns a bool + reason. A mismatch, or no expected target supplied at
  all, is not an integrity failure -- it just means a clean result can't
  be trusted as `SATISFIED`. Findings are unaffected either way.

## The integrity invariant from the original implementation (unchanged)

> A static adapter must not be able to manufacture `PASS` merely by
> emitting an arbitrary catalog `required_evidence` string as
> `SATISFIED`.

Each adapter declares an explicit `AUTHORIZED_EVIDENCE` mapping
(`{control_id: [exact required_evidence strings from catalog.json]}`).
[`adapter_base.build_runs()`](adapter_base.py) is the **only** function
that constructs evidence items, and it raises if asked to build a
contribution for any `(control_id, requirement)` pair outside that
mapping. This is enforced *twice*: once here, and again independently in
`evidence_model.py` (a run's `verifier.type` must be a member of the
*target control's* `verification.modes`) -- no `evidence_model.py` change
was needed for this defense-in-depth to already work, in either Phase 2
implementation.

## Result semantics

| Tool/artifact state | Result |
|---|---|
| Clean, target-verified, scope/coverage-complete for the requirement | `SATISFIED` -> contributes toward `PASS` |
| Recognized finding for an authorized requirement (any scope) | `VIOLATED` -> contributes toward `FAIL` |
| Clean, but target unverified / wrong commit / no context / partial scope / incomplete coverage | no contribution -> `UNPROVEN`, never `PASS` |
| Unknown/unmapped finding type | no contribution -> `UNPROVEN`, never `PASS` |
| Tool did not run (no artifact given) | no runs emitted -> `UNPROVEN`, never `PASS` |
| Artifact malformed, execution incomplete, or report_binding mismatch | `ERROR` |

## Adapters in this phase (3 of the 4 "preferred" tools)

| Adapter | Capability | Authorized for | Why not more |
|---|---|---|---|
| [`gitleaks_adapter.py`](gitleaks_adapter.py) | `SECRET_SCANNER` | `SEC-007` requirement 1 only ("no credential/token/private-key literal is committed in source"); `SATISFIED` requires `target.scope == "full-repo"` and a verified target | requirement 2, and all of `SEC-006`/`SEC-065`, make claims about a specific *scope* (a built frontend bundle; "only used server-side") this adapter's target model doesn't yet represent |
| [`osv_scanner_adapter.py`](osv_scanner_adapter.py) | `DEPENDENCY_SCANNER` | `SEC-060` (its only requirement, matched to its exact "no unresolved critical/high finding" wording); `SATISFIED` requires a verified target *and* `scanned_inputs` to cover every manifest the caller expects | `SEC-061`/`SEC-062` also list `DEPENDENCY_SCANNER` but need provenance/registry-reservation checks this tool doesn't do |
| [`semgrep_adapter.py`](semgrep_adapter.py) | `STATIC_ANALYZER` | `SEC-055`, `SEC-056`, but **only** via a rule mapping the *caller* supplies in `artifact["config"]["rule_map"]` -- see below | see "On the Semgrep rule table" |

**Trivy was deliberately not built this phase** -- its natural role
overlaps with `osv_scanner_adapter.py`'s coverage of `SEC-060`, and its
misconfiguration-scanning surface doesn't have a clean, unambiguous
single-requirement mapping the way the three adapters above do. Deferred
to a future, more carefully-scoped change rather than forced.

## On the Semgrep rule table

`semgrep_adapter.py` module-level `ILLUSTRATIVE_TEST_ONLY_RULE_MAP` is
**never read by `ingest()`.** It exists solely so tests can construct a
realistic-looking verified artifact without inventing a mapping ad hoc
per test case. The only rule mapping `ingest()` will ever use is
`artifact["config"]["rule_map"]` -- part of the caller-constructed,
trusted envelope, not this module's own guesswork. An artifact with no
`rule_map` (the default unless a caller deliberately populates one)
authorizes nothing: every mapped control stays `UNPROVEN`. This is by
design (`test-adapters.sh` CASE P10) -- an illustrative, unverified
mapping must never be capable of manufacturing a real `PASS` in
production, no matter how plausible the rule-ID strings look.

A mapped rule must additionally satisfy **both** conditions before a
clean result can emit `SATISFIED` for it: the exact rule appears in the
report's `rules_run`, *and* the artifact's declared target/scope is
verified against the caller's expectation (CASE P11 vs. P12). A finding
from a verified, mapped rule is trusted regardless of scope (CASE
"verified finding under partial scope").

## Adapter CLI

Each adapter is runnable directly for ad hoc use:

```
python3 diana/security/adapters/gitleaks_adapter.py <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]
python3 diana/security/adapters/osv_scanner_adapter.py <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]
python3 diana/security/adapters/semgrep_adapter.py <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]
```

`-` for `<artifact.json>` (or a nonexistent path) means "no tool output to
ingest" -- prints `{"version": 1, "runs": []}` (tool missing, not an
error). `-` for `<expected_target.json>` means "no expectation supplied" --
`verify_target()` then always fails, so no `SATISFIED` contribution can be
emitted (findings are unaffected). Each adapter prints
`{"version": 1, "runs": [...]}`; the `runs` array is
`evidence_model.py`-compatible and can be combined with other adapters'
runs (and hand-authored runs, e.g. from a future semantic reviewer) into
one file and fed to it directly:

```
python3 diana/security/evidence_model.py diana/security/catalog.json <combined-runs.json>
```

## Tests

`test-adapters.sh` (32 assertions, offline, using synthetic fixture
artifacts under `fixtures/*.json` -- never real scan output) proves, for
all three adapters:

- a clean, target-verified, scope-complete scan reaches `SATISFIED` and
  composes correctly through `evidence_model.py` into `PASS`;
- a clean scan with a wrong commit, missing target context, partial
  scope, or (for osv-scanner) incomplete manifest coverage produces **no
  contribution at all**, never `PASS`;
- an artifact whose `report_binding` hash doesn't match its wrapped
  report, or whose `execution.completed` is not `true`, fails closed to
  `ERROR`;
- a recognized finding is trusted (`VIOLATED` → `FAIL`) even under
  partial/mismatched scope -- the positive/negative evidence asymmetry;
- osv-scanner's `{}` (no `results` key) is `ERROR`, never a silent clean
  scan; an unrecognized severity value is also `ERROR`, not silently
  excluded from the critical/high check;
- Semgrep's illustrative rule table cannot authorize `PASS` in production
  (no `config.rule_map` supplied → nothing mapped); a caller-verified
  `rule_map` can, but only when the mapped rule actually ran *and* the
  target/scope was verified;
- tool-missing and artifact-malformed/incomplete are handled distinctly
  (`UNPROVEN` vs. `ERROR`);
- each adapter silently refuses to report on a control outside its own
  `AUTHORIZED_EVIDENCE` mapping, even when that control shares the same
  verifier capability in the catalog.

All tests are deterministic and offline.
