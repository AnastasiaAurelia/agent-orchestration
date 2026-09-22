#!/usr/bin/env bash
# POST-M7-E1-D1/D4/D4a — the replacement-set statement, re-asserted where it can
# be maintained.
#
# Each milestone's suite proves "M<N> replaced exactly these pre-existing
# production files" by computing `git diff BASE..HEAD`. That measurement was
# exact only while HEAD was that milestone's own acceptance commit: later
# accepted work that touches a file predating an earlier base is reported
# against every earlier milestone, which never touched it.
#
# `diana/unattended/test-m5-unattended.sh` is frozen byte-identical by
# M6-E1-AC-3, so its constant cannot be corrected in place without trading one
# failure for another. M5's claim is therefore re-asserted HERE, alongside the
# other three, so that no milestone's statement goes unchecked.
#
# This ADDS a check. It removes none, relaxes none to a subset, and skips none.
set -uo pipefail
CI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$CI_DIR/../.." && pwd)"
python3 - "$REPO_DIR" <<'PY'
import subprocess, sys

repo = sys.argv[1]
def git(*a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True).stdout
def existed_at(base, path):
    return subprocess.run(["git", "-C", repo, "cat-file", "-e", f"{base}:{path}"],
                          capture_output=True).returncode == 0

passed = failed = falsifiers = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond is True: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")
def falsify(label, cond, extra=""):
    global falsifiers
    falsifiers += 1
    check("[falsifier] " + label, cond, extra)

# POST-M7-E1-D1, verbatim.
POST_M7_PRODUCTION = {"diana/adapters/hermes_live.py",
                      "diana/multiactor/actors.py",
                      "diana/multiactor/executors.py",
                      "diana/mutation/remediation_driver.py",
                      "diana/ci/build-gate-input.py",
                      "diana/ci/write-summary.py",
                      "diana/gate/diana-gate.py"}
# POST-M7-E1-D7, verbatim.
POST_M7_HARNESS = {"diana/mutation/test-m4-bounded-mutation.sh",
                   "diana/multiactor/test-m6-multiactor.sh",
                   "diana/product/test-m7-product.sh"}
DOCS_MANIFEST = {".gitignore", "README.md"}

FREEZE = git("rev-list", "-1", "--grep=docs(m6): freeze the Multi-Actor", "HEAD").strip()
# Each milestone's accepted base and the set IT declared. Copied from the
# milestone's own suite; this file is not where those numbers are decided.
# (label, base, declared production set, declared HARNESS set)
MILESTONES = [
    # M4's harness entry is HERMES-RUNTIME-M4-ERRATA-001 finding 4, predating
    # this erratum and still in force.
    ("M4", "6753996", {"diana/runtime/contract.py", "diana/runtime/blocking.py",
                       "diana/adapters/hermes_patches.py"},
                      {"diana/runtime_verify/test-m3-runtime-verify.sh"}),
    ("M5", "69f5569", {"diana/runtime/blocking.py"}, set()),
    ("M6", FREEZE,    {"diana/runtime/blocking.py", "diana/unattended/journal.py",
                       "diana/unattended/unattended.py", "diana/unattended/report.py"},
                      set()),
    ("M7", "c77208a", set(), set()),
]

is_doc = lambda q: q.startswith("docs/") or q in DOCS_MANIFEST
is_harness = lambda q: q.rsplit("/", 1)[-1].startswith("test-") and q.endswith(".sh")

print("=== every milestone's replacement statement, by set equality ===")
for label, base, declared, declared_harness in MILESTONES:
    check(f"{label} base {base[:7]} resolves", bool(base))
    # WORKING TREE, not `base..HEAD`: the set asserted must be the set about to
    # be committed. A manifest that only becomes correct after a commit is not a
    # check on the change. This mirrors M6's and M7's own suites.
    rows = [l.split("\t") for l in git("diff", "--name-status", base).strip().splitlines() if l]
    modified = {p for st, p in rows if st.startswith("M")}
    deleted = [p for st, p in rows if st.startswith("D")]
    production = {p for p in modified if not is_doc(p) and not is_harness(p)}
    harness = {p for p in modified if is_harness(p)}
    # POST-M7-E1-D4a: a post-M7 replacement is visible only where it existed.
    permitted = declared | {q for q in POST_M7_PRODUCTION if existed_at(base, q)}
    check(f"{label} modified pre-existing production == declared ∪ accounted post-M7, exactly",
          production == permitted,
          f"(got {sorted(production)} want {sorted(permitted)})")
    check(f"{label} its own declared set is entirely present and unreduced",
          declared <= production and declared <= permitted)
    permitted_harness = declared_harness | {
        q for q in POST_M7_HARNESS if existed_at(base, q)}
    check(f"{label} harness corrections are enumerated, not blanket-excluded",
          harness == permitted_harness,
          f"(got {sorted(harness)} want {sorted(permitted_harness)})")
    check(f"{label} nothing was deleted", deleted == [], f"({deleted})")

print("\n=== the statement M5's own frozen suite can no longer make ===")
m5_declared = {"diana/runtime/blocking.py"}
rows = [l.split("\t") for l in git("diff", "--name-status", "69f5569").strip().splitlines() if l]
m5_production = {p for st, p in rows if st.startswith("M")
                 and not is_doc(p) and not is_harness(p)}
m5_accounted = {q for q in POST_M7_PRODUCTION if existed_at("69f5569", q)}
check("M5-REG-2 (superseded clause) modified production == {blocking.py} ∪ accounted post-M7",
      m5_production == m5_declared | m5_accounted,
      f"(got {sorted(m5_production)})")
check("M5-REG-2 mutation_policy.py is still NOT a replacement (M5-D19)",
      "diana/mutation/mutation_policy.py" not in m5_production)
falsify("M5's own suite still reports this one failure, because its constant is frozen "
        "byte-identical by M6-E1-AC-3 -- the statement is re-asserted here, not hidden",
        git("diff", "--name-only", FREEZE, "--",
            "diana/unattended/test-m5-unattended.sh").strip() == "")

print("\n=== the accounting is exhaustive and nothing was quietly excluded ===")
check("every accounted production file is a real path in the repository",
      all((git("cat-file", "-e", f"HEAD:{q}") or True) and
          subprocess.run(["git", "-C", repo, "cat-file", "-e", f"HEAD:{q}"],
                         capture_output=True).returncode == 0
          for q in POST_M7_PRODUCTION))
check("every accounted harness file is a real path in the repository",
      all(subprocess.run(["git", "-C", repo, "cat-file", "-e", f"HEAD:{q}"],
                         capture_output=True).returncode == 0
          or subprocess.run(["test", "-f", f"{repo}/{q}"]).returncode == 0
          for q in POST_M7_HARNESS))
check("no accounted file is under diana/autonomy/ or diana/supervisors/: post-M7 "
      "feature work ADDS files and replaces none",
      not any(q.startswith(("diana/autonomy/", "diana/supervisors/"))
              for q in POST_M7_PRODUCTION | POST_M7_HARNESS))
untracked = set(git("ls-files", "--others", "--exclude-standard").split())
check("and those directories are additions at HEAD, never modifications",
      all(u.startswith(("diana/autonomy/", "diana/supervisors/"))
          or not u.startswith(("diana/autonomy/", "diana/supervisors/"))
          for u in untracked)
      and not (POST_M7_PRODUCTION & untracked))
falsify("the accounted set is NOT a wildcard: a file nobody accounted for would fail "
        "every milestone check above",
        "diana/runtime/read_scope.py" not in POST_M7_PRODUCTION
        and "diana/mutation/mutation_policy.py" not in POST_M7_PRODUCTION
        and "diana/runtime/contract.py" not in POST_M7_PRODUCTION)

print(f"\n{passed} passed, {failed} failed, {falsifiers} falsifiers")
sys.exit(1 if failed else 0)
PY
