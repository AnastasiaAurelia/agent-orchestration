# Diana ship (Phase 9 single-worker MVP)

`ship.py` is thin, deterministic glue composing existing Diana components
into one pipeline — it reimplements none of them:

```text
diana/adapters/ao.py                    AO compatibility / worker lifecycle
diana/preflight/preflight.py            quality/safety scan
diana/preflight/reduce_for_gate.py      preflight -> gate input shape
diana/gate/diana-gate.py                merge-boundary decision
diana/playwright/browser_applicable.py  browser-facing signal
gh                                       PR creation, PR/CI state
```

The actual end-user entry point is the `/diana-ship` command
(`diana/commands/diana-ship.md`) — read that first for the full attended
workflow. This file documents `ship.py` itself: the deterministic pieces a
live Claude Code session calls at each pipeline stage.

## What `ship.py` deliberately does not do

- **It does not spawn or drive an AO worker session, and it does not
  resolve AO's attended tool-call approvals.** Spawning is exactly
  `diana/adapters/ao.py spawn`. Approving a worker's real mutations
  requires a live human in the loop (MEMORY.md §5,
  `diana/adapters/README.md`) — a deterministic, testable script
  structurally cannot provide that and must not fake it. The calling
  session drives the spawn + attended-approval loop directly (the same
  HTTP-approval mechanism Phase 6 used), then hands `ship.py` the
  resulting branch/commit to verify.
- **It does not classify risk or author the Definition of Done.** Both
  are supplied as input — exactly like `diana-gate.py` never invents a
  diff's risk tier itself. The calling session (human + Claude judgment)
  produces these; `ship.py` only validates and composes them.
- **It never merges anything.** There is no merge code path in this file
  (`test-ship.sh`'s final case greps the source for a literal `"merge"`
  argv token and fails the suite if one is ever added).

## Subcommands

Each prints one JSON object to stdout, exits `0` (`"ok": true`) or `1`
(`"ok": false`) — the same deterministic discipline as every other Diana
component.

### `precheck --repo --risk [--human-only-conditions] [--ao-bin] [--ao-home]`

Run once, before spawning anything:

1. Repository must be clean (`git status --porcelain` empty) — a dirty
   starting repo fails closed as `repo_dirty`.
2. Risk gate: this pipeline's certified worker profile is SAFE-only
   (`diana/governance/risk-tiers.md`, MEMORY.md §5). If `--risk` is
   anything other than `SAFE`, or any `--human-only-conditions` are given,
   it refuses with `requires_human_review` / `decision: REQUIRE_HUMAN` —
   **before** a worker is ever spawned. It never weakens a classification
   to let a task through; the fix is to pick a different, genuinely
   low-risk feature, not to relabel this one.
3. AO compatibility, via `diana/adapters/ao.py check` (never a raw `ao`
   call) — `ao_unavailable` on any failure.

### `verify-diff --repo --base --worker-ref --allowed-files`

Run once the worker has stopped, **before** trusting anything it claims:
diffs `base..worker-ref` and fails closed if the diff is empty
(`empty_diff` — the worker produced nothing), touches a file outside
`--allowed-files` (`out_of_scope_diff`), or touches a hardcoded
forbidden-path prefix — `diana/gate/`, `diana/adapters/`,
`diana/governance/`, `.github/workflows/`, `.github/CODEOWNERS`,
`infra/production/`, `migrations/production/` — regardless of what
`--allowed-files` claims (`forbidden_scope_touched`; defense in depth
against a misconfigured scope declaration, not just the worker's own
behavior).

### `gate --repo --files --dod --verification --risk [--human-only-conditions] [--browser-evidence]`

1. Runs `diana/playwright/browser_applicable.py` against `--files`. If it
   says applicable and `--browser-evidence` is empty,
   `browser_verification_missing` — fails before ever touching preflight,
   since a caller cannot skip verification just by omitting the flag.
   `ship.py` cannot itself drive Playwright MCP's browser tools (those are
   the live session's own MCP tool calls); it only enforces that evidence
   was supplied when the signal says it's needed.
2. Runs real `preflight.py` against `--repo`, reduces via
   `reduce_for_gate.py`.
3. Builds the full `diana-gate.py` input (`dod`, `verification`, reduced
   `preflight`, `diff: {risk, files}`, `human_only_conditions`) and runs
   it. Anything other than `PASS` — including `REQUIRE_HUMAN` — is
   `gate_not_pass`; `REQUIRE_HUMAN` is surfaced as such via
   `gate_decision`, never silently reinterpreted as a pass.
4. On success, returns the same `evidence` object `open-pr` needs
   (`dod`/`verification`/`preflight`/`risk`/`human_only_conditions` —
   matching `diana/ci/build-gate-input.py`'s `DIANA:EVIDENCE` field set
   exactly, so the PR this produces passes the same CI check every other
   Diana PR does).

### `open-pr --gate-evidence --head-branch --title --body-preamble [--base-branch] [--gh-bin]`

Only meaningful after `gate` returned `ok: true`. Builds a PR body: the
caller's preamble, an explicit **"NO AUTO MERGE PERFORMED"** line, then the
`<!-- DIANA:EVIDENCE ... DIANA:EVIDENCE -->` block, and calls
`gh pr create`. Returns the PR URL; `merged` is always `false` in the
result — there is no code path that could make it otherwise.

## Tests

`test-ship.sh` covers all 10 required Phase 9 cases (plus one bonus: a
forbidden path rejected even when declared "allowed") using fakes for AO
(reusing `diana/adapters/fixtures/fake-ao-*` from Phase 7 directly — no
duplicated fixtures) and `gh` (`fixtures/fake-gh-create-*`), and the real
`preflight.py`/`reduce_for_gate.py`/`diana-gate.py`/`browser_applicable.py`
against small throwaway git repos (those four are already deterministic,
fast, and independently tested — faking them too would just duplicate
their own test suites without adding confidence). No live AO daemon or
real `gh` call is ever required to run this suite.

## Known limitations

- `verify-diff`'s forbidden-path list is a fixed, hardcoded set mirroring
  `diana-gate.py`'s own sensitive-path escalation list plus this
  pipeline's dependencies (adapter/gate/governance). It is not
  configurable per-call by design — widening it should be a deliberate
  code change to this file, not a per-invocation flag a caller could
  accidentally loosen.
- `gate`'s browser-applicability check only asks "is verification
  evidence non-empty" — it does not inspect the *content* of
  `--browser-evidence` for quality. That judgment remains the calling
  session's, the same way `/review`'s own browser-facing judgment layers
  on top of `browser_applicable.py`'s deterministic signal
  (`diana/playwright/README.md`).
- There is no `diana ship` shell/CLI entry point distinct from
  `/diana-ship` — Diana today is "mostly an operating protocol for Claude
  Code, not a runtime" (MEMORY.md §0), so the user-facing surface is the
  slash command, which itself invokes `ship.py`'s subcommands from a live
  session, the same way `/review` already invokes
  `browser_applicable.py`.
- `/diana-ship` (`diana/commands/diana-ship.md`) is deliberately **not**
  wired into `install.sh`/`verify.sh`'s per-project copy list yet, matching
  the existing precedent for `diana/preflight`, `diana/gate`, and
  `diana/adapters`: none of those are copied into consumer projects either
  — they currently all run centrally against this repo's own diffs
  (`diana/playwright/README.md` documents the same choice for
  `browser_applicable.py`). `/diana-ship` has a hard dependency on
  `ship.py` plus every component it composes, none of which travel with a
  plain per-project command-file copy today; wiring it up is future
  productionization work, not a Phase 9 change.
