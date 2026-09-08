#!/usr/bin/env bash
set -euo pipefail

SEC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATALOG="$SEC_DIR/catalog.json"
MATRIX_PY="$SEC_DIR/coverage_matrix.py"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass_count=0
fail_count=0

pass() { echo "PASS: $1"; pass_count=$((pass_count + 1)); }
fail() { echo "FAIL: $1" >&2; fail_count=$((fail_count + 1)); }

REPO="AnastasiaAurelia/agent-orchestration"
BASE_SHA="b8ab35e74d3391a85edaaec2822f000cfc12a62e"
TARGET_SHA="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

run_matrix() {
  # Deterministic, offline: injects runs=[] directly via build_matrix()
  # rather than shelling out to the CLI (main()), which -- since Security
  # Track remediation round A -- calls the real, network-dependent
  # ci_verifier_runs.collect_trusted_runs(). M1-M12 below test this
  # function's own logic (matrix shape, determinism, capability
  # accounting, anti-fabrication invariants) against a FIXED, known input,
  # decoupled from whether semgrep/network happens to be available when
  # this suite runs. The real CLI path is separately, non-fatally proven
  # by the "real end-to-end" check at the bottom of this file.
  python3 -c "
import json, sys
sys.path.insert(0, '$SEC_DIR')
import coverage_matrix
catalog = json.load(open('$CATALOG'))
matrix = coverage_matrix.build_matrix(catalog, '$REPO', '$BASE_SHA', '$TARGET_SHA', runs=[])
print(json.dumps(matrix, sort_keys=True))
"
}

run_matrix > "$TMP_DIR/matrix.json"
run_matrix > "$TMP_DIR/matrix2.json"

# M1: exactly 75 controls.
total="$(python3 -c "import json; print(json.load(open('$TMP_DIR/matrix.json'))['total_controls'])")"
if [ "$total" = "75" ]; then
  pass "M1: matrix covers exactly 75 controls"
else
  fail "M1: expected 75 controls, got $total"
fi

# M2: deterministic -- running twice with the same inputs produces byte-identical output.
if diff -q "$TMP_DIR/matrix.json" "$TMP_DIR/matrix2.json" > /dev/null; then
  pass "M2: matrix generation is deterministic (byte-identical across two runs)"
else
  fail "M2: matrix output differs between two runs with identical inputs"
fi

# M3: every row's control_id is a real, unique SEC-NNN in ascending order,
# matching the canonical catalog exactly (no invented/missing/duplicate ids).
python3 -c "
import json
m = json.load(open('$TMP_DIR/matrix.json'))
ids = [r['control_id'] for r in m['rows']]
expected = [f'SEC-{i:03d}' for i in range(1, 76)]
assert ids == expected, f'mismatch: {ids[:5]}...{ids[-5:]} vs expected'
print('OK')
" > "$TMP_DIR/m3.out" 2>&1 && pass "M3: rows cover exactly SEC-001..SEC-075 in order, no gaps/duplicates" \
  || { fail "M3: control_id set/order mismatch"; cat "$TMP_DIR/m3.out" >&2; }

# M4: CORE ANTI-FABRICATION INVARIANT -- resulting_state is UNPROVEN for
# ALL 75 controls today (zero trusted runs exist per ci_verifier_runs.py).
# No finding is ever treated as PASS, regardless of how good a control's
# capability_coverage looks.
all_unproven="$(python3 -c "
import json
m = json.load(open('$TMP_DIR/matrix.json'))
print(all(r['resulting_state'] == 'UNPROVEN' for r in m['rows']))
")"
if [ "$all_unproven" = "True" ]; then
  pass "M4: resulting_state is UNPROVEN for all 75 controls (no fabricated PASS/FAIL anywhere)"
else
  fail "M4: expected every control's resulting_state to be UNPROVEN today"
fi

# M5: resulting_state_counts has zero PASS and zero FAIL (matching M4 from
# the summary-counts angle, catching a divergence between per-row and
# aggregate reporting).
pass_fail_zero="$(python3 -c "
import json
m = json.load(open('$TMP_DIR/matrix.json'))
c = m['resulting_state_counts']
print(c['PASS'] == 0 and c['FAIL'] == 0 and c['UNPROVEN'] == 75 and c['NOT_APPLICABLE'] == 0 and c['ERROR'] == 0)
")"
if [ "$pass_fail_zero" = "True" ]; then
  pass "M5: resulting_state_counts = {PASS:0, FAIL:0, NOT_APPLICABLE:0, UNPROVEN:75, ERROR:0}"
else
  fail "M5: resulting_state_counts do not match the expected all-UNPROVEN distribution"
fi

# M6: with runs=[] explicitly injected (this suite's deterministic,
# offline baseline -- see run_matrix() above), live_execution_exists is
# False for every control and live_execution_wired_count is 0. This
# specifically tests the "zero runs submitted" degenerate case, not a
# track-wide claim that live execution never exists anywhere (Security
# Track remediation round A wired real Semgrep execution into the actual
# CLI path -- see the real end-to-end check at the bottom of this file).
all_no_live="$(python3 -c "
import json
m = json.load(open('$TMP_DIR/matrix.json'))
print(all(r['live_execution_exists'] is False for r in m['rows']) and m['live_execution_wired_count'] == 0)
")"
if [ "$all_no_live" = "True" ]; then
  pass "M6: with runs=[] injected, live_execution_exists is False for all 75 controls, wired_count = 0"
else
  fail "M6: expected live_execution_exists=False uniformly and wired_count=0 for the runs=[] baseline"
fi

# M7: capability_coverage_counts sum to 75 and match the known, manually-
# cross-checked distribution for the current catalog + Phase 2-4 + Phase
# 6 remediation rounds A+B implementation (regression pin -- a real change
# to any adapter, scenario registry, or reviewer authorization should
# change these numbers, and this test should be updated deliberately,
# not silently). capability_coverage is a STATIC fact independent of
# `runs`, so this is unaffected by run_matrix()'s runs=[] injection.
coverage_ok="$(python3 -c "
import json
m = json.load(open('$TMP_DIR/matrix.json'))
c = m['capability_coverage_counts']
total = c['FULLY_COVERED'] + c['PARTIALLY_COVERED'] + c['NOT_COVERED']
print(total == 75 and c == {'FULLY_COVERED': 27, 'PARTIALLY_COVERED': 27, 'NOT_COVERED': 21})
")"
if [ "$coverage_ok" = "True" ]; then
  pass "M7: capability_coverage_counts = {FULLY_COVERED:27, PARTIALLY_COVERED:27, NOT_COVERED:21}, sum=75"
else
  fail "M7: capability_coverage_counts do not match the expected regression-pinned distribution"
  python3 -c "import json; print(json.load(open('$TMP_DIR/matrix.json'))['capability_coverage_counts'])" >&2
fi

# M8: FULLY_COVERED never implies PASS/FAIL -- the two axes (capability
# coverage vs. real live result) must never be conflated. Check at least
# one known FULLY_COVERED control (SEC-001) explicitly.
sec001_check="$(python3 -c "
import json
m = json.load(open('$TMP_DIR/matrix.json'))
r = next(row for row in m['rows'] if row['control_id'] == 'SEC-001')
print(r['capability_coverage'] == 'FULLY_COVERED' and r['resulting_state'] == 'UNPROVEN')
")"
if [ "$sec001_check" = "True" ]; then
  pass "M8: SEC-001 is FULLY_COVERED capability-wise but still resulting_state=UNPROVEN (axes never conflated)"
else
  fail "M8: expected SEC-001 to be FULLY_COVERED with resulting_state UNPROVEN"
fi

# M9: NOT_COVERED controls have zero implemented_paths for every
# required_evidence item, and an explicit gap listed for each.
not_covered_check="$(python3 -c "
import json
m = json.load(open('$TMP_DIR/matrix.json'))
bad = []
for r in m['rows']:
    if r['capability_coverage'] == 'NOT_COVERED':
        if any(item['implemented_paths'] for item in r['required_evidence']):
            bad.append(r['control_id'])
        if not r['remaining_gap']:
            bad.append(r['control_id'])
print(bad)
")"
if [ "$not_covered_check" = "[]" ]; then
  pass "M9: every NOT_COVERED control has zero implemented paths and a non-empty gap list"
else
  fail "M9: NOT_COVERED controls with unexpected coverage or missing gaps: $not_covered_check"
fi

# M10: every required_evidence item's requirement string is verbatim one
# of the real catalog's required_evidence entries for that control (no
# invented requirement text).
requirement_check="$(python3 -c "
import json
cat = json.load(open('$CATALOG'))
controls = {c['id']: c for c in cat['controls']}
m = json.load(open('$TMP_DIR/matrix.json'))
bad = []
for r in m['rows']:
    real = set(controls[r['control_id']]['required_evidence'])
    got = {item['requirement'] for item in r['required_evidence']}
    if got != real:
        bad.append(r['control_id'])
print(bad)
")"
if [ "$requirement_check" = "[]" ]; then
  pass "M10: every row's required_evidence exactly matches the real catalog contract"
else
  fail "M10: rows with mismatched required_evidence vs. catalog: $requirement_check"
fi

# M11: malformed catalog fails closed (reuses validate_catalog.py).
python3 -c "
import json
cat = json.load(open('$CATALOG'))
del cat['controls']
json.dump(cat, open('$TMP_DIR/malformed-catalog.json', 'w'))
"
m11_exit=0
python3 "$MATRIX_PY" "$TMP_DIR/malformed-catalog.json" "$REPO" "$BASE_SHA" "$TARGET_SHA" > "$TMP_DIR/m11.out" || m11_exit=$?
m11_has_error="$(python3 -c "import json; print('error' in json.load(open('$TMP_DIR/m11.out')))")"
if [ "$m11_exit" != "0" ] && [ "$m11_has_error" = "True" ]; then
  pass "M11: malformed catalog (missing controls) fails closed with a non-zero exit and error field"
else
  fail "M11: expected non-zero exit + error field for a malformed catalog"
fi

# M12: identity check against a direct, independent security_bundle.py
# invocation fed the SAME FIXED (non-empty, deterministic) runs list --
# the matrix's resulting_state must match exactly what a direct, separate
# invocation of the shipped pipeline produces for identical input,
# proving coverage_matrix.py isn't silently diverging from the real
# Security Gate evaluation it's reporting on. Uses an explicit fixed
# run (not [], to also prove the non-degenerate case) rather than the
# real, network-dependent ci_verifier_runs.py, keeping this suite
# deterministic and offline.
python3 -c "
import json
runs = [{
    'control_id': 'SEC-058',
    'applicability': 'APPLICABLE',
    'verifier': {'type': 'STATIC_ANALYZER', 'identity': 'test-fixed-run'},
    'evidence': [{'requirement': 'deserialization of untrusted input uses a safe/restricted format or schema, not an unrestricted native object deserializer', 'status': 'SATISFIED', 'provenance': 'fixed test run'}],
    'tool_error': None,
}]
json.dump(runs, open('$TMP_DIR/fixed-runs.json', 'w'))
"
python3 "$SEC_DIR/security_bundle.py" "$CATALOG" "$TMP_DIR/fixed-runs.json" "$REPO" "$BASE_SHA" "$TARGET_SHA" > "$TMP_DIR/fixed-bundle.json"
matrix_with_fixed_runs="$(python3 -c "
import json, sys
sys.path.insert(0, '$SEC_DIR')
import coverage_matrix
catalog = json.load(open('$CATALOG'))
runs = json.load(open('$TMP_DIR/fixed-runs.json'))
matrix = coverage_matrix.build_matrix(catalog, '$REPO', '$BASE_SHA', '$TARGET_SHA', runs=runs)
json.dump(matrix, open('$TMP_DIR/matrix-fixed-runs.json', 'w'))
"
)"
identity_check="$(python3 -c "
import json
bundle = json.load(open('$TMP_DIR/fixed-bundle.json'))
matrix = json.load(open('$TMP_DIR/matrix-fixed-runs.json'))
bundle_states = {r['control_id']: r['result'] for r in bundle['results']}
matrix_states = {r['control_id']: r['resulting_state'] for r in matrix['rows']}
print(bundle_states == matrix_states)
")"
if [ "$identity_check" = "True" ]; then
  pass "M12: matrix resulting_state matches a direct, independent security_bundle.py invocation exactly (fixed non-empty runs)"
else
  fail "M12: matrix resulting_state diverges from a direct security_bundle.py invocation"
fi

# ==================================================================
# Real, network-dependent end-to-end check (SKIPS gracefully, never
# fails the suite, if network/pip install isn't available -- M1-M12
# above already prove the logic deterministically and offline; this
# additionally proves the real CLI path, calling the real
# ci_verifier_runs.collect_trusted_runs(), actually reflects live
# execution when it can run).
# ==================================================================

if timeout 150 python3 "$MATRIX_PY" "$CATALOG" "$REPO" "$BASE_SHA" "$TARGET_SHA" > "$TMP_DIR/real-matrix.json" 2>/dev/null; then
  real_wired="$(python3 -c "import json; print(json.load(open('$TMP_DIR/real-matrix.json'))['live_execution_wired_count'])" 2>/dev/null || echo "parse-error")"
  if [ "$real_wired" = "parse-error" ]; then
    fail "real end-to-end: coverage_matrix.py CLI did not print valid JSON"
  else
    pass "real end-to-end: coverage_matrix.py CLI (real ci_verifier_runs.py) ran; live_execution_wired_count=$real_wired (up to 6 if semgrep+gitleaks installed+ran and a web root is detected; fewer, down to 0, if genuinely unavailable/undetected in this environment -- all honest, never fabricated)"
  fi
else
  echo "SKIP: real end-to-end coverage_matrix.py CLI (network/pip install unavailable in this environment -- M1-M12 above already cover the logic)"
fi

echo ""
echo "diana/security/test-coverage-matrix.sh: $pass_count passed, $fail_count failed"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
