#!/usr/bin/env bash
set -euo pipefail

SEC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATALOG="$SEC_DIR/catalog.json"
MODEL="$SEC_DIR/evidence_model.py"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass_count=0
fail_count=0

# Real required_evidence strings, used verbatim so evidence items link to
# the actual Phase 0 catalog contract rather than made-up text.
SEC001_REQ1="server-side object ownership/permission check exists on every read/write route"
SEC001_REQ2="cross-account negative access test (user A cannot access user B's object by id)"
SEC041_REQ1="request bodies are bound through an explicit allow-list of mutable fields, never bulk-assigned to the full model"
SEC041_REQ2="negative test proving a privileged/protected field cannot be set via unexpected request body fields"
SEC066_REQ1="row-level security is enabled on tables containing user-scoped data, with policies enforced at the database layer"
SEC066_REQ2="runtime test with multiple identities proving one user's database session cannot read/write another user's rows"

slugify() {
  echo "$1" | tr -c '[:alnum:]' '_'
}

get_field() {
  # get_field <out_file> <index> <dotted.field.path>
  python3 -c "
import json, sys
data = json.load(open(sys.argv[1]))
r = data['results'][int(sys.argv[2])]
for key in sys.argv[3].split('.'):
    r = r[key]
print(json.dumps(r) if not isinstance(r, str) else r)
" "$1" "$2" "$3"
}

run_model() {
  local runs_file="$1" out_file="$2"
  python3 "$MODEL" "$CATALOG" "$runs_file" > "$out_file"
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
  actual="$(get_field "$out_file" "$index" "result")"
  if [ "$actual" = "$expected" ]; then
    echo "PASS: $name -> $expected"
    pass_count=$((pass_count + 1))
  else
    echo "FAIL: $name (expected $expected, got $actual)" >&2
    cat "$out_file" >&2
    fail_count=$((fail_count + 1))
  fi
}

# ==================================================================
# CASE 1-13: preserved/adapted from the original (uncorrected) Phase 1
# model. All still evaluate the same way under per-control aggregation
# because each uses exactly one run per control_id, which trivially
# reduces to the original per-run behavior -- EXCEPT CASE 9, which
# originally submitted two runs sharing one control_id to prove "a
# malformed run doesn't block another run's result." That scenario is
# superseded by this correction (same-control runs now aggregate, and a
# malformed contribution deliberately poisons that control's result --
# see CASE F/J below); CASE 9 here instead uses two DIFFERENT controls to
# preserve its original, still-valid intent: a malformed run for one
# control must not affect an unrelated control's result.
# ==================================================================

expect_result "CASE 1: partial evidence cannot yield PASS" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_partial"},
    "evidence": [
      {"requirement": "'"$SEC001_REQ2"'", "status": "SATISFIED", "provenance": "negative access test"}
    ],
    "tool_error": null
  }
]' 0 "UNPROVEN"

expect_result "CASE 2: no evidence at all -> UNPROVEN" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_empty"},
    "evidence": [],
    "tool_error": null
  }
]' 0 "UNPROVEN"

expect_result "CASE 3: tool/execution failure -> ERROR" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_crash"},
    "evidence": [],
    "tool_error": {"message": "connection refused: verification target unreachable"}
  }
]' 0 "ERROR"

expect_result "CASE 4: not applicable -> NOT_APPLICABLE" '[
  {
    "control_id": "SEC-060",
    "applicability": "NOT_APPLICABLE",
    "verifier": {"type": "DEPENDENCY_SCANNER", "identity": "scan::no-manifest"},
    "evidence": [],
    "tool_error": null
  }
]' 0 "NOT_APPLICABLE"

expect_result "CASE 5: explicit violation -> FAIL" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_violation"},
    "evidence": [
      {"requirement": "'"$SEC001_REQ1"'", "status": "VIOLATED", "provenance": "code review found no ownership check on GET /orders/:id"},
      {"requirement": "'"$SEC001_REQ2"'", "status": "SATISFIED", "provenance": "negative access test"}
    ],
    "tool_error": null
  }
]' 0 "FAIL"

expect_result "CASE 6a: invalid evidence status fails closed" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_malformed"},
    "evidence": [
      {"requirement": "'"$SEC001_REQ1"'", "status": "MAYBE", "provenance": "unclear"}
    ],
    "tool_error": null
  }
]' 0 "ERROR"

expect_result "CASE 6b: unknown control_id fails closed" '[
  {
    "control_id": "SEC-999",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_unknown"},
    "evidence": [],
    "tool_error": null
  }
]' 0 "ERROR"

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

expect_result "CASE 7: single comprehensive dynamic run -> PASS" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_full"},
    "evidence": [
      {"requirement": "'"$SEC001_REQ1"'", "status": "SATISFIED", "provenance": "code review of order.py:42"},
      {"requirement": "'"$SEC001_REQ2"'", "status": "SATISFIED", "provenance": "negative test tests/test_bola.py::test_cross_account_denied"}
    ],
    "tool_error": null
  }
]' 0 "PASS"

expect_result "CASE 8: unknown applicability -> UNPROVEN" '[
  {
    "control_id": "SEC-001",
    "applicability": "UNKNOWN",
    "verifier": {"type": "SEMANTIC_REVIEW", "identity": "reviewer::unassessed"},
    "evidence": [],
    "tool_error": null
  }
]' 0 "UNPROVEN"

# CASE 9 (adapted): two DIFFERENT controls in one batch -- a malformed run
# for one must not affect the other's result.
BATCH9="$TMP_DIR/case9_batch.json"
cat > "$BATCH9" <<EOF
[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_full"},
    "evidence": [
      {"requirement": "$SEC001_REQ1", "status": "SATISFIED", "provenance": "code review"},
      {"requirement": "$SEC001_REQ2", "status": "SATISFIED", "provenance": "negative test"}
    ],
    "tool_error": null
  },
  {
    "control_id": "SEC-060",
    "applicability": "not-a-real-value",
    "verifier": {"type": "DEPENDENCY_SCANNER", "identity": "scan::bad_applicability"},
    "evidence": [],
    "tool_error": null
  }
]
EOF
BATCH9_OUT="$TMP_DIR/case9_batch.out.json"
if run_model "$BATCH9" "$BATCH9_OUT"; then
  r0="$(get_field "$BATCH9_OUT" 0 result)"
  r1="$(get_field "$BATCH9_OUT" 1 result)"
  if [ "$r0" = "PASS" ] && [ "$r1" = "ERROR" ]; then
    echo "PASS: CASE 9: malformed run for one control doesn't affect a different control's result"
    pass_count=$((pass_count + 1))
  else
    echo "FAIL: CASE 9: expected [PASS, ERROR] for [SEC-001, SEC-060], got [$r0, $r1]" >&2
    fail_count=$((fail_count + 1))
  fi
else
  echo "FAIL: CASE 9: evidence_model.py exited non-zero on a batch with one malformed run" >&2
  fail_count=$((fail_count + 1))
fi

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

set +e
python3 "$MODEL" "$TMP_DIR/does-not-exist.json" "$BATCH9" > "$TMP_DIR/case11.out.json"
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

PROV_FILE="$TMP_DIR/case12.json"
cat > "$PROV_FILE" <<EOF
[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_partial"},
    "evidence": [
      {"requirement": "$SEC001_REQ2", "status": "SATISFIED", "provenance": "negative test tests/test_bola.py::test_cross_account_denied"}
    ],
    "tool_error": null
  }
]
EOF
PROV_OUT="$TMP_DIR/case12.out.json"
run_model "$PROV_FILE" "$PROV_OUT"
if python3 -c "
import json, sys
out_path, req1 = sys.argv[1], sys.argv[2]
d = json.load(open(out_path))
r = d['results'][0]
assert r['result'] == 'UNPROVEN'
assert isinstance(r['reasons'], list) and len(r['reasons']) > 0
assert any(req1 in reason for reason in r['reasons'])
assert r['evidence'][0]['provenance'] == 'negative test tests/test_bola.py::test_cross_account_denied'
" "$PROV_OUT" "$SEC001_REQ1"; then
  echo "PASS: CASE 12: UNPROVEN carries a specific reason and evidence provenance"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: CASE 12: UNPROVEN reason/provenance missing or wrong" >&2
  fail_count=$((fail_count + 1))
fi

expect_result "CASE 13: observed_at omitted entirely still evaluates" '[
  {
    "control_id": "SEC-060",
    "applicability": "NOT_APPLICABLE",
    "verifier": {"type": "DEPENDENCY_SCANNER", "identity": "scan::no-manifest"},
    "evidence": [],
    "tool_error": null
  }
]' 0 "NOT_APPLICABLE"

# ==================================================================
# CASE A-J: evidence-provenance integrity correction (this fix)
# ==================================================================

# CASE A: SEC-001 + STATIC_ANALYZER attempts to satisfy all evidence.
# STATIC_ANALYZER is not in SEC-001's verification.modes
# (SEMANTIC_REVIEW, DYNAMIC_API) -> capability violation -> never PASS.
expect_result "CASE A: out-of-capability verifier cannot manufacture PASS (SEC-001)" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "STATIC_ANALYZER", "identity": "semgrep::fake-bola-rule"},
    "evidence": [
      {"requirement": "'"$SEC001_REQ1"'", "status": "SATISFIED", "provenance": "static pattern match"},
      {"requirement": "'"$SEC001_REQ2"'", "status": "SATISFIED", "provenance": "static pattern match"}
    ],
    "tool_error": null
  }
]' 0 "ERROR"

# CASE B: SEC-001 has semantic evidence claiming BOTH required_evidence
# items SATISFIED, but no DYNAMIC_API (or any dynamic-family) run was ever
# submitted. dynamic_required=true for SEC-001 -> UNPROVEN even though the
# evidence TEXT is textually complete. This is the exact regression the
# integrity correction targets: text completeness alone is not proof.
expect_result "CASE B: complete evidence text from a non-dynamic capability alone is insufficient when dynamic_required" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "SEMANTIC_REVIEW", "identity": "reviewer::code-only-claim"},
    "evidence": [
      {"requirement": "'"$SEC001_REQ1"'", "status": "SATISFIED", "provenance": "code review of order.py:42"},
      {"requirement": "'"$SEC001_REQ2"'", "status": "SATISFIED", "provenance": "reviewer asserts this without running a test"}
    ],
    "tool_error": null
  }
]' 0 "UNPROVEN"

# CASE C: SEC-001 has dynamic evidence for the cross-account test, but the
# required ownership-check evidence item was never claimed -> UNPROVEN
# (missing required evidence), regardless of dynamic coverage.
expect_result "CASE C: dynamic evidence present but a required evidence item is missing" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_cross_account_only"},
    "evidence": [
      {"requirement": "'"$SEC001_REQ2"'", "status": "SATISFIED", "provenance": "negative test tests/test_bola.py::test_cross_account_denied"}
    ],
    "tool_error": null
  }
]' 0 "UNPROVEN"

# CASE D: SEC-001 with complete valid evidence contributed by two runs,
# each from a permitted capability appropriate to what it claims -> PASS.
DCASE="$TMP_DIR/case_d.json"
cat > "$DCASE" <<EOF
[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "SEMANTIC_REVIEW", "identity": "reviewer::order_py_review"},
    "evidence": [
      {"requirement": "$SEC001_REQ1", "status": "SATISFIED", "provenance": "code review of order.py:42, ownership check confirmed"}
    ],
    "tool_error": null
  },
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_cross_account"},
    "evidence": [
      {"requirement": "$SEC001_REQ2", "status": "SATISFIED", "provenance": "negative test tests/test_bola.py::test_cross_account_denied"}
    ],
    "tool_error": null
  }
]
EOF
DCASE_OUT="$TMP_DIR/case_d.out.json"
if run_model "$DCASE" "$DCASE_OUT"; then
  rD="$(get_field "$DCASE_OUT" 0 result)"
  if [ "$rD" = "PASS" ]; then
    echo "PASS: CASE D: multi-verifier aggregation reaches PASS with complete, appropriately-sourced evidence"
    pass_count=$((pass_count + 1))
  else
    echo "FAIL: CASE D: expected PASS, got $rD" >&2
    fail_count=$((fail_count + 1))
  fi
else
  echo "FAIL: CASE D: evidence_model.py exited non-zero unexpectedly" >&2
  fail_count=$((fail_count + 1))
fi

# CASE E: a dynamic_required control (SEC-041 Mass Assignment) satisfied
# entirely via its OTHER permitted (non-dynamic) capability -- proves the
# dynamic gate is enforced independently of the catalog's static-analyzer
# alternative, on a second control, not just SEC-001.
expect_result "CASE E: dynamic_required control has no dynamic contribution -> UNPROVEN (SEC-041)" '[
  {
    "control_id": "SEC-041",
    "applicability": "APPLICABLE",
    "verifier": {"type": "STATIC_ANALYZER", "identity": "semgrep::mass-assignment-rule"},
    "evidence": [
      {"requirement": "'"$SEC041_REQ1"'", "status": "SATISFIED", "provenance": "static pattern match: allow-list binding confirmed"},
      {"requirement": "'"$SEC041_REQ2"'", "status": "SATISFIED", "provenance": "static pattern match"}
    ],
    "tool_error": null
  }
]' 0 "UNPROVEN"

# CASE F: wrong verifier capability on a different control (SEC-066,
# modes DYNAMIC_DB/SEMANTIC_REVIEW) -> explicit fail-closed result, never
# PASS. Diversifies CASE A's proof across a second control/mode-set.
expect_result "CASE F: wrong verifier capability fails closed, never PASS (SEC-066)" '[
  {
    "control_id": "SEC-066",
    "applicability": "APPLICABLE",
    "verifier": {"type": "STATIC_ANALYZER", "identity": "semgrep::fake-rls-rule"},
    "evidence": [
      {"requirement": "'"$SEC066_REQ1"'", "status": "SATISFIED", "provenance": "static pattern match"},
      {"requirement": "'"$SEC066_REQ2"'", "status": "SATISFIED", "provenance": "static pattern match"}
    ],
    "tool_error": null
  }
]' 0 "ERROR"

# CASE G: tool error in a run using an otherwise-permitted capability ->
# ERROR (this module's documented aggregation semantics: any problem run
# -- malformed, capability violation, or tool error -- makes the whole
# control's aggregate ERROR, never UNPROVEN and never PASS).
expect_result "CASE G: tool error in a permitted-capability run -> ERROR, never PASS" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_target_down"},
    "evidence": [],
    "tool_error": {"message": "verification target returned 503 for the entire test run"}
  }
]' 0 "ERROR"

# CASE H: explicit trusted violated evidence -> FAIL (same as CASE 5,
# restated here to match the required test list explicitly).
expect_result "CASE H: explicit trusted violated evidence -> FAIL" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_bola_violation_2"},
    "evidence": [
      {"requirement": "'"$SEC001_REQ1"'", "status": "VIOLATED", "provenance": "code review found no ownership check"},
      {"requirement": "'"$SEC001_REQ2"'", "status": "SATISFIED", "provenance": "negative test"}
    ],
    "tool_error": null
  }
]' 0 "FAIL"

# CASE I: NOT_APPLICABLE remains supported after the correction (same as
# CASE 4, restated here to match the required test list explicitly).
expect_result "CASE I: NOT_APPLICABLE remains supported" '[
  {
    "control_id": "SEC-060",
    "applicability": "NOT_APPLICABLE",
    "verifier": {"type": "DEPENDENCY_SCANNER", "identity": "scan::no-manifest-2"},
    "evidence": [],
    "tool_error": null
  }
]' 0 "NOT_APPLICABLE"

# CASE J: malformed verifier (an entirely unknown/invalid verifier type,
# not merely a valid type outside this control's permitted set) -> ERROR.
# Distinct from CASE A/F, which use a globally-valid type that is simply
# not permitted for that specific control.
expect_result "CASE J: malformed/unknown verifier type -> ERROR" '[
  {
    "control_id": "SEC-001",
    "applicability": "APPLICABLE",
    "verifier": {"type": "NMAP_SCAN", "identity": "nmap::port-scan"},
    "evidence": [
      {"requirement": "'"$SEC001_REQ1"'", "status": "SATISFIED", "provenance": "??"},
      {"requirement": "'"$SEC001_REQ2"'", "status": "SATISFIED", "provenance": "??"}
    ],
    "tool_error": null
  }
]' 0 "ERROR"

# ==================================================================
# Additional coverage (not in the required CASE A-J list, but the same
# integrity gap applies to human_judgment_required): SEC-043 requires both
# a dynamic capability AND a human/semantic-judgment capability
# (SEMANTIC_REVIEW is the only judgment-capable type in its modes).
# ==================================================================

SEC043_REQ1="documented abuse-case analysis for the workflow's state transitions"
SEC043_REQ2="test proving an out-of-order or repeated abuse of the workflow does not bypass its business rule"

# Dynamic evidence alone (no SEMANTIC_REVIEW/HUMAN contribution) satisfies
# the dynamic gate but not the human-judgment gate -> UNPROVEN.
expect_result "CASE K: human_judgment_required unmet despite dynamic coverage -> UNPROVEN (SEC-043)" '[
  {
    "control_id": "SEC-043",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_abuse_case_replay"},
    "evidence": [
      {"requirement": "'"$SEC043_REQ1"'", "status": "SATISFIED", "provenance": "automated claim, no reviewer involved"},
      {"requirement": "'"$SEC043_REQ2"'", "status": "SATISFIED", "provenance": "test tests/test_abuse.py::test_no_double_redeem"}
    ],
    "tool_error": null
  }
]' 0 "UNPROVEN"

# Both gates satisfied via two runs -> PASS.
KCASE="$TMP_DIR/case_k_pass.json"
cat > "$KCASE" <<EOF
[
  {
    "control_id": "SEC-043",
    "applicability": "APPLICABLE",
    "verifier": {"type": "SEMANTIC_REVIEW", "identity": "reviewer::abuse_case_analysis"},
    "evidence": [
      {"requirement": "$SEC043_REQ1", "status": "SATISFIED", "provenance": "documented review of coupon-redemption state machine"}
    ],
    "tool_error": null
  },
  {
    "control_id": "SEC-043",
    "applicability": "APPLICABLE",
    "verifier": {"type": "DYNAMIC_API", "identity": "pytest::test_abuse_case_replay"},
    "evidence": [
      {"requirement": "$SEC043_REQ2", "status": "SATISFIED", "provenance": "test tests/test_abuse.py::test_no_double_redeem"}
    ],
    "tool_error": null
  }
]
EOF
KCASE_OUT="$TMP_DIR/case_k_pass.out.json"
if run_model "$KCASE" "$KCASE_OUT"; then
  rK="$(get_field "$KCASE_OUT" 0 result)"
  if [ "$rK" = "PASS" ]; then
    echo "PASS: CASE L: dynamic + human-judgment gates both satisfied -> PASS (SEC-043)"
    pass_count=$((pass_count + 1))
  else
    echo "FAIL: CASE L: expected PASS, got $rK" >&2
    fail_count=$((fail_count + 1))
  fi
else
  echo "FAIL: CASE L: evidence_model.py exited non-zero unexpectedly" >&2
  fail_count=$((fail_count + 1))
fi

echo ""
echo "diana/security/test-evidence-model.sh: $pass_count passed, $fail_count failed"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
