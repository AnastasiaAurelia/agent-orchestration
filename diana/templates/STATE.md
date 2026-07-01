# Loop State

Template — copy to the project root as `STATE.md` when you set up your first
loop. This is the loop's memory between runs. `/orchestrate` reads it before
acting and rewrites it after every run; you can hand-edit it too — the next
run reads your edits as the current truth.

Keep one of these per project, shared across all loops registered in
`LOOP.md`, unless a loop's findings genuinely don't belong with the others.

```
Last run: <ISO8601 timestamp, set by /orchestrate — empty until first run>
Pause: <absent, or "loop-pause-all" to stop every loop before it acts>

## High Priority
<!-- Items the loop is actively acting on or waiting on a human for.
     One item per line, plus what was tried:
     - [ ] <description>
       Loop action: <what was attempted this run, or "none yet">
-->

## Watch List
<!-- Lower-priority items worth tracking but not acting on yet. -->

## Recent Noise
<!-- Findings dismissed as false positives or already resolved elsewhere.
     Kept briefly so the loop doesn't re-report them; prune on sight once
     you're sure they're stale. -->
```

## Pruning rule

Every run, before writing new findings: drop anything in `High Priority` or
`Watch List` that's since been resolved (merged, closed, fixed elsewhere),
and drop `Recent Noise` entries older than a few runs. A state file that only
ever grows is a loop with amnesia wearing a trenchcoat — it stops being
useful the moment nobody prunes it.
