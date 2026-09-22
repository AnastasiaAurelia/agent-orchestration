#!/usr/bin/env python3
"""Diana supervisors: the provider-neutral protocol.

A supervisor satisfies one seam:

    supervisor.diagnose(evidence: dict) -> dict        # raw, unvalidated
    supervisor.name -> str                             # audit identity
    supervisor.model -> str | None                     # audit identity

`diagnose` returns whatever the provider produced. It is NOT authority and is
never trusted: `schema.validate` is applied by the caller, and a provider that
raises, times out, or returns prose yields an escalation rather than a default.

## Why the core never imports an adapter

Nothing in `diana/autonomy/` imports `openai.py`, and nothing in the governance
path mentions a model name. A future Claude, Gemini or local adapter is a new
file satisfying this seam; policy validation does not change, which is the
property that makes "provider-neutral" a fact about the code rather than a
claim.

## What evidence a supervisor is given, and what it is not

`build_evidence` composes the deterministic record. It is built by Diana from
Diana's own artifacts -- the journal, reconciliation, provenance, budgets --
never from the backend's self-report. What is NOT handed over is as deliberate
as what is: no credentials, no environment, no contract digest to quote back, no
run directory path, and nothing from `~/.hermes/`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "autonomy"))
import escalation as _esc  # noqa: E402
import schema as _schema  # noqa: E402

# Bound every free-text field handed to a provider. Evidence is a summary, not a
# transcript, and an unbounded field is an unbounded prompt.
MAX_TEXT_CHARS = 4000
MAX_LIST_ITEMS = 200

# Anything token-shaped is removed before evidence leaves Diana. Evidence is
# built from Diana's own artifacts, which do not hold credentials -- this is the
# belt to that braces, because a verification transcript is repository output
# and repository output is not Diana's to vouch for.
_TOKEN_LIKE = re.compile(r"[A-Za-z0-9_\-]{32,}\.[A-Za-z0-9_\-.]{8,}"
                         r"|(?:sk|ghp|gho|xox[abps]|AKIA)[-_A-Za-z0-9]{12,}"
                         r"|eyJ[A-Za-z0-9_\-]{16,}")


def redact(text: object) -> str:
    return _TOKEN_LIKE.sub("<redacted>", str(text))[:MAX_TEXT_CHARS]


def _paths(values):
    return [str(v) for v in list(values or ())[:MAX_LIST_ITEMS]]


EVIDENCE_KEYS = (
    "schema_version", "root_run_id", "parent_run_id", "original_goal", "current_goal",
    "standing_authority", "current_authority", "run_state", "failure_reason",
    "attempt_history", "changed_files", "owned_changes", "preexisting_changes",
    "verification_passed", "verification_output", "reviewer_output",
    "builder_failure", "budget_remaining", "workspace_provenance",
    "autonomy_allows", "recovery_class",
)


def build_evidence(*, root_run_id, parent_run_id, original_goal, current_goal,
                   standing_doc, current_contract, run_state, failure_reason,
                   attempt_history, changed_files, owned_changes, preexisting_changes,
                   verification_passed, verification_output, reviewer_output,
                   builder_failure, budget_remaining, workspace_provenance,
                   recovery_class) -> dict:
    """The deterministic evidence record. Diana's own facts, bounded and redacted."""
    authority = standing_doc["authority"]
    envelope = authority["capability_envelope"]
    current_env = (current_contract or {}).get("capability_envelope") or {}
    evidence = {
        "schema_version": _schema.SCHEMA_VERSION,
        "root_run_id": str(root_run_id),
        "parent_run_id": str(parent_run_id),
        "original_goal": redact(original_goal),
        "current_goal": redact(current_goal),
        # The CEILING, so a planner can propose something inside it rather than
        # guessing and being refused. Stating the ceiling is not granting it.
        "standing_authority": {
            "write_scope": _paths((envelope.get("write_scope") or {}).get("allowed_roots")),
            "allowed_commands": _paths(envelope.get("allowed_commands")),
            "allowed_tools": _paths(envelope.get("allowed_tools")),
            "risk": authority.get("risk"), "depth": authority.get("depth"),
        },
        "current_authority": {
            "write_scope": _paths((current_env.get("write_scope") or {}).get("allowed_roots")),
            "allowed_commands": _paths(current_env.get("allowed_commands")),
        },
        "run_state": str(run_state),
        "failure_reason": redact(failure_reason),
        "attempt_history": list(attempt_history or ())[:MAX_LIST_ITEMS],
        "changed_files": _paths(changed_files),
        "owned_changes": _paths(owned_changes),
        "preexisting_changes": _paths(preexisting_changes),
        "verification_passed": bool(verification_passed),
        "verification_output": redact(verification_output),
        "reviewer_output": redact(reviewer_output),
        "builder_failure": redact(builder_failure),
        "budget_remaining": dict(budget_remaining or {}),
        "workspace_provenance": dict(workspace_provenance or {}),
        "autonomy_allows": dict(standing_doc["autonomy"]["allow"]),
        "recovery_class": str(recovery_class),
    }
    require_complete(evidence)
    return evidence


def require_complete(evidence: object) -> dict:
    """Missing evidence escalates: a diagnosis without inputs is a guess."""
    if not isinstance(evidence, dict):
        raise _esc.Escalation(
            _esc.SUPERVISOR_EVIDENCE_MISSING, "evidence is not an object")
    missing = sorted(set(EVIDENCE_KEYS) - set(evidence))
    if missing:
        raise _esc.Escalation(
            _esc.SUPERVISOR_EVIDENCE_MISSING,
            f"the supervisor cannot be asked to diagnose without {missing}")
    return evidence


class Supervisor:
    """The seam. Subclasses implement `diagnose`."""

    name = "supervisor"
    model: str | None = None

    def diagnose(self, evidence: dict) -> dict:  # pragma: no cover - interface
        raise NotImplementedError

    # --- the ONLY way a recommendation enters Diana ----------------------
    def recommend(self, evidence: dict) -> dict:
        """Diagnose, then validate. A provider failure is an escalation.

        Every exception a provider can raise is converted here into a fail-closed
        escalation, because the alternative -- letting a transport error
        propagate as an ordinary crash -- would stop the run in a state that
        says nothing about whether recovery was even attempted.
        """
        require_complete(evidence)
        try:
            raw = self.diagnose(evidence)
        except _esc.Escalation:
            raise
        except TimeoutError as exc:
            raise _esc.Escalation(
                _esc.SUPERVISOR_TIMEOUT,
                f"the supervisor did not answer in time ({redact(exc)})") from None
        except BaseException as exc:  # noqa: BLE001 - every failure is fail-closed
            raise _esc.Escalation(
                _esc.SUPERVISOR_UNAVAILABLE,
                f"the supervisor could not be consulted "
                f"({type(exc).__name__}: {redact(exc)[:200]})") from None
        return _schema.validate(raw)
