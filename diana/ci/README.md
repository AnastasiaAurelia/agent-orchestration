# Advisory CI Gate

The pull-request workflow reads a strict JSON evidence block from the PR body,
derives changed files from Git, runs the local Diana Gate, and writes the result
to the GitHub job summary. The gate step uses `continue-on-error`; the workflow
is observational and cannot block merge.

The synthetic matrix covers safe, missing evidence, blocker, human-only,
non-applicable stack, malformed evidence, sensitive-adjacent safe path, and
sensitive-path escalation cases. It measures classification only within the v1
contract. It does not prove semantic risk detection for paths/categories the
catalog does not yet recognize.

`actions/checkout` is pinned to the v7.0.1 commit inspected on 2026-09-02.
