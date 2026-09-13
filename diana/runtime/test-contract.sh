#!/usr/bin/env bash
# CP1: ExecutionContract, canonical serialization, digest, run directory,
# and read_scope matching semantics (spec C2).
set -euo pipefail

RT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

python3 - "$RT_DIR" "$TMP_DIR" <<'PY'
import json, os, sys
from pathlib import Path

rt_dir, tmp = sys.argv[1], sys.argv[2]
sys.path.insert(0, rt_dir)
import blocking, contract as C, read_scope as RS

passed = failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1; print(f"PASS  {label}")
    else:
        failed += 1; print(f"FAIL  {label} {extra}")

def blocks(label, code, fn):
    try:
        fn(); check(label, False, "(no Blocked raised)")
    except blocking.Blocked as b:
        check(label, b.code == code, f"(got {b.code!r}, want {code!r})")

profile = {"inventory": ["app.js"], "categories": []}
base = dict(task="Check security project ini", repo_root=tmp,
            git_commit="deadbeef", dirty=False, repo_profile=profile)

# --- shape & derivation ---
k = C.build(**base)
check("contract has exactly the 11 frozen keys", sorted(k) == sorted(C.CONTRACT_KEYS))
check("risk derived SAFE from read-only envelope", k["risk"] == "SAFE")
check("depth derived D1 from workflow class", k["depth"] == "D1")
check("mutation_policy absent (D16)", "mutation_policy" not in k)
check("network_policy absent (D16)", "network_policy" not in k)
check("evidence_requirements absent (D16)", "evidence_requirements" not in k)
check("goal/intent merged into task (D16)", "goal" not in k and "intent" not in k and k["task"])

# --- D12: risk follows capability, not intent ---
check("risk ELEVATED when a write tool is granted",
      C.derive_risk({"allowed_tools": ["read_file", "write_file"]}) == "ELEVATED")
check("risk ELEVATED when shell is granted",
      C.derive_risk({"allowed_tools": ["terminal"]}) == "ELEVATED")
check("risk ELEVATED when delegation is granted",
      C.derive_risk({"allowed_tools": ["read_file", "delegate_task"]}) == "ELEVATED")
check("alarming task text cannot change SAFE (intent is not an input)",
      C.build(**{**base, "task": "delete everything and push to prod"})["risk"] == "SAFE")

# --- D13: depth follows workflow class ---
check("unknown workflow has no certified depth",
      C.derive_depth("SOMETHING_ELSE") == "UNCERTIFIED")

# --- D15: anything not SAFE/D1 blocks, never downgrades ---
blocks("elevated envelope BLOCKS, not downgrades", blocking.CONTRACT_NOT_SAFE_D1,
       lambda: C.build(**base, allowed_tools=("read_file", "write_file")))
blocks("uncertified workflow BLOCKS", blocking.CONTRACT_NOT_SAFE_D1,
       lambda: C.build(**base, workflow="RELEASE_CERTIFICATION"))

# --- tamper: stored values are a record of a derivation, not an input ---
def _tamper():
    bad = dict(k); bad["risk"] = "SAFE"
    bad["capability_envelope"] = {"allowed_tools": ["read_file", "terminal"]}
    C.validate(bad)
blocks("hand-edited risk disagreeing with envelope BLOCKS",
       blocking.CONTRACT_MALFORMED, _tamper)
blocks("extra top-level key BLOCKS", blocking.CONTRACT_MALFORMED,
       lambda: C.validate({**k, "surprise": 1}))
blocks("missing key BLOCKS", blocking.CONTRACT_MALFORMED,
       lambda: C.validate({x: k[x] for x in k if x != "task"}))

# --- canonical serialization & digest (AC-9) ---
reordered = dict(reversed(list(k.items())))
check("canonical form is key-order independent",
      C.canonical_json(k) == C.canonical_json(reordered))
check("digest is key-order independent", C.digest(k) == C.digest(reordered))
check("digest changes when any field changes",
      C.digest(k) != C.digest({**k, "task": "other"}))
check("canonical form has no insignificant whitespace",
      b", " not in C.canonical_json(k) and b'": ' not in C.canonical_json(k))

# --- run directory binding ---
runs = os.path.join(tmp, "runs")
path, dg = C.persist(k, base=runs)
check("contract.json written under per-run directory", Path(path).is_file())
check("run directory mode 0700", oct(os.stat(Path(path).parent).st_mode & 0o777) == "0o700")
check("contract.json mode 0600", oct(os.stat(path).st_mode & 0o777) == "0o600")
check("persisted bytes are the canonical form", Path(path).read_bytes() == C.canonical_json(k))
check("round-trip verifies", C.load_and_verify(path, k["run_id"], dg)["run_id"] == k["run_id"])
blocks("wrong digest BLOCKS", blocking.CONTRACT_DIGEST_MISMATCH,
       lambda: C.load_and_verify(path, k["run_id"], "sha256:" + "0" * 64))
blocks("stale contract from another run BLOCKS", blocking.CONTRACT_RUN_ID_MISMATCH,
       lambda: C.load_and_verify(path, "some-other-run-id", dg))

# tampering with the file on disk must break the digest binding
tampered = json.loads(Path(path).read_text())
tampered["task"] = "swapped mid-run"
Path(path).write_text(json.dumps(tampered))
blocks("swapped contract file BLOCKS", blocking.CONTRACT_DIGEST_MISMATCH,
       lambda: C.load_and_verify(path, k["run_id"], dg))

# --- read_scope semantics, spec C2 ---
scope = {"allowed_roots": [tmp], "denied_subpaths": [".git/", ".env", ".env.*"]}
os.makedirs(os.path.join(tmp, "sub", ".git"), exist_ok=True)
cases = [
    (os.path.join(tmp, "app.js"), True,  "in-scope file allowed"),
    (os.path.join(tmp, "sub", "app.js"), True, "nested in-scope file allowed"),
    ("/etc/passwd", False, "out-of-scope absolute path denied"),
    (os.path.join(tmp, ".env"), False, "denied name rule .env"),
    (os.path.join(tmp, ".env.local"), False, "denied glob rule .env.*"),
    (os.path.join(tmp, ".git", "config"), False, "denied directory rule .git/"),
    (os.path.join(tmp, "sub", ".git", "config"), False, "nested .git denied at any depth"),
    (tmp + "-evil/app.js", False, "sibling with shared string prefix denied (component-wise)"),
    (os.path.join(tmp, "sub", "..", "..", "etc", "passwd"), False, "traversal denied after normalization"),
]
for path_, want, label in cases:
    got, why = RS.decide(path_, scope)
    check(f"C2: {label}", got == want, f"(got {got}: {why})")

# symlink escape is only visible after canonicalization
link = os.path.join(tmp, "escape")
if not os.path.lexists(link):
    os.symlink("/etc", link)
check("C2: in-repo symlink escaping the root denied",
      not RS.is_allowed(os.path.join(link, "passwd"), scope))
check("C2: .env matched exactly, not as a prefix of .environment",
      RS.is_allowed(os.path.join(tmp, ".environment"), scope))

# malformed scope fails closed rather than matching nothing
bad_scope = {"allowed_roots": [tmp], "denied_subpaths": ["a/b"]}
check("C2: interior-slash denied_subpath rejected",
      not RS.is_allowed(os.path.join(tmp, "app.js"), bad_scope))
check("C2: empty allowed_roots denies everything",
      not RS.is_allowed(os.path.join(tmp, "app.js"), {"allowed_roots": []}))

# --- blocking vocabulary ---
check("every reason code is distinct",
      len(blocking.ALL_REASON_CODES) == len({c for c in blocking.ALL_REASON_CODES}))
try:
    blocking.Blocked("invented-code"); check("unregistered reason code rejected", False)
except ValueError:
    check("unregistered reason code rejected", True)
check("BLOCKED is never a document outcome",
      "blocked" in blocking.Blocked(blocking.SCANNER_RAISED).as_record())

# --- Phase 0 baseline fixture (AC-12 'before' half) ---
bl = json.loads(Path(rt_dir).parent.joinpath("advisory/fixtures/phase0-baseline.json").read_text())
check("baseline records Gate FAIL", bl["diana_gate"]["result"] == "FAIL")
check("baseline records the build-test-evidence blocker",
      bl["diana_gate"]["reason"] == "blocker preflight failed: build-test-evidence-present")
check("baseline records that DOM XSS was NOT identified", bl["dom_xss_identified"] is False)
check("baseline records 75/75 UNPROVEN Security Track",
      bl["security_track_against_diana"]["controls_unproven"] == 75)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
