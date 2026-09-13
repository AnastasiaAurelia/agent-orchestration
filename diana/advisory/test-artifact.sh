#!/usr/bin/env bash
# CP4: ADVISORY_SECURITY_REVIEW v1 -- schema, outcome, digest integrity (AC-9),
# and structural incompatibility with Security Track evidence (AC-10).
set -euo pipefail

ADV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

python3 - "$ADV_DIR" "$TMP_DIR" <<'PY'
import copy, json, os, sys
from pathlib import Path

adv_dir, tmp = sys.argv[1], sys.argv[2]
diana = Path(adv_dir).parent
for sub in ("advisory", "profile", "runtime", "security"):
    sys.path.insert(0, str(diana / sub))
import artifact as A, blocking, contract as C, dom_scan as DS, repo_profile as RP
import evidence_model as EM

passed = failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")

def rejects(label, fn, exc=A.ArtifactError):
    try:
        fn(); check(label, False, "(no error raised)")
    except exc:
        check(label, True)
    except Exception as e:
        check(label, False, f"(raised {type(e).__name__}: {e})")

def make(fid):
    root = os.path.join(adv_dir, "fixtures", "repo", fid)
    scope = {"allowed_roots": [root], "denied_subpaths": [".git/", ".env", ".env.*"]}
    prof = RP.profile(root, scope)
    k = C.build(task="Check security project ini", repo_root=root,
                git_commit="0" * 40, dirty=False, repo_profile=prof)
    return k, DS.scan(root, RP.scannable(prof)), prof

k, res, prof = make("fx01")
doc = A.build(k, res, prof["categories"])

# --- shape (D34) ---
check("document has exactly the 13 frozen keys", sorted(doc) == sorted(A.ARTIFACT_KEYS))
check("document_type is ADVISORY_SECURITY_REVIEW", doc["document_type"] == "ADVISORY_SECURITY_REVIEW")
check("schema_version is 1", doc["schema_version"] == 1)
for gone in ("checks_performed", "skipped", "evidence", "advisory_only"):
    check(f"deleted field absent: {gone}", gone not in doc)
check("contract embedded exactly once", isinstance(doc["contract"], dict))
check("repo_profile lives inside the contract, not duplicated",
      "repo_profile" in doc["contract"] and "repo_profile" not in doc)
check("evidence is a property of a finding, not a peer array",
      all({"source", "sink", "code_excerpt"} <= set(f) for f in doc["findings"]))

# --- AC-9: digest integrity ---
check("contract_digest recomputes from the embedded contract",
      doc["contract_digest"] == C.digest(doc["contract"]))
def _tamper_contract():
    bad = copy.deepcopy(doc); bad["contract"]["task"] = "something else"; A.validate(bad)
rejects("tampering with the embedded contract breaks the digest", _tamper_contract)
def _tamper_digest():
    bad = copy.deepcopy(doc); bad["contract_digest"] = "sha256:" + "0" * 64; A.validate(bad)
rejects("a wrong digest is rejected", _tamper_digest)
def _mismatched_run_id():
    bad = copy.deepcopy(doc); bad["run_id"] = "other"; A.validate(bad)
rejects("run_id disagreeing with the contract is rejected", _mismatched_run_id)

# --- AC-10: structural incompatibility with Security Track evidence (D35) ---
controls = EM.load_controls(str(diana / "security" / "catalog.json"))
results = EM.evaluate([doc], controls)
statuses = json.dumps(results)
check("Security Track classifies the advisory document as MALFORMED",
      "MALFORMED" in statuses, f"(got {statuses[:200]})")
check("the document names no control_id the Security Track could resolve",
      "control_id" not in doc)
def all_keys(node):
    """Every key at every depth -- a KEY test, not a substring test: the word
    'applicability' legitimately appears inside coverage prose."""
    out = set()
    if isinstance(node, dict):
        for k, v in node.items():
            out.add(k); out |= all_keys(v)
    elif isinstance(node, list):
        for v in node:
            out |= all_keys(v)
    return out

doc_keys = all_keys(doc)
for field in sorted(A.SECURITY_TRACK_FIELDS):
    check(f"Security Track field absent as a key at any depth: {field}",
          field not in doc_keys)
check("_forbidden_keys finds nothing in a valid document", A._forbidden_keys(doc) == [])
check("the mirrored field set matches evidence_model.ALLOWED_RUN_FIELDS exactly",
      A.SECURITY_TRACK_FIELDS == frozenset(EM.ALLOWED_RUN_FIELDS))
for field in sorted(A.SECURITY_TRACK_FIELDS):
    def _inject(f=field):
        bad = copy.deepcopy(doc); bad["findings"][0][f] = "x"; A.validate(bad)
    rejects(f"injecting {field} into a finding is refused", _inject)
def _inject_deep():
    bad = copy.deepcopy(doc)
    bad["contract"]["repo_profile"]["categories"][0]["applicability"] = "APPLIES"
    A.validate(bad)
rejects("a Security Track field smuggled deep in the contract is refused", _inject_deep)

# --- outcome semantics (D36) ---
check("fx01 is COMPLETE", doc["outcome"] == "COMPLETE")
k8, res8, prof8 = make("fx08")
doc8 = A.build(k8, res8, prof8["categories"])
check("fx08 is INCOMPLETE because a concrete construct could not be analyzed",
      doc8["outcome"] == "INCOMPLETE")
check("fx08 records exactly one unsupported construct", len(doc8["unsupported_constructs"]) == 1)
check("derive_outcome: empty arrays => COMPLETE", A.derive_outcome([], []) == "COMPLETE")
check("derive_outcome: a scan issue => INCOMPLETE", A.derive_outcome([{"kind": "READ_FAILED"}], []) == "INCOMPLETE")
check("derive_outcome: an unsupported construct => INCOMPLETE", A.derive_outcome([], [{"x": 1}]) == "INCOMPLETE")
def _lying_outcome():
    bad = copy.deepcopy(doc8); bad["outcome"] = "COMPLETE"; A.validate(bad)
rejects("an outcome disagreeing with its own arrays is refused", _lying_outcome)
def _blocked_outcome():
    bad = copy.deepcopy(doc); bad["outcome"] = "BLOCKED"; A.validate(bad)
rejects("BLOCKED is never a valid document outcome", _blocked_outcome)

# --- unverified_observations (D1, D14) ---
obs = [{"note": "the hash fragment is rendered without escaping", "file": "app.js"}]
d2 = A.build(k, res, prof["categories"], obs)
check("observations are carried verbatim", d2["unverified_observations"] == obs)
check("observations carry no severity field",
      all("severity" not in o for o in d2["unverified_observations"]))
def blocks(label, fn):
    try:
        fn(); check(label, False, "(no Blocked raised)")
    except blocking.Blocked as b:
        check(label, b.code == blocking.HERMES_OUTPUT_SCHEMA_VIOLATION, f"(got {b.code})")
blocks("an observation carrying severity is REFUSED, not downgraded",
       lambda: A.build(k, res, prof["categories"], [{"note": "x", "severity": "HIGH"}]))
blocks("an observation proposing risk is refused (no proposal channel)",
       lambda: A.build(k, res, prof["categories"], [{"note": "x", "risk": "SAFE"}]))
blocks("an observation proposing depth is refused",
       lambda: A.build(k, res, prof["categories"], [{"note": "x", "depth": "D0"}]))
blocks("an unknown observation field fails validation rather than being recorded",
       lambda: A.build(k, res, prof["categories"], [{"note": "x", "extra": 1}]))
blocks("an observation with no note is refused",
       lambda: A.build(k, res, prof["categories"], [{"file": "app.js"}]))
check("a rejected observation never becomes an anomaly side channel",
      "anomal" not in json.dumps(doc).lower())

# --- finding schema ---
def _bad_flow():
    bad = copy.deepcopy(doc); bad["findings"][0]["flow"] = "INDIRECT"; A.validate(bad)
rejects("M1 refuses to emit a non-DIRECT flow", _bad_flow)
def _bad_sev():
    bad = copy.deepcopy(doc); bad["findings"][0]["severity"] = "INFO"; A.validate(bad)
rejects("an out-of-range severity is refused", _bad_sev)
def _bad_line():
    bad = copy.deepcopy(doc); bad["findings"][0]["line"] = 0; A.validate(bad)
rejects("a non 1-indexed line is refused", _bad_line)
def _bad_coverage():
    bad = copy.deepcopy(doc); bad["coverage"][0]["status"] = "skipped"; A.validate(bad)
rejects("a bare 'skipped' coverage status is refused", _bad_coverage)
def _reasonless_coverage():
    bad = copy.deepcopy(doc)
    bad["coverage"] = [{"category": "x", "status": "NOT_CHECKED"}]
    A.validate(bad)
rejects("a coverage entry with neither reason nor rule_ids is refused", _reasonless_coverage)
def _bad_issue_kind():
    bad = copy.deepcopy(doc8)
    bad["scan_issues"] = [{"file": "a.js", "kind": "WHATEVER"}]
    A.validate(bad)
rejects("an unknown scan_issues kind is refused", _bad_issue_kind)

# --- coverage carries both directions (D32) ---
statuses_seen = {c["status"] for c in doc["coverage"]}
check("coverage reports CHECKED categories", "CHECKED" in statuses_seen)
check("coverage reports NOT_CHECKED categories, never silent absence",
      "NOT_CHECKED" in statuses_seen)
check("coverage distinguishes NOT_APPLICABLE from APPLICABILITY_UNKNOWN",
      {"NOT_APPLICABLE", "APPLICABILITY_UNKNOWN"} <= statuses_seen)
check("the report explains what was checked, skipped, why, and its limits",
      bool(doc["coverage"]) and bool(doc["limitations"]) and
      all("reason" in c or "rule_ids" in c for c in doc["coverage"]))

# --- persistence lives outside the target repository (D9) ---
run_dir = os.path.join(tmp, "runs", doc["run_id"])
path = A.persist(doc, run_dir)
check("artifact written into the Diana run directory", Path(path).is_file())
check("artifact is NOT written inside the target repository",
      not str(path).startswith(doc["contract"]["target"]["repo_root"]))
check("persisted document round-trips and revalidates",
      A.validate(json.loads(Path(path).read_text())) is None)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
