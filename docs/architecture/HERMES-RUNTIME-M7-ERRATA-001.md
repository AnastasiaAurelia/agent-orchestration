# HERMES-RUNTIME-M7 — ERRATA 001

Status: **normative erratum** to [`HERMES-RUNTIME-M7.md`](HERMES-RUNTIME-M7.md).
Scope: **corrects the approval-binding mechanism**, which pre-implementation falsification proved
cannot work as frozen, and records the result of the entry-point preflight.

`HERMES-RUNTIME-M7.md` remains **byte-identical**; it is not edited in place. Every frozen M7
decision remains in force except where this erratum names a correction. Where this erratum and the
frozen text appear to disagree about **authority**, the frozen text wins and this erratum is the
defect. M1–M6 and all their errata remain frozen and unmodified.

**This erratum grants no capability.** It removes an impossible binding and replaces it with a
working one over the same authoritative objects. It adds no tool, command, scope, actor or approval
power.

---

## 1. Preflight A — what was falsified, and how

M7-D7/D9/D12 froze that the Proposal "contains the exact `ExecutionContract` that `approve()` would
persist — same `run_id`, same `created_at`" and is "identified by the contract digest it predicts".

**Measured, against the accepted builders:**

| Probe | Result |
|---|---|
| Two proposals built from identical inputs, nothing pinned | digests **DIFFER** |
| Same, with `run_id` **and** `created_at` pinned | digests **SAME** — the builder is deterministic once both are fixed |
| Does `unattended.approve()` accept `created_at`? | **No.** Its keyword parameters are `task, repo_root, allowed_commands, write_roots, max_attempts, total_seconds, runs_base, run_id, max_timeout_s, items` |
| Predicted digest vs the digest `approve()` persisted | **`sha256:c89313ed…` vs `sha256:876c045c…` — no match** |
| Re-predicting with `approve()`'s own `created_at` | **exact match** — proving `created_at` is the *sole* difference |
| Two contracts differing only in `created_at` | differing fields: `['created_at']`; digests differ |

So the frozen binding is **impossible by construction**: `created_at` is inside `CONTRACT_KEYS` and
therefore inside the digest, `approve()` always generates a fresh one, and M7-REG-2 forbids adding a
parameter to it. A proposal can never predict the digest that will be persisted.

**`created_at` is provenance, not authority.** Grepping every use shows it is constructed and stored
and never read to make a decision — no scope, tool, command, risk, depth, budget or freshness check
consults a contract's `created_at`. (`runpolicy` has its own separate `created_at` used for deadline
arithmetic; that is the run policy, not the contract.)

## 1b. Preflight A — what HELD, and by a different mechanism than the spec named

All four target states were measured. Every one changes the predicted digest:

| Target state | `target.git_commit` | `target.dirty` | `repo_profile` | digest changes |
|---|---|---|---|---|
| clean at commit A | baseline | `False` | baseline | — |
| advanced to commit B | **changed** | `False` | unchanged | **yes** |
| dirty: untracked new file | unchanged | **`True`** | **changed** | **yes** |
| dirty: tracked modification | unchanged | **`True`** | unchanged | **yes** |
| **gitignored change only** | unchanged | `False` | **changed** | **yes** |

The last row is the important one. A gitignored-only modification moves **neither** `git_commit`
**nor** `dirty` — the M5-F3 blind spot, where `git status --porcelain` sees nothing. It is caught
anyway, because `repo_profile.inventory` gains the new path (`build/a.bin` in the isolated probe).

**M7-D12's conclusion stands; its stated mechanism was incomplete.** Target freshness at approval is
carried by `target.git_commit`, `target.dirty` **and** `repo_profile`, and the third is what covers
the case the first two miss. This erratum records the mechanism accurately rather than leaving the
spec asserting a narrower reason than the one that actually holds.

---

## 2. The correction

**M7-E1-D1 — Approval binds a PROPOSAL digest over the authority-bearing objects, not the contract
digest.** The proposal digest covers:

- every `CONTRACT_KEYS` field **except `created_at`** — i.e. `contract_version`, `run_id`, `workflow`,
  `depth`, `risk`, `task`, `target`, `capability_envelope`, `read_scope`, `repo_profile`;
- the work-item document digest (`workitems.digest`);
- the actor-topology digest (`topology.digest`).

These are exactly the objects M5 and M6 already treat as authoritative and already re-verify on every
resume. **No duplicate freshness mechanism is introduced**: target freshness rides inside `target` and
`repo_profile`, both of which `approve()` rebuilds from the live repository (§1b).

**M7-E1-D2 — `created_at` is excluded because it is provenance, and its exclusion is proven, not
assumed.** It is measured non-decisional (§1), and an acceptance criterion asserts that changing it
alone does not change the proposal digest while changing any other field does.

**M7-E1-D3 — Approval re-derives and re-validates before any run is created; a stale proposal is
refused, never rebuilt and executed.** On approval Diana re-builds the proposal from the recorded
intent against the **live** repository, recomputes the proposal digest, and compares it to the
approved one. A mismatch is refused with its own reason. This closes the displayed → approved →
created TOCTOU window: the object that authorises the run is recomputed at the moment the run is
created, not trusted from display time.

**M7-E1-D4 — After `approve()` returns, the persisted contract must equal the predicted contract on
every field except `created_at`; any other difference aborts.** This is the check that makes M7-E1-D1
load-bearing rather than advisory: the proposal is a prediction, and a prediction that turns out
false must stop the run rather than be papered over. A run aborted here has already created durable
state, so the abort is reported with the run directory named.

**M7-E1-D5 — The approval API takes the proposal digest and has no free-text branch.** Unchanged from
M7-D13, restated because it is the property the corrected binding must preserve.

**M7-E1-D6 — M7-D7, M7-D9 and M7-D12 are corrected as follows, and only as follows.** M7-D7's "same
`created_at`" is struck; the proposal predicts every authority-bearing field and does not predict
`created_at`. M7-D9's "identified by the contract digest it predicts" becomes "identified by the
proposal digest of M7-E1-D1". M7-D12's conclusion is unchanged and its mechanism is as §1b measured.
Every other M7 decision is untouched.

---

## 3. Preflight B — result: HELD, no correction required

M7-REG-2 permits no modification of pre-existing production files. The frozen M7-D1/D2 surface was
tested against that constraint by inventorying the additive options:

- Root-level executable scripts are this repository's **established** invocation pattern —
  `install.sh`, `verify.sh`, `uninstall.sh`, `test-verify.sh` — each allow-listed individually in the
  `.gitignore` manifest, which M5/M6's own assertions classify as a manifest rather than production
  code (verified: M6-E1-AC-1 passes with the M7 spec and manifest line already committed).
- `diana-do` is free of collision at root; `diana/` is a directory and cannot also be a file.
- `.claude/commands/` is not currently allow-listed, and `install.sh` — which is what copies commands
  into a consumer project — **cannot be edited**.

**Conclusion:** a root-level additive executable satisfies the requirement with no edit to
`install.sh`, any existing slash command, `ao.py`, `ship.py`, or any packaging file. The stable,
documented invocation is:

```
./diana-do "<natural-language goal>"
```

It requires no internal Python module name, no runtime class, no journal API and no M1–M6
terminology. **No erratum is required for Preflight B.**

**M7-E1-D7 — Carried limitation, recorded rather than worked around:** because `install.sh` may not
be edited, a new slash command cannot travel to a consumer project. A slash command added under
`.claude/commands/` is discoverable in **this** repository only. The CLI is therefore the stable
product path and the only path M7 acceptance drives; the slash command is a convenience asset.

---

## 4. A second consequence of the empty replacement set

**M7-E1-D8 — M7 refusals use their own closed vocabulary in an additive module, not
`diana/runtime/blocking.py`.** `blocking.Blocked` rejects any code outside `ALL_REASON_CODES` — by
design, so a caller cannot invent an unfalsifiable reason — and `blocking.py` is a pre-existing
production file M7 may not modify (M7-REG-2).

This is a better fit than a workaround, and it is frozen as such: M7's refusals happen **before any
run exists**. They are pre-authority refusals, not run-blocking ones, and conflating them with M5/M6
reason codes would blur a real distinction — a refused proposal has no journal, no contract and no
run to be blocked. The vocabulary is closed, each code is distinct, and unknown codes are refused
exactly as `blocking.py` refuses them.

---

## 5. Acceptance criteria added by this erratum

In force alongside M7-AC-1 … M7-AC-25, none of which is weakened.

| # | Criterion |
|---|---|
| **M7-E1-AC-1** | The proposal digest is **reproducible**: the same intent against the same target with the same pinned `run_id` yields the same digest across processes. |
| **M7-E1-AC-2** | Changing **only** `created_at` does not change the proposal digest, while changing any authority-bearing field does — asserted field by field, not by one over-broad check. |
| **M7-E1-AC-3** | Each of the four measured target states yields a **different** proposal digest, the **gitignored-only** case included, and that case is proven to move `repo_profile` while `target` stays put. |
| **M7-E1-AC-4** | A **stale proposal is refused** with its own reason code, and **no run directory is created** — proven by approving a digest captured before the target moved. |
| **M7-E1-AC-5** | Approval **never rebuilds-and-runs**: the refusal path is proven not to fall through to a freshly derived proposal. |
| **M7-E1-AC-6** | The post-approve verification of M7-E1-D4 fires: a persisted contract differing on any field other than `created_at` aborts the run and names the run directory, proven by a falsifier that perturbs one field. |
| **M7-E1-AC-7** | The approval API **rejects free text**: it accepts a digest, and agreeable prose is refused by type rather than by content matching. |
| **M7-E1-AC-8** | The user-facing invocation is `./diana-do "<goal>"`, is executable from a clean shell, and requires no module path, runtime class, journal API or M1–M6 term. |
| **M7-E1-AC-9** | `install.sh`, all six existing slash commands, `ship.py`, `ao.py` and every CI adapter are **byte-identical** to `c77208a`; the modified pre-existing non-doc production set is **empty**, asserted by equality. |
| **M7-E1-AC-10** | M7's refusal vocabulary is closed and distinct from `blocking.ALL_REASON_CODES`; an unknown M7 code is refused. |

---

## 6. What this erratum does not do

- It does not widen M7-REG-2; the permitted-replacement set stays **empty**.
- It does not change what approval covers (M7-D15) or make approval category B (M7-D21/D22).
- It does not introduce a freshness mechanism parallel to M5-D11's; it binds the objects M5/M6
  already treat as authoritative.
- It does not weaken M7-D10 (ambiguity refuses), M7-D11 (no silent clamp) or M7-D17 (no widening).
