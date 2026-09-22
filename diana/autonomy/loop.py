#!/usr/bin/env python3
"""Diana autonomy: the one-approval recovery loop.

    approve once -> build -> review -> recover -> split -> retry -> COMPLETE
    and only:      new authority required -> BLOCKED_FOR_HUMAN

## What this module is, and what it refuses to be

It is a SCHEDULER over `actors.execute`. It starts no turn itself, grants
nothing, and owns no boundary: every run it starts is an ordinary M6 run with an
ordinary approved contract, driven by the same entry point a manual run uses.
What it adds is the decision to start the next one -- and the proofs that must
hold first.

The pipeline is fixed and every step can only refuse:

    recommendation -> closed schema -> policy permits this decision
                   -> derive child contract (DIANA derives it, never the planner)
                   -> prove child ⊆ standing approval, on every axis
                   -> prove every cumulative budget still permits it
                   -> prove the workspace transition is safe
                   -> only then start the child

A failure at any step is an escalation. There is no step that infers permission,
and no step that narrows a child to make it fit -- a clamped over-broad child is
an over-broad child that reported green.

## Why the supervisor is consulted as rarely as possible

A successful run pays no supervisor round trip at all: the loop consults one
only when a run has finished in a state `classify` calls recoverable. The
normal path is unchanged from today, which is also what makes the feature safe
to leave on -- it is inert until something goes wrong.

## Why children run one at a time, in a fixed order

`SPLIT_TASK` children are validated as a SET before any of them starts (an
invalid child prevents the whole launch), then executed in the order the
supervisor listed them, which is the order a reader of the audit record sees.
Sequential execution is not a limitation here -- two children sharing a write
scope are two writers to one workspace, and provenance is only decidable when
one run at a time owns its changes.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("runtime", "unattended", "multiactor", "mutation", "product", "supervisors"):
    sys.path.insert(0, str(_HERE.parent / _sub))
sys.path.insert(0, str(_HERE))

import actors as _actors  # noqa: E402
import audit as _audit  # noqa: E402
import blocking as _blocking  # noqa: E402
import classify as _classify  # noqa: E402
import escalation as _esc  # noqa: E402
import journal as _journal  # noqa: E402
import lineage as _lineage  # noqa: E402
import policy as _policy  # noqa: E402
import provenance as _prov  # noqa: E402
import reconcile as _reconcile  # noqa: E402
import remediate as _remediate  # noqa: E402
import schema as _schema  # noqa: E402
import standing as _standing  # noqa: E402
import subset as _subset  # noqa: E402

# Which autonomy allow-flag each continuing decision needs. A decision whose
# flag is off is refused BEFORE anything is derived: the policy the human
# approved decides what kinds of recovery exist, not the planner.
DECISION_REQUIRES = {
    _schema.RETRY_SAME: "same_scope_retries",
    _schema.RETRY_NARROWER: "narrower_child_runs",
    _schema.SPLIT_TASK: "task_splitting",
    _schema.PRESERVE_AND_RETRY: "preserve_verified_changes",
    _schema.REVERT_AND_RETRY: "revert_owned_unverified_changes",
}

# Path fragments that mean "this change is a dependency change". Deliberately
# generic filenames, not an ecosystem list: Diana must not learn what a package
# manager is, only that a manifest is a different kind of file from source.
DEPENDENCY_MANIFEST_NAMES = (
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "requirements.txt", "pyproject.toml", "poetry.lock", "Pipfile", "Pipfile.lock",
    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock", "Gemfile", "Gemfile.lock",
    "composer.json", "composer.lock", "pom.xml", "build.gradle", "build.gradle.kts",
)


def _is_dependency_change(paths) -> list[str]:
    hits = []
    for path in paths or ():
        name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
        if name in DEPENDENCY_MANIFEST_NAMES:
            hits.append(str(path))
    return sorted(set(hits))


class Session:
    """One standing approval, its lineage, and the runs it may still start."""

    def __init__(self, *, lineage_dir, standing_doc, standing_digest, supervisor,
                 backends, verify, runs_base=None, clock=time.monotonic):
        self.dir = Path(lineage_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.standing = _standing.require(standing_doc, standing_digest)
        self.standing_digest = standing_digest
        self.autonomy = self.standing["autonomy"]
        self.supervisor = supervisor
        # `backends(contract_block, run_directory) -> (builder, reviewer)`. The
        # M6 seam, injected, so a test proves the loop against deterministic
        # executors and production proves it against Hermes -- one code path.
        self.backends = backends
        self.verify = verify
        self.runs_base = runs_base
        self.clock = clock
        self.repo_root = self.standing["repo_root"]
        self.history: list[tuple] = []

    # --- persistence ------------------------------------------------------
    def start(self, root_run_id: str, goal: str) -> dict:
        self.lineage = _lineage.create(root_run_id, self.standing_digest, goal=goal)
        _lineage.write(self.dir, self.lineage)
        self.provenance = _prov.begin(root_run_id, self.repo_root)
        _prov.write(self.dir, self.provenance)
        return self.lineage

    def load(self) -> dict:
        self.lineage = _lineage.read(self.dir)
        self.provenance = _prov.read(self.dir)
        return self.lineage

    def _save(self) -> None:
        _lineage.write(self.dir, self.lineage)
        _prov.write(self.dir, self.provenance)

    # --- one run ----------------------------------------------------------
    def _execute(self, run_directory) -> dict:
        """Run one M6 run to a terminal state. Diana's own entry point."""
        import recovery as _recovery

        started = self.clock()
        loaded = _recovery.load_run(run_directory)
        builder, reviewer = self.backends(loaded["contract"], run_directory)
        blocked = None
        try:
            _actors.execute(run_directory, builder=builder, reviewer=reviewer,
                            verify=self.verify)
        except _blocking.Blocked as exc:
            blocked = exc
        record = _journal.read(run_directory)
        elapsed = int(self.clock() - started)
        touched = self._touched(run_directory, record)
        return {"record": record, "blocked": blocked, "elapsed": elapsed,
                "changed_files": touched, "contract": loaded["contract"],
                "run_directory": run_directory}

    @staticmethod
    def _touched(run_directory, record) -> list[str]:
        """Every path this run's own reconciliations recorded."""
        out: set[str] = set()
        for attempt in record.get("attempts") or []:
            name = attempt.get("reconciliation_file")
            if not name:
                continue
            try:
                data = json.loads((Path(run_directory) / name).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out |= set(data.get("paths_touched") or ())
        return sorted(out)

    def _settle(self, outcome: dict, run_id: str) -> None:
        record = outcome["record"]
        self.lineage = _lineage.settle_run(
            self.lineage, run_id, state=record["state"],
            attempts=_journal.attempts_used(record),
            changed_files=outcome["changed_files"],
            wall_clock_seconds=outcome["elapsed"])
        self.provenance = _prov.observe_run(
            self.provenance, run_id=run_id,
            reconciliation={"paths_touched": outcome["changed_files"]},
            repo_root=self.repo_root)
        if record["state"] == _journal.COMPLETE:
            # Only a run that actually completed promotes anything, and only
            # because Diana's own verification is what produced that COMPLETE.
            #
            # Preserved work is promoted with it, and that is not generosity: the
            # verification command ran against a tree that CONTAINED the
            # preserved changes, so they are part of what passed. Leaving them
            # unverified would report a completed lineage still holding
            # unverified changes, which is a false statement about the workspace.
            preserved = [e["path"] for e in self.provenance["entries"]
                         if e["state"] == _prov.PRESERVED_FOR_CHILD]
            self.provenance = _prov.mark_verified(
                self.provenance, list(outcome["changed_files"]) + preserved)
        self._save()

    # --- consulting the planner ------------------------------------------
    def _consult(self, outcome: dict, run_id: str, reason: str) -> tuple[dict, dict]:
        import base as _sup_base

        _lineage.prove_supervisor_call(self.lineage, self.autonomy)
        budget_before = _lineage.remaining(self.lineage, self.autonomy)
        record = outcome["record"]
        terminal = record.get("terminal") or {}
        owned = [e["path"] for e in self.provenance["entries"]
                 if e["state"] in (_prov.OWNED_UNVERIFIED, _prov.PRESERVED_FOR_CHILD)]
        preexisting = [e["path"] for e in self.provenance["entries"]
                       if e["state"] == _prov.USER_PREEXISTING]
        evidence = _sup_base.build_evidence(
            root_run_id=self.lineage["root_run_id"], parent_run_id=run_id,
            original_goal=self.standing["original_goal"],
            current_goal=outcome["contract"]["task"],
            standing_doc=self.standing, current_contract=outcome["contract"],
            run_state=record["state"], failure_reason=terminal.get("detail", ""),
            attempt_history=[{"attempt": a["attempt"], "actor": a["actor"],
                              "state": a["state"], "within_envelope": a["within_envelope"],
                              "turn_error": (a["turn_error"] or "")[:200]}
                             for a in record.get("attempts") or []],
            changed_files=outcome["changed_files"], owned_changes=owned,
            preexisting_changes=preexisting,
            verification_passed=bool(self.verify(outcome["contract"], None)),
            verification_output="", reviewer_output=self._reviewer_text(outcome),
            builder_failure=terminal.get("detail", ""),
            budget_remaining=budget_before,
            workspace_provenance={e["path"]: e["state"]
                                  for e in self.provenance["entries"]},
            recovery_class=reason)
        decision_id = uuid.uuid4().hex[:12]
        try:
            recommendation = self.supervisor.recommend(evidence)
        except _esc.Escalation as exc:
            _audit.write(self.dir, _audit.rejected(
                decision_id=decision_id, root_run_id=self.lineage["root_run_id"],
                parent_run_id=run_id, evidence=evidence,
                provider=self.supervisor.name, model=getattr(self.supervisor, "model", None),
                code=exc.code, detail=exc.detail, budget_before=budget_before))
            self.lineage = _lineage.record_supervisor_call(
                self.lineage, {"decision_id": decision_id, "accepted": False,
                               "rejection_code": exc.code})
            self._save()
            raise
        self.lineage = _lineage.record_supervisor_call(
            self.lineage, {"decision_id": decision_id, "accepted": None,
                           "decision": recommendation["decision"]})
        self._save()
        return recommendation, {"decision_id": decision_id, "evidence": evidence,
                                "budget_before": budget_before}

    @staticmethod
    def _reviewer_text(outcome: dict) -> str:
        for path in sorted(Path(outcome["run_directory"]).glob("review-verdict-*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            verdict = data.get("verdict") or {}
            findings = "; ".join(f.get("description", "") for f in verdict.get("findings") or ())
            return f"{verdict.get('decision')}: {verdict.get('summary','')} {findings}"
        return ""

    # --- validating a recommendation -------------------------------------
    def _plan_children(self, recommendation: dict, parent_contract: dict) -> list[dict]:
        """The child goals and requested scopes this decision implies."""
        decision = recommendation["decision"]
        parent_env = parent_contract["capability_envelope"]
        parent_scope = list((parent_env.get("write_scope") or {}).get("allowed_roots") or ())
        parent_cmds = list(parent_env.get("allowed_commands") or ())
        if decision == _schema.SPLIT_TASK:
            return [{"goal": c["goal"],
                     "write_scope": c["requested_write_scope"] or parent_scope,
                     "commands": c["requested_commands"] or parent_cmds}
                    for c in recommendation["children"]]
        goal = recommendation["next_goal"] or parent_contract["task"]
        return [{"goal": goal,
                 "write_scope": recommendation["requested_write_scope"] or parent_scope,
                 "commands": recommendation["requested_commands"] or parent_cmds}]

    def _derive_child(self, plan: dict, run_id: str) -> dict:
        """DIANA builds the contract. The planner supplied words and requests."""
        import recovery as _recovery

        observed = _recovery.observe_target(self.repo_root)
        return _remediate.build_contract(
            task=plan["goal"], repo_root=self.repo_root,
            git_commit=observed["git_commit"], dirty=observed["dirty"],
            allowed_commands=tuple(plan["commands"]),
            write_roots=tuple(plan["write_scope"]), run_id=run_id)

    def validate_recommendation(self, recommendation: dict, parent_contract: dict,
                                parent_depth: int) -> list[dict]:
        """Every proof, in order. Returns validated child plans or escalates."""
        decision = recommendation["decision"]
        if recommendation["human_required"]:
            raise _esc.Escalation(
                _esc.SUPERVISOR_REQUESTED_HUMAN, recommendation["reason"])
        if decision == _schema.ESCALATE_HUMAN:
            raise _esc.Escalation(
                _esc.SUPERVISOR_REQUESTED_HUMAN, recommendation["reason"])
        if decision == _schema.REQUEST_MORE_EVIDENCE:
            # Diana hands the supervisor everything it has, deterministically.
            # "I need more" therefore means "I cannot decide from Diana's facts",
            # which is a human's problem, not a second round trip.
            raise _esc.Escalation(
                _esc.SUPERVISOR_EVIDENCE_MISSING,
                "the supervisor asked for evidence Diana does not have: "
                + recommendation["reason"])
        needed = DECISION_REQUIRES.get(decision)
        if needed is None or not _policy.allows(self.autonomy, needed):
            raise _esc.Escalation(
                _esc.AUTHORITY_EXPANSION_REQUESTED,
                f"{decision} needs autonomy permission {needed!r}, which this standing "
                "approval does not grant",
                requested={"autonomy_allow": needed})

        plans = self._plan_children(recommendation, parent_contract)
        depth = parent_depth + 1
        validated = []
        for index, plan in enumerate(plans):
            # Dependency policy is checked on the REQUEST, before any contract
            # exists: a manifest edit must have been authorised at approval time.
            deps = _is_dependency_change(plan["write_scope"])
            if deps and not _policy.allows(self.autonomy, "dependency_changes"):
                raise _esc.Escalation(
                    _esc.DEPENDENCY_CHANGE_NOT_AUTHORIZED,
                    f"child {index} asks to write dependency manifest(s) {deps}, which "
                    "this standing approval did not authorise",
                    requested={"dependency_paths": deps})
            child_run_id = str(uuid.uuid4())
            try:
                child_contract = self._derive_child(plan, child_run_id)
            except _blocking.Blocked as exc:
                raise _esc.Escalation(
                    _esc.WRITE_SCOPE_NOT_SUBSET,
                    f"child {index} could not be given a valid contract: {exc.detail}",
                    requested={"write_scope": plan["write_scope"]}) from None
            proof = _subset.prove_within_standing(child_contract, self.standing)
            _lineage.prove_budget_for_child(self.lineage, self.autonomy, depth=depth)
            validated.append({"contract": child_contract, "run_id": child_run_id,
                              "goal": plan["goal"], "proof": proof, "depth": depth})
        # A split is validated as a SET: one invalid child prevents the launch of
        # all of them, because the supervisor reasoned about them together.
        return validated

    # --- workspace transition --------------------------------------------
    def apply_workspace(self, recommendation: dict) -> dict:
        """Preserve and revert, each proven safe. Escalates rather than guessing."""
        applied = {"preserved": [], "reverted": [], "removed": []}
        if recommendation["preserve_changes"]:
            if not _policy.allows(self.autonomy, "preserve_verified_changes"):
                raise _esc.Escalation(
                    _esc.AUTHORITY_EXPANSION_REQUESTED,
                    "preserving changes is not permitted by this standing approval",
                    requested={"autonomy_allow": "preserve_verified_changes"})
            self.provenance = _prov.mark_preserved(
                self.provenance, recommendation["preserve_changes"])
            applied["preserved"] = list(recommendation["preserve_changes"])
        if recommendation["revert_changes"]:
            if not _policy.allows(self.autonomy, "revert_owned_unverified_changes"):
                raise _esc.Escalation(
                    _esc.AUTHORITY_EXPANSION_REQUESTED,
                    "reverting changes is not permitted by this standing approval",
                    requested={"autonomy_allow": "revert_owned_unverified_changes"})
            result = _prov.apply_revert(
                self.provenance, recommendation["revert_changes"], repo_root=self.repo_root)
            self.provenance = result["record"]
            applied["reverted"] = result["reverted"]
            applied["removed"] = result["removed"]
        self._save()
        return applied

    # --- the loop ---------------------------------------------------------
    def run(self, root_run_directory, root_run_id: str) -> dict:
        """Drive the whole lineage to COMPLETE or BLOCKED_FOR_HUMAN.

        The return value is the SESSION outcome, not a run outcome: a lineage
        whose root failed but whose children completed the work is COMPLETE, and
        a lineage that ran out of proof is BLOCKED_FOR_HUMAN with the exact
        reason and the exact additional authority it would have needed.
        """
        pending = [{"run_directory": Path(root_run_directory), "run_id": root_run_id,
                    "depth": 0, "is_root": True, "plan": None}]
        completed, escalation = [], None
        try:
            while pending:
                job = pending.pop(0)
                if job["plan"] is not None:
                    # Created HERE, not when the recommendation was validated. A
                    # split's children are validated together but materialised one
                    # at a time, because a run's target binding is taken at
                    # approval and the previous child has since changed the tree.
                    # Approving all of them up front bound the later ones to a
                    # workspace that no longer existed, and they failed freshness
                    # instead of running -- measured, not theorised.
                    job = self._materialise(job)
                outcome = self._execute(job["run_directory"])
                self._settle(outcome, job["run_id"])
                record = outcome["record"]
                kind, reason = _classify.classify_outcome(record)
                if kind == _classify.TERMINAL_SUCCESS:
                    completed.append(job["run_id"])
                    continue
                self.history.append(
                    (outcome["contract"]["task"],
                     (record.get("terminal") or {}).get("reason_code")))
                _classify.prove_progress(self.history)
                _classify.require_recoverable(record)
                children = self._recover(outcome, job, reason)
                # Depth-first, in the supervisor's stated order, so the audit
                # record reads in the order the work actually happened.
                pending = children + pending
        except _esc.Escalation as exc:
            escalation = exc
        return self._finish(completed, escalation, root_run_id)

    def _recover(self, outcome: dict, job: dict, reason: str) -> list[dict]:
        """Consult, validate, transition the workspace, create the children."""
        recommendation, context = self._consult(outcome, job["run_id"], reason)
        decision_id = context["decision_id"]

        def reject(exc):
            _audit.write(self.dir, _audit.build(
                decision_id=decision_id, root_run_id=self.lineage["root_run_id"],
                parent_run_id=job["run_id"], evidence=context["evidence"],
                recommendation=recommendation, provider=self.supervisor.name,
                model=getattr(self.supervisor, "model", None), accepted=False,
                rejection_code=exc.code, rejection_detail=exc.detail,
                budget_before=context["budget_before"],
                budget_after=_lineage.remaining(self.lineage, self.autonomy)))
            self._save()

        if recommendation["decision"] == _schema.COMPLETE:
            # A planner does not get to declare the work done: completion is a
            # Diana predicate produced by a run that verified and was reviewed.
            exc = _esc.Escalation(
                _esc.SUPERVISOR_REQUESTED_HUMAN,
                "the supervisor reported the work already complete, but this run did "
                "not complete; completion is Diana's own verified outcome and is never "
                "taken from a recommendation")
            reject(exc); raise exc
        try:
            validated = self.validate_recommendation(
                recommendation, outcome["contract"], job["depth"])
            applied = self.apply_workspace(recommendation)
        except _esc.Escalation as exc:
            reject(exc); raise

        jobs = [{"run_directory": None, "run_id": plan["run_id"], "depth": plan["depth"],
                 "is_root": False,
                 "plan": {**plan, "parent_run_id": job["run_id"],
                          "decision": recommendation["decision"],
                          "decision_id": decision_id}}
                for plan in validated]
        _audit.write(self.dir, _audit.build(
            decision_id=decision_id, root_run_id=self.lineage["root_run_id"],
            parent_run_id=job["run_id"], evidence=context["evidence"],
            recommendation=recommendation, provider=self.supervisor.name,
            model=getattr(self.supervisor, "model", None), accepted=True,
            proof={"children": [p["proof"] for p in validated], "workspace": applied},
            child_run_ids=[p["run_id"] for p in validated],   # planned, created lazily
            budget_before=context["budget_before"],
            budget_after=_lineage.remaining(self.lineage, self.autonomy)))
        self._save()
        return jobs

    def _materialise(self, job: dict) -> dict:
        """Create the child run, and prove the run that exists IS the proven one.

        The subset proof was made against a contract Diana derived; the run is
        created by `actors.approve`, which derives its own against the live
        repository. If the two differ on any authority axis, the run that exists
        is not the run that was proven and it must not execute -- the rule
        `proposal.approve` applies to a human approval, applied to this one.
        """
        import recovery as _recovery

        plan = job["plan"]
        # Re-proven here, not merely at validation: the tree has moved since, and
        # a budget that was sufficient for the first child of a split may not be
        # sufficient for the third.
        _lineage.prove_budget_for_child(self.lineage, self.autonomy, depth=plan["depth"])
        envelope = plan["contract"]["capability_envelope"]
        approved = _actors.approve(
            task=plan["contract"]["task"], repo_root=self.repo_root,
            allowed_commands=tuple(envelope["allowed_commands"]),
            write_roots=tuple(envelope["write_scope"]["allowed_roots"]),
            runs_base=self.runs_base, run_id=plan["run_id"],
            max_attempts=self._child_attempts(), total_seconds=self._child_seconds(),
            items=[{"id": "item-1", "task": plan["goal"], "depends_on": []}])
        created = _journal.read(approved["run_directory"])
        created_contract, _pol, _items = _recovery.load_authority(
            approved["run_directory"], created)
        proven = _subset.prove_within_standing(created_contract, self.standing)
        if proven != plan["proof"]:
            raise _esc.Escalation(
                _esc.AUTHORITY_EXPANSION_REQUESTED,
                f"the created child run {plan['run_id']} does not carry the authority "
                "that was proven for it; it must not execute",
                requested={"proven": plan["proof"], "created": proven})
        self.lineage = _lineage.add_child(
            self.lineage, run_id=plan["run_id"], parent_run_id=plan["parent_run_id"],
            goal=plan["goal"], reason=plan["decision"],
            supervisor_decision_id=plan["decision_id"], depth=plan["depth"])
        self._save()
        return {**job, "run_directory": Path(approved["run_directory"]), "plan": None}

    # A child's own attempt budget. Small on purpose: a recovery child is a
    # narrower job than its parent, and the cumulative bound is the real ceiling.
    CHILD_MAX_ATTEMPTS = 4

    def _child_attempts(self) -> int:
        """A child never gets more attempts than the LINEAGE has left."""
        return max(1, min(self.CHILD_MAX_ATTEMPTS,
                          _lineage.remaining(self.lineage, self.autonomy)["attempts"]))

    def _child_seconds(self) -> int:
        return max(60, _lineage.remaining(self.lineage, self.autonomy)["wall_clock_seconds"])

    def _finish(self, completed: list, escalation, root_run_id: str) -> dict:
        unverified = sorted(e["path"] for e in self.provenance["entries"]
                            if e["state"] in (_prov.OWNED_UNVERIFIED, _prov.PRESERVED_FOR_CHILD))
        preserved = sorted(e["path"] for e in self.provenance["entries"]
                           if e["state"] == _prov.PRESERVED_FOR_CHILD)
        verified = sorted(e["path"] for e in self.provenance["entries"]
                          if e["state"] == _prov.OWNED_VERIFIED)
        flat_reverted = sorted({
            path
            for entry in _audit.read_all(self.dir)
            for key in ("reverted", "removed")
            for path in (entry.get("proof") or {}).get("workspace", {}).get(key, [])})
        outcome = {
            "outcome": _esc.BLOCKED_FOR_HUMAN if escalation else _journal.COMPLETE,
            "root_run_id": root_run_id,
            "completed_runs": completed,
            "runs_attempted": len(self.lineage["runs"]),
            "budget_remaining": _lineage.remaining(self.lineage, self.autonomy),
            "budget_used": dict(self.lineage["cumulative"]),
            "verified_changes": verified,
            "unverified_changes": unverified,
            "preserved_changes": preserved,
            "reverted_changes": flat_reverted,
            "escalation": escalation.as_record() if escalation else None,
            "supervisor_decisions": [
                {k: a.get(k) for k in ("decision_id", "decision", "accepted",
                                       "rejection_code", "child_run_ids")}
                for a in _audit.read_all(self.dir)],
        }
        self.lineage = _lineage.finish(self.lineage, {
            "outcome": outcome["outcome"],
            "escalation_code": escalation.code if escalation else None})
        self._save()
        return outcome
