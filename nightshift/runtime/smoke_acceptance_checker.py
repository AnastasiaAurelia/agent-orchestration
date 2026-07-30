"""Trusted, standalone acceptance checker for the Nightshift Claude smoke test.

Deployed outside any Claude-editable working directory (see
docs/nightshift/CLAUDE_EXECUTOR_SMOKE_TEST.md, which copies this exact,
already-tested file to /home/nightshift/state/smoke-002-acceptance.py) so a
Claude session cannot read, edit, or otherwise tamper with its own grader.

This exists because a plain ``python3 -m unittest discover -v`` was the
acceptance command in the first real supervised smoke test (Milestone 7C),
and an empty or near-empty test suite exits 0 with "Ran 0 tests / OK" --
indistinguishable from a real pass by exit code alone. This script never
trusts a bare exit code: it requires the two specific project files to
exist, loads exactly test_calculator.py's suite via the standard unittest
loader, and requires at least MIN_REQUIRED_TESTS discovered test cases
(counted structurally via TestSuite.countTestCases(), never by parsing
output text such as "Ran 3 tests") before treating any result as a pass.

Standard library only. Intended to be invoked once per process, as argv[0]
of a task's acceptance_command (a fresh `python3 <this file> <project_dir>`
subprocess each time, exactly like every other acceptance command in this
codebase) -- not designed for repeated in-process reuse within one
interpreter (unittest's discover() registers modules in sys.modules by
name, which would collide across repeated in-process calls; a fresh
subprocess per invocation, which is how it is always actually used, has no
such issue).
"""

from __future__ import annotations

import os
import sys
import unittest

MIN_REQUIRED_TESTS = 3
REQUIRED_FILES = ("calculator.py", "test_calculator.py")


def check(project_dir: str) -> int:
    """Return 0 only if both required files exist, at least MIN_REQUIRED_TESTS
    tests were discovered in test_calculator.py, and all of them passed.

    Any other condition -- a missing file, too few discovered tests, or any
    failure/error -- returns a nonzero exit code. Prints a short, specific
    reason to stderr in every rejection case so a human (or the durable
    evidence excerpt that captures this output) can see why without
    re-running anything.
    """
    for name in REQUIRED_FILES:
        if not os.path.isfile(os.path.join(project_dir, name)):
            print(f"REJECTED: required file missing: {name}", file=sys.stderr)
            return 1

    sys.path.insert(0, project_dir)
    try:
        suite = unittest.TestLoader().discover(
            start_dir=project_dir, pattern="test_calculator.py"
        )
        test_count = suite.countTestCases()
        if test_count < MIN_REQUIRED_TESTS:
            print(
                f"REJECTED: only {test_count} test(s) discovered in "
                f"test_calculator.py, require at least {MIN_REQUIRED_TESTS}",
                file=sys.stderr,
            )
            return 1

        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            print(
                f"REJECTED: {len(result.failures)} failure(s), "
                f"{len(result.errors)} error(s) out of {test_count} test(s)",
                file=sys.stderr,
            )
            return 1

        print(f"ACCEPTED: {test_count} test(s) discovered and passed")
        return 0
    finally:
        if project_dir in sys.path:
            sys.path.remove(project_dir)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) != 1:
        print("usage: smoke_acceptance_checker.py <project_dir>", file=sys.stderr)
        return 2
    project_dir = argv[0]
    if not os.path.isdir(project_dir):
        print(f"REJECTED: not a directory: {project_dir}", file=sys.stderr)
        return 1
    return check(project_dir)


if __name__ == "__main__":
    sys.exit(main())
