#!/usr/bin/env python3
"""Diana supervisors: the closed recommendation schema.

A supervisor is a RECOVERY PLANNER and nothing else. It observes a failed run
and says what bounded thing should happen next. It is advisory: Diana decides
whether the recommendation is executable, and Diana derives the contract.

## Why the supervisor can never return a contract

The one thing a planner must not be able to do is hand back the object that
grants authority. If it could, "validate the recommendation" would mean
"validate the grant a model wrote", and every control downstream would be
arguing with the model's own description of what it is allowed to do. So the
schema has no contract, no envelope, no tool list, no risk and no depth. It
carries a decision, a reason, a goal in words, and REQUESTS for scope and
commands -- requests which Diana then proves are subsets of what a human already
approved, or refuses.

## Why the schema is closed in both directions

An unknown decision fails closed because an unrecognised instruction is not a
safe default. An unknown FIELD fails closed for the same reason one level down:
a recommendation carrying `{"force": true}` is a recommendation whose author
believed it meant something, and silently dropping it would execute a plan
nobody validated. Roadmap invariant 4, applied to model output.

## Why `human_required` is not the escalation mechanism

A supervisor may set it, and Diana honours it -- but Diana escalates on its own
proofs regardless. The flag is a supervisor's opinion that a human is needed;
its absence is never evidence that one is not.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "autonomy"))
import escalation as _esc  # noqa: E402

SCHEMA_VERSION = 1

# The closed decision set. Adding one is a deliberate edit here plus a handler
# in the loop; a decision with no handler is refused rather than defaulted.
RETRY_SAME = "RETRY_SAME"
RETRY_NARROWER = "RETRY_NARROWER"
SPLIT_TASK = "SPLIT_TASK"
PRESERVE_AND_RETRY = "PRESERVE_AND_RETRY"
REVERT_AND_RETRY = "REVERT_AND_RETRY"
REQUEST_MORE_EVIDENCE = "REQUEST_MORE_EVIDENCE"
COMPLETE = "COMPLETE"
ESCALATE_HUMAN = "ESCALATE_HUMAN"

DECISIONS = (RETRY_SAME, RETRY_NARROWER, SPLIT_TASK, PRESERVE_AND_RETRY,
             REVERT_AND_RETRY, REQUEST_MORE_EVIDENCE, COMPLETE, ESCALATE_HUMAN)

# Decisions that cause Diana to attempt a child run at all. Everything else
# terminates the lineage one way or another.
CONTINUING = frozenset({RETRY_SAME, RETRY_NARROWER, SPLIT_TASK,
                        PRESERVE_AND_RETRY, REVERT_AND_RETRY})

CONFIDENCES = ("HIGH", "MEDIUM", "LOW")

RECOMMENDATION_KEYS = (
    "decision", "reason", "next_goal", "children",
    "requested_write_scope", "requested_commands",
    "preserve_changes", "revert_changes",
    "human_required", "confidence",
)

CHILD_KEYS = ("goal", "requested_write_scope", "requested_commands")


def _str_list(value, label: str) -> list[str]:
    if not isinstance(value, list):
        raise _esc.Escalation(_esc.SUPERVISOR_MALFORMED, f"{label} must be a list")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise _esc.Escalation(
                _esc.SUPERVISOR_MALFORMED, f"{label} must contain non-empty strings")
    return [item.strip() for item in value]


def validate(payload: object) -> dict:
    """Return the recommendation, or raise Escalation. Never returns a default."""
    if not isinstance(payload, dict):
        raise _esc.Escalation(
            _esc.SUPERVISOR_MALFORMED, "the supervisor response is not an object")
    missing = sorted(set(RECOMMENDATION_KEYS) - set(payload))
    extra = sorted(str(k) for k in set(payload) - set(RECOMMENDATION_KEYS))
    if extra:
        raise _esc.Escalation(
            _esc.SUPERVISOR_UNKNOWN_FIELD,
            f"the supervisor response carries field(s) no schema allows: {extra}; a "
            "field whose author believed it meant something is never dropped silently")
    if missing:
        raise _esc.Escalation(
            _esc.SUPERVISOR_MALFORMED,
            f"the supervisor response is missing {missing}")

    decision = payload["decision"]
    if decision not in DECISIONS:
        raise _esc.Escalation(
            _esc.SUPERVISOR_UNKNOWN_DECISION,
            f"{decision!r} is not one of {list(DECISIONS)}; an unrecognised instruction "
            "is not a safe default")
    if not isinstance(payload["reason"], str) or not payload["reason"].strip():
        raise _esc.Escalation(
            _esc.SUPERVISOR_MALFORMED, "reason must be a non-empty string")
    if not isinstance(payload["human_required"], bool):
        raise _esc.Escalation(
            _esc.SUPERVISOR_MALFORMED, "human_required must be a boolean")
    if payload["confidence"] not in CONFIDENCES:
        raise _esc.Escalation(
            _esc.SUPERVISOR_MALFORMED,
            f"confidence {payload['confidence']!r} is not one of {list(CONFIDENCES)}")

    next_goal = payload["next_goal"]
    if next_goal is not None and (not isinstance(next_goal, str) or not next_goal.strip()):
        raise _esc.Escalation(
            _esc.SUPERVISOR_MALFORMED, "next_goal must be a non-empty string or null")

    children = payload["children"]
    if not isinstance(children, list):
        raise _esc.Escalation(_esc.SUPERVISOR_MALFORMED, "children must be a list")
    clean_children = []
    for index, child in enumerate(children):
        if not isinstance(child, dict) or set(child) != set(CHILD_KEYS):
            raise _esc.Escalation(
                _esc.SUPERVISOR_MALFORMED,
                f"children[{index}] fields invalid: "
                f"missing={sorted(set(CHILD_KEYS) - set(child or ()))} "
                f"unexpected={sorted(str(k) for k in set(child or ()) - set(CHILD_KEYS))}")
        if not isinstance(child["goal"], str) or not child["goal"].strip():
            raise _esc.Escalation(
                _esc.SUPERVISOR_MALFORMED, f"children[{index}].goal must be a non-empty string")
        clean_children.append({
            "goal": child["goal"].strip(),
            "requested_write_scope": _str_list(
                child["requested_write_scope"], f"children[{index}].requested_write_scope"),
            "requested_commands": _str_list(
                child["requested_commands"], f"children[{index}].requested_commands"),
        })

    recommendation = {
        "decision": decision,
        "reason": payload["reason"].strip(),
        "next_goal": next_goal.strip() if isinstance(next_goal, str) else None,
        "children": clean_children,
        "requested_write_scope": _str_list(
            payload["requested_write_scope"], "requested_write_scope"),
        "requested_commands": _str_list(
            payload["requested_commands"], "requested_commands"),
        "preserve_changes": _str_list(payload["preserve_changes"], "preserve_changes"),
        "revert_changes": _str_list(payload["revert_changes"], "revert_changes"),
        "human_required": payload["human_required"],
        "confidence": payload["confidence"],
    }

    # --- shape rules that depend on the decision --------------------------
    if decision == SPLIT_TASK and not clean_children:
        raise _esc.Escalation(
            _esc.SUPERVISOR_MALFORMED, "SPLIT_TASK must name at least one child")
    if decision != SPLIT_TASK and clean_children:
        raise _esc.Escalation(
            _esc.SUPERVISOR_MALFORMED,
            f"{decision} carries children, which only SPLIT_TASK may")
    if decision in (RETRY_NARROWER, PRESERVE_AND_RETRY, REVERT_AND_RETRY) \
            and not recommendation["next_goal"]:
        raise _esc.Escalation(
            _esc.SUPERVISOR_MALFORMED, f"{decision} must state a next_goal")
    if decision == REVERT_AND_RETRY and not recommendation["revert_changes"]:
        raise _esc.Escalation(
            _esc.SUPERVISOR_MALFORMED, "REVERT_AND_RETRY must name what to revert")
    if decision == PRESERVE_AND_RETRY and not recommendation["preserve_changes"]:
        raise _esc.Escalation(
            _esc.SUPERVISOR_MALFORMED, "PRESERVE_AND_RETRY must name what to preserve")
    overlap = sorted(set(recommendation["preserve_changes"])
                     & set(recommendation["revert_changes"]))
    if overlap:
        # Asking for both is two incompatible instructions about one file.
        # Choosing either would be Diana deciding what the supervisor meant.
        raise _esc.Escalation(
            _esc.SUPERVISOR_MALFORMED,
            f"{overlap} appear in both preserve_changes and revert_changes; the "
            "recommendation contradicts itself and is not resolved in either direction")
    return recommendation


def empty(decision: str, reason: str, **overrides) -> dict:
    """A well-formed recommendation with every optional field empty."""
    payload = {
        "decision": decision, "reason": reason, "next_goal": None, "children": [],
        "requested_write_scope": [], "requested_commands": [],
        "preserve_changes": [], "revert_changes": [],
        "human_required": False, "confidence": "MEDIUM",
    }
    payload.update(overrides)
    return validate(payload)
