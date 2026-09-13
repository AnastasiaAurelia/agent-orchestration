#!/usr/bin/env bash
# The complete frozen M1 suite: every checkpoint suite, then a roll-up of all 13
# acceptance criteria from docs/architecture/HERMES-RUNTIME-M1.md.
#
# Each criterion names the suite that proves it, so "AC-n PASS" is traceable to
# assertions rather than asserted here a second time.
set -uo pipefail

ADV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIANA_DIR="$(cd "$ADV_DIR/.." && pwd)"

SUITES=(
  "$DIANA_DIR/runtime/test-contract.sh"
  "$DIANA_DIR/profile/test-repo-profile.sh"
  "$DIANA_DIR/advisory/test-dom-scan.sh"
  "$DIANA_DIR/advisory/test-artifact.sh"
  "$DIANA_DIR/adapters/test-hermes-confinement.sh"
  "$DIANA_DIR/adapters/test-hermes-capability.sh"
  "$DIANA_DIR/adapters/test-hermes-preflight.sh"
  "$DIANA_DIR/advisory/test-run.sh"
  "$DIANA_DIR/advisory/test-acceptance.sh"
)

declare -A RESULT
total_pass=0; total_fail=0; suite_fail=0

for suite in "${SUITES[@]}"; do
  name="$(basename "$suite")"
  out="$("$suite" 2>&1)"; rc=$?
  line="$(printf '%s\n' "$out" | grep -E '^[0-9]+ passed, [0-9]+ failed$' | tail -1)"
  if [ -z "$line" ]; then line="$(printf '%s\n' "$out" | tail -1)"; fi
  p="$(printf '%s' "$line" | sed -nE 's/^([0-9]+) passed.*/\1/p')"
  f="$(printf '%s' "$line" | sed -nE 's/.*, ([0-9]+) failed$/\1/p')"
  total_pass=$((total_pass + ${p:-0}))
  total_fail=$((total_fail + ${f:-0}))
  if [ "$rc" -eq 0 ]; then
    RESULT[$name]="PASS"; printf 'PASS  %-34s %s\n' "$name" "$line"
  else
    RESULT[$name]="FAIL"; suite_fail=$((suite_fail + 1))
    printf 'FAIL  %-34s %s\n' "$name" "$line"
    printf '%s\n' "$out" | grep '^FAIL' | head -5
  fi
done

ok() { [ "${RESULT[$1]:-FAIL}" = "PASS" ]; }
ac() {
  local id="$1" desc="$2"; shift 2
  for s in "$@"; do ok "$s" || { printf 'FAIL  %-6s %s\n' "$id" "$desc"; return 1; }; done
  printf 'PASS  %-6s %s  [%s]\n' "$id" "$desc" "$*"
}

echo
echo "=== ACCEPTANCE CRITERIA ==="
ac_fail=0
ac "AC-1"  "8 preflight-failure fixtures, each with its own reason code; clean config passes" test-hermes-preflight.sh || ac_fail=1
ac "AC-2"  "self-test on the real agent/pool-worker path, both halves" test-hermes-confinement.sh test-hermes-capability.sh || ac_fail=1
ac "AC-3"  "target repo byte- and git-state-identical before/after" test-acceptance.sh || ac_fail=1
ac "AC-4"  "artifact persisted outside the target repository" test-acceptance.sh || ac_fail=1
ac "AC-5"  "instrumented spies: zero Gate/ship/bundle/evidence_model invocations" test-acceptance.sh || ac_fail=1
ac "AC-6"  "no gh and no mutating git subprocess spawned" test-acceptance.sh || ac_fail=1
ac "AC-7"  "8 fixtures: exact findings, suppressions, outcomes, severity" test-dom-scan.sh test-run.sh || ac_fail=1
ac "AC-8"  "determinism; digest differs and validly recomputes per run" test-acceptance.sh test-dom-scan.sh || ac_fail=1
ac "AC-9"  "contract_digest recomputes under canonical serialization" test-contract.sh test-artifact.sh || ac_fail=1
ac "AC-10" "artifact fed to evidence_model classifies MALFORMED" test-artifact.sh || ac_fail=1
ac "AC-11" "unknown Hermes output field fails validation, not recorded" test-artifact.sh test-run.sh || ac_fail=1
ac "AC-12" "measured baseline delta: Gate FAIL + missed XSS vs DOM-XSS-001 HIGH" test-acceptance.sh || ac_fail=1
ac "AC-13" "AO path and pre-existing modules unchanged" test-acceptance.sh || ac_fail=1

echo
echo "M1 suites: $((${#SUITES[@]} - suite_fail))/${#SUITES[@]} green   assertions: ${total_pass} passed, ${total_fail} failed"
[ "$suite_fail" -eq 0 ] && [ "$ac_fail" -eq 0 ] && { echo "M1 ACCEPTANCE: PASS"; exit 0; }
echo "M1 ACCEPTANCE: FAIL"; exit 1
