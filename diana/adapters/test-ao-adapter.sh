#!/usr/bin/env bash
set -euo pipefail

ADAPTER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADAPTER="$ADAPTER_DIR/ao.py"
FIXTURES="$ADAPTER_DIR/fixtures"

assert_field() {
  local json="$1" path="$2" expected="$3"
  local actual
  actual="$(python3 -c "
import json, sys
data = json.loads(sys.argv[1])
for key in sys.argv[2].split('.'):
    data = data[key]
print(json.dumps(data))
" "$json" "$path")"
  if [ "$actual" != "$expected" ]; then
    echo "FAIL: expected $path == $expected, got $actual" >&2
    echo "  raw: $json" >&2
    exit 1
  fi
}

run_check() {
  local name="$1" ao_bin="$2" ao_home="$3" expected_ok="$4" expected_error="${5:-}"
  local output status
  set +e
  output="$(python3 "$ADAPTER" check --ao-bin "$ao_bin" --ao-home "$ao_home" 2>&1)"
  status=$?
  set -e
  local expected_status=0
  [ "$expected_ok" = "false" ] && expected_status=1
  if [ "$status" -ne "$expected_status" ]; then
    echo "FAIL $name: expected exit $expected_status, got $status ($output)" >&2
    exit 1
  fi
  assert_field "$output" "ok" "$expected_ok"
  if [ -n "$expected_error" ]; then
    assert_field "$output" "error" "\"$expected_error\""
  fi
  echo "PASS $name"
}

# CASE 1: compatible AO -> check PASS
run_check "compatible-direct-version" "$FIXTURES/fake-ao-compatible" "$FIXTURES" "true"
run_check "compatible-app-state-fallback" "$FIXTURES/fake-ao-dev-fallback" "$FIXTURES" "true"

# CASE 2: AO missing -> clear FAIL
run_check "ao-missing" "$FIXTURES/does-not-exist-ao" "$FIXTURES" "false" "ao_missing"

# CASE 3: wrong AO version -> clear incompatible FAIL
run_check "wrong-version" "$FIXTURES/fake-ao-wrong-version" "$FIXTURES" "false" "version_incompatible"

# Bonus: AO present + correct version, daemon not ready -> distinct failure
run_check "runtime-down" "$FIXTURES/fake-ao-runtime-down" "$FIXTURES" "false" "ao_runtime_unavailable"

# CASE 4: spawn command construction -> adapter builds exactly the expected
# argv, always with --harness claude-code regardless of caller intent (there
# is no --harness flag exposed by the CLI at all).
argv_file="$(mktemp)"
trap 'rm -f "$argv_file"' EXIT
export FAKE_AO_SPAWN_ARGV_FILE="$argv_file"
output="$(python3 "$ADAPTER" spawn \
    --ao-bin "$FIXTURES/fake-ao-spawn-record" \
    --project agent-orchestration \
    --name test-worker \
    --prompt "do the thing")"
unset FAKE_AO_SPAWN_ARGV_FILE
assert_field "$output" "ok" "true"
assert_field "$output" "session_id" "\"fake-session-1\""
recorded="$(cat "$argv_file")"
for expected_token in "--project" "agent-orchestration" "--harness" "claude-code" "--kind" "worker" "--mode" "chat" "--name" "test-worker" "--prompt" "do the thing"; do
  case "$recorded" in
    *"$expected_token"*) ;;
    *)
      echo "FAIL spawn-argv: expected token '$expected_token' in recorded argv: $recorded" >&2
      exit 1
      ;;
  esac
done
case "$recorded" in
  *codex*)
    echo "FAIL spawn-argv: recorded argv must never mention codex: $recorded" >&2
    exit 1
    ;;
esac
echo "PASS spawn-argv-construction (harness pinned to claude-code)"

set +e
output="$(python3 "$ADAPTER" spawn --ao-bin "$FIXTURES/fake-ao-spawn-fail" --project totally-bogus --name x --prompt y 2>&1)"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL spawn-fail: expected exit 1, got $status" >&2; exit 1; }
assert_field "$output" "ok" "false"
assert_field "$output" "error" "\"spawn_failed\""
echo "PASS spawn-fail"

# CASE 5: session/status failure -> clear non-zero failure
set +e
output="$(python3 "$ADAPTER" status --ao-bin "$FIXTURES/fake-ao-status-fail" --session totally-bogus 2>&1)"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL status-fail: expected exit 1, got $status" >&2; exit 1; }
assert_field "$output" "ok" "false"
assert_field "$output" "error" "\"status_failed\""
echo "PASS status-fail"

# CASE 6: termination failure -> clear non-zero failure
set +e
output="$(python3 "$ADAPTER" stop --ao-bin "$FIXTURES/fake-ao-stop-fail" --session totally-bogus 2>&1)"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL stop-fail: expected exit 1, got $status" >&2; exit 1; }
assert_field "$output" "ok" "false"
assert_field "$output" "error" "\"stop_failed\""
echo "PASS stop-fail"

echo "All Diana AO adapter tests passed."
