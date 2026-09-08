"""Shared, minimal framework for Diana Security semantic-review artifact
normalization (Security Phase 4).

A semantic review evidence artifact is produced by an EXTERNAL, fresh,
independent, read-only reviewer session -- Diana does not run a model,
spawn a reviewer, or make any judgment call itself. This module only
parses an already-produced artifact recording what a reviewer inspected,
reasoned about, and concluded, exactly as
`diana/security/adapters/adapter_base.py` only parses an already-produced
static tool report, and `diana/security/dynamic/dynamic_base.py` only
parses an already-produced dynamic scenario artifact:

    fresh, independent, read-only reviewer session   <- NOT this repository
            |
    bound semantic-review evidence artifact           <- what this module parses
            |
    thin Diana normalization (this module)
            |
    evidence_model.py

Reuses `diana/security/adapters/adapter_base.py`'s `verify_identity()`,
`verify_target()`, `build_runs()`, `tool_error_runs()`,
`tool_unavailable_runs()`, and `NotAuthorized` directly -- a code review's
target is just `{repository, commit}`, the same shape Phase 2's static
adapters already verify against, so this is genuine reuse, not
duplication or reinvention of the same binding logic a third time.

## A departure from Phase 1-3's principle, stated explicitly

Every prior phase's normalizer *computed* PASS/FAIL from raw, independently
checkable facts (an evidence-item text match, an assertion outcome) and
never trusted a verifier's own self-reported conclusion. Semantic review
is categorically different: there is no deeper raw fact for this module to
derive a verdict from -- reasoning about architecture, trust boundaries,
and business-logic invariants is the entire point of asking for judgment
in the first place. This module therefore *does* accept the reviewer's own
`result` field as the verdict -- but only after it survives strict
structural gates that make an unsubstantiated or dishonest claim
mechanically difficult to submit:

- **Independence and read-only-ness are declared, checked, and
  documented as a necessary-but-not-sufficient proxy.** `reviewer.
  session_type` must be exactly `"fresh_read_only"` and
  `execution.mutations_attempted` must be exactly `false`. This module
  cannot cryptographically prove a review was actually conducted by a
  fresh, independent session that never touched a mutation approval --
  that is an operational guarantee the surrounding orchestration
  (spawning a fresh reviewer session, denying any mutation approval it
  requests, exactly as the main Diana roadmap's Phase 10 Independent
  Reviewer pattern already established) must uphold. This module only
  refuses to accept an artifact that doesn't even *claim* those
  properties.
- **"Looks fine" is not evidence.** A self-declared `PASS` or `FAIL`
  must be substantiated: non-trivial reasoning text, at least one cited
  file that was actually declared as inspected, and reasoning that isn't
  composed solely of generic reassurance phrases (`VAGUE_PHRASES`).
  `UNPROVEN`/`NOT_APPLICABLE`/`ERROR` carry no such bar -- a reviewer
  saying "I couldn't establish this" needs less proof than one claiming
  to have established something.
- **No fabricated citations.** Every `evidence_references[].file` must be
  a member of `files_inspected` -- a reviewer cannot cite a file it never
  declared having looked at.
- **Requirement text is tied to the real catalog contract**, exactly as
  every prior phase does: `requirement` must equal one of the target
  control's actual `catalog.json` `required_evidence` strings.
- **Capability is checked explicitly, not left only to
  `evidence_model.py`'s own downstream check.** `verifier_type` must be
  `SEMANTIC_REVIEW` or `HUMAN` (the only two catalog verifier types
  representing judgment), and this module additionally pre-checks it
  against the *target control's* real `catalog.json` `verification.modes`
  before ever attempting to build a run -- an unauthorized capability is
  rejected here, with a specific reason, not just silently caught one
  layer downstream.

## AI/LLM controls (SEC-074, SEC-075) keep the same constitution

A `PASS` for either control must not rest on "the model refused" or "the
prompt tells it not to." See `reviewer_normalizer.py`'s
`AI_CONSTITUTION_CONTROLS` handling for the explicit structural guard.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapters"))
import adapter_base  # noqa: E402  (reuse verify_identity/verify_target/build_runs/tool_error_runs/tool_unavailable_runs/NotAuthorized/ArtifactError)

ArtifactError = adapter_base.ArtifactError

ALLOWED_REVIEWER_VERIFIER_TYPES = {"SEMANTIC_REVIEW", "HUMAN"}
ALLOWED_RESULT_STATES = {"PASS", "FAIL", "NOT_APPLICABLE", "UNPROVEN", "ERROR"}
ALLOWED_SESSION_TYPES = {"fresh_read_only"}

# Generic reassurance phrases that, alone, can never substantiate a PASS
# or FAIL claim. Matched case-insensitively as substrings.
VAGUE_PHRASES = {
    "looks fine",
    "looks good",
    "looks safe",
    "looks okay",
    "seems fine",
    "seems safe",
    "seems secure",
    "seems okay",
    "appears fine",
    "appears safe",
    "appears secure",
    "no obvious issue",
    "no obvious issues",
    "no issues found",
    "nothing stood out",
    "should be fine",
    "should be safe",
    "probably safe",
    "probably fine",
    "generally secure",
}

MIN_REASONING_LENGTH = 80  # characters; a pragmatic, documented minimum bar for a PASS/FAIL claim

REQUIRED_ENVELOPE_FIELDS = {
    "reviewer",
    "execution",
    "target",
    "verifier_type",
    "control_id",
    "requirement",
    "files_inspected",
    "architecture_reasoning",
    "call_chain",
    "evidence_references",
    "unresolved_assumptions",
    "result",
    "result_reasoning",
    "artifact_binding",
}
BOUND_FIELDS = (
    "reviewer",
    "execution",
    "target",
    "verifier_type",
    "control_id",
    "requirement",
    "files_inspected",
    "architecture_reasoning",
    "call_chain",
    "evidence_references",
    "unresolved_assumptions",
    "result",
    "result_reasoning",
)


def canonical_artifact_hash(envelope: dict[str, Any]) -> str:
    bound = {key: envelope[key] for key in BOUND_FIELDS}
    canonical = json.dumps(bound, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_vague_only(text: str) -> bool:
    """True if, after stripping any sentence containing a vague phrase,
    essentially nothing substantive remains (fewer than MIN_REASONING_LENGTH
    characters of non-vague content)."""
    lowered = text.lower()
    remainder = lowered
    for phrase in VAGUE_PHRASES:
        remainder = remainder.replace(phrase, "")
    # Strip punctuation/whitespace noise left behind.
    remainder = "".join(ch for ch in remainder if ch.isalnum() or ch.isspace())
    return len(remainder.strip()) < MIN_REASONING_LENGTH


def load_review_envelope(raw: Any) -> dict[str, Any]:
    """Structural + integrity validation of a semantic-review artifact.
    Raises ArtifactError for anything that makes the artifact untrustworthy
    or improperly substantiated. Does NOT check the declared target against
    a caller expectation, or the control/requirement against the real
    catalog -- see reviewer_normalizer.py for those, which need the loaded
    catalog to check against."""
    if not isinstance(raw, dict):
        raise ArtifactError("artifact must be a JSON object")

    missing = REQUIRED_ENVELOPE_FIELDS - set(raw.keys())
    if missing:
        raise ArtifactError(f"artifact missing required field(s): {sorted(missing)}")

    reviewer = raw["reviewer"]
    if (
        not isinstance(reviewer, dict)
        or not isinstance(reviewer.get("session_id"), str)
        or not reviewer.get("session_id", "").strip()
        or reviewer.get("independent_from_implementation") is not True
        or reviewer.get("session_type") not in ALLOWED_SESSION_TYPES
    ):
        raise ArtifactError(
            "artifact.reviewer must be an object with a non-empty session_id, "
            "independent_from_implementation=true, and session_type='fresh_read_only'"
        )

    execution = raw["execution"]
    if (
        not isinstance(execution, dict)
        or execution.get("completed") is not True
        or execution.get("mutations_attempted") is not False
    ):
        raise ArtifactError(
            "artifact.execution must be an object with completed=true and "
            "mutations_attempted=false -- a reviewer that attempted any mutation "
            "cannot be trusted as a read-only review"
        )

    target = raw["target"]
    if (
        not isinstance(target, dict)
        or not isinstance(target.get("repository"), str)
        or not target.get("repository", "").strip()
        or not isinstance(target.get("commit"), str)
        or not target.get("commit", "").strip()
    ):
        raise ArtifactError("artifact.target must be an object with non-empty string repository and commit")

    verifier_type = raw["verifier_type"]
    if verifier_type not in ALLOWED_REVIEWER_VERIFIER_TYPES:
        raise ArtifactError(
            f"artifact.verifier_type must be one of {sorted(ALLOWED_REVIEWER_VERIFIER_TYPES)}, got {verifier_type!r}"
        )

    control_id = raw["control_id"]
    if not isinstance(control_id, str) or not control_id.strip():
        raise ArtifactError("artifact.control_id must be a non-empty string")

    requirement = raw["requirement"]
    if not isinstance(requirement, str) or not requirement.strip():
        raise ArtifactError("artifact.requirement must be a non-empty string")

    files_inspected = raw["files_inspected"]
    if (
        not isinstance(files_inspected, list)
        or not files_inspected
        or not all(isinstance(f, str) and f.strip() for f in files_inspected)
    ):
        raise ArtifactError("artifact.files_inspected must be a non-empty list of non-empty strings")

    architecture_reasoning = raw["architecture_reasoning"]
    if not isinstance(architecture_reasoning, str) or not architecture_reasoning.strip():
        raise ArtifactError("artifact.architecture_reasoning must be a non-empty string")

    call_chain = raw["call_chain"]
    if not isinstance(call_chain, str) or not call_chain.strip():
        raise ArtifactError("artifact.call_chain must be a non-empty string")

    evidence_references = raw["evidence_references"]
    if not isinstance(evidence_references, list):
        raise ArtifactError("artifact.evidence_references must be a list")
    files_set = set(files_inspected)
    for ref in evidence_references:
        if (
            not isinstance(ref, dict)
            or not isinstance(ref.get("file"), str)
            or not ref.get("file", "").strip()
            or not isinstance(ref.get("detail"), str)
            or not ref.get("detail", "").strip()
        ):
            raise ArtifactError("each evidence_references entry must be an object with non-empty string file and detail")
        if ref["file"] not in files_set:
            raise ArtifactError(
                f"evidence_references cites {ref['file']!r}, which is not in files_inspected -- "
                f"no fabricated source citations"
            )

    unresolved_assumptions = raw["unresolved_assumptions"]
    if not isinstance(unresolved_assumptions, list) or not all(isinstance(a, str) for a in unresolved_assumptions):
        raise ArtifactError("artifact.unresolved_assumptions must be a list of strings")

    result = raw["result"]
    if result not in ALLOWED_RESULT_STATES:
        raise ArtifactError(f"artifact.result must be one of {sorted(ALLOWED_RESULT_STATES)}, got {result!r}")

    result_reasoning = raw["result_reasoning"]
    if not isinstance(result_reasoning, str) or not result_reasoning.strip():
        raise ArtifactError("artifact.result_reasoning must be a non-empty string")

    if result in ("PASS", "FAIL"):
        combined = f"{architecture_reasoning} {result_reasoning}"
        if len(combined.strip()) < MIN_REASONING_LENGTH:
            raise ArtifactError(
                f"a {result} verdict requires at least {MIN_REASONING_LENGTH} characters of substantive "
                f"reasoning across architecture_reasoning + result_reasoning -- 'looks fine' is not evidence"
            )
        if _is_vague_only(combined):
            raise ArtifactError(
                f"a {result} verdict's reasoning consists only of generic reassurance phrases "
                f"({sorted(VAGUE_PHRASES)}) with no substantive content -- 'looks fine' is not evidence"
            )
        if not evidence_references:
            raise ArtifactError(f"a {result} verdict requires at least one evidence_references entry")
        if not any(f in combined for f in files_inspected):
            raise ArtifactError(
                f"a {result} verdict's reasoning must reference at least one of files_inspected by name"
            )

    artifact_binding = raw["artifact_binding"]
    if not isinstance(artifact_binding, dict) or not isinstance(artifact_binding.get("sha256"), str):
        raise ArtifactError("artifact.artifact_binding must be an object with a string sha256")

    actual_hash = canonical_artifact_hash(raw)
    if actual_hash != artifact_binding["sha256"]:
        raise ArtifactError(
            "artifact.artifact_binding.sha256 does not match the bound fields -- "
            "the artifact may have been modified or substituted after binding"
        )

    return raw
