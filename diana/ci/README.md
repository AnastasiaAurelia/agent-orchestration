# Diana Gate CI

Two separate, independently required GitHub Actions checks. `diana-gate.yml`
(plain `pull_request`) reads a strict JSON evidence block from the PR body,
derives changed files from Git, runs the local Diana Gate, and writes the
result to the GitHub job summary. `diana-security-gate.yml`
(`pull_request_target` -- see below) independently evaluates the Security
Track's evidence. Neither workflow's code combines the two: GitHub branch
protection requiring both checks reproduces the intended combination
policy (see `diana/gate/README.md`'s "Security Phase 5" section).

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

## Security Phase 5: `diana-security-gate.yml` (separate workflow, `pull_request_target`)

`.github/workflows/diana-security-gate.yml` is a **separate workflow
file** from `diana-gate.yml`, triggered by `pull_request_target` instead
of `pull_request`. This is the actual trust root, not an implementation
detail: for `pull_request_target`, GitHub resolves and runs the
**workflow file itself** from the repository's default branch, never
from the PR head -- a PR that edits this workflow file (e.g. to remove
or neuter the Security Gate step) has zero effect on what actually runs
against it. Its `actions/checkout` step deliberately specifies no `ref:`
override, so it also resolves to the base branch's tip by default (never
the PR head or a merge commit) -- the entire checked-out tree, including
`diana/security/{ci_verifier_runs.py, security_bundle.py,
security_reducer.py}`, is already the protected base. No extraction step
is needed; those scripts are invoked directly from that checkout. The
workflow declares `permissions: contents: read` (no write scopes) and
references no `secrets.*` anywhere.

The job's own exit code (via the same `map-gate-result.py` convention
as the ordinary Gate) determines whether this check succeeds; branch
protection requiring both this check and the ordinary "Diana Gate" check
reproduces the combination policy without any shared runtime code.

An earlier design ran a git-archive-based extraction
([`run-security-gate.py`](run-security-gate.py)) *inside* a step of the
plain-`pull_request`-triggered `diana-gate.yml` -- that protected the
evaluator *scripts* from PR-head tampering but not the *workflow
orchestration itself* (a malicious PR could simply have deleted the step
that called it), so it was not a sound trust root. `run-security-gate.py`
remains in the repository as a local/offline dry-run tool and as the
reference implementation `test-security-gate.sh`'s S2+S3 test uses to
prove the underlying "protected base beats PR head" property against a
real git repository -- see its own module docstring for the full
explanation of why it is no longer what live CI invokes.

**Post-merge activation, and why Phase 6 is blocked until it's
confirmed**: `pull_request_target` workflows only run when the workflow
file already exists on the default branch -- this means `diana-security-
gate.yml` does **not** execute at all against the very PR that first
introduces it (there is nothing on the base branch to trigger from yet).
The Security Gate check therefore does not protect its own introducing
PR (see "bootstrap" in `diana/security/README.md`); after that PR
merges, the NEXT pull request against this repository is the first real
opportunity to confirm the workflow actually fires and produces a
sensible result. Security Phase 6 must not begin until that activation
has been observed.

The synthetic matrix (`test-advisory.py`) covers safe, missing evidence,
blocker, human-only, non-applicable stack, malformed evidence, sensitive-
adjacent safe path, and sensitive-path escalation cases against the gate's own
exit codes. `test-gate-mapping.sh` covers the exit-code-to-check-conclusion
mapping in isolation. Neither proves semantic risk detection for paths or
categories the catalog does not yet recognize.

`actions/checkout` is pinned to the v7.0.1 commit inspected on 2026-09-02.
