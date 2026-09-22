#!/usr/bin/env python3
"""Diana autonomy: the closed escalation vocabulary.

Diana already has two refusal vocabularies and this is deliberately a third,
because it names a third moment.

  `product/refusal.Refused`   -- PRE-authority. No run exists yet.
  `runtime/blocking.Blocked`  -- a run exists and cannot continue.
  `Escalation` (here)         -- a run has stopped, autonomous recovery was
                                 considered, and the next step needs a human.

Conflating the third with the second would lose the distinction the whole
feature exists to create: "your run stopped" is not "your run stopped AND I
tried N bounded recoveries AND the next one would need authority you did not
grant". An operator returning to an unattended run reads the code, and one
over-broad code would make every autonomous outcome look alike (M1's AC-1
reasoning, carried forward).

Every code here is a reason autonomy STOPPED rather than continued. There is no
code meaning "proceeded", because proceeding is not an escalation.
"""

from __future__ import annotations

# --- the recommendation could not be admitted -----------------------------
SUPERVISOR_UNAVAILABLE = "supervisor-unavailable"
SUPERVISOR_TIMEOUT = "supervisor-timeout"
SUPERVISOR_MALFORMED = "supervisor-malformed"
SUPERVISOR_UNKNOWN_DECISION = "supervisor-unknown-decision"
SUPERVISOR_UNKNOWN_FIELD = "supervisor-unknown-field"
SUPERVISOR_REQUESTED_HUMAN = "supervisor-requested-human"
SUPERVISOR_EVIDENCE_MISSING = "supervisor-evidence-missing"

# --- the recommendation was well formed and is not executable -------------
AUTHORITY_EXPANSION_REQUESTED = "authority-expansion-requested"
WRITE_SCOPE_NOT_SUBSET = "write-scope-not-subset"
COMMAND_NOT_SUBSET = "command-not-subset"
FORBIDDEN_CAPABILITY_REQUESTED = "forbidden-capability-requested"
DEPENDENCY_CHANGE_NOT_AUTHORIZED = "dependency-change-not-authorized"
HUMAN_ONLY_CONDITION = "human-only-condition"
REPOSITORY_MISMATCH = "repository-mismatch"
STANDING_APPROVAL_INVALID = "standing-approval-invalid"
STANDING_APPROVAL_DIGEST_MISMATCH = "standing-approval-digest-mismatch"
AUTONOMY_DISABLED = "autonomy-disabled"

# --- a bound was reached ---------------------------------------------------
CHILD_BUDGET_EXHAUSTED = "child-budget-exhausted"
CHILD_DEPTH_EXCEEDED = "child-depth-exceeded"
ATTEMPT_BUDGET_EXHAUSTED = "attempt-budget-exhausted"
WALL_CLOCK_EXHAUSTED = "wall-clock-exhausted"
CHANGED_FILE_BUDGET_EXHAUSTED = "changed-file-budget-exhausted"
SUPERVISOR_CALL_BUDGET_EXHAUSTED = "supervisor-call-budget-exhausted"
NO_PROGRESS = "no-progress"

# --- the workspace could not be transitioned safely ------------------------
PROVENANCE_AMBIGUOUS = "provenance-ambiguous"
REVERT_WOULD_LOSE_WORK = "revert-would-lose-work"
PRESERVE_CONFLICT = "preserve-conflict"
LINEAGE_CORRUPT = "lineage-corrupt"

# --- the failure was never a recoverable one -------------------------------
NOT_RECOVERABLE = "not-recoverable"

ALL_ESCALATIONS = frozenset(
    value for name, value in list(globals().items())
    if name.isupper() and name != "ALL_ESCALATIONS" and isinstance(value, str)
)

# The terminal state an escalation produces. Deliberately NOT one of
# `journal.TERMINAL_STATES`: the journal's schema is frozen and this is a
# lineage-level outcome, not a run-level one. A single run inside an escalated
# lineage is still FAILED or BLOCKED in its own journal, exactly as before.
BLOCKED_FOR_HUMAN = "BLOCKED_FOR_HUMAN"


class Escalation(Exception):
    """Autonomy stopped and a human is required. Carries its specific code."""

    def __init__(self, code: str, detail: str = "", *, requested: dict | None = None) -> None:
        if code not in ALL_ESCALATIONS:
            raise ValueError(f"unknown Diana escalation code: {code!r}")
        self.code = code
        self.detail = detail
        # The exact additional authority or decision being asked for, when there
        # is one. Structured so the CLI can state it precisely rather than
        # printing "human decision needed".
        self.requested = dict(requested or {})
        super().__init__(f"ESCALATION[{code}]" + (f": {detail}" if detail else ""))

    def as_record(self) -> dict:
        return {"escalated": True, "code": self.code, "detail": self.detail,
                "requested": dict(self.requested)}
