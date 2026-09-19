# M7 release audit and ERRATA-002 follow-up

The development checkout was recovered at `709b10e` on
`feature/hermes-product-ux`, with the two Claude-era production fixes and audit
harness still uncommitted. Frozen M7 is `aa25668`; ERRATA-001 is `249501f`.
ERRATA-002 was committed separately as `6c42fc9` before production changes.
Hashes of the three modified modules and untracked audit harness were identical
before and after that documentation-only commit.

The old regression loop (PID 157205, PPID 140338, PGID/SID 157205) completed all
26 Diana suites and exited. It was neither killed nor duplicated. Its results
predate ERRATA-002 implementation and are not final release evidence.

## Findings and corrections

| Finding | Classification | Correction and falsifying evidence |
|---|---|---|
| M7-A1: `lstrip("./")` loses the leading dot in protected paths | Implementation defect, recovered fix | Preserve dot-prefixed names; test `.git`, `.github`, prefixed/nested forms, and an allowed source path. Execute the historical catalogue to show the old failure. |
| M7-A2: replayed approval resets an existing ARMED journal | Implementation defect, recovered fix | Refuse an existing run before creation. Compare all run artifact bytes across refusal; execute the historical approval to show ARMED becoming APPROVED. |
| M7-E2: budget omitted from proposal identity | Frozen-spec contradiction, resolved in `6c42fc9`, then implementation fix | Bind normalized real M5 policy, use the single re-derived authority for creation, and verify durable policy/plan/topology before execution. |
| A4 mutations accumulate | Harness defect | Every attack builds a new pristine proposal with its own run ID. Independent attempts and runtime mutations require exact stale refusal and absence of the run directory. |
| A14 refusal expectation | Recovered harness defect | Existing-run refusal precedes freshness in the preserved fix; assert `proposal-already-approved` and unchanged authority. |
| A15 matches commentary | Recovered harness defect | Exclude comments/docstrings from the legacy-surface source scan. |
| A15 state-write scan searches punctuation removed by token joining | Newly confirmed harness defect | Inspect AST call nodes; an injected four-call source must be detected, and comments/docstrings must not be. |
| Acceptance's budget-widening falsifier accepts any non-BLOCKED state | Newly confirmed harness defect | Require the real attempt-budget-exhausted reason and test the widening decision on that reason. M6 records exhaustion as FAILED; a FAILED fallback alone says nothing about the decision. |

The first focused-test draft also incorrectly expected `.claude` to be a member
of the existing forbidden-prefix catalogue. It is not. Those two erroneous
harness expectations were replaced with actual `.github`/`.git` protected
entries; production policy was not expanded to satisfy the draft.

## Budget authority and independent tests

`proposal.policy_authority` validates a real `runpolicy.build` document and
normalizes it to `policy_version`, `run_id`, `max_attempts`,
`quiescence_grace_seconds`, and `total_seconds` (the exact difference between
`deadline_at` and `created_at`). The latter is the existing M5 approval parameter,
not a second budget mechanism. Version and grace remain fixed runtime values.
Per-command timeout stays covered by the contract.

`test-m7-budget.sh` independently increases and decreases each of attempts and
runtime. Each attack proves that reverting only its chosen field restores the
entire serialized proposal, receives `proposal-stale`, and creates no run
artifacts. Restoring the pristine proposal then succeeds. A separately created
fixture executes the actual `709b10e` implementation and proves it accepts the
same isolated mutation. No earlier mutation can cause these refusals.

Additional checks delay actual creation, verify the unchanged duration and
journal policy digest, substitute persisted attempts/duration/grace/version,
reject invalid input types without coercion, show limits in the approval view,
and preserve the journal and protected-path fixes. Post-creation mismatch is
reported with its run directory and cannot return an executable approval.

Pre-release targeted verification: acceptance 154 passed / 0 failed with 12
falsifiers; adversarial audit 59 held / 0 through (including two scanner
counterexamples); focused budget/regression suite 64 passed / 0 failed with 15
falsifiers. Final full-regression evidence must name the committed implementation
HEAD and be collected after this document and its tests are committed.

## Vacuity review

Run the existing read-only `diana/multiactor/vacuity_audit.py` scanner over the
embedded Python of acceptance, adversarial, and focused suites. For the
adversarial suite, add `attack` to its in-memory `ASSERT_FUNCS` set so the tool
actually inspects that wrapper. The scanner's production file is unchanged.

- Acceptance: three reported findings, each already labelled `[static pin]`:
  refusal vocabulary disjointness, rejection of an unknown refusal code, and
  the closed workflow set. The unknown-code check really catches a runtime
  ValueError; the simple scanner does not track exception-branch assignments.
  None is claimed as an end-to-end authority proof.
- Adversarial: zero scanner findings. Manual review separately identifies and
  labels three structural/source pins: no second contract builder, no legacy
  slash/ship/AO calls, and no direct journal/contract writes. A10's real contract
  equality and product runtime artifact checks supply behavioral counterparts;
  the state-write scanner itself now has positive and negative counterexamples.
- Focused: zero scanner findings. Historical implementation calls are executed,
  not replaced with boolean stand-ins. Run-directory absence is paired with
  restoration/approval on the same fixture, so an always-refusing implementation
  cannot pass. Each mutation has an independent baseline.

No unresolved vacuity finding remains in the reviewed checks. Static scans are
structural evidence, not proofs against all future code shapes or adversaries.
This is a local review; no independent external reviewer is implied.

## Release boundaries

M7 remains additive outside documentation and the established `.gitignore`
manifest exception. Compare all pre-existing non-doc files to `c77208a`, not
just a sample: the replacement set must be empty. This includes every slash
command, `install.sh`, `ship.py`, `ao.py`, all M1–M6 production modules and CI
adapters. All frozen specs/errata remain byte-identical to their freeze commits.

The invocation remains `./diana-do "<goal>"`, then
`./diana-do approve <proposal-digest>`. Approval identities produced before
ERRATA-002 must be re-proposed; they cannot authorize new runs under the new
binding. Existing runs retain M5/M6 authority and are never reset by approval.

M5-D20 remains undischarged. No merge, deployment, publication or certification
permission is added. Existing limitations remain: finite deterministic intent
vocabulary and catalogue; exclusions reduce write roots; advisory review has no
M7 bounded-run product path; the CLI is not installed into consumers by the
unchanged installer; AO evidence on this host is fixture-based; provider counts
can vary; same-user journal forgery, cooperative leases and reconciliation's
detection-only boundary are inherited rather than solved here.
