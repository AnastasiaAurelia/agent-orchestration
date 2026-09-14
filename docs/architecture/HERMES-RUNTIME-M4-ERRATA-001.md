# HERMES-RUNTIME-M4 — ERRATA 001

Status: **normative erratum** to [`HERMES-RUNTIME-M4.md`](HERMES-RUNTIME-M4.md).
Scope: **M4-REG-2 only.** Every other M4 decision (M4-D1…M4-D16) and every acceptance
criterion is untouched by this document.

`HERMES-RUNTIME-M4.md` remains **byte-identical**. It is not edited in place, and this erratum
does not rewrite it. Where this erratum and M4-REG-2's original text disagree about the
permitted-replacement set, **this erratum governs**; the original M4-REG-2 sentence is recorded
as the defect.

M1's D1–D38, M2's D1–D14 and M3's D1–D16 remain frozen and unmodified. This erratum widens
nothing outside M4's own branch and grants M4 no authority over any earlier milestone's modules.

---

## 1. When and how this was discovered

Found by the **post-implementation audit**, as audit finding 3, **before** M4 was approved,
**before** any push, **before** any pull request, and **before** any merge. M4 was implementation-
complete and committed (`7e2395e`) at the time, with its acceptance suite reporting 106/106 green.

The audit was checking whether `M4-AC-18`'s `M4-REG-2` assertion was *meaningful* rather than
merely passing. It was not: the assertion compared M4's changed-file set against a permitted set
defined **in the test file**, and that set was wider than the one the specification froze. The
suite therefore reported green over a violated frozen constraint.

This is the same failure mode M1's audit found and which the roadmap cites as its argument for
requiring an independent audit at all: *a checkpoint suite passing over a real defect.* It is
recorded here rather than quietly corrected.

## 2. The contradiction

**M4-REG-2, as frozen, says:**

> M4's permitted-replacement set is exactly **one item**: the additive, opt-in extension of
> `diana/runtime/contract.py` described in M4-D2 and M4-D4.

**M4-D1, as frozen, says:**

> Per-tool argument, path and command policy is consulted at the same two entries M1 proved
> (`model_tools.handle_function_call` and `agent/tool_executor._dispatch_authorized_once`).

Those two decisions **cannot both be satisfied**. Both entries named by M4-D1 are installed and
guarded in `diana/adapters/hermes_patches.py` — that module *is* M1's enforcement boundary. A
policy cannot be consulted at a boundary without modifying the module that implements the
boundary. Likewise M4-D15's `reconciliation-mismatch` outcome must be expressible as a blocking
reason code, and reason codes live in `diana/runtime/blocking.py`.

So M4-REG-2's "exactly one item" describes a design that M4-D1 and M4-D15 make impossible.

**This is a specification defect.** It is *not* evidence that the implementation may widen its
blast radius at will, and it is not a licence to modify any further pre-existing module. The
implementation is correct here and the frozen sentence is wrong; the remedy is to state the true
minimum set and to prove the diff equals it exactly.

## 3. The corrected permitted-replacement set

M4 may modify exactly these **pre-existing production-code** files, and no others:

| File | Why the frozen design requires it |
|---|---|
| `diana/runtime/contract.py` | **M4-D2** adds the optional `write_scope`, `allowed_commands` and `command_policy` envelope keys; **M4-D4** adds `validate()`'s `accept` parameter whose default stays M1's `SAFE`/`D1`; **M4-D5** certifies `BOUNDED_REMEDIATION` → `D2`. All three are contract-shaped and cannot live anywhere else. |
| `diana/runtime/blocking.py` | **M4-D4** needs a reason code distinct from M1's frozen `contract-not-safe-d1` (a milestone refusing a class it does not execute must not reuse M1's observable); **M4-D6** needs `write-scope-*` codes; **M4-D15** needs `reconciliation-mismatch`. Additions only — no existing code's value or meaning changes. |
| `diana/adapters/hermes_patches.py` | **M4-D1** names `model_tools.handle_function_call` and `agent/tool_executor._dispatch_authorized_once` as the enforcement entries. This module installs and guards both. The change is an optional `policy=None` parameter: omitted, the function behaves exactly as M1 froze it, which is what keeps every M1 suite green unmodified (`M4-AC-16`, and M1's 470/470). |

**No other pre-existing production module becomes permitted by this erratum.** In particular
nothing under `diana/advisory/`, `diana/profile/`, `diana/gate/`, `diana/preflight/`,
`diana/security/`, `diana/ship/`, `diana/ci/`, `diana/playwright/` or `diana/runtime_verify/`
(production code) may be modified by M4.

## 4. Classification — three categories, not one

M4-REG-2's original text, and the comment in M4's own acceptance suite, conflated distinct kinds
of change. They are separated here and must be asserted separately.

**(a) Permitted replacements — pre-existing production code.** Exactly the three files in §3.
The assertion is **set equality**, not subset: a diff that modifies *fewer* of them is as much a
signal worth failing on as one that modifies more, because it means the frozen design and the
implementation have drifted apart.

**(b) Docs and publish manifest — not production-code replacements.** `.gitignore` is a
deny-all-then-allowlist manifest; in this repository a new file is unpublishable until named
there, so adding entries is how M4's own files become tracked at all, and it changes no Diana/AO
behavior. Documents under `docs/architecture/` are specifications and errata. Neither category
may be described as a permitted production-code replacement, which is precisely what M4's suite
comment did when it called `blocking.py` and `hermes_patches.py` "docs/manifest files".

**(c) Test-harness corrections — not production code.** M4's branch additionally corrects the
committed **M3 acceptance harness** (`diana/runtime_verify/test-m3-runtime-verify.sh`), per audit
finding 4 and an explicit governance decision. That harness asserted M3's historical
changed-file facts against `M2_MERGE..HEAD`, so it reinterpreted every legitimate later-milestone
change as an M3 regression; and it required the global certified workflow map to equal exactly
`{ADVISORY_SECURITY_REVIEW}` forever, which M4-D5 necessarily breaks. M3's own frozen M3-REG-2
already grants the exemption its test never implemented:

> except where a future milestone explicitly freezes and proves a replacement

No M3 specification text is modified. No substantive M3 control is weakened. This is a test-
harness defect discovered by the M4 audit, corrected as test code and classified as such.

**New M4 files are additions, not permitted replacements.** Every file M4 creates — the
`diana/mutation/` modules, its fixtures, its acceptance suite, `HERMES-RUNTIME-M4.md`,
`M4-AUDIT.md` and this erratum — is an addition. Additions are never "replacements" and must
never be counted in category (a).

## 5. Corrected M4-REG-2

> **M4-REG-2 (as corrected by ERRATA-001)** — pre-existing Diana/AO modules and behavior remain
> unchanged, **except where a future milestone explicitly freezes and proves a replacement**.
> M4's permitted-replacement set of pre-existing **production-code** files is exactly:
> `diana/runtime/contract.py`, `diana/runtime/blocking.py`, `diana/adapters/hermes_patches.py`,
> each justified in §3 and each additive and opt-in. The set is asserted by **equality**.
> Changes to the publish manifest and to documents are classified separately (§4b) and are not
> production-code replacements. Test-harness corrections are classified separately (§4c). New M4
> files are additions (§4). `M4-AC-16` proves M1's own behavior is unchanged by the
> `contract.py` extension.

## 6. What this erratum does not do

- It does not modify `HERMES-RUNTIME-M4.md`, or any M1/M2/M3 specification.
- It does not amend or rewrite commit `7e2395e`.
- It does not relax any enforcement control, acceptance criterion, or boundary.
- It does not permit any pre-existing production module beyond the three in §3.
- It does not convert M4's audit findings into approval: M4 remains unapproved until the full
  re-run and re-audit are green.
