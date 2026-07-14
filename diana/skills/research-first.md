---
name: research-first
description: >
  Forces inspection of the repo, docs, logs, and actual error output before
  proposing a fix, answer, or explanation. Use whenever the task involves a
  bug, an unfamiliar area of the codebase, or any claim about "how the code
  works". Exists to prevent guessing dressed up as confidence.
triggers:
  - why is this failing
  - investigate
  - debug this
  - how does this work
allowed-tools:
  - Read
  - Grep
  - Bash
---

# Research First

Before forming a hypothesis, do these in order. Do not skip ahead to a fix
because the symptom "looks familiar."

1. **Read the actual error** — full stack trace or failure output, not just
   the first line. Note the exact file:line it points to.
2. **Read the surrounding code**, not just the line that errored — the
   function, its caller, and anything it mutates or depends on.
3. **Check for existing documentation of the behavior** — comments, tests,
   README/docs that describe what this code is supposed to do. A test that
   already covers this path is the fastest way to confirm or kill a
   hypothesis.
4. **Reproduce if you can** — run the failing case yourself rather than
   reasoning about it in the abstract.

Only after these four steps, form a hypothesis.

## Reporting discipline

Separate what you observed from what you're inferring. Bad:

> The bug is caused by a race condition in the queue handler.

Better:

> Observed: request X times out after ~30s under concurrent load (repro'd
> locally). Inferred: likely a race condition in the queue handler at
> `queue.ts:142`, because it doesn't lock before the read-modify-write —
> haven't confirmed with a targeted test yet.

If you haven't done steps 1-4, say so instead of presenting a guess as a
diagnosis.

## When the root cause still won't resolve

If steps 1-4 are genuinely done and the root cause still can't be stated
confidently, or a second distinct fix attempt has failed on what was
believed to be the same root cause, that's one of the trigger conditions
for `plan-review.md`'s manual advisor option — see that file for how to
offer it. Don't loop a third guess without considering it.
