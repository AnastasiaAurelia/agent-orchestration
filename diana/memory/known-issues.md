# Known Issues

Record confirmed problems or deliberate deferrals that would otherwise be
rediscovered. Include evidence and the condition for reconsideration.

```text
## Short issue name
- Observed: confirmed behavior or evidence
- Status: open | deferred
- Revisit when: concrete trigger
```

## AO requires Electron at pinned version

- Observed: at AO pin `88a86d12027e0e37aa793d2f016165dde18e83`, `ao start` is
  not a pure headless CLI/daemon path — it requires/launches the Electron
  desktop runtime.
- Status: open (accepted operational dependency for this pin, not a defect)
- Revisit when: a newer AO release exposes a genuinely headless `ao start`,
  or Diana needs a deployment target where Electron cannot run at all.

## diana-worker has no usable display; Xvfb required for AO Electron

- Observed: on this machine the restricted `diana-worker` account has no
  usable display of its own. The Phase 4 read-only prototype succeeded only
  by running AO's Electron runtime against a private Xvfb display owned by
  `diana-worker`.
- Status: open
- Revisit when: choosing a host/container image for AO workers in Phase 5+;
  it must provision an equivalent private virtual display, or AO must ship a
  true headless mode.

## AO ran without `--no-sandbox`

- Observed: AO successfully ran at the tested pin without requiring
  `--no-sandbox`. Recorded because this flag is a common Electron
  sandbox-bypass footgun and its absence was explicitly verified, not
  assumed.
- Status: closed / informational
- Revisit when: an AO upgrade changes launch flags — re-verify `--no-sandbox`
  is still unnecessary before trusting it again.

## AO CLI surface has drifted from MEMORY.md's assumed commands

- Observed: `MEMORY.md` (`Agent Orchestrator Decision`) expects
  `ao spawn` / `ao session get` / `ao session ls`. At the tested pin, current
  project registration syntax is `ao project add --path ...`, and
  `ao session get --json` exposes a reduced public CLI shape rather than the
  richer set originally assumed.
- Status: open
- Revisit when: writing/updating `diana/adapters/ao.*` — build the adapter
  against the verified CLI surface, not the MEMORY.md sketch, and update
  MEMORY.md's adapter section once the adapter is implemented.

## Diana must not couple to AO private daemon APIs for session metadata

- Observed: `ao session get --json` gives less metadata than AO's private
  daemon/storage internals could provide. Confirmed decision: Diana must not
  reach into AO's private daemon APIs or private storage to get richer
  session metadata, even though it's technically possible.
- Status: closed / deferred by design
- Revisit when: never, unless AO itself publishes those fields as stable
  public CLI/JSON output.

## Phase 4 scope boundaries (does not certify writes or execution safety)

- Observed: Phase 4 (AO Read-Only Prototype) succeeded end-to-end — isolated
  AO worktree, `AGENTS.md` present, canonical Diana memory present, portable
  Claude hook present, no source modifications, session state readable via
  the reduced CLI shape above. AO's Electron runtime is an operational
  dependency of this pin but is not, and must not become, Diana's browser
  verification backend — Playwright MCP remains that (per `MEMORY.md` §11).
  Phase 4 does not certify writes; Codex autonomous writes remain unsupported
  per `MEMORY.md` §5.
- Status: closed
- Revisit when: Phase 5 (Claude Worker Execution-Safety Certification) must
  independently test what an AO Claude worker can physically do while
  running, before any AO safe-write pilot is attempted.
