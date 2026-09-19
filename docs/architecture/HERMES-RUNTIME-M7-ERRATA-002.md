# HERMES-RUNTIME-M7 — ERRATA 002

Status: **normative erratum** to [M7](HERMES-RUNTIME-M7.md) and
[ERRATA-001](HERMES-RUNTIME-M7-ERRATA-001.md).
Scope: narrowly repair the omitted run-budget binding. Neither frozen file is
edited. M1–M6 production files and specifications remain unchanged.

## Evidence and contradiction

M7-D15 says approval covers the run policy's attempt cap and deadline.
M7-E1-D1 lists contract authority, work items and actor topology but omits the
run policy. On `709b10e` with the surviving audit fixes, changing only the
stored budget from `max_attempts=6, total_seconds=3600` to
`max_attempts=999, total_seconds=999999` left the proposal digest unchanged.
Approval succeeded and persisted the enlarged limits. This is authority
widening, not display drift. A4 concealed it by accumulating earlier mutations.

Before implementation, inspection and isolated calls to the real M5/M6 builders
established:

- `runpolicy.POLICY_KEYS` is exactly `policy_version`, `run_id`, `created_at`,
  `max_attempts`, `deadline_at`, `quiescence_grace_seconds`.
- `actors.approve(**kwargs)` calls `unattended.approve`, which accepts
  `max_attempts` and `total_seconds` and calls `runpolicy.build` once to create
  the durable policy. It exposes no policy `created_at`, absolute deadline or
  grace override. Policy version is 1; the builder's fixed grace is 20 seconds.
- A real `actors.approve` through the product API with 7 attempts and 1,234
  seconds persisted `max_attempts=7`, grace 20, and timestamps
  `2026-09-19T10:28:42Z` / `2026-09-19T10:49:16Z`: exactly 1,234 seconds apart.
- Calling `runpolicy.build` with 3,600 seconds and creation times 10:00 and
  11:00 UTC produced deadlines 11:00 and 12:00 respectively. The duration is
  invariant; predicting an absolute approval timestamp would be incorrect.
- M5 checks `max_attempts` and `deadline_at` when opening attempts, uses grace
  for quiescence, and re-verifies the complete policy digest on resume. Resume
  does not restart the duration. Per-command timeout is already part of the
  contract's `command_policy` and remains covered by contract authority.

## Normative correction

**M7-E2-D1 — One canonical proposal identity includes normalized run policy.**
Extend M7-E1-D1's single canonical digest with a `run_policy` object derived
from the real `runpolicy.build`, never a parallel policy implementation:

- `policy_version`, `run_id`, `max_attempts`, `quiescence_grace_seconds`, copied
  from the validated policy;
- `total_seconds`, the existing approval parameter, recovered exactly as
  `deadline_at - created_at` in integer seconds.

The other digest components remain every contract field except contract
`created_at`, the work-item digest, and the actor-topology digest. These cover
workflow, derived risk/depth, target/repository binding, tools, commands and
command timeout, effective write roots/denied paths, read scope and the plan.
Exclusions remain authority through the effective contract scope; explanatory
prose and `withheld_by_exclusion` are not independent grants. Validation against
the live target precedes authority creation. No display prose becomes authority.

Budget inputs use the exact existing names `max_attempts` and `total_seconds`:
positive integers, excluding booleans, with no silent coercion or unknown budget
keys. Policy version and grace are derived fixed values, not new user knobs.
Changing any normalized policy dimension changes the canonical identity.
Contract `created_at` remains excluded as provenance. Policy `created_at` is
NOT claimed to be non-decisional: it anchors the deadline. The proposal binds
its duration relationship instead of a wall-clock value that does not yet exist.

**M7-E2-D2 — Duration becomes a deadline exactly once at run creation.**
The approved proposal binds `total_seconds`; approval re-derives the same
normalized policy from the immutable proposal inputs and the live target.
After an exact digest match, the existing M5 creation path computes
`deadline_at = created_at + approved total_seconds` once for the durable run.
Both persisted timestamps stay authoritative thereafter. No caller can
substitute a longer duration, greater attempt cap or grace. Delaying approval
may shift both timestamps, but cannot change their approved difference.

**M7-E2-D3 — Approval consumes only re-derived authority.**
Approval accepts only the proposal digest as its authority argument; storage
locations remain non-authority configuration. It loads the proposal, preserves
the existing-run refusal, revalidates intent and budget, rebuilds against the
live target, recomputes the complete canonical digest, and requires equality.
It creates the run from that single re-derived set of contract inputs, work
items and normalized budget, not independent caller budget values or a second
read of the stored proposal. A changed valid attempt/time limit (including a
reduction) yields exactly `proposal-stale` and zero run-authority effects:
no run directory, contract, policy, journal or execution. Old identities that
omit policy cannot authorize a new run under this correction; re-propose.
Re-approval of an existing run remains `proposal-already-approved` and cannot
reset the journal, including ARMED or terminal states.

**M7-E2-D4 — Verify the resulting authority before execution.**
Retain M7-E1-D4's contract comparison excluding only contract `created_at`.
Also compare the persisted policy's normalized authority, work-item identity
and actor-topology identity with the re-derived approved objects. Verify policy
through the existing authority readers/digests. A post-creation discrepancy
aborts with `contract-prediction-failed`, names the durable run directory, and
never invokes execution. This is distinct from pre-creation stale refusal.

**M7-E2-D5 — Display the actual bounded duration and attempt cap.**
The approval view renders the canonical proposal's attempt and time limits and
fixed quiescence grace. Its words do not supply authority. It must not label a
predicted wall-clock deadline as already approved.

## Required falsification and release evidence

1. Each A4 mutation begins with an independent pristine proposal. No refusal
   may depend on an earlier attack.
2. Independently increase attempts and duration, holding every other field
   byte-identical. The old digest calculation stays equal (falsifier); the new
   calculation differs; approval returns `proposal-stale` with no run effects.
3. Decrease attempts, prove refusal, restore the exact original value and prove
   the original identity approves. This distinguishes binding from an always
   refusing or monotonic-only check. Test time reductions independently too.
4. Prove delayed creation preserves the approved duration, and persisted policy
   fields and runtime policy digest agree. Perturb each normalized policy
   dimension and demonstrate detection, including post-creation substitution.
5. Preserve independent re-approval/journal and dot-prefixed path regressions,
   exclusions, proposal freshness and free-text refusal tests. Review vacuity
   explicitly; static pins are labelled, not claimed as behavioral proofs.
6. Final M7 and M1–M6, all 26 Diana suites, and legacy AO/slash evidence must
   come from the corrected committed HEAD. Pre-fix results are historical only.

The permitted replacement set remains empty outside docs and the established
`.gitignore` manifest exception. M7 stays additive. This grants no new workflow,
capability, executor authority, merge authority or certification. M5-D20 remains
undischarged. No other frozen M7 decision is changed.
