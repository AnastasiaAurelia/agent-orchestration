# DIANA POST-M7 ERRATA-002 — PR #72 replacement-set reconciliation

Status: **Normative post-merge accounting reconciliation**

This erratum extends
[`DIANA-POST-M7-ERRATA-001.md`](DIANA-POST-M7-ERRATA-001.md) after the
accepted Hermes PM-runtime compatibility repair was merged to `main` by
**PR #72**, merge commit
`4ac0a470d6c80eb3669ea31e4f7594ec5717bf22`.

It changes regression accounting only. It does **not** change runtime
authority, Hermes capability/confinement enforcement, reviewer projection,
write-scope policy, approval semantics, or any fail-closed path.

## 1. Why ERRATA-002 exists

ERRATA-001 froze the then-current accepted post-M7 replacement set after
PRs #65/#66. PR #72 later replaced two additional files that predate the M4,
M5, M6 and M7 accounting bases:

| Accepted change | Merge | Pre-existing production files replaced |
|---|---|---|
| Current PM-managed Hermes runtime compatibility | `#72` / `4ac0a470` | `diana/adapters/hermes.py`, `diana/adapters/hermes_patches.py` |

PR #72 also added `diana/adapters/hermes_runtime.py` and
`diana/adapters/test-diana-do-runtime-failclosed.sh`. Those are additions,
not replacements, so they are deliberately absent from the replacement set.

## 2. POST-M7-E2-D1 — exact extension of the replacement set

The maintained accepted post-M7 production replacement set is the ERRATA-001
set plus exactly these two files:

- `diana/adapters/hermes.py`
- `diana/adapters/hermes_patches.py`

No wildcard, directory prefix, or subset rule is introduced. Every corrected
milestone assertion remains **set equality**:

```
milestone declared replacements
∪ accepted post-M7 replacements that existed at that milestone base
```

A modified pre-existing production file outside those sets still fails.

## 3. Why the two files are accounted

### `diana/adapters/hermes.py`

PR #72 replaced the old version/commit pin consumption with Diana's
fail-closed Hermes identity result. An unverifiable or mismatched identity is
blocked; no broader identity is admitted.

### `diana/adapters/hermes_patches.py`

PR #72 replaced the obsolete static `__version__` plus unconditional
`git rev-parse` identity derivation with Hermes's supported
`get_code_identity()` path and an exact certified-identity set. Unknown,
unreachable, or SHA-less identities remain unverifiable and therefore cannot
pass certification.

The newly certified current Hermes identity is exact:

`e1fdf003a668f97bf5a53d7675c1e70b1dcfec34`

This accounting erratum records that the already-merged security-boundary
change exists. It does not itself certify a new identity.

## 4. Harness accounting

PR #72 also changed the already-enumerated post-M7 milestone harnesses:

- `diana/mutation/test-m4-bounded-mutation.sh`
- `diana/multiactor/test-m6-multiactor.sh`
- `diana/product/test-m7-product.sh`

Those files were already explicitly classified as bounded test-harness
corrections by ERRATA-001. ERRATA-002 does not add a wildcard harness
exclusion.

The repository-wide maintained assertion remains
`diana/ci/test-post-m7-replacement-set.sh`, which re-asserts the M4, M5, M6
and M7 replacement statements against the current accepted repository state.

## 5. M5 frozen-suite status

`diana/unattended/test-m5-unattended.sh` remains byte-identical under
M6-E1-AC-3. Its historical local `M5_PRODUCTION` constant is not edited.

Therefore the policy from ERRATA-001 remains:

- the frozen M5 suite may continue to report its known historical
  replacement-set failure;
- the maintained M5 replacement statement is the exact repo-wide assertion in
  `diana/ci/test-post-m7-replacement-set.sh`;
- no M5 substantive authority or recovery proof is weakened to obtain a green
  accounting result.

## 6. Acceptance conditions

ERRATA-002 is satisfied only if all of the following hold:

1. M4, M6 and M7 milestone replacement checks accept the two #72 replacements
   only where each file existed at the corresponding milestone base.
2. `diana/ci/test-post-m7-replacement-set.sh` passes by exact set equality for
   M4, M5, M6 and M7.
3. `diana/adapters/hermes_runtime.py` is treated as an addition, never a
   replacement.
4. No runtime authority or security policy is changed by this reconciliation.
5. The frozen M5 test remains byte-identical.

## 7. Non-goals

This erratum does not claim:

- macOS support;
- Windows support;
- general production readiness;
- certification of any Hermes identity beyond the identities already accepted
  by the merged runtime repair;
- permission to weaken future replacement-set assertions.

It reconciles only the post-merge accounting created by accepted PR #72.
