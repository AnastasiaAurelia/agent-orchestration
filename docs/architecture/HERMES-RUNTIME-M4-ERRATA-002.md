# HERMES-RUNTIME-M4 — ERRATA 002

Status: **normative erratum** to [`HERMES-RUNTIME-M4.md`](HERMES-RUNTIME-M4.md).
Scope: **M4-D11's `workdir` clause only.** Every other M4 decision (M4-D1…M4-D16), every
acceptance criterion, and the whole of [`ERRATA-001`](HERMES-RUNTIME-M4-ERRATA-001.md) are
untouched by this document.

`HERMES-RUNTIME-M4.md` remains **byte-identical**. It is not edited in place, and this erratum
does not rewrite it. Where this erratum and M4-D11's original `workdir` sentence disagree,
**this erratum governs**; the original sentence is recorded as the defect.

M1's D1–D38, M2's D1–D14 and M3's D1–D16 remain frozen and unmodified. This erratum **narrows**
an envelope; it widens nothing, and it grants M4 no authority over any earlier milestone's
modules.

---

## 1. When and how this was discovered

Found by **M5's empirical Phase 0**, recorded there as finding F7, while establishing what a
bounded run's process and session state do across a restart. It was found *after* M4 was approved
and merged, which is why the remedy is a follow-up erratum and a follow-up commit rather than an
amendment: M4's history is not rewritten.

It is recorded in M4's audit trail as finding **F-A7**.

The discovery path matters. M5's subject is duration, so its Phase 0 asked which parts of a run's
state survive a process boundary. The session working directory does not — and that question is
what exposed a control whose correctness silently depended on it.

## 2. The contradiction

**M4-D11, as frozen, says:**

> `workdir` must resolve inside `write_scope`, and `timeout` is clamped. **An absent `workdir` is
> permitted and means the session default.**

**The implementation's own comment defended that permission:**

> An absent `workdir` is safe to default because the session cwd is still scope-checked.

**That reasoning is false, and was proven false by execution.** Nothing scope-checks the session
cwd. `decide_command` evaluated the `workdir` rule under `if workdir is not None`, so omission
skipped the check entirely, and the command then ran in whatever directory the ambient process or
session happened to supply.

Measured on the real M4 shape — `read_scope` the repository root, `write_scope` and
`workdir_roots` the single subdirectory `<root>/src`:

```
{command: <allowed>, timeout: 20, workdir: "<root>"}   ->  Verdict(deny)   # named: refused
{command: <allowed>, timeout: 20}                      ->  Verdict(allow)  # omitted: allowed
                                                       #  and it executed in "<root>"
```

**The value the policy refuses when it is named is the value it accepts when it is omitted.** That
is precisely the shape of audit finding **F-A2**, one argument further along: an omitted argument
reaching a bound that the declared policy would have rejected.

## 3. Why this is an authority defect and not a convenience default

The ambient session working directory is **not part of the approved `ExecutionContract`**. Nothing
in the contract declares it, nothing derives it, no digest covers it, and the user who approved the
envelope never saw it. It is inherited from whichever process happens to be running.

It is also **not a durable authority binding**. It does not survive a restart or a resume: a run
re-armed in a new process inherits a different ambient directory, so a control that reads it is a
control whose decision depends on state that changes when nobody is looking. M5's Phase 0 measured
exactly this — the same call, in three processes with three different ambient directories.

M1 D14's rule is that the agent proposes nothing about its own constraints. A default sourced from
ambient process state is a weaker version of the same failure: not the agent proposing a bound, but
**the environment supplying one that nobody approved**. Diana must never infer permission from
ambient cwd. Runtime and session cwd are **not authority**.

The severity is bounded and is stated plainly rather than inflated. M4's carried assumptions already
say the command allowlist is not a sandbox and that an allowed command may write wherever the Diana
process can, so this defect does not hand an allowed command a capability it otherwise lacked. What
it does is make the run's **declared** working-directory bound differ from its **actual** one, which
is the same category of harm M4-D11 exists to prevent: *"the run's bound is never quietly different
from the one requested."*

## 4. Corrected M4-D11

> **M4-D11 (as corrected by ERRATA-002)** — `terminal.workdir` is **REQUIRED**. A call that omits
> it is **refused** before dispatch; omission fails closed and is never treated as a session
> default. A supplied `workdir` must be a **non-empty string**, must be **canonicalized** before any
> comparison, and must resolve **inside the declared `workdir_roots`** (which derive from
> `write_scope`), evaluated under spec C2's canonicalize-then-contain discipline so that traversal
> and symlink escape deny rather than pass. Diana **never infers permission from the ambient process
> or session working directory**; runtime and session cwd are not an authority channel. `timeout`
> remains as M4-D11 and the audit's F-A2 correction left it: required, explicit, and refused rather
> than silently clamped when it exceeds the declared ceiling.

Unchanged from M4-D11: `timeout` semantics, the refusal of `background` (M4-D10), and every other
argument control.

## 5. The correction, and why it is the smallest one

The entire fix is the removal of an `if workdir is not None:` guard, plus the refusal that replaces
it, in `diana/mutation/mutation_policy.py` — **an M4-owned file, created by M4's own implementation
commit `7e2395e`.** It is therefore an edit to M4's own code, **not** a pre-existing production
module, and **ERRATA-001 §3's permitted-replacement set of pre-existing production files is
unchanged and not widened by this erratum.** That set remains exactly `diana/runtime/contract.py`,
`diana/runtime/blocking.py`, `diana/adapters/hermes_patches.py`.

No new control is introduced. The canonicalization and containment requirements of §4 were already
satisfied by the existing call into `read_scope.decide`, which canonicalizes first and denies on any
resolution failure (spec C2). The defect was never that the check was wrong; it was that the check
was **reachable only when the model opted in** — the same structure as F-A2.

One further file changes, and it is deliberately classified as **presentation, not enforcement**:
`diana/mutation/remediation_driver.py`'s prompt now names the required `workdir` and the timeout
ceiling. Per M2-D2 the model-visible surface is never a control — the policy at the dispatch
boundary is what refuses, and M4's adversarial cases bypass the model entirely to prove it. Naming
the bounds keeps M4-AC-17's thesis achievable without giving the agent any say in what those bounds
are (M1 D14). This mirrors how F-A2 was handled, where requiring an explicit `timeout` left the
milestone's end-to-end criterion intact.

## 6. What this erratum does not do

- It does not modify `HERMES-RUNTIME-M4.md`, `ERRATA-001`, or any M1/M2/M3 specification.
- It does not amend, rebase or rewrite any existing commit, including `7e2395e` and the M4 merges.
- It does not relax any enforcement control, acceptance criterion, or boundary. It **narrows** one.
- It does not widen ERRATA-001 §3's permitted-replacement set of pre-existing production files (§5).
- It does not grant, and must not be read as granting, any part of M5. Durable run state,
  resumption, and process ownership across a restart remain M5's subject and remain unimplemented.

## 7. Behavioral proof obligations

This erratum is only discharged when the following are asserted **behaviorally, through the real
dispatch path**, and are carried in M4's acceptance suite so the gap cannot silently reopen:

| # | Obligation |
|---|---|
| 1 | An allowed command with an explicit **in-scope** `workdir` **executes**, and is observed to run in that directory. |
| 2 | An allowed command with an explicit **out-of-scope** `workdir` is **refused**. |
| 3 | An allowed command with an **omitted** `workdir` is **refused**. |
| 4 | The omitted-`workdir` refusal leaves **no execution canary** — enforcement precedes the effect (M4-D13). |
| 5 | An ambient cwd outside scope **cannot** be used as an implicit authority channel. |
| 6 | Restart / new-process cwd differences **do not change** the policy decision. |
| 7 | The existing `timeout`, `background` and `pty` controls remain **intact**. |
