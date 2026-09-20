# Track B · B5 — Human Approval Acceptance Fixture (DO NOT MERGE)

Disposable evidence artifact for Track B Phase B5. It asserts nothing about the
system and must never be merged.

It exists so that the following can be measured on a real pull request, under
live enforcement:

- **B-AC-3** — a human CODEOWNER approval makes a `DIANA-AGENT` pull request
  mergeable;
- **B-AC-4 / CASE 1** — a subsequent diff-affecting push dismisses that
  approval;
- **B-AC-4a / CASES 2–4** — whether a push that does *not* change the diff
  leaves the approval standing;
- **B-AC-5** — a fresh approval restores eligibility.

Its content is deliberately trivial: the commits pushed onto it during the
measurement are the instrument, not this text.

**Closed unmerged once the evidence is recorded in**
`DIANA-HUMAN-APPROVAL-B5.md`.

---

MEASUREMENT MARKER: baseline
