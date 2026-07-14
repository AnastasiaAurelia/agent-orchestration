---
name: plan-review
description: >
  One-pass sanity check on a plan before implementation — collapses gstack's
  CEO/eng/design/DX review chain into four questions asked in a single pass
  instead of four separate agents. Use before starting any non-trivial task,
  or when asked to "review this plan", "sanity check this approach", or
  "is this the right way to do this".
triggers:
  - review this plan
  - sanity check this approach
  - is this the right approach
  - before we build this
allowed-tools:
  - Read
  - Grep
  - AskUserQuestion
---

# Plan Review

Run this as a single pass, not four separate reviews. For small tasks, do it
mentally in a couple of sentences per lens; for anything touching shared
state, data, or production, write the answers out.

## The four lenses

1. **Framing** — What is actually being asked? Is there a simpler version of
   this that gets 90% of the value? Is this solving the real problem or a
   symptom of it?
2. **Architecture & edge cases** — What's the smallest correct design? What
   breaks it (empty input, concurrent writes, network failure, partial
   completion)? What existing code does this interact with?
3. **Friction** — Where will this confuse the person using it (end user or
   the next engineer reading the code)? Is there a faster/more obvious path
   to the same outcome?
4. **Risk & blast radius** — What's reversible vs. not? What touches
   production, shared infra, or other people's in-progress work? What
   requires explicit confirmation before proceeding (see `careful` hook)?

## Output

Give a verdict, not a report:

- **Proceed** — plan is sound, state it in 1-2 sentences and start.
- **Proceed with changes** — name the one or two changes needed, then start.
- **Stop and ask** — the framing or risk lens surfaced something only the
  user can decide (scope, tradeoff, irreversible action). Ask via
  `AskUserQuestion` instead of guessing.

Do not produce a long written report for small tasks — the point is to catch
the one wrong assumption before writing code, not to perform thoroughness.

## Manual advisor option (optional, human-triggered)

If the **risk & blast radius** lens says this is irreversible or touches
shared/production state, or the **architecture** lens leaves two or more
genuinely different approaches equally plausible, you may offer the user a
manual second opinion before proceeding — never call one automatically.

Ask which they want: no advisor, a manual Opus session, or a manual Fable
session — there is no required order between Opus and Fable. If the user
has no preference, suggest alternating across occasions rather than
defaulting to the same one every time, so a real comparison becomes
possible later. This is for occasional, high-stakes moments — if it's
being reached for on most tasks, that's a sign to stop and reconsider, not
a routine step.

An advisor consult never counts as approval, review, or verification, and
never changes who signs off — it's a second opinion the executor can take
or leave.

If the user picks an advisor (or explicitly declines one after a trigger
fires), note that a consult happened. As soon as the outcome is known —
the guidance was applied, changed nothing, or wasn't used — stop and
explicitly remind the user to create or update `memory/advisor-log.md`
right then, per the schema in `memory/README.md`. Don't defer this to
"later" or assume it'll get written down unprompted; an on-demand log
that's never reminded is a log that's never written.
