# Diana × Hermes — Milestone Roadmap (M1–M7)

**Status: directional context. NOT a specification.**

This document exists so that decisions taken in one milestone do not quietly foreclose the next.

This document defines the intended capability sequence and product direction, but not the
implementation design or frozen scope of any future milestone.

## What this document is not

Nothing here is normative. Nothing here is frozen. Nothing here authorizes implementation.

**Every milestone after M1 requires its own full cycle before it can be considered complete:**

1. **Empirical design** — findings established against the actual Hermes code and a running system,
   not from documentation, intuition, or this roadmap.
2. **A frozen specification** — its own document, with numbered decisions, its own trusted computing
   base, and its own explicit carried assumptions.
3. **Implementation** — against that frozen specification, checkpoint by checkpoint.
4. **Acceptance tests** — objective, falsifiable, and behavioral rather than configuration-shaped
   wherever the property allows it.
5. **Independent audit** — reviewed as if by someone who did not write it, with a stated verdict.

A milestone that has skipped any of those five is **not** complete, regardless of whether its code
runs. M1's own history is the argument: its post-implementation audit found two real enforcement
defects that every checkpoint suite had passed over, and one of them falsified a frozen decision's
stated reasoning.

The normative specification for M1 is [`HERMES-RUNTIME-M1.md`](HERMES-RUNTIME-M1.md). This roadmap
does not modify, extend, reinterpret, or soften it.

---

## Product North Star

> The final Diana × Hermes architecture must support **unattended bounded execution**: a user can
> approve a defined execution envelope once, leave the run unattended for hours, and return to
> completed work or explicit blocked items without granting Hermes unrestricted authority.

The whole ladder below is one question asked at widening capability: **can the envelope stay real as
the envelope grows?** Every milestone adds capability on one side and must add proof on the other.

---

## The capability sequence

### M1 — Enforcement Boundary *(COMPLETE)*

A single `SAFE`/`D1` read-only advisory security review, bounded by an `ExecutionContract`, with
capability and confinement enforced in-process and proven behaviorally.

**Established:** capability is controlled by what Hermes is *given*, not what it is *denied*; `risk`
derives from the granted envelope and `depth` from the certified workflow class, neither proposable
by Hermes; a blocked run yields no artifact at all; the advisory document is structurally incapable
of being read as Security Track evidence.

**Deliberately not established:** a live model turn. M1's turn driver is scripted.

---

### M2 — Real Hermes LLM Turn

**M2 adds no authority.** It keeps the exact M1 envelope — `SAFE`/`D1`, read-only,
`allowed_tools = {read_file, search_files}`, the same `read_scope`, the same contract, the same
artifact schema. The **only** thing that changes is the driver: scripted turn → a real Hermes model.

*Why it is isolated this way:* M1 proved enforcement against a driver Diana wrote, which called the
same entries a model reaches but was never trying to do anything else. A model that was not told the
rules is the first adversary the boundary has actually met. Widening capability in the same milestone
would confound two variables — a failure could be the model or the new capability, and neither
explanation could be ruled out.

*Open questions a frozen M2 spec must answer empirically:* whether a live turn touches dispatch
entries the scripted driver never reached; how the boundary behaves under prompt-injected or
hallucinated tool names, as opposed to deliberately probed ones; whether observations from a real
model are worth keeping as ungraded context at all, or worth dropping.

---

### M3 — D2 Runtime / Browser Verification

The first milestone above `D1`. M1 defined `D1` as **static evidence only** — no payload string, no
browser proof — because a scanner emitting `#<img src=x onerror=alert(1)>` would be emitting an
assertion dressed as evidence. `D2` is where runtime reproduction earns its place, and the repository
already carries `diana/playwright/`.

Still read-only with respect to the target: executing a page is not mutating a repository.

*Direction:* a finding is promoted only when a reproduction actually ran and was observed. Depth
continues to come from the certified workflow class, never from the agent, and a new depth needs its
own certification rather than a promotion of an existing one.

*Open questions:* what constitutes an observed reproduction as opposed to a claimed one; whether
browser execution belongs inside the capability envelope or beside it as a Diana-side verifier, as
the deterministic scanner is in M1.

---

### M4 — Bounded Write + Shell + Tests

The first milestone that mutates, and the first that executes commands. M1 refused both outright:
read-only made "no mutation occurred" a *measurable* property rather than a policy, and deciding
whether a shell command is read-only is an unwinnable parsing game.

**This is where per-tool argument, path, and command policy belongs.** `capability_envelope`
`allowed_tools` grows from a name set into per-tool policy consulted at the same dispatch boundary —
the boundary gets a richer policy, it is not replaced. Introducing that policy earlier would be
machinery with nothing to constrain, since a name set is sufficient for two read tools.

*Direction:* writes confined to declared paths, with the before/after repository snapshot becoming a
**diff assertion** rather than an equality assertion. Shell must not re-open the parsing game — it
needs a different shape: declared commands, a constrained runner, or an isolated environment.

*Direction — and where Diana's original complaint gets answered:* the Phase 0 baseline failed on
`build-test-evidence-present`. A system that can run builds and tests under a bounded envelope can
**produce** that evidence instead of blocking for its absence.

*Open questions:* post-hoc reconciliation was deferred in M1 (D25) precisely because prevention made
detection redundant. Once writes exist, prevention alone stops proving what *happened*, so
reconciliation likely returns — and M1 established it needs an authoritative tool-call ledger that
Hermes may not fully provide.

---

### M5 — Unattended Bounded Execution

The North Star milestone. Approve once, leave, return to completed work or explicit blocked items.

*Direction:* durable run state, resumption after interruption, and a blocked-item report legible to
someone who was not watching. The hard part is not duration — it is that an envelope approved at hour
zero must still be the envelope in force at hour six, and must be **provably** so.

*Open questions:* what a long run does to contract binding and digest freshness; whether an
interrupted run may resume under its original contract or must be re-approved; how a blocked item is
surfaced so that returning to it is cheap.

---

### M6 — Multi-Actor / Reviewer, and gradual AO replacement

M1 explicitly excluded actor/reviewer ceremony, and its acceptance criteria actively protected the
existing AO path from modification (AC-13). M6 is where that protection is lifted **deliberately and
incrementally**, not incidentally.

*Direction:* more than one bounded actor, with review as a role rather than a ritual, and AO
responsibilities migrated one at a time with the old path kept working until each is genuinely
replaced.

*Open questions:* whether separate actors need separate envelopes or separate contracts; how a
reviewer's authority is bounded when its job is to judge another actor's work; what evidence a
migration step must produce before the corresponding AO capability is retired.

---

### M7 — Natural-Language Product UX

The surface. M1 already removed the slash command — `"Check security project ini"` routes through a
deterministic keyword rule, because the workflow class determines depth and letting a model choose it
would hand Hermes the depth channel it is denied.

*Direction:* generalize that into a product surface where a user states intent in natural language and
receives either completed work or explicit blocked items — with the envelope still derived, still
shown, and still not negotiable by the agent.

*Open questions:* how routing scales beyond a keyword rule without handing classification to the model;
how an envelope is presented to a person so that approving it once is an informed act rather than a
reflex.

---

## Dependency chain

```
M1  Enforcement Boundary
 → M2  Real Hermes LLM
 → M3  Runtime Verification
 → M4  Bounded Mutation / Shell / Tests
 → M5  Unattended Execution
 → M6  Multi-Actor / AO Migration
 → M7  Product UX
```

The sequence is intentional, not a preference. Each milestone's proof depends on the one before it
holding: a live model (M2) is only meaningful against a boundary already proven (M1); runtime
verification (M3) is only trustworthy if the model driving it is bounded; mutation (M4) should not be
granted to a system whose read-only behavior under a real model has not been observed; unattended
execution (M5) is the composition of everything below it; and multi-actor work (M6) multiplies
whatever guarantees M5 established, including their absence.

**No future milestone may claim completion unless prior milestone invariants remain green.** A
milestone that passes its own acceptance tests while breaking an earlier milestone's is not complete;
it is a regression with new features attached.

---

## Invariants that carry across every milestone

These are not up for renegotiation milestone by milestone. A milestone that needs one of them relaxed
needs a new design round, not an exception.

1. **Fail-closed, and ordered correctly.** A control must be evaluated *before* the risk it guards.
   M1's audit found safe mode verified after the import that loads plugins — the control was real and
   the ordering made it useless.
2. **The dispatch surface must be re-enumerated whenever capability widens.** M1's audit found 13
   tools, including `delegate_task`, reaching handlers through inline executors that never touched
   the entry everyone believed was sole. Any milestone that adds tools must re-derive that map from
   code rather than inherit this one.
3. **Behavioral evidence over configuration evidence** wherever the property permits it. Some
   controls can only ever be configuration-shaped; those should be named as such rather than
   presented as proven by execution.
4. **The agent proposes nothing about its own constraints.** No channel for `risk`, `depth`, or
   severity. Closed schemas, with unknown fields rejected rather than recorded — a log of anomalies
   is itself a side channel.
5. **An artifact is only ever produced by a run whose envelope was proven.** A blocked run yields a
   run record, never a deliverable, even when the deliverable's content would have been sound.
6. **Declared limitations are pinned by tests**, at a location, so a gap cannot silently close or
   silently widen without a test changing.

---

## Future Certification Track (after M7)

Not one of the canonical M1–M7 product milestones, and deliberately not on the capability sequence.
It is recorded here because it is important and because crossing it casually would cause real harm.

**Advisory findings into certified evidence.** M1 went to significant lengths to make the advisory
document *structurally* incapable of being mistaken for Security Track evidence: it carries none of
`evidence_model`'s six allowed run fields at any depth, and a test asserts the Security Track
classifies it `MALFORMED`.

If advisory findings are ever to contribute to certified evidence, they must do so by **satisfying
the Security Track's own requirements** — verifier provenance, target binding, evidence composition —
not by relaxing them. The current 75/75 `UNPROVEN` result is not a problem to be argued away; it is
the standard being met.

This is the track where *"we'll make the evidence fit"* would be the wrong move.

---

## How to use this document

Read it to check whether an M1-era decision is about to foreclose something later. Do not read it as
a plan of record, a scope commitment, or permission to begin.

When a milestone starts, this file is *input* to its design round — not a substitute for one.
