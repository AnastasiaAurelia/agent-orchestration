#!/usr/bin/env bash
set -euo pipefail

CI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAP="$CI_DIR/map-gate-result.py"

run_case() {
  local gate_exit="$1" expected_exit="$2"
  local status
  set +e
  python3 "$MAP" "$gate_exit" >/dev/null 2>&1
  status=$?
  set -e
  if [ "$status" -ne "$expected_exit" ]; then
    echo "FAIL gate_exit=$gate_exit: mapped to $status, expected $expected_exit" >&2
    exit 1
  fi
  echo "PASS gate_exit=$gate_exit -> check exit $status"
}

run_case 0 0    # PASS -> success
run_case 2 0    # REQUIRE_HUMAN -> success (independent review rule still applies)
run_case 1 1    # FAIL -> failure
run_case 3 1    # unexpected exit code -> fail closed
run_case abc 1  # non-integer -> fail closed
