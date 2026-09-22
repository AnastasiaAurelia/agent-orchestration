#!/usr/bin/env python3
"""Diana autonomy: workspace provenance — who wrote each file, and may Diana undo it.

## The failure this exists to remove

A prior run completes valid changes. A later run times out half-way through its
own edits. A third run starts. The working tree now holds changes from three
origins and a human has to decide, by hand and by memory, which belong to what.
Any automatic cleanup at that point is a guess, and the ways of guessing wrong
are `git reset --hard`, `git checkout .`, and "restore every dirty file" -- each
of which destroys work nobody asked to lose.

So Diana does not guess. It records, before every run, enough state to place
each path into exactly one class, and it reverts ONLY the class it can prove it
created and that nobody has touched since.

## The classes

  COMMITTED_BASELINE   present in HEAD and unmodified. Diana never touches it.
  USER_PREEXISTING     dirty before this lineage began. NEVER auto-reverted.
  OWNED_UNVERIFIED     created by a run in THIS lineage, not yet verified.
  OWNED_VERIFIED       created by a run in this lineage that reached COMPLETE.
  PRESERVED_FOR_CHILD  owned-unverified, explicitly carried into a child run.
  AMBIGUOUS            anything the record cannot place. Always escalates.

`AMBIGUOUS` is not an error state -- it is the honest answer whenever two
readings are possible, and it is why this module can be trusted: the safe path
is the only one it can take, because the unsafe ones are unreachable rather than
merely discouraged.

## Why content hashes rather than git

A child run must not have to commit just to be tracked, and a repository may be
dirty for reasons that predate Diana entirely. `reconcile.snapshot` already
produces a SHA-256 per regular file, never follows a symlink and never opens a
special file (audit finding F-A6). That is the primitive: provenance is the
difference between two snapshots plus the record of who took them.

## Why a revert is proven three ways

Diana reverts a path only when ALL of these hold:
  1. the lineage record says Diana created or modified it (ownership),
  2. it is still unverified (nothing has blessed it),
  3. its CURRENT hash equals the hash Diana last wrote (nobody edited it since).
Rule 3 is the one that matters most in practice: a user who edited a
Diana-written file while the run was going has made that file theirs, and
reverting it would destroy their edit. If any rule cannot be shown, the answer
is escalation, not a best-effort restore.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "runtime"))
sys.path.insert(0, str(_HERE.parent / "unattended"))
sys.path.insert(0, str(_HERE.parent / "mutation"))
import contract as _contract  # noqa: E402
import escalation as _esc  # noqa: E402
import journal as _journal  # noqa: E402
import reconcile as _reconcile  # noqa: E402

PROVENANCE_VERSION = 1
PROVENANCE_NAME = "workspace-provenance.json"

COMMITTED_BASELINE = "committed_baseline"
USER_PREEXISTING = "user_preexisting"
OWNED_UNVERIFIED = "owned_unverified"
OWNED_VERIFIED = "owned_verified"
PRESERVED_FOR_CHILD = "preserved_for_child"
AMBIGUOUS = "ambiguous"

CLASSES = (COMMITTED_BASELINE, USER_PREEXISTING, OWNED_UNVERIFIED,
           OWNED_VERIFIED, PRESERVED_FOR_CHILD, AMBIGUOUS)

# Only this class may ever be reverted automatically. Written as a set of one so
# that widening it is a visible edit to a named constant rather than a condition
# that quietly grew.
AUTO_REVERTABLE = frozenset({OWNED_UNVERIFIED})

RECORD_KEYS = ("provenance_version", "root_run_id", "repo_root",
               "baseline", "entries")
ENTRY_KEYS = ("path", "state", "run_id", "hash_at_record", "baseline_hash")


def _dirty_paths(root: str) -> set[str]:
    """Paths git reports as changed against HEAD, including untracked."""
    status = _reconcile.git_status(root)
    if status.startswith("<git "):
        # No usable git view. Every dirty/untracked judgement below would be a
        # guess, so the caller is told rather than given one.
        raise _esc.Escalation(
            _esc.PROVENANCE_AMBIGUOUS,
            f"the repository's git status could not be read ({status[:80]}), so "
            "pre-existing changes cannot be told apart from Diana's own")
    out: set[str] = set()
    for line in status.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        # Renames are reported as "old -> new"; both sides are dirty.
        if " -> " in path:
            before, after = path.split(" -> ", 1)
            out.add(before.strip()); out.add(after.strip())
        else:
            out.add(path)
    return out


def begin(root_run_id: str, repo_root: str) -> dict:
    """Record the workspace as it was BEFORE this lineage touched anything.

    Everything dirty at this instant is the user's, by definition and forever:
    no later run in this lineage can reclassify it, because Diana was not
    running when it changed.
    """
    repo_root = str(Path(repo_root).resolve())
    files = _reconcile.snapshot(repo_root)
    dirty = _dirty_paths(repo_root)
    entries = []
    for path in sorted(dirty):
        entries.append({
            "path": path,
            "state": USER_PREEXISTING,
            "run_id": None,
            "hash_at_record": files.get(path),
            "baseline_hash": files.get(path),
        })
    record = {
        "provenance_version": PROVENANCE_VERSION,
        "root_run_id": str(root_run_id),
        "repo_root": repo_root,
        "baseline": files,
        "entries": entries,
    }
    validate(record)
    return record


def validate(record: object) -> None:
    if not isinstance(record, dict):
        raise _esc.Escalation(_esc.PROVENANCE_AMBIGUOUS, "provenance record is not an object")
    missing = sorted(set(RECORD_KEYS) - set(record))
    extra = sorted(set(record) - set(RECORD_KEYS))
    if missing or extra:
        raise _esc.Escalation(
            _esc.PROVENANCE_AMBIGUOUS,
            f"provenance fields invalid: missing={missing} unexpected={extra}")
    if record["provenance_version"] != PROVENANCE_VERSION:
        raise _esc.Escalation(
            _esc.PROVENANCE_AMBIGUOUS,
            f"provenance_version {record['provenance_version']!r} != {PROVENANCE_VERSION}")
    if not isinstance(record["baseline"], dict):
        raise _esc.Escalation(_esc.PROVENANCE_AMBIGUOUS, "baseline must be an object")
    if not isinstance(record["entries"], list):
        raise _esc.Escalation(_esc.PROVENANCE_AMBIGUOUS, "entries must be a list")
    seen: set[str] = set()
    for index, entry in enumerate(record["entries"]):
        if not isinstance(entry, dict) or set(entry) != set(ENTRY_KEYS):
            raise _esc.Escalation(
                _esc.PROVENANCE_AMBIGUOUS,
                f"entries[{index}] fields invalid: "
                f"missing={sorted(set(ENTRY_KEYS) - set(entry or ()))} "
                f"unexpected={sorted(set(entry or ()) - set(ENTRY_KEYS))}")
        if entry["state"] not in CLASSES:
            raise _esc.Escalation(
                _esc.PROVENANCE_AMBIGUOUS,
                f"entries[{index}].state {entry['state']!r} is not one of {list(CLASSES)}")
        if not isinstance(entry["path"], str) or not entry["path"]:
            raise _esc.Escalation(
                _esc.PROVENANCE_AMBIGUOUS, f"entries[{index}].path must be a non-empty string")
        if entry["path"] in seen:
            raise _esc.Escalation(
                _esc.PROVENANCE_AMBIGUOUS,
                f"path {entry['path']!r} appears twice; its provenance is ambiguous")
        seen.add(entry["path"])


def digest(record: dict) -> str:
    validate(record)
    return _contract.digest(record)


def write(directory, record: dict) -> dict:
    validate(record)
    path = Path(directory) / PROVENANCE_NAME
    payload = {"digest": _contract.digest(record), "record": record}
    _journal.atomic_write(path, json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"))
    return record


def read(directory) -> dict:
    path = Path(directory) / PROVENANCE_NAME
    try:
        payload = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _esc.Escalation(
            _esc.PROVENANCE_AMBIGUOUS, f"provenance record unreadable: {exc}") from None
    if not isinstance(payload, dict) or set(payload) != {"digest", "record"}:
        raise _esc.Escalation(_esc.PROVENANCE_AMBIGUOUS, "provenance envelope is malformed")
    record = payload["record"]
    validate(record)
    if _contract.digest(record) != payload["digest"]:
        raise _esc.Escalation(
            _esc.PROVENANCE_AMBIGUOUS,
            "the provenance record does not hash to its own digest; it was edited, and "
            "nothing may be reverted on the strength of it")
    return record


def _entry(record: dict, path: str):
    for entry in record["entries"]:
        if entry["path"] == path:
            return entry
    return None


def observe_run(record: dict, *, run_id: str, reconciliation: dict,
                repo_root: str | None = None) -> dict:
    """Fold one finished run's reconciliation into the provenance record.

    A path this run touched becomes OWNED_UNVERIFIED and carries the hash Diana
    last saw -- which is what a later revert is checked against. A path that was
    already the USER's stays the user's: a run writing over a pre-existing dirty
    file does not transfer ownership, it makes the situation AMBIGUOUS, and
    ambiguous is the one state that can never be auto-reverted.
    """
    validate(record)
    root = str(Path(repo_root or record["repo_root"]).resolve())
    current = _reconcile.snapshot(root)
    touched = sorted(set(reconciliation.get("paths_touched") or ()))
    updated = json.loads(json.dumps(record))
    for path in touched:
        entry = _entry(updated, path)
        if entry is None:
            updated["entries"].append({
                "path": path, "state": OWNED_UNVERIFIED, "run_id": str(run_id),
                "hash_at_record": current.get(path),
                "baseline_hash": record["baseline"].get(path),
            })
            continue
        if entry["state"] in (USER_PREEXISTING, OWNED_VERIFIED):
            # Diana wrote over something it must never undo. Record the
            # collision honestly; do not take ownership of it.
            entry["state"] = AMBIGUOUS
            entry["hash_at_record"] = current.get(path)
            continue
        entry["state"] = OWNED_UNVERIFIED
        entry["run_id"] = str(run_id)
        entry["hash_at_record"] = current.get(path)
    updated["entries"].sort(key=lambda e: e["path"])
    validate(updated)
    return updated


def mark_verified(record: dict, paths) -> dict:
    """Promote owned-unverified paths to verified. Only Diana's own verification
    calls this, and only after the required verification actually succeeded."""
    validate(record)
    updated = json.loads(json.dumps(record))
    for path in sorted(set(paths or ())):
        entry = _entry(updated, path)
        if entry is None:
            continue
        if entry["state"] in (OWNED_UNVERIFIED, PRESERVED_FOR_CHILD):
            entry["state"] = OWNED_VERIFIED
    validate(updated)
    return updated


def mark_preserved(record: dict, paths) -> dict:
    """Carry owned-unverified changes into a child run, still UNVERIFIED.

    Preserving is a statement about provenance, never about correctness: a
    preserved change is recorded as not yet complete, and only the required
    verification can move it to OWNED_VERIFIED.
    """
    validate(record)
    updated = json.loads(json.dumps(record))
    for path in sorted(set(paths or ())):
        entry = _entry(updated, path)
        if entry is None:
            raise _esc.Escalation(
                _esc.PRESERVE_CONFLICT,
                f"{path!r} was asked to be preserved but Diana has no provenance for it")
        if entry["state"] not in (OWNED_UNVERIFIED, PRESERVED_FOR_CHILD):
            raise _esc.Escalation(
                _esc.PRESERVE_CONFLICT,
                f"{path!r} is {entry['state']}, which Diana does not own; it cannot be "
                "preserved as though this lineage had produced it")
        entry["state"] = PRESERVED_FOR_CHILD
    validate(updated)
    return updated


def classify(record: dict, path: str, *, repo_root: str | None = None) -> str:
    """The current class of one path, re-derived against the live tree."""
    validate(record)
    root = str(Path(repo_root or record["repo_root"]).resolve())
    current = _reconcile.snapshot(root)
    entry = _entry(record, path)
    if entry is None:
        if path in record["baseline"] and current.get(path) == record["baseline"][path]:
            return COMMITTED_BASELINE
        if path not in record["baseline"] and path not in current:
            return COMMITTED_BASELINE
        # Changed, and no record says who changed it.
        return AMBIGUOUS
    if entry["state"] == AMBIGUOUS:
        return AMBIGUOUS
    if entry["state"] in (OWNED_UNVERIFIED, PRESERVED_FOR_CHILD):
        if current.get(path) != entry["hash_at_record"]:
            # Rule 3: somebody changed it after Diana last wrote it.
            return AMBIGUOUS
    return entry["state"]


def plan_revert(record: dict, paths, *, repo_root: str | None = None) -> dict:
    """Which of `paths` may be reverted, and why each one may not. Never acts.

    Returns {"revert": [...], "refused": [{"path", "state", "reason"}]}. A caller
    that finds anything in `refused` must escalate: this function deliberately
    does not offer a "revert what you can" mode, because a partial revert of a
    set the supervisor reasoned about as a whole is a different change from the
    one anybody approved.
    """
    validate(record)
    root = str(Path(repo_root or record["repo_root"]).resolve())
    current = _reconcile.snapshot(root)
    revert, refused = [], []
    for path in sorted(set(paths or ())):
        state = classify(record, path, repo_root=root)
        entry = _entry(record, path)
        if state not in AUTO_REVERTABLE:
            refused.append({
                "path": path, "state": state,
                "reason": {
                    USER_PREEXISTING: "it was already changed before this session began",
                    OWNED_VERIFIED: "it holds verified work that completed",
                    COMMITTED_BASELINE: "it is unmodified committed content",
                    PRESERVED_FOR_CHILD: "it is preserved work carried into a child run",
                    AMBIGUOUS: "Diana cannot prove who last changed it",
                }.get(state, "its provenance does not permit an automatic revert")})
            continue
        if current.get(path) != (entry or {}).get("hash_at_record"):
            refused.append({"path": path, "state": AMBIGUOUS,
                            "reason": "it changed after Diana last wrote it"})
            continue
        revert.append(path)
    return {"revert": revert, "refused": refused}


def apply_revert(record: dict, paths, *, repo_root: str | None = None) -> dict:
    """Revert exactly the proven-safe set, from the recorded baseline.

    Refuses the whole operation unless every requested path is provably safe --
    there is no partial mode. Restores byte-for-byte from the baseline hash's
    content via git, and removes a file that had no baseline (Diana created it).
    """
    import subprocess

    validate(record)
    root = str(Path(repo_root or record["repo_root"]).resolve())
    plan = plan_revert(record, paths, repo_root=root)
    if plan["refused"]:
        worst = plan["refused"][0]
        raise _esc.Escalation(
            _esc.REVERT_WOULD_LOSE_WORK,
            f"{len(plan['refused'])} path(s) cannot be safely reverted, starting with "
            f"{worst['path']!r}: {worst['reason']}. Nothing was reverted",
            requested={"unsafe_paths": [r["path"] for r in plan["refused"]]})
    reverted, removed = [], []
    for path in plan["revert"]:
        target = Path(root) / path
        if path not in record["baseline"]:
            # Diana created it and it is unverified: removing it restores the
            # workspace, and nothing that predates the lineage is touched.
            try:
                target.unlink()
            except OSError as exc:
                raise _esc.Escalation(
                    _esc.REVERT_WOULD_LOSE_WORK,
                    f"{path!r} could not be removed ({exc}); the workspace is not in the "
                    "state Diana recorded and nothing further was changed") from None
            removed.append(path)
            continue
        proc = subprocess.run(["git", "-C", root, "checkout", "--", path],
                              capture_output=True, text=True, timeout=60, check=False)
        if proc.returncode != 0:
            raise _esc.Escalation(
                _esc.REVERT_WOULD_LOSE_WORK,
                f"{path!r} could not be restored from the repository "
                f"({proc.stderr[:120]}); nothing further was changed")
        reverted.append(path)
    updated = json.loads(json.dumps(record))
    updated["entries"] = [e for e in updated["entries"]
                          if e["path"] not in set(reverted) | set(removed)]
    validate(updated)
    return {"record": updated, "reverted": reverted, "removed": removed}
