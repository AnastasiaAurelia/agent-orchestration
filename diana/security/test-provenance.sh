#!/usr/bin/env bash
set -euo pipefail

SEC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROV="$SEC_DIR/provenance.py"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass_count=0
fail_count=0
pass() { echo "PASS: $1"; pass_count=$((pass_count + 1)); }
fail() { echo "FAIL: $1" >&2; fail_count=$((fail_count + 1)); }

build_valid_manifest() {
  python3 -c "
import json, sys
sys.path.insert(0, '$SEC_DIR')
import provenance
m = provenance.build_manifest(
    verifier_type='DEPENDENCY_SCANNER',
    tool_name='osv-scanner',
    tool_version='2.6.0',
    repository='AnastasiaAurelia/agent-orchestration',
    commit='c51ce15a301df2d7804b36d59e9d508b8653249b',
    environment='CI',
    target='.',
    started_at='2026-09-30T00:00:00Z',
    finished_at='2026-09-30T00:00:05Z',
    command_identity='osv-scanner scan source --lockfile=package-lock.json',
    ruleset_identity='osv.dev-database',
    artifact_digest='deadbeef' * 8,
    exit_status='0',
    coverage_description='dependency lockfiles scanned against OSV database',
)
print(json.dumps(m))
"
}

# CASE 1: a manifest this module builds round-trips through load_manifest()
manifest="$(build_valid_manifest)"
echo "$manifest" > "$TMP_DIR/manifest.json"
python3 "$PROV" "$TMP_DIR/manifest.json" > "$TMP_DIR/manifest_out.json"
if python3 -c "
import json
d = json.load(open('$TMP_DIR/manifest_out.json'))
raise SystemExit(0 if 'manifest' in d and 'error' not in d else 1)
"; then
  pass "CASE 1: valid manifest round-trips through load_manifest()"
else
  fail "CASE 1: valid manifest round-trips through load_manifest()"
fi

# CASE 2: mutating any bound field invalidates the manifest binding
for field in tool_version commit repository target ruleset_identity command_identity exit_status; do
  python3 -c "
import json
m = json.load(open('$TMP_DIR/manifest.json'))
m['$field'] = m['$field'] + '-TAMPERED'
print(json.dumps(m))
" > "$TMP_DIR/tampered.json"
  python3 "$PROV" "$TMP_DIR/tampered.json" > "$TMP_DIR/tampered_out.json" || true
  if python3 -c "
import json, sys
d = json.load(open('$TMP_DIR/tampered_out.json'))
sys.exit(0 if 'error' in d else 1)
"; then
    pass "CASE 2 ($field): mutation invalidates manifest_binding"
  else
    fail "CASE 2 ($field): mutation invalidates manifest_binding"
  fi
done

# CASE 3: unrecognized verifier_type is refused
python3 -c "
import sys
sys.path.insert(0, '$SEC_DIR')
import provenance
try:
    provenance.build_manifest(
        verifier_type='NOT_A_REAL_TYPE', tool_name='x', tool_version='1', repository='r', commit='c',
        environment='CI', target='.', started_at='t0', finished_at='t1', command_identity='cmd',
        ruleset_identity='rules', artifact_digest='ab', exit_status='0', coverage_description='desc',
    )
    sys.exit(1)
except provenance.ProvenanceError:
    sys.exit(0)
" && pass "CASE 3: unrecognized verifier_type refused" || fail "CASE 3: unrecognized verifier_type refused"

# CASE 4: empty-string field refused (fail closed, never a placeholder)
python3 -c "
import sys
sys.path.insert(0, '$SEC_DIR')
import provenance
try:
    provenance.build_manifest(
        verifier_type='DEPENDENCY_SCANNER', tool_name='', tool_version='1', repository='r', commit='c',
        environment='CI', target='.', started_at='t0', finished_at='t1', command_identity='cmd',
        ruleset_identity='rules', artifact_digest='ab', exit_status='0', coverage_description='desc',
    )
    sys.exit(1)
except provenance.ProvenanceError:
    sys.exit(0)
" && pass "CASE 4: empty-string field refused" || fail "CASE 4: empty-string field refused"

# CASE 5: a credential-shaped field name is refused, never silently accepted
python3 -c "
import sys
sys.path.insert(0, '$SEC_DIR')
import provenance
try:
    fields = dict(
        schema_version=1, verifier_type='DEPENDENCY_SCANNER', tool_name='x', tool_version='1',
        repository='r', commit='c', environment='CI', target='.', started_at='t0', finished_at='t1',
        command_identity='cmd', ruleset_identity='rules', artifact_digest='ab', exit_status='0',
        coverage_description='desc',
    )
    fields['api_key'] = 'shhh'
    provenance._finalize(fields)
    sys.exit(1)
except provenance.ProvenanceError:
    sys.exit(0)
" && pass "CASE 5: unknown/credential-shaped field name refused" || fail "CASE 5: unknown/credential-shaped field name refused"

# CASE 6: verify_manifest_digest() boolean helper agrees with load_manifest()
python3 -c "
import json, sys
sys.path.insert(0, '$SEC_DIR')
import provenance
m = json.load(open('$TMP_DIR/manifest.json'))
assert provenance.verify_manifest_digest(m) is True
m2 = dict(m)
m2['tool_version'] = 'TAMPERED'
assert provenance.verify_manifest_digest(m2) is False
" && pass "CASE 6: verify_manifest_digest() boolean helper" || fail "CASE 6: verify_manifest_digest() boolean helper"

echo ""
echo "test-provenance.sh: $pass_count passed, $fail_count failed"
[ "$fail_count" -eq 0 ]
