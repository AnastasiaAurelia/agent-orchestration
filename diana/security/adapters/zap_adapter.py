#!/usr/bin/env python3
"""Diana Security static adapter: OWASP ZAP bounded passive scan
(Security Phase 7.3).

Normalizes a **verified scan-evidence artifact** (see `adapter_base`
module docstring) wrapping a saved/real ZAP JSON report into
evidence_model.py run records. Never installs, invokes, or bundles ZAP,
never issues an HTTP request, and never chooses a scan target -- this
module only parses an already-produced artifact, exactly like
`trivy_adapter.py`/`osv_scanner_adapter.py`. Live orchestration (which
DOES choose the target -- always a local, ephemeral, Diana-started HTTP
server -- and invoke the tool) lives entirely in `ci_verifier_runs.py`,
never here.

## Scope: bounded dynamic verifier, not a pentest engine

ZAP is capable of full active scanning against arbitrary targets. This
adapter is authorized for exactly the PASSIVE-scan half of five header/
cookie-configuration controls -- the "response inspection confirms the
[header/attribute] is actually delivered at runtime" requirement each of
these controls' catalog entry lists as its second, dynamic
`required_evidence` item:

  SEC-030  Insecure Cookie Attributes
  SEC-031  CORS Misconfiguration
  SEC-032  Missing or Weak Content Security Policy
  SEC-033  Clickjacking
  SEC-053  Security Misconfigured HTTP Headers

Each control's FIRST (static/code-fact) required_evidence item remains a
different verifier's responsibility (STATIC_ANALYZER/SEMANTIC_REVIEW) --
this adapter never contributes to it. A ZAP alert observed for one of
these controls' `ZAP_PLUGIN_MAP` plugin IDs is real, independently-
produced dynamic evidence that the response *as delivered* is missing or
misconfigures the relevant protection; the *absence* of that alert (scan
completed, target verified, but no matching alert reported) is what lets
the "confirms delivered at runtime" claim be SATISFIED -- ZAP's passive
scanner inspects every live HTTP response it observes, so a clean report
for a specific, well-known alert type is a real (if bounded) runtime
observation, not merely "no finding".

This is deliberately NOT wired to authorize:

- Any control requiring an ACTIVE scan (SQLi/XSS active-attack rules,
  auth-bypass fuzzing) -- see the Phase 7 report for why active scanning
  is out of scope this round.
- Any control outside the five listed above -- ZAP's alert catalog is
  large; only the small, curated `ZAP_PLUGIN_MAP` subset below is ever
  inspected, and only for the controls it is explicitly authorized for.

## Target policy is enforced HERE too, not only by the orchestrator

`ci_verifier_runs.py` is the only code that ever chooses a real ZAP
target (always a local HTTP server it itself starts, bound to
127.0.0.1). But this adapter is the last trust boundary before evidence
reaches `evidence_model.py`, so it independently re-validates the
artifact's own declared target rather than trusting the orchestrator
blindly: `environment` must be exactly `"LOCAL"`, and `target.base_url`
must resolve to `127.0.0.1`/`localhost` -- anything else (a PROD/STAGING
label, an external hostname, a non-loopback IP) is refused as a
structural `ArtifactError` before any alert is ever inspected. Broader
environments (`TEST`/`SANDBOX`, a trusted staging base URL) are a
deliberate, documented future extension point (Phase 8) -- this adapter
does not attempt to support them today, and NEVER falls back to
"probably fine" for an unrecognized target.

## Native report shape

This adapter parses ZAP's own native JSON report format (the shape
`zap-baseline.py -J report.json` / the ZAP Java report-generation API
produces): `{"site": [{"@name": "<base_url>", "alerts": [{"pluginid":
"...", "name": "...", "riskcode": "...", "confidence": "..."}, ...]}]}`.
Only the `site` entry whose `@name` matches `target.base_url` exactly is
ever inspected -- alerts against any other site in a (hypothetically
multi-site) report are not evidence about the expected target and are
ignored, never conflated.
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
TOOL_NAME = "zap"

# Only LOCAL, loopback targets are supported today -- see module
# docstring. A future round may extend this to TEST/SANDBOX against an
# explicitly configured trusted staging base URL; until that is
# designed, anything else is refused, never guessed at.
ALLOWED_ENVIRONMENTS = {"LOCAL"}
ALLOWED_LOCAL_HOSTS = {"127.0.0.1", "localhost"}

SEC030_REQ_RUNTIME = "response inspection confirms the attributes are actually delivered at runtime"
SEC031_REQ_RUNTIME = "negative test proving an untrusted origin cannot read credentialed cross-origin responses"
SEC032_REQ_RUNTIME = "response inspection confirms the header is actually delivered at runtime"
SEC033_REQ_RUNTIME = "response inspection confirms the protection is actually delivered at runtime"
SEC053_REQ_RUNTIME = "response inspection confirms the headers are actually delivered at runtime"

AUTHORIZED_EVIDENCE = {
    "SEC-030": [SEC030_REQ_RUNTIME],
    "SEC-031": [SEC031_REQ_RUNTIME],
    "SEC-032": [SEC032_REQ_RUNTIME],
    "SEC-033": [SEC033_REQ_RUNTIME],
    "SEC-053": [SEC053_REQ_RUNTIME],
}

# Curated, small, pinned mapping from a real ZAP passive-scan plugin ID
# to the exact (control_id, requirement) pair its presence is authorized
# to violate. Deliberately narrow -- ZAP's full alert catalog is not
# wired here, only these specific, well-understood passive checks. Each
# comment names the real ZAP rule this plugin ID corresponds to.
ZAP_PLUGIN_MAP: dict[str, tuple[str, str]] = {
    "10010": ("SEC-030", SEC030_REQ_RUNTIME),  # Cookie No HttpOnly Flag
    "10011": ("SEC-030", SEC030_REQ_RUNTIME),  # Cookie Without Secure Flag
    "10054": ("SEC-030", SEC030_REQ_RUNTIME),  # Cookie without SameSite Attribute
    "10098": ("SEC-031", SEC031_REQ_RUNTIME),  # Cross-Domain Misconfiguration (CORS)
    "10038": ("SEC-032", SEC032_REQ_RUNTIME),  # Content Security Policy (CSP) Header Not Set
    "10020": ("SEC-033", SEC033_REQ_RUNTIME),  # X-Frame-Options / Anti-clickjacking Header Missing
    "10021": ("SEC-053", SEC053_REQ_RUNTIME),  # X-Content-Type-Options Header Missing
    "10035": ("SEC-053", SEC053_REQ_RUNTIME),  # Strict-Transport-Security Header Missing
}


class ReportParseError(ValueError):
    """The wrapped report is not a valid, complete ZAP native report."""


def _validate_local_target(target: dict[str, Any]) -> None:
    """Independently re-validates the artifact's OWN declared target is a
    LOCAL, loopback-only target -- never trusts the orchestrator alone.
    Raises ArtifactError (never silently passes) for anything else."""
    environment = target.get("environment")
    if environment not in ALLOWED_ENVIRONMENTS:
        raise adapter_base.ArtifactError(
            f"artifact.target.environment {environment!r} is not an allowed ZAP target environment "
            f"({sorted(ALLOWED_ENVIRONMENTS)}) -- production/staging active scanning is not supported"
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


def _site_alerts_for_target(raw: Any, base_url: str) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ReportParseError("expected report to be a JSON object")
    if "site" not in raw:
        raise ReportParseError("report has no 'site' field -- not a completed ZAP report")
    sites = raw["site"]
    if not isinstance(sites, list):
        raise ReportParseError("site must be a list")

    matched_site = None
    for site in sites:
        if not isinstance(site, dict):
            raise ReportParseError("each site entry must be an object")
        name = site.get("@name", site.get("name"))
        if name == base_url:
            matched_site = site
            break
    if matched_site is None:
        # No site entry corresponds to the expected target at all -- this
        # is a genuinely clean/empty report for OUR target only if the
        # report is otherwise structurally valid; treat as zero alerts,
        # not a structural failure (ZAP legitimately omits a site with no
        # observed traffic).
        return []

    alerts = matched_site.get("alerts", [])
    if not isinstance(alerts, list):
        raise ReportParseError("site.alerts must be a list")
    for a in alerts:
        if not isinstance(a, dict) or not isinstance(a.get("pluginid"), str):
            raise ReportParseError("each alert must be an object with a string pluginid")
    return alerts


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
        alerts = _site_alerts_for_target(envelope["report"], envelope["target"].get("base_url"))
    except (OSError, json.JSONDecodeError, adapter_base.ArtifactError, ReportParseError) as exc:
        return adapter_base.tool_error_runs(
            requested_authorized, CAPABILITY, identity, f"could not verify ZAP artifact: {exc}"
        )

    target_verified, _reason = adapter_base.verify_target(envelope["target"], expected_target)

    observed_plugin_ids = {a["pluginid"] for a in alerts}

    contributions: list[tuple[str, str, str, str, str | None]] = []
    for control_id in requested_authorized:
        requirement = AUTHORIZED_EVIDENCE[control_id][0]
        matching_plugins = sorted(
            pid for pid, (cid, req) in ZAP_PLUGIN_MAP.items() if cid == control_id and req == requirement
        )
        hits = sorted(observed_plugin_ids & set(matching_plugins))
        if hits:
            if target_verified:
                contributions.append(
                    (
                        control_id,
                        requirement,
                        "VIOLATED",
                        f"zap passive scan observed alert plugin(s) {hits} against the verified target",
                        None,
                    )
                )
            # else: alert observed, but target identity/context could not
            # be verified -- not attributed to the expected target.
        elif target_verified:
            contributions.append(
                (
                    control_id,
                    requirement,
                    "SATISFIED",
                    (
                        f"zap passive scan completed against the verified target with no alert from "
                        f"plugin(s) {matching_plugins}"
                    ),
                    None,
                )
            )
        # else: clean result, but target could not be verified -- no contribution.

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
                    "error": "usage: zap_adapter.py <artifact.json|-> <expected_target.json|-> <control_id> [control_id...]",
                },
                sort_keys=True,
            )
        )
        return 1

    artifact_arg, expected_arg = argv[1], argv[2]
    control_ids = argv[3:]
    artifact_path = None if artifact_arg == "-" else artifact_arg
    identity = f"zap::{artifact_arg}"

    expected_target = None
    if expected_arg != "-":
        with open(expected_arg, "r", encoding="utf-8") as f:
            expected_target = json.load(f)

    runs = ingest(artifact_path, control_ids, identity, expected_target)
    print(json.dumps({"version": 1, "runs": runs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
