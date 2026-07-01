---
name: loop-design
description: >
  Pre-flight checklist for a new or changing loop before it's registered in
  LOOP.md or upgraded to a higher level — sibling to plan-review but scoped
  to loop safety instead of general plans. Use before enabling any new
  /orchestrate loop, before raising a loop from L1 to L2/L3, or when asked
  to "set up a loop", "automate this check", or "run this on a schedule".
triggers:
  - set up a loop
  - automate this recurring task
  - run this on a schedule
  - raise this loop to L2
  - is this loop safe
allowed-tools:
  - Read
  - Grep
  - AskUserQuestion
---

# Loop Design

Run as a single pass before a loop goes into `LOOP.md`, not as a heavyweight
design doc. For a loop staying at L1 (report-only), a couple of sentences
per question is enough; for anything requesting L2 or L3, write the answers
out — the blast radius justifies it.

## The checklist

1. **Single goal** — what is this loop actually for, in one sentence? If you
   can't state it in one sentence, it's two loops wearing one name.
2. **Cadence** — on-demand, or a stated interval? Vague cadence ("run it
   sometimes") is not a cadence.
3. **Triage step distinct from verify step** — for anything above L1: is the
   step that proposes a change genuinely separate from the step that checks
   it, or would this collapse into one uninterrupted pass? If they'd
   collapse, this fails until that's fixed — see `LOOP.md`'s actor/verifier
   rule.
4. **Budget cap defined** — does `BUDGET.md` have real numbers for this
   loop (not a placeholder row), and is a downgrade-to-report-only path
   defined for when it's near the cap?
5. **State file present** — will this loop use `STATE.md` for memory between
   runs, with pruning, or will it re-discover the same findings every time?
6. **Kill switch documented** — does the loop check `Pause: loop-pause-all`
   in `STATE.md` before doing anything, every run, no exceptions?
7. **Level matches maturity** — is the requested level justified by actual
   track record, per `LOOP.md`'s readiness ladder? Default to L1 for
   anything new.

## Output

Give a verdict, not a design essay:

- **Proceed** — checklist holds, state the loop's one-sentence goal and
  level, then add its row to `LOOP.md`.
- **Proceed with changes** — name the one or two gaps (usually missing
  budget numbers, missing verifier separation, or an unjustified level),
  fix those, then add it.
- **Stop and ask** — the loop would touch production, shared state, or
  irreversible actions at L2/L3, and the tradeoff is the user's call, not
  yours. Ask via `AskUserQuestion` instead of assigning a level yourself.

Never approve L3 for a loop with no prior run history — there is nothing
yet to justify unattended trust.
