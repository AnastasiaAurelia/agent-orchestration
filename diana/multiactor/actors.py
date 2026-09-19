#!/usr/bin/env python3
"""Diana M6: the multi-actor run loop (M6-D3, M6-D4, M6-D11, M6-D15, M6-D20).

M6 grants no capability. This module adds exactly three things to M5: Diana
chooses which actor acts, Diana re-projects the approved envelope for that actor
and proves the projection live before the turn, and Diana writes down which
actor acted before it acts.

## Why this is a separate loop rather than a change to `unattended.execute`

Phase 0 F13 measured M5's `execute()` driving a run to a terminal state in ONE
call, with no pre-terminal return point at which a different actor or backend
could be handed the work. Widening `execute()` to select actors was refused by
M6-ERRATA-001's replacement set, which permits `unattended.py` exactly one
change (`run_attempt` gains `actor`). So M6 has its own loop.

**It reuses M5's own functions rather than re-deriving their semantics**, private
names included: `_budget_state`, `_settle_item`, `_block_dependents`,
`_terminate`, `_finish`, plus `run_attempt` and `discharge_obligation`. Reaching
for a sibling module's underscore names is a style cost. Re-implementing budget
arithmetic, item settlement or write-ahead ordering would be a CORRECTNESS cost,
and M5-A1 exists precisely because two copies of one piece of state can be made
to disagree. The style cost is the cheaper of the two.

## Why there is no durable scheduling state at all

An earlier implementation kept a `review-state.json` ledger recording which
attempt last built an item and what its review decided, and claimed nothing
authority-relevant was read from it. **The independent attack pass falsified
that claim** (finding M6-A1): `_accept_review` took the reviewed attempt number
FROM the ledger, so a ledger edited to name an older build would have a genuine
reviewer verdict blessing work the reviewer never saw. The ledger was not
digest-bound, so nothing would have noticed.

The fix is not to authenticate the ledger -- it is to remove it from the
decision path. Every scheduling input is now DERIVED from the digest-covered
journal: the attempt under review is the most recent clean BUILDER attempt, and
the acting role of each attempt is the journal's. The only per-run memory is a
set of rejected build attempts held for the duration of one `execute()` call. A
resume loses it, and losing it causes a RE-REVIEW rather than a completion --
one more attempt out of the shared budget, and no authority granted.

`review-verdict-<n>.json` is still written. It is evidence for a human, keyed to
the attempt that produced it, and nothing reads it back to decide anything.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("runtime", "adapters", "mutation", "unattended"):
    sys.path.insert(0, str(_HERE.parent / _sub))
sys.path.insert(0, str(_HERE))

import blocking  # noqa: E402
import hermes_patches as _patches  # noqa: E402
import journal as _journal  # noqa: E402
import mutation_policy as _policy  # noqa: E402
import projection as _projection  # noqa: E402
import recovery as _recovery  # noqa: E402
import runlock as _runlock  # noqa: E402
import selftest as _selftest  # noqa: E402
import topology as _topology  # noqa: E402
import unattended as _unattended  # noqa: E402
import verdict as _verdict  # noqa: E402
import workitems as _workitems  # noqa: E402

BUILDER = _topology.BUILDER
REVIEWER = _topology.REVIEWER

# The behavioral probe for each role: a tool the role's projection must refuse,
# driven through Hermes's REAL dispatch funnel. The builder's probe is a tool
# outside the approved envelope entirely; the reviewer's is a mutating tool the
# builder is allowed and the reviewer is not, which is the exact authority
# difference between the two roles (M6-AC-2).
FORBIDDEN_PROBE = {BUILDER: "delegate_task", REVIEWER: "write_file"}
# A tool every projection grants, used to prove the install did not simply
# refuse everything -- an enforcement "proof" that denies all tools would pass a
# refusal-only probe while having broken the run.
PERMITTED_PROBE = "read_file"


def _write_json(path: Path, payload: dict) -> None:
    _journal.atomic_write(path, json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"))


# --- approval ------------------------------------------------------------

def approve(**kwargs) -> dict:
    """M5 approval, plus the frozen actor topology (M6-D1, M6-E1-D4).

    Ordering is deliberate: the journal records the topology digest BEFORE
    `actors.json` exists on disk. A crash between the two therefore leaves a
    record demanding a topology document that is absent, which `load_topology`
    refuses -- rather than a topology document nothing is bound to, which would
    be a role set no approval covers.
    """
    appraisal = _unattended.approve(**kwargs)
    run_directory = Path(appraisal["run_directory"])
    document = _topology.build(run_id=appraisal["run_id"])

    # Prove the composition BEFORE the topology is durable: a topology whose
    # projections could exceed the approval must never reach disk (M6-AC-5).
    union = _projection.prove_union_within_parent(document, appraisal["contract"])

    record = _journal.read(run_directory)
    record = json.loads(json.dumps(record))
    record["actor_topology_digest"] = _topology.digest(document)
    _journal.write(run_directory, record)
    _write_json(run_directory / _topology.DOCUMENT_NAME, document)

    appraisal["record"] = record
    appraisal["topology"] = document
    appraisal["projection_union"] = union
    return appraisal


# --- resume-time topology binding ----------------------------------------

def load_topology(run_directory, record: dict, contract_block: dict) -> dict:
    """Re-verify the topology on every resume (M6-D10), in both directions.

    Both halves are mandatory and neither implies the other: a document that
    does not match the journal is a role set the approval did not cover, and a
    journal digest with no document is an approval whose role set cannot be
    read. Either one is BLOCKED.
    """
    recorded = record["actor_topology_digest"]
    path = Path(run_directory) / _topology.DOCUMENT_NAME
    if recorded is None:
        if path.exists():
            raise blocking.Blocked(
                blocking.ACTOR_TOPOLOGY_DIGEST_MISMATCH,
                "an actor topology document exists but the journal binds no topology digest")
        raise blocking.Blocked(
            blocking.ACTOR_TOPOLOGY_MALFORMED,
            "this run declared no actor topology; it is an M5 single-actor run "
            "and cannot be driven as a multi-actor run")
    try:
        document = json.loads(_journal.artifact_path(run_directory, _topology.DOCUMENT_NAME)
                              .read_bytes().decode("utf-8"))
    except blocking.Blocked as exc:
        # `artifact_path` speaks the JOURNAL's vocabulary (missing artifact,
        # unsafe path). Re-coded here so an operator reading the reason learns
        # that the ACTOR TOPOLOGY is what could not be established -- one
        # over-broad code shared with the journal would hide which document
        # failed, which is the distinction M6's reason codes exist to keep.
        raise blocking.Blocked(
            blocking.ACTOR_TOPOLOGY_MALFORMED,
            f"actor topology unusable ({exc.code}): {exc.detail}") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise blocking.Blocked(
            blocking.ACTOR_TOPOLOGY_MALFORMED, f"unreadable actor topology: {exc}") from None
    _topology.validate(document)
    actual = _topology.digest(document)
    if actual != recorded:
        raise blocking.Blocked(
            blocking.ACTOR_TOPOLOGY_DIGEST_MISMATCH, f"{actual} != {recorded}")
    if document["run_id"] != record["run_id"]:
        raise blocking.Blocked(
            blocking.ACTOR_TOPOLOGY_MALFORMED,
            f"topology run_id {document['run_id']!r} != journal run_id {record['run_id']!r}")

    # Every actor already recorded must be one this topology declares. A journal
    # that survived a topology change would otherwise carry attempts attributed
    # to a role the run no longer has.
    for attempt in record["attempts"]:
        _topology.require_role(document, attempt["actor"])

    # M6-AC-5 again, on resume: the approval is only as narrow as the union of
    # what its roles may do, and that is re-proven rather than remembered.
    _projection.prove_union_within_parent(document, contract_block)
    return document


# --- projection installation ---------------------------------------------

def install_projection(role: str, contract_block: dict, topology_doc: dict) -> dict:
    """Install `role`'s projection and PROVE it live behaviorally (M6-D14/D15).

    Phase 0 F5 measured enforcement as a property of the PROCESS: the guard
    closes over one allowed-set, and the install that is live is the last one
    installed. So a proof taken once per process says nothing about the actor
    now acting, and this runs before EVERY actor turn rather than once at
    resume.

    The proof drives Hermes's real `_dispatch_authorized_once` funnel -- the one
    the inline-executor bypass lives on -- and requires BOTH directions: a tool
    this role may not use does not reach its handler, and a tool it may use
    does. A one-sided probe would pass for an install that refused everything.
    """
    projected = _projection.derive(role, contract_block, topology_doc)
    # Audit finding M6-A3: `derive` proves the subset property, and this used to
    # rely on that alone -- so a `derive` that had been replaced, wrapped, or
    # simply changed installed whatever it returned. The behavioral probe did
    # not save it either: a probe covers ONE forbidden tool, and a widened
    # envelope can satisfy that probe while growing on a different axis.
    _projection.prove_subset(projected, contract_block)
    # Independent-review finding R-1: subset-of-the-approval is necessary and
    # NOT sufficient. Every role's envelope is a subset of the approval, so the
    # subset proof cannot tell the REVIEWER's frozen envelope from a REVIEWER
    # that has acquired `terminal` -- which was measured running a command
    # through the real funnel. This proves it is the role's frozen boundary.
    _projection.prove_role_shape(projected, contract_block)
    envelope = projected["capability_envelope"]

    _patches.install_confinement(projected["read_scope"])
    _patches.install_capability(envelope["allowed_tools"], policy=_policy.MutationPolicy(envelope))

    # Independent-review finding R-3: from here on the boundary has already been
    # REPLACED, so every remaining failure path must leave the process in a
    # state that grants nothing. Returning silently would leave an unproven
    # projection live; re-raising without clearing would leave it live too. An
    # empty allowed-tools set refuses every tool at the same choke point, which
    # is the only fail-closed state available -- `uninstall()` would restore
    # Hermes's UNPATCHED dispatch, which is fail-OPEN and must never be the
    # response to a failed proof.
    try:
        if not _patches.confinement_live() or not _patches.capability_live():
            raise blocking.Blocked(
                blocking.ACTOR_PROJECTION_NOT_PROVEN,
                f"{role}: the boundary is not live after installing the projection")

        forbidden = FORBIDDEN_PROBE[role]
        denied = _selftest._drive_real_dispatch(forbidden)
        if denied["executed"]:
            raise blocking.Blocked(
                blocking.ACTOR_PROJECTION_NOT_PROVEN,
                f"{role}: {forbidden} reached its handler through the real dispatch funnel")
        permitted = _selftest._drive_real_dispatch(PERMITTED_PROBE)
        if not permitted["executed"]:
            raise blocking.Blocked(
                blocking.ACTOR_PROJECTION_NOT_PROVEN,
                f"{role}: {PERMITTED_PROBE} did not reach its handler, so this install "
                "refuses everything and proves nothing")
    except BaseException:
        deny_all()
        raise
    return {"role": role, "projection": projected,
            "proof": {"forbidden_probe": forbidden, "forbidden_executed": False,
                      "permitted_probe": PERMITTED_PROBE, "permitted_executed": True}}


def deny_all() -> None:
    """Put the capability boundary into a state that grants nothing.

    Independent-review finding R-3. The empty allowed-tools set is refused by
    the SAME guard every tool call passes through, so this is the existing
    boundary parameterised to deny rather than a second mechanism.
    """
    _patches.install_capability((), policy=None)


# --- Diana-owned actor selection ------------------------------------------

def _is_clean_build(attempt: dict) -> bool:
    """A BUILDER attempt that finished, reconciled, and stayed in the envelope."""
    return (attempt.get("actor") == BUILDER
            and attempt.get("state") == "CLOSED"
            and attempt.get("reconciled") is True
            and attempt.get("within_envelope") is True
            and attempt.get("turn_error") is None)


def clean_build_by_number(record: dict, number: object) -> dict | None:
    """The journal's entry for `number`, but ONLY if it is a clean BUILDER attempt.

    Finding M6-A2: an attempt number alone does not say what the attempt was.
    Every use of a remembered number is re-validated against the digest-covered
    journal here, so a number that has come to mean something else -- a reviewer
    attempt, a failed build, an attempt that left the envelope -- resolves to
    nothing rather than to the wrong evidence.
    """
    for attempt in record.get("attempts") or []:
        if attempt.get("attempt") == number:
            return attempt if _is_clean_build(attempt) else None
    return None


def select_actor(item_id: str, record: dict, pending: dict, rejected: set,
                 *, verified: bool) -> str:
    """Diana chooses the acting role. Deterministic, and no input is the agent's.

    The rule in full: review a build only when THIS ITEM has a build that closed
    cleanly inside the envelope, AND Diana's own verification of this item
    passes, AND that build has not already been rejected. Everything else is the
    builder's turn.

    `pending` maps item -> the attempt number of that item's clean build, and
    `rejected` holds build numbers whose review rejected them. Both live for one
    `execute()` call only.

    **Why per-item, and why in memory.** Finding M6-A2: journal attempt entries
    carry no item id (M6-E1-D5 froze the key set), so "the most recent clean
    build" is a statement about the RUN, not about an item -- and in a run with
    independent items it let one item's build be reviewed as another's. Scoping
    must therefore be held by the loop. It is held in memory rather than on disk
    because finding M6-A1 established that unauthenticated on-disk scheduling
    state is on the authority path the moment anything reads it back: a crash
    loses this map, and losing it makes the next attempt a BUILD rather than a
    completion, which costs one attempt of the shared budget and grants nothing.
    The number is still re-validated against the journal before use
    (`clean_build_by_number`).
    """
    if not verified:
        return BUILDER
    number = pending.get(item_id)
    if number is None or number in rejected:
        return BUILDER
    if clean_build_by_number(record, number) is None:
        return BUILDER
    return REVIEWER


# --- the run loop --------------------------------------------------------

def execute(run_directory, *, builder, reviewer, verify,
            expected_run_id: str | None = None) -> dict:
    """Drive a multi-actor run to a terminal state (M6-D20's fixed sequence).

    `builder(contract_block, item_id)` and `reviewer(contract_block, item_id)`
    are executor backends -- injected callables at the seam Phase 0 F14 found
    already present. `reviewer` must expose `.verdict` after its call.

    `verify(contract_block, item_id)` is Diana's own deterministic verification.
    It is NOT the reviewer's job (M6-R2): a verification command executes
    repository code and can therefore mutate, so granting it to a read-only role
    would return the mutation authority the role exists to withhold.
    """
    # M6-D11 / audit finding M6-A4: one live executor per run, claimed BEFORE
    # the journal is read, so a peer cannot get as far as discharging an
    # obligation that belongs to a turn still in flight.
    with _runlock.RunLock(run_directory) as lock:
        return _execute_locked(run_directory, builder=builder, reviewer=reviewer,
                               verify=verify, expected_run_id=expected_run_id,
                               holder=lock.holder)


def _execute_locked(run_directory, *, builder, reviewer, verify,
                    expected_run_id, holder) -> dict:
    loaded = _recovery.load_run(run_directory, expected_run_id=expected_run_id)
    record, contract_block = loaded["record"], loaded["contract"]
    policy, items_doc = loaded["policy"], loaded["items"]
    pending: dict[str, int] = {}
    rejected: set[int] = set()

    topology_doc = load_topology(run_directory, record, contract_block)

    # M5-D10 ordering is unchanged: enforcement, then freshness, then work. The
    # parent envelope is installed first so a resume that cannot prove the
    # BASELINE boundary never reaches the per-actor projection.
    _recovery.reestablish_enforcement(contract_block)
    _recovery.require_fresh(record, allow_dirty_drift=_journal.attempts_used(record) > 0)

    if _journal.has_outstanding_obligation(record):
        # M6-D19/M6-AC-8: the obligation is discharged BEFORE any actor is
        # selected. A handoff across an unreconciled attempt is not refused by a
        # check placed later -- there is no path that reaches actor selection
        # with an obligation outstanding.
        outcome = _unattended.discharge_obligation(run_directory, record, contract_block, policy)
        record = outcome["record"]
        record = _unattended._settle_item(run_directory, record, items_doc, outcome,
                                          contract_block, _never_finished, policy)
        if outcome["blocked"]:
            return _unattended._finish(run_directory, record, contract_block, policy, items_doc)
        record = _journal.read(run_directory)

    while True:
        if record["state"] not in (_journal.APPROVED, _journal.ARMED, _journal.RECONCILED):
            raise blocking.Blocked(
                blocking.JOURNAL_ILLEGAL_TRANSITION,
                f"the loop cannot act on state {record['state']!r}")

        if _journal.is_cancelled(record):
            return _unattended._terminate(
                run_directory, record, contract_block, policy, items_doc,
                _journal.FAILED, blocking.RUN_CANCELLED,
                record["cancellation"].get("reason", "") or "cancelled")

        if _workitems.all_complete(items_doc, record["items"]):
            return _unattended._terminate(
                run_directory, record, contract_block, policy, items_doc,
                _journal.COMPLETE, _journal.WORK_FINISHED,
                "every declared work item is COMPLETE")

        exhausted, code, detail = _unattended._budget_state(record, policy)
        if exhausted:
            return _unattended._terminate(
                run_directory, record, contract_block, policy, items_doc,
                _journal.FAILED, code, detail)

        eligible = _workitems.eligible_items(items_doc, record["items"])
        if not eligible:
            blocked = _workitems.first_blocked(items_doc, record["items"])
            if blocked is not None:
                return _unattended._terminate(
                    run_directory, record, contract_block, policy, items_doc,
                    _journal.BLOCKED,
                    blocked.get("reason_code") or blocking.DEPENDENCY_BLOCKED,
                    f"item {blocked['id']!r}: {blocked.get('detail', '')}")
            raise blocking.Blocked(
                blocking.WORK_ITEMS_MALFORMED,
                "no item is eligible and none is blocked: item state is inconsistent")

        item_id = eligible[0]
        verified = bool(verify(contract_block, item_id))
        role = select_actor(item_id, record, pending, rejected, verified=verified)

        if record["state"] in (_journal.APPROVED, _journal.RECONCILED):
            record = _journal.transition(
                run_directory, record, _journal.ARMED,
                note=f"next attempt acts as {role}")

        # M6-D15: projection proven live for THIS actor, immediately before its
        # turn and after the arming transition, so nothing runs between the
        # proof and the work it guards.
        installed = install_projection(role, contract_block, topology_doc)

        driver = builder if role == BUILDER else reviewer
        outcome = _unattended.run_attempt(run_directory, record, contract_block, policy,
                                          driver, item_id=item_id, actor=role)
        record = outcome["record"]
        attempt_entry = record["attempts"][-1]

        finished = False
        if role == REVIEWER and not outcome["blocked"] and outcome.get("turn_error") is None:
            finished = _accept_review(run_directory, record, item_id, attempt_entry,
                                      pending[item_id], getattr(driver, "verdict", None))
            if not finished:
                rejected.add(pending[item_id])
        elif role == BUILDER and _is_clean_build(attempt_entry):
            pending[item_id] = attempt_entry["attempt"]

        record = _unattended._settle_item(
            run_directory, record, items_doc, outcome, contract_block,
            (lambda *_a, **_k: True) if finished else _never_finished,
            policy, item_id=item_id)
        if outcome["blocked"]:
            return _unattended._finish(run_directory, record, contract_block, policy, items_doc)
        record = _journal.read(run_directory)
        installed_role = installed["role"]  # retained for the run record's legibility
        del installed_role


def _never_finished(*_args, **_kwargs) -> bool:
    """Completion is decided by the reviewer-gated path alone (M6-R4).

    A BUILDER attempt can never complete an item on its own, however clean its
    reconciliation: in a run that declares a REVIEWER, "the diff stayed inside
    the envelope" is not the same statement as "the work is done", and only the
    second one closes an item.
    """
    return False


def _accept_review(run_directory, record: dict, item_id: str, attempt_entry: dict,
                   reviewed_number: int, raw_verdict) -> bool:
    """Validate a verdict against the JOURNAL. Returns whether the item is finished.

    `reviewed_number` is the build Diana scheduled this very review for, moments
    earlier in this same loop. It is re-validated against the digest-covered
    journal before use, and both roles come from the attempt entries Diana wrote
    BEFORE those turns ran -- so nothing the reviewer says, and nothing on disk
    outside the journal, can change whose work this verdict is about
    (M6-R7, findings M6-A1 and M6-A2).
    """
    reviewed_attempt = clean_build_by_number(record, reviewed_number)
    if reviewed_attempt is None:
        raise blocking.Blocked(
            blocking.REVIEW_VERDICT_WRONG_ACTOR,
            f"attempt {reviewed_number!r} is not a clean build of item {item_id!r} to review")

    accepted = _verdict.accept(raw_verdict, producing_attempt=attempt_entry,
                               reviewed_attempt=reviewed_attempt)
    _write_json(Path(run_directory) / f"review-verdict-{attempt_entry['attempt']:03d}.json",
                {"attempt": attempt_entry["attempt"], "item_id": item_id,
                 "reviewed_attempt": reviewed_attempt["attempt"],
                 "actor": attempt_entry["actor"], "verdict": accepted})
    return accepted["decision"] == _verdict.PASS
