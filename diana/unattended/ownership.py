#!/usr/bin/env python3
"""Diana M5: per-PID run ownership and quiescence (M5-D14, M5-D15).

## The problem this exists for

Phase 0 F6 measured it: Hermes spawns commands with `start_new_session=True`
(`process_registry.py:946`), so a command tree gets its own session and process
group. Diana was SIGKILLed four seconds into an allowed command; the tree was
reparented to the user's systemd, SURVIVED, and finished its work thirty seconds
later. M4-D10 refused `background: true` on the reasoning that a bound on the
tool call is not a bound on the work it started -- and the same turns out to be
true of FOREGROUND commands the moment Diana dies. M4's real bound was Diana's
liveness, which is precisely the assumption M5 removes.

Phase 0 F10 measured the other half: a foreground `terminal` call returns only
`{output, exit_code, error}`, the process registry stays empty for foreground
calls, and the policy layer sees only the tool name and arguments. So there is
no record, on either side, naming the tree a previous run started.

## The mechanism, and why it is per-PID

Phase 0 F16: with `DIANA_RUN_ID` stamped into the environment before the turn,
every process in the spawned tree inherits it and carries it readably at
`/proc/<pid>/environ`. That gives Diana a marker it OWNS, that survives its own
death, and that identifies descendants individually.

Two properties of `/proc/<pid>/environ` make it the right marker:

  * it reflects the environment at EXEC time, so a value set through `os.environ`
    after start marks descendants only and never the setter; and
  * a process cannot alter its OWN `/proc/<pid>/environ` after exec, so it cannot
    disown itself in place by unsetting the variable.

**A limitation, measured and not papered over.** A process can still `exec` a
CHILD with a scrubbed environment -- `bash -c 'unset DIANA_RUN_ID; sleep 30'`
execs `sleep` with the variable already removed, and that `sleep` carries no
stamp and is therefore not ownable. So the stamp bounds processes the run
spawned NORMALLY; it is not a containment boundary against a command written to
escape one. This sits inside M4's existing carried assumptions -- the command
allowlist is exact-match and "only as good as what Diana declares", and it "is
not a sandbox" -- and it is stated here rather than left implied, because the
opposite claim would make quiescence look stronger than it is.

A pid alone is not an identity -- the kernel recycles them -- so ownership is
`stamp AND start-time`, the same recycling defence Hermes's own
`_host_pid_is_ours` uses.

## Why a process-group kill is prohibited

M5-D14 forbids it, and the reason is recorded in M4's audit section 4: a cleanup
that killed a process group also killed the supervising session, because an MCP
server shared that group. Per F16 the command tree sits in its OWN pgid, so
per-PID action is always sufficient; a group kill buys nothing and can reach
processes that merely share a group. Name-pattern matching is prohibited for the
same class of reason: `pgrep -f python` is a guess, not a proof.
"""

from __future__ import annotations

import errno
import os
import signal
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "runtime"))
import blocking  # noqa: E402

STAMP_VAR = "DIANA_RUN_ID"
PROC = Path("/proc")

# Between SIGTERM and SIGKILL. Long enough for a test runner to flush and exit,
# short enough that an unattended run is not held hostage by one process.
DEFAULT_TERM_GRACE_SECONDS = 10.0
_POLL_INTERVAL = 0.1


def available() -> bool:
    """True when per-PID ownership can be proven at all.

    M5's carried assumption is explicit that this mechanism is Linux-specific.
    Where `/proc` is absent the correct behavior is BLOCKED, never a weaker check
    -- so callers ask this and refuse, rather than silently degrading.
    """
    return PROC.is_dir() and (PROC / "self" / "environ").exists()


def _read_environ(pid: int) -> bytes | None:
    try:
        return (PROC / str(pid) / "environ").read_bytes()
    except (OSError, ValueError):
        # Permission denied (another user's process) or the pid vanished. Either
        # way this is NOT our process: unreadable is never treated as owned.
        return None


def start_time(pid: int) -> int | None:
    """Field 22 of /proc/<pid>/stat: start time in clock ticks since boot.

    Read with the comm field stripped by splitting on the LAST ')', because a
    process name may itself contain spaces and parentheses.
    """
    try:
        raw = (PROC / str(pid) / "stat").read_text()
    except (OSError, ValueError):
        return None
    try:
        return int(raw.rsplit(")", 1)[1].split()[19])
    except (IndexError, ValueError):
        return None


def _stat_fields(pid: int) -> tuple[int, int, int] | None:
    """(ppid, pgid, sid) -- recorded for the report, never used as proof."""
    try:
        raw = (PROC / str(pid) / "stat").read_text()
        parts = raw.rsplit(")", 1)[1].split()
        return int(parts[1]), int(parts[2]), int(parts[3])
    except (OSError, ValueError, IndexError):
        return None


def cmdline(pid: int) -> str:
    try:
        raw = (PROC / str(pid) / "cmdline").read_bytes()
    except (OSError, ValueError):
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()


def is_owned(pid: int, run_id: str, expected_start: int | None = None) -> bool:
    """Ownership is the STAMP, and -- when known -- the START TIME.

    `expected_start` closes the recycling window: between recording a pid and
    acting on it the kernel may have handed that number to a stranger, and a
    stranger that happens to carry the stamp would still have a different start
    time. Never widen this to a name or a command-line match.
    """
    environ = _read_environ(pid)
    if environ is None:
        return False
    if f"{STAMP_VAR}={run_id}".encode() not in environ.split(b"\0"):
        return False
    if expected_start is not None and start_time(pid) != expected_start:
        return False
    return True


def owned_pids(run_id: str, *, exclude_self: bool = True) -> list[dict]:
    """Every live process provably belonging to this run.

    Enumerated from /proc rather than from any list Diana kept, because the list
    Diana kept is exactly what a crash destroys -- and because Hermes never told
    Diana the pid in the first place (F10).
    """
    if not available():
        raise blocking.Blocked(
            blocking.QUIESCENCE_NOT_PROVEN,
            "/proc is unavailable, so per-PID ownership cannot be proven")
    me = os.getpid()
    found = []
    for entry in PROC.iterdir():
        name = entry.name
        if not name.isdigit():
            continue
        pid = int(name)
        if exclude_self and pid == me:
            continue
        if not is_owned(pid, run_id):
            continue
        fields = _stat_fields(pid)
        found.append({
            "pid": pid,
            "start_time": start_time(pid),
            "ppid": fields[0] if fields else None,
            "pgid": fields[1] if fields else None,
            "sid": fields[2] if fields else None,
            "cmdline": cmdline(pid)[:200],
        })
    return sorted(found, key=lambda item: item["pid"])


def proc_state(pid: int) -> str | None:
    """The single-letter state from /proc/<pid>/stat, or None if it is gone."""
    try:
        raw = (PROC / str(pid) / "stat").read_text()
        return raw.rsplit(")", 1)[1].split()[0]
    except (OSError, ValueError, IndexError):
        return None


def alive(pid: int) -> bool:
    """True only when the pid is a RUNNING process, never for a zombie.

    A zombie still answers `kill(pid, 0)` -- it holds a process-table slot until
    its parent reaps it -- but it has no address space, executes nothing, and
    cannot touch the target. Counting one as alive would make quiescence
    unreachable whenever a parent died before reaping, which is precisely the
    crash case M5 exists for. Measured: a killed-but-unreaped child reports
    state 'Z' with an unreadable (empty) environ while `kill(pid, 0)` still
    succeeds.
    """
    state = proc_state(pid)
    if state is None:
        try:
            os.kill(pid, 0)
        except OSError as exc:
            return exc.errno == errno.EPERM   # exists, but not ours to inspect
        return True
    return state != "Z"


def _signal_owned(pid: int, expected_start: int | None, run_id: str, sig: int) -> str:
    """Signal a pid only after RE-PROVING ownership immediately beforehand.

    The re-proof is the whole point: a pid proven ours a second ago may have
    exited and been recycled since, and signalling on a stale proof is how a
    cleanup kills a stranger.
    """
    if not is_owned(pid, run_id, expected_start):
        return "not-owned-at-signal-time"
    try:
        os.kill(pid, sig)
        return "signalled"
    except ProcessLookupError:
        return "already-gone"
    except PermissionError:
        return "permission-denied"
    except OSError as exc:
        return f"error:{exc.errno}"


def terminate_owned(run_id: str, *, grace_seconds: float = DEFAULT_TERM_GRACE_SECONDS) -> dict:
    """Graceful first, bounded wait, then escalate -- per owned PID only.

    Order matters and is M5-D14's: SIGTERM to every proven-owned pid, wait up to
    `grace_seconds` for them to leave, then SIGKILL only those still proven owned.
    Nothing is signalled that has not been re-proven at that instant, and no
    process group is ever signalled.
    """
    report = {"term": [], "kill": [], "grace_seconds": grace_seconds,
              "remaining": [], "quiescent": False}
    targets = owned_pids(run_id)
    for item in targets:
        report["term"].append({
            "pid": item["pid"],
            "result": _signal_owned(item["pid"], item["start_time"], run_id, signal.SIGTERM),
        })

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not owned_pids(run_id):
            break
        time.sleep(_POLL_INTERVAL)

    for item in owned_pids(run_id):
        report["kill"].append({
            "pid": item["pid"],
            "result": _signal_owned(item["pid"], item["start_time"], run_id, signal.SIGKILL),
        })
    # A short settle so the kernel reaps before the final observation; the claim
    # M5-AC-14 makes is that descendants are OBSERVABLY gone, not that a kill was
    # requested (M3-D13's discipline).
    settle = time.monotonic() + 2.0
    while time.monotonic() < settle:
        if not owned_pids(run_id):
            break
        time.sleep(_POLL_INTERVAL)

    report["remaining"] = owned_pids(run_id)
    report["quiescent"] = not report["remaining"]
    return report


def require_quiescent(run_id: str, *, grace_seconds: float = DEFAULT_TERM_GRACE_SECONDS,
                      terminate: bool = True) -> dict:
    """Prove no process of this run is alive, or raise Blocked (M5-D15).

    Reconciling a target that a previous run's command tree is still writing to
    is a reading of a moving object, so this runs BEFORE the diff. Unprovable is
    refused rather than assumed (M5-D9).
    """
    if not available():
        raise blocking.Blocked(
            blocking.QUIESCENCE_NOT_PROVEN,
            "/proc is unavailable, so quiescence cannot be proven on this platform")
    live = owned_pids(run_id)
    if not live:
        return {"quiescent": True, "observed": [], "terminated": None}
    if not terminate:
        raise blocking.Blocked(
            blocking.QUIESCENCE_NOT_PROVEN,
            f"{len(live)} process(es) of this run are still alive: "
            f"{[item['pid'] for item in live]}")
    report = terminate_owned(run_id, grace_seconds=grace_seconds)
    if not report["quiescent"]:
        raise blocking.Blocked(
            blocking.QUIESCENCE_NOT_PROVEN,
            f"{len(report['remaining'])} process(es) survived termination: "
            f"{[item['pid'] for item in report['remaining']]}")
    return {"quiescent": True, "observed": live, "terminated": report}


def stamp_environment(run_id: str, env: dict | None = None) -> None:
    """Stamp the run id so DESCENDANTS carry it (M5-D5 ordering).

    Called before the turn is armed. Per F16 this marks children only -- the
    setter's own `/proc/self/environ` still shows its exec-time environment --
    which is exactly the semantics wanted, since Diana is not its own subject.
    """
    (env if env is not None else os.environ)[STAMP_VAR] = run_id
