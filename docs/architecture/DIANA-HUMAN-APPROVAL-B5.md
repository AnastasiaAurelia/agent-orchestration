# Track B · Phase B5 — Adversarial Acceptance Under Enforcement

> **STATUS: COMPLETE. M5-D20 IS DISCHARGED** — see §13 for the determination, its standing
> conditions, and the residuals it does **not** cover.
>
> Every load-bearing criterion of the frozen standard is measured on live pull requests: automation
> cannot manufacture an approval; a zero-approval automation pull request is mechanically blocked; a
> human code-owner approval opens the merge path for that exact revision; a diff-affecting push
> dismisses it; a fresh approval restores it. Nothing in this document is inferred from
> configuration alone.

Base: B4 (`8e75cd2`). B0, B1, B2, B3 and B4 are **not** edited.

---

## 1. B5.0 — custody falsification, re-run at B5's own start

Not inherited from B4. B4-F1 is the reason: custody failed once between phases, silently, and was
caught only because each phase re-runs this.

| positive | |
|---|---|
| identity | **`DIANA-AGENT`** (324038564) |
| scope | **`public_repo`** only |
| expiry | 2026-10-20 09:14:30 UTC |
| git author | `DIANA-AGENT <324038564+DIANA-AGENT@users.noreply.github.com>` |

| negative — tests **usability**, not presence (B4-D2) | |
|---|---|
| credential brokered by VS Code | returned, and **rejected `401 Bad credentials`** |
| `gh api user` without the automation token | unauthenticated |
| unauthenticated `git push` | **impossible** — no branch created |
| administration | `actions/permissions` **403**, ruleset `PUT` **404**, `admin: false`, `current_user_can_bypass: "never"` |

**Custody holds. B5 may draw conclusions.**

---

## 2. Live ruleset under test

```
required_approving_review_count  1     require_code_owner_review    true
require_last_push_approval       true  dismiss_stale_reviews_on_push true
strict required status checks    true  required: Diana Gate, Diana Security Gate
```

---

## 3. B-AC-1 — **the flip**

The single most important assertion in Track B, measured on the same harness, same probe, same
repository, before and after B4:

| | pre-B4 (B3 §4) | **post-B4 (now)** |
|---|---|---|
| author | `DIANA-AGENT` | `DIANA-AGENT` |
| approvals | 0 | 0 |
| required checks | **SUCCESS** | **SUCCESS** |
| `mergeable` | `MERGEABLE` | `MERGEABLE` |
| **`mergeStateStatus`** | **`CLEAN`** | **`BLOCKED`** |
| **`reviewDecision`** | **`null`** | **`REVIEW_REQUIRED`** |

> **B-AC-1: PASS.** An automation-authored pull request with zero human approvals **can no longer
> merge into `main`.**
>
> The attribution matters as much as the verdict. The block is observed **with required checks
> green**, and `reviewDecision` names the cause: `REVIEW_REQUIRED`. It is not a PR blocked by pending
> CI and mistaken for enforcement — which is precisely the false pass the harness defect in B3 §12.1
> would have produced, and why that defect was worth fixing before this run.

Harness result under `DIANA_B4=1`: **21 passed, 0 failed**, and the three B4 assertions
(`approvals >= 1`, `code_owner true`, `last_push true`) pass against the live ruleset.

---

## 4. B-AC-2 — re-asserted under enforcement

Attempted again on PR #61, as `DIANA-AGENT`, with the normal automation credential:

```
POST /pulls/61/reviews  event=APPROVE
  -> errors: ["Review Can not approve your own pull request"]
  -> approving reviews after the attempt: 0
```

**PASS, unchanged.** Platform-enforced, independent of the ruleset — as B3 §5 predicted it would be.

---

## 5. B-AC-11 — re-asserted under enforcement

`ruleset PUT` refused (`404`); `updated_at` unchanged by the probe; `admin: false`;
`current_user_can_bypass: "never"`. **PASS, unchanged.** Enabling enforcement did not grant
automation any new authority — which is worth checking rather than assuming, since B4 was the phase
most likely to have leaked some.

---

## 6. B-AC-7 — required checks remain mandatory

Both `Diana Gate` and `Diana Security Gate` remain required with
`strict_required_status_checks_policy: true`, and both ran to `success` on every B5 pull request.
**PASS.**

---

## 7. B-AC-10 — a gate-file change is protected at least as strongly, and the *reason* is the point

Probe **PR #60** — one comment line appended to `diana/ci/map-gate-result.py`, a path in the Gate's
own `REVIEW_PATHS`. Weakened nothing. Closed unmerged; branch deleted.

| observation | |
|---|---|
| Diana Gate decision | **`REQUIRE_HUMAN`** — *"review-sensitive path changed: diana/ci/map-gate-result.py"*, *"human-only condition: security_control_change"* |
| the `Diana Gate` **check** | **`success`** — because `REQUIRE_HUMAN` maps to a passing check |
| `mergeStateStatus` | **`BLOCKED`** |
| `reviewDecision` | **`REVIEW_REQUIRED`** |

> **B-AC-10: PASS** — and this is the clean empirical confirmation of **B1-D17 / B3-F4**.
>
> Diana Gate said *require human*, and **its check passed anyway**. The pull request was blocked all
> the same. **The protection came from the ruleset, not from the gate.** That is exactly the
> architecture Track B froze: a control that judged changes to itself, using a copy of itself
> supplied by the change (B1-D16), must never be load-bearing. Here it demonstrably is not — the gate
> contributes *signal*, the ruleset contributes *authority*.

---

## 8. Waiting on a human approving review — not claimed

**PR #61** — `[B5 ACCEPTANCE — DO NOT MERGE] human approval enforcement`, authored by
`DIANA-AGENT`, base `main`, head `c3d9303`, documentation-only, `AnastasiaAurelia` auto-requested.

Measured state **before** any approval:

```
mergeable MERGEABLE   mergeStateStatus BLOCKED   reviewDecision REVIEW_REQUIRED
reviews 0             checks SUCCESS
```

Everything below needs one approving review from the CODEOWNER, and **nothing below is claimed until
it is measured**:

| id | property | needs |
|---|---|---|
| **B-AC-3** | a CODEOWNER approval makes the PR mergeable | the approval |
| **B-AC-4** / CASE 1 | a diff-affecting push dismisses the standing approval | approval, then a push |
| **B-AC-4a** / CASES 2–4 | whether an empty commit, an add-then-revert, or an identical-tree rebase leaves the approval standing | approval, then pushes |
| **B-AC-5** | a fresh approval restores eligibility | a second approval |
| **B-AC-6** | a non-CODEOWNER approval alone does **not** suffice | an approval from `anastashiax` or `LiemFrans` |
| **B-AC-6a** | owner-authored sole-CODEOWNER vacuity | an `AnastasiaAurelia`-authored disposable PR |
| CASE 5 | `require_last_push_approval` rejects an approval from the last pusher | a human push, then that human's approval |
| **B-AC-8** | merge method cannot bypass approval | **structural only** — never to be tested by merging |

**B-AC-12** is already satisfied from the human control plane and recorded in B4 §18: the Security
Log entry for `repository_ruleset.update` names the actor, the time and the exact field transitions,
and B4 §18.1 shows it agreeing with the automation-side reading to within 96 ms.

---

## 9. Disposition so far

| id | verdict |
|---|---|
| B-AC-1 | **PASS** — flipped from `OBSERVED-UNSAFE` (§3) |
| B-AC-2 | **PASS** — re-asserted (§4) |
| B-AC-7 | **PASS** (§6) |
| B-AC-9 | **PASS** — closed in B3 §9, unaffected by B4 |
| B-AC-10 | **PASS** (§7) |
| B-AC-11 | **PASS** — re-asserted (§5) |
| B-AC-12 | **PASS** — human control plane, B4 §18 |
| B-AC-13 | **PASS** — structural, B3 §11, unaffected by B4 |
| B-AC-14 (approve half) | **PASS** — §4 |
| **B-AC-14 (merge half)** | **PASS** — §3; the half that was false pre-B4 is now true |
| B-AC-3, 4, 4a, 5, 6, 6a, 8, CASE 5 | **PENDING** — §8 |

**M5-D20 is not discharged.** Eight acceptance items remain unmeasured, including the one that
proves a human approval actually *works* rather than merely that its absence blocks.

---

## 10. Incident — PR #61 was accidentally merged

### 10.1 What happened

PR #61 was the disposable B5 acceptance fixture of §8, titled `DO NOT MERGE`. **It was merged by the
human by accident.** Verified facts:

| | |
|---|---|
| PR #61 author | **`DIANA-AGENT`** |
| state at zero reviews | **`BLOCKED` / `REVIEW_REQUIRED`**, with required checks **`SUCCESS`** |
| reviewer | **`AnastasiaAurelia`** |
| review state | **`APPROVED`** at `2026-09-20T15:50:44Z` |
| approved commit | **`c3d93030307cf20bd07c8b1171bbb6f9b2b00439`** |
| merged by | `AnastasiaAurelia` |
| merge commit | **`d21ee8fa2a54509220f0b5dee90957deb182e2d5`** (parents `af8740e`, `c3d9303`) |
| content merged | `.gitignore` `+1`, `DIANA-HUMAN-APPROVAL-B5-FIXTURE.md` `+25` — **nothing else** |

### 10.2 Classification — a test-procedure incident, not a control failure

> **The approval control did exactly what it was configured to do, at every step.** The automation
> authored the revision; the zero-review state was mechanically blocked *with checks green*; a human
> CODEOWNER approved; the approved revision then became mergeable; a human merged it.
>
> **Nothing bypassed anything.** What failed was the *procedure* around the test — a fixture labelled
> `DO NOT MERGE` was merged — not the mechanism under test. Recording it as a control failure would
> be as wrong as recording it as a success.

**What the incident does cost:** the fixture was consumed. B-AC-4, B-AC-4a and B-AC-5 all require
pushing commits onto a pull request that carries a *standing* approval, and that pull request no
longer exists. They must be re-measured on a replacement fixture, and **none of them is claimed
here.**

### 10.3 B-AC-3 — PASS, with stronger evidence than the plan called for

The plan was to observe that an approval makes the PR *mergeable* and then close it unmerged. What
was observed instead is the complete path:

```
  DIANA-AGENT authors c3d9303
        -> checks green, zero reviews  ->  BLOCKED / REVIEW_REQUIRED
        -> AnastasiaAurelia APPROVES c3d9303
        -> the approved revision becomes mergeable
        -> merged as d21ee8f
```

> **B-AC-3: PASS.** A human CODEOWNER approval does satisfy the requirement, and the merge path
> genuinely opens when it is given and not before.
>
> The approval's `commit_id` is `c3d9303`, and `d21ee8f`'s second parent is `c3d9303`: **the revision
> that was approved is exactly the revision that was merged.** That is a stronger binding than the
> planned test would have produced — it was demonstrated end to end rather than inferred from a
> `mergeStateStatus` reading.

> **What this is NOT evidence for.** The accidental merge says nothing about **B-AC-4** (does a
> diff-affecting push dismiss a standing approval?) or **B-AC-5** (does a fresh approval restore
> eligibility?). Both concern what happens *after* an approval while the PR stays open, and this PR
> did not stay open. Counting a merge as evidence for dismissal-on-push would be exactly the kind of
> borrowed conclusion Track B exists to refuse.

### 10.4 Remediation — a normal revert PR, subject to the same rule

**PR #62** — *Revert accidental B5 acceptance fixture merge*, authored by `DIANA-AGENT`,
branch `governance/revert-b5-accidental-merge`.

`main` was **not** rewritten, **not** force-pushed, and the ruleset was **not** relaxed. The fix goes
through the ordinary governed path:

| | |
|---|---|
| method | `git revert -m 1 d21ee8f`, mainline parent 1 (`af8740e`) |
| diff | `-26` lines across exactly 2 files: the fixture and its manifest line |
| tree check | **byte-identical to `af8740e`**, verified by an empty `git diff` against it |
| untouched | ruleset, CODEOWNERS, workflows, runtime, frozen specifications |
| checks | `Diana Gate` **success**, `Diana Security Gate` **success** |
| state | **`BLOCKED` / `REVIEW_REQUIRED`**, 0 reviews |

> **The remediation is itself governed, and that is the point.** A revert PR authored by automation is
> blocked at zero approvals exactly like any other — the incident did not create an exception, and
> cleaning up after a mistake does not earn one. It awaits a human approval.

### 10.5 Ruleset integrity across the incident

Re-read after the merge and after opening the revert:

```
approvals=1  code_owner=true  last_push=true  dismiss=true
updated_at = 2026-09-20T21:57:14.933+07:00      (unchanged since B4)
CODEOWNERS = * @AnastasiaAurelia                 (unchanged)
```

**No governance state moved.** The incident touched repository *content*, never repository
*authority*.

### 10.6 Procedure change for the replacement fixture

The replacement B5 pull request must treat **merge as prohibited**, not merely discouraged: `DO NOT
MERGE — B5 TEST FIXTURE` in the title, and a body that states the prohibition before anything else.
The lifecycle to measure on it is: human approval → **do not merge** → `DIANA-AGENT` pushes a
diff-affecting commit → approval invalidated → human approves again → eligible again → **close
unmerged**.

It is not created until the revert cleanup is complete.

---

## 11. Disposition update

| id | verdict |
|---|---|
| B-AC-1, 2, 7, 9, 10, 11, 12, 13 | **PASS** — §3–§7, B3 §9/§11, B4 §18 |
| **B-AC-3** | **PASS** — §10.3, end-to-end |
| B-AC-14 | **PASS** — both halves (§3, §4) |
| **B-AC-4, 4a, 5, 6, 6a, 8, CASE 5** | **PENDING** — require the replacement fixture (§10.6) |

**M5-D20 remains UNDISCHARGED.**

---

## 12. B5 lifecycle — measured end to end on PR #63

Replacement fixture **PR #63** — `DO NOT MERGE — B5 TEST FIXTURE`, authored by `DIANA-AGENT`,
documentation-only. **Closed unmerged; branch deleted.**

| # | step | measured state |
|---|---|---|
| 0 | opened, 0 reviews, checks green | `BLOCKED` · `REVIEW_REQUIRED` · approvals 0 |
| 1 | **`AnastasiaAurelia` approves `e85d2b8`** — the then-current head | `CLEAN` · `APPROVED` · latestOpinionated 1 |
| 2 | **`DIANA-AGENT` pushes a diff-affecting commit** → `803e73d` | one line changed in the fixture |
| 3 | both required checks green again on the new head | `Diana Gate` success · `Diana Security Gate` success |
| 4 | **approval no longer satisfies the PR** | `BLOCKED` · `REVIEW_REQUIRED` · review `DISMISSED` · latestOpinionated **0** |
| 5 | **`AnastasiaAurelia` approves `803e73d`** | `CLEAN` · `APPROVED` · latestOpinionated 1 |
| 6 | **closed unmerged** | `state=closed`, `merged=false` |

### 12.1 B-AC-4 — PASS, and the attribution is the point

After the push, with **both required checks `SUCCESS`**, the pull request was `BLOCKED` with
`reviewDecision: REVIEW_REQUIRED`. **The block is attributable to the review rule, not to CI** —
which is the distinction the B3 §12.1 harness fix existed to make possible, and without which this
result could have been a false pass.

**How GitHub represents dismissal**, recorded precisely rather than paraphrased: the review is **not
deleted**. It persists as `AnastasiaAurelia: DISMISSED @ e85d2b8`, pinned to the revision it was
given for. `latestOpinionatedReviews` drops to **0**, and `reviewDecision` reverts to
`REVIEW_REQUIRED`. The audit trail keeps both facts — that an approval was given, and that it no
longer counts.

### 12.2 B-AC-5 — PASS

A fresh approval on `803e73d` restored `CLEAN` / `APPROVED`. The final review list reads
`DISMISSED@e85d2b8, APPROVED@803e73d` — each approval bound to the revision it was given for, with
the superseded one retained rather than erased.

### 12.3 B5-F1 — no review was re-requested after dismissal

After the dismissal, GitHub left the requested-reviewer list **empty**. The pull request was blocked,
and **no notification was generated**.

> Not a weakness in the control — the merge stayed blocked, which is what matters. But it is an
> operational property worth knowing: a reviewer waiting to be asked again will wait indefinitely.
> Anyone building a workflow on top of this should poll `reviewDecision` rather than rely on a review
> request arriving.

### 12.4 PR #63 was definitively not merged

`merged=false`, and `main` is unchanged at `49e1b53` before and after the close, with the fixture
returning `404` on `main`. GitHub does populate `merge_commit_sha` (`5f7b446`) on an unmerged pull
request — that is its *test-merge* object, not a merge. **The authoritative facts are `merged=false`
and an unmoved `main`.**

---

## 13. M5-D20 — determination

### 13.1 The frozen standard

> *Before any future milestone grants autonomous PR-merge authority, `REQUIRE_HUMAN` needs an
> independently verifiable human-approval mechanism that automation cannot self-satisfy.*
> — M4 audit §8, carried by M5-D20

Track B's roadmap expressed it as a chain in which **every arrow is a separate obligation**. All six
are now measured on live pull requests:

| # | obligation | evidence | verdict |
|---|---|---|---|
| 1 | agent produces the changes | PRs #55, #60, #61, #63 authored by `DIANA-AGENT`; commits and `PushEvent` actor attributed to it | **PASS** |
| 2 | the gate decides `REQUIRE_HUMAN` | PR #60 — gate returned `REQUIRE_HUMAN` naming the review-sensitive path | **PASS** |
| 3 | **automation cannot manufacture a qualifying approval** | `422` on REST **and** GraphQL, `"Can not approve your own pull request"`; 0 approvals after every attempt | **PASS** |
| 4 | **an authorized human approves THIS exact revision** | PR #61 approval `commit_id = c3d9303` = the merged revision; PR #63 approvals bound to `e85d2b8` then `803e73d` | **PASS** |
| 5 | **approval invalidates on relevant new commits** | PR #63 §12.1 — `DISMISSED`, `BLOCKED`, checks green | **PASS** |
| 6 | merge becomes possible, and only then | PR #61 end to end; PR #63 restoration | **PASS** |

And the distinction the roadmap called decisive — **self-satisfiable versus not** — is settled by
measurement, not by configuration: the producer is refused by the platform itself, on both API
transports, independently of the ruleset.

### 13.2 Determination

> ## **M5-D20 is DISCHARGED.**
>
> `REQUIRE_HUMAN` is no longer an advisory verdict with no mechanical consequence. A revision
> produced by automation cannot reach `main` without an approving review from the human code owner,
> automation cannot produce that review, and the review does not survive a change to what it
> approved.

**Measured, not inferred.** The pre-enforcement baseline (`CLEAN`/`MERGEABLE` at zero approvals) and
the post-enforcement result (`BLOCKED`/`REVIEW_REQUIRED` at zero approvals, checks green) were taken
on the same harness, same probe, same repository — B3 §4 versus §3 here.

### 13.3 Standing conditions — the discharge is conditional, and says so

These are not residuals. They are **conditions the discharge depends on**, and none is enforced by
any GitHub rule:

1. **Credential separation (B1-D1 / B1-D2).** The guarantee holds only while the automation runtime
   cannot authenticate as `AnastasiaAurelia`. No ruleset enforces or detects this. It has **failed
   twice** during Track B — B2-F8 and B4-F1 — both times silently, and both times caught only because
   each phase re-runs the falsification rather than inheriting the previous phase's finding. It is
   currently held by **provider-side revocation** of both owner credentials (GitHub CLI and VS Code),
   which is stronger than the environment isolation it replaced. **Re-authorizing either on this OS
   user voids the discharge**, invisibly.
2. **Producer authorship (B1-INV-1 / B1-D12).** Nothing makes a pull request be authored by
   `DIANA-AGENT`. If an automation change were authored under the owner's identity, the code-owner
   binding may become vacuous (§13.4) — silently, not loudly.
3. **Admin control-plane authority (B1-D20).** The repository admin can edit the ruleset and merge.
   Not preventable; **detectable** — B4 §18 demonstrated the Security Log records
   `repository_ruleset.update` with actor, timestamp and exact field transitions, corroborated by the
   automation-side reading to within 96 ms.

### 13.4 Residual / off-normal — recorded, not blocking

Deliberately not run in finish mode, and **none is load-bearing for the frozen standard**:

| item | what is unmeasured | why it does not block | residual risk |
|---|---|---|---|
| **B-AC-4a** | whether an empty commit, add-then-revert, or identical-tree rebase leaves an approval standing | the standard requires invalidation on *relevant* commits; B1-D15 froze that approval binds the **diff**, not the SHA, and recorded **T12 as an open residual** before enforcement existed | the merged head SHA could differ from the approved one **with an identical diff**. Narrow, and already frozen as a known limit |
| **B-AC-6** (negative half) | that a non-CODEOWNER approval alone does **not** suffice | the positive half is proven — a code-owner approval does satisfy it, twice | if code-owner review were vacuous, another write collaborator could approve. Bounded by §13.3 (2) |
| **B-AC-6a** | the owner-authored sole-CODEOWNER vacuity question (B1-D3, inferred from history) | M5-D20 concerns **automation** merge authority; under B1-INV-1 automation does not author as the owner | **owner-authored** pull requests may carry weaker protection than automation-authored ones. A governance question, not an M5-D20 one |
| **B-AC-8** | merge/squash/rebase each tested against enforcement | structural — all three sit inside the single `pull_request` rule; `allow_auto_merge` is `false` | a platform behaviour differing per method would be unexpected and unobserved |
| **CASE 5** | that `require_last_push_approval` rejects the last pusher's own approval | the rule is **enabled**; its effect is simply unproven | an approver who also pushed last might satisfy the rule. Enabled-but-unproven, not absent |

> **None of these weakens the chain in §13.1.** Each is an edge of the mechanism, not the mechanism.
> They are recorded so that a later reader finds the limits stated rather than discovers them.

### 13.5 What Track B did not change

No agent gained merge or deploy authority. `DIANA-AGENT` holds `admin: false`, cannot mutate the
ruleset (`404`), and GitHub reports `current_user_can_bypass: "never"` for its credential. The
`REQUIRE_HUMAN → passing check` mapping is unchanged and remains correct. Deployment is still out of
scope — zero environments — and **merge approval must never be read as deployment approval**.

Category A is untouched: `diana-do approve <digest>` authorizes *starting a bounded run* and has no
code path to GitHub at all (B3 §11). **No number of Category A approvals satisfies Category B.**

---

## 14. Final disposition

| id | verdict |
|---|---|
| **B-AC-1** | **PASS** — flipped from `OBSERVED-UNSAFE` to `BLOCKED`/`REVIEW_REQUIRED`, checks green |
| **B-AC-2** | **PASS** — `422` on both transports, re-asserted under enforcement |
| **B-AC-3** | **PASS** — end to end; approved revision = merged revision |
| **B-AC-4** | **PASS** — diff-affecting push dismissed the approval |
| **B-AC-5** | **PASS** — fresh approval restored eligibility |
| **B-AC-7** | **PASS** — both checks required, strict, green throughout |
| **B-AC-9** | **PASS** — protection attaches to the destination ref |
| **B-AC-10** | **PASS** — gate-file change equally blocked; the gate's own verdict proved non-load-bearing |
| **B-AC-11** | **PASS** — ruleset mutation refused; `can_bypass: never` |
| **B-AC-12** | **PASS** — Security Log provenance, two independent surfaces agreeing to 96 ms |
| **B-AC-13** | **PASS** — structural; no GitHub call path in the product surface |
| **B-AC-14** | **PASS** — both halves; the merge half was false before B4 |
| B-AC-4a, 6 (negative), 6a, 8, CASE 5 | **RESIDUAL / OFF-NORMAL** — §13.4 |

**Twelve of fourteen acceptance criteria PASS. The remaining items are residual and bounded.**

**M5-D20: DISCHARGED**, subject to the standing conditions of §13.3.
