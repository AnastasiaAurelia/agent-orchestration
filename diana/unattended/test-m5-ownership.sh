#!/usr/bin/env bash
# M5 checkpoint 2: per-PID ownership and quiescence (M5-D14, M5-D15).
set -uo pipefail
UN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIANA_DIR="$(cd "$UN_DIR/.." && pwd)"
PY_BIN="python3"; HH="${DIANA_HERMES_HOME:-$HOME/.hermes/hermes-agent}"
[ -x "$HH/venv/bin/python3" ] && PY_BIN="$HH/venv/bin/python3"
TMP_DIR="$(mktemp -d)"; trap 'rm -rf "$TMP_DIR"' EXIT
"$PY_BIN" - "$DIANA_DIR" "$TMP_DIR" <<'PY'
import os, signal, subprocess, sys, time, uuid
from pathlib import Path
diana, tmp = sys.argv[1], Path(sys.argv[2])
for sub in ("unattended", "runtime"):
    sys.path.insert(0, str(Path(diana, sub)))
import blocking, ownership as O

passed = failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")
def raises(code, fn):
    try: fn(); return False
    except blocking.Blocked as e: return e.code == code
    except Exception: return False

RUN = "m5-own-" + uuid.uuid4().hex[:12]
OTHER = "m5-own-" + uuid.uuid4().hex[:12]
check("/proc-based ownership is available on this platform", O.available())

def spawn(run_id, seconds=30):
    env = dict(os.environ); env[O.STAMP_VAR] = run_id
    return subprocess.Popen(["bash", "-c", f"sleep {seconds}"], env=env,
                            start_new_session=True,           # as Hermes does
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

print("--- ownership is proven by stamp, per PID ---")
p1 = spawn(RUN); p2 = spawn(RUN); pother = spawn(OTHER)
time.sleep(0.6)
owned = O.owned_pids(RUN)
pids = {item["pid"] for item in owned}
check("both stamped children are found", {p1.pid, p2.pid} <= pids, f"({sorted(pids)})")
check("a DIFFERENT run's process is NOT claimed", pother.pid not in pids)
check("diana itself is not claimed as its own child", os.getpid() not in pids)
check("the tree runs in its own session, not diana's",
      all(item["sid"] != os.getsid(0) for item in owned), f"({[i['sid'] for i in owned]})")
check("ownership records a start_time for recycling defence",
      all(isinstance(item["start_time"], int) for item in owned))

print("--- ownership cannot be spoofed ---")
check("a pid with the stamp but a WRONG start_time is not owned",
      not O.is_owned(p1.pid, RUN, expected_start=(O.start_time(p1.pid) or 0) + 999))
check("a pid with the right start_time IS owned",
      O.is_owned(p1.pid, RUN, expected_start=O.start_time(p1.pid)))
check("a foreign run_id does not match a stamped process", not O.is_owned(p1.pid, OTHER))
check("pid 1 is never owned", not O.is_owned(1, RUN))
check("an unreadable/nonexistent pid is not owned", not O.is_owned(2**22, RUN))
# The TRUE property, asserted precisely: a process cannot rewrite its own
# /proc environ after exec, so it cannot disown itself in place.
env = dict(os.environ); env[O.STAMP_VAR] = RUN
p3 = subprocess.Popen(
    ["bash", "-c", f"unset {O.STAMP_VAR}; exec sleep 30"], env=env,
    start_new_session=True, stdout=subprocess.DEVNULL)
p3b = subprocess.Popen(
    ["python3", "-c",
     f"import os; os.environ.pop('{O.STAMP_VAR}', None); import time; time.sleep(30)"],
    env=env, start_new_session=True, stdout=subprocess.DEVNULL)
time.sleep(0.8)
check("a process that pops the var from its OWN environment is STILL owned "
      "(/proc environ is fixed at exec)", O.is_owned(p3b.pid, RUN))
# And the measured LIMITATION, asserted so it cannot be forgotten or overclaimed:
# re-exec'ing a child with a scrubbed environment does shed the stamp.
check("LIMITATION: a command that re-execs with a scrubbed env sheds the stamp",
      not O.is_owned(p3.pid, RUN),
      "(if this ever passes, the limitation changed and the docs must too)")
os.kill(p3.pid, signal.SIGKILL); p3.wait()

print("--- quiescence is refused while the run is alive ---")
check("require_quiescent(terminate=False) REFUSES while processes live",
      raises(blocking.QUIESCENCE_NOT_PROVEN,
             lambda: O.require_quiescent(RUN, terminate=False)))

print("--- graceful termination, then escalation, per owned PID ---")
# A child that IGNORES SIGTERM forces the escalation path to be exercised.
env = dict(os.environ); env[O.STAMP_VAR] = RUN
stubborn = subprocess.Popen(["bash", "-c", "trap '' TERM; sleep 60"], env=env,
                            start_new_session=True, stdout=subprocess.DEVNULL)
time.sleep(0.6)
before = {item["pid"] for item in O.owned_pids(RUN)}
check("the SIGTERM-ignoring child is owned before termination", stubborn.pid in before)
result = O.require_quiescent(RUN, grace_seconds=2.0)
check("quiescence is achieved", result["quiescent"])
check("SIGTERM was sent first, to every owned pid", len(result["terminated"]["term"]) > 0)
check("escalation to SIGKILL happened for the survivor",
      any(k["pid"] == stubborn.pid for k in result["terminated"]["kill"]),
      f"({result['terminated']['kill']})")
check("no owned process remains -- proven by OBSERVATION, not by having asked",
      O.owned_pids(RUN) == [])
for p in (p1, p2, p3b, stubborn):
    check(f"owned pid {p.pid} is observably gone", not O.alive(p.pid))

print("--- the OTHER run was never touched ---")
check("the foreign run's process is STILL ALIVE (no collateral kill)", O.alive(pother.pid))
check("and is still owned by its own run id", O.is_owned(pother.pid, OTHER))
os.kill(pother.pid, signal.SIGKILL); pother.wait()

print("--- quiescence on an already-quiet run ---")
res = O.require_quiescent("m5-own-never-existed")
check("a run with no processes is quiescent with nothing terminated",
      res["quiescent"] and res["terminated"] is None)

print("--- stamping marks descendants, not the setter ---")
marker = "m5-stamp-" + uuid.uuid4().hex[:8]
O.stamp_environment(marker)
check("the setter's own /proc environ does NOT show a runtime-set stamp",
      not O.is_owned(os.getpid(), marker))
child = subprocess.Popen(["bash", "-c", "sleep 5"], start_new_session=True,
                         stdout=subprocess.DEVNULL)
time.sleep(0.5)
check("but a child spawned afterwards DOES carry it", O.is_owned(child.pid, marker))
O.require_quiescent(marker, grace_seconds=2.0)
check("and that child is cleaned up too", not O.alive(child.pid))
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
