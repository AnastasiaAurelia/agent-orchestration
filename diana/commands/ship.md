---
description: Produce final handoff — changed files, tests run, risks, ship/no-ship verdict.
argument-hint:
---

# Ship

Produce a short handoff, not a narrative.

1. **Changed files** — `git diff --stat` (or equivalent), with a one-line
   reason per file for why it changed.
2. **Tests run** — what you ran this session and the result (pass/fail
   counts). If no tests were run, say so explicitly — do not imply testing
   happened if it didn't.
3. **Risks** — anything not covered by a test, anything touching shared or
   production state, anything hard to reverse.
4. **Verdict** — `SHIP` or `NO-SHIP`, one sentence why.

Default to `NO-SHIP` if:
- tests weren't actually run in this session, or
- there's an unreviewed destructive operation in the changes, or
- unrelated changes are mixed in with the intended fix/feature.
