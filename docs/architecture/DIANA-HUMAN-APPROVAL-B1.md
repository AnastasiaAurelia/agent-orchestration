# Track B · Phase B1 — Frozen Semantics for Mechanical Human Approval (M5-D20)

Status: **specification, frozen.** No GitHub setting, ruleset, CODEOWNERS entry, workflow or
credential is changed by this document. **M5-D20 remains undischarged.**

Base: B0 (`b62013a`), branch `governance/mechanical-human-approval`.
Change boundary: this file and the docs manifest. Nothing else.

This document freezes decisions (`B1-D*`), release invariants (`B1-INV-*`), and the acceptance
direction for B3 (`B-AC-*`). It does not implement them.

---

## 1. Thesis

`REQUIRE_HUMAN` is a verdict. M5-D20 requires it to become a **merge condition that the normal
automation credential cannot satisfy**.

```
  DIANA-AGENT  ──creates/pushes revision──▶  PR to main
                                              │
                                              ▼
                                    Diana checks decide
                                              │
                                              ▼
                     DIANA-AGENT cannot manufacture a qualifying approval
                                              │
                                              ▼
              AnastasiaAurelia (human, CODEOWNER) approves THIS revision
                                              │
                                              ▼
                              ruleset permits merge — and only then
```

The load-bearing rule, frozen:

> **B1-D1 — `producer_identity != qualifying_human_identity`.**
> Producer: `DIANA-AGENT`. Qualifying human: `AnastasiaAurelia`.
> `DIANA-AGENT` must not possess, and must not be able to obtain, credentials that authenticate as
> `AnastasiaAurelia`.

> **B1-D2 — Credential separation is an EXTERNAL PRECONDITION, not a ruleset guarantee.**
> No GitHub ruleset enforces it, observes it, or can detect its violation. Every property in §6
> holds *conditionally on* B1-D1. If the producer obtains the human credential, the guarantee
> collapses silently and GitHub will report nothing. This document never describes credential
> separation as something GitHub enforces.

---

## 2. Empirical facts, and two corrections to B0

B0 measured the live ruleset. B1 additionally read the **ruleset version history**
(`GET /repos/{r}/rulesets/22188373/history`) and the **full review record of every merged PR**. That
evidence falsifies two of B0's conclusions and supplies a third fact B0 did not have.

### 2.1 The target configuration already ran in this repository, and was deliberately removed

`diana-main-protection` (id `22188373`) has exactly three versions, **all authored by actor
`149037636` = `AnastasiaAurelia`**:

| version | when (UTC) | approvals | code-owner | last-push | dismiss-stale | thread-resolution | checks |
|---|---|---|---|---|---|---|---|
| `48563849` | 2026-09-03 11:13 | **1** | **true** | **true** | true | **true** | Diana Gate |
| `49002862` | 2026-09-08 12:10 | **1** | **true** | **true** | true | **true** | + Diana Security Gate |
| `49567930` | **2026-09-13 19:55** | **0** | **false** | **false** | true | false | unchanged |

`bypass_actors` was `[]` in all three. **Version `48563849`/`49002862` is, parameter for parameter,
the configuration §6 proposes to restore** — plus `required_review_thread_resolution`, which those
versions also had.

The weakening at 2026-09-13 19:55 UTC is not incidental. **PR #44 merged at 2026-09-13 19:58 UTC —
three minutes later — with zero approvals**, and every PR since has done the same. The protection was
not absent by oversight; it was **turned off by the admin, immediately before the M1–M7 runtime
series began merging unreviewed.** This is the admin control-plane escape of §10, already exercised,
and it is recorded here rather than described as hypothetical.

### 2.2 Correction to B0 — "zero reviews ever submitted" is **false**

B0 sampled PRs #49–#52 and concluded no review had ever been submitted on this repository. Reading
the complete record shows the opposite, and the pattern is exactly the topology B1 wants:

| PR author | PRs | approving reviewers |
|---|---|---|
| **`DIANA-AGENT`** | #30, #32, #33, #34, #35, #36, #38, #39, #42 | **`AnastasiaAurelia` on every one** (#30 and #42 also `anastashiax`) |
| `AnastasiaAurelia` | #37, #43 | `anastashiax` only |
| `AnastasiaAurelia` | #44–#52 | none *(after the weakening)* |

**Nine automation-authored pull requests were approved by the sole CODEOWNER and merged.** B-AC-3 is
therefore not speculative — it ran in production for ten days. `anastashiax` also merged #30, #37,
#42 and #43, so a second human has acted as merger.

### 2.3 Correction to B0 — "enabling A+B today would deadlock the repository" is **false**

B0 reasoned that with `AnastasiaAurelia` as both sole CODEOWNER and PR author, requiring a code-owner
approval would be unsatisfiable. **Measured behaviour contradicts this.** Under version `48563849`
(`count=1`, `code_owner=true`), PR #37 and PR #43 — both authored by `AnastasiaAurelia` — merged with
a **single approval from `anastashiax`, who is not a CODEOWNER**. No code-owner review was ever
requested on them (`requested_reviewers: []`).

The behaviour consistent with all eleven observations: **when the PR author is the only code owner for
the changed paths, GitHub does not request a code-owner review, and the code-owner requirement does
not block that PR.** Only the generic `required_approving_review_count` remains, satisfiable by any
other collaborator. GitHub does not document this case; the finding here is empirical.

> **This is worse than the deadlock B0 predicted, not better.** A deadlock is loud. This is silent:
> an owner-authored PR receives *strictly weaker* protection than an automation-authored one, the
> code-owner binding evaporates, and nothing in the UI or API announces it.

**B0's ordering conclusion survives, for a corrected reason.** B2-before-B4 is still mandatory — not
because B4-first would jam the repository, but because B4-first with the agent authenticating as the
sole CODEOWNER would produce a configuration that *looks* enforced and **is not**.

> **B1-D3 — The §2.3 behaviour is inferred from this repository's history, not from a controlled
> experiment or from GitHub documentation.** B3 must confirm it directly (B-AC-6a). Until then it is
> treated as true for *risk* purposes and unproven for *assurance* purposes.

### 2.4 Identity is split three ways today, and the git identity is not the one B0 reported

| layer | current value | resolves to |
|---|---|---|
| GitHub API actor (`gh auth`) | token of `AnastasiaAurelia` (id 149037636) | **owner / admin / sole CODEOWNER** |
| git `user.email` used for commits | `aurelanas@gmail.com` | **`anastashiax`** — a *different* write collaborator |
| intended automation identity | `DIANA-AGENT` (id 324038564, write, `admin:false`) | **unused since 2026-09-10** |

So commits the agent writes today are attributed to `anastashiax` while the PR is opened and merged by
`AnastasiaAurelia`. During the DIANA-AGENT era this was correct: commits on PR #39 are authored
`DIANA-AGENT <324038564+DIANA-AGENT@users.noreply.github.com>` and GitHub attributes them to
`DIANA-AGENT`. **A known-good configuration exists and can be restored.**

> **B1-D4 — B2 must change both the API credential and the git author/committer identity.**
> Switching only the `gh` token would leave every commit attributed to `anastashiax`, corrupting the
> audit trail and misrepresenting a human as the author of machine-written code.

### 2.5 Facts carried forward unchanged from B0

- Classic branch protection absent; one active ruleset targeting `refs/heads/main`; `main` is the only
  protected branch.
- `bypass_actors: []`, and the live ruleset reports **`current_user_can_bypass: "never"`** for the
  admin caller — GitHub's own statement that this ruleset has no bypass path.
- Required checks `Diana Gate` + `Diana Security Gate`, `strict_required_status_checks_policy: true`.
- `allowed_merge_methods: [merge, squash, rebase]`; `allow_auto_merge: false`.
- `.github/CODEOWNERS` = `* @AnastasiaAurelia`, unchanged since `1f5bf67` (2026-09-03); it is the only
  CODEOWNERS file in the tree.
- Owner is a **User account** → no organization rulesets, no teams.
- Zero environments; no deployment workflow; Diana holds no deploy authority.
- `GITHUB_TOKEN` in both workflows lacks `pull-requests: write` and cannot submit a review.
- `DIANA-AGENT` permissions: `{admin: false, maintain: false, push: true, triage: true, pull: true}`.

---

## 3. Identity model — frozen

| role | identity | may author protected PRs | may give qualifying approval | may merge | may edit rulesets |
|---|---|---|---|---|---|
| **Producer** | `DIANA-AGENT` | **must** (B1-INV-1) | **never** | only after §6 is satisfied | **no** — `admin: false` |
| **Qualifying human** | `AnastasiaAurelia` | must not, for protected automation work | **yes — sole CODEOWNER** | yes | yes (admin; §10) |
| Additional humans | `anastashiax`, `LiemFrans` | n/a | **no** — not CODEOWNERS | yes, once §6 satisfied | no |

> **B1-INV-1 (release invariant) — Protected automation PRs MUST be authored by `DIANA-AGENT`.**
> This is a requirement, not a preference. §2.3 shows the alternative is not a safe failure: an
> `AnastasiaAurelia`-authored PR silently voids the code-owner requirement that carries the entire
> human-approval guarantee. A `require_code_owner_review` rule whose binding depends on who happens to
> have opened the PR is not a mechanism.

> **B1-D5 — `AnastasiaAurelia` remains the sole CODEOWNER, and `DIANA-AGENT` must never be added.**
> Adding the producer to CODEOWNERS would let automation satisfy the human-review condition and
> destroys the property outright.

---

## 4. Credential classes — frozen

**AUTOMATION CREDENTIAL** — authenticates only as `DIANA-AGENT`; present in the agent runtime.
**HUMAN CREDENTIAL** — authenticates as `AnastasiaAurelia`; never present in the agent runtime; used
for qualifying review and for §10 administration.

### Least privilege for the automation credential

Measured against what the agent actually does — push a branch, open a PR, update it, read checks,
read reviews, read repository metadata. Using a fine-grained personal access token scoped to this one
repository:

| capability | permission | classification |
|---|---|---|
| push branch, read tree | `Contents: read & write` | **required** |
| open / update PR, read reviews | `Pull requests: read & write` | **required** |
| read check runs and statuses | `Checks: read`, `Commit statuses: read` | **required** |
| read repository metadata | `Metadata: read` | **required** (mandatory for fine-grained tokens) |
| edit workflow files | `Workflows: write` | **unnecessary** — today's token carries `workflow` scope; the agent does not need to modify CI, and §9 treats CI as a control file |
| administration / rulesets | `Administration: *` | **must be absent** — see B-AC-11 |
| manage secrets, environments, webhooks, pages | — | **unnecessary** |
| issues, discussions, projects | — | **unnecessary**; grant only if a later track needs it |

> **B1-D6 — The automation credential must be a fine-grained token limited to this repository, with
> `Administration` absent and `Workflows: write` absent.** Convenience is not a justification. A
> classic PAT with blanket `repo` scope (what is in use today) is **not acceptable** for B2: `repo`
> implies administration on repositories the identity can administer and cannot be narrowed.

> **B1-D7 — The automation credential must not be able to submit pull-request reviews *that count*.**
> GitHub allows any collaborator to submit a review, and `Pull requests: write` is required for the
> agent to open PRs at all, so this is **not** achieved by withholding a permission. It is achieved by
> B1-INV-1: the producer is the PR author, and GitHub refuses self-approval. The guarantee comes from
> authorship, not from scope.

---

## 5. Custody prerequisite — and the limits of proving it

**The question B1 must answer honestly: how can we know the automation runtime does not possess the
human credential?**

**We cannot know it. GitHub cannot prove it, and no mechanism in this track can.** Credential absence
is a negative property of an environment that Track B does not control and cannot exhaustively
inspect. Anything claiming otherwise would be false.

What Track B *can* do is make possession **costly to hide and cheap to detect**. The strongest
honest assertion:

> **B1-D8 — Custody assertion (the strongest claim Track B may make).**
> *At the time of assertion, on the inspected automation host and in the inspected configuration and
> secret stores, no credential authenticating as `AnastasiaAurelia` was found; the automation's
> authenticated identity was observed to be `DIANA-AGENT`; and the owner credential previously present
> was revoked at GitHub, so any undiscovered copy is inert.*
>
> This is a **point-in-time, scope-limited, positively-evidenced assertion — not a proof**, and it is
> void for any environment not inspected.

Acceptable evidence, in increasing strength:

1. **Inventory** — no owner token in the agent's environment, shell profiles, `gh` hosts file, git
   credential helpers, or any secret store the automation reads. *Weak: absence of evidence.*
2. **Positive identity observation** — `gh api user` from the automation runtime returns
   `DIANA-AGENT`. *Moderate: shows what it uses, not what it holds.*
3. **Behavioural refusal** — automation attempts an action only the owner may perform (e.g. a ruleset
   write) and GitHub refuses it as `DIANA-AGENT`. *Stronger: B-AC-11.*
4. **Revocation at the source** — the specific owner PAT previously used by automation is **deleted at
   GitHub**, not merely removed from disk. *Strongest available: it makes copies worthless rather than
   merely unfound, and converts an unprovable negative into a verifiable positive.*

> **B1-D9 — Revocation (4) is mandatory in B2; inventory alone is insufficient.** Deleting a file
> proves nothing about copies. Revoking the token invalidates every copy at once.

> **B1-D10 — Custody is the load-bearing control of this entire track, and it is the weakest link.**
> Every §6 property is conditional on it. This is stated plainly rather than assumed away.

---

## 6. Exact desired ruleset semantics — frozen, not applied

Target values for `diana-main-protection` (id `22188373`), to be applied in **B4 and not before**:

```
required_approving_review_count          = 1          (from 0)     CHANGE
require_code_owner_review                = true       (from false) CHANGE
require_last_push_approval               = true       (from false) CHANGE
dismiss_stale_reviews_on_push            = true                    PRESERVE
bypass_actors                            = []                      PRESERVE
required_status_checks                   = Diana Gate,
                                           Diana Security Gate     PRESERVE
strict_required_status_checks_policy     = true                    PRESERVE
deletion, non_fast_forward rules         present                   PRESERVE
allowed_merge_methods                    = merge, squash, rebase   PRESERVE
conditions.ref_name.include              = refs/heads/main         PRESERVE
```

This restores version `49002862` exactly, with one exception noted below.

> **B1-D11 — `required_review_thread_resolution` is deliberately left out of the frozen minimum.**
> Versions `48563849`/`49002862` had it `true`; the current version has it `false`. It is a review-
> hygiene control, not an authority control — an unresolved thread blocks merge but an *ignored* one
> does not, and it can be self-resolved by the approver. Restoring it is **recommended and
> low-risk**, but it is not part of the property M5-D20 requires and must not be counted toward it.

**The frozen merge condition.** A pull request to `main` requiring human review may merge only when
all nine hold:

| # | condition | mechanism |
|---|---|---|
| 1 | the PR author is the automation identity | **B1-INV-1** — procedural, see §11 T1 |
| 2 | at least one approving review exists | `required_approving_review_count = 1` |
| 3 | that approval is from the required CODEOWNER | `require_code_owner_review = true` + §7 |
| 4 | the approver is not the last person to push | `require_last_push_approval = true` |
| 5 | the approval applies to the current revision | `dismiss_stale_reviews_on_push` — **see §8 for the exact and weaker-than-SHA guarantee** |
| 6 | a relevant new push invalidates prior approval | same, same caveat |
| 7 | required Diana checks are satisfied | `required_status_checks`, `strict = true` |
| 8 | no bypass actor is used | `bypass_actors = []`; `current_user_can_bypass: "never"` |
| 9 | every merge method respects the rule | all three methods sit inside the same `pull_request` rule |

> **B1-D12 — Condition 1 is not mechanically enforced by any rule in this list.** Nothing in a GitHub
> ruleset requires a PR to be authored by a particular identity. B1-INV-1 is a **procedural
> invariant whose violation is detectable but not preventable**, and §11 T1 classifies it accordingly.
> Recording it as PREVENT would be false.

---

## 7. CODEOWNERS semantics — frozen

**`* @AnastasiaAurelia` is sufficient for the intended topology, and no change is needed — but only
because of B1-INV-1.**

- Every path is owned; there is no unowned path through which a change could escape code-owner review.
- `AnastasiaAurelia` has `admin`, satisfying GitHub's requirement that a CODEOWNERS entry hold write
  access; an entry naming an identity without write access is silently ignored.
- The file is at `.github/CODEOWNERS`, a recognised location, and is the only one in the tree.
- When `DIANA-AGENT` authors, the author is not a code owner, so a code-owner review is genuinely
  requested and genuinely required — **observed nine times** (§2.2).

> **B1-D13 — No CODEOWNERS change is required, or permitted, by Track B.** Adding `DIANA-AGENT` is
> forbidden (B1-D5). Adding `anastashiax` or `LiemFrans` would widen who may authorize agent output
> and is **out of scope for B1**; it is recorded in §16 as an open governance choice, not a mechanism.

> **B1-D14 — CODEOWNERS' effectiveness is conditional on B1-INV-1.** §2.3 established that the file
> is *vacuous* for a PR authored by its sole owner. The file is not a control by itself; the pair
> (file, authorship invariant) is.

---

## 8. Latest-push and stale-review semantics — exactly what GitHub guarantees

GitHub's wording, which the following answers do not exceed:

- `dismiss_stale_reviews_on_push` — *"dismiss stale pull request approvals when commits are pushed
  **that affect the diff in the pull request**."*
- `require_last_push_approval` — *"require an approval from **someone other than the last person to
  push** to a branch before a pull request can be merged."*

These are **two different predicates over two different things**, and collapsing them into "approval
binds the SHA" is wrong in a way that matters.

| Q | answer |
|---|---|
| **1. What happens after `DIANA-AGENT` pushes a new commit?** | If the push **affects the PR diff**, existing approvals are dismissed and the PR returns to unapproved. Independently, `DIANA-AGENT` becomes the last pusher. |
| **2. Is the old approval dismissed?** | Only if the pushed commits **affect the diff**. A push that leaves the diff unchanged (an empty commit; an add-then-revert pair; a rebase producing an identical tree) is **not guaranteed to dismiss anything**. |
| **3. Who must approve after the new push?** | The CODEOWNER, `AnastasiaAurelia` — and, by `require_last_push_approval`, anyone other than `DIANA-AGENT`. Both must hold at once; they are separate tests. |
| **4. May the most recent pusher approve?** | **No**, that is precisely what `require_last_push_approval` forbids. This also closes the case where the human approver pushes a small fix and then approves their own final state. |
| **5. Force-push / rewritten head?** | A force-push that changes the diff dismisses approvals like any other. A force-push preserving the diff falls under the Q2 gap. `non_fast_forward` protects `main` itself, **not the PR head branch** — the head branch is unprotected and freely rewritable by the producer. |
| **6. Merge commit vs squash vs rebase?** | Irrelevant to approval: all three are inside the same `pull_request` rule and none is a separate path. `strict_required_status_checks_policy: true` additionally requires the branch to be current with `main` before merging, so a stale head cannot merge with old check results. |

> **B1-D15 — Approval binds the DIFF, not the commit SHA. Track B must never claim SHA binding.**
> The residual is narrow and real: **a push that does not alter the pull request diff may leave a prior
> approval standing, so the merged head SHA can differ from the head SHA the human saw.** The tree is
> the same; the commit set need not be. `require_last_push_approval` does not close this — it tests
> *who* pushed last, and the standing approval is already from a different identity.
>
> B3 must **measure** this rather than assume it (B-AC-4a), because "affects the diff" is GitHub's
> phrase and its exact boundary is not documented.

---

## 9. Protected control files — including the gate that judges them

The protection model must survive changes **to the protection mechanism itself**. Measurement found
an asymmetry between the two required checks that is load-bearing and was not previously recorded in
this track.

| workflow | trigger | where the workflow definition and its scripts come from | PR-head controlled? |
|---|---|---|---|
| `diana-gate.yml` | `pull_request` | **the PR head** — `actions/checkout` of the PR ref, then `python3 diana/gate/diana-gate.py` from that checkout | **YES** |
| `diana-security-gate.yml` | `pull_request_target` | **the protected base** — default checkout resolves to the base tip; the PR head is used only as an opaque string | no |

**Consequence, stated without hand-waving:**

> **B1-D16 — `Diana Gate` is not a trustworthy control against a PR that targets it.** A pull request
> can edit `diana/gate/diana-gate.py` (for instance removing an entry from `REVIEW_PATHS`) or edit
> `diana-gate.yml` itself, and *that edited version is what runs on that same pull request*. If the
> job keeps the name `Diana Gate`, the required check reports success. The failure mode is asymmetric:
> **deleting** the workflow means the required check never reports and the PR is blocked
> (fails closed), whereas **neutering it while keeping the job name** passes (fails open).
>
> `Diana Security Gate` does not have this property, by deliberate design already documented in the
> workflow file: its definition and every script it runs come from the protected base.

So the two threats the brief raises are answered as follows.

**Could `DIANA-AGENT` propose a PR changing CODEOWNERS so a later PR can self-approve?** It could
*propose* it. Today that PR needs no approval, so **yes, today it would succeed** — Diana Gate would
escalate to `REQUIRE_HUMAN`, which maps to a *passing* check and blocks nothing. After B4 it cannot
reach `main` without `AnastasiaAurelia` approving the CODEOWNERS change itself.

**Could it change Diana Gate / `map-gate-result.py` semantics?** It can change them *for its own PR
run* (B1-D16) and land them on `main` only through the same approved-PR path.

> **B1-D17 — Track B's guarantee must not depend on Diana Gate being trustworthy, and does not.**
> The human-approval requirement is enforced by the ruleset, evaluated by GitHub, and is entirely
> independent of what any gate script decides. This is the correct structure: a control that judged
> changes to itself using a copy of itself supplied by the change would be circular. Diana Gate's
> `REVIEW_PATHS` remains valuable as **signalling** — it tells the reviewer *why* a PR is sensitive —
> and is classified as DETECT, never PREVENT.

> **B1-D18 — No per-path ruleset or second CODEOWNERS tier is adopted in B1.** `* @AnastasiaAurelia`
> already routes every path to the same single human, so a stricter tier for `.github/`,
> `diana/gate/`, `diana/security/` or the frozen specifications would name the same approver and add
> no authority. Revisit only if §16's multi-CODEOWNER question is ever answered yes.

> **B1-D19 — `Workflows: write` must be absent from the automation credential (B1-D6).** This does
> not prevent a workflow change from being *proposed* in a PR — file edits need only `Contents` — but
> it removes the agent's ability to push workflow changes on any path that does not go through review.

---

## 10. Admin authority and break-glass

Two paths must never be conflated:

| | **Normal merge path** | **Admin control-plane change** |
|---|---|---|
| what it is | satisfying §6 | editing or deleting the ruleset, then merging |
| mechanically protected | **yes** | **no** |
| available to automation | no (`DIANA-AGENT` has `admin: false`) | no |
| available to the human admin | yes | **yes, always** |
| evidence | reviews, check runs, PR timeline | ruleset version history (below) |

> **B1-D20 — The repository admin can bypass every control in this track by editing the ruleset, and
> Track B cannot prevent it.** Claiming otherwise would be false. §2.1 records that this has already
> happened once. `bypass_actors: []` and `current_user_can_bypass: "never"` mean there is no *silent*
> exemption on the merge path — the admin must **change the rule**, which is a distinct, recorded act.

**Audit evidence GitHub actually exposes** — measured, not assumed:

`GET /repos/{owner}/{repo}/rulesets/{id}/history` returns every version with `version_id`,
`updated_at`, and `actor` (`id`, `type`). `GET .../history/{version_id}` returns the **complete rule
state** at that version. Both were exercised in B1 and reconstructed the §2.1 table. Limits, stated
honestly: it records **who and when, not why**; it does **not** cover ruleset *deletion* (the history
goes with it); and `GET /user/audit-log` returns 404 for a personal account, so there is **no REST
audit-log API** here — the web security log is the only other record.

> **B1-D21 — Break-glass semantics (specified, not implemented).** A break-glass action is: an
> explicitly exceptional, human-admin-only change to the ruleset, using the **human credential only**,
> with (i) the reason recorded in a durable location before the change, (ii) the pre-change
> `version_id` captured, (iii) protection restored immediately afterwards, and (iv) the restoration
> verified by re-reading the ruleset. It is **never** a substitute for approval: it changes the rule,
> it does not satisfy it. It must never be available to an automation credential, and no automated
> path may invoke it.

> **B1-D22 — Break-glass is not implemented in B1, and Track B does not build automated recovery.**
> Any automated ruleset restoration would require giving some automated identity ruleset-write
> authority, which is the exact authority this track exists to withhold. B6 documents the manual
> procedure.

---

## 11. Threat disposition — T1–T12 frozen

Categories are **not** equivalent. **PREVENT**: GitHub mechanically refuses. **DETECT**: it happens
and leaves evidence. **POLICY**: relies on someone choosing correctly; no mechanism. **OUTSIDE**:
outside the threat model, stated so deliberately.

| # | Threat | **Today** | **After B2+B4** | Expected property (frozen) |
|---|---|---|---|---|
| **T1** | Producer approves its own PR | POLICY *(count=0; nothing to approve)* | **PREVENT** | GitHub refuses self-approval. Depends on B1-INV-1, itself **DETECT** (B1-D12) — so T1 is PREVENT *given* the invariant holds, and the invariant's violation is visible in `pull_request.user.login`. |
| **T2** | Bot/App/automation credential approval | PREVENT for `GITHUB_TOKEN`; **OUTSIDE** for the agent's own credential, which is the owner's | **PREVENT**, conditional on §5 | Workflow tokens lack `pull-requests: write` and GitHub discounts them. The agent's credential is defeated by authorship (B1-D7), not by scope. |
| **T3** | New push after approval | PREVENT *(dismiss-on-push already on)* | **PREVENT**, with §8 caveat | A push affecting the diff dismisses approvals; `require_last_push_approval` additionally bars the last pusher from being the approver. |
| **T4** | Rewritten head after approval | PREVENT when the diff changes | **PREVENT**, with §8 caveat | Same predicate. The PR head branch is **not** protected by `non_fast_forward`; only `main` is. |
| **T5** | CODEOWNERS mutation | **DETECT only** — Diana Gate flags it, `REQUIRE_HUMAN` maps to a passing check, and no approval is required | **PREVENT** | A CODEOWNERS change reaches `main` only via a PR the CODEOWNER approved. |
| **T6** | Workflow / gate mutation | **DETECT, and weaker than it appears** — Diana Gate is PR-head-controlled (B1-D16) | **PREVENT** for reaching `main` | Independent of gate trustworthiness (B1-D17). Neutering a gate still requires an approved PR to land. |
| **T7** | Alternate merge path | PREVENT | **PREVENT** | All three merge methods are inside one `pull_request` rule; `allow_auto_merge: false`. |
| **T8** | Admin changes protection | **DETECT** — and **already observed** (§2.1) | **DETECT** | Not preventable (B1-D20). Evidence: ruleset version history. Acceptable only when classified as break-glass (B1-D21). |
| **T9** | Base-branch retargeting | PREVENT for `main` | **PREVENT** | The ruleset conditions on the *destination* `refs/heads/main`; retargeting elsewhere merges into an unprotected branch, which is not a path into `main`. Anything later merging **into** `main` is governed again. |
| **T10** | Non-code-owner approval counts | **n/a** — count is 0 | **PREVENT**, conditional on B1-INV-1 | `require_code_owner_review` restricts it — **except** in the §2.3 vacuity case, which B1-INV-1 exists to exclude. Measure as B-AC-6/6a. |
| **T11** | Stale approval survives | PREVENT for diff-affecting pushes | **PREVENT**, with §8 caveat | Same predicate as T3. |
| **T12** | Human approves revision A, revision B merges | **partial** | **partial — PREVENT for diff-changing revisions, residual otherwise** | **The one threat not fully closed.** A push not affecting the diff may leave the approval standing (B1-D15). Recorded as a residual, not as a solved threat. |

---

## 12. Non-goals

- Changing any GitHub setting, CODEOWNERS entry, workflow, or credential in B1. **None was changed.**
- Granting any agent merge, deploy, or ruleset authority. Track B **removes a gap; it grants nothing.**
- Changing the `REQUIRE_HUMAN → passing check` mapping, which remains correct: a required check that
  failed on `REQUIRE_HUMAN` would deadlock the PR against itself. The merge condition comes from the
  review rule, never from a check.
- Treating any status check as human approval.
- Making Diana Gate a trust root (B1-D17).
- Implementing break-glass, automated rollback, or any B3 test.
- **Deployment.** Zero environments, no deployment workflow, no deploy authority. B1 certifies **no
  deployment mechanism**. One future invariant only: **merge approval must never be interpreted as
  deployment approval**; a deployment gate, if ever added, needs its own independent human reviewer.
- Category A conversion. M7's `diana-do approve <digest>` authorizes *starting a bounded run*. **No
  number of Category A approvals satisfies Category B** (B-AC-13).
- Certification Track (A), workflow expansion (C), distribution (D).
- Claiming M5-D20 is discharged. **It is not.**

---

## 13. B2 prerequisites and ordering — frozen

**Never reverse this order.** B0 reached the right ordering for the wrong reason; §2.3 supplies the
right one — B4 before B2 yields a configuration that looks enforced and is not.

| step | action | done when |
|---|---|---|
| **B2.1** | Provision a fine-grained token for `DIANA-AGENT`, repository-scoped, per B1-D6 | token exists; `Administration` and `Workflows` absent |
| **B2.2** | Verify least privilege | a ruleset write attempt with it is refused (B-AC-11) |
| **B2.3** | Point the agent runtime at it — **both** `gh` auth **and** git `user.name`/`user.email` (B1-D4) | `gh api user` → `DIANA-AGENT`; a test commit is attributed to `DIANA-AGENT` |
| **B2.4** | **Revoke** the owner PAT at GitHub, then inventory the runtime (B1-D9) | the old token 401s; §5 assertion recorded with its scope |
| **B2.5** | Prove a real PR can be authored, pushed and updated by `DIANA-AGENT` | one PR open, authored by `DIANA-AGENT`, checks green |
| **B2.6** | Only now begin B3, then B4 | — |

> **B1-D23 — B2.4 must revoke, not merely remove.** Deleting a credential file leaves copies valid.
> Revocation invalidates all copies at once and is the only step that converts an unprovable negative
> into a verifiable positive.

> **B1-D24 — B2 must not be started until §16's open questions Q1 and Q2 are answered.** Both are
> operational decisions the human owner must make; neither is a specification gap.

---

## 14. B3 acceptance direction — frozen, not implemented

Each must be **behavioural** and carry a falsifier: a way for the test to fail if the property does
not hold. A test that cannot fail proves nothing.

| id | property |
|---|---|
| **B-AC-1** | A `DIANA-AGENT`-authored PR with zero human approvals **cannot** merge. |
| **B-AC-2** | `DIANA-AGENT` **cannot** submit a qualifying approval for its own PR. |
| **B-AC-3** | An `AnastasiaAurelia` approval **does** satisfy the requirement for a `DIANA-AGENT`-authored PR. *(Already observed nine times, §2.2 — must still be re-established under the new credential.)* |
| **B-AC-4** | After `DIANA-AGENT` pushes a new **diff-affecting** commit, the previous approval no longer permits merge. |
| **B-AC-4a** | **Measure the §8 residual.** Determine empirically whether a push that does **not** affect the diff (empty commit; add-then-revert; identical-tree rebase) dismisses the approval. Record the answer, whatever it is; do not assume it. |
| **B-AC-5** | A fresh qualifying approval after the new push restores eligibility. |
| **B-AC-6** | Only the configured CODEOWNER satisfies the code-owner requirement: an approval from `anastashiax` or `LiemFrans` alone does **not** suffice for a `DIANA-AGENT`-authored PR. |
| **B-AC-6a** | **Confirm §2.3 directly** (B1-D3): a PR authored by `AnastasiaAurelia` under the B4 ruleset — does a non-CODEOWNER approval merge it? If yes, B1-INV-1's necessity is proven; if no, §2.3 is corrected. Either outcome is a result. |
| **B-AC-7** | Both required Diana checks remain mandatory; a PR cannot merge while either is missing or failing. |
| **B-AC-8** | Changing merge method (merge / squash / rebase) does not bypass the approval requirement. |
| **B-AC-9** | Retargeting the base cannot silently escape protection: any path into `main` is governed. |
| **B-AC-10** | A PR modifying CODEOWNERS, workflows, gate implementation or frozen specifications receives **at least** the same human protection as ordinary code. |
| **B-AC-11** | Automation authenticated only as `DIANA-AGENT` **cannot** modify repository rulesets with its normal credential. *(Supported already: `admin: false`.)* |
| **B-AC-12** | Human administrative break-glass is distinguishable from ordinary merge approval and leaves auditable evidence — a new `version_id` with `actor` in the ruleset history. |
| **B-AC-13** | A Category A runtime approval (`diana-do approve <digest>`) **cannot** satisfy Category B: it produces no review, no check, and no merge eligibility. |
| **B-AC-14** | The exact normal automation credential can **neither** approve **nor** merge its own revision without intervention by the human identity. |

> **B1-D25 — B3 must not run adversarial merge experiments against `main`.** Use a disposable
> protected target, or non-destructive PRs that are closed rather than merged. No B3 test may merge
> to `main`, and no B3 test may mutate the live ruleset except through the B4/rollback procedure.

---

## 15. Rollback — defined before enforcement, as required

> **B1-D26 — The exact pre-B4 ruleset state is `version_id` `49567930`,** retrievable in full via
> `GET /repos/AnastasiaAurelia/agent-orchestration/rulesets/22188373/history/49567930`. Its parameter
> values are recorded in §2.1 and in B0. **Rollback does not depend on this document being accurate** —
> GitHub holds the authoritative snapshot.

Requirements:

1. Rollback restores version `49567930`'s `pull_request` parameters exactly.
2. Rollback uses the **human credential only**. It is a §10 control-plane action.
3. It must be **unavailable to every automation credential** — satisfied structurally by B1-D6
   (`Administration` absent), not by policy.
4. **No automated self-rollback.** An agent that could restore the ruleset on failure would hold
   ruleset-mutation authority, which is exactly what this track withholds. B1-D22.
5. B4 must capture the new `version_id` immediately after the change, so both directions are pinned.
6. Rollback is **not** break-glass: break-glass weakens protection to get work through; rollback
   restores a known state after a failed enforcement change. Both are admin actions; they are not
   interchangeable, and the recorded reason must say which one occurred.

> **B1-D27 — Ruleset *deletion* is unrecoverable from the history API** — the history is deleted with
> the ruleset. B4 must never delete and recreate the ruleset; it must **edit in place** so that
> `22188373`'s history remains a continuous record.

---

## 16. Residual limitations

Stated plainly, because a control model that hides its own limits is not a control model.

1. **Credential custody is unprovable** (§5). Every property here is conditional on it. This is the
   weakest link and no ruleset can strengthen it.
2. **The admin can bypass everything** by editing the ruleset (B1-D20) — and **has done so** (§2.1).
   Detectable, not preventable, while one person holds both roles.
3. **B1-INV-1 is not mechanically enforced.** Nothing makes a PR be authored by `DIANA-AGENT`. If it
   is violated, the code-owner binding silently evaporates (§2.3) rather than failing loudly.
4. **Approval binds the diff, not the SHA** (B1-D15). T12 is not fully closed.
5. **One human approver.** `AnastasiaAurelia` is the sole CODEOWNER and a single point of failure for
   both availability and judgement. Widening it trades a bottleneck for a wider authorization surface;
   B1 does not decide this.
6. **`Diana Gate` is PR-head-controlled** (B1-D16) and cannot be relied on against a PR that targets
   it. Track B is structured so that it does not need to be (B1-D17).
7. **No REST audit-log API** for a personal account. Ruleset history covers rulesets; other
   administrative acts are visible only in the web security log.
8. **§2.3 is inferred, not experimentally confirmed** (B1-D3). B-AC-6a exists to settle it.
9. **Deployment is uncertified** because none exists. This is a scope statement, not an assurance.

### Open questions blocking B2

| | question | blocks |
|---|---|---|
| **Q1** | How is the `DIANA-AGENT` fine-grained token provisioned to the agent runtime, and where does it live such that inventory (§5) is meaningful? | **B2.1, B2.3** |
| **Q2** | What is the break-glass path if `AnastasiaAurelia` is unavailable — and is an unreviewable admin merge acceptable, or should `anastashiax` be added as a second CODEOWNER first? | **B2.4** (revocation is hard to undo) |
| Q3 | Should `required_review_thread_resolution` be restored to `true` (B1-D11)? | no |
| Q4 | Should `.github/`, `diana/gate/`, `diana/security/` and the frozen specifications get a distinct owner tier (B1-D18)? | no |
| Q5 | Does `require_last_push_approval` obstruct a human pushing a review fix onto an agent branch? | B3 measures it |

---

## Summary of the frozen position

`M5-D20` requires an approval automation cannot manufacture. The mechanism is
`required_approving_review_count = 1` + `require_code_owner_review = true` +
`require_last_push_approval = true`, on the already-correct foundation of `dismiss_stale_reviews_on_push`,
`bypass_actors: []`, and two strict required checks — **conditional on the producer being
`DIANA-AGENT` and the human credential being absent from the agent runtime.**

That configuration is not speculative. It ran in this repository from 2026-09-03 to 2026-09-13, nine
automation-authored pull requests were approved by the human CODEOWNER under it, and it was switched
off by the admin three minutes before the first unreviewed merge. **Track B is a restoration with the
identity discipline that was missing, not an invention.**

**M5-D20 remains undischarged. B1 freezes semantics; it changes nothing.**
