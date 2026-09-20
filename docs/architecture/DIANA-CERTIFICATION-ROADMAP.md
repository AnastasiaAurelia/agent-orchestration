# Track A — Security Evidence Certification (ROADMAP — NOT IMPLEMENTED)

Status: **planning only.** Nothing in this document is implemented, frozen or authorized. No control
is certified today, and this roadmap does not certify one.

Scope: Diana's **internal security evidence certification**. This is not a personal or professional
certificate, not a compliance badge, and not a claim about any external standard.

---

## The problem

The Security Gate evaluates **75 controls** and reports **75/75 UNPROVEN**. That is the honest
default, and it is the standard being met rather than a failure — but it means Diana can currently
say *"I have no evidence"* and cannot say *"I have proof."*

The governing principle, and the reason this track is slow by design:

> A finding is not certified because a model says so, a scanner returned green, a reviewer agreed,
> or a check exists. **Certification requires evidence.**

The failure mode this track exists to refuse is the attractive one: closing the gap by lowering the
bar. Every shortcut below is a non-goal.

---

## C0 — Empirical baseline (first phase, measure before designing)

Inventory, by execution rather than from documentation:

- the Security Gate's decision logic and its escalation rules;
- the control catalogue: how many controls, their severities, their requirement text;
- the evidence model: its closed run fields, its evidence-item fields, its status vocabulary
  (currently `SATISFIED` / `VIOLATED`), and what it rejects;
- the adapters (deterministic repo, semgrep, gitleaks, osv) and what each actually observes;
- the dynamic and reviewer paths;
- the coverage matrix and how applicability is currently represented;
- exactly **why** each of the 75 is UNPROVEN — the reasons already differ ("applicability has not
  been established" versus "no verification runs submitted"), and that distinction is the shape of
  the work.

Deliverable: a measured table of *what evidence exists* versus *what is missing*, per control.

---

## C1 — Evidence model

Define, as a closed schema: **claim · subject · control · evidence artifact · source · target binding
· freshness · verifier · applicability · status**.

Specify what can and cannot count as evidence. The load-bearing rule:

> **No model prose becomes authoritative evidence by itself.** A model may summarise evidence; it may
> not be the evidence.

Unknown fields are refused rather than recorded — a log of anomalies is itself a side channel.

---

## C2 — Applicability

**Before testing a control, prove it applies.** An authentication control only applies if an auth
surface is identified; a data-at-rest control only if a datastore or its configuration can be
established; a branch-protection control only if the platform is in scope.

The rule that prevents the worst error:

> **"No evidence" must never become "FAIL."**

Differentiate the states the empirical design supports — candidates are `NOT_APPLICABLE`,
`APPLICABILITY_UNKNOWN`, `UNPROVEN`, `PROVEN`, `CERTIFIED`, `VIOLATED`. **Do not freeze this
vocabulary until C0 shows what the current code can express**: the existing model already carries
`NOT_APPLICABLE` and an `APPLICABLE` notion, and a vocabulary that contradicts it would be a
migration, not a definition.

---

## C3 — Deterministic security verification

Bounded deterministic tests for applicable controls. Each requires an explicit target, bounded
authority, deterministic observation, and its own reason code.

> **No arbitrary agent judgment is the deciding evidence.** An agent may run a verifier; the verifier
> decides.

---

## C4 — Falsification and counterexample

For certification-quality claims, require a negative test that demonstrates the verifier can **detect
the broken condition**.

> A test that only passes on the current system is insufficient.

This is the discipline M1–M7 already applies to its own acceptance suites, carried into evidence.

---

## C5 — Evidence integrity

Bind every piece of evidence to: target repository, commit/version/configuration, the artifacts
examined, the control definition, the test implementation and its version, execution time, and the
verifier's identity where relevant.

The property to prove: **evidence from one target or version cannot be replayed onto another.** This
is the same shape as the proposal-digest binding M7 already uses, and should reuse that reasoning
rather than invent a parallel one.

---

## C6 — Independent verification

Prevent the self-certification loop:

```
  actor A produces a claim → actor A changes identity → actor A certifies itself
```

M6 already refuses exactly this shape for reviewer verdicts, and its mechanism is the precedent: the
acting role is journaled before the turn, so provenance is not something the author can state.

Determine empirically whether certification requires a separate reviewer role, a separate
deterministic verifier, a separate process or backend, or human involvement for specific controls —
and for which controls each is necessary.

> **Diana owns the final certification state transition**, exactly as it owns every run state
> transition today.

---

## C7 — Certification decision

Define the exact state machine, derived from C0 rather than assumed. Candidate direction:

```
  UNPROVEN → APPLICABLE → PROVEN → independently verified → CERTIFIED
```

Define expiry and freshness. **A certified result must become stale when its relevant target
changes** — the same property M7's proposal digest already has, and for the same reason.

---

## C8 — Certification UX

Human-readable results must distinguish four different facts:

| State | Must say |
|---|---|
| **CERTIFIED** | which control, what was tested, the exact evidence, target and version, verifier, freshness |
| **UNPROVEN** | precisely what is missing |
| **NOT_APPLICABLE** | why it does not apply, and how that was established |
| **VIOLATED** | the exact observed violation |

> **Never make "75/75 UNPROVEN" look like 75 failures.** It currently does not, and it must not start
> to as the vocabulary grows.

---

## Non-goals

This track does **not** solve the problem by:

- lowering evidence requirements;
- treating reviewer prose as certified evidence;
- mapping green CI directly to certification;
- hiding unknown applicability;
- converting advisory claims into proof;
- granting security actors broader runtime authority.

---

## Process

The established discipline, unchanged:

```
measure → frozen spec → dedicated freeze commit → implementation →
behavioural acceptance → falsification → independent audit → regressions →
PR → human merge
```

**Not implemented in this documentation phase.** The first action of this track is C0, and C0 is a
measurement, not a design.

## Stop conditions

Stop and re-freeze rather than proceed if: the current evidence model cannot express a state the
design needs; a control's applicability cannot be established deterministically; or certification
would require giving a security actor authority beyond the approved parent envelope.
