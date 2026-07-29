"""Focused unit tests for nightshift.runtime.isolation.

These test the path-level overlap/containment logic in isolation, using
only temporary directories -- never real production paths. See this
milestone's adversarial review for what these tests do and do not prove
about actual OS-level (Linux-user) isolation.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from nightshift.runtime import isolation  # noqa: E402


class IsolationTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nightshift-isolation-test-")
        self.nightshift_root = os.path.join(self.tmpdir, "nightshift-workspace")
        self.production_root = os.path.join(self.tmpdir, "researchlens-production")
        os.makedirs(self.nightshift_root)
        os.makedirs(self.production_root)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_disjoint_paths_are_allowed(self):
        decision = isolation.validate_isolation(self.nightshift_root, [self.production_root])

        self.assertEqual(decision.outcome, isolation.IsolationOutcome.ALLOWED)
        self.assertEqual(decision.resolved_nightshift_root, os.path.realpath(self.nightshift_root))

    def test_production_path_overlap_is_rejected_when_nightshift_root_is_inside_it(self):
        nested = os.path.join(self.production_root, "nightshift-workspace")
        os.makedirs(nested)

        decision = isolation.validate_isolation(nested, [self.production_root])

        self.assertEqual(decision.outcome, isolation.IsolationOutcome.REJECTED)
        self.assertIn("overlaps", decision.reason)

    def test_production_path_overlap_is_rejected_when_production_is_inside_nightshift_root(self):
        nested_production = os.path.join(self.nightshift_root, "researchlens-production")
        os.makedirs(nested_production)

        decision = isolation.validate_isolation(self.nightshift_root, [nested_production])

        self.assertEqual(decision.outcome, isolation.IsolationOutcome.REJECTED)
        self.assertIn("overlaps", decision.reason)

    def test_exact_same_path_is_rejected(self):
        decision = isolation.validate_isolation(self.production_root, [self.production_root])

        self.assertEqual(decision.outcome, isolation.IsolationOutcome.REJECTED)

    def test_symlink_from_nightshift_root_into_production_is_rejected(self):
        escape_link = os.path.join(self.nightshift_root, "sneaky-link")
        os.symlink(self.production_root, escape_link)

        decision = isolation.validate_isolation(escape_link, [self.production_root])

        self.assertEqual(decision.outcome, isolation.IsolationOutcome.REJECTED)
        self.assertIn("overlaps", decision.reason)

    def test_symlink_from_production_into_nightshift_root_is_rejected(self):
        link_into_workspace = os.path.join(self.production_root, "sneaky-link")
        os.symlink(self.nightshift_root, link_into_workspace)

        decision = isolation.validate_isolation(self.nightshift_root, [link_into_workspace])

        self.assertEqual(decision.outcome, isolation.IsolationOutcome.REJECTED)

    def test_nonexistent_nightshift_root_fails_closed(self):
        missing = os.path.join(self.tmpdir, "does-not-exist")

        decision = isolation.validate_isolation(missing, [self.production_root])

        self.assertEqual(decision.outcome, isolation.IsolationOutcome.REJECTED)
        self.assertIn("not an existing directory", decision.reason)

    def test_nonexistent_forbidden_path_fails_closed(self):
        missing_production = os.path.join(self.tmpdir, "production-typo")

        decision = isolation.validate_isolation(self.nightshift_root, [missing_production])

        self.assertEqual(decision.outcome, isolation.IsolationOutcome.REJECTED)
        self.assertIn("does not exist", decision.reason)

    def test_empty_forbidden_paths_list_fails_closed(self):
        decision = isolation.validate_isolation(self.nightshift_root, [])

        self.assertEqual(decision.outcome, isolation.IsolationOutcome.REJECTED)
        self.assertIn("fail closed", decision.reason)

    def test_empty_nightshift_root_fails_closed(self):
        decision = isolation.validate_isolation("", [self.production_root])

        self.assertEqual(decision.outcome, isolation.IsolationOutcome.REJECTED)


if __name__ == "__main__":
    unittest.main()
