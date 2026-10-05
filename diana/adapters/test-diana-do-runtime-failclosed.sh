#!/usr/bin/env bash
# CP7-adjacent: diana-do (the PRODUCT entry point, not a test harness) must
# never report success or SKIP when Hermes's runtime cannot be resolved. It
# must fail closed with a non-zero exit and Diana's own refusal vocabulary.
#
# This is deliberately NOT part of test-hermes-preflight.sh: that script's
# SKIP-on-environmental-unavailability is a TEST HARNESS convention (the
# matrix needs an installed, certifiable Hermes to isolate its cases) --
# diana-do itself has no such license. Every case here runs unconditionally,
# with no Hermes installation required, because what is under test is
# exactly the failure path that fires when one is absent/broken.
set -uo pipefail
AD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$AD_DIR/../.." && pwd)"
DIANA_DO="$REPO_DIR/diana-do"

passed=0; failed=0
check() {
  local label="$1"; shift
  if "$@"; then passed=$((passed+1)); echo "PASS  $label"
  else failed=$((failed+1)); echo "FAIL  $label"; fi
}

# --- (1) a Hermes home that does not exist at all -------------------------
out1="$(DIANA_HERMES_HOME=/nonexistent/hermes-home-does-not-exist-$$ "$DIANA_DO" status x 2>&1)"
rc1=$?
check "nonexistent HERMES_HOME: diana-do exits non-zero" bash -c "exit $([ "$rc1" -ne 0 ]; echo $?)"
check "nonexistent HERMES_HOME: never reports SKIP" bash -c "! grep -q '^SKIP' <<<\"$out1\""
check "nonexistent HERMES_HOME: never reports success/COMPLETE" \
      bash -c "! grep -qE 'COMPLETE|^OK$' <<<\"$out1\""
check "nonexistent HERMES_HOME: refuses in Diana's own vocabulary" \
      bash -c "grep -q 'REFUSED BY DIANA  \[hermes-unreachable\]' <<<\"$out1\""

# --- (2) a Hermes home that exists but is not a Hermes installation -------
empty_dir="$(mktemp -d)"
out2="$(DIANA_HERMES_HOME="$empty_dir" "$DIANA_DO" status x 2>&1)"
rc2=$?
rm -rf "$empty_dir"
check "empty HERMES_HOME dir: diana-do exits non-zero" bash -c "exit $([ "$rc2" -ne 0 ]; echo $?)"
check "empty HERMES_HOME dir: never reports SKIP" bash -c "! grep -q '^SKIP' <<<\"$out2\""
check "empty HERMES_HOME dir: refuses in Diana's own vocabulary" \
      bash -c "grep -q 'REFUSED BY DIANA  \[hermes-unreachable\]' <<<\"$out2\""

# --- (3) the exit code is EXIT_BLOCKED (3), matching product.py's own code -
check "nonexistent HERMES_HOME: exit code is exactly 3 (EXIT_BLOCKED)" \
      bash -c "exit $([ "$rc1" -eq 3 ]; echo $?)"

# --- (4) falsifier: a REAL Hermes home, if one is configured, is NOT refused
# this way -- proves the check above is actually discriminating, not always
# refusing regardless of input.
real_home="${DIANA_HERMES_HOME:-$HOME/.hermes/hermes-agent}"
if [ -d "$real_home" ] && [ -f "$real_home/model_tools.py" ]; then
  out4="$(DIANA_HERMES_HOME="$real_home" "$DIANA_DO" status x 2>&1)"
  check "[falsifier] a REAL Hermes home is never refused as hermes-unreachable" \
        bash -c "! grep -q 'hermes-unreachable' <<<\"$out4\""
else
  echo "SKIP  [falsifier] no real Hermes installation available to contrast against"
fi

echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
