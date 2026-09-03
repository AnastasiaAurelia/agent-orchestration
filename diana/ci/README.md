# Diana Gate CI

The pull-request workflow reads a strict JSON evidence block from the PR body,
derives changed files from Git, runs the local Diana Gate, and writes the
result to the GitHub job summary.

`diana-gate.py`'s own exit code (`0` PASS, `1` FAIL, `2` REQUIRE_HUMAN) is
translated by `map-gate-result.py` into the required-check conclusion:

- `PASS` -> check succeeds.
- `REQUIRE_HUMAN` -> check succeeds, with an explicit `::notice::` annotation
  and job-summary note. Merge still stays blocked by the independent required
  human/code-owner review rule, not by this check — this avoids a REQUIRE_HUMAN
  PR deadlocking against its own required status.
- `FAIL` -> check fails, blocking merge.
- Any other exit code (unexpected crash, non-integer input, or other
  undefined runner state) -> check fails. Fail closed.

`diana-gate.py`'s decision semantics are never altered to fit CI; only the
check-status mapping around it changes.

The synthetic matrix (`test-advisory.py`) covers safe, missing evidence,
blocker, human-only, non-applicable stack, malformed evidence, sensitive-
adjacent safe path, and sensitive-path escalation cases against the gate's own
exit codes. `test-gate-mapping.sh` covers the exit-code-to-check-conclusion
mapping in isolation. Neither proves semantic risk detection for paths or
categories the catalog does not yet recognize.

`actions/checkout` is pinned to the v7.0.1 commit inspected on 2026-09-02.
