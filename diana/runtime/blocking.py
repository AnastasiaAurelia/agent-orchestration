#!/usr/bin/env python3
"""Diana runtime: the single BLOCKED vocabulary shared by every M1 component.

Per spec D37, *any* trusted-computing-base or process failure blocks the run
outright -- no ADVISORY_SECURITY_REVIEW is written, even when the deterministic
scanner's findings would have been sound. Blocking is therefore not an error
path bolted onto the side; it is the fail-closed default that every component
reaches for, and the reason code is the thing an operator reads.

Each reason code is distinct on purpose (acceptance criterion AC-1): the eight
preflight-failure fixtures must each produce *their own* code, so that one
over-broad check cannot pass all eight and prove nothing about any of them.

`BLOCKED` is never an outcome value inside a produced document (D36). A blocked
run produces only a Diana run record.
"""

from __future__ import annotations

# --- Preflight / trusted computing base (spec: TCB table, AC-1) ---
HERMES_UNREACHABLE = "hermes-unreachable"
HERMES_VERSION_PIN_MISMATCH = "hermes-version-pin-mismatch"
HERMES_COMMIT_PIN_MISMATCH = "hermes-commit-pin-mismatch"
SAFE_MODE_NOT_ENABLED = "hermes-safe-mode-not-enabled"
BACKGROUND_REVIEW_ENABLED = "hermes-background-review-enabled"
SIDE_QUESTION_ENABLED = "hermes-side-question-enabled"
HERMES_MD_PRESENT = "hermes-md-present"
AGENTS_OVERRIDE_PRESENT = "agents-override-md-present"
CAPABILITY_PATCH_NOT_LIVE = "capability-patch-not-live"
CONFINEMENT_PATCH_NOT_LIVE = "confinement-patch-not-live"

# --- Contract integrity (spec: ExecutionContract v1, D12/D13/D15) ---
CONTRACT_MALFORMED = "contract-malformed"
CONTRACT_DIGEST_MISMATCH = "contract-digest-mismatch"
CONTRACT_RUN_ID_MISMATCH = "contract-run-id-mismatch"
CONTRACT_NOT_SAFE_D1 = "contract-not-safe-d1"
READ_SCOPE_MALFORMED = "read-scope-malformed"

# --- Live turn (M2-D10): a failed turn is a process failure, not a partial result ---
HERMES_TURN_FAILED = "hermes-turn-failed"
HERMES_PROVIDER_UNAVAILABLE = "hermes-provider-unavailable"

# --- Bounded mutation (M4-D4, M4-D15) ---
# CONTRACT_CLASS_NOT_ACCEPTED is distinct from CONTRACT_NOT_SAFE_D1 on purpose:
# M1's code means "M1 executes only SAFE/D1" and is a frozen observable, so a
# later milestone refusing a class it does not execute must say something else.
CONTRACT_CLASS_NOT_ACCEPTED = "contract-class-not-accepted"
WRITE_SCOPE_MALFORMED = "write-scope-malformed"
WRITE_SCOPE_EXCEEDS_READ_SCOPE = "write-scope-exceeds-read-scope"
RECONCILIATION_MISMATCH = "reconciliation-mismatch"

# --- Run-time process failures (spec: failure-semantics table) ---
REPO_PROFILE_FAILED = "repo-profile-failed"
SCANNER_RAISED = "scanner-raised"
HERMES_OUTPUT_SCHEMA_VIOLATION = "hermes-output-schema-violation"

ALL_REASON_CODES = frozenset(
    value
    for name, value in list(globals().items())
    if name.isupper() and name != "ALL_REASON_CODES" and isinstance(value, str)
)


class Blocked(Exception):
    """A fail-closed refusal to proceed, carrying its specific reason code.

    Raising this is always correct where a component cannot *prove* the
    property it is responsible for. Never downgrade a Blocked into a warning,
    a default value, or a partial result: the whole point of M1 is that an
    advisory artifact is only ever produced by a run whose envelope was proven.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        if code not in ALL_REASON_CODES:
            # An unregistered code would let a caller invent an unfalsifiable
            # reason, so the vocabulary itself fails closed.
            raise ValueError(f"unknown BLOCKED reason code: {code!r}")
        self.code = code
        self.detail = detail
        super().__init__(f"BLOCKED[{code}]" + (f": {detail}" if detail else ""))

    def as_record(self) -> dict:
        """The run-record form. Deliberately NOT an ADVISORY_SECURITY_REVIEW."""
        return {"blocked": True, "reason_code": self.code, "detail": self.detail}
