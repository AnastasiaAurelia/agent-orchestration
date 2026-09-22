#!/usr/bin/env python3
"""Diana autonomy: the lineage ledger — who descended from whom, and what is left.

One autonomous session is a TREE of runs: a root the human approved, and the
recovery children Diana started under the standing approval. The ledger is the
tree, plus every cumulative budget the standing approval bounds.

## Why it is digest-bound, not merely written down

Finding M6-A1 is the governing precedent: unauthenticated on-disk scheduling
state is on the authority path the moment anything reads it back. Everything in
this ledger is read back to decide whether the NEXT child may start -- depth,
child count, attempts, wall clock, changed files, supervisor calls. An editable
ledger would therefore be an editable budget, and a run could be granted a ninth
child by editing a number.

So the ledger uses the journal's own envelope: `{"digest": ..., "record": ...}`,
atomically written, digest re-verified on every read. A tampered ledger does not
produce a wrong budget -- it produces `lineage-corrupt` and stops.

## Why budgets are cumulative across the whole tree

The bound a human approved was on the SESSION, not on each run. Per-run budgets
would let a tree of eight runs spend eight times the approved attempts while
every individual run stayed inside its own limit -- which is the composition
failure M6-AC-5 exists to catch one level up. Every counter here is a total.

## Why the changed-file budget is a SET and not a count

Counting would let a loop that rewrites the same file forever look cheap while a
single child touching many files looked expensive. The bound a human cares
about is "how much of my repository did this session touch", which is the union
of the paths, so the union is what is stored and what is bounded.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "runtime"))
sys.path.insert(0, str(_HERE.parent / "unattended"))
import contract as _contract  # noqa: E402
import escalation as _esc  # noqa: E402
import journal as _journal  # noqa: E402
import policy as _policy  # noqa: E402

LINEAGE_VERSION = 1
LINEAGE_NAME = "lineage.json"

RECORD_KEYS = (
    "lineage_version",
    "root_run_id",
    "standing_digest",
    "runs",
    "cumulative",
    "supervisor_decisions",
    "outcome",
)

RUN_KEYS = (
    "run_id", "parent_run_id", "depth", "goal", "reason",
    "supervisor_decision_id", "state", "attempts", "changed_files",
)

CUMULATIVE_KEYS = (
    "child_runs", "attempts", "wall_clock_seconds", "changed_files", "supervisor_calls",
)


def _now(clock=None) -> str:
    return _journal._utc_now() if clock is None else clock()


def create(root_run_id: str, standing_digest: str, *, goal: str) -> dict:
    """A fresh lineage holding only the root run."""
    return {
        "lineage_version": LINEAGE_VERSION,
        "root_run_id": str(root_run_id),
        "standing_digest": str(standing_digest),
        "runs": [{
            "run_id": str(root_run_id), "parent_run_id": None, "depth": 0,
            "goal": str(goal), "reason": "root", "supervisor_decision_id": None,
            "state": "RUNNING", "attempts": 0, "changed_files": [],
        }],
        "cumulative": {"child_runs": 0, "attempts": 0, "wall_clock_seconds": 0,
                       "changed_files": [], "supervisor_calls": 0},
        "supervisor_decisions": [],
        "outcome": None,
    }


def validate(record: object) -> None:
    """Closed schema, fail-closed. Raises Escalation."""
    if not isinstance(record, dict):
        raise _esc.Escalation(_esc.LINEAGE_CORRUPT, "lineage record is not an object")
    missing = sorted(set(RECORD_KEYS) - set(record))
    extra = sorted(set(record) - set(RECORD_KEYS))
    if missing or extra:
        raise _esc.Escalation(
            _esc.LINEAGE_CORRUPT,
            f"lineage fields invalid: missing={missing} unexpected={extra}")
    if record["lineage_version"] != LINEAGE_VERSION:
        raise _esc.Escalation(
            _esc.LINEAGE_CORRUPT,
            f"lineage_version {record['lineage_version']!r} != {LINEAGE_VERSION}")
    if not isinstance(record["runs"], list) or not record["runs"]:
        raise _esc.Escalation(_esc.LINEAGE_CORRUPT, "runs must be a non-empty list")
    seen: set[str] = set()
    for index, run in enumerate(record["runs"]):
        if not isinstance(run, dict) or set(run) != set(RUN_KEYS):
            raise _esc.Escalation(
                _esc.LINEAGE_CORRUPT,
                f"runs[{index}] fields invalid: "
                f"missing={sorted(set(RUN_KEYS) - set(run or ()))} "
                f"unexpected={sorted(set(run or ()) - set(RUN_KEYS))}")
        if not isinstance(run["run_id"], str) or run["run_id"] in seen:
            raise _esc.Escalation(
                _esc.LINEAGE_CORRUPT, "run ids must be unique non-empty strings")
        seen.add(run["run_id"])
        if type(run["depth"]) is not int or run["depth"] < 0:
            raise _esc.Escalation(_esc.LINEAGE_CORRUPT, "depth must be a non-negative integer")
        if run["parent_run_id"] is not None and run["parent_run_id"] not in seen:
            # Parents are always written before children, so an unknown parent
            # means the list was reordered or an entry was removed.
            raise _esc.Escalation(
                _esc.LINEAGE_CORRUPT,
                f"runs[{index}] names parent {run['parent_run_id']!r} that does not "
                "precede it in this lineage")
    cumulative = record["cumulative"]
    if not isinstance(cumulative, dict) or set(cumulative) != set(CUMULATIVE_KEYS):
        raise _esc.Escalation(
            _esc.LINEAGE_CORRUPT,
            f"cumulative fields invalid: "
            f"missing={sorted(set(CUMULATIVE_KEYS) - set(cumulative or ()))} "
            f"unexpected={sorted(set(cumulative or ()) - set(CUMULATIVE_KEYS))}")
    if not isinstance(cumulative["changed_files"], list):
        raise _esc.Escalation(_esc.LINEAGE_CORRUPT, "cumulative.changed_files must be a list")
    for key in ("child_runs", "attempts", "wall_clock_seconds", "supervisor_calls"):
        if type(cumulative[key]) is not int or cumulative[key] < 0:
            raise _esc.Escalation(
                _esc.LINEAGE_CORRUPT, f"cumulative.{key} must be a non-negative integer")


def digest(record: dict) -> str:
    validate(record)
    return _contract.digest(record)


def write(directory, record: dict) -> dict:
    """Persist atomically, in the journal's own envelope."""
    validate(record)
    path = Path(directory) / LINEAGE_NAME
    payload = {"digest": _contract.digest(record), "record": record}
    _journal.atomic_write(path, json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"))
    return record


def read(directory) -> dict:
    """Read and RE-VERIFY. A ledger that does not hash to its own digest is dead."""
    path = Path(directory) / LINEAGE_NAME
    try:
        payload = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _esc.Escalation(
            _esc.LINEAGE_CORRUPT, f"lineage ledger unreadable: {exc}") from None
    if not isinstance(payload, dict) or set(payload) != {"digest", "record"}:
        raise _esc.Escalation(_esc.LINEAGE_CORRUPT, "lineage envelope is malformed")
    record = payload["record"]
    validate(record)
    actual = _contract.digest(record)
    if actual != payload["digest"]:
        raise _esc.Escalation(
            _esc.LINEAGE_CORRUPT,
            f"the lineage ledger hashes to {actual}, not the {payload['digest']} it "
            "carries; it was edited and no budget can be read from it")
    return record


# --- budgets ---------------------------------------------------------------

def remaining(record: dict, autonomy_policy: dict) -> dict:
    """What is left of every cumulative bound. Never negative."""
    validate(record)
    _policy.validate(autonomy_policy)
    limits = autonomy_policy["limits"]
    used = record["cumulative"]
    return {
        "child_runs": max(0, limits["max_child_runs"] - used["child_runs"]),
        "child_depth": limits["max_child_depth"],
        "attempts": max(0, limits["max_total_attempts"] - used["attempts"]),
        "wall_clock_seconds": max(
            0, limits["max_wall_clock_seconds"] - used["wall_clock_seconds"]),
        "changed_files": max(0, limits["max_changed_files"] - len(used["changed_files"])),
        "supervisor_calls": max(
            0, limits["max_supervisor_calls"] - used["supervisor_calls"]),
    }


def prove_budget_for_child(record: dict, autonomy_policy: dict, *, depth: int) -> dict:
    """Every cumulative bound still permits one more child. Raises Escalation.

    Checked BEFORE the child is created, because a bound discovered afterwards
    is a bound that was exceeded.
    """
    left = remaining(record, autonomy_policy)
    limits = autonomy_policy["limits"]
    if depth > limits["max_child_depth"]:
        raise _esc.Escalation(
            _esc.CHILD_DEPTH_EXCEEDED,
            f"a child at depth {depth} would exceed the approved maximum depth "
            f"{limits['max_child_depth']}",
            requested={"increase_limit": "max_child_depth",
                       "current_value": limits["max_child_depth"], "needed": depth})
    for key, code, label, limit_key in (
        ("child_runs", _esc.CHILD_BUDGET_EXHAUSTED, "child runs", "max_child_runs"),
        ("attempts", _esc.ATTEMPT_BUDGET_EXHAUSTED, "attempts", "max_total_attempts"),
        ("wall_clock_seconds", _esc.WALL_CLOCK_EXHAUSTED, "wall-clock seconds",
         "max_wall_clock_seconds"),
        ("changed_files", _esc.CHANGED_FILE_BUDGET_EXHAUSTED, "changed files",
         "max_changed_files"),
    ):
        if left[key] < 1:
            used = (len(record["cumulative"]["changed_files"]) if key == "changed_files"
                    else record["cumulative"][key])
            raise _esc.Escalation(
                code,
                f"the standing approval's {label} budget is exhausted "
                f"({used} of {limits[limit_key]} used)",
                # A budget escalation HAS a precise remedy, and saying "no
                # additional authority would help" about one is false: a larger
                # budget is exactly what would help. Naming the limit and its
                # current value is what lets a human re-propose without guessing.
                requested={"increase_limit": limit_key,
                           "current_value": limits[limit_key], "used": used})
    return left


def prove_supervisor_call(record: dict, autonomy_policy: dict) -> None:
    """One more supervisor call is still within budget."""
    if remaining(record, autonomy_policy)["supervisor_calls"] < 1:
        limit = autonomy_policy["limits"]["max_supervisor_calls"]
        used = record["cumulative"]["supervisor_calls"]
        raise _esc.Escalation(
            _esc.SUPERVISOR_CALL_BUDGET_EXHAUSTED,
            f"the standing approval's supervisor-call budget is exhausted "
            f"({used} of {limit} used)",
            requested={"increase_limit": "max_supervisor_calls",
                       "current_value": limit, "used": used})


# --- mutation --------------------------------------------------------------

def add_child(record: dict, *, run_id: str, parent_run_id: str, goal: str,
              reason: str, supervisor_decision_id, depth: int) -> dict:
    """Record a child Diana has decided to start. Copy-on-write."""
    updated = json.loads(json.dumps(record))
    updated["runs"].append({
        "run_id": str(run_id), "parent_run_id": str(parent_run_id), "depth": int(depth),
        "goal": str(goal), "reason": str(reason),
        "supervisor_decision_id": supervisor_decision_id,
        "state": "RUNNING", "attempts": 0, "changed_files": [],
    })
    updated["cumulative"]["child_runs"] += 1
    validate(updated)
    return updated


def settle_run(record: dict, run_id: str, *, state: str, attempts: int,
               changed_files, wall_clock_seconds: int) -> dict:
    """Fold one finished run's consumption into the cumulative totals."""
    updated = json.loads(json.dumps(record))
    for run in updated["runs"]:
        if run["run_id"] == run_id:
            run["state"] = str(state)
            run["attempts"] = int(attempts)
            run["changed_files"] = sorted(set(str(p) for p in changed_files or ()))
            break
    else:
        raise _esc.Escalation(
            _esc.LINEAGE_CORRUPT, f"run {run_id!r} is not part of this lineage")
    updated["cumulative"]["attempts"] += int(attempts)
    updated["cumulative"]["wall_clock_seconds"] += int(wall_clock_seconds)
    updated["cumulative"]["changed_files"] = sorted(
        set(updated["cumulative"]["changed_files"]) | set(str(p) for p in changed_files or ()))
    validate(updated)
    return updated


def record_supervisor_call(record: dict, decision_record: dict) -> dict:
    """Count a supervisor call and keep its audit reference."""
    updated = json.loads(json.dumps(record))
    updated["cumulative"]["supervisor_calls"] += 1
    updated["supervisor_decisions"].append(decision_record)
    validate(updated)
    return updated


def finish(record: dict, outcome: dict) -> dict:
    updated = json.loads(json.dumps(record))
    updated["outcome"] = outcome
    validate(updated)
    return updated


def depth_of(record: dict, run_id: str) -> int:
    for run in record["runs"]:
        if run["run_id"] == run_id:
            return int(run["depth"])
    raise _esc.Escalation(
        _esc.LINEAGE_CORRUPT, f"run {run_id!r} is not part of this lineage")
