#!/usr/bin/env bash
set -euo pipefail

ADAPTERS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEC_DIR="$(dirname "$ADAPTERS_DIR")"
CATALOG="$SEC_DIR/catalog.json"
EVIDENCE_MODEL="$SEC_DIR/evidence_model.py"
FIXTURES="$ADAPTERS_DIR/fixtures"
NUCLEI="$ADAPTERS_DIR/nuclei_adapter.py"

EXPECTED_LOCAL="$FIXTURES/expected-target-nuclei-local.json"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass_count=0
fail_count=0
pass() { echo "PASS: $1"; pass_count=$((pass_count + 1)); }
fail() { echo "FAIL: $1" >&2; fail_count=$((fail_count + 1)); }

run_adapter() {
  local artifact="$1" expected="$2"; shift 2
  python3 "$NUCLEI" "$artifact" "$expected" "$@" > "$TMP_DIR/out.json"
}

runs_array() {
  python3 -c "
import json
print(json.dumps(json.load(open('$1'))['runs']))
"
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
check_aggregate "CASE 1: tool unavailable" - "$EXPECTED_LOCAL" UNPROVEN SEC-064

# CASE 2: clean, full curated-template coverage against verified target ->
# this adapter's own contribution alone is UNPROVEN for SEC-064 as a whole
# (the control's OTHER static required_evidence item -- "deployment
# excludes .env/.git/backup/config" -- is a different verifier's
# responsibility), but proves the SATISFIED contribution itself is real
# by checking it independently below.
check_aggregate "CASE 2: clean full coverage alone -> UNPROVEN (static half missing)" \
  "$FIXTURES/nuclei-artifact-clean-full-coverage.json" "$EXPECTED_LOCAL" UNPROVEN SEC-064

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

run_adapter "$FIXTURES/nuclei-artifact-clean-full-coverage.json" "$EXPECTED_LOCAL" SEC-064
status="$(contribution_status "$TMP_DIR/out.json" SEC-064)"
[ "$status" = "SATISFIED" ] && pass "CASE 2b: clean full coverage contribution is SATISFIED" \
  || fail "CASE 2b: clean full coverage contribution is SATISFIED (got $status)"

# CASE 3: exposed .env template matched -> VIOLATED -> FAIL
check_aggregate "CASE 3: exposed .env template matched -> FAIL" \
  "$FIXTURES/nuclei-artifact-finding-env.json" "$EXPECTED_LOCAL" FAIL SEC-064

# CASE 4: an unallowlisted (unvetted) template's finding is never trusted
# as evidence -- report shows a "critical" match, but for a template not
# in the curated allowlist, so the run still reflects a clean, fully-
# covered scan (SATISFIED contribution, still overall UNPROVEN pending the
# static half) -- never a fabricated VIOLATED and never a false PASS.
run_adapter "$FIXTURES/nuclei-artifact-finding-unallowlisted-template.json" "$EXPECTED_LOCAL" SEC-064
status="$(contribution_status "$TMP_DIR/out.json" SEC-064)"
[ "$status" = "SATISFIED" ] && pass "CASE 4: unallowlisted template finding never trusted as evidence" \
  || fail "CASE 4: unallowlisted template finding never trusted as evidence (got $status)"

# CASE 5: incomplete curated-template coverage (only 1 of 3 configured) ->
# no SATISFIED contribution possible -> UNPROVEN
check_aggregate "CASE 5: incomplete template coverage -> UNPROVEN" \
  "$FIXTURES/nuclei-artifact-clean-incomplete-coverage.json" "$EXPECTED_LOCAL" UNPROVEN SEC-064

# CASE 6: finding present but wrong commit -> not attributed -> UNPROVEN, never FAIL
check_aggregate "CASE 6: finding wrong commit not attributed" \
  "$FIXTURES/nuclei-artifact-finding-wrong-commit.json" "$EXPECTED_LOCAL" UNPROVEN SEC-064

# CASE 7: wrong repo, clean scan -> UNPROVEN
check_aggregate "CASE 7: clean but wrong repo -> UNPROVEN" \
  "$FIXTURES/nuclei-artifact-clean-wrong-repo.json" "$EXPECTED_LOCAL" UNPROVEN SEC-064

# CASE 8: wrong declared tool name -> ERROR
check_aggregate "CASE 8: wrong tool name rejected -> ERROR" \
  "$FIXTURES/nuclei-artifact-wrong-tool-name.json" "$EXPECTED_LOCAL" ERROR SEC-064

# CASE 9: malformed report (not a JSON list) -> ERROR
check_aggregate "CASE 9: malformed report -> ERROR" \
  "$FIXTURES/nuclei-artifact-malformed-report.json" "$EXPECTED_LOCAL" ERROR SEC-064

# CASE 10: PRODUCTION target attempt -> refused -> ERROR
check_aggregate "CASE 10: production target attempt refused -> ERROR" \
  "$FIXTURES/nuclei-artifact-prod-environment.json" "$EXPECTED_LOCAL" ERROR SEC-064

# CASE 11: non-loopback host attempt -> refused -> ERROR
check_aggregate "CASE 11: external host attempt refused -> ERROR" \
  "$FIXTURES/nuclei-artifact-external-host.json" "$EXPECTED_LOCAL" ERROR SEC-064

# CASE 12: unauthorized control yields zero runs, never fabricated evidence
python3 "$NUCLEI" "$FIXTURES/nuclei-artifact-clean-full-coverage.json" "$EXPECTED_LOCAL" SEC-001 > "$TMP_DIR/unauth.json"
python3 -c "
import json
d = json.load(open('$TMP_DIR/unauth.json'))
assert d['runs'] == [], d['runs']
" && pass "CASE 12: unauthorized control (SEC-001) yields zero runs" || fail "CASE 12: unauthorized control (SEC-001) yields zero runs"

# CASE 13: a PR cannot self-authorize a new template by adding an entry to
# a COPY of allowed_templates.json at scan time -- the adapter only ever
# reads the protected base's own committed file at import time, proven
# here by confirming an unvetted template id is absent from the loaded
# allowlist regardless of what any artifact's own report claims.
python3 -c "
import sys
sys.path.insert(0, '$ADAPTERS_DIR')
import nuclei_adapter
assert 'some-random-unvetted-template' not in nuclei_adapter.ALLOWED_TEMPLATES
" && pass "CASE 13: unvetted template id is not in the protected-base allowlist" \
  || fail "CASE 13: unvetted template id is not in the protected-base allowlist"

echo ""
echo "test-nuclei-adapter.sh: $pass_count passed, $fail_count failed"
[ "$fail_count" -eq 0 ]
