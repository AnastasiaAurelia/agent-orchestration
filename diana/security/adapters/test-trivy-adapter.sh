#!/usr/bin/env bash
set -euo pipefail

ADAPTERS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEC_DIR="$(dirname "$ADAPTERS_DIR")"
CATALOG="$SEC_DIR/catalog.json"
EVIDENCE_MODEL="$SEC_DIR/evidence_model.py"
FIXTURES="$ADAPTERS_DIR/fixtures"
TRIVY="$ADAPTERS_DIR/trivy_adapter.py"

EXPECTED_FULL_REPO_MANIFESTS="$FIXTURES/expected-target-full-repo-with-manifests.json"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass_count=0
fail_count=0
pass() { echo "PASS: $1"; pass_count=$((pass_count + 1)); }
fail() { echo "FAIL: $1" >&2; fail_count=$((fail_count + 1)); }

run_adapter() {
  local artifact="$1" expected="$2"; shift 2
  python3 "$TRIVY" "$artifact" "$expected" "$@" > "$TMP_DIR/out.json"
}

runs_array() {
  python3 -c "
import json
print(json.dumps(json.load(open('$1'))['runs']))
"
}

contribution_status() {
  python3 -c "
import json, sys
d = json.load(open('$1'))
for r in d['runs']:
    if r['control_id'] == sys.argv[1]:
        print(r['evidence'][0]['status'] if r['evidence'] else 'NONE')
        sys.exit(0)
print('NONE')
" "$2"
}

tool_error_present() {
  python3 -c "
import json, sys
d = json.load(open('$1'))
for r in d['runs']:
    if r['control_id'] == sys.argv[1]:
        sys.exit(0 if r.get('tool_error') is not None else 1)
sys.exit(1)
" "$2"
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

check_contribution() {
  local name="$1" artifact="$2" expected="$3" control_id="$4" expected_status="$5"
  run_adapter "$artifact" "$expected" "$control_id"
  local actual
  actual="$(contribution_status "$TMP_DIR/out.json" "$control_id")"
  if [ "$actual" = "$expected_status" ]; then
    pass "$name -> $expected_status"
  else
    fail "$name (expected $expected_status, got $actual)"
    cat "$TMP_DIR/out.json" >&2
  fi
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

# CASE 1: tool missing (no artifact) -> UNPROVEN
check_aggregate "CASE 1: tool unavailable" - "$EXPECTED_FULL_REPO_MANIFESTS" UNPROVEN SEC-060

# CASE 2: clean, full manifest coverage -> PASS
check_aggregate "CASE 2: clean full coverage" \
  "$FIXTURES/trivy-artifact-clean-full-coverage.json" "$EXPECTED_FULL_REPO_MANIFESTS" PASS SEC-060

# CASE 3: known CRITICAL/HIGH finding, full coverage -> FAIL
check_aggregate "CASE 3: finding high full coverage" \
  "$FIXTURES/trivy-artifact-finding-high-full-coverage.json" "$EXPECTED_FULL_REPO_MANIFESTS" FAIL SEC-060

# CASE 4: malformed report (no Results key, no null) -> ERROR
check_aggregate "CASE 4: malformed report" \
  "$FIXTURES/trivy-artifact-malformed-report.json" "$EXPECTED_FULL_REPO_MANIFESTS" ERROR SEC-060

# CASE 4b: empty object report (Results key entirely absent) -> ERROR, never PASS
check_aggregate "CASE 4b: empty object report never PASS" \
  "$FIXTURES/trivy-artifact-empty-object-report.json" "$EXPECTED_FULL_REPO_MANIFESTS" ERROR SEC-060

# CASE 5: target mismatch (wrong commit) on a finding -> not attributed, no contribution -> UNPROVEN
check_aggregate "CASE 5: finding wrong commit not attributed" \
  "$FIXTURES/trivy-artifact-finding-high-wrong-commit.json" "$EXPECTED_FULL_REPO_MANIFESTS" UNPROVEN SEC-060

# CASE 6: target mismatch (wrong repo) on a finding -> not attributed -> UNPROVEN
check_aggregate "CASE 6: finding wrong repo not attributed" \
  "$FIXTURES/trivy-artifact-finding-high-wrong-repo.json" "$EXPECTED_FULL_REPO_MANIFESTS" UNPROVEN SEC-060

# CASE 7: unsupported/unrecognized severity value -> structural ERROR, never silently dropped
check_aggregate "CASE 7: unrecognized severity" \
  "$FIXTURES/trivy-artifact-unrecognized-severity.json" "$EXPECTED_FULL_REPO_MANIFESTS" ERROR SEC-060

# CASE 8: timeout / crash is represented identically to tool-unavailable by
# the live-wiring layer (ci_verifier_runs.py), not this adapter -- the
# adapter-level equivalent is "no artifact produced", already covered by
# CASE 1. Documented here so the mapping from spec requirement -> test is
# explicit and not silently missing.
pass "CASE 8: timeout/crash equivalence documented (see ci_verifier_runs live-wiring tests)"

# CASE 9: low-only findings, full coverage -> PASS (no unresolved CRITICAL/HIGH)
check_aggregate "CASE 9: low-only finding full coverage" \
  "$FIXTURES/trivy-artifact-low-only-full-coverage.json" "$EXPECTED_FULL_REPO_MANIFESTS" PASS SEC-060

# CASE 10: incomplete manifest coverage, clean report -> UNPROVEN (coverage not proven complete)
check_aggregate "CASE 10: clean but incomplete manifest coverage" \
  "$FIXTURES/trivy-artifact-clean-incomplete-manifests.json" "$EXPECTED_FULL_REPO_MANIFESTS" UNPROVEN SEC-060

# CASE 11: wrong declared tool name (osv-scanner report fed to Trivy adapter) -> ERROR
check_aggregate "CASE 11: wrong tool name rejected" \
  "$FIXTURES/trivy-artifact-wrong-tool-name.json" "$EXPECTED_FULL_REPO_MANIFESTS" ERROR SEC-060

# CASE 12: adapter capability violation -- requesting a control this adapter
# is not authorized for returns zero runs, never fabricated evidence.
python3 "$TRIVY" "$FIXTURES/trivy-artifact-clean-full-coverage.json" "$EXPECTED_FULL_REPO_MANIFESTS" SEC-061 > "$TMP_DIR/unauth.json"
python3 -c "
import json
d = json.load(open('$TMP_DIR/unauth.json'))
assert d['runs'] == [], d['runs']
" && pass "CASE 12: unauthorized control (SEC-061) yields zero runs" || fail "CASE 12: unauthorized control (SEC-061) yields zero runs"

# CASE 13: a config-class (IaC misconfiguration) finding in the SAME report
# must NEVER be treated as SEC-060 dependency evidence -- this adapter is
# not authorized for a misconfiguration claim at all (see module docstring).
check_aggregate "CASE 13: config-class finding not conflated with dependency evidence" \
  "$FIXTURES/trivy-artifact-config-class-finding-only.json" "$EXPECTED_FULL_REPO_MANIFESTS" PASS SEC-060

echo ""
echo "test-trivy-adapter.sh: $pass_count passed, $fail_count failed"
[ "$fail_count" -eq 0 ]
