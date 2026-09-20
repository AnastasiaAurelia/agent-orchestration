# Track B · Phase B5 — Adversarial Acceptance Under Enforcement

> **STATUS: IN PROGRESS — A TEST-PROCEDURE INCIDENT OCCURRED (§10).**
> The enforcement-only items are settled (§3–§7). **B-AC-3 is settled with stronger evidence than
> planned**, as a by-product of the incident (§10.3). The remaining lifecycle items — B-AC-4, 4a, 5,
> 6, 6a and CASE 5 — are **not** claimed and must be re-measured on a replacement fixture.
> **M5-D20 remains UNDISCHARGED.**

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
