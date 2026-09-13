#!/usr/bin/env bash
# CP8: run wiring, natural-language routing, closed Hermes output schema,
# unverified_observations, and blocked-run semantics.
set -euo pipefail

ADV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${DIANA_HERMES_HOME:-$HOME/.hermes/hermes-agent}"
PY_BIN="python3"
[ -x "$HERMES_HOME/venv/bin/python3" ] && PY_BIN="$HERMES_HOME/venv/bin/python3"
if [ ! -d "$HERMES_HOME" ]; then
  echo "SKIP  Hermes not installed at $HERMES_HOME"; exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
export HERMES_SAFE_MODE=1

"$PY_BIN" - "$ADV_DIR" "$TMP_DIR" "$HERMES_HOME" <<'PY'
import json, os, sys
from pathlib import Path

adv_dir, tmp, hermes_home = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ["DIANA_HERMES_HOME"] = hermes_home
sys.path.insert(0, adv_dir)
sys.path.insert(0, str(Path(adv_dir).parent / "runtime"))
import blocking, run as R

passed = failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")

def blocks(label, code, fn):
    try:
        fn(); check(label, False, "(no Blocked raised)")
    except blocking.Blocked as b:
        check(label, b.code == code, f"(got {b.code!r}, want {code!r})")

CONFIG = {"auxiliary": {"background_review": {"enabled": False}, "side_question": {"enabled": False}},
          "approvals": {"mode": "manual"}}
ENV = {"HERMES_SAFE_MODE": "1"}
FIX = lambda fid: str(Path(adv_dir, "fixtures", "repo", fid))

def go(fid="fx01", task="Check security project ini", driver=R.scripted_turn_driver, base=None):
    return R.execute(task=task, repo_root=FIX(fid), runs_base=base or os.path.join(tmp, "runs"),
                     env=ENV, config=CONFIG, hermes_home=hermes_home, turn_driver=driver)

# ===== routing: no slash command =====
check("the literal M1 request routes", R.route("Check security project ini") == "ADVISORY_SECURITY_REVIEW")
for phrasing in ("check the security of this project", "review this repo for vulnerabilities",
                 "audit the project security", "can you inspect this for XSS?"):
    check(f"natural phrasing routes: {phrasing!r}", R.route(phrasing) == "ADVISORY_SECURITY_REVIEW")
for nope in ("check the tests", "what does this project do?", "security", "", None, 42):
    check(f"non-review request does not route: {nope!r}", R.route(nope) is None)
for mutating in ("fix the security bug", "patch the XSS vulnerability", "review and fix security issues"):
    check(f"a mutating request refuses to route as read-only review: {mutating!r}",
          R.route(mutating) is None)
blocks("an unroutable request BLOCKS rather than guessing a workflow",
       blocking.CONTRACT_NOT_SAFE_D1, lambda: go(task="what does this project do?"))

# ===== end to end on the baseline fixture =====
out = go()
doc = out["document"]
check("end to end produces an ADVISORY_SECURITY_REVIEW", doc["document_type"] == "ADVISORY_SECURITY_REVIEW")
check("the contract is SAFE", doc["contract"]["risk"] == "SAFE")
check("the contract is D1", doc["contract"]["depth"] == "D1")
check("the workflow is the certified advisory class", doc["contract"]["workflow"] == "ADVISORY_SECURITY_REVIEW")
check("the envelope is exactly read_file and search_files",
      doc["contract"]["capability_envelope"]["allowed_tools"] == ["read_file", "search_files"])
check("the planted DOM XSS is identified",
      [f["rule_id"] for f in doc["findings"]] == ["DOM-XSS-001"])
check("the finding is HIGH at the exact sink line",
      doc["findings"][0]["severity"] == "HIGH" and doc["findings"][0]["line"] == 7
      and doc["findings"][0]["file"] == "app.js")
check("the finding cites a source and a sink",
      doc["findings"][0]["source"]["kind"] == "location.hash"
      and doc["findings"][0]["sink"]["kind"] == "innerHTML")
check("outcome is COMPLETE for the baseline fixture", doc["outcome"] == "COMPLETE")
check("the repository profile is embedded in the contract",
      "app.js" in doc["contract"]["repo_profile"]["inventory"])
check("irrelevant areas are explicitly skipped with reasons",
      any(c["status"] == "NOT_CHECKED" and c.get("reason") for c in doc["coverage"]))
check("limitations are stated", len(doc["limitations"]) >= 4)

# ===== run binding =====
check("contract persisted under the per-run directory",
      Path(out["contract_path"]).is_file()
      and Path(out["contract_path"]).parent.name == out["run_id"])
check("DIANA_RUN_ID is exported for the run", os.environ["DIANA_RUN_ID"] == out["run_id"])
check("DIANA_CONTRACT_DIGEST is exported for the run",
      os.environ["DIANA_CONTRACT_DIGEST"] == out["contract_digest"])
check("the artifact lives outside the target repository",
      not out["artifact_path"].startswith(FIX("fx01")))

# ===== Hermes is the substrate, not the detector (D1) =====
no_hermes = go(driver=R.null_turn_driver)
check("findings are identical with NO Hermes turn at all",
      json.dumps(no_hermes["document"]["findings"], sort_keys=True)
      == json.dumps(doc["findings"], sort_keys=True))
check("a Hermes turn contributes only ungraded observations",
      len(doc["unverified_observations"]) >= 1
      and no_hermes["document"]["unverified_observations"] == [])
check("observations carry no severity",
      all("severity" not in o for o in doc["unverified_observations"]))
check("observations reference a file but never claim a finding",
      all(set(o) <= {"note", "file"} for o in doc["unverified_observations"]))

# ===== the scripted turn actually exercised the real execution path =====
check("the run proved a non-envelope tool is refused from inside the turn",
      not Path(FIX("fx01"), "diana-should-never-exist.txt").exists())

# ===== closed Hermes output schema (D14) =====
for bad, why in (
    ([{"note": "x", "severity": "HIGH"}], "severity"),
    ([{"note": "x", "risk": "SAFE"}], "risk"),
    ([{"note": "x", "depth": "D0"}], "depth"),
    ([{"note": "x", "confidence": 0.9}], "an unknown field"),
    (["just a string"], "a non-object"),
    ([{"file": "a.js"}], "a missing note"),
):
    blocks(f"a Hermes turn returning {why} BLOCKS the run",
           blocking.HERMES_OUTPUT_SCHEMA_VIOLATION,
           lambda b=bad: go(driver=lambda c, p=None, v=b: v))

# ===== failure semantics (D37) =====
runs_b = os.path.join(tmp, "blockedruns")
try:
    go(task="what is this?", base=runs_b)
except blocking.Blocked as exc:
    path = R.record_blocked(exc, runs_base=runs_b, run_id="blocked-run")
    check("a blocked run writes a run record", Path(path).is_file())
    rec = json.loads(Path(path).read_text())
    check("the run record is not an advisory artifact",
          rec.get("blocked") is True and "document_type" not in rec)
    check("the run record carries the specific reason code", rec["reason_code"] in blocking.ALL_REASON_CODES)
check("a blocked run produced NO advisory artifact anywhere",
      not list(Path(runs_b).rglob("advisory-security-review.json")))

blocks("a nonexistent target BLOCKS at profiling", blocking.REPO_PROFILE_FAILED,
       lambda: R.execute(task="Check security project ini", repo_root=os.path.join(tmp, "nope"),
                         runs_base=os.path.join(tmp, "runs"), env=ENV, config=CONFIG,
                         hermes_home=hermes_home, turn_driver=R.null_turn_driver))

import dom_scan
real_scan = dom_scan.scan
def boom(*a, **k): raise RuntimeError("synthetic scanner failure")
dom_scan.scan = boom
try:
    blocks("a scanner exception BLOCKS rather than emitting a partial artifact",
           blocking.SCANNER_RAISED, lambda: go(driver=R.null_turn_driver))
finally:
    dom_scan.scan = real_scan

# a preflight failure must stop the run before any artifact exists
blocks("a preflight failure BLOCKS the whole run", blocking.SAFE_MODE_NOT_ENABLED,
       lambda: R.execute(task="Check security project ini", repo_root=FIX("fx01"),
                         runs_base=os.path.join(tmp, "pfruns"), env={"HERMES_SAFE_MODE": "0"},
                         config=CONFIG, hermes_home=hermes_home, turn_driver=R.null_turn_driver))
check("no artifact was written for the preflight-blocked run",
      not list(Path(tmp, "pfruns").rglob("advisory-security-review.json"))
      if Path(tmp, "pfruns").exists() else True)

# ===== fixture 8 end to end: INCOMPLETE, not a silent pass =====
eight = go(fid="fx08", driver=R.null_turn_driver)
check("fx08 end to end is INCOMPLETE", eight["document"]["outcome"] == "INCOMPLETE")
check("fx08 records the template-literal construct with a location",
      eight["document"]["unsupported_constructs"][0]["construct"] == "template_literal")
check("fx08 reports no finding", eight["document"]["findings"] == [])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
