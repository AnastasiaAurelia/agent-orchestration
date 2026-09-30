#!/usr/bin/env bash
set -euo pipefail

ADAPTERS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEC_DIR="$(dirname "$ADAPTERS_DIR")"
CATALOG="$SEC_DIR/catalog.json"
EVIDENCE_MODEL="$SEC_DIR/evidence_model.py"
FIXTURES="$ADAPTERS_DIR/fixtures"
ZAP="$ADAPTERS_DIR/zap_adapter.py"

EXPECTED_LOCAL="$FIXTURES/expected-target-zap-local.json"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass_count=0
fail_count=0
pass() { echo "PASS: $1"; pass_count=$((pass_count + 1)); }
fail() { echo "FAIL: $1" >&2; fail_count=$((fail_count + 1)); }

run_adapter() {
  local artifact="$1" expected="$2"; shift 2
  python3 "$ZAP" "$artifact" "$expected" "$@" > "$TMP_DIR/out.json"
}

runs_array() {
  python3 -c "
import json
print(json.dumps(json.load(open('$1'))['runs']))
"
}

evaluate() {
  local runs_json="$1" out="$2"
  echo "$runs_json" > "$TMP_DIR/runs_in.json"
  python3 "$EVIDENCE_MODEL" "$CATALOG" "$TMP_DIR/runs_in.json" > "$out"
}

result_for() {
  python3 -c "
import json, sys
d = json.load(open('$1'))
for r in d['results']:
    if r['control_id'] == sys.argv[1]:
        print(r['result'])
        sys.exit(0)
print('MISSING')
" "$2"
}

check_aggregate() {
  local name="$1" artifact="$2" expected="$3" expected_result="$4"; shift 4
  run_adapter "$artifact" "$expected" "$@"
  local runs
  runs="$(runs_array "$TMP_DIR/out.json")"
  evaluate "$runs" "$TMP_DIR/agg.json"
  local ok=1
  for cid in "$@"; do
    r="$(result_for "$TMP_DIR/agg.json" "$cid")"
    [ "$r" = "$expected_result" ] || { ok=0; fail "$name (expected $expected_result for $cid, got $r)"; }
  done
  [ "$ok" -eq 1 ] && pass "$name -> $expected_result"
}

# CASE 1: tool missing (no artifact) -> UNPROVEN, for each authorized control
for cid in SEC-030 SEC-031 SEC-032 SEC-033 SEC-053; do
  check_aggregate "CASE 1: tool unavailable ($cid)" - "$EXPECTED_LOCAL" UNPROVEN "$cid"
done

# CASE 2: clean scan, verified local target, no matching alert -> SATISFIED
# alone (each of these controls needs BOTH required_evidence items -- the
# static half is not contributed here -- so the aggregate is UNPROVEN, not
# PASS. This proves ZAP alone can never manufacture PASS for a control it
# only partially covers.)
check_aggregate "CASE 2: clean scan alone -> UNPROVEN (static half missing)" \
  "$FIXTURES/zap-artifact-clean.json" "$EXPECTED_LOCAL" UNPROVEN SEC-032

# CASE 3: cookie alert present -> SEC-030 FAIL
check_aggregate "CASE 3: cookie alert -> FAIL" \
  "$FIXTURES/zap-artifact-finding-cookie.json" "$EXPECTED_LOCAL" FAIL SEC-030

# CASE 4: CSP alert present -> SEC-032 FAIL
check_aggregate "CASE 4: CSP alert -> FAIL" \
  "$FIXTURES/zap-artifact-finding-csp.json" "$EXPECTED_LOCAL" FAIL SEC-032

# CASE 5: clickjacking alert present -> SEC-033 FAIL
check_aggregate "CASE 5: clickjacking alert -> FAIL" \
  "$FIXTURES/zap-artifact-finding-clickjacking.json" "$EXPECTED_LOCAL" FAIL SEC-033

# CASE 6: CORS alert present -> SEC-031 FAIL
check_aggregate "CASE 6: CORS alert -> FAIL" \
  "$FIXTURES/zap-artifact-finding-cors.json" "$EXPECTED_LOCAL" FAIL SEC-031

# CASE 7: headers alert present -> SEC-053 FAIL
check_aggregate "CASE 7: headers alert -> FAIL" \
  "$FIXTURES/zap-artifact-finding-headers.json" "$EXPECTED_LOCAL" FAIL SEC-053

# CASE 8: target mismatch (wrong commit), clean scan -> no contribution -> UNPROVEN
check_aggregate "CASE 8: clean but wrong commit -> UNPROVEN" \
  "$FIXTURES/zap-artifact-clean-wrong-commit.json" "$EXPECTED_LOCAL" UNPROVEN SEC-032

# CASE 9: target mismatch (wrong repo), clean scan -> UNPROVEN
check_aggregate "CASE 9: clean but wrong repo -> UNPROVEN" \
  "$FIXTURES/zap-artifact-clean-wrong-repo.json" "$EXPECTED_LOCAL" UNPROVEN SEC-032

# CASE 10: finding present but wrong commit -> not attributed -> UNPROVEN, never FAIL
check_aggregate "CASE 10: finding wrong commit not attributed" \
  "$FIXTURES/zap-artifact-finding-wrong-commit.json" "$EXPECTED_LOCAL" UNPROVEN SEC-030

# CASE 11: wrong declared tool name -> ERROR
check_aggregate "CASE 11: wrong tool name rejected -> ERROR" \
  "$FIXTURES/zap-artifact-wrong-tool-name.json" "$EXPECTED_LOCAL" ERROR SEC-032

# CASE 12: malformed report (no 'site' field) -> ERROR
check_aggregate "CASE 12: malformed report -> ERROR" \
  "$FIXTURES/zap-artifact-malformed-report.json" "$EXPECTED_LOCAL" ERROR SEC-032

# CASE 13: PRODUCTION target attempt (environment=PROD, external base_url)
# -> refused as a structural ArtifactError -> ERROR, never silently scanned.
check_aggregate "CASE 13: production target attempt refused -> ERROR" \
  "$FIXTURES/zap-artifact-prod-environment.json" "$EXPECTED_LOCAL" ERROR SEC-032

# CASE 14: non-loopback host even under a LOCAL label -> refused -> ERROR
check_aggregate "CASE 14: external host attempt refused -> ERROR" \
  "$FIXTURES/zap-artifact-external-host.json" "$EXPECTED_LOCAL" ERROR SEC-032

# CASE 15: unauthorized control (e.g. SEC-001) yields zero runs, never fabricated evidence
python3 "$ZAP" "$FIXTURES/zap-artifact-clean.json" "$EXPECTED_LOCAL" SEC-001 > "$TMP_DIR/unauth.json"
python3 -c "
import json
d = json.load(open('$TMP_DIR/unauth.json'))
assert d['runs'] == [], d['runs']
" && pass "CASE 15: unauthorized control (SEC-001) yields zero runs" || fail "CASE 15: unauthorized control (SEC-001) yields zero runs"

# CASE 16: alert on an unrelated plugin id is never conflated with an
# authorized control's evidence -- a report whose only alert is for a
# plugin id this adapter does not curate still yields SATISFIED for the
# controls it IS authorized for (proves the plugin map is a strict
# allowlist, not "any alert means violated").
check_aggregate "CASE 16: unrelated/uncurated plugin id never conflated" \
  "$FIXTURES/zap-artifact-finding-cookie.json" "$EXPECTED_LOCAL" UNPROVEN SEC-032

echo ""
echo "test-zap-adapter.sh: $pass_count passed, $fail_count failed"
[ "$fail_count" -eq 0 ]
