# DIANA POST-M7 ERRATA-002 — accepted replacement-set reconciliation

Status: **Normative post-merge accounting reconciliation**

This erratum extends
[`DIANA-POST-M7-ERRATA-001.md`](DIANA-POST-M7-ERRATA-001.md) to reconcile the
complete set of **already accepted** post-M7 changes visible from the M4, M5,
M6 and M7 accounting bases, including the merged current-Hermes repair in PR
#72.

It changes regression accounting only. It does **not** change runtime authority,
Hermes capability/confinement enforcement, reviewer projection, write-scope
policy, approval semantics, security policy, or any fail-closed path.

## 1. Why ERRATA-002 exists

ERRATA-001 captured the accepted replacement set at one point in time. Later
accepted work replaced additional files that predate one or more milestone
bases. The original draft of ERRATA-002 accounted only for PR #72 and therefore
remained incomplete: the already-merged security-evidence work and its bounded
harness/documentation edits were still visible to the milestone diffs and were
being misclassified as unexplained milestone replacements.

The maintained statement is therefore based on the actual accepted repository
state, not on one pull request in isolation.

## 2. POST-M7-E2-D1 — exact accepted production replacement set

The complete accepted post-M7 production replacement set maintained by this
erratum is exactly:

- `diana/adapters/hermes.py`
- `diana/adapters/hermes_live.py`
- `diana/adapters/hermes_patches.py`
- `diana/ci/build-gate-input.py`
- `diana/ci/run-security-gate.py`
- `diana/ci/write-summary.py`
- `diana/gate/diana-gate.py`
- `diana/multiactor/actors.py`
- `diana/multiactor/executors.py`
- `diana/mutation/remediation_driver.py`
- `diana/security/adapters/semgrep_adapter.py`
- `diana/security/ci_verifier_runs.py`
- `diana/security/coverage_matrix.py`
- `diana/security/verifiers/semgrep-rules.yml`

No wildcard, directory-prefix or subset rule is introduced.

Every maintained milestone assertion remains exact set equality:

```
milestone-declared replacements
∪ accepted post-M7 replacements that existed at that milestone base
```

A modified pre-existing production file outside those sets still fails.

## 3. PR #72 identity repair

PR #72, merge commit
`4ac0a470d6c80eb3669ea31e4f7594ec5717bf22`, accounts for the later accepted
changes to:

- `diana/adapters/hermes.py`
- `diana/adapters/hermes_patches.py`

and adds `diana/adapters/hermes_runtime.py` plus
`diana/adapters/test-diana-do-runtime-failclosed.sh`.

Those latter files are additions at the milestone bases, not replacements, so
they are deliberately absent from POST-M7-E2-D1.

The exact newly certified Hermes identity accepted by that merged repair remains:

`e1fdf003a668f97bf5a53d7675c1e70b1dcfec34`

ERRATA-002 records the already-accepted change. It does not certify any new
Hermes identity.

## 4. Bounded accepted harness corrections

The complete accepted post-M7 shell-harness correction set is explicitly
enumerated:

- `diana/adapters/test-hermes-capability.sh`
- `diana/adapters/test-hermes-confinement.sh`
- `diana/adapters/test-hermes-preflight.sh`
- `diana/advisory/test-acceptance.sh`
- `diana/advisory/test-m2-live-turn.sh`
- `diana/advisory/test-run.sh`
- `diana/multiactor/test-m6-audit.sh`
- `diana/multiactor/test-m6-lease.sh`
- `diana/multiactor/test-m6-multiactor.sh`
- `diana/multiactor/test-m6-review.sh`
- `diana/multiactor/test-m6-thirdpass.sh`
- `diana/mutation/test-m4-bounded-mutation.sh`
- `diana/product/test-m7-product.sh`
- `diana/security/adapters/test-adapters.sh`
- `diana/security/test-ci-verifier-runs.sh`
- `diana/security/test-coverage-matrix.sh`
- `diana/security/test-security-gate.sh`

The milestone checks continue to ask git whether each path existed at the
relevant base. A later-added harness therefore does not become a historical
replacement merely because it appears in this closed set.

No blanket `test-*.sh` exclusion is introduced.

## 5. Bounded accepted documentation classification

The following accepted modified files are documentation rather than production
code and are explicitly classified as such:

- `MEMORY.md`
- `diana/security/README.md`
- `diana/security/adapters/README.md`

This extends the existing explicit documentation treatment of `.gitignore`,
root `README.md`, and files under `docs/`.

It is an enumerated classification, not a rule that every future README-like
path is automatically exempt from production accounting.

## 6. M5 frozen-suite status

`diana/unattended/test-m5-unattended.sh` remains byte-identical under
M6-E1-AC-3. Its historical local `M5_PRODUCTION` constant is not edited.

Therefore:

- the frozen M5 suite may continue to report its known historical
  replacement-set failure;
- the maintained M5 replacement statement is the exact repo-wide assertion in
  `diana/ci/test-post-m7-replacement-set.sh`;
- no M5 substantive proof is weakened to manufacture a green result.

## 7. Acceptance conditions

ERRATA-002 is satisfied only if all of the following hold:

1. M4, M6 and M7 accept only the enumerated production replacements that
   actually existed at their respective bases.
2. `diana/ci/test-post-m7-replacement-set.sh` passes exact replacement and
   harness equality for M4, M5, M6 and M7.
3. The bounded documentation classification above prevents documentation-only
   changes from being misreported as production replacements.
4. Added files remain additions and are not rewritten as historical
   replacements.
5. No runtime authority or security policy is changed by this reconciliation.
6. The frozen M5 test remains byte-identical.

## 8. Production-hardening PR #74

PR #74 is **not** pre-registered here because it is still unmerged.

Its production-facing files `diana-do`, `diana/product/proposal.py`,
`diana/product/release.py`, and `diana/product/runtime_install.py` are all
**additions relative to each M4/M5/M7 accounting base** where relevant; the
current accounting mechanism must continue to determine that from git rather
than from a speculative pre-merge exception.

If a future accepted change replaces a pre-existing file at one of the frozen
bases, that accepted change must be reconciled only after merge, with a new
bounded accounting decision.

## 9. Non-goals

This erratum does not claim:

- macOS support;
- Windows support;
- general production readiness;
- certification of unsupported Hermes identities;
- permission to weaken exact set-equality accounting;
- acceptance of unmerged PR #74 work.

It reconciles only already-accepted post-M7 repository history.
