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

## Catalog (Phase 8, 10 checks)

Grown from evidence, not from a numeric target — see "What Phase 8
deliberately did not add" below for what was inspected and left out.

| id | category | applicable when | severity |
|---|---|---|---|
| `exposed-secret-config-files` | security | always | BLOCKER |
| `frontend-client-secret-leakage` | security | frontend detected | BLOCKER |
| `localhost-staging-url-residue` | quality | frontend detected | BLOCKER |
| `accidental-noindex` | findability | frontend detected | BLOCKER |
| `build-test-evidence-present` | process | always | BLOCKER |
| `stripe-webhook-signature-verification` | security | Stripe detected | BLOCKER |
| `convex-auth-config-present` | security | Convex detected | BLOCKER |
| `oversized-static-assets` | quality | a committed image/font/video asset exists | WARNING |
| `dependency-lockfile-present` | process | `package.json`, or `pyproject.toml` with a `[tool.poetry]` table, exists | WARNING |
| `missing-404-page-evidence` | findability | routed Next.js app or `public/index.html`-rooted static site | WARNING |

This is intentionally not the full future ~37-check catalog (see
`MEMORY.md` §10, §18 Phase 8) — see "What Phase 8 deliberately did not add"
for the categories inspected and left out, and why.

**Applicability detection** is a handful of small, purpose-built functions
(`is_frontend`, `stripe_detected`, `convex_detected`, `uses_next`,
`is_routed_web_app`, `has_static_binary_assets`, `has_dependency_manifest`)
reading `package.json`, common frontend entry files,
`requirements.txt`/`pyproject.toml`, and directory/file presence. This is
deliberately not a generic capability-registry abstraction — each check
owns its own applicability function directly, matching MEMORY.md §17's "no
premature capability registry" and "no provider-specific logic" (no SDKs,
no live provider API calls — plain filesystem/text pattern matching only).

### `BLOCKER` vs `WARNING`

The gate input schema has always accepted `severity: "WARNING"`
(`diana/gate/diana-gate.py`'s `PREFLIGHT_RESULTS`/severity validation
predates this phase), but v0's six checks were all `BLOCKER` and so never
exercised it. Phase 8's three new checks are deliberately `WARNING`:
asset size, lockfile presence, and a missing custom 404 page are quality/
reproducibility signals worth surfacing, not safety-blocking conditions —
an applicable `WARNING` check that `FAIL`s is recorded in the gate's
`checks` output but never turns the gate decision itself to `FAIL` (see
`test-gate-integration.sh`'s `warning-fail-does-not-block` case, added this
phase). No gate code changed to support this; the existing generic
severity handling already covered it.

### v0 migration: the Stripe/Convex split

v0's `stack-specific-safety-configuration` bundled two unrelated stacks
under one catalog id with no fixture-based regression coverage for either
concern (confirmed by inspection — `test-preflight.sh` only ever asserted
its `SKIP` path). Phase 8 splits it into
`stripe-webhook-signature-verification` and `convex-auth-config-present`,
each independently applicable and now each with real PASS/FAIL fixture
coverage. Detection logic is unchanged from v0, just separated; severity
stays `BLOCKER` for both (a missing webhook signature check or a missing
Convex auth config are genuine security-relevant gaps, matching the
original bundled check's severity).

### Threshold and convention choices

- `oversized-static-assets`: 1,000,000 bytes (~1 MB) per committed image/
  font/video file, excluding anything under an already-ignored build/vendor
  directory (`IGNORED_DIRS`). Deliberately conservative — common
  web-performance guidance treats a single raw web asset at or above 1 MB
  as needing compression before shipping regardless of format. Not a
  security check; `WARNING` severity.
- `dependency-lockfile-present`: applies to any `package.json` (npm/yarn/
  pnpm; the file's own parse-validity doesn't matter — a malformed
  `package.json` still proves an npm-style project exists, and the check
  only asks whether a sibling lockfile is committed) or a `pyproject.toml`
  that actually declares a `[tool.poetry]` table (not merely any
  `pyproject.toml` — this repository's own `backend-only` fixture has one
  for pytest config only, and correctly stays inapplicable for this check).
- `missing-404-page-evidence`: applicable only for a Next.js app with a
  `pages/` or `app/` router directory, or a classic `public/index.html`-
  rooted static site — the two conventions where a custom 404 is a
  well-established, low-ambiguity practice. A plain single-file frontend
  demo (this repo's own `frontend-clean` fixture: root `index.html`, no
  router, no `public/`) deliberately stays `SKIP`, matching the "library/
  package → website 404 checks SKIP" applicability principle.

## Known limitations

- Detection is filename/extension/text-pattern based, not AST-aware. It can
  miss obfuscated cases and, in principle, could still false-positive on an
  unusual layout; the fixture suite (`test-preflight.sh`) documents the
  specific false-positive case it is proven against (`.env.example`-style
  safe suffixes, and a documentation file that merely mentions "secrets").
- Fail-closed behavior for malformed input is proven specifically for the
  check whose evidence source is directly unreadable
  (`build-test-evidence-present` when `package.json` cannot be parsed) and
  for tool-level invocation errors (bad/missing repo path). Broader
  "inconclusive applicability" handling (e.g., forcing frontend-dependent
  checks to stay applicable when `package.json` is malformed and no other
  frontend signal exists) is left as a documented gap, not silently assumed
  safe — see `test-preflight.sh`'s CASE F comment.
- `missing-404-page-evidence` only recognizes two routing conventions
  (Next.js `pages`/`app`, and `public/index.html`-rooted static sites).
  Other frameworks with their own routed-page conventions (Nuxt, SvelteKit,
  Remix, Astro) are not yet covered — narrower applicability was chosen
  over guessing at conventions not verified against a real fixture.
- `dependency-lockfile-present` checks npm/yarn/pnpm and Poetry only; other
  ecosystems (pip without Poetry, Go modules, Cargo, etc.) are not covered
  in this phase.

## What Phase 8 deliberately did not add

Inspected and left out, rather than implemented with weak signal:

- **`robots.txt`/sitemap presence** beyond what `accidental-noindex`
  already checks (a `Disallow: /` with no `Allow`). A dedicated "sitemap
  referenced" check was considered and rejected: unlike a lockfile, there
  is no near-universal convention that a legitimate small site must ship a
  sitemap, so a generic "missing sitemap" finding would carry poor
  signal-to-noise and mostly flag correct, intentional configurations.
- **Metadata/Open Graph tag presence.** Rejected for this phase: the
  actual convention varies too much by framework (raw `<meta>` tags in a
  static HTML entry point vs. Next.js's `generateMetadata`/`export const
  metadata` API vs. Vue head-management libraries) to check reliably
  without a much higher false-negative rate than the rest of this catalog.
- **`AUTOMATED_EXTERNAL` checks** (Playwright user flows, SPF/DKIM/DMARC,
  Core Web Vitals, analytics beacons, Stripe webhook *delivery*
  verification, rate-limit probes, mobile flows). `diana/playwright`
  already proves a real headless-browser verification path, but it is
  wired only into `/review` today and produces ephemeral, gitignored
  evidence (`diana/playwright/evidence/`) rather than a stable per-repo
  artifact this repository-directory scanner could read at scan time. No
  repo in this project currently produces a committed browser-evidence
  artifact for Preflight to consume, so building that consumption boundary
  now would be speculative coupling ahead of a demonstrated need, not an
  evidence-based expansion. The conceptual separation this would need if a
  real need appears — `external verifier → evidence artifact → Preflight
  consumes evidence → Diana Gate consumes reduced Preflight result` — is
  recorded here for whoever picks this up next; `preflight.py` itself must
  never become a browser runner.
- **`HUMAN`/judgement checks** (legal adequacy, business/paywall
  correctness, Merchant-of-Record posture, semantic security correctness,
  analytics-funnel meaningfulness). None of these can be soundly reduced to
  the existing `PASS`/`FAIL`/`SKIP` vocabulary without either fabricating
  automated judgement of something inherently human, or inventing a new
  result state — the latter would be an architecture change to
  `diana-gate.py`'s input schema, out of scope for this phase. Left
  entirely unautomated and undeclared in the catalog; a future phase that
  wants to represent these must design that schema change deliberately, not
  as a side effect of catalog growth.

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
finding is what drives Diana Gate's `FAIL` independent of the declared diff
risk, and (added in Phase 8) a case proving an applicable `WARNING`/`FAIL`
finding does *not* drive Diana Gate's `FAIL`.

## Tests

- `test-preflight.sh` — the fixture matrix (CASE A–F from the Phase 2 spec,
  plus Phase 8's split-check, lockfile, 404-page, and asset-size regression
  cases).
- `test-gate-integration.sh` — proves preflight output flows into Diana
  Gate through `reduce_for_gate.py` without merging the two implementations,
  for both `BLOCKER` and (Phase 8) `WARNING` severity.
