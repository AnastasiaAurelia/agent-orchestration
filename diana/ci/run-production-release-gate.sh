#!/usr/bin/env bash
# Diana production-release certification gate.
#
# This is intentionally stricter than ordinary PR CI. A required suite that
# SKIPs is a release failure. Run only on the supported Linux certification
# environment with the exact supported Hermes runtime/provider available.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Resolve ONE Hermes home at gate entry and export it explicitly. Child suites
# must certify the same runtime rather than independently falling back to a
# different profile/default because DIANA_HERMES_HOME was only a shell-local
# variable in the invoking terminal.
HERMES_HOME="${DIANA_HERMES_HOME:-$HOME/.hermes/hermes-agent}"
export DIANA_HERMES_HOME="$HERMES_HOME"
HERMES_PYTHON="$(python3 "$ROOT/diana/adapters/hermes_runtime.py" "$HERMES_HOME" 2>&1)" || {
  echo "RELEASE-FAIL Hermes PM runtime could not be resolved for $HERMES_HOME"
  echo "$HERMES_PYTHON"
  exit 3
}
export DIANA_HERMES_PYTHON="$HERMES_PYTHON"
echo "RELEASE-INFO DIANA_HERMES_HOME=$DIANA_HERMES_HOME"
echo "RELEASE-INFO DIANA_HERMES_PYTHON=$DIANA_HERMES_PYTHON"

fail=0
pass=0

required() {
  local label="$1"; shift
  local out
  out="$(mktemp)"
  echo "=== $label ==="
  set +e
  "$@" >"$out" 2>&1
  local rc=$?
  set -e
  cat "$out"
  local pass_lines fail_lines skip_lines
  pass_lines="$(grep -c '^PASS  ' "$out" || true)"
  fail_lines="$(grep -c '^FAIL  ' "$out" || true)"
  skip_lines="$(grep -c '^[[:space:]]*SKIP\([[:space:]]\|$\)' "$out" || true)"
  # A suite-level skip means the suite produced no substantive assertions.
  # Nested/falsifier paths may emit SKIP while the enclosing required suite
  # still executes hundreds of assertions; those must be classified by rc.
  if [ "$rc" -eq 0 ] && [ "$pass_lines" -eq 0 ] && [ "$fail_lines" -eq 0 ] && [ "$skip_lines" -gt 0 ]; then
    echo "RELEASE-FAIL  $label skipped"
    fail=$((fail+1))
  elif [ "$rc" -ne 0 ]; then
    echo "RELEASE-FAIL  $label exited $rc"
    fail=$((fail+1))
  else
    echo "RELEASE-PASS  $label"
    pass=$((pass+1))
  fi
  rm -f "$out"
}

required_known_m5_exception() {
  local out
  out="$(mktemp)"
  echo "=== M5 unattended (governed historical accounting exception) ==="
  set +e
  bash diana/unattended/test-m5-unattended.sh >"$out" 2>&1
  local rc=$?
  set -e
  cat "$out"
  local fail_lines
  fail_lines="$(grep -c '^FAIL  ' "$out" || true)"
  if grep -Eq '^[[:space:]]*SKIP([[:space:]]|$)' "$out"; then
    echo "RELEASE-FAIL  M5 unattended skipped"
    fail=$((fail+1))
  elif [ "$rc" -eq 0 ]; then
    echo "RELEASE-FAIL  M5 unattended unexpectedly lost its governed frozen exception"
    fail=$((fail+1))
  elif [ "$fail_lines" -ne 1 ] || ! grep -Fq       "FAIL  M5-REG-2 modified pre-existing PRODUCTION code equals M5's declared set exactly" "$out"; then
    echo "RELEASE-FAIL  M5 unattended differs from the one bounded historical exception"
    fail=$((fail+1))
  else
    echo "RELEASE-PASS  M5 unattended substantive suite ran; only the exact frozen accounting exception remains"
    pass=$((pass+1))
  fi
  rm -f "$out"
}

[ "$(uname -s)" = "Linux" ] || {
  echo "RELEASE-FAIL unsupported OS: $(uname -s) (Linux required)"
  exit 3
}
python3 - <<'PY'
import sys
if sys.version_info[:2] != (3, 11):
    raise SystemExit(
        f"RELEASE-FAIL bootstrap Python {sys.version_info.major}.{sys.version_info.minor}; 3.11 required"
    )
print("RELEASE-PASS bootstrap Python 3.11")
PY

"$HERMES_PYTHON" - <<'PY'
import sys
v = sys.version_info[:2]
if not ((3, 11) <= v < (3, 15)):
    raise SystemExit(
        f"RELEASE-FAIL Hermes runtime Python {v[0]}.{v[1]}; supported >=3.11,<3.15"
    )
print(f"RELEASE-PASS Hermes runtime Python {v[0]}.{v[1]}")
PY

# Product/distribution first. If the installed/runtime story is not healthy,
# historical milestone evidence cannot make the release shippable.
required "runtime version" ./diana-do version
required "operator doctor" ./diana-do doctor
required "production distribution" bash diana/product/test-production-release.sh
required "post-M7 accounting" bash diana/ci/test-post-m7-replacement-set.sh

# Exact Hermes boundary evidence.
required "Hermes confinement" bash diana/adapters/test-hermes-confinement.sh
required "Hermes capability" bash diana/adapters/test-hermes-capability.sh
required "Hermes preflight" bash diana/adapters/test-hermes-preflight.sh
required "Hermes provider runtime" bash diana/adapters/test-hermes-provider-runtime.sh
required "diana-do runtime fail-closed" bash diana/adapters/test-diana-do-runtime-failclosed.sh

# Milestone/runtime evidence.
required "M4 bounded mutation" bash diana/mutation/test-m4-bounded-mutation.sh
required "M5 journal" bash diana/unattended/test-m5-journal.sh
required "M5 ownership" bash diana/unattended/test-m5-ownership.sh
required "M5 proofs" python3 diana/unattended/test_m5_proofs.py
# Run the frozen M5 unattended suite too. It is accepted only when its sole
# failure is exactly the immutable historical M5-REG-2 accounting clause
# superseded by the repo-wide exact reconciliation above. Any second failure,
# skip, or changed failure label is a release failure.
required_known_m5_exception
required "M6 review" bash diana/multiactor/test-m6-review.sh
required "M6 lease" bash diana/multiactor/test-m6-lease.sh
required "M6 multiactor" bash diana/multiactor/test-m6-multiactor.sh
required "M6 production reviewer" bash diana/multiactor/test-production-reviewer.sh
required "M7 product" bash diana/product/test-m7-product.sh
required "M7 audit" bash diana/product/test-m7-audit.sh
required "M7 budget" bash diana/product/test-m7-budget.sh
required "autonomy E2E" bash diana/autonomy/test-autonomy-e2e.sh

# Security regression families. The live GitHub Diana Gate and Diana Security
# Gate remain mandatory merge controls; these local suites certify the release
# tree itself rather than pretending a release script can self-approve a PR.
required "gate input regression" bash diana/ci/test-build-gate-input.sh
required "gate mapping regression" bash diana/ci/test-gate-mapping.sh
required "security catalog" bash diana/security/test-catalog.sh
required "security verifier runs" bash diana/security/test-ci-verifier-runs.sh
required "security evidence model" bash diana/security/test-evidence-model.sh
required "security provenance" bash diana/security/test-provenance.sh
required "security gate" bash diana/security/test-security-gate.sh

echo
echo "release gate: $pass passed, $fail failed"
if [ "$fail" -ne 0 ]; then
  echo "PRODUCTION READINESS VERDICT: NOT READY"
  exit 1
fi
echo "PRODUCTION READINESS VERDICT: release-gate evidence green; human/repository acceptance still required"
