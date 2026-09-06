---
description: Ship one small, real, SAFE-risk feature end-to-end through a single certified AO Claude worker — from goal to an open, unmerged PR.
argument-hint: "<feature description>"
---

# Diana ship (single-worker MVP, Phase 9)

This is a different, larger thing than `/ship` (which only produces a
handoff report for work already done in the current session). `/diana-ship`
drives an entire pipeline through one attended AO Claude worker: goal → DoD
→ risk classification → worker → independent verification → Preflight →
Diana Gate → PR. **It never merges.** The human stays the final approval
floor — both for the worker's real mutations (one-time approval each) and
for the resulting PR.

The glue at each deterministic stage below is `diana/ship/ship.py`
(`diana/ship/README.md`) — call its subcommands rather than reimplementing
their logic inline. This command supplies the judgment `ship.py` can't:
understanding the goal, writing the DoD, classifying risk, and driving the
one attended AO session.

## 0. Validate repository state

`git status --porcelain` on the target repo must be empty, and
`git rev-parse HEAD` vs `git rev-parse origin/main` should match or be a
clean fast-forward — do this the same way Phase 6/7/8 did (fetch, compare,
fast-forward only if behind with no local work; never overwrite
uncommitted work). If dirty or diverged in a way you can't safely resolve,
stop and tell the user.

## 1. Consult canonical memory

Read `MEMORY.md`, `AGENTS.md`, `diana/governance/risk-tiers.md`, and
relevant `diana/memory/*.md` before proposing anything — do not restart the
architecture discussion or redo settled decisions.

## 2. Accept and normalize the goal

Restate the user's `"<feature description>"` in your own words: intended
outcome, intended scope, explicit non-goals. If it's ambiguous enough to
change scope or risk, ask before proceeding rather than guessing.

## 3. Produce the Definition of Done

Write an explicit, machine-consumable DoD as `{"present": true, "evidence":
[...]}` — see `diana/gate/README.md`'s input schema. At minimum state the
intended outcome, intended scope, verification expected, files/areas
expected (and explicitly prohibited, where useful), whether the change is
browser-facing, and non-goals. This is ephemeral execution evidence for
this ship run, not a second canonical memory system — it lives with this
run's PR evidence, not in `diana/memory/`.

## 4. Classify risk

Using `diana/governance/risk-tiers.md`, classify the task: `SAFE`,
`CONSEQUENTIAL`, `DANGEROUS`, or `HUMAN_ONLY`, plus any
`human_only_conditions`. Do not weaken the classification to force a task
through — if it isn't genuinely `SAFE` with no human-only conditions, stop
and tell the user this pipeline can't run it unattended-through-worker; a
different, smaller, genuinely low-risk feature is needed instead.

## 5. Precheck

```
python3 diana/ship/ship.py precheck --repo <repo> --risk SAFE \
  [--human-only-conditions '[]']
```

If this fails, stop and report exactly why (`repo_dirty`,
`requires_human_review`, `ao_unavailable`) — do not route around it.

## 6. Spawn exactly one Claude worker

Through `diana/adapters/ao.py spawn` only (never a raw `ao spawn`
elsewhere) — harness is always `claude-code`, hardcoded by the adapter
itself. The prompt must include: the exact feature goal, the DoD, the
`SAFE` classification, expected file scope, explicitly prohibited scope,
required tests, and explicit instructions: no merge, no deploy, no
unrelated refactor, commit to the worker's own branch, then STOP.

## 7. Observe and attend to the worker

Poll `diana/adapters/ao.py status --session <id>`. When AO surfaces a
pending tool-call approval for a real mutation, resolve it **one at a
time**, **one-time** (`decisionId: "allow"`, never `allow_always`) via
AO's own approval API — the same attended loop Phase 6 used
(`diana/adapters/README.md` documents the exact endpoint). Do not
pre-approve or batch-approve. If the worker fails, stalls, or never
produces a commit, stop here — do not proceed to verification with
nothing to verify.

## 8. Independent verification — do not trust the worker's own claim

```
python3 diana/ship/ship.py verify-diff --repo <repo> --base <start-sha> \
  --worker-ref <worker-branch> --allowed-files '[...]'
```

Then run whatever tests are actually relevant to this specific feature
yourself (this is feature-specific judgment `ship.py` deliberately does not
hardcode) and record the results as the `verification` evidence for the
next step.

## 9. Preflight, Playwright, and Diana Gate

```
python3 diana/ship/ship.py gate --repo <repo-at-worker-ref> \
  --files '[...from verify-diff...]' \
  --dod '<DoD from step 3>' --verification '<evidence from step 8>' \
  --risk SAFE [--browser-evidence '["..."]']
```

If the diff is browser-facing, `ship.py` will refuse without
`--browser-evidence` — actually perform Playwright MCP verification first
(never the AO Electron browser; see `diana/playwright/README.md`) and pass
its evidence. If this stage fails for any reason (`gate_not_pass`,
`browser_verification_missing`, or anything else), stop — do not create a
PR, and do not reinterpret `REQUIRE_HUMAN` as a pass.

## 10. Open the PR

```
python3 diana/ship/ship.py open-pr --gate-evidence '<from step 9>' \
  --head-branch <worker-branch> --title "..." --body-preamble "..."
```

Push the worker branch first if needed (`gh`/`git push`, never AO). Then
inspect required CI with `gh pr checks` / `gh pr view` — it must be the
same required "Diana Gate" check every other Diana PR goes through, and it
must succeed on its own terms; never bypass or weaken it.

## 11. Report and stop

One concise completion report: feature, DoD, risk, worker session/branch/
commit, verification result, Playwright result (or explicit skip),
Preflight summary, Diana Gate decision, PR URL and CI state, and exactly
how many times you needed the human (approvals + final review — nothing
else). Then stop. Merging is the human's decision, made outside this
command, whenever they choose.
