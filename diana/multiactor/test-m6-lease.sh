#!/usr/bin/env bash
# M6-ERRATA-002 acceptance: the run lease (M6-E2-AC-1 .. M6-E2-AC-12).
#
# Independent-review finding R-2: M6-A4's lock was entry-point-scoped, so a peer
# using M5's still-public API reconciled a live run. These cases drive the REAL
# public entry points from a REAL second process and assert the absence of every
# effect individually -- a refusal that arrives after an effect is not a refusal.
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
import json, os, signal, subprocess, sys, tempfile, textwrap, time, uuid
from pathlib import Path

diana, tmp, repo_dir, hermes_home = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
for sub in ("multiactor", "unattended", "runtime", "mutation", "adapters", "profile"):
    sys.path.insert(0, str(Path(diana, sub)))
import blocking, journal as J, ownership as O, reconcile as RC, unattended as U
import actors as A, executors as E, runlease as RLS, runlock as RL

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

RUNS = Path(tempfile.mkdtemp(prefix="m6-lease-", dir=str(tmp)))
BROKEN = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b    # repaired\n"

def fresh(name):
    root = tmp / f"l-{name}-{uuid.uuid4().hex[:6]}"
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
    g("init", "-q"); g("config", "user.email", "l@x"); g("config", "user.name", "l")
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

def in_flight(root, appr):
    """Drive a run to a durable TURN_ACTIVE attempt, as a crash would leave it."""
    rd = Path(appr["run_directory"])
    rec = J.transition(rd, J.read(rd), J.ARMED, note="armed")
    (rd / "pre-turn-snapshot-001.json").write_text(json.dumps(
        {"files": RC.snapshot(str(root)), "git": RC.git_status(str(root))}))
    rec = J.start_attempt(rd, rec, snapshot_file="pre-turn-snapshot-001.json", actor="BUILDER")
    J.transition(rd, rec, J.TURN_ACTIVE, note="in flight")
    return rd

def lease_holder_process(rd):
    """A REAL separate process that takes the lease and stays alive."""
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import sys,time;"
         f"sys.path.insert(0,{str(Path(diana, 'runtime'))!r});"
         f"import runlease;l=runlease.RunLease({str(rd)!r});l.acquire();"
         "print('LEASED',flush=True);time.sleep(300)"],
        stdout=subprocess.PIPE, text=True, start_new_session=True)
    assert proc.stdout.readline().strip() == "LEASED"
    return proc

print("=== M6-E2-AC-1..3 — a peer is refused at EVERY public entry, before any effect ===")
root = fresh("peer"); appr = approve(root); rd = in_flight(root, appr)
cb = appr["contract"]; pol = json.loads((rd / "run-policy.json").read_text())
owner = lease_holder_process(rd)
files_before = sorted(p.name for p in rd.iterdir())
journal_before = (rd / "journal.json").read_bytes()
target_before = RC.snapshot(str(root))
check("M6-E2-AC-1 (setup) the run is TURN_ACTIVE with an outstanding obligation and a live owner",
      J.read(rd)["state"] == "TURN_ACTIVE"
      and J.has_outstanding_obligation(J.read(rd)) is True
      and RLS.probe(rd)["held"] is True)
codes = {
    "unattended.execute": code_of(lambda: U.execute(
        rd, turn_driver=build_only(root), is_work_finished=lambda *a, **k: True)),
    "discharge_obligation": code_of(lambda: U.discharge_obligation(rd, J.read(rd), cb, pol)),
}
armed_view = json.loads(json.dumps(J.read(rd))); armed_view["state"] = "ARMED"
codes["run_attempt"] = code_of(lambda: U.run_attempt(
    rd, armed_view, cb, pol, build_only(root), item_id="item-1", actor="BUILDER"))
for entry, got in codes.items():
    check(f"M6-E2-AC-1/3 peer via {entry} is refused with exactly actor-handoff-refused",
          got == blocking.ACTOR_HANDOFF_REFUSED, f"(got {got})")
attempt = J.read(rd)["attempts"][0]
check("M6-E2-AC-2 no reconciliation record was written",
      not (rd / "reconciliation-001.json").exists())
check("M6-E2-AC-2 no new pre-turn snapshot was written",
      sorted(p.name for p in rd.iterdir()) == files_before,
      f"({set(p.name for p in rd.iterdir()) - set(files_before)})")
check("M6-E2-AC-2 the attempt was not closed", attempt["state"] == "OPEN", f"({attempt['state']})")
check("M6-E2-AC-2 reconciled was not flipped to true", attempt["reconciled"] is False)
check("M6-E2-AC-2 the journal did not advance toward ARMED",
      J.read(rd)["state"] == "TURN_ACTIVE", f"({J.read(rd)['state']})")
check("M6-E2-AC-2 no new attempt was started", len(J.read(rd)["attempts"]) == 1)
check("M6-E2-AC-2 the journal file is byte-unchanged",
      (rd / "journal.json").read_bytes() == journal_before)
check("M6-E2-AC-2 the target repository is byte-unchanged", RC.snapshot(str(root)) == target_before)
check("M6-E2-AC-9 no process-group, name-pattern or broad PID action exists in the lease",
      not any(t in (Path(diana, "runtime", "runlease.py").read_text()
                    + Path(diana, "multiactor", "runlock.py").read_text())
              for t in ("killpg", "pkill", "os.kill", "-9", "getpgid")))

print("\n=== M6-E2-AC-4 — the legitimate owner is NOT deadlocked by its own guard ===")
os.kill(owner.pid, signal.SIGKILL); owner.wait(timeout=20); time.sleep(0.3)
mine = RL.RunLock(rd); mine.acquire()
check("M6-E2-AC-4 the owner's own probe reports the lease as its own",
      RLS.probe(rd)["is_self"] is True)
outcome = U.discharge_obligation(rd, J.read(rd), cb, pol)
check("M6-E2-AC-4 the owner discharges the obligation through the guarded path",
      outcome["blocked"] is False and J.read(rd)["attempts"][0]["reconciled"] is True)
check("M6-E2-AC-5 the obligation was discharged exactly once",
      sorted(p.name for p in rd.iterdir() if p.name.startswith("reconciliation-"))
      == ["reconciliation-001.json"])
mine.release()

print("\n=== M6-E2-AC-5..8 — recovery, crash, stale file, fork ===")
root = fresh("recover"); appr = approve(root); rd = in_flight(root, appr)
dead = lease_holder_process(rd)
os.kill(dead.pid, signal.SIGKILL); dead.wait(timeout=20); time.sleep(0.3)
check("M6-E2-AC-7 a crash releases the lease", RLS.probe(rd)["held"] is False)
check("M6-E2-AC-6 the stale lock FILE remains on disk", (rd / "executor.lock").is_file())
result = A.execute(rd, builder=build_only(root), reviewer=E.ScriptedReviewer([PASS_V()]),
                   verify=verify)
rec = J.read(rd)
check("M6-E2-AC-6 a stale file does not block recovery: a fresh executor completed the run",
      rec["terminal"]["outcome"] == "COMPLETE", f"({rec['terminal']})")
check("M6-E2-AC-5 exactly one reconciliation per attempt, none duplicated",
      len([p for p in rd.iterdir() if p.name.startswith("reconciliation-")]) == len(rec["attempts"]))
check("M6-E2-AC-5 the crashed attempt's obligation was discharged by the fresh executor",
      rec["attempts"][0]["reconciled"] is True and rec["attempts"][0]["actor"] == "BUILDER")

root = fresh("fork"); appr = approve(root); rd = in_flight(root, appr)
forked = subprocess.Popen(
    [sys.executable, "-c",
     "import os,sys,time;"
     f"sys.path.insert(0,{str(Path(diana, 'runtime'))!r});"
     f"import runlease;l=runlease.RunLease({str(rd)!r});l.acquire();print('LEASED',flush=True);"
     "pid=os.fork()\nif pid==0:\n    time.sleep(120)\nelse:\n    time.sleep(0.5); os._exit(0)"],
    stdout=subprocess.PIPE, text=True, start_new_session=True)
assert forked.stdout.readline().strip() == "LEASED"
forked.wait(timeout=30); time.sleep(1.0)
grandchildren = [int(e.name) for e in Path("/proc").iterdir()
                 if e.name.isdigit() and "time.sleep(120)" in O.cmdline(int(e.name))]
check("M6-E2-AC-8 a forked descendant RETAINS the lease after the acquirer exits "
      "(flock follows the open file description)",
      RLS.probe(rd)["held"] is True)
check("M6-E2-AC-8 the run is refused rather than silently advanced while it is retained",
      code_of(lambda: U.discharge_obligation(
          rd, J.read(rd), appr["contract"],
          json.loads((rd / "run-policy.json").read_text()))) == blocking.ACTOR_HANDOFF_REFUSED)
for pid in grandchildren:                      # per-PID only, never a group kill (M5-D14)
    try: os.kill(pid, signal.SIGKILL)
    except OSError: pass
time.sleep(0.5)
falsify("M6-E2-AC-8 once the descendant dies the lease frees, so the refusal above was "
        "the retained lease and not a permanent block",
        RLS.probe(rd)["held"] is False)

print("\n=== M6-E2-AC-10 — a run with no lease file is M5's case, unchanged ===")
root = fresh("m5style")
m5 = U.approve(task="m5 style", repo_root=str(root), allowed_commands=("true",),
               write_roots=(str(root / "src"),), runs_base=str(RUNS), max_attempts=2)
rd5 = Path(m5["run_directory"])
check("M6-E2-AC-10 (setup) an M5 run has no lease file", not (rd5 / "executor.lock").exists())
check("M6-E2-AC-10 require_lease permits a run with no lease file",
      RLS.require_lease(rd5)["held"] is False)
def m5_driver(cb, item_id=None):
    Path(cb["target"]["repo_root"], "src", "calc.py").write_text(FIXED)
m5_driver.record = None
U.execute(rd5, turn_driver=m5_driver, is_work_finished=lambda *a, **k: True)
check("M6-E2-AC-10 an M5 single-actor run still completes end to end",
      J.read(rd5)["terminal"]["outcome"] == "COMPLETE", f"({J.read(rd5)['terminal']})")
check("M6-E2-AC-10 its attempt resolved to the sole actor, as M5 always behaved",
      J.read(rd5)["attempts"][0]["actor"] == "BUILDER"
      and J.read(rd5)["actor_topology_digest"] is None)

print("\n=== falsifiers: the guard is load-bearing, not decorative ===")
root = fresh("fals"); appr = approve(root); rd = in_flight(root, appr)
cb = appr["contract"]; pol = json.loads((rd / "run-policy.json").read_text())
held = lease_holder_process(rd)
blocked_code = code_of(lambda: U.discharge_obligation(rd, J.read(rd), cb, pol))
os.kill(held.pid, signal.SIGKILL); held.wait(timeout=20); time.sleep(0.3)
freed = U.discharge_obligation(rd, J.read(rd), cb, pol)
falsify("M6-E2-AC-1 the SAME call that was refused under a live lease succeeds once it "
        "is released, so the refusal was the lease and not the call",
        blocked_code == blocking.ACTOR_HANDOFF_REFUSED and freed["blocked"] is False,
        f"({blocked_code}, {freed['blocked']})")
real_probe = RLS.probe
try:
    RLS.probe = lambda rd_: {"held": True, "holder": {"pid": -1, "start_time": -1},
                             "is_self": False}
    forced = code_of(lambda: U.run_attempt(rd, J.read(rd), cb, pol, build_only(root),
                                           item_id="item-1", actor="BUILDER"))
finally:
    RLS.probe = real_probe
falsify("M6-E2-AC-3 with the probe forced to report a foreign holder, run_attempt refuses "
        "before writing its snapshot -- the guard is what stops it",
        forced == blocking.ACTOR_HANDOFF_REFUSED, f"({forced})")
check("M6-E2-AC-11 runlock.RunLock is the lease primitive, not a second implementation",
      issubclass(RL.RunLock, RLS.RunLease))
check("M6-E2-AC-11 runlease.start_time agrees with ownership.start_time",
      RLS.start_time(os.getpid()) == O.start_time(os.getpid()))

print(f"\n{passed} passed, {failed} failed, {falsifiers} falsifiers")
sys.exit(1 if failed else 0)
PY
