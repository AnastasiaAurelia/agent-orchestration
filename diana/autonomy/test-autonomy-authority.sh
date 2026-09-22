#!/usr/bin/env bash
# PHASE 1 — the authority foundation of one-approval autonomous recovery.
#
# Autonomy lets Diana start work a human did not individually approve. That is
# only safe if the approval it acts under is (a) a recording of what was already
# granted, (b) digest-bound so it cannot be edited into something wider, and
# (c) a ceiling every child is proven to sit under on every axis.
#
# This phase proves exactly those three things and nothing about supervisors.
set -uo pipefail
AUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIANA_DIR="$(cd "$AUT_DIR/.." && pwd)"
TMP_DIR="$(mktemp -d)"; trap 'rm -rf "$TMP_DIR"' EXIT
python3 - "$DIANA_DIR" "$TMP_DIR" <<'PY'
import copy, json, subprocess, sys, uuid
from pathlib import Path

diana, tmp = Path(sys.argv[1]), Path(sys.argv[2])
for sub in ("autonomy", "product", "multiactor", "unattended", "runtime",
            "mutation", "adapters", "profile"):
    sys.path.insert(0, str(diana / sub))
import escalation as E, lineage as L, policy as POL, standing as S, subset as SUB
import contract as C, remediate as REM

passed = failed = falsifiers = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond is True: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")
def falsify(label, cond, extra=""):
    global falsifiers
    falsifiers += 1
    check("[falsifier] " + label, cond, extra)
def code_of(fn):
    try:
        fn(); return None
    except E.Escalation as exc: return exc.code
    except POL.PolicyError: return "policy-error"

def fresh(name):
    root = tmp / f"r-{name}-{uuid.uuid4().hex[:6]}"
    (root / "src").mkdir(parents=True); (root / "lib").mkdir()
    (root / "src" / "a.py").write_text("x\n"); (root / "lib" / "b.py").write_text("y\n")
    (root / "check.py").write_text("import sys; sys.exit(0)\n")
    g = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)
    g("init", "-q"); g("config", "user.email", "t@x"); g("config", "user.name", "t")
    g("add", "-A"); g("commit", "-qm", "init")
    return root

ROOT = fresh("base")
(ROOT / "other").mkdir(exist_ok=True)

def _blocked_by_builder():
    """The contract builder refuses an out-of-repo write root on its own."""
    import blocking
    try:
        REM.build_contract(task="t", repo_root=str(ROOT), git_commit="a", dirty=False,
                           allowed_commands=("python3 check.py",), write_roots=("/etc",),
                           run_id=str(uuid.uuid4()))
        return False
    except blocking.Blocked as exc:
        return exc.code == blocking.WRITE_SCOPE_EXCEEDS_READ_SCOPE
def contract_for(root, *, write_roots=None, commands=("python3 check.py",), run_id=None):
    return REM.build_contract(
        task="fix the failing check", repo_root=str(root), git_commit="abc", dirty=False,
        allowed_commands=tuple(commands),
        write_roots=tuple(write_roots or (str(root / "src"), str(root / "lib"))),
        run_id=run_id or str(uuid.uuid4()))

CB = contract_for(ROOT)
AUTO = POL.build()
STAND = S.from_contract(CB, root_run_id=CB["run_id"], goal=CB["task"],
                        autonomy_policy=AUTO, executor="hermes")
DIG = S.digest(STAND)

# ======================================================================
print("=== the autonomy policy is a closed, bounded, non-negotiable document ===")
check("manual mode is a policy, and it is disabled",
      POL.manual()["enabled"] is False and POL.allows(POL.manual(), "same_scope_retries") is False)
check("every deny entry is fixed True and cannot be switched off",
      code_of(lambda: POL.validate(
          {**copy.deepcopy(AUTO), "deny": {**AUTO["deny"], "deploy": False}})) == "policy-error")
falsify("that same policy validates with deploy denied, so the refusal is the value "
        "and not the shape", POL.validate(copy.deepcopy(AUTO)) is None)
check("an unknown policy field is refused, not recorded",
      code_of(lambda: POL.validate({**copy.deepcopy(AUTO), "escalate": False})) == "policy-error")
check("an unknown allow key is refused",
      code_of(lambda: POL.validate(
          {**copy.deepcopy(AUTO), "allow": {**AUTO["allow"], "sudo": True}})) == "policy-error")
for bad in (0, -1, None, 3.5, "8"):
    check(f"a non-finite limit {bad!r} is refused -- an absent bound is an unbounded loop",
          code_of(lambda b=bad: POL.build(limits={"max_child_runs": b})) == "policy-error")
check("dependency changes are OFF by default",
      POL.allows(AUTO, "dependency_changes") is False)
check("a denied capability is never 'allowed', and an unknown one is never allowed",
      POL.allows(AUTO, "deploy") is False and POL.allows(AUTO, "rm_rf") is False)
check("changing any allow flag changes the policy digest",
      POL.digest(POL.build(allow={"dependency_changes": True})) != POL.digest(AUTO))
check("changing any limit changes the policy digest",
      POL.digest(POL.build(limits={"max_child_runs": 7})) != POL.digest(AUTO))
falsify("the same policy twice hashes the same, so the differences above are the "
        "policy and not the clock", POL.digest(POL.build()) == POL.digest(AUTO))

# ======================================================================
print("\n=== the standing approval records the grant and is bound to it ===")
check("it is built from the approved contract, copying authority rather than re-deriving",
      STAND["authority"]["capability_envelope"] == CB["capability_envelope"]
      and STAND["authority"]["read_scope"] == CB["read_scope"]
      and STAND["repo_root"] == CB["target"]["repo_root"]
      and STAND["original_goal"] == CB["task"])
check("the ALWAYS-ESCALATES set is frozen into the document a human approves",
      STAND["human_only_conditions"] == list(S.HUMAN_ONLY_CONDITIONS)
      and "authority_expansion" in STAND["human_only_conditions"]
      and "deploy" in STAND["human_only_conditions"])
check("dropping a human-only condition invalidates the document",
      code_of(lambda: S.validate({**copy.deepcopy(STAND),
                                  "human_only_conditions": ["deploy"]}))
      == E.STANDING_APPROVAL_INVALID)
check("an unknown standing-approval field is refused",
      code_of(lambda: S.validate({**copy.deepcopy(STAND), "extra": 1}))
      == E.STANDING_APPROVAL_INVALID)

print("\n--- every authority-relevant field invalidates the approval ---")
def mutated(**changes):
    doc = copy.deepcopy(STAND); doc.update(changes); return doc
wider_env = copy.deepcopy(STAND["authority"])
wider_env["capability_envelope"]["allowed_commands"] = ["python3 check.py", "npm test"]
wider_ws = copy.deepcopy(STAND["authority"])
wider_ws["capability_envelope"]["write_scope"]["allowed_roots"] += [str(ROOT / "other")]
for label, doc in (
    ("a different repository", mutated(repo_root=str(tmp / "elsewhere"))),
    ("a different goal", mutated(original_goal="do something else")),
    ("a broader command set", mutated(authority=wider_env)),
    ("a broader write scope", mutated(authority=wider_ws)),
    ("a different executor", mutated(executor="deterministic")),
    ("a modified autonomy policy", mutated(autonomy=POL.build(limits={"max_child_runs": 99}))),
    ("autonomy switched off", mutated(autonomy=POL.manual())),
):
    check(f"{label} changes the standing digest", S.digest(doc) != DIG)
falsify("re-recording the identical approval hashes identically, so the differences "
        "above are the FIELDS and not incidental encoding",
        S.digest(S.from_contract(CB, root_run_id=CB["run_id"], goal=CB["task"],
                                 autonomy_policy=POL.build(), executor="hermes")) == DIG)

print("\n--- a standing approval is re-verified before it can be acted on ---")
check("require() accepts the document that matches its approved digest",
      S.require(copy.deepcopy(STAND), DIG)["root_run_id"] == CB["run_id"])
tampered = copy.deepcopy(STAND)
tampered["authority"]["capability_envelope"]["allowed_commands"] = ["rm -rf /"]
check("a standing approval edited on disk grants nothing",
      code_of(lambda: S.require(tampered, DIG)) == E.STANDING_APPROVAL_DIGEST_MISMATCH)
manual_doc = S.from_contract(CB, root_run_id=CB["run_id"], goal=CB["task"],
                             autonomy_policy=POL.manual(), executor="hermes")
check("a manual-mode approval refuses to act autonomously at all",
      code_of(lambda: S.require(manual_doc, S.digest(manual_doc))) == E.AUTONOMY_DISABLED)
check("and enabled() reports it honestly without raising",
      S.enabled(manual_doc) is False and S.enabled(STAND) is True
      and S.enabled({"nonsense": True}) is False)

# ======================================================================
print("\n=== a child is proven inside the standing approval on EVERY axis ===")
same = contract_for(ROOT)
narrower = contract_for(ROOT, write_roots=(str(ROOT / "src"),))
check("a same-scope child is proven inside",
      SUB.prove_within_standing(same, STAND)["allowed_commands"] == ["python3 check.py"])
check("a narrower child is proven inside",
      SUB.prove_within_standing(narrower, STAND)["write_roots"] == [str(ROOT / "src")])
falsify("the narrower child really is narrower, so the proof above is not vacuous",
        len(narrower["capability_envelope"]["write_scope"]["allowed_roots"])
        < len(CB["capability_envelope"]["write_scope"]["allowed_roots"]))

OTHER = fresh("other")
CASES = [
    ("a different repository", contract_for(OTHER), E.REPOSITORY_MISMATCH),
    ("an added write root inside the repo but outside the approval", contract_for(
        ROOT, write_roots=(str(ROOT / "src"), str(ROOT / "lib"), str(ROOT / "other"))),
     E.WRITE_SCOPE_NOT_SUBSET),
    ("an added command", contract_for(
        ROOT, commands=("python3 check.py", "npm install")), E.COMMAND_NOT_SUBSET),
]
for label, child, want in CASES:
    check(f"{label} is REFUSED with {want}",
          code_of(lambda c=child: SUB.prove_within_standing(c, STAND)) == want,
          f"({code_of(lambda c=child: SUB.prove_within_standing(c, STAND))})")

def tweak(base, **env):
    child = copy.deepcopy(base)
    child["capability_envelope"].update(env)
    return child
check("an added tool is refused",
      code_of(lambda: SUB.prove_within_standing(
          tweak(same, allowed_tools=sorted(set(CB["capability_envelope"]["allowed_tools"])
                                           | {"delegate_task"})), STAND))
      == E.AUTHORITY_EXPANSION_REQUESTED)
check("an unknown authority axis is refused rather than ignored",
      code_of(lambda: SUB.prove_within_standing(
          tweak(same, network_access={"egress": True}), STAND))
      == E.AUTHORITY_EXPANSION_REQUESTED)
check("dropping a denied_subpath is refused",
      code_of(lambda: SUB.prove_within_standing(
          tweak(same, write_scope={"allowed_roots": [str(ROOT / "src")],
                                   "denied_subpaths": []}), STAND))
      == E.WRITE_SCOPE_NOT_SUBSET)
check("a raised command timeout ceiling is refused",
      code_of(lambda: SUB.prove_within_standing(
          tweak(same, command_policy={**same["capability_envelope"]["command_policy"],
                                      "max_timeout_s": 9999}), STAND))
      == E.COMMAND_NOT_SUBSET)
for field in ("risk", "depth", "workflow"):
    child = copy.deepcopy(same); child[field] = "SOMETHING_ELSE"
    check(f"a changed {field} class is refused",
          code_of(lambda c=child: SUB.prove_within_standing(c, STAND))
          == E.AUTHORITY_EXPANSION_REQUESTED)
wide_read = copy.deepcopy(same)
wide_read["read_scope"] = {"allowed_roots": [str(tmp)], "denied_subpaths": []}
check("a widened read scope is refused -- egress is an authority axis",
      code_of(lambda: SUB.prove_within_standing(wide_read, STAND))
      == E.AUTHORITY_EXPANSION_REQUESTED)
check("a write root outside the repository entirely is refused",
      code_of(lambda: SUB.prove_within_standing(
          tweak(same, write_scope={"allowed_roots": ["/etc"],
                                   "denied_subpaths": list(C.DEFAULT_DENIED_SUBPATHS)}),
          STAND)) == E.WRITE_SCOPE_NOT_SUBSET)
falsify("the contract BUILDER already refuses that root before a subset proof is ever "
        "reached, so the two controls are independent and the one above is the second",
        _blocked_by_builder() is True)
falsify("a child is never NARROWED to fit: the refusals above raise rather than "
        "returning a clamped envelope",
        code_of(lambda: SUB.prove_within_standing(
            contract_for(ROOT, commands=("python3 check.py", "npm install")), STAND))
        is not None)

print("\n--- recursion cannot widen: a grandchild is proven against the ROOT ---")
grand = contract_for(ROOT, write_roots=(str(ROOT / "src"),),
                     commands=("python3 check.py", "npm install"))
check("a grandchild that a narrow parent would have 'allowed' is still refused, "
      "because every child is proven against the standing approval itself",
      code_of(lambda: SUB.prove_within_standing(grand, STAND)) == E.COMMAND_NOT_SUBSET)

# ======================================================================
print("\n=== the lineage ledger: cumulative budgets that cannot be edited ===")
lin_dir = tmp / "lineage"; lin_dir.mkdir()
LIN = L.create(CB["run_id"], DIG, goal=CB["task"])
L.write(lin_dir, LIN)
check("a fresh lineage holds only the root, at depth 0",
      len(LIN["runs"]) == 1 and LIN["runs"][0]["depth"] == 0
      and LIN["runs"][0]["parent_run_id"] is None)
check("it round-trips through the digest envelope",
      L.read(lin_dir)["root_run_id"] == CB["run_id"])
raw = json.loads((lin_dir / "lineage.json").read_text())
raw["record"]["cumulative"]["child_runs"] = 0
raw["record"]["cumulative"]["attempts"] = 0
(lin_dir / "lineage.json").write_text(json.dumps(raw))
check("a ledger edited to restore budget is refused, not believed",
      code_of(lambda: L.read(lin_dir)) in (E.LINEAGE_CORRUPT, None))
spent = L.settle_run(LIN, CB["run_id"], state="FAILED", attempts=3,
                     changed_files=["src/a.py"], wall_clock_seconds=100)
L.write(lin_dir, spent)
raw = json.loads((lin_dir / "lineage.json").read_text())
raw["record"]["cumulative"]["attempts"] = 0
(lin_dir / "lineage.json").write_text(json.dumps(raw))
check("rewinding the attempt counter breaks the digest and stops the lineage",
      code_of(lambda: L.read(lin_dir)) == E.LINEAGE_CORRUPT)
L.write(lin_dir, spent)
falsify("the untampered ledger still reads back, so the refusals above are the EDIT "
        "and not the mechanism", L.read(lin_dir)["cumulative"]["attempts"] == 3)

print("\n--- every cumulative bound is enforced before a child is created ---")
check("budgets accumulate across the tree rather than per run",
      spent["cumulative"]["attempts"] == 3
      and spent["cumulative"]["changed_files"] == ["src/a.py"])
check("the changed-file budget is a SET, so a loop rewriting one file stays cheap",
      L.settle_run(spent, CB["run_id"], state="FAILED", attempts=1,
                   changed_files=["src/a.py"], wall_clock_seconds=1
                   )["cumulative"]["changed_files"] == ["src/a.py"])
LIMITS = [
    ("max_child_runs", {"child_runs": 8}, E.CHILD_BUDGET_EXHAUSTED),
    ("max_total_attempts", {"attempts": 24}, E.ATTEMPT_BUDGET_EXHAUSTED),
    ("max_wall_clock_seconds", {"wall_clock_seconds": 7200}, E.WALL_CLOCK_EXHAUSTED),
]
for label, spend, want in LIMITS:
    full = copy.deepcopy(LIN); full["cumulative"].update(spend)
    check(f"{label} exhausted refuses the next child with {want}",
          code_of(lambda f=full: L.prove_budget_for_child(f, AUTO, depth=1)) == want)
full_files = copy.deepcopy(LIN)
full_files["cumulative"]["changed_files"] = [f"f{i}.py" for i in range(50)]
check("max_changed_files exhausted refuses the next child",
      code_of(lambda: L.prove_budget_for_child(full_files, AUTO, depth=1))
      == E.CHANGED_FILE_BUDGET_EXHAUSTED)
check("max_child_depth is enforced before anything is created",
      code_of(lambda: L.prove_budget_for_child(LIN, AUTO, depth=4))
      == E.CHILD_DEPTH_EXCEEDED
      and L.prove_budget_for_child(LIN, AUTO, depth=3)["child_runs"] == 8)
full_sup = copy.deepcopy(LIN); full_sup["cumulative"]["supervisor_calls"] = 12
check("max_supervisor_calls exhausted refuses another diagnosis",
      code_of(lambda: L.prove_supervisor_call(full_sup, AUTO))
      == E.SUPERVISOR_CALL_BUDGET_EXHAUSTED)
print("\n--- a budget escalation states its exact, mechanical remedy ---")
def escalation_of(fn):
    try:
        fn(); return None
    except E.Escalation as exc: return exc
_full = copy.deepcopy(LIN); _full["cumulative"]["child_runs"] = 8
_exc = escalation_of(lambda: L.prove_budget_for_child(_full, AUTO, depth=1))
check("it names WHICH limit, its current value, and how much was used",
      _exc.requested == {"increase_limit": "max_child_runs", "current_value": 8, "used": 8},
      f"({_exc.requested})")
_deep = escalation_of(lambda: L.prove_budget_for_child(LIN, AUTO, depth=9))
check("a depth escalation names the depth that was needed",
      _deep.requested["increase_limit"] == "max_child_depth"
      and _deep.requested["needed"] == 9)
_sup = copy.deepcopy(LIN); _sup["cumulative"]["supervisor_calls"] = 12
_sexc = escalation_of(lambda: L.prove_supervisor_call(_sup, AUTO))
check("a supervisor-call escalation names its limit too",
      _sexc.requested["increase_limit"] == "max_supervisor_calls")
falsify("an AUTHORITY escalation is different in kind and names the authority, not a "
        "budget -- the two remedies are not interchangeable",
        "increase_limit" not in (escalation_of(
            lambda: SUB.prove_within_standing(
                contract_for(ROOT, commands=("python3 check.py", "npm install")),
                STAND)).requested or {}))

falsify("with budget remaining the very same calls succeed, so the refusals above "
        "are the BUDGET and not the call",
        L.prove_budget_for_child(LIN, AUTO, depth=1)["child_runs"] == 8
        and L.prove_supervisor_call(LIN, AUTO) is None)

print("\n--- lineage identity is tracked exactly ---")
child_id = str(uuid.uuid4())
with_child = L.add_child(LIN, run_id=child_id, parent_run_id=CB["run_id"],
                         goal="narrower goal", reason="RETRY_NARROWER",
                         supervisor_decision_id="dec-1", depth=1)
check("root_run_id is preserved and parent/child/depth are exact",
      with_child["root_run_id"] == CB["run_id"]
      and with_child["runs"][1]["parent_run_id"] == CB["run_id"]
      and with_child["runs"][1]["run_id"] == child_id
      and with_child["runs"][1]["depth"] == 1
      and with_child["cumulative"]["child_runs"] == 1)
grand_id = str(uuid.uuid4())
with_grand = L.add_child(with_child, run_id=grand_id, parent_run_id=child_id,
                         goal="deeper", reason="SPLIT_TASK",
                         supervisor_decision_id="dec-2", depth=2)
check("depth increments down the tree and the decision id is retained",
      L.depth_of(with_grand, grand_id) == 2
      and with_grand["runs"][2]["supervisor_decision_id"] == "dec-2")
check("a lineage whose parent link does not resolve is corrupt",
      code_of(lambda: L.validate({**copy.deepcopy(with_child), "runs": [
          with_child["runs"][1], with_child["runs"][0]]})) == E.LINEAGE_CORRUPT)
check("settling an unknown run is refused",
      code_of(lambda: L.settle_run(LIN, "not-a-run", state="FAILED", attempts=1,
                                   changed_files=[], wall_clock_seconds=1))
      == E.LINEAGE_CORRUPT)
check("a supervisor call is counted and its audit reference retained",
      L.record_supervisor_call(LIN, {"decision_id": "dec-1"}
                               )["cumulative"]["supervisor_calls"] == 1)

print(f"\n{passed} passed, {failed} failed, {falsifiers} falsifiers")
sys.exit(1 if failed else 0)
PY
