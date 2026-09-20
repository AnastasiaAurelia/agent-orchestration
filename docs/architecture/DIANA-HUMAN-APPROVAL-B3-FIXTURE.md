# Track B · B3 — Adversarial Acceptance Fixture (DO NOT MERGE)

Disposable evidence artifact. It asserts nothing about the system and must
never be merged. It exists so that Track B can measure, on a real pull request
authored by the automation identity:

- the pre-enforcement negative baseline — whether a `DIANA-AGENT` pull request
  with **zero** human approvals is mergeable into `main` today;
- whether GitHub refuses a self-approval submitted by the pull request's own
  author using the normal automation credential;
- whether CODEOWNERS routing requests the human owner even while
  `require_code_owner_review` is `false`;
- that both required Diana checks run normally on an automation-authored
  pull request.

It changes no rule, no ruleset, no CODEOWNERS entry, no workflow and no
runtime file.

**This pull request is closed unmerged once the evidence is recorded in**
`DIANA-HUMAN-APPROVAL-B3.md`.

Merging it would add a meaningless file to `main` and would exercise the exact
zero-approval merge path that Track B exists to close.
