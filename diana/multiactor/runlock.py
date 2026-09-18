#!/usr/bin/env python3
"""Diana M6: one live executor per run (M6-D11, audit finding M6-A4).

M6-D11 promises that at most one actor is live at any instant in a run. The
independent attack pass falsified that promise: a SECOND executor process
started on the same run directory while the first was still inside its turn,
found `TURN_ACTIVE`, discharged the obligation, and carried the run on -- while
the first process was alive and could still write to the target.

M5-D15's quiescence proof does not close this, and that is not a bug in it.
Phase 0 F16 established that the ownership stamp marks DESCENDANTS and never the
setter, which is exactly the semantics M5 wanted: Diana is not its own subject.
So a peer executor is invisible to `owned_pids` by design, and asking quiescence
to cover it would mean making Diana stamp itself -- which would then make one
executor's quiescence proof terminate its peer, the collision Phase 0 F12
measured.

The right control is therefore not a wider quiescence proof but an exclusion
lock, and the right lock is the kernel's:

* `flock(LOCK_EX | LOCK_NB)` is atomic, so two processes cannot both believe they
  acquired it -- there is no read-then-write window to lose;
* the kernel releases it when the holding process dies, however it dies, so a
  SIGKILLed executor leaves no stale lock and no liveness heuristic is needed.
  A PID file would have needed exactly the recycling defence M5-D14 describes,
  and would still have had a window.

The holder's identity is also written alongside, as readable metadata. It is for
the operator and for the run report; nothing decides with it.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "runtime"))
sys.path.insert(0, str(_HERE.parent / "unattended"))
import blocking  # noqa: E402
import ownership as _ownership  # noqa: E402

LOCK_NAME = "executor.lock"


class RunLock:
    """An exclusive, kernel-held claim on one run directory."""

    def __init__(self, run_directory) -> None:
        self.path = Path(run_directory) / LOCK_NAME
        self._fd: int | None = None

    def acquire(self) -> dict:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            held_by = ""
            try:
                held_by = os.read(fd, 4096).decode("utf-8", "replace")
            except OSError:
                pass
            os.close(fd)
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise blocking.Blocked(
                    blocking.ACTOR_HANDOFF_REFUSED,
                    "another executor process is live on this run; actors are strictly "
                    f"sequential (M6-D11). Holder: {held_by.strip()[:200]}") from None
            raise blocking.Blocked(
                blocking.ACTOR_HANDOFF_REFUSED, f"could not lock the run: {exc}") from None
        self._fd = fd
        holder = {"pid": os.getpid(), "start_time": _ownership.start_time(os.getpid())}
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps(holder, sort_keys=True).encode("utf-8"))
        os.fsync(fd)
        return holder

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
        self.holder = self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False
