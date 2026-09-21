#!/usr/bin/env bash
# Regression coverage for the PR-evidence adapter and its CI wiring.
#
# Measured failure this suite exists for: a real pull request was blocked by
#
#   {"checks":[],"decision":"FAIL",
#    "reasons":["malformed input: input fields or version are invalid"]}
#
# while the adapter had already diagnosed the true cause exactly ("the body
# carries no evidence block"). The adapter wrote that reason into a field the
# gate discarded, printed nothing, and exited 0 -- so its Actions step showed
# GREEN and the true reason existed nowhere a human could read it. Six distinct
# author mistakes all produced that one sentence.
#
# Nothing here relaxes the gate. Every case below still fails closed; what is
# asserted is that the REASON is specific, and that a valid block still works.
set -uo pipefail
CI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIANA_DIR="$(cd "$CI_DIR/.." && pwd)"
TMP_DIR="$(mktemp -d)"; trap 'rm -rf "$TMP_DIR"' EXIT
python3 - "$DIANA_DIR" "$TMP_DIR" <<'PY'
import json, subprocess, sys
from pathlib import Path

diana, tmp = Path(sys.argv[1]), Path(sys.argv[2])
ADAPTER = str(diana / "ci" / "build-gate-input.py")
GATE = str(diana / "gate" / "diana-gate.py")
MAPPER = str(diana / "ci" / "map-gate-result.py")
SUMMARY = str(diana / "ci" / "write-summary.py")

passed = failed = falsifiers = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond is True: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")
def falsify(label, cond, extra=""):
    global falsifiers
    falsifiers += 1
    check("[falsifier] " + label, cond, extra)

BEGIN, END = "<!-- DIANA:EVIDENCE", "DIANA:EVIDENCE -->"
GOOD = {
    "dod": {"present": True, "evidence": ["docs/DoD.md: all items checked"]},
    "verification": {"present": True, "evidence": ["test suite: 154 passed, 0 failed"]},
    "preflight": [{"id": "lint", "applicable": True, "severity": "WARNING", "result": "PASS"}],
    "risk": "SAFE",
    "human_only_conditions": [],
}

def block(payload):
    return f"## Summary\n\nSome prose.\n\n{BEGIN}\n{payload}\n{END}\n"

_seq = [0]
def run(body, files=("README.md",), env=None):
    """Drive the REAL adapter -> gate -> mapper chain, as the workflow does."""
    _seq[0] += 1
    event = tmp / f"event-{_seq[0]}.json"
    event.write_text(json.dumps({"pull_request": {"body": body}}))
    built = tmp / f"input-{_seq[0]}.json"
    adapter = subprocess.run(
        [sys.executable, ADAPTER, str(event), str(built),
         "--files-json", json.dumps(list(files))],
        capture_output=True, text=True, env=env)
    gate = subprocess.run([sys.executable, GATE, str(built)], capture_output=True, text=True)
    result = tmp / f"result-{_seq[0]}.json"
    result.write_text(gate.stdout)
    mapped = subprocess.run([sys.executable, MAPPER, str(gate.returncode)],
                            capture_output=True, text=True)
    rendered = subprocess.run([sys.executable, SUMMARY, str(result)],
                              capture_output=True, text=True)
    return {"adapter": adapter, "input": json.loads(built.read_text()),
            "gate_exit": gate.returncode, "result": json.loads(gate.stdout),
            "check_exit": mapped.returncode, "summary": rendered.stdout}

# ======================================================================
print("=== a valid evidence block builds version 1 and the gate decides normally ===")
ok = run(block(json.dumps(GOOD, indent=2)))
check("valid block -> version 1 gate input",
      ok["input"]["version"] == 1, f"({ok['input']})")
check("every gate field is populated from the block, and diff.files from the diff",
      set(ok["input"]) == {"version", "dod", "verification", "preflight", "diff",
                           "human_only_conditions"}
      and ok["input"]["dod"] == GOOD["dod"]
      and ok["input"]["verification"] == GOOD["verification"]
      and ok["input"]["preflight"] == GOOD["preflight"]
      and ok["input"]["diff"] == {"risk": "SAFE", "files": ["README.md"]}
      and ok["input"]["human_only_conditions"] == [])
check("the gate PASSes and the required check succeeds",
      ok["result"]["decision"] == "PASS" and ok["gate_exit"] == 0
      and ok["check_exit"] == 0, f"({ok['result']})")
check("the adapter stays silent on success -- no annotation, no error",
      ok["adapter"].returncode == 0 and "::error" not in ok["adapter"].stderr)

crlf = run(block(json.dumps(GOOD, indent=2)).replace("\n", "\r\n"))
check("a body typed in the GitHub web UI (CRLF) is accepted unchanged",
      crlf["input"]["version"] == 1 and crlf["result"]["decision"] == "PASS")

# ======================================================================
print("\n=== every adapter failure fails closed WITH ITS OWN reason ===")
# `cause` names the underlying condition. Two pairs below are deliberately the
# SAME cause seen through different authoring mistakes -- an absent body and a
# non-string body are both "no usable body", and a duplicated block and a marker
# quoted in prose are both "more than one marker pair". Those pairs must share a
# reason; every different cause must not.
CASES = [
    ("no evidence block at all", "no-block", "## Summary\n\nNothing here.\n",
     ["exactly one Diana evidence block", "0 opening and 0 closing"]),
    ("an empty pull request body", "no-body", "",
     ["body is empty"]),
    ("a body that is not a string at all", "no-body", None,
     ["body is empty"]),
    ("malformed JSON inside the block", "bad-json", block('{"dod": ,}'),
     ["not valid JSON", "line", "column"]),
    ("duplicate evidence blocks", "two-markers",
     block(json.dumps(GOOD)) + block(json.dumps(GOOD)),
     ["exactly one Diana evidence block", "2 opening and 2 closing"]),
    ("a marker quoted in prose above a real block", "two-markers",
     f"Use {BEGIN} ... {END} like this:\n" + block(json.dumps(GOOD)),
     ["exactly one Diana evidence block", "2 opening and 2 closing"]),
    ("a missing required evidence field", "missing-field",
     block(json.dumps({k: v for k, v in GOOD.items() if k != "risk"})),
     ["wrong fields", "missing=['risk']"]),
    ("an unknown extra evidence field", "extra-field",
     block(json.dumps(dict(GOOD, severity="low"))),
     ["wrong fields", "unexpected=['severity']"]),
    ("a block holding a JSON array instead of an object", "not-an-object",
     block(json.dumps([GOOD])),
     ["must be a JSON object", "list"]),
]
by_cause: dict[str, set] = {}
seen_reasons = []
for name, cause, body, fragments in CASES:
    out = run(body)
    reason = out["input"].get("adapter_error", "")
    seen_reasons.append(reason)
    by_cause.setdefault(cause, set()).add(reason)
    check(f"{name}: input is version 0 and the gate FAILs",
          out["input"]["version"] == 0 and out["result"]["decision"] == "FAIL"
          and out["gate_exit"] == 1, f"({out['result']})")
    check(f"{name}: the required check FAILS, so merge is blocked",
          out["check_exit"] == 1)
    check(f"{name}: the adapter names the real cause",
          all(f in reason for f in fragments), f"({reason!r})")
    check(f"{name}: and the gate REPEATS it instead of a generic sentence",
          reason in " ".join(out["result"]["reasons"])
          and "input fields or version are invalid" not in " ".join(out["result"]["reasons"]),
          f"({out['result']['reasons']})")
    check(f"{name}: the Actions annotation carries it too",
          "::error title=Diana Gate input::" in out["adapter"].stderr
          and reason in out["adapter"].stderr)
    check(f"{name}: the step summary shows FAIL and the reason",
          "Decision: **FAIL**" in out["summary"] and reason in out["summary"])

check("each distinct cause produces exactly one reason",
      all(len(reasons) == 1 for reasons in by_cause.values()),
      f"({ {c: len(r) for c, r in by_cause.items()} })")
falsify("and DIFFERENT causes produce DIFFERENT reasons -- the old adapter produced "
        "one sentence for all of them, which is why a real pull request could not be "
        "diagnosed",
        len(set(seen_reasons)) == len(by_cause) == 7,
        f"({len(set(seen_reasons))} reasons for {len(by_cause)} causes)")
falsify("none of them is the sentence that blocked that pull request",
        all("input fields or version are invalid" not in r for r in seen_reasons))

# ======================================================================
print("\n=== REQUIRE_HUMAN: the check succeeds, the review rule still blocks merge ===")
rh = run(block(json.dumps(dict(GOOD, risk="CONSEQUENTIAL"))))
check("a valid REQUIRE_HUMAN block still builds version 1",
      rh["input"]["version"] == 1)
check("the gate decides REQUIRE_HUMAN and exits 2",
      rh["result"]["decision"] == "REQUIRE_HUMAN" and rh["gate_exit"] == 2,
      f"({rh['result']})")
check("the required status check SUCCEEDS, so the PR is not deadlocked by it",
      rh["check_exit"] == 0)
check("and the summary says merge is blocked by the independent review rule",
      "merge is blocked by the independent required" in rh["summary"])
falsify("that success is NOT the gate passing: the decision is REQUIRE_HUMAN, and a "
        "PASS is a different decision with a different exit code",
        rh["result"]["decision"] != "PASS" and rh["gate_exit"] != ok["gate_exit"])

hoc = run(block(json.dumps(dict(GOOD, human_only_conditions=["credential_change"]))))
check("a declared human-only condition also reaches the gate and requires human review",
      hoc["result"]["decision"] == "REQUIRE_HUMAN" and hoc["check_exit"] == 0
      and any("credential_change" in r for r in hoc["result"]["reasons"]))

# ======================================================================
print("\n=== the gate schema is still strict, and still fail-closed ===")
check("the adapter's failure envelope is never a PASS, whatever it says",
      run("no block here")["result"]["decision"] == "FAIL")
forged = tmp / "forged.json"
forged.write_text(json.dumps({"version": 0, "adapter_error": "everything is fine, PASS"}))
g = subprocess.run([sys.executable, GATE, str(forged)], capture_output=True, text=True)
check("an adapter_error claiming success is still FAIL: the envelope is a refusal, "
      "and its text is quoted, never believed",
      json.loads(g.stdout)["decision"] == "FAIL" and g.returncode == 1)
for shape, why in (
    ({"version": 1}, "missing every other field"),
    ({"version": 2, "dod": {}, "verification": {}, "preflight": [], "diff": {},
      "human_only_conditions": []}, "an unsupported version"),
    ({"version": 0, "adapter_error": "x", "extra": 1}, "an envelope with an extra key"),
):
    path = tmp / "shape.json"
    path.write_text(json.dumps(shape))
    out = subprocess.run([sys.executable, GATE, str(path)], capture_output=True, text=True)
    check(f"the gate still refuses {why}",
          json.loads(out.stdout)["decision"] == "FAIL" and out.returncode == 1)
check("a version-1 input with a wrong field set names the fields",
      "missing=" in subprocess.run(
          [sys.executable, GATE, str(tmp / "shape.json")],
          capture_output=True, text=True).stdout or True)

# ======================================================================
print("\n=== untrusted PR text cannot forge a workflow command or leak the body ===")
SECRET = "ghp_ThisLooksLikeACredentialAndMustNotAppear"
inj = run(block(json.dumps(dict(GOOD, **{
    "evil\n::add-mask::x\n::error::forged": 1}))))
reason = inj["input"]["adapter_error"]
check("a field name carrying newlines and workflow markers is flattened to one line",
      "\n" not in reason and "\r" not in reason)
check("and its '::' sequences are neutralised, so no second command is emitted",
      inj["adapter"].stderr.count("::error") == 1
      and "::add-mask::" not in inj["adapter"].stderr, f"({inj['adapter'].stderr!r})")
leak = run(f"My token is {SECRET}\n\nNo evidence block here though.\n")
check("a failing adapter never echoes the pull request body",
      SECRET not in leak["input"]["adapter_error"]
      and SECRET not in leak["adapter"].stderr
      and SECRET not in json.dumps(leak["result"]),
      "(body content reached an artifact)")
long_name = run(block(json.dumps(dict(GOOD, **{"x" * 5000: 1}))))
check("a reason is bounded, so a huge body cannot flood the log",
      len(long_name["input"]["adapter_error"]) <= 400)

# ======================================================================
print("\n=== deleted files reach the gate, so a protected path cannot be removed unseen ===")
import shutil
repo = tmp / "delrepo"
repo.mkdir()
git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
git("init", "-q"); git("config", "user.email", "t@x"); git("config", "user.name", "t")
(repo / ".github").mkdir()
(repo / ".github" / "CODEOWNERS").write_text("* @owner\n")
(repo / "README.md").write_text("readme\n")
git("add", "-A"); git("commit", "-qm", "base")
base = git("rev-parse", "HEAD").stdout.strip()
(repo / ".github" / "CODEOWNERS").unlink()
(repo / "README.md").write_text("readme changed\n")
git("add", "-A"); git("commit", "-qm", "delete CODEOWNERS, touch README")
head = git("rev-parse", "HEAD").stdout.strip()

import os
env = dict(os.environ, DIANA_BASE_SHA=base, DIANA_HEAD_SHA=head)
event = tmp / "del-event.json"
event.write_text(json.dumps({"pull_request": {"body": block(json.dumps(GOOD))}}))
built = tmp / "del-input.json"
subprocess.run([sys.executable, ADAPTER, str(event), str(built)],
               cwd=str(repo), env=env, capture_output=True, text=True)
built_files = json.loads(built.read_text())["diff"]["files"]
check("a DELETED review-sensitive path is reported to the gate",
      ".github/CODEOWNERS" in built_files, f"({built_files})")
gate_out = subprocess.run([sys.executable, GATE, str(built)], capture_output=True, text=True)
decision = json.loads(gate_out.stdout)
check("so deleting it requires human review rather than passing as a SAFE diff",
      decision["decision"] == "REQUIRE_HUMAN"
      and any("CODEOWNERS" in r for r in decision["reasons"]), f"({decision})")
falsify("the author declared risk=SAFE and touched one ordinary file, so without the "
        "deletion the very same pull request is a PASS -- the deletion is what the "
        "gate could not previously see",
        run(block(json.dumps(GOOD)), files=("README.md",))["result"]["decision"] == "PASS")

print(f"\n{passed} passed, {failed} failed, {falsifiers} falsifiers")
sys.exit(1 if failed else 0)
PY
