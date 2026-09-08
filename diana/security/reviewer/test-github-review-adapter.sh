#!/usr/bin/env bash
set -euo pipefail

# Security Track remediation round C, Priority 3: tests for
# github_review_adapter.py -- the GitHub-backed human-review normalizer,
# built as a capability-only module (not yet live-wired into
# ci_verifier_runs.py; see the module's own docstring for the exact,
# verified permission blocker).

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SEC_DIR="$REPO_ROOT/diana/security"

OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT

if python3 <<'PYEOF' > "$OUT"
import sys, json, hashlib, tempfile, os
sys.path.insert(0, "diana/security/reviewer")
sys.path.insert(0, "diana/security/adapters")
import github_review_adapter as gra
import evidence_model

results = []

def check(name, condition):
    results.append((name, bool(condition)))

catalog_controls = gra._load_catalog_controls("diana/security/catalog.json")
REPO = "owner/repo"
COMMIT = "deadbeef" * 5
EXPECTED = {"repository": REPO, "commit": COMMIT}


def build_envelope(**overrides):
    control_id = overrides.pop("control_id", "SEC-016")
    req_index = overrides.pop("req_index", 0)
    requirement = overrides.pop("requirement", catalog_controls[control_id]["required_evidence"][req_index])
    env = {
        "reviewer": overrides.pop("reviewer", {"login": "a-codeowner", "review_id": 111}),
        "target": overrides.pop("target", dict(EXPECTED)),
        "control_id": control_id,
        "requirement": requirement,
        "judgment": overrides.pop("judgment", "APPROVE"),
        "rationale": overrides.pop(
            "rationale",
            f"Reviewed {control_id} in diana/auth/hash.py -- confirmed the claim holds after inspection.",
        ),
        "independence": overrides.pop(
            "independence", {"reviewer_is_pr_author": False, "reviewer_is_diana_agent": False}
        ),
        "submitted_at": overrides.pop("submitted_at", "2026-09-08T12:00:00Z"),
    }
    assert not overrides, f"unused overrides: {overrides}"
    bound = {k: env[k] for k in gra.BOUND_FIELDS}
    canonical = json.dumps(bound, sort_keys=True, separators=(",", ":"))
    env["artifact_binding"] = {"sha256": hashlib.sha256(canonical.encode()).hexdigest()}
    return env


def write_artifact(env):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(env, f)
    f.close()
    return f.name


def run(env, control_ids=("SEC-016",), expected=None, identity="test"):
    path = write_artifact(env)
    try:
        return gra.ingest(catalog_controls, path, list(control_ids), identity, expected if expected is not None else EXPECTED)
    finally:
        os.unlink(path)


def status_of(runs, control_id="SEC-016"):
    for r in runs:
        if r["control_id"] == control_id:
            return r["evidence"][0]["status"] if r["evidence"] else "NONE"
    return "MISSING"


# --- structural validation ------------------------------------------------

try:
    gra.load_review_envelope({"reviewer": {}})
    check("missing fields -> ArtifactError", False)
except gra.ArtifactError:
    check("missing fields -> ArtifactError", True)

env_unknown = build_envelope()
env_unknown["extra_field"] = "nope"
try:
    gra.load_review_envelope(env_unknown)
    check("unknown field -> ArtifactError", False)
except gra.ArtifactError:
    check("unknown field -> ArtifactError", True)

try:
    gra.load_review_envelope({**build_envelope(), "reviewer": {"login": "", "review_id": 1}})
    check("empty reviewer login -> ArtifactError", False)
except gra.ArtifactError:
    check("empty reviewer login -> ArtifactError", True)

try:
    gra.load_review_envelope({**build_envelope(), "reviewer": {"login": "x", "review_id": -1}})
    check("negative review_id -> ArtifactError", False)
except gra.ArtifactError:
    check("negative review_id -> ArtifactError", True)

try:
    bad = build_envelope()
    bad["judgment"] = "TOTALLY_MADE_UP"
    gra.load_review_envelope(bad)
    check("unrecognized judgment string -> ArtifactError", False)
except gra.ArtifactError:
    check("unrecognized judgment string -> ArtifactError", True)

tampered = build_envelope()
tampered["rationale"] = tampered["rationale"] + " (tampered after hashing)"
try:
    gra.load_review_envelope(tampered)
    check("tampered rationale after hashing -> ArtifactError (binding mismatch)", False)
except gra.ArtifactError:
    check("tampered rationale after hashing -> ArtifactError (binding mismatch)", True)

# artifact_binding is deterministic for identical content
e1 = build_envelope()
e2 = build_envelope()
check("artifact_binding is deterministic for identical content", e1["artifact_binding"] == e2["artifact_binding"])

# --- core behavior: APPROVE / REQUEST_CHANGES / vague / non-independent / stale --

r = run(build_envelope(judgment="APPROVE"))
check("APPROVE + substantiated rationale -> SATISFIED", status_of(r) == "SATISFIED")

r = run(build_envelope(judgment="REQUEST_CHANGES", rationale="SEC-016 concern: found plain sha256 used for password storage in a legacy path."))
check("REQUEST_CHANGES + substantiated rationale -> VIOLATED", status_of(r) == "VIOLATED")

r = run(build_envelope(judgment="APPROVE", rationale="lgtm"))
check("vague rationale ('lgtm') -> no contribution (empty evidence)", status_of(r) == "NONE")

r = run(build_envelope(judgment="APPROVE", rationale="short"))
check("rationale below minimum length -> no contribution", status_of(r) == "NONE")

r = run(build_envelope(judgment="APPROVE", rationale="Reviewed thoroughly and everything looks correct after careful inspection today."))
check("rationale not naming the control_id -> no contribution", status_of(r) == "NONE")

r = run(build_envelope(judgment="APPROVE", independence={"reviewer_is_pr_author": True, "reviewer_is_diana_agent": False}))
check("reviewer_is_pr_author=true -> ERROR (never trusted)", r[0]["tool_error"] is not None and status_of(r) == "NONE")

r = run(build_envelope(judgment="APPROVE", independence={"reviewer_is_pr_author": False, "reviewer_is_diana_agent": True}))
check("reviewer_is_diana_agent=true -> ERROR (never trusted)", r[0]["tool_error"] is not None and status_of(r) == "NONE")

r = run(build_envelope(judgment="APPROVE"), expected={"repository": REPO, "commit": "cafebabe" * 5})
check("stale/mismatched commit + APPROVE -> no contribution (staleness handled by exact-commit binding)", status_of(r) == "NONE")

r = run(build_envelope(judgment="REQUEST_CHANGES", rationale="SEC-016 concern: something is wrong here."), expected={"repository": REPO, "commit": "cafebabe" * 5})
check("stale/mismatched commit + REQUEST_CHANGES -> no contribution (identity also required)", status_of(r) == "NONE")

r = run(build_envelope(judgment="COMMENTED"))
check("COMMENTED (not a terminal judgment) -> no contribution", status_of(r) == "NONE")

r = run(build_envelope(judgment="DISMISSED"))
check("DISMISSED (not a terminal judgment) -> no contribution", status_of(r) == "NONE")

# --- authorization / catalog integrity -------------------------------------

r = run(build_envelope(control_id="SEC-001", requirement="fabricated requirement text not in catalog"), control_ids=["SEC-001"])
check("requirement text not matching catalog -> ERROR", r[0]["tool_error"] is not None)

r = run(build_envelope(control_id="SEC-060", req_index=0), control_ids=["SEC-060"])
check("SEC-060 (DEPENDENCY_SCANNER only, no SEMANTIC_REVIEW/HUMAN) -> not authorized, empty result", r == [])

r = gra.ingest(catalog_controls, None, ["SEC-016"], "test", EXPECTED)
check("no artifact provided -> explicit tool_unavailable run (UNKNOWN applicability)", r[0]["applicability"] == "UNKNOWN" and r[0]["evidence"] == [])

authorized = gra._authorized_control_ids(catalog_controls)
check("authorized set is catalog-derived and non-trivial (>=30 controls)", len(authorized) >= 30)
check("SEC-060 correctly excluded from authorized set (DEPENDENCY_SCANNER only)", "SEC-060" not in authorized)
check("SEC-016 correctly included in authorized set", "SEC-016" in authorized)

# --- end-to-end proof: two independent contributions reach genuine PASS
# through evidence_model.py UNCHANGED (mechanism proof, not a claim about
# any real repository's current state -- see this file's own header and
# the round C final report for that distinction) -----------------------

control = catalog_controls["SEC-016"]
req0, req1 = control["required_evidence"]

env_a = build_envelope(
    control_id="SEC-016",
    requirement=req0,
    judgment="APPROVE",
    reviewer={"login": "reviewer-a", "review_id": 201},
    rationale="Reviewed SEC-016 in diana/auth/hash.py: bcrypt with cost factor 12 and a per-user salt via bcrypt's own salt generation.",
)
env_b = build_envelope(
    control_id="SEC-016",
    requirement=req1,
    judgment="APPROVE",
    reviewer={"login": "reviewer-b", "review_id": 202},
    rationale="Reviewed SEC-016 in diana/auth/hash.py: no md5/sha1/plain-sha256 or reversible-encryption path exists for password storage.",
)

path_a = write_artifact(env_a)
path_b = write_artifact(env_b)
try:
    runs_a = gra.ingest(catalog_controls, path_a, ["SEC-016"], "reviewer-a-run", EXPECTED)
    runs_b = gra.ingest(catalog_controls, path_b, ["SEC-016"], "reviewer-b-run", EXPECTED)
finally:
    os.unlink(path_a)
    os.unlink(path_b)

combined_runs = runs_a + runs_b
aggregate = evidence_model.evaluate(combined_runs, {"SEC-016": control})
sec016_result = next(r for r in aggregate if r["control_id"] == "SEC-016")
check(
    "two independent, substantiated APPROVE reviews (different reviewers, different requirement items) "
    "reach genuine PASS through evidence_model.py UNCHANGED",
    sec016_result["result"] == "PASS",
)
check("the genuine PASS carries both reviewers' evidence (full chain, not collapsed)", len(sec016_result["evidence"]) == 2)

# A single reviewer approving BOTH items should NOT be silently treated
# differently -- same mechanism, same reviewer, still reaches PASS (this
# module does not require distinct reviewers per item; that is a
# governance policy choice for whatever script/ruleset produces artifacts,
# not something this normalizer enforces).
env_c = build_envelope(control_id="SEC-016", requirement=req0, judgment="APPROVE", reviewer={"login": "reviewer-a", "review_id": 301})
env_d = build_envelope(control_id="SEC-016", requirement=req1, judgment="APPROVE", reviewer={"login": "reviewer-a", "review_id": 302})
path_c = write_artifact(env_c)
path_d = write_artifact(env_d)
try:
    runs_c = gra.ingest(catalog_controls, path_c, ["SEC-016"], "r", EXPECTED)
    runs_d = gra.ingest(catalog_controls, path_d, ["SEC-016"], "r", EXPECTED)
finally:
    os.unlink(path_c)
    os.unlink(path_d)
aggregate2 = evidence_model.evaluate(runs_c + runs_d, {"SEC-016": control})
sec016_result2 = next(r for r in aggregate2 if r["control_id"] == "SEC-016")
check("same reviewer approving both items also reaches genuine PASS", sec016_result2["result"] == "PASS")

# One VIOLATED item must keep the whole control from PASS even if the
# other item is cleanly SATISFIED.
env_e = build_envelope(control_id="SEC-016", requirement=req0, judgment="APPROVE", reviewer={"login": "reviewer-a", "review_id": 401})
env_f = build_envelope(control_id="SEC-016", requirement=req1, judgment="REQUEST_CHANGES", reviewer={"login": "reviewer-b", "review_id": 402}, rationale="SEC-016 concern: found a fast general-purpose hash used in one code path.")
path_e = write_artifact(env_e)
path_f = write_artifact(env_f)
try:
    runs_e = gra.ingest(catalog_controls, path_e, ["SEC-016"], "r", EXPECTED)
    runs_f = gra.ingest(catalog_controls, path_f, ["SEC-016"], "r", EXPECTED)
finally:
    os.unlink(path_e)
    os.unlink(path_f)
aggregate3 = evidence_model.evaluate(runs_e + runs_f, {"SEC-016": control})
sec016_result3 = next(r for r in aggregate3 if r["control_id"] == "SEC-016")
check("one VIOLATED item keeps the whole control from PASS (never averaged away)", sec016_result3["result"] == "FAIL")

failed = [name for name, ok in results if not ok]
for name, ok in results:
    print(("PASS: " if ok else "FAIL: ") + name)
sys.exit(1 if failed else 0)
PYEOF
then
  :
fi
cat "$OUT"
pass_count="$(grep -c '^PASS:' "$OUT" || true)"
fail_count="$(grep -c '^FAIL:' "$OUT" || true)"
echo ""
echo "diana/security/reviewer/test-github-review-adapter.sh: $pass_count passed, $fail_count failed"
if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
