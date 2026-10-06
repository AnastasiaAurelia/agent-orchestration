# Diana Production-Readiness Audit

Status: **Phase 1 inventory complete.** This document is an audit, not a production-readiness claim.

Baseline audited: PR #73 head `e8583230b09a395ac178ef369226330b39b14c3d`, which includes the post-merge accounting reconciliation stacked on accepted PR #72.

Hard constraints:
- Linux is the only proven runtime platform.
- macOS and Windows remain unsupported/unproven.
- Hermes identities remain exact and fail-closed.
- No test, frozen proof, human-approval boundary, or gate may be weakened for convenience.
- PR #73 must merge before this hardening track can be accepted.

## Inventory

| Area | Exact path(s) | Current behavior | Production risk | Severity | Proposed fix | Proof required |
|---|---|---|---|---|---|---|
| 1. Packaging / distribution | `diana-do`, `install.sh`, `docs/architecture/DIANA-DISTRIBUTION-ROADMAP.md` | Portable Layer 2 install exists, but governed runtime stays in the checkout and is not installed on PATH. No package metadata or release artifact contract. | Operators cannot install a reproducible governed runtime independently of a source checkout. | BLOCKER | Add a separate governed-runtime installer, deterministic install root, runtime launcher, release manifest and uninstall path without changing Layer 2 semantics. | Fresh install into an empty HOME-like prefix; launcher resolves only installed files; uninstall removes only Diana-managed runtime files. |
| 2. Clean install | `install.sh`, `verify.sh`, runtime tree | Layer 2 has install/verify; governed runtime has no clean-install verifier. | A partially copied or stale runtime can look usable until execution. | BLOCKER | Add runtime install verification including file manifest, version, interpreter/runtime resolution and support matrix. | Deliberately omit/corrupt one installed file and prove verifier refuses. |
| 3. Runtime bootstrap | `diana-do`, `diana/adapters/hermes_runtime.py` | Hermes PM interpreter is resolved fail-closed. Launcher still assumes a source-tree-relative Diana layout. | Runtime cannot safely move to an installed prefix yet. | HIGH | Make launcher locate an explicit Diana runtime root and refuse ambiguous/missing layouts. | Source checkout and installed runtime both select the intended root; malformed root refuses. |
| 4. Hermes compatibility / identity | `diana/adapters/hermes_patches.py`, `diana/adapters/hermes.py` | Exact certified git identities only; unknown/unreachable/mismatch fail closed. | Good security posture, but release metadata does not yet bind which identities a Diana release supports. | HIGH | Emit certified identity set into release manifest and operator diagnostics; never broaden matching semantics. | Version/doctor output exactly matches source constant; unknown and mismatch remain refused. |
| 5. Upgrade | none for governed runtime | No governed-runtime upgrade mechanism. | In-place manual replacement can mix versions or invalidate old approvals/runs. | BLOCKER | Atomic-ish staged upgrade with explicit version compatibility and managed backup. Preserve active run state. | Upgrade N→N+1; interrupted stage; incompatible source; active-run refusal/migration rule. |
| 6. Rollback | none for governed runtime | No governed-runtime rollback mechanism. | Failed upgrade may leave no deterministic recovery path. | BLOCKER | One-version managed rollback of Diana-owned runtime files only. | Upgrade, mutate/corrupt current install, rollback, verify prior manifest and launcher. |
| 7. Configuration | environment seams in `diana/product/product.py`; Hermes home env | Operational settings are mostly env vars; security policy remains code-bound. No documented supported config inventory. | Operators may rely on undocumented env behavior or mistake policy for configuration. | MEDIUM | Publish a closed operator-config inventory and classify each setting as convenience vs authority-bound. | Unknown runtime-installer options refuse; config docs match code. |
| 8. Secrets / credentials handling | supervisor/provider adapters; Security Gate workflow | Security Gate reads no secrets. Runtime can use Hermes/provider credentials indirectly. No unified diagnostic redaction contract. | Doctor/diagnostics could accidentally expose provider secrets if implemented carelessly. | HIGH | Diagnostics report presence/source class only, never values; add redaction tests. | Inject credential-shaped env values and prove diagnostics contain none. |
| 9. Failure / fail-closed behavior | `diana-do`, blocking/refusal modules, Hermes adapters | Critical runtime/identity failures are fail-closed. Packaging/install failures have no governed runtime contract because packaging does not exist. | Distribution layer could become a new fail-open path. | HIGH | Define installer/verifier refusal codes and partial-install marker semantics. | Interrupted install never yields a runnable verified runtime. |
| 10. Crash recovery / restart recovery | `diana/unattended/journal.py`, recovery/ownership modules | Durable journal and recovery semantics exist; M5 evidence is strong. Production packaging/release has not re-certified this from an installed runtime. | Source-tree tests do not prove installed-runtime recovery. | HIGH | Include installed-runtime crash/recovery scenario in release gate. | Kill a live governed run at defined phases and recover from Diana-owned journal. |
| 11. Durable state / journal integrity | `diana/unattended/journal.py` | Journal is crash-atomic and digest-protected against corruption/stale state, not authentication against an attacker who can rewrite the run dir. | Operators may overread the digest as adversarial authenticity. | MEDIUM | Surface the exact integrity guarantee in production contract/runbook; add release-gate corruption tests. | Truncate/tamper/stale journal and prove load/recovery refuses. |
| 12. Builder / Reviewer production path | `diana/multiactor/*`, `diana/product/product.py` | Real Hermes builder/reviewer path exists and has prior validated runs. | A release needs fresh installed-runtime evidence, not only historical branch evidence. | BLOCKER | Release certification must execute real governed Builder/Reviewer under the exact supported Hermes identity. | Reviewer remains read-only; verdict binds reviewed attempt; no-verdict rejects. |
| 13. Verification / evidence persistence | M6 evidence builder, journal/report paths | Verification result is orchestrator-owned and reviewer evidence is tied to attempt. No release artifact summarizes certification evidence. | Release consumers cannot tell what was actually certified. | HIGH | Produce machine-readable release manifest plus human report with exact test evidence and hashes. | Manifest generation deterministic and bound to git commit/version/Hermes identity. |
| 14. Observability / operator diagnostics | product CLI currently has propose/show/status/result only | No `version` or `doctor`; Hermes runtime Python and support state are not easily inspectable. | Operational failures are harder to diagnose and support. | BLOCKER | Add deterministic `version` and `doctor` commands with no model call and no secrets. | Commands work without provider credentials and fail nonzero on unsupported environment. |
| 15. Release versioning | no normative runtime version source | No semantic product version or release schema. Journal has a schema version, but product release identity is absent. | Cannot reason about upgrade compatibility or bind approvals to release policy evolution. | BLOCKER | Add one authoritative Diana runtime version and release-manifest schema. | Runtime reports exact version; release manifest binds commit and policy/runtime versions. |
| 16. Release gates | only `Diana Gate` and `Diana Security Gate` workflows | PR gates exist but there is no strict production-release certification gate. | Green PR checks can be mistaken for product certification. | BLOCKER | Add a dedicated Linux release-gate script/workflow that treats required skips as failure. | Gate fails if Hermes is unavailable, required suite skips, install/upgrade/rollback fails, or manifest mismatch occurs. |
| 17. Supported-platform declaration | README and distribution roadmap | README states Linux-only in practice; macOS/Windows unproven. No machine-readable support declaration. | Packaging could accidentally imply portability. | MEDIUM | Add support matrix to release manifest and doctor. Linux only for this track. | macOS/Windows report unsupported, never “warning but continue” for governed runtime certification. |
| 18. Dependency pinning / reproducibility | exact Hermes identity; pinned GitHub Actions | Hermes is exact; Python floor is only “exercised on 3.11”; Diana has no dependency/package lock because it is stdlib-only. | Reproducibility is incomplete without declared Python and OS requirements. | HIGH | Declare supported Python range/floor after syntax/import audit; bind it into doctor/release manifest. | CI on declared Python version(s); unsupported version refuses certification. |
| 19. Security regression coverage | `diana/security/*`, M1–M7 suites | Strong existing boundary tests and Security Gate. Distribution, upgrade and rollback are new attack surfaces with no adversarial suite yet. | New operational code can bypass otherwise-correct runtime controls. | BLOCKER | Add adversarial tests for install path traversal, symlink overwrite, manifest tampering, upgrade/rollback tampering, env abuse and stale artifact replay. | Every attack is refused before modifying unmanaged paths or running governed work. |
| 20. Documentation / operator runbook | `README.md`, architecture docs | Architecture is detailed, but no production operator runbook covering runtime install, version, doctor, upgrade, rollback, recovery and release evidence. | Operators may use historical/internal procedures instead of a supported flow. | HIGH | Add production operator runbook and update README only after behavior exists. | Docs commands are exercised by release test; unsupported claims remain explicit. |

## Blocking conclusion

Diana is **NOT READY** for a production-ready release claim at this audit point.

The existing governed execution controls are substantially stronger than the distribution surface around them. The blockers are therefore mostly productization/release-certification blockers, not evidence that the core authority model is absent.

The hardening implementation should proceed in this order:

1. normative production acceptance contract;
2. authoritative version + deterministic diagnostics;
3. separate governed-runtime packaging/install verification;
4. bounded upgrade/rollback;
5. dedicated release gate and release manifest;
6. real installed-runtime Hermes certification;
7. operator runbook and final adversarial review.

macOS portability remains explicitly out of scope.


## Hardening follow-up findings

### M7 153/2 root cause

The observed `153 passed / 2 failed` M7 product result was not a new product-path defect introduced by production hardening. The two failures were accounting classification failures inherited from an incomplete post-M7 reconciliation.

The first draft of PR #73 accounted for the current-Hermes repair but omitted already-accepted security-evidence replacements, bounded harness edits, and three non-`docs/` documentation files. Reconstructing the exact modified sets from the frozen M4/M5/M6/M7 bases showed those omissions deterministically.

PR #73 has now been corrected so the reconstructed production and harness sets have **zero extra and zero missing paths for M4, M5, M6 and M7**. The hardening branch is synced with that corrected accounting state. A fresh M7 run is still required as execution evidence, but the `153/2` accounting root cause is identified and corrected rather than hidden.

### Release-manifest trust boundary

The first hardening implementation only proved file ↔ manifest self-consistency. An actor able to rewrite the runtime prefix could therefore rewrite a runtime file and its matching manifest hash together.

The candidate manager now authenticates the complete manifest with HMAC-SHA256 using a 32-byte local trust key stored **outside** the runtime prefix. Verification rejects:
- missing or malformed authentication;
- manifest forgery with recomputed file hashes but stale/unknown authentication;
- a trust key located inside the runtime prefix;
- a symlinked trust key;
- group/other-readable trust-key permissions.

This closes the specific prefix-only forgery gap. It does **not** claim resistance to a same-user compromise that can also read or rewrite the external trust key; that remains outside this distribution-layer guarantee.

### Interrupted upgrade / rollback state

Upgrade and rollback now write a durable `transition.json` marker before changing `current` / `previous` symlinks. A hard crash that leaves this marker makes later verification/upgrade/rollback refuse ambiguous state instead of guessing which release is authoritative.

A non-empty stale `.staging` directory is likewise refused. Catchable activation failures restore the prior verified state where possible; a hard crash remains fail-closed via the transition marker.

### Accounting impact of PR #74

PR #74 must still merge **after** PR #73. However, its production-facing hardening files are:
- `diana-do`
- `diana/product/proposal.py`
- `diana/product/release.py`
- `diana/product/runtime_install.py`

All four are **additions**, not modifications, relative to the frozen M4, M5 and M7 accounting bases (and likewise do not constitute a pre-existing replacement at the relevant earlier bases). Therefore, under the current replacement-set accounting model, PR #74 does **not** require a speculative new post-merge replacement erratum merely because it contains production code.

If future accepted work replaces a file that already existed at a frozen milestone base, that later accepted replacement must be reconciled after merge. No such exception is pre-registered for unmerged #74.

