#!/usr/bin/env bash
# Diana production-release certification gate.
#
# This is intentionally stricter than ordinary PR CI. A required suite that
# SKIPs is a release failure. Run only on the supported Linux certification
# environment with the exact supported Hermes runtime/provider available.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

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
  if grep -Eq '(^|[[:space:]])SKIP([[:space:]]|$)' "$out"; then
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

[ "$(uname -s)" = "Linux" ] || {
  echo "RELEASE-FAIL unsupported OS: $(uname -s) (Linux required)"
  exit 3
}
python3 - <<'PY'
import sys
if sys.version_info[:2] != (3, 11):
    raise SystemExit(f"RELEASE-FAIL Python {sys.version_info.major}.{sys.version_info.minor}; 3.11 required")
print("RELEASE-PASS Python 3.11")
PY

# Product/distribution first. If the installed/runtime story is not healthy,
# historical milestone evidence cannot make the release shippable.
required "operator doctor" ./diana-do doctor
required "production distribution" bash diana/product/test-production-release.sh
required "post-M7 accounting" bash diana/ci/test-post-m7-replacement-set.sh

# Exact Hermes boundary evidence.
required "Hermes confinement" bash diana/adapters/test-hermes-confinement.sh
required "Hermes capability" bash diana/adapters/test-hermes-capability.sh
required "Hermes preflight" bash diana/adapters/test-hermes-preflight.sh
required "diana-do runtime fail-closed" bash diana/adapters/test-diana-do-runtime-failclosed.sh

# Milestone/runtime evidence.
required "M4 bounded mutation" bash diana/mutation/test-m4-bounded-mutation.sh
required "M5 journal" bash diana/unattended/test-m5-journal.sh
required "M5 ownership" bash diana/unattended/test-m5-ownership.sh
# M5 unattended has one historically governed frozen accounting failure under
# ERRATA-001/002. It is NOT silently converted to green here. The maintained
# repo-wide accounting assertion above must pass, while the substantive M5
# suites below still run.
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
