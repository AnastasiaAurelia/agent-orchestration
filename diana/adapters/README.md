# Diana AO Adapter

`ao.py` is Diana's only integration point with Agent Orchestrator (AO). No
other file in this repository may construct a raw `ao` command; if a future
change needs new AO lifecycle behavior, it goes through this adapter, not
through a scattered inline `subprocess` call elsewhere.

AO can in principle be replaced by any other worker/session/worktree runtime
by rewriting this one file (and its tests/fixtures) — nothing else in Diana
encodes AO's CLI syntax, private daemon HTTP API, or private SQLite database.

## Contract

Four subcommands, each printing one JSON object to stdout and exiting `0` on
success or `1` on failure — the same deterministic, non-LLM-decided
success/failure discipline as [`diana/gate`](../gate/README.md):

```
python3 ao.py check   [--ao-bin PATH] [--ao-home PATH]
python3 ao.py spawn   --project ID --name NAME --prompt TEXT [--branch B] [--ao-bin PATH]
python3 ao.py status  --session ID [--project ID] [--ao-bin PATH]
python3 ao.py stop    --session ID [--project ID] [--ao-bin PATH]
```

- `check` — is a compatible AO present and its daemon ready? Failure modes:
  `ao_missing`, `version_unknown`, `version_incompatible`,
  `ao_runtime_unavailable`.
- `spawn` — start a **claude-code** worker session in a registered project.
  `--harness` is not exposed as a caller-controlled flag: it is hardcoded to
  `claude-code` inside the adapter. AO + Codex autonomous writes are not
  certified (see root `MEMORY.md` §5); this adapter cannot be used to route
  around that, regardless of what a caller asks for. Failure mode:
  `spawn_failed` (also `spawn_unparseable` if AO's own success output ever
  changes shape).
- `status` — fetch one session's state. Failure mode: `status_failed`.
- `stop` — terminate one session. Failure mode: `stop_failed`.

`ao_bin` resolution order: `--ao-bin` flag, then `DIANA_AO_BIN` env var, then
`PATH`. The adapter never guesses at AO's ephemeral AppImage FUSE mount path
under `/tmp` — that path only exists while AO's Electron shell happens to be
running under a particular process tree, so trusting it would make Diana
depend on a private runtime implementation detail instead of a supported
entry point. A caller that only has that mount path must pass it explicitly.

## AO version pin and why `check` reads a file, not just `ao version`

Pinned AO version: `0.12.10` (upstream source commit
`ed88a86d12027e0e37aa793d2f016165dde18e83`, recorded in
`diana/memory/known-issues.md` — that commit **cannot** be verified from the
installed binary itself; only the release version can).

AO's own public/supported `ao version` command is tried first. At the
currently pinned build it prints the placeholder string `dev` rather than a
real release version, so `check` falls back to AO's own on-disk
install-state record, `<ao-home>/app-state.json` (default `~/.ao`, override
with `--ao-home` / `DIANA_AO_HOME`). That file is AO's own recorded
installation metadata — not its private runtime SQLite database (`ao.db`)
and not a call to its private daemon HTTP API (`/api/v1/...`); it is the
same signal used to manually confirm the AO version during Phases 4 and 6.
If a future AO release makes `ao version` print a real semver, `check` uses
that directly and the fallback is never consulted. If neither source yields
a usable version, `check` fails as `version_unknown` rather than assuming
compatibility.

`check` additionally runs `ao doctor --json` (public, documented command)
and requires its `Core`/`daemon` check to report `PASS`, to distinguish "AO
installed at the right version but its daemon isn't running"
(`ao_runtime_unavailable`) from a version mismatch.

## Known limitation: `ao session kill` does not fail clearly on an unknown id

Verified directly against the real 0.12.10 daemon: `ao session get
<bogus-id>` fails clearly (`Unknown session (SESSION_NOT_FOUND)`, exit 1),
but `ao session kill <bogus-id>` prints `session <bogus-id> killed
(workspace preserved)` and exits `0` regardless of whether the session ever
existed. `stop()` faithfully propagates whatever AO itself reports; it
cannot invent a failure AO's own CLI does not surface. The adapter's tests
for `stop` failure therefore model a genuine failure AO *can* report (daemon
unreachable), not a nonexistent-session id — see
`fixtures/fake-ao-stop-fail`.

## Tests

`test-ao-adapter.sh` uses small fake `ao` shims under `fixtures/` (never the
real daemon) to deterministically cover: a compatible AO reporting its
version directly; a compatible AO reporting `dev` and falling back to
`app-state.json`; AO missing; wrong version; daemon-installed-but-not-ready;
exact `spawn` argv construction (proving `--harness claude-code` is always
present and `codex` never appears, without spawning a real session); spawn
failure; status failure; and stop failure.

A one-time safe real-integration check (documented in the Phase 7 evidence,
not repeated as an automated test here) additionally proved `check`, `spawn`,
`status`, and `stop` all work end-to-end through this adapter against the
live pinned AO 0.12.10 daemon, using a read-only probe prompt that made no
file changes.

## Explicitly out of scope for this adapter

- GitHub PR/CI/review state — that is `gh`'s job (see root `MEMORY.md` §12).
- Browser automation/verification — that is Playwright MCP's job; AO's own
  Electron browser is deliberately unused by Diana.
- Upgrading or downgrading AO automatically.
- Any Codex (or other non-claude-code harness) write path.
