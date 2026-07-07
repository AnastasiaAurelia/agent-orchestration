# Loop Registry

This file is a template. Copy it to the project root as `LOOP.md` the first
time you set up a recurring/orchestrated task — do not create it up front on
every project, the same convention as `memory/`.

A "loop" is any task Diana runs more than once against the same goal, via
`/orchestrate`, instead of a single one-shot `/fix` or `/review`. Every loop
needs this file plus `STATE.md`, `RUN_LOG.md`, and `BUDGET.md` at the project
root before `/orchestrate` will run it — `/loop-audit` checks for exactly
that.

## Readiness ladder

A loop starts at L1 and only moves up when the level below has actually run
enough times to trust it. Do not assign L2 or L3 on day one.

- **L1 — report only.** Investigates, writes findings to `STATE.md`, appends
  a run to `RUN_LOG.md`. Never edits files, runs commands that change state,
  or commits. This is the default for every loop unless explicitly upgraded
  below.
- **L2 — fix + verify, human-gated.** May make a change, but the change and
  its verification must happen as separate, non-collapsible steps (see
  "Actor/verifier separation" below), and the result waits for human
  approval before anything is committed or merged.
- **L3 — unattended.** May commit/merge without a human in the loop. Only
  assign this after the same loop has run at L2 repeatedly with no
  escalations. Treat any L3 loop as a standing risk, not a convenience.

## Actor/verifier separation (non-negotiable)

The step that makes a change and the step that checks the change must not be
the same uninterrupted pass. After acting, stop, re-read the result as if you
had not just written it, and confirm independently (re-run the test, re-read
the diff, re-check the assertion) before recording the outcome as anything
other than `escalated`. A loop that certifies its own work is the single
biggest way this goes wrong — `/loop-audit` will fail any loop that can't
show this separation. Model choice does not change any of this — running a
step on a stronger or different model is not a separate verification step,
and is never grounds to skip the human gate at L2/L3.

## Kill switch

Any loop must stop before doing any work if `STATE.md` has:

```
Pause: loop-pause-all
```

This is a single global switch, checked first, every run, no exceptions.

## Registry

| Loop | Cadence | Level | Command | Human gate |
|---|---|---|---|---|
| _example-loop_ | _e.g. on-demand / daily_ | L1 | `/orchestrate example-loop` | _report reviewed by: you_ |

Add one row per loop. Delete the example row once you have a real one.

## See also

- `STATE.md` — what the loop remembers between runs.
- `RUN_LOG.md` — append-only record of what each run did.
- `BUDGET.md` — the caps `/orchestrate` enforces before it acts.
