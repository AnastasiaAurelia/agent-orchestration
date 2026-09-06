---
description: Ship one small, real, SAFE-risk feature end-to-end through a single certified AO Claude worker plus an independent read-only reviewer — from goal to an open, unmerged PR.
argument-hint: "<feature description>"
---

# Diana ship (single-worker MVP + independent reviewer, Phases 9-10)

This is a different, larger thing than `/ship` (which only produces a
handoff report for work already done in the current session). `/diana-ship`
drives an entire pipeline through one attended AO Claude actor **and** one
independent, read-only AO Claude reviewer: goal → DoD → risk classification
→ actor → actor tests → basic diff scope check → independent reviewer →
reviewer PASS → independent verification → Preflight → Diana Gate → PR.
**It never merges.** The human stays the final approval floor — both for
the actor's real mutations (one-time approval each) and for the resulting
PR. The reviewer never gets write approval at all (see step 8).

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

## 6. Spawn exactly one Claude actor

Through `diana/adapters/ao.py spawn` only (never a raw `ao spawn`
elsewhere) — harness is always `claude-code`, hardcoded by the adapter
itself. The prompt must include: the exact feature goal, the DoD, the
`SAFE` classification, expected file scope, explicitly prohibited scope,
required tests, and explicit instructions: no merge, no deploy, no
unrelated refactor, commit to the actor's own branch, then STOP.

## 7. Observe and attend to the actor

Poll `diana/adapters/ao.py status --session <id>` (wraps the public `ao
session get --json`; never AO's private daemon HTTP API or SQLite — see
`diana/adapters/README.md`'s "Attended mutation approvals" section for why
no supported CLI exists to resolve one, and what the alternative is). Its
returned `session.status`/`session.activity.state` (e.g. `"needs_input"` /
`"waiting_input"`) is the signal that AO is waiting on the session for
something — which may or may not be a real mutation.

When that happens, **surface it to the human plainly** in this
conversation — e.g. "AO session `<id>` needs your attention; please open
it and resolve whatever it's asking for" — and tell them how to look, if
they don't already have AO's UI open: connect a VNC client to the
temporary, loopback-only bridge to AO's private worker display (`:50`),
per `diana/adapters/README.md`. The human looks at AO's own UI, sees the
actual pending item, and — for a genuine mutation approval — chooses
**Allow Once**, never "Always Allow" and never pre-approved or batched.
Then continue polling status until it moves past `needs_input`/
`waiting_input` again. Do not resolve anything programmatically yourself;
this step's only job is to detect the need and hand it to a human. If the
actor fails, stalls, or never produces a commit, stop here — do not
proceed with nothing to review.

Then do the same basic scope check Phase 9 always did, before spending a
reviewer session on anything:

```
python3 diana/ship/ship.py verify-diff --repo <repo> --base <start-sha> \
  --worker-ref <actor-branch> --allowed-files '[...]'
```

## 8. Independent reviewer — separate session, read-only, no shared history

Spawn a **second, separate** AO Claude session through
`diana/adapters/ao.py spawn` (same adapter, same certified profile — never
a raw `ao spawn`). This must be a genuinely fresh session: do not continue
the actor's conversation, do not hand it the actor's chat history or
self-review, and do not tell it "the implementation is correct" or
otherwise bias it. It receives only:

```
ROLE: Independent read-only verifier. You have NO write responsibility.
ORIGINAL GOAL: <the goal from step 2>
DEFINITION OF DONE: <the DoD from step 3>
RISK: SAFE
BASE SHA: <start-sha>
ACTOR COMMIT: <actor-branch/commit>
ALLOWED SCOPE: <the same --allowed-files as step 7>
PROHIBITED SCOPE: everything else

REVIEW TASK:
- Inspect the diff between BASE SHA and ACTOR COMMIT independently.
- Compare it against the Definition of Done, criterion by criterion.
- Identify correctness gaps, unrelated changes, and whether the actor's
  own tests actually exercise the DoD (not just re-assert something
  trivial).
- Return exactly one JSON object on your final line, matching this shape:
  {"decision": "PASS" or "FAIL", "summary": "...",
   "findings": [{"severity": "...", "description": "...", "evidence": "..."}],
   "dod_checks": [{"criterion": "...", "result": "PASS" or "FAIL", "evidence": "..."}]}

STRICT RULES: do not edit any file, do not run a command that writes
anything, do not commit, do not push, do not merge, do not fix anything
yourself, no production mutation, no external publication. If you believe
a fix is needed, describe it as a finding - do not apply it.
```

This session must never be granted a mutation approval — not even through
a human clicking Allow Once. Poll its status the same way as step 7. If it
ever shows `needs_input`/`waiting_input` at all, that alone is a
reviewer-safety defect regardless of what the pending item turns out to
be: **immediately stop the reviewer session**
(`diana/adapters/ao.py stop --session <id>`) without resolving anything
first, through AO's UI or otherwise, and treat the run as failed. A
genuinely read-only reviewer prompt should never produce this in the first
place; do not investigate what it wanted, do not grant it "just this
once," and do not open a VNC bridge to look — stopping the session is the
whole response.

After the reviewer stops, validate its verdict deterministically and
independently confirm it wrote nothing:

```
python3 diana/ship/ship.py review-verdict --verdict '<reviewer's final JSON>'
python3 diana/ship/ship.py reviewer-readonly-check --repo <reviewer worktree> \
  --expected-head <actor-commit-sha>
```

Both must report `ok: true` before continuing. `review-verdict` fails
closed on a `FAIL` decision, malformed/missing output (crashed or stalled
reviewer), or a self-contradictory verdict (`PASS` alongside a failing
`dod_checks` entry). `reviewer-readonly-check` fails closed if the
reviewer's worktree HEAD moved or has any uncommitted changes at all.

**If the reviewer FAILs** (at most one correction cycle — do not loop):

```
python3 diana/ship/ship.py correction-prompt --goal "..." --dod '<DoD>' \
  --risk SAFE --verdict '<reviewer's FAIL JSON>'
```

Spawn one **new** actor session through `diana/adapters/ao.py spawn` (the
adapter has no "send a follow-up message" capability, and adding one is
out of scope for Phase 10 — a fresh spawn keeps actor and reviewer spawns
uniform). Its prompt is the correction prompt above; its very first
instructions must tell it to `git checkout <the original actor branch>` in
its own isolated worktree before making any change (the branch is already
a ref in the shared repository, reachable from any worktree — the same way
Phase 6 observed all local branches visible everywhere), so the correction
lands as a new commit on top of the original actor commit, not a
disconnected one. Attend to its approvals the same way as step 7, then
return to the top of this step with a **fresh, new** reviewer session
(never the same reviewer session or conversation) reviewing the corrected
commit. If this second review also fails, stop and report to the human
rather than attempting a second correction cycle automatically.

## 9. Independent verification — do not trust the actor's own claim

Run whatever tests are actually relevant to this specific feature yourself
(feature-specific judgment `ship.py` deliberately does not hardcode) and
record the results as the `verification` evidence for the next step.

## 10. Preflight, Playwright, and Diana Gate

```
python3 diana/ship/ship.py gate --repo <repo-at-actor-ref> \
  --files '[...from verify-diff...]' \
  --dod '<DoD from step 3>' --verification '<evidence from step 9>' \
  --risk SAFE [--browser-evidence '["..."]']
```

A reviewer `PASS` is a precondition for reaching this step, not a
replacement for it — `ship.py gate` still independently runs real
Preflight and Diana Gate regardless of the reviewer's verdict. If the diff
is browser-facing, `ship.py` will refuse without `--browser-evidence` —
actually perform Playwright MCP verification first (never the AO Electron
browser; see `diana/playwright/README.md`) and pass its evidence. A
reviewer `PASS` cannot waive missing browser evidence. If this stage fails
for any reason (`gate_not_pass`, `browser_verification_missing`, or
anything else), stop — do not create a PR, and do not reinterpret
`REQUIRE_HUMAN` as a pass.

## 11. Open the PR

```
python3 diana/ship/ship.py open-pr --gate-evidence '<from step 10>' \
  --head-branch <actor-branch> --title "..." --body-preamble "..."
```

Push the actor branch first if needed (`gh`/`git push`, never AO). Then
inspect required CI with `gh pr checks` / `gh pr view` — it must be the
same required "Diana Gate" check every other Diana PR goes through, and it
must succeed on its own terms; never bypass or weaken it.

## 12. Report and stop

One concise completion report: feature, DoD, risk, actor session/branch/
commit, reviewer session(s) and verdict(s) (including any correction
cycle), verification result, Playwright result (or explicit skip),
Preflight summary, Diana Gate decision, PR URL and CI state, and exactly
how many times you needed the human (approvals + final review — nothing
else). Then stop. Merging is the human's decision, made outside this
command, whenever they choose.
