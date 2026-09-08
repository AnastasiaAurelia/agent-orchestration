#!/usr/bin/env python3
"""Diana Security CI-controlled trusted verifier runs (Security Phase 5,
live-wired in Security Track remediation round A).

THE single, explicit extension point for supplying real trusted
verification evidence to the Security Gate. Prints a JSON array of
`evidence_model.py`-shaped "run" records -- the only input
`security_bundle.build_bundle()` is ever given for the real CI pipeline
(see `diana/ci/run-security-gate.py` / `.github/workflows/diana-security-
gate.yml`).

## What changed in this round

Previously this always printed `[]`: no live verifier execution was
wired into CI at all. This round wires in exactly ONE real, trusted
verifier family -- Semgrep, a real, independently-maintained static
analyzer -- using the repository's own PINNED, committed rule file
(`diana/security/verifiers/semgrep-rules.yml`), never a live external
rule registry (`--config=auto`/`p/...` would depend on a remote registry
at scan time, which is neither deterministic nor reproducible). Everything
else (Gitleaks, the deterministic-repo scan, every dynamic scenario,
every semantic reviewer contribution) remains capability-only, not yet
live -- that is the honest, explicit state after this round, not a claim
of completeness. See `diana/security/README.md`'s "Security Track
remediation round A" section for the full accounting.

## Trust properties preserved (unchanged from Phase 5)

- **CI-controlled only.** This script takes no arguments and reads no
  PR-supplied content: not the PR body, not an arbitrary checked-in
  file. It determines its OWN scan target by reading the actual git
  state of the checkout it is running in (`git rev-parse HEAD`, `git
  remote get-url origin`) -- which, because `diana-security-gate.yml`
  runs under `pull_request_target` with a default (base-rooted)
  checkout, is always the protected base, never the PR head.
- **No PR-head executable code.** Semgrep is a pattern-matching static
  analysis TOOL: it reads the checkout's files as DATA (text/AST
  pattern matching), it never executes, imports, or evaluates anything
  in them. Installing and invoking Semgrep itself is real, trusted code
  this script runs directly -- not code taken from a PR head.
- **Unavailable tool != PASS; tool error != PASS; no finding != PASS.**
  If Semgrep cannot be found or installed (`_find_or_install_semgrep`
  returns `None`), or its own execution fails / produces unparseable
  output (`_run_semgrep` returns `None`), or the git-derived target
  cannot be determined, this module calls `semgrep_adapter.ingest()`
  with `artifact_path=None` -- the EXACT same "tool unavailable" path
  Phase 2 already built and tested, which deterministically aggregates
  to `UNPROVEN`, never `PASS`, never silently absent. A genuinely clean
  scan (the rule ran, zero findings, target verified) is what produces
  `SATISFIED` -- computed by the real, unmodified `semgrep_adapter.py`,
  not by this module.
- **No secrets.** Nothing here reads or references any credential;
  Semgrep needs none to scan local files.
- **No synthetic evidence outside tests.** The artifact this module
  builds always wraps Semgrep's own REAL, just-produced JSON output --
  never a fabricated/hand-written report. Synthetic reports exist only
  in `test-ci-verifier-runs.sh`, clearly separated from this file's own
  runtime path.
- **Producer/tool identity explicit.** The built artifact's
  `tool.name`/`tool.version` are Semgrep's own real, installed version
  string, never a placeholder.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from typing import Any

SEC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SEC_DIR / "adapters"))
import semgrep_adapter  # noqa: E402

RULES_PATH = SEC_DIR / "verifiers" / "semgrep-rules.yml"

# The real, production authorization for this live-wired run -- distinct
# from semgrep_adapter.ILLUSTRATIVE_TEST_ONLY_RULE_MAP (tests only, never
# read here). Every check_id below must exist in RULES_PATH, and every
# (control_id, requirement) pair must be one semgrep_adapter.py's own
# AUTHORIZED_EVIDENCE actually permits -- semgrep_adapter.ingest() itself
# re-validates this independently and fails closed (ArtifactError ->
# ERROR) if it does not, so this module cannot smuggle authorization for
# a control the adapter was never designed to prove.
LIVE_RULE_MAP: dict[str, list[str]] = {
    "diana.shell-injection-via-concatenation": ["SEC-010", semgrep_adapter.SEC010_REQ],
    "diana.template-injection-via-render": ["SEC-011", semgrep_adapter.SEC011_REQ],
    "diana.jwt-unsafe-verification": ["SEC-021", semgrep_adapter.SEC021_REQ],
    "diana.insecure-deserialization": ["SEC-058", semgrep_adapter.SEC058_REQ],
}
LIVE_CONTROL_IDS = sorted({pair[0] for pair in LIVE_RULE_MAP.values()})

_SUBPROCESS_TIMEOUT_SECONDS = 120


def _run(cmd: list[str], cwd: str | None = None, timeout: int = _SUBPROCESS_TIMEOUT_SECONDS) -> subprocess.CompletedProcess | None:
    """Runs a subprocess; never raises. Returns None on any failure
    (missing binary, timeout, unexpected OS error) so callers can
    uniformly degrade to 'tool unavailable' rather than crash."""
    try:
        return subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def git_repository_identity(repo_root: str) -> tuple[str, str] | None:
    """Determines (repository, commit) from the ACTUAL git state of the
    checkout at repo_root -- never from an externally-supplied argument
    or environment variable, so there is nothing here for a PR to
    influence. Returns None if either cannot be determined."""
    commit_proc = _run(["git", "-C", repo_root, "rev-parse", "HEAD"])
    if commit_proc is None or commit_proc.returncode != 0:
        return None
    commit = commit_proc.stdout.decode("utf-8", "replace").strip()
    if not commit:
        return None

    remote_proc = _run(["git", "-C", repo_root, "remote", "get-url", "origin"])
    if remote_proc is None or remote_proc.returncode != 0:
        return None
    remote_url = remote_proc.stdout.decode("utf-8", "replace").strip()
    repository = _parse_owner_repo(remote_url)
    if repository is None:
        return None
    return repository, commit


def _parse_owner_repo(remote_url: str) -> str | None:
    """Extracts 'owner/repo' from a git remote URL (https or ssh form).
    Returns None for anything that doesn't cleanly parse -- fail closed
    rather than guess."""
    url = remote_url.strip()
    if url.endswith(".git"):
        url = url[: -len(".git")]
    if url.startswith("git@"):
        # git@github.com:owner/repo
        _, _, path = url.partition(":")
    elif "://" in url:
        # https://github.com/owner/repo
        _, _, path = url.partition("://")
        _, _, path = path.partition("/")
    else:
        return None
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    return f"{parts[-2]}/{parts[-1]}"


def _find_or_install_semgrep(workdir: Path) -> str | None:
    """Returns a path to a working `semgrep` executable, or None if
    unavailable. Tries an already-on-PATH semgrep first (cheap, no
    network); falls back to installing one into an ephemeral venv under
    `workdir` (network-dependent -- gracefully returns None on any
    failure, e.g. no network access, rather than raising)."""
    on_path = shutil.which("semgrep")
    if on_path is not None:
        return on_path

    venv_dir = workdir / "semgrep-venv"
    try:
        venv.create(str(venv_dir), with_pip=True)
    except Exception:  # noqa: BLE001 -- any venv-creation failure degrades to unavailable
        return None

    pip_path = venv_dir / "bin" / "pip"
    if not pip_path.exists():
        return None
    install_proc = _run([str(pip_path), "install", "--quiet", "semgrep"], timeout=180)
    if install_proc is None or install_proc.returncode != 0:
        return None

    semgrep_path = venv_dir / "bin" / "semgrep"
    return str(semgrep_path) if semgrep_path.exists() else None


def _run_semgrep(semgrep_path: str, rules_path: Path, target_root: str) -> dict[str, Any] | None:
    """Runs semgrep --config=rules_path --json against target_root.
    Returns the parsed JSON dict on a clean (exit 0, valid JSON, no
    semgrep-reported errors) run; None on any failure."""
    proc = _run([semgrep_path, f"--config={rules_path}", "--json", "--quiet", target_root], timeout=_SUBPROCESS_TIMEOUT_SECONDS)
    if proc is None or proc.returncode != 0:
        return None
    try:
        report = json.loads(proc.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(report, dict) or report.get("errors"):
        return None
    return report


def build_semgrep_envelope(
    repository: str,
    commit: str,
    root: str,
    semgrep_report: dict[str, Any],
    rule_ids: list[str],
) -> dict[str, Any]:
    """Pure function: wraps a real, already-produced Semgrep report into
    the adapter_base scan-evidence-artifact envelope. Never invokes
    Semgrep itself -- separated out for deterministic, offline unit
    testing (see test-ci-verifier-runs.sh)."""
    results = [
        {"check_id": r["check_id"], "path": r.get("path", "unknown-file")}
        for r in semgrep_report.get("results", [])
        if isinstance(r, dict) and isinstance(r.get("check_id"), str)
    ]
    envelope: dict[str, Any] = {
        "tool": {"name": "semgrep", "version": str(semgrep_report.get("version", "unknown"))},
        "execution": {"completed": True},
        "target": {"repository": repository, "commit": commit, "root": root, "scope": "full-repo"},
        "config": {"rule_map": LIVE_RULE_MAP},
        "scanned_inputs": sorted(set(semgrep_report.get("paths", {}).get("scanned", []))),
        "report": {"results": results, "rules_run": sorted(rule_ids)},
    }
    bound = {k: envelope[k] for k in ("tool", "execution", "target", "config", "scanned_inputs", "report")}
    canonical = json.dumps(bound, sort_keys=True, separators=(",", ":"))
    envelope["artifact_binding"] = {"sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
    return envelope


def collect_semgrep_runs(repo_root: str = ".") -> list[dict[str, Any]]:
    """Orchestrates real Semgrep execution end to end. Any failure at any
    step (git identity, tool availability, execution) degrades to
    semgrep_adapter.ingest(None, ...) -- explicit UNPROVEN, never a
    crash, never fabricated evidence."""
    identity = "ci_verifier_runs::semgrep-live"

    target = git_repository_identity(repo_root)
    if target is None:
        return semgrep_adapter.ingest(None, LIVE_CONTROL_IDS, identity, None)
    repository, commit = target
    expected_target = {"repository": repository, "commit": commit}

    with tempfile.TemporaryDirectory() as tmp:
        semgrep_path = _find_or_install_semgrep(Path(tmp))
        if semgrep_path is None:
            return semgrep_adapter.ingest(None, LIVE_CONTROL_IDS, identity, expected_target)

        report = _run_semgrep(semgrep_path, RULES_PATH, repo_root)
        if report is None:
            return semgrep_adapter.ingest(None, LIVE_CONTROL_IDS, identity, expected_target)

        envelope = build_semgrep_envelope(repository, commit, repo_root, report, list(LIVE_RULE_MAP.keys()))
        artifact_path = Path(tmp) / "semgrep-artifact.json"
        artifact_path.write_text(json.dumps(envelope), encoding="utf-8")

        return semgrep_adapter.ingest(str(artifact_path), LIVE_CONTROL_IDS, identity, expected_target)


def collect_trusted_runs() -> list[dict[str, Any]]:
    """Returns the list of evidence_model.py-shaped runs this CI
    invocation is able to trust. Wraps the real orchestration in a
    top-level guard: any wholly unexpected failure degrades to `[]`
    (identical to "nothing collected"), never propagates an exception
    that could take down the Security Gate pipeline."""
    try:
        return collect_semgrep_runs()
    except Exception:  # noqa: BLE001 -- absolute safety net, see docstring
        return []


def main() -> int:
    print(json.dumps(collect_trusted_runs(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
