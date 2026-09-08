#!/usr/bin/env bash
set -euo pipefail

ADAPTERS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEC_DIR="$(dirname "$ADAPTERS_DIR")"
CATALOG="$SEC_DIR/catalog.json"
EVIDENCE_MODEL="$SEC_DIR/evidence_model.py"
FIXTURES="$ADAPTERS_DIR/fixtures"

GITLEAKS="$ADAPTERS_DIR/gitleaks_adapter.py"
OSV="$ADAPTERS_DIR/osv_scanner_adapter.py"
SEMGREP="$ADAPTERS_DIR/semgrep_adapter.py"

EXPECTED_FULL_REPO="$FIXTURES/expected-target-full-repo.json"
EXPECTED_FULL_REPO_MANIFESTS="$FIXTURES/expected-target-full-repo-with-manifests.json"
EXPECTED_MISSING_COMMIT="$FIXTURES/expected-target-missing-commit.json"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass_count=0
fail_count=0

pass() { echo "PASS: $1"; pass_count=$((pass_count + 1)); }
fail() { echo "FAIL: $1" >&2; fail_count=$((fail_count + 1)); }

run_adapter() {
  # run_adapter <adapter.py> <artifact-path-or-'-'> <expected-path-or-'-'> <control_id...>
  local adapter="$1"; shift
  python3 "$adapter" "$@" > "$TMP_DIR/adapter_out.json"
}

adapter_runs() {
  python3 -c "
import json, sys
print(json.dumps(json.load(open(sys.argv[1]))['runs']))
" "$1"
}

contribution_status() {
  # Status of the (only) evidence item this adapter emitted for
  # <control_id>, or NONE if no run for that control_id was emitted.
  python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
for r in d['runs']:
    if r['control_id'] == sys.argv[2]:
        print(r['evidence'][0]['status'] if r['evidence'] else 'NONE')
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

check_contribution() {
  # check_contribution <name> <adapter> <artifact> <expected> <control_id> <expected_status>
  local name="$1" adapter="$2" artifact="$3" expected="$4" control_id="$5" expected_status="$6"
  run_adapter "$adapter" "$artifact" "$expected" "$control_id"
  local actual
  actual="$(contribution_status "$TMP_DIR/adapter_out.json" "$control_id")"
  if [ "$actual" = "$expected_status" ]; then
    pass "$name -> $expected_status"
  else
    fail "$name (expected $expected_status, got $actual)"
    cat "$TMP_DIR/adapter_out.json" >&2
  fi
}

check_aggregate() {
  # check_aggregate <name> <adapter> <artifact> <expected> <expected_result> <control_id...>
  local name="$1" adapter="$2" artifact="$3" expected="$4" expected_result="$5"; shift 5
  run_adapter "$adapter" "$artifact" "$expected" "$@"
  local runs
  runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
  evaluate_runs "$runs" "$TMP_DIR/agg.out.json"
  local all_ok=1
  for cid in "$@"; do
    r="$(result_for "$TMP_DIR/agg.out.json" "$cid")"
    if [ "$r" != "$expected_result" ]; then
      all_ok=0
      fail "$name (expected $expected_result for $cid, got $r)"
    fi
  done
  if [ "$all_ok" -eq 1 ]; then
    pass "$name -> $expected_result"
  fi
}

# ==================================================================
# T1-T4: target identity vs. scan coverage split (all three adapters)
# ==================================================================

# T1: same repo + same commit + partial scope + recognized finding => FAIL allowed
check_contribution "T1 (gitleaks): same identity + partial scope + finding" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-finding-partial-scope.json" "$EXPECTED_FULL_REPO" SEC-007 VIOLATED
check_contribution "T1 (osv-scanner): same identity + incomplete manifest coverage + finding" \
  "$OSV" "$FIXTURES/osv-artifact-finding-high-incomplete-manifests.json" "$EXPECTED_FULL_REPO_MANIFESTS" SEC-060 VIOLATED
check_contribution "T1 (semgrep): same identity + partial scope + finding" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-finding-verified-partial-scope.json" "$EXPECTED_FULL_REPO" SEC-056 VIOLATED

# T2: wrong repository + recognized finding => NOT FAIL
check_contribution "T2 (gitleaks): wrong repository + finding" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-finding-wrong-repo.json" "$EXPECTED_FULL_REPO" SEC-007 NONE
check_contribution "T2 (osv-scanner): wrong repository + finding" \
  "$OSV" "$FIXTURES/osv-artifact-finding-high-wrong-repo.json" "$EXPECTED_FULL_REPO_MANIFESTS" SEC-060 NONE
check_contribution "T2 (semgrep): wrong repository + finding" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-finding-verified-wrong-repo.json" "$EXPECTED_FULL_REPO" SEC-056 NONE

# T3: wrong commit + recognized finding => NOT FAIL
check_contribution "T3 (gitleaks): wrong commit + finding" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-finding-wrong-commit.json" "$EXPECTED_FULL_REPO" SEC-007 NONE
check_contribution "T3 (osv-scanner): wrong commit + finding" \
  "$OSV" "$FIXTURES/osv-artifact-finding-high-wrong-commit.json" "$EXPECTED_FULL_REPO_MANIFESTS" SEC-060 NONE
check_contribution "T3 (semgrep): wrong commit + finding" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-finding-verified-wrong-commit.json" "$EXPECTED_FULL_REPO" SEC-056 NONE

# T4: missing expected commit => never PASS / never FAIL (gitleaks, both a
# clean and a finding artifact against an expectation with no commit key)
check_contribution "T4 (gitleaks): missing expected commit + clean report" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-clean-full-repo.json" "$EXPECTED_MISSING_COMMIT" SEC-007 NONE
check_contribution "T4 (gitleaks): missing expected commit + finding" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-finding-full-repo-for-missing-commit-test.json" "$EXPECTED_MISSING_COMMIT" SEC-007 NONE

# T5: clean + correct repo/commit + incomplete coverage => UNPROVEN
check_aggregate "T5 (gitleaks): clean + correct identity + partial scope" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-clean-partial-scope.json" "$EXPECTED_FULL_REPO" UNPROVEN SEC-007
check_aggregate "T5 (osv-scanner): clean + correct identity + incomplete manifests" \
  "$OSV" "$FIXTURES/osv-artifact-clean-incomplete-manifests.json" "$EXPECTED_FULL_REPO_MANIFESTS" UNPROVEN SEC-060
check_aggregate "T5 (semgrep): clean + correct identity + partial scope" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-clean-verified-partial-scope.json" "$EXPECTED_FULL_REPO" UNPROVEN SEC-056

# T6: clean + correct identity + complete coverage => SATISFIED (and PASS
# end to end, since each mapped control here needs only this one capability)
check_aggregate "T6 (gitleaks): clean + correct identity + full coverage" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-clean-full-repo.json" "$EXPECTED_FULL_REPO" UNPROVEN SEC-007
# (SEC-007 needs a 2nd, differently-capable contribution too -- see the
# combined end-to-end PASS check further below; UNPROVEN here on its own
# is correct and expected, matching Phase 1's aggregation.)
check_aggregate "T6 (osv-scanner): clean + correct identity + full coverage" \
  "$OSV" "$FIXTURES/osv-artifact-clean-full-coverage.json" "$EXPECTED_FULL_REPO_MANIFESTS" PASS SEC-060
check_aggregate "T6 (semgrep): clean + correct identity + full coverage" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-clean-verified-full-scope.json" "$EXPECTED_FULL_REPO" PASS SEC-055 SEC-056

# End-to-end: gitleaks' verified-clean contribution + a second run
# supplying SEC-007's other permitted-capability requirement -> PASS.
run_adapter "$GITLEAKS" "$FIXTURES/gitleaks-artifact-clean-full-repo.json" "$EXPECTED_FULL_REPO" SEC-007
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
python3 -c "
import json
gitleaks_runs = json.loads('''$runs''')
static_run = {
    'control_id': 'SEC-007',
    'applicability': 'APPLICABLE',
    'verifier': {'type': 'STATIC_ANALYZER', 'identity': 'semgrep::config-loading-check'},
    'evidence': [
        {'requirement': 'secrets are loaded from environment/secret-manager configuration, not source',
         'status': 'SATISFIED', 'provenance': 'static pattern match confirms os.environ usage, no literal fallback'}
    ],
    'tool_error': None,
}
json.dump(gitleaks_runs + [static_run], open('$TMP_DIR/g_combined.json', 'w'))
"
python3 "$EVIDENCE_MODEL" "$CATALOG" "$TMP_DIR/g_combined.json" > "$TMP_DIR/g_combined.out.json"
r="$(result_for "$TMP_DIR/g_combined.out.json" SEC-007)"
[ "$r" = "PASS" ] && pass "T6b (gitleaks): verified clean scan + static-analyzer 2nd requirement -> PASS" \
  || fail "T6b: expected PASS, got $r"

# ==================================================================
# T7: per-adapter tool identity
# ==================================================================

check_aggregate "T7 (gitleaks): tool.name mismatch (trufflehog, compatible shape)" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-wrong-tool-name.json" "$EXPECTED_FULL_REPO" ERROR SEC-007
check_aggregate "T7 (osv-scanner): tool.name mismatch (trivy, compatible shape)" \
  "$OSV" "$FIXTURES/osv-artifact-wrong-tool-name.json" "$EXPECTED_FULL_REPO_MANIFESTS" ERROR SEC-060
check_aggregate "T7 (semgrep): tool.name mismatch (codeql, compatible shape)" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-wrong-tool-name.json" "$EXPECTED_FULL_REPO" ERROR SEC-055 SEC-056

# ==================================================================
# T8-T12: artifact-level binding -- tampering any bound field without
# recomputing the binding must be detected
# ==================================================================

check_aggregate "T8: target.commit mutated without rebuilding artifact_binding" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-binding-mutated-commit.json" "$EXPECTED_FULL_REPO" ERROR SEC-007
check_aggregate "T9: target.repository mutated without rebuilding artifact_binding" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-binding-mutated-repository.json" "$EXPECTED_FULL_REPO" ERROR SEC-007
check_aggregate "T10: config mutated without rebuilding artifact_binding" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-binding-mutated-config.json" "$EXPECTED_FULL_REPO" ERROR SEC-007
check_aggregate "T11: scanned_inputs mutated without rebuilding artifact_binding" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-binding-mutated-scanned-inputs.json" "$EXPECTED_FULL_REPO" ERROR SEC-007
check_aggregate "T12: report mutated without rebuilding artifact_binding" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-binding-mutated-report.json" "$EXPECTED_FULL_REPO" ERROR SEC-007

# ==================================================================
# T13-T16: Semgrep rule-map authorization validation
# ==================================================================

# T13: valid, authorized mapping -> accepted (already proven by T6/PASS
# above; restated explicitly here against the required test name).
check_aggregate "T13: valid Semgrep authorized mapping is accepted" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-clean-verified-full-scope.json" "$EXPECTED_FULL_REPO" PASS SEC-055 SEC-056

# T14: valid control, wrong requirement text -> ERROR
check_aggregate "T14: Semgrep valid control + wrong requirement text in rule_map" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-wrong-requirement-mapping.json" "$EXPECTED_FULL_REPO" ERROR SEC-056

# T15: rule_map names a control this adapter isn't authorized for -> ERROR
check_aggregate "T15: Semgrep rule_map names an unauthorized control (SEC-001)" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-unauthorized-control-mapping.json" "$EXPECTED_FULL_REPO" ERROR SEC-056

# T16: malformed check_id (empty string) in rule_map -> ERROR
check_aggregate "T16: Semgrep malformed check_id in rule_map" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-malformed-check-id-mapping.json" "$EXPECTED_FULL_REPO" ERROR SEC-056

# ==================================================================
# T17: tool artifact missing => explicit UNPROVEN result, not an absent one
# ==================================================================

for adapter_name in "gitleaks:$GITLEAKS:SEC-007" "osv-scanner:$OSV:SEC-060" "semgrep:$SEMGREP:SEC-056"; do
  IFS=':' read -r label adapter_bin control_id <<< "$adapter_name"
  run_adapter "$adapter_bin" "$TMP_DIR/does-not-exist.json" "$EXPECTED_FULL_REPO" "$control_id"
  runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
  if [ "$runs" = "[]" ]; then
    fail "T17 ($label): expected an explicit run for tool-missing, got empty runs"
  else
    evaluate_runs "$runs" "$TMP_DIR/t17.out.json"
    r="$(result_for "$TMP_DIR/t17.out.json" "$control_id")"
    if [ "$r" = "UNPROVEN" ]; then
      pass "T17 ($label): tool-missing produces an explicit UNPROVEN result, not an absent one"
    else
      fail "T17 ($label): expected UNPROVEN, got $r"
    fi
  fi
done

# ==================================================================
# Preserved from the earlier implementation: still-valid behaviors
# ==================================================================

# P5/P6 (renamed to match the binding-tamper family, but this is a
# distinct case from T8-T12: execution never completed at all).
check_aggregate "P6 (gitleaks): execution did not complete" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-execution-incomplete.json" "$EXPECTED_FULL_REPO" ERROR SEC-007

check_aggregate "(gitleaks): malformed wrapped report" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-malformed-report.json" "$EXPECTED_FULL_REPO" ERROR SEC-007

check_aggregate "(gitleaks): artifact missing required fields" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-missing-fields.json" "$EXPECTED_FULL_REPO" ERROR SEC-007

run_adapter "$GITLEAKS" "$FIXTURES/gitleaks-artifact-clean-full-repo.json" "$EXPECTED_FULL_REPO" SEC-006 SEC-065
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "(gitleaks): silently refuses unauthorized controls (SEC-006, SEC-065)" \
  || fail "expected empty runs for unauthorized controls, got $runs"

check_aggregate "P8 (osv-scanner): empty-object report is NOT a clean scan" \
  "$OSV" "$FIXTURES/osv-artifact-empty-object-report.json" "$EXPECTED_FULL_REPO_MANIFESTS" ERROR SEC-060

check_aggregate "(osv-scanner): LOW-only + full coverage -> PASS" \
  "$OSV" "$FIXTURES/osv-artifact-low-only-full-coverage.json" "$EXPECTED_FULL_REPO_MANIFESTS" PASS SEC-060

check_aggregate "(osv-scanner): unrecognized severity value" \
  "$OSV" "$FIXTURES/osv-artifact-unrecognized-severity.json" "$EXPECTED_FULL_REPO_MANIFESTS" ERROR SEC-060

run_adapter "$OSV" "$FIXTURES/osv-artifact-clean-full-coverage.json" "$EXPECTED_FULL_REPO_MANIFESTS" SEC-061 SEC-062
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "(osv-scanner): silently refuses unauthorized controls (SEC-061, SEC-062)" \
  || fail "expected empty runs for unauthorized controls, got $runs"

check_aggregate "P10 (semgrep): no verified rule_map -> illustrative mapping cannot create PASS" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-clean-no-rule-map.json" "$EXPECTED_FULL_REPO" UNPROVEN SEC-056

check_aggregate "(semgrep): unmapped check_id (even with a verified rule_map present) produces no contribution" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-unmapped-rule-verified.json" "$EXPECTED_FULL_REPO" UNPROVEN SEC-055 SEC-056

check_contribution "(semgrep): verified rule_map but mapped rule never ran" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-rule-not-run-verified.json" "$EXPECTED_FULL_REPO" SEC-056 NONE

check_aggregate "(semgrep): malformed wrapped report" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-malformed-report.json" "$EXPECTED_FULL_REPO" ERROR SEC-055 SEC-056

run_adapter "$SEMGREP" "$FIXTURES/semgrep-artifact-clean-verified-full-scope.json" "$EXPECTED_FULL_REPO" SEC-001
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "(semgrep): silently refuses an unauthorized control (SEC-001)" \
  || fail "expected empty runs for an unauthorized control, got $runs"

echo ""
echo "diana/security/adapters/test-adapters.sh: $pass_count passed, $fail_count failed"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
