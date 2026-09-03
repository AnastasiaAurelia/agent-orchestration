#!/usr/bin/env bash
set -euo pipefail

GATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE="$GATE_DIR/diana-gate.py"
FIXTURES="$GATE_DIR/fixtures"

run_case() {
  local fixture="$1" expected_decision="$2" expected_exit="$3" expected_check="${4:-}"
  local output status
  set +e
  output="$(python3 "$GATE" "$FIXTURES/$fixture.json")"
  status=$?
  set -e
  python3 - "$output" "$expected_decision" "$expected_check" <<'PY'
import json, sys
result=json.loads(sys.argv[1])
assert result["decision"] == sys.argv[2], result
if sys.argv[3]:
    check_id, check_result = sys.argv[3].split(":", 1)
    assert {"id": check_id, "result": check_result} in result["checks"], result
PY
  if [ "$status" -ne "$expected_exit" ]; then
    echo "FAIL $fixture: exit $status, expected $expected_exit" >&2
    exit 1
  fi
  echo "PASS $fixture -> $expected_decision (exit $status)"
}

run_case safe PASS 0
run_case missing-dod FAIL 1
run_case blocker-preflight FAIL 1
run_case human-only REQUIRE_HUMAN 2
run_case irrelevant-stack PASS 0 stripe-webhook:SKIP
run_case malformed FAIL 1
run_case nearby-safe PASS 0
