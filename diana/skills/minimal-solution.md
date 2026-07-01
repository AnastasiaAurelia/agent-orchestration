---
name: minimal-solution
description: >
  Forces the smallest solution that actually satisfies the task before any
  code is written — question whether it needs to exist, reuse before
  rewriting, standard library before custom code, native platform features
  before dependencies. Use on any coding task: writing, adding, refactoring,
  fixing, reviewing, or choosing a library/dependency. Also use when the user
  complains about over-engineering, bloat, or unnecessary dependencies.
allowed-tools:
  - Read
  - Grep
---

# Minimal Solution

The ladder runs *after* you understand the problem, not instead of it. Read
the task and the code it touches, trace the real flow, then climb.

## The ladder

Stop at the first rung that holds:

1. **Does this need to exist at all?** Speculative need — skip it, say so in
   one line (YAGNI).
2. **Already in this codebase?** A helper, util, type, or pattern that
   already lives here — reuse it instead of re-implementing it.
3. **Stdlib does it?** Use it.
4. **Native platform feature covers it?** Prefer it over a library.
5. **Already-installed dependency solves it?** Use it — don't add a new one
   for what a few lines can do.
6. **Can it be one line?** One line.
7. **Only then**: the minimum code that works.

## When NOT to minimize

Never simplify away: understanding the problem before touching code, input
validation at trust boundaries, error handling that prevents data loss,
security measures, accessibility basics, or anything explicitly requested by
the user. A small diff that skips comprehension to look minimal is not
minimal — it's a second bug waiting to surface.

## Output

State what was skipped and why, in one line, not a design essay:
`[change] → skipped: [X], add when [Y].`
