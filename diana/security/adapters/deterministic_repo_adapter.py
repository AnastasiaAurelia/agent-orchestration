#!/usr/bin/env python3
"""Diana Security static adapter: deterministic repo/deployment-tree scan
(Security Track remediation round A).

Normalizes a **verified scan-evidence artifact** (see `adapter_base`
module docstring) wrapping a caller-supplied listing of paths that WOULD
be publicly served from a deployment's web root into `evidence_model.py`
run records. Never installs, invokes, or bundles anything -- this module
only parses an already-produced artifact, exactly like every other
Phase 2 adapter.

Unlike Gitleaks/Semgrep, there is no third-party tool here: `DETERMINISTIC_
REPO` (catalog mode) names a check Diana itself can settle by simple,
unambiguous file-path membership -- either a sensitive path is present in
the served listing, or it isn't. There is no pattern-matching ambiguity
(unlike a secret scanner's regex heuristics or a static analyzer's
code-shape heuristics), which is exactly why this is safe to implement as
a genuinely deterministic check rather than a fuzzy one.

## Mapped control

`SEC-064` Exposed `.env`, Git, Backup, or Config Files -- the static
("deployment excludes `.env`, `.git`, backup, and config files from the
public web root") half only; `dynamic_required=True`, so a companion
`DYNAMIC_API` contribution (a real negative-fetch test) is still required
to reach PASS -- see `diana/security/dynamic/scenarios.py`.

## Sensitive path patterns (deliberately narrow, documented)

`SENSITIVE_PATH_PREFIXES`/`SENSITIVE_PATH_SUFFIXES` below are a fixed,
conservative allowlist-of-badness, not a general heuristic: exact,
well-known sensitive path shapes only (`.env*`, `.git`/`.git/*`, common
database-dump/backup suffixes). This deliberately does NOT flag generic
archive extensions (`.zip`, `.tar.gz`) on their own, since those are
routinely served legitimately (downloads, assets) and a blanket match
would produce false positives this adapter cannot safely resolve --
narrower and occasionally under-inclusive is preferred over broad and
wrong, consistent with "implement only evidence paths that can be
reproduced deterministically."

## Artifact shape

See `adapter_base` for the envelope shape. `tool.name` must be
`"diana-deterministic-repo-scan"` (there is no third-party tool being
wrapped here -- this names Diana's own internal deterministic check, not
a fabricated third-party identity). `report` must be
`{"served_paths": [...]}`, a flat list of every path the artifact
producer asserts would be publicly reachable from the deployment's web
root.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_base  # noqa: E402

CAPABILITY = "DETERMINISTIC_REPO"
TOOL_NAME = "diana-deterministic-repo-scan"

SEC064_REQ_NO_SENSITIVE_FILES = "deployment excludes .env, .git, backup, and config files from the public web root"

AUTHORIZED_EVIDENCE = {
    "SEC-064": [SEC064_REQ_NO_SENSITIVE_FILES],
}

SENSITIVE_PATH_PREFIXES = (".env", ".git")
SENSITIVE_PATH_SUFFIXES = (".sql", ".sql.gz", ".dump", ".bak", ".backup")


class ReportParseError(ValueError):
    """The wrapped report is not a valid deterministic-repo-scan report."""


def parse_report(raw: Any) -> list[str]:
    if not isinstance(raw, dict) or "served_paths" not in raw:
        raise ReportParseError("report must be an object with a served_paths field")
    served_paths = raw["served_paths"]
    if not isinstance(served_paths, list) or not all(isinstance(p, str) for p in served_paths):
        raise ReportParseError("served_paths must be a list of strings")
    return served_paths


def _is_sensitive(path: str) -> bool:
    normalized = path.lstrip("/")
    for prefix in SENSITIVE_PATH_PREFIXES:
        if normalized == prefix or normalized.startswith(prefix + "/") or normalized.startswith(prefix + "."):
            return True
    for suffix in SENSITIVE_PATH_SUFFIXES:
        if normalized.endswith(suffix):
            return True
    return False


def ingest(
    artifact_path: str | None,
    control_ids: list[str],
    identity: str,
    expected_target: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Returns evidence_model.py run records for the requested control_ids
    this adapter is authorized for. Controls this adapter is not
    authorized for are silently skipped (never fabricated evidence)."""
    requested_authorized = [c for c in control_ids if c in AUTHORIZED_EVIDENCE]
    if not requested_authorized:
        return []

    if artifact_path is None or not Path(artifact_path).exists():
        return adapter_base.tool_unavailable_runs(
            requested_authorized, CAPABILITY, identity, "no artifact provided"
        )

    try:
        with open(artifact_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        envelope = adapter_base.load_envelope(raw)
        adapter_base.verify_tool_identity(envelope["tool"], TOOL_NAME)
        served_paths = parse_report(envelope["report"])
    except (OSError, json.JSONDecodeError, adapter_base.ArtifactError, ReportParseError) as exc:
        return adapter_base.tool_error_runs(
            requested_authorized, CAPABILITY, identity, f"could not verify deterministic-repo-scan artifact: {exc}"
        )

    identity_verified, _identity_reason = adapter_base.verify_identity(envelope["target"], expected_target)
    target_verified, _target_reason = adapter_base.verify_target(envelope["target"], expected_target)

    sensitive_hits = [p for p in served_paths if _is_sensitive(p)]

    contributions = []
    if "SEC-064" in requested_authorized:
        if sensitive_hits:
            if identity_verified:
                for path in sensitive_hits:
                    contributions.append(
                        (
                            "SEC-064",
                            SEC064_REQ_NO_SENSITIVE_FILES,
                            "VIOLATED",
                            f"deterministic-repo-scan finding: sensitive path publicly served: {path}",
                            None,
                        )
                    )
            # else: hit(s) present, but target identity could not be
            # verified against the caller's expectation -- not attributed,
            # no contribution.
        elif target_verified:
            contributions.append(
                (
                    "SEC-064",
                    SEC064_REQ_NO_SENSITIVE_FILES,
                    "SATISFIED",
                    (
                        f"deterministic-repo-scan found zero sensitive paths among "
                        f"{len(served_paths)} served path(s); target verified "
                        f"(repository={envelope['target'].get('repository')!r}, "
                        f"commit={envelope['target'].get('commit')!r})"
                    ),
                    None,
                )
            )
        # else: clean result, but target could not be verified -- no
        # contribution at all.

    try:
        return adapter_base.build_runs(contributions, AUTHORIZED_EVIDENCE, CAPABILITY, identity, requested_authorized)
    except adapter_base.NotAuthorized as exc:
        return adapter_base.tool_error_runs(requested_authorized, CAPABILITY, identity, str(exc))


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(
            json.dumps(
                {
                    "version": 1,
                    "error": "usage: deterministic_repo_adapter.py <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]",
                },
                sort_keys=True,
            )
        )
        return 1

    artifact_arg, expected_arg = argv[1], argv[2]
    control_ids = argv[3:]
    artifact_path = None if artifact_arg == "-" else artifact_arg
    identity = f"deterministic-repo-scan::{artifact_arg}"

    expected_target = None
    if expected_arg != "-":
        with open(expected_arg, "r", encoding="utf-8") as f:
            expected_target = json.load(f)

    runs = ingest(artifact_path, control_ids, identity, expected_target)
    print(json.dumps({"version": 1, "runs": runs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
