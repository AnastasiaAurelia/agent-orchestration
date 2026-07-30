"""Tests for nightshift.runtime.smoke_acceptance_checker.

Every test invokes the checker as a real subprocess (`python3
smoke_acceptance_checker.py <project_dir>`), exactly like a task's
acceptance_command always does in production -- this also sidesteps
unittest's own module-registration-by-name behavior, which would otherwise
make repeated in-process calls to check() collide with each other across
tests (see the module's own docstring).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

CHECKER_PATH = os.path.join(REPO_ROOT, "nightshift", "runtime", "smoke_acceptance_checker.py")

_VALID_CALCULATOR = "def add(a, b):\n    return a + b\n"

_THREE_PASSING_TESTS = """
import unittest
from calculator import add


class AddTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(2, 3), 5)

    def test_negative(self):
        self.assertEqual(add(-2, -3), -5)

    def test_zero(self):
        self.assertEqual(add(0, 0), 0)
"""

_THREE_TESTS_ONE_FAILING = """
import unittest
from calculator import add


class AddTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(2, 3), 5)

    def test_negative(self):
        self.assertEqual(add(-2, -3), -5)

    def test_zero_is_wrong_on_purpose(self):
        self.assertEqual(add(0, 0), 1)
"""

_TWO_PASSING_TESTS = """
import unittest
from calculator import add


class AddTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(2, 3), 5)

    def test_negative(self):
        self.assertEqual(add(-2, -3), -5)
"""


def _run_checker(project_dir):
    return subprocess.run(
        [sys.executable, CHECKER_PATH, project_dir],
        capture_output=True,
        text=True,
        timeout=30,
    )


class SmokeAcceptanceCheckerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nightshift-smoke-checker-test-")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, name, content):
        with open(os.path.join(self.tmpdir, name), "w", encoding="utf-8") as f:
            f.write(content)

    def test_missing_calculator_py_is_rejected(self):
        self._write("test_calculator.py", _THREE_PASSING_TESTS)

        result = _run_checker(self.tmpdir)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("calculator.py", result.stderr)

    def test_missing_test_calculator_py_is_rejected(self):
        self._write("calculator.py", _VALID_CALCULATOR)

        result = _run_checker(self.tmpdir)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("test_calculator.py", result.stderr)

    def test_zero_discovered_tests_is_rejected(self):
        self._write("calculator.py", _VALID_CALCULATOR)
        self._write("test_calculator.py", "# no tests here\n")

        result = _run_checker(self.tmpdir)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only 0 test(s) discovered", result.stderr)

    def test_one_or_two_discovered_tests_is_rejected(self):
        self._write("calculator.py", _VALID_CALCULATOR)
        self._write("test_calculator.py", _TWO_PASSING_TESTS)

        result = _run_checker(self.tmpdir)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only 2 test(s) discovered", result.stderr)

    def test_three_passing_tests_is_accepted(self):
        self._write("calculator.py", _VALID_CALCULATOR)
        self._write("test_calculator.py", _THREE_PASSING_TESTS)

        result = _run_checker(self.tmpdir)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ACCEPTED", result.stdout)

    def test_three_tests_with_a_failure_is_rejected(self):
        self._write("calculator.py", _VALID_CALCULATOR)
        self._write("test_calculator.py", _THREE_TESTS_ONE_FAILING)

        result = _run_checker(self.tmpdir)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failure(s)", result.stderr)

    def test_does_not_rely_only_on_parsing_ran_n_tests_text(self):
        # A test_calculator.py that prints a fake "Ran 3 tests / OK" banner
        # to stdout via a print() statement (not real unittest output) must
        # not fool the checker -- it must still see zero real discovered
        # tests and reject, since it counts structurally via
        # TestSuite.countTestCases(), never by scanning output text.
        self._write("calculator.py", _VALID_CALCULATOR)
        self._write(
            "test_calculator.py",
            "print('Ran 3 tests in 0.001s')\nprint()\nprint('OK')\n",
        )

        result = _run_checker(self.tmpdir)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only 0 test(s) discovered", result.stderr)

    def test_nonexistent_project_dir_is_rejected(self):
        result = _run_checker(os.path.join(self.tmpdir, "does-not-exist"))

        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
