#!/usr/bin/env python3
"""Diana supervisors: deterministic test doubles.

The autonomous control loop is a STATE MACHINE, and a state machine is proven
with inputs it controls. A live provider proves the adapter; it cannot prove the
loop, because its answers are not reproducible. So every loop property Diana
claims is proven against these.

`ScriptedSupervisor` returns a fixed sequence, so an acceptance scenario reads
as the sequence of recommendations it is about. `FailingSupervisor` and
`SlowSupervisor` exist because "the provider broke" is a first-class path that
must fail closed rather than a situation to be hand-waved in a comment.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import base as _base  # noqa: E402
import schema as _schema  # noqa: E402


class ScriptedSupervisor(_base.Supervisor):
    """Returns `responses` in order. Records every evidence record it saw."""

    name = "scripted-supervisor"
    model = None

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.seen: list[dict] = []

    def diagnose(self, evidence: dict) -> dict:
        self.seen.append(evidence)
        if not self.responses:
            # Running out of script is a provider that stopped answering.
            raise RuntimeError("the scripted supervisor has no further response")
        response = self.responses.pop(0)
        return response(evidence) if callable(response) else response


class FailingSupervisor(_base.Supervisor):
    """A provider that is simply unavailable."""

    name = "failing-supervisor"

    def __init__(self, exception=None) -> None:
        self.exception = exception or RuntimeError("provider unreachable")

    def diagnose(self, evidence: dict) -> dict:
        raise self.exception


class SlowSupervisor(_base.Supervisor):
    """A provider that exceeds its deadline."""

    name = "slow-supervisor"

    def diagnose(self, evidence: dict) -> dict:
        raise TimeoutError("supervisor deadline exceeded")


class HumanSupervisor(_base.Supervisor):
    """The no-op supervisor: always hands the decision back to a person.

    This is what `--autonomy` without a configured provider resolves to, and it
    is the safe default rather than a degraded one -- Diana still records the
    failure, the budgets and the provenance, and still states precisely what it
    would need. It simply does not plan.
    """

    name = "human-supervisor"

    def diagnose(self, evidence: dict) -> dict:
        return _schema.empty(
            _schema.ESCALATE_HUMAN,
            "no automated supervisor is configured for this run, so recovery planning "
            "is a human decision",
            human_required=True, confidence="HIGH")
