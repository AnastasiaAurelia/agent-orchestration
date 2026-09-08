# Diana Security static adapters (Security Phase 2)

Thin, deterministic normalizers that turn a **verified scan-evidence
artifact** wrapping an already-produced tool report (a saved JSON file --
real or fixture) into [`evidence_model.py`](../evidence_model.py) run
records. None of these adapters install, invoke, or bundle the underlying
tool, make a network call, or spawn a subprocess -- **Diana orchestrates
tools, it does not reimplement them.** No scanner (Semgrep, Gitleaks,
osv-scanner, Trivy, or any other) is installed anywhere in this repository
by this phase.

## Human review history

This phase went through two correction rounds after the first
implementation, both addressing the same underlying theme: **positive
claims about a target need proof, not just plausible-looking data.**

1. **Provenance/scope correction.** The first implementation treated a
   syntactically valid "clean" native report as sufficient on its own for
   `SATISFIED`, without proving the tool scanned the intended target/
   commit/scope, and had a fail-open bug (osv-scanner's `raw.get("results",
   [])` silently treated a missing `results` key as "zero
   vulnerabilities"). Fixed by introducing a verified scan-evidence
   artifact envelope.
2. **Final trust-boundary correction (this round).** Even after (1),
   *findings* were still trusted regardless of target verification --
   too broad. Also: `load_envelope()`'s integrity hash only covered the
   native `report`, not the envelope's other trusted fields; nothing
   validated which *tool* actually produced a report; Semgrep's
   caller-supplied `rule_map` was shape-checked but not authorization-
   checked; and "tool missing" silently produced zero runs instead of an
   explicit result. All four are fixed below.

## Core invariant: identity vs. coverage

**A clean tool report is not proof of a clean target, and neither is a
finding proof about the wrong one.** Positive and negative evidence have
different requirements:

- **A recognized finding (`VIOLATED`) may come from PARTIAL COVERAGE** --
  finding one real problem does not require having scanned everything --
  **but it must still be attributed to the correct TARGET IDENTITY**: the
  same `repository` and `commit` the caller expected
  (`adapter_base.verify_identity()`). A finding from the wrong repository,
  the wrong commit, or an artifact/expectation missing that identity
  entirely is not evidence about the *expected* target at all -- it is
  dropped, never attributed.
- **A clean result (`SATISFIED`) requires target identity AND scan
  coverage** (`adapter_base.verify_target()`: identity plus every other
  expectation the caller supplied, e.g. `scope`) -- identity match alone
  is not enough; the requirement's relevant surface must actually have
  been scanned.

## The verified scan-evidence artifact

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
  "artifact_binding": {"sha256": "<sha256 of the canonical JSON of {tool, execution, target, config, scanned_inputs, report}>"}
}
```

`target`/`config`/`scanned_inputs` describe what the caller *did* -- they
are not derived from the (semi-trusted) native `report`.
`artifact_binding.sha256` covers **every** bound field (`tool`,
`execution`, `target`, `config`, `scanned_inputs`, `report`), not just the
report -- changing any one of them without recomputing the hash is
detected. **This hash detects corruption or substitution of the
artifact's own declared fields after the binding was computed. It does
NOT authenticate who produced the artifact** -- there is no PKI,
signature, or attestation service anywhere in this module, and none
should be added here; that is a different, larger problem this phase
deliberately does not attempt to solve.

Checks, all in [`adapter_base.py`](adapter_base.py), none of them making a
network call:

- **`load_envelope()`** -- structural + integrity validation. Raises
  `ArtifactError` (→ `ERROR`) for missing structure, `execution.completed`
  not `true`, or an `artifact_binding` hash mismatch. At that point
  nothing in the artifact -- findings included -- can be trusted.
- **`verify_tool_identity(tool, expected_name)`** -- each adapter calls
  this with its own exact expected tool name (`"gitleaks"`,
  `"osv-scanner"`, `"semgrep"`). A syntactically compatible report
  declaring a *different* tool is rejected (`ArtifactError` → `ERROR`),
  not guessed at.
- **`verify_identity(target, expected_target)`** -- repository + commit
  only. Both the artifact's target and the caller's expectation must
  supply non-empty values for both, or identity cannot be established.
  Gates whether a finding (`VIOLATED`) can be attributed to the expected
  target.
- **`verify_target(target, expected_target)`** -- identity plus every
  other key the caller supplied (e.g. `scope`). Gates whether a clean
  result (`SATISFIED`) can be trusted.

## The integrity invariant from the original implementation (unchanged)

> A static adapter must not be able to manufacture `PASS` merely by
> emitting an arbitrary catalog `required_evidence` string as
> `SATISFIED`.

Each adapter declares an explicit `AUTHORIZED_EVIDENCE` mapping
(`{control_id: [exact required_evidence strings from catalog.json]}`).
[`adapter_base.build_runs()`](adapter_base.py) is the **only** function
that constructs evidence items, and raises `NotAuthorized` for any
`(control_id, requirement)` pair outside that mapping -- every adapter's
`ingest()` catches this as a defense-in-depth backstop and converts it to
an `ERROR` run rather than letting it escape uncaught. This is enforced
*twice*: once here, and again independently in `evidence_model.py` (a
run's `verifier.type` must be a member of the *target control's*
`verification.modes`) -- no `evidence_model.py` change was needed for
this defense-in-depth to already work, across any of the three Phase 2
implementations so far.

**Semgrep's `config.rule_map` gets the same authorization check, not just
a shape check**: every entry's `[control_id, requirement]` pair must name
a control this adapter is authorized for and the exact authorized
requirement text -- a caller cannot smuggle authorization for a control
this adapter was never designed to prove by writing it into the
artifact's own config block (`ArtifactError` → `ERROR` otherwise).

## Tool-missing is an explicit result, not silence

When no artifact is available, adapters build one explicit run per
requested (authorized) control via `adapter_base.tool_unavailable_runs()`
-- `applicability: "UNKNOWN"`, empty evidence, no `tool_error` -- which
deterministically aggregates to `UNPROVEN` in `evidence_model.py`, never
`NOT_APPLICABLE`, never `PASS`, and distinct from the control simply being
absent from the results. More generally, **once an artifact has been
successfully parsed, every requested-authorized control always gets a
run** -- `build_runs()`'s `requested_control_ids` parameter fills in an
empty-evidence run for any control that received no contribution, so a
"clean but insufficient coverage" scan (e.g. correct identity, partial
scope) also surfaces as an explicit `UNPROVEN`, not an absent result.

## Result semantics

| Tool/artifact state | Result |
|---|---|
| Clean, correct target identity + coverage complete for the requirement | `SATISFIED` -> contributes toward `PASS` |
| Recognized finding, correct target identity (any coverage) | `VIOLATED` -> contributes toward `FAIL` |
| Recognized finding, wrong/missing target identity | not attributed -> `UNPROVEN` |
| Clean, but identity unverified / wrong commit / no context / partial scope / incomplete coverage | `UNPROVEN` |
| Unknown/unmapped finding type | `UNPROVEN` |
| Tool did not run (no artifact given) | explicit `UNPROVEN` |
| Artifact malformed, execution incomplete, wrong tool identity, artifact_binding mismatch, or unauthorized rule_map entry | `ERROR` |

## Adapters in this phase (3 of the 4 "preferred" tools)

| Adapter | Capability | Tool identity | Authorized for | Why not more |
|---|---|---|---|---|
| [`gitleaks_adapter.py`](gitleaks_adapter.py) | `SECRET_SCANNER` | `"gitleaks"` | `SEC-007` requirement 1 only; `SATISFIED` also requires `target.scope == "full-repo"` | requirement 2, and all of `SEC-006`/`SEC-065`, make a scope claim this adapter's model doesn't yet represent |
| [`osv_scanner_adapter.py`](osv_scanner_adapter.py) | `DEPENDENCY_SCANNER` | `"osv-scanner"` | `SEC-060` (its only requirement); `SATISFIED` also requires `scanned_inputs` to cover every expected manifest | `SEC-061`/`SEC-062` need provenance/registry-reservation checks this tool doesn't do |
| [`semgrep_adapter.py`](semgrep_adapter.py) | `STATIC_ANALYZER` | `"semgrep"` | `SEC-055`, `SEC-056`, only via a caller-supplied, authorization-checked `config.rule_map` | see "On the Semgrep rule table" |

**Trivy was deliberately not built this phase** -- its natural role
overlaps with `osv_scanner_adapter.py`'s coverage of `SEC-060`, and its
misconfiguration-scanning surface doesn't have a clean, unambiguous
single-requirement mapping the way the three adapters above do. Deferred
to a future, more carefully-scoped change rather than forced.

## On the Semgrep rule table

`ILLUSTRATIVE_TEST_ONLY_RULE_MAP` is **never read by `ingest()`.** It
exists solely so tests can construct a realistic-looking verified
artifact. The only rule mapping `ingest()` ever consults is
`artifact["config"]["rule_map"]`; an artifact with none authorizes
nothing. Every entry in a supplied `rule_map` is validated against
`AUTHORIZED_EVIDENCE` -- an entry naming an unknown control, an
unauthorized control, the wrong requirement text, or a malformed
`check_id` fails the whole artifact closed to `ERROR`.

## Adapter CLI

```
python3 diana/security/adapters/gitleaks_adapter.py <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]
python3 diana/security/adapters/osv_scanner_adapter.py <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]
python3 diana/security/adapters/semgrep_adapter.py <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]
```

`-` for `<artifact.json>` (or a nonexistent path) means "no tool output to
ingest." `-` for `<expected_target.json>` means "no expectation supplied."
Each adapter prints `{"version": 1, "runs": [...]}`; the `runs` array is
`evidence_model.py`-compatible and can be combined with other adapters'
runs into one file and fed to it directly:

```
python3 diana/security/evidence_model.py diana/security/catalog.json <combined-runs.json>
```

## Tests

`test-adapters.sh` (46 assertions, offline, using synthetic fixture
artifacts under `fixtures/*.json` -- never real scan output) covers, for
all three adapters, the 17 required cases (T1-T17: identity-vs-coverage
split for findings and clean results, missing expected commit, tool
identity mismatch, artifact-binding tampering across all five bound
fields, Semgrep rule-map authorization, explicit tool-missing) plus every
previously-proven behavior from the earlier two implementations,
re-verified against the current artifact shape.

All tests are deterministic and offline.
