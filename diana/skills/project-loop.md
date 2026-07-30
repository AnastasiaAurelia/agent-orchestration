---
name: project-loop
description: >
  Advances an approved backlog through bounded, verified iterations using
  Diana's existing loop files and safety levels. Use when asked to work through
  a project backlog, run the project loop, or continue with the next bounded
  item. This skill does not silently initialize, schedule, deploy, push, merge,
  or spawn agents.
triggers:
  - run the project loop
  - work through this backlog
  - continue with the next item
  - take the next bounded item
allowed-tools:
  - Read
  - Grep
  - Bash
  - Edit
  - Write
  - AskUserQuestion
---

# Project Loop

Advance an already approved backlog through Diana's existing loop machinery.
This is a bounded, sequential workflow, not a second orchestrator: `LOOP.md`,
`STATE.md`, `RUN_LOG.md`, and `BUDGET.md` remain the only loop registry, state,
run log, and limits.

Run `loop-design` before enabling this workflow for a project. If the four root
loop files are missing, follow `/orchestrate`'s initialization rule: only copy
the templates from `.claude/templates/diana/` when the user explicitly asks to
initialize a loop. Never create `VISION.md`, `LOOP_STATE.md`,
`loop.config.yml`, or `agent.config.yml`.

## Bounds

One invocation processes at most the user-supplied iteration limit. If none was
supplied, the limit is **one item**. If the user supplied a runtime limit,
record the start time and stop before selecting another item once elapsed time
has reached that limit. `BUDGET.md` caps and `Pause: loop-pause-all` are
additional hard limits and are checked before every item.

Never schedule another invocation, recurse, spawn Claude or a subagent, use
unattended infrastructure, deploy, push, merge, open a PR, or handle
credentials. Authentication, security-sensitive access, production access, or
any request for those actions is a human-input stop.

## Preconditions

Before changing code:

1. Follow `/orchestrate` steps 1-5 to resolve the registered loop, pause state,
   budget, prior state, and effective L1/L2/L3 level. A new loop remains L1.
2. Locate the backlog the user approved. Treat only explicit unchecked entries
   in that source as eligible; do not invent work from general project vision,
   issues, TODO searches, or reviewer suggestions.
3. For each eligible item, require:
   - a stable identifier or exact source location;
   - a bounded outcome that can be completed in one iteration;
   - explicit acceptance criteria;
   - named test/check commands, discovered from repository docs or CI.
4. If any requirement is missing or ambiguous, move or retain the item under
   `STATE.md`'s `High Priority` with `Loop action: awaiting human input`, log
   `escalated`, and stop. Do not guess acceptance criteria or commands.

At L1, inspect the backlog and state only, record the next eligible item, update
the run log as `report-only`, and stop without planning implementation, editing,
running state-changing commands, or committing.

## One iteration

At L2 or L3, run these phases for exactly one eligible item:

1. **Inspect backlog** — prune resolved state entries, preserve backlog order,
   and choose the first eligible item whose dependencies are complete. Selection
   is mechanical: do not reorder by preference, novelty, or inferred value.
2. **Plan** — apply `research-first`, `plan-review`, and `minimal-solution`.
   Write a brief plan tied to the item's acceptance criteria, affected files,
   and named checks. Stop if the item is not bounded after inspection.
3. **Implement** — make only the smallest change needed for this item. Do not
   combine cleanup, another backlog item, or a newly discovered enhancement.
4. **Test** — run every named acceptance command. A command passes only with
   exit status 0. If a command cannot run, that is not a pass.
5. **Adversarial review** — start a separate review pass: re-read the approved
   item and acceptance criteria, inspect the complete diff, and assume the
   implementation is over-confident. Run `/review`, then re-run the affected
   acceptance commands after any repair.
6. **Ship and commit gate** — verification is `PASS` only when all of these
   observable conditions hold:
   - every acceptance criterion is checked;
   - every named command most recently exited 0;
   - `/review` has no unresolved correctness, security, missing-test, scope, or
     unrelated-change finding;
   - the diff contains only this item's implementation and tests;
   - `git diff --check` exits 0.

   Run `/ship` with this evidence. A `NO-SHIP` verdict blocks the item. A
   `SHIP` verdict is necessary but cannot override a failed observable
   condition above.

   At L2, record `fix-proposed` and stop for human approval; do not commit.
   At L3, commit only after `PASS` and `SHIP`, using a message scoped to the
   item. Never push or merge. A stronger model or a self-authored statement is
   not evidence for this gate.
7. **Update loop state** — apply the transition table below, update
   `STATE.md`, and append exactly one `RUN_LOG.md` entry using its existing
   schema. Do not rewrite previous log entries.
8. **Select next** — if bounds allow another iteration, return to preconditions
   and mechanically select the next eligible item. Otherwise, report the next
   eligible item (or backlog completion) and stop.

## Deterministic transitions

Use the first matching row. The evidence column, not model confidence,
determines the transition.

| Evidence | State transition | Run outcome | Continue? |
|---|---|---|---|
| No unchecked approved entries | Prune resolved entries; record backlog complete | `no-op` | Stop |
| Pause, budget, iteration, or runtime limit reached | Leave item pending; note the reached limit | `no-op` | Stop |
| Missing/ambiguous criteria, dependency, or command | High Priority: awaiting human input | `escalated` | Stop |
| Auth, security-sensitive, or production access required | High Priority: awaiting human input | `escalated` | Stop |
| Acceptance command nonzero, safe repair unavailable | High Priority: blocked with command and exit status | `escalated` | Stop |
| Review has an unresolved blocking finding | High Priority: blocked with finding | `escalated` | Stop |
| L2 verification is `PASS` | High Priority: verified, awaiting approval | `fix-proposed` | Stop |
| L3 verification is `PASS` and commit succeeds | Remove completed item from High Priority; note commit | `fix-proposed` | Within bounds |
| Commit fails | High Priority: blocked with git error | `escalated` | Stop |

Backlog completion is determined only by the approved backlog's explicit item
markers. Test and review results never silently mark source backlog entries
complete; update the backlog only when its documented convention or explicit
human instruction authorizes that edit.

## Required stop report

Report the effective level, selected item, acceptance commands and exit
statuses, review result, commit hash if one was created, state/log transition,
the exact stop reason, and the next eligible item if one exists.
