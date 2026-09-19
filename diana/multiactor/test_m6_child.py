#!/usr/bin/env python3
"""M6 acceptance helper: run a multi-actor run in a KILLABLE child process.

A crash case has to be a real uncatchable kill of a real process. Simulating one
with an exception would exercise the handler M5 exists because SIGKILL skips
(Phase 0 F2), so the harness spawns this module and kills it for real.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("multiactor", "unattended", "runtime", "mutation", "adapters", "profile"):
    sys.path.insert(0, str(_HERE.parent / _sub))

import actors as A  # noqa: E402
import executors as E  # noqa: E402

FIXED = "def add(a, b):\n    return a + b    # repaired\n"


def _mark() -> None:
    """Signal liveness OUTSIDE the target repository.

    A marker written inside the target is an out-of-envelope mutation, and
    Diana's reconciliation correctly BLOCKS the run for it -- which would make
    every crash case fail for the harness's reason instead of the one under
    test. The controls being right is why this file cannot take the shortcut.
    """
    path = os.environ.get("M6_TURN_MARKER")
    if path:
        Path(path).write_text("1")


def verify(cb, item_id=None):
    import subprocess
    return subprocess.run([sys.executable, "-B", "check.py"],
                          cwd=cb["target"]["repo_root"], capture_output=True).returncode == 0


class SlowBuilder(E.ScriptedBuilder):
    """Writes the fix, then idles inside the turn so the parent can kill it."""

    name = "slow-builder"

    def _run(self, cb, item_id=None):
        super()._run(cb, item_id)
        _mark()
        time.sleep(600)


class EscapingSlowBuilder(SlowBuilder):
    """Also writes OUTSIDE write_scope, into a gitignored path, then idles."""

    name = "escaping-slow-builder"

    def _run(self, cb, item_id=None):
        root = Path(cb["target"]["repo_root"])
        (root / "src" / "calc.py").write_text(FIXED)
        (root / "build").mkdir(exist_ok=True)
        (root / "build" / "artifact.bin").write_bytes(b"escaped during the turn")
        _mark()
        time.sleep(600)


BUILDERS = {
    "scripted-A": lambda: E.ScriptedBuilder({"item-1": ("src/calc.py", FIXED)}),
    "template-B": lambda: E.TemplateBuilder({"item-1": ("src/calc.py", FIXED)}),
    "slow": lambda: SlowBuilder({"item-1": ("src/calc.py", FIXED)}),
    "escaping-slow": lambda: EscapingSlowBuilder({"item-1": ("src/calc.py", FIXED)}),
    "noop": lambda: E.ScriptedBuilder({}, no_op_on=("item-1",)),
}


def passing_verdict():
    return {"decision": "PASS", "summary": "ok", "findings": [],
            "dod_checks": [{"criterion": "add is correct", "result": "PASS",
                            "evidence": "check.py exits 0"}]}


def main() -> int:
    run_directory, backend = sys.argv[1], sys.argv[2]
    builder = BUILDERS[backend]()
    reviewer = E.ScriptedReviewer([passing_verdict() for _ in range(6)])
    try:
        A.execute(run_directory, builder=builder, reviewer=reviewer, verify=verify)
    except BaseException as exc:  # noqa: BLE001 - reported to the parent, never masked
        print(json.dumps({"blocked": type(exc).__name__, "detail": str(exc)[:300],
                          "code": getattr(exc, "code", None)}))
        return 3
    print(json.dumps({"ok": True, "builder_calls": builder.calls,
                      "reviewer_calls": reviewer.calls}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
