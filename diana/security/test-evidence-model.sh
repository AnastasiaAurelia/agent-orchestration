#!/usr/bin/env bash
set -euo pipefail

SEC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATALOG="$SEC_DIR/catalog.json"
MODEL="$SEC_DIR/evidence_model.py"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass_count=0
fail_count=0

# Real required_evidence strings for SEC-001 (Broken Object Level
# Authorization), used verbatim below so evidence items link to the actual
# Phase 0 catalog contract rather than made-up text.
REQ1="server-side object ownership/permission check exists on every read/write route"
REQ2="cross-account negative access test (user A cannot access user B's object by id)"

get_result() {
  # get_result <runs_json_file> <index> -> prints the "result" field of
  # results[index] as a bare string.
  python3 -c "
import json, sys
data = json.load(open(sys.argv[1]))
print(data['results'][int(sys.argv[2])]['result'])
" "$1" "$2"
}

run_model() {
  local runs_file="$1" out_file="$2"
  python3 "$MODEL" "$CATALOG" "$runs_file" > "$out_file"
}

slugify() {
  echo "$1" | tr -c '[:alnum:]' '_'
}

expect_result() {
  local name="$1" runs_json="$2" index="$3" expected="$4"
  local slug
  slug="$(slugify "$name")"
  local runs_file="$TMP_DIR/${slug}.json"
  local out_file="$TMP_DIR/${slug}.out.json"
  echo "$runs_json" > "$runs_file"
  if ! run_model "$runs_file" "$out_file"; then
    echo "FAIL: $name (evidence_model.py exited non-zero unexpectedly)" >&2
    cat "$out_file" >&2
    fail_count=$((fail_count + 1))
    return
  fi
  local actual
  actual="$(get_result "$out_file" "$index")"
  if [ "$actual" = "$expected" ]; then
    echo "PASS: $name -> $expected"
    pass_count=$((pass_count + 1))
  else
    echo "FAIL: $name (expected $expected, got $actual)" >&2
    cat "$out_file" >&2
    fail_count=$((fail_count + 1))
  fi
}

# --- CASE 1: PASS requires ALL required evidence, not just some ---
# Only one of SEC-001's two required_evidence items is present -> UNPROVEN,
# proving PASS cannot be emitted with missing required evidence.
expect_result "CASE 1: partial evidence cannot yield PASS" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_partial"},
    "evidence": [
      {"requirement": "'"$REQ1"'", "status": "SATISFIED", "provenance": "code review"}
    ],
    "tool_error": null
  }
]' 0 "UNPROVEN"

# --- CASE 2: missing verifier evidence entirely => UNPROVEN, not PASS ---
expect_result "CASE 2: no evidence at all -> UNPROVEN" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_empty"},
    "evidence": [],
    "tool_error": null
  }
]' 0 "UNPROVEN"

# --- CASE 3: tool failure => ERROR ---
expect_result "CASE 3: tool/execution failure -> ERROR" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_crash"},
    "evidence": [],
    "tool_error": {"message": "connection refused: verification target unreachable"}
  }
]' 0 "ERROR"

# --- CASE 4: irrelevant control => NOT_APPLICABLE ---
expect_result "CASE 4: not applicable -> NOT_APPLICABLE" '[
  {
    "control_id": "SEC-060",
    "applicability": "NOT_APPLICABLE",
    "verifier": {"type": "DEPENDENCY_SCANNER", "identity": "scan::no-manifest"},
    "evidence": [],
    "tool_error": null
  }
]' 0 "NOT_APPLICABLE"

# --- CASE 5: explicit violated evidence => FAIL ---
expect_result "CASE 5: explicit violation -> FAIL" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_violation"},
    "evidence": [
      {"requirement": "'"$REQ1"'", "status": "VIOLATED", "provenance": "code review found no ownership check on GET /orders/:id"},
      {"requirement": "'"$REQ2"'", "status": "SATISFIED", "provenance": "negative access test"}
    ],
    "tool_error": null
  }
]' 0 "FAIL"

# --- CASE 6a: malformed evidence (invalid status enum) fails closed to ERROR, never PASS ---
expect_result "CASE 6a: invalid evidence status fails closed" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_malformed"},
    "evidence": [
      {"requirement": "'"$REQ1"'", "status": "MAYBE", "provenance": "unclear"}
    ],
    "tool_error": null
  }
]' 0 "ERROR"

# --- CASE 6b: malformed evidence (unknown control_id) fails closed to ERROR ---
expect_result "CASE 6b: unknown control_id fails closed" '[
  {
    "control_id": "SEC-999",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_unknown"},
    "evidence": [],
    "tool_error": null
  }
]' 0 "ERROR"

# --- CASE 6c: malformed evidence (evidence item's requirement text does not
# belong to the control's contract) fails closed to ERROR, never PASS ---
expect_result "CASE 6c: evidence item requirement not in contract fails closed" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_wrong_requirement"},
    "evidence": [
      {"requirement": "this text does not match any SEC-001 required_evidence item", "status": "SATISFIED", "provenance": "code review"}
    ],
    "tool_error": null
  }
]' 0 "ERROR"

# --- CASE 7: full satisfaction of every required_evidence item => PASS ---
expect_result "CASE 7: all required evidence satisfied -> PASS" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_full"},
    "evidence": [
      {"requirement": "'"$REQ1"'", "status": "SATISFIED", "provenance": "code review of order.py:42"},
      {"requirement": "'"$REQ2"'", "status": "SATISFIED", "provenance": "negative test tests/test_bola.py::test_cross_account_denied"}
    ],
    "tool_error": null
  }
]' 0 "PASS"

# --- CASE 8: applicability UNKNOWN => UNPROVEN, never PASS ---
expect_result "CASE 8: unknown applicability -> UNPROVEN" '[
  {
    "control_id": "SEC-001",
    "applicability": "UNKNOWN",
    "verifier": {"type": "SEMANTIC_REVIEW", "identity": "reviewer::unassessed"},
    "evidence": [],
    "tool_error": null
  }
]' 0 "UNPROVEN"

# --- CASE 9: a batch mixes a well-formed run and a malformed run; the
# malformed run fails closed to ERROR without preventing the well-formed
# run's own result from being reported ---
BATCH_FILE="$TMP_DIR/case9_batch.json"
cat > "$BATCH_FILE" <<EOF
[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_full"},
    "evidence": [
      {"requirement": "$REQ1", "status": "SATISFIED", "provenance": "code review"},
      {"requirement": "$REQ2", "status": "SATISFIED", "provenance": "negative test"}
    ],
    "tool_error": null
  },
  {
    "control_id": "SEC-001",
    "applicability": "not-a-real-value",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_bad_applicability"},
    "evidence": [],
    "tool_error": null
  }
]
EOF
BATCH_OUT="$TMP_DIR/case9_batch.out.json"
if run_model "$BATCH_FILE" "$BATCH_OUT"; then
  r0="$(get_result "$BATCH_OUT" 0)"
  r1="$(get_result "$BATCH_OUT" 1)"
  if [ "$r0" = "PASS" ] && [ "$r1" = "ERROR" ]; then
    echo "PASS: CASE 9: mixed batch (well-formed PASS survives, malformed run fails closed to ERROR)"
    pass_count=$((pass_count + 1))
  else
    echo "FAIL: CASE 9: expected [PASS, ERROR], got [$r0, $r1]" >&2
    fail_count=$((fail_count + 1))
  fi
else
  echo "FAIL: CASE 9: evidence_model.py exited non-zero on a batch with one malformed run" >&2
  fail_count=$((fail_count + 1))
fi

# --- CASE 10: tool-level fail-closed on a malformed top-level runs file
# (not a JSON array at all) -> exit 1, error field only, no results array ---
BAD_TOPLEVEL="$TMP_DIR/case10_not_a_list.json"
echo '{"not": "a list"}' > "$BAD_TOPLEVEL"
set +e
python3 "$MODEL" "$CATALOG" "$BAD_TOPLEVEL" > "$TMP_DIR/case10.out.json"
status=$?
set -e
if [ "$status" -eq 1 ] && python3 -c "
import json, sys
d = json.load(open('$TMP_DIR/case10.out.json'))
sys.exit(0 if ('error' in d and 'results' not in d) else 1)
"; then
  echo "PASS: CASE 10: tool-level fail-closed on non-array runs file"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: CASE 10: expected exit 1 with error-only JSON on non-array runs file" >&2
  fail_count=$((fail_count + 1))
fi

# --- CASE 11: tool-level fail-closed on an unreadable catalog path ---
set +e
python3 "$MODEL" "$TMP_DIR/does-not-exist.json" "$BATCH_FILE" > "$TMP_DIR/case11.out.json"
status=$?
set -e
if [ "$status" -eq 1 ] && python3 -c "
import json, sys
d = json.load(open('$TMP_DIR/case11.out.json'))
sys.exit(0 if ('error' in d and 'results' not in d) else 1)
"; then
  echo "PASS: CASE 11: tool-level fail-closed on missing catalog file"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: CASE 11: expected exit 1 with error-only JSON on missing catalog file" >&2
  fail_count=$((fail_count + 1))
fi

# --- CASE 12: provenance/reasons are present and non-empty on every result
# (required concept: reason for UNPROVEN, provenance carried through) ---
PROV_FILE="$TMP_DIR/case12.json"
cat > "$PROV_FILE" <<EOF
[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_partial"},
    "evidence": [
      {"requirement": "$REQ1", "status": "SATISFIED", "provenance": "code review of order.py:42"}
    ],
    "tool_error": null
  }
]
EOF
PROV_OUT="$TMP_DIR/case12.out.json"
run_model "$PROV_FILE" "$PROV_OUT"
if python3 -c "
import json, sys
out_path, req2 = sys.argv[1], sys.argv[2]
d = json.load(open(out_path))
r = d['results'][0]
assert r['result'] == 'UNPROVEN'
assert isinstance(r['reasons'], list) and len(r['reasons']) > 0
assert any(req2 in reason for reason in r['reasons'])
assert r['evidence'][0]['provenance'] == 'code review of order.py:42'
" "$PROV_OUT" "$REQ2"; then
  echo "PASS: CASE 12: UNPROVEN carries a specific reason and evidence provenance"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: CASE 12: UNPROVEN reason/provenance missing or wrong" >&2
  fail_count=$((fail_count + 1))
fi

# --- CASE 13: observed_at is an optional pass-through only -- omitting it
# entirely still produces a valid result, proving no wall-clock dependency ---
expect_result "CASE 13: observed_at omitted entirely still evaluates" '[
  {
    "control_id": "SEC-060",
    "applicability": "NOT_APPLICABLE",
    "verifier": {"type": "DEPENDENCY_SCANNER", "identity": "scan::no-manifest"},
    "evidence": [],
    "tool_error": null
  }
]' 0 "NOT_APPLICABLE"

echo ""
echo "diana/security/test-evidence-model.sh: $pass_count passed, $fail_count failed"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
