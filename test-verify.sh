#!/usr/bin/env bash
# Tests for verify.sh's "Diana source repo" note.
#
# Proves:
#   (a) the note IS shown when the target looks like Diana's own source repo
#       (has diana/gate/diana-gate.py, no .claude/commands/fix.md)
#   (b) the note is NOT shown for a plain uninstalled/empty target directory
#
# Usage: bash test-verify.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY_SH="$SCRIPT_DIR/verify.sh"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

FAIL=0
NOTE_SNIPPET="looks like Diana's own source repository"

check() {
  local desc="$1" expect="$2" actual="$3"
  if [ "$expect" = "$actual" ]; then
    printf 'PASS  %s\n' "$desc"
  else
    printf 'FAIL  %s  (expected %s, got %s)\n' "$desc" "$expect" "$actual"
    FAIL=1
  fi
}

# --- Fixture (a): shaped like Diana's own source repo, no installation ---
DIANA_SOURCE_LIKE="$WORKDIR/diana-source-like"
mkdir -p "$DIANA_SOURCE_LIKE/diana/gate"
: > "$DIANA_SOURCE_LIKE/diana/gate/diana-gate.py"

OUTPUT_A="$("$VERIFY_SH" "$DIANA_SOURCE_LIKE" 2>&1)"
if echo "$OUTPUT_A" | grep -qF "$NOTE_SNIPPET"; then
  check "note shown for Diana-source-like target" "present" "present"
else
  check "note shown for Diana-source-like target" "present" "absent"
fi

# --- Fixture (b): plain empty/uninstalled target (no diana/ dir at all) ---
PLAIN_TARGET="$WORKDIR/plain-uninstalled"
mkdir -p "$PLAIN_TARGET"

OUTPUT_B="$("$VERIFY_SH" "$PLAIN_TARGET" 2>&1)"
if echo "$OUTPUT_B" | grep -qF "$NOTE_SNIPPET"; then
  check "note absent for plain uninstalled target" "absent" "present"
else
  check "note absent for plain uninstalled target" "absent" "absent"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "All test-verify.sh checks passed."
  exit 0
else
  echo "test-verify.sh: one or more checks failed."
  exit 1
fi
