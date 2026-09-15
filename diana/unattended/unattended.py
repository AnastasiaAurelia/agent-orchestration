#!/usr/bin/env python3
"""Diana M5: the unattended bounded run (spec: HERMES-RUNTIME-M5.md).

Approve one envelope, leave, and return to COMPLETE, FAILED or BLOCKED -- with
the envelope approved at hour zero still PROVABLY the envelope in force at hour
six, across process death and resumption.

## M5 grants nothing

M5-D1: the envelope is M4's `BOUNDED_REMEDIATION` envelope byte-for-byte. Same
`allowed_tools`, same `write_scope`, same exact-match `allowed_commands`,
`risk = ELEVATED` derived from the envelope, `depth = D2` from the same
certified class. No tool is added, no scope widened, no workflow class
certified. What changes is duration, and the fact that the run has a durable
identity that outlives the process running it.

## The one ordering rule everything rests on

M5-D5, write-ahead: nothing that can mutate begins until the contract, the run
policy and the pre-turn snapshot are durable and fsynced. Phase 0 F12 proved
that is sufficient -- a fresh process holding only those could reconcile a
crashed run and catch the gitignored escape git could not see -- and F2 proved
that without it a SIGKILL leaves an out-of-envelope mutation with nothing on
disk recording that a run ever happened.

## Retry is not resumption

M5-D16. A new attempt starts only from ARMED, which by M5-D5 means a FRESH
pre-turn snapshot is already durable -- so each attempt is independently
attributable and a later attempt is never blamed for an earlier one's changes.
An attempt whose predecessor is unreconciled is refused outright, because
re-running a partially applied mutation over an unaudited tree compounds exactly
the damage reconciliation exists to detect.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("runtime", "adapters", "mutation", "profile", "advisory"):
    sys.path.insert(0, str(_HERE.parent / _sub))
sys.path.insert(0, str(_HERE))

import blocking  # noqa: E402
import contract as _contract  # noqa: E402
import journal as _journal  # noqa: E402
import ownership as _ownership  # noqa: E402
import reconcile as _reconcile  # noqa: E402
import recovery as _recovery  # noqa: E402
import remediate as _remediate  # noqa: E402
import report as _report  # noqa: E402
import runpolicy as _runpolicy  # noqa: E402
import workitems as _workitems  # noqa: E402


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _utc(ts: _dt.datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _snapshot_name(attempt: int) -> str:
    return f"pre-turn-snapshot-{attempt:03d}.json"


def _reconciliation_name(attempt: int) -> str:
    return f"reconciliation-{attempt:03d}.json"


def _turn_record_name(attempt: int) -> str:
    return f"turn-record-{attempt:03d}.json"


def _write_json(path: Path, payload: dict) -> None:
    _journal.atomic_write(
        path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))


# --- approval (APPROVED -> ARMED) ----------------------------------------

def approve(*, task: str, repo_root: str, allowed_commands, write_roots=None,
            max_attempts: int = _runpolicy.DEFAULT_MAX_ATTEMPTS,
            total_seconds: int = 3600, runs_base: str | None = None,
            run_id: str | None = None, max_timeout_s: int = 300,
            items=None) -> dict:
    """Create the run: contract, policy and journal, all durable.

    This is the ONLY place a contract is built. Every later phase reuses it and
    none may regenerate it (M5-D12).
    """
    repo_root = os.path.realpath(repo_root)
    contract_block = _remediate.build_contract(
        task=task, repo_root=repo_root, git_commit="pending", dirty=False,
        allowed_commands=allowed_commands, write_roots=write_roots,
        max_timeout_s=max_timeout_s, run_id=run_id)

    # Bind the contract to the target's ACTUAL identity at approval time, so the
    # freshness check of M5-D11 has something real to compare against. Phase 0
    # F9 found nothing ever compared target.git_commit to the live repository;
    # this is the value that check will use.
    observed = _recovery.observe_target(repo_root)
    contract_block["target"]["git_commit"] = observed["git_commit"]
    contract_block["target"]["dirty"] = observed["dirty"]
    _contract.validate(contract_block, accept=(_contract.M4_CLASS,))

    run_directory = _contract.run_dir(contract_block["run_id"], runs_base)

    # Diana-owned state lives OUTSIDE the target (M1 D9, M5-D3). Inside it, the
    # journal would be seen by Diana's own reconciliation as a change to the
    # target, and every freshness check would observe a permanently dirty tree
    # that the run itself created. Refused rather than tolerated, because the
    # failure it causes is silent and looks like target drift.
    resolved_runs = Path(os.path.realpath(run_directory))
    resolved_root = Path(os.path.realpath(repo_root))
    if resolved_runs == resolved_root or resolved_root in resolved_runs.parents:
        raise blocking.Blocked(
            blocking.JOURNAL_PATH_UNSAFE,
            f"the run directory {resolved_runs} is inside the target {resolved_root}; "
            "Diana-owned state must live outside the repository it audits")

    _journal.open_dir(run_directory, create=True)
    contract_path, contract_digest = _contract.persist(
        contract_block, base=runs_base, accept=(_contract.M4_CLASS,))

    policy = _runpolicy.build(run_id=contract_block["run_id"],
                              max_attempts=max_attempts, total_seconds=total_seconds)
    _write_json(Path(run_directory) / _recovery.POLICY_NAME, policy)

    # ERRATA-001 M5-E1-D1/D5: the item set is fixed here and the graph is
    # validated BEFORE the run is armed, so an invalid graph can never have
    # executed anything. A run with no declared items is one implicit item, so
    # every M5 caller that predates the erratum keeps working unchanged.
    # `items=None` means "not decomposed" and yields one implicit item, so every
    # M5 caller that predates the erratum keeps working unchanged. An EXPLICITLY
    # empty list is a different statement -- the approver declared no work -- and
    # is refused rather than silently turned into one item.
    declared = ([{"id": "item-1", "task": task, "depends_on": []}] if items is None
                else list(items))
    items_doc = _workitems.build(run_id=contract_block["run_id"], items=declared)
    _write_json(Path(run_directory) / _recovery.WORK_ITEMS_NAME, items_doc)

    record = _journal.new_record(
        run_id=contract_block["run_id"],
        contract_digest=contract_digest,
        run_policy_digest=_runpolicy.digest(policy),
        work_items_digest=_workitems.digest(items_doc),
        items=_workitems.initial_status(items_doc),
        target_binding={"repo_root": repo_root, "git_commit": observed["git_commit"],
                        "dirty": observed["dirty"], "observed_at": _utc(_now())})
    _journal.write(run_directory, record)
    return {"run_id": contract_block["run_id"], "run_directory": str(run_directory),
            "contract": contract_block, "policy": policy, "record": record,
            "items": items_doc,
            "contract_digest": contract_digest, "contract_path": str(contract_path)}


def cancel(run_directory, reason: str = "operator cancelled") -> dict:
    """Record a durable cancellation (M5-E1-D11). Diana-owned and one-way.

    Deliberately does NOT terminate the run here. M5-E1-D12: an outstanding
    reconciliation is still owed, and the run reaches its terminal state through
    the normal loop, which discharges that obligation first. A cancel that also
    terminated would be a way to stop without auditing what already happened.
    """
    record = _journal.read(run_directory)
    updated = _journal.cancel(run_directory, record, reason=reason)
    return {"run_id": updated["run_id"], "cancelled": True,
            "cancellation": updated["cancellation"], "record": updated}


# --- the obligation (M5-D6, M5-D15) --------------------------------------

def discharge_obligation(run_directory, record: dict, contract_block: dict,
                         policy: dict) -> dict:
    """Reconcile an attempt that was in flight. Quiescence FIRST (M5-D15).

    Called both by the live loop after a turn and by a resume that finds an
    outstanding obligation. It is the same code in both cases on purpose: an
    audit that behaves differently depending on whether anybody was watching is
    not an audit.
    """
    attempt = _journal.open_attempt(record)
    if attempt is None:
        raise blocking.Blocked(
            blocking.RECONCILIATION_OBLIGATION_OUTSTANDING,
            "the journal shows an in-flight turn but no open attempt to reconcile")

    if record["state"] == _journal.TURN_ACTIVE:
        record = _journal.transition(run_directory, record, _journal.RECONCILING,
                                     note="discharging reconciliation obligation")

    # Reconciling a target a previous tree is still writing to is a reading of a
    # moving object, so quiescence precedes the diff and is PROVEN, not assumed.
    quiescence = _ownership.require_quiescent(
        record["run_id"], grace_seconds=float(policy["quiescence_grace_seconds"]))

    # AUDIT FINDING M5-A2: read through the safety check, so a snapshot replaced
    # by a symlink cannot make Diana reconcile against a "before" state someone
    # else chose.
    try:
        before = _journal.read_artifact(run_directory, attempt["snapshot_file"])
    except blocking.Blocked as exc:
        # Without the pre-turn snapshot the diff cannot be computed at all, and
        # "I cannot tell what happened" is never "nothing happened" (M5-D9).
        record = _journal.transition(
            run_directory, record, _journal.BLOCKED,
            terminal_reason=exc.code,
            terminal_detail=f"pre-turn snapshot unusable: {exc.detail}"[:400])
        raise

    root = contract_block["target"]["repo_root"]
    after = {"files": _reconcile.snapshot(root), "git": _reconcile.git_status(root)}
    result = _reconcile.reconcile(
        root=root, before=before["files"], after=after["files"],
        before_git=before["git"], after_git=after["git"],
        write_scope=contract_block["capability_envelope"]["write_scope"])
    result["quiescence"] = quiescence
    _write_json(Path(run_directory) / _reconciliation_name(attempt["attempt"]), result)

    record = _journal.update_attempt(
        run_directory, record, state="CLOSED", reconciled=True,
        within_envelope=bool(result["within_envelope"]),
        reconciliation_file=_reconciliation_name(attempt["attempt"]),
        ended_at=_utc(_now()))

    if not result["within_envelope"]:
        offenders = [entry["path"] for entry in result["paths_outside_write_scope"]]
        record = _journal.transition(
            run_directory, record, _journal.BLOCKED,
            terminal_reason=blocking.RECONCILIATION_MISMATCH,
            terminal_detail=f"{len(offenders)} path(s) outside write_scope: {offenders[:10]}")
        return {"record": record, "reconciliation": result, "blocked": True}

    record = _journal.transition(run_directory, record, _journal.RECONCILED,
                                 note="diff stayed inside the envelope")
    return {"record": record, "reconciliation": result, "blocked": False}


# --- one attempt (ARMED -> TURN_ACTIVE -> RECONCILING -> RECONCILED) ------

def run_attempt(run_directory, record: dict, contract_block: dict, policy: dict,
                turn_driver, item_id: str | None = None) -> dict:
    """Arm, snapshot durably, run the turn, then discharge the obligation."""
    if record["state"] != _journal.ARMED:
        raise blocking.Blocked(
            blocking.JOURNAL_ILLEGAL_TRANSITION,
            f"an attempt may only start from ARMED, not {record['state']!r}")

    attempt_number = _journal.attempts_used(record) + 1
    snapshot_file = _snapshot_name(attempt_number)

    # M5-D5 write-ahead: the audit's inputs become durable BEFORE any mutation.
    # Each attempt gets its OWN snapshot so attribution stays per-attempt
    # (M5-D16) -- a second attempt is never blamed for the first's changes.
    root = contract_block["target"]["repo_root"]
    before = {"files": _reconcile.snapshot(root), "git": _reconcile.git_status(root)}
    _write_json(Path(run_directory) / snapshot_file, before)

    record = _journal.start_attempt(run_directory, record, snapshot_file=snapshot_file)

    # Stamp BEFORE the turn so every process the turn spawns is ownable (M5-D14).
    _ownership.stamp_environment(record["run_id"])

    if item_id is not None:
        record = _journal.set_item_status(run_directory, record, item_id,
                                          _journal.RUNNING, bump_attempt=True)
    record = _journal.transition(run_directory, record, _journal.TURN_ACTIVE,
                                 note=f"attempt {attempt_number}"
                                      + (f" for item {item_id}" if item_id else ""))

    turn_error = None
    try:
        turn_driver(contract_block) if item_id is None else turn_driver(contract_block, item_id)
    except BaseException as exc:  # noqa: BLE001
        # M4's audit finding F-A5, carried into the durable case: reconciliation
        # must not depend on the failure having been normalised first. A turn
        # that died half-way is the case most likely to have left something
        # behind, so every exception is held and re-raised only after the audit.
        turn_error = exc

    turn_record = getattr(turn_driver, "record", None)
    if isinstance(turn_record, dict):
        _write_json(Path(run_directory) / _turn_record_name(attempt_number), turn_record)
    if turn_error is not None:
        record = _journal.update_attempt(
            run_directory, record,
            turn_error=f"{type(turn_error).__name__}: {turn_error}"[:500])

    outcome = discharge_obligation(run_directory, record, contract_block, policy)
    outcome["turn_error"] = turn_error
    return outcome


# --- the run loop --------------------------------------------------------

def _budget_state(record: dict, policy: dict) -> tuple[bool, str, str]:
    """(exhausted, reason_code, detail). Deterministic: no wall-clock guessing."""
    if _runpolicy.expired(policy):
        return True, blocking.RUN_DEADLINE_EXCEEDED, (
            f"absolute deadline {policy['deadline_at']} reached")
    if _journal.attempts_used(record) >= policy["max_attempts"]:
        return True, blocking.ATTEMPT_BUDGET_EXHAUSTED, (
            f"{_journal.attempts_used(record)} of {policy['max_attempts']} attempts used")
    return False, "", ""


def execute(run_directory, *, turn_driver, is_work_finished,
            expected_run_id: str | None = None, require_quiescence: bool = True) -> dict:
    """Drive a run to a terminal state, starting or resuming as the journal says.

    `is_work_finished(contract_block, reconciliation, item_id)` is Diana-side and
    decides whether the ITEM just attempted is done. It is a Diana predicate on
    purpose: letting the agent declare itself finished would hand it the one
    channel M1 D14 denies it, and ERRATA-001 M5-E1-D4 makes that explicit --
    completion is never inferred from Hermes text and is recorded only after the
    required reconciliation has been discharged.

    `turn_driver(contract_block, item_id)` is called ONLY for an eligible item
    (M5-E1-D6). A dependency-blocked item's driver is never invoked at all.
    """
    loaded = _recovery.load_run(run_directory, expected_run_id=expected_run_id)
    record, contract_block = loaded["record"], loaded["contract"]
    policy, items_doc = loaded["policy"], loaded["items"]

    # M5-D10: enforcement before anything else, proven behaviorally.
    _recovery.reestablish_enforcement(contract_block)
    # M5-D11 second proof. Dirty drift is expected once the run's own attempts
    # have written; a MOVED COMMIT never is.
    _recovery.require_fresh(record, allow_dirty_drift=_journal.attempts_used(record) > 0)

    # M5-D6, and M5-E1-D12: an outstanding obligation is discharged before ANY
    # new work AND before a cancellation is allowed to end the run.
    if _journal.has_outstanding_obligation(record):
        outcome = discharge_obligation(run_directory, record, contract_block, policy)
        record = outcome["record"]
        record = _settle_item(run_directory, record, items_doc, outcome,
                              contract_block, is_work_finished, policy)
        if outcome["blocked"]:
            return _finish(run_directory, record, contract_block, policy, items_doc)
        record = _journal.read(run_directory)

    while True:
        if record["state"] in (_journal.APPROVED, _journal.ARMED, _journal.RECONCILED):
            # ERRATA-001 M5-E1-D11: once cancellation is durable, no new item and
            # no new turn may start. Checked before eligibility, so a cancelled
            # run cannot slip one more attempt in.
            if _journal.is_cancelled(record):
                return _terminate(run_directory, record, contract_block, policy, items_doc,
                                  _journal.FAILED, blocking.RUN_CANCELLED,
                                  record["cancellation"].get("reason", "") or "cancelled")

            if _workitems.all_complete(items_doc, record["items"]):
                return _terminate(run_directory, record, contract_block, policy, items_doc,
                                  _journal.COMPLETE, _journal.WORK_FINISHED,
                                  "every declared work item is COMPLETE")

            # M5-E1-D14's derivation order is load-bearing: budget exhaustion is
            # FAILED per the frozen M5-D13/D16, and must therefore be decided
            # BEFORE the blocked-item derivation, or an unfinished item would
            # make an out-of-budget run report BLOCKED instead.
            exhausted, code, detail = _budget_state(record, policy)
            if exhausted:
                return _terminate(run_directory, record, contract_block, policy, items_doc,
                                  _journal.FAILED, code, detail)

            eligible = _workitems.eligible_items(items_doc, record["items"])
            if not eligible:
                blocked = _workitems.first_blocked(items_doc, record["items"])
                if blocked is not None:
                    return _terminate(
                        run_directory, record, contract_block, policy, items_doc,
                        _journal.BLOCKED,
                        blocked.get("reason_code") or blocking.DEPENDENCY_BLOCKED,
                        f"item {blocked['id']!r}: {blocked.get('detail', '')}")
                # No eligible item and none blocked can only mean the graph and
                # the status map disagree, which M5-D9 refuses to resolve.
                raise blocking.Blocked(
                    blocking.WORK_ITEMS_MALFORMED,
                    "no item is eligible and none is blocked: item state is inconsistent")

            if record["state"] in (_journal.APPROVED, _journal.RECONCILED):
                record = _journal.transition(
                    run_directory, record, _journal.ARMED,
                    note="write-ahead complete" if record["state"] == _journal.APPROVED
                    else "budget remains and work is unfinished")

            outcome = run_attempt(run_directory, record, contract_block, policy,
                                  turn_driver, item_id=eligible[0])
            record = outcome["record"]
            record = _settle_item(run_directory, record, items_doc, outcome,
                                  contract_block, is_work_finished, policy,
                                  item_id=eligible[0])
            if outcome["blocked"]:
                # Envelope violation: `discharge_obligation` already drove the run
                # terminal. Nothing further may run.
                return _finish(run_directory, record, contract_block, policy, items_doc)
            # A failed turn blocked only its own item; the loop re-derives
            # eligibility and independent work proceeds.
            continue

        raise blocking.Blocked(
            blocking.JOURNAL_ILLEGAL_TRANSITION,
            f"the run loop cannot proceed from state {record['state']!r}")


def _settle_item(run_directory, record, items_doc, outcome, contract_block,
                 is_work_finished, policy, item_id: str | None = None):
    """Record the item's Diana-owned outcome AFTER reconciliation (M5-E1-D4).

    Completion is never inferred from what the model said: it is decided by a
    Diana predicate, and only once the reconciliation for that attempt exists.
    """
    if item_id is None:
        running = [i for i, e in record["items"].items() if e["status"] == _journal.RUNNING]
        if not running:
            return record
        item_id = running[0]
    if record["items"][item_id]["status"] != _journal.RUNNING:
        return record

    attempt = (record["attempts"] or [{}])[-1]
    recon_file = attempt.get("reconciliation_file")
    reconciliation = _last_reconciliation(run_directory, record)

    if outcome["blocked"]:
        # An envelope violation is a RUN-level fail-closed event, not merely a
        # failed unit of work. Frozen M5-D8 draws RECONCILING -> BLOCKED for
        # exactly this case and M4-D15 says a mismatch blocks and yields no
        # deliverable; ERRATA-001's own precedence rule is that where it appears
        # to disagree with the frozen text about AUTHORITY, the frozen text
        # wins. So independent items do NOT continue past a mismatch -- the run
        # has already been driven terminal by `discharge_obligation`.
        record = _journal.set_item_status(
            run_directory, record, item_id, _journal.ITEM_BLOCKED,
            reason_code=blocking.RECONCILIATION_MISMATCH,
            detail="the attempt's diff left the envelope",
            reconciliation_file=recon_file)
        return _block_dependents(run_directory, record, items_doc, item_id)

    if outcome.get("turn_error") is not None:
        # The turn failed but the envelope HELD -- reconciliation was clean. That
        # is a failed unit of work, not a boundary failure, so it blocks this
        # item and whatever transitively depends on it while leaving independent
        # items free to proceed (M5-E1-D7). This is the distinction that makes
        # "continue the independent work" safe rather than reckless: the run only
        # keeps going when nothing escaped.
        record = _journal.set_item_status(
            run_directory, record, item_id, _journal.ITEM_BLOCKED,
            reason_code=blocking.HERMES_TURN_FAILED,
            detail=f"turn failed: {str(outcome['turn_error'])[:200]}",
            reconciliation_file=recon_file)
        return _block_dependents(run_directory, record, items_doc, item_id)

    finished = bool(is_work_finished(contract_block, reconciliation, item_id))
    if finished:
        return _journal.set_item_status(
            run_directory, record, item_id, _journal.ITEM_COMPLETE,
            reason_code=None, detail="work finished and the diff stayed inside the envelope",
            reconciliation_file=recon_file)

    # Unfinished but the envelope held: the item is simply not done. It is
    # re-armed rather than blocked, and if the run has no budget left the LOOP
    # terminates FAILED -- "the work did not finish" is a fact about the run's
    # budget (M5-D7), not a boundary failure of this item.
    # Otherwise the item stays available for another attempt within the run's
    # budget; RUNNING -> PENDING is not an item transition, so the item is
    # re-armed by rewriting the status map rather than by an illegal move.
    items = json.loads(json.dumps(record["items"]))
    items[item_id]["status"] = _journal.PENDING
    return _journal.apply_item_status(
        run_directory, record, items, f"item {item_id}: re-armed for another attempt")


def _block_dependents(run_directory, record, items_doc, item_id):
    """Propagate BLOCKED transitively (M5-E1-D7), in one durable write."""
    items = json.loads(json.dumps(record["items"]))
    changed = _workitems.propagate_blocked(item_id, items_doc, items)
    if not changed:
        return record
    return _journal.apply_item_status(
        run_directory, record, items,
        f"dependency-blocked by {item_id}: {sorted(changed)}")


def _terminate(run_directory, record, contract_block, policy, items_doc,
               state, reason_code, detail):
    record = _journal.transition(run_directory, record, state,
                                 terminal_reason=reason_code, terminal_detail=detail)
    return _finish(run_directory, record, contract_block, policy, items_doc)


def _last_reconciliation(run_directory, record: dict) -> dict:
    attempts = record.get("attempts") or []
    if not attempts:
        return {}
    name = attempts[-1].get("reconciliation_file")
    if not name:
        return {}
    try:
        return json.loads((Path(run_directory) / name).read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _finish(run_directory, record: dict, contract_block: dict, policy: dict,
            items_doc: dict | None = None) -> dict:
    """Write the report from Diana-owned state and return the terminal result."""
    document = _report.build(record, contract_block, policy, run_directory, items_doc)
    path = _report.persist(document, run_directory)
    return {"run_id": record["run_id"], "outcome": record["state"],
            "reason_code": (record.get("terminal") or {}).get("reason_code"),
            "detail": (record.get("terminal") or {}).get("detail"),
            "record": record, "report": document, "report_path": str(path)}


def read_result(run_directory) -> dict:
    """Read a finished run without resuming it. Terminal runs are read, not run."""
    record = _journal.read(run_directory)
    return {"run_id": record["run_id"], "outcome": record["state"],
            "terminal": record.get("terminal"), "record": record}
