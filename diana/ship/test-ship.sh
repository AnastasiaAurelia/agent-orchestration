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

# Precise, not blanket: the real invariant is "never call gh pr merge / never
# merge a PR into protected main." Phase 11's cmd_integrate legitimately uses
# plain `git merge` between two disposable worker branches on an isolated
# integration branch - a local, non-protected-branch operation this task
# itself requires ("use normal Git semantics" for integration). So this
# checks only the one function that ever touches `gh` (cmd_open_pr), not the
# whole file.
python3 -c "
import re, sys
src = open(sys.argv[1]).read()
match = re.search(r'def cmd_open_pr\(.*?\n(?=def |\Z)', src, re.DOTALL)
assert match, 'cmd_open_pr function not found'
body = match.group(0)
assert '\"merge\"' not in body, (
    'cmd_open_pr (the only gh-facing function) must never reference a '
    'merge subcommand - it must never merge a PR into protected main'
)
" "$SHIP"
echo "PASS case-10-pr-created-no-merge-code-path"

echo "=== Phase 10: independent reviewer orchestration ==="

pass_verdict='{"decision":"PASS","summary":"implementation satisfies the DoD","findings":[],"dod_checks":[{"criterion":"add a subtract helper","result":"PASS","evidence":"app/main.py defines sub()"}]}'
fail_verdict='{"decision":"FAIL","summary":"implementation does not satisfy the DoD","findings":[{"severity":"BLOCKER","description":"sub() adds instead of subtracting","evidence":"app/main.py: return a + b"}],"dod_checks":[{"criterion":"add a subtract helper","result":"FAIL","evidence":"app/main.py: return a + b"}]}'

echo "--- CASE 1: actor valid + reviewer PASS -> Gate stage permitted ---"
repo10a="$(mktemp -d)"; make_repo "$repo10a"
out="$(python3 "$SHIP" review-verdict --verdict "$pass_verdict")"
assert_field "$out" "ok" "true"
out="$(python3 "$SHIP" gate --repo "$repo10a" --files '["app/main.py"]' \
  --dod '{"present":true,"evidence":["add a subtract helper"]}' \
  --verification '{"present":true,"evidence":["ran app/test_main.sh"]}' \
  --risk SAFE)"
assert_field "$out" "ok" "true"
assert_field "$out" "gate_decision" "\"PASS\""
rm -rf "$repo10a"
echo "PASS phase10-case-1-reviewer-pass-permits-gate"

echo "--- CASE 2: reviewer FAIL -> Gate not called, no PR ---"
set +e
out="$(python3 "$SHIP" review-verdict --verdict "$fail_verdict")"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase10-case-2: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"review_failed\""
echo "PASS phase10-case-2-reviewer-fail-blocks-gate"

echo "--- CASE 3: reviewer malformed output -> fail closed ---"
set +e
out="$(python3 "$SHIP" review-verdict --verdict '{"decision":"PASS","summary":"x"}')"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase10-case-3: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"malformed_verdict\""
echo "PASS phase10-case-3-malformed-verdict-fails-closed"

echo "--- CASE 4: reviewer process/session failure (no output at all) -> fail closed ---"
set +e
out="$(python3 "$SHIP" review-verdict --verdict "")"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase10-case-4: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"no_verdict_produced\""
echo "PASS phase10-case-4-reviewer-crash-fails-closed"

echo "--- CASE 5: reviewer attempts/reports mutation -> fail closed ---"
repo10b="$(mktemp -d)"; make_repo "$repo10b"
head10b="$(git -C "$repo10b" rev-parse HEAD)"
echo "reviewer should never write this" > "$repo10b/reviewer-tampered.txt"
set +e
out="$(python3 "$SHIP" reviewer-readonly-check --repo "$repo10b" --expected-head "$head10b")"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase10-case-5a: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"reviewer_mutation_detected\""
git -C "$repo10b" clean -q -fd
# Also cover a reviewer that went further and committed.
echo "committed by reviewer" > "$repo10b/sneaky.txt"
git -C "$repo10b" add -A && git -C "$repo10b" commit -q -m "reviewer should never do this"
set +e
out="$(python3 "$SHIP" reviewer-readonly-check --repo "$repo10b" --expected-head "$head10b")"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase10-case-5b: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"reviewer_mutation_detected\""
rm -rf "$repo10b"
echo "PASS phase10-case-5-reviewer-mutation-detected"

echo "--- CASE 6: reviewer findings surface to the actor correction path ---"
out="$(python3 "$SHIP" correction-prompt --goal "add a subtract helper" \
  --dod '{"present":true,"evidence":["add a subtract helper"]}' --risk SAFE \
  --verdict "$fail_verdict")"
assert_field "$out" "ok" "true"
prompt_text="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['prompt'])" "$out")"
case "$prompt_text" in
  *"sub() adds instead of subtracting"*) ;;
  *) echo "FAIL phase10-case-6: correction prompt did not surface the reviewer's finding" >&2; exit 1 ;;
esac
case "$prompt_text" in
  *"unrelated refactor"*) ;;
  *) echo "FAIL phase10-case-6: correction prompt missing scope-discipline instruction" >&2; exit 1 ;;
esac
echo "PASS phase10-case-6-findings-surfaced-to-correction-prompt"

echo "--- CASE 7: corrected actor output + FRESH reviewer PASS -> continue (same code path as case 1, no special-cased 'round 2' state) ---"
repo10c="$(mktemp -d)"; make_repo "$repo10c"
out="$(python3 "$SHIP" review-verdict --verdict "$pass_verdict")"
assert_field "$out" "ok" "true"
out="$(python3 "$SHIP" gate --repo "$repo10c" --files '["app/main.py"]' \
  --dod '{"present":true,"evidence":["add a subtract helper"]}' \
  --verification '{"present":true,"evidence":["ran app/test_main.sh after correction"]}' \
  --risk SAFE)"
assert_field "$out" "ok" "true"
assert_field "$out" "gate_decision" "\"PASS\""
rm -rf "$repo10c"
echo "PASS phase10-case-7-corrected-output-fresh-reviewer-pass"

echo "--- CASE 8: reviewer PASS but Preflight BLOCKER/FAIL -> no PR ---"
repo10d="$(mktemp -d)"
echo "STRIPE_SECRET_KEY=whatever" > "$repo10d/.env"
out="$(python3 "$SHIP" review-verdict --verdict "$pass_verdict")"
assert_field "$out" "ok" "true"
set +e
out="$(python3 "$SHIP" gate --repo "$repo10d" --files '["app.py"]' \
  --dod '{"present":true,"evidence":["x"]}' \
  --verification '{"present":true,"evidence":["y"]}' \
  --risk SAFE)"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase10-case-8: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"gate_not_pass\""
rm -rf "$repo10d"
echo "PASS phase10-case-8-reviewer-pass-preflight-blocker-still-blocks"

echo "--- CASE 9: reviewer PASS but Diana Gate FAIL (non-preflight reason) -> no PR ---"
repo10e="$(mktemp -d)"; make_repo "$repo10e"
out="$(python3 "$SHIP" review-verdict --verdict "$pass_verdict")"
assert_field "$out" "ok" "true"
set +e
out="$(python3 "$SHIP" gate --repo "$repo10e" --files '["app/main.py"]' \
  --dod '{"present":true,"evidence":["x"]}' \
  --verification '{"present":false,"evidence":[]}' \
  --risk SAFE)"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase10-case-9: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"gate_not_pass\""
rm -rf "$repo10e"
echo "PASS phase10-case-9-reviewer-pass-gate-fail-still-blocks"

echo "--- CASE 10: reviewer PASS + verification PASS + Gate PASS -> ready/open PR path, STOP before merge ---"
repo10f="$(mktemp -d)"; make_repo "$repo10f"
out="$(python3 "$SHIP" review-verdict --verdict "$pass_verdict")"
assert_field "$out" "ok" "true"
out="$(python3 "$SHIP" gate --repo "$repo10f" --files '["app/main.py"]' \
  --dod '{"present":true,"evidence":["add a subtract helper"]}' \
  --verification '{"present":true,"evidence":["ran app/test_main.sh"]}' \
  --risk SAFE)"
assert_field "$out" "ok" "true"
gate_evidence10f="$(python3 -c "import json,sys; print(json.dumps(json.loads(sys.argv[1])['evidence']))" "$out")"
argv_file10f="$(mktemp)"
export FAKE_GH_ARGV_FILE="$argv_file10f"
out="$(python3 "$SHIP" open-pr --gate-evidence "$gate_evidence10f" \
  --head-branch worker --title "add subtract helper" --body-preamble "feature: add subtract helper" \
  --gh-bin "$SHIP_FIXTURES/fake-gh-create-ok")"
unset FAKE_GH_ARGV_FILE
assert_field "$out" "ok" "true"
assert_field "$out" "merged" "false"
rm -f "$argv_file10f"
rm -rf "$repo10f"
echo "PASS phase10-case-10-reviewer-pass-verification-pass-gate-pass-ready-pr"

echo "=== Foundation repair: production surfaces must not require AO's private approval API ==="
# Scoped to the actual production workflow instructions/code a session
# follows to run /diana-ship - NOT this test file itself (whose forbidden-
# pattern list below would otherwise self-match), NOT
# diana/adapters/README.md (which legitimately documents, as clearly-
# labeled history, that a private endpoint was used during past attended
# experiments), and NOT diana/memory/*.md (canonical project history). A
# private approval dependency belongs in none of the files below; if one
# appears, that is exactly the class of drift this guard exists to catch.
PRODUCTION_SURFACES=(
  "$REPO_ROOT/diana/commands/diana-ship.md"
  "$SHIP"
  "$SHIP_DIR/README.md"
)
FORBIDDEN_PATTERNS=(
  "/api/v1/"
  "decisionId"
  "allow_always"
  "127.0.0.1:3001"
  "localhost:3001"
)
for surface in "${PRODUCTION_SURFACES[@]}"; do
  for pattern in "${FORBIDDEN_PATTERNS[@]}"; do
    hits="$(grep -cF "$pattern" "$surface" || true)"
    if [ "$hits" -ne 0 ]; then
      echo "FAIL: $surface contains forbidden pattern '$pattern' (production workflow must not depend on AO's private approval API)" >&2
      exit 1
    fi
  done
done
# Positive check: the adapter's own README must still carry the
# repair's disclaimer distinguishing historical evidence from production
# instruction, rather than silently losing it in a future edit.
if ! grep -qF "must not be read as" "$REPO_ROOT/diana/adapters/README.md"; then
  echo "FAIL: diana/adapters/README.md is missing the historical-vs-production approval disclaimer" >&2
  exit 1
fi
echo "PASS production-surfaces-do-not-require-private-ao-approval-api"

echo "=== Phase 11: bounded multi-worker orchestration ==="

valid_plan='{
  "goal": "add os-cruft preflight check",
  "risk": "SAFE",
  "dod": {"present": true, "evidence": ["add os-cruft-files-committed check"]},
  "workers": [
    {"id": "implementation", "role": "actor", "allowed_files": ["diana/preflight/preflight.py"]},
    {"id": "tests", "role": "actor", "allowed_files": ["diana/preflight/test-preflight.sh", "diana/preflight/fixtures/os-cruft-present.json"]}
  ]
}'

echo "--- CASE 1: two valid disjoint worker scopes -> plan PASS ---"
out="$(python3 "$SHIP" plan-validate --plan "$valid_plan")"
assert_field "$out" "ok" "true"
echo "PASS phase11-case-1-valid-plan"

echo "--- CASE 2: overlapping allowed-file scopes -> FAIL before workers ---"
overlapping_plan='{
  "goal": "x", "risk": "SAFE",
  "dod": {"present": true, "evidence": ["x"]},
  "workers": [
    {"id": "a", "role": "actor", "allowed_files": ["diana/preflight/preflight.py"]},
    {"id": "b", "role": "actor", "allowed_files": ["diana/preflight/preflight.py"]}
  ]
}'
set +e
out="$(python3 "$SHIP" plan-validate --plan "$overlapping_plan")"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase11-case-2: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"overlapping_scopes\""
echo "PASS phase11-case-2-overlapping-scopes-fail-before-spawn"

echo "--- CASE 3: Worker A edits outside its scope -> FAIL ---"
repo11a="$(mktemp -d)"; make_repo "$repo11a"
base11a="$(git -C "$repo11a" rev-parse HEAD)"
git -C "$repo11a" switch -q -c worker-a
echo "unexpected" > "$repo11a/out-of-scope.txt"
git -C "$repo11a" add -A && git -C "$repo11a" commit -q -m "worker a oops"
workerA11a="$(git -C "$repo11a" rev-parse HEAD)"
set +e
out="$(python3 "$SHIP" verify-diff --repo "$repo11a" --base "$base11a" --worker-ref "$workerA11a" --allowed-files '["app/main.py"]')"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase11-case-3: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"out_of_scope_diff\""
rm -rf "$repo11a"
echo "PASS phase11-case-3-worker-a-out-of-scope"

echo "--- CASE 4: Worker B edits outside its scope -> FAIL ---"
repo11b="$(mktemp -d)"; make_repo "$repo11b"
base11b="$(git -C "$repo11b" rev-parse HEAD)"
git -C "$repo11b" switch -q -c worker-b
echo "unexpected" > "$repo11b/out-of-scope-b.txt"
git -C "$repo11b" add -A && git -C "$repo11b" commit -q -m "worker b oops"
workerB11b="$(git -C "$repo11b" rev-parse HEAD)"
set +e
out="$(python3 "$SHIP" verify-diff --repo "$repo11b" --base "$base11b" --worker-ref "$workerB11b" --allowed-files '["app/test_main.sh"]')"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase11-case-4: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"out_of_scope_diff\""
rm -rf "$repo11b"
echo "PASS phase11-case-4-worker-b-out-of-scope"

echo "--- CASE 5: actual changed-file intersection non-empty -> FAIL ---"
set +e
out="$(python3 "$SHIP" cross-worker-check --files-a '["diana/preflight/preflight.py","shared.txt"]' --files-b '["shared.txt","diana/preflight/test-preflight.sh"]')"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase11-case-5: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"overlapping_changed_files\""
echo "PASS phase11-case-5-changed-file-intersection-nonempty"

echo "--- CASE 6: one worker fails (produces no commit) -> no integration/no PR ---"
repo11f="$(mktemp -d)"; make_repo "$repo11f"
base11f="$(git -C "$repo11f" rev-parse HEAD)"
set +e
out="$(python3 "$SHIP" verify-diff --repo "$repo11f" --base "$base11f" --worker-ref "$base11f" --allowed-files '["app/main.py"]')"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase11-case-6: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"empty_diff\""
rm -rf "$repo11f"
echo "PASS phase11-case-6-one-worker-fails-no-integration"

echo "--- CASE 7: integration conflict -> FAIL CLOSED ---"
repo11g="$(mktemp -d)"
git init -q "$repo11g"
git -C "$repo11g" config user.email test@test.com; git -C "$repo11g" config user.name test
echo "base" > "$repo11g/shared.txt"
git -C "$repo11g" add -A && git -C "$repo11g" commit -q -m base
base11g="$(git -C "$repo11g" rev-parse HEAD)"
git -C "$repo11g" switch -q -c worker-a-conflict
echo "a-version" > "$repo11g/shared.txt"
git -C "$repo11g" commit -q -am a
git -C "$repo11g" switch -q -c worker-b-conflict "$base11g"
echo "b-version" > "$repo11g/shared.txt"
git -C "$repo11g" commit -q -am b
integration_wt11g="$(mktemp -u)"
set +e
out="$(python3 "$SHIP" integrate --repo "$repo11g" --base "$base11g" \
  --branch-a worker-a-conflict --branch-b worker-b-conflict \
  --integration-branch diana/phase11-test-integration-conflict --worktree-path "$integration_wt11g")"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase11-case-7: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"integration_conflict\""
git -C "$repo11g" worktree remove "$integration_wt11g" --force 2>/dev/null || true
rm -rf "$repo11g" "$integration_wt11g"
echo "PASS phase11-case-7-integration-conflict-fails-closed"

echo "--- CASE 8: A+B integrate cleanly -> combined verification permitted ---"
repo11h="$(mktemp -d)"; make_repo "$repo11h"
base11h="$(git -C "$repo11h" rev-parse HEAD)"
git -C "$repo11h" switch -q -c worker-a-clean
echo "def sub(a, b): return a - b" >> "$repo11h/app/main.py"
git -C "$repo11h" commit -q -am "worker a: implementation"
git -C "$repo11h" switch -q -c worker-b-clean "$base11h"
echo "echo more tests" >> "$repo11h/app/test_main.sh"
git -C "$repo11h" commit -q -am "worker b: tests"
integration_wt11h="$(mktemp -u)"
out="$(python3 "$SHIP" integrate --repo "$repo11h" --base "$base11h" \
  --branch-a worker-a-clean --branch-b worker-b-clean \
  --integration-branch diana/phase11-test-integration-clean --worktree-path "$integration_wt11h")"
assert_field "$out" "ok" "true"
integrated_commit11h="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['commit'])" "$out")"
grep -q "def sub" "$integration_wt11h/app/main.py"
grep -q "more tests" "$integration_wt11h/app/test_main.sh"
git -C "$repo11h" worktree remove "$integration_wt11h" --force 2>/dev/null || true
rm -rf "$repo11h" "$integration_wt11h"
echo "PASS phase11-case-8-clean-integration-contains-both-outputs"

echo "--- CASE 9: Reviewer C FAIL -> no Gate/PR ---"
set +e
out="$(python3 "$SHIP" review-verdict --verdict '{"decision":"FAIL","summary":"integration does not satisfy the DoD","findings":[{"severity":"BLOCKER","description":"tests do not cover the new check","evidence":"test-preflight.sh unchanged"}],"dod_checks":[{"criterion":"add os-cruft-files-committed check","result":"FAIL","evidence":"no test coverage"}]}')"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase11-case-9: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"review_failed\""
echo "PASS phase11-case-9-reviewer-fail-blocks-gate"

echo "--- CASE 10: reviewer mutation detected -> FAIL ---"
repo11i="$(mktemp -d)"; make_repo "$repo11i"
head11i="$(git -C "$repo11i" rev-parse HEAD)"
echo "reviewer c should never write this" > "$repo11i/reviewer-c-tampered.txt"
set +e
out="$(python3 "$SHIP" reviewer-readonly-check --repo "$repo11i" --expected-head "$head11i")"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase11-case-10: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"reviewer_mutation_detected\""
rm -rf "$repo11i"
echo "PASS phase11-case-10-reviewer-c-mutation-detected"

echo "--- CASE 11: reviewer PASS but Preflight BLOCKER/FAIL -> no PR ---"
repo11j="$(mktemp -d)"
echo "STRIPE_SECRET_KEY=whatever" > "$repo11j/.env"
set +e
out="$(python3 "$SHIP" gate --repo "$repo11j" --files '["app.py"]' \
  --dod '{"present":true,"evidence":["x"]}' \
  --verification '{"present":true,"evidence":["y"]}' \
  --risk SAFE)"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase11-case-11: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"gate_not_pass\""
rm -rf "$repo11j"
echo "PASS phase11-case-11-reviewer-pass-preflight-blocker-still-blocks"

echo "--- CASE 12: reviewer PASS but Diana Gate FAIL (non-preflight reason) -> no PR ---"
repo11k="$(mktemp -d)"; make_repo "$repo11k"
set +e
out="$(python3 "$SHIP" gate --repo "$repo11k" --files '["app/main.py"]' \
  --dod '{"present":true,"evidence":["x"]}' \
  --verification '{"present":false,"evidence":[]}' \
  --risk SAFE)"
status=$?
set -e
[ "$status" -eq 1 ] || { echo "FAIL phase11-case-12: expected exit 1, got $status" >&2; exit 1; }
assert_field "$out" "ok" "false"
assert_field "$out" "error" "\"gate_not_pass\""
rm -rf "$repo11k"
echo "PASS phase11-case-12-reviewer-pass-gate-fail-still-blocks"

echo "--- CASE 13: clean A/B + clean integration + reviewer PASS + verification PASS + Gate PASS -> ready/open PR, STOP before merge ---"
repo11l="$(mktemp -d)"; make_repo "$repo11l"
base11l="$(git -C "$repo11l" rev-parse HEAD)"
git -C "$repo11l" switch -q -c worker-a-final
echo "def sub(a, b): return a - b" >> "$repo11l/app/main.py"
git -C "$repo11l" commit -q -am "worker a: implementation"
workerA11l="$(git -C "$repo11l" rev-parse HEAD)"
git -C "$repo11l" switch -q -c worker-b-final "$base11l"
echo "echo more tests" >> "$repo11l/app/test_main.sh"
git -C "$repo11l" commit -q -am "worker b: tests"
workerB11l="$(git -C "$repo11l" rev-parse HEAD)"

out="$(python3 "$SHIP" verify-diff --repo "$repo11l" --base "$base11l" --worker-ref "$workerA11l" --allowed-files '["app/main.py"]')"
assert_field "$out" "ok" "true"
filesA11l="$(python3 -c "import json,sys; print(json.dumps(json.loads(sys.argv[1])['files']))" "$out")"

out="$(python3 "$SHIP" verify-diff --repo "$repo11l" --base "$base11l" --worker-ref "$workerB11l" --allowed-files '["app/test_main.sh"]')"
assert_field "$out" "ok" "true"
filesB11l="$(python3 -c "import json,sys; print(json.dumps(json.loads(sys.argv[1])['files']))" "$out")"

out="$(python3 "$SHIP" cross-worker-check --files-a "$filesA11l" --files-b "$filesB11l")"
assert_field "$out" "ok" "true"

integration_wt11l="$(mktemp -u)"
out="$(python3 "$SHIP" integrate --repo "$repo11l" --base "$base11l" \
  --branch-a worker-a-final --branch-b worker-b-final \
  --integration-branch diana/phase11-test-integration-final --worktree-path "$integration_wt11l")"
assert_field "$out" "ok" "true"

out="$(python3 "$SHIP" review-verdict --verdict '{"decision":"PASS","summary":"integration satisfies the DoD","findings":[],"dod_checks":[{"criterion":"add subtract helper with test coverage","result":"PASS","evidence":"both changes present and consistent"}]}')"
assert_field "$out" "ok" "true"

out="$(python3 "$SHIP" gate --repo "$integration_wt11l" --files "$(python3 -c "import json; print(json.dumps(json.loads('$filesA11l') + json.loads('$filesB11l')))")" \
  --dod '{"present":true,"evidence":["add subtract helper with test coverage"]}' \
  --verification '{"present":true,"evidence":["ran integrated app/test_main.sh"]}' \
  --risk SAFE)"
assert_field "$out" "ok" "true"
assert_field "$out" "gate_decision" "\"PASS\""
gate_evidence11l="$(python3 -c "import json,sys; print(json.dumps(json.loads(sys.argv[1])['evidence']))" "$out")"

argv_file11l="$(mktemp)"
export FAKE_GH_ARGV_FILE="$argv_file11l"
out="$(python3 "$SHIP" open-pr --gate-evidence "$gate_evidence11l" \
  --head-branch diana/phase11-test-integration-final --title "add subtract helper + tests" \
  --body-preamble "multi-worker feature: add subtract helper with test coverage" \
  --gh-bin "$SHIP_FIXTURES/fake-gh-create-ok")"
unset FAKE_GH_ARGV_FILE
assert_field "$out" "ok" "true"
assert_field "$out" "merged" "false"
rm -f "$argv_file11l"
git -C "$repo11l" worktree remove "$integration_wt11l" --force 2>/dev/null || true
rm -rf "$repo11l" "$integration_wt11l"
echo "PASS phase11-case-13-ready-pr-path-stop-before-merge"

echo "All Diana ship workflow tests passed."
