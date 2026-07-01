---
description: Assess whether a registered loop is safe to run, or to run at its assigned level — does not fix anything, only scores readiness.
argument-hint: [loop name from LOOP.md — omit to audit all registered loops]
---

# Loop Audit

Read-only, same posture as `/review`: assess and report, don't fix. Run this
before enabling a new loop, before raising a loop's level, or any time a
loop's behavior looks off.

1. **Files present** — do `LOOP.md`, `STATE.md`, `RUN_LOG.md`, and
   `BUDGET.md` exist at the project root, and does the loop have a row in
   each? Missing any one of these is an automatic **not ready**.
2. **Kill switch reachable** — does `STATE.md` support a `Pause:
   loop-pause-all` line, and does `LOOP.md`/`orchestrate.md`'s process
   actually check it first? (Check the command, not just that the file has
   a slot for it.)
3. **Actor/verifier separation** — for any loop above L1, is there a real,
   separate verification step, or does the same pass that makes the change
   also certify it? A loop that self-certifies fails this check regardless
   of what level it claims to be.
4. **Budget enforcement is real** — does `BUDGET.md` have a row for this
   loop with actual numbers (not placeholders), and does the run process
   genuinely compute today's usage from `RUN_LOG.md` before acting? A
   budget file that exists but isn't read before acting is a **fail**, not
   a partial pass.
5. **Run log discipline** — do entries follow the documented schema
   (timestamp, loop, level, outcome, items_found, actions_taken), and is
   `outcome` always one of the four allowed values? Vague or missing
   outcomes are a red flag.
6. **Level matches evidence** — is the loop assigned a level (`LOOP.md`)
   consistent with its track record (`RUN_LOG.md`)? L3 with no L2 history,
   or L2 with a string of `escalated` outcomes and no fixes since, are both
   findings.

## Output

Score each loop **L0 (not ready)**, **L1 (report-only ready)**, **L2 (fix +
verify ready)**, or **L3 (unattended ready)** — this is the *highest* level
the evidence supports, which may be lower than what `LOOP.md` currently
assigns it. List the specific gaps for anything short of the assigned
level; if there are none, say so plainly instead of padding the report.

Do not change `LOOP.md`'s assigned level yourself — recommend it, and let
the user decide whether to downgrade or fix the gap first.
