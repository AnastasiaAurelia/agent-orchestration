#!/usr/bin/env bash
# M6 FOCUSED THIRD PASS — only the three controls the independent review touched.
#
#   1. R-1 prove_role_shape: near-miss Reviewer projections, one axis at a time.
#   2. R-3 fail-closed install: a failure at EVERY validation and probe stage.
#   3. R-2 run lease: the peer bypass, from a real second process.
#
# Written as if by someone who does not believe these controls work. Every case
# drives the real dispatch funnel or a real process; none is satisfied by a model
# declining to act.
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
import json, os, signal, subprocess, sys, tempfile, textwrap, time, types, uuid
from pathlib import Path

diana, tmp, repo_dir, hermes_home = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
for sub in ("multiactor", "unattended", "runtime", "mutation", "adapters", "profile"):
    sys.path.insert(0, str(Path(diana, sub)))
import blocking, journal as J, reconcile as RC, unattended as U
import actors as A, executors as E, projection as P, runlease as RLS
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

RUNS = Path(tempfile.mkdtemp(prefix="m6-third-", dir=str(tmp)))
BROKEN = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b    # repaired\n"

def fresh(name):
    root = tmp / f"t3-{name}-{uuid.uuid4().hex[:6]}"
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
    g("init", "-q"); g("config", "user.email", "t@x"); g("config", "user.name", "t")
    g("add", "-A"); g("commit", "-qm", "init")
    return root

def approve(root, **kw):
    kw.setdefault("allowed_commands", ("python3 check.py",))
    kw.setdefault("write_roots", (str(Path(root) / "src"),))
    kw.setdefault("max_attempts", 8); kw.setdefault("total_seconds", 1800)
    return A.approve(task="repair add", repo_root=str(root), runs_base=str(RUNS), **kw)

def drive(tool, args=None):
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

root = fresh("base"); appr = approve(root); cb = appr["contract"]; topo = appr["topology"]
SRC = str(Path(root) / "src")
PARENT = cb["capability_envelope"]
LIVE = {
    "read_file": {"path": str(Path(root) / "src" / "calc.py")},
    "search_files": {"pattern": "add", "path": SRC},
    "write_file": {"path": str(Path(root) / "src" / "calc.py"), "content": BROKEN},
    "patch": {"mode": "patch",
              "patch": f"*** Update File: {Path(root) / 'src' / 'calc.py'}\n-a\n+b\n"},
    "terminal": {"command": "python3 check.py", "timeout": 30, "workdir": SRC},
    "delegate_task": {"goal": "escape"},
}

print("=== 1. R-1 — near-miss REVIEWER projections, one axis at a time ===")
base_tools = ["read_file", "search_files"]
NEAR_MISSES = {
    "terminal added": {"allowed_tools": base_tools + ["terminal"]},
    "patch added": {"allowed_tools": base_tools + ["patch"]},
    "write_file added": {"allowed_tools": base_tools + ["write_file"]},
    "delegate_task added": {"allowed_tools": base_tools + ["delegate_task"]},
    "command added": {"allowed_tools": base_tools,
                      "allowed_commands": list(PARENT["allowed_commands"])},
    "write_scope added": {"allowed_tools": base_tools,
                          "write_scope": dict(PARENT["write_scope"])},
    "unknown envelope field": {"allowed_tools": base_tools, "superpowers": True},
    "correct tools, wrong command policy": {
        "allowed_tools": base_tools,
        "command_policy": dict(PARENT["command_policy"])},
    "a tool REMOVED (narrower than frozen)": {"allowed_tools": ["read_file"]},
}
for label, env in NEAR_MISSES.items():
    proj = {"role": "REVIEWER", "capability_envelope": env, "read_scope": cb["read_scope"]}
    shape = code_of(lambda p=proj: P.prove_role_shape(p, cb))
    check(f"R-1 near-miss refused by the role proof: {label}",
          shape == blocking.ACTOR_PROJECTION_NOT_SUBSET, f"(got {shape})")
broad_read = {"role": "REVIEWER", "capability_envelope": {"allowed_tools": base_tools},
              "read_scope": {"allowed_roots": ["/"],
                             "denied_subpaths": cb["read_scope"]["denied_subpaths"]}}
check("R-1 near-miss refused: read_scope broadened",
      code_of(lambda: P.prove_role_shape(broad_read, cb)) == blocking.ACTOR_PROJECTION_NOT_SUBSET)
narrow_read = {"role": "REVIEWER", "capability_envelope": {"allowed_tools": base_tools},
               "read_scope": {"allowed_roots": [SRC],
                              "denied_subpaths": cb["read_scope"]["denied_subpaths"]}}
check("R-1 near-miss refused: read_scope narrowed (the comparison is by VALUE, not "
      "'within the parent')",
      code_of(lambda: P.prove_role_shape(narrow_read, cb)) == blocking.ACTOR_PROJECTION_NOT_SUBSET)
builder_as_reviewer = {"role": "REVIEWER", "capability_envelope": dict(PARENT),
                       "read_scope": cb["read_scope"]}
check("R-1 the BUILDER envelope offered as a REVIEWER projection is refused",
      code_of(lambda: P.prove_role_shape(builder_as_reviewer, cb))
      == blocking.ACTOR_PROJECTION_NOT_SUBSET)
reviewer_as_builder = {"role": "BUILDER", "capability_envelope": {"allowed_tools": base_tools},
                       "read_scope": cb["read_scope"]}
check("R-1 the REVIEWER envelope offered as a BUILDER projection is refused",
      code_of(lambda: P.prove_role_shape(reviewer_as_builder, cb))
      == blocking.ACTOR_PROJECTION_NOT_SUBSET)
check("R-1 the near-misses that are legal SUBSETS pass prove_subset, so the role proof "
      "is doing work the subset proof cannot",
      code_of(lambda: P.prove_subset(
          {"role": "REVIEWER",
           "capability_envelope": {"allowed_tools": base_tools + ["terminal"]},
           "read_scope": cb["read_scope"]}, cb)) is None)
falsify("R-1 the genuine projections pass the role proof, so it is not refusing everything",
        P.derive("BUILDER", cb, topo)["role"] == "BUILDER"
        and P.derive("REVIEWER", cb, topo)["role"] == "REVIEWER")
for role, must_refuse in (("BUILDER", ("delegate_task",)),
                          ("REVIEWER", ("write_file", "patch", "terminal", "delegate_task"))):
    A.install_projection(role, cb, topo)
    refused = {t: drive(t, LIVE[t])["executed"] for t in must_refuse}
    check(f"R-1 {role}'s installed projection refuses {list(must_refuse)} through the real funnel",
          not any(refused.values()), f"({refused})")

print("\n=== 2. R-3 — a failure at EVERY stage leaves nothing granted ===")
def builder_installed():
    A.install_projection("BUILDER", cb, topo)
    return sorted(HP._STATE["capability"]["allowed_tools"])

stages = {}
# (a) failure at derive
builder_installed()
real_derive = P.derive
try:
    def boom(*a, **k):
        raise blocking.Blocked(blocking.ACTOR_UNKNOWN, "derive exploded")
    P.derive = boom
    stages["derive raises"] = (code_of(lambda: A.install_projection("REVIEWER", cb, topo)),
                               sorted(HP._STATE["capability"]["allowed_tools"]))
finally:
    P.derive = real_derive
# (b) failure at prove_subset
builder_installed()
try:
    P.derive = lambda r, c, d: {"role": r,
                                "capability_envelope": {"allowed_tools": ["execute_code"]},
                                "read_scope": c["read_scope"]}
    stages["subset refuses"] = (code_of(lambda: A.install_projection("REVIEWER", cb, topo)),
                                sorted(HP._STATE["capability"]["allowed_tools"]))
finally:
    P.derive = real_derive
# (c) failure at prove_role_shape
builder_installed()
try:
    P.derive = lambda r, c, d: {"role": r,
                                "capability_envelope": {"allowed_tools": base_tools + ["terminal"],
                                                        "allowed_commands": list(PARENT["allowed_commands"]),
                                                        "command_policy": dict(PARENT["command_policy"])},
                                "read_scope": c["read_scope"]}
    stages["role shape refuses"] = (code_of(lambda: A.install_projection("REVIEWER", cb, topo)),
                                    sorted(HP._STATE["capability"]["allowed_tools"]))
finally:
    P.derive = real_derive
# (d) failure at the liveness flag check
builder_installed()
real_cap_live = HP.capability_live
try:
    HP.capability_live = lambda: False
    stages["liveness flag false"] = (code_of(lambda: A.install_projection("REVIEWER", cb, topo)),
                                     sorted(HP._STATE["capability"]["allowed_tools"]))
finally:
    HP.capability_live = real_cap_live
# (e) failure at the forbidden probe
builder_installed()
real_drive = ST._drive_real_dispatch
try:
    ST._drive_real_dispatch = lambda n: {"executed": True, "result": "{}"}
    stages["forbidden probe executes"] = (code_of(lambda: A.install_projection("REVIEWER", cb, topo)),
                                          sorted(HP._STATE["capability"]["allowed_tools"]))
finally:
    ST._drive_real_dispatch = real_drive
# (f) failure at the permitted probe
builder_installed()
try:
    ST._drive_real_dispatch = lambda n: {"executed": False, "result": "{}"}
    stages["permitted probe refused"] = (code_of(lambda: A.install_projection("REVIEWER", cb, topo)),
                                         sorted(HP._STATE["capability"]["allowed_tools"]))
finally:
    ST._drive_real_dispatch = real_drive

for label, (code, live) in stages.items():
    check(f"R-3 {label}: refused", code is not None, f"(code={code})")
PRE_INSTALL = ("derive raises", "subset refuses", "role shape refuses")
for label, (code, live) in stages.items():
    if label in PRE_INSTALL:
        check(f"R-3 {label}: refused BEFORE the boundary was replaced, so the previous "
              f"BUILDER projection is intact",
              live == sorted(PARENT["allowed_tools"]), f"(live={live})")
    else:
        check(f"R-3 {label}: refused AFTER the boundary was replaced, so it fails closed "
              f"to deny-all", live == [], f"(live={live})")
# deny-all must reach the real dispatch guards, not merely the flag.
builder_installed()
try:
    ST._drive_real_dispatch = lambda n: {"executed": True, "result": "{}"}
    code_of(lambda: A.install_projection("REVIEWER", cb, topo))
finally:
    ST._drive_real_dispatch = real_drive
denied = {t: drive(t, LIVE[t])["executed"]
          for t in ("read_file", "search_files", "write_file", "patch", "terminal", "delegate_task")}
check("R-3 deny-all refuses EVERY tool through the real dispatch funnel",
      not any(denied.values()), f"({denied})")
check("R-3 deny-all keeps the Diana guard installed rather than restoring unpatched Hermes",
      HP.capability_live() is True and HP.confinement_live() is True)
# A substring grep here would match the COMMENT explaining why uninstall() is
# not used -- a source grep matching a docstring is the vacuity pattern this
# review exists to catch, so the check is on the AST: no CALL to uninstall.
import ast as _ast
_tree = _ast.parse(Path(diana, "multiactor", "actors.py").read_text())
_uninstall_calls = [n for n in _ast.walk(_tree)
                    if isinstance(n, _ast.Call)
                    and getattr(n.func, "attr", getattr(n.func, "id", "")) == "uninstall"]
check("R-3 uninstall() is never CALLED as the failure fallback (AST, not a grep)",
      _uninstall_calls == [], f"({len(_uninstall_calls)} call(s))")
_denied_after = drive("read_file", LIVE["read_file"])
check("R-3 and the behavioral consequence holds: after a failed install, read_file is "
      "refused -- unpatched Hermes would have executed it",
      _denied_after["executed"] is False, f"({_denied_after['result'][:90]})")
proj = A.install_projection("REVIEWER", cb, topo)
after = {t: drive(t, LIVE[t])["executed"]
         for t in ("read_file", "search_files", "write_file", "patch", "terminal")}
check("R-3 a subsequent valid REVIEWER projection installs cleanly and grants ONLY "
      "reviewer authority",
      after["read_file"] and after["search_files"]
      and not after["write_file"] and not after["patch"] and not after["terminal"],
      f"({after})")
falsify("R-3 a subsequent BUILDER projection recovers full builder authority, so deny-all "
        "is not a permanent state",
        (A.install_projection("BUILDER", cb, topo) is not None)
        and drive("write_file", LIVE["write_file"])["executed"] is True)

print("\n=== 3. R-2 — the peer bypass, from a real second process ===")
root2 = fresh("peer"); appr2 = approve(root2); rd = Path(appr2["run_directory"])
rec = J.transition(rd, J.read(rd), J.ARMED, note="armed")
(rd / "pre-turn-snapshot-001.json").write_text(json.dumps(
    {"files": RC.snapshot(str(root2)), "git": RC.git_status(str(root2))}))
rec = J.start_attempt(rd, rec, snapshot_file="pre-turn-snapshot-001.json", actor="BUILDER")
J.transition(rd, rec, J.TURN_ACTIVE, note="in flight")
owner = subprocess.Popen(
    [sys.executable, "-c",
     "import sys,time;"
     f"sys.path.insert(0,{str(Path(diana, 'runtime'))!r});"
     f"import runlease;l=runlease.RunLease({str(rd)!r});l.acquire();"
     "print('LEASED',flush=True);time.sleep(300)"],
    stdout=subprocess.PIPE, text=True, start_new_session=True)
assert owner.stdout.readline().strip() == "LEASED"
before = sorted(p.name for p in rd.iterdir())
journal_before = (rd / "journal.json").read_bytes()
target_before = RC.snapshot(str(root2))
peer = code_of(lambda: U.execute(rd, turn_driver=E.ScriptedBuilder({}),
                                 is_work_finished=lambda *a, **k: True))
check("R-2 the peer is refused with exactly actor-handoff-refused",
      peer == blocking.ACTOR_HANDOFF_REFUSED, f"({peer})")
check("R-2 no reconciliation side effect occurred before the refusal",
      not (rd / "reconciliation-001.json").exists()
      and sorted(p.name for p in rd.iterdir()) == before
      and (rd / "journal.json").read_bytes() == journal_before
      and J.read(rd)["state"] == "TURN_ACTIVE"
      and J.read(rd)["attempts"][0]["reconciled"] is False
      and RC.snapshot(str(root2)) == target_before)
os.kill(owner.pid, signal.SIGKILL); owner.wait(timeout=20); time.sleep(0.3)
falsify("R-2 with the owner gone the SAME peer call proceeds, so the refusal was the "
        "lease and not the entry point",
        U.discharge_obligation(rd, J.read(rd), appr2["contract"],
                               json.loads((rd / "run-policy.json").read_text()))["blocked"]
        is False)

print(f"\n{passed} passed, {failed} failed, {falsifiers} falsifiers")
sys.exit(1 if failed else 0)
PY
