#!/usr/bin/env bash
# CP2: deterministic repository profile, applicability, inventory confinement.
set -euo pipefail

PROFILE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

python3 - "$PROFILE_DIR" "$TMP_DIR" <<'PY'
import json, os, sys
from pathlib import Path

profile_dir, tmp = sys.argv[1], sys.argv[2]
sys.path.insert(0, profile_dir)
sys.path.insert(0, str(Path(profile_dir).parent / "runtime"))
import repo_profile as RP

passed = failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")

def status_of(prof, category):
    return next(c["status"] for c in prof["categories"] if c["category"] == category)

def make(root, files):
    for rel, body in files.items():
        p = Path(root, rel); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(body)
    return root

def scope_for(root):
    return {"allowed_roots": [root], "denied_subpaths": [".git/", ".env", ".env.*"]}

# --- static client-only fixture ---
static = make(os.path.join(tmp, "static"), {
    "index.html": "<script src=app.js></script>",
    "app.js": "el.innerHTML = location.hash;",
    "style.css": "body{}",
    ".env": "SECRET=1",
    ".env.local": "SECRET=2",
    ".git/config": "[core]",
    "sub/.git/config": "[core]",
})
prof = RP.profile(static, scope_for(static))

check("inventory is repo-relative POSIX", all(not p.startswith("/") for p in prof["inventory"]))
check("inventory is sorted", prof["inventory"] == sorted(prof["inventory"]))
check("inventory carries no file contents",
      all(isinstance(p, str) and "\n" not in p for p in prof["inventory"]))
check("inventory excludes .env", ".env" not in prof["inventory"])
check("inventory excludes .env.local", ".env.local" not in prof["inventory"])
check("inventory excludes .git/ contents",
      not any(p.startswith(".git/") for p in prof["inventory"]))
check("inventory excludes nested .git/ contents",
      not any("/.git/" in p for p in prof["inventory"]))
check("inventory includes in-scope source", set(prof["inventory"]) == {"app.js", "index.html", "style.css"},
      f"(got {prof['inventory']})")
check("inventory established complete", prof["inventory_complete"] is True)
check("scope note limits the claim to the reviewed repo", "reviewed repository" in prof["scope_note"])

# --- applicability (D33) ---
check("dom_xss CHECKED when client-side files exist", status_of(prof, "dom_xss") == "CHECKED")
check("secrets_in_source NOT_CHECKED, not absent", status_of(prof, "secrets_in_source") == "NOT_CHECKED")
check("external_scripts NOT_CHECKED, not absent", status_of(prof, "external_scripts") == "NOT_CHECKED")
check("code_injection NOT_CHECKED, not absent", status_of(prof, "code_injection") == "NOT_CHECKED")
check("event_handler_injection NOT_CHECKED", status_of(prof, "event_handler_injection") == "NOT_CHECKED")
check("sqli NOT_APPLICABLE earned on complete client-only inventory",
      status_of(prof, "sqli") == "NOT_APPLICABLE")
check("authn_authz stays UNKNOWN even when nothing suggests a backend",
      status_of(prof, "authn_authz") == "APPLICABILITY_UNKNOWN")
check("every category carries a reason or rule_ids",
      all(("reason" in c) or ("rule_ids" in c) for c in prof["categories"]))
check("no category uses a bare 'skipped' status",
      all(c["status"] in {"CHECKED","NOT_CHECKED","NOT_APPLICABLE","APPLICABILITY_UNKNOWN"}
          for c in prof["categories"]))

# --- a package manifest must defeat NOT_APPLICABLE ---
noded = make(os.path.join(tmp, "noded"), {"app.js": "x", "package.json": "{}"})
pn = RP.profile(noded, scope_for(noded))
check("package.json forces sqli to UNKNOWN (backend cannot be ruled out)",
      status_of(pn, "sqli") == "APPLICABILITY_UNKNOWN")
check("package.json forces ssrf to UNKNOWN", status_of(pn, "ssrf") == "APPLICABILITY_UNKNOWN")

served = make(os.path.join(tmp, "served"), {"app.js": "x", "server.py": "x"})
ps = RP.profile(served, scope_for(served))
check("server-side source forces sqli to UNKNOWN", status_of(ps, "sqli") == "APPLICABILITY_UNKNOWN")

sqled = make(os.path.join(tmp, "sqled"), {"app.js": "x", "schema.sql": "x"})
check("a .sql artifact forces sqli to UNKNOWN",
      status_of(RP.profile(sqled, scope_for(sqled)), "sqli") == "APPLICABILITY_UNKNOWN")

# --- no client-side code at all ---
empty = make(os.path.join(tmp, "docsonly"), {"README.md": "hi"})
pe = RP.profile(empty, scope_for(empty))
check("dom_xss NOT_APPLICABLE when no HTML/JS in a complete inventory",
      status_of(pe, "dom_xss") == "NOT_APPLICABLE")

# --- incompleteness must downgrade every NOT_APPLICABLE ---
import repo_profile
real_walk = repo_profile._walk
repo_profile._walk = lambda root, scope: (["app.js"], False, ["synthetic: could not enumerate"])
try:
    pi = repo_profile.profile(static, scope_for(static))
    check("incomplete inventory is recorded", pi["inventory_complete"] is False)
    check("incomplete inventory downgrades sqli to UNKNOWN",
          status_of(pi, "sqli") == "APPLICABILITY_UNKNOWN")
    check("incomplete inventory never yields NOT_APPLICABLE anywhere",
          all(c["status"] != "NOT_APPLICABLE" for c in pi["categories"]))
    check("incompleteness reason is stated",
          any("complete" in c.get("reason", "") for c in pi["categories"]))
finally:
    repo_profile._walk = real_walk

# --- symlink escaping the root is excluded and recorded, not silently dropped ---
linked = make(os.path.join(tmp, "linked"), {"app.js": "x"})
os.symlink("/etc/passwd", os.path.join(linked, "leak.txt"))
pl = RP.profile(linked, scope_for(linked))
check("symlink escaping the root is excluded from the inventory",
      "leak.txt" not in pl["inventory"])
check("symlink escape does NOT count as enumeration failure (policy exclusion)",
      pl["inventory_complete"] is True)
check("symlink escape is recorded in notes, never silently dropped",
      any("leak.txt" in n and "outside the repository" in n for n in pl["inventory_notes"]))
check("routine denied_subpaths exclusions are counted in notes",
      any("denied_subpaths" in n for n in RP.profile(static, scope_for(static))["inventory_notes"]))

# --- determinism ---
a = RP.profile(static, scope_for(static))
b = RP.profile(static, scope_for(static))
check("profile is byte-identical across runs",
      json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True))
check("profile contains no absolute host paths",
      "/tmp" not in json.dumps({k: v for k, v in a.items() if k != "inventory_notes"}))

# --- scannable set ---
check("scannable() selects only HTML/JS", set(RP.scannable(prof)) == {"app.js", "index.html"})
check("scannable() is deterministic", RP.scannable(prof) == sorted(RP.scannable(prof)))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
