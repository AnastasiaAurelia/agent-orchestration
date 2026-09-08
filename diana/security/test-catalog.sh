#!/usr/bin/env bash
set -euo pipefail

SEC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATALOG="$SEC_DIR/catalog.json"
VALIDATOR="$SEC_DIR/validate_catalog.py"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass_count=0
fail_count=0

expect_pass() {
  local name="$1" file="$2"
  if python3 "$VALIDATOR" "$file" >/dev/null 2>&1; then
    echo "PASS: $name"
    pass_count=$((pass_count + 1))
  else
    echo "FAIL: $name (expected validator PASS, got FAIL)" >&2
    python3 "$VALIDATOR" "$file" >&2 || true
    fail_count=$((fail_count + 1))
  fi
}

expect_fail() {
  local name="$1" file="$2"
  if python3 "$VALIDATOR" "$file" >/dev/null 2>&1; then
    echo "FAIL: $name (expected validator FAIL, got PASS)" >&2
    fail_count=$((fail_count + 1))
  else
    echo "PASS: $name"
    pass_count=$((pass_count + 1))
  fi
}

# CASE A -- canonical catalog validates
expect_pass "CASE A: canonical catalog validates" "$CATALOG"

# Helper: run a Python mutation over the canonical catalog and write it to a
# temp file, for the malformed-input cases below.
mutate() {
  local out="$1" pyexpr="$2"
  python3 -c "
import json
with open('$CATALOG') as f:
    catalog = json.load(f)
$pyexpr
with open('$out', 'w') as f:
    json.dump(catalog, f)
"
}

# CASE B -- duplicate SEC id
B="$TMP_DIR/case_b.json"
mutate "$B" "catalog['controls'][1]['id'] = catalog['controls'][0]['id']"
expect_fail "CASE B: duplicate SEC id" "$B"

# CASE C -- one control removed
C="$TMP_DIR/case_c.json"
mutate "$C" "catalog['controls'].pop()"
expect_fail "CASE C: one control removed (count/sequence != 75)" "$C"

# CASE D -- numbering gap (renumber SEC-002 to SEC-076, leaving a gap at 2
# and an out-of-range id/source_number, while keeping the list at 75 items)
D="$TMP_DIR/case_d.json"
mutate "$D" "
c = catalog['controls'][1]
c['id'] = 'SEC-076'
c['source_number'] = 76
"
expect_fail "CASE D: numbering gap" "$D"

# CASE E -- invalid severity
E="$TMP_DIR/case_e.json"
mutate "$E" "catalog['controls'][0]['severity'] = 'SEVERE'"
expect_fail "CASE E: invalid severity" "$E"

# CASE F -- empty required_evidence
F="$TMP_DIR/case_f.json"
mutate "$F" "catalog['controls'][0]['required_evidence'] = []"
expect_fail "CASE F: empty required_evidence" "$F"

# CASE G -- unknown verifier type
G="$TMP_DIR/case_g.json"
mutate "$G" "catalog['controls'][0]['verification']['modes'] = ['NMAP_SCAN']"
expect_fail "CASE G: unknown verifier type" "$G"

# CASE H -- dynamic_required=true but no dynamic verifier
H="$TMP_DIR/case_h.json"
mutate "$H" "
c = catalog['controls'][0]
c['verification']['dynamic_required'] = True
c['verification']['modes'] = ['SEMANTIC_REVIEW']
"
expect_fail "CASE H: dynamic_required=true but no dynamic verifier" "$H"

# CASE I -- source-derived spot checks
assert_spot_check() {
  local id="$1" expected_number="$2" expected_title="$3" expected_severity="$4"
  python3 -c "
import json, sys
catalog = json.load(open('$CATALOG'))
by_id = {c['id']: c for c in catalog['controls']}
c = by_id.get('$id')
assert c is not None, f'$id not found in catalog'
assert c['source_number'] == $expected_number, f\"$id source_number {c['source_number']} != $expected_number\"
assert c['title'] == '''$expected_title''', f\"$id title {c['title']!r} != '$expected_title'\"
assert c['severity'] == '$expected_severity', f\"$id severity {c['severity']} != $expected_severity\"
"
}

spot_check_failed=0
assert_spot_check "SEC-001" 1 "Broken Object Level Authorization (IDOR/BOLA)" "HIGH" || spot_check_failed=1
assert_spot_check "SEC-008" 8 "SQL Injection" "CRITICAL" || spot_check_failed=1
assert_spot_check "SEC-060" 60 "Dependency With Known Vulnerabilities" "HIGH" || spot_check_failed=1
assert_spot_check "SEC-066" 66 "Database Row-Level Security Misconfiguration" "CRITICAL" || spot_check_failed=1
assert_spot_check "SEC-068" 68 "Webhook Signature Verification Missing" "CRITICAL" || spot_check_failed=1
assert_spot_check "SEC-070" 70 "Payment Amount / Price Tampering" "CRITICAL" || spot_check_failed=1
assert_spot_check "SEC-074" 74 "Insecure AI/LLM Tool Authorization" "CRITICAL" || spot_check_failed=1
assert_spot_check "SEC-075" 75 "Prompt Injection Causing Unsafe Tool Use" "HIGH" || spot_check_failed=1

if [ "$spot_check_failed" -eq 0 ]; then
  echo "PASS: CASE I: source-derived spot checks"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: CASE I: source-derived spot checks" >&2
  fail_count=$((fail_count + 1))
fi

# CASE J -- source ordering (controls array is ordered SEC-001..SEC-075)
if python3 -c "
import json, sys
catalog = json.load(open('$CATALOG'))
ids = [c['id'] for c in catalog['controls']]
expected = [f'SEC-{n:03d}' for n in range(1, 76)]
sys.exit(0 if ids == expected else 1)
"; then
  echo "PASS: CASE J: source ordering (SEC-001..SEC-075)"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: CASE J: source ordering (SEC-001..SEC-075)" >&2
  fail_count=$((fail_count + 1))
fi

echo ""
echo "diana/security/test-catalog.sh: $pass_count passed, $fail_count failed"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
