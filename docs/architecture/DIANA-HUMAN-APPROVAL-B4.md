# Track B · Phase B4 — Mechanical Human Approval Enforcement

> **STATUS: ENFORCEMENT IS ENABLED. CUSTODY FAILED, THEN WAS RESTORED BY PROVIDER-SIDE REVOCATION.**
>
> The human applied exactly the three intended fields and nothing else (§8). The mechanism is
> configured.
>
> The B2-F8 exposure recurred before post-change verification (§10) and was then **closed at the
> provider** rather than by environment isolation (§16). Custody is re-established and re-falsified.
>
> **B4's evidence remains CONFIGURATION ENABLED, not SECURITY PROPERTY PROVEN.**
> **M5-D20 remains UNDISCHARGED** until B5.

Base: B3 (`982f081`). B0, B1, B2 and B3 are **not** edited.

---

## 1. Purpose, and what B4 is not

B4 changes the live `main` ruleset so that a pull request requires a **human code-owner approval**
before it can merge. It is the first phase in Track B that changes anything enforceable.

> **B4 enables the mechanism. It does not prove the mechanism works.**
>
> B4's evidence is **CONFIGURATION ENABLED**. It is *not* **SECURITY PROPERTY PROVEN**. The
> difference is load-bearing and this document will not blur it: a configuration that looks correct
> in the API response is not the same as a configuration observed to refuse an unapproved merge.
> **B5 supplies the second claim; B4 cannot.**

**M5-D20 is not discharged by B4.** It is discharged, if at all, only after B5 adversarially
demonstrates the properties B1 froze.

---

## 2. B4.0 — custody revalidation

Re-run in the live agent process. **Not inherited from B2 or B3**, because a ruleset change made
while automation could act as the owner would be governance theatre.

| positive | observed |
|---|---|
| `GH_TOKEN` | present (never printed) |
| `gh api user` | **`DIANA-AGENT`** |
| `X-OAuth-Scopes` | **`public_repo`** only |
| token expiry | 2026-10-20 09:14:30 UTC |
| repo-local git author | `DIANA-AGENT <324038564+DIANA-AGENT@users.noreply.github.com>` |

| negative | observed |
|---|---|
| `GIT_ASKPASS`, `SSH_ASKPASS` | absent |
| `VSCODE_GIT_IPC_HANDLE`, `VSCODE_GIT_ASKPASS_{NODE,MAIN,EXTRA_ARGS}` | absent |
| `GITHUB_TOKEN` | absent |

**Falsification with `GH_TOKEN` removed** — all three required to fail, and all three did:
`git credential fill` returned no credential; `git push` could not authenticate and created no
branch; `gh api user` could not authenticate.

**Custody holds at the B4 observation point.** The standing condition of B2-D7 is unchanged.

---

## 3. B4.1 — exact pre-B4 live baseline

Read live as `DIANA-AGENT` immediately before the human checkpoint. **Eighteen fields compared;
no drift.**

| field | value |
|---|---|
| ruleset id / name | `22188373` / `diana-main-protection` |
| enforcement | `active` |
| target | `branch`, `include: ["refs/heads/main"]`, `exclude: []` |
| rules present | `deletion`, `non_fast_forward`, `pull_request`, `required_status_checks` |
| **`required_approving_review_count`** | **`0`** |
| **`require_code_owner_review`** | **`false`** |
| **`require_last_push_approval`** | **`false`** |
| `dismiss_stale_reviews_on_push` | `true` |
| `required_review_thread_resolution` | `false` |
| `require_extra_approval_for_unattributed_changes` | `false` |
| `required_reviewers` | `[]` |
| `allowed_merge_methods` | `["merge", "squash", "rebase"]` |
| `strict_required_status_checks_policy` | `true` |
| required checks | `Diana Gate`, `Diana Security Gate` |
| `current_user_can_bypass` (as `DIANA-AGENT`) | **`never`** |
| `updated_at` | `2026-09-14T02:55:45.216+07:00` |
| rulesets on repository | exactly one — `[22188373]` |
| classic branch protection | absent (`404 Not Found`) |

`bypass_actors` is **not present in the response the automation identity receives** (B3-F1). Its
absence from the payload is a visibility limit, **not** evidence that the field is unset — §9 keeps
that distinction.

---

## 4. Exact intended change — three fields, nothing else

```
required_approving_review_count   0     ->  1
require_code_owner_review         false ->  true
require_last_push_approval        false ->  true
```

**Everything else must be preserved byte-for-byte in meaning:**

`dismiss_stale_reviews_on_push = true` · `bypass_actors = []` · `enforcement = active` ·
target `refs/heads/main` · `Diana Gate` and `Diana Security Gate` required ·
`strict_required_status_checks_policy = true` · `deletion` rule · `non_fast_forward` rule ·
`allowed_merge_methods = [merge, squash, rebase]` · `required_review_thread_resolution = false` ·
`require_extra_approval_for_unattributed_changes = false` · `required_reviewers = []`.

The change set is **not** to be broadened. `required_review_thread_resolution` stays `false`
(B1-D11): it is review hygiene, not authority, and must not be counted toward M5-D20.

### Why these three, together

| field | what it adds | what it does **not** do alone |
|---|---|---|
| `required_approving_review_count = 1` | an approval must exist, and GitHub forbids self-approval (B3-F3) | any collaborator could satisfy it |
| `require_code_owner_review = true` | the approval must come from the CODEOWNER — `AnastasiaAurelia` | vacuous if the author is the sole code owner (B1 §2.3, unproven — B-AC-6a) |
| `require_last_push_approval = true` | the approver cannot be the last pusher | nothing about *who* the approver is |

None is sufficient alone. The property B1 froze is the conjunction, resting on the external
precondition that the producer is `DIANA-AGENT` and the human credential is absent (B1-D1, B1-D2).

---

## 5. Authority boundary — who performs this change

> **`DIANA-AGENT` must not, and does not, have the authority to make this change.**

That is the point of the whole track, so B4 does not carve an exception for itself:

- automation **prepares** the change and verifies the baseline;
- automation **verifies** the result afterwards, read-only;
- **the human, `AnastasiaAurelia`, performs the mutation** through the browser control plane.

No admin credential is introduced into the automation environment. The owner's CLI credential is not
used — it was provider-revoked in B2 and stays revoked. Verified directly: the automation identity
holds `admin: false`, a ruleset `PUT` is refused, and GitHub reports `current_user_can_bypass: never`
for this token.

**A Track B that granted automation ruleset authority in order to enforce human approval would have
disproven its own thesis.**

---

## 6. Rollback snapshot

**Pre-B4 ruleset version: `49567930`** — frozen by B1-D26 and captured in full in B2 §13.2.

> **B4 must EDIT THE EXISTING RULESET IN PLACE.** Do not delete it, recreate it, replace it, or add a
> second overlapping ruleset. Deleting destroys the version-history chain and with it the rollback
> reference (B1-D27).

Automation **cannot** read ruleset history (`404`, admin-only — B2-F7), so it can neither verify the
snapshot nor restore it. **Rollback is human-only, by construction rather than by policy.**

---

## 7. B5 dependency

B4 leaves every enforcement property unproven. B5 must, in order: re-run the custody falsification;
run `DIANA_B4=1 governance/test-human-approval-b3.sh` and observe **B-AC-1 flip from `OBSERVED-UNSAFE`
to `PASS` with required checks green**, so the block is attributable to the approval rule and not to
pending CI; then settle B-AC-3, 4, 4a, 5, 6, 6a, 8, 10, 12 and CASE 5.

Until then the honest statement is: *the configuration is enabled and its effect is unmeasured.*

---

## 8. Post-B4 live ruleset — verified by automation, read-only

Re-read as `DIANA-AGENT` after the human saved. Compared field by field against the §3 baseline.

### 8.1 The semantic delta is exactly the three intended fields

| field | before | after | |
|---|---|---|---|
| `required_approving_review_count` | `0` | **`1`** | applied |
| `require_code_owner_review` | `false` | **`true`** | applied |
| `require_last_push_approval` | `false` | **`true`** | applied |

**Unexpected changes: NONE.**

### 8.2 Fifteen preserved fields, each verified identical

`id` `22188373` · `name` `diana-main-protection` · `enforcement` `active` · `target` `branch` ·
`include ["refs/heads/main"]` · `exclude []` · rules `deletion, non_fast_forward, pull_request,
required_status_checks` · `dismiss_stale_reviews_on_push true` · `required_review_thread_resolution
false` · `require_extra_approval_for_unattributed_changes false` · `required_reviewers []` ·
`allowed_merge_methods [merge, squash, rebase]` · `strict_required_status_checks_policy true` ·
required checks `Diana Gate, Diana Security Gate` · `current_user_can_bypass never`.

`updated_at` moved `2026-09-14T02:55:45.216+07:00` → **`2026-09-20T21:57:14.933+07:00`**.

### 8.3 Edited in place, as B1-D27 requires

The ruleset id is still `22188373`, the repository still has **exactly one** ruleset, and classic
branch protection is still absent. **Nothing was deleted or recreated**, so the version-history chain
— and with it the rollback reference — survives.

---

## 9. Bypass verification, and the limit of what automation can say

| observation | source |
|---|---|
| `current_user_can_bypass == "never"` | **automation-observed** — GitHub's statement about *this* credential |
| `bypass_actors == []` | **not observable by automation** — the field is absent from the response this identity receives (B3-F1) |

> The distinction is preserved deliberately. **Automation proves it cannot bypass.** It does **not**
> prove the configured bypass list is empty, and the field's absence from the payload is a
> **visibility limit, not evidence that the list is empty**. Establishing the list is a human/admin
> read.

---

## 10. B4-F1 — the custody precondition failed before post-change verification

> **This is the B2-F8 exposure recurring, and it is reported rather than smoothed over.**

Custody **held** at B4.0 and B4.1, in the process that performed the pre-mutation checks. Between
then and post-change verification the **agent process was relaunched from inside VS Code's
environment**. Measured in the current process:

```
  GIT_ASKPASS              PRESENT          VSCODE_GIT_IPC_HANDLE     PRESENT
  VSCODE_GIT_ASKPASS_NODE  PRESENT          VSCODE_GIT_ASKPASS_MAIN   PRESENT
  GH_TOKEN                 absent (no longer ambient; supplied per command from the 600-mode file)

  pid 13437  claude   GIT_ASKPASS, VSCODE_GIT_IPC_HANDLE      <- inherited by the agent itself

  $ git credential fill
  username=149037636        <- AnastasiaAurelia's user id
  password=<a credential that is NOT the DIANA-AGENT token>
```

**A working `AnastasiaAurelia` credential is reachable from the agent process on the git transport.**
The API transport remains clean (`gh api user` without an explicit token cannot authenticate).

**B2-D7 predicted exactly this**, in these words: *"Any relaunch that inherits the parent shell's
environment silently restores the exposure — and it would restore it as a working write credential,
with no error to notice."* It recurred within one phase, and **the only reason it was caught is that
B4.0's falsification is re-run rather than inherited.** A phase that trusted the previous phase's
custody finding would have proceeded under a false premise and produced confident nonsense.

**What this does and does not invalidate:**

| | |
|---|---|
| the human's ruleset edit | **unaffected** — performed in a browser, not by the agent |
| §8's field-by-field verification | **unaffected** — read-only observation of GitHub state, true regardless of what credentials the reader holds |
| Track B's custody assertion (B2 §16.3) | **VOID as of this observation** |
| B5's premise | **VOID** — B5 proves automation cannot self-satisfy approval, which is meaningless while automation can borrow the owner's identity |

**Remediation** is unchanged from B2 §15.4: relaunch the agent outside VS Code's environment (the
remediation that worked), **or** sign out of GitHub in VS Code *and* revoke the Visual Studio Code
authorization at GitHub — signing out locally is not revocation. Re-verification is B4.0's
falsification, which must pass before B5 begins.

> **B4-D1 — Environment isolation is not a control that stays fixed.** It survives only as long as
> the launch path preserves it, and nothing in this repository can enforce or detect that. Either
> every phase re-falsifies custody at its own start — which is now demonstrated to be load-bearing,
> not ceremony — or the VS Code authorization is revoked at the provider so the credential is
> worthless rather than merely unreachable.

---

## 11. Ruleset history evidence — OUTSTANDING, not waived

B1-D26 and the B4 direction require capturing the new ruleset version identifier, actor and
timestamp. **Automation cannot read ruleset history** (`404`, admin-only — B2-F7), so this evidence
can only be human-observed.

| item | status |
|---|---|
| timestamp | **not supplied** — the report returned the literal placeholder `[PASTE]` |
| actor | reported as `AnastasiaAurelia` (human-attested) |
| new version identifier | **not supplied** — literal placeholder `[PASTE]` |

> **This requirement is recorded as OUTSTANDING and explicitly not waived.** It is *not* a case of
> GitHub failing to expose the data; the values simply were not provided. Two of the three are still
> needed.
>
> Automation **can** corroborate that *a* change occurred and when: `updated_at` moved to
> **`2026-09-20T21:57:14.933+07:00`**. It **cannot** corroborate the actor or the version id, and
> neither is claimed here.

**Evidence provenance, kept separate as required:**

| automation-observed (read-only, this document) | human-observed (attestation only) |
|---|---|
| the three-field delta | that the edit was performed in the browser UI |
| all fifteen preserved fields | the acting identity (`AnastasiaAurelia`) |
| ruleset id, count, in-place edit | the ruleset history entry |
| `updated_at` before and after | the new version identifier *(outstanding)* |
| `current_user_can_bypass == "never"` | `bypass_actors == []` *(outstanding)* |

---

## 12. Rollback procedure — documented, not exercised

If enforcement makes normal operation unexpectedly impossible, the human — and only the human —
restores from snapshot **`49567930`** through the browser control plane:

```
required_approving_review_count   1     ->  0
require_code_owner_review         true  ->  false
require_last_push_approval        true  ->  false
                                  (every unrelated field preserved)
```

Rollback is **human-only, exceptional, and recorded**. It is not automation authority, not an
ordinary approval path, and `DIANA-AGENT` is deliberately not granted ruleset permission to perform
it — so it *cannot* be automated rather than merely *is not*. It is also **not** break-glass
(B1-D21): break-glass weakens protection to let work through; rollback restores a known state after a
failed enforcement change. The recorded reason must say which occurred.

---

## 13. Residual limitations

1. **Custody is failed right now** (§10). Everything downstream is blocked until it is restored.
2. **B4 proves configuration, not behaviour.** No unapproved merge has been observed to be refused.
   Every enforcement property remains `DEFERRED-TO-B5`.
3. **History evidence is outstanding** (§11) — timestamp and version identifier.
4. **`bypass_actors` is unverifiable by automation** (§9); only the per-token claim is established.
5. **B-AC-6a is unproven** (B3 §7.2) — the sole-CODEOWNER vacuity question is now *live*, because
   `require_code_owner_review` is `true` for the first time. If the vacuity holds, an
   owner-authored PR gets weaker protection than an automation-authored one.
6. **Cases 2–4 unmeasured** (B3 §8) — the diff-versus-SHA residual, B1-D15.
7. **Token expires 2026-10-20**, inside the B5 window (B2-D6).
8. **Diana Gate still contributes no merge protection** (B3-F4), by design.

---

## 14. B5 obligations

**B5 is blocked** until §10 is remediated and B4.0's falsification passes again. Then, in order:

1. re-run the custody falsification — **first**, and not inherited;
2. `DIANA_B4=1 governance/test-human-approval-b3.sh` — **B-AC-1 must flip from `OBSERVED-UNSAFE` to
   `PASS`, with required checks green**, so the block is attributable to the approval rule rather
   than to pending CI;
3. B-AC-3 — a human CODEOWNER approval makes a `DIANA-AGENT` PR mergeable;
4. B-AC-4 / CASE 1 — a diff-affecting push dismisses a standing approval;
5. B-AC-4a / CASES 2–4 — measure the non-diff boundary and record the answer whatever it is;
6. B-AC-5 — a fresh approval restores eligibility;
7. B-AC-6 — a non-CODEOWNER approval alone does not suffice;
8. B-AC-6a — settle the sole-CODEOWNER vacuity question under the now-live rule;
9. B-AC-8 — all three merge methods remain governed;
10. B-AC-10 — a CODEOWNERS or gate-file PR receives at least the same protection;
11. CASE 5 — `require_last_push_approval` rejects an approval from the last pusher;
12. B-AC-12 — a ruleset change appears in history with actor and timestamp (human-side read);
13. re-assert B-AC-2, B-AC-11, B-AC-13 — unchanged, or it is a regression.

---

## 15. Statement of status

**The mechanism is enabled. The mechanism is unproven. The custody precondition it depends on is
restored and re-falsified (§16).**

**M5-D20 remains UNDISCHARGED.**

---

## 16. B4-F2 — custody restored, this time by revocation rather than isolation

The human revoked the **Visual Studio Code** GitHub authorization for `AnastasiaAurelia` at the
provider. The confirmation was **not** taken on trust; the falsification was re-run in the live agent
process.

### 16.1 The result is more interesting than a clean pass

**The broker channel still exists.** `GIT_ASKPASS`, `VSCODE_GIT_IPC_HANDLE`,
`VSCODE_GIT_ASKPASS_NODE` and `VSCODE_GIT_ASKPASS_MAIN` are all still present in the agent process,
and `git credential fill` **still returns a credential** for `username=149037636` — the owner's user
id.

**But the credential is dead:**

| probe | result |
|---|---|
| `git push` without `GH_TOKEN` | **`remote: Invalid username or token`** — authentication failed, **no branch created** |
| the brokered credential used against the API | **`401 Bad credentials`** |
| `gh api user` without `GH_TOKEN` | not authenticated (`gh` store is `{}`) |
| `~/.git-credentials`, `~/.netrc`, `~/.config/hub` | absent |
| credential helpers configured | only `!gh auth git-credential`, and `gh` is logged out |

> **B4-D2 — "No usable owner credential" is the property; "no credential returned" is only a proxy,
> and the two have now come apart.** A brokered string that GitHub rejects is not authority. The
> falsification therefore tests **usability**, empirically, rather than presence.

### 16.2 Why this is stronger than the B2 remediation

B2 closed this channel by **environment isolation** — the agent was relaunched without the broker
variables — and B2-D7 recorded the weakness plainly: custody then depended on the launcher, and any
relaunch inheriting VS Code's environment silently restored the exposure. **§10 is that prediction
coming true within one phase.**

Revocation removes that dependency:

| | isolation (B2) | **revocation (B4)** |
|---|---|---|
| credential status | valid, merely unreachable | **invalid everywhere** |
| survives an agent relaunch? | **no** — exposure returns silently | **yes** |
| depends on the launch path? | **yes** | no |
| detectable if it regresses? | only by re-running the falsification | the credential must be re-authorized, a deliberate human act |

**B4-D1 stands, and is now satisfied by its second branch:** the VS Code authorization is revoked at
the provider, so the credential is worthless rather than merely unreachable. Per-phase re-falsification
remains mandatory regardless — it is what caught §10.

### 16.3 Custody assertion at the B4 close

> *At this observation point, within the inspected automation environment, the only working GitHub
> credential is the `DIANA-AGENT` classic PAT scoped `public_repo`, which cannot administer the
> repository (`admin: false`, `actions/permissions` 403, ruleset `PUT` refused,
> `current_user_can_bypass: "never"`). Both previously observed `AnastasiaAurelia` credentials — the
> GitHub CLI OAuth token and the VS Code-brokered token — are provider-invalidated and were each
> empirically observed to be rejected. No authenticated read or write is possible without the
> automation credential.*

**Still not claimed:** global absence; that no copy exists elsewhere; protection against a future
re-authorization; permanence. Re-authorizing VS Code or `gh` as the owner on this OS user would void
the precondition again, and **nothing in this repository can detect that** — which is why every phase
re-falsifies.

---

## 17. B4-F3 — two harness defects found while re-validating, and fixed

The B3 harness is the instrument B5 depends on. Re-running its preconditions under the new
(revoked, not isolated) custody model exposed two defects. Both are fixed in
`governance/test-human-approval-b3.sh`; the B3 document is **not** edited.

**Defect 1 — it aborted on a proxy rather than the property.** The harness treated the *presence* of
any broker variable, and *any* password returned by `git credential fill`, as proof that an owner
credential was reachable. Under isolation that was right. Under revocation it is **wrong in the
blocking direction**: it aborted with `credential-broker variable GIT_ASKPASS is present` on a
repository whose owner credentials are all dead, and would have blocked B5 indefinitely. Fixed to
note broker variables as a risk signal, then **test whether a brokered credential actually
authenticates**, aborting only if it does — plus a behavioural backstop that an unauthenticated push
must fail.

**Defect 2 — it could commit to the caller's branch.** The harness ran
`git checkout --detach origin/main` **without checking the exit status**. With a modified file that
does not exist on `origin/main`, git refuses the checkout; every later command then ran on the
**calling branch**, and the artifact commit landed on real Track B work. This was **observed, not
theorised**: commit `3b390f4` was created on `governance/mechanical-human-approval`. It was local
only, never pushed, and was removed by `git reset --mixed 7d99498`; the remote head never moved.
Fixed with three guards: refuse to run with a dirty working tree, check the checkout's exit status,
and assert `HEAD` is genuinely detached before doing anything else. The cleanup trap also now removes
the artifact file.

> Both defects share the shape this project keeps rediscovering: **a check that tested the
> circumstances of the last failure rather than the property it claimed to establish.** The first
> encoded "the environment looked like *this* when we were exposed"; the second assumed a command had
> succeeded because it had succeeded before. Neither could fail correctly until it was run under
> conditions different from the ones that produced it.

**Verification after the fix:** preconditions now pass — broker variables noted, brokered credential
observed rejected, API unauthenticated, unauthenticated push impossible — and the dirty-tree guard
fires and aborts, leaving the branch untouched. The harness was deliberately **not** run to
completion: that is B5.
