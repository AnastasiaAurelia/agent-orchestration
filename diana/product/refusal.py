#!/usr/bin/env python3
"""Diana M7: the product layer's own closed refusal vocabulary (M7-E1-D8).

`diana/runtime/blocking.py` refuses any code outside `ALL_REASON_CODES` -- by
design, so a caller cannot invent an unfalsifiable reason -- and it is a
pre-existing production file M7 may not modify (M7-REG-2). So M7 brings its own
vocabulary rather than borrowing one.

That is the better fit, not a workaround. Every refusal here happens **before a
run exists**: there is no contract, no journal, no lease and no attempt to be
blocked. A refused proposal is a pre-authority refusal, and conflating it with
M5/M6's run-blocking reason codes would blur a real distinction an operator
needs -- "your request was not turned into authority" is not "your run stopped".

The vocabulary is closed and each code is distinct, for M1 AC-1's reason: an
operator reads the code, and one over-broad code passing several different
failures would prove nothing about any of them.
"""

from __future__ import annotations

# --- intent could not be turned into a proposal ---------------------------
INTENT_UNROUTABLE = "intent-unroutable"
INTENT_AMBIGUOUS = "intent-ambiguous"
INTENT_MALFORMED = "intent-malformed"
INTENT_UNKNOWN_FIELD = "intent-unknown-field"

# --- the request asked for authority policy will not grant ----------------
COMMAND_NOT_IN_CATALOGUE = "command-not-in-catalogue"
WRITE_PATH_FORBIDDEN = "write-path-forbidden"
WRITE_PATH_OUTSIDE_REPO = "write-path-outside-repo"
WORKFLOW_NOT_CERTIFIED = "workflow-not-certified"
WORKFLOW_NO_PRODUCT_PATH = "workflow-no-product-path"
RISK_OR_DEPTH_PROPOSED = "risk-or-depth-proposed"
NO_WRITABLE_SCOPE = "no-writable-scope"
NO_VERIFICATION_COMMAND = "no-verification-command"

# --- proposal / approval binding ------------------------------------------
PROPOSAL_MALFORMED = "proposal-malformed"
PROPOSAL_NOT_FOUND = "proposal-not-found"
PROPOSAL_STALE = "proposal-stale"
APPROVAL_DIGEST_MISMATCH = "approval-digest-mismatch"
APPROVAL_NOT_A_DIGEST = "approval-not-a-digest"
CONTRACT_PREDICTION_FAILED = "contract-prediction-failed"

ALL_REFUSALS = frozenset(
    value for name, value in list(globals().items())
    if name.isupper() and name != "ALL_REFUSALS" and isinstance(value, str)
)


class Refused(Exception):
    """A pre-authority refusal, carrying its specific code.

    Deliberately NOT `blocking.Blocked`: this is raised where no run exists, and
    a caller that catches run-blocking reasons must not silently absorb a
    refusal to create authority in the first place.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        if code not in ALL_REFUSALS:
            raise ValueError(f"unknown M7 refusal code: {code!r}")
        self.code = code
        self.detail = detail
        super().__init__(f"REFUSED[{code}]" + (f": {detail}" if detail else ""))

    def as_record(self) -> dict:
        return {"refused": True, "code": self.code, "detail": self.detail}
