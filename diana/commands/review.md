---
description: Review the current diff for bugs, overengineering, missing tests, and unrelated changes.
argument-hint: [optional: PR number, or "staged"]
---

# Review

1. Get the diff: `git diff` (unstaged), `git diff --staged`, or
   `gh pr diff <n>` if a PR number is given.
2. Check for, in this order:
   - **Correctness bugs** — logic errors, off-by-one, unhandled edge cases,
     error handling missing at boundaries (user input, external calls).
   - **Overengineering** — abstractions, config, or flags this task didn't
     ask for. Three similar lines beat a premature helper. Use this format
     per finding:
     - `delete:` location → what to remove → why
     - `stdlib:` location → dependency/custom code → standard-library replacement
     - `native:` location → custom logic → platform/framework feature
     - `yagni:` location → speculative abstraction → simpler current need
     - `shrink:` location → oversized implementation → smaller equivalent
   - **Missing tests** — does new/changed behavior have a test, and does
     that test actually exercise the change (not just re-assert a mock)?
   - **Unrelated changes** — anything in the diff not explained by the
     stated task. Flag it for removal or a separate change.
3. Output a ranked list of findings, most severe first. If there is nothing
   worth flagging, say "no findings" — don't pad the review with
   observations that require no action.

Do not fix anything in this pass unless explicitly asked; report first.
