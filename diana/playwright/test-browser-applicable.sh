#!/usr/bin/env bash
# CASE A / fixture matrix for diana/playwright/browser_applicable.py.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$DIR/browser_applicable.py"
FIXTURES="$DIR/fixtures"

# run_case FIXTURE EXPECTED_APPLICABLE(0/1) EXPECTED_EXIT
run_case() {
  local fixture="$1" expected_applicable="$2" expected_exit="$3"
  local output status
  set +e
  output="$(python3 "$SCRIPT" "$FIXTURES/$fixture.json")"
  status=$?
  set -e
  if [ "$status" -ne "$expected_exit" ]; then
    echo "FAIL $fixture: exit $status (expected $expected_exit): $output" >&2
    exit 1
  fi
  if [ "$expected_exit" -eq 0 ]; then
    python3 - "$output" "$expected_applicable" <<'PY'
import json, sys
result = json.loads(sys.argv[1])
expected = bool(int(sys.argv[2]))
assert result["applicable"] == expected, result
assert result["reasons"], result
PY
  fi
  echo "PASS $fixture"
}

# CASE A: backend-only diff -> browser verification SKIP (not applicable).
run_case diff-backend-only 0 0

# Browser-facing diff -> applicable.
run_case diff-frontend-facing 1 0

# Malformed input (empty files array) -> fail closed, non-zero exit.
run_case diff-malformed 0 1

echo "All browser_applicable.py fixture cases passed."
