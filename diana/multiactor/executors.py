#!/usr/bin/env python3
"""Diana M6: executor backends at the seam M5 already had (M6-D21, F14).

Phase 0 F14 found the executor is ALREADY an injected callable at
`unattended.execute` and `unattended.run_attempt`. So M6 adds no `AgentExecutor`
class hierarchy: a hierarchy would add a name, not a bound. What M6 adds is that
Diana selects which callable acts and writes the choice down.

A backend is therefore anything satisfying the seam:

    driver(contract_block, item_id) -> None        # may raise; Diana reconciles
    driver.record  -> dict | None                  # observability only (M2-D13)
    driver.verdict -> dict | None                  # reviewer backends only

Two deterministic backends are provided so that **backend replaceability is
proven against a second implementation rather than against a vendor** (M6-D21).
`HermesBuilder` is the real-model backend, reusing M4's `RemediationDriver`
unchanged.

## What `driver.record` is NOT

Phase 0 F9 measured `turn-record-<n>.json` accepting whatever a driver puts in
it -- a driver was given `{"backend_self_report": "hermes-deepseek"}` and Diana
persisted it verbatim. So a backend may name itself there, and that name is
never read to decide anything. The acting actor is the journal's (M6-D6), and
the backend's self-report is not an identity.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("runtime", "adapters", "mutation"):
    sys.path.insert(0, str(_HERE.parent / _sub))

import blocking  # noqa: E402


class _Backend:
    """Common seam plumbing. Subclasses implement `_run`."""

    name = "backend"

    def __init__(self) -> None:
        self.record: dict | None = None
        self.calls: list[str | None] = []

    def __call__(self, contract_block: dict, item_id: str | None = None):
        self.calls.append(item_id)
        self.record = {"backend_self_report": self.name, "item": item_id}
        return self._run(contract_block, item_id)

    def _run(self, contract_block: dict, item_id: str | None):  # pragma: no cover
        raise NotImplementedError


class ScriptedBuilder(_Backend):
    """Deterministic backend A: applies a caller-supplied edit map.

    `edits` maps item id -> (relative path, content). A deterministic backend is
    not a weaker proof of the SEAM than a model would be: the seam's contract is
    what it receives and what Diana does with the result, and neither depends on
    how the text inside the turn was produced.
    """

    name = "scripted-builder-A"

    def __init__(self, edits: dict, fail_on=(), no_op_on=()) -> None:
        super().__init__()
        self.edits, self.fail_on, self.no_op_on = edits, set(fail_on), set(no_op_on)

    def _run(self, contract_block: dict, item_id: str | None):
        if item_id in self.fail_on:
            raise RuntimeError(f"{self.name}: deliberate turn failure on {item_id}")
        if item_id in self.no_op_on:
            return None
        plan = self.edits.get(item_id)
        if plan is None:
            return None
        rel, content = plan
        path = Path(contract_block["target"]["repo_root"]) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return None


class TemplateBuilder(ScriptedBuilder):
    """Deterministic backend B: a DIFFERENT implementation at the same seam.

    It reaches the same target state by rewriting through a template rather than
    by writing a literal, so a run that switches from `ScriptedBuilder` to this
    one has genuinely changed backend and not merely relabelled one.
    """

    name = "template-builder-B"

    def _run(self, contract_block: dict, item_id: str | None):
        if item_id in self.fail_on:
            raise RuntimeError(f"{self.name}: deliberate turn failure on {item_id}")
        if item_id in self.no_op_on:
            return None
        plan = self.edits.get(item_id)
        if plan is None:
            return None
        rel, content = plan
        path = Path(contract_block["target"]["repo_root"]) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text() if path.exists() else ""
        rendered = "".join(
            line if line.strip() else line
            for line in content.splitlines(keepends=True)
        )
        path.write_text(rendered if rendered != existing else content)
        return None


class ScriptedReviewer(_Backend):
    """Deterministic reviewer backend: emits a verdict, mutates nothing.

    It is given Diana-supplied evidence (M6-R3) and returns a structured verdict.
    Its read-only-ness is NOT established by this class being well-behaved -- it
    is established by the projection installed before its turn, proven live
    through the real dispatch funnel, and by reconciliation afterwards.
    """

    name = "scripted-reviewer"

    def __init__(self, verdicts) -> None:
        super().__init__()
        self._verdicts = list(verdicts)
        self.verdict: dict | None = None
        self.evidence_seen: list[dict] = []

    def _run(self, contract_block: dict, item_id: str | None):
        self.verdict = self._verdicts.pop(0) if self._verdicts else None
        return None


class SilentReviewer(_Backend):
    """A reviewer that produces no verdict at all -- crashed, stalled, refusing.

    M6-R5 requires this to be indistinguishable from an explicit FAIL, which is
    why it exists as a first-class backend rather than as a test stub.
    """

    name = "silent-reviewer"
    verdict = None

    def _run(self, contract_block: dict, item_id: str | None):
        return None


class HermesBuilder(_Backend):
    """The real-model backend: M4's `RemediationDriver`, reused unmodified."""

    name = "hermes-remediation"

    def __init__(self, *, hermes_home=None, prompt=None) -> None:
        super().__init__()
        sys.path.insert(0, str(_HERE.parent / "mutation"))
        import remediation_driver as _rd

        self._driver = _rd.RemediationDriver(hermes_home=hermes_home, prompt=prompt)

    def _run(self, contract_block: dict, item_id: str | None):
        result = self._driver(contract_block)
        inner = self._driver.record or {}
        self.record = {"backend_self_report": self.name, "item": item_id, **inner}
        return result


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


class HermesReviewer(_Backend):
    """A real-model reviewer turn under the REVIEWER projection.

    The model is asked to end with a JSON verdict. Diana parses it and hands it
    to `verdict.validate`, so a model that returns prose, truncates, or refuses
    yields NO verdict -- which M6-R5 makes identical to a rejection. That is the
    designed behavior, not a degraded one.
    """

    name = "hermes-reviewer"

    def __init__(self, *, evidence_for, hermes_home=None) -> None:
        super().__init__()
        self._evidence_for = evidence_for
        self._hermes_home = hermes_home
        self.verdict: dict | None = None
        self.evidence_seen: list[dict] = []

    def _run(self, contract_block: dict, item_id: str | None):
        sys.path.insert(0, str(_HERE.parent / "adapters"))
        import hermes_live as _live

        evidence = self._evidence_for(contract_block, item_id)
        self.evidence_seen.append(evidence)
        prompt = (
            "You are reviewing one unit of completed work. You have READ-ONLY access.\n\n"
            f"Task under review: {evidence['task']}\n"
            f"Envelope in force: {json.dumps(evidence['envelope'], sort_keys=True)}\n"
            f"Paths the attempt touched: {evidence['paths_touched']}\n"
            f"Paths outside write_scope: {evidence['paths_outside_write_scope']}\n"
            f"Diana's deterministic verification: "
            f"{'PASSED' if evidence['verification_passed'] else 'FAILED'}\n"
            f"git status before/after: {evidence['git_status_before']!r} / "
            f"{evidence['git_status_after']!r}\n\n"
            "Read the changed files and judge whether the task is satisfied.\n"
            "Reply with ONE JSON object and nothing else:\n"
            '{"decision":"PASS"|"FAIL","summary":"...","findings":'
            '[{"description":"...","evidence":"..."}],'
            '"dod_checks":[{"criterion":"...","result":"PASS"|"FAIL","evidence":"..."}]}'
        )
        driver = _live.LiveTurnDriver(hermes_home=self._hermes_home, narrow=False,
                                      prompt=prompt)
        observations = driver(contract_block)
        inner = driver.record or {}
        self.record = {"backend_self_report": self.name, "item": item_id, **inner}
        text = (observations or [{}])[0].get("note", "") if observations else ""
        match = _JSON_BLOCK.search(text or "")
        if match is None:
            self.verdict = None
            return None
        try:
            self.verdict = json.loads(match.group(0))
        except json.JSONDecodeError:
            self.verdict = None
        return None


def evidence_builder(run_directory, record_provider, verify):
    """Diana-supplied reviewer evidence, composed exactly as M6-R3 fixes it.

    Phase 0 F16: the reviewer has no tool path to the run directory at all, so
    whatever it sees, Diana hands it. What is NOT handed over is as deliberate
    as what is: no journal, no contract digest, no run policy, no other actor's
    prompt, and nothing under `~/.hermes/`.
    """
    def build(contract_block: dict, item_id: str | None) -> dict:
        record = record_provider()
        attempts = record.get("attempts") or []
        latest = attempts[-1] if attempts else {}
        reconciliation = {}
        name = latest.get("reconciliation_file")
        if name:
            try:
                reconciliation = json.loads(
                    (Path(run_directory) / name).read_bytes().decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                reconciliation = {}
        envelope = contract_block["capability_envelope"]
        return {
            "task": contract_block["task"],
            "item_id": item_id,
            "envelope": {"allowed_tools": envelope.get("allowed_tools"),
                         "write_scope": (envelope.get("write_scope") or {}).get("allowed_roots"),
                         "allowed_commands": envelope.get("allowed_commands")},
            "paths_touched": reconciliation.get("paths_touched", []),
            "paths_outside_write_scope": reconciliation.get("paths_outside_write_scope", []),
            "within_envelope": reconciliation.get("within_envelope"),
            "git_status_before": reconciliation.get("git_status_before", ""),
            "git_status_after": reconciliation.get("git_status_after", ""),
            "verification_passed": bool(verify(contract_block, item_id)),
        }
    return build
