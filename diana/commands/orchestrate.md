---
description: Run one iteration of a registered loop — checks budget and pause state first, defaults to report-only, never self-certifies its own changes.
argument-hint: <loop name from LOOP.md>
---

# Orchestrate

Runs exactly one iteration of the named loop, then stops. This is not a
scheduler or daemon — it does one pass and reports; the user (or `/loop`)
decides when to run it again.

If `LOOP.md`, `STATE.md`, `RUN_LOG.md`, or `BUDGET.md` don't exist yet at the
project root, they are missing project state, not something to create
silently — installing Diana never creates them. Look for the reference
templates in `.claude/templates/diana/` first. If the user explicitly asked
to set up or initialize a loop in this invocation, copy the missing file(s)
from there to the project root and say so. Otherwise, stop and ask before
creating any of them — do not infer loop initialization just from being
asked to run `/orchestrate`. Never overwrite a root loop file that already
exists, even if it looks stale. If `.claude/templates/diana/` itself is
missing, tell the user Diana isn't installed with loop support and stop.

1. **Find the loop** — look up the named loop in `LOOP.md`'s registry table.
   If it isn't listed, stop and say so; do not improvise a loop that was
   never registered.
2. **Check the kill switch** — if `STATE.md` has `Pause: loop-pause-all`,
   append a `no-op` entry to `RUN_LOG.md` noting the pause and stop. Do not
   proceed past this step for any reason.
3. **Check the budget** — apply `BUDGET.md`'s enforcement steps exactly:
   sum this loop's runs and `tokens_estimate` from the last 24h of
   `RUN_LOG.md`. At/over the runs-per-day or 100%-tokens cap, log `no-op`
   and stop. At/over 80% of the tokens cap, continue but force this run to
   report-only regardless of the loop's assigned level. This check is real,
   not advisory — do not skip it because the loop "probably" has budget
   left.
4. **Read state** — read `STATE.md`'s `High Priority` and `Watch List` for
   this loop's prior findings, so you don't re-report resolved or noise
   items.
5. **Resolve the level, then act.** Before doing anything, resolve which
   level actually applies this run:
   - Only run at L2 if `LOOP.md` explicitly assigns this loop L2.
   - Only run at L3 if `LOOP.md` explicitly assigns this loop L3 **and**
     `RUN_LOG.md` shows prior L2 runs with no unresolved escalations.
   - If the level is blank, ambiguous, missing, or you're not certain which
     applies — default to L1 report-only. Never guess upward.
   - (Downgraded further to report-only by step 3 if the budget requires
     it, regardless of what's resolved above.)

   Then act at the resolved level:
   - **L1 (report-only):** investigate only. Do not edit files, run
     fix commands, or commit. Record findings.
   - **L2 (fix + verify):** you may make one small change, then verify it
     as a genuinely separate step, not a continuation of the same
     reasoning pass — see `LOOP.md`'s actor/verifier rule for what
     "separate" means. Before calling the outcome anything but
     `escalated`, that separate verification must have actually happened.
     Wait for human approval before this is committed or merged;
     recording it in `STATE.md`/`RUN_LOG.md` is not approval.
   - **L3 (unattended):** same actor/verifier separation as L2, but may
     commit without waiting for a human.
6. **Update `STATE.md`** — prune resolved/stale items, add new findings
   under the right section, and note `Loop action: <what was tried>` next
   to anything the loop acted on.
7. **Append to `RUN_LOG.md`** — one JSONL line per the schema in that file:
   timestamp, loop, level, outcome (`report-only` / `fix-proposed` /
   `escalated` / `no-op`), items_found, actions_taken, tokens_estimate if
   knowable.
8. **Report to the user** — one short summary: what level this ran at, what
   was found/done, and the outcome. If step 2 or 3 stopped the run early,
   say that plainly instead of a normal-looking report.

Never spawn a subagent or the Task tool as part of this command unless the
user explicitly asks for one in this invocation — a loop is inline work, not
a place to grow an agent fleet.
