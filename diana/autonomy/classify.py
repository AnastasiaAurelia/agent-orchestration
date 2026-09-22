#!/usr/bin/env python3
"""Diana autonomy: is this failure one recovery could address?

Not every FAILED run is the same kind of FAILED, and treating them alike is how
a recovery loop either gives up on ordinary problems or grinds forever on
impossible ones. This module answers one question -- may a supervisor even be
consulted about this outcome? -- from the run's own terminal reason code.

## Why it reads reason codes and not messages

Diana's reason codes are a closed vocabulary (`runtime/blocking.py`) chosen so
that every distinct failure has its own. Classifying on the CODE is therefore
deterministic and complete; classifying on a message would be string-matching
prose that no control owns. Nothing here mentions a language, a framework, a
test runner or a project: a domain-specific failure name would make Diana
repository-shaped, which is the thing it must never be.

## Why unknown codes escalate

A code this table does not know is a failure Diana has not reasoned about. The
safe reading of "I do not recognise this" is not "try again".
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "runtime"))
sys.path.insert(0, str(_HERE.parent / "unattended"))
import blocking as _blocking  # noqa: E402
import escalation as _esc  # noqa: E402
import journal as _journal  # noqa: E402

RECOVERABLE = "recoverable"
TERMINAL_SUCCESS = "complete"
ESCALATE = "escalate"

# Failures a bounded recovery can plausibly address: the work did not finish,
# or finished wrong, in a way a narrower or differently-shaped attempt might.
RECOVERABLE_REASONS = {
    _blocking.HERMES_TURN_FAILED: "the working turn did not finish",
    _blocking.ATTEMPT_BUDGET_EXHAUSTED: "this run used its attempts",
    _blocking.RUN_DEADLINE_EXCEEDED: "this run reached its time limit",
    _blocking.REVIEW_VERDICT_ABSENT: "the reviewer produced no verdict",
    _blocking.REVIEW_VERDICT_MALFORMED: "the reviewer's verdict was malformed",
    _blocking.REVIEW_VERDICT_SELF_CONTRADICTORY: "the reviewer contradicted itself",
    _blocking.RECONCILIATION_MISMATCH: "the attempt changed files outside its scope",
    _blocking.DEPENDENCY_BLOCKED: "an earlier step did not finish",
}

# Failures where the next step is authority, identity or a human judgement.
# Retrying these is not bounded recovery; it is repetition.
ESCALATING_REASONS = {
    _blocking.HERMES_PROVIDER_UNAVAILABLE: "the model provider could not be reached",
    _blocking.TARGET_MOVED: "the repository changed underneath the run",
    _blocking.TARGET_UNREADABLE: "the repository could not be read",
    _blocking.RUN_ID_MISMATCH: "the run identity did not match",
    _blocking.CONTRACT_DIGEST_MISMATCH: "the approved contract no longer matches",
    _blocking.RUN_POLICY_DIGEST_MISMATCH: "the approved run policy no longer matches",
    _blocking.JOURNAL_DIGEST_MISMATCH: "the journal no longer matches its digest",
    _blocking.JOURNAL_STALE: "the journal is stale",
    _blocking.ACTOR_TOPOLOGY_DIGEST_MISMATCH: "the actor topology no longer matches",
    _blocking.ACTOR_PROJECTION_NOT_PROVEN: "an actor's boundary could not be proven",
    _blocking.ACTOR_PROJECTION_NOT_SUBSET: "an actor's boundary exceeded the approval",
    _blocking.REVIEW_VERDICT_WRONG_ACTOR: "a verdict came from the wrong actor",
    _blocking.QUIESCENCE_NOT_PROVEN: "a process from this run could not be shown to have stopped",
    _blocking.RUN_CANCELLED: "the run was cancelled",
    _blocking.WRITE_SCOPE_EXCEEDS_READ_SCOPE: "the approved scopes are inconsistent",
    _blocking.ENFORCEMENT_NOT_REESTABLISHED: "the enforcement boundary could not be re-established",
}


def classify_outcome(record: dict) -> tuple[str, str]:
    """(class, human-readable reason) for a finished run's journal record."""
    terminal = record.get("terminal") or {}
    state = record.get("state")
    if state == _journal.COMPLETE:
        return TERMINAL_SUCCESS, "every declared work item is complete"
    code = terminal.get("reason_code")
    if code in RECOVERABLE_REASONS:
        return RECOVERABLE, RECOVERABLE_REASONS[code]
    if code in ESCALATING_REASONS:
        return ESCALATE, ESCALATING_REASONS[code]
    return ESCALATE, (f"the run stopped with {code!r}, which Diana has no bounded "
                      "recovery for")


def require_recoverable(record: dict) -> str:
    """Raise unless a supervisor may be consulted about this outcome."""
    kind, reason = classify_outcome(record)
    if kind == RECOVERABLE:
        return reason
    if kind == TERMINAL_SUCCESS:
        raise _esc.Escalation(
            _esc.NOT_RECOVERABLE, "this run completed; there is nothing to recover")
    raise _esc.Escalation(
        _esc.NOT_RECOVERABLE, reason,
        requested={"reason_code": (record.get("terminal") or {}).get("reason_code")})


def prove_progress(history: list) -> None:
    """A recovery loop that repeats itself is not recovering.

    `history` is the ordered list of (goal, reason_code) pairs this lineage has
    already produced. Two consecutive children with the same goal AND the same
    failure have demonstrated that the plan does not work, and a third is
    spending a human's budget to learn nothing.
    """
    if len(history) < 2:
        return
    last, previous = history[-1], history[-2]
    if last == previous:
        raise _esc.Escalation(
            _esc.NO_PROGRESS,
            f"the last two recovery attempts had the same goal and failed the same way "
            f"({last[1]!r}); further repetition would spend budget without learning "
            "anything")
