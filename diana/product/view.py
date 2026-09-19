#!/usr/bin/env python3
"""Diana M7: view-models, mechanically derived from authoritative objects.

**Nothing here is written by a model, and nothing here parses model prose to
decide a permission** (M7-U2). Every line is a pure function of the contract,
the work-item document, the journal or the run report. A hallucinated summary of
authority is not merely discouraged -- it is unreachable, because the renderers
take those objects and there is no text input to mislead them.

The translation is from internal vocabulary to user vocabulary, never from
internal authority to a looser one: "Can edit files under src/" says exactly
what `write_scope.allowed_roots` grants, and "Cannot edit" is the union of the
denied subpaths and the paths an exclusion withheld.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("unattended", "runtime"):
    sys.path.insert(0, str(_HERE.parent / _sub))
import journal as _journal  # noqa: E402

TICK, ARROW, DOT, CROSS = "✓", "→", "○", "✗"

# Reason codes translated for a reader who does not know M1-M6. The mapping is
# fixed; an unknown code is shown verbatim rather than guessed at.
_REASON_PLAIN = {
    "reconciliation-mismatch": "files were changed outside the approved area",
    "hermes-turn-failed": "the working turn did not finish",
    "attempt-budget-exhausted": "the approved number of attempts was used up",
    "run-deadline-exceeded": "the approved time limit was reached",
    "dependency-blocked": "an earlier step it depends on did not finish",
    "review-verdict-absent": "the reviewer produced no verdict",
    "target-moved": "the repository changed underneath the run",
    "quiescence-not-proven": "a process from this run could not be shown to have stopped",
    "run-cancelled": "the run was cancelled",
}


def _plain(code):
    return _REASON_PLAIN.get(code, code or "")


def _rel(paths, repo_root):
    out = []
    for p in paths or []:
        try:
            out.append(str(Path(p).relative_to(repo_root)))
        except ValueError:
            out.append(str(p))
    return out


def plan_view(proposal: dict) -> str:
    """GOAL / PLAN / AUTHORITY, derived from the predicted contract."""
    contract = proposal["predicted_contract"]
    envelope = contract["capability_envelope"]
    repo = proposal["repo_root"]
    items = proposal["predicted_items"]["items"]
    roles = proposal["predicted_topology"]["roles"]

    can_edit = _rel((envelope.get("write_scope") or {}).get("allowed_roots"), repo)
    cannot = list((envelope.get("write_scope") or {}).get("denied_subpaths") or [])
    cannot += [f"{p}  (you excluded this)" for p in proposal.get("withheld_by_exclusion", [])]
    commands = envelope.get("allowed_commands") or []

    lines = ["", "GOAL", f"  {contract['task']}", "", "PLAN"]
    for n, item in enumerate(items, 1):
        lines.append(f"  {n}. {item['task']}"
                     + (f"   (after {', '.join(item['depends_on'])})" if item["depends_on"] else ""))
    lines.append(f"  then: {' then '.join(r.lower() for r in roles)}")
    lines += ["", "AUTHORITY",
              f"  Can read    {', '.join(_rel(contract['read_scope']['allowed_roots'], repo)) or '.'}"
              f" (this repository)"]
    lines.append(f"  Can edit    {', '.join(can_edit) if can_edit else '(nothing)'}")
    lines.append(f"  Cannot edit {', '.join(cannot) if cannot else '(nothing else)'}")
    lines.append(f"  Can run     {', '.join(repr(c) for c in commands) if commands else '(nothing)'}")
    lines.append(f"  Cannot      anything not listed above — no network, no deploy, "
                 f"no merge, no credentials")
    lines.append(f"  Limits      {proposal['budget']['max_attempts']} attempts, "
                 f"{proposal['budget']['total_seconds']} seconds from run creation")
    lines.append(f"  Stop grace  {proposal['predicted_policy']['quiescence_grace_seconds']} seconds")
    lines += ["", "APPROVAL REQUIRED",
              f"  Risk {contract['risk']} · needs your explicit approval before anything runs.",
              f"  This approves starting this run only. It is not a merge, deploy or "
              f"review approval.",
              "", f"  {proposal['proposal_digest']}", ""]
    return "\n".join(lines)


def progress_view(run_directory, repo_root=None) -> str:
    """PROGRESS, derived from the journal and nothing else (M7-U4)."""
    record = _journal.read(run_directory)
    items = record["items"]
    lines = ["", "PROGRESS", f"  run {record['run_id']}   state {record['state'].lower()}"]
    for item_id, entry in sorted(items.items()):
        status = entry["status"]
        mark = {"COMPLETE": TICK, "RUNNING": ARROW, "PENDING": DOT, "BLOCKED": CROSS}[status]
        note = ""
        if status == "BLOCKED":
            note = f"   — {_plain(entry.get('reason_code'))}"
        elif entry.get("attempts"):
            note = f"   ({entry['attempts']} attempt{'s' if entry['attempts'] != 1 else ''})"
        lines.append(f"  {mark} {item_id}{note}")
    attempts = record.get("attempts") or []
    if attempts:
        last = attempts[-1]
        role = (last.get("actor") or "").lower()
        lines.append(f"  active role: {role or 'none'}"
                     f"   attempt {last['attempt']}"
                     f"   {'open' if last['state'] == 'OPEN' else 'closed'}")
    if record.get("cancellation"):
        lines.append(f"  cancelled: {record['cancellation'].get('reason', '')}")
    lines.append("")
    return "\n".join(lines)


def result_view(report: dict, repo_root=None) -> str:
    """RESULT, derived from the run report (M7-U5), fact and explanation split."""
    outcome = report["outcome"]
    lines = ["", "RESULT", f"  {outcome}"]
    touched = sorted({p for a in report.get("attempts", []) for p in a.get("paths_touched", [])})
    verified = [a for a in report.get("attempts", []) if a.get("within_envelope") is True]
    lines.append(f"  files changed        {len(touched)}"
                 + (f"   {', '.join(touched[:6])}" if touched else ""))
    lines.append(f"  attempts             {len(report.get('attempts', []))}"
                 f"   ({len(verified)} stayed inside the approved area)")
    roles = [a.get("actor") for a in report.get("attempts", []) if a.get("actor")]
    if roles:
        lines.append(f"  who worked           {' → '.join(r.lower() for r in roles)}")
    budget = report.get("budget") or {}
    if budget:
        lines.append(f"  budget               {budget.get('attempts_used')} of "
                     f"{budget.get('max_attempts')} attempts")
    blocked = report.get("blocked_items") or []
    if blocked:
        lines.append("")
        lines.append("  BLOCKED — a human decision is needed")
        for b in blocked:
            lines.append(f"    what was attempted   {b.get('what_was_attempted')}")
            lines.append(f"    refused by           {b.get('refused_by')}")
            lines.append(f"    why                  {_plain(b.get('reason_code'))}")
            lines.append(f"    detail               {b.get('detail', '')}")
            lines.append(f"    you must decide      {b.get('human_decision_required')}")
            widens = b.get("would_widen_authority")
            lines.append(f"    would that widen what you approved?  "
                         f"{'YES — a new approval and a new run are required' if widens else 'no'}")
    lines += ["", "  The lines above are Diana's own record of what happened.", ""]
    return "\n".join(lines)


def blocked_widens(blocked_item: dict) -> bool:
    """M7-D16's seventh question, COMPUTED rather than asserted.

    A decision widens the approved envelope when satisfying it would require
    authority the contract did not grant: a path outside `write_scope`, or a
    control that refused because the diff left the envelope. Anything else --
    an exhausted budget, a failed turn, a dependency -- is answerable inside the
    authority already approved.
    """
    if blocked_item.get("paths_outside_write_scope"):
        return True
    return blocked_item.get("reason_code") == "reconciliation-mismatch"
