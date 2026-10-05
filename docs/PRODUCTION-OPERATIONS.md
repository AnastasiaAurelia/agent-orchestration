# Diana Governed Runtime — Production Operations

Status: **hardening track; not yet a production-ready release claim.**

The portable project integration (`./install.sh`) and the governed runtime are separate surfaces.

- `./install.sh`: installs project-local Claude/Diana integration.
- `./runtime-install.sh`: manages the versioned governed `diana-do` runtime.

## Supported environment

For this hardening track:
- Linux only.
- Python 3.11 for release certification.
- Hermes must resolve through its PM-managed runtime.
- Hermes identity must exactly match one of Diana's certified identities.

macOS and Windows remain unsupported/unproven.

## Inspect the source runtime

```bash
./diana-do version
./diana-do doctor
```

`doctor` is deterministic and makes no model call. It is intentionally able to report a missing/broken Hermes bootstrap before governed execution starts.

A failed doctor is a refusal to certify/start the governed runtime environment; it is not silently downgraded to a warning.

## Install the governed runtime

The installer refuses dirty runtime-bearing source. Commit/review the exact source first.

Default locations:
- runtime prefix: `~/.local/share/diana`
- launcher: `~/.local/bin/diana-do`

```bash
bash ./runtime-install.sh install
bash ./runtime-install.sh verify
~/.local/bin/diana-do version
~/.local/bin/diana-do doctor
```

Custom locations for test/managed environments:

```bash
bash ./runtime-install.sh install \
  --prefix /safe/managed/prefix \
  --bin-dir /safe/managed/bin
```

The runtime is installed under a versioned release directory. `current` is an atomic symlink to the active verified release. The launcher is a Diana-owned symlink to `current/diana-do`.

The installer refuses to overwrite a pre-existing launcher that it cannot prove is Diana-managed.

## Upgrade

```bash
bash ./runtime-install.sh upgrade
bash ./runtime-install.sh verify
```

Upgrade stages and verifies the new runtime before activating it. The previously active verified release becomes the rollback target.

Diana run/proposal state under `~/.diana` is outside the runtime installation prefix and is not rewritten by an upgrade.

New proposals are bound to Diana's release identity. A proposal produced under a different Diana release is refused and must be proposed/approved again. This prevents a newer release from reinterpreting an older approval.

## Rollback

```bash
bash ./runtime-install.sh rollback
bash ./runtime-install.sh verify
```

Rollback is limited to the immediately previous verified Diana-managed release. If no verified previous release exists, rollback refuses.

## Uninstall governed runtime

```bash
bash ./runtime-install.sh uninstall
```

Uninstall refuses if the managed prefix contains unexpected/unmanaged entries or if the launcher is not the Diana-owned symlink it expects.

It does not remove target-project files and does not remove `~/.diana` run/proposal history.

## Runtime use

Normal bounded flow remains:

```bash
diana-do "Fix the failing tests in this repo, but don't touch auth"
diana-do approve sha256:<proposal-digest> --repo /path/to/repo
diana-do status <run-id>
diana-do result <run-id>
```

Approval remains exact and bounded. Upgrade, rollback, packaging and diagnostics do not grant new tools, wider scopes, merge authority or deployment authority.

## Production release gate

Run only on the supported Linux certification environment with the exact Hermes identity/provider available:

```bash
bash diana/ci/run-production-release-gate.sh
```

A required suite that reports `SKIP` is a release failure. Environmental skips are not production-certification evidence.

The script does not self-approve GitHub changes. Diana Gate, Diana Security Gate, branch protection and any required independent human approval remain repository controls outside the local release script.

## Recovery

The authoritative run state remains Diana's durable journal under the configured run base (default `~/.diana/runs`).

A runtime upgrade does not migrate or rewrite active run state. If a release cannot safely interpret an existing approval/run under its accepted schema/policy, the correct behavior is refusal/new approval, not silent widening.

## Troubleshooting

Start with:

```bash
diana-do doctor
```

Key outcomes:
- unsupported OS → refuse production certification;
- unsupported Python → refuse production certification;
- Hermes runtime unresolved → refuse governed execution;
- Hermes identity unverifiable/mismatch → refuse governed execution;
- dirty source checkout → refuse source-checkout production certification;
- installed manifest/file mismatch → `runtime-install.sh verify` refuses.

Do not fix these by broadening identity matching, bypassing PM runtime discovery, disabling release checks or editing frozen proof suites.
