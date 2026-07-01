---
description: Disciplined bug-fix loop — inspect, find root cause, smallest patch, test, repeat if needed.
argument-hint: [bug description, error message, or failing test]
---

# Fix

Apply `research-first` before step 2. Do not shortcut to a patch.

1. **Inspect** — reproduce the bug or read the exact failing test/error
   output. Read the surrounding code, not just the failing line.
2. **Root cause** — state the root cause in one sentence before touching any
   code. If you can't state it confidently, keep reading — do not patch a
   symptom while unsure of the cause. Before patching a shared function,
   inspect its callers; a guard in the shared function is often safer and
   smaller than patching only the reported path.
3. **Smallest patch** — make the minimal change that fixes the root cause.
   No refactors, no renames, no unrelated cleanup riding along.
4. **Test** — run the failing case (existing test, or a new one if none
   covers this) and confirm it now passes. Run nearby tests to check for
   regressions.
5. **If it still fails** — go back to step 2. Do not stack a second patch on
   top of a wrong hypothesis; that's how bugs turn into messes.
6. **Report**:
   - Root cause (one sentence)
   - Patch (file:line, and why this is the smallest correct fix)
   - Test evidence (what you ran, what passed)

If argument is a bug description with no repro steps, ask for one before
guessing at the cause.
