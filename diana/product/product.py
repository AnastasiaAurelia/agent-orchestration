#!/usr/bin/env python3
"""Diana M7: the product CLI -- the runtime's first production entry point.

Phase 0 finding F1: every caller of `unattended.approve`, `actors.approve`,
`actors.execute` and `advisory.run.execute` was an acceptance suite. The M1-M6
runtime was complete, proven, and unreachable. This file is the entry point it
never had.

## One path, not a fast path

M7-D18. This CLI calls `actors.approve` and `actors.execute` -- the accepted M6
entry points -- and reimplements none of them. There is no product-specific
route around contract validation, projection, role shape, freshness, the
journal, reconciliation, the run lease, actor identity, budgets, reviewer
semantics or blocked handling, because there is no second route at all.

## Why the executor backend is a flag and the authority is not

M6-D21 froze the executor as a replaceable seam that carries no authority, and
M6 proved a backend switch creates no envelope and no budget. `--executor`
therefore selects who does the typing; it cannot select what may be typed. Every
backend runs under the same projections, proven live before each turn.

Usage:
    diana-do "<goal>"                 propose, and show Goal / Plan / Authority
    diana-do approve <digest>         approve that exact proposal, then run it
    diana-do show <digest>            show a proposal again
    diana-do status <run-id>          progress, from Diana's own journal
    diana-do result <run-id>          result, from Diana's own run report
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("runtime", "unattended", "mutation", "multiactor", "adapters", "profile"):
    sys.path.insert(0, str(_HERE.parent / _sub))
sys.path.insert(0, str(_HERE))

import blocking  # noqa: E402
import journal as _journal  # noqa: E402
import report as _report  # noqa: E402
import recovery as _recovery  # noqa: E402
import runpolicy as _runpolicy  # noqa: E402
import actors as _actors  # noqa: E402
import executors as _executors  # noqa: E402
import proposal as _proposal  # noqa: E402
import refusal as _ref  # noqa: E402
import view as _view  # noqa: E402

EXIT_OK, EXIT_REFUSED, EXIT_BLOCKED = 0, 2, 3


def _runs_base() -> str | None:
    return os.environ.get("DIANA_RUNS_BASE") or None


def _run_dir(run_id: str) -> Path:
    base = _runs_base()
    root = Path(base) if base else Path(os.path.expanduser("~/.diana/runs"))
    return root / run_id


def _verify_for(contract_block):
    """Diana's OWN verification (M7/M6-R2): the reviewer never runs commands."""
    commands = contract_block["capability_envelope"].get("allowed_commands") or []
    root = contract_block["target"]["repo_root"]

    def verify(cb, item_id=None):
        if not commands:
            return False
        proc = subprocess.run(commands[0], shell=True, cwd=root,
                              capture_output=True, timeout=300)
        return proc.returncode == 0
    return verify


def _backends(kind: str, contract_block, run_directory=None):
    """(builder, reviewer). The seam M6 froze; carries no authority."""
    verify = _verify_for(contract_block)
    if kind == "hermes":
        # The reviewer's evidence is composed by M6's `evidence_builder` from
        # the digest-covered journal and the reconciliation record of the exact
        # builder attempt under review. It was previously a literal --
        # `paths_touched: []`, `within_envelope: None`,
        # `verification_passed: True` -- which told the production reviewer that
        # a build it was asked to judge had changed nothing and had passed. A
        # reviewer cannot be a completion gate on evidence that is not true, and
        # a hardcoded `verification_passed: True` is an approval Diana never
        # measured.
        builder = _executors.HermesBuilder()
        reviewer = _executors.HermesReviewer(
            evidence_for=_executors.evidence_builder(
                run_directory, lambda: _journal.read(run_directory), verify))
        return builder, reviewer
    # A deterministic in-repository backend. It types; it does not decide what
    # it may type -- the projection does, and it is proven live before its turn.

    # TEST-ONLY SEAM, on the M2-D7 precedent: a scripted edit map so acceptance
    # can drive a real run to a real terminal outcome without a live model
    # deciding to cooperate. It grants NOTHING -- every write still passes the
    # installed projection and is still reconciled afterwards, which an
    # acceptance falsifier proves by scripting a write outside write_scope and
    # watching Diana block the run. A production invocation never sets it.
    edits = {}
    raw = os.environ.get("DIANA_PRODUCT_SCRIPTED_EDITS")
    if raw:
        edits = json.loads(raw)

    class DeterministicBuilder(_executors.ScriptedBuilder):
        name = "diana-deterministic-builder"

        def _run(self, cb, item_id=None):
            plan = edits.get(item_id or "item-1")
            if not plan:
                return None
            target = Path(cb["target"]["repo_root"]) / plan[0]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(plan[1])
            return None

    class DianaGatedReviewer(_executors.ScriptedReviewer):
        name = "diana-gated-reviewer"

        def _run(self, cb, item_id=None):
            ok = bool(verify(cb, item_id))
            self.verdict = {
                "decision": "PASS" if ok else "FAIL",
                "summary": "Diana's own verification " + ("passed" if ok else "did not pass"),
                "findings": [] if ok else [{"description": "verification did not pass",
                                            "evidence": "Diana ran the approved command"}],
                "dod_checks": [{"criterion": "approved verification command exits 0",
                                "result": "PASS" if ok else "FAIL",
                                "evidence": "Diana ran it, not the reviewer"}]}
            return None
    return DeterministicBuilder({}), DianaGatedReviewer([])


def cmd_propose(args) -> int:
    proposal = _proposal.build(args.goal, args.repo or os.getcwd(), base=args.proposals_base)
    print(_view.plan_view(proposal))
    # The launcher is not installed on PATH. Preserve storage and target context
    # with absolute, shell-quoted paths so this line works from another cwd.
    command = [str(_HERE.parent.parent / "diana-do"), "approve",
               proposal["proposal_digest"], "--repo", proposal["repo_root"],
               "--proposals-base", str(_proposal.proposals_dir(args.proposals_base).resolve().parent)]
    if args.executor != "hermes":
        command += ["--executor", args.executor]
    print(f"  To start it:   {shlex.join(command)}")
    print(f"  To do nothing: ignore this. Nothing has run and no run exists yet.\n")
    return EXIT_OK


def cmd_show(args) -> int:
    print(_view.plan_view(_proposal.load(args.digest, args.proposals_base)))
    return EXIT_OK


def cmd_approve(args) -> int:
    approved = _proposal.approve(args.digest, base=args.proposals_base,
                                 runs_base=_runs_base(), expected_repo=args.repo)
    run_directory = approved["run_directory"]
    print(f"\nAPPROVED  run {approved['run_id']}\n")
    loaded = _recovery.load_run(run_directory)
    builder, reviewer = _backends(args.executor, loaded["contract"], run_directory)
    try:
        _actors.execute(run_directory, builder=builder, reviewer=reviewer,
                        verify=_verify_for(loaded["contract"]))
    except blocking.Blocked as exc:
        print(_view.progress_view(run_directory))
        print(f"  stopped: {exc.code}\n")
    print(_view.progress_view(run_directory))
    return _emit_result(run_directory)


def _emit_result(run_directory) -> int:
    record = _journal.read(run_directory)
    contract_block, policy, items_doc = (
        _recovery.load_authority(run_directory, record))
    document = _report.build(record, contract_block, policy, run_directory, items_doc)
    for item in document.get("blocked_items") or []:
        item["would_widen_authority"] = _view.blocked_widens(item)
    print(_view.result_view(document, contract_block["target"]["repo_root"]))
    if document["outcome"] == "BLOCKED":
        return EXIT_BLOCKED
    return EXIT_OK


def cmd_status(args) -> int:
    print(_view.progress_view(_run_dir(args.run_id)))
    return EXIT_OK


def cmd_result(args) -> int:
    return _emit_result(_run_dir(args.run_id))


def _shared() -> argparse.ArgumentParser:
    """Options accepted in the same place whichever verb is used."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=argparse.SUPPRESS)
    common.add_argument("--proposals-base", default=argparse.SUPPRESS)
    common.add_argument("--executor", choices=("hermes", "deterministic"), default=argparse.SUPPRESS)
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _shared()
    parser = argparse.ArgumentParser(prog="diana-do", add_help=True, parents=[common])
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("approve", parents=[common]); p.add_argument("digest")
    p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("show", parents=[common]); p.add_argument("digest")
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("status", parents=[common]); p.add_argument("run_id")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("result", parents=[common]); p.add_argument("run_id")
    p.set_defaults(fn=cmd_result)
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    known = {"approve", "show", "status", "result"}
    # A bare goal is the normal path; a subcommand is the expert one.
    if argv and argv[0] not in known and not argv[0].startswith("-"):
        args = parser.parse_args(argv[1:])
        args.goal = argv[0]
        args.fn = cmd_propose
    else:
        args = parser.parse_args(argv)
        if not getattr(args, "fn", None):
            parser.print_help()
            return EXIT_OK
    # Suppressed shared defaults prevent subparser defaults from erasing options
    # supplied before the verb, especially an explicit expected repository.
    for name, default in (("repo", None), ("proposals_base", None), ("executor", "hermes")):
        if not hasattr(args, name):
            setattr(args, name, default)
    try:
        return args.fn(args)
    except _ref.Refused as exc:
        print(f"\nNOT PROPOSED  [{exc.code}]\n  {exc.detail}\n", file=sys.stderr)
        return EXIT_REFUSED
    except blocking.Blocked as exc:
        print(f"\nREFUSED BY DIANA  [{exc.code}]\n  {exc.detail}\n", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
