# Diana Nightshift — Research Brief

## 1. Status and Decision

- **Status:** Research
- **Decision state:** GO WITH REDUCED SCOPE
- **Last updated:** 2026-07-28
- This document is research input, not an approved implementation spec. Nothing
  here authorizes building the full concept described below in one pass — see
  §6 for what is actually in scope and §7 for what is deliberately deferred.

This brief replaces an earlier 0-byte placeholder committed at
`docs/nightshift/RESEARCH.md` (commit `1002ed2`). The original four-pillar
concept (deterministic state machine + Karpathy-style autoresearch +
planner/generator/evaluator role separation + a source-backed knowledge graph)
was reviewed against this repository and against the cited external sources.
Parts of it do not survive that review and are not carried forward as if they
were settled (see §4, §7).

## 2. Problem

Diana already has a working notion of a "loop" — `/orchestrate`
(`diana/commands/orchestrate.md`) plus `LOOP.md`, `STATE.md`, `BUDGET.md`, and
`RUN_LOG.md` (templates at `diana/templates/`). That machinery defines a kill
switch, a budget cap, an L1/L2/L3 readiness ladder, and an explicit
actor/verifier separation rule (`diana/templates/LOOP.md:30-40`: "A loop that
certifies its own work is the single biggest way this goes wrong").

The gap: **every one of those checks is enforced by a Claude session reading
markdown and choosing to comply.** `orchestrate.md` instructs the model to
"check the kill switch," "check the budget," and resolve its own level
(`diana/commands/orchestrate.md:26-47`) — there is no shell/Python/Node script
anywhere in this repository that parses `STATE.md` or `BUDGET.md` and refuses
to proceed on its own. That is sufficient for a supervised, interactive
session where a human is present to notice non-compliance. It is not
sufficient for a session running unattended, overnight, with nobody watching.

**Central thesis:** a deterministic wrapper should claim one bounded task,
launch one fresh Claude Code session, recover from failure, and produce a
trustworthy report — without requiring a human to supervise the run while it
happens. Everything in this brief is organized around proving that thesis
with the smallest amount of new code, before adding anything else.

## 3. Existing Diana Foundation

| Component | Path | What it already provides | What Nightshift needs beyond it |
|---|---|---|---|
| `/orchestrate` | `diana/commands/orchestrate.md` | The policy shape: kill switch check, budget check, level resolution, actor/verifier separation, run-log append | Deterministic (code) enforcement of all of the above — currently these are all prompt instructions to the model, not code |
| `LOOP.md` | `diana/templates/LOOP.md` | Readiness ladder (L1/L2/L3), non-negotiable actor/verifier separation rule (`:30-40`), kill switch definition (`:42-50`) | A runtime that reads this and physically cannot skip the check |
| `STATE.md` | `diana/templates/STATE.md` | Loop memory schema, pruning rule, `Pause: loop-pause-all` kill switch field | Same — currently read/written by the model's own discipline, not enforced externally |
| `BUDGET.md` | `diana/templates/BUDGET.md` | Cap schema (runs/day, tokens/day) and an explicit enforcement procedure (`:20-35`) | A script that actually sums `RUN_LOG.md` and refuses to proceed, rather than a model doing the arithmetic on trust |
| `RUN_LOG.md` | `diana/templates/RUN_LOG.md` | A proven JSONL append-only schema and pruning rule — directly reusable for Nightshift's own run log | Nothing extra — reuse the schema as-is |
| `research-first` | `diana/skills/research-first.md` | Generic inspection discipline for any bounded task | Nothing Nightshift-specific — reusable unchanged inside the executor's prompt |
| `minimal-solution` | `diana/skills/minimal-solution.md` | Generic scope discipline | Reusable unchanged |
| `/fix` | `diana/commands/fix.md` | Root cause → smallest patch → test → report shape | Useful as the executor's working discipline; its self-report must never be the acceptance mechanism |
| `/review` | `diana/commands/review.md` | Self-review pass shape | Same caveat as `/fix` |
| `/ship` | `diana/commands/ship.md` | SHIP/NO-SHIP verdict shape, defaults to NO-SHIP if no test ran (`:19-22`) | The verdict is **self-reported by the same session that did the work** — useful as report shape, not usable as the deterministic acceptance gate |
| `check-careful.sh` | `diana/hooks/check-careful.sh` | PreToolUse regex check on Bash commands, returns `{"permissionDecision":"ask", ...}` (`:81-84`) | "ask" requires an interactive human; it is a no-op or a hang with nobody present overnight. Also self-documented as pattern matching, not a shell parser (`README.md:314-315`) — a nudge for supervised sessions, not a security boundary for unattended ones |
| Cost reporting | `diana/hooks/cost-report.md`, Stop-hook JSONL log at `~/.claude/diana/costs.jsonl` | A working, proven pattern for per-session token/cost logging | Nightshift's report should cross-reference this by `session_id`, not build a second cost pipeline |
| Installer / verifier / uninstaller | `install.sh`, `verify.sh`, `uninstall.sh` | Idempotent backup-then-overwrite pattern (`install.sh:67-98`), PASS/FAIL checks, "only remove what we installed" discipline (`uninstall.sh:187-193`) | Directly reusable pattern for Nightshift's own install/verify/uninstall scripts — no new installer style needed |

**Summary distinction:**
- **Reusable policy, unchanged:** `research-first`, `minimal-solution`, the
  actor/verifier principle in `LOOP.md`, the `RUN_LOG.md` schema.
- **Reusable installation pattern:** `install.sh` / `verify.sh` /
  `uninstall.sh`'s backup-and-idempotency conventions.
- **Missing deterministic enforcement:** everything `/orchestrate` currently
  asks the model to self-check (pause flag, budget, level, actor/verifier
  separation) has no code behind it. This is the actual gap Nightshift's MVP
  must close — not by extending `/orchestrate`, but by writing the first real
  enforcement code this repository has had.
- **Unsuitable for unattended use as-is:** `check-careful.sh`'s interactive
  "ask" response.

## 4. Source Findings

### The Night Shift

The originating concept (a bounded, auditable overnight AI work session) is
this project's own framing, not an external citation. SUPPORTED ONLY BY LOCAL
RESEARCH BRIEF — there is no independent source to verify beyond the
architecture conclusions in §5–§8 of this document.

### Karpathy Autoresearch

- Repository confirmed to exist: `github.com/karpathy/autoresearch`
  (VERIFIED FROM ORIGINAL SOURCE — checked via GitHub API, 92k+ stars, real
  README fetched).
- The actual pattern: **one narrow mutable surface** — a single file,
  `train.py` — is the only thing the agent is allowed to edit; `prepare.py` is
  fixed. **One fixed objective metric** — validation bits-per-byte
  (`val_bpb`), chosen specifically because it is comparable regardless of what
  the agent changes. **One fixed time budget** — training always runs for
  exactly 5 minutes wall-clock, deliberately chosen so ~12 experiments/hour
  are directly comparable. (All VERIFIED FROM ORIGINAL SOURCE — quoted from
  the fetched README's "Design choices" section.)
- The operating model is a **single long-running conversational session with
  all permissions disabled** ("Simply spin up your Claude/Codex... and disable
  all permissions"), not a queue of fresh sessions launched by an external
  scheduler. There is no lock manager, no crash-recovery step, no role
  separation, and no fresh-session-per-iteration model described anywhere in
  the source (VERIFIED FROM ORIGINAL SOURCE, by absence — not mentioned in the
  README's "How it works" or "Design choices" sections).
- **Correct characterization: `autoresearch` is not a general-purpose agent
  orchestration framework.** Its safety comes entirely from the narrowness of
  its scope (one file, one metric, one fixed budget) and from a domain
  (single-GPU LLM training) that has a clean, non-gameable success signal.
  General software repositories, and Nightshift's target tasks, do not have an
  equivalent single clean metric. Citing this repo as precedent for
  "autonomous ideation" or "open-ended modify/measure/keep-or-revert" on
  arbitrary tasks would be a category error — it is precedent only for the
  much narrower claim that a fixed-budget, fixed-metric loop can run
  unsupervised, which is not what most Nightshift tasks will look like.

### Anthropic Long-Running Harness

Source: `anthropic.com/engineering/harness-design-long-running-apps`
(VERIFIED FROM ORIGINAL SOURCE — fetched directly).

- Recommends specialized roles — planner, generator, evaluator — specifically
  because "agents tend to respond by confidently praising the work... even
  when, to a human observer, the quality is obviously mediocre." This is
  direct support for never letting an executor session certify its own
  output.
- Recommends context resets between sessions with structured file-based
  handoffs, rather than in-place summarization, so state survives a fresh
  session boundary cleanly.
- Recommends active testing (e.g., browser automation) over code review alone
  for the evaluator role, and "sprint contracts"/grading criteria that convert
  subjective judgment into measurable criteria before work begins.
- Notes explicitly that harness components encode assumptions about model
  limitations and should be re-evaluated as models improve — i.e., this is not
  a permanent architecture, it is a current-generation scaffold.
- This source supports the *principle* Diana already states in
  `LOOP.md:30-40`. It does not, by itself, justify building four dedicated
  role-based Claude Code **skills** for Nightshift — see §8 for why most of
  these roles classify as deterministic code or one-shot runtime prompts
  instead.

### Claude Knowledge Graph Cookbook

Source: `platform.claude.com/cookbook/capabilities-knowledge-graph-guide`
(VERIFIED FROM ORIGINAL SOURCE — fetched directly).

- This is a **document extraction and question-answering precedent**: given a
  static corpus of documents, extract entities/relations via structured LLM
  calls, resolve duplicate surface forms via LLM clustering with
  descriptions as context, track provenance as `source_docs`/`source_doc`
  fields on nodes/edges, and answer multi-hop questions by serializing
  subgraphs back to the model. Storage is NetworkX in-memory at dev scale;
  Neo4j/Neptune/Postgres are explicitly called out as "production scale"
  upgrades only.
- Contradiction handling is folded into the synthesis prompt ("resolving any
  contradictions by preferring the most specific claim") — it is not a
  standalone "contradiction detector" component in the source's own design.
- **Correct characterization: this is not direct evidence for Nightshift
  runtime state management.** It is a pattern for building a Q&A graph over a
  document corpus (the source's own worked example is Apollo 11 facts). It
  says nothing about tracking coding-agent session provenance, task outcomes,
  or repository state over time. Any knowledge-graph component for Nightshift
  would be an analogy borrowed from this cookbook, not a precedent it
  actually establishes — and is deferred entirely for this phase (§7).

### Beam/X Source

Source: `x.com/beamnxw/status/2081022966645535079` — **could not be
retrieved**. The fetch returned `HTTP 402 Payment Required`. **No claim from
this source is verified, and none is represented as verified anywhere in this
document.** It is CURRENTLY UNVERIFIED in its entirety and should not be
relied upon for any architectural decision until it can actually be read.

## 5. Architectural Conclusions

- Nightshift should be **optional and separate from Diana core** — a
  companion capability, not a merge into `diana/`. Diana's own stated
  philosophy (`README.md:12-14`, "not a giant AgentOps platform... not a
  multi-agent marketplace") and its "Growth Discipline" test
  (`diana/CLAUDE.md:70-80`: does it directly support the core loop, can it be
  added/tested/removed in under a day, will it be used weekly) are not met by
  the full four-pillar concept, and should not be relaxed to fit it in.
- The MVP must use **deterministic runtime code** for claim/lock/retry/
  acceptance/report — not prompt instructions. This is the one gap §3
  identified in Diana's existing loop machinery, and it is the actual
  substance of what needs building.
- **Runtime roles should not automatically become Claude Code skills.**
  Skills in this repository are installed persistently
  (`install.sh:84-86`: copied to `.claude/skills/<name>/SKILL.md`) so an
  *interactive* session can recognize a natural-language trigger. Nightshift's
  sessions are launched programmatically with an explicit, pre-chosen prompt
  — there is no "recognize the intent" moment for a trigger list to serve.
  Building `nightshift-executor.md`, `-evaluator.md`, `-curator.md`,
  `-reporter.md`, `-ideator.md`, `-experimenter.md`, and
  `-graph-maintainer.md` as skills, as the original concept proposed, would be
  installed prompt-folder bloat with no discovery moment to justify it (see
  §8).
- **Existing Diana policy should be reused where applicable** — the
  actor/verifier separation rule, the `RUN_LOG.md` schema, and the
  installer/verifier/uninstaller conventions are proven and should not be
  reinvented (§3).
- **Prompt instructions are not security boundaries.** `check-careful.sh`'s
  "ask" response and any executor-session self-report are both prompt-level;
  neither is sufficient on its own for anything unattended (§12).
- **The model must not own queue, lock, retry, acceptance, or reporting
  state.** All five are deterministic-runtime-owned (§11). A Claude session
  may read from and write into its own bounded working copy; it does not
  write the canonical queue, lock, retry counter, acceptance verdict, or
  report.

## 6. MVP Scope

The MVP includes only:

- a deterministic queue (JSONL, one line per task);
- atomic claim (OS-level exclusive lock, e.g. `flock`);
- overlap prevention (lock + liveness check on the recorded PID);
- a fresh Claude Code session per task (`claude -p`, no `--resume`/
  `--continue`);
- bounded execution (one task contract, restricted tool allowlist, no push/
  network access by omission);
- an authentication preflight that fails closed before any budget is spent
  (§10);
- an explicit timeout enforced externally (`timeout`(1)/process-group kill —
  the `claude` CLI itself has no native `--timeout` flag, confirmed via local
  `claude --help`);
- independent, deterministic acceptance (real exit code from an actual test/
  build run, never the session's self-report);
- crash recovery (stale-lock detection via dead-PID + max-age, requeue);
- a hard retry cap (small integer, then permanently failed, never infinite);
- a deterministic local report generated purely from state files;
- full captured stdout/stderr as visible failure evidence.

Nothing beyond this list is in scope for the MVP (see §7).

## 7. Explicitly Deferred

| Deferred capability | Evidence required before adding |
|---|---|
| Autonomous ideation | A trusted, boring track record of the queue mechanism over many hand-authored tasks |
| Curator | Concurrency or multi-task volume that actually produces something to curate — none exists at one-task-at-a-time scale |
| Planner | A large-enough set of hand-authored task contracts to show a stable, automatable schema |
| Independent LLM evaluator | A logged pattern, over real MVP runs, of tasks that pass deterministic tests but are still substantively wrong |
| Knowledge graph | Evidence that plain prose memory (`diana/memory/*.md` convention) is demonstrably insufficient for a specific, named query need |
| Entity resolution | Same — dependent on the knowledge graph actually being justified first |
| Autoresearch mode (open-ended modify/measure/keep-or-revert) | A bounded domain with a clean, non-gameable objective metric, comparable to `autoresearch`'s `val_bpb` — most target tasks will not have this |
| Git worktrees | An actual observed crash that corrupted a working tree mid-edit, or a genuine need for concurrent tasks |
| Slack / webhooks | The local file report has been used and trusted for real, plus an explicit human-configured, human-approved channel and secret-storage story |
| Browser automation | A target project's acceptance criteria genuinely requiring UI verification |
| Multi-repository execution | Proven single-repo reliability first |
| API fallback | An explicit, opt-in decision the human turns on knowingly — never a silent default |
| Deployment | Not evidence-gated — out of scope for this capability entirely, pending separate explicit authorization |
| Git push | Same — always a human action, not something evidence unlocks |
| Package installation | Same — a reason to stop and ask, never to auto-install |
| Production database mutation | Not applicable to this capability at all |

## 8. Primitive Classification

| Component | Primitive | Persistent install? | Notes |
|---|---|---|---|
| Executor | Runtime prompt/template, generated per task | No | Invoked via `claude -p`; not a skill — there is no trigger-recognition moment |
| Evaluator | Deterministic runtime code (exit-code/test check) is primary | Yes (the check itself) | Any LLM-based secondary check, if ever added later, is a runtime prompt invoked separately from the executor, never the same pass |
| Reporter | Deterministic runtime code | Yes | Built from state files only; extends the `cost-report.md` JSONL pattern |
| Queue manager | Deterministic runtime code | Yes | New — no equivalent exists in this repo today |
| Lock manager | Deterministic runtime code + OS `flock` | Yes | New |
| Reaper | Deterministic runtime code (PID liveness + timeout kill) | Yes | New |
| Scheduler | Scheduler configuration (systemd timer or cron) | Yes (unit file/crontab line) | Choice undetermined — see §9 |
| Acceptance checker | Deterministic runtime code | Yes | The component `ship.md`'s self-reported verdict must never be mistaken for |
| Authentication preflight | Deterministic runtime code | Yes | New — genuinely missing everywhere in this repo today |
| Timeout controller | Deterministic runtime code + OS `timeout`(1) | Yes | GNU `timeout` confirmed present locally |
| Outbound-action blocker | Deterministic runtime code, enforced via the CLI's own `--allowedTools`/`--disallowedTools`/`--permission-mode` flags | Yes (invocation config) | `check-careful.sh`'s interactive "ask" is the wrong mechanism for this |
| Task contract | Data/schema file | Yes (a template) | Mirrors the existing `LOOP.md`/`STATE.md` "copy to project root" convention |
| Completion command | Slash command | Yes (thin, on-demand) | Mirrors `diana/commands/loop-audit.md`'s read-only posture |

## 9. Dependency Decisions

| Dependency | Verdict |
|---|---|
| LangChain | Unnecessary / actively harmful complexity — contradicts this repo's stated "not a framework" philosophy |
| LangGraph | Same |
| CrewAI | Same |
| AutoGen | Same |
| Neo4j | Unnecessary for MVP (no knowledge graph in scope); useful later only if a graph is ever built past dev scale |
| NetworkX | Unnecessary for MVP, same reasoning |
| SQLite | Unnecessary for MVP — not installed on the inspected machine; the existing JSONL append-only pattern already covers single-writer MVP scale |
| JSONL | Required for MVP — matches `diana/templates/RUN_LOG.md`'s existing schema exactly, zero new dependency |
| OS-native file locking (`flock`/`fcntl.flock`) | Required for MVP — no third-party locking library needed |
| cron | **Not decided.** Present on the inspected machine, no crontab currently configured. Requires a small research spike before selection. |
| systemd timer | **Not decided.** Present and already in active use on the inspected machine for unrelated jobs, which is a point in its favor for logging/failure-hook integration, but this is not sufficient to select it outright. Requires the same spike as cron — and neither survives host/laptop suspend, which is a separate, still-open risk regardless of which is chosen. |
| Anthropic Python SDK | Unnecessary — the design shells out to the already-installed `claude` CLI to preserve subscription auth; a direct SDK call would require an API key and reintroduce metered billing |
| Git worktrees | Unnecessary for MVP (§7); if added later, prefer the CLI's own native `-w`/`--worktree` flag (confirmed present) over a hand-rolled `git worktree` wrapper |
| Docker / container isolation | Unnecessary for MVP; useful later only if task descriptions must be sandboxed more strongly than OS-level tool restriction provides |
| Slack API | Unnecessary for MVP (§7); actively harmful complexity if added before basic local-file reliability is proven — it is a new outbound-network surface and a new secret to store |

## 10. Subscription Authentication

**Verified local facts** (inspected directly on the reviewed machine):

- Current Claude Code authentication method: `claude.ai` OAuth
  (`claude auth status` → `authMethod: "claude.ai"`, `apiProvider:
  "firstParty"`).
- Subscription type: `pro`.
- `claude setup-token` exists as a documented first-party subcommand: "Set up
  a long-lived authentication token (requires Claude subscription)" — the
  CLI's own answer for enabling non-interactive subscription use.
- No `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / cloud-provider credential
  environment variables were present during inspection of the interactive
  session's environment.
- No token or credential value is recorded anywhere in this document.

**Unresolved questions, not settled by the above:**

- Whether OAuth token refresh happens transparently when `claude -p` is
  launched by cron/systemd — a different, more stripped environment than an
  interactive shell — is unverified.
- Whether a scheduler's environment inherits enough of the user's profile to
  reach the same credential state observed interactively is unverified;
  cron/systemd-user environments generally do not auto-source a full shell
  profile, but this was not confirmed specifically for this CLI's credential
  path.
- Expired-auth behavior (fail closed vs. hang vs. silent no-op) is unverified
  — deliberately not tested here, since forcing an expiry would risk
  disrupting the reviewed account's real login state.
- Silent API-fallback risk: if a scheduler's environment ever contains a
  stray `ANTHROPIC_API_KEY` from an unrelated tool, a run could silently bill
  through the metered API instead of the subscription, with no visible
  symptom besides a bill. Nothing currently checks for this.
- Behavior across host/laptop suspend during a scheduled window is
  unverified for both cron and systemd timers.

**Scheduled unattended execution is not approved until a real authentication
preflight spike passes** — i.e., until a `claude -p` invocation, launched by
the actual chosen scheduler in its actual stripped environment, is observed
to succeed on subscription auth end-to-end, with a preflight check that fails
closed if it does not.

## 11. State Ownership

| Item | Owning layer |
|---|---|
| Queue | Deterministic runtime |
| Task status | Deterministic runtime |
| Retry count | Deterministic runtime |
| Lock | OS (`flock`) + deterministic runtime |
| Claimed task | Deterministic runtime |
| PID | OS (wrapper tracks the spawned child directly) |
| Timeout | OS (`timeout`(1)/process-group kill) + deterministic runtime |
| Test result | Deterministic runtime (captured real exit code) |
| Acceptance result | Independent evaluator / deterministic runtime — never the executor session itself |
| Report | Deterministic runtime |
| Outbound notification | Disabled in MVP — not applicable until explicitly added later with human approval |
| Graph state | Disabled in MVP — not applicable |

## 12. Safety Principles

- **Fail closed** — any preflight or check that cannot confirm a safe
  condition stops the run rather than proceeding optimistically.
- **No outbound action** — no network/messaging tool in the executor's scope
  for this phase.
- **No git push** — always a human action, never in the executor's allowed
  tool/command scope.
- **No deployment.**
- **No package installation** — a reason to stop and mark the task failed,
  not to auto-install.
- **No production database access.**
- **Restricted tool allowlist** — enforced via the CLI's own
  `--allowedTools`/`--disallowedTools`/`--permission-mode` flags, not via
  `check-careful.sh`'s interactive "ask."
- **Path-bound execution** — all writes validated against the bounded
  working-copy root before they land; no writes outside it.
- **Atomic state writes** — queue/lock/log files written via a temp-file-plus-
  rename pattern, never left partially written.
- **No model-owned canonical state** — matches §11 exactly.
- **No self-certified completion** — acceptance is always a separate,
  deterministic check, independent of the executor session's own claim.
- **No silent API billing** — the authentication preflight (§10) must
  actively confirm subscription auth before proceeding, not merely assume
  the absence of an API key.

## 13. Trust-Test Requirements

Grouped from the full failure-mode audit; every category below must pass
before any scheduled, unattended run is permitted:

- **Concurrency and locking** — two concurrent claim attempts yield exactly
  one success; a live lock cannot be stolen; a stale (dead-PID) lock becomes
  recoverable; two overlapping scheduled runs resolve to exactly one active
  worker.
- **Authentication** — expired or missing auth is detected before any budget
  is spent and fails closed; a stray API credential in the environment is
  detected and flagged rather than silently used.
- **Process lifecycle** — a task exceeding its timebox is killed, including
  any child processes; forced termination of the wrapper itself leaves a
  recoverable, not corrupted, lock/queue state.
- **State corruption** — malformed queue entries, duplicate task IDs, and
  partial/truncated writes are all detected and rejected rather than trusted;
  a disk-full condition during a write does not leave the queue file
  half-written.
- **Acceptance integrity** — a session that claims success but fails real
  tests is marked failed; a session that exits zero without doing the work is
  still checked independently; missing or malformed run-log evidence is
  treated as a failure, not a silent pass.
- **Security boundaries** — attempts at secret access, destructive commands,
  external messaging, git push, package installation, and writes outside the
  bounded working copy are all blocked by tool/path restriction, not by an
  unanswered "ask" prompt.
- **Reporting and recovery** — a report is still generated after a crash or
  after a run with zero successes, and states that plainly rather than
  omitting or padding it; a manual edit to the queue during an in-flight
  claim is detected rather than silently overwritten.

## 14. First Implementation Milestone

**Implement only the deterministic queue-claim-lock primitive.**

No Claude invocation. No scheduler. No report generator. No knowledge graph.
No ideation. No evaluator agent.

**Acceptance:**

- Two concurrent claim attempts against the same queue produce exactly one
  success and one clean, logged rejection.
- A live lock cannot be stolen by a second claim attempt.
- A lock held by a verifiably dead PID becomes recoverable/reclaimable after
  a staleness threshold.
- All of the above are proven by automated tests, not manual inspection.

## 15. Non-Goals

Mirroring `README.md`'s existing "v0.1 Philosophy" section
(`README.md:317-337`), this capability explicitly rejects, for this phase:

- An AgentOps platform.
- A generic multi-agent framework.
- A dashboard.
- An agent marketplace.
- A router service.
- A premature or generic plugin system.
- Prompt-folder proliferation — see §8's rejection of role-based skill files.
- Speculative infrastructure of any kind not required by §6's MVP scope.
