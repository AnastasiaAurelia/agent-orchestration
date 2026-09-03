# DIANA — Architecture Memory / Handoff

_Last updated: 2026-08-31_

This file is the canonical handoff for the current Diana architecture discussion.  
Use it to start a fresh Claude Code / Codex / ChatGPT session without re-deriving the architecture from scratch.

---

## 0. Executive Summary

Diana should **not** become a giant AgentOps platform, model gateway, browser engine, worktree manager, or inference provider.

The target architecture is:

- **Diana** = policy + workflows + Definition of Done + project memory + governance + quality gates
- **Agent Orchestrator (AO)** = worker/session/worktree execution runtime
- **Playwright MCP** = browser automation and verification
- **GitHub CLI (`gh`)** = PR / CI state
- **OmniRoute** = optional user-side provider routing, configured outside Diana
- **Headroom** = not in the active roadmap; reconsider only if measured token pain appears
- **Context7 / Firecrawl / Perplexity** = optional capabilities only when a real use case appears
- **No Diana Cloud, no shared owner API key, no custom provider router**

The biggest architectural finding from the feasibility review:

> **Markdown rules are not enforcement.**

Diana currently has instruction-level governance but no provider-neutral runtime enforcement.  
Before adding autonomous workers, Diana must first get deterministic enforcement via:

1. **execution-side safety / sandboxing**
2. **merge-side `diana-gate` + branch protection + human review**

A merge gate protects trunk integrity but **does not** prevent a running worker from causing destructive side effects before a PR exists.

---

# 1. Current Diana Reality

Diana today is mostly an **operating protocol for Claude Code**, not a runtime.

Current approximate shape:

- Markdown-heavy repo
- A few shell scripts
- Commands
- Skills
- Hooks
- Memory conventions
- Loop governance templates

Important current assets that must be preserved:

```text
/fix
/review
/ship
/orchestrate
/loop-audit

plan-review
research-first
minimal-solution
loop-design

LOOP.md
STATE.md
RUN_LOG.md
BUDGET.md

check-careful.sh
memory conventions
installer lifecycle
```

The differentiated part of Diana is the existing governance/loop model:

- L1 / L2 / L3 trust/readiness ladder
- actor/verifier separation
- budget limits
- loop kill-switch behavior
- state/run-log conventions

These are **not replaced by AO**.

---

# 2. Intentional Identity Change

Existing Diana language includes ideas such as:

- “Diana is not a framework”
- `/orchestrate` should not silently grow an agent fleet
- growth should remain removable, disciplined, and justified

The new architecture does **not** mean Diana becomes a giant framework.

The intended amendment is:

```text
Diana remains a thin policy/workflow/governance layer.

External execution runtimes may be used by explicit workflows,
but Diana itself does not become:

- a model router
- a worktree manager
- a browser engine
- an inference provider
- a giant AgentOps platform
```

Diana's job is to decide **how work should be done and what counts as done**.

---

# 3. Final Architecture

```text
                         USER
                          │
              Claude / Codex / CLI
                          │
                          ▼
                ┌─────────────────┐
                │      DIANA      │
                │ policy/workflow │
                │ governance/DoD  │
                └────────┬────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
      AGENTS.md        MEMORY        PREFLIGHT
      policy/DoD       decisions     quality gates
                         │
                         ▼
                  EXECUTION POLICY
                         │
                         ▼
                 Agent Orchestrator
                         │
                  safe worker profile
                         │
                         ▼
                   git worktree
                         │
                  implementation
                         │
                         ▼
                    tests/review
                         │
               Playwright if needed
                         │
                         ▼
                       PR
                         │
                         ▼
                    DIANA-GATE
                         │
                  required CI check
                         │
                         ▼
                   HUMAN APPROVAL
                         │
                         ▼
                       MERGE
```

Provider/inference path stays separate:

```text
Claude / Codex
     │
     ├──────── native provider
     │
     └──────── OmniRoute (user optional)
                       │
                  model providers
```

Diana does **not** become a mandatory inference proxy.

---

# 4. Safety Architecture

The final safety model must have **two enforcement boundaries**:

```text
TASK
  │
  ▼
INSTRUCTION POLICY
AGENTS.md / Diana rules
  │
  ▼
EXECUTION SAFETY / SANDBOX
what the worker can physically do
  │
  ▼
AO WORKER
  │
  ▼
WORKTREE
  │
  ▼
DIANA-GATE
what is allowed to merge
  │
  ▼
HUMAN REVIEW
  │
  ▼
MERGE
```

Important distinction:

```text
human review required
≠
agent prohibited from executing
```

Some actions are allowed to be prepared by an agent but require human approval before merge.

Other actions are **HUMAN_ONLY** and should never be autonomously executed.

---

# 5. Critical AO / Codex Safety Finding

The feasibility review found that AO currently launches Codex with dangerous bypass behavior by default, including flags equivalent to:

```text
--dangerously-bypass-approvals-and-sandbox
--dangerously-bypass-hook-trust
```

There is no clean per-project permission knob exposed for Codex in the inspected AO version.

Therefore:

```text
AO + Claude Code worker
    ✅ PILOT / candidate for safe-write after certification

AO + Codex read-only investigation
    ⚠️ prototype carefully / treat as untrusted

AO + Codex autonomous write
    ❌ NOT CERTIFIED
```

Do **not** justify unsafe Codex execution by saying “the merge gate will catch it.”

Damage can happen before merge:

- deleting files outside intended worktree scope
- network exfiltration
- production API calls
- credential misuse
- cloud CLI commands
- database mutation
- external side effects

Codex autonomous writes through AO remain blocked until one of these is true:

1. AO upstream exposes safe permission controls
2. we contribute a minimal safe AO patch
3. Codex runs inside a proven outer OS/container sandbox

Then Codex must repeat:

```text
read-only prototype
→ execution-safety test
→ safe-write pilot
```

---

# 6. Canonical Policy and Memory

## 6.1 Canonical engineering policy

Use:

```text
AGENTS.md
```

as the canonical provider-neutral engineering constitution.

`CLAUDE.md` remains Claude-specific and should point to `AGENTS.md` instead of duplicating the same policy.

Core principles to absorb from Karpathy-inspired guidelines:

- Think before acting.
- State assumptions and ambiguity.
- Reuse before build.
- Choose the simplest sufficient solution.
- Make surgical changes.
- Do not modify unrelated code.
- Define Definition of Done before implementation.
- Verify before claiming completion.

Reference:

https://github.com/multica-ai/andrej-karpathy-skills

Do **not** install it as a Claude-only dependency. Absorb the useful principles.

---

## 6.2 Canonical memory ownership

Recommended:

```text
diana/memory/

├── decisions.md
├── known-issues.md
├── advisor-log.md
└── learned-rules.md
```

Keep existing:

```text
STATE.md
RUN_LOG.md
BUDGET.md
```

Ownership:

```text
Engineering rules
→ AGENTS.md

Project decisions
→ diana/memory/decisions.md

Known issues
→ diana/memory/known-issues.md

Advisor log
→ diana/memory/advisor-log.md

Future learned behavior
→ diana/memory/learned-rules.md

Loop state/history
→ STATE.md / RUN_LOG.md / BUDGET.md

AO session/worker telemetry
→ AO internal state
```

AO execution state should **not** become Diana project memory.

Headroom must not become a competing memory writer.

---

# 7. Governance Policy

Create:

```text
diana/governance/risk-tiers.md
```

Initial tiers:

## SAFE

Examples:

- read
- inspect
- search
- normal tests
- static analysis
- reversible local changes

## CONSEQUENTIAL

Agent may prepare/perform only through explicit workflow, with an approval requirement.

## DANGEROUS / HUMAN_ONLY

Initial hard-floor categories:

- production deployment
- destructive production database migration
- production data deletion
- credential/key rotation or replacement
- billing/payment infrastructure changes
- disabling security controls
- destructive shared git history changes
- external publishing
- consequential outbound communication
- branch protection / CI protection changes

Important:

This policy is initially **instruction/classification**, not enforcement.

Hard enforcement arrives through execution safety + `diana-gate`.

---

# 8. Core Software Component #1 — `diana-gate`

`diana-gate` is the first provider-neutral hard enforcement Diana should own.

Suggested path:

```text
diana/gate/diana-gate.sh
```

or another very small deterministic implementation if shell proves too fragile.

Initial responsibilities:

```text
1. Is Definition-of-Done evidence present?
2. Is required verification/test evidence present?
3. Did any blocker preflight check fail?
4. Is the diff risky?
5. Does it trigger any human_only rule?
6. Are inputs malformed?
```

Outputs:

```text
PASS
FAIL
REQUIRE_HUMAN
```

No LLM decides the exit code.

Mandatory fixture matrix:

```text
SAFE source change + DoD + no blocker
→ PASS

missing DoD evidence
→ FAIL

blocker preflight result
→ FAIL

human_only/risky change
→ REQUIRE_HUMAN

irrelevant stack-specific check
→ SKIP, not FAIL

malformed/missing input
→ FAIL CLOSED

safe test touching a sensitive-adjacent path
→ must not false-positive
```

Activation sequence:

```text
STEP A
local gate only

STEP B
synthetic/fixture tests

STEP C
non-blocking CI status

STEP D
after several correct runs:
required CI status + branch protection + human review
```

Do **not** make the first version a required branch-protection gate immediately.

---

# 9. Claude Hook Portability

Current Diana safety hook is registered through a local/gitignored settings file.

AO worktrees use normal `git worktree add`, so ignored/untracked files do not automatically move into new worktrees.

For Claude Code workers, investigate/implement committed:

```text
.claude/settings.json
```

for Diana's portable hook registration, while allowing AO to keep its own:

```text
.claude/settings.local.json
```

The goal is:

```text
fresh worktree
→ Diana committed hook present
→ AO local hooks present
→ both coexist
```

Test:

- normal command works
- deliberately risky test command triggers Diana hook
- fresh worktree inherits configuration
- AO hook + Diana hook coexist

This protects **Claude Code workers only**.

It does not protect Codex.

---

# 10. Core Software Component #2 — Preflight

Future command:

```text
diana preflight
```

Do not implement 37 giant prompts.

Use a check catalog.

Suggested schema:

```yaml
id:
category:
applicable_when:
severity:
check_type:
evidence_required:
auto_fixable:
```

Three evidence classes:

## DETERMINISTIC

Examples:

- suspicious exposed `.env` / config files
- frontend secret patterns
- localhost/staging URLs
- accidental `noindex`
- robots/sitemap issues
- metadata/OG presence
- 404 route
- oversized assets
- build/test evidence

## AUTOMATED EXTERNAL

Examples:

- Playwright flows
- SPF/DKIM/DMARC DNS checks
- Core Web Vitals
- analytics beacon firing
- rate-limit probing
- Stripe webhook verification
- mobile core-flow

## HUMAN / JUDGEMENT

Examples:

- adequacy of legal wording
- correctness of business/legal MoR posture
- semantic correctness of complex RLS/paywall logic
- meaningfulness of an analytics funnel

Never label a human/judgement check as “AI passed.”

All checks must be applicability-aware:

```text
No Stripe
→ Stripe check SKIP

No web frontend
→ SEO/browser checks SKIP

No Convex
→ Convex-specific checks SKIP
```

Start with ~6 deterministic checks, then grow toward the full 37 from evidence.

---

# 11. Browser Decision

Use:

https://github.com/microsoft/playwright-mcp

for browser automation.

Do **not** use AO's Electron browser in the Diana architecture.

Reason:

AO's browser is already a full browser automation engine and requires Electron. Running both AO browser + Playwright duplicates capability.

Final ownership:

```text
AO
= worker/session/worktree

Playwright MCP
= browser automation / verification
```

Playwright should initially be integrated only into `/review`.

Prototype must prove:

- headless operation
- open local app
- click
- fill
- navigate
- capture evidence
- detect a real broken flow
- worker/worktree configuration reaches Playwright reliably

---

# 12. Agent Orchestrator Decision

Use:

https://github.com/Untrivial-ai/agent-orchestrator

but keep the integration boundary narrow.

AO owns only:

```text
spawn worker
session lifecycle
worktree
worker lifecycle/status
```

Diana should not depend directly on AO database/private daemon internals.

Use one adapter only:

```text
diana/adapters/ao.*
```

Only that adapter knows AO commands.

Expected calls:

```text
ao spawn
ao session get
ao session ls
```

Pin an exact AO version.

Reason: the inspected AO CLI is moving quickly and `ao spawn` does not currently expose clean JSON output.

For PR/CI state, use:

```text
gh
```

instead of reaching into AO's private daemon API.

Architecture:

```text
Diana
 ├── AO adapter → workers/worktrees
 └── gh         → PR/CI state
```

---

# 13. OmniRoute Decision

Repo:

https://github.com/diegosouzapw/OmniRoute

Final verdict:

> **User-configured outside Diana.**

No Diana integration code is needed.

Expected:

```text
Claude / Codex
     ↓
user's OmniRoute
     ↓
user's credentials
     ↓
providers
```

Diana remains unaware of provider credentials.

Important BYOK invariant:

```text
User A
→ their client / OmniRoute
→ their credentials
→ their bill

User B
→ their client / OmniRoute
→ their credentials
→ their bill
```

Never:

```text
public user
→ Diana
→ owner API key
```

Document for users:

- use own credentials
- set OmniRoute storage encryption where supported
- understand emergency fallback behavior
- avoid unstable `auto/*` routing if it breaks session semantics
- know how to bypass OmniRoute for a session

## Ultrafast

Feasibility inspection suggested:

```text
ultrafast
→ appears to pass through on the wire

fast
→ may be rewritten to priority
```

Do not trust this without an end-to-end test.

Test:

```text
client
→ OmniRoute
→ OpenAI
→ verify actual returned service tier
```

If routing semantics are wrong:

```text
unset OmniRoute base URL
→ native provider
```

Do **not** build a Diana ModelRouter to solve this.

---

# 14. Headroom Decision

Repo:

https://github.com/headroomlabs-ai/headroom

Final verdict:

> **Remove from active roadmap. Reconsider only when measured token pain exists.**

Reasons:

- Diana currently does not assemble model messages, so Headroom library mode has no natural call site.
- wrapper mode introduces a proxy/request-path layer
- wrapper/settings behavior can collide with Diana-managed Claude settings
- code/file reads are not necessarily where the strongest compression gain is realized
- quality regression / re-reading risk exists
- Diana should not create a second memory system

Reconsider only if all are true:

```text
measured token pain
+
clear insertion point
+
quality benchmark shows no regression
```

If ever revisited:

- no competing router
- no autonomous `headroom learn`
- no competing canonical memory
- measure task success, not only token savings

---

# 15. Optional Capabilities

These are not core dependencies.

## Context7

https://github.com/upstash/context7

Use when current/version-specific documentation is materially needed.

No capability abstraction until there is a second docs provider.

---

## Firecrawl

https://github.com/firecrawl/firecrawl-mcp-server

Use for:

- multi-page extraction
- site crawling
- structured web extraction

For simple interaction with one website, Playwright is enough.

---

## Perplexity MCP

https://github.com/perplexityai/modelcontextprotocol

Use only if Diana workflows materially need an additional research backend with citations.

Not core.

---

# 16. Conditional Preflight References

Use only when the detected stack needs them.

Web quality:

https://github.com/addyosmani/web-quality-skills

Convex:

https://github.com/get-convex/agent-skills

Stripe:

https://github.com/t3dotgg/stripe-recommendations

Marketing/legal reference:

https://github.com/kostja94/marketing-skills

These are references/conditional capabilities, not global Diana dependencies.

---

# 17. Explicit Non-Goals

Do not add these unless a future architecture review proves a new gap.

```text
❌ Diana Cloud
❌ shared owner API key
❌ hosted inference
❌ user billing system

❌ custom ModelRouter
❌ custom provider adapters
❌ custom worktree manager
❌ custom browser engine
❌ custom compression engine

❌ Headroom active integration
❌ premature capability registry

❌ CrewAI
❌ browser-use
❌ Claude Mem
❌ AnythingLLM
❌ Cline embedded as subsystem
❌ Pipecat core
❌ Postiz core

❌ AO Electron browser

❌ autonomous AO Codex write
   until execution safety is solved
```

---

# 18. Master Roadmap — Start to End

## PHASE 0 — Freeze Current Diana

Goal:
Create a known-good baseline before architecture changes.

Do:

- tag current Diana
- record fresh install
- reinstall
- verify
- uninstall
- confirm existing commands/hooks/loop/memory behavior

Suggested tag:

```text
diana-pre-runtime-baseline
```

Exit:
Everything current is known-good.

Rollback:
Checkout baseline tag.

---

## PHASE 1 — Canonical Identity / `AGENTS.md`

Goal:
Make Diana provider-neutral.

Do:

- create root `AGENTS.md`
- make it canonical engineering policy
- point `CLAUDE.md` to it
- deliberately revise the “Diana is not a framework” language
- explicitly state Diana remains a thin policy/workflow/governance layer

Exit:

```text
Claude Code → sees Diana policy
Codex       → sees same Diana policy
```

No duplicated constitution.

---

## PHASE 1B — Canonical Memory

Goal:
Remove ambiguity before multi-agent work.

Do:

```text
diana/memory/
├── decisions.md
├── known-issues.md
├── advisor-log.md
└── learned-rules.md
```

Keep loop runtime files:

```text
STATE.md
RUN_LOG.md
BUDGET.md
```

Exit:
Every type of memory has exactly one owner.

---

## PHASE 1C — Advisory Governance Tiers

Goal:
Define policy before enforcement.

Create:

```text
diana/governance/risk-tiers.md
```

Define:

```text
SAFE
CONSEQUENTIAL
DANGEROUS
HUMAN_ONLY
```

Exit:
Rules are committed and readable by both Claude and Codex.

Important:
Still advisory/instruction level.

---

## PHASE 1D — Local `diana-gate`

Goal:
Build first deterministic enforcement logic.

Create:

```text
diana/gate/diana-gate.sh
```

Test the full fixture matrix.

Exit:
All gate fixture tests pass.

No CI enforcement yet.

---

## PHASE 1E — Portable Claude Safety Hook

Goal:
Make Diana hook survive worktrees.

Investigate/use committed:

```text
.claude/settings.json
```

while preserving user settings and AO's local settings.

Exit:

- new worktree contains Diana hook
- risky fixture triggers hook
- normal commands work
- AO hooks + Diana hooks coexist

Claude only.

---

## PHASE 1F — Non-Blocking CI Gate

Goal:
Observe gate behavior in realistic PRs without blocking development.

Add advisory CI status.

Measure:

- false positives
- false negatives
- bad path classifications
- missing evidence cases

Exit:
No unacceptable false classification in the test set.

---

## PHASE 1G — Required CI + Branch Protection

Goal:
Create real merge-boundary enforcement.

Make:

```text
Diana Gate = required status
Human review = required
Branch protection = enabled
```

Agents must not be allowed to modify branch protection.

Exit:
Deliberately invalid PR cannot merge.

---

## PHASE 2 — Preflight v0

Goal:
Prove deterministic quality gates.

Implement ~6 high-value checks only.

Candidate checks:

1. suspicious exposed secrets/config
2. likely frontend secret leakage
3. localhost/staging residue
4. accidental noindex
5. expected build/test evidence
6. required stack-specific safety configuration

Exit:
Runs correctly on two very different repos with irrelevant checks skipped.

---

## PHASE 3 — Playwright Prototype

Goal:
Give `/review` real browser verification.

Add Playwright MCP directly.

No capability registry.

Exit:
One real frontend bug can be reproduced and verified headlessly.

AO browser remains off.

---

## PHASE 4 — AO Read-Only Prototype

Goal:
Prove Diana can delegate safely before allowing writes.

Pin exact AO version.

Use one AO adapter.

First task:

> Inspect this repo and report where auth is implemented.

Worker:

```text
Claude Code only
```

Verify:

- worker spawned
- isolated worktree exists
- `AGENTS.md` is present
- Diana memory is visible
- Claude hook exists
- worker does not change source
- status can be retrieved

Exit:
One safe investigation task works end-to-end.

---

## PHASE 5 — Worker Execution-Safety Certification

Goal:
Determine what the AO Claude worker can physically do while running.

Verify:

- AO launch flags
- no bypassPermissions
- normal Claude permission model
- Diana committed hook active
- worktree isolation
- credential scoping
- network/tool behavior understood

Use disposable fixtures to test risky operations.

Exit:

```text
CLAUDE AO WORKER
CERTIFIED FOR SAFE-WRITE PILOT
```

If this cannot be proven:

```text
STOP AO WRITE ROADMAP
```

---

## PHASE 6 — AO Safe-Write Pilot

Goal:
Allow one tiny, controlled file change.

Example:

```text
fix one deterministic failing test
```

Flow:

```text
Diana DoD
→ risk classification
→ AO Claude worker
→ isolated worktree
→ edit
→ tests
→ commit branch
→ STOP
```

Never:

```text
AUTO MERGE
AUTO DEPLOY
```

Exit:
Only intended files changed and all changes remain isolated.

---

## PHASE 7 — Productionize AO Adapter

Goal:
Make AO a supported execution backend without broad coupling.

AO owns:

```text
spawn
session
worktree
worker lifecycle
```

GitHub CLI owns:

```text
PR / CI state
```

Architecture:

```text
Diana
 ├── AO adapter → workers/worktrees
 └── gh         → PR/CI
```

Exit:
AO can be upgraded/replaced by changing one adapter.

---

## PHASE 8 — Grow Preflight Toward Full 37 Checks

Goal:
Turn the checklist into evidence-backed launch validation.

Add checks incrementally.

Deterministic:

- security/config
- findability
- metadata
- build artifacts
- static checks

Automated external:

- Playwright flows
- DNS
- Core Web Vitals
- analytics
- Stripe
- rate-limit probes
- mobile flow

Human:

- legal adequacy
- business/paywall correctness
- MoR posture
- semantic security review where deterministic proof is impossible

Exit:
Preflight becomes useful without pretending every check is automatable.

---

## PHASE 9 — `diana ship` Single-Worker MVP

Goal:
Prove the complete workflow with one trusted worker.

```text
diana ship "feature X"
      │
      ▼
inspect memory
      │
define goal
      │
define DoD
      │
risk classify
      │
AO Claude worker
      │
isolated worktree
      │
implementation
      │
tests
      │
Playwright if applicable
      │
preflight
      │
PR
      │
Diana Gate
      │
human review
      │
merge
```

Exit:
One real feature goes from requirement → verified PR without manual micromanagement of every middle step.

---

## PHASE 10 — Independent Reviewer

Goal:
Restore strong actor/verifier separation in the automated path.

```text
Worker A
implementation
     │
     ▼
Worker B
independent reviewer
     │
     ├── FAIL → back to A
     │
     └── PASS → gate
```

Reviewer begins read-only.

Exit:
Reviewer catches a deliberately planted defect.

---

## PHASE 11 — Multi-Worker Execution

Goal:
Add real parallelism only after the single-worker path is trustworthy.

Start with non-overlapping responsibilities:

```text
worker A → implementation
worker B → tests
worker C → review
```

Avoid several agents editing the same files initially.

Exit:

- no worktree collisions
- correct integration
- deterministic review
- governance still enforced

---

# 19. Parallel / Optional Track

These do not block the main roadmap.

## OmniRoute

User setup only.

Test Ultrafast separately.

No Diana code.

## Context7

Add only when current docs repeatedly matter.

## Firecrawl

Add only when site extraction repeatedly matters.

## Perplexity

Add only when research workflows justify it.

---

# 20. Blocked / Later

## AO Codex Writes

Blocked until safe execution is proven.

## `diana learn`

Blocked because current Diana logs do not yet record enough provider-neutral correction signal.

Before implementing, capture:

- user corrections
- review failures
- repeated instructions
- recurring successful patterns

Future flow:

```text
sessions
→ candidate rules
→ human approval
→ learned-rules.md
```

Never silent self-modification.

## Headroom

Backlog only.

Revisit on measured token pain + clear call site + quality benchmark.

---

# 21. Complete Phase Map

```text
0   Freeze current Diana
│
├─ 1   Canonical AGENTS.md / identity
│
├─ 1B  Canonical memory
│
├─ 1C  Advisory governance tiers
│
├─ 1D  Local diana-gate
│
├─ 1E  Portable Claude safety hook
│
├─ 1F  Non-blocking CI gate
│
├─ 1G  Required CI + branch protection
│
├─ 2   Preflight v0
│
├─ 3   Playwright prototype
│
├─ 4   AO read-only
│
├─ 5   Claude worker execution-safety certification
│
├─ 6   AO safe-write pilot
│
├─ 7   Stable/pinned AO adapter + gh PR/CI
│
├─ 8   Grow preflight toward 37 checks
│
├─ 9   diana ship — single worker
│
├─ 10  Independent reviewer
│
└─ 11  Multi-worker execution


PARALLEL / OPTIONAL
├─ OmniRoute user setup + Ultrafast test
├─ Context7 if needed
├─ Firecrawl if needed
└─ Perplexity if needed


BLOCKED / LATER
├─ AO Codex write
├─ diana learn
└─ Headroom
```

---

# 22. Current Immediate Next Step

**Do not install AO yet.**

**Do not install Playwright yet.**

**Do not configure OmniRoute as part of Diana.**

**Do not install Headroom.**

**Do not enable branch protection yet.**

The next implementation should only cover the provider-neutral foundation + local/advisory gate prototype.

Immediate scope:

1. Create canonical `AGENTS.md`
2. Make `CLAUDE.md` reference it
3. Declare canonical Diana memory ownership
4. Create advisory `risk-tiers.md`
5. Implement local `diana-gate`
6. Add deterministic gate fixtures/tests
7. Prepare/migrate portable Claude safety-hook registration if confirmed safe
8. Update installer/verify/uninstall lifecycle
9. Ask whether the gate is reliable enough for **non-blocking CI**
10. STOP before the next phase

Only after the local gate is trustworthy:

```text
non-blocking CI
→ required CI
→ branch protection
→ preflight
→ Playwright
→ AO
```

---

# 23. Rules for Future Architecture Decisions

When continuing this project, apply these rules:

1. **Reuse before build.**
2. Every new subsystem must solve a demonstrated gap.
3. Do not add an abstraction with only one implementation unless it already creates concrete leverage.
4. Exactly one owner per responsibility.
5. No duplicated memory.
6. No duplicated browser engine.
7. No duplicated model router.
8. No duplicated compression layer.
9. Provider credentials remain user-owned and outside Diana.
10. Autonomy is added only after the enforcement boundary beneath it is proven.
11. Never confuse “cannot merge” with “cannot cause damage while running.”
12. Experimental integrations must be pinned and wrapped behind one adapter.
13. Optional infrastructure must remain optional; basic Diana must keep working without it.
14. A phase is complete only when its exit criteria pass.
15. If a phase fails, stop and fix it rather than stacking the next dependency on top.

---

# 24. Session Restart Prompt

For a new Claude Code / Codex / ChatGPT session:

```text
Read MEMORY.md completely before making architecture changes.

This file contains the current agreed Diana architecture and master roadmap.

Do not restart the architecture discussion from zero.

Preserve these fixed decisions unless new source evidence disproves them:

- Diana = policy/workflow/memory/governance/quality
- AO = execution/worktree/session backend
- Playwright = browser automation
- gh = PR/CI state
- OmniRoute = optional user infrastructure outside Diana
- Headroom = not active
- no custom ModelRouter
- no Diana Cloud
- no shared owner API credentials
- AO Codex autonomous writes remain blocked until execution safety is solved
- execution safety + merge gate are both required
- current immediate phase is the provider-neutral foundation + local diana-gate

Before implementing anything, inspect the current repository and compare it against the phase state recorded in MEMORY.md.

Do not redo completed phases.

Tell me:
1. current phase
2. which exit criteria already pass
3. what remains
4. exact files you propose to modify

Then wait for approval.
```

---

# 25. One-Line North Star

> **Diana is a thin, provider-neutral policy and workflow brain that defines how work should be done and what counts as done; mature external infrastructure executes the work, while deterministic gates and human approval keep autonomy bounded.**
