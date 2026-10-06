#!/usr/bin/env bash
# Behavioral-runtime-compatibility: exact-identity classification and the
# bounded compat_preflight that resolves an unlisted-but-verifiable SHA.
# Spec: fix/behavioral-runtime-compatibility.
set -euo pipefail

AD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${DIANA_HERMES_HOME:-$HOME/.hermes/hermes-agent}"
if [ ! -d "$HERMES_HOME" ]; then
  echo "SKIP  Hermes not installed at $HERMES_HOME"; exit 0
fi
PY_BIN="$(python3 "$AD_DIR/hermes_runtime.py" "$HERMES_HOME")" || {
  echo "SKIP  could not establish Hermes's bootstrap/runtime under $HERMES_HOME"; exit 0
}

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
export HERMES_SAFE_MODE=1

"$PY_BIN" - "$AD_DIR" "$TMP_DIR" "$HERMES_HOME" <<'PY'
import sys
from pathlib import Path

ad_dir, tmp, hermes_home = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, ad_dir)
sys.path.insert(0, str(Path(ad_dir).parent / "runtime"))
import blocking, hermes as H, hermes_patches as HP

passed = failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")

ORIGINAL_IDENTITIES = tuple(dict(x) for x in HP.CERTIFIED_IDENTITIES)

# --- 1. known certified exact SHA -> certified fast path --------------------
real_identity = HP.hermes_identity(hermes_home)
real_status = HP.identity_certified(real_identity)
check("a known certified exact SHA classifies as certified",
      real_status == HP.IDENTITY_CERTIFIED, f"(got {real_status} {real_identity})")
gate = H.resolve_identity_gate(hermes_home, identity=real_identity)
check("the identity gate takes the certified fast path with no preflight",
      gate["state"] == HP.IDENTITY_CERTIFIED and gate["preflight"] is None,
      f"(got {gate})")

# --- 2. SHA prefix only must not count as identity equality -----------------
prefix_identity = dict(real_identity)
prefix_identity["sha"] = real_identity["sha"][:12]
check("a SHA PREFIX of a certified identity is not treated as equality",
      HP.identity_certified(prefix_identity) != HP.IDENTITY_CERTIFIED,
      f"(got {HP.identity_certified(prefix_identity)})")
superstring_identity = dict(real_identity)
superstring_identity["sha"] = real_identity["sha"] + "00"
check("a SHA with trailing extra characters is not treated as equality",
      HP.identity_certified(superstring_identity) != HP.IDENTITY_CERTIFIED,
      f"(got {HP.identity_certified(superstring_identity)})")

# --- 3. unresolved/non-git/unverifiable identity -> refused -----------------
for bad in ({"source": "unknown", "sha": None}, {"source": "unreachable", "sha": None},
            {"source": "git", "sha": None}, {}):
    check(f"unverifiable identity {bad} classifies as unverifiable",
          HP.identity_certified(bad) == HP.IDENTITY_UNVERIFIABLE)
gate_unverifiable = H.resolve_identity_gate(hermes_home, identity={"source": "unknown", "sha": None})
check("the identity gate classifies an unverifiable identity as unverifiable, never a pass",
      gate_unverifiable["state"] == HP.IDENTITY_UNVERIFIABLE, f"(got {gate_unverifiable})")

# --- 4. exact unknown SHA + passing compatibility preflight -----------------
# Force the REAL, fully-functional certified install to be classified as
# "unlisted" by hiding its own identity from the registry for this probe
# only -- this drives the bounded preflight against a genuinely real,
# behaviorally-live Hermes checkout, not a stub, while proving it end to end.
HP.CERTIFIED_IDENTITIES = ()
try:
    gate_compat = H.resolve_identity_gate(hermes_home, identity=real_identity)
    check("an exact, verifiable, unlisted SHA that passes the behavioral preflight "
          "classifies as compatible-unlisted",
          gate_compat["state"] == "compatible-unlisted", f"(got {gate_compat['state']})")
    check("a passing preflight report is recorded",
          isinstance(gate_compat["preflight"], dict)
          and gate_compat["preflight"].get("result") == "compatible",
          f"(got {gate_compat.get('preflight')})")
    # The real governed-run entry point must actually ALLOW it, not just the
    # raw classification helper.
    repo = Path(tmp, "repo"); repo.mkdir(parents=True, exist_ok=True)
    report = H.check_pre_import(repo_root=str(repo), env={"HERMES_SAFE_MODE": "1"},
                                 hermes_home=hermes_home)
    check("check_pre_import allows a compatible-unlisted identity through",
          isinstance(report, dict))
finally:
    HP.CERTIFIED_IDENTITIES = ORIGINAL_IDENTITIES
check("the preflight NEVER mutates CERTIFIED_IDENTITIES as a side effect",
      tuple(dict(x) for x in HP.CERTIFIED_IDENTITIES) == ORIGINAL_IDENTITIES,
      f"(got {HP.CERTIFIED_IDENTITIES})")

# --- 5. exact unknown SHA + failing interface check -> incompatible --------
import subprocess
fake_home = Path(tmp, "fake-hermes-real-git")
(fake_home / "hermes_cli").mkdir(parents=True, exist_ok=True)
(fake_home / "model_tools.py").write_text("# stub, deliberately missing every real interface\n")
(fake_home / "hermes_cli" / "__init__.py").write_text("")
(fake_home / "hermes_cli" / "version_info.py").write_text(
    "def get_code_identity():\n"
    "    import subprocess\n"
    "    sha = subprocess.run(['git','rev-parse','HEAD'],cwd=%r,"
    "capture_output=True,text=True).stdout.strip()\n"
    "    return {'source':'git','sha':sha}\n" % str(fake_home)
)
g = lambda *a: subprocess.run(["git", "-C", str(fake_home), *a], capture_output=True, text=True, check=True)
g("init", "-q"); g("config", "user.email", "t@test"); g("config", "user.name", "t")
g("add", "-A"); g("commit", "-qm", "init")
fake_identity = HP.hermes_identity(str(fake_home))
check("the incompatible fixture resolves a real, distinct, exact git identity",
      fake_identity.get("source") == "git" and fake_identity.get("sha")
      and fake_identity.get("sha") != real_identity.get("sha"), f"(got {fake_identity})")
check("that identity is raw-classified as unlisted, not unverifiable or certified",
      HP.identity_certified(fake_identity) == HP.IDENTITY_UNLISTED,
      f"(got {HP.identity_certified(fake_identity)})")
gate_incompat = H.resolve_identity_gate(str(fake_home), identity=fake_identity)
check("an exact, verifiable, unlisted SHA that FAILS required-interface checks "
      "classifies as incompatible", gate_incompat["state"] == "incompatible",
      f"(got {gate_incompat})")
try:
    H.check_pre_import(repo_root=str(repo), env={"HERMES_SAFE_MODE": "1"}, hermes_home=str(fake_home))
    check("check_pre_import REFUSES an incompatible unlisted identity", False,
          "(no Blocked raised)")
except blocking.Blocked as b:
    check("check_pre_import REFUSES an incompatible unlisted identity",
          b.code == blocking.HERMES_COMPATIBILITY_PREFLIGHT_FAILED, f"(got {b.code!r})")
check("the incompatible-classification path also never mutates CERTIFIED_IDENTITIES",
      tuple(dict(x) for x in HP.CERTIFIED_IDENTITIES) == ORIGINAL_IDENTITIES,
      f"(got {HP.CERTIFIED_IDENTITIES})")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
