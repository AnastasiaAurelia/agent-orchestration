#!/usr/bin/env python3
"""Diana Security GitHub-backed human-review normalizer (Security Track
remediation round C, Priority 3).

## Why this exists as a SEPARATE module, not an extension of reviewer_normalizer.py

`reviewer_normalizer.py`/`reviewer_base.py` (Phase 4) already normalize a
judgment artifact into evidence, but their envelope is shaped for an
AI/agent REVIEW SESSION specifically: `session_type` is a closed enum
containing exactly one value (`"fresh_read_only"`), and `files_inspected`/
`architecture_reasoning`/`call_chain` read as an agent's own structured
session log, not something a human clicking "Approve" on a GitHub PR
naturally produces. Round C investigated whether a real GitHub-backed
human review can become the trusted judgment source (preferred over
wiring an LLM) -- forcing that data into the AI-session-shaped contract
would mean either (a) requiring reviewers to hand-author an AI-review-
level structured document just to approve a control, which is not what
"GitHub-backed human review" means, or (b) silently relaxing that
contract's structural bar for a category of evidence it was never
designed to validate. Neither is honest. This module is a PARALLEL,
independently-scoped normalizer with its own appropriately human-
governance-shaped structural checks, following the exact same overall
architecture (parse an already-produced, verified artifact; never run
anything; reuse `adapter_base` directly) every other Phase 2-4 normalizer
already uses.

## What artifact this module expects

A REAL, already-fetched GitHub PR review (`gh api repos/<owner>/<repo>/
pulls/<n>/reviews`), translated into a small envelope by whatever trusted
script calls this module (see "Live-wiring status" below for why that
script does not exist yet). This module never calls the GitHub API
itself -- exactly as `adapter_base.py` never invokes Semgrep/Gitleaks,
and `reviewer_base.py` never spawns a reviewer session.

```jsonc
{
  "reviewer": {"login": "octocat", "review_id": 123456789},
  "target": {"repository": "owner/repo", "commit": "<40-hex commit sha>"},
  "control_id": "SEC-016",
  "requirement": "<verbatim catalog.json required_evidence string>",
  "judgment": "APPROVE",              // or "REQUEST_CHANGES" -- GitHub's
                                       // own real review states, nothing
                                       // else counts as a terminal
                                       // judgment (COMMENTED/DISMISSED/
                                       // PENDING never produce evidence)
  "rationale": "Reviewed diana/auth/hash.py:42 for SEC-016 -- bcrypt cost 12, per-user salt via bcrypt's own salt generation, no fast/reversible hash path found.",
  "independence": {
    "reviewer_is_pr_author": false,
    "reviewer_is_diana_agent": false
  },
  "submitted_at": "2026-09-08T12:00:00Z",
  "artifact_binding": {"sha256": "<sha256 of the canonical JSON of every field above except artifact_binding itself>"}
}
```

## Trust properties, mapped explicitly onto Round C's requirements

- **Reviewer identity comes from GitHub, never the PR body.** `reviewer.
  login`/`reviewer.review_id` are expected to be copied verbatim from a
  real `gh api .../reviews` response by the (not-yet-built, see below)
  live-wiring script -- never typed by a PR author into a PR-body
  evidence block, which this whole track has treated as untrusted input
  since Phase 5's `ci_verifier_runs.py` design.
- **Reviewer independence is a structural gate, not a self-declared
  courtesy field.** `independence.reviewer_is_pr_author` and
  `independence.reviewer_is_diana_agent` must BOTH be `false` or this
  module raises `ArtifactError` (-> an explicit `ERROR` run, never a
  silent downgrade) -- a non-independent "review" can never become
  authorization evidence, matching this round's explicit "no self-
  certification path" success criterion. The honesty of these two
  booleans rests on the same provenance guarantee every other artifact in
  this track rests on: they are computed by trusted, protected-base code
  from a real GitHub API response, not asserted by whoever produced the
  raw review.
- **Judgment is bound to the exact reviewed commit SHA, and staleness is
  handled by the SAME exact-match binding every other adapter already
  uses** -- `adapter_base.verify_identity()`/`verify_target()` compare
  `target.commit` against the caller's `expected_target.commit` (the
  commit currently being evaluated). A review submitted against an older
  commit (before a subsequent push) simply fails this exact match and
  contributes nothing -- no new staleness mechanism was invented; this is
  the identical mechanism Phase 2 built for scan artifacts, reused
  unchanged.
- **Evidence records control_id, reviewer identity, target commit SHA,
  judgment, rationale, and timestamp** -- every one of Round C's required
  fields is a required, structurally-checked envelope field below; none
  of them can be omitted without `load_review_envelope()` raising.
- **No PR-body self-certification.** This module never reads a PR body,
  a PR-body evidence block, or anything a PR author unilaterally
  controls -- only a review a GitHub-recognized OTHER account actually
  submitted through GitHub's own review mechanism.
- **Judgment is per-control, not a blanket PR approval.** `rationale`
  must literally name the `control_id` it is judging (checked below) --
  a generic "LGTM" approval of an entire PR, with no per-control content,
  structurally cannot become evidence for any specific catalog control.
- **Repository governance decides reviewer eligibility, deliberately left
  outside this module.** Whether a given GitHub login is "eligible" to
  produce trusted judgment (a CODEOWNER, a specific team, any non-author
  collaborator) is a governance/policy decision this module does not
  make -- it only enforces the two independence facts every design would
  need regardless (not the author, not the Diana worker account). A live-
  wiring script MAY apply a stricter eligibility filter (e.g. CODEOWNERS
  membership) before ever producing an artifact this module would accept;
  that stricter filter is a deployment/governance choice, not something
  a normalizer parsing an already-produced artifact should decide.

## Live-wiring status: capability-only, not yet wired into ci_verifier_runs.py

This module is built and tested exactly like every other Phase 2-4
normalizer was before it went live: capability-only first, wired into
`ci_verifier_runs.py` in a LATER round once verified safe. Unlike
Semgrep/Gitleaks/the deterministic-repo-scan/the one live dynamic
scenario (all installed/executed with zero `.github/workflows/*.yml`
changes), fetching real PR review data via `gh api` from inside
`diana-security-gate.yml` requires a `pull-requests: read` permission
that workflow's `permissions:` block does not currently grant (it
declares `contents: read` ONLY -- verified directly against the live
workflow file; GitHub Actions treats any explicit `permissions:` block as
authoritative, so an unlisted scope is `none`, not "default"). Adding
that scope is a `.github/workflows/diana-security-gate.yml` edit, which
this session's git/gh credential (`DIANA-AGENT`) cannot push (missing the
`workflow` OAuth scope -- the same constraint documented since Security
Phase 5). Live-wiring this module is therefore a precisely-scoped,
ready-to-implement follow-up requiring one human-pushed workflow-
permission change, not attempted or half-built this round.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapters"))
import adapter_base  # noqa: E402  (reuse verify_identity/verify_target/build_runs/tool_error_runs/tool_unavailable_runs/NotAuthorized/ArtifactError)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import validate_catalog  # noqa: E402  (reuse the Phase 0 validator)

ArtifactError = adapter_base.ArtifactError

CAPABILITY = "SEMANTIC_REVIEW"  # the catalog never defines a distinct "HUMAN"
# mode in practice (verified empirically: every judgment-requiring control's
# real verification.modes lists SEMANTIC_REVIEW, never HUMAN alone) -- this
# module's contributions must be typed SEMANTIC_REVIEW to pass
# evidence_model.py's per-control mode-permission check, exactly as
# reviewer_normalizer.py's own ALLOWED_REVIEWER_VERIFIER_TYPES already
# treats SEMANTIC_REVIEW/HUMAN as the same judgment-capability concept
# regardless of whether a model or a human produced the judgment.

ALLOWED_JUDGMENTS = {"APPROVE", "REQUEST_CHANGES"}  # GitHub's own real
# terminal review states this module accepts. COMMENTED/DISMISSED/PENDING
# are not a judgment at all and never produce a contribution.

MIN_RATIONALE_LENGTH = 40

VAGUE_RATIONALE_PHRASES = {
    "looks good",
    "looks fine",
    "lgtm",
    "seems fine",
    "seems ok",
    "no issues",
    "approved",
    "good to go",
}

REQUIRED_ENVELOPE_FIELDS = {
    "reviewer",
    "target",
    "control_id",
    "requirement",
    "judgment",
    "rationale",
    "independence",
    "submitted_at",
    "artifact_binding",
}
BOUND_FIELDS = (
    "reviewer", "target", "control_id", "requirement", "judgment",
    "rationale", "independence", "submitted_at",
)


def canonical_artifact_hash(envelope: dict[str, Any]) -> str:
    bound = {key: envelope[key] for key in BOUND_FIELDS}
    canonical = json.dumps(bound, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_review_envelope(raw: Any) -> dict[str, Any]:
    """Structural + integrity validation. Raises ArtifactError for
    anything that makes the artifact untrustworthy as a whole. Does not
    check the declared target against any expectation, catalog
    membership, or independence policy conclusions -- see ingest()."""
    if not isinstance(raw, dict):
        raise ArtifactError("artifact must be a JSON object")

    missing = REQUIRED_ENVELOPE_FIELDS - set(raw.keys())
    if missing:
        raise ArtifactError(f"artifact missing required field(s): {sorted(missing)}")
    unknown = set(raw.keys()) - REQUIRED_ENVELOPE_FIELDS
    if unknown:
        raise ArtifactError(f"artifact has unknown field(s): {sorted(unknown)}")

    reviewer = raw["reviewer"]
    if (
        not isinstance(reviewer, dict)
        or not isinstance(reviewer.get("login"), str)
        or not reviewer.get("login", "").strip()
        or not isinstance(reviewer.get("review_id"), int)
        or isinstance(reviewer.get("review_id"), bool)
        or reviewer.get("review_id") <= 0
    ):
        raise ArtifactError("artifact.reviewer must be an object with a non-empty string login and a positive integer review_id")

    target = raw["target"]
    if (
        not isinstance(target, dict)
        or not isinstance(target.get("repository"), str)
        or not target.get("repository", "").strip()
        or not isinstance(target.get("commit"), str)
        or not target.get("commit", "").strip()
    ):
        raise ArtifactError("artifact.target must be an object with non-empty string repository and commit")

    if not isinstance(raw["control_id"], str) or not raw["control_id"].strip():
        raise ArtifactError("artifact.control_id must be a non-empty string")

    if not isinstance(raw["requirement"], str) or not raw["requirement"].strip():
        raise ArtifactError("artifact.requirement must be a non-empty string")

    if raw["judgment"] not in ALLOWED_JUDGMENTS | {"COMMENTED", "DISMISSED", "PENDING"}:
        raise ArtifactError(
            f"artifact.judgment {raw['judgment']!r} is not a recognized GitHub review state"
        )

    rationale = raw["rationale"]
    if not isinstance(rationale, str) or not rationale.strip():
        raise ArtifactError("artifact.rationale must be a non-empty string")

    independence = raw["independence"]
    if (
        not isinstance(independence, dict)
        or not isinstance(independence.get("reviewer_is_pr_author"), bool)
        or not isinstance(independence.get("reviewer_is_diana_agent"), bool)
    ):
        raise ArtifactError(
            "artifact.independence must be an object with boolean reviewer_is_pr_author and reviewer_is_diana_agent"
        )

    if not isinstance(raw["submitted_at"], str) or not raw["submitted_at"].strip():
        raise ArtifactError("artifact.submitted_at must be a non-empty string")

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


def _rationale_substantiated(rationale: str, control_id: str) -> bool:
    """A human sign-off's rationale must actually engage with the SPECIFIC
    control it is judging, not be a blanket PR approval reused for every
    control. Requires: minimum length, the literal control_id string
    present, and not composed solely of generic reassurance phrases --
    the same "looks fine is not evidence" principle reviewer_base.py
    already established for AI-session reviews, adapted (lower bar,
    control-id citation instead of files_inspected citation) for a human
    governance sign-off rather than an architectural walkthrough."""
    if len(rationale.strip()) < MIN_RATIONALE_LENGTH:
        return False
    if control_id not in rationale:
        return False
    stripped = re.sub(r"[^a-z0-9\s]", "", rationale.lower()).strip()
    if stripped in VAGUE_RATIONALE_PHRASES:
        return False
    return True


def _load_catalog_controls(catalog_path: str) -> dict[str, dict[str, Any]]:
    with open(catalog_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    errors = validate_catalog.validate(catalog)
    if errors:
        raise ArtifactError(f"catalog is not structurally valid: {errors}")
    return {c["id"]: c for c in catalog["controls"]}


def _authorized_control_ids(catalog_controls: dict[str, dict[str, Any]]) -> set[str]:
    """Same catalog-derived authorization reviewer_normalizer.py already
    uses (any control whose real verification.modes includes
    SEMANTIC_REVIEW or HUMAN) -- recomputed independently from the same
    catalog field, not imported, so this module has no hidden coupling to
    reviewer_normalizer.py's internals and cannot silently drift if that
    module's own computation ever changes."""
    return {
        control["id"]
        for control in catalog_controls.values()
        if set(control["verification"]["modes"]) & {"SEMANTIC_REVIEW", "HUMAN"}
    }


def _capability_for_control(catalog_controls: dict[str, dict[str, Any]], control_id: str) -> str:
    modes = set(catalog_controls[control_id]["verification"]["modes"]) & {"SEMANTIC_REVIEW", "HUMAN"}
    return sorted(modes)[0]


def ingest(
    catalog_controls: dict[str, dict[str, Any]],
    artifact_path: str | None,
    control_ids: list[str],
    identity: str,
    expected_target: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Returns evidence_model.py run records for the requested control_ids
    this module is catalog-authorized for. Controls the catalog doesn't
    permit a judgment capability for are silently skipped."""
    authorized_ids = _authorized_control_ids(catalog_controls)
    requested_authorized = [c for c in control_ids if c in authorized_ids]
    if not requested_authorized:
        return []

    if artifact_path is None or not Path(artifact_path).exists():
        runs: list[dict[str, Any]] = []
        for control_id in requested_authorized:
            capability = _capability_for_control(catalog_controls, control_id)
            runs.extend(
                adapter_base.tool_unavailable_runs([control_id], capability, identity, "no review artifact provided")
            )
        return runs

    try:
        with open(artifact_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        envelope = load_review_envelope(raw)

        control_id = envelope["control_id"]
        if control_id not in requested_authorized:
            return []

        control = catalog_controls.get(control_id)
        if control is None:
            raise ArtifactError(f"unknown control_id {control_id!r} -- not in the catalog")

        if envelope["requirement"] not in control["required_evidence"]:
            raise ArtifactError(
                f"artifact.requirement {envelope['requirement']!r} does not match any of "
                f"{control_id}'s real catalog required_evidence strings"
            )

        allowed_modes = set(control["verification"]["modes"])
        if CAPABILITY not in allowed_modes:
            raise ArtifactError(
                f"{control_id}'s catalog verification.modes ({sorted(allowed_modes)}) does not permit "
                f"{CAPABILITY} -- this control cannot be reviewed via judgment capability"
            )

        independence = envelope["independence"]
        if independence["reviewer_is_pr_author"] or independence["reviewer_is_diana_agent"]:
            raise ArtifactError(
                "artifact.independence declares a non-independent reviewer (the PR author or the "
                "Diana worker account itself) -- a review is never trusted as authorization evidence "
                "unless it comes from a genuinely independent GitHub identity"
            )
    except (OSError, json.JSONDecodeError, ArtifactError) as exc:
        runs = []
        for control_id in requested_authorized:
            capability = _capability_for_control(catalog_controls, control_id)
            runs.extend(
                adapter_base.tool_error_runs(
                    [control_id], capability, identity, f"could not verify GitHub-review artifact: {exc}"
                )
            )
        return runs

    control_id = envelope["control_id"]
    requirement = envelope["requirement"]
    judgment = envelope["judgment"]

    if judgment not in ALLOWED_JUDGMENTS:
        # COMMENTED/DISMISSED/PENDING: not a terminal judgment at all --
        # no contribution, but the control still gets an explicit run
        # (never silently absent), matching reviewer_normalizer.py's
        # "not attributed" pattern.
        return adapter_base.build_runs([], {control_id: [requirement]}, CAPABILITY, identity, [control_id])

    rationale = envelope["rationale"]
    if not _rationale_substantiated(rationale, control_id):
        # Structurally present but unsubstantiated ("LGTM"-shaped, or not
        # naming the control it claims to judge) -- explicit empty-
        # evidence run, deterministically UNPROVEN, never treated as a
        # tool error (the artifact itself is well-formed; its CONTENT
        # just doesn't clear the substantiation bar).
        return adapter_base.build_runs([], {control_id: [requirement]}, CAPABILITY, identity, [control_id])

    if judgment == "REQUEST_CHANGES":
        # A real, observed "this does not satisfy the control" claim is
        # trusted from partial coverage (identity only), same asymmetry
        # every adapter in this track already applies.
        identity_verified, _reason = adapter_base.verify_identity(envelope["target"], expected_target)
        if not identity_verified:
            return adapter_base.build_runs([], {control_id: [requirement]}, CAPABILITY, identity, [control_id])
        contributions = [
            (
                control_id,
                requirement,
                "VIOLATED",
                f"GitHub review by @{envelope['reviewer']['login']} (review id {envelope['reviewer']['review_id']}) "
                f"requested changes: {rationale}",
                None,
            )
        ]
    else:
        # APPROVE -> a clean result needs the FULL target verification
        # (identity plus every other caller-supplied expectation, e.g.
        # repository), not just identity -- same asymmetry as everywhere
        # else in this track.
        target_verified, _reason = adapter_base.verify_target(envelope["target"], expected_target)
        if not target_verified:
            return adapter_base.build_runs([], {control_id: [requirement]}, CAPABILITY, identity, [control_id])
        contributions = [
            (
                control_id,
                requirement,
                "SATISFIED",
                f"GitHub review by @{envelope['reviewer']['login']} (review id {envelope['reviewer']['review_id']}) "
                f"approved: {rationale}",
                None,
            )
        ]

    try:
        return adapter_base.build_runs(contributions, {control_id: [requirement]}, CAPABILITY, identity, [control_id])
    except adapter_base.NotAuthorized as exc:
        return adapter_base.tool_error_runs([control_id], CAPABILITY, identity, str(exc))


def main(argv: list[str]) -> int:
    if len(argv) < 5:
        print(
            json.dumps(
                {
                    "version": 1,
                    "error": "usage: github_review_adapter.py <catalog.json> <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]",
                },
                sort_keys=True,
            )
        )
        return 1

    catalog_path, artifact_arg, expected_arg = argv[1], argv[2], argv[3]
    control_ids = argv[4:]
    artifact_path = None if artifact_arg == "-" else artifact_arg
    identity = f"github-review::{artifact_arg}"

    try:
        catalog_controls = _load_catalog_controls(catalog_path)
    except (OSError, json.JSONDecodeError, ArtifactError) as exc:
        print(json.dumps({"version": 1, "error": f"could not load catalog: {exc}"}, sort_keys=True))
        return 1

    expected_target = None
    if expected_arg != "-":
        with open(expected_arg, "r", encoding="utf-8") as f:
            expected_target = json.load(f)

    runs = ingest(catalog_controls, artifact_path, control_ids, identity, expected_target)
    print(json.dumps({"version": 1, "runs": runs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
