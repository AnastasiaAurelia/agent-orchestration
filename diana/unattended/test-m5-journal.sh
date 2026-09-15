#!/usr/bin/env bash
# M5 checkpoint 1: run policy + journal (M5-D3, M5-D4, M5-D8, M5-D9).
set -uo pipefail
UN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIANA_DIR="$(cd "$UN_DIR/.." && pwd)"
PY_BIN="python3"; HH="${DIANA_HERMES_HOME:-$HOME/.hermes/hermes-agent}"
[ -x "$HH/venv/bin/python3" ] && PY_BIN="$HH/venv/bin/python3"
TMP_DIR="$(mktemp -d)"; trap 'rm -rf "$TMP_DIR"' EXIT
"$PY_BIN" - "$DIANA_DIR" "$TMP_DIR" <<'PY'
import json, os, stat, sys, subprocess
from pathlib import Path
diana, tmp = sys.argv[1], Path(sys.argv[2])
for sub in ("unattended", "runtime"):
    sys.path.insert(0, str(Path(diana, sub)))
import blocking, journal as J, runpolicy as RP, contract as C

passed = failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")
def raises(code, fn):
    try: fn(); return False
    except blocking.Blocked as e: return e.code == code
    except Exception: return False

print("--- run policy ---")
pol = RP.build(run_id="r1", max_attempts=2, total_seconds=3600)
check("policy builds and validates", pol["max_attempts"] == 2)
check("deadline is ABSOLUTE, not a duration", pol["deadline_at"] > pol["created_at"])
check("policy digest recomputes", RP.digest(pol) == RP.digest(dict(pol)))
check("an unknown policy key is refused",
      raises(blocking.RUN_POLICY_MALFORMED, lambda: RP.validate({**pol, "x": 1})))
check("max_attempts=0 is refused",
      raises(blocking.RUN_POLICY_MALFORMED, lambda: RP.validate({**pol, "max_attempts": 0})))
check("a boolean max_attempts is refused, not coerced to 1",
      raises(blocking.RUN_POLICY_MALFORMED, lambda: RP.validate({**pol, "max_attempts": True})))
check("a deadline before created_at is refused",
      raises(blocking.RUN_POLICY_MALFORMED,
             lambda: RP.validate({**pol, "deadline_at": "2000-01-01T00:00:00Z"})))
check("an expired policy reports expired",
      RP.expired(RP.build(run_id="r", max_attempts=1, total_seconds=1,
                          created_at="2000-01-01T00:00:00Z")))

print("--- journal: atomic write + digest integrity ---")
rd = tmp / "runs" / "r1"
binding = {"repo_root": "/x", "git_commit": "abc", "dirty": False, "observed_at": "2026-01-01T00:00:00Z"}
rec = J.new_record(run_id="r1", contract_digest="sha256:" + "a"*64,
                   run_policy_digest=RP.digest(pol), target_binding=binding)
J.write(rd, rec)
check("journal round-trips", J.read(rd)["run_id"] == "r1")
check("journal file is 0600", stat.S_IMODE((rd / "journal.json").stat().st_mode) == 0o600)
check("run directory is 0700", stat.S_IMODE(rd.stat().st_mode) == 0o700)
check("no temp files left behind", not [p for p in rd.iterdir() if p.name.startswith(".journal")])

# A SCHEMA-VALID tamper: only the digest can catch this one, so it proves the
# digest rather than the schema. Flipping the bound commit is the realistic
# attack -- it would relax the freshness check of M5-D11.
raw = json.loads((rd / "journal.json").read_text())
raw["record"]["target_binding"]["git_commit"] = "deadbeef"
(rd / "journal.json").write_text(json.dumps(raw))
check("a SCHEMA-VALID tampered record is refused by DIGEST",
      raises(blocking.JOURNAL_DIGEST_MISMATCH, lambda: J.read(rd)))
# A schema-INVALID tamper is caught earlier, by the closed schema. Both layers
# are asserted so neither can silently become the only one.
J.write(rd, rec)
raw = json.loads((rd / "journal.json").read_text())
raw["record"]["state"] = "COMPLETE"          # terminal state, no terminal block
(rd / "journal.json").write_text(json.dumps(raw))
check("a schema-INVALID tamper is refused by the closed schema, before the digest",
      raises(blocking.JOURNAL_MALFORMED, lambda: J.read(rd)))
# And the digest alone still catches a forged state when the schema is satisfied.
J.write(rd, rec)
raw = json.loads((rd / "journal.json").read_text())
raw["record"]["state"] = "COMPLETE"
raw["record"]["terminal"] = {"outcome": "COMPLETE", "reason_code": "hermes-turn-failed",
                             "detail": "forged", "at": "2026-01-01T00:00:00Z"}
(rd / "journal.json").write_text(json.dumps(raw))
check("a forged COMPLETE that satisfies the schema is still refused by digest",
      raises(blocking.JOURNAL_DIGEST_MISMATCH, lambda: J.read(rd)))

(rd / "journal.json").write_text('{"record": {"run_id"')
check("a TORN write is treated as absent, not partially trusted",
      raises(blocking.JOURNAL_MALFORMED, lambda: J.read(rd)))

J.write(rd, rec)
raw = json.loads((rd / "journal.json").read_text())
raw["record"]["unexpected"] = 1
(rd / "journal.json").write_text(json.dumps(raw))
check("an UNKNOWN journal key is refused (closed schema)",
      raises(blocking.JOURNAL_MALFORMED, lambda: J.read(rd)))

print("--- journal: path safety (M5-D9) ---")
J.write(rd, rec)
evil = tmp / "runs" / "evil"
os.symlink(str(rd), str(evil))
check("a run directory reached through a SYMLINK is refused",
      raises(blocking.JOURNAL_PATH_UNSAFE, lambda: J.read(evil)))
rd2 = tmp / "runs" / "r2"; J.write(rd2, rec)
os.chmod(rd2, 0o777)
check("a group/world-writable run directory is refused",
      raises(blocking.JOURNAL_PATH_UNSAFE, lambda: J.read(rd2)))
os.chmod(rd2, 0o700)
jp = rd2 / "journal.json"; jp.unlink(); os.symlink(str(rd / "journal.json"), str(jp))
check("a journal that is itself a SYMLINK is refused",
      raises(blocking.JOURNAL_PATH_UNSAFE, lambda: J.read(rd2)))

print("--- state machine: only the frozen arrows (M5-D8) ---")
rd3 = tmp / "runs" / "r3"; r = J.new_record(run_id="r3", contract_digest="sha256:"+"b"*64,
                                            run_policy_digest=RP.digest(pol), target_binding=binding)
J.write(rd3, r)
check("APPROVED -> TURN_ACTIVE is ILLEGAL (write-ahead cannot be skipped)",
      raises(blocking.JOURNAL_ILLEGAL_TRANSITION, lambda: J.transition(rd3, r, J.TURN_ACTIVE)))
r = J.transition(rd3, r, J.ARMED, note="armed")
check("APPROVED -> ARMED is legal", r["state"] == "ARMED")
check("the transition was persisted BEFORE returning", J.read(rd3)["state"] == "ARMED")
r = J.start_attempt(rd3, r, snapshot_file="s1.json")
r = J.transition(rd3, r, J.TURN_ACTIVE)
check("ARMED -> TURN_ACTIVE is legal", r["state"] == "TURN_ACTIVE")
for bad in (J.ARMED, J.COMPLETE, J.FAILED, J.RECONCILED):
    check(f"TURN_ACTIVE -> {bad} is ILLEGAL (no path skips the audit)",
          raises(blocking.JOURNAL_ILLEGAL_TRANSITION,
                 lambda b=bad: J.transition(rd3, r, b, terminal_reason=blocking.HERMES_TURN_FAILED)))
check("TURN_ACTIVE has an outstanding obligation", J.has_outstanding_obligation(r))
r = J.transition(rd3, r, J.RECONCILING)
r2 = J.transition(rd3, r, J.RECONCILED)
check("RECONCILING -> RECONCILED is legal", r2["state"] == "RECONCILED")
r3 = J.transition(rd3, r2, J.COMPLETE, terminal_reason=blocking.RECONCILIATION_MISMATCH)
check("a terminal transition records its reason code", r3["terminal"]["reason_code"] is not None)
check("TERMINAL IS TERMINAL: no transition out of COMPLETE",
      raises(blocking.RUN_ALREADY_TERMINAL, lambda: J.transition(rd3, r3, J.ARMED)))
check("terminal-state resurrection is refused even to another terminal",
      raises(blocking.RUN_ALREADY_TERMINAL,
             lambda: J.transition(rd3, r3, J.FAILED, terminal_reason=blocking.HERMES_TURN_FAILED)))
check("a terminal transition without a reason code is refused",
      raises(blocking.JOURNAL_MALFORMED, lambda: J.transition(rd3, r2, J.FAILED)))
check("a terminal transition with an UNREGISTERED reason code is refused",
      raises(blocking.JOURNAL_MALFORMED,
             lambda: J.transition(rd3, r2, J.FAILED, terminal_reason="made-up-code")))
check("a record claiming terminal while non-terminal is refused",
      raises(blocking.JOURNAL_MALFORMED, lambda: J.validate(
          {**r2, "terminal": {"outcome": "COMPLETE", "reason_code": "x", "detail": "", "at": "z"}})))
check("a terminal STATE with no terminal block is refused",
      raises(blocking.JOURNAL_MALFORMED, lambda: J.validate({**r2, "state": "COMPLETE"})))
check("transition does not mutate the caller's record", r2["state"] == "RECONCILED")

print("--- obligations ---")
check("a closed, reconciled attempt has no outstanding obligation",
      not J.has_outstanding_obligation(
          {**r2, "attempts": [{"attempt": 1, "state": "CLOSED", "reconciled": True}]}))
check("an OPEN unreconciled attempt IS an outstanding obligation",
      J.has_outstanding_obligation(
          {**r2, "attempts": [{"attempt": 1, "state": "OPEN", "reconciled": False}]}))
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
