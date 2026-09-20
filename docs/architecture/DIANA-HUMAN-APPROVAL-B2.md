# Track B · Phase B2 — Automation Identity and Credential Provisioning

> **STATUS: B2 IS INCOMPLETE.** B2.0–B2.3 are **complete**. Work is **halted before B2.4**, which
> retires the human credential and requires explicit human confirmation.
> **No credential has been revoked or removed. The `AnastasiaAurelia` GitHub CLI OAuth
> authorization is untouched — not logged out, not revoked.** M5-D20 remains undischarged.

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
| **B2.1** provision `DIANA-AGENT` credential | **COMPLETE** (human-performed) — §7 |
| **B2.2** verify least privilege | **COMPLETE** — §8; the §7.2 deviation is **resolved** in §12 |
| **B2.3** repoint automation identity | **COMPLETE** — §9 |
| **B2.4** retire the human credential | **HALTED — requires explicit human confirmation.** Nothing revoked. |
| **B2.5** real `DIANA-AGENT` PR proof | **substantially complete** — PR #53, §10; closed unmerged |

## 4. Custody evidence so far

| level | status |
|---|---|
| 1. **Inventory** | **done** — §1.3; one owner credential found, its location and identity recorded |
| 2. **Positive identity** | **done** — `gh api user` under the automation credential returns `DIANA-AGENT` (§8.1) |
| 3. **Behavioural** | **done** — PR #53 is authored by `DIANA-AGENT`, its commit is attributed by GitHub to `DIANA-AGENT`, and the `PushEvent` actor is `DIANA-AGENT` (§10) |
| 4. **Revocation** | **NOT achieved** — nothing revoked; this is B2.4 and is deliberately outstanding |

**No custody assertion is made by this document.** B1-D8's assertion requires level 4.
The automation environment **still contains** a working credential authenticating as
`AnastasiaAurelia` — in the `gh` keyring, and it remains the default for any command that does not
set `GH_TOKEN`. **That is the exposure B2 exists to remove, and it is still present.** What B2.0–B2.3
established is that a sufficient replacement exists and works; not that the owner credential is gone.

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

## 7. B2.1 — the automation credential as actually provisioned

Created by the human in a browser UI; the agent never saw the secret and never requested it. Stored
at `~/.config/diana/gh-token`, mode `600`, and used **only** as
`GH_TOKEN="$(<~/.config/diana/gh-token)"` prefixed to individual commands. It was never printed,
echoed, logged, copied, or written into any file, command history entry or document.

| property | measured value |
|---|---|
| identity | **`DIANA-AGENT`** (id 324038564, User) |
| type | **classic PAT** — confirmed by GitHub's own rejection message in §8.3 |
| expiry | **2026-10-03 08:27:23 UTC** (13 days) |
| repository role | `write` — `{admin: false, maintain: false, push: true, triage: true, pull: true}` |
| transport | `GH_TOKEN` environment variable, per command |

### 7.1 Why `GH_TOKEN` rather than `gh auth login --with-token`

The human established, and it is recorded here as a constraint rather than a preference, that
`gh auth login --with-token` **rejects a minimally-scoped token** — the current GitHub CLI requires
`repo`, `read:org` and `gist` for that path. A least-privilege credential therefore **cannot** be
stored in `gh`'s own credential store; `GH_TOKEN` is the only transport that accepts it.

Two consequences were then verified empirically rather than assumed, using a **dummy token** so that
no real credential was exposed:

| question | result |
|---|---|
| Does `gh auth git-credential` honour `GH_TOKEN`? | **Yes** — it returned the dummy verbatim with `username=x-access-token`. `git push` authenticates as the environment token. |
| Does `gh api` honour `GH_TOKEN` over the keyring? | **Yes** — the dummy was rejected with `Bad credentials` rather than silently falling back to the working keyring token. |

> **B2-D3 — There is no silent fallback.** The second result matters more than the first: when the
> automation credential is present it is *used*, and a broken or expired automation token fails
> **closed** rather than quietly escalating to the owner's credential. This was measured, not assumed.

> **B2-F3 — `GH_TOKEN` is per-command, not ambient.** It is not exported in any shell profile, the
> systemd user environment, or the agent process's environment — verified across the whole process
> ancestry. **Any command that omits the prefix still runs as `AnastasiaAurelia`.** Until B2.4, the
> owner credential is the *default* and the automation credential is the exception. That is the
> correct order — never retire a credential before its replacement is proven — but it must not be
> mistaken for custody.

### 7.2 B2-E3 — the granted scopes are **not** what B2-D2 froze

B2-D2 required `public_repo` as the only scope. Measured:

```
X-Oauth-Scopes: gist, read:org, repo
```

The token carries **full `repo`**, plus `gist` and `read:org`. `public_repo` was not granted.
This is recorded as a deviation rather than accepted silently.

**Measured blast radius — currently nil beyond the intended target:**

| | |
|---|---|
| repositories reachable | **exactly one**: `AnastasiaAurelia/agent-orchestration` (public, `write`) |
| organizations | none |
| gists owned | none |

So `repo` and `public_repo` are **presently equivalent in effect** for this account: there is no
private repository and no organization for the wider scope to reach. The deviation is **latent, not
actual** — if `DIANA-AGENT` is ever added to a private repository, this token reaches it
automatically, where a `public_repo` token would not.

**What the deviation does *not* undermine:**

- **Administration remains impossible** (B2-D1): `admin: false` is a property of the *account's role*,
  and no PAT scope can raise it. GitHub confirms this directly — the ruleset reports
  `current_user_can_bypass: "never"` **for this token**.
- **`workflow` is still absent**, which is the exclusion B1-D19 actually cares about, and §8.3 proves
  it behaviourally.

> **B2-D4 — The deviation is reported, not absorbed.** Whether to regenerate the token with
> `public_repo` only is a human decision, and it should be made **before B2.4**: once the owner
> credential is revoked, regenerating the automation credential is harder. The recommendation is to
> regenerate — the safety margin costs one minute — but the current token is *sufficient* and
> *materially less privileged than the credential it replaces*.

---

## 8. B2.2 — least privilege, verified behaviourally

### 8.1 Positive identity

```
$ GH_TOKEN=… gh api user          ->  {"login":"DIANA-AGENT","id":324038564,"type":"User"}
$ gh api user   (no GH_TOKEN)     ->  AnastasiaAurelia        # still the default; see B2-F3
```

### 8.2 CAN / CANNOT

Only **read-only** probes were used for the denial tests. No administrative mutation was attempted,
per the instruction not to test destructive administration.

| operation | result |
|---|---|
| **CAN** read repository metadata | 200 |
| **CAN** list pull requests | 200 |
| **CAN** read check runs / commit status | 200 |
| **CAN** read reviews and requested reviewers | 200 |
| **CAN** read file contents | 200 |
| **CAN** fetch, and push an ordinary feature branch | §9 |
| **CAN** create a pull request and update it (body and title) | §10 |
| **CANNOT** `GET actions/permissions` | **403** |
| **CANNOT** `GET hooks` | **404** + *"needs the `admin:repo_hook` scope"* |
| **CANNOT** `GET keys` (deploy keys) | **404** |
| **CANNOT** modify rulesets | **structural** — `admin: false`; ruleset write requires admin; GitHub reports `current_user_can_bypass: "never"` for this token |

**Two probes were inconclusive and are reported as such rather than counted as passes:**

- `GET actions/secrets` returned **200 with `{"total_count": 0, "secrets": []}`**. The repository
  holds **no secrets**, so this is not a valid denial test — there is nothing to be denied. It is
  *not* evidence that the token can read secrets, and *not* evidence that it cannot.
- `GET branches/main/protection` returned **404 `Branch not protected`** — the same response the
  owner receives, because classic protection is absent. It measures the repository, not the token.
- `GET rulesets/{id}` returned **200**: ruleset *reads* are available to this token. Reading is not
  mutating, and the same response reports that it cannot bypass.

### 8.3 The exclusion that matters, proven behaviourally

A push containing a one-line edit to `.github/workflows/diana-gate.yml` was attempted with the
automation credential:

```
! [remote rejected] HEAD -> b2-scope-probe
  (refusing to allow a Personal Access Token to create or update workflow
   `.github/workflows/diana-gate.yml` without `workflow` scope)
```

The branch **did not come into existence** (`404 Branch not found`), and the local file was restored.
This establishes three things at once: the credential is a **classic PAT**; **`workflow` scope is
genuinely absent**, not merely unlisted; and **B1-D19 holds behaviourally**. The credential it
replaces *does* carry `workflow` scope, so this is a real reduction in authority.

---

## 9. B2.3 — automation git identity

```
repo-local   user.name  = DIANA-AGENT
repo-local   user.email = 324038564+DIANA-AGENT@users.noreply.github.com
global       unchanged  = Anastasia <aurelanas@gmail.com>     # the human's own default
```

Set **repo-locally on purpose.** The global identity belongs to the human and is used for their other
work; overwriting it would be a change B2 was not asked to make. The consequence is recorded honestly:
**this separation is scoped to this repository only.**

The address is **empirical, not invented** (as B2.3 requires): it is the exact address GitHub already
attributed to `DIANA-AGENT` on the PR #39 commits during the 2026-09-08/09 era, and §10 re-confirms
GitHub still resolves it to `DIANA-AGENT` today.

**Before and after, on the same branch:**

| commit | git author | GitHub attributes to |
|---|---|---|
| `39c4f4e` (B2.0, before) | `Anastasia <aurelanas@gmail.com>` | **`anastashiax`** — a *human collaborator* |
| `989e9d3` (proof, after) | `DIANA-AGENT <324038564+…>` | **`DIANA-AGENT`** |

This is B1-D4's concern demonstrated rather than argued: changing only the API credential would have
left every commit attributed to a human.

---

## 10. B2.5 (partial) — the proof pull request

**PR #53** — `[B2 PROOF — DO NOT MERGE] automation identity is DIANA-AGENT`. Documentation-only;
**closed unmerged**; proof branch deleted from the remote. The PR record is retained as evidence.

| property | value, as reported by GitHub |
|---|---|
| author | **`DIANA-AGENT`** |
| `author_association` | **`COLLABORATOR`** — not `OWNER` |
| commit `989e9d3` author / committer | **`DIANA-AGENT` / `DIANA-AGENT`** |
| `PushEvent` actor | **`DIANA-AGENT`** — the authenticated pusher, not git metadata |
| update its own PR (title, body) | **yes**, twice |
| Diana Gate | **success** |
| Diana Security Gate | **success** |
| auto-requested reviewer | **`AnastasiaAurelia`** |

### 10.1 Two findings worth keeping

> **B2-F4 — GitHub auto-requested `AnastasiaAurelia` as reviewer on a `DIANA-AGENT` PR, even with
> `require_code_owner_review: false`.** CODEOWNERS routing is *live today*; only the **requirement**
> is switched off. This corroborates B1 §7: when automation authors, the code owner is genuinely
> requested — the mechanism B4 turns on is already wired, it simply does not block.

> **B2-F5 — The zero-approval merge path was directly observed, and deliberately not used.**
> With both required checks green, GitHub reported:
>
> ```
> mergeable: MERGEABLE   mergeStateStatus: CLEAN   reviewDecision: null   reviews: 0
> ```
>
> A `DIANA-AGENT`-authored pull request was **mergeable into `main` with zero human approvals.**
> That is precisely the gap M5-D20 names and B4 closes. It is **recorded as evidence, not exploited**:
> the PR was closed unmerged. This is the live negative case for **B-AC-1**, captured before the fix.

### 10.2 A process note, recorded because it is evidence about the gate

The first two evidence blocks submitted with PR #53 were **malformed by the agent**, and Diana Gate
**failed closed** both times — first `malformed input: input fields or version are invalid` (wrong
delimiters: the block requires `<!-- DIANA:EVIDENCE … DIANA:EVIDENCE -->`, not a fenced code block),
then `dod must be an object` (`dod` and `verification` are `{present, evidence[]}` objects and
`preflight` is an array of `{id, applicable, severity, result}`). Only a correctly-formed block
passed.

That is the **correct** behaviour and is recorded as a positive result: the gate rejected
agent-produced input it could not validate rather than interpreting it charitably. It also shows the
agent did not discover the schema by reading it first — the failures did.

---

## 12. B2-E3 resolved — replacement credential

The over-scoped credential of §7.2 was **replaced by the human** with a newly generated classic PAT
before B2.4, per B2-D4's recommendation. The agent never saw either secret. The file at
`~/.config/diana/gh-token` (mode `600`) now holds the replacement; the previous value was overwritten.

| property | retired token | **replacement** |
|---|---|---|
| identity | `DIANA-AGENT` | **`DIANA-AGENT`** (324038564) |
| `X-OAuth-Scopes` | `gist, read:org, repo` | **`public_repo`** — the only scope |
| expiry | 2026-10-03 08:27:23 UTC | **2026-10-20 09:14:30 UTC** |

The differing expiry independently confirms these are distinct credentials.

### 12.1 Nine-point verification — all pass

| # | property | result |
|---|---|---|
| 1 | `gh api user` reports `DIANA-AGENT` | **pass** — id 324038564 |
| 2 | scope is `public_repo` only | **pass** — `X-Oauth-Scopes: public_repo` |
| 3 | metadata / read operations | **pass** — repo, file contents, pull requests |
| 4 | feature-branch push | **pass** — `governance/b2-e3-proof`, commit `8abb835` attributed to `DIANA-AGENT` |
| 5 | PR creation and update | **pass** — PR **#54**, author `DIANA-AGENT`, `author_association: COLLABORATOR`; title PATCHed and reverted |
| 6 | checks and reviews readable | **pass** — reviews, requested reviewers, commit status |
| 7 | workflow modification **refused** | **pass** — *"refusing to allow a Personal Access Token to create or update workflow … without `workflow` scope"*; branch never created (404) |
| 8 | administration **unavailable** | **pass** — `actions/permissions` 403; `hooks` 404; `keys` 404; `admin: false`; ruleset reports `current_user_can_bypass: "never"`; a `PUT` to the ruleset was **refused 404** |
| 9 | ruleset / CODEOWNERS / workflows unchanged | **pass** — see §12.2 |

Both required checks ran green on PR #54 (**Diana Gate: success**, **Diana Security Gate: success**),
so the replacement credential exercises the full automation path end to end.

### 12.2 Invariants after the refused administration write

A ruleset `PUT` was attempted **deliberately, with the ruleset's existing name as the only field**, so
that a successful call would have been a no-op. It was refused. Verified afterwards:

```
ruleset versions : 3, latest 49567930 @ 2026-09-14T02:55:45+07:00   (unchanged)
pull_request rule: approvals=0  code_owner=false  last_push=false  dismiss=true  bypass=0
CODEOWNERS       : * @AnastasiaAurelia   (remote and local, byte-identical to main)
workflows        : byte-identical to main
```

PR #54 was **closed unmerged** and its branch deleted. `mergeStateStatus: CLEAN` with `reviews: 0` was
observed once more and again **not** acted on — B2-F5 reconfirmed on the replacement credential.

### 12.3 Superseded token — human-confirmed retired

**Status: retired.** The human deleted the superseded classic PAT (the one expiring
**2026-10-03 08:27:23 UTC**) from the `DIANA-AGENT` account's token list.

Re-verified immediately afterwards that the replacement is unaffected:

```
gh api user            ->  DIANA-AGENT
X-Oauth-Scopes         ->  public_repo
token expiration       ->  2026-10-20 09:14:30 UTC
```

Identity, scope and expiry are all unchanged, confirming the surviving credential is the intended
replacement and that the correct one of the two was deleted.

> **B2-F6 — This retirement is a human attestation, not an agent-verified fact, and it cannot be
> re-tested.** The superseded secret was overwritten in `~/.config/diana/gh-token` and never copied,
> so the agent cannot attempt authentication with it to observe a failure. GitHub exposes **no REST
> endpoint** for a user to list or delete their own classic PATs (`GET /user/tokens` → 404), so the
> agent can neither enumerate the account's remaining tokens nor confirm the deletion independently.
>
> **The evidence available is therefore: (i) the human's confirmation that the token was deleted, and
> (ii) the agent's verification that the replacement still authenticates with the intended scope.**
> Together these are strong practical grounds and **not** a proof of revocation. This is the same
> class of limitation B1-D8 names for custody generally, recorded here rather than rounded up to
> certainty.
>
> The residual risk it leaves is bounded and worth stating precisely: had the deletion not occurred,
> the exposure would be a `repo`-scoped `DIANA-AGENT` credential, valid until 2026-10-03, unable to
> administer the repository (`admin: false`, B2-D1) but able to push, open pull requests, and reach
> any private repository `DIANA-AGENT` might later be added to.

### 12.4 Original statement of the limitation (retained)

> The retired token's value was overwritten in `~/.config/diana/gh-token` and was never copied
> elsewhere, so **the agent cannot test whether it still authenticates.** GitHub exposes **no REST
> endpoint** for a user to list or delete their own classic PATs (`GET /user/tokens` → 404), so the
> agent can neither enumerate nor revoke it.
>
> **Deleting it is a human UI action on the `DIANA-AGENT` account, and its completion is verifiable
> only there.** Until then a `repo`-scoped `DIANA-AGENT` credential remains valid until
> 2026-10-03 — orphaned rather than in use, but live. Its authority ceiling is unchanged
> (`admin: false`, B2-D1), so it cannot administer the repository; but it *can* push, open pull
> requests, and reach any private repository `DIANA-AGENT` is later added to.

---

## 11. Readiness for B3

**Not ready — B2.4 is outstanding, and it is the step that actually establishes custody.**

Everything B2.4 depends on is now proven: the replacement credential exists, authenticates as
`DIANA-AGENT`, performs every operation the automation needs, is provably unable to administer the
repository or touch workflows, and produces correctly attributed commits and pull requests. What has
**not** happened is the removal of the owner credential from agent reach — so today the agent can
still act as `AnastasiaAurelia` by simply not setting `GH_TOKEN` (B2-F3).

Two decisions belong to the human before B2.4:

1. ~~Regenerate the automation token with `public_repo` only, and delete the superseded one~~ —
   **done**, §12. Both parts are complete; the deletion is a human attestation (B2-F6).
2. **How to retire the owner credential** (B2-E1). It is a GitHub CLI OAuth authorization, not a PAT;
   revoking it disables `gh` for that account **everywhere**, not only on this machine. `gh auth
   logout` is local deletion and does **not** satisfy B1-D9/D23.
