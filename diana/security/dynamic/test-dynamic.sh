#!/usr/bin/env bash
set -euo pipefail

DYNAMIC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEC_DIR="$(dirname "$DYNAMIC_DIR")"
CATALOG="$SEC_DIR/catalog.json"
EVIDENCE_MODEL="$SEC_DIR/evidence_model.py"
FIXTURES="$DYNAMIC_DIR/fixtures"
NORMALIZER="$DYNAMIC_DIR/dynamic_normalizer.py"
EXPECTED="$FIXTURES/expected-target.json"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass_count=0
fail_count=0

pass() { echo "PASS: $1"; pass_count=$((pass_count + 1)); }
fail() { echo "FAIL: $1" >&2; fail_count=$((fail_count + 1)); }

run_normalizer() {
  # run_normalizer <artifact-path-or-'-'> <expected-path-or-'-'> <control_id...>
  python3 "$NORMALIZER" "$@" > "$TMP_DIR/norm_out.json"
}

normalizer_runs() {
  python3 -c "
import json, sys
print(json.dumps(json.load(open(sys.argv[1]))['runs']))
" "$1"
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
# Every one of these 17 scenario controls has TWO required_evidence items
# in the catalog; each dynamic scenario here only ever covers the one that
# is inherently a runtime/negative-test claim (by design -- see
# scenarios.py). Reaching full aggregate PASS therefore requires a second,
# complementary contribution for the control's other (typically semantic/
# static) requirement, exactly as Phase 2's Gitleaks+static-analyzer
# combination test demonstrated for SEC-007. This helper adds that
# synthetic companion run (representing a verifier this phase does not
# implement) so the test proves real end-to-end PASS composition, not
# just "this one scenario's own contribution was SATISFIED."
check_aggregate_with_companion() {
  local name="$1" artifact="$2" expected="$3" control_id="$4" companion_req="$5" companion_type="$6"
  run_normalizer "$artifact" "$expected" "$control_id"
  local runs
  runs="$(normalizer_runs "$TMP_DIR/norm_out.json")"
  python3 -c "
import json
dynamic_runs = json.loads('''$runs''')
companion = {
    'control_id': '$control_id',
    'applicability': 'APPLICABLE',
    'verifier': {'type': '$companion_type', 'identity': 'companion::${control_id}-other-requirement'},
    'evidence': [
        {'requirement': '''$companion_req''', 'status': 'SATISFIED', 'provenance': 'synthetic companion contribution for test composition'}
    ],
    'tool_error': None,
}
json.dump(dynamic_runs + [companion], open('$TMP_DIR/combined.json', 'w'))
"
  python3 "$EVIDENCE_MODEL" "$CATALOG" "$TMP_DIR/combined.json" > "$TMP_DIR/agg.out.json"
  local r
  r="$(result_for "$TMP_DIR/agg.out.json" "$control_id")"
  if [ "$r" = "PASS" ]; then
    pass "$name -> PASS (dynamic contribution + companion)"
  else
    fail "$name (expected PASS for $control_id after adding companion, got $r)"
    cat "$TMP_DIR/norm_out.json" >&2
  fi
}

# ==================================================================
# CASE A-Y (required), plus Z1-Z4 bonus safety-invariant coverage
# ==================================================================

check_aggregate "CASE A: production environment refused" \
  "$FIXTURES/case-a-prod-refused.json" "$EXPECTED" ERROR SEC-003

check_aggregate "CASE B: unknown external environment refused" \
  "$FIXTURES/case-b-unknown-env-refused.json" "$EXPECTED" ERROR SEC-003

SEC003_OTHER_REQ="every sensitive endpoint requires a valid authenticated session/token"
for env_name in local test sandbox; do
  check_aggregate_with_companion "CASE C ($env_name): LOCAL/TEST/SANDBOX accepted" \
    "$FIXTURES/case-c-env-${env_name}-accepted.json" "$EXPECTED" SEC-003 "$SEC003_OTHER_REQ" SEMANTIC_REVIEW
done

check_aggregate "CASE D: wrong target/build cannot PASS" \
  "$FIXTURES/case-d-wrong-target.json" "$EXPECTED" UNPROVEN SEC-003

check_aggregate "CASE E: skipped assertion -> UNPROVEN" \
  "$FIXTURES/case-e-skipped-assertion.json" "$EXPECTED" UNPROVEN SEC-003

check_aggregate "CASE F: execution crash -> ERROR" \
  "$FIXTURES/case-f-execution-crash.json" "$EXPECTED" ERROR SEC-003

SEC001_OTHER_REQ="server-side object ownership/permission check exists on every read/write route"
check_aggregate_with_companion "CASE G: authorization denied + state unchanged -> PASS" \
  "$FIXTURES/case-g-bola-denied-state-unchanged.json" "$EXPECTED" SEC-001 "$SEC001_OTHER_REQ" SEMANTIC_REVIEW

check_aggregate "CASE H: unauthorized action succeeds -> FAIL" \
  "$FIXTURES/case-h-privileged-action-allowed.json" "$EXPECTED" FAIL SEC-002

check_aggregate "CASE I: cross-account test requires distinct identities" \
  "$FIXTURES/case-i-missing-distinct-identity.json" "$EXPECTED" ERROR SEC-001

check_aggregate_with_companion "CASE J: anonymous protected endpoint test" \
  "$FIXTURES/case-j-anonymous-endpoint-denied.json" "$EXPECTED" SEC-003 "$SEC003_OTHER_REQ" SEMANTIC_REVIEW

SEC012_OTHER_REQ="stored user content is output-encoded or sanitized at render time"
check_aggregate_with_companion "CASE K: safe browser hostile input -> PASS" \
  "$FIXTURES/case-k-stored-xss-inert.json" "$EXPECTED" SEC-012 "$SEC012_OTHER_REQ" STATIC_ANALYZER

check_aggregate "CASE L: browser code execution detected -> FAIL" \
  "$FIXTURES/case-l-stored-xss-executed.json" "$EXPECTED" FAIL SEC-012

SEC067_OTHER_REQ="every data access path filters by the caller's tenant, enforced server-side or at the database layer"
check_aggregate_with_companion "CASE M: DB cross-tenant denied -> PASS" \
  "$FIXTURES/case-m-cross-tenant-denied.json" "$EXPECTED" SEC-067 "$SEC067_OTHER_REQ" DYNAMIC_API

check_aggregate "CASE N: cross-tenant succeeds -> FAIL" \
  "$FIXTURES/case-n-cross-tenant-allowed.json" "$EXPECTED" FAIL SEC-067

SEC068_OTHER_REQ="inbound webhook handler verifies the provider's signature before processing the payload"
check_aggregate_with_companion "CASE O: invalid webhook signature rejected -> PASS" \
  "$FIXTURES/case-o-webhook-signature-rejected.json" "$EXPECTED" SEC-068 "$SEC068_OTHER_REQ" SEMANTIC_REVIEW

check_aggregate "CASE P: duplicate webhook creates duplicate side effect -> FAIL" \
  "$FIXTURES/case-p-webhook-replay-duplicated.json" "$EXPECTED" FAIL SEC-069

SEC070_OTHER_REQ="the charged amount/price is computed and verified server-side from trusted data, never trusted from a client-supplied value"
check_aggregate_with_companion "CASE Q: price tampering rejected/recalculated -> PASS" \
  "$FIXTURES/case-q-price-tampering-rejected.json" "$EXPECTED" SEC-070 "$SEC070_OTHER_REQ" DYNAMIC_API

SEC071_OTHER_REQ="entitlement is granted only after the provider webhook signature and event state are verified server-side"
check_aggregate_with_companion "CASE R: client-only entitlement cannot grant privilege" \
  "$FIXTURES/case-r-forged-event-no-entitlement.json" "$EXPECTED" SEC-071 "$SEC071_OTHER_REQ" DYNAMIC_API

check_aggregate "CASE S: bounded concurrency invariant -> PASS" \
  "$FIXTURES/case-s-concurrency-invariant-held.json" "$EXPECTED" PASS SEC-044

check_aggregate "CASE T: race violation -> FAIL" \
  "$FIXTURES/case-t-concurrency-invariant-violated.json" "$EXPECTED" FAIL SEC-044

# CASE U: sandbox fixture unavailable -> UNPROVEN
check_aggregate "CASE U: sandbox fixture unavailable -> UNPROVEN" \
  "$TMP_DIR/does-not-exist.json" "$EXPECTED" UNPROVEN SEC-070

check_aggregate "CASE V: artifact corruption -> ERROR" \
  "$FIXTURES/case-v-artifact-corrupted.json" "$EXPECTED" ERROR SEC-003

# CASE W: cleanup failure visible. The dynamic contribution alone stays
# UNPROVEN (SEC-001 needs a 2nd, complementary requirement too -- same as
# every other multi-item control here), but the cleanup failure must be
# visible in its evidence provenance regardless of aggregate result --
# "do not hide it" is checked directly against the normalizer's own
# output, independent of whatever the rest of the contract ends up doing.
run_normalizer "$FIXTURES/case-w-cleanup-failure-visible.json" "$EXPECTED" SEC-001
runs="$(normalizer_runs "$TMP_DIR/norm_out.json")"
if echo "$runs" | grep -q '"status": "SATISFIED"' && echo "$runs" | grep -q "cleanup visibility" && echo "$runs" | grep -q "success=False"; then
  pass "CASE W: cleanup failure visible in evidence, scenario contribution still SATISFIED"
else
  fail "CASE W: expected SATISFIED contribution with cleanup failure visible in provenance, got: $runs"
fi

SEC074_OTHER_REQ="tool-call authorization is enforced by code outside the model, using the caller's real permissions, not the model's own output"
check_aggregate_with_companion "CASE X: AI forbidden action rejected outside model -> PASS" \
  "$FIXTURES/case-x-ai-tool-call-rejected.json" "$EXPECTED" SEC-074 "$SEC074_OTHER_REQ" SEMANTIC_REVIEW

SEC075_OTHER_REQ="untrusted content is isolated from trusted instructions and cannot itself grant tool permissions"
check_aggregate_with_companion "CASE Y: prompt injection cannot bypass external authorization -> PASS" \
  "$FIXTURES/case-y-prompt-injection-blocked.json" "$EXPECTED" SEC-075 "$SEC075_OTHER_REQ" SEMANTIC_REVIEW

# ==================================================================
# Bonus: safety invariants this framework enforces, each proven once
# ==================================================================

check_aggregate "CASE Z1: AI model-refusal-alone is not sufficient evidence" \
  "$FIXTURES/case-z1-ai-model-refusal-alone-not-proof.json" "$EXPECTED" ERROR SEC-074

check_aggregate "CASE Z2: DB scenario forbids privileged/admin bypass credentials" \
  "$FIXTURES/case-z2-db-privileged-bypass-forbidden.json" "$EXPECTED" ERROR SEC-066

check_aggregate "CASE Z3: concurrency ceiling enforced (never load testing)" \
  "$FIXTURES/case-z3-concurrency-ceiling-exceeded.json" "$EXPECTED" ERROR SEC-044

check_aggregate "CASE Z4: unknown scenario.id fails closed" \
  "$FIXTURES/case-z4-unknown-scenario-id.json" "$EXPECTED" ERROR SEC-003

# Not authorized / not requested control -> silently no contribution.
run_normalizer "$FIXTURES/case-j-anonymous-endpoint-denied.json" "$EXPECTED" SEC-999
runs="$(normalizer_runs "$TMP_DIR/norm_out.json")"
[ "$runs" = "[]" ] && pass "CASE Z5: unregistered control_id produces no contribution" \
  || fail "CASE Z5: expected empty runs, got $runs"

echo ""
echo "diana/security/dynamic/test-dynamic.sh: $pass_count passed, $fail_count failed"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
