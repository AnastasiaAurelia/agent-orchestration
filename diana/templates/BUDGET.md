# Loop Budget

Template — copy to the project root as `BUDGET.md` when you set up your
first loop. This is not documentation Claude reads for background — every
cap here is checked and enforced by `/orchestrate` before it does any work.
A cap that isn't actually checked is worse than no cap: it looks safe and
isn't.

## Caps

| Loop | Max runs/day | Max tokens/day (est.) |
|---|---|---|
| _example-loop_ | 1 | 100000 |

Add one row per loop registered in `LOOP.md`. Delete the example row once
you have a real one. Leave a cap at a conservative number until the loop has
proven it needs more — raising a cap is a one-line edit; a runaway loop that
already blew past a missing cap is not.

## Enforcement (what `/orchestrate` must actually do, every run)

1. Sum `tokens_estimate` and count entries for this loop from `RUN_LOG.md`
   entries in the last 24 hours.
2. If `runs_today >= Max runs/day`, or `Pause: loop-pause-all` is set in
   `STATE.md` → **stop before doing any work.** Append a `no-op` entry to
   `RUN_LOG.md` noting the cap or pause, and exit.
3. If `tokens_today >= 80% of Max tokens/day` → **drop to report-only for
   this run** regardless of the loop's assigned level in `LOOP.md`. Do not
   fix, commit, or spawn anything.
4. If `tokens_today >= 100% of Max tokens/day` → **stop before doing any
   work**, same as step 2.
5. Otherwise, proceed at the level `LOOP.md` assigns this loop.

If a run finds nothing actionable, exit cheaply — don't spend budget looking
harder than the loop's purpose calls for.
