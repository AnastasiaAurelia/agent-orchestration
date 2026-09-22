# Diana — POST-M7 ERRATA 001

Status: **normative erratum** to the regression clauses of
[`HERMES-RUNTIME-M4-ERRATA-001.md`](HERMES-RUNTIME-M4-ERRATA-001.md) (M4-REG-2),
[`HERMES-RUNTIME-M5.md`](HERMES-RUNTIME-M5.md) (M5-REG-2),
[`HERMES-RUNTIME-M6-ERRATA-001.md`](HERMES-RUNTIME-M6-ERRATA-001.md) (M6-E1-AC-1, M6-E1-AC-2) and
[`HERMES-RUNTIME-M7.md`](HERMES-RUNTIME-M7.md) (M7-AC-21, M7-REG-2).

Scope: **the replacement-set bookkeeping only.** This erratum enumerates, by set equality, the
pre-existing production-code files that **accepted post-M7 work** has replaced, so that each
milestone's regression clause once again states what it was written to state.

Every frozen specification remains **byte-identical**. None is edited in place. Every M1–M7
decision and acceptance criterion remains in force, unmodified, and this erratum weakens none of
them. Where this erratum and a frozen text appear to disagree about **authority**, the frozen text
wins and this erratum is the defect.

**This erratum grants no capability.** It adds no tool, no command, no scope, no network, no
subagent, no role and no repository operation. It changes no runtime behaviour, no enforcement
boundary, no Gate policy, no reviewer projection and no fail-closed path. It changes only what four
regression assertions compare against.

---

## 1. Why this erratum exists

Each milestone's regression clause makes a statement of the form:

> **M*N* replaced exactly these pre-existing production files, and no others.**

Each milestone's acceptance suite proves it by computing `git diff --name-status BASE..HEAD` and
comparing the `M`-status paths against a declared set.

That measurement is a **proxy**, and it was exact only while `HEAD` was that milestone's own
acceptance commit. The moment any later, separately-approved work replaces a file that already
existed at an earlier milestone's base, the proxy reports it against *every* earlier milestone —
even though no earlier milestone did anything. The statement is still true; the measurement has
stopped matching it.

This is not hypothetical and it is not a defect in the later work. Three accepted pull requests
replaced files that predate the M4, M5, M6 and M7 bases:

| Accepted change | Merge | Pre-existing production files it replaced |
|---|---|---|
| Hermes builder/reviewer production path repair | `#65` | `diana/adapters/hermes_live.py`, `diana/multiactor/actors.py`, `diana/multiactor/executors.py`, `diana/mutation/remediation_driver.py` |
| Diana Gate input hardening | `0f121ce` (in `#65`) | `diana/ci/build-gate-input.py`, `diana/ci/write-summary.py`, `diana/gate/diana-gate.py` |
| Generic write-scope derivation | `#66` | *(none — every file it touched is an M7 addition)* |

Left unreconciled, this compounds: every future accepted change to any pre-M7 file would add one
more spurious failure to four suites at once, and the failures would say nothing about the milestone
they are attributed to. Reconciling it is therefore maintenance of an existing proof, not a
relaxation of one.

## 2. The post-M7 replacement set — frozen, by set equality

**POST-M7-E1-D1 — The pre-existing production-code files replaced by accepted post-M7 work are
exactly these seven, and no others.**

| File | Accepted in | What changed, and nothing else |
|---|---|---|
| `diana/adapters/hermes_live.py` | `#65` | OAuth credential resolution for an OAuth-backed provider; an explicit per-turn schema set; the complete final response kept in memory for the caller; an explicit per-turn wall-clock bound. The `SAFE`/`D1` contract, `allowed_tools`, `read_scope` and the artifact shape are **unchanged**. Presentation remains presentation (M2-D2). |
| `diana/multiactor/actors.py` | `#65` | Adds `brief_reviewer`, which passes the already-selected attempt number and Diana's own verification result to the reviewer backend **in memory**. No durable scheduling state; `_accept_review` still re-derives the reviewed attempt from the digest-covered journal (M6-A1, M6-A2 unchanged). |
| `diana/multiactor/executors.py` | `#65` | Deterministic, fail-closed verdict extraction replacing a greedy regex; the reviewer turn presented `projection.REVIEWER_TOOLS`; evidence composed from the reviewed attempt's own reconciliation. `verdict.py`'s closed schema is **untouched**, and absence is still identical to rejection (M6-R5). |
| `diana/mutation/remediation_driver.py` | `#65` | The approved task text is stated in the builder prompt, and the verification command is framed as necessary and not sufficient. M4 ERRATA-002's `workdir`/`timeout`/allowlist guidance is retained verbatim. Presentation only; the dispatch boundary is unchanged. |
| `diana/ci/build-gate-input.py` | `0f121ce` | Per-cause refusal reasons, a GitHub annotation and step-summary line, and `--diff-filter=ACMRD` so a **deleted** review-sensitive path is visible to the Gate. Strictly more is refused than before; nothing is newly admitted. |
| `diana/gate/diana-gate.py` | `0f121ce` | The adapter's own failure envelope is recognised **and quoted**, and field/version refusals name the field. The closed input schema, every decision path and every exit code are **unchanged**; the only exit from the new branch is a refusal. |
| `diana/ci/write-summary.py` | `0f121ce` | Renders `FAIL` instead of a traceback when the gate produced no readable result. Reporting only. |

**POST-M7-E1-D2 — `README.md` is documentation, and the milestone docs manifests omitted it.**
Every milestone suite classifies a path as documentation with
`q.startswith("docs/") or q in DOCS_MANIFEST`, where `DOCS_MANIFEST` is `{".gitignore"}`. `README.md`
is neither, so a documentation edit was being counted as a production replacement in all four
suites. That is a gap in the manifest, not a replacement, and it is corrected here.

**POST-M7-E1-D3 — Everything else is excluded.** Accepted post-M7 work replaces **no** other
pre-existing production file. In particular it does not modify `diana/runtime/read_scope.py`,
`diana/runtime/contract.py`, `diana/adapters/hermes_patches.py`, `diana/mutation/mutation_policy.py`,
`diana/mutation/reconcile.py`, `diana/unattended/journal.py`, `diana/unattended/recovery.py`,
`diana/unattended/ownership.py`, or anything under `diana/ship/`, `diana/security/`,
`diana/preflight/` or `diana/advisory/`.

**POST-M7-E1-D4 — The corrected assertion is still set equality.** Each milestone's suite now
compares its modified-production set against

```
that milestone's declared set  ∪  POST-M7-E1-D1's set
```

It is **not** relaxed to a subset, not skipped, and not made advisory. A file outside **both** sets
still fails the assertion, which is the property the clause exists to provide. What changes is only
that a file this erratum accounts for is no longer attributed to a milestone that never touched it.

**POST-M7-E1-D4a — A post-M7 replacement counts only at a base where the file already existed.**
`diana/multiactor/actors.py` and `diana/multiactor/executors.py` are M6 *additions*, and
`diana/mutation/remediation_driver.py` is an M4 *addition*: at an earlier base they appear with
status `A`, not `M`, and that milestone's suite never sees them as replacements. Each suite
therefore admits `q ∈ POST-M7-E1-D1` only when `git cat-file -e BASE:q` succeeds. The membership is
asked of git rather than hand-listed per milestone, so it cannot drift from the repository, and the
comparison remains exact equality at every base.

**POST-M7-E1-D7 — The milestone suites this erratum corrects, by set equality.**
`diana/mutation/test-m4-bounded-mutation.sh`, `diana/multiactor/test-m6-multiactor.sh` and
`diana/product/test-m7-product.sh` are modified by this erratum and by nothing else. They are **test
code, not production code** — the classification `HERMES-RUNTIME-M4-ERRATA-001` finding 4 already
established for `diana/runtime_verify/test-m3-runtime-verify.sh`, applied consistently to the two
suites that previously lacked it. The classification is bounded by this enumerated set, not by a
blanket exclusion: a harness edit outside it still fails, in all three suites.

`diana/unattended/test-m5-unattended.sh` is deliberately **absent** from that set; see §4.

## 3. Two byte-identity clauses, corrected in the same way

**POST-M7-E1-D5 — `M6-E1-AC-2` (`diana/adapters/hermes_live.py` excluded and byte-identical).**
Both halves were one assertion. The first half — *excluded from M6's replacement set* — remains
**true and still asserted**: M6 did not touch this file. The second half — *byte-identical to the M6
freeze* — became false when `#65` replaced it. The assertion is therefore split: exclusion from M6's
set is checked unchanged, and byte-identity is required of every excluded file **except** those
POST-M7-E1-D1 accounts for. No excluded file becomes unchecked.

**POST-M7-E1-D6 — `M7-AC-21` (`diana/ci/build-gate-input.py`, `diana/gate/diana-gate.py`).**
The same correction, for the same reason. The remaining eleven frozen paths are still required to be
byte-identical to M7's accepted base, and the two named here are still required to be in
POST-M7-E1-D1's enumerated set — an unlisted change to either still fails.

Note the ordinary consequence, which is the mechanism working as designed: both files are in the
Gate's own `REVIEW_PATHS`, so a pull request changing them is `REQUIRE_HUMAN`. Reconciling the
regression bookkeeping does not, and must not, reduce that.

## 4. `M5-REG-2` — recorded, not repaired

`test-m5-unattended.sh` is frozen byte-identical by **M6-E1-AC-3**. Its `M5_PRODUCTION` constant
therefore **cannot be corrected in place**: any edit to that file fails M6-E1-AC-3, trading one
failure for another.

This erratum does **not** weaken M6-E1-AC-3 to make room, and does not edit the frozen file. The
situation is recorded instead:

- M5's substantive claim — *M5 replaced only `diana/runtime/blocking.py`, and `mutation_policy.py`
  is not in its set (M5-D19)* — remains **true**, and the `mutation_policy.py` half is still
  asserted and still passes inside M5's own suite.
- The file-set half of M5-REG-2 is **superseded by POST-M7-E1-D1/D4** and is re-asserted, for M5's
  base along with the other three, by `diana/ci/test-post-m7-replacement-set.sh`.
- `test-m5-unattended.sh` consequently continues to report one failure. It is a **known, governed,
  documented** state, not a hidden one, and it is the only one.

Two ways to close it exist, and both are governance decisions for a human rather than an
implementation choice:

1. Amend **M6-E1-AC-3** by its own erratum so that a later erratum may correct a regression
   *manifest constant* in a frozen M5 test while every M5 *proof* stays byte-identical. This
   requires distinguishing manifest from proof inside one file, which byte-identity cannot express,
   so it would mean replacing the byte-identity check with something weaker.
2. Leave M5's suite as it stands and treat POST-M7-E1-D4's repo-wide check as the maintained
   statement, which is what this erratum does.

Option 2 is taken because option 1 weakens a byte-identity check, and a byte-identity check that has
learned to make exceptions is no longer one.

## 5. What this erratum does not do

- It does not delete, skip or disable any assertion.
- It does not change any equality comparison into a subset comparison.
- It does not mark any failure as allowed.
- It does not exclude any new file in order to obtain a green suite. Files added by post-M7 work —
  including everything under `diana/autonomy/` and `diana/supervisors/` — are **additions** at every
  milestone base and have never appeared in any replacement set. They are named nowhere in
  POST-M7-E1-D1 because there is nothing to account for.
- It does not modify runtime authority, the enforcement boundary, Gate policy, the reviewer's
  read-only projection, write-scope enforcement, the command catalogue or any fail-closed path.
