#!/usr/bin/env bash
# Regression coverage for repository-agnostic write-scope derivation.
#
# Measured defect this suite exists for: write authority was drawn ONLY from
# four fixed directory names (src, lib, tests, test). A repository whose code
# lives in a root-level file, or under any other directory, had no writable
# scope at all -- and a request that NAMED such a path was answered with a
# different, narrower scope, silently, rather than with that path or an honest
# refusal. Diana could not act as a generic coding orchestrator.
#
# Nothing here widens authority. A named path is a REQUEST, validated against
# the same frozen policy every other Intent field is, and every unsafe form is
# a refusal rather than a quiet omission.
set -uo pipefail
PROD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIANA_DIR="$(cd "$PROD_DIR/.." && pwd)"
REPO_DIR="$(cd "$DIANA_DIR/.." && pwd)"
TMP_DIR="$(mktemp -d)"; trap 'rm -rf "$TMP_DIR"' EXIT
python3 - "$DIANA_DIR" "$TMP_DIR" "$REPO_DIR" <<'PY'
import json, os, subprocess, sys, uuid
from pathlib import Path

diana, tmp, repo_dir = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
for sub in ("product", "multiactor", "unattended", "runtime", "mutation", "adapters", "profile"):
    sys.path.insert(0, str(diana / sub))
import catalogue as CAT, intent as I, proposal as P, refusal as R
import contract as C, projection as PJ, topology as T

passed = failed = falsifiers = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond is True: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")
def falsify(label, cond, extra=""):
    global falsifiers
    falsifiers += 1
    check("[falsifier] " + label, cond, extra)
def refused(fn):
    try:
        fn(); return None
    except R.Refused as exc: return exc.code

def fresh(name, *, legacy=False, symlink=False):
    """A repository shaped like NONE of the fallback candidates."""
    root = tmp / f"r-{name}-{uuid.uuid4().hex[:6]}"
    for d in ("app", "packages/core", "services/api", "compiler", "config"):
        (root / d).mkdir(parents=True)
    (root / "foo.js").write_text("x\n")
    (root / "app" / "main.py").write_text("x\n")
    (root / "packages" / "core" / "index.ts").write_text("x\n")
    (root / "services" / "api" / "server.ts").write_text("x\n")
    (root / "compiler" / "parser.py").write_text("x\n")
    (root / "config" / ".env").write_text("SECRET=1\n")
    (root / "package.json").write_text('{"scripts":{"test":"true"}}\n')
    if legacy:
        (root / "src").mkdir()
        (root / "src" / "legacy.py").write_text("x\n")
    if symlink:
        os.symlink("/etc", root / "escape")
    g = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)
    g("init", "-q"); g("config", "user.email", "t@x"); g("config", "user.name", "t")
    g("add", "-A"); g("commit", "-qm", "init")
    return root

def paths(goal, root):
    return I.classify(goal, str(root))["write_paths"]

ROOT = fresh("base")

# ======================================================================
print("=== explicit paths become the write scope, on any repository shape ===")
check("a ROOT-LEVEL file is accepted -- the case the old derivation could not "
      "express at all", paths("fix the crash in foo.js", ROOT) == ["foo.js"],
      f"({paths('fix the crash in foo.js', ROOT)})")
check("a NESTED file under a non-candidate directory is accepted",
      paths("fix the crash in app/main.py", ROOT) == ["app/main.py"])
check("a deeply nested file is accepted",
      paths("fix services/api/server.ts", ROOT) == ["services/api/server.ts"])
check("an explicit DIRECTORY is accepted, with or without a trailing slash",
      paths("fix the exports in packages/core/", ROOT) == ["packages/core"]
      and paths("fix the exports in packages/core", ROOT) == ["packages/core"])
check("mixed files and directories are accepted together, canonically ordered",
      paths("fix services/api/server.ts and app/main.py and packages/core/", ROOT)
      == ["app/main.py", "packages/core", "services/api/server.ts"],
      f"({paths('fix services/api/server.ts and app/main.py and packages/core/', ROOT)})")
check("a not-yet-existing file inside an existing directory is accepted, so a "
      "repair may add the regression test it was asked for",
      paths("fix app/main.py and add app/main_test.py", ROOT)
      == ["app/main.py", "app/main_test.py"])
check("an explicit ./ prefix names a not-yet-existing root-level file",
      paths("fix ./newfile.js", ROOT) == ["newfile.js"])
falsify("none of these paths is reachable from the fallback table, so the results "
        "above cannot have come from it",
        not any(p.split("/")[0] in CAT.WRITE_ROOT_CANDIDATES
                for p in ["foo.js", "app/main.py", "services/api/server.ts",
                          "packages/core", "newfile.js"]))

print("\n--- prose that merely resembles a path is NOT authority ---")
check("a bare word matching a real directory does not grant it",
      refused(lambda: paths("fix the parser in compiler", ROOT)) == R.NO_WRITABLE_SCOPE)
falsify("that directory really does exist, so the refusal is the RULE and not a "
        "missing directory", (ROOT / "compiler").is_dir())
check("'and/or' and '24/7' name nothing and are ignored, while a real path in the "
      "same sentence is still found",
      paths("fix and/or handling and 24/7 uptime in foo.js", ROOT) == ["foo.js"])
check("surrounding markdown, quotes and sentence punctuation are trimmed",
      paths("fix the guard in `app/main.py`.", ROOT) == ["app/main.py"]
      and paths('fix "app/main.py", please', ROOT) == ["app/main.py"]
      and paths("fix app/main.py (the entry point)", ROOT) == ["app/main.py"])

print("\n--- normalisation and de-duplication are deterministic ---")
check("the same path named twice is normalised once",
      paths("fix app/main.py and also app/main.py", ROOT) == ["app/main.py"])
check("./ and trailing-slash spellings of one path collapse to one entry",
      paths("fix ./app/main.py and app/main.py", ROOT) == ["app/main.py"])
check("a path already covered by a named ancestor is dropped, not repeated",
      paths("fix app/ and app/main.py", ROOT) == ["app"],
      f"({paths('fix app/ and app/main.py', ROOT)})")
check("the scope is a function of the SET of named paths, not their order",
      paths("fix app/main.py then foo.js", ROOT) == paths("fix foo.js then app/main.py", ROOT))

# ======================================================================
print("\n=== an unsafe or unavailable named path REFUSES; it is never dropped ===")
SYM = fresh("sym", symlink=True)
CASES = [
    ("an absolute path", "fix /etc/passwd", ROOT, R.WRITE_PATH_OUTSIDE_REPO),
    ("a home-relative path", "fix ~/.ssh/id_rsa", ROOT, R.WRITE_PATH_OUTSIDE_REPO),
    ("upward traversal", "fix ../../etc/shadow", ROOT, R.WRITE_PATH_OUTSIDE_REPO),
    ("traversal written with backslashes", "fix ..\\..\\etc\\shadow", ROOT,
     R.WRITE_PATH_OUTSIDE_REPO),
    ("a traversal that re-enters the repo", "fix app/../../etc/passwd", ROOT,
     R.WRITE_PATH_OUTSIDE_REPO),
    ("the repository root itself", "fix ./", ROOT, R.WRITE_PATH_OUTSIDE_REPO),
    ("a symlink escaping the repository", "fix escape/passwd", SYM,
     R.WRITE_PATH_OUTSIDE_REPO),
    ("the git directory", "fix .git/config", ROOT, R.WRITE_PATH_FORBIDDEN),
    ("the CI surface", "fix .github/workflows/ci.yml", ROOT, R.WRITE_PATH_FORBIDDEN),
    ("Diana's own enforcement surface", "fix diana/gate/diana-gate.py", ROOT,
     R.WRITE_PATH_FORBIDDEN),
    ("an environment file", "fix config/.env", ROOT, R.WRITE_PATH_FORBIDDEN),
    ("a suffixed environment file", "fix config/.env.production", ROOT,
     R.WRITE_PATH_FORBIDDEN),
]
for label, goal, root, want in CASES:
    check(f"{label} is REFUSED with {want}",
          refused(lambda g=goal, r=root: paths(g, r)) == want,
          f"({refused(lambda g=goal, r=root: paths(g, r))})")

print("\n--- the critical property: an unsafe path cannot be silently ignored ---")
mixed = "fix app/main.py and /etc/passwd"
check("naming one safe and one unsafe path refuses the whole request",
      refused(lambda: paths(mixed, ROOT)) == R.WRITE_PATH_OUTSIDE_REPO)
falsify("the safe half of that request WOULD have been grantable on its own, so the "
        "refusal is the unsafe path and not the sentence",
        paths("fix app/main.py", ROOT) == ["app/main.py"])
LEG = fresh("legacy-and-unsafe", legacy=True)
check("an unsafe path is refused even when a legacy fallback scope was available "
      "to quietly substitute",
      refused(lambda: paths("fix .git/config", LEG)) == R.WRITE_PATH_FORBIDDEN)
falsify("that repository really does have a fallback scope, so the refusal replaced "
        "a silent substitution", paths("fix the tests", LEG) == ["src"])
check("a named path excluded by the same request refuses rather than choosing",
      refused(lambda: paths("fix app/auth/login.py but don't touch auth",
                            fresh("excl"))) in
      (R.WRITE_PATH_EXCLUDED, R.NO_WRITABLE_SCOPE))

# ======================================================================
print("\n=== a request naming no path preserves the legacy fallback exactly ===")
check("the fallback table is unchanged",
      CAT.WRITE_ROOT_CANDIDATES == ("src", "lib", "tests", "test"))
check("a repository with a fallback directory and no named path still derives it",
      paths("fix the failing tests in this repo", LEG) == ["src"])
check("the historical exclusion behaviour still applies to the fallback",
      "auth" not in json.dumps(paths(
          "Fix the failing tests in this repo, but don't touch auth or deployment",
          fresh("legacy-auth", legacy=True))))
check("a repository with neither a named path nor a fallback still refuses",
      refused(lambda: paths("fix the tests", ROOT)) == R.NO_WRITABLE_SCOPE)
def detail_of(fn):
    try:
        fn(); return ""
    except R.Refused as exc: return exc.detail
check("and the refusal tells the user how to name a path instead",
      "Name the file or directory to change"
      in detail_of(lambda: paths("fix the tests", ROOT)),
      f"({detail_of(lambda: paths('fix the tests', ROOT))!r})")

# ======================================================================
print("\n=== the named scope reaches the Builder unchanged, and grants nothing more ===")
run_id = str(uuid.uuid4())
goal = "fix services/api/server.ts and foo.js"
intent_doc = I.classify(goal, str(ROOT))
derived = P._derive(intent_doc, str(ROOT), run_id, 6, 3600)
cb = derived["contract"]
granted = cb["capability_envelope"]["write_scope"]["allowed_roots"]
expect = [str((ROOT / p).resolve()) for p in ["foo.js", "services/api/server.ts"]]
check("the approved write_scope is EXACTLY the named paths, absolute and resolved",
      sorted(granted) == sorted(expect), f"({granted})")
check("BUILDER's projection is that envelope exactly -- the Builder receives what "
      "was approved, unchanged",
      PJ.derive("BUILDER", cb, T.build(run_id=run_id))["capability_envelope"]
      ["write_scope"]["allowed_roots"] == granted)
check("REVIEWER remains read-only: no write_scope at all, and only read tools",
      PJ.derive("REVIEWER", cb, T.build(run_id=run_id))["capability_envelope"]
      == {"allowed_tools": ["read_file", "search_files"]})
import read_scope as RS
def writable(rel):
    """The REAL decision function the dispatch boundary uses."""
    return RS.decide(str(ROOT / rel), cb["capability_envelope"]["write_scope"])[0]
check("the granted scope admits the named root-level file and refuses a sibling",
      writable("foo.js") is True and writable("other.js") is False)
check("a named FILE grants that file only -- not the directory holding it",
      writable("services/api/server.ts") is True
      and writable("services/api/deep/x.ts") is False
      and writable("services/api/other.ts") is False)
dir_intent = I.classify("fix packages/core/", str(ROOT))
dir_cb = P._derive(dir_intent, str(ROOT), str(uuid.uuid4()), 6, 3600)["contract"]
dir_ws = dir_cb["capability_envelope"]["write_scope"]
check("a named DIRECTORY grants what is under it, and nothing beside it",
      RS.decide(str(ROOT / "packages/core/index.ts"), dir_ws)[0] is True
      and RS.decide(str(ROOT / "packages/core/deep/x.ts"), dir_ws)[0] is True
      and RS.decide(str(ROOT / "packages/other.ts"), dir_ws)[0] is False)
falsify("a path that merely shares a prefix with the named file is not writable, so "
        "an exact-file scope is exact", writable("foo.js.bak") is False)
check("nothing else in the repository became writable",
      all(writable(p) is False for p in
          ("app/main.py", "packages/core/index.ts", "compiler/parser.py",
           "package.json", "config/.env")))

print("\n--- no other authority axis moved ---")
check("allowed_tools is M4's envelope exactly",
      cb["capability_envelope"]["allowed_tools"]
      == sorted(["read_file", "search_files", "write_file", "patch", "terminal"]))
check("allowed_commands still comes only from the frozen catalogue",
      set(cb["capability_envelope"]["allowed_commands"])
      <= set(CAT.commands_for(str(ROOT))))
check("risk and depth are still derived from the class, never from the request",
      cb["risk"] == "ELEVATED" and cb["depth"] == "D2")
check("naming a path proposes no new field: the Intent schema is unchanged",
      set(intent_doc) - {"_withheld"} == set(I.INTENT_KEYS))
falsify("a named path cannot smuggle risk or depth -- the schema refuses the field",
        refused(lambda: I.validate(dict(intent_doc, risk="SAFE"), str(ROOT)))
        == R.INTENT_UNKNOWN_FIELD)

# ======================================================================
print("\n=== changing the named paths changes the proposal digest ===")
# One fixed run_id throughout, so the ONLY thing varying is the named paths.
# `P.build` mints a fresh run_id per call and run_id is inside the authority
# view, so comparing two `build` results would prove nothing about paths.
FIXED = str(uuid.uuid4())
def digest_for(goal):
    d = P._derive(I.classify(goal, str(ROOT)), str(ROOT), FIXED, 6, 3600)
    return P.digest_of(d["contract"], d["items"], d["topology"], d["policy"])
d1 = digest_for("fix app/main.py")
d2 = digest_for("fix foo.js")
d3 = digest_for("fix app/main.py and foo.js")
check("two different named paths produce two different digests", d1 != d2)
check("adding a second named path changes the digest again", d3 != d1 and d3 != d2)
falsify("with the paths held constant the digest is stable, so the differences above "
        "are the PATHS and not incidental run identity",
        digest_for("fix app/main.py") == d1)
_a = P._derive(I.classify("fix app/main.py and foo.js", str(ROOT)),
               str(ROOT), FIXED, 6, 3600)["contract"]
_b = P._derive(I.classify("fix foo.js and app/main.py", str(ROOT)),
               str(ROOT), FIXED, 6, 3600)["contract"]
falsify("naming the same paths in the other order grants the IDENTICAL write scope; "
        "the digest still differs only because the task text is itself part of the "
        "authority, which is the digest working rather than the scope wobbling",
        _a["capability_envelope"]["write_scope"] == _b["capability_envelope"]["write_scope"]
        and _a["task"] != _b["task"]
        and digest_for("fix foo.js and app/main.py") != d3)

# ======================================================================
print("\n=== the validator still refuses a hand-forged write_paths ===")
forged = dict(intent_doc)
for bad, want in ((["/etc"], R.WRITE_PATH_OUTSIDE_REPO),
                  (["../.."], R.WRITE_PATH_OUTSIDE_REPO),
                  ([".git"], R.WRITE_PATH_FORBIDDEN),
                  (["diana/runtime"], R.WRITE_PATH_FORBIDDEN),
                  ([".env"], R.WRITE_PATH_FORBIDDEN)):
    forged = dict(intent_doc); forged["write_paths"] = bad
    code = refused(lambda f=forged: I.validate(f, str(ROOT)))
    check(f"validate() refuses a forged write_paths {bad}", code == want, f"({code})")

print(f"\n{passed} passed, {failed} failed, {falsifiers} falsifiers")
sys.exit(1 if failed else 0)
PY
