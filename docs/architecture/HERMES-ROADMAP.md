# Diana × Hermes — Milestone Roadmap (M1–M7)

**Status: directional context. NOT a specification.**

This document exists so that decisions taken in one milestone do not quietly foreclose the next. It is
a map of intended direction, not a commitment to scope, sequence, or design.

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

## The ladder

### M1 — Enforcement boundary, read-only advisory *(COMPLETE)*

A single `SAFE`/`D1` read-only advisory security review, bounded by an `ExecutionContract`, with
capability and confinement enforced in-process and proven behaviorally.

**Established:** capability is controlled by what Hermes is *given*, not what it is *denied*; `risk`
derives from the granted envelope and `depth` from the certified workflow class, neither proposable
by Hermes; a blocked run yields no artifact at all; the advisory document is structurally incapable
of being read as Security Track evidence.

**Deliberately not established:** a live model turn. M1's turn driver is scripted.

---

### M2 — The live turn, and per-tool argument policy

The first milestone where a real model drives the loop. M1's enforcement held against a scripted
driver that called the same entries a model reaches; that is necessary evidence and not sufficient
evidence.

*Direction:* extend `capability_envelope.allowed_tools` from a name set into per-tool **argument**
policy consulted at the same dispatch boundary — the boundary gets a richer policy, it does not get
replaced.

*Open questions a frozen M2 spec must answer empirically:* what a live turn does to the dispatch
surface; whether prompt-injected tool names behave as the scripted probes did; whether observations
from a real model are worth keeping ungraded, or worth nothing at all.

---

### M3 — Bounded writes

The first milestone that mutates. The read-only envelope was what made M1's "no mutation occurred"
a *measurable* property rather than a policy; M3 gives that up and must replace it with something
equally checkable.

*Direction:* writes confined to declared paths, with the before/after repository snapshot becoming a
**diff assertion** rather than an equality assertion.

*Open questions:* post-hoc reconciliation was deferred in M1 (D25) precisely because prevention made
detection redundant. Once writes exist, prevention alone stops proving what *happened*, so
reconciliation likely returns — and M1 established it needs an authoritative tool-call ledger that
Hermes may not fully provide.

---

### M4 — Bounded shell and test execution

The capability M1 refused outright, because deciding whether a shell command is read-only is an
unwinnable parsing game. M4 must not re-open that game; it must find a different shape — declared
commands, a constrained runner, or an isolated environment.

*Direction:* this is also where Diana's original complaint gets answered. The Phase 0 baseline failed
on `build-test-evidence-present`; a system that can run builds and tests under a bounded envelope can
produce that evidence instead of blocking for its absence.

---

### M5 — Unattended multi-hour execution

The North Star milestone. Approve once, leave, return to completed work or explicit blocked items.

*Direction:* durable run state, resumption after interruption, and a blocked-item report that is
legible to someone who was not watching. The hard part is not duration — it is that an envelope
approved at hour zero must still be the envelope in force at hour six, and must be *provably* so.

---

### M6 — The depth ladder above D1

M1 defined D1 as static evidence only: no payload, no browser proof, because an unexecuted payload is
an assertion dressed as evidence. D2+ is where runtime reproduction earns its place — the repository
already carries `diana/playwright/`.

*Direction:* depth continues to come from the certified workflow class, never from the agent. Each
new depth needs its own certification, not a promotion of an existing one.

---

### M7 — Advisory findings into certified evidence

The last separation to be crossed deliberately, and the one most likely to cause harm if crossed
casually. M1 went to significant lengths to make the advisory document **structurally incapable** of
being mistaken for Security Track evidence.

*Direction:* if advisory findings are ever to contribute to certified evidence, they must do so by
**satisfying the Security Track's own requirements** — verifier provenance, target binding, evidence
composition — not by relaxing them. The current 75/75 `UNPROVEN` result is not a problem to be
argued away; it is the standard being met.

*This is the milestone where "we'll make the evidence fit" would be the wrong move.*

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

## How to use this document

Read it to check whether an M1-era decision is about to foreclose something later. Do not read it as
a plan of record, a scope commitment, or permission to begin.

When a milestone starts, this file is *input* to its design round — not a substitute for one.
