#!/usr/bin/env bash
set -euo pipefail

# Proves preflight.py's output can be consumed deterministically by
# diana-gate.py through the small reduce_for_gate.py adapter, with each
# component invoked as its own separate process. Preflight detects
# conditions; Diana Gate decides whether the supplied evidence is allowed to
# proceed. Neither script imports or knows about the other's internals.

PREFLIGHT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PREFLIGHT_DIR/../.." && pwd)"
PREFLIGHT="$PREFLIGHT_DIR/preflight.py"
REDUCE="$PREFLIGHT_DIR/reduce_for_gate.py"
GATE="$REPO_ROOT/diana/gate/diana-gate.py"
FIXTURES="$PREFLIGHT_DIR/fixtures"

materialize() {
  python3 - "$1" "$2" <<'PY'
import json, os, sys
fixture_path, tmpdir = sys.argv[1], sys.argv[2]
with open(fixture_path, encoding="utf-8") as fh:
    spec = json.load(fh)
for rel, content in spec["files"].items():
    path = os.path.join(tmpdir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
PY
}

# build_gate_input PREFLIGHT_JSON RISK FILES_JSON -> full diana-gate.py input on stdout
build_gate_input() {
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys
preflight_checks = json.loads(sys.argv[1])
risk = sys.argv[2]
files = json.loads(sys.argv[3])
gate_input = {
    "version": 1,
    "dod": {"present": True, "evidence": ["Phase 2 integration test"]},
    "verification": {"present": True, "evidence": ["preflight scan completed"]},
    "preflight": preflight_checks,
    "diff": {"risk": risk, "files": files},
    "human_only_conditions": [],
}
print(json.dumps(gate_input, sort_keys=True))
PY
}

assert_decision() {
  local label="$1" gate_input="$2" expected="$3"
  local output status
  set +e
  output="$(echo "$gate_input" | python3 "$GATE" /dev/stdin)"
  status=$?
  set -e
  python3 - "$output" "$expected" <<'PY'
import json, sys
result = json.loads(sys.argv[1])
assert result["decision"] == sys.argv[2], result
PY
  echo "PASS $label -> $expected (exit $status)"
}

# Integration case 1: a clean frontend repo (CASE C fixture) with SAFE risk
# and passing preflight -> Diana Gate PASS.
tmp_clean="$(mktemp -d)"
materialize "$FIXTURES/frontend-clean.json" "$tmp_clean"
preflight_out="$(python3 "$PREFLIGHT" "$tmp_clean")"
reduced="$(echo "$preflight_out" | python3 "$REDUCE")"
gate_input="$(build_gate_input "$reduced" SAFE '["src/config.ts"]')"
assert_decision "clean-frontend-safe-risk" "$gate_input" PASS
rm -rf "$tmp_clean"

# Integration case 2: a frontend repo with localhost residue (CASE B
# fixture) -> preflight reports a BLOCKER FAIL -> Diana Gate FAIL, even
# though the declared diff risk is SAFE. Preflight's finding, not Gate's own
# logic, is what drives the failure here.
tmp_residue="$(mktemp -d)"
materialize "$FIXTURES/frontend-with-residue.json" "$tmp_residue"
preflight_out="$(python3 "$PREFLIGHT" "$tmp_residue")"
reduced="$(echo "$preflight_out" | python3 "$REDUCE")"
gate_input="$(build_gate_input "$reduced" SAFE '["src/config.ts"]')"
assert_decision "frontend-residue-safe-risk" "$gate_input" FAIL
rm -rf "$tmp_residue"

# Integration case 3: a backend-only repo (CASE A fixture) -> every
# frontend-only preflight check SKIPs cleanly and contributes no failure;
# Diana Gate still reaches PASS.
tmp_backend="$(mktemp -d)"
materialize "$FIXTURES/backend-only.json" "$tmp_backend"
preflight_out="$(python3 "$PREFLIGHT" "$tmp_backend")"
reduced="$(echo "$preflight_out" | python3 "$REDUCE")"
gate_input="$(build_gate_input "$reduced" SAFE '["app/main.py"]')"
assert_decision "backend-only-safe-risk" "$gate_input" PASS
rm -rf "$tmp_backend"

# Integration case 4 (Phase 8): a WARNING-severity preflight FAIL (the
# frontend-clean fixture has package.json but no committed lockfile, so
# dependency-lockfile-present applies and FAILs) must NOT block Diana Gate,
# unlike a BLOCKER FAIL (case 2 above) - proving the gate's existing generic
# severity handling extends correctly to real WARNING checks now that the
# catalog has some, with no gate code change required.
tmp_warning="$(mktemp -d)"
materialize "$FIXTURES/frontend-clean.json" "$tmp_warning"
preflight_out="$(python3 "$PREFLIGHT" "$tmp_warning")"
python3 -c "
import json, sys
result = json.loads(sys.argv[1])
check = next(c for c in result['checks'] if c['id'] == 'dependency-lockfile-present')
assert check['applicable'] is True and check['severity'] == 'WARNING' and check['result'] == 'FAIL', check
" "$preflight_out"
reduced="$(echo "$preflight_out" | python3 "$REDUCE")"
gate_input="$(build_gate_input "$reduced" SAFE '["src/config.ts"]')"
assert_decision "warning-fail-does-not-block" "$gate_input" PASS
rm -rf "$tmp_warning"

echo "All Diana Preflight <-> Diana Gate integration tests passed."
