#!/usr/bin/env bash
# CP9: the frozen M1 acceptance suite -- AC-3, AC-4, AC-5, AC-6, AC-12, AC-13.
# The detection and integrity criteria (AC-1,2,7,8,9,10,11) are owned by the
# checkpoint suites; this script proves the end-to-end system properties.
set -euo pipefail

ADV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$ADV_DIR/../.." && pwd)"
HERMES_HOME="${DIANA_HERMES_HOME:-$HOME/.hermes/hermes-agent}"
PY_BIN="python3"
[ -x "$HERMES_HOME/venv/bin/python3" ] && PY_BIN="$HERMES_HOME/venv/bin/python3"
if [ ! -d "$HERMES_HOME" ]; then
  echo "SKIP  Hermes not installed at $HERMES_HOME"; exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
export HERMES_SAFE_MODE=1

"$PY_BIN" - "$ADV_DIR" "$TMP_DIR" "$HERMES_HOME" "$REPO_ROOT" <<'PY'
import hashlib, importlib.util, json, os, subprocess, sys
from pathlib import Path

adv_dir, tmp, hermes_home, repo_root = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
os.environ["DIANA_HERMES_HOME"] = hermes_home
diana = Path(adv_dir).parent
for sub in ("advisory", "runtime", "profile", "adapters", "security"):
    sys.path.insert(0, str(diana / sub))
import blocking, run as R

passed = failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")

CONFIG = {"auxiliary": {"background_review": {"enabled": False}, "side_question": {"enabled": False}},
          "approvals": {"mode": "manual"}}
ENV = {"HERMES_SAFE_MODE": "1"}
FIXTURE = str(Path(adv_dir, "fixtures", "repo", "fx01"))

# ---------- instrumentation ----------
# AC-5: spies at the ACTUAL call sites. Non-execution must be observed, never
# inferred from the absence of resulting files.
calls = {"gate": [], "ship": [], "bundle": [], "evidence": [], "reducer": []}

def spy(module, name, bucket):
    original = getattr(module, name)
    def watched(*a, **k):
        calls[bucket].append(name)
        return original(*a, **k)
    setattr(module, name, watched)

def load_hyphenated(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

gate_mod = load_hyphenated(str(diana / "gate" / "diana-gate.py"), "diana_gate_spy")
ship_mod = load_hyphenated(str(diana / "ship" / "ship.py"), "diana_ship_spy")
import security_bundle, security_reducer, evidence_model

for fn in ("evaluate", "main"):
    spy(gate_mod, fn, "gate")
for fn in ("cmd_gate", "cmd_open_pr", "cmd_precheck", "cmd_integrate", "cmd_review_verdict", "main"):
    spy(ship_mod, fn, "ship")
for fn in ("build_bundle", "validate_bundle", "main"):
    spy(security_bundle, fn, "bundle")
for fn in ("evaluate", "load_controls"):
    spy(evidence_model, fn, "evidence")
for fn in ("reduce_bundle", "evaluate"):
    spy(security_reducer, fn, "reducer")

# AC-6: observe every subprocess this process spawns.
spawned = []
real_run, real_popen = subprocess.run, subprocess.Popen
def watched_run(cmd, *a, **k):
    spawned.append(cmd); return real_run(cmd, *a, **k)
def watched_popen(cmd, *a, **k):
    spawned.append(cmd); return real_popen(cmd, *a, **k)
subprocess.run, subprocess.Popen = watched_run, watched_popen

# ---------- AC-3 preconditions: snapshot the target repository ----------
def tree_hashes(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            p = Path(dirpath, name)
            try:
                out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError as exc:
                out[str(p.relative_to(root))] = f"unreadable:{exc.errno}"
    return out

def git_porcelain(root):
    r = real_run(["git", "-C", root, "status", "--porcelain", "--", "."],
                 capture_output=True, text=True, check=False)
    return r.stdout

before_hashes = tree_hashes(FIXTURE)
before_status = git_porcelain(FIXTURE)
before_listing = sorted(str(p.relative_to(FIXTURE)) for p in Path(FIXTURE).rglob("*"))

# ---------- the run ----------
out = R.execute(task="Check security project ini", repo_root=FIXTURE,
                runs_base=os.path.join(tmp, "runs"), env=ENV, config=CONFIG,
                hermes_home=hermes_home)
doc = out["document"]

after_hashes = tree_hashes(FIXTURE)
after_status = git_porcelain(FIXTURE)
after_listing = sorted(str(p.relative_to(FIXTURE)) for p in Path(FIXTURE).rglob("*"))

# ---------- AC-3: repository immutability ----------
check("AC-3 every file hash in the target is byte-identical after the run",
      before_hashes == after_hashes,
      f"(changed: {sorted(set(before_hashes.items()) ^ set(after_hashes.items()))[:3]})")
check("AC-3 no file was added or removed in the target", before_listing == after_listing)
check("AC-3 git working state is unchanged", before_status == after_status)
check("AC-3 the run created nothing in the target repository",
      not Path(FIXTURE, "diana-should-never-exist.txt").exists())

# ---------- AC-4: artifact persisted outside the target repository ----------
artifact_path = Path(out["artifact_path"]).resolve()
check("AC-4 the artifact exists", artifact_path.is_file())
check("AC-4 the artifact is outside the target repository",
      Path(FIXTURE).resolve() not in artifact_path.parents)
check("AC-4 the artifact is under the Diana per-run directory",
      artifact_path.parent.name == out["run_id"])
check("AC-4 the contract is stored beside it, outside the target",
      Path(FIXTURE).resolve() not in Path(out["contract_path"]).resolve().parents)

# ---------- AC-5: no Gate, no Security Track, no PR ----------
check("AC-5 no Diana Gate function executed", calls["gate"] == [], f"(called {calls['gate']})")
check("AC-5 no ship.py function executed", calls["ship"] == [], f"(called {calls['ship']})")
check("AC-5 no Security Track bundle was constructed", calls["bundle"] == [], f"(called {calls['bundle']})")
check("AC-5 no evidence_model entry point was invoked", calls["evidence"] == [], f"(called {calls['evidence']})")
check("AC-5 no Security Track reducer ran", calls["reducer"] == [], f"(called {calls['reducer']})")
check("AC-5 no PR was created (cmd_open_pr never called)", "cmd_open_pr" not in calls["ship"])
check("AC-5 the spies are wired to real call sites, not inferred",
      callable(getattr(gate_mod, "evaluate")) and callable(getattr(ship_mod, "cmd_open_pr")))
# Prove the spies would catch a call -- otherwise "zero invocations" is unfalsifiable.
gate_mod.evaluate({})if False else None
try:
    gate_mod.evaluate({"foo": 1})
except Exception:
    pass
check("AC-5 the spy mechanism is falsifiable (a deliberate call is recorded)",
      calls["gate"] == ["evaluate"])
calls["gate"].clear()

# ---------- AC-6: no git/gh mutation subprocess ----------
MUTATING = ("commit", "push", "merge", "rebase", "reset", "checkout", "tag", "am",
            "cherry-pick", "revert", "clean", "stash", "apply", "restore")
git_calls = [c for c in spawned if isinstance(c, (list, tuple)) and c and str(c[0]).endswith("git")]
gh_calls = [c for c in spawned if isinstance(c, (list, tuple)) and c and str(c[0]).endswith("gh")]
mutating = [c for c in git_calls if any(str(a) in MUTATING for a in c)]
check("AC-6 no gh subprocess was spawned at all", gh_calls == [], f"(spawned {gh_calls})")
check("AC-6 no mutating git subprocess was spawned", mutating == [], f"(spawned {mutating})")
check("AC-6 git was used read-only only (rev-parse / status)",
      all(any(str(a) in ("rev-parse", "status") for a in c) for c in git_calls),
      f"(spawned {git_calls})")

# ---------- AC-12: the measured baseline delta ----------
baseline = json.loads(Path(adv_dir, "fixtures", "phase0-baseline.json").read_text())
check("AC-12 baseline: Diana Gate FAILed on this fixture",
      baseline["diana_gate"]["result"] == "FAIL")
check("AC-12 baseline: the reason was the missing build/test evidence blocker",
      baseline["diana_gate"]["reason"] == "blocker preflight failed: build-test-evidence-present")
check("AC-12 baseline: the DOM XSS was NOT identified", baseline["dom_xss_identified"] is False)
check("AC-12 baseline fixture is the one under test",
      baseline["fixture"].endswith("fx01") and Path(FIXTURE).name == "fx01")
check("AC-12 M1: the DOM XSS IS identified",
      [f["rule_id"] for f in doc["findings"]] == ["DOM-XSS-001"])
check("AC-12 M1: severity HIGH at the exact expected sink line",
      doc["findings"][0]["severity"] == "HIGH"
      and doc["findings"][0]["file"] == baseline["planted_vulnerability"]["file"]
      and doc["findings"][0]["line"] == 7)
check("AC-12 M1: the review completed instead of failing on missing tests",
      doc["outcome"] == "COMPLETE")
check("AC-12 M1: no Gate, no Security Track, no PR on the advisory path",
      calls["gate"] == [] and calls["ship"] == [] and calls["bundle"] == [] and calls["evidence"] == [])
check("AC-12 M1: the run is SAFE/D1",
      doc["contract"]["risk"] == "SAFE" and doc["contract"]["depth"] == "D1")

# ---------- AC-8: determinism of the whole artifact ----------
# Two complete artifacts CANNOT be byte-identical: contract_digest covers
# run_id and created_at, so it MUST differ. Exempting it silently would hide a
# broken digest, so the deterministic analysis sections are compared directly
# and digest validity is asserted separately for each run.
import contract as _C
run_a = R.execute(task="Check security project ini", repo_root=FIXTURE,
                  runs_base=os.path.join(tmp, "det-a"), env=ENV, config=CONFIG,
                  hermes_home=hermes_home, turn_driver=R.null_turn_driver)["document"]
run_b = R.execute(task="Check security project ini", repo_root=FIXTURE,
                  runs_base=os.path.join(tmp, "det-b"), env=ENV, config=CONFIG,
                  hermes_home=hermes_home, turn_driver=R.null_turn_driver)["document"]

DETERMINISTIC = ("findings", "suppressed", "coverage", "unsupported_constructs",
                 "scan_issues", "limitations")
for section in DETERMINISTIC:
    check(f"AC-8 {section} is byte-identical across runs",
          json.dumps(run_a[section], sort_keys=True) == json.dumps(run_b[section], sort_keys=True))
check("AC-8 the deterministic repo_profile content is byte-identical across runs",
      json.dumps(run_a["contract"]["repo_profile"], sort_keys=True)
      == json.dumps(run_b["contract"]["repo_profile"], sort_keys=True))
check("AC-8 run_id differs between runs", run_a["run_id"] != run_b["run_id"])
check("AC-8 contract_digest DIFFERS between runs (it covers run_id/created_at)",
      run_a["contract_digest"] != run_b["contract_digest"])
check("AC-8 contract_digest validly recomputes for run A",
      run_a["contract_digest"] == _C.digest(run_a["contract"]))
check("AC-8 contract_digest validly recomputes for run B",
      run_b["contract_digest"] == _C.digest(run_b["contract"]))
norm_a = dict(run_a); norm_b = dict(run_b)
for d in (norm_a, norm_b):
    d.pop("run_id"); d.pop("contract_digest")
    d["contract"] = {k: v for k, v in d["contract"].items() if k not in ("run_id", "created_at")}
check("AC-8 normalized documents are byte-identical (the equivalent form)",
      json.dumps(norm_a, sort_keys=True) == json.dumps(norm_b, sort_keys=True))

# ---------- AC-13: AO path and regression scope ----------
# HISTORICAL ASSERTION. This measures what the M1 implementation itself did,
# between the M1 freeze commit and the M1 completion commit. It is evidence
# about M1 and stays true forever; it is deliberately NOT evaluated against
# HEAD, because "nothing changed since the M1 freeze" is not a perpetual
# repository invariant and must never be reinterpreted as one. Later milestones
# carry their own regression invariants.
FREEZE = "2b26f7bfcaa41b3b068635bb207ca807b2967964"
M1_COMPLETION = "3c9a485"  # the M1 post-audit fix: the last commit of M1 itself
status = real_run(["git", "-C", repo_root, "diff", "--name-status", FREEZE, M1_COMPLETION],
                  capture_output=True, text=True, check=False).stdout.splitlines()
entries = [line.split("\t", 1) for line in status if "\t" in line]
modified = sorted(path for code, path in entries if code.startswith("M"))
added = sorted(path for code, path in entries if code.startswith("A"))
deleted = sorted(path for code, path in entries if code.startswith("D"))

# The invariant is about MODIFICATION, not path spelling: M1 could add files
# anywhere it owned, but must not have changed code that already worked.
ALLOWED_MODIFIED = [".gitignore", "docs/architecture/HERMES-RUNTIME-M1.md"]
check("AC-13 (historical) M1 modified only .gitignore and its own spec",
      modified == ALLOWED_MODIFIED, f"(modified: {modified})")
check("AC-13 (historical) M1 deleted nothing", deleted == [], f"(deleted: {deleted})")
check("AC-13 (historical) M1 added files under its own modules only",
      all(a.startswith(("diana/runtime/", "diana/profile/", "diana/advisory/",
                        "diana/adapters/")) for a in added),
      f"(stray additions: {[a for a in added if not a.startswith(('diana/runtime/','diana/profile/','diana/advisory/','diana/adapters/'))]})")
check("AC-13 (historical) the AO adapter source is untouched by M1",
      "diana/adapters/ao.py" not in modified)
check("AC-13 (historical) the AO adapter test is untouched by M1",
      "diana/adapters/test-ao-adapter.sh" not in modified)
for pre_existing in ("diana/gate/", "diana/ship/", "diana/security/", "diana/preflight/",
                     "diana/ci/", "diana/playwright/", "diana/hooks/", "diana/skills/"):
    check(f"AC-13 (historical) pre-existing module untouched by M1: {pre_existing}",
          not any(m.startswith(pre_existing) for m in modified))
check("AC-13 (historical) M1 modified no pre-existing Diana Python module",
      not any(m.endswith(".py") for m in modified))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
