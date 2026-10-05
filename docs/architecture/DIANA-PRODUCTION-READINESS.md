# Diana Production-Readiness Contract

Status: **Normative acceptance contract for the Linux governed-runtime release track.**

This document defines what must be true before Diana may be described as production-ready for the supported environment. It does not itself make that claim.

## Scope

Supported target for this track:
- OS: Linux only.
- Governed runtime: `diana-do`.
- Execution dependency: an exact Hermes identity already certified in Diana.
- Portable Layer 2 `install.sh`: remains a separate product surface and must not be silently redefined by runtime packaging.

Explicitly out of scope:
- macOS support;
- Windows support;
- deployment authority;
- broad/ranged/"latest" Hermes compatibility;
- weakening any frozen proof, human-approval requirement, or fail-closed boundary.

## Acceptance criteria

### A. Fresh installation — PR-A

A production runtime install MUST:
1. install only into a deterministic Diana-managed prefix;
2. create a launcher that resolves only that installed runtime;
3. publish a manifest containing Diana version, source commit, support matrix and certified Hermes identities;
4. verify every required installed file before reporting success;
5. fail nonzero if installation is partial or inconsistent;
6. never overwrite unrelated files without refusal.

Evidence: fresh install test in an empty prefix plus corruption and collision falsifiers.

### B. Existing installation upgrade — PR-B

An upgrade MUST:
1. identify current and incoming Diana versions;
2. stage new Diana-owned files before activation;
3. refuse an ambiguous or unverifiable source;
4. preserve run/proposal state unless an explicit migration exists;
5. never reinterpret an old approval under wider policy;
6. leave either the previous verified runtime or the new verified runtime active, never a mixed tree.

Evidence: N→N+1, interrupted-stage and incompatible-source tests.

### C. Rollback — PR-C

Rollback MUST:
1. target only the immediately previous verified Diana-owned runtime;
2. never modify target-project user files;
3. restore the previous release manifest and launcher target;
4. refuse when no verified rollback snapshot exists.

Evidence: upgrade then rollback with byte/manifest verification and no-unmanaged-file-change falsifier.

### D. Supported Hermes identity — PR-D

Diana MUST:
1. accept only exact entries in `CERTIFIED_IDENTITIES`;
2. reject unknown, unreachable, SHA-less and mismatched identities;
3. report the active identity without broadening matching semantics;
4. include the certified set in release metadata.

Evidence: existing identity tests plus release/doctor consistency test.

### E. Hermes runtime discovery — PR-E

Runtime discovery MUST:
1. use Hermes's supported PM resolver;
2. never hardcode per-machine install/environment hashes;
3. never fall back to ambient Python when Hermes runtime resolution fails;
4. report the resolved interpreter path diagnostically without exposing secrets.

Evidence: PM-managed runtime success and fail-closed missing/broken-runtime tests.

### F. Real governed execution — PR-F

At least one release-certification run MUST execute the real governed path:
proposal → exact approval → Builder → Diana verification → Reviewer → terminal result.

A deterministic/mock-only run is insufficient certification evidence.

Evidence: persisted run ID, journal/report evidence, exact Diana commit and Hermes identity.

### G. Builder execution — PR-G

The Builder MUST:
1. receive only its approved projection;
2. remain inside write/read/command scope;
3. have every dispatch adjudicated at Diana's proven boundary;
4. be blocked on envelope escape.

Evidence: live authorized operation plus write-scope, tool and command falsifiers.

### H. Reviewer execution — PR-H

The Reviewer MUST:
1. run under the read-only reviewer projection;
2. receive Diana-owned evidence for the exact attempt under review;
3. be unable to mutate repository state;
4. yield rejection when verdict transport is absent, malformed or ambiguous.

Evidence: production-reviewer suite plus live reviewer run and mutation falsifier.

### I. Write-scope confinement — PR-I

Any write outside approved write scope, including traversal or symlink-mediated escape, MUST be refused before the operation succeeds.

Evidence: M4/M6/M7 falsifiers plus release security regression suite.

### J. Capability enforcement — PR-J

A tool not in the approved capability set MUST be unreachable through every supported dispatch path.

Evidence: confinement/capability/inline-executor behavioral tests against exact Hermes identity.

### K. Forbidden tool refusal — PR-K

Unknown, newly introduced or explicitly forbidden Hermes tools MUST default to refused.

Evidence: unknown-name and forbidden-tool falsifiers.

### L. Out-of-scope path refusal — PR-L

Reads and writes outside contract scope MUST fail closed. Resolver or path-normalization ambiguity MUST NOT widen scope.

Evidence: traversal, absolute-path, symlink and resolver-failure tests.

### M. Crash/restart recovery — PR-M

For a recoverable interrupted run:
1. durable state written before action remains authoritative;
2. restart MUST inspect journal/repository state rather than trusting Hermes durable state;
3. divergent state MUST reconcile to a blocked/recovery decision, not silent continuation;
4. recovery MUST not widen authority.

Evidence: forced termination at defined execution phases and fresh-process recovery test.

### N. Durable journal integrity — PR-N

The journal MUST:
1. remain crash-atomic;
2. reject truncation/corruption/stale or mismatched state;
3. reside outside Hermes read/write scope;
4. document that its digest is corruption integrity, not authenticity against an attacker with arbitrary run-directory write access.

Evidence: corruption, stale-run and loose-permission falsifiers.

### O. Reconciliation — PR-O

Reconciliation MUST detect state divergence after attempted mutation and MUST never be presented as prevention.

Evidence: changed-path and out-of-envelope divergence tests.

### P. Verification evidence — PR-P

Verification MUST:
1. be executed by Diana, not trusted from Builder/Reviewer prose;
2. persist enough evidence to bind the result to the reviewed attempt;
3. fail closed on missing or indeterminate verification;
4. appear in release certification evidence without secrets.

Evidence: attempt-binding tests and persisted report inspection.

### Q. Human approval boundary — PR-Q

Production-readiness MUST NOT weaken repository human-approval controls.

A security-control change that requires human review remains human-only. An automation account cannot substitute for required independent human approval.

Evidence: existing Track B acceptance plus live repository rule behavior where applicable.

### R. CI / release gate — PR-R

A dedicated production release gate MUST require:
- Diana Gate;
- Diana Security Gate;
- post-M7 accounting reconciliation;
- Hermes confinement/capability/preflight;
- `diana-do` fail-closed tests;
- relevant M4/M5/M6/M7 and autonomy suites;
- fresh governed-runtime install;
- upgrade;
- rollback;
- runtime version/doctor;
- release manifest validation;
- Linux support declaration.

A required BLOCKER test that SKIPs is a release failure. Environmental SKIP is not certification evidence.

### S. Operator diagnostics — PR-S

Without a model call, an operator MUST be able to determine:
- Diana version;
- Diana release/source identity;
- supported OS status;
- active Hermes identity;
- whether that identity is certified;
- resolved Hermes Python;
- runtime/proposal/run storage roots;
- whether the environment is safe to start a governed run.

Diagnostics MUST never print credential values.

Evidence: `version`/`doctor` tests including secret-redaction falsifier.

### T. Linux-only support statement — PR-T

Every production-facing support surface MUST state Linux-only for the governed runtime until a separate portability track proves another platform.

On unsupported platforms, production certification MUST refuse rather than silently degrade a load-bearing ownership/lease proof.

Evidence: release manifest + doctor support matrix + docs consistency check.

## Production-ready decision

A release may receive verdict **READY** only if:
1. PR-A through PR-T are all satisfied with current evidence;
2. no BLOCKER remains;
3. the dedicated release gate passes without a required skip;
4. the exact tested Diana source commit and Hermes identity are recorded;
5. PR #73 accounting reconciliation is accepted on `main`;
6. human-required repository approvals have been satisfied.

Otherwise the required verdict is **NOT READY**.

## Compatibility rule

An old approval is never upgraded into broader authority merely because a newer Diana release can understand it. Any schema/policy migration that could widen interpretation MUST refuse or require a new proposal and approval.

## Evidence rule

Historical green evidence may motivate or explain a change, but a production release claim requires fresh evidence for the release commit and the supported runtime environment.
