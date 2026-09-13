#!/usr/bin/env bash
# CP6: the capability patch at handle_function_call, proven behaviorally on the
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

tree = ST.build_probe_tree(tmp)
scope = {"allowed_roots": [tree["repo"]], "denied_subpaths": [".git/", ".env", ".env.*"]}
ALLOWED = ("read_file", "search_files")

sys.path.insert(0, hermes_home)
import model_tools as mt
from tools.registry import registry

# --- the hole is real: schema hiding would not close it (D21/D22) ---
names = {e.name for e in registry.get_all_entries()}
check("the global registry holds far more than the envelope",
      len(names) > 50, f"(got {len(names)})")
for dangerous in ("write_file", "patch", "terminal", "execute_code", "delegate_task"):
    check(f"registry contains {dangerous} regardless of what the model is shown",
          dangerous in names)

hole = str(Path(tmp, "hole.txt"))
mt.handle_function_call("write_file", {"path": hole, "content": "written by hermes"})
check("WITHOUT the patch, a crafted write_file call actually writes the file",
      Path(hole).exists())
check("WITHOUT the patch, no approval gate stopped it", Path(hole).read_text().strip() == "written by hermes")

# --- the inline-executor bypass: handle_function_call is NOT the only entry ---
from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS, resolve_invoke_tool_executor
check("13 tools resolve to inline executors that never reach handle_function_call",
      len(INLINE_TOOL_EXECUTORS) == 13, f"(got {len(INLINE_TOOL_EXECUTORS)})")
check("delegate_task is one of them", "delegate_task" in INLINE_TOOL_EXECUTORS)
before = ST._drive_real_dispatch("delegate_task")
check("WITHOUT the dispatch guard, delegate_task's handler RUNS on the agent-loop funnel",
      before["executed"] is True, f"(got {before})")

# --- install and probe on the real worker path ---
HP.install_confinement(scope)
HP.install_capability(ALLOWED)
check("capability reports live", HP.capability_live() is True)

results = ST.capability_probes(tree)
for r in results:
    check(f"AC-2 {r['probe']}", r["ok"], f"({r['detail']})")
check("assert_all passes when every probe passes",
      ST.assert_all(results, blocking.CAPABILITY_PATCH_NOT_LIVE) is None)

# --- the agent-loop funnel, where the inline bypass lived ---
funnel = ST.dispatch_funnel_probes()
for r in funnel:
    check(f"AC-2 {r['probe']}", r["ok"], f"({r['detail']})")
after = ST._drive_real_dispatch("delegate_task")
check("WITH the dispatch guard, delegate_task's handler never runs",
      after["executed"] is False and "diana:" in after["result"], f"(got {after})")
check("the funnel guard covers every inline-executor name",
      len(funnel) >= len(INLINE_TOOL_EXECUTORS) + 2)

# --- the invariant, stated directly ---
canary = str(Path(tree["repo"], "invariant.txt"))
out = str(mt.handle_function_call("write_file", {"path": canary, "content": "x"}))
check("a non-envelope tool is refused before its handler executes",
      "diana:" in out and not Path(canary).exists())
check("the registry still contains the tool; existence is irrelevant",
      "write_file" in {e.name for e in registry.get_all_entries()})

# --- unknown / future tool names deny by set membership, no special case ---
for synthetic in ("diana_synthetic_future_tool", "hermes_v2_new_tool", ""):
    check(f"unknown tool name denied: {synthetic!r}",
          "diana:" in str(mt.handle_function_call(synthetic, {})))

# --- the connector branch cannot route around the boundary ---
check("connector-prefixed name is refused at handle_function_call",
      "diana:" in str(mt.handle_function_call("connector__acme__send", {"x": 1})))

# --- falsifiability: probes must fail when the patch is removed ---
HP.uninstall()
check("capability reports not live after uninstall", HP.capability_live() is False)
check("uninstall restores the real dispatch funnel (the bypass returns)",
      ST._drive_real_dispatch("delegate_task")["executed"] is True)
after = ST.capability_probes(tree)
check("probes FAIL when the patch is not live (the self-test is falsifiable)",
      any(not r["ok"] for r in after))
try:
    ST.assert_all(after, blocking.CAPABILITY_PATCH_NOT_LIVE)
    check("a dead capability patch BLOCKS the run", False, "(no Blocked raised)")
except blocking.Blocked as b:
    check("a dead capability patch BLOCKS with its own reason code",
          b.code == blocking.CAPABILITY_PATCH_NOT_LIVE, f"(got {b.code})")

# --- reinstall; enforcement holds on a thread that never saw the install ---
HP.install_confinement(scope)
HP.install_capability(ALLOWED)
import threading
seen = {}
def worker():
    seen["denied"] = "diana:" in str(mt.handle_function_call("terminal", {"command": "echo hi"}))
    # A distinct file: Hermes's own repeated-read guard trips after the same
    # path is read several times in one task, and that would mask the property
    # under test.
    fresh = Path(tree["repo"], "thread_probe.js")
    fresh.write_text("var sentinel = 'thread-in-scope';\n")
    seen["allowed"] = "thread-in-scope" in str(
        mt.handle_function_call("read_file", {"path": str(fresh)}))
t = threading.Thread(target=worker); t.start(); t.join(timeout=60)
check("capability enforcement holds on an unrelated thread", seen.get("denied") is True)
check("allowed tools still work on an unrelated thread", seen.get("allowed") is True)

# --- the two patches compose: capability first, then confinement ---
check("an allowed tool is still confined by read_scope",
      "diana:" in str(mt.handle_function_call("read_file", {"path": "/etc/passwd"})))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
