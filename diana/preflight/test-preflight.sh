#!/usr/bin/env bash
set -euo pipefail

PREFLIGHT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFLIGHT="$PREFLIGHT_DIR/preflight.py"
FIXTURES="$PREFLIGHT_DIR/fixtures"

materialize() {
  local fixture="$1" tmpdir="$2"
  python3 - "$FIXTURES/$fixture.json" "$tmpdir" <<'PY'
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

# run_case FIXTURE "check_id:applicable(0/1):result" ...
run_case() {
  local fixture="$1"
  shift
  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN
  materialize "$fixture" "$tmpdir"
  local output status
  set +e
  output="$(python3 "$PREFLIGHT" "$tmpdir")"
  status=$?
  set -e
  if [ "$status" -ne 0 ]; then
    echo "FAIL $fixture: preflight exited $status (expected 0): $output" >&2
    exit 1
  fi
  python3 - "$output" "$@" <<'PY'
import json, sys
result = json.loads(sys.argv[1])
by_id = {c["id"]: c for c in result["checks"]}
for spec in sys.argv[2:]:
    check_id, applicable, expected_result = spec.split(":", 2)
    check = by_id[check_id]
    assert check["applicable"] == bool(int(applicable)), (check_id, check)
    assert check["result"] == expected_result, (check_id, check)
PY
  echo "PASS $fixture"
}

# CASE A: backend/non-frontend repository -> frontend-only checks SKIP,
# always-applicable checks still run and pass.
run_case backend-only \
  "exposed-secret-config-files:1:PASS" \
  "frontend-client-secret-leakage:0:SKIP" \
  "localhost-staging-url-residue:0:SKIP" \
  "accidental-noindex:0:SKIP" \
  "build-test-evidence-present:1:PASS" \
  "stack-specific-safety-configuration:0:SKIP"

# CASE B: frontend repository with localhost/staging residue -> FAIL.
run_case frontend-with-residue \
  "localhost-staging-url-residue:1:FAIL"

# CASE C: frontend repository without the residue -> PASS.
run_case frontend-clean \
  "localhost-staging-url-residue:1:PASS"

# CASE D: repository containing a suspicious committed secret/config fixture
# -> secret check FAILs.
run_case secret-exposure \
  "exposed-secret-config-files:1:FAIL"

# CASE E: safe similarly-named fixture that is not an actual secret -> no
# naive false positive.
run_case safe-lookalike \
  "exposed-secret-config-files:1:PASS"

# CASE F: malformed preflight configuration (unparseable package.json) ->
# fail closed on the check whose evidence source is unreadable, rather than
# crashing or silently passing.
run_case malformed-package-json \
  "build-test-evidence-present:1:FAIL" \
  "exposed-secret-config-files:1:PASS"

# Regression: precise test-path detection. Ordinary production files whose
# names merely contain "test"/"spec" as a substring (src/contest/config.ts,
# src/specification.ts) must still be scanned and FAIL on real findings.
run_case test-path-precision-catch \
  "localhost-staging-url-residue:1:FAIL" \
  "frontend-client-secret-leakage:1:FAIL"

# Regression: genuine test-convention files (a tests/ directory, a *.spec.*
# filename) must still be excluded from residue/secret-leakage scanning even
# though a production file with a similar substring is now correctly caught.
run_case test-path-precision-exclude \
  "localhost-staging-url-residue:1:PASS" \
  "frontend-client-secret-leakage:1:PASS"

# Regression: a genuine shell test runner (test-*.sh with a #!.../sh
# shebang) is recognized as build/test evidence on its own, with no
# package.json/pyproject/Makefile/CI test command present.
run_case shell-test-runner \
  "build-test-evidence-present:1:PASS"

# Regression: shell filenames that merely contain a "test"-like substring
# without the required test[-_]/-[_]test convention must not false-positive
# as test-runner evidence.
run_case shell-test-lookalike \
  "build-test-evidence-present:1:FAIL"

echo "--- CASE F (tool-level): nonexistent repo path ---"
set +e
output="$(python3 "$PREFLIGHT" /nonexistent/path/does-not-exist 2>&1)"
status=$?
set -e
if [ "$status" -ne 1 ]; then
  echo "FAIL: expected exit 1 for a nonexistent repo path, got $status: $output" >&2
  exit 1
fi
echo "$output" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert "error" in d and "checks" not in d, d'
echo "PASS nonexistent-repo-path (fail closed, exit 1, no checks emitted)"

echo "All Diana Preflight tests passed."
