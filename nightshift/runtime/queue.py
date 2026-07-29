"""Deterministic queue -> claim -> lock primitive for Diana Nightshift.

This module is the entire approved milestone: it reads a small JSON task
queue, atomically claims exactly one pending task per call, and recovers
tasks abandoned by a dead claimant once a stale threshold has passed. It does
not invoke Claude Code, does not schedule anything, and does not report
anything beyond its own return value. See docs/nightshift/RESEARCH.md for the
research this milestone is scoped from.

State format
------------
The canonical queue is a single UTF-8 JSON file shaped like::

    {
      "tasks": [
        {
          "id": "task-001",
          "status": "pending",
          "title": "Human-readable description",
          "attempt_count": 0,
          "claimed_pid": null,
          "claimed_at": null
        }
      ]
    }

Allowed statuses are "pending", "claimed", "done", and "failed". "done" and
"failed" are terminal: neither can ever be claimed, completed, or failed
again. Do not add statuses here for future phases; extend this file only
when a later, separately-approved milestone actually needs them.

``max_attempts`` is a required, positive-integer, per-task field: the total
number of times a task may be claimed before a failure becomes permanent.
It is fixed at task-authoring time and never mutated by this module.

``acceptance_command`` (non-empty list of strings), ``working_dir`` (an
existing directory path), and ``timeout_seconds`` (a positive number) are
the task contract :func:`run_acceptance` uses to deterministically decide
whether a task's work passed -- never the executor's own claim. All three
are required, fixed at task-authoring time, and never mutated by this
module. ``acceptance_command`` is always argv form; it is never assembled
into or run through a shell.

``executor_command`` (non-empty list of strings) and
``executor_timeout_seconds`` (a positive number) are the bounded work
command :func:`run_executor` launches, sharing the same ``working_dir`` as
acceptance (acceptance checks what the executor left behind there).
``timeout_seconds`` bounds the *acceptance* command specifically;
``executor_timeout_seconds`` bounds the *executor* command specifically --
they are deliberately separate fields, since the two phases can need very
different time budgets. The executor's own exit code is never authoritative
-- :func:`run_task` always runs acceptance afterward regardless of it,
exactly like every other part of this design defers to acceptance alone.

``validate_queue`` only checks the *structural* shape of these three fields
(right types, non-empty). It deliberately does not check that
``working_dir`` exists on disk: that is an environmental condition specific
to whenever one particular task's acceptance is actually run, not a
queue-wide schema property -- one task's missing directory must not make
``validate_queue`` reject every other task in the same file.
:func:`run_acceptance` checks it lazily, per task, at run time.

Known limitation (deferred to a later, separately-approved milestone): the
stale-lock recovery path in :func:`claim_next` returns an abandoned
"claimed" task straight back to "pending" unconditionally -- it does not yet
compare ``attempt_count`` to ``max_attempts``. An abandoned task recovered
that way can therefore be claimed again even past its retry ceiling. This is
deliberate: teaching abandoned-run recovery about retry limits is scoped to
the crash/abandoned-run recovery milestone, not this one, and adding that
check here without also fixing the recovery path would let recovery write a
queue state this module's own validator would then reject on the next read.

``claimed_pid`` / ``claimed_at`` are the *persistent claim metadata*: who
claimed a task and when. They are written into the canonical file and
survive across process restarts. They are distinct from the *short-lived OS
lock* described below, which only exists to serialize the read-modify-write
critical section of a single claim attempt.

Ownership contract
------------------
``claimed_pid`` is meaningful only for as long as the recorded process stays
alive. :func:`claim_next` has no opinion on *who* that should be -- it
records whichever PID the caller supplies (``claimant_pid``, defaulting to
``os.getpid()`` of the immediate caller) and nothing more. This module does
not run a worker, a heartbeat, or a lease-renewal loop; that is out of scope
for this milestone.

This matters concretely for the bundled CLI (see :func:`main`): the CLI
process itself is a one-shot debug/test entry point. It claims a task, then
exits immediately. Whatever PID it recorded is therefore dead the moment the
command returns, and the very next :func:`claim_next` call against that
queue (with any non-zero stale threshold that has since elapsed) is free to
recover it. **Do not treat a bare CLI claim as durable ownership of ongoing
work.** A future long-lived wrapper that actually performs the claimed task
must call :func:`claim_next` as a library function from within its own
process (not via a short-lived subprocess) and pass that process's own,
still-running PID -- which the default ``claimant_pid=None`` already does
correctly, precisely because in that case the "immediate caller" *is* the
long-lived process.

Locking
-------
A sibling file ``<queue_path>.lock`` is opened (created if absent) and an
exclusive, **non-blocking** ``fcntl.flock`` (``LOCK_EX | LOCK_NB``) is
attempted for the duration of one read-validate-mutate-write pass. This is
the mechanism that guarantees two concurrent processes calling
:func:`claim_next` against the same queue file never both select the same
pending task. The lock file's *contents* are never read or written -- it
exists only as an flock target.

The lock attempt is non-blocking on purpose: a caller must never wait
indefinitely for this short-lived mutex, even if whatever process currently
holds it has hung. If the lock cannot be acquired immediately, the call
returns ``LOCK_BUSY`` right away (see "Exit outcomes" below) without reading
or touching the canonical queue file at all -- acquisition is attempted
before the queue file is ever opened. This is a purely transient, retryable
condition about contention for the mutex itself.

``LOCK_BUSY`` must never be confused with ``LOCK_HELD``: ``LOCK_BUSY`` means
"another process currently holds the short-lived OS mutex protecting the
critical section"; ``LOCK_HELD`` means "the OS mutex was acquired fine, but
every candidate *task* is under a persistent claim (``claimed_pid`` /
``claimed_at``) this call won't touch." The two are checked at different
layers and can occur independently of each other. A live claimant recorded
inside the queue (``claimed_pid`` alive) is never affected by OS-lock
contention either way; liveness is checked separately via ``os.kill(pid,
0)`` once the mutex is held.

Atomic writes
-------------
Every write to the canonical file follows: serialize the complete new
state -> write to a temp file in the same directory -> flush + fsync the
temp file -> ``os.replace`` the temp file onto the canonical path -> best
effort fsync of the containing directory. The canonical file is never
truncated or mutated in place, so a crash or injected failure at any point
before ``os.replace`` leaves the original canonical file untouched.

Exit outcomes
-------------
:func:`claim_next` returns a :class:`ClaimResult` whose ``outcome`` field is
exactly one of:

``CLAIMED``
    A task was claimed by this call. ``task_id`` names it. If a stale
    claim was recovered as part of reaching this outcome, its id appears in
    ``recovered_stale_task_ids`` -- this is how "stale-lock recovery" is
    reported: as an attribute of the claim it enabled, not as a separate
    mutually-exclusive outcome, since recovery is always a means to a claim
    within a single atomic pass, never an end in itself here.

``LOCK_HELD``
    No task was claimed because every candidate task is currently under a
    persistent claim this call is not willing to touch: either the recorded
    PID is confirmed alive, or it is dead but has not yet exceeded the stale
    threshold. Both cases reject cleanly and identically at this outcome
    level; a caller that needs to distinguish them can inspect
    ``blocking_task_id`` alongside external knowledge. This outcome is only
    ever reached *after* the short-lived OS mutex was acquired successfully
    -- see ``LOCK_BUSY`` for contention on the mutex itself.

``LOCK_BUSY``
    The short-lived OS mutex (the ``<queue_path>.lock`` flock) was already
    held by another process attempting a claim on this same queue file, so
    this call backed off immediately instead of waiting. The canonical
    queue file is guaranteed untouched -- it is never opened until after the
    lock is held. Retry later; this is a transient condition, not a
    judgment about any task's ownership.

``NO_PENDING_TASK``
    The queue contains no pending task and no task currently blocking a
    claim (i.e. it is empty, or every task is in a state this call has
    nothing to do with).

``MALFORMED_QUEUE``
    The canonical file failed validation before any mutation was
    attempted. The canonical file is guaranteed untouched. ``message``
    explains what failed.

``INTERNAL_FAILURE``
    An unexpected error occurred during the write phase (after
    validation succeeded). The canonical file is guaranteed untouched,
    because the failure necessarily occurred before or during the atomic
    ``os.replace`` step, which never partially applies.

:func:`complete_task` and :func:`fail_task` return a separate
:class:`TransitionResult` (see its class docstring for its own
``TransitionOutcome`` values: ``DONE``, ``REQUEUED``,
``FAILED_PERMANENTLY``, ``WRONG_OWNER``, ``INVALID_STATE``,
``TASK_NOT_FOUND``, ``LOCK_BUSY``, ``MALFORMED_QUEUE``,
``INTERNAL_FAILURE``) -- the completion/retry state machine is a distinct
concern from claiming, even though both share the same lock and atomic
write primitives.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from enum import Enum
from typing import Any, Optional

ALLOWED_STATUSES = ("pending", "claimed", "done", "failed")
TERMINAL_STATUSES = ("done", "failed")
REQUIRED_TASK_FIELDS = (
    "id",
    "status",
    "title",
    "attempt_count",
    "max_attempts",
    "claimed_pid",
    "claimed_at",
    "acceptance_command",
    "working_dir",
    "timeout_seconds",
    "executor_command",
    "executor_timeout_seconds",
)
DEFAULT_STALE_THRESHOLD_SECONDS = 300


class MalformedQueueError(Exception):
    """Raised when the canonical queue fails validation.

    Never caught to "repair" the data -- callers must surface this as a
    clean failure and leave the canonical file exactly as it was.
    """


class ClaimOutcome(str, Enum):
    CLAIMED = "claimed"
    LOCK_HELD = "lock_held"
    LOCK_BUSY = "lock_busy"
    NO_PENDING_TASK = "no_pending_task"
    MALFORMED_QUEUE = "malformed_queue"
    INTERNAL_FAILURE = "internal_failure"


class _LockBusyError(Exception):
    """Internal signal only: the short-lived OS mutex is held elsewhere.

    Never propagated past claim_next() -- it is always converted into a
    ClaimResult(outcome=ClaimOutcome.LOCK_BUSY) at the call site.
    """


class TransitionOutcome(str, Enum):
    """Outcomes shared by :func:`complete_task` and :func:`fail_task`.

    ``DONE``
        The task was a valid, owned, "claimed" task and is now "done".
        Only returned by :func:`complete_task`.

    ``REQUEUED``
        The task failed but had not yet exhausted ``max_attempts``, so it
        is back to "pending" (claim metadata cleared) and claimable again.
        Only returned by :func:`fail_task`.

    ``FAILED_PERMANENTLY``
        The task failed at or past its ``max_attempts`` ceiling and is now
        "failed" (claim metadata cleared). Terminal -- never claimable
        again. Only returned by :func:`fail_task`.

    ``WRONG_OWNER``
        The task is "claimed", but not by the ``owner_pid`` making this
        call. Nothing was modified.

    ``INVALID_STATE``
        The task is not currently "claimed" (already "pending", "done", or
        "failed"), so it cannot be completed or failed right now. Nothing
        was modified.

    ``TASK_NOT_FOUND``
        No task with the given id exists in the queue. Nothing was
        modified.

    ``LOCK_BUSY``
        The short-lived OS mutex was held elsewhere; back off and retry.
        The canonical file was never even opened.

    ``MALFORMED_QUEUE``
        The canonical file failed validation before any mutation was
        attempted. The canonical file is guaranteed untouched.

    ``INTERNAL_FAILURE``
        An unexpected error occurred during the write phase. The canonical
        file is guaranteed untouched, for the same reason as
        ClaimOutcome.INTERNAL_FAILURE.
    """

    DONE = "done"
    REQUEUED = "requeued"
    FAILED_PERMANENTLY = "failed_permanently"
    WRONG_OWNER = "wrong_owner"
    INVALID_STATE = "invalid_state"
    TASK_NOT_FOUND = "task_not_found"
    LOCK_BUSY = "lock_busy"
    MALFORMED_QUEUE = "malformed_queue"
    INTERNAL_FAILURE = "internal_failure"


@dataclasses.dataclass(frozen=True)
class TransitionResult:
    outcome: TransitionOutcome
    task_id: Optional[str] = None
    attempt_count: Optional[int] = None
    message: Optional[str] = None

    def to_json_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "task_id": self.task_id,
            "attempt_count": self.attempt_count,
            "message": self.message,
        }


@dataclasses.dataclass(frozen=True)
class ClaimResult:
    outcome: ClaimOutcome
    task_id: Optional[str] = None
    attempt_count: Optional[int] = None
    recovered_stale_task_ids: tuple = ()
    blocking_task_id: Optional[str] = None
    message: Optional[str] = None

    def to_json_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "task_id": self.task_id,
            "attempt_count": self.attempt_count,
            "recovered_stale_task_ids": list(self.recovered_stale_task_ids),
            "blocking_task_id": self.blocking_task_id,
            "message": self.message,
        }


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_timestamp(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value)


def _is_pid_alive(pid: int) -> bool:
    """Return True iff a process with this PID currently exists.

    ``os.kill(pid, 0)`` sends no signal; it only asks the kernel whether the
    PID is valid. ProcessLookupError means it is not. PermissionError means
    it exists but this process cannot signal it -- still alive.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    else:
        return True


def validate_queue(data: Any) -> dict:
    """Validate raw parsed JSON as a well-formed queue.

    Raises MalformedQueueError with a specific message on any violation.
    Returns the same data unchanged on success -- this function never
    repairs, coerces, or guesses at intent.
    """
    if not isinstance(data, dict):
        raise MalformedQueueError("top-level queue value is not a JSON object")

    if set(data.keys()) != {"tasks"}:
        raise MalformedQueueError(
            f"top-level object must have exactly one key 'tasks', got {sorted(data.keys())}"
        )

    tasks = data["tasks"]
    if not isinstance(tasks, list):
        raise MalformedQueueError("'tasks' must be a JSON array")

    seen_ids = set()
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise MalformedQueueError(f"task at index {index} is not a JSON object")

        missing = [f for f in REQUIRED_TASK_FIELDS if f not in task]
        if missing:
            raise MalformedQueueError(
                f"task at index {index} is missing required field(s): {missing}"
            )
        extra = set(task.keys()) - set(REQUIRED_TASK_FIELDS)
        if extra:
            raise MalformedQueueError(
                f"task at index {index} has unsupported field(s): {sorted(extra)}"
            )

        task_id = task["id"]
        if not isinstance(task_id, str) or not task_id:
            raise MalformedQueueError(f"task at index {index} has an invalid 'id'")
        if task_id in seen_ids:
            raise MalformedQueueError(f"duplicate task id: {task_id!r}")
        seen_ids.add(task_id)

        status = task["status"]
        if status not in ALLOWED_STATUSES:
            raise MalformedQueueError(
                f"task {task_id!r} has unsupported status {status!r}; "
                f"allowed: {ALLOWED_STATUSES}"
            )

        title = task["title"]
        if not isinstance(title, str):
            raise MalformedQueueError(f"task {task_id!r} has a non-string 'title'")

        attempt_count = task["attempt_count"]
        if isinstance(attempt_count, bool) or not isinstance(attempt_count, int):
            raise MalformedQueueError(
                f"task {task_id!r} has a non-integer 'attempt_count'"
            )
        if attempt_count < 0:
            raise MalformedQueueError(
                f"task {task_id!r} has a negative 'attempt_count'"
            )

        max_attempts = task["max_attempts"]
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise MalformedQueueError(
                f"task {task_id!r} has a non-integer 'max_attempts'"
            )
        if max_attempts < 1:
            raise MalformedQueueError(
                f"task {task_id!r} has a non-positive 'max_attempts'"
            )

        acceptance_command = task["acceptance_command"]
        if (
            not isinstance(acceptance_command, list)
            or not acceptance_command
            or not all(isinstance(part, str) for part in acceptance_command)
        ):
            raise MalformedQueueError(
                f"task {task_id!r} has an invalid 'acceptance_command' "
                "(must be a non-empty list of strings)"
            )

        working_dir = task["working_dir"]
        if not isinstance(working_dir, str) or not working_dir:
            raise MalformedQueueError(
                f"task {task_id!r} has an invalid 'working_dir' (must be a non-empty string)"
            )

        timeout_seconds = task["timeout_seconds"]
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise MalformedQueueError(
                f"task {task_id!r} has a non-numeric 'timeout_seconds'"
            )
        if timeout_seconds <= 0:
            raise MalformedQueueError(
                f"task {task_id!r} has a non-positive 'timeout_seconds'"
            )

        executor_command = task["executor_command"]
        if (
            not isinstance(executor_command, list)
            or not executor_command
            or not all(isinstance(part, str) for part in executor_command)
        ):
            raise MalformedQueueError(
                f"task {task_id!r} has an invalid 'executor_command' "
                "(must be a non-empty list of strings)"
            )

        executor_timeout_seconds = task["executor_timeout_seconds"]
        if isinstance(executor_timeout_seconds, bool) or not isinstance(
            executor_timeout_seconds, (int, float)
        ):
            raise MalformedQueueError(
                f"task {task_id!r} has a non-numeric 'executor_timeout_seconds'"
            )
        if executor_timeout_seconds <= 0:
            raise MalformedQueueError(
                f"task {task_id!r} has a non-positive 'executor_timeout_seconds'"
            )

        claimed_pid = task["claimed_pid"]
        claimed_at = task["claimed_at"]
        if claimed_pid is not None and (
            isinstance(claimed_pid, bool) or not isinstance(claimed_pid, int)
        ):
            raise MalformedQueueError(
                f"task {task_id!r} has a non-integer 'claimed_pid'"
            )
        if claimed_at is not None and not isinstance(claimed_at, str):
            raise MalformedQueueError(
                f"task {task_id!r} has a non-string 'claimed_at'"
            )
        if claimed_at is not None:
            try:
                _parse_timestamp(claimed_at)
            except ValueError as exc:
                raise MalformedQueueError(
                    f"task {task_id!r} has an unparseable 'claimed_at': {exc}"
                ) from exc

        if status == "claimed":
            if claimed_pid is None or claimed_at is None:
                raise MalformedQueueError(
                    f"task {task_id!r} is 'claimed' but is missing claim metadata"
                )
        else:
            # "pending", "done", and "failed" all carry no active claim.
            if claimed_pid is not None or claimed_at is not None:
                raise MalformedQueueError(
                    f"task {task_id!r} has status {status!r} but carries claim metadata"
                )

    return data


def _atomic_write(path: str, data: dict) -> None:
    """Write ``data`` to ``path`` atomically.

    Serializes to a temp file in the same directory, flushes and fsyncs it,
    then uses os.replace for the final swap. Never truncates the canonical
    path directly. Best-effort fsync of the containing directory afterward.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".queue-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as tmp_file:
            json.dump(data, tmp_file, indent=2, sort_keys=True)
            tmp_file.write("\n")
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
    else:
        with contextlib.suppress(OSError):
            dir_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)


@contextlib.contextmanager
def _os_lock(lock_path: str):
    """Hold a short-lived exclusive OS lock for one critical section.

    This is the OS-level mutex required to serialize concurrent claim
    attempts against the same queue file. It is unrelated to, and does not
    replace, the persistent claimed_pid/claimed_at metadata stored inside
    task records.

    The acquisition is non-blocking (LOCK_EX | LOCK_NB): a caller must never
    wait indefinitely for this mutex, including if whatever process
    currently holds it has hung rather than exited. If it cannot be
    acquired immediately, this raises _LockBusyError -- the queue file
    itself is never opened in that case, since acquisition is attempted
    before entering the `yield`ed critical section.
    """
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise _LockBusyError() from exc
        try:
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)


def _read_and_validate(queue_path: str) -> dict:
    """Read, parse, and strictly validate the canonical queue file.

    Raises json.JSONDecodeError or MalformedQueueError on any problem --
    callers convert both into a MALFORMED_QUEUE-shaped outcome. Never
    repairs anything.
    """
    with open(queue_path, "r", encoding="utf-8") as f:
        raw_text = f.read()
    return validate_queue(json.loads(raw_text))


def _find_task(data: dict, task_id: str) -> Optional[dict]:
    for task in data["tasks"]:
        if task["id"] == task_id:
            return task
    return None


def claim_next(
    queue_path: str,
    stale_threshold_seconds: int = DEFAULT_STALE_THRESHOLD_SECONDS,
    lock_path: Optional[str] = None,
    claimant_pid: Optional[int] = None,
) -> ClaimResult:
    """Atomically claim exactly one pending task from ``queue_path``.

    Recovers any stale claim (dead PID, age past ``stale_threshold_seconds``)
    back to "pending" as part of the same locked pass before selecting a
    candidate to claim. Never touches a live claim or a dead-but-not-yet-
    stale claim.
    """
    if lock_path is None:
        lock_path = queue_path + ".lock"
    if claimant_pid is None:
        claimant_pid = os.getpid()

    try:
        with _os_lock(lock_path):
            try:
                data = _read_and_validate(queue_path)
            except (json.JSONDecodeError, MalformedQueueError) as exc:
                return ClaimResult(
                    outcome=ClaimOutcome.MALFORMED_QUEUE,
                    message=str(exc),
                )

            now = _utcnow()
            tasks = data["tasks"]
            recovered_ids = []
            blocking_task_id = None

            for task in tasks:
                if task["status"] != "claimed":
                    continue
                pid = task["claimed_pid"]
                claimed_at = _parse_timestamp(task["claimed_at"])
                age_seconds = (now - claimed_at).total_seconds()
                if not _is_pid_alive(pid) and age_seconds > stale_threshold_seconds:
                    task["status"] = "pending"
                    task["claimed_pid"] = None
                    task["claimed_at"] = None
                    recovered_ids.append(task["id"])
                elif blocking_task_id is None:
                    blocking_task_id = task["id"]

            claimed_task = None
            for task in tasks:
                if task["status"] == "pending":
                    claimed_task = task
                    break

            if claimed_task is None:
                # A recovered task is marked "pending" in `tasks` above, so
                # the loop just before this can only fail to find one when
                # recovered_ids is empty too -- nothing was mutated, so no
                # write is needed here.
                outcome = (
                    ClaimOutcome.LOCK_HELD
                    if blocking_task_id is not None
                    else ClaimOutcome.NO_PENDING_TASK
                )
                return ClaimResult(
                    outcome=outcome,
                    recovered_stale_task_ids=tuple(recovered_ids),
                    blocking_task_id=blocking_task_id,
                )

            claimed_task["status"] = "claimed"
            claimed_task["claimed_pid"] = claimant_pid
            claimed_task["claimed_at"] = now.isoformat()
            claimed_task["attempt_count"] += 1

            try:
                _atomic_write(queue_path, data)
            except OSError as exc:
                return ClaimResult(
                    outcome=ClaimOutcome.INTERNAL_FAILURE,
                    message=f"write failed: {exc}",
                )

            return ClaimResult(
                outcome=ClaimOutcome.CLAIMED,
                task_id=claimed_task["id"],
                attempt_count=claimed_task["attempt_count"],
                recovered_stale_task_ids=tuple(recovered_ids),
            )
    except _LockBusyError:
        return ClaimResult(
            outcome=ClaimOutcome.LOCK_BUSY,
            message="the short-lived OS mutex for this queue is held by another process",
        )
    except OSError as exc:
        return ClaimResult(
            outcome=ClaimOutcome.INTERNAL_FAILURE,
            message=f"unexpected OS error: {exc}",
        )


def complete_task(
    queue_path: str,
    task_id: str,
    owner_pid: Optional[int] = None,
    lock_path: Optional[str] = None,
) -> TransitionResult:
    """Mark a "claimed" task "done", clearing its claim ownership metadata.

    Only succeeds if ``task_id`` exists, is currently "claimed", and is
    owned by ``owner_pid`` (defaulting to os.getpid() of the caller) --
    exactly the same identity check :func:`claim_next` recorded at claim
    time, never a looser "some owner exists" check.
    """
    if lock_path is None:
        lock_path = queue_path + ".lock"
    if owner_pid is None:
        owner_pid = os.getpid()

    try:
        with _os_lock(lock_path):
            try:
                data = _read_and_validate(queue_path)
            except (json.JSONDecodeError, MalformedQueueError) as exc:
                return TransitionResult(
                    outcome=TransitionOutcome.MALFORMED_QUEUE,
                    task_id=task_id,
                    message=str(exc),
                )

            task = _find_task(data, task_id)
            if task is None:
                return TransitionResult(
                    outcome=TransitionOutcome.TASK_NOT_FOUND, task_id=task_id
                )

            if task["status"] != "claimed":
                return TransitionResult(
                    outcome=TransitionOutcome.INVALID_STATE,
                    task_id=task_id,
                    attempt_count=task["attempt_count"],
                    message=f"task status is {task['status']!r}, not 'claimed'",
                )

            if task["claimed_pid"] != owner_pid:
                return TransitionResult(
                    outcome=TransitionOutcome.WRONG_OWNER,
                    task_id=task_id,
                    attempt_count=task["attempt_count"],
                )

            task["status"] = "done"
            task["claimed_pid"] = None
            task["claimed_at"] = None

            try:
                _atomic_write(queue_path, data)
            except OSError as exc:
                return TransitionResult(
                    outcome=TransitionOutcome.INTERNAL_FAILURE,
                    task_id=task_id,
                    message=f"write failed: {exc}",
                )

            return TransitionResult(
                outcome=TransitionOutcome.DONE,
                task_id=task_id,
                attempt_count=task["attempt_count"],
            )
    except _LockBusyError:
        return TransitionResult(
            outcome=TransitionOutcome.LOCK_BUSY,
            task_id=task_id,
            message="the short-lived OS mutex for this queue is held by another process",
        )
    except OSError as exc:
        return TransitionResult(
            outcome=TransitionOutcome.INTERNAL_FAILURE,
            task_id=task_id,
            message=f"unexpected OS error: {exc}",
        )


def fail_task(
    queue_path: str,
    task_id: str,
    owner_pid: Optional[int] = None,
    lock_path: Optional[str] = None,
) -> TransitionResult:
    """Record a failed attempt on a "claimed" task, owned by ``owner_pid``.

    If attempt_count has not yet reached max_attempts, the task returns to
    "pending" (claim metadata cleared) and is claimable again. Otherwise it
    becomes permanently "failed" (claim metadata cleared, never claimable
    again). attempt_count itself is never modified here -- it was already
    incremented exactly once, at claim time, by claim_next().
    """
    if lock_path is None:
        lock_path = queue_path + ".lock"
    if owner_pid is None:
        owner_pid = os.getpid()

    try:
        with _os_lock(lock_path):
            try:
                data = _read_and_validate(queue_path)
            except (json.JSONDecodeError, MalformedQueueError) as exc:
                return TransitionResult(
                    outcome=TransitionOutcome.MALFORMED_QUEUE,
                    task_id=task_id,
                    message=str(exc),
                )

            task = _find_task(data, task_id)
            if task is None:
                return TransitionResult(
                    outcome=TransitionOutcome.TASK_NOT_FOUND, task_id=task_id
                )

            if task["status"] != "claimed":
                return TransitionResult(
                    outcome=TransitionOutcome.INVALID_STATE,
                    task_id=task_id,
                    attempt_count=task["attempt_count"],
                    message=f"task status is {task['status']!r}, not 'claimed'",
                )

            if task["claimed_pid"] != owner_pid:
                return TransitionResult(
                    outcome=TransitionOutcome.WRONG_OWNER,
                    task_id=task_id,
                    attempt_count=task["attempt_count"],
                )

            if task["attempt_count"] >= task["max_attempts"]:
                task["status"] = "failed"
                outcome = TransitionOutcome.FAILED_PERMANENTLY
            else:
                task["status"] = "pending"
                outcome = TransitionOutcome.REQUEUED
            task["claimed_pid"] = None
            task["claimed_at"] = None

            try:
                _atomic_write(queue_path, data)
            except OSError as exc:
                return TransitionResult(
                    outcome=TransitionOutcome.INTERNAL_FAILURE,
                    task_id=task_id,
                    message=f"write failed: {exc}",
                )

            return TransitionResult(
                outcome=outcome,
                task_id=task_id,
                attempt_count=task["attempt_count"],
            )
    except _LockBusyError:
        return TransitionResult(
            outcome=TransitionOutcome.LOCK_BUSY,
            task_id=task_id,
            message="the short-lived OS mutex for this queue is held by another process",
        )
    except OSError as exc:
        return TransitionResult(
            outcome=TransitionOutcome.INTERNAL_FAILURE,
            task_id=task_id,
            message=f"unexpected OS error: {exc}",
        )


class AcceptanceOutcome(str, Enum):
    """Why an acceptance run did or did not pass.

    All four non-passing reasons are deliberately treated identically by
    :func:`run_acceptance_and_record`: every one of them drives fail_task(),
    exactly like any other failed attempt. Only PASSED drives complete_task().
    """

    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    MISSING_COMMAND = "missing_command"
    MALFORMED_CONTRACT = "malformed_contract"


@dataclasses.dataclass(frozen=True)
class AcceptanceResult:
    passed: bool
    reason: AcceptanceOutcome
    command: tuple
    exit_code: Optional[int]
    stdout: str
    stderr: str
    started_at: Optional[str]
    ended_at: Optional[str]
    message: Optional[str] = None

    def to_json_dict(self) -> dict:
        return {
            "passed": self.passed,
            "reason": self.reason.value,
            "command": list(self.command),
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "message": self.message,
        }


def run_acceptance(task: dict) -> AcceptanceResult:
    """Deterministically check one task's work: run its acceptance command.

    Reads only task["acceptance_command"], task["working_dir"], and
    task["timeout_seconds"] -- never any text the executor may have
    written elsewhere. Exit code 0 is the only way to pass. The command is
    always run as an argv list with shell=False, so no shell is ever
    invoked and no string concatenation can inject anything. Evidence
    never includes the process environment -- only argv, exit code,
    timestamps, and whatever the command itself wrote to stdout/stderr.

    Re-validates the three contract fields defensively (structurally
    identical to validate_queue's own checks) so this function is safe to
    call directly on any task dict, not only ones that already passed
    validate_queue. Additionally checks that working_dir actually exists on
    disk -- the one thing validate_queue deliberately leaves to this
    function (see the module docstring's "Ownership contract"-adjacent note
    on acceptance fields).
    """
    command = task.get("acceptance_command")
    working_dir = task.get("working_dir")
    timeout_seconds = task.get("timeout_seconds")

    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) for part in command)
    ):
        return AcceptanceResult(
            passed=False,
            reason=AcceptanceOutcome.MALFORMED_CONTRACT,
            command=tuple(command) if isinstance(command, list) else (),
            exit_code=None,
            stdout="",
            stderr="",
            started_at=None,
            ended_at=None,
            message="acceptance_command must be a non-empty list of strings",
        )

    if not isinstance(working_dir, str) or not working_dir or not os.path.isdir(working_dir):
        return AcceptanceResult(
            passed=False,
            reason=AcceptanceOutcome.MALFORMED_CONTRACT,
            command=tuple(command),
            exit_code=None,
            stdout="",
            stderr="",
            started_at=None,
            ended_at=None,
            message=f"working_dir {working_dir!r} is not an existing directory",
        )

    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        return AcceptanceResult(
            passed=False,
            reason=AcceptanceOutcome.MALFORMED_CONTRACT,
            command=tuple(command),
            exit_code=None,
            stdout="",
            stderr="",
            started_at=None,
            ended_at=None,
            message="timeout_seconds must be a positive number",
        )
    if timeout_seconds <= 0:
        return AcceptanceResult(
            passed=False,
            reason=AcceptanceOutcome.MALFORMED_CONTRACT,
            command=tuple(command),
            exit_code=None,
            stdout="",
            stderr="",
            started_at=None,
            ended_at=None,
            message="timeout_seconds must be a positive number",
        )

    started_at = _utcnow().isoformat()
    try:
        completed = subprocess.run(
            command,
            cwd=working_dir,
            timeout=timeout_seconds,
            capture_output=True,
            text=True,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return AcceptanceResult(
            passed=False,
            reason=AcceptanceOutcome.TIMED_OUT,
            command=tuple(command),
            exit_code=None,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            started_at=started_at,
            ended_at=_utcnow().isoformat(),
            message=f"acceptance command exceeded {timeout_seconds}s timeout",
        )
    except OSError as exc:
        # Covers FileNotFoundError, PermissionError, NotADirectoryError,
        # IsADirectoryError, and any other OS-level failure to exec the
        # command -- all are "the command could not be run", grouped under
        # MISSING_COMMAND. TimeoutExpired is a SubprocessError, not an
        # OSError, so it can never be caught here by mistake -- it is
        # already handled by the except clause above this one.
        return AcceptanceResult(
            passed=False,
            reason=AcceptanceOutcome.MISSING_COMMAND,
            command=tuple(command),
            exit_code=None,
            stdout="",
            stderr="",
            started_at=started_at,
            ended_at=_utcnow().isoformat(),
            message=str(exc),
        )

    passed = completed.returncode == 0
    return AcceptanceResult(
        passed=passed,
        reason=AcceptanceOutcome.PASSED if passed else AcceptanceOutcome.FAILED,
        command=tuple(command),
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        started_at=started_at,
        ended_at=_utcnow().isoformat(),
    )


@dataclasses.dataclass(frozen=True)
class AcceptanceRun:
    """The full result of one run_acceptance_and_record() call."""

    acceptance: AcceptanceResult
    transition: TransitionResult

    def to_json_dict(self) -> dict:
        return {
            "acceptance": self.acceptance.to_json_dict(),
            "transition": self.transition.to_json_dict(),
        }


def run_acceptance_and_record(
    queue_path: str,
    task_id: str,
    owner_pid: Optional[int] = None,
    lock_path: Optional[str] = None,
) -> AcceptanceRun:
    """Run a task's acceptance command, then apply the resulting transition.

    Reads the task's contract fields with a plain, unlocked read -- the
    acceptance command can take up to timeout_seconds, and holding the
    short-lived OS mutex for that long would block every other claim/
    complete/fail call against this queue file. Only the final
    complete_task()/fail_task() call (already independently lock-protected)
    performs the actual state mutation, re-reading and re-checking
    ownership fresh at that point -- so a task being reclaimed by someone
    else in between is still handled safely: the transition call simply
    returns WRONG_OWNER rather than corrupting anything.

    A PASSED acceptance drives complete_task(); every other AcceptanceOutcome
    (FAILED, TIMED_OUT, MISSING_COMMAND, MALFORMED_CONTRACT) drives
    fail_task() identically -- from the retry state machine's point of
    view, all four are simply "this attempt did not succeed".
    """
    if owner_pid is None:
        owner_pid = os.getpid()

    try:
        data = _read_and_validate(queue_path)
    except (json.JSONDecodeError, MalformedQueueError) as exc:
        acceptance = AcceptanceResult(
            passed=False,
            reason=AcceptanceOutcome.MALFORMED_CONTRACT,
            command=(),
            exit_code=None,
            stdout="",
            stderr="",
            started_at=None,
            ended_at=None,
            message=f"cannot read queue: {exc}",
        )
        transition = TransitionResult(
            outcome=TransitionOutcome.MALFORMED_QUEUE, task_id=task_id, message=str(exc)
        )
        return AcceptanceRun(acceptance=acceptance, transition=transition)

    task = _find_task(data, task_id)
    if task is None:
        acceptance = AcceptanceResult(
            passed=False,
            reason=AcceptanceOutcome.MALFORMED_CONTRACT,
            command=(),
            exit_code=None,
            stdout="",
            stderr="",
            started_at=None,
            ended_at=None,
            message=f"no such task: {task_id!r}",
        )
        transition = TransitionResult(outcome=TransitionOutcome.TASK_NOT_FOUND, task_id=task_id)
        return AcceptanceRun(acceptance=acceptance, transition=transition)

    acceptance = run_acceptance(task)

    if acceptance.passed:
        transition = complete_task(queue_path, task_id, owner_pid=owner_pid, lock_path=lock_path)
    else:
        transition = fail_task(queue_path, task_id, owner_pid=owner_pid, lock_path=lock_path)

    return AcceptanceRun(acceptance=acceptance, transition=transition)


class ExecutorOutcome(str, Enum):
    """How the executor phase of a task run ended.

    None of these values feed done/retry/failed directly -- only
    run_acceptance()'s verdict does that. This enum exists purely to
    describe what happened during the executor phase for evidence
    purposes.
    """

    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    MISSING_EXECUTABLE = "missing_executable"
    MALFORMED_CONTRACT = "malformed_contract"


@dataclasses.dataclass(frozen=True)
class ExecutorResult:
    outcome: ExecutorOutcome
    command: tuple
    pid: Optional[int]
    exit_code: Optional[int]
    stdout: str
    stderr: str
    started_at: Optional[str]
    ended_at: Optional[str]
    message: Optional[str] = None

    def to_json_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "command": list(self.command),
            "pid": self.pid,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "message": self.message,
        }


def run_executor(task: dict) -> ExecutorResult:
    """Launch one fresh child process for task["executor_command"].

    Never invokes `claude`, never touches Claude authentication, never
    makes a network call -- it launches exactly and only whatever argv the
    task contract names, with shell=False. Runs in its own process group
    (POSIX setsid via start_new_session=True) so that on timeout the
    *entire* process group -- the executor and anything it itself spawned
    without starting its own new session -- can be killed together, not
    just the direct child.

    The executor's own exit code is captured as evidence only; it never by
    itself decides task success. That is exclusively run_acceptance()'s job
    (see run_task()).
    """
    command = task.get("executor_command")
    working_dir = task.get("working_dir")
    executor_timeout_seconds = task.get("executor_timeout_seconds")

    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) for part in command)
    ):
        return ExecutorResult(
            outcome=ExecutorOutcome.MALFORMED_CONTRACT,
            command=tuple(command) if isinstance(command, list) else (),
            pid=None,
            exit_code=None,
            stdout="",
            stderr="",
            started_at=None,
            ended_at=None,
            message="executor_command must be a non-empty list of strings",
        )

    if not isinstance(working_dir, str) or not working_dir or not os.path.isdir(working_dir):
        return ExecutorResult(
            outcome=ExecutorOutcome.MALFORMED_CONTRACT,
            command=tuple(command),
            pid=None,
            exit_code=None,
            stdout="",
            stderr="",
            started_at=None,
            ended_at=None,
            message=f"working_dir {working_dir!r} is not an existing directory",
        )

    if isinstance(executor_timeout_seconds, bool) or not isinstance(
        executor_timeout_seconds, (int, float)
    ):
        return ExecutorResult(
            outcome=ExecutorOutcome.MALFORMED_CONTRACT,
            command=tuple(command),
            pid=None,
            exit_code=None,
            stdout="",
            stderr="",
            started_at=None,
            ended_at=None,
            message="executor_timeout_seconds must be a positive number",
        )
    if executor_timeout_seconds <= 0:
        return ExecutorResult(
            outcome=ExecutorOutcome.MALFORMED_CONTRACT,
            command=tuple(command),
            pid=None,
            exit_code=None,
            stdout="",
            stderr="",
            started_at=None,
            ended_at=None,
            message="executor_timeout_seconds must be a positive number",
        )

    started_at = _utcnow().isoformat()
    try:
        proc = subprocess.Popen(
            command,
            cwd=working_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            start_new_session=True,
        )
    except OSError as exc:
        return ExecutorResult(
            outcome=ExecutorOutcome.MISSING_EXECUTABLE,
            command=tuple(command),
            pid=None,
            exit_code=None,
            stdout="",
            stderr="",
            started_at=started_at,
            ended_at=_utcnow().isoformat(),
            message=str(exc),
        )

    pid = proc.pid
    try:
        stdout, stderr = proc.communicate(timeout=executor_timeout_seconds)
        return ExecutorResult(
            outcome=ExecutorOutcome.COMPLETED,
            command=tuple(command),
            pid=pid,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            started_at=started_at,
            ended_at=_utcnow().isoformat(),
        )
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass  # already gone by the time we tried to kill it
        stdout, stderr = proc.communicate()  # reap and collect whatever remains
        return ExecutorResult(
            outcome=ExecutorOutcome.TIMED_OUT,
            command=tuple(command),
            pid=pid,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            started_at=started_at,
            ended_at=_utcnow().isoformat(),
            message=f"executor exceeded {executor_timeout_seconds}s timeout",
        )


@dataclasses.dataclass(frozen=True)
class TaskRun:
    """The full result of one run_task() call: executor phase + acceptance phase."""

    executor: ExecutorResult
    acceptance_run: AcceptanceRun

    def to_json_dict(self) -> dict:
        return {
            "executor": self.executor.to_json_dict(),
            "acceptance_run": self.acceptance_run.to_json_dict(),
        }


def _short_circuit_task_run(
    task_id: str, transition_outcome: TransitionOutcome, message: str
) -> TaskRun:
    """Build a TaskRun for the cases where nothing could even be attempted.

    Shared by both of run_task()'s pre-flight checks (queue unreadable,
    task not found) so the placeholder-evidence shape isn't duplicated
    twice for what is, in both cases, "neither phase ever ran".
    """
    executor = ExecutorResult(
        outcome=ExecutorOutcome.MALFORMED_CONTRACT,
        command=(),
        pid=None,
        exit_code=None,
        stdout="",
        stderr="",
        started_at=None,
        ended_at=None,
        message=message,
    )
    acceptance = AcceptanceResult(
        passed=False,
        reason=AcceptanceOutcome.MALFORMED_CONTRACT,
        command=(),
        exit_code=None,
        stdout="",
        stderr="",
        started_at=None,
        ended_at=None,
        message=message,
    )
    transition = TransitionResult(
        outcome=transition_outcome,
        task_id=task_id,
        message=message if transition_outcome == TransitionOutcome.MALFORMED_QUEUE else None,
    )
    return TaskRun(
        executor=executor, acceptance_run=AcceptanceRun(acceptance=acceptance, transition=transition)
    )


def run_task(
    queue_path: str,
    task_id: str,
    owner_pid: Optional[int] = None,
    lock_path: Optional[str] = None,
) -> TaskRun:
    """Run one claimed task's executor, then always run acceptance after it.

    The executor's outcome (completed with any exit code, timed out, or
    failed to launch) never itself decides done/retry/failed -- acceptance
    always runs afterward and is the sole authority on that, exactly as
    run_acceptance_and_record() already enforces on its own. This function
    only adds the executor phase in front of the unchanged Milestone 3
    pipeline; it does not duplicate any of that pipeline's logic.
    """
    if owner_pid is None:
        owner_pid = os.getpid()

    try:
        data = _read_and_validate(queue_path)
    except (json.JSONDecodeError, MalformedQueueError) as exc:
        return _short_circuit_task_run(
            task_id, TransitionOutcome.MALFORMED_QUEUE, f"cannot read queue: {exc}"
        )

    task = _find_task(data, task_id)
    if task is None:
        return _short_circuit_task_run(
            task_id, TransitionOutcome.TASK_NOT_FOUND, f"no such task: {task_id!r}"
        )

    executor_result = run_executor(task)
    acceptance_run = run_acceptance_and_record(
        queue_path, task_id, owner_pid=owner_pid, lock_path=lock_path
    )
    return TaskRun(executor=executor_result, acceptance_run=acceptance_run)


def _wait_for_sentinel(sentinel_path: str) -> None:
    while not os.path.exists(sentinel_path):
        time.sleep(0.001)


def main(argv=None) -> int:
    """CLI entry point.

    Subcommands: `claim <queue_path>`, `complete <queue_path> <task_id>
    [--owner-pid PID]`, `fail <queue_path> <task_id> [--owner-pid PID]`.

    This is a debug/test interface, not a durable ownership mechanism: it
    records its own PID (or an explicitly passed ``--owner-pid``) as the
    actor and then exits immediately after printing its result, so that PID
    is dead the instant the command returns. See the "Ownership contract"
    section of this module's docstring before using this CLI to represent
    anything whose liveness must be tracked -- a real owner must be a
    long-lived process calling claim_next()/complete_task()/fail_task()
    directly, not this one-shot command.

    Exit code 0 means the call ran to completion; the actual result (one of
    the ClaimOutcome or TransitionOutcome values, matching the subcommand)
    is printed as a JSON object on stdout under the "outcome" key. Exit code
    1 means an exception escaped this function unexpectedly (defensive
    only -- should not occur, since every operation here converts
    anticipated failures into a result object).
    """
    parser = argparse.ArgumentParser(prog="nightshift.runtime.queue")
    sub = parser.add_subparsers(dest="command", required=True)

    claim_parser = sub.add_parser("claim")
    claim_parser.add_argument("queue_path")
    claim_parser.add_argument(
        "--stale-threshold",
        type=int,
        default=DEFAULT_STALE_THRESHOLD_SECONDS,
        dest="stale_threshold_seconds",
    )
    claim_parser.add_argument("--wait-for", dest="wait_for", default=None)

    complete_parser = sub.add_parser("complete")
    complete_parser.add_argument("queue_path")
    complete_parser.add_argument("task_id")
    complete_parser.add_argument("--owner-pid", type=int, default=None, dest="owner_pid")
    complete_parser.add_argument("--wait-for", dest="wait_for", default=None)

    fail_parser = sub.add_parser("fail")
    fail_parser.add_argument("queue_path")
    fail_parser.add_argument("task_id")
    fail_parser.add_argument("--owner-pid", type=int, default=None, dest="owner_pid")
    fail_parser.add_argument("--wait-for", dest="wait_for", default=None)

    args = parser.parse_args(argv)

    if args.command == "claim":
        if args.wait_for:
            _wait_for_sentinel(args.wait_for)
        result = claim_next(
            args.queue_path,
            stale_threshold_seconds=args.stale_threshold_seconds,
        )
        print(json.dumps(result.to_json_dict()))
        return 0

    if args.command == "complete":
        if args.wait_for:
            _wait_for_sentinel(args.wait_for)
        result = complete_task(args.queue_path, args.task_id, owner_pid=args.owner_pid)
        print(json.dumps(result.to_json_dict()))
        return 0

    if args.command == "fail":
        if args.wait_for:
            _wait_for_sentinel(args.wait_for)
        result = fail_task(args.queue_path, args.task_id, owner_pid=args.owner_pid)
        print(json.dumps(result.to_json_dict()))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
