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
    autonomy = proposal.get("autonomy")
    if autonomy and autonomy.get("enabled"):
        allow, limits = autonomy["allow"], autonomy["limits"]
        yes = lambda flag: "yes" if allow.get(flag) else "no"
        lines += ["", "AUTONOMY",
                  "  Diana may recover from ordinary failures without asking again.",
                  f"  Same-scope retries          {yes('same_scope_retries')}",
                  f"  Narrower child runs         {yes('narrower_child_runs')}",
                  f"  Task splitting              {yes('task_splitting')}",
                  f"  Preserve partial work       {yes('preserve_verified_changes')}",
                  f"  Revert its own unverified   {yes('revert_owned_unverified_changes')}",
                  f"  Dependency changes          {yes('dependency_changes')}",
                  f"  Budgets                     {limits['max_child_runs']} child runs, "
                  f"depth {limits['max_child_depth']}, "
                  f"{limits['max_total_attempts']} attempts, "
                  f"{limits['max_wall_clock_seconds']}s, "
                  f"{limits['max_changed_files']} files, "
                  f"{limits['max_supervisor_calls']} diagnoses",
                  "", "ALWAYS ESCALATES",
                  "  Diana stops and asks you, whatever the recovery plan says:"]
        lines += [f"  {CROSS} {c.replace('_', ' ')}"
                  for c in proposal.get("predicted_standing", {}).get(
                      "human_only_conditions", [])]
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


# --- autonomous session outcome ------------------------------------------
#
# The escalation text is the whole point of the feature's failure path. "Human
# decision needed" is useless: a person returning to a stopped session needs to
# know what was tried, what remains, what is in their working tree, and exactly
# which additional authority would unblock it. Every line below is derived from
# the session outcome; none is written by a model.

_ESCALATION_PLAIN = {
    "authority-expansion-requested": "the next step needs authority you did not grant",
    "write-scope-not-subset": "the next step needs to write outside the approved area",
    "command-not-subset": "the next step needs to run a command you did not approve",
    "dependency-change-not-authorized": "the next step needs to change dependencies",
    "forbidden-capability-requested": "the next step needs a capability Diana never grants",
    "supervisor-requested-human": "the recovery planner says a person must decide",
    "supervisor-unavailable": "the recovery planner could not be consulted",
    "supervisor-timeout": "the recovery planner did not answer in time",
    "supervisor-malformed": "the recovery planner's answer could not be understood",
    "supervisor-unknown-decision": "the recovery planner proposed something Diana does not do",
    "supervisor-unknown-field": "the recovery planner's answer carried an unknown instruction",
    "supervisor-evidence-missing": "the recovery planner needed evidence Diana does not have",
    "child-budget-exhausted": "the approved number of recovery runs was used up",
    "child-depth-exceeded": "recovery reached the approved depth limit",
    "attempt-budget-exhausted": "the approved number of attempts was used up",
    "wall-clock-exhausted": "the approved time limit was reached",
    "changed-file-budget-exhausted": "the approved number of changed files was reached",
    "supervisor-call-budget-exhausted": "the approved number of diagnoses was used up",
    "no-progress": "recovery repeated itself without making progress",
    "provenance-ambiguous": "Diana could not prove who last changed a file",
    "revert-would-lose-work": "undoing a change would have destroyed work Diana does not own",
    "preserve-conflict": "a change could not be preserved as asked",
    "lineage-corrupt": "the recovery ledger no longer matches its own digest",
    "not-recoverable": "this failure has no bounded recovery",
    "repository-mismatch": "the next step named a different repository",
    "standing-approval-invalid": "the standing approval could not be read",
    "standing-approval-digest-mismatch": "the standing approval was changed after you approved it",
    "autonomy-disabled": "this run was approved in manual mode",
}


def autonomy_view(outcome: dict) -> str:
    """What the session did, and -- if it stopped -- exactly what it needs."""
    used, left = outcome["budget_used"], outcome["budget_remaining"]
    decisions = outcome.get("supervisor_decisions") or []
    accepted = [d for d in decisions if d.get("accepted")]
    lines = ["", "AUTONOMOUS SESSION",
             f"  Outcome            {outcome['outcome']}",
             f"  Runs               {outcome['runs_attempted']} "
             f"({len(outcome['completed_runs'])} completed)",
             f"  Recovery plans     {len(decisions)} requested, {len(accepted)} acted on",
             f"  Budget used        {used['child_runs']} child runs, "
             f"{used['attempts']} attempts, {used['wall_clock_seconds']}s, "
             f"{len(used['changed_files'])} files, "
             f"{used['supervisor_calls']} diagnoses",
             f"  Budget left        {left['child_runs']} child runs, "
             f"{left['attempts']} attempts, {left['wall_clock_seconds']}s, "
             f"{left['changed_files']} files, {left['supervisor_calls']} diagnoses"]

    lines += ["", "  WORKSPACE"]
    for label, paths, note in (
        ("verified", outcome["verified_changes"], "complete and checked"),
        ("preserved", outcome["preserved_changes"], "kept, NOT yet verified"),
        ("unverified", outcome["unverified_changes"], "left in place, NOT verified"),
        ("reverted", outcome["reverted_changes"], "undone by Diana"),
    ):
        if paths:
            lines.append(f"    {label:10} {', '.join(paths)}   ({note})")
    if not any(outcome[k] for k in ("verified_changes", "preserved_changes",
                                    "unverified_changes", "reverted_changes")):
        lines.append("    (nothing was changed)")

    lines += ["", "  RUNS",
              f"    root       {outcome['root_run_id']}"]
    for run_id in outcome["completed_runs"]:
        lines.append(f"    completed  {run_id}")
    lines.append("    (per-run detail: diana-do result <run-id>)")

    escalation = outcome.get("escalation")
    if not escalation:
        lines += ["", f"  {TICK} Finished without needing you.", ""]
        return "\n".join(lines)

    code = escalation["code"]
    lines += ["", "STOPPED FOR YOU",
              f"  Why                {_ESCALATION_PLAIN.get(code, code)}",
              f"  Reason code        {code}",
              f"  Detail             {escalation['detail']}"]
    requested = escalation.get("requested") or {}
    if "increase_limit" in requested:
        # A budget is the one escalation with an exact, mechanical remedy.
        lines += ["  Diana would need a larger budget:",
                  f"    {requested['increase_limit']}: currently "
                  f"{requested.get('current_value')}, "
                  f"{requested.get('used', requested.get('needed'))} used",
                  "  Everything else about the approval can stay the same."]
    elif requested:
        lines.append("  Diana would need:")
        for key, value in sorted(requested.items()):
            shown = ", ".join(str(v) for v in value) if isinstance(value, list) else value
            lines.append(f"    {key.replace('_', ' ')}: {shown}")
    else:
        lines.append("  No additional authority would help; this needs your judgement.")
    lines += ["",
              "  Approving this does NOT happen by re-running the same command: propose",
              "  again with the authority above included, review it, and approve that.",
              ""]
    return "\n".join(lines)
