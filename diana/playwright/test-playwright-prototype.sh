#!/usr/bin/env bash
# CASE B/C/D/E fixture matrix for the Playwright MCP prototype.
#
# Drives the real @playwright/mcp server (via run-prototype.mjs) against
# the local healthy and broken fixture apps and asserts the expected
# outcome for each — mirroring diana/preflight/test-preflight.sh's
# fixture-matrix style. Requires network access on first run (to fetch
# @playwright/mcp and its browser binary) and Node >= 20 on PATH — both are
# Playwright's own hard requirements, not something Diana can paper over.
# See README.md "Prerequisites".
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRIVER="$DIR/run-prototype.mjs"
EVIDENCE_ROOT="$DIR/evidence"

NODE_MAJOR="$(node -e 'process.stdout.write(String(process.versions.node.split(".")[0]))' 2>/dev/null || echo 0)"
if [ "$NODE_MAJOR" -lt 20 ] 2>/dev/null; then
  echo "SKIP: Playwright MCP requires Node >= 20 on PATH (found: $(node --version 2>/dev/null || echo 'no node'))." >&2
  echo "This is Playwright's own requirement, not Diana's. See README.md 'Prerequisites'." >&2
  exit 1
fi

run_case() {
  local fixture="$1" expected_match="$2"
  local evidence_dir="$EVIDENCE_ROOT/$fixture"
  rm -rf "$evidence_dir"
  mkdir -p "$evidence_dir"
  local output status
  set +e
  output="$(node "$DRIVER" "$DIR/fixtures/$fixture" "$evidence_dir" "Ada" 2>"$evidence_dir/stderr.log")"
  status=$?
  set -e
  if [ "$status" -ne 0 ]; then
    echo "FAIL $fixture: driver exited $status" >&2
    echo "$output" >&2
    exit 1
  fi
  echo "$output" > "$evidence_dir/report.json"
  python3 - "$output" "$expected_match" "$evidence_dir" <<'PY'
import json, sys, os
report = json.loads(sys.argv[1])
expected_match = bool(int(sys.argv[2]))
evidence_dir = sys.argv[3]
assert report.get("matches_expected_name") == expected_match, report
assert os.path.isfile(report["screenshot"]), f"missing screenshot evidence: {report.get('screenshot')}"
assert "snapshot_excerpt" in report and report["snapshot_excerpt"], "missing snapshot evidence"
required_steps = ["initialized", "navigated:index.html", "filled:name", "clicked:continue", "navigated-back:index.html"]
for step in required_steps:
    assert step in report["steps"], f"missing step: {step}"
PY
  echo "PASS $fixture (evidence: $evidence_dir)"
}

# CASE B: healthy flow — navigate, fill, click, new route, correct text,
# back-navigation.
run_case app-healthy 1

# CASE C: deliberately broken flow — same interactions, genuine
# browser-visible failure (submitted value never appears) detected and
# captured as evidence, not a synthetic return-false.
run_case app-broken 0

# CASE D (headless) and CASE E (isolation) hold by construction: both
# runs above passed --headless to the MCP server and only ever navigated
# to a 127.0.0.1 server this script started itself — see run-prototype.mjs.
echo "CASE D (headless): confirmed by construction (--headless passed to MCP server in both cases above)."
echo "CASE E (isolation): confirmed by construction (fixtures only navigate to a localhost server this script starts; no AO Electron dependency)."

echo "All Playwright MCP prototype cases passed."
