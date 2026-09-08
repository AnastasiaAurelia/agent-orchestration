#!/usr/bin/env bash
set -euo pipefail

DYNAMIC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEC_DIR="$(dirname "$DYNAMIC_DIR")"
CATALOG="$SEC_DIR/catalog.json"
EVIDENCE_MODEL="$SEC_DIR/evidence_model.py"
FIXTURES="$DYNAMIC_DIR/fixtures"
NORMALIZER="$DYNAMIC_DIR/dynamic_normalizer.py"

EXPECTED_TEST="$FIXTURES/expected-context-test.json"
EXPECTED_LOCAL="$FIXTURES/expected-context-local.json"
EXPECTED_SANDBOX="$FIXTURES/expected-context-sandbox.json"
EXPECTED_NO_ENV="$FIXTURES/expected-context-no-environment.json"

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

run_verifier_type() {
  # verifier.type of the (only) run for <control_id> in the normalizer's
  # raw output, or NONE if absent.
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
# CASE A-Y (required), plus Z1-Z5 and E1-E11 correction-round coverage
# ==================================================================

check_aggregate "CASE A: production environment refused" \
  "$FIXTURES/case-a-prod-refused.json" "$EXPECTED_TEST" ERROR SEC-003

check_aggregate "CASE B: unknown external environment refused" \
  "$FIXTURES/case-b-unknown-env-refused.json" "$EXPECTED_TEST" ERROR SEC-003

SEC003_OTHER_REQ="every sensitive endpoint requires a valid authenticated session/token"
check_aggregate_with_companion "CASE C (local): LOCAL/TEST/SANDBOX accepted" \
  "$FIXTURES/case-c-env-local-accepted.json" "$EXPECTED_LOCAL" SEC-003 "$SEC003_OTHER_REQ" SEMANTIC_REVIEW
check_aggregate_with_companion "CASE C (test): LOCAL/TEST/SANDBOX accepted" \
  "$FIXTURES/case-c-env-test-accepted.json" "$EXPECTED_TEST" SEC-003 "$SEC003_OTHER_REQ" SEMANTIC_REVIEW
check_aggregate_with_companion "CASE C (sandbox): LOCAL/TEST/SANDBOX accepted" \
  "$FIXTURES/case-c-env-sandbox-accepted.json" "$EXPECTED_SANDBOX" SEC-003 "$SEC003_OTHER_REQ" SEMANTIC_REVIEW

check_aggregate "CASE D: wrong target/build cannot PASS" \
  "$FIXTURES/case-d-wrong-target.json" "$EXPECTED_TEST" UNPROVEN SEC-003

check_aggregate "CASE E: skipped assertion -> UNPROVEN" \
  "$FIXTURES/case-e-skipped-assertion.json" "$EXPECTED_TEST" UNPROVEN SEC-003

check_aggregate "CASE F: execution crash -> ERROR" \
  "$FIXTURES/case-f-execution-crash.json" "$EXPECTED_TEST" ERROR SEC-003

SEC001_OTHER_REQ="server-side object ownership/permission check exists on every read/write route"
check_aggregate_with_companion "CASE G: authorization denied + state unchanged -> PASS" \
  "$FIXTURES/case-g-bola-denied-state-unchanged.json" "$EXPECTED_TEST" SEC-001 "$SEC001_OTHER_REQ" SEMANTIC_REVIEW

check_aggregate "CASE H: unauthorized action succeeds -> FAIL" \
  "$FIXTURES/case-h-privileged-action-allowed.json" "$EXPECTED_TEST" FAIL SEC-002

check_aggregate "CASE I: cross-account test requires distinct identities" \
  "$FIXTURES/case-i-missing-distinct-identity.json" "$EXPECTED_TEST" ERROR SEC-001

check_aggregate_with_companion "CASE J: anonymous protected endpoint test" \
  "$FIXTURES/case-j-anonymous-endpoint-denied.json" "$EXPECTED_TEST" SEC-003 "$SEC003_OTHER_REQ" SEMANTIC_REVIEW

SEC012_OTHER_REQ="stored user content is output-encoded or sanitized at render time"
check_aggregate_with_companion "CASE K: safe browser hostile input -> PASS" \
  "$FIXTURES/case-k-stored-xss-inert.json" "$EXPECTED_TEST" SEC-012 "$SEC012_OTHER_REQ" STATIC_ANALYZER

check_aggregate "CASE L: browser code execution detected -> FAIL" \
  "$FIXTURES/case-l-stored-xss-executed.json" "$EXPECTED_TEST" FAIL SEC-012

SEC067_OTHER_REQ="every data access path filters by the caller's tenant, enforced server-side or at the database layer"
check_aggregate_with_companion "CASE M: DB cross-tenant denied -> PASS" \
  "$FIXTURES/case-m-cross-tenant-denied.json" "$EXPECTED_TEST" SEC-067 "$SEC067_OTHER_REQ" DYNAMIC_API

check_aggregate "CASE N: cross-tenant succeeds -> FAIL" \
  "$FIXTURES/case-n-cross-tenant-allowed.json" "$EXPECTED_TEST" FAIL SEC-067

SEC068_OTHER_REQ="inbound webhook handler verifies the provider's signature before processing the payload"
check_aggregate_with_companion "CASE O: invalid webhook signature rejected -> PASS" \
  "$FIXTURES/case-o-webhook-signature-rejected.json" "$EXPECTED_TEST" SEC-068 "$SEC068_OTHER_REQ" SEMANTIC_REVIEW

check_aggregate "CASE P: duplicate webhook creates duplicate side effect -> FAIL" \
  "$FIXTURES/case-p-webhook-replay-duplicated.json" "$EXPECTED_TEST" FAIL SEC-069

SEC070_OTHER_REQ="the charged amount/price is computed and verified server-side from trusted data, never trusted from a client-supplied value"
check_aggregate_with_companion "CASE Q: price tampering rejected/recalculated -> PASS" \
  "$FIXTURES/case-q-price-tampering-rejected.json" "$EXPECTED_TEST" SEC-070 "$SEC070_OTHER_REQ" DYNAMIC_API

SEC071_OTHER_REQ="entitlement is granted only after the provider webhook signature and event state are verified server-side"
check_aggregate_with_companion "CASE R: client-only entitlement cannot grant privilege" \
  "$FIXTURES/case-r-forged-event-no-entitlement.json" "$EXPECTED_TEST" SEC-071 "$SEC071_OTHER_REQ" DYNAMIC_API

check_aggregate "CASE S: bounded concurrency invariant -> PASS" \
  "$FIXTURES/case-s-concurrency-invariant-held.json" "$EXPECTED_TEST" PASS SEC-044

check_aggregate "CASE T: race violation -> FAIL" \
  "$FIXTURES/case-t-concurrency-invariant-violated.json" "$EXPECTED_TEST" FAIL SEC-044

# CASE U: sandbox fixture unavailable -> UNPROVEN
check_aggregate "CASE U: sandbox fixture unavailable -> UNPROVEN" \
  "$TMP_DIR/does-not-exist.json" "$EXPECTED_TEST" UNPROVEN SEC-070

check_aggregate "CASE V: artifact corruption -> ERROR" \
  "$FIXTURES/case-v-artifact-corrupted.json" "$EXPECTED_TEST" ERROR SEC-003

SEC074_OTHER_REQ="tool-call authorization is enforced by code outside the model, using the caller's real permissions, not the model's own output"
check_aggregate_with_companion "CASE X: AI forbidden action rejected outside model -> PASS" \
  "$FIXTURES/case-x-ai-tool-call-rejected.json" "$EXPECTED_TEST" SEC-074 "$SEC074_OTHER_REQ" SEMANTIC_REVIEW

SEC075_OTHER_REQ="untrusted content is isolated from trusted instructions and cannot itself grant tool permissions"
check_aggregate_with_companion "CASE Y: prompt injection cannot bypass external authorization -> PASS" \
  "$FIXTURES/case-y-prompt-injection-blocked.json" "$EXPECTED_TEST" SEC-075 "$SEC075_OTHER_REQ" SEMANTIC_REVIEW

# ==================================================================
# Bonus: safety invariants proven since the first Phase 3 implementation
# ==================================================================

check_aggregate "CASE Z1: AI model-refusal-alone is not sufficient evidence" \
  "$FIXTURES/case-z1-ai-model-refusal-alone-not-proof.json" "$EXPECTED_TEST" ERROR SEC-074

check_aggregate "CASE Z2: DB scenario forbids privileged/admin bypass credentials" \
  "$FIXTURES/case-z2-db-privileged-bypass-forbidden.json" "$EXPECTED_TEST" ERROR SEC-066

check_aggregate "CASE Z3: concurrency ceiling enforced (never load testing)" \
  "$FIXTURES/case-z3-concurrency-ceiling-exceeded.json" "$EXPECTED_TEST" ERROR SEC-044

check_aggregate "CASE Z4: unknown scenario.id fails closed" \
  "$FIXTURES/case-z4-unknown-scenario-id.json" "$EXPECTED_TEST" ERROR SEC-003

# Not authorized / not requested control -> silently no contribution.
run_normalizer "$FIXTURES/case-j-anonymous-endpoint-denied.json" "$EXPECTED_TEST" SEC-999
runs="$(normalizer_runs "$TMP_DIR/norm_out.json")"
[ "$runs" = "[]" ] && pass "CASE Z5: unregistered control_id produces no contribution" \
  || fail "CASE Z5: expected empty runs, got $runs"

# ==================================================================
# Final dynamic trust-boundary correction (human review): E1-E11
# ==================================================================

# E1: expected environment TEST + artifact environment SANDBOX, with a
# real observed violation -> never PASS AND never FAIL. Environment
# mismatch blocks attribution exactly like a repo/commit mismatch does.
check_aggregate "E1: environment mismatch with a real violation -> never FAIL" \
  "$FIXTURES/case-e1-env-mismatch-with-violation.json" "$EXPECTED_TEST" UNPROVEN SEC-002

# E2: expected SANDBOX + artifact SANDBOX + matching repo/commit -> normal
# evidence path (SATISFIED reachable via companion, same as CASE C).
check_aggregate_with_companion "E2: matching SANDBOX expectation -> normal evidence path" \
  "$FIXTURES/case-c-env-sandbox-accepted.json" "$EXPECTED_SANDBOX" SEC-003 "$SEC003_OTHER_REQ" SEMANTIC_REVIEW

# E3: missing expected environment -> never PASS.
check_aggregate "E3: missing expected environment -> never PASS" \
  "$FIXTURES/case-j-anonymous-endpoint-denied.json" "$EXPECTED_NO_ENV" UNPROVEN SEC-003

# E4: SEC-012 artifact unavailable -> explicit UNPROVEN using the
# permitted DYNAMIC_BROWSER capability, never a fabricated DYNAMIC_API.
run_normalizer "$TMP_DIR/does-not-exist.json" "$EXPECTED_TEST" SEC-012
vt="$(run_verifier_type "$TMP_DIR/norm_out.json" SEC-012)"
runs="$(normalizer_runs "$TMP_DIR/norm_out.json")"
evaluate_runs "$runs" "$TMP_DIR/e4.out.json"
r="$(result_for "$TMP_DIR/e4.out.json" SEC-012)"
if [ "$vt" = "DYNAMIC_BROWSER" ] && [ "$r" = "UNPROVEN" ]; then
  pass "E4: SEC-012 unavailable -> UNPROVEN via DYNAMIC_BROWSER (not DYNAMIC_API)"
else
  fail "E4: expected verifier_type=DYNAMIC_BROWSER and result=UNPROVEN, got verifier_type=$vt result=$r"
fi

# E5: SEC-044 artifact unavailable -> DYNAMIC_CONCURRENCY.
run_normalizer "$TMP_DIR/does-not-exist.json" "$EXPECTED_TEST" SEC-044
vt="$(run_verifier_type "$TMP_DIR/norm_out.json" SEC-044)"
[ "$vt" = "DYNAMIC_CONCURRENCY" ] && pass "E5: SEC-044 unavailable -> UNPROVEN via DYNAMIC_CONCURRENCY (not DYNAMIC_API)" \
  || fail "E5: expected verifier_type=DYNAMIC_CONCURRENCY, got $vt"

# E6: SEC-066 artifact unavailable -> DYNAMIC_DB.
run_normalizer "$TMP_DIR/does-not-exist.json" "$EXPECTED_TEST" SEC-066
vt="$(run_verifier_type "$TMP_DIR/norm_out.json" SEC-066)"
[ "$vt" = "DYNAMIC_DB" ] && pass "E6: SEC-066 unavailable -> UNPROVEN via DYNAMIC_DB (not DYNAMIC_API)" \
  || fail "E6: expected verifier_type=DYNAMIC_DB, got $vt"

# E7: malformed artifact (fails structural validation before scenario
# identity is known), requested for SEC-012 (a non-DYNAMIC_API-only
# control) -> deterministic ERROR using SEC-012's own permitted
# capability, never a fabricated DYNAMIC_API.
run_normalizer "$FIXTURES/case-e7-malformed-non-dynamic-api-control.json" "$EXPECTED_TEST" SEC-012
vt="$(run_verifier_type "$TMP_DIR/norm_out.json" SEC-012)"
runs="$(normalizer_runs "$TMP_DIR/norm_out.json")"
evaluate_runs "$runs" "$TMP_DIR/e7.out.json"
r="$(result_for "$TMP_DIR/e7.out.json" SEC-012)"
if [ "$vt" = "DYNAMIC_BROWSER" ] && [ "$r" = "ERROR" ]; then
  pass "E7: malformed artifact for SEC-012 -> ERROR via DYNAMIC_BROWSER (no fabricated capability)"
else
  fail "E7: expected verifier_type=DYNAMIC_BROWSER and result=ERROR, got verifier_type=$vt result=$r"
fi

# E8: cleanup.required=true, performed=false -> ERROR / non-PASS.
check_aggregate "E8: cleanup required but not performed -> ERROR" \
  "$FIXTURES/case-e8-cleanup-required-not-performed.json" "$EXPECTED_TEST" ERROR SEC-001

# E9: cleanup.required=true, performed=true, success=false -> ERROR /
# non-PASS. Supersedes the original implementation's CASE W, which
# incorrectly allowed SATISFIED here; the cleanup failure must still be
# visible in the provenance text even though the result is now ERROR, not
# SATISFIED.
run_normalizer "$FIXTURES/case-e9-cleanup-performed-but-failed.json" "$EXPECTED_TEST" SEC-001
runs="$(normalizer_runs "$TMP_DIR/norm_out.json")"
evaluate_runs "$runs" "$TMP_DIR/e9.out.json"
r="$(result_for "$TMP_DIR/e9.out.json" SEC-001)"
if [ "$r" = "ERROR" ] && echo "$runs" | grep -q "cleanup" && echo "$runs" | grep -q "success=False"; then
  pass "E9: cleanup performed but failed -> ERROR, cleanup failure still visible in provenance"
else
  fail "E9: expected ERROR with cleanup failure visible in provenance, got result=$r runs=$runs"
fi

# E10: cleanup.required=true, performed=true, success=true -> normal
# result path (SATISFIED reachable, cleanup resolved).
check_aggregate_with_companion "E10: cleanup required and successfully resolved -> normal PASS path" \
  "$FIXTURES/case-e10-cleanup-required-and-resolved.json" "$EXPECTED_TEST" SEC-001 "$SEC001_OTHER_REQ" SEMANTIC_REVIEW

# E11: cleanup.required=false -> cleanup performed/success values are
# irrelevant; PASS still reachable.
check_aggregate_with_companion "E11: cleanup not required -> no cleanup failure regardless of performed/success" \
  "$FIXTURES/case-e11-cleanup-not-required.json" "$EXPECTED_TEST" SEC-001 "$SEC001_OTHER_REQ" SEMANTIC_REVIEW

# ==================================================================
# R1-R6: Security Track remediation round A -- 6 new scenarios
# (SEC-010, SEC-011, SEC-021, SEC-035, SEC-058, SEC-064)
# ==================================================================

build_scenario_artifact() {
  # build_scenario_artifact <out_file> <scenario_id> <control_id> <requirement> <assertion_id> <outcome>
  local out_file="$1" scenario_id="$2" control_id="$3" requirement="$4" assertion_id="$5" outcome="$6"
  python3 -c "
import hashlib, json

env = {
    'environment': 'TEST',
    'target': {
        'repository': 'AnastasiaAurelia/agent-orchestration',
        'commit': '6b855b85f5cdca683e1a332eddf40021c83781d5',
        'base_url': 'http://localhost:8080',
    },
    'scenario': {'id': '$scenario_id', 'version': '1.0.0'},
    'verifier_mode': 'DYNAMIC_API',
    'identities': [],
    'execution': {'completed': True},
    'assertions': [{'id': '$assertion_id', 'outcome': '$outcome'}],
    'cleanup': {'required': False, 'performed': False, 'success': True},
    'result': {'control_id': '$control_id', 'requirement': '''$requirement'''},
}
bound = {k: env[k] for k in ('environment', 'target', 'scenario', 'verifier_mode', 'identities', 'execution', 'assertions', 'cleanup', 'result')}
canonical = json.dumps(bound, sort_keys=True, separators=(',', ':'))
env['artifact_binding'] = {'sha256': hashlib.sha256(canonical.encode()).hexdigest()}
json.dump(env, open('$out_file', 'w'))
"
}

R_SCENARIOS=(
  "shell-metacharacter-payload-inert|SEC-010|negative test proving a shell metacharacter payload does not execute unintended commands|shell_metacharacter_payload_no_effect"
  "template-expression-payload-not-evaluated|SEC-011|negative test proving a template-expression payload is not evaluated|template_expression_payload_not_evaluated"
  "forged-none-algorithm-jwt-rejected|SEC-021|negative test proving a token with a forged/none-algorithm signature is rejected|forged_jwt_rejected"
  "ssrf-internal-address-request-rejected|SEC-035|negative test proving a request targeting an internal address is rejected|internal_address_request_rejected"
  "crafted-deserialization-payload-inert|SEC-058|negative test proving a crafted serialized payload does not achieve code execution or object injection|crafted_payload_no_code_execution"
  "sensitive-file-paths-not-fetchable|SEC-064|negative test proving common sensitive file paths (.env, .git/config, backup archives) are not publicly fetchable|sensitive_file_paths_not_fetchable"
)

for entry in "${R_SCENARIOS[@]}"; do
  IFS='|' read -r scenario_id control_id requirement assertion_id <<< "$entry"
  build_scenario_artifact "$TMP_DIR/r-${scenario_id}-passed.json" "$scenario_id" "$control_id" "$requirement" "$assertion_id" PASSED
  check_aggregate "R1 ($control_id): $scenario_id assertion PASSED -> SATISFIED contribution (UNPROVEN alone, 2nd requirement needed)" \
    "$TMP_DIR/r-${scenario_id}-passed.json" "$EXPECTED_TEST" UNPROVEN "$control_id"
  run_verifier="$(run_verifier_type "$TMP_DIR/norm_out.json" "$control_id")"
  if [ "$run_verifier" = "DYNAMIC_API" ]; then
    pass "R2 ($control_id): $scenario_id reports verifier.type=DYNAMIC_API"
  else
    fail "R2 ($control_id): expected verifier.type=DYNAMIC_API, got $run_verifier"
  fi

  build_scenario_artifact "$TMP_DIR/r-${scenario_id}-failed.json" "$scenario_id" "$control_id" "$requirement" "$assertion_id" FAILED
  check_aggregate "R3 ($control_id): $scenario_id assertion FAILED -> FAIL" \
    "$TMP_DIR/r-${scenario_id}-failed.json" "$EXPECTED_TEST" FAIL "$control_id"
done

echo ""
echo "diana/security/dynamic/test-dynamic.sh: $pass_count passed, $fail_count failed"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
