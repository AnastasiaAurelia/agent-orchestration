#!/usr/bin/env python3
"""Diana M4: post-hoc reconciliation of what actually changed (M4-D14, M4-D15).

M1 D25 deferred reconciliation, and the reasoning was sound at the time:
prevention made detection redundant, because with two confined read tools there
was no mutation to reconcile. Once writes exist that argument expires --
prevention still stops what it sees, but it stops proving what *happened*.

## Why this is detection, reported beside prevention -- never instead of it

An allowed command is a real hole in the path policy. `write_scope` bounds the
tools; it does not bound `pytest`, which can write wherever the process can.
Reconciliation is how a run learns that something landed outside the envelope.
It cannot prevent that, and must never be presented as if it could.

## Why Diana computes both views itself

M1 established that a reconciliation control needs an authoritative ledger and
that Hermes may not fully provide one. So nothing here is supplied by Hermes: a
filesystem snapshot Diana walks, and `git status --porcelain` Diana runs. The
agent contributes no input to its own audit.

Two views rather than one because they fail differently. A hash snapshot sees
content changes anywhere under the root, including untracked files git may be
configured to ignore; git sees renames, deletions and staged state that a naive
path-by-path diff reports confusingly. Agreement between two independent views
is worth more than either alone.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runtime"))
import blocking  # noqa: E402
import read_scope as _read_scope  # noqa: E402

# Directories never walked: .git churns on every git invocation and would swamp
# the diff with Diana's own observation. It is also denied by every scope.
_SKIP_DIRS = {".git"}


def _link_record(path: Path) -> str:
    """Record a symlink BY ITS TARGET STRING, without following it (F-A6).

    Hashing `os.readlink` output means a repointed link is a detected change,
    while Diana never opens whatever it aims at.
    """
    try:
        target = os.readlink(path)
    except OSError as exc:
        return f"<unreadable-symlink: {type(exc).__name__}>"
    return "<symlink:" + hashlib.sha256(target.encode("utf-8", "surrogateescape")).hexdigest() + ">"


def snapshot(root: str) -> dict[str, str]:
    """SHA-256 of every REGULAR file under `root`. Diana's own view, not Hermes's.

    Audit finding F-A6: this used `Path.read_bytes()`, which follows symlinks. A
    symlink committed inside the target repository therefore made Diana's own
    reconciliation read a file OUTSIDE the repository -- the reconciliation
    control reaching past the boundary it exists to police -- and a link aimed at
    a fifo or device node could block the snapshot indefinitely, hanging the audit
    rather than reporting it.

    Nothing here follows a link or opens a non-regular file. Links and special
    files are recorded deterministically by what they ARE, so a change to the link
    itself is still detected while its target is never touched. `os.walk` already
    runs with `followlinks=False`; symlinked directories are additionally recorded
    and pruned explicitly rather than left silently unrepresented.
    """
    out: dict[str, str] = {}
    root = os.path.realpath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        kept: list[str] = []
        for name in sorted(dirnames):
            if name in _SKIP_DIRS:
                continue
            path = Path(dirpath, name)
            if path.is_symlink():
                # A symlinked directory: record the link, never descend into it.
                out[str(path.relative_to(root))] = _link_record(path)
                continue
            kept.append(name)
        dirnames[:] = kept

        for name in sorted(filenames):
            path = Path(dirpath, name)
            rel = str(path.relative_to(root))
            try:
                st = path.lstat()          # lstat: never resolves the final link
                if stat.S_ISLNK(st.st_mode):
                    out[rel] = _link_record(path)
                elif not stat.S_ISREG(st.st_mode):
                    # fifo, socket, device, door: recorded by type, never opened,
                    # because opening one can block forever.
                    out[rel] = f"<special:{stat.S_IFMT(st.st_mode):#o}>"
                else:
                    out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as exc:
                # An unreadable file is recorded as unreadable rather than
                # omitted: a file that silently vanishes from both snapshots
                # would reconcile as "unchanged".
                out[rel] = f"<unreadable: {type(exc).__name__}>"
    return out


def git_status(root: str) -> str:
    try:
        proc = subprocess.run(["git", "-C", root, "status", "--porcelain"],
                              capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"<git unavailable: {type(exc).__name__}: {exc}>"
    return proc.stdout if proc.returncode == 0 else f"<git failed: {proc.stderr[:200]}>"


def diff(before: dict, after: dict) -> dict:
    """Changed / created / deleted, as relative paths."""
    keys = set(before) | set(after)
    return {
        "created": sorted(k for k in keys if k not in before),
        "deleted": sorted(k for k in keys if k not in after),
        "modified": sorted(k for k in keys if k in before and k in after and before[k] != after[k]),
    }


def reconcile(*, root: str, before: dict, after: dict,
              before_git: str, after_git: str, write_scope: dict) -> dict:
    """Compare the observed diff against the envelope. Never raises; reports."""
    changes = diff(before, after)
    touched = sorted(set(changes["created"]) | set(changes["deleted"]) | set(changes["modified"]))
    outside = []
    for rel in touched:
        absolute = os.path.join(os.path.realpath(root), rel)
        allowed, why = _read_scope.decide(absolute, write_scope)
        if not allowed:
            outside.append({"path": rel, "reason": why})
    return {
        "changes": changes,
        "paths_touched": touched,
        "paths_outside_write_scope": outside,
        "git_status_before": before_git,
        "git_status_after": after_git,
        "git_status_changed": before_git != after_git,
        "within_envelope": not outside,
    }


def require_within_envelope(report: dict) -> None:
    """Raise Blocked when the observed diff left the envelope (M4-D15).

    Per M1 D37 a run whose envelope cannot be proven yields a run record and no
    deliverable, even when the work performed would have been sound.
    """
    if not report.get("within_envelope"):
        offenders = [entry["path"] for entry in report.get("paths_outside_write_scope", [])]
        raise blocking.Blocked(
            blocking.RECONCILIATION_MISMATCH,
            f"{len(offenders)} path(s) changed outside write_scope: {offenders[:10]}",
        )
