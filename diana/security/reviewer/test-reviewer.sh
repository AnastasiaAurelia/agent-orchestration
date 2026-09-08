#!/usr/bin/env bash
set -euo pipefail

REVIEWER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEC_DIR="$(dirname "$REVIEWER_DIR")"
CATALOG="$SEC_DIR/catalog.json"
EVIDENCE_MODEL="$SEC_DIR/evidence_model.py"
FIXTURES="$REVIEWER_DIR/fixtures"
NORMALIZER="$REVIEWER_DIR/reviewer_normalizer.py"
EXPECTED="$FIXTURES/expected-target.json"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass_count=0
fail_count=0

pass() { echo "PASS: $1"; pass_count=$((pass_count + 1)); }
fail() { echo "FAIL: $1" >&2; fail_count=$((fail_count + 1)); }

run_normalizer() {
  # run_normalizer <artifact-path-or-'-'> <expected-path-or-'-'> <control_id...>
  python3 "$NORMALIZER" "$CATALOG" "$@" > "$TMP_DIR/norm_out.json"
}

normalizer_runs() {
  python3 -c "
import json, sys
print(json.dumps(json.load(open(sys.argv[1]))['runs']))
" "$1"
}

run_verifier_type() {
  python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
for r in d['runs']:
    if r['control_id'] == sys.argv[2]:
        print(r['verifier']['type'])
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

run_evidence_status() {
  # run_evidence_status <normalizer-out.json> <control_id>
  # Prints the semantic contribution's own evidence[0].status ("SATISFIED"
  # / "VIOLATED"), "EMPTY" if the run carries no evidence (the
  # UNPROVEN-shaped empty-evidence path), or "NONE" if no run at all was
  # emitted for that control. Distinct from result_for(): this checks the
  # normalizer's own single contribution, not the full evidence_model.py
  # aggregate (which may need a companion run to reach PASS/FAIL).
  python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
for r in d['runs']:
    if r['control_id'] == sys.argv[2]:
        ev = r.get('evidence') or []
        print(ev[0]['status'] if ev else 'EMPTY')
        sys.exit(0)
print('NONE')
" "$1" "$2"
}

check_aggregate() {
  # check_aggregate <name> <artifact> <expected> <expected_result> <control_id>
  local name="$1" artifact="$2" expected="$3" expected_result="$4" control_id="$5"
  run_normalizer "$artifact" "$expected" "$control_id"
  local runs
  runs="$(normalizer_runs "$TMP_DIR/norm_out.json")"
  evaluate_runs "$runs" "$TMP_DIR/agg.out.json"
  local r
  r="$(result_for "$TMP_DIR/agg.out.json" "$control_id")"
  if [ "$r" = "$expected_result" ]; then
    pass "$name -> $expected_result"
  else
    fail "$name (expected $expected_result for $control_id, got $r)"
    cat "$TMP_DIR/norm_out.json" >&2
  fi
}

check_evidence_status() {
  # check_evidence_status <name> <artifact> <expected> <control_id> <expected_status>
  # Checks the normalizer's own emitted evidence status directly (see
  # run_evidence_status), independent of full multi-run aggregation.
  local name="$1" artifact="$2" expected="$3" control_id="$4" expected_status="$5"
  run_normalizer "$artifact" "$expected" "$control_id"
  local s
  s="$(run_evidence_status "$TMP_DIR/norm_out.json" "$control_id")"
  if [ "$s" = "$expected_status" ]; then
    pass "$name -> evidence status $expected_status"
  else
    fail "$name (expected evidence status $expected_status for $control_id, got $s)"
    cat "$TMP_DIR/norm_out.json" >&2
  fi
}

# check_aggregate_with_companion <name> <artifact> <expected> <control_id>
#   <companion_requirement> <companion_verifier_type> [expected_result=PASS]
#
# Proves "semantic evidence can complement dynamic/static evidence
# correctly": the reviewer's own contribution alone only ever covers ONE
# of a control's two required_evidence items (by design, same discipline
# as Phase 2/3), so reaching full aggregate PASS needs a second,
# complementary contribution -- exactly the same composition pattern
# already proven in Phase 2 (Gitleaks+static-analyzer) and Phase 3
# (dynamic scenario+semantic/static companion). An optional 7th argument
# overrides the expected result away from PASS -- used to prove that a
# downgraded (UNPROVEN) semantic contribution stays downgraded even when a
# full companion contribution is added (U4/U5): a complete companion
# cannot rescue a reviewer artifact that was itself never actually PASS.
check_aggregate_with_companion() {
  local name="$1" artifact="$2" expected="$3" control_id="$4" companion_req="$5" companion_type="$6"
  local expected_result="${7:-PASS}"
  run_normalizer "$artifact" "$expected" "$control_id"
  local runs
  runs="$(normalizer_runs "$TMP_DIR/norm_out.json")"
  python3 -c "
import json
reviewer_runs = json.loads('''$runs''')
companion = {
    'control_id': '$control_id',
    'applicability': 'APPLICABLE',
    'verifier': {'type': '$companion_type', 'identity': 'companion::${control_id}-other-requirement'},
    'evidence': [
        {'requirement': '''$companion_req''', 'status': 'SATISFIED', 'provenance': 'synthetic companion contribution for test composition'}
    ],
    'tool_error': None,
}
json.dump(reviewer_runs + [companion], open('$TMP_DIR/combined.json', 'w'))
"
  python3 "$EVIDENCE_MODEL" "$CATALOG" "$TMP_DIR/combined.json" > "$TMP_DIR/agg.out.json"
  local r
  r="$(result_for "$TMP_DIR/agg.out.json" "$control_id")"
  if [ "$r" = "$expected_result" ]; then
    pass "$name -> $expected_result (semantic contribution + companion)"
  else
    fail "$name (expected $expected_result for $control_id after adding companion, got $r)"
    cat "$TMP_DIR/norm_out.json" >&2
  fi
}

# ==================================================================
# Required independence tests
# ==================================================================

SEC001_REQ2="cross-account negative access test (user A cannot access user B's object by id)"
check_aggregate_with_companion "CASE 1: substantiated PASS composes with dynamic companion" \
  "$FIXTURES/case-01-sec001-pass-substantiated.json" "$EXPECTED" SEC-001 "$SEC001_REQ2" DYNAMIC_API

check_aggregate "CASE 2: vague reviewer text cannot become PASS" \
  "$FIXTURES/case-02-vague-text-cannot-pass.json" "$EXPECTED" ERROR SEC-001

check_aggregate "CASE 3: missing evidence -> UNPROVEN" \
  "$FIXTURES/case-03-missing-evidence-unproven.json" "$EXPECTED" UNPROVEN SEC-001

check_aggregate "CASE 4: explicit concrete flaw -> FAIL" \
  "$FIXTURES/case-04-concrete-flaw-fail.json" "$EXPECTED" FAIL SEC-001

check_aggregate "CASE 5: malformed output -> ERROR" \
  "$FIXTURES/case-05-malformed.json" "$EXPECTED" ERROR SEC-001

check_aggregate "CASE 6: wrong target commit -> UNPROVEN (not attributed)" \
  "$FIXTURES/case-06-wrong-commit.json" "$EXPECTED" UNPROVEN SEC-001

check_aggregate "CASE 7a: invalid verifier_type value -> ERROR" \
  "$FIXTURES/case-07a-invalid-verifier-type.json" "$EXPECTED" ERROR SEC-001

check_aggregate "CASE 7b: unauthorized verifier capability for this control -> ERROR, never PASS" \
  "$FIXTURES/case-07b-control-does-not-permit-verifier-type.json" "$EXPECTED" ERROR SEC-001

check_aggregate "CASE 8a: AI model-refusal-alone cannot prove SEC-074" \
  "$FIXTURES/case-08a-ai-refusal-alone-not-proof.json" "$EXPECTED" ERROR SEC-074

SEC074_REQ2="negative test proving a tool call the model attempts outside the caller's authorization is rejected by the enforcement layer, not merely discouraged by a prompt"
check_aggregate_with_companion "CASE 8b: AI PASS with explicit external-enforcement flag composes" \
  "$FIXTURES/case-08b-ai-external-enforcement-pass.json" "$EXPECTED" SEC-074 "$SEC074_REQ2" DYNAMIC_API

check_aggregate "CASE 9 / N1: well-substantiated explicit NOT_APPLICABLE" \
  "$FIXTURES/case-09-not-applicable.json" "$EXPECTED" NOT_APPLICABLE SEC-001

# ==================================================================
# Additional coverage: composition with non-DYNAMIC_API capabilities,
# and the human_judgment_required gate
# ==================================================================

SEC066_REQ2="runtime test with multiple identities proving one user's database session cannot read/write another user's rows"
check_aggregate_with_companion "CASE 10: SEC-066 semantic contribution composes with DYNAMIC_DB companion" \
  "$FIXTURES/case-10-sec066-semantic-satisfied.json" "$EXPECTED" SEC-066 "$SEC066_REQ2" DYNAMIC_DB

SEC043_REQ2="test proving an out-of-order or repeated abuse of the workflow does not bypass its business rule"
check_aggregate_with_companion "CASE 11: SEC-043 business-logic PASS satisfies human_judgment_required gate, composes with dynamic companion" \
  "$FIXTURES/case-11-sec043-business-logic-pass.json" "$EXPECTED" SEC-043 "$SEC043_REQ2" DYNAMIC_API

SEC061_REQ1="dependency provenance/integrity is checked (lockfile hash pinning, publisher reputation)"
check_aggregate_with_companion "CASE 12: SEC-061 dependency-trust PASS satisfies human_judgment_required gate, composes with DEPENDENCY_SCANNER companion" \
  "$FIXTURES/case-12-sec061-dependency-trust-pass.json" "$EXPECTED" SEC-061 "$SEC061_REQ1" DEPENDENCY_SCANNER

# Tool-missing: explicit UNPROVEN, tagged with the control's real
# catalog-permitted capability, never a fabricated one.
run_normalizer "$TMP_DIR/does-not-exist.json" "$EXPECTED" SEC-001
vt="$(run_verifier_type "$TMP_DIR/norm_out.json" SEC-001)"
runs="$(normalizer_runs "$TMP_DIR/norm_out.json")"
evaluate_runs "$runs" "$TMP_DIR/tool_missing.out.json"
r="$(result_for "$TMP_DIR/tool_missing.out.json" SEC-001)"
if [ "$vt" = "SEMANTIC_REVIEW" ] && [ "$r" = "UNPROVEN" ]; then
  pass "CASE 13: review artifact unavailable -> explicit UNPROVEN via SEMANTIC_REVIEW"
else
  fail "CASE 13: expected verifier_type=SEMANTIC_REVIEW and result=UNPROVEN, got verifier_type=$vt result=$r"
fi

# Catalog-derived authorization: controls the Phase 4 kickoff's own
# illustrative list names, but whose real catalog verification.modes do
# NOT include SEMANTIC_REVIEW/HUMAN, must be silently unauthorized here.
for control_id in SEC-040 SEC-062 SEC-067 SEC-070 SEC-071; do
  run_normalizer "$FIXTURES/case-01-sec001-pass-substantiated.json" "$EXPECTED" "$control_id"
  runs="$(normalizer_runs "$TMP_DIR/norm_out.json")"
  if [ "$runs" = "[]" ]; then
    pass "CASE 14 ($control_id): catalog-unauthorized control produces no contribution"
  else
    fail "CASE 14 ($control_id): expected empty runs (catalog modes don't permit SEMANTIC_REVIEW/HUMAN), got $runs"
  fi
done

# ==================================================================
# Final semantic evidence integrity correction (human review):
# artifact-field binding (A1-A5), NOT_APPLICABLE substantiation (N1-N5,
# N1 covered above by CASE 9), and unresolved-assumption downgrade
# (U1-U5).
# ==================================================================

# A1: a valid artifact with ai_authorization_context present is correctly
#     integrity-bound (hash covers this optional field too) and accepted --
#     evidence status reflects the reviewer's real verdict, not ERROR.
check_evidence_status "TEST A1: valid SEC-074 PASS artifact w/ ai_authorization_context accepted" \
  "$FIXTURES/case-08b-ai-external-enforcement-pass.json" "$EXPECTED" SEC-074 SATISFIED

# A2: ai_authorization_context.enforced_outside_model flipped false->true
#     after the binding was computed, without rebuilding it -- must be
#     caught as a hash mismatch, never silently accepted.
check_aggregate "TEST A2: tampered ai_authorization_context (unrebuilt binding) -> ERROR" \
  "$FIXTURES/case-28-tamper-ai-context.json" "$EXPECTED" ERROR SEC-074

# A3: reviewer/session metadata changed after the binding was computed.
check_aggregate "TEST A3: tampered reviewer session metadata (unrebuilt binding) -> ERROR" \
  "$FIXTURES/case-29-tamper-reviewer-metadata.json" "$EXPECTED" ERROR SEC-001

# A4: an unknown top-level field is rejected by the explicit schema check.
check_aggregate "TEST A4: unknown top-level field -> ERROR" \
  "$FIXTURES/case-30-unknown-top-level-field.json" "$EXPECTED" ERROR SEC-001

# A5: evidence_references content changed after the binding was computed.
check_aggregate "TEST A5: tampered evidence_references (unrebuilt binding) -> ERROR" \
  "$FIXTURES/case-31-tamper-evidence-references.json" "$EXPECTED" ERROR SEC-001

# N2: hedged/uncertain NOT_APPLICABLE reasoning ("probably not applicable",
#     "doesn't seem to be used") must never become NOT_APPLICABLE.
check_aggregate "TEST N2: hedged/uncertain NOT_APPLICABLE reasoning -> never NOT_APPLICABLE (ERROR)" \
  "$FIXTURES/case-20-notapplicable-uncertain-language.json" "$EXPECTED" ERROR SEC-001

# N3: NOT_APPLICABLE with zero evidence_references -- not NOT_APPLICABLE.
check_aggregate "TEST N3: NOT_APPLICABLE with zero evidence_references -> never NOT_APPLICABLE (ERROR)" \
  "$FIXTURES/case-21-notapplicable-no-evidence.json" "$EXPECTED" ERROR SEC-001

# N4: otherwise-substantiated NOT_APPLICABLE with an unresolved
#     applicability assumption -- downgraded to UNPROVEN.
check_aggregate "TEST N4: NOT_APPLICABLE with unresolved applicability assumption -> UNPROVEN" \
  "$FIXTURES/case-22-notapplicable-unresolved-assumption.json" "$EXPECTED" UNPROVEN SEC-001

# N5: NOT_APPLICABLE for the wrong target commit -- never attributed as
#     NOT_APPLICABLE for the caller's expected target.
check_aggregate "TEST N5: NOT_APPLICABLE with wrong target commit -> UNPROVEN (not attributed)" \
  "$FIXTURES/case-23-notapplicable-wrong-commit.json" "$EXPECTED" UNPROVEN SEC-001

# U1: PASS with empty unresolved_assumptions stays on the normal PASS
#     track (evidence status SATISFIED, not downgraded).
check_evidence_status "TEST U1: PASS + empty unresolved_assumptions -> normal PASS path" \
  "$FIXTURES/case-01-sec001-pass-substantiated.json" "$EXPECTED" SEC-001 SATISFIED

# U2: PASS + a non-empty unresolved_assumptions -- downgraded to UNPROVEN.
check_evidence_status "TEST U2: PASS + unresolved assumption -> downgraded (empty evidence)" \
  "$FIXTURES/case-24-pass-unresolved-assumption.json" "$EXPECTED" SEC-001 EMPTY
check_aggregate "TEST U2: PASS + unresolved assumption -> UNPROVEN, never PASS" \
  "$FIXTURES/case-24-pass-unresolved-assumption.json" "$EXPECTED" UNPROVEN SEC-001

# U3: a concrete, cited FAIL with an unresolved assumption elsewhere stays
#     visible as FAIL -- only PASS/NOT_APPLICABLE are downgraded.
check_aggregate "TEST U3: concrete FAIL + unresolved assumption -> FAIL remains visible" \
  "$FIXTURES/case-25-fail-unresolved-assumption.json" "$EXPECTED" FAIL SEC-001

# U4: SEC-043 business-logic PASS with an unresolved workflow assumption --
#     downgraded to UNPROVEN even with a full companion contribution.
check_aggregate_with_companion "TEST U4: SEC-043 PASS + unresolved workflow assumption -> UNPROVEN despite companion" \
  "$FIXTURES/case-26-sec043-pass-unresolved-assumption.json" "$EXPECTED" SEC-043 "$SEC043_REQ2" DYNAMIC_API UNPROVEN

# U5: SEC-061 dependency-trust PASS with an unresolved provenance
#     assumption -- downgraded to UNPROVEN even with a full companion.
check_aggregate_with_companion "TEST U5: SEC-061 PASS + unresolved provenance assumption -> UNPROVEN despite companion" \
  "$FIXTURES/case-27-sec061-pass-unresolved-assumption.json" "$EXPECTED" SEC-061 "$SEC061_REQ1" DEPENDENCY_SCANNER UNPROVEN

echo ""
echo "diana/security/reviewer/test-reviewer.sh: $pass_count passed, $fail_count failed"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
