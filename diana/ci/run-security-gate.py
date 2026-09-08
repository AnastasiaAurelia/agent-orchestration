#!/usr/bin/env python3
"""CI-controlled Security Gate orchestrator (Security Phase 5).

Runs the TRUSTED security policy/evaluator implementation -- catalog.json,
validate_catalog.py, evidence_model.py, security_bundle.py,
security_reducer.py, ci_verifier_runs.py -- extracted from the pull
request's PROTECTED BASE SHA, never the PR head. This is the whole point
of "prevent security self-certification" (Security Phase 5, section 3): a
PR that modifies any of those files must not be able to use ITS OWN
modified version to judge itself. The trusted evaluator always comes from
whatever was already merged to the base branch before this PR existed.

    PR HEAD                         PROTECTED BASE SHA
      |                                    |
    target being analyzed          trusted security policy/
    (repository, base_sha,         catalog/reducer/evaluator
     target_sha identify it)       code, extracted here and
                                    run in isolation from the
                                    PR head's working tree

## Bootstrap exception (read before "fixing" a bootstrap skip)

The Security Phase 5 PR that first introduces these six files has, by
definition, no such files at its OWN base commit -- there is nothing yet
to extract. That is expected and explicitly detected here, not silently
papered over: this script emits `{"decision": "SKIPPED_BOOTSTRAP", ...}`
when any TRUSTED_FILES path is missing from the base extraction. Security
Phase 5's own PR does NOT claim this integration retroactively protects
itself -- that PR remains protected only by the pre-existing Diana Gate
(DoD/verification/Preflight/diff-risk/human_only_conditions) plus the
required human/code-owner review, exactly as before this phase existed
(`diana-gate.py`'s `combine_with_security()` treats "SKIPPED_BOOTSTRAP"
as a pure pass-through: zero penalty, zero escalation). After this PR
merges, every subsequent PR's base includes these files, so the bootstrap
branch is never taken again under ordinary operation.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

TRUSTED_FILES = (
    "diana/security/catalog.json",
    "diana/security/validate_catalog.py",
    "diana/security/evidence_model.py",
    "diana/security/security_bundle.py",
    "diana/security/security_reducer.py",
    "diana/security/ci_verifier_runs.py",
)


class OrchestrationError(RuntimeError):
    pass


def _safe_extractall(tar: tarfile.TarFile, dest: Path) -> None:
    """Defensive extraction guard: refuse any member whose resolved path
    would land outside `dest`. `git archive` of a real repository tree
    cannot actually produce such a member (git does not track paths with
    `..` components), but this makes that invariant an enforced check
    rather than an assumed one."""
    dest_resolved = dest.resolve()
    for member in tar.getmembers():
        member_path = (dest / member.name).resolve()
        if member_path != dest_resolved and dest_resolved not in member_path.parents:
            raise OrchestrationError(f"refusing to extract unsafe tar member path: {member.name!r}")
    tar.extractall(path=dest)  # noqa: S202 -- members individually validated above


def extract_trusted_tree(repo_root: str, base_sha: str, dest: Path) -> bool:
    """Extracts diana/security/ as it existed at base_sha into dest.
    Returns True iff every TRUSTED_FILES path exists after extraction (a
    real, evaluable protected-base implementation); False means the
    Security Phase 5 bootstrap case (not an error -- see module
    docstring). Raises OrchestrationError if `git archive` itself fails
    (e.g. an invalid base_sha) -- that IS an error, distinct from
    bootstrap."""
    result = subprocess.run(
        ["git", "-C", repo_root, "archive", base_sha, "--", "diana/security"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        if "did not match any files" in stderr:
            # diana/security/ does not exist at all at this base -- an
            # even earlier form of the bootstrap case (some trusted files
            # missing) than TRUSTED_FILES's own presence check below can
            # detect, since there is nothing to extract at all. Not a
            # real error.
            return False
        raise OrchestrationError(f"git archive of base {base_sha!r} failed: {stderr}")
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as tar:
        _safe_extractall(tar, dest)
    return all((dest / path).is_file() for path in TRUSTED_FILES)


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    return result


def run_trusted_pipeline(dest: Path, repository: str, base_sha: str, target_sha: str) -> dict:
    """Runs the base-extracted, trusted ci_verifier_runs.py ->
    security_bundle.py -> security_reducer.py pipeline, entirely from
    files under `dest` (never the PR head's working tree)."""
    security_dir = dest / "diana" / "security"

    runs_proc = _run([sys.executable, str(security_dir / "ci_verifier_runs.py")])
    if runs_proc.returncode != 0:
        return {
            "decision": "FAIL",
            "reasons": [
                f"trusted ci_verifier_runs.py failed: {runs_proc.stderr.decode('utf-8', 'replace').strip()}"
            ],
        }

    with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as runs_file:
        runs_file.write(runs_proc.stdout)
        runs_path = runs_file.name
    try:
        bundle_proc = _run(
            [
                sys.executable,
                str(security_dir / "security_bundle.py"),
                str(security_dir / "catalog.json"),
                runs_path,
                repository,
                base_sha,
                target_sha,
            ]
        )
    finally:
        os.unlink(runs_path)

    if bundle_proc.returncode != 0:
        return {
            "decision": "FAIL",
            "reasons": [
                f"trusted security_bundle.py build failed: {bundle_proc.stdout.decode('utf-8', 'replace').strip()}"
            ],
        }

    with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as bundle_file:
        bundle_file.write(bundle_proc.stdout)
        bundle_path = bundle_file.name
    try:
        reducer_proc = _run(
            [
                sys.executable,
                str(security_dir / "security_reducer.py"),
                str(security_dir / "catalog.json"),
                bundle_path,
                repository,
                base_sha,
                target_sha,
            ]
        )
    finally:
        os.unlink(bundle_path)

    try:
        return json.loads(reducer_proc.stdout.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {
            "decision": "FAIL",
            "reasons": [f"trusted security_reducer.py produced unparseable output: {exc}"],
        }


def run_security_gate(repo_root: str, base_sha: str, target_sha: str, repository: str) -> dict:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            available = extract_trusted_tree(repo_root, base_sha, dest)
            if not available:
                return {
                    "decision": "SKIPPED_BOOTSTRAP",
                    "reasons": [
                        f"trusted security evaluator not present at base {base_sha} -- "
                        "Security Phase 5 bootstrap PR, or a base predating Phase 5; this "
                        "PR's protection relies on the existing Diana Gate + required human "
                        "review only (see diana/ci/run-security-gate.py)"
                    ],
                }
            return run_trusted_pipeline(dest, repository, base_sha, target_sha)
    except OrchestrationError as exc:
        return {"decision": "FAIL", "reasons": [f"security gate orchestration failed: {exc}"]}


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        result = {
            "decision": "FAIL",
            "reasons": ["usage: run-security-gate.py <repo_root> <base_sha> <target_sha> <repository>"],
        }
        print(json.dumps(result, sort_keys=True))
        return 1

    repo_root, base_sha, target_sha, repository = argv
    result = run_security_gate(repo_root, base_sha, target_sha, repository)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
