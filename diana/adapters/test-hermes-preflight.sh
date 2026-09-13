#!/usr/bin/env bash
# CP7: the fail-closed Hermes preflight and the 8-case preflight-failure matrix.
set -euo pipefail

AD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${DIANA_HERMES_HOME:-$HOME/.hermes/hermes-agent}"
PY_BIN="python3"
[ -x "$HERMES_HOME/venv/bin/python3" ] && PY_BIN="$HERMES_HOME/venv/bin/python3"
if [ ! -d "$HERMES_HOME" ]; then
  echo "SKIP  Hermes not installed at $HERMES_HOME"; exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
export HERMES_SAFE_MODE=1

"$PY_BIN" - "$AD_DIR" "$TMP_DIR" "$HERMES_HOME" <<'PY'
import json, os, sys
from pathlib import Path

ad_dir, tmp, hermes_home = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ["DIANA_HERMES_HOME"] = hermes_home
sys.path.insert(0, ad_dir)
sys.path.insert(0, str(Path(ad_dir).parent / "runtime"))
import blocking, hermes as H, hermes_patches as HP, selftest as ST

passed = failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")

repo = Path(tmp, "repo"); repo.mkdir(parents=True, exist_ok=True)
(repo / "app.js").write_text("var a = 1;\n")
GOOD_ENV = {"HERMES_SAFE_MODE": "1"}
GOOD_CONFIG = {
    "auxiliary": {"background_review": {"enabled": False}, "side_question": {"enabled": False}},
    "approvals": {"mode": "manual"},
}

def run(env=None, config=None, repo_root=None, require_patches=False, home=None):
    return H.check(repo_root=str(repo_root or repo),
                   env=GOOD_ENV if env is None else env,
                   config=GOOD_CONFIG if config is None else config,
                   hermes_home=home or hermes_home,
                   require_patches=require_patches)

def blocks(label, code, fn):
    try:
        fn(); check(label, False, "(no Blocked raised)")
    except blocking.Blocked as b:
        check(label, b.code == code, f"(got {b.code!r}, want {code!r})")

# --- the clean configuration passes (AC-1 second half) ---
report = run()
check("a clean configuration passes preflight", isinstance(report, dict))
check("every control reports PASS", all(c["status"] == "PASS" for c in report["controls"]))
check("preflight records approvals.mode as context, not a control",
      report["recorded_context"]["approvals.mode"] == "manual"
      and not any("approval" in c["control"] for c in report["controls"]))

# --- the 8 frozen failure cases, each with ITS OWN reason code ---
matrix = json.loads(Path(ad_dir, "fixtures", "hermes-config", "preflight-failures.json").read_text())
check("the frozen matrix has exactly 8 cases", len(matrix["cases"]) == 8)
codes = [c["reason_code"] for c in matrix["cases"]]
check("all 8 reason codes are distinct", len(set(codes)) == 8)
check("every matrix reason code is a registered code",
      all(c in blocking.ALL_REASON_CODES for c in codes))

by_id = {c["id"]: c["reason_code"] for c in matrix["cases"]}

blocks("1. HERMES_SAFE_MODE != 1", by_id["safe-mode-off"],
       lambda: run(env={"HERMES_SAFE_MODE": "0"}))
blocks("1b. HERMES_SAFE_MODE unset also blocks (the original list had this inverted)",
       by_id["safe-mode-off"], lambda: run(env={}))

blocks("2. background_review.enabled == true", by_id["background-review-on"],
       lambda: run(config={**GOOD_CONFIG, "auxiliary": {**GOOD_CONFIG["auxiliary"],
                                                        "background_review": {"enabled": True}}}))
blocks("2b. an ABSENT background_review block blocks, because its default is enabled",
       by_id["background-review-on"],
       lambda: run(config={"auxiliary": {"side_question": {"enabled": False}}}))

blocks("3. side-question auxiliary execution enabled", by_id["side-question-on"],
       lambda: run(config={**GOOD_CONFIG, "auxiliary": {**GOOD_CONFIG["auxiliary"],
                                                        "side_question": {"enabled": True}}}))

poisoned = Path(tmp, "poisoned"); poisoned.mkdir(exist_ok=True)
(poisoned / ".hermes.md").write_text("# override instructions\n")
blocks("4. .hermes.md present", by_id["hermes-md-present"], lambda: run(repo_root=poisoned))

poisoned2 = Path(tmp, "poisoned2"); poisoned2.mkdir(exist_ok=True)
(poisoned2 / "AGENTS.override.md").write_text("# override instructions\n")
blocks("5. AGENTS.override.md present", by_id["agents-override-present"],
       lambda: run(repo_root=poisoned2))

fake_home = Path(tmp, "fake-hermes"); (fake_home / "hermes_cli").mkdir(parents=True, exist_ok=True)
(fake_home / "model_tools.py").write_text("# stub\n")
(fake_home / "hermes_cli" / "__init__.py").write_text('__version__ = "0.0.1-not-the-pin"\n')
blocks("6. Hermes version pin mismatch", by_id["version-pin-mismatch"],
       lambda: run(home=str(fake_home)))

tree = ST.build_probe_tree(tmp)
scope = {"allowed_roots": [tree["repo"]], "denied_subpaths": [".git/", ".env", ".env.*"]}
# Isolate each patch failure: leave the OTHER patch live, or the first check to
# run would claim the failure and the two cases would be indistinguishable.
HP.uninstall()
HP.install_confinement(scope)
blocks("7. capability patch not live", by_id["capability-patch-dead"],
       lambda: H.check(repo_root=str(repo), env=GOOD_ENV, config=GOOD_CONFIG,
                       hermes_home=hermes_home, require_patches=True))

HP.uninstall()
HP.install_capability(("read_file", "search_files"))
blocks("8. confinement patch not live", by_id["confinement-patch-dead"],
       lambda: H.check(repo_root=str(repo), env=GOOD_ENV, config=GOOD_CONFIG,
                       hermes_home=hermes_home, require_patches=True))

# --- the pre-import phase: controls proven BEFORE Hermes is imported ---
# Importing model_tools with HERMES_SAFE_MODE unset loads plugin modules
# in-process, so these four controls must not depend on a Hermes import.
import subprocess as _sp
probe = (
    "import sys, os, json;"
    "sys.path.insert(0, %r); sys.path.insert(0, %r);"
    "os.environ.pop('HERMES_SAFE_MODE', None);"
    "import hermes as H, blocking;"
    "\ntry:\n"
    "    H.check_pre_import(repo_root=%r, env={}, hermes_home=%r)\n"
    "    print('NO_BLOCK')\n"
    "except blocking.Blocked as b:\n"
    "    print('BLOCKED:' + b.code)\n"
    "print('HERMES_IMPORTED:' + str(any(m in sys.modules for m in ('model_tools', 'tools.registry'))))"
) % (ad_dir, str(Path(ad_dir).parent / "runtime"), str(repo), hermes_home)
res = _sp.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120)
out = res.stdout
check("pre-import phase BLOCKS when HERMES_SAFE_MODE is unset",
      "BLOCKED:" + blocking.SAFE_MODE_NOT_ENABLED in out, f"(got {out.strip()!r} {res.stderr[-200:]})")
check("pre-import phase did NOT import Hermes (no plugin discovery ran)",
      "HERMES_IMPORTED:False" in out, f"(got {out.strip()!r})")
check("pre-import phase passes with a clean environment",
      isinstance(H.check_pre_import(repo_root=str(repo), env=GOOD_ENV, hermes_home=hermes_home), dict))
for label, kwargs, code in (
    ("version pin", {"hermes_home": str(fake_home)}, by_id["version-pin-mismatch"]),
    (".hermes.md", {"repo_root": str(poisoned)}, by_id["hermes-md-present"]),
    ("AGENTS.override.md", {"repo_root": str(poisoned2)}, by_id["agents-override-present"]),
):
    blocks(f"pre-import phase enforces {label}", code,
           lambda kw=kwargs: H.check_pre_import(
               **{"repo_root": str(repo), "env": GOOD_ENV, "hermes_home": hermes_home, **kw}))

# --- with both patches live and probed, preflight passes end to end ---
HP.install_confinement(scope)
full = H.check(repo_root=str(repo), env=GOOD_ENV, config=GOOD_CONFIG,
               hermes_home=hermes_home, require_patches=True, probe_tree=tree)
check("preflight passes with both patches live and behaviorally probed",
      any(c["control"] == "confinement-self-test" for c in full["controls"])
      and any(c["control"] == "capability-self-test" for c in full["controls"]))

# --- unreachable installation ---
blocks("Hermes unreachable blocks", blocking.HERMES_UNREACHABLE,
       lambda: run(home=str(Path(tmp, "nothing-here"))))

# --- controls the grill removed must NOT be enforced ---
check("subagent_auto_approve is subsumed, not a separate control",
      "subagent_auto_approve" in matrix["subsumed_controls"]
      and not any("subagent" in c["control"] for c in full["controls"]))
check("approvals.mode is listed as recorded context, not a control",
      "approvals.mode" in matrix["recorded_context_not_controls"])
ok_with_any_approvals = H.check(
    repo_root=str(repo), env=GOOD_ENV,
    config={**GOOD_CONFIG, "approvals": {"mode": "auto"}},
    hermes_home=hermes_home, require_patches=False)
check("approvals.mode=auto does NOT block (it is not on the security path)",
      ok_with_any_approvals["recorded_context"]["approvals.mode"] == "auto")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
