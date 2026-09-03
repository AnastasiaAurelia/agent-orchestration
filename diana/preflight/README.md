# Diana Preflight v0

`preflight.py` is a deterministic, applicability-aware quality/safety scanner.
It makes no LLM calls and no network calls. It reads a repository directory
and emits one JSON result listing every catalog check's applicability and
outcome.

**Preflight detects. Diana Gate decides.** Preflight never produces a
merge decision (`PASS`/`FAIL`/`REQUIRE_HUMAN`) — that remains
[`diana/gate/diana-gate.py`](../gate/diana-gate.py)'s sole responsibility.
Preflight only reports, per check: is this check applicable to this repo,
and if so, did it pass. The two components run as separate processes and
neither imports the other.

## Usage

```
python3 preflight.py REPO_ROOT
```

Exit codes:

- `0`: the scan completed. Individual checks may have `FAIL`ed — that is
  data in the JSON result, not a tool failure.
- `1`: the tool itself could not run (bad usage, missing/unreadable repo
  path, internal error). Fail closed: only an `error` field is emitted, no
  `checks` array, so a caller cannot mistake "could not scan" for "scanned
  clean."

## Output shape

```json
{
  "version": 1,
  "checks": [
    {
      "id": "exposed-secret-config-files",
      "category": "security",
      "severity": "BLOCKER",
      "check_type": "DETERMINISTIC",
      "evidence_required": true,
      "auto_fixable": false,
      "applicable": true,
      "result": "PASS",
      "detail": ["no suspicious secret/config filenames found"]
    }
  ]
}
```

If a check is not applicable to the scanned repository, `applicable` is
`false` and `result` is always `SKIP` — an irrelevant check never produces a
failure.

## v0 catalog (6 checks)

| id | category | applicable when | severity |
|---|---|---|---|
| `exposed-secret-config-files` | security | always | BLOCKER |
| `frontend-client-secret-leakage` | security | frontend detected | BLOCKER |
| `localhost-staging-url-residue` | quality | frontend detected | BLOCKER |
| `accidental-noindex` | findability | frontend detected | BLOCKER |
| `build-test-evidence-present` | process | always | BLOCKER |
| `stack-specific-safety-configuration` | security | Stripe or Convex detected | BLOCKER |

This is intentionally not the full future ~37-check catalog (see
`MEMORY.md` §10, §18 Phase 8). It is six high-value checks chosen to prove
the applicability-aware, deterministic pattern end to end.

**Applicability detection** is a handful of small, purpose-built functions
(`is_frontend`, `stripe_detected`, `convex_detected`) reading `package.json`,
common frontend entry files, `requirements.txt`/`pyproject.toml`, and the
presence of a `convex/` directory. This is deliberately not a generic
capability-registry abstraction — each check owns its own applicability
function directly, matching MEMORY.md §17's "no premature capability
registry" and "no provider-specific logic" (no SDKs, no live provider API
calls — plain filesystem/text pattern matching only).

## Known limitations (v0)

- Detection is filename/extension/text-pattern based, not AST-aware. It can
  miss obfuscated cases and, in principle, could still false-positive on an
  unusual layout; the fixture suite (`test-preflight.sh`) documents the
  specific false-positive case it is proven against (`.env.example`-style
  safe suffixes, and a documentation file that merely mentions "secrets").
- `stack-specific-safety-configuration` bundles Stripe and Convex checks
  under one catalog id for v0. Phase 8 should split these into distinct ids
  as the catalog grows past a handful of stacks.
- Fail-closed behavior for malformed input is proven specifically for the
  check whose evidence source is directly unreadable
  (`build-test-evidence-present` when `package.json` cannot be parsed) and
  for tool-level invocation errors (bad/missing repo path). Broader
  "inconclusive applicability" handling (e.g., forcing frontend-dependent
  checks to stay applicable when `package.json` is malformed and no other
  frontend signal exists) is left as a documented gap, not silently assumed
  safe — see `test-preflight.sh`'s CASE F comment.
- No `AUTOMATED EXTERNAL` (e.g. Playwright, DNS) or `HUMAN` tier checks are
  implemented here; those remain later phases (MEMORY.md §10, Phase 3/8).

## Composing with Diana Gate

`reduce_for_gate.py` reduces preflight's richer per-check objects to the
exact 4-field shape `diana-gate.py`'s input schema requires
(`id`, `applicable`, `severity`, `result`):

```
python3 preflight.py REPO_ROOT | python3 reduce_for_gate.py
```

The reduced array is the value for the gate input's `preflight` field. This
mirrors the existing `diana/ci/build-gate-input.py` adapter pattern: a small,
single-purpose translation step between two components that each stay
independently testable. `test-gate-integration.sh` proves this composition
end to end — including a case where preflight's own `BLOCKER`/`FAIL`
finding is what drives Diana Gate's `FAIL`, independent of the declared
diff risk.

## Tests

- `test-preflight.sh` — the fixture matrix (CASE A–F from the Phase 2 spec).
- `test-gate-integration.sh` — proves preflight output flows into Diana
  Gate through `reduce_for_gate.py` without merging the two implementations.
