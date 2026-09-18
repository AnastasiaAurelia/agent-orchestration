#!/usr/bin/env python3
"""Diana M6: the reviewer verdict -- a closed schema and an INPUT (M6-R4, M6-R5).

A verdict never transitions anything. `is_work_finished` remains the Diana
predicate (M5-E1-D4), it still runs only after reconciliation has been
discharged, and an attempt whose diff left the envelope is BLOCKED whatever the
reviewer said. What this module does is decide whether a reviewer produced a
*well-formed* judgement at all.

## Why absence must look exactly like failure

M6-R5. A crashed, stalled, refusing or silently-truncated reviewer session must
be indistinguishable from an explicit `FAIL`. The tempting alternative -- "no
objection was raised, so continue" -- converts every reviewer malfunction into
an approval, and the malfunction most likely to happen is the one where the
reviewer never got far enough to object.

## Why there is no `severity` field, unlike the AO verdict

`diana/ship/ship.py`'s reviewer contract carries `severity` on each finding.
M6's does not, and the omission is deliberate: M1 D14 denies the agent any
channel to propose severity, risk or depth, and M5-D17 restates it for the run
report. A reviewer is an agent. Accepting a severity it authored would reopen
that channel through the role that looks most trustworthy, which is exactly the
place it would be least noticed. `severity` is therefore not merely ignored --
it is an unknown key, and an unknown key is a refusal (roadmap invariant 4).

## Why a self-contradictory verdict is refused rather than resolved

A `PASS` alongside a failing `dod_checks` entry is two statements that cannot
both be acted on. Picking either one means Diana deciding what the reviewer
meant. Refusing means Diana declining to guess -- the same choice `ship.py`
made, for the same reason, and the same fail-closed direction M5-D9 requires.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "runtime"))
import blocking  # noqa: E402
import topology as _topology  # noqa: E402

VERDICT_KEYS = ("decision", "summary", "findings", "dod_checks")
FINDING_KEYS = ("description", "evidence")
CHECK_KEYS = ("criterion", "result", "evidence")

PASS = "PASS"
FAIL = "FAIL"
DECISIONS = (PASS, FAIL)


def _require_entries(items, keys, label) -> None:
    if not isinstance(items, list):
        raise blocking.Blocked(
            blocking.REVIEW_VERDICT_MALFORMED, f"{label} must be a list")
    for index, entry in enumerate(items):
        if not isinstance(entry, dict) or set(entry) != set(keys):
            missing = sorted(set(keys) - set(entry or ()))
            extra = sorted(set(entry or ()) - set(keys))
            raise blocking.Blocked(
                blocking.REVIEW_VERDICT_MALFORMED,
                f"{label}[{index}] shape invalid: missing={missing} unexpected={extra}")
        for key in keys:
            if not isinstance(entry[key], str) or not entry[key].strip():
                raise blocking.Blocked(
                    blocking.REVIEW_VERDICT_MALFORMED,
                    f"{label}[{index}].{key} must be a non-empty string")


def validate(verdict: object) -> dict:
    """Return the verdict, or raise Blocked with its own reason code.

    Absence, malformation and self-contradiction get DIFFERENT codes on
    purpose: one over-broad check that refused all three would prove nothing
    about any of them (M1's AC-1 reasoning, and M6-AC-16 asserts each).
    """
    if verdict is None:
        raise blocking.Blocked(
            blocking.REVIEW_VERDICT_ABSENT,
            "the reviewer produced no verdict; absence is failure, never approval")
    if not isinstance(verdict, dict):
        raise blocking.Blocked(
            blocking.REVIEW_VERDICT_MALFORMED, "verdict is not an object")
    missing = sorted(set(VERDICT_KEYS) - set(verdict))
    extra = sorted(set(verdict) - set(VERDICT_KEYS))
    if missing or extra:
        raise blocking.Blocked(
            blocking.REVIEW_VERDICT_MALFORMED,
            f"verdict shape invalid: missing={missing} unexpected={extra}")
    if verdict["decision"] not in DECISIONS:
        raise blocking.Blocked(
            blocking.REVIEW_VERDICT_MALFORMED,
            f"decision {verdict['decision']!r} is not one of {list(DECISIONS)}")
    if not isinstance(verdict["summary"], str) or not verdict["summary"].strip():
        raise blocking.Blocked(
            blocking.REVIEW_VERDICT_MALFORMED, "summary must be a non-empty string")
    _require_entries(verdict["findings"], FINDING_KEYS, "findings")
    _require_entries(verdict["dod_checks"], CHECK_KEYS, "dod_checks")
    for index, check in enumerate(verdict["dod_checks"]):
        if check["result"] not in DECISIONS:
            raise blocking.Blocked(
                blocking.REVIEW_VERDICT_MALFORMED,
                f"dod_checks[{index}].result {check['result']!r} is not one of {list(DECISIONS)}")

    failing = [c for c in verdict["dod_checks"] if c["result"] == FAIL]
    if verdict["decision"] == PASS and failing:
        raise blocking.Blocked(
            blocking.REVIEW_VERDICT_SELF_CONTRADICTORY,
            f"decision is PASS while {len(failing)} dod_check(s) report FAIL; "
            "the verdict contradicts itself and is not resolved in either direction")
    if verdict["decision"] == FAIL and not verdict["findings"] and not failing:
        raise blocking.Blocked(
            blocking.REVIEW_VERDICT_MALFORMED,
            "a FAIL verdict must state at least one finding or one failing dod_check; "
            "a bare rejection is malformed, not a valid rejection")
    return verdict


def accept(verdict: object, *, producing_attempt: dict, reviewed_attempt: dict) -> dict:
    """Admit a verdict only from a REVIEWER attempt that is not the one under review.

    M6-R7, and it is the whole anti-impersonation mechanism: the role of every
    attempt was journaled by Diana BEFORE the turn ran (M6-D4), so a verdict's
    provenance is not something its author can state. A BUILDER attempt's output
    is not readable as a verdict whatever it contains, and a reviewer cannot
    review its own work because an attempt is never its own reviewer.
    """
    producing_actor = producing_attempt.get("actor")
    if producing_actor != _topology.REVIEWER:
        raise blocking.Blocked(
            blocking.REVIEW_VERDICT_WRONG_ACTOR,
            f"attempt {producing_attempt.get('attempt')} acted as {producing_actor!r}, "
            f"so its output is not a reviewer verdict")
    if producing_attempt.get("attempt") == reviewed_attempt.get("attempt"):
        raise blocking.Blocked(
            blocking.REVIEW_VERDICT_WRONG_ACTOR,
            "an attempt cannot review itself")
    if reviewed_attempt.get("actor") == _topology.REVIEWER:
        raise blocking.Blocked(
            blocking.REVIEW_VERDICT_WRONG_ACTOR,
            f"attempt {reviewed_attempt.get('attempt')} was itself a REVIEWER attempt; "
            "a reviewer's own output is not reviewable work")
    return validate(verdict)
