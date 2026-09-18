#!/usr/bin/env python3
"""Diana: the run lease -- one live executor per run (M6-ERRATA-002).

Independent-review finding R-2. M6-A4 took an exclusive `flock` in
`actors.execute`, which establishes exclusion only between callers that take it.
`unattended.execute`, `discharge_obligation` and `run_attempt` are public and
unchanged from M5, so a peer using them took no lock at all -- and was measured
reconciling a run that was durably `TURN_ACTIVE` while its owner was still alive,
writing the reconciliation record, closing the attempt and restoring retry
eligibility before refusing on an unrelated check. A refusal after those effects
is too late (M6-E2-D4).

The lease is therefore checked at the effects, not at one entry point.

## Why this probes and never acquires

Measured, before the design was chosen: a second `flock(LOCK_EX | LOCK_NB)` on a
DIFFERENT descriptor in the SAME process returns `EAGAIN`. `flock` is held per
open file description, not per process. So a guard that tried to acquire the
lease would **deadlock the legitimate owner**, which already holds it on another
descriptor. The guard opens its own descriptor, tries the lock only to learn
whether anyone holds it, and releases immediately -- which was also measured to
be harmless to the owner's lock.

## Why liveness is the lock and never the file

The lock file persists after release and after a crash. Treating its existence as
"held" would wedge every run whose executor ever died, and would need exactly the
stale-PID heuristic M5-D14 avoids. The kernel releases the lock when the holding
process dies by any means, including `SIGKILL`, so the `flock` answers liveness
and the file only answers identity.

## What this is not

It is not authority and it is not containment (M6-E2-D6). It answers one
question -- may this process act on this run right now -- and constrains nothing
about what a process does once admitted. The holder record is a same-user
readable file, so this is not a defence against a hostile same-user process,
which could already rewrite the journal (M6-E2-D8). What it closes completely is
a legitimate older entry point silently advancing a live run, which is the defect
that was actually measured.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blocking  # noqa: E402

LOCK_NAME = "executor.lock"
_PROC = Path("/proc")


def start_time(pid: int) -> int | None:
    """Field 22 of `/proc/<pid>/stat`: start ticks since boot.

    Mirrors `diana/unattended/ownership.start_time`, which is the canonical one.
    It is duplicated rather than imported because `diana/runtime/` is imported BY
    `diana/unattended/`, and importing back would make the dependency circular.
    The two are pinned equal by an acceptance assertion, at a location, so they
    cannot drift (roadmap invariant 6).
    """
    try:
        raw = (_PROC / str(pid) / "stat").read_text()
    except (OSError, ValueError):
        return None
    try:
        return int(raw.rsplit(")", 1)[1].split()[19])
    except (IndexError, ValueError):
        return None


def _self_record() -> dict:
    pid = os.getpid()
    return {"pid": pid, "start_time": start_time(pid)}


def lock_path(run_directory) -> Path:
    return Path(run_directory) / LOCK_NAME


def _read_holder(fd: int) -> dict | None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 4096).decode("utf-8", "replace").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        holder = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return holder if isinstance(holder, dict) else None


def probe(run_directory) -> dict:
    """Who, if anyone, holds this run's lease. Never acquires it.

    Returns {"held": bool, "holder": dict | None, "is_self": bool}.
    """
    path = lock_path(run_directory)
    if not path.exists():
        return {"held": False, "holder": None, "is_self": False}
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        # Unreadable lease state is not evidence of absence. Fail closed.
        raise blocking.Blocked(
            blocking.ACTOR_HANDOFF_REFUSED,
            f"the run lease at {path} could not be read") from None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise blocking.Blocked(
                    blocking.ACTOR_HANDOFF_REFUSED,
                    f"the run lease could not be probed: {exc}") from None
            holder = _read_holder(fd)
            me = _self_record()
            is_self = bool(holder
                           and holder.get("pid") == me["pid"]
                           and holder.get("start_time") == me["start_time"])
            return {"held": True, "holder": holder, "is_self": is_self}
        # Acquired, so nobody held it. Release at once: this descriptor is ours
        # alone, and closing it cannot affect an owner's separate descriptor.
        fcntl.flock(fd, fcntl.LOCK_UN)
        return {"held": False, "holder": None, "is_self": False}
    finally:
        os.close(fd)


def require_lease(run_directory) -> dict:
    """Raise Blocked unless this process may act on this run (M6-E2-D5).

    Called as the FIRST statement of every function that can advance a run, so
    the refusal precedes reconciliation, attempt closure, journal advance, retry
    eligibility and any new turn.

    A run with no lease file is M5's standalone case and proceeds unchanged,
    which is what keeps every M5 caller and every M5 test working untouched.
    """
    state = probe(run_directory)
    if not state["held"] or state["is_self"]:
        return state
    holder = state["holder"] or {}
    raise blocking.Blocked(
        blocking.ACTOR_HANDOFF_REFUSED,
        "another executor process holds this run's lease; no process may advance a run "
        f"it does not own (M6-E2-D4). Holder: pid={holder.get('pid')} "
        f"start_time={holder.get('start_time')}")


class RunLease:
    """An exclusive, kernel-held claim on one run directory."""

    def __init__(self, run_directory) -> None:
        self.path = lock_path(run_directory)
        self._fd: int | None = None
        self.holder: dict | None = None

    def acquire(self) -> dict:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            held_by = _read_holder(fd) or {}
            os.close(fd)
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise blocking.Blocked(
                    blocking.ACTOR_HANDOFF_REFUSED,
                    "another executor process is live on this run; actors are strictly "
                    f"sequential (M6-D11). Holder: pid={held_by.get('pid')} "
                    f"start_time={held_by.get('start_time')}") from None
            raise blocking.Blocked(
                blocking.ACTOR_HANDOFF_REFUSED, f"could not lock the run: {exc}") from None
        self._fd = fd
        self.holder = _self_record()
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps(self.holder, sort_keys=True).encode("utf-8"))
        os.fsync(fd)
        return self.holder

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                os.close(self._fd)
            finally:
                self._fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False
