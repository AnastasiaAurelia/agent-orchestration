# Diana Playwright MCP integration (Phase 3 prototype)

Gives `/review` real, headless browser verification for browser-facing
changes, using [Playwright MCP](https://github.com/microsoft/playwright-mcp)
directly. No capability registry, no custom browser engine, no AO Electron
browser — see MEMORY.md §11 and §17.

## Ownership boundary

```text
Diana owns          Playwright MCP owns
-----------          -------------------
applicability        browser control
/review workflow      navigation
DoD / evidence         click / fill interaction
interpretation          browser observation, screenshots
```

Diana never re-implements browser automation. Playwright MCP never decides
what counts as a passing review — that stays `/review`'s judgment, applied
to the evidence Playwright MCP produces. This mirrors the existing
"Preflight detects, Gate decides" split (`diana/preflight/README.md`):
here, `browser_applicable.py` detects; `/review` decides.

## Current dependency (inspected 2026-09-04)

- Package: `@playwright/mcp`, version **0.0.80** (`npx @playwright/mcp@latest`
  resolves here at inspection time — pin drifts forward by design, since
  the upstream project has no stable/LTS release channel yet).
- Depends on `playwright`/`playwright-core` **1.63.0-alpha-2026-08-31**.
- License: Apache-2.0. Repo: microsoft/playwright-mcp (36.8k stars at
  inspection time).
- Config format: standard `{"mcpServers": {"playwright": {"command": "npx",
  "args": [...]}}}`, identical across nearly every MCP client including
  Claude Code (`claude mcp add playwright npx @playwright/mcp@latest`).
- Transport: stdio, newline-delimited JSON-RPC 2.0 (MCP spec
  2026-07-28 stdio transport). The server itself still speaks the
  `initialize`/`notifications/initialized` handshake (protocol version
  `2025-06-18`) rather than the newer per-request-metadata era — verified
  empirically against the actual running server, not assumed from the spec
  page alone.
- Headless: `--headless` flag (headed by default otherwise).
- Isolation: `--isolated` keeps the browser profile in memory instead of a
  persistent per-workspace profile dir. The upstream README explicitly
  warns a persistent profile "can only be used by one browser instance at
  a time" and recommends `--isolated` for concurrent clients sharing a
  workspace — directly relevant to AO worktrees running Diana in parallel,
  so Diana's installed config passes it by default.
- Browser binaries: **downloaded automatically**, not bundled in the npm
  package. The first `npx @playwright/mcp@latest` run installs the
  `playwright` dependency, whose postinstall step downloads the needed
  browser binary to `~/.cache/ms-playwright/` (Linux). This requires
  network access on first use in any given environment/cache; subsequent
  runs reuse the cache. There is no separate `npx playwright install`
  step required.
- No project-local dependency is necessary. `npx @playwright/mcp@latest`
  fetches and runs the package on demand; Diana does not add a
  `package.json` or a Playwright test-framework dependency anywhere in
  this repo. (Microsoft also publishes a separate
  [`playwright-cli`](https://github.com/microsoft/playwright-cli)
  "CLI + SKILLS" tool that its own README says many coding agents now
  prefer over MCP for token efficiency — noted here as dependency
  context/drift, not adopted: the architecture in MEMORY.md §11 and this
  phase's directive both specify Playwright **MCP**, and CLI+SKILLS was
  not evaluated further.)

## Prerequisite: Node.js >= 20

This is **Playwright's own hard requirement**, not something Diana adds or
can relax. `playwright@1.63.0-alpha-2026-08-31` refuses to start on Node
< 20 with an explicit error, not just an `engines` warning:

```text
You are running Node.js 18.19.1.
Playwright requires Node.js 20 or higher.
```

This was found empirically on this exact execution identity: `diana-worker`
runs system Node 18.19.1 (`/usr/bin/node`, apt-installed), which cannot run
`@playwright/mcp` at all. `diana/playwright/test-playwright-prototype.sh`
detects this and exits with a clear `SKIP` message rather than hanging or
producing a confusing failure. Anyone installing this integration needs a
Node >= 20 available on `PATH` — Diana does not bundle or silently install
one. This is a genuine known limitation of the current environment, not a
prototype defect; see "Known limitations" below.

## Committed MCP configuration

`diana/mcp/playwright.json` is the canonical, Diana-managed MCP server
fragment (mirrors `diana/hooks/hooks.json`'s role for the Claude safety
hook). This repository's own root `.mcp.json` uses the identical content,
so opening this repo in an MCP-aware client already offers the `playwright`
server — proving the config survives a normal `git worktree add` (it's a
tracked file, unlike `.claude/settings.local.json`), the same portability
fix Phase 1E applied to the safety hook.

`install.sh`/`uninstall.sh`/`verify.sh` merge/strip/verify this same
fragment into a target project's own root `.mcp.json`, using the same
idempotent, backup-before-overwrite, content-signature approach the hook
merge already uses in `install.sh`:

- The Diana-managed entry is identified by its `@playwright/mcp` command
  signature, not just the key name `playwright`. If a project already has
  its own differently-configured `playwright` MCP server (same key, no
  Diana signature), install/uninstall leave it untouched and report it as
  skipped rather than silently overwriting a user's own server — no
  credentials are ever written, and unrelated `mcpServers` entries are
  always preserved.
- Re-running install is idempotent: an unchanged Diana entry is reported
  `unchanged`, not rewritten.
- Uninstall removes only the Diana-signed entry; if that leaves
  `.mcp.json` empty, the file itself is removed (mirroring the existing
  AGENTS.md/CLAUDE.md "only contained the Diana section" behavior).

No credentials of any kind are involved — Playwright MCP needs none for
local browser automation.

## Applicability: is this diff browser-facing?

`browser_applicable.py` is a small, deterministic, dependency-free check —
independent of `diana/preflight`'s own frontend detector (no shared code;
same "no premature capability registry" boundary that already keeps
Preflight and Gate from importing each other). Given a diff's changed file
list, it flags a diff as browser-facing only when it touches an
unambiguous browser-rendered extension (`.html`, `.css`, `.scss`, `.sass`,
`.less`, `.jsx`, `.tsx`, `.vue`, `.svelte`). A bare `.js`/`.ts` change is
**not** enough signal on its own — it could just as easily be a CLI, a
backend, or a build script. This is a documented, intentional gap: the
deterministic check is a first-pass signal for `/review`'s own judgment,
not a replacement for it, exactly like Preflight's own applicability
functions are filename/pattern heuristics rather than AST-aware analysis.

```
python3 browser_applicable.py fixtures/diff-frontend-facing.json
# {"applicable": true, "reasons": [...], "version": 1}
```

## What `/review` does with this (see `diana/commands/review.md`)

```text
inspect change
    |
browser-facing? (browser_applicable.py signal + /review's own judgment)
    |-- no  --> Playwright SKIP, noted in the review output
    |-- yes
         |
    browser verification (Playwright MCP tool calls: browser_navigate,
    browser_click, browser_type, browser_snapshot, browser_take_screenshot,
    ...)
         |
    evidence captured (accessibility snapshot text + screenshot, see
    "Evidence format" below)
         |
    review result (folded into /review's existing ranked findings output —
    not a second source of truth; a browser-observed failure is reported
    the same way a correctness bug is)
```

`/review`'s browser-facing determination stays LLM judgment layered on the
deterministic signal above — the same way `/review` already judges
"overengineering" or "unrelated changes" by reading the diff, not by a
script. This keeps `review.md` a single portable file installable into a
target project without any extra script dependency, consistent with how
`fix.md`/`ship.md` work today. `browser_applicable.py` is this repo's own
reference implementation and regression-tested proof that the applicability
rule is sound and deterministic; it is not (yet) part of `install.sh`'s
per-project file copy list, matching how `diana/preflight/*.py` and
`diana/gate/*.py` are not copied into consumer projects today either — both
currently run centrally against this repo's own diffs. Wiring
applicability scripts into every consumer project's install is
out of scope for this prototype phase.

When Playwright MCP tools are actually available in a live `/review`
session (i.e., the coding agent's own MCP client, not this repo's test
harness), the agent calls them directly — there is no runtime dependency
on any script in this directory. `run-prototype.mjs` exists purely to
prove and regression-test the same tool sequence deterministically,
without a live agent session driving it turn by turn.

## Evidence format

A browser verification pass or failure should report, at minimum:

- the URL(s) navigated
- the interactions performed (fill/click/etc.) in order
- an accessibility snapshot (`browser_snapshot`) excerpt showing the
  observed state — this is what actually proves or disproves the expected
  behavior, not the screenshot
- a screenshot (`browser_take_screenshot`) as visual corroboration

`run-prototype.mjs` writes exactly this shape to stdout as JSON and saves
the screenshot + Playwright's own console/page-snapshot artifacts under
the evidence directory passed on its command line (gitignored:
`diana/playwright/evidence/` is generated output, not checked in).

**Known quirk**: passing a relative `filename` to `browser_take_screenshot`
was observed resolving against the MCP server process's own CWD, not
`--output-dir`, even though the upstream README's `--output-dir` doc text
suggests relative filenames "stay within the output directory." Verified
empirically — `run-prototype.mjs` works around this by always passing an
absolute screenshot path.

## Known limitations

- Requires Node >= 20 on `PATH`; this exact execution identity's system
  Node (18.19.1) does not satisfy that, so this repo's own default shell
  cannot run the prototype without a locally-obtained newer Node (not
  installed system-wide by this phase's work — see "Prerequisite" above).
- `browser_applicable.py`'s extension-based heuristic will not flag a
  frontend change that only touches `.js`/`.ts` files with no companion
  browser-rendered extension in the same diff — documented gap, not a
  silent false-negative pretending to be safe (mirrors
  `diana/preflight/README.md`'s own documented gaps).
- First run in any fresh environment needs network access to fetch
  `@playwright/mcp` and download its browser binary; there is no offline
  fallback in this phase.
- `install.sh`'s `.mcp.json` merge only recognizes a pre-existing entry as
  "Diana's" by content signature (`@playwright/mcp` in its command/args).
  A hand-written `playwright` entry that happens to reference
  `@playwright/mcp` differently (e.g. pinned to a specific version) would
  still be treated as foreign since the full fragment must match for
  "unchanged" — it would be backed up and reported as updated on the next
  `install.sh` run. This is the same backup-before-overwrite safety net
  `install.sh` already uses elsewhere, not a new risk.

## Tests

- `test-browser-applicable.sh` — CASE A (SKIP) + a browser-facing case +
  malformed-input fail-closed case for `browser_applicable.py`.
- `test-playwright-prototype.sh` — CASE B (healthy flow), CASE C (broken
  flow detected with evidence), CASE D (headless, by construction), CASE E
  (isolation: localhost-only, no AO Electron dependency, by construction).
  Requires Node >= 20 on `PATH` and network access; exits with a clear
  `SKIP` message otherwise rather than a confusing hang or crash.
