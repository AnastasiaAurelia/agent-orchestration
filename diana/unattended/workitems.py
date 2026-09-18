#!/usr/bin/env python3
"""Diana M5 ERRATA-001: the minimal work-item and dependency model.

Implements M5-E1-D1..D9 and M5-E1-D15. Orchestration only -- this module grants
no capability, names no tool, and has no channel Hermes can reach.

## Why the graph is Diana's and not the model's

Without an explicit graph, "may this unit proceed when that one failed?" is
answered by whatever the turn driver happens to do, which means by the model.
M1 D14 denies the agent any say over its own constraints, and ordering IS a
constraint: an item that runs after its prerequisite failed is an item running
outside the conditions its approval assumed.

So eligibility here is a pure function of Diana-owned durable state:
`PENDING` plus every declared dependency `COMPLETE`. There is no override, no
"force" flag, and no way to ask.

## Why blocking propagates transitively

If B depends on A and C depends on B, then A failing makes C's prerequisites
unreachable just as surely as it makes B's. Marking only the direct dependent
would leave C `PENDING` forever -- eligible never, but also never explained.
Walking the closure states the consequence once, durably, with a reason.

## Why the shape is fixed at approval

M5-E1-D1. A run that could grow items after approval would be a run whose scope
the approver never saw, which is the same defect as an envelope that could widen
mid-run. The item set is validated once, digest-bound, and thereafter read-only.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "runtime"))
import blocking  # noqa: E402
import contract as _contract  # noqa: E402

DOCUMENT_VERSION = 1
DOCUMENT_NAME = "work-items.json"

PENDING = "PENDING"
RUNNING = "RUNNING"
COMPLETE = "COMPLETE"
BLOCKED = "BLOCKED"
ITEM_STATES = frozenset({PENDING, RUNNING, COMPLETE, BLOCKED})
ITEM_TERMINAL = frozenset({COMPLETE, BLOCKED})

DOCUMENT_KEYS = ("document_version", "run_id", "items")
ITEM_KEYS = ("id", "task", "depends_on")


def build(*, run_id: str, items) -> dict:
    """Build and validate the item document. Raises Blocked on any graph defect."""
    document = {
        "document_version": DOCUMENT_VERSION,
        "run_id": run_id,
        "items": [
            {"id": str(item["id"]), "task": str(item.get("task", "")),
             "depends_on": [str(d) for d in (item.get("depends_on") or [])]}
            for item in items
        ],
    }
    validate(document)
    return document


def validate(document: object) -> None:
    """Closed schema plus full graph validation, all fail-closed (M5-E1-D5)."""
    if not isinstance(document, dict):
        raise blocking.Blocked(blocking.WORK_ITEMS_MALFORMED, "document is not an object")
    missing = sorted(set(DOCUMENT_KEYS) - set(document))
    extra = sorted(set(document) - set(DOCUMENT_KEYS))
    if missing or extra:
        raise blocking.Blocked(
            blocking.WORK_ITEMS_MALFORMED, f"missing={missing} unexpected={extra}")
    if document["document_version"] != DOCUMENT_VERSION:
        raise blocking.Blocked(
            blocking.WORK_ITEMS_MALFORMED,
            f"document_version {document['document_version']!r} != {DOCUMENT_VERSION}")
    if not isinstance(document["run_id"], str) or not document["run_id"]:
        raise blocking.Blocked(blocking.WORK_ITEMS_MALFORMED, "run_id must be a non-empty string")

    items = document["items"]
    if not isinstance(items, list) or not items:
        raise blocking.Blocked(
            blocking.WORK_ITEMS_MALFORMED, "items must be a non-empty list")

    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != set(ITEM_KEYS):
            raise blocking.Blocked(
                blocking.WORK_ITEMS_MALFORMED, f"item shape invalid: {item!r}")
        item_id = item["id"]
        if not isinstance(item_id, str) or not item_id:
            raise blocking.Blocked(
                blocking.WORK_ITEMS_MALFORMED, "item id must be a non-empty string")
        if item_id in seen:
            # Duplicate ids make identity ambiguous, and an ambiguous identity
            # cannot carry durable status (M5-E1-D3).
            raise blocking.Blocked(
                blocking.WORK_ITEM_DUPLICATE_ID, f"duplicate item id {item_id!r}")
        seen.add(item_id)
        if not isinstance(item["task"], str):
            raise blocking.Blocked(blocking.WORK_ITEMS_MALFORMED, "item task must be a string")
        deps = item["depends_on"]
        if not isinstance(deps, list) or not all(isinstance(d, str) and d for d in deps):
            raise blocking.Blocked(
                blocking.WORK_ITEMS_MALFORMED,
                f"depends_on must be a list of non-empty strings for {item_id!r}")

    for item in items:
        for dep in item["depends_on"]:
            if dep == item["id"]:
                raise blocking.Blocked(
                    blocking.WORK_ITEM_SELF_DEPENDENCY, f"{item['id']!r} depends on itself")
            if dep not in seen:
                raise blocking.Blocked(
                    blocking.WORK_ITEM_UNKNOWN_DEPENDENCY,
                    f"{item['id']!r} depends on unknown item {dep!r}")

    cycle = find_cycle(items)
    if cycle:
        raise blocking.Blocked(
            blocking.WORK_ITEM_CYCLE, "dependency cycle: " + " -> ".join(cycle))


def find_cycle(items) -> list[str] | None:
    """Return one cycle as a path, or None. Iterative DFS with an explicit stack.

    Iterative rather than recursive so a deep or hostile graph cannot exhaust the
    interpreter stack and turn a graph defect into a crash.
    """
    deps = {item["id"]: list(item["depends_on"]) for item in items}
    UNVISITED, ACTIVE, DONE = 0, 1, 2
    colour = {node: UNVISITED for node in deps}
    for root in deps:
        if colour[root] != UNVISITED:
            continue
        stack = [(root, iter(deps[root]))]
        path = [root]
        colour[root] = ACTIVE
        while stack:
            node, children = stack[-1]
            advanced = False
            for child in children:
                if colour.get(child, DONE) == ACTIVE:
                    return path[path.index(child):] + [child]
                if colour.get(child, DONE) == UNVISITED:
                    colour[child] = ACTIVE
                    path.append(child)
                    stack.append((child, iter(deps[child])))
                    advanced = True
                    break
            if not advanced:
                colour[node] = DONE
                stack.pop()
                path.pop()
    return None


def digest(document: dict) -> str:
    return _contract.digest(document)


def ids(document: dict) -> list[str]:
    return [item["id"] for item in document["items"]]


def by_id(document: dict) -> dict:
    return {item["id"]: item for item in document["items"]}


def initial_status(document: dict) -> dict:
    """Every item starts PENDING with no attempts. Diana-owned from the start."""
    return {item["id"]: {"status": PENDING, "attempts": 0, "reason_code": None,
                         "detail": "", "reconciliation_file": None}
            for item in document["items"]}


# --- eligibility and propagation (M5-E1-D6, M5-E1-D7) --------------------

def is_eligible(item_id: str, document: dict, status: dict) -> bool:
    """PENDING, and every declared dependency COMPLETE. No other route exists."""
    entry = status.get(item_id)
    if not entry or entry["status"] != PENDING:
        return False
    for dep in by_id(document)[item_id]["depends_on"]:
        if status.get(dep, {}).get("status") != COMPLETE:
            return False
    return True


def eligible_items(document: dict, status: dict) -> list[str]:
    """Declaration order, so the same graph always runs in the same sequence."""
    return [i for i in ids(document) if is_eligible(i, document, status)]


def dependents_closure(item_id: str, document: dict) -> list[str]:
    """Every item that depends on `item_id` directly or transitively."""
    reverse: dict[str, list[str]] = {i: [] for i in ids(document)}
    for item in document["items"]:
        for dep in item["depends_on"]:
            reverse[dep].append(item["id"])
    out, stack, seen = [], list(reverse[item_id]), set()
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        out.append(node)
        stack.extend(reverse[node])
    return sorted(out)


def propagate_blocked(item_id: str, document: dict, status: dict) -> list[str]:
    """Mark the closure BLOCKED with `dependency-blocked`. Returns those changed.

    Terminal item states are never overwritten (M5-E1-D15): an item that already
    finished keeps the outcome it earned, and one already blocked keeps its
    original, more specific reason.
    """
    changed = []
    for dependent in dependents_closure(item_id, document):
        entry = status.get(dependent)
        if not entry or entry["status"] in ITEM_TERMINAL:
            continue
        entry["status"] = BLOCKED
        entry["reason_code"] = blocking.DEPENDENCY_BLOCKED
        entry["detail"] = f"depends on blocked item {item_id!r}"
        changed.append(dependent)
    return changed


def all_complete(document: dict, status: dict) -> bool:
    return all(status.get(i, {}).get("status") == COMPLETE for i in ids(document))


def first_blocked(document: dict, status: dict) -> dict | None:
    for i in ids(document):
        entry = status.get(i) or {}
        if entry.get("status") == BLOCKED:
            return {"id": i, **entry}
    return None
