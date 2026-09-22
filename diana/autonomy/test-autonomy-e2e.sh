#!/usr/bin/env bash
# PHASE 3 — the autonomous control loop, end to end.
#
# The bar this suite exists to meet: schemas existing and helpers passing proves
# nothing about the feature. What must be proven is the whole loop --
#
#   ROOT APPROVED -> failure -> supervisor -> Diana validation -> child ->
#   review -> aggregation -> COMPLETE
#
# and, separately, that a supervisor asking for wider authority produces
# BLOCKED_FOR_HUMAN rather than a run.
#
# Every supervisor here is a deterministic double. A live provider cannot prove
# a state machine, because its answers are not reproducible.
set -uo pipefail
AUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIANA_DIR="$(cd "$AUT_DIR/.." && pwd)"
HERMES_HOME="${DIANA_HERMES_HOME:-$HOME/.hermes/hermes-agent}"
PY_BIN="python3"; [ -x "$HERMES_HOME/venv/bin/python3" ] && PY_BIN="$HERMES_HOME/venv/bin/python3"
[ -d "$HERMES_HOME" ] || { echo "SKIP  Hermes not installed at $HERMES_HOME"; exit 0; }
TMP_DIR="$(mktemp -d)"; trap 'rm -rf "$TMP_DIR"' EXIT
export HERMES_SAFE_MODE=1 DIANA_HERMES_HOME="$HERMES_HOME"
"$PY_BIN" - "$DIANA_DIR" "$TMP_DIR" <<'PY'
import copy, json, subprocess, sys, tempfile, textwrap, uuid
from pathlib import Path

diana, tmp = Path(sys.argv[1]), Path(sys.argv[2])
for sub in ("autonomy", "supervisors", "product", "multiactor", "unattended",
            "runtime", "mutation", "adapters", "profile"):
    sys.path.insert(0, str(diana / sub))
import escalation as E, lineage as L, loop as LOOP, policy as POL
import provenance as PV, standing as S, subset as SUB, audit as AUD
import schema as SCH, mock as MOCK
import actors as A, executors as EX, journal as J, remediate as REM
import projection as PJ, topology as T

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

RUNS = Path(tempfile.mkdtemp(prefix="auto-", dir=str(tmp)))
BROKEN = "def add(a, b):\n    return a - b\n"
FIXED  = "def add(a, b):\n    return a + b\n"

def fresh(name, *, user_dirty=False):
    root = tmp / f"e-{name}-{uuid.uuid4().hex[:6]}"
    (root / "src").mkdir(parents=True); (root / "lib").mkdir()
    (root / "src" / "calc.py").write_text(BROKEN)
    (root / "lib" / "helper.py").write_text("helper = 1\n")
    (root / "src" / "notes.py").write_text("notes = 'original'\n")
    (root / "check.py").write_text(textwrap.dedent("""\
        import sys
        sys.path.insert(0, "src")
        from calc import add
        sys.exit(0 if add(2, 3) == 5 else 1)
        """))
    g = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)
    g("init", "-q"); g("config", "user.email", "t@x"); g("config", "user.name", "t")
    g("add", "-A"); g("commit", "-qm", "init")
    if user_dirty:
        (root / "src" / "notes.py").write_text("notes = 'THE USER WAS EDITING THIS'\n")
    return root

def verify(cb, item_id=None):
    return subprocess.run([sys.executable, "-B", "check.py"],
                          cwd=cb["target"]["repo_root"], capture_output=True).returncode == 0

def approve_root(root, *, commands=("python3 check.py",), goal="repair add"):
    run_id = str(uuid.uuid4())
    return A.approve(task=goal, repo_root=str(root), runs_base=str(RUNS),
                     allowed_commands=tuple(commands),
                     write_roots=(str(root / "src"), str(root / "lib")),
                     max_attempts=4, total_seconds=1800, run_id=run_id)

def standing_for(appraisal, *, autonomy=None, executor="deterministic"):
    cb = appraisal["contract"]
    doc = S.from_contract(cb, root_run_id=cb["run_id"], goal=cb["task"],
                          autonomy_policy=autonomy or POL.build(), executor=executor)
    return doc, S.digest(doc)

class Builder(EX._Backend):
    """Deterministic builder: a scripted plan per run, keyed by goal substring."""
    name = "e2e-builder"
    def __init__(self, plans, fail_goals=()):
        super().__init__(); self.plans = dict(plans); self.fail_goals = set(fail_goals)
        self.goals_seen = []
    def _run(self, cb, item_id=None):
        goal = cb["task"]; self.goals_seen.append(goal)
        for marker in self.fail_goals:
            if marker in goal:
                raise RuntimeError(f"builder deliberately failed on {marker!r}")
        for marker, (rel, content) in self.plans.items():
            if marker in goal:
                p = Path(cb["target"]["repo_root"]) / rel
                p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content)
                return None
        return None

class Reviewer(EX.ScriptedReviewer):
    name = "e2e-reviewer"
    def _run(self, cb, item_id=None):
        ok = bool(verify(cb, item_id))
        self.verdict = {"decision": "PASS" if ok else "FAIL",
                        "summary": "verification " + ("passed" if ok else "did not pass"),
                        "findings": [] if ok else [{"description": "not yet correct",
                                                    "evidence": "the check command failed"}],
                        "dod_checks": [{"criterion": "approved check exits 0",
                                        "result": "PASS" if ok else "FAIL",
                                        "evidence": "Diana ran it"}]}
        return None

def backends_for(builder):
    return lambda cb, rd: (builder, Reviewer([]))

def session(appraisal, supervisor, builder, *, autonomy=None, executor="deterministic"):
    doc, dig = standing_for(appraisal, autonomy=autonomy, executor=executor)
    sess = LOOP.Session(lineage_dir=tmp / f"lin-{uuid.uuid4().hex[:6]}",
                        standing_doc=doc, standing_digest=dig, supervisor=supervisor,
                        backends=backends_for(builder), verify=verify, runs_base=str(RUNS))
    sess.start(appraisal["run_id"], appraisal["contract"]["task"])
    return sess

def rec(decision, **kw):
    return SCH.empty(decision, kw.pop("reason", "deterministic test recommendation"), **kw)

# ======================================================================
print("=== SCENARIO A — failure -> supervisor -> validated child -> COMPLETE ===")
rootA = fresh("scenarioA")
apprA = approve_root(rootA)
supA = MOCK.ScriptedSupervisor([
    rec(SCH.RETRY_NARROWER, next_goal="repair add in src only",
        requested_write_scope=[str(rootA / "src")],
        reason="the first attempt did not produce a passing check"),
])
bldA = Builder({"src only": ("src/calc.py", FIXED)})
sessA = session(apprA, supA, bldA)
outA = sessA.run(apprA["run_directory"], apprA["run_id"])

check("the lineage ends COMPLETE with zero human interaction",
      outA["outcome"] == J.COMPLETE and outA["escalation"] is None, f"({outA['outcome']})")
check("the root failed and a CHILD completed the work",
      len(outA["completed_runs"]) == 1
      and outA["completed_runs"][0] != apprA["run_id"]
      and outA["runs_attempted"] == 2, f"({outA})")
check("the supervisor was consulted exactly once",
      sessA.lineage["cumulative"]["supervisor_calls"] == 1)
falsify("a successful run pays no supervisor round trip: the root's own failure is "
        "what caused the single call", len(supA.seen) == 1)
check("lineage identity is exact: root preserved, parent correct, depth incremented",
      sessA.lineage["root_run_id"] == apprA["run_id"]
      and sessA.lineage["runs"][1]["parent_run_id"] == apprA["run_id"]
      and sessA.lineage["runs"][1]["depth"] == 1)
check("the child ran the goal the supervisor proposed, not the root's goal",
      "src only" in bldA.goals_seen[-1] and bldA.goals_seen[0] == "repair add")
check("the verified change is recorded as verified, and nothing is left unverified",
      outA["verified_changes"] == ["src/calc.py"] and outA["unverified_changes"] == [])
audits = AUD.read_all(sessA.dir)
check("an audit record exists for the supervisor call and says it was accepted",
      len(audits) == 1 and audits[0]["accepted"] is True
      and audits[0]["decision"] == SCH.RETRY_NARROWER
      and audits[0]["child_run_ids"] == [sessA.lineage["runs"][1]["run_id"]])
check("the audit records the proof Diana made, and brackets the decision with budgets",
      audits[0]["proof"]["children"][0]["write_roots"] == [str(rootA / "src")]
      and audits[0]["budget_before"]["child_runs"] == 8
      and "child_runs" in audits[0]["budget_after"])
check("and the lineage shows the child budget actually consumed",
      sessA.lineage["cumulative"]["child_runs"] == 1
      and outA["budget_remaining"]["child_runs"] == 7)
check("the audit stores the evidence DIGEST, never the evidence itself",
      audits[0]["evidence_digest"].startswith("sha256:")
      and "verification_output" not in json.dumps(audits[0]))

# ======================================================================
print("\n=== SCENARIO A2 — SPLIT_TASK: every child validated, ordered, completed ===")
rootA2 = fresh("split")
apprA2 = approve_root(rootA2, goal="repair add and the helper")
supA2 = MOCK.ScriptedSupervisor([
    rec(SCH.SPLIT_TASK, reason="the task is two independent concerns",
        children=[{"goal": "concern one: repair add",
                   "requested_write_scope": [str(rootA2 / "src")],
                   "requested_commands": ["python3 check.py"]},
                  {"goal": "concern two: update the helper",
                   "requested_write_scope": [str(rootA2 / "lib")],
                   "requested_commands": ["python3 check.py"]}]),
])
bldA2 = Builder({"concern one": ("src/calc.py", FIXED),
                 "concern two": ("lib/helper.py", "helper = 2\n")})
sessA2 = session(apprA2, supA2, bldA2)
outA2 = sessA2.run(apprA2["run_directory"], apprA2["run_id"])
check("both children ran and the lineage completed",
      outA2["outcome"] == J.COMPLETE and len(outA2["completed_runs"]) == 2)
check("children ran in the supervisor's stated order, deterministically",
      [g for g in bldA2.goals_seen if "concern" in g]
      == ["concern one: repair add", "concern two: update the helper"],
      f"({bldA2.goals_seen})")
check("each child got its OWN narrower write scope, validated separately",
      sorted(p["write_roots"][0] for p in
             AUD.read_all(sessA2.dir)[0]["proof"]["children"])
      == sorted([str(rootA2 / "lib"), str(rootA2 / "src")]))
check("the cumulative changed-file set is the union across the tree",
      sorted(sessA2.lineage["cumulative"]["changed_files"])
      == ["lib/helper.py", "src/calc.py"])

print("\n--- one invalid split child prevents the launch of ALL of them ---")
rootA3 = fresh("splitbad")
apprA3 = approve_root(rootA3)
supA3 = MOCK.ScriptedSupervisor([
    rec(SCH.SPLIT_TASK, reason="two concerns, one of them out of scope",
        children=[{"goal": "valid child", "requested_write_scope": [str(rootA3 / "src")],
                   "requested_commands": ["python3 check.py"]},
                  {"goal": "invalid child", "requested_write_scope": [str(rootA3 / "other")],
                   "requested_commands": ["python3 check.py"]}]),
])
bldA3 = Builder({"valid child": ("src/calc.py", FIXED)})
sessA3 = session(apprA3, supA3, bldA3)
outA3 = sessA3.run(apprA3["run_directory"], apprA3["run_id"])
check("the whole split is refused and the lineage escalates",
      outA3["outcome"] == E.BLOCKED_FOR_HUMAN
      and outA3["escalation"]["code"] == E.WRITE_SCOPE_NOT_SUBSET)
falsify("not even the VALID child was started, because the set was reasoned about "
        "together", "valid child" not in " ".join(bldA3.goals_seen)
        and sessA3.lineage["cumulative"]["child_runs"] == 0)
check("the rejection is audited with its exact code",
      AUD.read_all(sessA3.dir)[0]["accepted"] is False
      and AUD.read_all(sessA3.dir)[0]["rejection_code"] == E.WRITE_SCOPE_NOT_SUBSET)

# ======================================================================
print("\n=== SCENARIO C — wider authority requested -> BLOCKED_FOR_HUMAN ===")
WIDER = [
    ("a write root outside the approval",
     lambda r: rec(SCH.RETRY_NARROWER, next_goal="g", requested_write_scope=[str(r / "other")]),
     E.WRITE_SCOPE_NOT_SUBSET),
    ("a command outside the approval",
     lambda r: rec(SCH.RETRY_NARROWER, next_goal="g",
                   requested_write_scope=[str(r / "src")],
                   requested_commands=["python3 check.py", "npm install"]),
     E.COMMAND_NOT_SUBSET),
    ("a dependency manifest that was never authorised",
     lambda r: rec(SCH.RETRY_NARROWER, next_goal="g",
                   requested_write_scope=[str(r / "src" / "package.json")]),
     E.DEPENDENCY_CHANGE_NOT_AUTHORIZED),
    ("the supervisor flagging a human is required",
     lambda r: rec(SCH.RETRY_SAME, human_required=True, reason="needs a deploy"),
     E.SUPERVISOR_REQUESTED_HUMAN),
    ("the supervisor asking for a human outright",
     lambda r: rec(SCH.ESCALATE_HUMAN, reason="credentials are required"),
     E.SUPERVISOR_REQUESTED_HUMAN),
    ("the supervisor declaring the work already complete",
     lambda r: rec(SCH.COMPLETE, reason="looks done to me"),
     E.SUPERVISOR_REQUESTED_HUMAN),
    ("the supervisor asking for more evidence Diana does not have",
     lambda r: rec(SCH.REQUEST_MORE_EVIDENCE, reason="I need the production logs"),
     E.SUPERVISOR_EVIDENCE_MISSING),
]
for label, make, want in WIDER:
    root = fresh("wider"); appr = approve_root(root)
    bld = Builder({})
    sess = session(appr, MOCK.ScriptedSupervisor([make(root)]), bld)
    out = sess.run(appr["run_directory"], appr["run_id"])
    check(f"{label} -> BLOCKED_FOR_HUMAN [{want}]",
          out["outcome"] == E.BLOCKED_FOR_HUMAN and out["escalation"]["code"] == want,
          f"({out['outcome']}, {out['escalation']})")
    check(f"{label}: no child run was created",
          sess.lineage["cumulative"]["child_runs"] == 0)

print("\n--- the escalation says exactly what extra authority was asked for ---")
rootC = fresh("precise"); apprC = approve_root(rootC)
sessC = session(apprC, MOCK.ScriptedSupervisor([
    rec(SCH.RETRY_NARROWER, next_goal="g",
        requested_write_scope=[str(rootC / "other")])]), Builder({}))
outC = sessC.run(apprC["run_directory"], apprC["run_id"])
check("the escalation names the precise path requested, not 'human decision needed'",
      outC["escalation"]["requested"]["write_scope"] == [str(rootC / "other")]
      and "outside the approved write scope" in outC["escalation"]["detail"])
check("it reports what Diana tried, budgets remaining, and workspace state",
      outC["runs_attempted"] >= 1 and "child_runs" in outC["budget_remaining"]
      and "unverified_changes" in outC and "reverted_changes" in outC)

# ======================================================================
print("\n=== SCENARIO B — provenance recovery: revert only Diana's own change ===")
rootB = fresh("provenance", user_dirty=True)
USER_TEXT = (rootB / "src" / "notes.py").read_text()
apprB = approve_root(rootB, goal="ROOTPASS repair add")
# The root builder writes a WRONG partial fix; the supervisor asks for it to be
# reverted and a narrower child to redo it.
bldB = Builder({"ROOTPASS": ("src/calc.py", "def add(a, b):\n    return a * b\n"),
                "CHILDPASS": ("src/calc.py", FIXED)})
supB = MOCK.ScriptedSupervisor([
    rec(SCH.REVERT_AND_RETRY, next_goal="CHILDPASS redo the repair correctly",
        revert_changes=["src/calc.py"], requested_write_scope=[str(rootB / "src")],
        reason="the partial implementation is wrong; undo it and redo it narrowly"),
])
sessB = session(apprB, supB, bldB)
check("the user's edit is recorded as theirs before anything runs",
      PV.classify(sessB.provenance, "src/notes.py") == PV.USER_PREEXISTING)
outB = sessB.run(apprB["run_directory"], apprB["run_id"])
check("the lineage completed after the revert-and-retry",
      outB["outcome"] == J.COMPLETE, f"({outB['outcome']}, {outB['escalation']})")
check("THE CRITICAL PROPERTY: the user's file is preserved byte-for-byte",
      (rootB / "src" / "notes.py").read_text() == USER_TEXT)
check("Diana's own wrong change was reverted, then redone correctly",
      "src/calc.py" in outB["reverted_changes"]
      and (rootB / "src" / "calc.py").read_text() == FIXED)
check("the user's file was never in the revert set",
      "src/notes.py" not in outB["reverted_changes"])
falsify("asking to revert the USER's file instead would have been refused, so the "
        "revert above was permitted by PROVENANCE and not by the request",
        code_of(lambda: PV.apply_revert(sessB.provenance, ["src/notes.py"],
                                        repo_root=str(rootB)))
        == E.REVERT_WOULD_LOSE_WORK)

print("\n--- preserve carries work forward without calling it verified ---")
rootP = fresh("preserve")
apprP = approve_root(rootP, goal="ROOTPASS repair add")
bldP = Builder({"ROOTPASS": ("lib/helper.py", "helper = 'partial but useful'\n"),
                "CHILDPASS": ("src/calc.py", FIXED)})
supP = MOCK.ScriptedSupervisor([
    rec(SCH.PRESERVE_AND_RETRY, next_goal="CHILDPASS finish the repair",
        preserve_changes=["lib/helper.py"],
        requested_write_scope=[str(rootP / "src"), str(rootP / "lib")],
        reason="keep the helper work and finish the calculation"),
])
sessP = session(apprP, supP, bldP)
outP = sessP.run(apprP["run_directory"], apprP["run_id"])
check("the preserved change survived into the child and the lineage completed",
      outP["outcome"] == J.COMPLETE
      and (rootP / "lib" / "helper.py").read_text() == "helper = 'partial but useful'\n")
check("preserving is recorded as a provenance act, not as verification",
      AUD.read_all(sessP.dir)[0]["proof"]["workspace"]["preserved"] == ["lib/helper.py"])
check("the preserved change became verified only when a run actually COMPLETED, "
      "because the verification that produced it ran against a tree containing it",
      "lib/helper.py" in outP["verified_changes"]
      and outP["unverified_changes"] == [], f"({outP['verified_changes']})")

# ======================================================================
print("\n=== fail closed: provider failures never become permission ===")
PROVIDERS = [
    ("an unavailable provider", MOCK.FailingSupervisor(), E.SUPERVISOR_UNAVAILABLE),
    ("a provider that times out", MOCK.SlowSupervisor(), E.SUPERVISOR_TIMEOUT),
    ("a malformed response", MOCK.ScriptedSupervisor([{"decision": "RETRY_SAME"}]),
     E.SUPERVISOR_MALFORMED),
    ("an unknown decision",
     MOCK.ScriptedSupervisor([dict(rec(SCH.RETRY_SAME), decision="JUST_DO_IT")]),
     E.SUPERVISOR_UNKNOWN_DECISION),
    ("an unknown field",
     MOCK.ScriptedSupervisor([dict(rec(SCH.RETRY_SAME), force=True)]),
     E.SUPERVISOR_UNKNOWN_FIELD),
    ("no supervisor configured at all", MOCK.HumanSupervisor(), E.SUPERVISOR_REQUESTED_HUMAN),
]
for label, sup, want in PROVIDERS:
    root = fresh("failclosed"); appr = approve_root(root)
    sess = session(appr, sup, Builder({}))
    out = sess.run(appr["run_directory"], appr["run_id"])
    check(f"{label} -> BLOCKED_FOR_HUMAN [{want}]",
          out["outcome"] == E.BLOCKED_FOR_HUMAN and out["escalation"]["code"] == want,
          f"({out['escalation']})")
    check(f"{label}: nothing was started and the failure is audited",
          sess.lineage["cumulative"]["child_runs"] == 0
          and AUD.read_all(sess.dir)[0]["accepted"] is False)

# ======================================================================
print("\n=== bounds: every cumulative budget stops the loop ===")
rootL = fresh("loops"); apprL = approve_root(rootL)
narrow = POL.build(limits={"max_child_runs": 1})
supL = MOCK.ScriptedSupervisor([
    rec(SCH.RETRY_NARROWER, next_goal=f"attempt {n}",
        requested_write_scope=[str(rootL / "src")], reason=f"try {n}") for n in range(5)])
sessL = session(apprL, supL, Builder({}), autonomy=narrow)
outL = sessL.run(apprL["run_directory"], apprL["run_id"])
check("max_child_runs stops the lineage with the budget code",
      outL["outcome"] == E.BLOCKED_FOR_HUMAN
      and outL["escalation"]["code"] == E.CHILD_BUDGET_EXHAUSTED, f"({outL['escalation']})")
check("and the budget report shows the child budget at zero",
      outL["budget_remaining"]["child_runs"] == 0 and outL["budget_used"]["child_runs"] == 1)

rootD = fresh("depth"); apprD = approve_root(rootD)
deep = POL.build(limits={"max_child_depth": 1, "max_child_runs": 8})
supD = MOCK.ScriptedSupervisor([
    rec(SCH.RETRY_NARROWER, next_goal=f"deeper {n}",
        requested_write_scope=[str(rootD / "src")], reason="go deeper") for n in range(5)])
sessD = session(apprD, supD, Builder({}), autonomy=deep)
outD = sessD.run(apprD["run_directory"], apprD["run_id"])
check("max_child_depth stops recursion",
      outD["outcome"] == E.BLOCKED_FOR_HUMAN
      and outD["escalation"]["code"] == E.CHILD_DEPTH_EXCEEDED, f"({outD['escalation']})")

rootS = fresh("supcalls"); apprS = approve_root(rootS)
few = POL.build(limits={"max_supervisor_calls": 1})
supS = MOCK.ScriptedSupervisor([
    rec(SCH.RETRY_NARROWER, next_goal=f"try {n}",
        requested_write_scope=[str(rootS / "src")], reason="again") for n in range(5)])
sessS = session(apprS, supS, Builder({}), autonomy=few)
outS = sessS.run(apprS["run_directory"], apprS["run_id"])
check("max_supervisor_calls stops further diagnosis",
      outS["outcome"] == E.BLOCKED_FOR_HUMAN
      and outS["escalation"]["code"] == E.SUPERVISOR_CALL_BUDGET_EXHAUSTED,
      f"({outS['escalation']})")

rootN = fresh("noprogress"); apprN = approve_root(rootN)
supN = MOCK.ScriptedSupervisor([
    rec(SCH.RETRY_SAME, reason="same again") for _ in range(6)])
sessN = session(apprN, supN, Builder({}))
outN = sessN.run(apprN["run_directory"], apprN["run_id"])
check("a recovery loop that repeats itself eventually escalates for no progress",
      outN["outcome"] == E.BLOCKED_FOR_HUMAN
      and outN["escalation"]["code"] in (E.NO_PROGRESS, E.CHILD_BUDGET_EXHAUSTED),
      f"({outN['escalation']})")

# ======================================================================
print("\n=== autonomy policy gates which recoveries exist at all ===")
for flag, decision, maker in (
    ("same_scope_retries", SCH.RETRY_SAME, lambda r: rec(SCH.RETRY_SAME, reason="again")),
    ("narrower_child_runs", SCH.RETRY_NARROWER,
     lambda r: rec(SCH.RETRY_NARROWER, next_goal="g",
                   requested_write_scope=[str(r / "src")])),
    ("task_splitting", SCH.SPLIT_TASK,
     lambda r: rec(SCH.SPLIT_TASK, children=[{"goal": "c", "requested_write_scope": [],
                                              "requested_commands": []}])),
):
    root = fresh("gated"); appr = approve_root(root)
    off = POL.build(allow={flag: False})
    sess = session(appr, MOCK.ScriptedSupervisor([maker(root)]), Builder({}), autonomy=off)
    out = sess.run(appr["run_directory"], appr["run_id"])
    check(f"{decision} is refused when {flag} is not granted",
          out["outcome"] == E.BLOCKED_FOR_HUMAN
          and out["escalation"]["code"] == E.AUTHORITY_EXPANSION_REQUESTED
          and out["escalation"]["requested"]["autonomy_allow"] == flag,
          f"({out['escalation']})")

print("\n--- a dependency change is allowed ONLY when pre-authorised ---")
rootDep = fresh("deps")
(rootDep / "src" / "package.json").write_text('{"name":"x"}\n')
subprocess.run(["git", "-C", str(rootDep), "add", "-A"], capture_output=True)
subprocess.run(["git", "-C", str(rootDep), "-c", "user.email=t@x", "-c", "user.name=t",
                "commit", "-qm", "manifest"], capture_output=True)
apprDep = approve_root(rootDep)
# The scope names BOTH the source directory and the manifest, which is what a
# real dependency change looks like: code plus the file that declares the dep.
mk = lambda: rec(SCH.RETRY_NARROWER, next_goal="update the manifest and the code",
                 requested_write_scope=[str(rootDep / "src"),
                                        str(rootDep / "src" / "package.json")],
                 reason="a dependency must change")
sessNo = session(apprDep, MOCK.ScriptedSupervisor([mk()]), Builder({}))
outNo = sessNo.run(apprDep["run_directory"], apprDep["run_id"])
check("denied by default: a manifest edit escalates with its own code",
      outNo["escalation"]["code"] == E.DEPENDENCY_CHANGE_NOT_AUTHORIZED
      and outNo["escalation"]["requested"]["dependency_paths"]
      == [str(rootDep / "src" / "package.json")])
# A terminal run is read, never resumed, so the second session needs its own root.
apprDep2 = approve_root(rootDep)
sessYes = session(apprDep2, MOCK.ScriptedSupervisor([mk()]),
                  Builder({"update the manifest and the code": ("src/calc.py", FIXED)}),
                  autonomy=POL.build(allow={"dependency_changes": True}))
outYes = sessYes.run(apprDep2["run_directory"], apprDep2["run_id"])
check("pre-authorised: the same recommendation runs, still inside the write scope",
      outYes["outcome"] == J.COMPLETE, f"({outYes['escalation']})")
falsify("authorising dependency changes did NOT grant a command or network: the "
        "command set is still exactly what was approved",
        sorted(AUD.read_all(sessYes.dir)[0]["proof"]["children"][0]["allowed_commands"])
        == ["python3 check.py"])

# ======================================================================
print("\n=== the child the Builder receives is the child that was proven ===")
provenA = AUD.read_all(sessA.dir)[0]["proof"]["children"][0]
child_rd = Path(str(RUNS)) / sessA.lineage["runs"][1]["run_id"]
child_rec = J.read(child_rd)
import recovery as RCV
child_cb, _p, _i = RCV.load_authority(child_rd, child_rec)
check("the created child's authority equals the proof Diana recorded",
      SUB.prove_within_standing(child_cb, sessA.standing) == provenA)
check("the child's write scope is the narrowed one, not the root's",
      child_cb["capability_envelope"]["write_scope"]["allowed_roots"] == [str(rootA / "src")])
check("REVIEWER stayed read-only in the child run",
      PJ.derive("REVIEWER", child_cb, T.build(run_id=child_cb["run_id"]))
      ["capability_envelope"] == {"allowed_tools": ["read_file", "search_files"]})
check("the child never gained a tool, command or risk class",
      child_cb["capability_envelope"]["allowed_tools"]
      == sessA.standing["authority"]["capability_envelope"]["allowed_tools"]
      and child_cb["risk"] == sessA.standing["authority"]["risk"]
      and child_cb["depth"] == sessA.standing["authority"]["depth"])

print("\n--- a standing approval never crosses repositories ---")
other = fresh("otherrepo")
check("a child contract naming another repository is refused",
      code_of(lambda: SUB.prove_within_standing(
          REM.build_contract(task="t", repo_root=str(other), git_commit="a", dirty=False,
                             allowed_commands=("python3 check.py",),
                             write_roots=(str(other / "src"),), run_id=str(uuid.uuid4())),
          sessA.standing)) == E.REPOSITORY_MISMATCH)

print(f"\n{passed} passed, {failed} failed, {falsifiers} falsifiers")
sys.exit(1 if failed else 0)
PY
