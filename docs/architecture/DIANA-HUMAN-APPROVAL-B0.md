# Track B · Phase B0 — Empirical Baseline (READ-ONLY OBSERVATION)

Status: **measurement only.** No GitHub setting was changed. No ruleset, branch protection,
CODEOWNERS entry, workflow, reviewer or merge was modified, and no bypass was tested by mutation.
Every value below was read from the live GitHub API on the date of this document.

Scope: **Category B only** — whether a resulting repository revision may merge. Category A, Diana's
runtime approval to *start a bounded run*, is implemented by M7 and is not this track's subject. No
number of Category A approvals satisfies Category B.

Branch: `governance/mechanical-human-approval`, from accepted main `af8740e`.

---

## 1. Live protection baseline

**Classic branch protection: absent.** `GET /repos/{r}/branches/main/protection` returns
`404 Branch not protected`. All protection comes from a repository **ruleset**.

**Ruleset `diana-main-protection`** — id `22188373`, `enforcement: active`, `source_type: Repository`,
targeting exactly `refs/heads/main`. It is the only ruleset, and there are no others for any target.

```
bypass_actors: []            ← empty; no actor is exempt, admins included

RULE deletion
RULE non_fast_forward
RULE pull_request
     required_approving_review_count          = 0        ← THE GAP
     require_code_owner_review                = false    ← THE GAP
     require_last_push_approval               = false
     dismiss_stale_reviews_on_push            = true     ← already correct
     required_reviewers                       = []
     required_review_thread_resolution        = false
     require_extra_approval_for_unattributed_changes = false
     allowed_merge_methods                    = [merge, squash, rebase]
RULE required_status_checks
     strict_required_status_checks_policy     = true
     required_status_checks = [ "Diana Gate", "Diana Security Gate" ]  (integration 15368)
```

**Two corrections to the previously recorded baseline.** The accepted M4 audit recorded three values
and did not record the rest; measuring the live ruleset shows:

- `dismiss_stale_reviews_on_push` is **`true`**, not unset. A new push already invalidates prior
  approvals. One of the five properties Track B needs is therefore *already in place* and was not
  previously credited.
- `bypass_actors` is **empty**. Under rulesets — unlike classic protection's `enforce_admins` — an
  admin is subject to the rule unless explicitly listed as a bypass actor. Verified: querying
  `GET /repos/{r}/rules/branches/main` as the **admin** identity returns all four rules, meaning they
  apply to that caller.

The three values the M4 audit recorded remain accurate: approving-review count `0`, code-owner review
`false`, last-push approval `false`.

**Environments: none.** `GET /repos/{r}/environments` returns `total_count: 0`. There are no
deployment gates because there is no deployment.

---

## 2. Identity model

**The repository is not single-owner.** This is the finding that changes the conclusion.

| Identity | Type | Repo role | Can push | Can approve | Can merge |
|---|---|---|---|---|---|
| `AnastasiaAurelia` | User | **admin** (owner) | yes | yes — **except on PRs she authored** | yes |
| `LiemFrans` | User | write | yes | yes | yes, once rules are satisfied |
| `anastashiax` | User | write | yes | yes | yes, once rules are satisfied |
| `DIANA-AGENT` | User | write | yes | yes | yes, once rules are satisfied |

`.github/CODEOWNERS` contains exactly `* @AnastasiaAurelia`. The owner is a **User account, not an
organization**, so there are no organization rulesets and **no teams** — mechanism H ("separate
human-only GitHub team") is unavailable without changing the ownership model.

`DIANA-AGENT` is a real GitHub user account with write access, created 2026-09-02. It exists and is
**not currently used**.

### The credential actually in use

Local `gh` authenticates as **`AnastasiaAurelia`** — the admin/owner — with scopes
`gist, read:org, repo, workflow`.

So today the agent operates **with the owner's credential**, and the collapse is total:

```
  producer  = AnastasiaAurelia
  approver  = AnastasiaAurelia
  merger    = AnastasiaAurelia
```

Measured across every pull request in the M5–M7 sequence:

| PR | author | mergedBy | reviews submitted |
|---|---|---|---|
| #49 | AnastasiaAurelia | AnastasiaAurelia | **0** |
| #50 | AnastasiaAurelia | AnastasiaAurelia | **0** |
| #51 | AnastasiaAurelia | AnastasiaAurelia | **0** |
| #52 | AnastasiaAurelia | AnastasiaAurelia | **0** |

GraphQL on #52 confirms `viewerDidAuthor: true`, `reviewDecision: null`, `latestReviews: []`. **No
review has ever been submitted on this repository.** Four merges to a protected branch have occurred
with zero human approvals, entirely legitimately, because zero is what the rule requires.

### Can automation submit a qualifying approval?

Three separate answers, and the distinction matters:

- **GitHub Actions (`GITHUB_TOKEN`)** — **no.** Both workflows declare `permissions: contents: read`
  (the security gate adds `pull-requests: read`). Neither has `pull-requests: write`, so the workflow
  token cannot submit a review at all. GitHub additionally does not count `GITHUB_TOKEN` reviews
  toward required approvals.
- **A GitHub App** — none is installed for this purpose; `integration_id 15368` on the required checks
  is GitHub Actions itself, publishing check results, not an approver.
- **The agent's own credential** — **this is the live exposure.** It is the owner's PAT with `repo`
  scope. It cannot approve a PR it authored (GitHub forbids self-approval), but it could approve any
  PR authored by someone else, and it can merge.

---

## 3. Threat analysis

Legend: **M** prevented mechanically · **D** detected only · **P** policy only · **O** outside the
current threat model.

| # | Threat | Today | After the recommended change |
|---|---|---|---|
| **T1** | Agent opens a PR and approves its own PR | **M** — GitHub refuses self-approval (`viewerDidAuthor: true`) | **M** |
| **T2** | Agent uses an App/bot credential to approve | **M** for `GITHUB_TOKEN` (no `pull-requests: write`; not counted). **O** for the agent's own PAT — it holds the owner's token | **M**, *only if* the agent's credential is `DIANA-AGENT` and never the owner's |
| **T3** | Agent pushes a new commit after human approval | **M** — `dismiss_stale_reviews_on_push: true` already dismisses it | **M** |
| **T4** | Agent rewrites the PR head after approval | **M** — same dismissal, plus `non_fast_forward` on main | **M** |
| **T5** | Agent edits `.github/CODEOWNERS` | **D** — Diana Gate lists it as review-sensitive and escalates to `REQUIRE_HUMAN`; but that verdict maps to a passing check, and reaching main needs only a PR today | **M** — the edit lands on main only through an approved PR |
| **T6** | Agent edits workflow or rules-related files | **D** — `.github/workflows/` is review-sensitive to the Gate; the local token *has* `workflow` scope | **M** for merge to main; **O** for the ruleset itself (§5) |
| **T7** | Merge method that bypasses review | **M** — all three methods are inside the same `pull_request` rule | **M** |
| **T8** | Admin/owner bypasses protection | **D/P** — `bypass_actors` is empty so there is no silent bypass, but an admin can edit or delete the ruleset (§5) | unchanged — **D/P** |
| **T9** | Agent changes the base branch | **M** for main — the ruleset targets `refs/heads/main`, so any merge into main is governed regardless of source | **M** |
| **T10** | Approval from an unrelated non-code-owner counts | **n/a** — count is 0, so no approval counts for anything | **M** — `require_code_owner_review` restricts it to CODEOWNERS |
| **T11** | Stale approval survives authority-relevant changes | **M** — already dismissed on push | **M** |
| **T12** | Human approves SHA *x*, automation merges SHA *y* | **M** — dismissal on push plus `strict_required_status_checks_policy: true` (branch must be current) | **M** |

**The honest summary of today:** most of the twelve are already handled. The gap is narrow and
specific — **`required_approving_review_count = 0` means no human approval is required at all**, and
the agent holds the owner's credential.

---

## 4. Mechanism comparison

| | Mechanism | Guarantees | Who satisfies it | Agent self-satisfiable? | Invalidated by push? | Verdict |
|---|---|---|---|---|---|---|
| **A** | `required_approving_review_count ≥ 1` | At least one approval from a non-author | Any collaborator except the author | **No**, if the agent is not also the approver identity | Yes (dismissal already on) | **Necessary** |
| **B** | `require_code_owner_review: true` | Approval must come from CODEOWNERS | `AnastasiaAurelia` only | **No**, if the agent authors as `DIANA-AGENT` | Yes | **Necessary** |
| **C** | Dismiss stale approvals on push | Approval tracks the head | — | — | — | **Already enabled** |
| **D** | `require_last_push_approval: true` | The last pusher cannot be the approver | A different identity from the pusher | No | — | **Recommended**; closes the case where the approver pushes a "small fix" then approves |
| **E** | Restrict bypass actors | No exempt actor | — | — | — | **Already correct** (`bypass_actors: []`) |
| **F** | Rulesets over classic protection | Admins are not implicitly exempt | — | — | — | **Already in use** |
| **G** | Protected environments | Independent deploy-time approval | Named reviewers | No | — | **Not applicable today** — zero environments, no deploy authority |
| **H** | Separate human-only team | Approval bound to a team | A team | No | — | **Unavailable** — owner is a User account; no teams exist |
| **I** | Signed external approval artifact | Cryptographic binding to a revision | A key holder | Depends on key custody | By construction | **Not recommended** — moves trust to key custody without adding a property A+B+D lack |
| **J** | Combination | — | — | — | — | **A + B + D**, on the existing C/E/F foundation |

**Mechanism I deserves its refusal stated plainly.** A signed approval artifact would only be stronger
than platform review if the signing key were outside the agent's reach — which is the *same* property
as the agent not holding the approver's credential. It adds a custody problem and no new guarantee.

---

## 5. Can this topology discharge M5-D20?

**Yes — and the blocker is a credential-separation problem, not a rule problem.**

The roadmap anticipated that a single-owner repository might be unable to produce a
non-self-satisfiable approval. **That premise is false here:** the repository has four collaborators,
two of them additional humans, and a dedicated agent account.

But enabling A + B *today* would **deadlock the repository**, and it is worth being exact about why:

1. GitHub forbids approving your own pull request.
2. Every PR is authored by `AnastasiaAurelia`, because the agent uses her credential.
3. The only CODEOWNER is `AnastasiaAurelia`.
4. So the only permitted approver would be the one identity that is barred from approving.

**The required change is therefore ordered, and the order matters:**

```
  FIRST   the agent authors as DIANA-AGENT, never as the owner
          (a DIANA-AGENT credential; the owner's PAT withdrawn from the agent)
  THEN    required_approving_review_count = 1
          require_code_owner_review = true
          require_last_push_approval = true
```

Reversed, the repository becomes unmergeable by anyone. This is the stop condition the roadmap named,
reached for a different reason than expected — not "no second human exists", but "the agent is wearing
the human's identity".

After that change the chain holds end to end:

```
  DIANA-AGENT produces the revision
        → Diana Gate / Security Gate decide (REQUIRE_HUMAN where applicable)
        → DIANA-AGENT cannot approve: it authored the PR, and it is not a CODEOWNER
        → AnastasiaAurelia, a human and the CODEOWNER, approves this exact head
        → any further DIANA-AGENT push dismisses that approval
        → only then can merge proceed
```

**The property that must hold, restated as the thing to test in B3:** the agent, using every
credential it normally possesses, cannot produce a qualifying approval. That is true only while the
agent's credential is `DIANA-AGENT` and the owner's PAT is not available to it. **Credential custody
becomes the load-bearing control**, and no ruleset can enforce it — which must be said out loud rather
than assumed away.

---

## 6. Residual bypasses, stated honestly

- **The admin can edit or delete the ruleset.** `bypass_actors: []` prevents a silent exemption, but
  an admin can change the rule and then merge. This is **detectable** (rulesets have an audit history)
  and **not preventable** while the same person owns the repository. Any mechanism that claimed
  otherwise would be false.
- **The agent's token carries `workflow` scope.** It can modify workflow files; reaching main still
  requires an approved PR after the change, but the scope is broader than the task needs.
- **`REQUIRE_HUMAN` remains advisory in itself.** Track B does not change the
  `REQUIRE_HUMAN → passing check` mapping, which is correct: a check that failed would deadlock the PR
  against itself. The merge condition comes from the review rule, not from the check.
- **Category A is untouched.** Diana's runtime approval neither gains nor grants merge authority.

---

## 7. Deployment implications

**None today.** There are zero environments, no deployment workflow, and Diana holds no deploy
authority. Mechanism G is therefore documented and **not implemented** — implementing an environment
gate for a deployment that does not exist would be ceremony. If deployment authority is ever added, it
needs its own independent reviewer gate, and Category B approval for merge must not be read as
approval to deploy.

---

## 8. Unresolved questions for B1

1. **Credential custody.** How is the `DIANA-AGENT` credential provisioned to the agent, and how is
   the owner's PAT positively withheld? This is the real control and it lives outside GitHub settings.
2. **Bootstrap and break-glass.** If the only CODEOWNER is unavailable, how does an urgent fix land?
   Any answer must be explicit and auditable, never a silent admin merge.
3. **Should `LiemFrans` / `anastashiax` become CODEOWNERS?** It would remove the single-approver
   dependency, at the cost of widening who can authorize agent-produced change.
4. **Does `require_last_push_approval` interact badly** with a human pushing a review fix onto an
   agent's branch?
5. **Which paths deserve a stricter rule than main's default** — `.github/`, `diana/gate/`,
   `diana/security/`, the frozen specifications?
6. **How is the agent's identity asserted in commits**, given git author and GitHub actor differ today
   (commits are authored `Anastasia <aurelanas@gmail.com>` while the API actor is the owner account)?

---

## 9. Non-goals

- Changing any GitHub setting in B0. **None was changed.**
- Granting any agent merge or deploy authority. Track B *removes* a gap; it grants nothing.
- Changing the `REQUIRE_HUMAN → passing check` mapping.
- Treating a status check as human approval.
- Solving security evidence certification — that is Track A and is unrelated.
- Expanding workflow classes or packaging — Tracks C and D.
- Claiming M5-D20 is discharged. **It is not.** B0 is a measurement; discharge requires B1–B6.

---

## 10. Recommendation

**Adopt A + B + D on the existing C/E/F foundation, but only after credential separation.**

Ordered:

| Step | Action | Kind |
|---|---|---|
| **B1** | Freeze the approval semantics and the credential-separation requirement | specification |
| **B2** | Provision the `DIANA-AGENT` credential; withdraw the owner's PAT from agent use | **operational, outside GitHub settings** |
| **B3** | Adversarial acceptance against a non-`main` test target where feasible | verification |
| **B4** | Set `required_approving_review_count = 1`, `require_code_owner_review = true`, `require_last_push_approval = true` | settings change |
| **B5** | Prove the agent cannot self-satisfy, using every credential it holds | falsification |
| **B6** | Document break-glass and recovery | documentation |

**B2 before B4 is not a preference.** Reversing them makes the repository unmergeable.
