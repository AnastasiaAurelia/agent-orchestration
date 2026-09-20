# Track B — Mechanical Human Approval (M5-D20) (ROADMAP — SUPERSEDED)

> **SUPERSEDED. Track B is complete and M5-D20 is DISCHARGED.**
> This document is the **original plan**, retained unedited below as the record of what was intended
> before any measurement. Several of its assumptions were later falsified by evidence — notably the
> single-owner premise in "An honest constraint to confront" and the stop condition at the end.
>
> For what was actually measured and concluded, read the phase documents:
> [B0](DIANA-HUMAN-APPROVAL-B0.md) baseline · [B1](DIANA-HUMAN-APPROVAL-B1.md) frozen semantics ·
> [B2](DIANA-HUMAN-APPROVAL-B2.md) credential custody · [B3](DIANA-HUMAN-APPROVAL-B3.md) adversarial
> baseline · [B4](DIANA-HUMAN-APPROVAL-B4.md) enforcement · [B5](DIANA-HUMAN-APPROVAL-B5.md)
> acceptance and the discharge determination.

Status: **planning only.** No repository rule, ruleset, branch protection or environment is changed by
this document. *(As written. Superseded — see the notice above.)*

---

## The problem

`REQUIRE_HUMAN` is today a **semantic verdict, not a mechanical merge condition.**

Measured, and recorded in the accepted M4 audit:

| Parameter | Value |
|---|---|
| `required_approving_review_count` | **0** |
| `require_code_owner_review` | **false** |
| `require_last_push_approval` | **false** |

`.github/CODEOWNERS` contains an owner entry, but with code-owner review disabled it has **no
merge-blocking effect**.

And `map-gate-result.py` maps gate exit 2 (`REQUIRE_HUMAN`) to a **passing** check. That mapping is
correct and deliberate — a required status check that failed on `REQUIRE_HUMAN` would deadlock the
pull request against its own required check — and the run log says merge "stays blocked by the
independent required human/code-owner review rule."

**No such rule is configured.** So the carried requirement stands:

> Before any future milestone grants autonomous PR-merge authority, `REQUIRE_HUMAN` needs an
> independently verifiable human-approval mechanism that automation cannot self-satisfy.

The distinction that matters is **self-satisfiable versus not**. A required status check is satisfied
by automation and therefore cannot represent human approval.

---

## The property to prove

```
  agent produces changes
        → gate decides REQUIRE_HUMAN
        → automation cannot manufacture a qualifying approval
        → an authorized human approves THIS exact revision
        → approval invalidates on relevant new commits
        → merge (or deploy) becomes possible
```

Every arrow is a separate obligation. The fourth is the one most often skipped: an approval that
survives a subsequent push is an approval of something the human never saw.

---

## The two categories, which never convert

| | |
|---|---|
| **Category A — Diana runtime approval** | "Start this exact bounded run." What `diana-do approve <digest>` asks for. Already implemented, already binds an immutable proposal identity. |
| **Category B — repository / deployment approval** | "This exact resulting revision may merge or deploy." **Not implemented. This track's subject.** |

> **Category A must never satisfy Category B.** M7 added a human approval step for A, which is exactly
> what could be misread as discharging M5-D20. It does not, and the architecture document says so in
> the same words.

---

## Candidate mechanisms — to evaluate, not to adopt

**Do not choose a mechanism merely because it exists.** Each must be assessed against the property
above and against this repository's actual threat model.

| Mechanism | What it would provide | What to verify empirically |
|---|---|---|
| `required_approving_review_count >= 1` | An approval from an identity other than the check runner | Whether the agent's identity can approve; whether a bot token qualifies |
| `require_code_owner_review: true` | Binds approval to a named owner set | Whether CODEOWNERS resolves as expected for every changed path |
| Dismiss stale reviews on new commits | Approval invalidates when the revision changes | Default behaviour and whether it covers force-push |
| `require_last_push_approval: true` | The pusher cannot approve their own final commit | Interaction with a single-maintainer repository |
| Protected environments | A separate deploy-time human gate | Only relevant once deployment exists — it does not today |
| Signed approval artifacts | Approval bound cryptographically to a revision | Whether it adds anything over platform review, or merely moves trust |

**An honest constraint to confront:** this repository has a single human owner. Several mechanisms
that are sound in a multi-maintainer setting degrade to "the owner approves their own agent's work",
which is a *procedural* control resting on the owner's discretion — exactly what the accepted M4
audit already says the current state is. The roadmap must say plainly which mechanisms genuinely
change that and which only appear to.

---

## Phases

1. **B0 — Empirical baseline.** Read the live ruleset and CODEOWNERS via the API rather than from
   documentation; determine which identities can approve; determine what the agent's own credentials
   can and cannot do. Measure before designing.
2. **B1 — Threat model.** State precisely which actor is being constrained: a hostile external actor,
   a compromised agent, or an agent behaving as designed but unsupervised. The mechanisms differ.
3. **B2 — Mechanism selection.** Choose against the property, with the single-owner constraint stated.
4. **B3 — Acceptance direction.** Prove, behaviourally: automation attempting to approve is refused;
   an approval is invalidated by a subsequent commit; a `REQUIRE_HUMAN` change cannot reach `main`
   without a qualifying approval. Each with a falsifier.
5. **B4 — Rollout order.** Enable in an order where a misconfiguration fails closed, and where the
   repository does not become unmergeable by its own owner during the transition.

---

## Non-goals

- Granting any agent merge or deploy authority. This track **removes** a gap; it grants nothing.
- Changing the `REQUIRE_HUMAN → passing check` mapping, which is correct as it stands.
- Treating a status check as human approval.
- Weakening the gate to make the flow smoother.

## Stop conditions

Stop and report rather than proceed if: the chosen mechanism would make the repository unmergeable by
its legitimate owner; or if the only mechanisms available reduce to self-approval, in which case the
honest outcome is to record that M5-D20 **cannot** be discharged under a single-owner model and to say
so, rather than to declare it discharged by a mechanism that does not bind.
