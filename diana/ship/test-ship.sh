#!/usr/bin/env bash
set -euo pipefail

# Deterministic tests for diana/ship/ship.py's own orchestration/gating
# logic. Never spawns a real AO worker and never calls the real `gh` -
# both are faked. Reuses diana/adapters/ao.py's real Phase 7 fake-ao-*
# fixtures for the AO-compatibility check rather than duplicating them.

SHIP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHIP="$SHIP_DIR/ship.py"
REPO_ROOT="$(cd "$SHIP_DIR/../.." && pwd)"
AO_FIXTURES="$REPO_ROOT/diana/adapters/fixtures"
SHIP_FIXTURES="$SHIP_DIR/fixtures"

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

# A tiny throwaway git repo used as the "shared repo" for verify-diff/gate
# cases: a base commit, plus a worker branch with one in-scope change.
make_repo() {
  local dir="$1"
  git init -q "$dir"
  git -C "$dir" config user.email test@test.com
  git -C "$dir" config user.name test
  mkdir -p "$dir/app"
  echo "def add(a, b): return a + b" > "$dir/app/main.py"
  cat > "$dir/app/test_main.sh" <<'SCRIPT'
#!/usr/bin/env bash
echo test placeholder
SCRIPT
  git -C "$dir" add -A
  git -C "$dir" commit -q -m base
}

echo "=== CASE 1: SAFE task, successful worker, tests+preflight+gate -> ready PR path ==="
repo1="$(mktemp -d)"
make_repo "$repo1"
base1="$(git -C "$repo1" rev-parse HEAD)"
git -C "$repo1" switch -q -c worker
echo "def sub(a, b): return a - b" >> "$repo1/app/main.py"
git -C "$repo1" commit -q -am feature
worker1="$(git -C "$repo1" rev-parse HEAD)"

out="$(python3 "$SHIP" precheck --repo "$repo1" --risk SAFE \
  --ao-bin "$AO_FIXTURES/fake-ao-compatible" --ao-home "$AO_FIXTURES")"
assert_field "$out" "ok" "true"
assert_field "$out" "ready_to_spawn" "true"

out="$(python3 "$SHIP" verify-diff --repo "$repo1" --base "$base1" --worker-ref "$worker1" \
  --allowed-files '["app/main.py"]')"
assert_field "$out" "ok" "true"

out="$(python3 "$SHIP" gate --repo "$repo1" --files '["app/main.py"]' \
  --dod '{"present":true,"evidence":["add a subtract helper"]}' \
  --verification '{"present":true,"evidence":["ran app/test_main.sh"]}' \
  --risk SAFE)"
assert_field "$out" "ok" "true"
assert_field "$out" "gate_decision" "\"PASS\""
assert_field "$out" "browser_status" "\"skipped_not_applicable\""
gate_evidence="$(python3 -c "import json,sys; print(json.dumps(json.loads(sys.argv[1])['evidence']))" "$out")"

argv_file="$(mktemp)"
export FAKE_GH_ARGV_FILE="$argv_file"
out="$(python3 "$SHIP" open-pr --gate-evidence "$gate_evidence" \
  --head-branch worker --title "add subtract helper" --body-preamble "feature: add subtract helper" \
  --gh-bin "$SHIP_FIXTURES/fake-gh-create-ok")"
unset FAKE_GH_ARGV_FILE
assert_field "$out" "ok" "true"
assert_field "$out" "merged" "false"
grep -q "^https://github.com/fake/repo/pull/999$" <(python3 -c "import json,sys; print(json.loads(sys.argv[1])['pr_url'])" "$out")
rm -f "$argv_file"
rm -rf "$repo1"
echo "PASS case-1-ready-pr-path"

echo "=== CASE 2: risk HUMAN_ONLY -> no worker spawned (precheck refuses) ==="
repo2="$(mktemp -d)"; make_repo "$repo2"
out="$(python3 "$SHIP" precheck --repo "$repo2" --risk SAFE \
  --human-only-conditions '["credential_change"]' \
  --ao-bin "$AO_FIXTURES/fake-ao-compatible" --ao-home "$AO_FIXTURES" 2>&1)" && status=0 || status=$?
[ "$status" -eq 1 ] || { echo "FAIL case-2: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"requires_human_review\""
assert_field "$out" "decision" "\"REQUIRE_HUMAN\""
rm -rf "$repo2"
echo "PASS case-2-human-only-blocks-before-spawn"

echo "=== CASE 3: AO unavailable -> fail before worker ==="
repo3="$(mktemp -d)"; make_repo "$repo3"
set +e
out="$(python3 "$SHIP" precheck --repo "$repo3" --risk SAFE --ao-bin "$AO_FIXTURES/does-not-exist-ao")"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL case-3: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"ao_unavailable\""
rm -rf "$repo3"
echo "PASS case-3-ao-unavailable"

echo "=== CASE 4: worker failure (no commit produced) -> no PR ==="
repo4="$(mktemp -d)"; make_repo "$repo4"
base4="$(git -C "$repo4" rev-parse HEAD)"
set +e
out="$(python3 "$SHIP" verify-diff --repo "$repo4" --base "$base4" --worker-ref "$base4" --allowed-files '["app/main.py"]')"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL case-4: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"empty_diff\""
rm -rf "$repo4"
echo "PASS case-4-worker-failure-empty-diff"

echo "=== CASE 5: unexpected/out-of-scope diff -> no PR ==="
repo5="$(mktemp -d)"; make_repo "$repo5"
base5="$(git -C "$repo5" rev-parse HEAD)"
git -C "$repo5" switch -q -c worker
echo "unexpected" > "$repo5/scope-violation.txt"
git -C "$repo5" add -A && git -C "$repo5" commit -q -m oops
worker5="$(git -C "$repo5" rev-parse HEAD)"
set +e
out="$(python3 "$SHIP" verify-diff --repo "$repo5" --base "$base5" --worker-ref "$worker5" --allowed-files '["app/main.py"]')"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL case-5: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"out_of_scope_diff\""
rm -rf "$repo5"
echo "PASS case-5-out-of-scope-diff"

# Bonus, same family as case 5: a forbidden path (e.g. diana/gate/) is
# rejected even if --allowed-files was (mis)configured to cover it.
repo5b="$(mktemp -d)"; make_repo "$repo5b"
base5b="$(git -C "$repo5b" rev-parse HEAD)"
mkdir -p "$repo5b/diana/gate"
git -C "$repo5b" switch -q -c worker
echo "tampered" > "$repo5b/diana/gate/diana-gate.py"
git -C "$repo5b" add -A && git -C "$repo5b" commit -q -m oops
worker5b="$(git -C "$repo5b" rev-parse HEAD)"
set +e
out="$(python3 "$SHIP" verify-diff --repo "$repo5b" --base "$base5b" --worker-ref "$worker5b" --allowed-files '["diana/gate/diana-gate.py"]')"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL case-5b: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"forbidden_scope_touched\""
rm -rf "$repo5b"
echo "PASS case-5b-forbidden-path-rejected-even-if-declared-allowed"

echo "=== CASE 6: Preflight BLOCKER/FAIL -> no PR ==="
repo6="$(mktemp -d)"
echo "STRIPE_SECRET_KEY=whatever" > "$repo6/.env"
set +e
out="$(python3 "$SHIP" gate --repo "$repo6" --files '["app.py"]' \
  --dod '{"present":true,"evidence":["x"]}' \
  --verification '{"present":true,"evidence":["y"]}' \
  --risk SAFE)"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL case-6: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"gate_not_pass\""
assert_field "$out" "gate_decision" "\"FAIL\""
rm -rf "$repo6"
echo "PASS case-6-preflight-blocker-fail-no-pr"

echo "=== CASE 7: Diana Gate FAIL for a non-preflight reason -> no PR ==="
repo7="$(mktemp -d)"; make_repo "$repo7"
set +e
out="$(python3 "$SHIP" gate --repo "$repo7" --files '["app/main.py"]' \
  --dod '{"present":true,"evidence":["x"]}' \
  --verification '{"present":false,"evidence":[]}' \
  --risk SAFE)"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL case-7: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"gate_not_pass\""
assert_field "$out" "gate_decision" "\"FAIL\""
rm -rf "$repo7"
echo "PASS case-7-gate-fail-missing-verification-no-pr"

echo "=== CASE 8: browser-inapplicable change -> Playwright SKIP, otherwise normal ==="
repo8="$(mktemp -d)"; make_repo "$repo8"
out="$(python3 "$SHIP" gate --repo "$repo8" --files '["app/main.py"]' \
  --dod '{"present":true,"evidence":["x"]}' \
  --verification '{"present":true,"evidence":["y"]}' \
  --risk SAFE)"
assert_field "$out" "ok" "true"
assert_field "$out" "browser_status" "\"skipped_not_applicable\""
rm -rf "$repo8"
echo "PASS case-8-browser-inapplicable-skip"

echo "=== CASE 9: browser-applicable change with missing verification -> no ready PR ==="
repo9="$(mktemp -d)"; make_repo "$repo9"
set +e
out="$(python3 "$SHIP" gate --repo "$repo9" --files '["src/Button.tsx"]' \
  --dod '{"present":true,"evidence":["x"]}' \
  --verification '{"present":true,"evidence":["y"]}' \
  --risk SAFE)"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL case-9: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"browser_verification_missing\""
rm -rf "$repo9"
echo "PASS case-9-browser-applicable-missing-evidence-no-pr"

echo "=== CASE 10: successful PR creation -> STOP before merge ==="
argv_file="$(mktemp)"
export FAKE_GH_ARGV_FILE="$argv_file"
out="$(python3 "$SHIP" open-pr \
  --gate-evidence '{"dod":{"present":true,"evidence":["x"]},"verification":{"present":true,"evidence":["y"]},"preflight":[],"risk":"SAFE","human_only_conditions":[]}' \
  --head-branch diana/ship/case10 --title "case 10" --body-preamble "case 10 body" \
  --gh-bin "$SHIP_FIXTURES/fake-gh-create-ok")"
unset FAKE_GH_ARGV_FILE
assert_field "$out" "ok" "true"
assert_field "$out" "merged" "false"
python3 -c "
import sys
argv = open(sys.argv[1], 'rb').read().decode('utf-8').split('\0')[:-1]
body = argv[argv.index('--body') + 1]
assert 'NO AUTO MERGE PERFORMED' in body, body
assert '\"risk\": \"SAFE\"' in body, body
" "$argv_file"
rm -f "$argv_file"

set +e
out="$(python3 "$SHIP" open-pr \
  --gate-evidence '{"dod":{"present":true,"evidence":["x"]},"verification":{"present":true,"evidence":["y"]},"preflight":[],"risk":"SAFE","human_only_conditions":[]}' \
  --head-branch diana/ship/case10 --title "case 10" --body-preamble "case 10 body" \
  --gh-bin "$SHIP_FIXTURES/fake-gh-create-fail")"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL case-10-failure-path: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"pr_creation_failed\""

merge_hits="$(grep -cF '"merge"' "$SHIP" || true)"
if [ "$merge_hits" -ne 0 ]; then
  echo "FAIL: ship.py contains a quoted \"merge\" argv literal - it must never call a merge subcommand" >&2
  exit 1
fi
echo "PASS case-10-pr-created-no-merge-code-path"

echo "All Diana ship workflow tests passed."
