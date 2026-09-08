# Diana Gate CI

The pull-request workflow reads a strict JSON evidence block from the PR body,
derives changed files from Git, runs the local Diana Gate, runs the Security
Gate, combines the two, and writes the result to the GitHub job summary.

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

## Security Phase 5: `run-security-gate.py`

After the existing Diana Gate step (unchanged), the workflow runs
[`run-security-gate.py`](run-security-gate.py) `$GITHUB_WORKSPACE
$DIANA_BASE_SHA $DIANA_HEAD_SHA "${{ github.repository }}"`. It extracts
the TRUSTED security policy/evaluator (`diana/security/{catalog.json,
validate_catalog.py, evidence_model.py, security_bundle.py,
security_reducer.py, ci_verifier_runs.py}`) from the pull request's
protected base SHA (`git archive`, never the PR head's working tree) and
runs the whole trusted pipeline from that isolated extraction, so a PR
modifying any of those files cannot weaken its own current evaluation.
When those files don't exist yet at the base (the Security Phase 5 PR's
own bootstrap case), it emits `{"decision": "SKIPPED_BOOTSTRAP", ...}`
instead of erroring. See `diana/security/README.md`'s "Security Phase 5"
section for the full design.

The result is combined with the existing Diana Gate's result via
`diana-gate.py combine GATE_RESULT.json SECURITY_RESULT.json` (a second,
additive CLI mode on the existing, otherwise-unmodified `diana-gate.py`)
before `write-summary.py` and `map-gate-result.py` run -- both of those
scripts are completely unchanged, they are simply fed the combined result
instead of the Gate-only result.

The synthetic matrix (`test-advisory.py`) covers safe, missing evidence,
blocker, human-only, non-applicable stack, malformed evidence, sensitive-
adjacent safe path, and sensitive-path escalation cases against the gate's own
exit codes. `test-gate-mapping.sh` covers the exit-code-to-check-conclusion
mapping in isolation. Neither proves semantic risk detection for paths or
categories the catalog does not yet recognize.

`actions/checkout` is pinned to the v7.0.1 commit inspected on 2026-09-02.
