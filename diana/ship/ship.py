#!/usr/bin/env python3
"""Diana ship: thin, deterministic glue for the single-worker MVP pipeline.

Composes existing Diana components - it does not reimplement any of them:

    diana/adapters/ao.py        - AO compatibility/worker lifecycle
    diana/preflight/preflight.py + reduce_for_gate.py - quality/safety scan
    diana/gate/diana-gate.py    - merge-boundary decision
    diana/playwright/browser_applicable.py - browser-facing signal
    gh                          - PR creation, never AO, never a private API

What this script does NOT do, by design:

- It does not spawn or drive an AO worker session, and it does not resolve
  AO's attended tool-call approvals. Spawning is exactly `diana/adapters/
  ao.py spawn`; approving a worker's real mutations requires a live human
  in the loop (see MEMORY.md section 5 / diana/adapters/README.md), which
  this deterministic, testable script structurally cannot provide and must
  not fake. The calling session drives that part directly, the same way
  Phase 6 did, then hands this script the resulting branch/commit to
  verify.
- It does not decide risk classification or author the Definition of Done
  - both are supplied as input, exactly like diana-gate.py never invents a
  diff's risk tier itself.
- It never merges anything. There is no merge code path in this file.

Subcommands (each prints one JSON object to stdout, exits 0 ok / 1 not ok):

    precheck    - repo-dirty check, risk/human-only gate, AO compatibility
    verify-diff - base..worker-ref scope check (in-scope vs forbidden paths)
    gate        - browser applicability + preflight + Diana Gate decision
    open-pr     - gh pr create with a build-gate-input.py-compatible
                  DIANA:EVIDENCE block, only once the gate stage says PASS
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AO_ADAPTER = REPO_ROOT / "diana" / "adapters" / "ao.py"
DEFAULT_PREFLIGHT = REPO_ROOT / "diana" / "preflight" / "preflight.py"
DEFAULT_REDUCE = REPO_ROOT / "diana" / "preflight" / "reduce_for_gate.py"
DEFAULT_GATE = REPO_ROOT / "diana" / "gate" / "diana-gate.py"
DEFAULT_BROWSER_APPLICABLE = REPO_ROOT / "diana" / "playwright" / "browser_applicable.py"

RISKS = {"SAFE", "CONSEQUENTIAL", "DANGEROUS", "HUMAN_ONLY"}

# Paths a worker must never touch for a Phase-9-class SAFE single-feature
# task, regardless of what --allowed-files claims - defense in depth against
# a misconfigured or overbroad scope declaration. Mirrors the components
# this pipeline itself depends on plus diana-gate.py's own sensitive-path
# escalation list (diana/gate/diana-gate.py's REVIEW_PATHS/REVIEW_PREFIXES).
FORBIDDEN_PREFIXES = (
    "diana/gate/",
    "diana/adapters/",
    "diana/governance/",
    ".github/workflows/",
    ".github/CODEOWNERS",
    "infra/production/",
    "migrations/production/",
)


def ok(stage: str, **fields: Any) -> dict:
    return {"ok": True, "stage": stage, **fields}


def fail(stage: str, code: str, detail: str, **fields: Any) -> dict:
    return {"ok": False, "stage": stage, "error": code, "detail": detail, **fields}


def run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def load_json_arg(raw: str | None, default: Any) -> Any:
    if raw is None:
        return default
    return json.loads(raw)


def cmd_precheck(args: argparse.Namespace) -> dict:
    repo = Path(args.repo)
    if not repo.is_dir():
        return fail("precheck", "repo_missing", f"repo path is not a directory: {repo}")

    status = run(["git", "-C", str(repo), "status", "--porcelain"])
    if status.returncode != 0:
        return fail("precheck", "repo_unreadable", status.stderr.strip() or "git status failed")
    if status.stdout.strip():
        return fail("precheck", "repo_dirty", "repository has uncommitted changes")

    if args.risk not in RISKS:
        return fail("precheck", "invalid_risk", f"unknown risk tier: {args.risk!r}")

    human_only_conditions = load_json_arg(args.human_only_conditions, [])
    if not isinstance(human_only_conditions, list):
        return fail("precheck", "invalid_input", "human-only-conditions must be a JSON array")

    if args.risk != "SAFE" or human_only_conditions:
        # Per MEMORY.md section 5 / diana/governance/risk-tiers.md: this
        # pipeline's certified worker profile is SAFE-only. It refuses to
        # proceed toward a spawn for anything else rather than weakening
        # classification to let a task through.
        return fail(
            "precheck", "requires_human_review",
            "task risk is not SAFE, or human-only conditions apply; "
            "this pipeline only proceeds to spawn for a SAFE task with no "
            "human-only conditions",
            decision="REQUIRE_HUMAN",
        )

    ao_adapter = args.ao_adapter
    ao_check_cmd = ["python3", ao_adapter, "check"]
    if args.ao_bin:
        ao_check_cmd += ["--ao-bin", args.ao_bin]
    if args.ao_home:
        ao_check_cmd += ["--ao-home", args.ao_home]
    result = run(ao_check_cmd)
    try:
        ao_result = json.loads(result.stdout)
    except json.JSONDecodeError:
        return fail("precheck", "ao_unavailable", result.stdout.strip() or result.stderr.strip())
    if not ao_result.get("ok"):
        return fail(
            "precheck", "ao_unavailable",
            f"{ao_result.get('error')}: {ao_result.get('detail')}",
        )

    return ok("precheck", ready_to_spawn=True, ao_version=ao_result.get("version"))


def cmd_verify_diff(args: argparse.Namespace) -> dict:
    repo = args.repo
    diff = run(["git", "-C", repo, "diff", "--name-only", args.base, args.worker_ref])
    if diff.returncode != 0:
        return fail("verify-diff", "diff_failed", diff.stderr.strip() or "git diff failed")
    files = [f for f in diff.stdout.splitlines() if f]
    if not files:
        return fail("verify-diff", "empty_diff", "worker branch has no changes versus base")

    forbidden_hit = [f for f in files if f.startswith(FORBIDDEN_PREFIXES)]
    if forbidden_hit:
        return fail(
            "verify-diff", "forbidden_scope_touched",
            "diff touches a path this pipeline never allows a worker to change",
            files=forbidden_hit,
        )

    allowed = load_json_arg(args.allowed_files, None)
    if allowed is None:
        return fail("verify-diff", "invalid_input", "--allowed-files is required")
    if not isinstance(allowed, list) or not allowed:
        return fail("verify-diff", "invalid_input", "--allowed-files must be a non-empty JSON array")

    def in_scope(path: str) -> bool:
        return any(path == entry or path.startswith(entry) for entry in allowed)

    unexpected = [f for f in files if not in_scope(f)]
    if unexpected:
        return fail(
            "verify-diff", "out_of_scope_diff",
            "diff touches files outside the declared expected scope",
            files=unexpected,
        )

    return ok("verify-diff", files=files)


def cmd_gate(args: argparse.Namespace) -> dict:
    files = load_json_arg(args.files, None)
    if not isinstance(files, list) or not files:
        return fail("gate", "invalid_input", "--files must be a non-empty JSON array")

    browser_input = json.dumps({"files": files})
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write(browser_input)
        browser_input_path = fh.name
    try:
        browser_result = run(["python3", args.browser_applicable_script, browser_input_path])
    finally:
        Path(browser_input_path).unlink(missing_ok=True)

    try:
        browser = json.loads(browser_result.stdout)
    except json.JSONDecodeError:
        return fail("gate", "browser_applicability_failed", browser_result.stdout.strip())

    browser_evidence = load_json_arg(args.browser_evidence, [])
    if browser.get("applicable"):
        if not browser_evidence:
            return fail(
                "gate", "browser_verification_missing",
                "diff is browser-facing but no Playwright verification evidence was supplied",
                browser_reasons=browser.get("reasons"),
            )
        browser_status = "verified"
    else:
        browser_status = "skipped_not_applicable"

    preflight_result = run(["python3", args.preflight_script, args.repo])
    try:
        preflight_json = json.loads(preflight_result.stdout)
    except json.JSONDecodeError:
        return fail("gate", "preflight_failed", preflight_result.stdout.strip())
    if "checks" not in preflight_json:
        return fail("gate", "preflight_failed", preflight_json.get("error", "preflight produced no checks"))

    reduce_result = subprocess.run(
        ["python3", args.reduce_script],
        input=json.dumps(preflight_json), capture_output=True, text=True, timeout=30,
    )
    try:
        reduced = json.loads(reduce_result.stdout)
    except json.JSONDecodeError:
        return fail("gate", "preflight_reduce_failed", reduce_result.stdout.strip())

    dod = load_json_arg(args.dod, None)
    verification = load_json_arg(args.verification, None)
    if dod is None or verification is None:
        return fail("gate", "invalid_input", "--dod and --verification are required")
    human_only_conditions = load_json_arg(args.human_only_conditions, [])

    gate_input = {
        "version": 1,
        "dod": dod,
        "verification": verification,
        "preflight": reduced,
        "diff": {"risk": args.risk, "files": files},
        "human_only_conditions": human_only_conditions,
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(gate_input, fh)
        gate_input_path = fh.name
    try:
        gate_result = run(["python3", args.gate_script, gate_input_path])
    finally:
        Path(gate_input_path).unlink(missing_ok=True)

    try:
        decision = json.loads(gate_result.stdout)
    except json.JSONDecodeError:
        return fail("gate", "gate_failed", gate_result.stdout.strip())

    evidence = {
        "dod": dod,
        "verification": verification,
        "preflight": reduced,
        "risk": args.risk,
        "human_only_conditions": human_only_conditions,
    }
    if decision.get("decision") != "PASS":
        return fail(
            "gate", "gate_not_pass",
            f"Diana Gate decision: {decision.get('decision')}",
            gate_decision=decision.get("decision"),
            gate_reasons=decision.get("reasons"),
            browser_status=browser_status,
            evidence=evidence,
        )

    return ok(
        "gate",
        gate_decision="PASS",
        browser_status=browser_status,
        preflight=preflight_json,
        evidence=evidence,
        files=files,
    )


def cmd_open_pr(args: argparse.Namespace) -> dict:
    gate_evidence_raw = load_json_arg(args.gate_evidence, None)
    if gate_evidence_raw is None:
        return fail("open-pr", "invalid_input", "--gate-evidence is required (the gate stage's evidence object)")
    evidence = {
        "dod": gate_evidence_raw["dod"],
        "verification": gate_evidence_raw["verification"],
        "preflight": gate_evidence_raw["preflight"],
        "risk": gate_evidence_raw["risk"],
        "human_only_conditions": gate_evidence_raw["human_only_conditions"],
    }
    body = (
        f"{args.body_preamble}\n\n"
        "NO AUTO MERGE PERFORMED. This pull request stops here for human review.\n\n"
        "<!-- DIANA:EVIDENCE\n"
        f"{json.dumps(evidence, sort_keys=True)}\n"
        "DIANA:EVIDENCE -->"
    )
    cmd = [
        args.gh_bin, "pr", "create",
        "--base", args.base_branch,
        "--head", args.head_branch,
        "--title", args.title,
        "--body", body,
    ]
    result = run(cmd, timeout=60)
    if result.returncode != 0:
        return fail("open-pr", "pr_creation_failed", result.stderr.strip() or result.stdout.strip())
    return ok("open-pr", pr_url=result.stdout.strip(), merged=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("precheck")
    p.add_argument("--repo", required=True)
    p.add_argument("--risk", required=True)
    p.add_argument("--human-only-conditions")
    p.add_argument("--ao-adapter", default=str(DEFAULT_AO_ADAPTER))
    p.add_argument("--ao-bin")
    p.add_argument("--ao-home")
    p.set_defaults(func=cmd_precheck)

    p = sub.add_parser("verify-diff")
    p.add_argument("--repo", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--worker-ref", required=True)
    p.add_argument("--allowed-files", required=True)
    p.set_defaults(func=cmd_verify_diff)

    p = sub.add_parser("gate")
    p.add_argument("--repo", required=True)
    p.add_argument("--files", required=True)
    p.add_argument("--dod", required=True)
    p.add_argument("--verification", required=True)
    p.add_argument("--risk", required=True)
    p.add_argument("--human-only-conditions")
    p.add_argument("--browser-evidence")
    p.add_argument("--preflight-script", default=str(DEFAULT_PREFLIGHT))
    p.add_argument("--reduce-script", default=str(DEFAULT_REDUCE))
    p.add_argument("--gate-script", default=str(DEFAULT_GATE))
    p.add_argument("--browser-applicable-script", default=str(DEFAULT_BROWSER_APPLICABLE))
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("open-pr")
    p.add_argument("--gate-evidence", required=True)
    p.add_argument("--base-branch", default="main")
    p.add_argument("--head-branch", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body-preamble", required=True)
    p.add_argument("--gh-bin", default="gh")
    p.set_defaults(func=cmd_open_pr)

    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    result = args.func(args)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
