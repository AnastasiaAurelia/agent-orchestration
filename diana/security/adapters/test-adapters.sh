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

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass_count=0
fail_count=0

pass() { echo "PASS: $1"; pass_count=$((pass_count + 1)); }
fail() { echo "FAIL: $1" >&2; fail_count=$((fail_count + 1)); }

# Run an adapter's runs list through evidence_model.py and print the
# result for the given control_id.
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

# Extract just the "runs" array from an adapter's own JSON output.
adapter_runs() {
  python3 -c "
import json, sys
print(json.dumps(json.load(open(sys.argv[1]))['runs']))
" "$1"
}

run_adapter() {
  # run_adapter <adapter.py> <fixture-path-or-'-'> <control_id...> -> writes adapter stdout to $TMP_DIR/adapter_out.json
  local adapter="$1"; shift
  local fixture="$1"; shift
  python3 "$adapter" "$fixture" "$@" > "$TMP_DIR/adapter_out.json"
}

# ==================================================================
# Gitleaks adapter
# ==================================================================

# CASE G1: clean scan -> SATISFIED for SEC-007's ONE authorized item, but
# evidence_model.py's aggregate is still UNPROVEN because SEC-007's other
# required_evidence item ("secrets loaded from config") is never claimed
# by this adapter -- proving a clean scan alone cannot manufacture PASS
# for a control this adapter is only PARTIALLY authorized for.
run_adapter "$GITLEAKS" "$FIXTURES/gitleaks-clean.json" SEC-007
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/g1.out.json"
r="$(result_for "$TMP_DIR/g1.out.json" SEC-007)"
[ "$r" = "UNPROVEN" ] && pass "CASE G1: gitleaks clean scan alone is UNPROVEN for SEC-007 (2nd requirement not authorized)" \
  || fail "CASE G1: expected UNPROVEN, got $r"

# CASE G2: gitleaks clean scan + a second run supplying the 2nd
# requirement from SEC-007's OTHER permitted capability (STATIC_ANALYZER;
# SEC-007's verification.modes is [SECRET_SCANNER, STATIC_ANALYZER], not
# SEMANTIC_REVIEW) together satisfy SEC-007 -> PASS, proving multi-
# verifier aggregation composes correctly with an adapter's partial
# contribution.
G2_RUNS="$TMP_DIR/g2_runs.json"
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
json.dump(gitleaks_runs + [static_run], open('$G2_RUNS', 'w'))
"
python3 "$EVIDENCE_MODEL" "$CATALOG" "$G2_RUNS" > "$TMP_DIR/g2.out.json"
r="$(result_for "$TMP_DIR/g2.out.json" SEC-007)"
[ "$r" = "PASS" ] && pass "CASE G2: gitleaks clean scan + a static-analyzer contribution together satisfy SEC-007 -> PASS" \
  || fail "CASE G2: expected PASS, got $r"

# CASE G3: a real finding -> VIOLATED -> aggregate FAIL.
run_adapter "$GITLEAKS" "$FIXTURES/gitleaks-finding.json" SEC-007
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/g3.out.json"
r="$(result_for "$TMP_DIR/g3.out.json" SEC-007)"
[ "$r" = "FAIL" ] && pass "CASE G3: gitleaks finding -> FAIL" || fail "CASE G3: expected FAIL, got $r"

# CASE G4: tool missing (no report file) -> adapter emits no runs -> UNPROVEN.
run_adapter "$GITLEAKS" "$TMP_DIR/does-not-exist.json" SEC-007
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
if [ "$runs" = "[]" ]; then
  pass "CASE G4: gitleaks tool-missing emits no runs"
else
  fail "CASE G4: expected empty runs when tool output is missing, got $runs"
fi
evaluate_runs "$runs" "$TMP_DIR/g4.out.json" 2>/dev/null || true
r="$(result_for "$TMP_DIR/g4.out.json" SEC-007 2>/dev/null || echo MISSING)"
[ "$r" = "MISSING" ] && pass "CASE G4b: tool-missing control never appears in evidence_model results (no runs submitted)" \
  || fail "CASE G4b: expected control to be absent from results, got $r"

# CASE G5: malformed report (wrong shape) -> ERROR.
run_adapter "$GITLEAKS" "$FIXTURES/gitleaks-malformed.json" SEC-007
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/g5.out.json"
r="$(result_for "$TMP_DIR/g5.out.json" SEC-007)"
[ "$r" = "ERROR" ] && pass "CASE G5: malformed gitleaks report -> ERROR" || fail "CASE G5: expected ERROR, got $r"

# CASE G6: gitleaks (SECRET_SCANNER capability) is not authorized for
# SEC-006/SEC-065 -- requesting them returns no runs at all (silently
# skipped, never fabricated), regardless of scan content.
run_adapter "$GITLEAKS" "$FIXTURES/gitleaks-clean.json" SEC-006 SEC-065
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "CASE G6: gitleaks silently refuses unauthorized controls (SEC-006, SEC-065)" \
  || fail "CASE G6: expected empty runs for unauthorized controls, got $runs"

# ==================================================================
# osv-scanner adapter
# ==================================================================

# CASE O1: clean scan -> SEC-060 fully satisfiable by this capability
# alone (single mode, single requirement) -> PASS.
run_adapter "$OSV" "$FIXTURES/osv-clean.json" SEC-060
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/o1.out.json"
r="$(result_for "$TMP_DIR/o1.out.json" SEC-060)"
[ "$r" = "PASS" ] && pass "CASE O1: osv-scanner clean scan alone -> PASS (SEC-060 needs only DEPENDENCY_SCANNER)" \
  || fail "CASE O1: expected PASS, got $r"

# CASE O2: a HIGH-severity finding -> VIOLATED -> FAIL.
run_adapter "$OSV" "$FIXTURES/osv-finding-high.json" SEC-060
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/o2.out.json"
r="$(result_for "$TMP_DIR/o2.out.json" SEC-060)"
[ "$r" = "FAIL" ] && pass "CASE O2: osv-scanner HIGH finding -> FAIL" || fail "CASE O2: expected FAIL, got $r"

# CASE O3: only LOW-severity findings present -> still SATISFIED, matching
# the catalog's exact wording ("no unresolved critical/high finding") --
# proves the adapter doesn't overclaim beyond what the control actually
# requires.
run_adapter "$OSV" "$FIXTURES/osv-finding-low-only.json" SEC-060
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/o3.out.json"
r="$(result_for "$TMP_DIR/o3.out.json" SEC-060)"
[ "$r" = "PASS" ] && pass "CASE O3: osv-scanner LOW-only findings still -> PASS (matches catalog's critical/high wording)" \
  || fail "CASE O3: expected PASS, got $r"

# CASE O4: tool missing -> no runs -> UNPROVEN (absent from results).
run_adapter "$OSV" "$TMP_DIR/does-not-exist.json" SEC-060
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "CASE O4: osv-scanner tool-missing emits no runs" \
  || fail "CASE O4: expected empty runs, got $runs"

# CASE O5: malformed report -> ERROR.
run_adapter "$OSV" "$FIXTURES/osv-malformed.json" SEC-060
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/o5.out.json"
r="$(result_for "$TMP_DIR/o5.out.json" SEC-060)"
[ "$r" = "ERROR" ] && pass "CASE O5: malformed osv-scanner report -> ERROR" || fail "CASE O5: expected ERROR, got $r"

# CASE O6: osv-scanner is not authorized for SEC-061/SEC-062 even though
# both list DEPENDENCY_SCANNER -- requesting them returns nothing.
run_adapter "$OSV" "$FIXTURES/osv-clean.json" SEC-061 SEC-062
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "CASE O6: osv-scanner silently refuses unauthorized controls (SEC-061, SEC-062)" \
  || fail "CASE O6: expected empty runs for unauthorized controls, got $runs"

# ==================================================================
# Semgrep adapter
# ==================================================================

# CASE S1: clean scan where the mapped rules actually ran -> SATISFIED
# for both SEC-055 and SEC-056 -> both PASS (neither is dynamic_required
# or human_judgment_required, so STATIC_ANALYZER alone suffices).
run_adapter "$SEMGREP" "$FIXTURES/semgrep-clean-with-rules-run.json" SEC-055 SEC-056
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/s1.out.json"
r55="$(result_for "$TMP_DIR/s1.out.json" SEC-055)"
r56="$(result_for "$TMP_DIR/s1.out.json" SEC-056)"
[ "$r55" = "PASS" ] && [ "$r56" = "PASS" ] && pass "CASE S1: semgrep clean, rule-covered scan -> PASS for SEC-055 and SEC-056" \
  || fail "CASE S1: expected [PASS, PASS], got [$r55, $r56]"

# CASE S2: a real finding for a mapped rule -> VIOLATED -> FAIL for that
# control only (SEC-056 here); SEC-055's rule never ran in this fixture,
# so SEC-055 gets no contribution at all -> UNPROVEN, not silently PASS.
run_adapter "$SEMGREP" "$FIXTURES/semgrep-finding.json" SEC-055 SEC-056
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/s2.out.json"
r55="$(result_for "$TMP_DIR/s2.out.json" SEC-055)"
r56="$(result_for "$TMP_DIR/s2.out.json" SEC-056)"
[ "$r56" = "FAIL" ] && [ "$r55" = "MISSING" ] && pass "CASE S2: semgrep finding -> FAIL for the matched control; unrun rule leaves the other control unaddressed" \
  || fail "CASE S2: expected [MISSING, FAIL] for [SEC-055, SEC-056], got [$r55, $r56]"

# CASE S3: unmapped check_id -> silently dropped, no contribution for any
# control -- proves "unknown/unmapped output: UNPROVEN, never PASS."
run_adapter "$SEMGREP" "$FIXTURES/semgrep-unmapped-rule.json" SEC-055 SEC-056
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "CASE S3: unmapped semgrep check_id produces no contribution at all" \
  || fail "CASE S3: expected empty runs for an entirely-unmapped rule, got $runs"

# CASE S4: mapped rules exist in RULE_MAP but NONE of them actually ran in
# this scan (rules_run only lists an unrelated rule) -> "clean" is
# meaningless here, so no SATISFIED contribution is emitted -> UNPROVEN.
run_adapter "$SEMGREP" "$FIXTURES/semgrep-rule-not-run.json" SEC-056
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "CASE S4: semgrep clean result is not treated as evidence when the mapped rule never ran" \
  || fail "CASE S4: expected empty runs when the mapped rule didn't run, got $runs"

# CASE S5: tool missing -> no runs.
run_adapter "$SEMGREP" "$TMP_DIR/does-not-exist.json" SEC-056
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "CASE S5: semgrep tool-missing emits no runs" || fail "CASE S5: expected empty runs, got $runs"

# CASE S6: malformed report -> ERROR.
run_adapter "$SEMGREP" "$FIXTURES/semgrep-malformed.json" SEC-055 SEC-056
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
evaluate_runs "$runs" "$TMP_DIR/s6.out.json"
r55="$(result_for "$TMP_DIR/s6.out.json" SEC-055)"
r56="$(result_for "$TMP_DIR/s6.out.json" SEC-056)"
[ "$r55" = "ERROR" ] && [ "$r56" = "ERROR" ] && pass "CASE S6: malformed semgrep report -> ERROR for every requested control" \
  || fail "CASE S6: expected [ERROR, ERROR], got [$r55, $r56]"

# CASE S7: semgrep (STATIC_ANALYZER) is not authorized for SEC-001 (which
# needs SEMANTIC_REVIEW/DYNAMIC_API) -- requesting it returns nothing,
# and even if it somehow did, evidence_model.py's own capability check
# would independently reject a STATIC_ANALYZER contribution to SEC-001
# (defense in depth, proven already by test-evidence-model.sh CASE A).
run_adapter "$SEMGREP" "$FIXTURES/semgrep-clean-with-rules-run.json" SEC-001
runs="$(adapter_runs "$TMP_DIR/adapter_out.json")"
[ "$runs" = "[]" ] && pass "CASE S7: semgrep silently refuses an unauthorized control (SEC-001)" \
  || fail "CASE S7: expected empty runs for an unauthorized control, got $runs"

echo ""
echo "diana/security/adapters/test-adapters.sh: $pass_count passed, $fail_count failed"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
