#!/usr/bin/env python3
"""Diana M5: the run journal -- Diana-owned authoritative durable state.

Implements M5-D3 (Diana owns it), M5-D4 (crash-atomic), M5-D8 (state machine,
journaled before acted on) and M5-D9 (absence and ambiguity fail closed).

## Why the journal exists at all

M5's Phase 0 measured the gap it closes. F1: M4's mutation path persisted
nothing -- contract, pre-turn snapshot and turn record lived only in process
memory. F2: SIGTERM and SIGKILL therefore skipped reconciliation entirely, so a
mutating turn that died half-way left an out-of-envelope file on disk with
nothing on disk recording that a run had ever happened. F12: a fresh process
given only a contract and a pre-turn snapshot, written BEFORE the turn, could
reconcile the crashed run and detect exactly what git alone could not see.

So the journal is not bookkeeping. It is the thing that makes reconciliation an
obligation of the RUN rather than of the process (M5-D6).

## Why nothing here is ever read from Hermes

M5-D3: Hermes-owned durable state (`~/.hermes/processes.json`, the terminal
snapshot cache, the gateway's own files) is observed and reported, never
trusted. Recovery must never ask Hermes whether an effect occurred, because the
component that may have caused the effect is not a witness to it. Every field
below is computed by Diana.

## What the digest does and does not defend

The digest is stored beside the record, so it is integrity against CORRUPTION,
TRUNCATION and STALE state -- a torn write, a half-flushed file, a journal from
another run. It is NOT authentication against an adversary who can already write
arbitrary bytes to the run directory; such an adversary could recompute it. What
keeps Hermes out is that the run directory is outside `read_scope` and
`write_scope` entirely, and Hermes has no shell that Diana did not declare.
That distinction is stated rather than blurred, and it is why `open_dir` refuses
a run directory reachable through a symlink or with loose permissions.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "runtime"))
sys.path.insert(0, str(_HERE))
import blocking  # noqa: E402
import contract as _contract  # noqa: E402
import runpolicy as _runpolicy  # noqa: E402

JOURNAL_VERSION = 2   # ERRATA-001 added items and cancellation
JOURNAL_NAME = "journal.json"

# --- the frozen state machine (M5-D8) ------------------------------------
APPROVED = "APPROVED"
ARMED = "ARMED"
TURN_ACTIVE = "TURN_ACTIVE"
RECONCILING = "RECONCILING"
RECONCILED = "RECONCILED"
COMPLETE = "COMPLETE"
FAILED = "FAILED"
BLOCKED = "BLOCKED"

TERMINAL_STATES = frozenset({COMPLETE, FAILED, BLOCKED})

# COMPLETE is a success, so its reason may NOT come from `blocking.py`. That
# module is the fail-closed vocabulary -- "the reason code is the thing an
# operator reads" when a run refused to proceed -- and putting a success value
# in it would let a success be raised as a `Blocked`. M5 therefore keeps its own
# closed completion vocabulary, and every terminal state still carries exactly
# one specific, registered reason.
WORK_FINISHED = "work-finished"
COMPLETION_REASONS = frozenset({WORK_FINISHED})
ALL_STATES = frozenset({APPROVED, ARMED, TURN_ACTIVE, RECONCILING, RECONCILED} | TERMINAL_STATES)

# Exactly the arrows the frozen diagram draws, and no others.
#
# TURN_ACTIVE leads ONLY to RECONCILING. That single restriction is what makes
# M5-D6 structural instead of aspirational: there is no path out of a turn that
# skips the audit, so "reconcile before retry" cannot be forgotten by a caller.
# A turn that errored still reconciles first and is judged afterwards, which is
# why FAILED is reachable from RECONCILED rather than from TURN_ACTIVE.
LEGAL_TRANSITIONS = {
    APPROVED:    frozenset({ARMED, FAILED, BLOCKED}),
    ARMED:       frozenset({TURN_ACTIVE, FAILED, BLOCKED}),
    TURN_ACTIVE: frozenset({RECONCILING}),
    RECONCILING: frozenset({RECONCILED, BLOCKED}),
    RECONCILED:  frozenset({ARMED, COMPLETE, FAILED, BLOCKED}),
    COMPLETE:    frozenset(),
    FAILED:      frozenset(),
    BLOCKED:     frozenset(),
}

RECORD_KEYS = (
    "journal_version",
    "run_id",
    "contract_digest",
    "run_policy_digest",
    # ERRATA-001: the item set is digest-bound exactly as the contract and the
    # run policy are, and re-verified on every resume (M5-E1-D2).
    "work_items_digest",
    "state",
    "target_binding",
    "attempts",
    # ERRATA-001: Diana-owned item status (M5-E1-D4) and cancellation (M5-E1-D11).
    "items",
    "cancellation",
    "terminal",
    "history",
)

TARGET_BINDING_KEYS = ("repo_root", "git_commit", "dirty", "observed_at")


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- item status (ERRATA-001 M5-E1-D4, M5-E1-D15) ------------------------
PENDING = "PENDING"
RUNNING = "RUNNING"
ITEM_COMPLETE = "COMPLETE"
ITEM_BLOCKED = "BLOCKED"
ITEM_STATES = frozenset({PENDING, RUNNING, ITEM_COMPLETE, ITEM_BLOCKED})
ITEM_TERMINAL = frozenset({ITEM_COMPLETE, ITEM_BLOCKED})

# Exactly the arrows an item may take. RUNNING leads only to a terminal state,
# and terminal item states have no outgoing arrows at all -- M5-D8's discipline
# one level down, enforced at the single place item status changes.
ITEM_TRANSITIONS = {
    PENDING: frozenset({RUNNING, ITEM_BLOCKED}),
    RUNNING: frozenset({ITEM_COMPLETE, ITEM_BLOCKED}),
    ITEM_COMPLETE: frozenset(),
    ITEM_BLOCKED: frozenset(),
}


# --- crash-atomic persistence (M5-D4) ------------------------------------

def atomic_write(path: Path, payload: bytes, mode: int = 0o600) -> None:
    """Temp file in the SAME directory, fsync, then `os.replace`.

    Same directory because `os.replace` is only atomic within a filesystem. The
    fsync is on the data before the rename, and then on the DIRECTORY after it,
    so the rename itself survives a power loss rather than merely the bytes.

    Phase 0 F11 measured that Diana's existing `contract.persist` is a plain
    write with neither fsync nor rename. That is safe for M1, which writes once
    before the run and whose torn contract fails its digest -- but M5 updates
    state while a mutation is in flight, where "either the old record or the new
    one, never a third thing" is load-bearing.
    """
    path = Path(path)
    directory = path.parent
    handle, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(directory))
    try:
        os.write(handle, payload)
        os.fsync(handle)
        os.close(handle)
        handle = -1
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        tmp = None
    finally:
        if handle != -1:
            os.close(handle)
        if tmp is not None:
            # Never leave a partial temp file behind to be mistaken for state.
            try:
                os.unlink(tmp)
            except OSError:
                pass
    dir_fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def open_dir(run_directory, *, create: bool = False) -> Path:
    """Return the run directory, refusing an unsafe path (M5-D9).

    Symlink and state-path attacks are refused here rather than at use: a run
    directory reached through a symlink is one an attacker could have redirected
    after approval, and the journal's whole value is that it describes THIS run.
    """
    directory = Path(run_directory)
    if create:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    if not directory.exists():
        raise blocking.Blocked(blocking.JOURNAL_MALFORMED, f"no run directory at {directory}")
    if directory.is_symlink():
        raise blocking.Blocked(
            blocking.JOURNAL_PATH_UNSAFE, f"run directory is a symlink: {directory}")
    st = directory.lstat()
    if not stat.S_ISDIR(st.st_mode):
        raise blocking.Blocked(
            blocking.JOURNAL_PATH_UNSAFE, f"run directory is not a directory: {directory}")
    if st.st_uid != os.getuid():
        raise blocking.Blocked(
            blocking.JOURNAL_PATH_UNSAFE,
            f"run directory is owned by uid {st.st_uid}, not {os.getuid()}")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise blocking.Blocked(
            blocking.JOURNAL_PATH_UNSAFE,
            f"run directory is group/world writable: {stat.S_IMODE(st.st_mode):#o}")
    journal = directory / JOURNAL_NAME
    if journal.is_symlink():
        raise blocking.Blocked(
            blocking.JOURNAL_PATH_UNSAFE, f"journal is a symlink: {journal}")
    return directory


# --- the record ----------------------------------------------------------

def new_record(*, run_id: str, contract_digest: str, run_policy_digest: str,
               target_binding: dict, work_items_digest: str, items: dict) -> dict:
    record = {
        "journal_version": JOURNAL_VERSION,
        "run_id": run_id,
        "contract_digest": contract_digest,
        "run_policy_digest": run_policy_digest,
        "work_items_digest": work_items_digest,
        "items": dict(items),
        "cancellation": None,
        "state": APPROVED,
        "target_binding": dict(target_binding),
        "attempts": [],
        "terminal": None,
        "history": [{"from": None, "to": APPROVED, "at": _utc_now(), "note": "approved"}],
    }
    validate(record)
    return record


def validate(record: object) -> None:
    """Closed schema, fail-closed. An unknown key is a refusal, not a note."""
    if not isinstance(record, dict):
        raise blocking.Blocked(blocking.JOURNAL_MALFORMED, "journal record is not an object")
    missing = sorted(set(RECORD_KEYS) - set(record))
    extra = sorted(set(record) - set(RECORD_KEYS))
    if missing or extra:
        raise blocking.Blocked(
            blocking.JOURNAL_MALFORMED, f"missing={missing} unexpected={extra}")
    if record["journal_version"] != JOURNAL_VERSION:
        raise blocking.Blocked(
            blocking.JOURNAL_MALFORMED,
            f"journal_version {record['journal_version']!r} != {JOURNAL_VERSION}")
    if not isinstance(record["run_id"], str) or not record["run_id"]:
        raise blocking.Blocked(blocking.JOURNAL_MALFORMED, "run_id must be a non-empty string")
    for key in ("contract_digest", "run_policy_digest", "work_items_digest"):
        value = record[key]
        if not isinstance(value, str) or not value.startswith("sha256:"):
            raise blocking.Blocked(blocking.JOURNAL_MALFORMED, f"{key} is not a sha256 digest")
    if record["state"] not in ALL_STATES:
        raise blocking.Blocked(
            blocking.JOURNAL_MALFORMED, f"unknown state {record['state']!r}")
    binding = record["target_binding"]
    if not isinstance(binding, dict) or set(binding) != set(TARGET_BINDING_KEYS):
        raise blocking.Blocked(blocking.JOURNAL_MALFORMED, "target_binding shape invalid")
    if not isinstance(binding["dirty"], bool):
        raise blocking.Blocked(blocking.JOURNAL_MALFORMED, "target_binding.dirty must be a boolean")
    if not isinstance(record["attempts"], list):
        raise blocking.Blocked(blocking.JOURNAL_MALFORMED, "attempts must be a list")
    for attempt in record["attempts"]:
        if not isinstance(attempt, dict) or "attempt" not in attempt or "state" not in attempt:
            raise blocking.Blocked(blocking.JOURNAL_MALFORMED, "attempt entry shape invalid")
    if not isinstance(record["history"], list) or not record["history"]:
        raise blocking.Blocked(blocking.JOURNAL_MALFORMED, "history must be a non-empty list")
    items = record["items"]
    if not isinstance(items, dict) or not items:
        raise blocking.Blocked(blocking.JOURNAL_MALFORMED, "items must be a non-empty object")
    for item_id, entry in items.items():
        if not isinstance(item_id, str) or not item_id:
            raise blocking.Blocked(blocking.JOURNAL_MALFORMED, "item id must be a non-empty string")
        if not isinstance(entry, dict) or set(entry) != {
                "status", "attempts", "reason_code", "detail", "reconciliation_file"}:
            raise blocking.Blocked(
                blocking.JOURNAL_MALFORMED, f"item entry shape invalid for {item_id!r}")
        if entry["status"] not in ITEM_STATES:
            raise blocking.Blocked(
                blocking.JOURNAL_MALFORMED,
                f"unknown item status {entry['status']!r} for {item_id!r}")
        if not isinstance(entry["attempts"], int) or isinstance(entry["attempts"], bool):
            raise blocking.Blocked(
                blocking.JOURNAL_MALFORMED, f"item attempts must be an integer for {item_id!r}")

    cancellation = record["cancellation"]
    if cancellation is not None:
        if not isinstance(cancellation, dict) or set(cancellation) != {"at", "reason"}:
            raise blocking.Blocked(blocking.JOURNAL_MALFORMED, "cancellation shape invalid")
        if not isinstance(cancellation["reason"], str):
            raise blocking.Blocked(blocking.JOURNAL_MALFORMED, "cancellation reason must be a string")

    terminal = record["terminal"]
    if terminal is not None:
        if not isinstance(terminal, dict) or set(terminal) != {"outcome", "reason_code", "detail", "at"}:
            raise blocking.Blocked(blocking.JOURNAL_MALFORMED, "terminal shape invalid")
        if terminal["outcome"] not in TERMINAL_STATES:
            raise blocking.Blocked(
                blocking.JOURNAL_MALFORMED, f"terminal outcome {terminal['outcome']!r} invalid")
        # A record claiming a terminal outcome while sitting in a non-terminal
        # state is exactly the ambiguity M5-D9 refuses to resolve optimistically.
        if record["state"] != terminal["outcome"]:
            raise blocking.Blocked(
                blocking.JOURNAL_MALFORMED,
                f"state {record['state']!r} disagrees with terminal {terminal['outcome']!r}")
    elif record["state"] in TERMINAL_STATES:
        raise blocking.Blocked(
            blocking.JOURNAL_MALFORMED,
            f"state {record['state']!r} is terminal but no terminal block is recorded")


def digest(record: dict) -> str:
    return _contract.digest(record)


def envelope(record: dict) -> bytes:
    """The on-disk form: the record plus its digest, canonically serialized."""
    return _contract.canonical_json({"record": record, "digest": digest(record)})


def write(run_directory, record: dict) -> Path:
    """Validate, then persist crash-atomically (M5-D4)."""
    validate(record)
    directory = open_dir(run_directory, create=True)
    path = directory / JOURNAL_NAME
    atomic_write(path, envelope(record))
    return path


def read(run_directory) -> dict:
    """Read the journal back, proving integrity. Absence and damage both raise."""
    directory = open_dir(run_directory)
    path = directory / JOURNAL_NAME
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise blocking.Blocked(
            blocking.JOURNAL_MALFORMED, f"unreadable journal: {exc}") from None
    try:
        outer = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # A torn write lands here and is treated as ABSENT rather than as a
        # partially-trusted record (M5-AC-2).
        raise blocking.Blocked(
            blocking.JOURNAL_MALFORMED, f"journal is not valid JSON: {exc}") from None
    if not isinstance(outer, dict) or set(outer) != {"record", "digest"}:
        raise blocking.Blocked(blocking.JOURNAL_MALFORMED, "journal envelope shape invalid")
    record = outer["record"]
    validate(record)
    actual = digest(record)
    if actual != outer["digest"]:
        raise blocking.Blocked(
            blocking.JOURNAL_DIGEST_MISMATCH, f"{actual} != {outer['digest']}")
    _require_not_rolled_back(directory, record)
    return record


def _artifact_index(name: str, prefix: str) -> int | None:
    if not name.startswith(prefix) or not name.endswith(".json"):
        return None
    try:
        return int(name[len(prefix):-len(".json")])
    except ValueError:
        return None


def _require_not_rolled_back(directory: Path, record: dict) -> None:
    """Refuse a journal that an EARLIER, genuinely-valid copy has replaced.

    AUDIT FINDING M5-A1. The digest proves a record is one Diana wrote; it does
    NOT prove it is the LATEST one Diana wrote. Restoring a journal saved earlier
    in the same run therefore passes every integrity check while rewinding
    `attempts` -- which resets the retry budget of M5-D16, the one bound that
    stops an unattended run retrying forever.

    The rollback is detectable because per-attempt artifacts are never removed:
    a reconciliation record for attempt N proves attempt N was recorded, so a
    journal claiming fewer attempts than the artifacts on disk is stale. The
    snapshot bound is one higher because M5-D5 writes the snapshot BEFORE the
    attempt is journaled, so exactly one un-journaled snapshot is legitimate.
    """
    attempts = len(record.get("attempts") or [])
    try:
        names = [entry.name for entry in directory.iterdir()]
    except OSError:
        return
    max_recon = max((i for i in (_artifact_index(n, "reconciliation-") for n in names)
                     if i is not None), default=0)
    max_snap = max((i for i in (_artifact_index(n, "pre-turn-snapshot-") for n in names)
                    if i is not None), default=0)
    if max_recon > attempts:
        raise blocking.Blocked(
            blocking.JOURNAL_STALE,
            f"reconciliation record for attempt {max_recon} exists but the journal records "
            f"only {attempts} attempt(s): the journal is an earlier copy")
    if max_snap > attempts + 1:
        raise blocking.Blocked(
            blocking.JOURNAL_STALE,
            f"pre-turn snapshot for attempt {max_snap} exists but the journal records "
            f"only {attempts} attempt(s): the journal is an earlier copy")


def artifact_path(run_directory, name: str) -> Path:
    """Resolve a per-attempt artifact name, refusing anything but a plain file.

    AUDIT FINDING M5-A2. `discharge_obligation` read the pre-turn snapshot by
    joining the journal's `snapshot_file` onto the run directory and opening it.
    The NAME is digest-protected, but the FILE is not: replacing
    `pre-turn-snapshot-001.json` with a symlink needs no digest change at all,
    and Diana would then reconcile against a "before" state chosen by whoever
    planted the link -- which is the audit reading a state the attacker supplied.
    The same reasoning as `open_dir`'s, applied one level down.
    """
    if not isinstance(name, str) or not name:
        raise blocking.Blocked(blocking.JOURNAL_MALFORMED, "artifact name is not a string")
    if name != os.path.basename(name) or name in (".", ".."):
        raise blocking.Blocked(
            blocking.JOURNAL_PATH_UNSAFE, f"artifact name is not a plain basename: {name!r}")
    directory = open_dir(run_directory)
    path = directory / name
    if path.is_symlink():
        raise blocking.Blocked(
            blocking.JOURNAL_PATH_UNSAFE, f"artifact is a symlink: {path}")
    if not path.is_file():
        raise blocking.Blocked(blocking.JOURNAL_MALFORMED, f"artifact is missing: {path}")
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise blocking.Blocked(
            blocking.JOURNAL_PATH_UNSAFE, f"artifact is not a regular file: {path}")
    return path


def read_artifact(run_directory, name: str) -> dict:
    """Read a per-attempt artifact as JSON, through the safety check above."""
    path = artifact_path(run_directory, name)
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise blocking.Blocked(
            blocking.JOURNAL_MALFORMED, f"unreadable artifact {name}: {exc}") from None


def exists(run_directory) -> bool:
    try:
        return (Path(run_directory) / JOURNAL_NAME).is_file()
    except OSError:
        return False


# --- transitions (M5-D8: journaled BEFORE they are acted on) --------------

def transition(run_directory, record: dict, to_state: str, *, note: str = "",
               terminal_reason: str | None = None, terminal_detail: str = "") -> dict:
    """Move to `to_state`, persisting BEFORE the caller acts on it.

    Returns the NEW record. The old one is not mutated in place, so a caller
    holding the previous record cannot accidentally act on a state that was
    never durable.
    """
    current = record["state"]
    if current in TERMINAL_STATES:
        # M5-D8: terminal is terminal. Resurrection is refused here, at the one
        # place every transition passes through, rather than at each caller.
        raise blocking.Blocked(
            blocking.RUN_ALREADY_TERMINAL,
            f"run is {current}; it may be read, never resumed (requested {to_state})")
    if to_state not in LEGAL_TRANSITIONS.get(current, frozenset()):
        raise blocking.Blocked(
            blocking.JOURNAL_ILLEGAL_TRANSITION,
            f"{current} -> {to_state} is not a permitted transition")
    updated = json.loads(json.dumps(record))  # deep copy, no shared substructure
    updated["state"] = to_state
    updated["history"] = list(updated["history"]) + [
        {"from": current, "to": to_state, "at": _utc_now(), "note": note}
    ]
    if to_state in TERMINAL_STATES:
        if terminal_reason is None:
            raise blocking.Blocked(
                blocking.JOURNAL_MALFORMED,
                f"a {to_state} transition must carry a reason code")
        permitted = COMPLETION_REASONS if to_state == COMPLETE else blocking.ALL_REASON_CODES
        if terminal_reason not in permitted:
            raise blocking.Blocked(
                blocking.JOURNAL_MALFORMED,
                f"reason {terminal_reason!r} is not valid for a {to_state} transition")
        updated["terminal"] = {
            "outcome": to_state, "reason_code": terminal_reason,
            "detail": terminal_detail, "at": _utc_now(),
        }
    write(run_directory, updated)
    return updated


def start_attempt(run_directory, record: dict, *, snapshot_file: str) -> dict:
    """Open a new attempt. Called while ARMED, before the turn begins."""
    if record["state"] != ARMED:
        raise blocking.Blocked(
            blocking.JOURNAL_ILLEGAL_TRANSITION,
            f"an attempt may only start from ARMED, not {record['state']!r}")
    updated = json.loads(json.dumps(record))
    updated["attempts"] = list(updated["attempts"]) + [{
        "attempt": len(updated["attempts"]) + 1,
        "state": "OPEN",
        "started_at": _utc_now(),
        "snapshot_file": snapshot_file,
        "ended_at": None,
        "reconciled": False,
        "within_envelope": None,
        "turn_error": None,
    }]
    write(run_directory, updated)
    return updated


def update_attempt(run_directory, record: dict, **fields) -> dict:
    """Update the open attempt. Every write goes through the atomic path."""
    if not record["attempts"]:
        raise blocking.Blocked(blocking.JOURNAL_MALFORMED, "no attempt to update")
    updated = json.loads(json.dumps(record))
    updated["attempts"][-1].update(fields)
    write(run_directory, updated)
    return updated


def open_attempt(record: dict) -> dict | None:
    """The attempt that has not been closed out, if any."""
    if not record["attempts"]:
        return None
    last = record["attempts"][-1]
    return last if last.get("state") == "OPEN" else None


def attempts_used(record: dict) -> int:
    return len(record["attempts"])


def has_outstanding_obligation(record: dict) -> bool:
    """True when a turn was in flight, or an attempt closed without reconciling.

    M5-D6: this is the question a resuming process asks first, and a `True`
    answer is an obligation to discharge -- never an invitation to continue.
    """
    if record["state"] in (TURN_ACTIVE, RECONCILING):
        return True
    last = open_attempt(record)
    return bool(last and not last.get("reconciled"))


# --- item and cancellation transitions (ERRATA-001) ----------------------

def set_item_status(run_directory, record: dict, item_id: str, to_status: str, *,
                    reason_code: str | None = None, detail: str = "",
                    reconciliation_file: str | None = None,
                    bump_attempt: bool = False) -> dict:
    """Move one item's status, refusing anything the item machine forbids.

    Terminal item states are refused here rather than at each caller, for the
    same reason the run's terminal check lives in `transition`: one choke point
    is auditable, and a rule enforced in five places is a rule enforced in four.
    """
    entry = record["items"].get(item_id)
    if entry is None:
        raise blocking.Blocked(blocking.JOURNAL_MALFORMED, f"unknown item {item_id!r}")
    current = entry["status"]
    if current in ITEM_TERMINAL:
        raise blocking.Blocked(
            blocking.RUN_ALREADY_TERMINAL,
            f"item {item_id!r} is {current}; a terminal item is never re-entered")
    if to_status not in ITEM_TRANSITIONS[current]:
        raise blocking.Blocked(
            blocking.JOURNAL_ILLEGAL_TRANSITION,
            f"item {item_id!r}: {current} -> {to_status} is not permitted")
    updated = json.loads(json.dumps(record))
    target = updated["items"][item_id]
    target["status"] = to_status
    if reason_code is not None:
        target["reason_code"] = reason_code
    if detail:
        target["detail"] = detail
    if reconciliation_file is not None:
        target["reconciliation_file"] = reconciliation_file
    if bump_attempt:
        target["attempts"] = int(target["attempts"]) + 1
    updated["history"] = list(updated["history"]) + [
        {"from": record["state"], "to": record["state"], "at": _utc_now(),
         "note": f"item {item_id}: {current} -> {to_status}"}
    ]
    write(run_directory, updated)
    return updated


def apply_item_status(run_directory, record: dict, items: dict, note: str) -> dict:
    """Persist a whole item-status map at once (used by blocked propagation)."""
    updated = json.loads(json.dumps(record))
    updated["items"] = json.loads(json.dumps(items))
    updated["history"] = list(updated["history"]) + [
        {"from": record["state"], "to": record["state"], "at": _utc_now(), "note": note}
    ]
    write(run_directory, updated)
    return updated


def cancel(run_directory, record: dict, reason: str = "") -> dict:
    """Record a durable, one-way cancellation (M5-E1-D11).

    Written BEFORE it takes effect, like every other transition, so a crash
    between the request and the effect leaves the cancellation in force rather
    than losing it. Cancelling an already-terminal run is refused: the run has
    an outcome it earned, and cancellation does not reach back into it
    (M5-E1-D13's ordering rule).
    """
    if record["state"] in TERMINAL_STATES:
        raise blocking.Blocked(
            blocking.RUN_ALREADY_TERMINAL,
            f"run is {record['state']}; a terminal run cannot be cancelled")
    if record.get("cancellation") is not None:
        return record        # idempotent: already cancelled, and it is one-way
    updated = json.loads(json.dumps(record))
    updated["cancellation"] = {"at": _utc_now(), "reason": reason}
    updated["history"] = list(updated["history"]) + [
        {"from": record["state"], "to": record["state"], "at": _utc_now(),
         "note": "cancellation recorded"}
    ]
    write(run_directory, updated)
    return updated


def is_cancelled(record: dict) -> bool:
    return record.get("cancellation") is not None
