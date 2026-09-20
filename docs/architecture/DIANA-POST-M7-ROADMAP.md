# Diana — Post-M7 Master Roadmap (PLANNING — NOTHING HERE IS IMPLEMENTED)

Status: **planning only.** The M1–M7 runtime roadmap is complete and accepted. Everything in this
document is future work and none of it is frozen, authorized or started.

These are deliberately **named parallel tracks, not M8/M9/M10.** They solve different problems, have
different prerequisites, and are not a single sequence — numbering them would imply an ordering the
dependencies do not support.

| | Track | Document |
|---|---|---|
| **A** | Security Evidence Certification | [`DIANA-CERTIFICATION-ROADMAP.md`](DIANA-CERTIFICATION-ROADMAP.md) |
| **B** | Mechanical Human Approval (M5-D20) | [`DIANA-HUMAN-APPROVAL-ROADMAP.md`](DIANA-HUMAN-APPROVAL-ROADMAP.md) |
| **C** | Certified Workflow Expansion | [`DIANA-WORKFLOW-ROADMAP.md`](DIANA-WORKFLOW-ROADMAP.md) |
| **D** | Distribution & Productization | [`DIANA-DISTRIBUTION-ROADMAP.md`](DIANA-DISTRIBUTION-ROADMAP.md) |

---

## Dependency map

Derived from what each track actually needs, not from a preferred order.

```
                        M1–M7 COMPLETE
                              │
        ┌─────────────┬───────┴───────┬─────────────┐
        ▼             ▼               ▼             ▼
   [FUTURE] A    [FUTURE] B      [FUTURE] D    (C's 13-point
  Certification   Human Approval  Distribution   checklist is
        │             │               │          usable today)
        │             │               │             │
        │             └──────┐        │             │
        │                    ▼        │             │
        │        merge/deploy authority│            │
        │        becomes discussible   │            │
        │                              │            │
        └──────────────┬───────────────┘            │
                       ▼                            │
              richer evidence for  ◀────────────────┘
              new classes' verification
                       │
                       ▼
                 [FUTURE] C
          Certified Workflow Expansion
                       │
                       ▼
              broader production use
```

**What actually depends on what:**

- **A → C (soft).** A new class needs deterministic verification (requirement 9). Certification-grade
  evidence makes that stronger, but C does not *block* on A — M7's `BOUNDED_REMEDIATION` already
  verifies deterministically without any certified control.
- **B → merge/deploy authority (hard).** No milestone may grant autonomous merge or deploy authority
  until B closes. This is the only hard gate in the diagram, and it is carried from M5-D20.
- **D → C (soft, and often overlooked).** Every new class multiplies the surface a user must install,
  configure and diagnose. Expanding classes before distribution is stable widens something that is
  already hard to deliver.
- **A and B are independent.** They touch different systems — A the evidence model, B the repository
  ruleset — and neither needs the other.
- **D is independent of A and B.** Packaging does not need certified evidence or a merge rule.

**Can proceed in parallel:** A, B and D. **Should follow:** C.

---

## Track summaries

### Track A — Security Evidence Certification

**Problem.** The Security Gate reports 75/75 `UNPROVEN`. Diana can say "I have no evidence" and cannot
say "I have proof."

**Why it matters.** Without it, every security claim stays advisory, and the gap is permanently
tempting to close by lowering the bar.

**Baseline.** 75 controls; evidence-item statuses `SATISFIED`/`VIOLATED`; a closed evidence model that
already rejects unknown fields; four adapters; applicability partially represented.

**Prerequisites.** None. C0 is a measurement and can begin immediately.

**Phases.** C0 baseline → C1 evidence model → C2 applicability → C3 deterministic verification →
C4 falsification → C5 integrity → C6 independent verification → C7 decision → C8 UX.

**Acceptance direction.** A control moves to `CERTIFIED` only with a bound artifact, a named verifier,
proven applicability, a passing negative test, and a Diana-owned state transition.

**Constraints.** No model prose as evidence; no green-CI-to-certified mapping; no broader runtime
authority for security actors.

**Non-goals.** Compliance badges; external standards; certifying by assertion.

---

### Track B — Mechanical Human Approval (M5-D20)

**Problem.** `REQUIRE_HUMAN` is advisory. Measured ruleset: `required_approving_review_count: 0`,
`require_code_owner_review: false`, `require_last_push_approval: false`. The gate's `REQUIRE_HUMAN`
maps to a *passing* check, and the rule it defers to is not configured.

**Why it matters.** An agent holding merge authority could satisfy every mechanical condition on a
`DANGEROUS` change by itself — `REQUIRE_HUMAN` would be decorative exactly when it matters most.

**Baseline.** The accepted M4 audit §8, restated by M5-D20 and again by M6 and M7.

**Prerequisites.** None technically. One honest constraint: a **single-owner repository** limits what
several candidate mechanisms actually prove.

**Phases.** B0 measure the live ruleset → B1 threat model → B2 mechanism selection → B3 acceptance
direction → B4 fail-closed rollout.

**Acceptance direction.** Automation attempting to approve is refused; approval invalidates on a new
commit; a `REQUIRE_HUMAN` change cannot reach `main` without a qualifying approval — each with a
falsifier.

**Constraints.** Grants no agent any merge or deploy authority. Does not change the
`REQUIRE_HUMAN → passing check` mapping, which is correct.

**Stop condition.** If the only available mechanisms reduce to self-approval, record that M5-D20
**cannot** be discharged under a single-owner model — do not declare it discharged by a mechanism that
does not bind.

---

### Track C — Certified Workflow Expansion

**Problem.** Two certified classes exist; only one is runnable from the product surface. The pressure
will be to add classes by extending a keyword list.

**Why it matters.** Routing decides which certified class a request *is*; it must never decide what a
class *may do*.

**Baseline.** `ADVISORY_SECURITY_REVIEW` (D1/SAFE, no product path) and `BOUNDED_REMEDIATION`
(D2/ELEVATED, full product path).

**Prerequisites.** Soft dependency on A for stronger verification and on D for deliverability. The
13-point checklist is usable today without either.

**Phases.** Per class: intent → routing boundary → envelope → scope → catalogue → derivation →
topology → work items → verification → blocked behaviour → falsifiers → independent review → union
proof.

**Constraints.** No class may need a capability M1–M7 refused: network, credentials, unrestricted
shell, merge, deploy.

**Non-goals.** Adding a class by keyword; widening an existing class to cover a new one.

---

### Track D — Distribution & Productization

**Problem.** The product entry point exists and works, and cannot currently reach another machine or
another project. `install.sh` ships four slash commands and not the CLI.

**Why it matters.** A runtime nobody can install is a runtime nobody can evaluate.

**Baseline.** No packaging; stdlib-only dependencies plus a pinned Hermes; a root shell launcher not
on `PATH`; Linux assumptions (`/proc`, `flock`) that are real and currently implicit.

**Prerequisites.** None.

**Phases.** G1 installation → G2 doctor → G3 CLI ergonomics → G4 error UX → G5 config → G6
cross-platform → G7 versioning → G8 documentation.

**Constraints.** Policy is not user-editable. No platform claim without proof. An approval issued
under a narrower policy must never become valid under a wider release.

**Non-goals.** Weakening a control for convenience; adding CLI verbs in planning; shipping before the
platform story is stated.

---

## What must remain true across every track

Carried forward from M1–M7 and not renegotiable track by track:

1. **Model output is never authority.**
2. **Risk and depth are derived, never proposed.**
3. **Approval binds an exact, immutable object.**
4. **Ambiguity narrows or refuses; it never widens.**
5. **Widening requires a new approval and a new run.**
6. **The product path and the expert path reach the same enforcement boundary.**
7. **Reconciliation is detection, never prevention.**
8. **"No evidence" is never "FAIL."**
9. **Nothing is called retired while it still runs.**
10. **A declared limitation is pinned by a test, at a location.**

---

## Recommended first track

**Track B**, and the reasoning is dependency-based rather than aesthetic.

B is the only **hard** gate in the map: no future milestone may grant merge or deploy authority until
it closes, so it blocks more future work than any other track. It is also the smallest — its first
phase reads a ruleset via the API and changes nothing — and it is the one whose *outcome may be
negative*. If a single-owner repository cannot produce a non-self-satisfiable approval, that is worth
knowing before Tracks A, C and D are built on the assumption that merge authority eventually becomes
reachable.

A and D can proceed in parallel with it. C should wait.
