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
3. **Browser verification, only when the diff is browser-facing.** Decide
   whether the change touches browser-rendered surface (UI components,
   pages, styles, markup) — not every change needs this, and it must never
   become mandatory for a CLI/library/backend-only change. If this project
   has `diana/playwright/browser_applicable.py`, run it against the diff's
   changed-file list as a deterministic first-pass signal; otherwise use
   the same judgment you already apply to the checks above. If applicable
   and a Playwright MCP server is available, verify the actual behavior
   with its tools (`browser_navigate`, `browser_click`, `browser_type`,
   `browser_fill_form`, `browser_snapshot`, `browser_take_screenshot`,
   ...) instead of reasoning about UI behavior from the diff alone.
   Capture an accessibility-snapshot excerpt and a screenshot as evidence
   for any finding this surfaces, and report it inline as a normal
   finding — not a second, separate verdict. If not applicable, state
   "browser verification: skipped (not browser-facing)" and move on. See
   `diana/playwright/README.md` for the full ownership boundary and
   evidence format.
4. Output a ranked list of findings, most severe first. If there is nothing
   worth flagging, say "no findings" — don't pad the review with
   observations that require no action.

Do not fix anything in this pass unless explicitly asked; report first.
