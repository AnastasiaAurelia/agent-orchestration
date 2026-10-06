#!/usr/bin/env bash
# Production distribution/diagnostics/adversarial regression tests.
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

# Work from a local clone so upgrade/version tests never dirty the checkout
# running the suite.
git clone -q --local "$ROOT" "$TMP/src"
SRC="$TMP/src"
PREFIX="$TMP/prefix"
BIN="$TMP/bin"
TRUST="$TMP/trust/runtime-trust.key"
export DIANA_RUNTIME_TRUST_FILE="$TRUST"
MANAGER=(python3 "$SRC/diana/product/runtime_install.py")

check "version command is deterministic JSON"   python3 "$SRC/diana/product/release.py" version

# No Hermes is required for VERSION. Doctor must fail closed rather than turn a
# missing runtime into a green diagnostic, and must not print unrelated secret
# values from the environment.
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
PY
check "proposal approval is bound to Diana release identity" test "$?" -eq 0
git -C "$SRC" checkout -q -- VERSION

check "fresh governed-runtime install"   "${MANAGER[@]}" install --prefix "$PREFIX" --bin-dir "$BIN"
check "installed runtime verifies with authenticated manifest"   "${MANAGER[@]}" verify --prefix "$PREFIX" --bin-dir "$BIN"
check "launcher is Diana-managed symlink" test -L "$BIN/diana-do"

DIANA_RUNTIME_TRUST_FILE="$TRUST" "$BIN/diana-do" doctor-json "$TMP/no-hermes" \
  >"$TMP/installed-doctor.json" 2>/dev/null && installed_doctor_rc=0 || installed_doctor_rc=$?
check "installed doctor still fails closed without Hermes" test "$installed_doctor_rc" -eq 3
check "installed doctor authenticates its release before diagnosing Hermes" \
  grep -Fq '"manifest_authenticated": true' "$TMP/installed-doctor.json"
check "trust key is outside runtime prefix"   bash -c 'case "$1" in "$2"/*) exit 1;; *) exit 0;; esac' _ "$TRUST" "$PREFIX"
check "trust key is private"   bash -c '[ "$(stat -c %a "$1")" = 600 ]' _ "$TRUST"

# Trust key must never live inside the runtime prefix.
expect_fail "trust key inside runtime prefix is refused"   "${MANAGER[@]}" verify --prefix "$PREFIX" --bin-dir "$BIN"   --trust-file "$PREFIX/forged.key"

# A collision must be refused rather than overwritten.
mkdir -p "$TMP/collision-bin"
printf 'foreign\n' > "$TMP/collision-bin/diana-do"
expect_fail "unmanaged launcher collision is refused"   "${MANAGER[@]}" install --prefix "$TMP/collision-prefix" --bin-dir "$TMP/collision-bin"
check "foreign launcher was not overwritten" grep -qx 'foreign' "$TMP/collision-bin/diana-do"

# A symlinked runtime prefix is ambiguous authority and must be refused.
mkdir -p "$TMP/real-prefix"
ln -s "$TMP/real-prefix" "$TMP/symlink-prefix"
expect_fail "symlink runtime prefix is refused"   "${MANAGER[@]}" install --prefix "$TMP/symlink-prefix" --bin-dir "$TMP/symlink-bin"

# Clone one installed prefix safely for isolated tamper tests. Repoint current
# and launcher to the COPY so a negative test can never mutate the live fixture.
clone_prefix() {
  local src="$1" dst="$2" bindir="$3"
  cp -a "$src" "$dst"
  local rid
  rid="$(basename "$(readlink "$src/current")")"
  ln -sfn "$dst/releases/$rid" "$dst/current"
  mkdir -p "$bindir"
  ln -sfn "$dst/current/diana-do" "$bindir/diana-do"
}

clone_prefix "$PREFIX" "$TMP/corrupt-prefix" "$TMP/corrupt-bin"
printf 'tampered\n' > "$TMP/corrupt-prefix/current/VERSION"
expect_fail "installed-file tampering is detected"   "${MANAGER[@]}" verify --prefix "$TMP/corrupt-prefix" --bin-dir "$TMP/corrupt-bin"

# Stronger falsifier: forge BOTH a runtime file and its manifest hash/size so
# the release is internally self-consistent. HMAC authentication must still
# reject it because the attacker is limited to the runtime prefix and does not
# possess the external trust key.
clone_prefix "$PREFIX" "$TMP/forged-prefix" "$TMP/forged-bin"
printf 'forged-but-self-consistent\n' > "$TMP/forged-prefix/current/VERSION"
python3 - "$TMP/forged-prefix/current" <<'PY'
import hashlib, json, sys
from pathlib import Path
root=Path(sys.argv[1])
m=root/"release-manifest.json"
doc=json.loads(m.read_text())
p=root/"VERSION"
doc["files"]["VERSION"]={"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"size":p.stat().st_size}
# Deliberately leave auth unchanged: a prefix-only attacker cannot recompute it.
m.write_text(json.dumps(doc,indent=2,sort_keys=True)+"\n")
PY
expect_fail "self-consistent forged manifest is rejected by external trust anchor"   "${MANAGER[@]}" verify --prefix "$TMP/forged-prefix" --bin-dir "$TMP/forged-bin"

# Simulate a hard crash during transition. Presence of the durable marker is
# enough to make all external verification refuse instead of guessing which
# symlink pair is authoritative.
printf '{"operation":"upgrade"}\n' > "$PREFIX/transition.json"
expect_fail "interrupted activation marker fails closed"   "${MANAGER[@]}" verify --prefix "$PREFIX" --bin-dir "$BIN"
rm -f "$PREFIX/transition.json"

# Simulate an interrupted staging operation. Upgrade must refuse ambiguous
# partial state rather than deleting or adopting it.
mkdir -p "$PREFIX/.staging/abandoned"
printf 'partial\n' > "$PREFIX/.staging/abandoned/file"
expect_fail "interrupted staging is refused"   "${MANAGER[@]}" upgrade --prefix "$PREFIX" --bin-dir "$BIN"
rm -rf "$PREFIX/.staging/abandoned"
rmdir "$PREFIX/.staging" 2>/dev/null || true

# Upgrade under a new explicit version, then rollback to the immediately prior
# verified release.
printf '0.1.0-dev.2\n' > "$SRC/VERSION"
git -C "$SRC" add VERSION
git -C "$SRC" -c user.email=prod@test -c user.name=prod commit -qm 'test: bump runtime version'
check "bounded runtime upgrade"   "${MANAGER[@]}" upgrade --prefix "$PREFIX" --bin-dir "$BIN"
check "upgraded runtime verifies"   "${MANAGER[@]}" verify --prefix "$PREFIX" --bin-dir "$BIN"
check "upgrade activated the new explicit version"   grep -qx '0.1.0-dev.2' "$PREFIX/current/VERSION"
expect_fail "same-version upgrade is refused explicitly"   "${MANAGER[@]}" upgrade --prefix "$PREFIX" --bin-dir "$BIN"

# Independent rollback falsifiers.
P2="$TMP/no-previous-prefix"; B2="$TMP/no-previous-bin"; T2="$TMP/trust2/key"
DIANA_RUNTIME_TRUST_FILE="$T2" "${MANAGER[@]}" install --prefix "$P2" --bin-dir "$B2" >/dev/null
expect_fail "rollback with no previous release is refused"   env DIANA_RUNTIME_TRUST_FILE="$T2" "${MANAGER[@]}" rollback --prefix "$P2" --bin-dir "$B2"

# Corrupt the previous release in an isolated copy of the upgraded installation.
cp -a "$PREFIX" "$TMP/rollback-corrupt-prefix"
CURRID="$(basename "$(readlink "$PREFIX/current")")"
PREVID="$(basename "$(readlink "$PREFIX/previous")")"
ln -sfn "$TMP/rollback-corrupt-prefix/releases/$CURRID" "$TMP/rollback-corrupt-prefix/current"
ln -sfn "$TMP/rollback-corrupt-prefix/releases/$PREVID" "$TMP/rollback-corrupt-prefix/previous"
mkdir -p "$TMP/rollback-corrupt-bin"
ln -sfn "$TMP/rollback-corrupt-prefix/current/diana-do" "$TMP/rollback-corrupt-bin/diana-do"
printf 'corrupt previous\n' > "$TMP/rollback-corrupt-prefix/previous/VERSION"
expect_fail "rollback refuses corrupted previous release"   "${MANAGER[@]}" rollback --prefix "$TMP/rollback-corrupt-prefix" --bin-dir "$TMP/rollback-corrupt-bin"

check "rollback to prior verified runtime"   "${MANAGER[@]}" rollback --prefix "$PREFIX" --bin-dir "$BIN"
check "rollback restored prior version"   grep -qx '0.1.0-dev.1' "$PREFIX/current/VERSION"
check "rollback result verifies"   "${MANAGER[@]}" verify --prefix "$PREFIX" --bin-dir "$BIN"

check "managed uninstall"   "${MANAGER[@]}" uninstall --prefix "$PREFIX" --bin-dir "$BIN"
check "uninstall removes managed prefix" test ! -e "$PREFIX"
check "uninstall removes managed launcher" test ! -e "$BIN/diana-do"
check "uninstall deliberately retains external trust key" test -f "$TRUST"

echo
echo "$pass passed, $fail failed"
test "$fail" -eq 0
