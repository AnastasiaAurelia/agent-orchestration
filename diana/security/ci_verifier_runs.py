#!/usr/bin/env python3
"""Diana Security CI-controlled trusted verifier runs (Security Phase 5,
live-wired in Security Track remediation rounds A and B).

THE single, explicit extension point for supplying real trusted
verification evidence to the Security Gate. Prints a JSON array of
`evidence_model.py`-shaped "run" records -- the only input
`security_bundle.build_bundle()` is ever given for the real CI pipeline
(see `diana/ci/run-security-gate.py` / `.github/workflows/diana-security-
gate.yml`).

## What's wired live so far

- **Semgrep** (`STATIC_ANALYZER`, remediation round A) -- a real,
  independently-maintained static analyzer, using the repository's own
  PINNED, committed rule file (`diana/security/verifiers/semgrep-rules.yml`),
  never a live external rule registry.
- **Gitleaks** (`SECRET_SCANNER`, remediation round B) -- a real,
  independently-maintained secret scanner, downloaded as a version-
  pinned, checksum-verified release binary (never `@latest`, never an
  unverified download), using the repository's own PINNED, committed
  config (`diana/security/verifiers/gitleaks-config.toml`, which extends
  Gitleaks' default ruleset with an allowlist for this Security Track's
  own synthetic test fixtures -- see that file's own comments for why
  that is honest, not evidence-hiding). Always scans `SCOPE_FULL_REPO`
  (`SEC-007`); additionally scans a detected frontend-bundle build
  output directory for `SEC-006`/`SEC-065` ONLY if one is actually
  found (`_detect_frontend_bundle_dir`) -- if this repository has no
  such directory, those two controls correctly stay `UNPROVEN` rather
  than a scope being fabricated.
- **Deterministic repo/deployment-tree scan** (`DETERMINISTIC_REPO`,
  remediation round B) -- no third-party tool; Diana's own deterministic
  file-path-membership check (see `deterministic_repo_adapter.py`).
  Reuses the exact same real-directory detection as Gitleaks' frontend-
  bundle scope (`_detect_frontend_bundle_dir`) to find this repository's
  public deployment web root -- the same conventional directory names
  (`dist`, `build`, `out`, `.next`, `public/build`) represent both "the
  frontend bundle" and "what a static file server would publicly serve"
  in the common case. A real, deterministic filesystem traversal of that
  directory (sorted, no execution, protected-base checkout only) becomes
  the artifact's `served_paths`. If this repository has no such
  directory (true today -- it is Diana's own tooling, not a deployed web
  app), `SEC-064`'s static half simply receives no contribution here,
  correctly staying `UNPROVEN` rather than a served-path listing being
  fabricated for a deployment surface that does not exist.

- **`sensitive-file-paths-not-fetchable`** (`DYNAMIC_API`, remediation
  round B) -- SEC-064's dynamic half. A real, LOCAL-only HTTP server
  (bound to `127.0.0.1`, OS-assigned ephemeral port, this process's own,
  torn down in a `finally` block) serves the SAME detected deployment
  web root as the deterministic-repo-scan, and real negative-fetch GET
  requests are issued for a small, fixed, non-PR-influenced set of
  well-known sensitive paths (`SENSITIVE_FETCH_CANDIDATE_PATHS`). No
  production target, no public network exposure, no PR-provided paths or
  commands, no PR-head code executed (only static file bytes are
  served). ONLY attempted when a real web root directory is actually
  detected -- otherwise `SEC-064`'s dynamic half correctly stays
  `UNPROVEN`, same principle as the other two live families above.

Every OTHER dynamic scenario and every semantic reviewer contribution
remains capability-only, not yet live -- that is the honest, explicit
state after these rounds, not a claim of completeness. See
`diana/security/README.md`'s "Security Track remediation round A"/
"round B" sections for the full accounting.

## Trust properties preserved across both live families

- **CI-controlled only.** This script takes no arguments and reads no
  PR-supplied content: not the PR body, not an arbitrary checked-in
  file. It determines its OWN scan target by reading the actual git
  state of the checkout it is running in (`git rev-parse HEAD`, `git
  remote get-url origin`) -- which, because `diana-security-gate.yml`
  runs under `pull_request_target` with a default (base-rooted)
  checkout, is always the protected base, never the PR head. This is
  the EXECUTOR trust root for Semgrep/Gitleaks/the deterministic repo
  scan/the dynamic scenario: the code that runs, and the files it
  scans, always come from the protected base.
  `collect_github_review_runs()` (Security Track remediation round E)
  is the one deliberate exception to "target == base": a GitHub human
  review is genuinely, by GitHub's own design, bound to the PR HEAD
  commit under review, never the base -- so REVIEW TARGET IDENTITY is
  bound to `DIANA_HEAD_SHA` (the workflow's own
  `github.event.pull_request.head.sha`, strictly validated as a full
  40-hex SHA and cross-checked against GitHub's live PR-metadata API
  response), while the code that fetches and evaluates that review
  still runs only from the same protected-base executor as everything
  else here -- PR head is DATA/IDENTITY ONLY, never checked out,
  fetched, imported, or executed.
- **No PR-head executable code.** Both Semgrep and Gitleaks are
  pattern-matching TOOLS: they read the checkout's files as DATA (text/
  AST/regex matching), never executing, importing, or evaluating
  anything in them. Installing and invoking either tool is real, trusted
  code this script runs directly -- not code taken from a PR head.
  Gitleaks specifically is a downloaded BINARY, not source pulled from a
  PR -- its release asset is checksum-verified against a hash PINNED IN
  THIS FILE'S OWN SOURCE before it is ever executed, so a compromised or
  substituted download is detected and refused, never silently run.
- **Unavailable tool != PASS; tool error != PASS; no finding != PASS.**
  If a tool cannot be found/installed/verified, or its own execution
  fails / produces unparseable output, or the git-derived target cannot
  be determined, this module calls the relevant adapter's `ingest()`
  with `artifact_path=None` -- the EXACT same "tool unavailable" path
  Phase 2 already built and tested, which deterministically aggregates
  to `UNPROVEN`, never `PASS`, never silently absent. A genuinely clean
  scan (the check ran, zero findings, target verified) is what produces
  `SATISFIED` -- computed by the real, unmodified adapter, not by this
  module.
- **No secrets.** Nothing here reads or references any credential;
  neither tool needs one to scan local files.
- **No synthetic evidence outside tests.** The artifacts this module
  builds always wrap the real tool's REAL, just-produced output -- never
  a fabricated/hand-written report. Synthetic reports exist only in
  `test-ci-verifier-runs.sh`, clearly separated from this file's own
  runtime path.
- **Producer/tool identity explicit.** Every built artifact's
  `tool.name`/`tool.version` are the real, installed tool's own version
  string, never a placeholder.
- **No fabricated scope.** Gitleaks' frontend-bundle-scoped scans only
  ever happen, and are only ever declared, when a real build-output
  directory was actually found and actually scanned -- see
  `_detect_frontend_bundle_dir`.
"""

from __future__ import annotations

import functools
import hashlib
import http.server
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.error
import urllib.request
import venv
from pathlib import Path
from typing import Any

SEC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SEC_DIR / "adapters"))
import semgrep_adapter  # noqa: E402
import gitleaks_adapter  # noqa: E402
import deterministic_repo_adapter  # noqa: E402
sys.path.insert(0, str(SEC_DIR / "dynamic"))
import dynamic_normalizer  # noqa: E402
from scenarios import SCENARIO_REGISTRY  # noqa: E402
sys.path.insert(0, str(SEC_DIR / "reviewer"))
import github_review_adapter  # noqa: E402

# The exact BOUND_FIELDS tuple dynamic_base.py uses for its own
# artifact_binding hash -- duplicated here (not imported) only because
# it's a fixed structural constant, the same reason ci_verifier_runs.py
# doesn't import adapter_base.BOUND_FIELDS either; both envelope builders
# below stay hash-compatible with their real, unmodified normalizer.
_SCENARIO_BOUND_FIELDS = (
    "environment", "target", "scenario", "verifier_mode",
    "identities", "execution", "assertions", "cleanup", "result",
)

RULES_PATH = SEC_DIR / "verifiers" / "semgrep-rules.yml"
GITLEAKS_CONFIG_PATH = SEC_DIR / "verifiers" / "gitleaks-config.toml"
CATALOG_PATH = SEC_DIR / "catalog.json"

# The real, verified GitHub login this session's credential authenticates
# as (`gh api user --jq '.login'`) -- a review submitted BY this login is
# never trusted as independent judgment, regardless of what its review
# body claims. This is a worker-identity fact, not a secret.
DIANA_AGENT_LOGIN = "DIANA-AGENT"

# A GitHub review's body must contain one or more of these structured
# blocks to carry any control-specific judgment at all -- GitHub's own
# review UI has no per-control concept, so this is the minimum structure
# a reviewer adds themselves to say WHICH catalog control their
# Approve/Request-changes state is about, and WHY. A bare "LGTM" (no
# block at all) parses to zero blocks -> zero contributions, by
# construction -- never a guess at which control a blank approval might
# have meant.
_GITHUB_REVIEW_MARKER_RE = re.compile(
    r"<!--\s*DIANA:HUMAN-REVIEW\s*(\{.*?\})\s*DIANA:HUMAN-REVIEW\s*-->", re.DOTALL
)

# The real, production authorization for the Semgrep live-wired run --
# distinct from semgrep_adapter.ILLUSTRATIVE_TEST_ONLY_RULE_MAP (tests
# only, never read here). Every check_id below must exist in RULES_PATH,
# and every (control_id, requirement) pair must be one semgrep_adapter.py's
# own AUTHORIZED_EVIDENCE actually permits -- semgrep_adapter.ingest()
# itself re-validates this independently and fails closed (ArtifactError
# -> ERROR) if it does not, so this module cannot smuggle authorization
# for a control the adapter was never designed to prove.
SEMGREP_LIVE_RULE_MAP: dict[str, list[str]] = {
    "diana.shell-injection-via-concatenation": ["SEC-010", semgrep_adapter.SEC010_REQ],
    "diana.template-injection-via-render": ["SEC-011", semgrep_adapter.SEC011_REQ],
    "diana.jwt-unsafe-verification": ["SEC-021", semgrep_adapter.SEC021_REQ],
    "diana.insecure-deserialization": ["SEC-058", semgrep_adapter.SEC058_REQ],
}
SEMGREP_LIVE_CONTROL_IDS = sorted({pair[0] for pair in SEMGREP_LIVE_RULE_MAP.values()})

# Gitleaks: version-pinned, checksum-verified release binary (never
# `@latest`, never an unverified download -- see module docstring).
# Update both together when bumping the version; the checksum comes from
# Gitleaks' own published `_checksums.txt` for that release.
GITLEAKS_VERSION = "8.30.1"
GITLEAKS_LINUX_X64_URL = (
    f"https://github.com/gitleaks/gitleaks/releases/download/v{GITLEAKS_VERSION}/"
    f"gitleaks_{GITLEAKS_VERSION}_linux_x64.tar.gz"
)
GITLEAKS_LINUX_X64_SHA256 = "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"

# Conventional frontend/client build-output directory names. Checked for
# actual presence at repo_root before ever declaring a frontend-bundle-
# scoped scan -- never assumed, never fabricated (see
# _detect_frontend_bundle_dir).
FRONTEND_BUNDLE_DIR_CANDIDATES = ("dist", "build", "out", ".next", "public/build")

_SUBPROCESS_TIMEOUT_SECONDS = 120
_DOWNLOAD_TIMEOUT_SECONDS = 60

# Diana's own internal deterministic-repo-scan check has no third-party
# tool version to report -- this names ITS OWN traversal-logic version
# (bump only if the traversal/served-path semantics below change), never
# a fabricated placeholder.
DETERMINISTIC_REPO_SCAN_VERSION = "1"

# The dynamic scenario this module brings live: SEC-064's negative-fetch
# half, run against a real, LOCAL (127.0.0.1-only, ephemeral-port) HTTP
# server this script itself starts and tears down -- never a production
# target, never externally reachable. Candidate paths are a small, fixed,
# non-PR-influenced list (never derived from PR-supplied content), same
# spirit as deterministic_repo_adapter.py's own SENSITIVE_PATH_PREFIXES/
# SUFFIXES.
SENSITIVE_FETCH_SCENARIO_ID = "sensitive-file-paths-not-fetchable"
SENSITIVE_FETCH_SCENARIO_VERSION = "1"
SENSITIVE_FETCH_CANDIDATE_PATHS = (".env", ".git/config", "backup.sql", "backup.zip")
_LOCAL_FETCH_TIMEOUT_SECONDS = 3


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
        "config": {"rule_map": SEMGREP_LIVE_RULE_MAP},
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
        return semgrep_adapter.ingest(None, SEMGREP_LIVE_CONTROL_IDS, identity, None)
    repository, commit = target
    expected_target = {"repository": repository, "commit": commit}

    with tempfile.TemporaryDirectory() as tmp:
        semgrep_path = _find_or_install_semgrep(Path(tmp))
        if semgrep_path is None:
            return semgrep_adapter.ingest(None, SEMGREP_LIVE_CONTROL_IDS, identity, expected_target)

        report = _run_semgrep(semgrep_path, RULES_PATH, repo_root)
        if report is None:
            return semgrep_adapter.ingest(None, SEMGREP_LIVE_CONTROL_IDS, identity, expected_target)

        envelope = build_semgrep_envelope(repository, commit, repo_root, report, list(SEMGREP_LIVE_RULE_MAP.keys()))
        artifact_path = Path(tmp) / "semgrep-artifact.json"
        artifact_path.write_text(json.dumps(envelope), encoding="utf-8")

        return semgrep_adapter.ingest(str(artifact_path), SEMGREP_LIVE_CONTROL_IDS, identity, expected_target)


def _detect_frontend_bundle_dir(repo_root: str) -> str | None:
    """Returns the path to a real, existing conventional frontend/client
    build-output directory under repo_root, or None if none of the
    candidates exist. Never assumed, never fabricated -- a
    frontend-bundle-scoped scan is only ever declared when this actually
    finds something real to scan."""
    root = Path(repo_root)
    for candidate in FRONTEND_BUNDLE_DIR_CANDIDATES:
        path = root / candidate
        if path.is_dir():
            return str(path)
    return None


def _diag(stage: str, detail: str = "") -> None:
    """Emits one safe, non-secret diagnostic line to STDERR (never
    stdout -- stdout is the JSON runs array `diana-security-gate.yml`
    parses, and nothing here may corrupt it). `stage` is always one of
    the fixed GITLEAKS_DIAG_STAGES checkpoints (Security Track
    remediation round D, Priority 2) so a CI log can be grepped for
    exactly where a run stopped progressing. `detail`, when given, is
    restricted by every call site below to booleans, counts, exit codes,
    exception TYPE names, or fixed strings -- never file contents,
    finding values, tokens, or any other secret-shaped data."""
    print(f"[gitleaks-diag] {stage}{': ' + detail if detail else ''}", file=sys.stderr)


GITLEAKS_DIAG_STAGES = (
    "ON_PATH_CHECK",
    "DOWNLOAD_ATTEMPTED",
    "DOWNLOAD_SUCCEEDED",
    "CHECKSUM_VERIFIED",
    "BINARY_EXECUTABLE",
    "SCAN_LAUNCHED",
    "SCAN_EXITED_SUCCESSFULLY",
    "ARTIFACT_PARSED",
    "NORMALIZED_RUN_PRODUCED",
    "EVIDENCE_MODEL_ACCEPTED",
    "CONTROL_CONTRIBUTION_ACCEPTED",
)


def _verify_and_extract_gitleaks(archive_bytes: bytes, dest: Path) -> str | None:
    """Verifies archive_bytes against the pinned GITLEAKS_LINUX_X64_SHA256
    checksum BEFORE extracting anything. Returns the path to the
    extracted `gitleaks` binary, or None if the checksum doesn't match or
    extraction fails -- a compromised/substituted download is refused,
    never silently run."""
    actual_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    if actual_sha256 != GITLEAKS_LINUX_X64_SHA256:
        _diag("CHECKSUM_VERIFIED", f"MISMATCH (downloaded {len(archive_bytes)} bytes; refusing to extract)")
        return None
    _diag("CHECKSUM_VERIFIED", "match")
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
            dest_resolved = dest.resolve()
            for member in tar.getmembers():
                member_path = (dest / member.name).resolve()
                if member_path != dest_resolved and dest_resolved not in member_path.parents:
                    _diag("BINARY_EXECUTABLE", "refused: path-traversal member in archive")
                    return None  # refuse any path-traversal member, fail closed
            tar.extractall(path=dest)  # noqa: S202 -- members individually validated above
    except (tarfile.TarError, OSError) as exc:
        _diag("BINARY_EXECUTABLE", f"extraction failed: {type(exc).__name__}")
        return None
    binary_path = dest / "gitleaks"
    if not binary_path.is_file():
        _diag("BINARY_EXECUTABLE", "no 'gitleaks' file found after extraction")
        return None
    binary_path.chmod(0o755)
    _diag("BINARY_EXECUTABLE", "yes")
    return str(binary_path)


def _find_or_install_gitleaks(workdir: Path) -> str | None:
    """Returns a path to a working `gitleaks` executable, or None if
    unavailable. Tries an already-on-PATH gitleaks first (cheap, no
    network); falls back to downloading the pinned, checksum-verified
    Linux x86_64 release binary. Only supports linux/x86_64 (matching
    GitHub Actions' ubuntu-latest runners, the only environment this
    actually needs to work in) -- any other platform, or any download/
    checksum/network failure, gracefully returns None rather than
    raising. Emits GITLEAKS_DIAG_STAGES checkpoints to stderr throughout
    (Security Track remediation round D, Priority 2 -- diagnosing a real
    CI-vs-local reliability gap found on PR #34)."""
    on_path = shutil.which("gitleaks")
    if on_path is not None:
        _diag("ON_PATH_CHECK", f"found at {on_path}")
        return on_path
    _diag("ON_PATH_CHECK", "not found; will attempt pinned download")

    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        _diag("DOWNLOAD_ATTEMPTED", f"skipped: unsupported platform {platform.system()}/{platform.machine()}")
        return None

    _diag("DOWNLOAD_ATTEMPTED", GITLEAKS_LINUX_X64_URL)
    try:
        request = urllib.request.Request(GITLEAKS_LINUX_X64_URL, headers={"User-Agent": "diana-security-ci"})
        with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:  # noqa: S310 -- fixed, pinned HTTPS URL to a known release asset
            archive_bytes = response.read()
    except urllib.error.HTTPError as exc:
        _diag("DOWNLOAD_SUCCEEDED", f"no: HTTP {exc.code}")
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _diag("DOWNLOAD_SUCCEEDED", f"no: {type(exc).__name__}: {exc}")
        return None
    _diag("DOWNLOAD_SUCCEEDED", f"yes ({len(archive_bytes)} bytes)")

    return _verify_and_extract_gitleaks(archive_bytes, workdir)


def _run_gitleaks(gitleaks_path: str, config_path: Path, target_root: str) -> list[dict[str, Any]] | None:
    """Runs `gitleaks dir` against target_root (the working tree exactly
    as currently checked out -- matching "full-repo"/"frontend-bundle"
    scope semantics precisely, not historical git commits). Returns the
    parsed findings list on a clean (exit 0, valid JSON) run; None on any
    failure. `--exit-code=0` forces a consistent exit code regardless of
    finding count, so a non-zero exit here means the tool genuinely
    failed to run, never "it found something." Emits GITLEAKS_DIAG_STAGES
    checkpoints to stderr -- never the findings themselves (finding
    VALUES are never diagnostic output, only a count)."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        report_path = f.name
    try:
        _diag("SCAN_LAUNCHED", f"target={target_root!r}")
        proc = _run(
            [
                gitleaks_path, "dir",
                f"--config={config_path}",
                "--report-format=json",
                f"--report-path={report_path}",
                "--no-banner",
                "--exit-code=0",
                target_root,
            ],
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        if proc is None:
            _diag("SCAN_EXITED_SUCCESSFULLY", "no: subprocess did not complete (timeout or OS error)")
            return None
        if proc.returncode != 0:
            stderr_tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] if proc.stderr else []
            _diag("SCAN_EXITED_SUCCESSFULLY", f"no: exit code {proc.returncode}; stderr tail: {stderr_tail}")
            return None
        _diag("SCAN_EXITED_SUCCESSFULLY", "yes")
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                findings = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            _diag("ARTIFACT_PARSED", f"no: {type(exc).__name__}")
            return None
        if not isinstance(findings, list):
            _diag("ARTIFACT_PARSED", f"no: report was not a JSON list (got {type(findings).__name__})")
            return None
        _diag("ARTIFACT_PARSED", f"yes ({len(findings)} finding(s) -- count only, never contents)")
        return findings
    finally:
        Path(report_path).unlink(missing_ok=True)


def build_gitleaks_envelope(
    repository: str,
    commit: str,
    root: str,
    scope: str,
    findings: list[dict[str, Any]],
    gitleaks_version: str,
) -> dict[str, Any]:
    """Pure function: wraps a real, already-produced Gitleaks report into
    the adapter_base scan-evidence-artifact envelope, for one declared
    scope. Never invokes Gitleaks itself -- separated out for
    deterministic, offline unit testing."""
    envelope: dict[str, Any] = {
        "tool": {"name": "gitleaks", "version": gitleaks_version},
        "execution": {"completed": True},
        "target": {"repository": repository, "commit": commit, "root": root, "scope": scope},
        "config": {},
        "scanned_inputs": [],
        "report": findings,
    }
    bound = {k: envelope[k] for k in ("tool", "execution", "target", "config", "scanned_inputs", "report")}
    canonical = json.dumps(bound, sort_keys=True, separators=(",", ":"))
    envelope["artifact_binding"] = {"sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
    return envelope


def _ingest_gitleaks_scope(
    gitleaks_path: str | None,
    tmp: Path,
    repository: str,
    commit: str,
    root: str,
    scope: str,
    control_ids: list[str],
    identity: str,
    expected_target: dict[str, Any],
) -> list[dict[str, Any]]:
    """One scoped Gitleaks scan -> gitleaks_adapter.ingest() for exactly
    the controls that scope is relevant to. Degrades to `ingest(None, ...)`
    (explicit UNPROVEN) on any failure, exactly like the Semgrep path.
    Emits the final three GITLEAKS_DIAG_STAGES checkpoints (Security
    Track remediation round D, Priority 2)."""
    if gitleaks_path is None:
        return gitleaks_adapter.ingest(None, control_ids, f"{identity}::{scope}", expected_target)

    findings = _run_gitleaks(gitleaks_path, GITLEAKS_CONFIG_PATH, root)
    if findings is None:
        return gitleaks_adapter.ingest(None, control_ids, f"{identity}::{scope}", expected_target)

    envelope = build_gitleaks_envelope(repository, commit, root, scope, findings, GITLEAKS_VERSION)
    artifact_path = tmp / f"gitleaks-artifact-{scope}.json"
    artifact_path.write_text(json.dumps(envelope), encoding="utf-8")
    _diag("NORMALIZED_RUN_PRODUCED", f"scope={scope!r}, controls={control_ids}")
    runs = gitleaks_adapter.ingest(str(artifact_path), control_ids, f"{identity}::{scope}", expected_target)
    accepted = [r for r in runs if r.get("tool_error") is None]
    _diag(
        "EVIDENCE_MODEL_ACCEPTED",
        f"{len(accepted)}/{len(runs)} run(s) had no tool_error (scope={scope!r})",
    )
    contributed = [r["control_id"] for r in runs if r.get("evidence")]
    _diag(
        "CONTROL_CONTRIBUTION_ACCEPTED",
        f"{contributed if contributed else 'none'} (scope={scope!r})",
    )
    return runs


def collect_gitleaks_runs(repo_root: str = ".") -> list[dict[str, Any]]:
    """Orchestrates real Gitleaks execution end to end, across every
    scope this repository actually has a real surface for. Always
    attempts SCOPE_FULL_REPO (SEC-007). Additionally attempts
    SCOPE_FRONTEND_BUNDLE and SCOPE_FULL_REPO_AND_FRONTEND_BUNDLE (SEC-006/
    SEC-065) ONLY when `_detect_frontend_bundle_dir` finds a real build-
    output directory -- if this repository has none, those two controls
    simply receive no contribution here at all (not a fabricated scope,
    not a fabricated finding), correctly leaving them exactly as
    UNPROVEN as if this function had never run for them."""
    identity = "ci_verifier_runs::gitleaks-live"

    target = git_repository_identity(repo_root)
    if target is None:
        all_controls = sorted(gitleaks_adapter.AUTHORIZED_EVIDENCE.keys())
        return gitleaks_adapter.ingest(None, all_controls, identity, None)
    repository, commit = target
    expected_target = {"repository": repository, "commit": commit}

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        gitleaks_path = _find_or_install_gitleaks(tmp)

        runs = list(
            _ingest_gitleaks_scope(
                gitleaks_path, tmp, repository, commit, repo_root,
                gitleaks_adapter.SCOPE_FULL_REPO, ["SEC-007"], identity, expected_target,
            )
        )

        frontend_dir = _detect_frontend_bundle_dir(repo_root)
        if frontend_dir is not None:
            runs.extend(
                _ingest_gitleaks_scope(
                    gitleaks_path, tmp, repository, commit, frontend_dir,
                    gitleaks_adapter.SCOPE_FRONTEND_BUNDLE, ["SEC-006"], identity, expected_target,
                )
            )
            # The combined scope (SEC-065) asserts BOTH surfaces were
            # scanned together -- only declared when a real frontend
            # bundle dir was actually found, scanning repo_root (which
            # already contains the frontend_dir as a subdirectory).
            runs.extend(
                _ingest_gitleaks_scope(
                    gitleaks_path, tmp, repository, commit, repo_root,
                    gitleaks_adapter.SCOPE_FULL_REPO_AND_FRONTEND_BUNDLE, ["SEC-065"], identity, expected_target,
                )
            )

        return runs


def _list_served_paths(root_dir: str) -> list[str]:
    """Deterministically walks root_dir (a real, already-detected public
    deployment web root) and returns every regular file's path RELATIVE
    to root_dir, using forward slashes, sorted. Pure filesystem
    inspection of the protected-base checkout as DATA -- no execution of
    anything found, no network access, no PR-supplied input. Directory
    entries are not themselves included (only files, matching "what a
    request for this path would actually return"); symlinks are walked
    as encountered by os.walk but never followed outside root_dir's own
    tree since os.walk does not follow directory symlinks by default."""
    root = Path(root_dir)
    paths: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            full = Path(dirpath) / filename
            try:
                rel = full.relative_to(root)
            except ValueError:
                continue
            paths.append(rel.as_posix())
    return sorted(paths)


def build_deterministic_repo_envelope(
    repository: str,
    commit: str,
    root: str,
    served_paths: list[str],
) -> dict[str, Any]:
    """Pure function: wraps a real, already-computed served_paths listing
    into the adapter_base scan-evidence-artifact envelope. Never walks
    the filesystem itself -- separated out for deterministic, offline
    unit testing (see test-ci-verifier-runs.sh)."""
    envelope: dict[str, Any] = {
        "tool": {"name": deterministic_repo_adapter.TOOL_NAME, "version": DETERMINISTIC_REPO_SCAN_VERSION},
        "execution": {"completed": True},
        "target": {"repository": repository, "commit": commit, "root": root},
        "config": {},
        "scanned_inputs": list(served_paths),
        "report": {"served_paths": list(served_paths)},
    }
    bound = {k: envelope[k] for k in ("tool", "execution", "target", "config", "scanned_inputs", "report")}
    canonical = json.dumps(bound, sort_keys=True, separators=(",", ":"))
    envelope["artifact_binding"] = {"sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
    return envelope


def collect_deterministic_repo_runs(repo_root: str = ".") -> list[dict[str, Any]]:
    """Orchestrates the real deterministic-repo-scan end to end for
    SEC-064's static half. Reuses `_detect_frontend_bundle_dir` to find
    this repository's public deployment web root -- if none exists (true
    for this repository today), degrades to `deterministic_repo_adapter.
    ingest(None, ...)`, the explicit UNPROVEN path, rather than
    fabricating a served-path listing for a deployment surface that does
    not exist. Any failure (git identity, directory traversal) degrades
    the same way -- never a crash, never fabricated evidence."""
    identity = "ci_verifier_runs::deterministic-repo-scan"
    control_ids = sorted(deterministic_repo_adapter.AUTHORIZED_EVIDENCE.keys())

    target = git_repository_identity(repo_root)
    if target is None:
        return deterministic_repo_adapter.ingest(None, control_ids, identity, None)
    repository, commit = target
    expected_target = {"repository": repository, "commit": commit}

    web_root_dir = _detect_frontend_bundle_dir(repo_root)
    if web_root_dir is None:
        return deterministic_repo_adapter.ingest(None, control_ids, identity, expected_target)

    try:
        served_paths = _list_served_paths(web_root_dir)
    except OSError:
        return deterministic_repo_adapter.ingest(None, control_ids, identity, expected_target)

    envelope = build_deterministic_repo_envelope(repository, commit, web_root_dir, served_paths)
    with tempfile.TemporaryDirectory() as tmp_str:
        artifact_path = Path(tmp_str) / "deterministic-repo-artifact.json"
        artifact_path.write_text(json.dumps(envelope), encoding="utf-8")
        return deterministic_repo_adapter.ingest(str(artifact_path), control_ids, identity, expected_target)


def _start_local_static_server(directory: str) -> tuple[http.server.ThreadingHTTPServer, threading.Thread, int]:
    """Starts a real HTTP server bound ONLY to 127.0.0.1, on an
    OS-assigned ephemeral port, serving `directory` as static file BYTES
    (SimpleHTTPRequestHandler never executes anything it serves -- the
    same "read as data, never execute" property Semgrep/Gitleaks have).
    Never binds 0.0.0.0 or any externally-reachable address, never
    long-lived -- the caller tears this down in a `finally` block.
    Raises OSError if the bind itself fails (e.g. sandboxed environment
    refuses local sockets); the caller degrades to tool-unavailable."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, server.server_address[1]


def _fetch_status(base_url: str, path: str) -> int | None:
    """GETs base_url+path (127.0.0.1 only, this process's own just-started
    local server) with a short, bounded timeout. Returns the real HTTP
    status code, or None on any failure (connection error, timeout) -- a
    failed probe is never itself treated as "not fetchable" evidence; the
    caller degrades the whole scenario to tool-unavailable if any probe
    does not complete."""
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "diana-security-ci-local-probe"})
        with urllib.request.urlopen(request, timeout=_LOCAL_FETCH_TIMEOUT_SECONDS) as response:  # noqa: S310 -- fixed 127.0.0.1 URL to this process's own local server
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def build_sensitive_fetch_scenario_envelope(
    repository: str,
    commit: str,
    base_url: str,
    outcome: str,
    probe_results: dict[str, int],
) -> dict[str, Any]:
    """Pure function: wraps real, already-observed local HTTP fetch
    results into the dynamic_base scenario-evidence-artifact envelope for
    the `sensitive-file-paths-not-fetchable` scenario. Never issues the
    fetches itself -- separated out for deterministic, offline unit
    testing (see test-ci-verifier-runs.sh). `outcome` (`"PASSED"` or
    `"FAILED"`) and `probe_results` (path -> real observed HTTP status)
    must already be computed by the caller from real fetches; this
    function only shapes them into the envelope, never re-derives or
    fabricates a result."""
    spec = SCENARIO_REGISTRY[SENSITIVE_FETCH_SCENARIO_ID]
    envelope: dict[str, Any] = {
        "environment": "LOCAL",
        "target": {"repository": repository, "commit": commit, "base_url": base_url},
        "scenario": {"id": SENSITIVE_FETCH_SCENARIO_ID, "version": SENSITIVE_FETCH_SCENARIO_VERSION},
        "verifier_mode": "DYNAMIC_API",
        "identities": [],
        "execution": {"completed": True, "probe_results": probe_results},
        "assertions": [{"id": "sensitive_file_paths_not_fetchable", "outcome": outcome}],
        "cleanup": {"required": False, "performed": True, "success": True},
        "result": {"control_id": spec["control_id"], "requirement": spec["requirement"]},
    }
    bound = {k: envelope[k] for k in _SCENARIO_BOUND_FIELDS}
    canonical = json.dumps(bound, sort_keys=True, separators=(",", ":"))
    envelope["artifact_binding"] = {"sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
    return envelope


def collect_sensitive_path_fetch_scenario_runs(repo_root: str = ".") -> list[dict[str, Any]]:
    """Orchestrates ONE real, LOCAL, bounded dynamic scenario for
    SEC-064's dynamic half. Spins up a real HTTP server bound only to
    127.0.0.1 on an OS-assigned ephemeral port, serving the SAME detected
    public deployment web root the deterministic-repo-scan uses
    (`_detect_frontend_bundle_dir`), and issues real negative-fetch GET
    requests for a small, fixed, non-PR-influenced set of well-known
    sensitive paths (`SENSITIVE_FETCH_CANDIDATE_PATHS`).

    Safety properties: no production target (127.0.0.1 only, never
    externally reachable); no public attack traffic; no PR-provided
    paths or commands (the candidate list is a fixed module constant);
    scenario identity (repository/commit) comes from the trusted base's
    own git state, never a PR; the server only serves static file bytes,
    never executes anything found in the target directory; every probe
    has a short, bounded timeout (`_LOCAL_FETCH_TIMEOUT_SECONDS`); the
    server is always torn down in a `finally` block, real or degraded
    path. Any failure at any step (git identity, no web root detected,
    server bind failure, any single probe not completing) degrades to
    `dynamic_normalizer.ingest(None, ...)` -- the explicit
    tool-unavailable path -- never a crash, never a fabricated result."""
    identity = "ci_verifier_runs::dynamic-sensitive-path-fetch"
    control_ids = ["SEC-064"]

    target = git_repository_identity(repo_root)
    if target is None:
        return dynamic_normalizer.ingest(None, control_ids, identity, None)
    repository, commit = target

    web_root_dir = _detect_frontend_bundle_dir(repo_root)
    if web_root_dir is None:
        return dynamic_normalizer.ingest(None, control_ids, identity, None)

    try:
        server, _thread, port = _start_local_static_server(web_root_dir)
    except OSError:
        return dynamic_normalizer.ingest(None, control_ids, identity, None)

    base_url = f"http://127.0.0.1:{port}"
    try:
        probe_results: dict[str, int] = {}
        for path in SENSITIVE_FETCH_CANDIDATE_PATHS:
            status = _fetch_status(base_url, path)
            if status is None:
                return dynamic_normalizer.ingest(None, control_ids, identity, None)
            probe_results[path] = status
    finally:
        server.shutdown()
        server.server_close()

    outcome = "FAILED" if any(status == 200 for status in probe_results.values()) else "PASSED"
    envelope = build_sensitive_fetch_scenario_envelope(repository, commit, base_url, outcome, probe_results)
    expected_context = {"environment": "LOCAL", "repository": repository, "commit": commit, "base_url": base_url}

    with tempfile.TemporaryDirectory() as tmp_str:
        artifact_path = Path(tmp_str) / "sensitive-path-fetch-scenario.json"
        artifact_path.write_text(json.dumps(envelope), encoding="utf-8")
        return dynamic_normalizer.ingest(str(artifact_path), control_ids, identity, expected_context)


# Every control_id any live-wired family in this module could possibly
# emit a run for -- the union across all four collect_*_runs() families.
# Used by test-security-gate.sh to prove a planted fake artifact never
# smuggles evidence for a control none of these live families actually
# cover, without hardcoding a duplicate list that could silently drift.
ALL_LIVE_WIREABLE_CONTROL_IDS = sorted(
    set(SEMGREP_LIVE_CONTROL_IDS)
    | set(gitleaks_adapter.AUTHORIZED_EVIDENCE.keys())
    | set(deterministic_repo_adapter.AUTHORIZED_EVIDENCE.keys())
    | {SCENARIO_REGISTRY[SENSITIVE_FETCH_SCENARIO_ID]["control_id"]}
    | github_review_adapter._authorized_control_ids(
        {c["id"]: c for c in json.load(open(CATALOG_PATH, "r", encoding="utf-8"))["controls"]}
    )
)


def _gh_api(path: str) -> Any | None:
    """Runs `gh api <path>` (read-only GET; this function never passes
    -X or a request body) and parses its JSON stdout. Returns None on
    ANY failure -- missing `gh`, no/insufficient auth, network failure,
    non-2xx response, unparseable output -- so callers uniformly degrade
    to tool-unavailable rather than crash or guess. Needs only
    `pull-requests: read` (to read PR metadata and reviews); never
    requests a write scope."""
    proc = _run(["gh", "api", path], timeout=30)
    if proc is None or proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _pr_number_from_env() -> int | None:
    """The PR number is read from an environment variable the WORKFLOW
    itself sets from GitHub's own `github.event.pull_request.number` --
    GitHub's own determination of which PR triggered this run, not a
    string a PR could type into its own body or a commit message. Absent
    or non-numeric -> None, degrading to tool-unavailable (this function
    never guesses a PR number)."""
    raw = os.environ.get("DIANA_PR_NUMBER", "").strip()
    return int(raw) if raw.isdigit() else None


_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _valid_full_sha(value: Any) -> bool:
    """Strict shape check for a real git/GitHub commit SHA: exactly 40
    lowercase hex characters. Anything else (short SHA, mixed case,
    non-hex, wrong length, non-string, empty) fails closed -- this
    function never guesses or normalizes, it only accepts what a real
    SHA already looks like."""
    return isinstance(value, str) and bool(_FULL_SHA_RE.fullmatch(value))


def _head_sha_from_env() -> str | None:
    """The PR HEAD commit SHA is read from an environment variable the
    WORKFLOW itself sets from GitHub's own
    `github.event.pull_request.head.sha` (see `DIANA_HEAD_SHA` in
    `diana-security-gate.yml`) -- GitHub's own determination of which
    commit is under review, not a string a PR could type into its own
    body or a commit message. Strictly validated as a full 40-hex SHA;
    absent, truncated, or malformed -> None, degrading to tool-
    unavailable exactly like a missing PR number already does (fail
    closed, never guess).

    This is the REVIEW TARGET identity (Security Track remediation
    round E) -- deliberately distinct from `git_repository_identity()`'s
    commit, which names the EXECUTOR trust root (the protected base this
    script itself runs from). A GitHub review's own `commit_id` is bound
    by GitHub to the PR HEAD at the time of review, never to the base;
    conflating the two (the pre-round-E defect) meant a review's
    `commit_id` could never match the executor's base commit, so every
    genuine, current review was silently treated as stale/absent."""
    raw = os.environ.get("DIANA_HEAD_SHA", "").strip()
    return raw if _valid_full_sha(raw) else None


def _parse_human_review_blocks(body: str) -> list[dict[str, Any]]:
    """Extracts every well-formed `<!-- DIANA:HUMAN-REVIEW {...}
    DIANA:HUMAN-REVIEW -->` JSON object from a real GitHub review body.
    A block must be a JSON object with non-empty string `control_id` and
    `rationale` fields to count; anything else (malformed JSON, wrong
    shape, missing fields) is silently skipped -- one malformed block
    never invalidates other well-formed blocks in the same review body,
    and a body with zero valid blocks correctly yields zero
    contributions (a blanket approval never becomes evidence for any
    control it never named)."""
    blocks: list[dict[str, Any]] = []
    for match in _GITHUB_REVIEW_MARKER_RE.finditer(body):
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if (
            isinstance(parsed, dict)
            and isinstance(parsed.get("control_id"), str)
            and parsed.get("control_id", "").strip()
            and isinstance(parsed.get("rationale"), str)
            and parsed.get("rationale", "").strip()
        ):
            blocks.append(parsed)
    return blocks


def build_github_review_envelope(
    repository: str,
    commit: str,
    reviewer_login: str,
    review_id: int,
    control_id: str,
    requirement: str,
    judgment: str,
    rationale: str,
    independence: dict[str, bool],
    submitted_at: str,
) -> dict[str, Any]:
    """Pure function: shapes real, already-fetched GitHub review fields
    into the github_review_adapter.py envelope, for ONE (review,
    control_id, requirement) triple. Never calls the GitHub API itself --
    separated out for deterministic, offline unit testing, exactly like
    every other build_*_envelope function in this module."""
    envelope: dict[str, Any] = {
        "reviewer": {"login": reviewer_login, "review_id": review_id},
        "target": {"repository": repository, "commit": commit},
        "control_id": control_id,
        "requirement": requirement,
        "judgment": judgment,
        "rationale": rationale,
        "independence": independence,
        "submitted_at": submitted_at,
    }
    bound = {k: envelope[k] for k in github_review_adapter.BOUND_FIELDS}
    canonical = json.dumps(bound, sort_keys=True, separators=(",", ":"))
    envelope["artifact_binding"] = {"sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
    return envelope


def collect_github_review_runs(repo_root: str = ".") -> list[dict[str, Any]]:
    """Orchestrates real, live GitHub-backed human-review evidence end to
    end. Fetches the current PR's metadata and reviews via `gh api`
    (protected-base, trusted code -- the fetch itself, and every
    independence/binding computation below, runs from the trusted
    checkout, never from PR-supplied content), builds one
    github_review_adapter.py artifact per (well-formed structured block,
    control's own required_evidence item), and ingests each through the
    real, unmodified adapter.

    Target binding (Security Track remediation round E; see the module
    docstring's "Trust properties" section for the full rationale). Two
    genuinely different identities are involved and must never be
    conflated:

    - EXECUTOR trust root: `git_repository_identity()`'s commit -- the
      protected base this script itself runs from. Used ONLY to derive
      `repository` here; the base commit itself is not the review
      target and is deliberately not enforced against `DIANA_BASE_SHA`
      (main may legitimately have advanced between event dispatch and
      this checkout -- that is not a defect to fail closed on).
    - REVIEW target identity: `DIANA_HEAD_SHA` (`_head_sha_from_env()`),
      the workflow's own `github.event.pull_request.head.sha`, strictly
      validated as a full 40-hex SHA. This is the commit a real GitHub
      review's `commit_id` is actually bound to (verified empirically:
      PR #37's own real review carried `commit_id` equal to PR #37's
      HEAD, never its base) -- so it is what `expected_target["commit"]`
      and the staleness check below are bound to, never the base
      commit. `DIANA_HEAD_SHA` is cross-checked against GitHub's own
      live PR-metadata API response (`pr_data["head"]["sha"]`, plus PR
      number and base-repository full_name) before being trusted for
      anything -- a mismatch (malformed env value, or the event and the
      live API state disagreeing, e.g. a new push landed between event
      dispatch and this fetch) degrades to tool-unavailable rather than
      guessing which source to believe. PR head is DATA/IDENTITY ONLY
      throughout: never checked out, fetched, imported, or executed.

    Reviewer identity (`login`, `review_id`) is copied verbatim from the
    real `gh api .../reviews` response. Independence
    (`reviewer_is_pr_author`/`reviewer_is_diana_agent`) is computed HERE,
    by trusted code, from the PR's real fetched author login and the
    fixed `DIANA_AGENT_LOGIN` constant -- never self-declared by a PR or
    a review body. A review whose `commit_id` does not exactly match the
    PR HEAD SHA currently being evaluated is skipped BEFORE even
    reaching the adapter (defense in depth; `github_review_adapter.py`'s
    own target binding would independently reject it too) -- this is
    exactly how staleness after a new push is handled: no new
    mechanism, the same exact-commit binding this whole track already
    uses everywhere, now bound to the correct (head, not base) commit.
    `COMMENTED`/`DISMISSED`/`PENDING` review states are not a terminal
    judgment and are skipped. Any failure at any step (PR number
    unavailable, head SHA unavailable/malformed, git identity
    unavailable, `gh api` failure, PR-metadata/event mismatch) degrades
    to `github_review_adapter.ingest(catalog_controls, None, ...)` --
    the explicit tool-unavailable path, never a crash, never fabricated
    evidence."""
    identity = "ci_verifier_runs::github-review"

    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    catalog_controls = {c["id"]: c for c in catalog["controls"]}
    control_ids = sorted(github_review_adapter._authorized_control_ids(catalog_controls))

    base_target = git_repository_identity(repo_root)
    pr_number = _pr_number_from_env()
    head_sha = _head_sha_from_env()
    if base_target is None or pr_number is None or head_sha is None:
        return github_review_adapter.ingest(catalog_controls, None, control_ids, identity, None)
    repository, _base_commit = base_target
    expected_target = {"repository": repository, "commit": head_sha}

    pr_data = _gh_api(f"repos/{repository}/pulls/{pr_number}")
    reviews = _gh_api(f"repos/{repository}/pulls/{pr_number}/reviews")
    if not isinstance(pr_data, dict) or not isinstance(reviews, list):
        return github_review_adapter.ingest(catalog_controls, None, control_ids, identity, expected_target)

    # Cross-check GitHub's own live PR-metadata response against the
    # trusted, workflow-supplied event identity before trusting anything
    # derived from it -- fail closed on any disagreement rather than
    # guess which source to believe (e.g. a push landing between event
    # dispatch and this fetch would move the real head out from under
    # `DIANA_HEAD_SHA`; safest to treat that window as tool-unavailable).
    pr_head = pr_data.get("head")
    pr_base = pr_data.get("base")
    pr_base_repo = pr_base.get("repo") if isinstance(pr_base, dict) else None
    pr_base_repo_full_name = pr_base_repo.get("full_name") if isinstance(pr_base_repo, dict) else None
    if (
        pr_data.get("number") != pr_number
        or pr_base_repo_full_name != repository
        or not isinstance(pr_head, dict)
        or pr_head.get("sha") != head_sha
    ):
        return github_review_adapter.ingest(catalog_controls, None, control_ids, identity, expected_target)

    pr_author_login = pr_data.get("user", {}).get("login") if isinstance(pr_data.get("user"), dict) else None

    all_runs: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        artifact_index = 0
        for review in reviews:
            if not isinstance(review, dict):
                continue
            reviewer = review.get("user")
            reviewer_login = reviewer.get("login") if isinstance(reviewer, dict) else None
            review_id = review.get("id")
            state = review.get("state")
            commit_id = review.get("commit_id")
            body = review.get("body") or ""
            submitted_at = review.get("submitted_at")
            if (
                not isinstance(reviewer_login, str) or not reviewer_login
                or not isinstance(review_id, int) or isinstance(review_id, bool)
                or not isinstance(commit_id, str)
                or not isinstance(submitted_at, str) or not submitted_at
            ):
                continue
            if commit_id != head_sha:
                continue  # stale relative to the PR HEAD being evaluated -- never attempted
            if state not in ("APPROVED", "CHANGES_REQUESTED"):
                continue  # COMMENTED/DISMISSED/PENDING: not a terminal judgment
            judgment = "APPROVE" if state == "APPROVED" else "REQUEST_CHANGES"
            independence = {
                "reviewer_is_pr_author": reviewer_login == pr_author_login,
                "reviewer_is_diana_agent": reviewer_login == DIANA_AGENT_LOGIN,
            }
            for block in _parse_human_review_blocks(body):
                control_id = block["control_id"]
                if control_id not in control_ids:
                    continue
                control = catalog_controls.get(control_id)
                if control is None:
                    continue
                for requirement in control["required_evidence"]:
                    envelope = build_github_review_envelope(
                        repository, head_sha, reviewer_login, review_id, control_id,
                        requirement, judgment, block["rationale"], independence, submitted_at,
                    )
                    artifact_path = tmp / f"github-review-{artifact_index}.json"
                    artifact_index += 1
                    artifact_path.write_text(json.dumps(envelope), encoding="utf-8")
                    all_runs.extend(
                        github_review_adapter.ingest(
                            catalog_controls, str(artifact_path), [control_id],
                            f"{identity}::{reviewer_login}::{review_id}", expected_target,
                        )
                    )

    covered = {r["control_id"] for r in all_runs}
    missing = [c for c in control_ids if c not in covered]
    if missing:
        all_runs.extend(github_review_adapter.ingest(catalog_controls, None, missing, identity, expected_target))
    return all_runs


def collect_trusted_runs() -> list[dict[str, Any]]:
    """Returns the list of evidence_model.py-shaped runs this CI
    invocation is able to trust, combined across every live-wired
    verifier family. Each family's own orchestration function already
    degrades to explicit UNPROVEN runs on failure (never raises under
    normal operation); this wraps the WHOLE combination in a top-level
    guard too, so a wholly unexpected failure in either family degrades
    to `[]` (identical to "nothing collected") rather than ever
    propagating an exception that could take down the Security Gate
    pipeline. One family's failure never suppresses another's real
    evidence -- both are attempted independently and their results
    concatenated."""
    runs: list[dict[str, Any]] = []
    try:
        runs.extend(collect_semgrep_runs())
    except Exception:  # noqa: BLE001 -- absolute safety net, see docstring
        pass
    try:
        runs.extend(collect_gitleaks_runs())
    except Exception:  # noqa: BLE001 -- absolute safety net, see docstring
        pass
    try:
        runs.extend(collect_deterministic_repo_runs())
    except Exception:  # noqa: BLE001 -- absolute safety net, see docstring
        pass
    try:
        runs.extend(collect_sensitive_path_fetch_scenario_runs())
    except Exception:  # noqa: BLE001 -- absolute safety net, see docstring
        pass
    try:
        runs.extend(collect_github_review_runs())
    except Exception:  # noqa: BLE001 -- absolute safety net, see docstring
        pass
    return runs


def main() -> int:
    print(json.dumps(collect_trusted_runs(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
