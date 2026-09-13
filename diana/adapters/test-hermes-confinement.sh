#!/usr/bin/env bash
# CP5: the confinement patch, proven behaviorally through the real tools on the
# actual pool-worker execution path.
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
import os, sys
from pathlib import Path

ad_dir, tmp, hermes_home = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ["DIANA_HERMES_HOME"] = hermes_home
sys.path.insert(0, ad_dir)
sys.path.insert(0, str(Path(ad_dir).parent / "runtime"))
import blocking, hermes_patches as HP, selftest as ST

passed = failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")

# --- C1 version pin ---
check("Hermes version matches the C1 pin",
      HP.hermes_version() == HP.PINNED_VERSION, f"(got {HP.hermes_version()})")
check("Hermes git commit matches the C1 pin",
      HP.hermes_commit() == HP.PINNED_COMMIT, f"(got {HP.hermes_commit()})")

tree = ST.build_probe_tree(tmp)
scope = {"allowed_roots": [tree["repo"]], "denied_subpaths": [".git/", ".env", ".env.*"]}

# --- the patch must be load-bearing: prove the hole exists without it ---
sys.path.insert(0, hermes_home)
import tools.file_tools as ft
unconfined = str(ft.read_file_tool(path=tree["outside"]))
check("WITHOUT the patch, Hermes reads a file outside the repo (the hole is real)",
      "diana-outside-sentinel" in unconfined, f"(got {unconfined[:120]})")
unconfined_env = str(ft.read_file_tool(path=tree["denied_env"]))
check("Hermes has its own .env protection, so denied_subpaths is defense in depth there",
      "must-not-be-read" not in unconfined_env)
check("Hermes has NO equivalent protection for arbitrary out-of-root reads",
      "diana-outside-sentinel" in unconfined)
check("search_files is unconfined without the patch",
      "secret.txt" in str(ft.search_tool(pattern="*", target="files",
                                         path=str(Path(tree["outside"]).parent))))

# --- install and probe on the real worker path ---
HP.install_confinement(scope)
check("confinement reports live", HP.confinement_live() is True)
results = ST.confinement_probes(tree)
for r in results:
    check(f"AC-2 {r['probe']}", r["ok"], f"({r['detail']})")
check("assert_all passes when every probe passes",
      ST.assert_all(results, blocking.CONFINEMENT_PATCH_NOT_LIVE) is None)

# --- the probes must actually fail when the patch is removed ---
HP.uninstall()
check("confinement reports not live after uninstall", HP.confinement_live() is False)
after = ST.confinement_probes(tree)
check("probes FAIL when the patch is not live (the self-test is falsifiable)",
      any(not r["ok"] for r in after))
try:
    ST.assert_all(after, blocking.CONFINEMENT_PATCH_NOT_LIVE)
    check("a dead patch BLOCKS the run", False, "(no Blocked raised)")
except blocking.Blocked as b:
    check("a dead patch BLOCKS the run with its own reason code",
          b.code == blocking.CONFINEMENT_PATCH_NOT_LIVE, f"(got {b.code})")

# --- reinstall: enforcement holds across worker threads, not just the caller ---
HP.install_confinement(scope)
import threading
seen = {}
def worker():
    seen["denied"] = ST._denied(ST._read(tree["outside"]))
t = threading.Thread(target=worker); t.start(); t.join(timeout=60)
check("enforcement holds on a thread that never saw the install call",
      seen.get("denied") is True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
