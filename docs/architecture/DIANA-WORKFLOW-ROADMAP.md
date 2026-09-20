# Track C — Certified Workflow Expansion (ROADMAP — NOT IMPLEMENTED)

Status: **planning only.** No workflow class is certified by this document. The certified set today is
exactly what the code says it is, and this roadmap does not add to it.

---

## The principle this track exists to protect

M7 made natural language a product entry point. The failure mode that invites is obvious:

> "Add keywords, and suddenly arbitrary workflows are certified."

They are not. Routing decides **which certified class a request is**; it cannot decide **what a class
may do**. A third workflow class requires its own certification — it is **not** a routing-table update.

---

## Currently certified classes (measured, not assumed)

From `diana/runtime/contract.py` and `diana/product/catalogue.py`:

| Class | Depth | Risk | Product path today |
|---|---|---|---|
| `ADVISORY_SECURITY_REVIEW` | `D1` | `SAFE` | **No bounded-run product path.** Refused with `workflow-no-product-path`: it is a certified class, but the product flow starts bounded repairs only. |
| `BOUNDED_REMEDIATION` | `D2` | `ELEVATED` | **Yes** — the full Goal → Plan → Approval → Builder → Reviewer → Result flow. |

That is the entire certified set: **two classes, one of them runnable from the product surface.**

Depth comes from the class. Risk derives from the granted envelope. Neither is proposable.

---

## Requirements for any new class

A new class is admitted only when **all thirteen** are satisfied and frozen:

1. **Clear user intent** — a real request people actually make, not a capability looking for a use.
2. **Deterministic routing boundary** — a rule that separates it from every existing class without a
   model call, including a disqualifying rule so a neighbouring intent cannot drift into it.
3. **Explicit authority envelope** — the exact `allowed_tools`.
4. **Read and write scope** — including how exclusions reduce it.
5. **Command catalogue** — exact-match entries with their applicability evidence. No parsing.
6. **Risk and depth derivation** — from the class and the envelope, never proposed.
7. **Actor topology** — which roles exist and what each role's frozen projection is.
8. **Work-item semantics** — what a unit of work is, and what its dependencies mean.
9. **Deterministic verification** — what Diana runs to decide the work is done. Not the reviewer.
10. **Blocked behaviour** — which controls can refuse it, with which reason codes, and what human
    decision each implies.
11. **Falsification tests** — for every control, a test that fails when the control is removed.
12. **Independent review** — by someone who did not write it, attacking the fix rather than the
    original defect.
13. **No authority composition beyond parent policy** — the union of the new class's projections must
    not exceed what a user approved, and must be proven by containment.

---

## Candidate future classes — **none of these is certified**

Listed as plausible subjects only. Marking any of them certified requires the full process above.

- test repair
- lint / type repair
- bounded refactor
- dependency upgrade
- documentation maintenance
- static-analysis remediation
- security remediation
- controlled browser verification

Two observations worth carrying into the design rather than discovering later. **Dependency upgrade**
and **security remediation** are the two whose natural envelope wants network access or credential
reach, which every milestone so far has refused — they are not simply "more of the same" and should
not be sequenced first. **Controlled browser verification** already has a runtime (M3) but no product
class, so it is the one where the gap is a class definition rather than new capability.

---

## Certification checklist template

```
CLASS:              <NAME>
Depth:              <derived from class>       Risk: <derived from envelope>
Routing rule:       <deterministic; include disqualifying terms>
allowed_tools:      <exact set>
write_scope:        <roots>          denied: <subpaths>
Command catalogue:  <exact strings + applicability evidence>
Actor topology:     <roles>          projections: <frozen shape per role>
Work items:         <what a unit is; dependency meaning>
Verification:       <what Diana runs, deterministically>
Blocked paths:      <control → reason code → human decision>
Falsifiers:         <one per control>
Union proof:        <⊆ parent envelope, by containment>
Independent review: <who, verdict, findings>
```

---

## Process

`measure → frozen spec → freeze commit → implementation → behavioural acceptance → falsification →
independent audit → regressions → PR → human merge`, exactly as M1–M7.

## Non-goals

- Adding a class by extending a keyword list.
- Reusing an existing class's envelope for a different kind of work because it "fits".
- Letting a model name a class that is not in the certified set — already refused today.
- Widening an existing class to cover a new one.

## Stop conditions

Stop rather than proceed if: a candidate class cannot be separated from an existing one
deterministically; its verification would require the reviewer to execute commands; or its envelope
would need a capability M1–M7 has refused (network, credentials, unrestricted shell, merge, deploy).
