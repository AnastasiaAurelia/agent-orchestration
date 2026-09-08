#!/usr/bin/env python3
"""Diana Security CI-controlled trusted verifier runs (Security Phase 5).

THE single, explicit extension point for supplying real trusted
verification evidence to the Security Gate. Prints a JSON array of
`evidence_model.py`-shaped "run" records -- the only input
`security_bundle.build_bundle()` is ever given for the real CI pipeline
(see `diana/ci/run-security-gate.py`).

## Currently always empty, and that is correct

This prints `[]` today. No live static analyzer, dependency scanner,
secret scanner, dynamic scenario runner, or semantic reviewer session is
wired into CI yet -- Security Phase 2/3/4 built NORMALIZERS for
already-produced evidence artifacts, not live tool/session execution, and
no trusted reviewer-orchestration channel (spawn a fresh, independent,
read-only reviewer session; capture its resulting artifact) exists yet
either. An empty result here means every one of the 75 canonical controls
comes back `UNPROVEN` from `security_bundle.build_bundle()`, which
`security_reducer.py` maps to `REQUIRE_HUMAN` -- the honest, correct,
fail-closed state for a Security Track that has not yet wired any live
verifier into CI. This is expected and intended, not an oversight: see
"Producer trust" in the Security Phase 5 PR description.

## Deliberately reads nothing from the pull request

This script takes no arguments and reads no PR body, no PR-supplied file,
no arbitrary repository content -- it cannot be influenced by anything a
PR author controls. That is the entire point: trusted Security evidence
must come from a CI-controlled producer, never from the PR body (the
existing `<!-- DIANA:EVIDENCE -->` block stays scoped to ordinary DoD/
verification/Preflight/risk evidence, exactly as before this phase) and
never from an arbitrary checked-in JSON file, even one with a
structurally valid `artifact_binding` (that hash proves internal artifact
integrity only, never who produced it or that it came through a genuine
CI-controlled verifier path -- see Phase 2/3/4's `adapter_base.py`/
`reviewer_base.py` docstrings and the Phase 5 PR description).

## Extending this for real verifier execution

When a future phase wires in real trusted verifier execution -- actually
running gitleaks/osv-scanner/semgrep in CI and piping their output
through the Phase 2 adapters, a genuine dynamic-scenario runner for
Phase 3, or a real reviewer-orchestration channel for Phase 4 -- it is
added HERE, and only here. `security_bundle.py`, `security_reducer.py`,
and the Gate integration (`.github/workflows/diana-security-gate.yml`)
do not need to change: they already consume whatever
`collect_trusted_runs()` returns.
"""

from __future__ import annotations

import json


def collect_trusted_runs() -> list:
    """Returns the list of evidence_model.py-shaped runs this CI
    invocation is able to trust. Always [] today -- see module docstring."""
    return []


def main() -> int:
    print(json.dumps(collect_trusted_runs(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
