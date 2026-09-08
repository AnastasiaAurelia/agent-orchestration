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

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass_count=0
fail_count=0

pass() { echo "PASS: $1"; pass_count=$((pass_count + 1)); }
fail() { echo "FAIL: $1" >&2; fail_count=$((fail_count + 1)); }

run_adapter() {
  # run_adapter <adapter.py> <artifact-path-or-'-'> <expected-path-or-'-'> <control_id...>
  # -> writes adapter stdout to $TMP_DIR/adapter_out.json
  local adapter="$1"; shift
  python3 "$adapter" "$@" > "$TMP_DIR/adapter_out.json"
}

adapter_runs() {
  python3 -c "
import json, sys
print(json.dumps(json.load(open(sys.argv[1]))['runs']))
" "$1"
}

# Extracts the status of the (only) evidence item this adapter emitted
# for <control_id>, or NONE if no run for that control_id was emitted at
# all -- the direct way to check "did this artifact produce a SATISFIED/
# VIOLATED contribution", without going through evidence_model.py.
contribution_status() {
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

check_aggregate_error() {
  # check_aggregate_error <name> <adapter> <artifact> <expected> <control_id...>
  local name="$1" adapter="$2" artifact="$3" expected="$4"; shift 4
  run_adapter "$adapter" "$artifact" "$expected" "$@"
  local runs
  runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
  evaluate_runs "$runs" "$TMP_DIR/agg.out.json"
  local all_error=1
  for cid in "$@"; do
    r="$(result_for "$TMP_DIR/agg.out.json" "$cid")"
    if [ "$r" != "ERROR" ]; then
      all_error=0
      fail "$name (expected ERROR for $cid, got $r)"
    fi
  done
  [ "$all_error" -eq 1 ] && pass "$name -> ERROR"
}

# ==================================================================
# Gitleaks: SEC-007, one authorized requirement, target/scope-aware
# ==================================================================

# CASE P1: clean report + correct target/commit/full-repo scope -> the
# adapter's one authorized SEC-007 requirement gets a SATISFIED
# contribution.
check_contribution "CASE P1 (gitleaks): clean + verified full-repo target" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-clean-full-repo.json" "$EXPECTED_FULL_REPO" SEC-007 SATISFIED

# End-to-end: that SATISFIED contribution, combined with a second run
# supplying SEC-007's other permitted-capability requirement, reaches
# PASS -- proving Phase 1 aggregation still composes correctly.
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
[ "$r" = "PASS" ] && pass "CASE P1b (gitleaks): verified clean scan + static-analyzer 2nd requirement -> PASS" \
  || fail "CASE P1b: expected PASS, got $r"

# CASE P2: clean report + WRONG commit -> not PASS (no contribution).
check_contribution "CASE P2 (gitleaks): clean + wrong commit" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-clean-wrong-commit.json" "$EXPECTED_FULL_REPO" SEC-007 NONE

# CASE P3: clean report + missing scan context entirely -> not PASS.
check_contribution "CASE P3 (gitleaks): clean + no target context" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-clean-no-context.json" "$EXPECTED_FULL_REPO" SEC-007 NONE

# CASE P4: clean report + partial/subdirectory scope -> not PASS for a
# repository-wide requirement.
check_contribution "CASE P4 (gitleaks): clean + partial scope" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-clean-partial-scope.json" "$EXPECTED_FULL_REPO" SEC-007 NONE

# CASE P5: report_binding hash mismatch (tampered/substituted report) ->
# ERROR, not silently trusted.
check_aggregate_error "CASE P5 (gitleaks): report_binding hash mismatch" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-binding-mismatch.json" "$EXPECTED_FULL_REPO" SEC-007

# CASE P6: execution.completed=false -> ERROR.
check_aggregate_error "CASE P6 (gitleaks): execution did not complete" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-execution-incomplete.json" "$EXPECTED_FULL_REPO" SEC-007

# CASE P7: a recognized finding under PARTIAL scope is still trusted and
# visible as VIOLATED -- positive evidence doesn't need full coverage.
check_contribution "CASE P7 (gitleaks): finding under partial scope still VIOLATED" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-finding-partial-scope.json" "$EXPECTED_FULL_REPO" SEC-007 VIOLATED

# A finding under full-repo scope -> also VIOLATED, and end-to-end FAIL.
run_adapter "$GITLEAKS" "$FIXTURES/gitleaks-artifact-finding-full-repo.json" "$EXPECTED_FULL_REPO" SEC-007
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/g_fail.out.json"
r="$(result_for "$TMP_DIR/g_fail.out.json" SEC-007)"
[ "$r" = "FAIL" ] && pass "CASE (gitleaks): finding -> aggregate FAIL" || fail "expected FAIL, got $r"

# Malformed wrapped report (wrong shape) -> ERROR.
check_aggregate_error "CASE (gitleaks): malformed wrapped report" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-malformed-report.json" "$EXPECTED_FULL_REPO" SEC-007

# Structurally incomplete artifact (missing required envelope fields) -> ERROR.
check_aggregate_error "CASE (gitleaks): artifact missing required fields" \
  "$GITLEAKS" "$FIXTURES/gitleaks-artifact-missing-fields.json" "$EXPECTED_FULL_REPO" SEC-007

# Tool missing (no artifact at all) -> no runs -> UNPROVEN.
run_adapter "$GITLEAKS" "$TMP_DIR/does-not-exist.json" "$EXPECTED_FULL_REPO" SEC-007
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "CASE (gitleaks): tool-missing emits no runs" || fail "expected empty runs, got $runs"

# Silently refuses unauthorized controls.
run_adapter "$GITLEAKS" "$FIXTURES/gitleaks-artifact-clean-full-repo.json" "$EXPECTED_FULL_REPO" SEC-006 SEC-065
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "CASE (gitleaks): silently refuses unauthorized controls (SEC-006, SEC-065)" \
  || fail "expected empty runs for unauthorized controls, got $runs"

# ==================================================================
# osv-scanner: SEC-060, fail-open bug fixed, manifest-coverage-aware
# ==================================================================

# CASE P8: {} report (no 'results' key at all) -> ERROR, never treated as
# a clean scan. This is the exact fail-open bug from human review.
check_aggregate_error "CASE P8 (osv-scanner): empty-object report is NOT a clean scan" \
  "$OSV" "$FIXTURES/osv-artifact-empty-object-report.json" "$EXPECTED_FULL_REPO_MANIFESTS" SEC-060

# CASE P9: clean report, but scanned_inputs doesn't cover all expected
# manifests -> UNPROVEN (no contribution), not PASS.
check_contribution "CASE P9 (osv-scanner): clean + incomplete manifest coverage" \
  "$OSV" "$FIXTURES/osv-artifact-clean-incomplete-manifests.json" "$EXPECTED_FULL_REPO_MANIFESTS" SEC-060 NONE

# Clean + full manifest coverage + verified target -> SATISFIED -> PASS
# end-to-end (SEC-060 needs only DEPENDENCY_SCANNER).
run_adapter "$OSV" "$FIXTURES/osv-artifact-clean-full-coverage.json" "$EXPECTED_FULL_REPO_MANIFESTS" SEC-060
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/o_pass.out.json"
r="$(result_for "$TMP_DIR/o_pass.out.json" SEC-060)"
[ "$r" = "PASS" ] && pass "CASE (osv-scanner): clean + full manifest coverage -> PASS" || fail "expected PASS, got $r"

# A HIGH finding is trusted even with incomplete manifest coverage
# (positive evidence asymmetry) -> VIOLATED -> FAIL.
run_adapter "$OSV" "$FIXTURES/osv-artifact-finding-high-incomplete-manifests.json" "$EXPECTED_FULL_REPO_MANIFESTS" SEC-060
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/o_fail_partial.out.json"
r="$(result_for "$TMP_DIR/o_fail_partial.out.json" SEC-060)"
[ "$r" = "FAIL" ] && pass "CASE (osv-scanner): HIGH finding trusted despite incomplete manifest coverage -> FAIL" \
  || fail "expected FAIL, got $r"

# A HIGH finding with full coverage -> also FAIL.
run_adapter "$OSV" "$FIXTURES/osv-artifact-finding-high-full-coverage.json" "$EXPECTED_FULL_REPO_MANIFESTS" SEC-060
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/o_fail_full.out.json"
r="$(result_for "$TMP_DIR/o_fail_full.out.json" SEC-060)"
[ "$r" = "FAIL" ] && pass "CASE (osv-scanner): HIGH finding + full coverage -> FAIL" || fail "expected FAIL, got $r"

# Only LOW findings present + full coverage -> still SATISFIED, matching
# the catalog's exact "no unresolved critical/high" wording -> PASS.
run_adapter "$OSV" "$FIXTURES/osv-artifact-low-only-full-coverage.json" "$EXPECTED_FULL_REPO_MANIFESTS" SEC-060
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/o_low.out.json"
r="$(result_for "$TMP_DIR/o_low.out.json" SEC-060)"
[ "$r" = "PASS" ] && pass "CASE (osv-scanner): LOW-only + full coverage -> PASS" || fail "expected PASS, got $r"

# An unrecognized severity string fails closed to ERROR for the whole
# artifact, rather than being silently excluded from the critical/high
# check.
check_aggregate_error "CASE (osv-scanner): unrecognized severity value" \
  "$OSV" "$FIXTURES/osv-artifact-unrecognized-severity.json" "$EXPECTED_FULL_REPO_MANIFESTS" SEC-060

# Tool missing.
run_adapter "$OSV" "$TMP_DIR/does-not-exist.json" "$EXPECTED_FULL_REPO_MANIFESTS" SEC-060
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "CASE (osv-scanner): tool-missing emits no runs" || fail "expected empty runs, got $runs"

# Silently refuses unauthorized controls.
run_adapter "$OSV" "$FIXTURES/osv-artifact-clean-full-coverage.json" "$EXPECTED_FULL_REPO_MANIFESTS" SEC-061 SEC-062
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "CASE (osv-scanner): silently refuses unauthorized controls (SEC-061, SEC-062)" \
  || fail "expected empty runs for unauthorized controls, got $runs"

# ==================================================================
# Semgrep: SEC-055/SEC-056, verified rule_map only, target/scope-aware
# ==================================================================

# CASE P10: no config.rule_map supplied at all -- the illustrative
# constant in this module must NOT be consulted by production code, so
# nothing is authorized despite rules_run listing the illustrative rule
# IDs verbatim.
check_contribution "CASE P10 (semgrep): no verified rule_map -> illustrative mapping cannot create PASS" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-clean-no-rule-map.json" "$EXPECTED_FULL_REPO" SEC-056 NONE

# CASE P11: a caller-verified rule_map, the mapped rules actually ran,
# and the target/scope matches what was expected -> SATISFIED for both
# mapped controls -> both PASS (neither is dynamic/human-judgment gated).
run_adapter "$SEMGREP" "$FIXTURES/semgrep-artifact-clean-verified-full-scope.json" "$EXPECTED_FULL_REPO" SEC-055 SEC-056
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/s_pass.out.json"
r55="$(result_for "$TMP_DIR/s_pass.out.json" SEC-055)"
r56="$(result_for "$TMP_DIR/s_pass.out.json" SEC-056)"
[ "$r55" = "PASS" ] && [ "$r56" = "PASS" ] && pass "CASE P11 (semgrep): verified rule_map + rules ran + full scope -> PASS for SEC-055 and SEC-056" \
  || fail "CASE P11: expected [PASS, PASS], got [$r55, $r56]"

# CASE P12: same verified rule_map and rules ran, but the artifact's
# declared scope is "partial" while the caller expected "full-repo" ->
# target mismatch -> not PASS.
check_contribution "CASE P12 (semgrep): verified rule_map + rules ran but scope mismatch" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-clean-verified-partial-scope.json" "$EXPECTED_FULL_REPO" SEC-056 NONE

# A recognized finding from a verified, mapped rule is trusted regardless
# of scope -> VIOLATED -> FAIL.
run_adapter "$SEMGREP" "$FIXTURES/semgrep-artifact-finding-verified.json" "$EXPECTED_FULL_REPO" SEC-055 SEC-056
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/s_fail.out.json"
r56="$(result_for "$TMP_DIR/s_fail.out.json" SEC-056)"
[ "$r56" = "FAIL" ] && pass "CASE (semgrep): verified finding -> FAIL" || fail "expected FAIL, got $r56"

# Same finding under partial scope -- still trusted -> FAIL (positive
# evidence asymmetry, same as Gitleaks/osv-scanner above).
run_adapter "$SEMGREP" "$FIXTURES/semgrep-artifact-finding-verified-partial-scope.json" "$EXPECTED_FULL_REPO" SEC-056
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/s_fail_partial.out.json"
r56="$(result_for "$TMP_DIR/s_fail_partial.out.json" SEC-056)"
[ "$r56" = "FAIL" ] && pass "CASE (semgrep): verified finding under partial scope still -> FAIL" \
  || fail "expected FAIL, got $r56"

# An unmapped check_id (not in the verified rule_map either) is silently
# dropped -- no contribution for any control.
run_adapter "$SEMGREP" "$FIXTURES/semgrep-artifact-unmapped-rule-verified.json" "$EXPECTED_FULL_REPO" SEC-055 SEC-056
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "CASE (semgrep): unmapped check_id (even with a verified rule_map present) produces no contribution" \
  || fail "expected empty runs, got $runs"

# The mapped rule exists in the verified rule_map but never actually ran
# (rules_run only lists an unrelated rule) -> no contribution.
check_contribution "CASE (semgrep): verified rule_map but mapped rule never ran" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-rule-not-run-verified.json" "$EXPECTED_FULL_REPO" SEC-056 NONE

# Malformed wrapped report -> ERROR.
check_aggregate_error "CASE (semgrep): malformed wrapped report" \
  "$SEMGREP" "$FIXTURES/semgrep-artifact-malformed-report.json" "$EXPECTED_FULL_REPO" SEC-055 SEC-056

# Tool missing.
run_adapter "$SEMGREP" "$TMP_DIR/does-not-exist.json" "$EXPECTED_FULL_REPO" SEC-056
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "CASE (semgrep): tool-missing emits no runs" || fail "expected empty runs, got $runs"

# Silently refuses an unauthorized control (SEC-001 needs
# SEMANTIC_REVIEW/DYNAMIC_API, not STATIC_ANALYZER).
run_adapter "$SEMGREP" "$FIXTURES/semgrep-artifact-clean-verified-full-scope.json" "$EXPECTED_FULL_REPO" SEC-001
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "CASE (semgrep): silently refuses an unauthorized control (SEC-001)" \
  || fail "expected empty runs for an unauthorized control, got $runs"

echo ""
echo "diana/security/adapters/test-adapters.sh: $pass_count passed, $fail_count failed"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
