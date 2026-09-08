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
  python3 "$MATRIX_PY" "$CATALOG" "$REPO" "$BASE_SHA" "$TARGET_SHA"
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

# M6: live_execution_exists is False for every control -- track-wide,
# honest fact (no phase has ever installed/run a live tool, browser, DB,
# or model).
all_no_live="$(python3 -c "
import json
m = json.load(open('$TMP_DIR/matrix.json'))
print(all(r['live_execution_exists'] is False for r in m['rows']) and m['live_execution_wired_count'] == 0)
")"
if [ "$all_no_live" = "True" ]; then
  pass "M6: live_execution_exists is False for all 75 controls, live_execution_wired_count = 0"
else
  fail "M6: expected live_execution_exists=False uniformly and wired_count=0"
fi

# M7: capability_coverage_counts sum to 75 and match the known, manually-
# cross-checked distribution for the current catalog + Phase 2-4
# implementation (regression pin -- a real change to any adapter,
# scenario registry, or reviewer authorization should change these
# numbers, and this test should be updated deliberately, not silently).
coverage_ok="$(python3 -c "
import json
m = json.load(open('$TMP_DIR/matrix.json'))
c = m['capability_coverage_counts']
total = c['FULLY_COVERED'] + c['PARTIALLY_COVERED'] + c['NOT_COVERED']
print(total == 75 and c == {'FULLY_COVERED': 21, 'PARTIALLY_COVERED': 25, 'NOT_COVERED': 29})
")"
if [ "$coverage_ok" = "True" ]; then
  pass "M7: capability_coverage_counts = {FULLY_COVERED:21, PARTIALLY_COVERED:25, NOT_COVERED:29}, sum=75"
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

# M12: identity check against the real, independently-invoked
# security_bundle.py CLI -- the matrix's resulting_state must match
# exactly what a direct, separate invocation of the shipped pipeline
# produces (proving coverage_matrix.py isn't silently diverging from the
# real Security Gate evaluation it's reporting on).
python3 "$SEC_DIR/ci_verifier_runs.py" > "$TMP_DIR/real-runs.json"
python3 "$SEC_DIR/security_bundle.py" "$CATALOG" "$TMP_DIR/real-runs.json" "$REPO" "$BASE_SHA" "$TARGET_SHA" > "$TMP_DIR/real-bundle.json"
identity_check="$(python3 -c "
import json
bundle = json.load(open('$TMP_DIR/real-bundle.json'))
matrix = json.load(open('$TMP_DIR/matrix.json'))
bundle_states = {r['control_id']: r['result'] for r in bundle['results']}
matrix_states = {r['control_id']: r['resulting_state'] for r in matrix['rows']}
print(bundle_states == matrix_states)
")"
if [ "$identity_check" = "True" ]; then
  pass "M12: matrix resulting_state matches a direct, independent security_bundle.py invocation exactly"
else
  fail "M12: matrix resulting_state diverges from a direct security_bundle.py invocation"
fi

echo ""
echo "diana/security/test-coverage-matrix.sh: $pass_count passed, $fail_count failed"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
