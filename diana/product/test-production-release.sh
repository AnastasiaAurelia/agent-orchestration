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

# A stored proposal is release-bound. Create it, then change only Diana's
# declared release version and prove the old approval cannot be reused.
python3 - "$SRC" "$TMP" <<'PY'
import subprocess, sys, textwrap
from pathlib import Path
src, tmp = Path(sys.argv[1]), Path(sys.argv[2])
target = tmp / "release-bound-target"
(target / "src" / "core").mkdir(parents=True)
(target / "src" / "auth").mkdir(parents=True)
(target / "tests").mkdir()
(target / "src" / "core" / "calc.py").write_text("def add(a,b):\n    return a-b\n")
(target / "check.py").write_text(textwrap.dedent("""\
    import sys
    sys.path.insert(0, "src")
    from core.calc import add
    sys.exit(0 if add(2,3) == 5 else 1)
"""))
g=lambda *a: subprocess.run(["git","-C",str(target),*a],capture_output=True,text=True)
g("init","-q"); g("config","user.email","prod@test"); g("config","user.name","prod")
g("add","-A"); g("commit","-qm","init")
for sub in ("product","multiactor","unattended","runtime","mutation","adapters","profile"):
    sys.path.insert(0, str(src / "diana" / sub))
import proposal, refusal
base = tmp / "release-bound-proposals"
p = proposal.build(
    "Fix the failing tests in this repo, but don't touch auth or deployment",
    str(target), base=str(base))
(src / "VERSION").write_text("9.9.9-release-change\n")
try:
    proposal.load(p["proposal_digest"], str(base))
except refusal.Refused as exc:
    if exc.code != refusal.PROPOSAL_STALE:
        raise SystemExit(f"wrong refusal: {exc.code}")
else:
    raise SystemExit("proposal survived a Diana release identity change")
print("release binding refused stale approval")
PY
check "proposal approval is bound to Diana release identity" test "$?" -eq 0

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
git -C "$SRC" add VERSION
git -C "$SRC" -c user.email=prod@test -c user.name=prod commit -qm 'test: bump runtime version'
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
