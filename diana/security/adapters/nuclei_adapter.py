#!/usr/bin/env python3
"""Diana Security static adapter: curated Nuclei verifier (Security
Phase 7.4).

Normalizes a **verified scan-evidence artifact** wrapping a saved/real
Nuclei JSON(L) report into evidence_model.py run records. Never installs,
invokes, or bundles Nuclei, never issues an HTTP request, and never
chooses templates or a scan target -- this module only parses an
already-produced artifact, exactly like `zap_adapter.py`/
`trivy_adapter.py`. Live orchestration lives entirely in
`ci_verifier_runs.py`.

## Curated templates only -- never `-u <url> -t <template>` unrestricted

Diana never exposes "run any Nuclei template against any URL." Every
template ID this adapter is willing to treat as trusted evidence is
listed, individually, with its own authorized `(control_id, requirement)`
binding, in `diana/security/nuclei/allowed_templates.json` -- a file that
lives in the protected base, the same trust root as
`verifiers/semgrep-rules.yml`/`verifiers/gitleaks-config.toml`. **A PR
cannot add a template entry to that file and have the SAME PR's evidence
trusted against it** -- `diana-security-gate.yml` runs under
`pull_request_target` with a base-rooted checkout (see
`ci_verifier_runs.py`'s module docstring), so this adapter only ever
reads the protected base's own committed allowlist, never anything a PR
head supplied.

A Nuclei finding whose `template-id` is NOT in the allowlist is silently
dropped -- it is never evidence for anything, regardless of what the
native report otherwise contains. This is enforced independently of
(and in addition to) the usual `AUTHORIZED_EVIDENCE` control/requirement
check every adapter already has.

## Scope: exactly SEC-064's dynamic half, no more

Today's allowlist only ever authorizes `SEC-064` (Exposed .env, Git,
Backup, or Config Files) -- the same control the `sensitive-file-paths-
not-fetchable` dynamic scenario and `deterministic_repo_adapter.py`
already contribute to. Nuclei contributing the SAME control via the SAME
capability (`DYNAMIC_API`) as a genuinely independent, third detection
mechanism is intentional (mirrors Trivy adding a second independent
`DEPENDENCY_SCANNER` contributor to `SEC-060` alongside osv-scanner) --
it does not change SEC-064's coverage classification, it only gives
`evidence_model.py` an additional trusted source for the same, already-
authorized claim.

## Target policy mirrors zap_adapter.py exactly

`environment` must be `"LOCAL"` and `target.base_url` must resolve to a
loopback host -- re-validated here independently of the orchestrator,
never trusted blindly. See `zap_adapter.py`'s module docstring for the
full rationale; this module intentionally duplicates that specific check
(not import-shares it) because a template-allowlist adapter and a
plugin-ID adapter are different enough tools that coupling their target
validation to one shared function would obscure, not simplify, either
one's authorization story.

## Native report shape

Nuclei's native `-jsonl` output is one JSON object per matched line, not
a single JSON document. This adapter therefore does not parse Nuclei's
raw stdout directly (`ci_verifier_runs.py`'s orchestration layer is
responsible for collecting those lines into a list) -- it expects
`report` to already be a JSON list of objects shaped like Nuclei's real
finding object: `{"template-id": "...", "info": {"severity": "..."},
"host": "<base_url>", "matched-at": "<url>"}`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_base  # noqa: E402

CAPABILITY = "DYNAMIC_API"
TOOL_NAME = "nuclei"

ALLOWED_ENVIRONMENTS = {"LOCAL"}
ALLOWED_LOCAL_HOSTS = {"127.0.0.1", "localhost"}

# The pinned, protected-base template allowlist -- read once at import
# time (never re-fetched, never network-sourced). A malformed or missing
# allowlist file degrades this adapter to authorizing NOTHING (fail
# closed), never a crash and never an implicit "trust everything".
_ALLOWLIST_PATH = Path(__file__).resolve().parent.parent / "nuclei" / "allowed_templates.json"
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # repo root


def _template_digest_matches(template_path: str, expected_digest: str) -> bool:
    """Recomputes the sha256 of the actual committed template file on
    disk and compares it against the allowlist's own declared digest --
    a template file modified without updating its digest (or vice versa)
    is refused, never silently trusted. Any I/O failure fails closed
    (treated as a mismatch, never treated as a pass)."""
    if not expected_digest.startswith("sha256:"):
        return False
    try:
        content = (_REPO_ROOT / template_path).read_bytes()
    except OSError:
        return False
    import hashlib

    actual = "sha256:" + hashlib.sha256(content).hexdigest()
    return actual == expected_digest


def _load_allowlist() -> dict[str, dict[str, Any]]:
    try:
        with open(_ALLOWLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        entries = data["templates"]
        by_id: dict[str, dict[str, Any]] = {}
        for entry in entries:
            template_id = entry["template_id"]
            template_path = entry["template_path"]
            template_digest = entry["template_digest"]
            if not _template_digest_matches(template_path, template_digest):
                # Fail closed: a template whose on-disk content doesn't
                # match its pinned digest is never authorized, regardless
                # of what the allowlist's other fields claim about it.
                continue
            by_id[template_id] = {
                "control_id": entry["control_id"],
                "requirement": entry["requirement"],
                "allowed_severity": set(entry["allowed_severity"]),
                "template_digest": template_digest,
                "template_path": template_path,
            }
        return by_id
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return {}


ALLOWED_TEMPLATES = _load_allowlist()

# Derived from the allowlist -- never hand-duplicated, so it can never
# silently drift from the actual curated entries.
AUTHORIZED_EVIDENCE: dict[str, list[str]] = {}
for _entry in ALLOWED_TEMPLATES.values():
    AUTHORIZED_EVIDENCE.setdefault(_entry["control_id"], [])
    if _entry["requirement"] not in AUTHORIZED_EVIDENCE[_entry["control_id"]]:
        AUTHORIZED_EVIDENCE[_entry["control_id"]].append(_entry["requirement"])


class ReportParseError(ValueError):
    """The wrapped report is not a valid, complete Nuclei native report."""


def _validate_local_target(target: dict[str, Any]) -> None:
    environment = target.get("environment")
    if environment not in ALLOWED_ENVIRONMENTS:
        raise adapter_base.ArtifactError(
            f"artifact.target.environment {environment!r} is not an allowed Nuclei target environment "
            f"({sorted(ALLOWED_ENVIRONMENTS)}) -- production/staging scanning is not supported"
        )
    base_url = target.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        raise adapter_base.ArtifactError("artifact.target.base_url must be a non-empty string")
    host = urlparse(base_url).hostname
    if host not in ALLOWED_LOCAL_HOSTS:
        raise adapter_base.ArtifactError(
            f"artifact.target.base_url host {host!r} is not an allowed local host "
            f"({sorted(ALLOWED_LOCAL_HOSTS)}) -- refused, never a remote/production target"
        )


def _iter_findings(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ReportParseError("expected report to be a JSON list of Nuclei finding objects")
    findings = []
    for f in raw:
        if not isinstance(f, dict):
            raise ReportParseError("each finding must be an object")
        template_id = f.get("template-id")
        if not isinstance(template_id, str) or not template_id:
            raise ReportParseError("each finding must have a non-empty string 'template-id'")
        info = f.get("info", {})
        severity = info.get("severity") if isinstance(info, dict) else None
        if not isinstance(severity, str) or not severity:
            raise ReportParseError(f"finding for template {template_id!r} must have info.severity")
        findings.append({"template_id": template_id, "severity": severity, "host": f.get("host")})
    return findings


def ingest(
    artifact_path: str | None,
    control_ids: list[str],
    identity: str,
    expected_target: dict[str, Any] | None,
) -> list[dict[str, Any]]:
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
        _validate_local_target(envelope["target"])
        findings = _iter_findings(envelope["report"])
    except (OSError, json.JSONDecodeError, adapter_base.ArtifactError, ReportParseError) as exc:
        return adapter_base.tool_error_runs(
            requested_authorized, CAPABILITY, identity, f"could not verify Nuclei artifact: {exc}"
        )

    target_verified, _reason = adapter_base.verify_target(envelope["target"], expected_target)

    # Config coverage: the exact set of allowlisted template IDs this run
    # was actually configured to execute (never inferred, only what the
    # caller declared in config.template_ids) -- mirrors osv-scanner's/
    # Trivy's "expected manifests <= scanned_inputs" coverage check.
    configured_templates = set(envelope.get("config", {}).get("template_ids", []))

    contributions: list[tuple[str, str, str, str, str | None]] = []
    for control_id in requested_authorized:
        requirement = AUTHORIZED_EVIDENCE[control_id][0]
        relevant_template_ids = {
            tid
            for tid, spec in ALLOWED_TEMPLATES.items()
            if spec["control_id"] == control_id and spec["requirement"] == requirement
        }
        # A finding only counts if its template is both in the global
        # allowlist AND was actually one of the templates this run
        # declared it was configured to execute for this requirement --
        # double-checked, never assumed from the report alone.
        hits = sorted(
            f["template_id"]
            for f in findings
            if f["template_id"] in relevant_template_ids and f["template_id"] in configured_templates
        )
        coverage_complete = relevant_template_ids <= configured_templates
        if hits:
            if target_verified:
                contributions.append(
                    (
                        control_id,
                        requirement,
                        "VIOLATED",
                        f"nuclei curated template(s) {hits} matched against the verified target",
                        None,
                    )
                )
            # else: finding present, but target could not be verified.
        elif target_verified and coverage_complete:
            contributions.append(
                (
                    control_id,
                    requirement,
                    "SATISFIED",
                    (
                        f"nuclei curated template(s) {sorted(relevant_template_ids)} ran against the verified "
                        f"target with no match"
                    ),
                    None,
                )
            )
        # else: clean, but target/coverage could not be verified as complete.

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
                    "error": "usage: nuclei_adapter.py <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]",
                },
                sort_keys=True,
            )
        )
        return 1

    artifact_arg, expected_arg = argv[1], argv[2]
    control_ids = argv[3:]
    artifact_path = None if artifact_arg == "-" else artifact_arg
    identity = f"nuclei::{artifact_arg}"

    expected_target = None
    if expected_arg != "-":
        with open(expected_arg, "r", encoding="utf-8") as f:
            expected_target = json.load(f)

    runs = ingest(artifact_path, control_ids, identity, expected_target)
    print(json.dumps({"version": 1, "runs": runs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
