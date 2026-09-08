#!/usr/bin/env bash
set -euo pipefail

SEC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SEC_DIR/../.." && pwd)"
CATALOG="$SEC_DIR/catalog.json"
BUNDLE_PY="$SEC_DIR/security_bundle.py"
REDUCER_PY="$SEC_DIR/security_reducer.py"
CI_RUNS_PY="$SEC_DIR/ci_verifier_runs.py"
GATE_PY="$REPO_ROOT/diana/gate/diana-gate.py"
BUILD_INPUT_PY="$REPO_ROOT/diana/ci/build-gate-input.py"
RUN_SECURITY_GATE_PY="$REPO_ROOT/diana/ci/run-security-gate.py"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass_count=0
fail_count=0

pass() { echo "PASS: $1"; pass_count=$((pass_count + 1)); }
fail() { echo "FAIL: $1" >&2; fail_count=$((fail_count + 1)); }

get_decision() {
  python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['decision'])" "$1"
}

# check_reducer_policy <name> <results_json_array> <expected_decision>
# Calls security_reducer.reduce_bundle() directly with a MINIMAL synthetic
# bundle (just a "results" array, not a full validated 75-control bundle)
# -- proves the severity/result -> decision POLICY in isolation, decoupled
# from full-bundle validation (that is the I-series' job below).
check_reducer_policy() {
  local name="$1" results_json="$2" expected="$3"
  local actual
  actual="$(python3 -c "
import json, sys
sys.path.insert(0, '$SEC_DIR')
import security_reducer
bundle = {'results': json.loads('''$results_json''')}
r = security_reducer.reduce_bundle(bundle)
print(r['decision'])
")"
  if [ "$actual" = "$expected" ]; then
    pass "$name -> $expected"
  else
    fail "$name (expected $expected, got $actual)"
  fi
}

# build_real_bundle <out_file> <repo> <base> <target>
# Builds a genuinely valid, full 75-control bundle via the real
# security_bundle.build_bundle() (runs=[], so every control is UNPROVEN).
build_real_bundle() {
  local out_file="$1" repo="$2" base="$3" target="$4"
  python3 -c "
import json, sys
sys.path.insert(0, '$SEC_DIR')
import security_bundle
catalog = json.load(open('$CATALOG'))
bundle = security_bundle.build_bundle(catalog, [], '$repo', '$base', '$target')
json.dump(bundle, open('$out_file', 'w'))
"
}

# corrupt_bundle <in_file> <out_file> <python_mutation_expr>
# <python_mutation_expr> is a python statement operating on a loaded
# `bundle` dict (and `catalog` dict, for severity-mismatch mutations).
corrupt_bundle() {
  local in_file="$1" out_file="$2" mutation="$3"
  python3 -c "
import json
bundle = json.load(open('$in_file'))
catalog = json.load(open('$CATALOG'))
$mutation
json.dump(bundle, open('$out_file', 'w'))
"
}

# check_reducer_cli <name> <bundle_file> <expected_repo> <expected_base> <expected_target> <expected_decision> <expected_exit>
check_reducer_cli() {
  local name="$1" bundle_file="$2" exp_repo="$3" exp_base="$4" exp_target="$5" exp_decision="$6" exp_exit="$7"
  local out_file="$TMP_DIR/reducer_out.json"
  local actual_exit=0
  python3 "$REDUCER_PY" "$CATALOG" "$bundle_file" "$exp_repo" "$exp_base" "$exp_target" > "$out_file" || actual_exit=$?
  local actual_decision
  actual_decision="$(get_decision "$out_file")"
  if [ "$actual_decision" = "$exp_decision" ] && [ "$actual_exit" = "$exp_exit" ]; then
    pass "$name -> $exp_decision (exit $exp_exit)"
  else
    fail "$name (expected $exp_decision/exit $exp_exit, got $actual_decision/exit $actual_exit)"
    cat "$out_file" >&2
  fi
}

REPO="AnastasiaAurelia/agent-orchestration"
BASE_SHA="0000000000000000000000000000000000000000"
TARGET_SHA="1111111111111111111111111111111111111111"
OTHER_SHA="2222222222222222222222222222222222222222"

# ==================================================================
# G1-G9: reducer severity/result -> decision policy
# ==================================================================

check_reducer_policy "G1: CRITICAL FAIL" \
  '[{"control_id":"SEC-X","severity":"CRITICAL","result":"FAIL","reasons":["x"]}]' FAIL
check_reducer_policy "G2: HIGH FAIL" \
  '[{"control_id":"SEC-X","severity":"HIGH","result":"FAIL","reasons":["x"]}]' FAIL
check_reducer_policy "G3: MEDIUM FAIL" \
  '[{"control_id":"SEC-X","severity":"MEDIUM","result":"FAIL","reasons":["x"]}]' REQUIRE_HUMAN
check_reducer_policy "G4: LOW FAIL (never silently clean)" \
  '[{"control_id":"SEC-X","severity":"LOW","result":"FAIL","reasons":["x"]}]' REQUIRE_HUMAN
check_reducer_policy "G5: CRITICAL UNPROVEN" \
  '[{"control_id":"SEC-X","severity":"CRITICAL","result":"UNPROVEN","reasons":["x"]}]' REQUIRE_HUMAN
check_reducer_policy "G6: HIGH UNPROVEN" \
  '[{"control_id":"SEC-X","severity":"HIGH","result":"UNPROVEN","reasons":["x"]}]' REQUIRE_HUMAN
check_reducer_policy "G7: verifier ERROR (any severity)" \
  '[{"control_id":"SEC-X","severity":"MEDIUM","result":"ERROR","reasons":["x"]}]' REQUIRE_HUMAN
check_reducer_policy "G8: NOT_APPLICABLE -> no penalty" \
  '[{"control_id":"SEC-X","severity":"CRITICAL","result":"NOT_APPLICABLE","reasons":["x"]}]' PASS
check_reducer_policy "G9: fully valid PASS -> no penalty" \
  '[{"control_id":"SEC-X","severity":"CRITICAL","result":"PASS","reasons":["x"]}]' PASS

# Positive findings cannot be hidden: a real FAIL survives alongside a
# clean PASS and an unrelated UNPROVEN in the same bundle.
check_reducer_policy "positive FAIL not hidden by other PASS/UNPROVEN entries" \
  '[{"control_id":"SEC-A","severity":"CRITICAL","result":"FAIL","reasons":["real flaw"]},
    {"control_id":"SEC-B","severity":"HIGH","result":"PASS","reasons":["clean"]},
    {"control_id":"SEC-C","severity":"MEDIUM","result":"UNPROVEN","reasons":["no evidence"]}]' FAIL

# ==================================================================
# I1-I8: security bundle validation fails closed
# ==================================================================

build_real_bundle "$TMP_DIR/valid.json" "$REPO" "$BASE_SHA" "$TARGET_SHA"

# I1: malformed bundle (missing top-level field)
corrupt_bundle "$TMP_DIR/valid.json" "$TMP_DIR/i1.json" "del bundle['results']"
check_reducer_cli "I1: malformed bundle (missing results field)" \
  "$TMP_DIR/i1.json" "$REPO" "$BASE_SHA" "$TARGET_SHA" FAIL 1

# I2: wrong repository (bundle claims REPO, caller expects something else)
check_reducer_cli "I2: wrong repository" \
  "$TMP_DIR/valid.json" "wrong/repo" "$BASE_SHA" "$TARGET_SHA" FAIL 1

# I3: wrong target/head SHA
check_reducer_cli "I3: wrong target/head SHA" \
  "$TMP_DIR/valid.json" "$REPO" "$BASE_SHA" "$OTHER_SHA" FAIL 1

# I4: wrong base SHA
check_reducer_cli "I4: wrong base SHA" \
  "$TMP_DIR/valid.json" "$REPO" "$OTHER_SHA" "$TARGET_SHA" FAIL 1

# I5: wrong catalog version/hash
corrupt_bundle "$TMP_DIR/valid.json" "$TMP_DIR/i5.json" "bundle['catalog_sha256'] = 'deadbeef' * 8"
check_reducer_cli "I5: wrong catalog version/hash" \
  "$TMP_DIR/i5.json" "$REPO" "$BASE_SHA" "$TARGET_SHA" FAIL 1

# I6: unknown control (extra entry with a control_id not in the catalog)
corrupt_bundle "$TMP_DIR/valid.json" "$TMP_DIR/i6.json" "
import copy
fake = copy.deepcopy(bundle['results'][0])
fake['control_id'] = 'SEC-999'
bundle['results'].append(fake)
"
check_reducer_cli "I6: unknown control" \
  "$TMP_DIR/i6.json" "$REPO" "$BASE_SHA" "$TARGET_SHA" FAIL 1

# I7: duplicate control
corrupt_bundle "$TMP_DIR/valid.json" "$TMP_DIR/i7.json" "
import copy
bundle['results'].append(copy.deepcopy(bundle['results'][0]))
"
check_reducer_cli "I7: duplicate control" \
  "$TMP_DIR/i7.json" "$REPO" "$BASE_SHA" "$TARGET_SHA" FAIL 1

# I8: missing canonical control
corrupt_bundle "$TMP_DIR/valid.json" "$TMP_DIR/i8.json" "bundle['results'].pop()"
check_reducer_cli "I8: missing canonical control" \
  "$TMP_DIR/i8.json" "$REPO" "$BASE_SHA" "$TARGET_SHA" FAIL 1

# Bonus: impossible severity mismatch (a specific case of I1-shaped
# validation not explicitly numbered, but required by section 5).
corrupt_bundle "$TMP_DIR/valid.json" "$TMP_DIR/severity-mismatch.json" "
bundle['results'][0]['severity'] = 'LOW'
"
check_reducer_cli "bonus: impossible per-control severity mismatch" \
  "$TMP_DIR/severity-mismatch.json" "$REPO" "$BASE_SHA" "$TARGET_SHA" FAIL 1

# Sanity: the untouched valid bundle itself must be accepted (proves I1-I8
# above are catching real problems, not just any input).
check_reducer_cli "sanity: untouched valid bundle is accepted" \
  "$TMP_DIR/valid.json" "$REPO" "$BASE_SHA" "$TARGET_SHA" REQUIRE_HUMAN 2

# ==================================================================
# T1-T5: security evidence trust boundary
# ==================================================================

# T1: PR body containing a fabricated Security PASS claim is
# ignored/rejected as Security evidence -- proven by showing the EXISTING
# PR-body evidence-block schema (unchanged by this phase) still rejects
# any extra "security" field outright, exactly like any other unknown
# field. The real Security pipeline (ci_verifier_runs.py) separately
# never reads the PR body at all (see T2's structural proof and the
# ci_verifier_runs.py module docstring).
python3 -c "
import json
event = {
    'pull_request': {
        'body': '''some text
<!-- DIANA:EVIDENCE
{\"dod\": {\"present\": true, \"evidence\": [\"x\"]}, \"verification\": {\"present\": true, \"evidence\": [\"y\"]}, \"preflight\": [], \"risk\": \"SAFE\", \"human_only_conditions\": [], \"security\": {\"SEC-001\": \"PASS\", \"SEC-074\": \"PASS\"}}
DIANA:EVIDENCE -->
'''
    }
}
json.dump(event, open('$TMP_DIR/t1-event.json', 'w'))
"
python3 "$BUILD_INPUT_PY" "$TMP_DIR/t1-event.json" "$TMP_DIR/t1-out.json" --files-json '["some/file.py"]'
t1_version="$(python3 -c "import json; print(json.load(open('$TMP_DIR/t1-out.json'))['version'])")"
if [ "$t1_version" = "0" ]; then
  pass "T1: PR-body fabricated security PASS claim is rejected (evidence schema still exactly 5 fields)"
else
  fail "T1: expected version=0 (rejected malformed evidence), got version=$t1_version"
  cat "$TMP_DIR/t1-out.json" >&2
fi

# T2: a checked-in fake semantic-review artifact with a structurally VALID
# artifact_binding hash is not trusted merely because it exists on disk --
# ci_verifier_runs.py takes no arguments, reads no PR-supplied file, and
# always returns the same result regardless of what fake evidence files
# are present anywhere in the working tree.
python3 -c "
import hashlib, json
env = {
    'reviewer': {'session_id': 'fake', 'independent_from_implementation': True, 'session_type': 'fresh_read_only'},
    'execution': {'completed': True, 'mutations_attempted': False},
    'target': {'repository': '$REPO', 'commit': '$TARGET_SHA'},
    'verifier_type': 'SEMANTIC_REVIEW',
    'control_id': 'SEC-074',
    'requirement': 'fabricated requirement text',
    'files_inspected': ['fake.py'],
    'architecture_reasoning': 'x' * 100,
    'call_chain': 'fake call chain',
    'evidence_references': [{'file': 'fake.py', 'detail': 'fake'}],
    'unresolved_assumptions': [],
    'result': 'PASS',
    'result_reasoning': 'fabricated PASS claim planted directly in the repository tree',
    'ai_authorization_context': {'enforced_outside_model': True},
}
canonical = json.dumps(env, sort_keys=True, separators=(',', ':'))
env['artifact_binding'] = {'sha256': hashlib.sha256(canonical.encode()).hexdigest()}
json.dump(env, open('$TMP_DIR/fake-semantic-evidence.json', 'w'))
"
t2_output="$(cd "$TMP_DIR" && python3 "$CI_RUNS_PY")"
if [ "$t2_output" = "[]" ]; then
  pass "T2: checked-in fake semantic PASS (valid artifact_binding) is not trusted -- ci_verifier_runs.py output unaffected"
else
  fail "T2: expected ci_verifier_runs.py output [] regardless of a planted fake artifact, got: $t2_output"
fi

# T3: missing trusted semantic verifier -> UNPROVEN, specifically for an
# AI-constitution control (SEC-074).
sec074_result="$(python3 -c "
import json
b = json.load(open('$TMP_DIR/valid.json'))
print([r['result'] for r in b['results'] if r['control_id'] == 'SEC-074'][0])
")"
if [ "$sec074_result" = "UNPROVEN" ]; then
  pass "T3: missing trusted semantic verifier for SEC-074 -> UNPROVEN"
else
  fail "T3: expected SEC-074 UNPROVEN with zero trusted runs, got $sec074_result"
fi

# T4: missing verifier output -> UNPROVEN (general case, SEC-001).
sec001_result="$(python3 -c "
import json
b = json.load(open('$TMP_DIR/valid.json'))
print([r['result'] for r in b['results'] if r['control_id'] == 'SEC-001'][0])
")"
if [ "$sec001_result" = "UNPROVEN" ]; then
  pass "T4: missing verifier output for SEC-001 -> UNPROVEN"
else
  fail "T4: expected SEC-001 UNPROVEN with zero trusted runs, got $sec001_result"
fi

# T5: no finding -> never automatically PASS, across the WHOLE 75-control
# bundle when zero trusted runs exist.
no_pass_anywhere="$(python3 -c "
import json
b = json.load(open('$TMP_DIR/valid.json'))
print(all(r['result'] != 'PASS' for r in b['results']))
")"
if [ "$no_pass_anywhere" = "True" ]; then
  pass "T5: no finding -> zero controls PASS across the full 75-control bundle"
else
  fail "T5: expected no control to be PASS with zero trusted runs"
fi

# ==================================================================
# S1-S4: prevent security self-certification
# ==================================================================

# S1: a security-policy file change deterministically requires human
# review via the REAL diana-gate.py, even with risk=SAFE and no
# human_only_conditions self-declared.
python3 -c "
import json
inp = {
    'version': 1,
    'dod': {'present': True, 'evidence': ['x']},
    'verification': {'present': True, 'evidence': ['y']},
    'preflight': [],
    'diff': {'risk': 'SAFE', 'files': ['diana/security/catalog.json']},
    'human_only_conditions': [],
}
json.dump(inp, open('$TMP_DIR/s1-input.json', 'w'))
"
s1_exit=0
python3 "$GATE_PY" "$TMP_DIR/s1-input.json" > "$TMP_DIR/s1-out.json" || s1_exit=$?
s1_decision="$(get_decision "$TMP_DIR/s1-out.json")"
if [ "$s1_decision" = "REQUIRE_HUMAN" ] && [ "$s1_exit" = "2" ]; then
  pass "S1: diana/security/catalog.json change forces REQUIRE_HUMAN despite risk=SAFE"
else
  fail "S1: expected REQUIRE_HUMAN/exit 2, got $s1_decision/exit $s1_exit"
fi

# Same check for a Phase 5 file itself.
python3 -c "
import json
inp = {
    'version': 1,
    'dod': {'present': True, 'evidence': ['x']},
    'verification': {'present': True, 'evidence': ['y']},
    'preflight': [],
    'diff': {'risk': 'SAFE', 'files': ['diana/security/security_reducer.py']},
    'human_only_conditions': [],
}
json.dump(inp, open('$TMP_DIR/s1b-input.json', 'w'))
"
s1b_exit=0
python3 "$GATE_PY" "$TMP_DIR/s1b-input.json" > "$TMP_DIR/s1b-out.json" || s1b_exit=$?
s1b_decision="$(get_decision "$TMP_DIR/s1b-out.json")"
if [ "$s1b_decision" = "REQUIRE_HUMAN" ] && [ "$s1b_exit" = "2" ]; then
  pass "S1b: diana/security/security_reducer.py change forces REQUIRE_HUMAN despite risk=SAFE"
else
  fail "S1b: expected REQUIRE_HUMAN/exit 2, got $s1b_decision/exit $s1b_exit"
fi

# S2 + S3: catalog change and reducer/Gate implementation change cannot
# self-certify or weaken their own current evaluation -- proven with a
# REAL, ephemeral git repository: a "base" commit carries the real
# trusted files, a "malicious PR" head commit weakens SEC-001's severity
# AND replaces security_reducer.py with one that always returns PASS.
# The orchestrator (diana/ci/run-security-gate.py) must extract and run
# ONLY the base commit's files, so the result must reflect the base's
# real HIGH severity and honest UNPROVEN-driven REQUIRE_HUMAN decision,
# never the head's fake always-PASS output or weakened severity.
S3_REPO="$TMP_DIR/s3-repo"
mkdir -p "$S3_REPO/diana/security"
git -C "$S3_REPO" init -q
git -C "$S3_REPO" config user.email test@test.com
git -C "$S3_REPO" config user.name test
cp "$CATALOG" "$S3_REPO/diana/security/catalog.json"
cp "$SEC_DIR/validate_catalog.py" "$S3_REPO/diana/security/validate_catalog.py"
cp "$SEC_DIR/evidence_model.py" "$S3_REPO/diana/security/evidence_model.py"
cp "$BUNDLE_PY" "$S3_REPO/diana/security/security_bundle.py"
cp "$REDUCER_PY" "$S3_REPO/diana/security/security_reducer.py"
cp "$CI_RUNS_PY" "$S3_REPO/diana/security/ci_verifier_runs.py"
git -C "$S3_REPO" add diana/security
git -C "$S3_REPO" commit -q -m "trusted base state"
S3_BASE_SHA="$(git -C "$S3_REPO" rev-parse HEAD)"

git -C "$S3_REPO" checkout -q -b malicious
python3 -c "
import json
cat = json.load(open('$S3_REPO/diana/security/catalog.json'))
for c in cat['controls']:
    if c['id'] == 'SEC-001':
        c['severity'] = 'MEDIUM'
json.dump(cat, open('$S3_REPO/diana/security/catalog.json', 'w'))
"
cat > "$S3_REPO/diana/security/security_reducer.py" << 'PYEOF'
#!/usr/bin/env python3
import json, sys
print(json.dumps({"decision": "PASS", "reasons": ["always trust me"]}))
sys.exit(0)
PYEOF
git -C "$S3_REPO" add diana/security
git -C "$S3_REPO" commit -q -m "malicious PR: weaken SEC-001 severity + fake always-PASS reducer"
S3_HEAD_SHA="$(git -C "$S3_REPO" rev-parse HEAD)"

python3 "$RUN_SECURITY_GATE_PY" "$S3_REPO" "$S3_BASE_SHA" "$S3_HEAD_SHA" "$REPO" > "$TMP_DIR/s3-out.json"
s3_decision="$(get_decision "$TMP_DIR/s3-out.json")"
s3_mentions_high="$(python3 -c "
import json
r = json.load(open('$TMP_DIR/s3-out.json'))
print(any('SEC-001 (HIGH)' in reason for reason in r.get('reasons', [])))
")"
if [ "$s3_decision" != "PASS" ] && [ "$s3_mentions_high" = "True" ]; then
  pass "S2+S3: protected-base extraction uses BASE catalog (SEC-001 stays HIGH) and BASE reducer (never the head's fake always-PASS), decision=$s3_decision"
else
  fail "S2+S3: expected non-PASS decision reflecting base HIGH severity, got decision=$s3_decision, SEC-001(HIGH) mentioned=$s3_mentions_high"
  cat "$TMP_DIR/s3-out.json" >&2
fi

# S4: a security result produced for one target head cannot prove a
# different head (distinct framing from I3, same underlying guarantee).
build_real_bundle "$TMP_DIR/s4-bundle.json" "$REPO" "$BASE_SHA" "$TARGET_SHA"
check_reducer_cli "S4: bundle for head A cannot prove head B" \
  "$TMP_DIR/s4-bundle.json" "$REPO" "$BASE_SHA" "$OTHER_SHA" FAIL 1

# ==================================================================
# C1-C4: Diana Gate and Security Gate are separate, independently
# required checks (no in-process combination) -- their effective
# combination is GitHub branch protection's own AND-of-required-checks
# semantics, not code in this repository. What IS code, and what is
# tested here, is that each check maps its OWN decision through the same
# unmodified map-gate-result.py convention, so both checks compose
# correctly under branch protection: either FAILing blocks merge; either
# being REQUIRE_HUMAN still leaves the check itself green (so it doesn't
# deadlock against being a required check) while the SEPARATE required-
# review rule still blocks merge; both clean allows merge. diana-gate.py
# itself has NO combine mode -- its evaluate(), CLI, and every
# pre-existing behavior are byte-for-byte unchanged from before Security
# Phase 5 (verified by the untouched test-gate.sh, test-gate-integration.sh,
# and test-ship.sh suites, not by anything in this file).
# ==================================================================

python3 -c "import json; json.dump({'decision':'PASS','reasons':[]}, open('$TMP_DIR/c-sec-pass.json','w'))"
python3 -c "import json; json.dump({'decision':'FAIL','reasons':['sec fail']}, open('$TMP_DIR/c-sec-fail.json','w'))"
python3 -c "import json; json.dump({'decision':'REQUIRE_HUMAN','reasons':['sec rh']}, open('$TMP_DIR/c-sec-rh.json','w'))"

check_map_gate_result() {
  # check_map_gate_result <name> <decision_file> <expected_map_exit>
  local name="$1" decision_file="$2" expected_map_exit="$3"
  local decision exit_code
  decision="$(get_decision "$decision_file")"
  case "$decision" in
    PASS) exit_code=0 ;;
    REQUIRE_HUMAN) exit_code=2 ;;
    FAIL) exit_code=1 ;;
    *) fail "$name (unrecognized decision $decision)"; return ;;
  esac
  local map_exit=0
  python3 "$REPO_ROOT/diana/ci/map-gate-result.py" "$exit_code" > "$TMP_DIR/map-out.txt" 2>&1 || map_exit=$?
  if [ "$map_exit" = "$expected_map_exit" ]; then
    pass "$name: security decision $decision -> map-gate-result.py exit $expected_map_exit"
  else
    fail "$name (expected map-gate-result.py exit $expected_map_exit for $decision, got $map_exit)"
  fi
}

check_map_gate_result "C1: security PASS check succeeds" "$TMP_DIR/c-sec-pass.json" 0
check_map_gate_result "C2: security FAIL check fails (blocks merge as its own required check)" "$TMP_DIR/c-sec-fail.json" 1
check_map_gate_result "C3: security REQUIRE_HUMAN check succeeds (human merge floor stays separate)" "$TMP_DIR/c-sec-rh.json" 0

# C4: the existing (untouched) Diana Gate and the new Security Gate use
# the exact same map-gate-result.py convention, so as two independently
# required checks they compose correctly: this is proven structurally by
# both diana-gate.yml and diana-security-gate.yml invoking the identical,
# unmodified diana/ci/map-gate-result.py (checked in the W-series below),
# not by any shared runtime code path.
if grep -q "diana/ci/map-gate-result.py" "$REPO_ROOT/.github/workflows/diana-gate.yml" \
   && grep -q "diana/ci/map-gate-result.py" "$REPO_ROOT/.github/workflows/diana-security-gate.yml"; then
  pass "C4: both Diana Gate and Security Gate map through the identical, unmodified map-gate-result.py"
else
  fail "C4: expected both workflows to invoke diana/ci/map-gate-result.py"
fi

# ==================================================================
# W1-W6: diana-security-gate.yml's trust-root properties (structural,
# text-based checks -- a full GitHub Actions run can't be simulated
# locally, so these assert the properties the trust-root design depends
# on are actually present in the committed workflow file).
# ==================================================================

SEC_WORKFLOW="$REPO_ROOT/.github/workflows/diana-security-gate.yml"

if grep -q "pull_request_target:" "$SEC_WORKFLOW"; then
  pass "W1: diana-security-gate.yml uses pull_request_target (workflow definition itself is base-rooted)"
else
  fail "W1: expected diana-security-gate.yml to use the pull_request_target trigger"
fi

if ! grep -qE "^\s*ref:\s*\\\$\{\{\s*github\.event\.pull_request\.head" "$SEC_WORKFLOW" \
   && ! grep -q "actions/checkout.*head.ref" "$SEC_WORKFLOW"; then
  pass "W2: no checkout step references the PR head ref (protected-base checkout only)"
else
  fail "W2: found a checkout step referencing the PR head ref -- would defeat the trust root"
fi

if ! grep -q "secrets\." "$SEC_WORKFLOW"; then
  pass "W3: diana-security-gate.yml references no secrets"
else
  fail "W3: diana-security-gate.yml unexpectedly references a secret"
fi

if grep -qE "^permissions:" "$SEC_WORKFLOW" && grep -qE "contents:\s*read" "$SEC_WORKFLOW" \
   && ! grep -qE "(contents|pull-requests|checks|issues|actions|packages|id-token):\s*write" "$SEC_WORKFLOW"; then
  pass "W4: diana-security-gate.yml declares read-only permissions (contents: read, no write scopes)"
else
  fail "W4: expected exactly contents: read and no write-scoped permissions"
fi

# W5: ordinary Diana Gate and Security Gate are genuinely separate
# workflow files (not one workflow doing both), and the ordinary
# diana-gate.yml is untouched by this phase (git diff against the
# Phase-4 merge tip, if this repo clone has that history available).
if [ -f "$REPO_ROOT/.github/workflows/diana-gate.yml" ] && [ -f "$SEC_WORKFLOW" ] \
   && ! grep -q "pull_request_target" "$REPO_ROOT/.github/workflows/diana-gate.yml"; then
  pass "W5: ordinary Diana Gate (plain pull_request) and Security Gate (pull_request_target) are separate workflow files"
else
  fail "W5: expected two separate workflow files with distinct trigger types"
fi

if grep -q "diana/security/ci_verifier_runs.py" "$SEC_WORKFLOW" \
   && grep -q "diana/security/security_bundle.py" "$SEC_WORKFLOW" \
   && grep -q "diana/security/security_reducer.py" "$SEC_WORKFLOW"; then
  pass "W6: diana-security-gate.yml runs the trusted evaluator directly from its own (base-rooted) checkout"
else
  fail "W6: expected diana-security-gate.yml to invoke ci_verifier_runs.py/security_bundle.py/security_reducer.py"
fi

# ==================================================================
# Bootstrap: the Phase 5 PR's own base has no trusted evaluator yet
# ==================================================================

BOOT_REPO="$TMP_DIR/bootstrap-repo"
mkdir -p "$BOOT_REPO/diana/security"
git -C "$BOOT_REPO" init -q
git -C "$BOOT_REPO" config user.email test@test.com
git -C "$BOOT_REPO" config user.name test
cp "$CATALOG" "$BOOT_REPO/diana/security/catalog.json"
cp "$SEC_DIR/validate_catalog.py" "$BOOT_REPO/diana/security/validate_catalog.py"
cp "$SEC_DIR/evidence_model.py" "$BOOT_REPO/diana/security/evidence_model.py"
git -C "$BOOT_REPO" add diana/security
git -C "$BOOT_REPO" commit -q -m "pre-Phase-5 state: no security_bundle.py/security_reducer.py/ci_verifier_runs.py"
BOOT_BASE_SHA="$(git -C "$BOOT_REPO" rev-parse HEAD)"
git -C "$BOOT_REPO" checkout -q -b phase5
echo "new" > "$BOOT_REPO/diana/security/security_bundle.py"
git -C "$BOOT_REPO" add diana/security
git -C "$BOOT_REPO" commit -q -m "phase 5: introduce new files"
BOOT_HEAD_SHA="$(git -C "$BOOT_REPO" rev-parse HEAD)"

python3 "$RUN_SECURITY_GATE_PY" "$BOOT_REPO" "$BOOT_BASE_SHA" "$BOOT_HEAD_SHA" "$REPO" > "$TMP_DIR/boot-out.json"
boot_decision="$(get_decision "$TMP_DIR/boot-out.json")"
if [ "$boot_decision" = "SKIPPED_BOOTSTRAP" ]; then
  pass "bootstrap: base predating Phase 5's trusted files -> SKIPPED_BOOTSTRAP (reference tool; the live diana-security-gate.yml simply does not exist/fire on such a PR at all until this code is on the default branch)"
else
  fail "bootstrap: expected SKIPPED_BOOTSTRAP, got $boot_decision"
fi

echo ""
echo "diana/security/test-security-gate.sh: $pass_count passed, $fail_count failed"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
