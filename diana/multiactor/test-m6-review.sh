#!/usr/bin/env bash
# M6 INDEPENDENT REVIEW — regression coverage for the findings of the review pass.
#
# These are not re-statements of the author's acceptance suite. Each case here
# exists because attacking a FIX found something the fix did not cover:
#
#   R-1  install_projection proved "subset of the approval" and never "this
#        role's frozen envelope". A REVIEWER carrying `terminal` is a legal
#        subset, and was measured executing a command through the real funnel.
#   R-2  M6's run lock is entry-point-scoped. M5's still-public
#        unattended.execute discharges a LOCKED run's in-flight obligation.
#        PINNED here as a known limitation, not fixed (see M6-REVIEW.md).
#   R-3  install_projection left whatever it had installed live on any failure
#        after the boundary was replaced.
#   V-1  an attack assertion accepted two reason codes where one is frozen, so
#        it passed without ever exercising the run-policy digest.
set -uo pipefail
MA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIANA_DIR="$(cd "$MA_DIR/.." && pwd)"
REPO_DIR="$(cd "$DIANA_DIR/.." && pwd)"
HERMES_HOME="${DIANA_HERMES_HOME:-$HOME/.hermes/hermes-agent}"
PY_BIN="python3"; [ -x "$HERMES_HOME/venv/bin/python3" ] && PY_BIN="$HERMES_HOME/venv/bin/python3"
[ -d "$HERMES_HOME" ] || { echo "SKIP  Hermes not installed at $HERMES_HOME"; exit 0; }
TMP_DIR="$(mktemp -d)"; trap 'rm -rf "$TMP_DIR"' EXIT
export HERMES_SAFE_MODE=1 DIANA_HERMES_HOME="$HERMES_HOME"
"$PY_BIN" - "$DIANA_DIR" "$TMP_DIR" "$REPO_DIR" "$HERMES_HOME" <<'PY'
import json, os, subprocess, sys, tempfile, textwrap, types, uuid
from pathlib import Path

diana, tmp, repo_dir, hermes_home = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
for sub in ("multiactor", "unattended", "runtime", "mutation", "adapters", "profile"):
    sys.path.insert(0, str(Path(diana, sub)))
import blocking, journal as J, reconcile as RC, unattended as U
import actors as A, executors as E, projection as P, topology as T
import hermes_patches as HP, selftest as ST

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
    except blocking.Blocked as exc:
        return exc.code

RUNS = Path(tempfile.mkdtemp(prefix="m6-review-", dir=str(tmp)))
BROKEN = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b    # repaired\n"

def fresh(name):
    root = tmp / f"r-{name}-{uuid.uuid4().hex[:6]}"
    (root / "src").mkdir(parents=True)
    (root / "src" / "calc.py").write_text(BROKEN)
    (root / "check.py").write_text(textwrap.dedent("""\
        import sys
        sys.path.insert(0, "src")
        from calc import add
        sys.exit(0 if add(2, 3) == 5 else 1)
        """))
    (root / ".gitignore").write_text("build/\n")
    g = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)
    g("init", "-q"); g("config", "user.email", "r@x"); g("config", "user.name", "r")
    g("add", "-A"); g("commit", "-qm", "init")
    return root

def verify(cb, item_id=None):
    return subprocess.run([sys.executable, "-B", "check.py"],
                          cwd=cb["target"]["repo_root"], capture_output=True).returncode == 0

def approve(root, **kw):
    kw.setdefault("allowed_commands", ("python3 check.py",))
    kw.setdefault("write_roots", (str(Path(root) / "src"),))
    kw.setdefault("max_attempts", 8); kw.setdefault("total_seconds", 1800)
    return A.approve(task="repair add", repo_root=str(root), runs_base=str(RUNS), **kw)

def PASS_V():
    return {"decision": "PASS", "summary": "ok", "findings": [],
            "dod_checks": [{"criterion": "c", "result": "PASS", "evidence": "e"}]}
def build_only(root):
    return E.ScriptedBuilder({"item-1": ("src/calc.py", FIXED)})

def drive(tool, args=None):
    """The real dispatch funnel, with arguments."""
    sys.path.insert(0, hermes_home)
    import agent.tool_executor as te
    from agent.tool_guardrails import ToolCallGuardrailController
    ran = {"x": False}
    def sentinel(_a):
        ran["x"] = True; return '{"ok": "handler"}'
    ag = types.SimpleNamespace(
        _tool_guardrails=ToolCallGuardrailController(),
        _guardrail_block_result=lambda d: '{"error": "g"}',
        _checkpoint_mgr=types.SimpleNamespace(enabled=False), _current_tool=None,
        _touch_activity=lambda *a, **k: None, _turns_since_memory=0, _iters_since_skill=0,
        tool_progress_callback=None, tool_start_callback=None, quiet_mode=True,
        tool_progress_mode="off", verbose_logging=False, log_prefix_chars=80, log_prefix="")
    ref = te._ToolCallRef(tool, dict(args or {}), "t", "c1", [])
    st = te._ManagedToolResult(result=None, args=dict(args or {}), middleware_trace=[],
                               blocked=False, dispatched=False)
    r = te._dispatch_authorized_once(ag, st, ref, execute=sentinel, scope_block=None,
                                     display_index=None, begin_execution=None,
                                     authorization_gate=None)
    return {"executed": ran["x"], "result": str(r)}

print("=== R-1: a projection must be the ROLE's frozen envelope, not merely a subset ===")
root = fresh("shape"); ap = approve(root); cb, topo = ap["contract"], ap["topology"]
src_dir = str(Path(root) / "src")
SHAPE_VIOLATION = {
    "allowed_tools": ["read_file", "search_files", "terminal"],
    "allowed_commands": list(cb["capability_envelope"]["allowed_commands"]),
    "command_policy": dict(cb["capability_envelope"]["command_policy"]),
}
violating = {"role": "REVIEWER", "capability_envelope": SHAPE_VIOLATION,
             "read_scope": cb["read_scope"]}
check("R-1 the violating REVIEWER projection IS a legal subset of the approval, so the "
      "subset proof alone cannot catch it",
      code_of(lambda: P.prove_subset(violating, cb)) is None)
check("R-1 the role-shape proof catches it",
      code_of(lambda: P.prove_role_shape(violating, cb)) == blocking.ACTOR_PROJECTION_NOT_SUBSET,
      f"({code_of(lambda: P.prove_role_shape(violating, cb))})")
A.install_projection("BUILDER", cb, topo)
baseline = sorted(HP._STATE["capability"]["allowed_tools"])
real_derive = P.derive
try:
    P.derive = lambda role, contract, doc: dict(violating, role=role)
    shape_code = code_of(lambda: A.install_projection("REVIEWER", cb, topo))
finally:
    P.derive = real_derive
live = sorted(HP._STATE["capability"]["allowed_tools"])
check("R-1 install_projection refuses a shape-violating projection",
      shape_code == blocking.ACTOR_PROJECTION_NOT_SUBSET, f"({shape_code})")
check("R-1 the refusal left the previous boundary, not an unguarded process",
      live == baseline, f"(baseline={baseline} live={live})")
check("R-1 a REVIEWER never gained command authority through the wrong shape",
      "terminal" not in P.derive("REVIEWER", cb, topo)["capability_envelope"]["allowed_tools"])
A.install_projection("REVIEWER", cb, topo)
term = drive("terminal", {"command": "python3 check.py", "timeout": 30, "workdir": src_dir})
check("R-1 the genuine REVIEWER projection refuses terminal through the real funnel",
      term["executed"] is False and "capability envelope" in term["result"],
      f"({term['result'][:110]})")
A.install_projection("BUILDER", cb, topo)
falsify("R-1 the same terminal call DOES reach its handler under the BUILDER projection, "
        "so the refusal above is the role and not the call",
        drive("terminal", {"command": "python3 check.py", "timeout": 30,
                           "workdir": src_dir})["executed"] is True)
falsify("R-1 the genuine projections still pass the role-shape proof, so it is not "
        "refusing every projection",
        P.derive("BUILDER", cb, topo)["role"] == "BUILDER"
        and P.derive("REVIEWER", cb, topo)["role"] == "REVIEWER")

print("\n=== R-3: a failed install must leave nothing granted ===")
A.install_projection("BUILDER", cb, topo)
real_drive = ST._drive_real_dispatch
try:
    ST._drive_real_dispatch = lambda n: {"executed": True, "result": "{}"}
    probe_code = code_of(lambda: A.install_projection("REVIEWER", cb, topo))
finally:
    ST._drive_real_dispatch = real_drive
after = sorted(HP._STATE["capability"]["allowed_tools"])
check("R-3 an install refused AFTER the boundary was replaced fails closed to deny-all",
      probe_code == blocking.ACTOR_PROJECTION_NOT_PROVEN and after == [],
      f"({probe_code}, live={after})")
check("R-3 deny-all really denies through the real funnel",
      drive("read_file", {"path": str(Path(root) / "src" / "calc.py")})["executed"] is False)
check("R-3 deny-all is the SAME guard, not a second mechanism",
      HP.capability_live() is True)
A.install_projection("BUILDER", cb, topo)
falsify("R-3 a normal install recovers from deny-all, so the fail-closed state is not "
        "permanent",
        drive("read_file", {"path": str(Path(root) / "src" / "calc.py")})["executed"] is True)

print("\n=== V-1: the run-policy digest, exercised on a NON-terminal run ===")
root = fresh("policy"); ap = approve(root, max_attempts=2); rd = Path(ap["run_directory"])
policy = json.loads((rd / "run-policy.json").read_text())
policy["max_attempts"] = 99
(rd / "run-policy.json").write_text(json.dumps(policy))
code = code_of(lambda: A.execute(rd, builder=build_only(root),
                                 reviewer=E.ScriptedReviewer([PASS_V()]), verify=verify))
check("V-1 raising the budget on disk is refused by the run-policy DIGEST, exactly",
      code == blocking.RUN_POLICY_DIGEST_MISMATCH, f"({code})")
check("V-1 no attempt was started under the forged budget",
      J.read(rd)["attempts"] == [])
root = fresh("policy-ok"); ap2 = approve(root, max_attempts=2); rd2 = Path(ap2["run_directory"])
falsify("V-1 an untouched policy lets the SAME call proceed, so the digest check is "
        "what refused above",
        A.execute(rd2, builder=build_only(root), reviewer=E.ScriptedReviewer([PASS_V()]),
                  verify=verify) is not None
        and J.read(rd2)["terminal"]["outcome"] == "COMPLETE")

print("\n=== R-2: PINNED known limitation — the run lock is entry-point-scoped ===")
# Roadmap invariant 6: a declared limitation is pinned by a test, at a location,
# so it cannot silently widen OR silently close without this test changing.
import runlock as RL
root = fresh("pin"); ap = approve(root); rd = Path(ap["run_directory"])
rec = J.transition(rd, J.read(rd), J.ARMED, note="armed")
(rd / "pre-turn-snapshot-001.json").write_text(json.dumps(
    {"files": RC.snapshot(str(root)), "git": RC.git_status(str(root))}))
rec = J.start_attempt(rd, rec, snapshot_file="pre-turn-snapshot-001.json", actor="BUILDER")
rec = J.transition(rd, rec, J.TURN_ACTIVE, note="in flight")
lock = RL.RunLock(rd); lock.acquire()
check("R-2 (setup) the run is locked and has an in-flight attempt",
      J.read(rd)["state"] == "TURN_ACTIVE" and J.has_outstanding_obligation(J.read(rd)) is True)
m6_code = code_of(lambda: A.execute(rd, builder=build_only(root),
                                    reviewer=E.ScriptedReviewer([PASS_V()]), verify=verify))
check("R-2 M6's OWN entry point is refused by the lock",
      m6_code == blocking.ACTOR_HANDOFF_REFUSED, f"({m6_code})")
m5_code = code_of(lambda: U.execute(rd, turn_driver=build_only(root),
                                    is_work_finished=lambda *a, **k: True))
discharged = (rd / "reconciliation-001.json").exists()
attempt = J.read(rd)["attempts"][0]
check("R-2 [KNOWN GAP, pinned] M5's unattended.execute is NOT stopped by M6's lock and "
      "discharges the locked run's in-flight obligation before refusing on the actor rule",
      m5_code == blocking.ACTOR_NOT_RECORDED and discharged is True
      and attempt["reconciled"] is True,
      f"(code={m5_code}, reconciled={attempt['reconciled']}, recon_file={discharged})")
print("      ^ this PASS records a limitation, not a guarantee. Closing it requires an")
print("        erratum widening unattended.py's permitted change; see M6-REVIEW.md R-2.")
lock.release()

print(f"\n{passed} passed, {failed} failed, {falsifiers} falsifiers")
sys.exit(1 if failed else 0)
PY
