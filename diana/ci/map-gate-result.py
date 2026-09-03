#!/usr/bin/env python3
"""Map diana-gate.py's exit code to a required-check conclusion.

diana-gate.py's own PASS/FAIL/REQUIRE_HUMAN semantics are never changed here.
Only the GitHub check status is translated, to avoid a REQUIRE_HUMAN PR
deadlocking against its own required status check:

    PASS (0)          -> check succeeds
    REQUIRE_HUMAN (2) -> check succeeds; merge stays blocked by the
                         independent required human/code-owner review rule
    FAIL (1)          -> check fails
    anything else     -> check fails (fail closed: unexpected gate exit code,
                         non-integer input, or other undefined runner state)
"""

from __future__ import annotations

import sys

SUCCESS_EXIT_CODES = {0, 2}


def main() -> int:
    if len(sys.argv) != 2:
        print("::error::usage: map-gate-result.py GATE_EXIT_CODE", file=sys.stderr)
        return 1

    try:
        gate_exit = int(sys.argv[1])
    except ValueError:
        print(
            f"::error::Diana Gate exit code is not an integer: {sys.argv[1]!r} — failing closed",
            file=sys.stderr,
        )
        return 1

    if gate_exit == 0:
        print("Diana Gate decision: PASS")
        return 0
    if gate_exit == 2:
        print(
            "::notice::Diana Gate decision: REQUIRE_HUMAN — this check succeeds; "
            "merge stays blocked by the independent required human/code-owner review rule."
        )
        return 0
    if gate_exit == 1:
        print("::error::Diana Gate decision: FAIL", file=sys.stderr)
        return 1

    print(
        f"::error::Diana Gate returned unexpected exit code {gate_exit} — failing closed",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
