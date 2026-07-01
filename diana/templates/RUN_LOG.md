# Run Log

Template — copy to the project root as `RUN_LOG.md` when you set up your
first loop. Append-only audit trail: one JSON object per line (JSONL), one
line per `/orchestrate` run, oldest first. Never edit past entries; never
rewrite history to make a run look better than it was.

## Schema

```
{
  "timestamp": "<ISO8601>",
  "loop": "<name, matching a row in LOOP.md>",
  "level": "L1 | L2 | L3",
  "outcome": "report-only | fix-proposed | escalated | no-op",
  "items_found": <int>,
  "actions_taken": <int>,
  "tokens_estimate": <int, best-effort, omit if unknown>
}
```

`outcome` must be one of the four values above — nothing vaguer. If the run
did nothing because there was nothing actionable, that's `no-op`, not a
missing entry: a loop with no log line for a run it actually performed is
indistinguishable from a loop that silently died.

## Pruning rule

Keep the last 30 days of entries. When `/orchestrate` runs, it may drop
older lines before appending the new one. This file is a debugging and
budget-accounting aid, not a permanent archive — if you need permanent
history, that's what git history on this file is for.

## Entries

<!-- Append JSONL lines below this point. Empty until the first run. -->
