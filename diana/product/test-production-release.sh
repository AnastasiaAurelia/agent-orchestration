#!/usr/bin/env bash
# Production distribution/diagnostics regression tests.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0
check() {
  local label="$1"; shift
  if "$@"; then
    echo "PASS  $label"; pass=$((pass+1))
  else
    echo "FAIL  $label"; fail=$((fail+1))
  fi
}
expect_fail() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "FAIL  $label"; fail=$((fail+1))
  else
    echo "PASS  $label"; pass=$((pass+1))
  fi
}

# Work from a local clone so upgrade can change VERSION without touching the
# checkout that is running the test.
git clone -q --local "$ROOT" "$TMP/src"
SRC="$TMP/src"
PREFIX="$TMP/prefix"
BIN="$TMP/bin"
MANAGER=(python3 "$SRC/diana/product/runtime_install.py")

check "version command is deterministic JSON"   python3 "$SRC/diana/product/release.py" version

# No Hermes is required for VERSION. doctor must fail closed rather than turn
# a missing runtime into a green diagnostic.
env -u DIANA_HERMES_HOME SECRET_TOKEN='must-not-appear'   python3 "$SRC/diana/product/release.py" doctor-json "$TMP/no-hermes"   >"$TMP/doctor.json" 2>/dev/null && doctor_rc=0 || doctor_rc=$?
check "doctor refuses a missing Hermes runtime" test "$doctor_rc" -eq 3
check "doctor never prints credential values"   bash -c '! grep -q "must-not-appear" "$1"' _ "$TMP/doctor.json"

check "fresh governed-runtime install"   "${MANAGER[@]}" install --prefix "$PREFIX" --bin-dir "$BIN"
check "installed runtime verifies"   "${MANAGER[@]}" verify --prefix "$PREFIX" --bin-dir "$BIN"
check "launcher is Diana-managed symlink" test -L "$BIN/diana-do"

# A collision must be refused rather than overwritten.
mkdir -p "$TMP/collision-bin"
printf 'foreign\n' > "$TMP/collision-bin/diana-do"
expect_fail "unmanaged launcher collision is refused"   "${MANAGER[@]}" install --prefix "$TMP/collision-prefix" --bin-dir "$TMP/collision-bin"
check "foreign launcher was not overwritten"   grep -qx 'foreign' "$TMP/collision-bin/diana-do"

# Corruption of an installed file must make verification fail.
cp -a "$PREFIX" "$TMP/corrupt-prefix"
printf 'tampered\n' > "$TMP/corrupt-prefix/current/VERSION"
expect_fail "installed-file tampering is detected"   "${MANAGER[@]}" verify --prefix "$TMP/corrupt-prefix" --bin-dir "$BIN"

# Upgrade under a new explicit version, then rollback to the immediately prior
# verified release.
printf '0.1.0-dev.2\n' > "$SRC/VERSION"
check "bounded runtime upgrade"   "${MANAGER[@]}" upgrade --prefix "$PREFIX" --bin-dir "$BIN"
check "upgraded runtime verifies"   "${MANAGER[@]}" verify --prefix "$PREFIX" --bin-dir "$BIN"
check "upgrade activated the new explicit version"   grep -qx '0.1.0-dev.2' "$PREFIX/current/VERSION"
check "rollback to prior verified runtime"   "${MANAGER[@]}" rollback --prefix "$PREFIX" --bin-dir "$BIN"
check "rollback restored prior version"   grep -qx '0.1.0-dev.1' "$PREFIX/current/VERSION"

check "managed uninstall"   "${MANAGER[@]}" uninstall --prefix "$PREFIX" --bin-dir "$BIN"
check "uninstall removes managed prefix" test ! -e "$PREFIX"
check "uninstall removes managed launcher" test ! -e "$BIN/diana-do"

echo
echo "$pass passed, $fail failed"
test "$fail" -eq 0
