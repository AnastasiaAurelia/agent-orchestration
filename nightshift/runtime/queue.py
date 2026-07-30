"""Deterministic queue, claim/lock, retry, acceptance, executor, recovery,
and reporting primitives for Diana Nightshift (Milestones 1-6).

This module reads a small JSON task queue, atomically claims exactly one
pending task per call, runs a bounded executor and a deterministic
acceptance check, drives the resulting done/retry/failed state machine,
recovers tasks abandoned by a dead claimant, and can render a local
Markdown report from the queue and an optional run log. It does not invoke
Claude Code (`claude`/`claude -p`), does not touch Claude authentication,
does not schedule anything (no cron/systemd integration), and does not make
any network or external-messaging call anywhere in this file. See
docs/nightshift/RESEARCH.md for the research this milestone series is
scoped from, and the Mandatory Human Approval Gate in that scope's
governing task for what remains explicitly out of bounds until a separate,
supervised integration milestone.

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
-- :func:`run_task` always runs acceptance afterward whenever the executor
actually ran (COMPLETED or TIMED_OUT); if the executor never ran at all
(MALFORMED_CONTRACT, MISSING_EXECUTABLE, or POLICY_REJECTED -- see
"Security policy" below), :func:`run_task` fails the attempt directly
instead, since there is nothing for acceptance to meaningfully check and a
trivially-passing acceptance command must not paper over a task that never
executed.

``approved_root`` (non-empty string) is the directory ``working_dir`` must
resolve inside of -- see "Security policy" below.

``validate_queue`` only checks the *structural* shape of these fields
(right types, non-empty). It deliberately does not check that
``working_dir``/``approved_root`` exist on disk, or that one is inside the
other: those are environmental conditions specific to whenever one
particular task actually runs, not a queue-wide schema property -- one
task's bad paths must not make ``validate_queue`` reject every other task
in the same file. :func:`nightshift.runtime.policy.validate_working_dir`
checks it lazily, per task, at run time.

Security policy
----------------
:mod:`nightshift.runtime.policy` is the deterministic boundary applied to
every executor/acceptance command before it is ever launched: executable
resolution, a denylist of dangerous operations (different lists for
executor vs. acceptance), an environment built by allowlist from empty
(never the full parent environment), and the ``working_dir``/
``approved_root`` containment check. A rejection at any of these points
produces ``ExecutorOutcome.POLICY_REJECTED`` / ``AcceptanceOutcome.
POLICY_REJECTED`` -- the command is never launched -- and flows into
:func:`fail_task` exactly like any other non-passing outcome. This is a
boundary for a *trusted* task author running *generic* commands
unattended, not a sandbox for an untrusted one; see that module's own
docstring for the full threat model and what remains possible.

Abandoned-run recovery: both :func:`claim_next` (as a side effect of looking
for work) and the standalone :func:`reap_abandoned_tasks` (for recovery on
its own, independent of claiming) share the same retry-aware decision via
``_recover_abandoned_claims``: a "claimed" task whose owner PID is dead and
whose ``claimed_at`` age exceeds the caller's stale threshold is recovered
to "pending" if ``attempt_count < max_attempts`` (claimable again), or
straight to "failed" if not (it already used its last attempt while
unsupervised, and must never be handed out again). A live claim, and a dead
claim that has not yet exceeded the stale threshold, are both left
untouched either way. Because recovery is retry-aware, ``validate_queue``
also enforces ``attempt_count < max_attempts`` whenever status is
"pending" -- a "pending" task that already exhausted its attempts can no
longer occur, from any code path in this module.

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
import re
import signal
import subprocess
import sys
import tempfile
import time
from enum import Enum
from typing import Any, Optional

from nightshift.runtime import policy as _policy

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
    "approved_root",
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
    recovered_failed_task_ids: tuple = ()
    blocking_task_id: Optional[str] = None
    message: Optional[str] = None

    def to_json_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "task_id": self.task_id,
            "attempt_count": self.attempt_count,
            "recovered_stale_task_ids": list(self.recovered_stale_task_ids),
            "recovered_failed_task_ids": list(self.recovered_failed_task_ids),
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
        if status == "pending" and attempt_count >= max_attempts:
            raise MalformedQueueError(
                f"task {task_id!r} is 'pending' but attempt_count ({attempt_count}) "
                f"already reached max_attempts ({max_attempts})"
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

        approved_root = task["approved_root"]
        if not isinstance(approved_root, str) or not approved_root:
            raise MalformedQueueError(
                f"task {task_id!r} has an invalid 'approved_root' (must be a non-empty string)"
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


def get_task(queue_path: str, task_id: str) -> Optional[dict]:
    """Read-only lookup of one task's current contract fields.

    Unlocked, exactly like run_acceptance_and_record()'s own initial read --
    contract fields (title, working_dir, approved_root, executor_command,
    etc.) are immutable once a task exists, so no lock is needed to read
    them safely. This exists for adapters that need those fields after
    claiming (e.g. a Claude-specific executor building a task-specific
    invocation) without reaching into this module's private helpers or
    re-implementing queue reading a second time elsewhere.

    Callers needing a live, lock-protected view of *mutable* fields
    (status, attempt_count) should use claim_next()/complete_task()/
    fail_task()'s own return values instead -- this is not a substitute
    for those.

    Returns None if the task doesn't exist, or if the queue can't be read
    or fails validation -- both are "not found" from this function's point
    of view; it never guesses or repairs anything.
    """
    try:
        data = _read_and_validate(queue_path)
    except (json.JSONDecodeError, MalformedQueueError):
        return None
    return _find_task(data, task_id)


def _recover_abandoned_claims(tasks: list, now: datetime.datetime, stale_threshold_seconds: int):
    """Mutate ``tasks`` in place, recovering abandoned "claimed" entries.

    Shared by claim_next() and reap_abandoned_tasks() so the retry-aware
    decision lives in exactly one place. A task is only ever touched here
    if its recorded PID is confirmed dead AND its claimed_at age exceeds
    stale_threshold_seconds -- a live claim, or a dead-but-not-yet-stale
    claim, is left completely untouched either way.

    Returns (requeued_ids, failed_ids, blocking_task_id):
      - requeued_ids: recovered to "pending" (attempt_count < max_attempts)
      - failed_ids: recovered straight to "failed" (already at the limit)
      - blocking_task_id: the first task left untouched because it is
        still live or not yet stale (None if there was none)
    """
    requeued_ids = []
    failed_ids = []
    blocking_task_id = None

    for task in tasks:
        if task["status"] != "claimed":
            continue
        pid = task["claimed_pid"]
        claimed_at = _parse_timestamp(task["claimed_at"])
        age_seconds = (now - claimed_at).total_seconds()
        if not _is_pid_alive(pid) and age_seconds > stale_threshold_seconds:
            if task["attempt_count"] >= task["max_attempts"]:
                task["status"] = "failed"
                failed_ids.append(task["id"])
            else:
                task["status"] = "pending"
                requeued_ids.append(task["id"])
            task["claimed_pid"] = None
            task["claimed_at"] = None
        elif blocking_task_id is None:
            blocking_task_id = task["id"]

    return requeued_ids, failed_ids, blocking_task_id


def _append_run_log(
    run_log_path: Optional[str],
    event: str,
    task_id: str,
    attempt_count: Optional[int],
    detail: Optional[str] = None,
    now: Optional[datetime.datetime] = None,
    executor_outcome: Optional[str] = None,
    executor_exit_code: Optional[int] = None,
    acceptance_outcome: Optional[str] = None,
    acceptance_exit_code: Optional[int] = None,
    evidence_excerpt: Optional[str] = None,
) -> None:
    """Best-effort append of one JSONL evidence line for the report generator.

    Does nothing if run_log_path is None -- every function that accepts it
    defaults to None, so this is opt-in and fully backward compatible with
    every earlier milestone's calls. The five ``executor_*``/``acceptance_*``/
    ``evidence_excerpt`` fields (Milestone 7C.1) are additive and optional --
    every existing call site that omits them still produces the exact same
    entry shape as before, and _load_run_log_events()/render_report() only
    ever read fields by name, so old and new log lines coexist safely in the
    same file. ``evidence_excerpt`` is the only field that can ever carry
    process output text, and only a short, redacted excerpt (see
    _safe_log_excerpt in claude_executor-adjacent evidence recording) --
    never a full stdout/stderr capture, an environment, or a raw auth-status
    payload. Swallows OSError: a failure to log must never fail the actual
    state transition that already happened; the queue file remains the sole
    canonical state, this is auxiliary evidence only.
    """
    if run_log_path is None:
        return
    entry = {
        "timestamp": (now or _utcnow()).isoformat(),
        "task_id": task_id,
        "event": event,
        "attempt_count": attempt_count,
        "detail": detail,
        "executor_outcome": executor_outcome,
        "executor_exit_code": executor_exit_code,
        "acceptance_outcome": acceptance_outcome,
        "acceptance_exit_code": acceptance_exit_code,
        "evidence_excerpt": evidence_excerpt,
    }
    try:
        with open(run_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError:
        pass


_ABANDONED_RUN_DETAIL = "abandoned: owner process was dead and past the stale threshold"


def _log_recovered_tasks(
    run_log_path: Optional[str],
    tasks: list,
    requeued_ids: list,
    failed_ids: list,
    now: datetime.datetime,
) -> None:
    """Append one log line per task _recover_abandoned_claims() touched.

    Shared by claim_next() and reap_abandoned_tasks() so both abandoned-run
    recovery paths produce identically-shaped evidence for the report.
    """
    if run_log_path is None or (not requeued_ids and not failed_ids):
        return
    by_id = {task["id"]: task for task in tasks}
    for task_id in requeued_ids:
        _append_run_log(
            run_log_path,
            "recovered_requeued",
            task_id,
            by_id[task_id]["attempt_count"],
            detail=_ABANDONED_RUN_DETAIL,
            now=now,
        )
    for task_id in failed_ids:
        _append_run_log(
            run_log_path,
            "recovered_failed",
            task_id,
            by_id[task_id]["attempt_count"],
            detail=_ABANDONED_RUN_DETAIL,
            now=now,
        )


def claim_next(
    queue_path: str,
    stale_threshold_seconds: int = DEFAULT_STALE_THRESHOLD_SECONDS,
    lock_path: Optional[str] = None,
    claimant_pid: Optional[int] = None,
    run_log_path: Optional[str] = None,
) -> ClaimResult:
    """Atomically claim exactly one pending task from ``queue_path``.

    Recovers any abandoned "claimed" task (dead PID, age past
    ``stale_threshold_seconds``) as part of the same locked pass before
    selecting a candidate to claim, via the same retry-aware logic
    ``reap_abandoned_tasks`` uses on its own -- see
    ``_recover_abandoned_claims``. Never touches a live claim or a
    dead-but-not-yet-stale claim. If ``run_log_path`` is given, any
    recovery is appended to it for the report generator; the claim itself
    is not separately logged (current "claimed" counts come from the queue
    snapshot, not the log).
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
            requeued_ids, failed_ids, blocking_task_id = _recover_abandoned_claims(
                tasks, now, stale_threshold_seconds
            )

            claimed_task = None
            for task in tasks:
                if task["status"] == "pending":
                    claimed_task = task
                    break

            if claimed_task is None:
                # A requeued task is marked "pending" in `tasks` above, so
                # this loop can only fail to find one when requeued_ids is
                # also empty -- but a task recovered straight to "failed"
                # never becomes "pending", so failed_ids alone can still
                # mean something was mutated even with nothing to claim.
                if failed_ids:
                    try:
                        _atomic_write(queue_path, data)
                    except OSError as exc:
                        return ClaimResult(
                            outcome=ClaimOutcome.INTERNAL_FAILURE,
                            message=f"write failed: {exc}",
                        )
                _log_recovered_tasks(run_log_path, tasks, requeued_ids, failed_ids, now)
                outcome = (
                    ClaimOutcome.LOCK_HELD
                    if blocking_task_id is not None
                    else ClaimOutcome.NO_PENDING_TASK
                )
                return ClaimResult(
                    outcome=outcome,
                    recovered_stale_task_ids=tuple(requeued_ids),
                    recovered_failed_task_ids=tuple(failed_ids),
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

            _log_recovered_tasks(run_log_path, tasks, requeued_ids, failed_ids, now)
            return ClaimResult(
                outcome=ClaimOutcome.CLAIMED,
                task_id=claimed_task["id"],
                attempt_count=claimed_task["attempt_count"],
                recovered_stale_task_ids=tuple(requeued_ids),
                recovered_failed_task_ids=tuple(failed_ids),
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
    run_log_path: Optional[str] = None,
) -> TransitionResult:
    """Mark a "claimed" task "done", clearing its claim ownership metadata.

    Only succeeds if ``task_id`` exists, is currently "claimed", and is
    owned by ``owner_pid`` (defaulting to os.getpid() of the caller) --
    exactly the same identity check :func:`claim_next` recorded at claim
    time, never a looser "some owner exists" check. If ``run_log_path`` is
    given, a successful completion is appended for the report generator.
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

            _append_run_log(run_log_path, "done", task_id, task["attempt_count"])
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
    run_log_path: Optional[str] = None,
    detail: Optional[str] = None,
) -> TransitionResult:
    """Record a failed attempt on a "claimed" task, owned by ``owner_pid``.

    If attempt_count has not yet reached max_attempts, the task returns to
    "pending" (claim metadata cleared) and is claimable again. Otherwise it
    becomes permanently "failed" (claim metadata cleared, never claimable
    again). attempt_count itself is never modified here -- it was already
    incremented exactly once, at claim time, by claim_next(). If
    ``run_log_path`` is given, the outcome is appended for the report
    generator; ``detail`` (e.g. an AcceptanceOutcome value like
    "timed_out") is carried through unchanged so the report can show
    exactly why, without this function needing to know what an acceptance
    check even is.
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

            _append_run_log(
                run_log_path,
                "requeued" if outcome == TransitionOutcome.REQUEUED else "failed_permanently",
                task_id,
                task["attempt_count"],
                detail=detail,
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


class ReapOutcome(str, Enum):
    """Outcome of one reap_abandoned_tasks() call.

    ``RECOVERED``
        At least one abandoned task's ownership changed -- see
        requeued_task_ids / failed_task_ids on the result for which.

    ``NOTHING_TO_REAP``
        The queue is valid and nothing needed recovering: no "claimed"
        task was both dead and past the stale threshold. A live claim or a
        dead-but-not-yet-stale claim is not an error, just nothing to do.

    ``LOCK_BUSY`` / ``MALFORMED_QUEUE`` / ``INTERNAL_FAILURE``
        Same meaning as the identically-named ClaimOutcome values.
    """

    RECOVERED = "recovered"
    NOTHING_TO_REAP = "nothing_to_reap"
    LOCK_BUSY = "lock_busy"
    MALFORMED_QUEUE = "malformed_queue"
    INTERNAL_FAILURE = "internal_failure"


@dataclasses.dataclass(frozen=True)
class ReapResult:
    outcome: ReapOutcome
    requeued_task_ids: tuple = ()
    failed_task_ids: tuple = ()
    message: Optional[str] = None

    def to_json_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "requeued_task_ids": list(self.requeued_task_ids),
            "failed_task_ids": list(self.failed_task_ids),
            "message": self.message,
        }


def reap_abandoned_tasks(
    queue_path: str,
    stale_threshold_seconds: int = DEFAULT_STALE_THRESHOLD_SECONDS,
    lock_path: Optional[str] = None,
    run_log_path: Optional[str] = None,
) -> ReapResult:
    """Recover abandoned "claimed" tasks without attempting to claim work.

    Applies the exact same dead-PID + staleness-threshold + retry-limit
    decision claim_next() applies as a side effect of looking for work (see
    _recover_abandoned_claims) -- this function exists so recovery can be
    triggered on its own, independent of claiming, e.g. by a periodic health
    check. Never steals a live claim or a dead-but-not-yet-stale claim.
    Atomic and lock-protected exactly like every other mutating operation
    in this module: a malformed queue or a busy OS mutex leaves the
    canonical file provably untouched.
    """
    if lock_path is None:
        lock_path = queue_path + ".lock"

    try:
        with _os_lock(lock_path):
            try:
                data = _read_and_validate(queue_path)
            except (json.JSONDecodeError, MalformedQueueError) as exc:
                return ReapResult(outcome=ReapOutcome.MALFORMED_QUEUE, message=str(exc))

            now = _utcnow()
            tasks = data["tasks"]
            requeued_ids, failed_ids, _blocking_task_id = _recover_abandoned_claims(
                tasks, now, stale_threshold_seconds
            )

            if not requeued_ids and not failed_ids:
                return ReapResult(outcome=ReapOutcome.NOTHING_TO_REAP)

            try:
                _atomic_write(queue_path, data)
            except OSError as exc:
                return ReapResult(
                    outcome=ReapOutcome.INTERNAL_FAILURE, message=f"write failed: {exc}"
                )

            _log_recovered_tasks(run_log_path, tasks, requeued_ids, failed_ids, now)
            return ReapResult(
                outcome=ReapOutcome.RECOVERED,
                requeued_task_ids=tuple(requeued_ids),
                failed_task_ids=tuple(failed_ids),
            )
    except _LockBusyError:
        return ReapResult(
            outcome=ReapOutcome.LOCK_BUSY,
            message="the short-lived OS mutex for this queue is held by another process",
        )
    except OSError as exc:
        return ReapResult(
            outcome=ReapOutcome.INTERNAL_FAILURE, message=f"unexpected OS error: {exc}"
        )


class AcceptanceOutcome(str, Enum):
    """Why an acceptance run did or did not pass.

    All five non-passing reasons are deliberately treated identically by
    :func:`run_acceptance_and_record`: every one of them drives fail_task(),
    exactly like any other failed attempt. Only PASSED drives complete_task().
    """

    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    MISSING_COMMAND = "missing_command"
    MALFORMED_CONTRACT = "malformed_contract"
    POLICY_REJECTED = "policy_rejected"


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

    Before ever launching the command, also applies the deterministic
    policy boundary (nightshift.runtime.policy): working_dir must resolve
    inside approved_root, and the command must pass ACCEPTANCE_POLICY's
    deny rules. Either failure returns POLICY_REJECTED without spawning
    anything. The child process's environment is built by allowlist
    (policy.build_allowed_env()), never inherited from this process.
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

    approved_root = task.get("approved_root")
    working_dir_decision = _policy.validate_working_dir(working_dir, approved_root)
    if not working_dir_decision.allowed:
        return AcceptanceResult(
            passed=False,
            reason=AcceptanceOutcome.POLICY_REJECTED,
            command=tuple(command),
            exit_code=None,
            stdout="",
            stderr="",
            started_at=None,
            ended_at=None,
            message=working_dir_decision.reason,
        )

    policy_decision = _policy.check_command(command, working_dir, _policy.ACCEPTANCE_POLICY)
    if not policy_decision.allowed:
        # Two distinct situations, not one: the executable could not be
        # resolved at all (unchanged MISSING_COMMAND, exactly as before this
        # milestone) vs. it resolved fine but matched a deny rule (the new
        # POLICY_REJECTED). Conflating them would break existing behavior
        # for a plain nonexistent-executable case.
        reason = (
            AcceptanceOutcome.MISSING_COMMAND
            if policy_decision.resolved_executable is None
            else AcceptanceOutcome.POLICY_REJECTED
        )
        return AcceptanceResult(
            passed=False,
            reason=reason,
            command=tuple(command),
            exit_code=None,
            stdout="",
            stderr="",
            started_at=None,
            ended_at=None,
            message=policy_decision.reason,
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
            env=_policy.build_allowed_env(),
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


# Token-shaped substrings that must never survive into durable evidence,
# even inside a short excerpt of captured stdout/stderr (Milestone 7C.1).
# Deliberately crude and conservative (bearer tokens, "sk-..." style API
# keys, and any long opaque run of id-like characters) -- a false-positive
# redaction only loses a little diagnostic text; a false negative could leak
# a credential into the run log, which is the outcome that must never
# happen.
_TOKEN_LIKE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{10,}|Bearer\s+[A-Za-z0-9._-]{10,}|[A-Za-z0-9_-]{40,})",
    re.IGNORECASE,
)
_EVIDENCE_EXCERPT_MAX_LEN = 300


def _redact(text: str) -> str:
    return _TOKEN_LIKE_RE.sub("[REDACTED]", text)


def _safe_log_excerpt(executor_result: Optional["ExecutorResult"]) -> Optional[str]:
    """A short, redacted excerpt of executor output for durable evidence only.

    None for a clean success (COMPLETED, exit_code 0) -- there is nothing
    diagnostically necessary to retain, and less retained text is less risk.
    For anything else, prefers stderr (where CLI error/auth messages usually
    land) falling back to stdout, then redacts token-shaped substrings and
    bounds the length -- this is deliberately not the full capture already
    held in the in-memory ExecutorResult (which the terminal CycleResult
    still prints in full, unchanged from before this milestone); only this
    bounded, screened excerpt is ever written to the durable run log.
    """
    if executor_result is None:
        return None
    if executor_result.outcome == ExecutorOutcome.COMPLETED and executor_result.exit_code == 0:
        return None
    raw = (executor_result.stderr or executor_result.stdout or "").strip()
    if not raw:
        return None
    return _redact(raw)[:_EVIDENCE_EXCERPT_MAX_LEN]


def _executor_succeeded(executor_result: Optional["ExecutorResult"]) -> bool:
    """The Completion Invariant (Milestone 7C.1): did the executor actually succeed?

    None means "no executor phase to judge" (a direct/standalone caller
    exercising acceptance on its own, unchanged since before this
    milestone) -- treated as trivially true so existing direct callers keep
    their exact prior behavior. Otherwise, only COMPLETED with exit_code
    exactly 0 counts as success; TIMED_OUT, AUTH_FAILED, and a COMPLETED
    outcome with any nonzero exit_code are all "did not succeed", regardless
    of what an acceptance command run afterward decides on its own.
    """
    return executor_result is None or (
        executor_result.outcome == ExecutorOutcome.COMPLETED and executor_result.exit_code == 0
    )


def _executor_failure_detail(executor_result: "ExecutorResult") -> str:
    """Human-readable, report-visible reason a non-succeeding executor forced failure."""
    if executor_result.outcome == ExecutorOutcome.AUTH_FAILED:
        cause = f"executor authentication failure (exit code {executor_result.exit_code})"
    elif executor_result.outcome == ExecutorOutcome.TIMED_OUT:
        cause = "executor timed out"
    else:
        cause = f"executor nonzero exit code ({executor_result.exit_code})"
    return (
        f"{cause}; acceptance result is not sufficient for completion because the "
        "executor did not succeed"
    )


def run_acceptance_and_record(
    queue_path: str,
    task_id: str,
    owner_pid: Optional[int] = None,
    lock_path: Optional[str] = None,
    run_log_path: Optional[str] = None,
    executor_result: Optional["ExecutorResult"] = None,
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

    Completion Invariant (Milestone 7C.1): a PASSED acceptance drives
    complete_task() only when _executor_succeeded(executor_result) is also
    true. Every other AcceptanceOutcome (FAILED, TIMED_OUT, MISSING_COMMAND,
    MALFORMED_CONTRACT) drives fail_task() identically, exactly as before --
    but so does a PASSED acceptance whose executor did not succeed: a
    trivially-passing acceptance command must never mark a task "done" when
    the executor that supposedly did the work timed out, failed to
    authenticate, or exited non-zero. Acceptance still runs and its result
    is still recorded either way, since it remains useful diagnostic
    evidence -- it is only the *transition* that the executor's own outcome
    can veto here, never whether acceptance itself runs.

    ``executor_result``, when given, is the just-completed executor phase
    this acceptance run follows (see _executor_succeeded()). It is None for
    a direct/standalone caller exercising acceptance on its own -- that case
    is unchanged from every milestone before 7C.1.

    A structured, redaction-safe evidence line (event "task_run_evidence")
    is appended to the run log -- when one is configured -- *before* the
    complete_task()/fail_task() call that produces the actual transition, so
    a "done" (or "requeued"/"failed_permanently") log event can never exist
    without the executor/acceptance evidence that justified it already
    durably recorded ahead of it.
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

    _append_run_log(
        run_log_path,
        "task_run_evidence",
        task_id,
        task["attempt_count"],
        executor_outcome=executor_result.outcome.value if executor_result is not None else None,
        executor_exit_code=executor_result.exit_code if executor_result is not None else None,
        acceptance_outcome=acceptance.reason.value,
        acceptance_exit_code=acceptance.exit_code,
        evidence_excerpt=_safe_log_excerpt(executor_result),
    )

    if not _executor_succeeded(executor_result):
        transition = fail_task(
            queue_path,
            task_id,
            owner_pid=owner_pid,
            lock_path=lock_path,
            run_log_path=run_log_path,
            detail=_executor_failure_detail(executor_result),
        )
    elif acceptance.passed:
        transition = complete_task(
            queue_path, task_id, owner_pid=owner_pid, lock_path=lock_path, run_log_path=run_log_path
        )
    else:
        transition = fail_task(
            queue_path,
            task_id,
            owner_pid=owner_pid,
            lock_path=lock_path,
            run_log_path=run_log_path,
            detail=acceptance.reason.value,
        )

    return AcceptanceRun(acceptance=acceptance, transition=transition)


class ExecutorOutcome(str, Enum):
    """How the executor phase of a task run ended.

    COMPLETED, TIMED_OUT, and AUTH_FAILED all mean the executor genuinely
    ran (to completion or not); run_task() still runs acceptance afterward
    regardless, exactly as before -- but since Milestone 7C.1, only a
    COMPLETED outcome with exit_code == 0 can ever let a passing acceptance
    result drive complete_task() (see run_acceptance_and_record()'s
    Completion Invariant). TIMED_OUT, AUTH_FAILED, and a COMPLETED outcome
    with a nonzero exit_code all force fail_task() regardless of what
    acceptance decided on its own -- a passing acceptance command must
    never paper over an executor that timed out, failed to authenticate, or
    exited non-zero.

    AUTH_FAILED (Milestone 7C.1) is produced only by
    nightshift.runtime.claude_executor's own post-hoc classification of a
    nonzero-exit real Claude invocation whose captured output matches
    recognized authentication-failure evidence (e.g. an HTTP 401, "access
    token has expired") -- the generic queue.run_executor() here never
    produces it, since that classification is Claude-specific.

    MALFORMED_CONTRACT, MISSING_EXECUTABLE, and POLICY_REJECTED mean the
    executor never ran at all -- run_task() fails the attempt directly for
    these three (see run_task()'s docstring), since there is nothing for
    acceptance to meaningfully check and a lenient acceptance command must
    not paper over a task that never executed.
    """

    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    MISSING_EXECUTABLE = "missing_executable"
    MALFORMED_CONTRACT = "malformed_contract"
    POLICY_REJECTED = "policy_rejected"
    AUTH_FAILED = "auth_failed"


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

    Before ever launching the command, also applies the deterministic
    policy boundary (nightshift.runtime.policy): working_dir must resolve
    inside approved_root, and the command must pass EXECUTOR_POLICY's deny
    rules. Either failure returns POLICY_REJECTED without spawning
    anything. The child process's environment is built by allowlist
    (policy.build_allowed_env()), never inherited from this process.
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

    approved_root = task.get("approved_root")
    working_dir_decision = _policy.validate_working_dir(working_dir, approved_root)
    if not working_dir_decision.allowed:
        return ExecutorResult(
            outcome=ExecutorOutcome.POLICY_REJECTED,
            command=tuple(command),
            pid=None,
            exit_code=None,
            stdout="",
            stderr="",
            started_at=None,
            ended_at=None,
            message=working_dir_decision.reason,
        )

    policy_decision = _policy.check_command(command, working_dir, _policy.EXECUTOR_POLICY)
    if not policy_decision.allowed:
        # See run_acceptance()'s identical comment: resolution failure stays
        # MISSING_EXECUTABLE (unchanged); a resolved-but-denied executable
        # is the new POLICY_REJECTED.
        outcome = (
            ExecutorOutcome.MISSING_EXECUTABLE
            if policy_decision.resolved_executable is None
            else ExecutorOutcome.POLICY_REJECTED
        )
        return ExecutorResult(
            outcome=outcome,
            command=tuple(command),
            pid=None,
            exit_code=None,
            stdout="",
            stderr="",
            started_at=None,
            ended_at=None,
            message=policy_decision.reason,
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
            env=_policy.build_allowed_env(),
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


_EXECUTOR_NEVER_RAN_OUTCOMES = frozenset(
    {
        ExecutorOutcome.MALFORMED_CONTRACT,
        ExecutorOutcome.MISSING_EXECUTABLE,
        ExecutorOutcome.POLICY_REJECTED,
    }
)


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
    run_log_path: Optional[str] = None,
) -> TaskRun:
    """Run one claimed task's executor, then run acceptance if it actually ran.

    If the executor genuinely ran (COMPLETED or TIMED_OUT), acceptance
    always runs afterward -- but since Milestone 7C.1, its PASSED result can
    only drive complete_task() when the executor also succeeded (COMPLETED
    with exit_code exactly 0); see run_acceptance_and_record()'s Completion
    Invariant. A timed-out or nonzero-exit executor forces fail_task()
    regardless of what acceptance decided on its own.

    If the executor never ran at all (MALFORMED_CONTRACT,
    MISSING_EXECUTABLE, or POLICY_REJECTED), this function fails the
    attempt directly via fail_task() instead of running acceptance:
    otherwise a lenient or trivial acceptance command (exactly the shape
    most task contracts use) could mark a policy-rejected or never-executed
    task "done", silently defeating the point of rejecting it in the first
    place.
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

    if executor_result.outcome in _EXECUTOR_NEVER_RAN_OUTCOMES:
        transition = fail_task(
            queue_path,
            task_id,
            owner_pid=owner_pid,
            lock_path=lock_path,
            run_log_path=run_log_path,
            detail=f"executor_{executor_result.outcome.value}",
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
            message="acceptance skipped: the executor never ran",
        )
        return TaskRun(
            executor=executor_result, acceptance_run=AcceptanceRun(acceptance=acceptance, transition=transition)
        )

    acceptance_run = run_acceptance_and_record(
        queue_path,
        task_id,
        owner_pid=owner_pid,
        lock_path=lock_path,
        run_log_path=run_log_path,
        executor_result=executor_result,
    )
    return TaskRun(executor=executor_result, acceptance_run=acceptance_run)


# ---------------------------------------------------------------------------
# Deterministic local report
#
# File-only, stdlib-only. No language model is ever invoked here, no network
# call is ever made, and nothing here embellishes or infers beyond what the
# queue snapshot and run log literally state. render_report() is a pure
# function -- given the same arguments it always produces byte-identical
# output -- so that it can be tested without any filesystem or clock
# coupling; write_report() is the thin I/O wrapper around it.
# ---------------------------------------------------------------------------

_REPORT_EVENT_LABELS = {
    "done": "done",
    "requeued": "requeued for another attempt",
    "failed_permanently": "permanently failed",
    "recovered_requeued": "recovered from an abandoned run, requeued",
    "recovered_failed": "recovered from an abandoned run, permanently failed",
    "task_run_evidence": "run evidence recorded",
}


def _load_run_log_events(run_log_path: Optional[str]):
    """Read a JSONL run log. Returns (valid_events, malformed_line_count).

    A missing path or missing file is not an error -- it just means no
    history exists yet (e.g. no run has ever happened). Each malformed
    line is skipped and counted, never guessed at or repaired, matching
    this module's validation philosophy everywhere else.
    """
    if run_log_path is None or not os.path.exists(run_log_path):
        return [], 0
    events = []
    malformed = 0
    with open(run_log_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                malformed += 1
    return events, malformed


def render_report(
    queue_data: Optional[dict],
    queue_error: Optional[str],
    log_events: list,
    generated_at: str,
    malformed_log_line_count: int = 0,
) -> str:
    """Render the Markdown report text. Pure: no I/O, no clock, no model.

    ``queue_data`` is an already-validated queue dict, or None if the queue
    could not be read (see ``queue_error``) -- a report is still produced
    either way, so this remains useful after a crash or when the queue
    itself is currently unreadable. Every line traces directly to a queue
    field or a logged event; nothing is summarized, interpreted, or
    embellished beyond that.
    """
    lines = ["# Nightshift Report", "", f"Generated: {generated_at}", ""]

    if queue_error is not None:
        lines.append(f"**Queue unreadable:** {queue_error}")
        lines.append("")
        tasks = []
    else:
        tasks = queue_data["tasks"] if queue_data else []

    done_count = 0
    pending_new_count = 0
    pending_retry_count = 0
    claimed_count = 0
    failed_count = 0
    next_pending = []
    for task in tasks:
        status = task["status"]
        if status == "pending":
            if task["attempt_count"] == 0:
                pending_new_count += 1
            else:
                pending_retry_count += 1
            next_pending.append(task)
        elif status == "claimed":
            claimed_count += 1
        elif status == "done":
            done_count += 1
        elif status == "failed":
            failed_count += 1

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total tasks: {len(tasks)}")
    lines.append(f"- Done: {done_count}")
    lines.append(f"- Pending (never attempted): {pending_new_count}")
    lines.append(f"- Pending retry: {pending_retry_count}")
    lines.append(f"- Claimed (in progress): {claimed_count}")
    lines.append(f"- Permanently failed: {failed_count}")
    lines.append("")

    lines.append("## Next Pending Tasks")
    lines.append("")
    if next_pending:
        for task in next_pending:
            lines.append(
                f"- `{task['id']}`: {task['title']} "
                f"(attempt {task['attempt_count']}/{task['max_attempts']})"
            )
    else:
        lines.append("(none)")
    lines.append("")

    lines.append("## Run History")
    lines.append("")
    if log_events:
        for event in sorted(log_events, key=lambda e: e.get("timestamp", "")):
            label = _REPORT_EVENT_LABELS.get(event.get("event"), str(event.get("event")))
            detail = event.get("detail")
            detail_suffix = f" -- {detail}" if detail else ""
            lines.append(
                f"- {event.get('timestamp', '?')} — `{event.get('task_id', '?')}` — "
                f"{label} (attempt {event.get('attempt_count', '?')}){detail_suffix}"
            )
    else:
        lines.append("No runs recorded yet.")
    lines.append("")

    if malformed_log_line_count:
        lines.append(
            f"_{malformed_log_line_count} malformed run-log line(s) were ignored and "
            "excluded from this report._"
        )
        lines.append("")

    return "\n".join(lines) + "\n"


def _atomic_write_text(path: str, text: str) -> None:
    """Same atomic temp-file-then-replace pattern as _atomic_write, for text."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".report-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as tmp_file:
            tmp_file.write(text)
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


def write_report(
    queue_path: str,
    report_dir: str,
    run_log_path: Optional[str] = None,
    now: Optional[datetime.datetime] = None,
) -> str:
    """Render and atomically write a dated Markdown report; return its path.

    Reads only the queue file and the run log -- no model, no network call.
    Always produces a report, even when the queue is missing/malformed or
    the log is empty/missing/partly corrupt, so this remains useful after a
    crash or when nothing has ever succeeded. Writing twice for the same
    calendar day overwrites that day's file with the latest snapshot
    (deterministic given the same underlying data, not an accumulating
    history -- the run log is the history).
    """
    if now is None:
        now = _utcnow()
    if run_log_path is None:
        run_log_path = queue_path + ".log.jsonl"

    queue_data = None
    queue_error = None
    try:
        queue_data = _read_and_validate(queue_path)
    except FileNotFoundError:
        queue_error = f"queue file does not exist yet: {queue_path}"
    except (json.JSONDecodeError, MalformedQueueError) as exc:
        queue_error = str(exc)

    log_events, malformed_log_line_count = _load_run_log_events(run_log_path)

    report_text = render_report(
        queue_data=queue_data,
        queue_error=queue_error,
        log_events=log_events,
        generated_at=now.isoformat(),
        malformed_log_line_count=malformed_log_line_count,
    )

    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, now.strftime("%Y-%m-%d") + ".md")
    _atomic_write_text(report_path, report_text)
    return report_path


def _wait_for_sentinel(sentinel_path: str) -> None:
    while not os.path.exists(sentinel_path):
        time.sleep(0.001)


def main(argv=None) -> int:
    """CLI entry point.

    Subcommands: `claim <queue_path>`, `complete <queue_path> <task_id>
    [--owner-pid PID]`, `fail <queue_path> <task_id> [--owner-pid PID]`,
    `reap <queue_path> [--stale-threshold SECONDS]`.

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

    reap_parser = sub.add_parser("reap")
    reap_parser.add_argument("queue_path")
    reap_parser.add_argument(
        "--stale-threshold",
        type=int,
        default=DEFAULT_STALE_THRESHOLD_SECONDS,
        dest="stale_threshold_seconds",
    )
    reap_parser.add_argument("--wait-for", dest="wait_for", default=None)

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

    if args.command == "reap":
        if args.wait_for:
            _wait_for_sentinel(args.wait_for)
        result = reap_abandoned_tasks(
            args.queue_path, stale_threshold_seconds=args.stale_threshold_seconds
        )
        print(json.dumps(result.to_json_dict()))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
