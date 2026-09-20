# Track B · Phase B2 — Automation Identity and Credential Provisioning

> **STATUS: B2 IS INCOMPLETE.** This document records **B2.0 only** — the pre-mutation inventory.
> Work is **halted at B2.1**, which requires a human to create a credential in a browser UI.
> **No credential has been created, changed, revoked or removed. No identity has been repointed.**
> B2.2, B2.3, B2.4 and B2.5 have **not** run. M5-D20 remains undischarged.

Base: B1 (`9b09e8f`), branch `governance/mechanical-human-approval`.
B0 and B1 are **not** edited. Findings that correct them are recorded here, in the B0→B1 pattern.

---

## 1. B2.0 — pre-mutation inventory

Measured read-only. **No secret value was printed, logged, or written to any file.** Recorded only:
presence, identity, location/type, and observable scope.

### 1.1 Automation GitHub API identity

| property | value |
|---|---|
| authenticated account | **`AnastasiaAurelia`** (id 149037636, User) |
| repository role | **admin** (owner, sole CODEOWNER) |
| credential type | **`gho_…` — a GitHub CLI *OAuth* token**, not a PAT |
| storage | **system keyring** (`gh` secure storage) — `hosts.yml` holds no `oauth_token` field |
| observable scopes | `gist`, `read:org`, **`repo`**, **`workflow`** |
| expiry | none reported |

### 1.2 git identity and how pushes authenticate

| property | value |
|---|---|
| `user.name` / `user.email` | `Anastasia` / `aurelanas@gmail.com` — **set globally**, not per-repo |
| GitHub attribution of that email | **`anastashiax`** — a *human write collaborator*, not the owner |
| credential helper | `credential.https://github.com.helper = !/usr/bin/gh auth git-credential` |
| consequence | **`git push` authenticates with the same `gh` token.** Repointing `gh` repoints push. |
| remote | `https://github.com/AnastasiaAurelia/agent-orchestration.git` (no embedded credential) |
| last 5 commits | authored `Anastasia <aurelanas@gmail.com>` → attributed to **`anastashiax`** |

This confirms B1 §2.4's three-way identity split, with the git layer pointing at `anastashiax`.

### 1.3 Credential surfaces inspected

| surface | result |
|---|---|
| `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`, `GITHUB_ENTERPRISE_TOKEN`, `GH_HOST`, `GH_CONFIG_DIR` | **all absent** |
| any `GH_*` / `GITHUB_*` / `*TOKEN=` environment variable | none GitHub-related |
| `~/.config/gh/hosts.yml` | one account, `AnastasiaAurelia`; **no token on disk** |
| `~/.config/gh/config.yml` | no credential material |
| `~/.git-credentials`, `~/.netrc`, `~/.config/hub`, `~/.gh_token`, `~/.github_token` | **all absent** |
| project tree (`agent-orchestration`, excluding `.git`) | **no token-shaped string**; no `.env`, `.pem`, `*secret*`, `*credentials*` file |
| `~/.hermes/**` (agent runtime home) | **no full-length token-shaped string.** An initial prefix-only scan flagged `~/.hermes/.env`; re-checked against the full token grammar it holds **none**, and its only GitHub reference is a **commented-out** `# GITHUB_TOKEN` template at line 464. Zero of its 13 live keys mention GH/GITHUB/TOKEN/PAT/CRED. |

> **B2-F1 — Exactly one GitHub credential is reachable by the agent: the `gh` keyring entry for
> `AnastasiaAurelia`.** It backs both `gh api` and `git push`. There is no second credential, no
> environment-variable token, and no token on disk. The custody problem is therefore **narrow and
> single-valued** — which is good news for B2.4, and makes the inventory tractable.

> **B2-F2 — The keyring is shared with the OS user, and that is the real custody boundary.**
> The agent runs as OS user `anas` and can reach that user's keyring. Removing `AnastasiaAurelia`
> from `gh` at B2.4 is necessary but **not permanent**: if the human later runs `gh auth login` as
> `AnastasiaAurelia` on this same OS user, the owner credential re-enters agent reach silently. The
> Q1 decision already requires human approval to occur in a **separate human-only environment**;
> B2-F2 records that this is not a preference but the load-bearing condition, and that nothing on
> this machine enforces it.

---

## 2. Two frozen B1 assumptions are falsified

B2's failure conditions require stopping rather than papering over. Both are recorded here; **B1 is
not edited in place.**

### 2.1 B2-E1 — There is no owner *PAT*. The credential is a GitHub CLI OAuth token.

B1 §2.4, B1-D9 and B1-D23 — and B2.4's own brief — all say "the owner **PAT**". Measurement shows a
**`gho_` OAuth token** issued to the *GitHub CLI* OAuth application, held in the system keyring.

Consequences for the retirement step:

- It is **not** listed under *Settings → Developer settings → Personal access tokens*, so the
  revocation procedure B1 anticipated does not apply to it.
- Its server-side revocation surface is the **GitHub CLI application authorization** for the
  `AnastasiaAurelia` account (*Settings → Applications → Authorized OAuth Apps → GitHub CLI*).
- **`gh auth logout` is a local deletion, not a revocation.** It satisfies neither B1-D9 nor B1-D23.
- Revoking that authorization invalidates `gh` for `AnastasiaAurelia` **everywhere that account uses
  `gh`**, not only on this machine. That is a human operational decision, not an agent decision.

**B1-D9/D23's *principle* is unchanged and still binding** — retire at the server, not merely on
disk. Only the mechanism differs, and B2.4 must use the OAuth-app revocation path.

### 2.2 B2-E2 — B1-D6's fine-grained, repository-scoped token is **not achievable** here.

B1-D6 froze: *"a fine-grained token limited to this repository… A classic PAT with blanket `repo`
scope is **not acceptable**."* GitHub's documented model makes this impossible:

> *"Each token is limited to access resources owned by a single user or organization."*
> Listed explicitly among what fine-grained tokens **cannot** do: *"Using fine-grained personal
> access token to contribute to repositories where the user is an outside or repository
> collaborator."*

`DIANA-AGENT` is a **repository collaborator** on a repository owned by a **different personal
account**. A fine-grained PAT created by `DIANA-AGENT` can only target repositories owned by
`DIANA-AGENT`. **It cannot reach `AnastasiaAurelia/agent-orchestration` at all.** This is a
consequence of the repository being owned by a personal account rather than an organization —
the same root cause that made mechanism H (teams) unavailable in B0.

**The substance of B1-D6 survives; the mechanism changes.** B1-D6's real requirement is *no
Administration authority*, and that is achieved here more robustly than by scope labels:

> **B2-D1 — The role is the ceiling, not the token scope.** `DIANA-AGENT` holds
> `{admin: false, maintain: false, push: true, triage: true, pull: true}` on this repository. **No
> classic-PAT scope can raise it.** A `repo`-scoped classic PAT held by `DIANA-AGENT` still cannot
> modify rulesets, branch protection, secrets or repository settings, because the *account* cannot.
> Administration is excluded structurally by the collaborator role, and would remain excluded even
> if the token were over-scoped.

> **B2-D2 — Corrected automation credential design: a classic PAT owned by `DIANA-AGENT` with
> `public_repo` as its only scope.** The repository is **public** (`private: false`), so
> `public_repo` — write access to public repositories only — is sufficient for branch push, PR
> creation and update, and status/check/review reads. It is **strictly narrower than today's
> credential** on every axis:
>
> | | today (`AnastasiaAurelia`, `gho_`) | B2 target (`DIANA-AGENT`, classic PAT) |
> |---|---|---|
> | identity | **owner / admin / sole CODEOWNER** | write collaborator |
> | `repo` (incl. private + org repos) | **yes** | no |
> | `public_repo` | implied by `repo` | **yes — the only scope** |
> | `workflow` (push workflow changes) | **yes** | **no** |
> | `gist`, `read:org` | yes | no |
> | administration / rulesets | **yes (admin role)** | **no (role ceiling, B2-D1)** |
>
> This satisfies B1-D19 (no `Workflows: write`) and B1-D6's intent, and it is a genuine reduction —
> not a relabelling. **B2 does not accept a `repo`-scoped token**: `public_repo` must be verified
> sufficient in B2.2, and if it is not, the shortfall must be reported rather than widened silently.

### 2.3 Assumptions that survived unchanged

- `DIANA-AGENT` exists, is a User account (id 324038564, created 2026-09-02), and holds write access.
- Its commit identity is **empirically established, not invented**:
  `DIANA-AGENT <324038564+DIANA-AGENT@users.noreply.github.com>`, confirmed by GitHub's own
  attribution of the commits on PR #39 to `DIANA-AGENT`.
- Repointing `gh` also repoints `git push` (§1.2), so one lever moves both API and push identity.
- The owner's role, CODEOWNERS, the ruleset, and both workflows are untouched.

---

## 3. Status of each B2 step

| step | state |
|---|---|
| **B2.0** pre-mutation inventory | **COMPLETE** — §1 |
| **B2.1** provision `DIANA-AGENT` credential | **HALTED — requires human UI action.** Nothing created. |
| **B2.2** verify least privilege | not started |
| **B2.3** repoint automation identity | not started |
| **B2.4** retire the human credential | not started — requires explicit human confirmation |
| **B2.5** real `DIANA-AGENT` PR proof | not started |

## 4. Custody evidence so far

| level | status |
|---|---|
| 1. **Inventory** | **done** — §1.3; one credential found, its location and identity recorded |
| 2. **Positive identity** | **not achieved** — automation still authenticates as `AnastasiaAurelia` |
| 3. **Behavioural** | **not achieved** — no `DIANA-AGENT`-authored PR yet |
| 4. **Revocation** | **not achieved** — nothing revoked |

**No custody assertion is made by this document.** B1-D8's assertion may be stated only after
levels 2–4 hold. At present the automation environment **does** contain a credential authenticating
as `AnastasiaAurelia`, which is the exposure B2 exists to remove.

## 5. Recovery route, established before any mutation

Required by the critical safety rule — recorded **before** anything changes:

1. The human retains browser/control-plane access to `AnastasiaAurelia` independently of this
   machine's `gh` state. Nothing in B2 touches the web session.
2. The `AnastasiaAurelia` `gh` credential is **untouched** and remains active until B2.4, which
   requires explicit human confirmation.
3. If a `DIANA-AGENT` credential proves insufficient, the correct action is to **stop and report** —
   never to widen scope silently, and never to proceed to B2.4.
4. B2.4 is reversible in the sense that matters: the human can re-authorize GitHub CLI for
   `AnastasiaAurelia` at any time from a browser. Loss of `gh` on this machine is **not** loss of
   repository control.
5. Per the Q2 decision: if `AnastasiaAurelia` is unavailable, the repository may become temporarily
   unmergeable. That is **accepted fail-closed availability loss, not a security failure** — and it
   is not caused by B2, which changes no merge rule.

## 6. Residual limitations at B2.0

1. **B2-F2** — the keyring boundary is the OS user, not the process. Custody depends on the human
   never authenticating as the owner in this environment again; nothing here enforces that.
2. **B2-E2** — no fine-grained token is possible while the repository is owned by a personal
   account. `public_repo` is the narrowest achievable scope, and the real guarantee comes from the
   **role ceiling** (B2-D1), which is stronger but less legible than a scope list.
3. **B2-E1** — retiring the owner credential means revoking a GitHub CLI authorization with effects
   beyond this machine.
4. Absence of a credential in the surfaces of §1.3 is **not** proof of absence anywhere.

## 7. Readiness for B3

**Not ready.** B3 cannot begin until B2.1–B2.5 complete. B2 is blocked on one human action.
