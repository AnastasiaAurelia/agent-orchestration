# Diana Security static adapters (Security Phase 2)

Thin, deterministic normalizers that turn an **already-produced** tool
report (a saved JSON file -- real or fixture) into
[`evidence_model.py`](../evidence_model.py) run records. None of these
adapters install, invoke, or bundle the underlying tool, make a network
call, or spawn a subprocess -- **Diana orchestrates tools, it does not
reimplement them.** No scanner (Semgrep, Gitleaks, osv-scanner, Trivy, or
any other) is installed anywhere in this repository by this phase.

## The integrity invariant this whole module exists to enforce

> A static adapter must not be able to manufacture `PASS` merely by
> emitting an arbitrary catalog `required_evidence` string as
> `SATISFIED`.

Each adapter declares an explicit, hardcoded `AUTHORIZED_EVIDENCE` mapping
(`{control_id: [exact required_evidence strings from catalog.json]}`).
[`adapter_base.build_runs()`](adapter_base.py) is the **only** function
that constructs evidence items, and it raises if asked to build a
contribution for any `(control_id, requirement)` pair outside that
mapping. Each adapter module is separately responsible for never handing
`build_runs()` anything it isn't itself authorized to claim -- e.g. an
unrecognized Semgrep `check_id` is silently dropped before it ever reaches
that function, not fabricated into evidence.

This is enforced *twice*: once here (an adapter can't emit evidence for a
control/requirement pair it isn't mapped to), and again independently in
[`evidence_model.py`](../evidence_model.py) (a run's `verifier.type` must
be a member of the *target control's* `verification.modes`, checked
against the catalog, not the adapter). An adapter bug that somehow got
past this file's checks would still be caught by evidence_model.py's own
capability check -- see `diana/security/test-evidence-model.sh` CASE A/F
for that proof, which predates this phase and needed no change here.

## Result semantics

| Tool state | Adapter behavior | evidence_model.py result |
|---|---|---|
| Clean scan, meaningfully covering an authorized requirement | `SATISFIED` contribution | contributes toward `PASS` |
| Recognized finding for an authorized requirement | `VIOLATED` contribution | contributes toward `FAIL` |
| Unknown/unmapped finding type | no contribution (silently dropped) | `UNPROVEN` (missing required evidence), never `PASS` |
| Tool did not run (no output artifact given) | no runs emitted at all | `UNPROVEN` ("no verification runs submitted"), never `PASS` |
| Tool output exists but can't be parsed as this tool's format | `tool_error`-flagged run per requested control | `ERROR` |

"Meaningfully covering" matters: a clean result only counts when the
adapter can *prove* the relevant check actually ran. The Semgrep adapter
is the concrete example -- it requires the report's `rules_run` list to
include a rule mapped to the control in question before treating zero
matching findings as `SATISFIED`; if the mapped rule never ran, that
control gets no contribution at all (see `test-adapters.sh` CASE S4).

## Adapters in this phase (3 of the 4 "preferred" tools)

| Adapter | Capability | Authorized for | Why not more |
|---|---|---|---|
| [`gitleaks_adapter.py`](gitleaks_adapter.py) | `SECRET_SCANNER` | `SEC-007` requirement 1 only ("no credential/token/private-key literal is committed in source") | `SEC-007`'s 2nd requirement, and all of `SEC-006`/`SEC-065`, make claims about a specific *scope* (a built frontend bundle; "only used server-side") a plain source-tree scan can't establish without knowing what was actually scanned |
| [`osv_scanner_adapter.py`](osv_scanner_adapter.py) | `DEPENDENCY_SCANNER` | `SEC-060` (its only requirement, matching its exact "no unresolved critical/high finding" wording -- LOW/MEDIUM findings alone still satisfy it) | `SEC-061`/`SEC-062` also list `DEPENDENCY_SCANNER`, but a known-vulnerability lookup doesn't establish "provenance/integrity" or "reserved private registry name" -- different checks entirely |
| [`semgrep_adapter.py`](semgrep_adapter.py) | `STATIC_ANALYZER` | `SEC-055`, `SEC-056` (each via a small illustrative `check_id -> (control, requirement)` table) | See "On the Semgrep rule table" below |

**Trivy was deliberately not built this phase.** Its natural role
(dependency/container/IaC scanning) overlaps heavily with what
`osv_scanner_adapter.py` already covers for `SEC-060`, and its
misconfiguration-scanning surface (`SEC-052` debug-mode, exposed-config
controls, etc.) doesn't have a clean, unambiguous single-requirement
mapping the way the three adapters above do. Rather than force a mapping
that risks the exact overclaiming bug this phase exists to prevent,
building a Trivy adapter is left to a future, more carefully-scoped
change -- "smallest sufficient," not "cover every named tool shallowly."

## On the Semgrep rule table

Semgrep is a general pattern-matching engine, not a single-purpose tool
like Gitleaks or osv-scanner -- its `check_id` can mean anything depending
on which rules were configured. **The `RULE_MAP` entries in
`semgrep_adapter.py` are illustrative example rule-ID strings, not
verified against Semgrep's live public registry** (this environment makes
no network calls and does not run Semgrep). A real deployment must
populate or replace this table with the exact rule IDs from its actual
configured ruleset. An unrecognized `check_id` correctly stays unmapped
(`UNPROVEN`, never `PASS`) regardless -- an incomplete or wrong table
fails closed, it does not overclaim.

## Adapter CLI

Each adapter is runnable directly for ad hoc use:

```
python3 diana/security/adapters/gitleaks_adapter.py <report.json|-> <control_id> [control_id...]
python3 diana/security/adapters/osv_scanner_adapter.py <report.json|-> <control_id> [control_id...]
python3 diana/security/adapters/semgrep_adapter.py <report.json|-> <control_id> [control_id...]
```

`-` (or a nonexistent path) means "no tool output to ingest" -- the
adapter prints `{"version": 1, "runs": []}` (tool missing, not an error).
Each prints `{"version": 1, "runs": [...]}`; the `runs` array is
`evidence_model.py`-compatible and can be combined with other adapters'
runs (and hand-authored runs, e.g. from a future semantic reviewer) into
one file and fed to it directly:

```
python3 diana/security/evidence_model.py diana/security/catalog.json <combined-runs.json>
```

## Tests

`test-adapters.sh` proves, for all three adapters and using committed
fixture reports (`fixtures/*.json` -- small, synthetic, not real scan
output) rather than a live tool invocation:

- a clean scan alone is `PASS` only when the adapter's authorization
  actually covers the control's *entire* `required_evidence` contract
  (`SEC-060`), and correctly stays `UNPROVEN` when it only covers part of
  it (`SEC-007`), until a second, appropriately-capable run supplies the
  rest;
- a real finding maps to `FAIL`;
- an unmapped/unrecognized finding type produces no contribution at all;
- a clean result is not treated as evidence when the adapter can't prove
  the relevant check actually ran (Semgrep only);
- tool-missing and tool-execution-failure are handled distinctly
  (`UNPROVEN` vs. `ERROR`);
- each adapter silently refuses to report on a control outside its own
  `AUTHORIZED_EVIDENCE` mapping, even when that control shares the same
  verifier capability in the catalog.

All tests are deterministic and offline.
