#!/usr/bin/env bash
set -euo pipefail

REVIEWER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEC_DIR="$(dirname "$REVIEWER_DIR")"
CATALOG="$SEC_DIR/catalog.json"
EVIDENCE_MODEL="$SEC_DIR/evidence_model.py"
FIXTURES="$REVIEWER_DIR/fixtures"
NORMALIZER="$REVIEWER_DIR/reviewer_normalizer.py"
EXPECTED="$FIXTURES/expected-target.json"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass_count=0
fail_count=0

pass() { echo "PASS: $1"; pass_count=$((pass_count + 1)); }
fail() { echo "FAIL: $1" >&2; fail_count=$((fail_count + 1)); }

run_normalizer() {
  # run_normalizer <artifact-path-or-'-'> <expected-path-or-'-'> <control_id...>
  python3 "$NORMALIZER" "$CATALOG" "$@" > "$TMP_DIR/norm_out.json"
}

normalizer_runs() {
  python3 -c "
import json, sys
print(json.dumps(json.load(open(sys.argv[1]))['runs']))
" "$1"
}

run_verifier_type() {
  python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
for r in d['runs']:
    if r['control_id'] == sys.argv[2]:
        print(r['verifier']['type'])
        sys.exit(0)
print('NONE')
" "$1" "$2"
}

evaluate_runs() {
  local runs_json_array="$1" out_file="$2"
  echo "$runs_json_array" > "$TMP_DIR/runs_in.json"
  python3 "$EVIDENCE_MODEL" "$CATALOG" "$TMP_DIR/runs_in.json" > "$out_file"
}

result_for() {
  python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
for r in d['results']:
    if r['control_id'] == sys.argv[2]:
        print(r['result'])
        sys.exit(0)
print('MISSING')
" "$1" "$2"
}

check_aggregate() {
  # check_aggregate <name> <artifact> <expected> <expected_result> <control_id>
  local name="$1" artifact="$2" expected="$3" expected_result="$4" control_id="$5"
  run_normalizer "$artifact" "$expected" "$control_id"
  local runs
  runs="$(normalizer_runs "$TMP_DIR/norm_out.json")"
  evaluate_runs "$runs" "$TMP_DIR/agg.out.json"
  local r
  r="$(result_for "$TMP_DIR/agg.out.json" "$control_id")"
  if [ "$r" = "$expected_result" ]; then
    pass "$name -> $expected_result"
  else
    fail "$name (expected $expected_result for $control_id, got $r)"
    cat "$TMP_DIR/norm_out.json" >&2
  fi
}

# check_aggregate_with_companion <name> <artifact> <expected> <control_id>
#   <companion_requirement> <companion_verifier_type>
#
# Proves "semantic evidence can complement dynamic/static evidence
# correctly": the reviewer's own contribution alone only ever covers ONE
# of a control's two required_evidence items (by design, same discipline
# as Phase 2/3), so reaching full aggregate PASS needs a second,
# complementary contribution -- exactly the same composition pattern
# already proven in Phase 2 (Gitleaks+static-analyzer) and Phase 3
# (dynamic scenario+semantic/static companion).
check_aggregate_with_companion() {
  local name="$1" artifact="$2" expected="$3" control_id="$4" companion_req="$5" companion_type="$6"
  run_normalizer "$artifact" "$expected" "$control_id"
  local runs
  runs="$(normalizer_runs "$TMP_DIR/norm_out.json")"
  python3 -c "
import json
reviewer_runs = json.loads('''$runs''')
companion = {
    'control_id': '$control_id',
    'applicability': 'APPLICABLE',
    'verifier': {'type': '$companion_type', 'identity': 'companion::${control_id}-other-requirement'},
    'evidence': [
        {'requirement': '''$companion_req''', 'status': 'SATISFIED', 'provenance': 'synthetic companion contribution for test composition'}
    ],
    'tool_error': None,
}
json.dump(reviewer_runs + [companion], open('$TMP_DIR/combined.json', 'w'))
"
  python3 "$EVIDENCE_MODEL" "$CATALOG" "$TMP_DIR/combined.json" > "$TMP_DIR/agg.out.json"
  local r
  r="$(result_for "$TMP_DIR/agg.out.json" "$control_id")"
  if [ "$r" = "PASS" ]; then
    pass "$name -> PASS (semantic contribution + companion)"
  else
    fail "$name (expected PASS for $control_id after adding companion, got $r)"
    cat "$TMP_DIR/norm_out.json" >&2
  fi
}

# ==================================================================
# Required independence tests
# ==================================================================

SEC001_REQ2="cross-account negative access test (user A cannot access user B's object by id)"
check_aggregate_with_companion "CASE 1: substantiated PASS composes with dynamic companion" \
  "$FIXTURES/case-01-sec001-pass-substantiated.json" "$EXPECTED" SEC-001 "$SEC001_REQ2" DYNAMIC_API

check_aggregate "CASE 2: vague reviewer text cannot become PASS" \
  "$FIXTURES/case-02-vague-text-cannot-pass.json" "$EXPECTED" ERROR SEC-001

check_aggregate "CASE 3: missing evidence -> UNPROVEN" \
  "$FIXTURES/case-03-missing-evidence-unproven.json" "$EXPECTED" UNPROVEN SEC-001

check_aggregate "CASE 4: explicit concrete flaw -> FAIL" \
  "$FIXTURES/case-04-concrete-flaw-fail.json" "$EXPECTED" FAIL SEC-001

check_aggregate "CASE 5: malformed output -> ERROR" \
  "$FIXTURES/case-05-malformed.json" "$EXPECTED" ERROR SEC-001

check_aggregate "CASE 6: wrong target commit -> UNPROVEN (not attributed)" \
  "$FIXTURES/case-06-wrong-commit.json" "$EXPECTED" UNPROVEN SEC-001

check_aggregate "CASE 7a: invalid verifier_type value -> ERROR" \
  "$FIXTURES/case-07a-invalid-verifier-type.json" "$EXPECTED" ERROR SEC-001

check_aggregate "CASE 7b: unauthorized verifier capability for this control -> ERROR, never PASS" \
  "$FIXTURES/case-07b-control-does-not-permit-verifier-type.json" "$EXPECTED" ERROR SEC-001

check_aggregate "CASE 8a: AI model-refusal-alone cannot prove SEC-074" \
  "$FIXTURES/case-08a-ai-refusal-alone-not-proof.json" "$EXPECTED" ERROR SEC-074

SEC074_REQ2="negative test proving a tool call the model attempts outside the caller's authorization is rejected by the enforcement layer, not merely discouraged by a prompt"
check_aggregate_with_companion "CASE 8b: AI PASS with explicit external-enforcement flag composes" \
  "$FIXTURES/case-08b-ai-external-enforcement-pass.json" "$EXPECTED" SEC-074 "$SEC074_REQ2" DYNAMIC_API

check_aggregate "CASE 9: NOT_APPLICABLE result path" \
  "$FIXTURES/case-09-not-applicable.json" "$EXPECTED" NOT_APPLICABLE SEC-001

# ==================================================================
# Additional coverage: composition with non-DYNAMIC_API capabilities,
# and the human_judgment_required gate
# ==================================================================

SEC066_REQ2="runtime test with multiple identities proving one user's database session cannot read/write another user's rows"
check_aggregate_with_companion "CASE 10: SEC-066 semantic contribution composes with DYNAMIC_DB companion" \
  "$FIXTURES/case-10-sec066-semantic-satisfied.json" "$EXPECTED" SEC-066 "$SEC066_REQ2" DYNAMIC_DB

SEC043_REQ2="test proving an out-of-order or repeated abuse of the workflow does not bypass its business rule"
check_aggregate_with_companion "CASE 11: SEC-043 business-logic PASS satisfies human_judgment_required gate, composes with dynamic companion" \
  "$FIXTURES/case-11-sec043-business-logic-pass.json" "$EXPECTED" SEC-043 "$SEC043_REQ2" DYNAMIC_API

SEC061_REQ1="dependency provenance/integrity is checked (lockfile hash pinning, publisher reputation)"
check_aggregate_with_companion "CASE 12: SEC-061 dependency-trust PASS satisfies human_judgment_required gate, composes with DEPENDENCY_SCANNER companion" \
  "$FIXTURES/case-12-sec061-dependency-trust-pass.json" "$EXPECTED" SEC-061 "$SEC061_REQ1" DEPENDENCY_SCANNER

# Tool-missing: explicit UNPROVEN, tagged with the control's real
# catalog-permitted capability, never a fabricated one.
run_normalizer "$TMP_DIR/does-not-exist.json" "$EXPECTED" SEC-001
vt="$(run_verifier_type "$TMP_DIR/norm_out.json" SEC-001)"
runs="$(normalizer_runs "$TMP_DIR/norm_out.json")"
evaluate_runs "$runs" "$TMP_DIR/tool_missing.out.json"
r="$(result_for "$TMP_DIR/tool_missing.out.json" SEC-001)"
if [ "$vt" = "SEMANTIC_REVIEW" ] && [ "$r" = "UNPROVEN" ]; then
  pass "CASE 13: review artifact unavailable -> explicit UNPROVEN via SEMANTIC_REVIEW"
else
  fail "CASE 13: expected verifier_type=SEMANTIC_REVIEW and result=UNPROVEN, got verifier_type=$vt result=$r"
fi

# Catalog-derived authorization: controls the Phase 4 kickoff's own
# illustrative list names, but whose real catalog verification.modes do
# NOT include SEMANTIC_REVIEW/HUMAN, must be silently unauthorized here.
for control_id in SEC-040 SEC-062 SEC-067 SEC-070 SEC-071; do
  run_normalizer "$FIXTURES/case-01-sec001-pass-substantiated.json" "$EXPECTED" "$control_id"
  runs="$(normalizer_runs "$TMP_DIR/norm_out.json")"
  if [ "$runs" = "[]" ]; then
    pass "CASE 14 ($control_id): catalog-unauthorized control produces no contribution"
  else
    fail "CASE 14 ($control_id): expected empty runs (catalog modes don't permit SEMANTIC_REVIEW/HUMAN), got $runs"
  fi
done

echo ""
echo "diana/security/reviewer/test-reviewer.sh: $pass_count passed, $fail_count failed"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
