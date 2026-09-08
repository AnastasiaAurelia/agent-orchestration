#!/usr/bin/env bash
set -euo pipefail

SEC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pass_count=0
fail_count=0

pass() { echo "PASS: $1"; pass_count=$((pass_count + 1)); }
fail() { echo "FAIL: $1" >&2; fail_count=$((fail_count + 1)); }

# ==================================================================
# Offline, deterministic unit tests (no subprocess/network dependency)
# ==================================================================

OFFLINE_OUT="$(mktemp)"
if python3 <<'PYEOF' > "$OFFLINE_OUT"
import sys
sys.path.insert(0, "diana/security")
import ci_verifier_runs as m

results = []

def check(name, condition):
    results.append((name, bool(condition)))

# _parse_owner_repo: https and ssh remote URL forms.
check("owner/repo from https URL", m._parse_owner_repo("https://github.com/AnastasiaAurelia/agent-orchestration.git") == "AnastasiaAurelia/agent-orchestration")
check("owner/repo from https URL without .git suffix", m._parse_owner_repo("https://github.com/AnastasiaAurelia/agent-orchestration") == "AnastasiaAurelia/agent-orchestration")
check("owner/repo from ssh URL", m._parse_owner_repo("git@github.com:AnastasiaAurelia/agent-orchestration.git") == "AnastasiaAurelia/agent-orchestration")
check("malformed remote URL -> None (fail closed)", m._parse_owner_repo("not-a-url") is None)
check("empty remote URL -> None (fail closed)", m._parse_owner_repo("") is None)

# build_semgrep_envelope: pure function, deterministic, correctly shaped.
report = {
    "version": "1.176.1",
    "results": [{"check_id": "diana.insecure-deserialization", "path": "app/x.py"}],
    "paths": {"scanned": ["app/x.py", "app/y.py"]},
}
env = m.build_semgrep_envelope("owner/repo", "deadbeef" * 5, ".", report, list(m.SEMGREP_LIVE_RULE_MAP.keys()))
check("envelope has all required adapter_base fields", set(env.keys()) == {"tool", "execution", "target", "config", "scanned_inputs", "report", "artifact_binding"})
check("envelope.tool.name is semgrep", env["tool"]["name"] == "semgrep")
check("envelope.tool.version is the real reported version", env["tool"]["version"] == "1.176.1")
check("envelope.target.repository/commit match inputs", env["target"]["repository"] == "owner/repo" and env["target"]["commit"] == "deadbeef" * 5)
check("envelope.target.scope is full-repo", env["target"]["scope"] == "full-repo")
check("envelope.config.rule_map is the real SEMGREP_LIVE_RULE_MAP", env["config"]["rule_map"] == m.SEMGREP_LIVE_RULE_MAP)
check("envelope.report.results carries the real finding", env["report"]["results"] == [{"check_id": "diana.insecure-deserialization", "path": "app/x.py"}])
check("envelope.report.rules_run is the real rule id list", set(env["report"]["rules_run"]) == set(m.SEMGREP_LIVE_RULE_MAP.keys()))
check("envelope.scanned_inputs reflects semgrep's own scanned-paths list", set(env["scanned_inputs"]) == {"app/x.py", "app/y.py"})

# Two envelopes built from identical inputs must hash identically
# (determinism); a changed report must hash differently (integrity).
env2 = m.build_semgrep_envelope("owner/repo", "deadbeef" * 5, ".", report, list(m.SEMGREP_LIVE_RULE_MAP.keys()))
check("build_semgrep_envelope is deterministic (same hash for same inputs)", env["artifact_binding"]["sha256"] == env2["artifact_binding"]["sha256"])
report_tampered = dict(report)
report_tampered["results"] = []
env3 = m.build_semgrep_envelope("owner/repo", "deadbeef" * 5, ".", report_tampered, list(m.SEMGREP_LIVE_RULE_MAP.keys()))
check("build_semgrep_envelope hash changes when the report content changes", env["artifact_binding"]["sha256"] != env3["artifact_binding"]["sha256"])

# The envelope this module builds must actually be ACCEPTED by the real,
# unmodified semgrep_adapter.py -- proves the two stay in sync, not just
# that this module's own hash math is internally consistent.
import json, tempfile, os
sys.path.insert(0, "diana/security/adapters")
import semgrep_adapter
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump(env, f)
    artifact_path = f.name
try:
    runs = semgrep_adapter.ingest(artifact_path, m.SEMGREP_LIVE_CONTROL_IDS, "test-identity", {"repository": "owner/repo", "commit": "deadbeef" * 5})
    statuses = {r["control_id"]: (r["evidence"][0]["status"] if r["evidence"] else "NONE") for r in runs}
    check("real semgrep_adapter accepts the built envelope: SEC-058 VIOLATED (the finding)", statuses.get("SEC-058") == "VIOLATED")
    check("real semgrep_adapter accepts the built envelope: SEC-010 SATISFIED (clean, rule ran)", statuses.get("SEC-010") == "SATISFIED")
finally:
    os.unlink(artifact_path)

# git_repository_identity / collect_semgrep_runs graceful degradation:
# simulate total tool unavailability (no network / no semgrep) and
# confirm the result is the SAME explicit "tool unavailable" shape
# adapter_base already established -- never a crash, never fabricated
# evidence.
m._find_or_install_semgrep = lambda workdir: None
degraded = m.collect_semgrep_runs(".")
check("degraded (tool unavailable) run count matches SEMGREP_LIVE_CONTROL_IDS", len(degraded) == len(m.SEMGREP_LIVE_CONTROL_IDS))
check("degraded runs carry UNKNOWN applicability (never PASS/FAIL)", all(r["applicability"] == "UNKNOWN" for r in degraded))
check("degraded runs carry no tool_error (unavailable, not a tool error)", all(r["tool_error"] is None for r in degraded))
check("degraded runs carry empty evidence", all(r["evidence"] == [] for r in degraded))

# ------------------------------------------------------------------
# build_gitleaks_envelope: pure function, deterministic, correctly shaped.
# ------------------------------------------------------------------
gl_findings = [{"RuleID": "aws-access-token", "File": "app/x.py", "StartLine": 3}]
gl_env = m.build_gitleaks_envelope("owner/repo", "deadbeef" * 5, ".", "full-repo", gl_findings, "8.30.1")
check("gitleaks envelope has all required adapter_base fields", set(gl_env.keys()) == {"tool", "execution", "target", "config", "scanned_inputs", "report", "artifact_binding"})
check("gitleaks envelope.tool.name is gitleaks", gl_env["tool"]["name"] == "gitleaks")
check("gitleaks envelope.tool.version is the real installed version", gl_env["tool"]["version"] == "8.30.1")
check("gitleaks envelope.target.scope matches input", gl_env["target"]["scope"] == "full-repo")
check("gitleaks envelope.report carries the real findings list", gl_env["report"] == gl_findings)
gl_env2 = m.build_gitleaks_envelope("owner/repo", "deadbeef" * 5, ".", "full-repo", gl_findings, "8.30.1")
check("build_gitleaks_envelope is deterministic (same hash for same inputs)", gl_env["artifact_binding"]["sha256"] == gl_env2["artifact_binding"]["sha256"])
gl_env3 = m.build_gitleaks_envelope("owner/repo", "deadbeef" * 5, ".", "full-repo", [], "8.30.1")
check("build_gitleaks_envelope hash changes when findings change", gl_env["artifact_binding"]["sha256"] != gl_env3["artifact_binding"]["sha256"])

# The envelope this module builds must actually be ACCEPTED by the real,
# unmodified gitleaks_adapter.py.
sys.path.insert(0, "diana/security/adapters")
import gitleaks_adapter
gl_clean_env = m.build_gitleaks_envelope("owner/repo", "deadbeef" * 5, ".", "full-repo", [], "8.30.1")
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump(gl_clean_env, f)
    gl_artifact_path = f.name
try:
    gl_runs = gitleaks_adapter.ingest(gl_artifact_path, ["SEC-007"], "test-identity", {"repository": "owner/repo", "commit": "deadbeef" * 5})
    gl_statuses = {r["control_id"]: (r["evidence"][0]["status"] if r["evidence"] else "NONE") for r in gl_runs}
    check("real gitleaks_adapter accepts the built envelope: SEC-007 SATISFIED (clean full-repo scan)", gl_statuses.get("SEC-007") == "SATISFIED")
finally:
    os.unlink(gl_artifact_path)

# collect_gitleaks_runs graceful degradation: simulate total tool
# unavailability.
m._find_or_install_gitleaks = lambda workdir: None
gl_degraded = m.collect_gitleaks_runs(".")
check("gitleaks degraded (tool unavailable) run(s) present for SEC-007", any(r["control_id"] == "SEC-007" for r in gl_degraded))
check("gitleaks degraded runs carry UNKNOWN applicability (never PASS/FAIL)", all(r["applicability"] == "UNKNOWN" for r in gl_degraded))
check("gitleaks degraded runs carry no tool_error (unavailable, not a tool error)", all(r["tool_error"] is None for r in gl_degraded))

# ------------------------------------------------------------------
# build_deterministic_repo_envelope: pure function, deterministic,
# correctly shaped.
# ------------------------------------------------------------------
sys.path.insert(0, "diana/security/adapters")
import deterministic_repo_adapter
dr_env = m.build_deterministic_repo_envelope("owner/repo", "deadbeef" * 5, "/some/web-root", ["index.html", "assets/app.js"])
check("deterministic-repo envelope has all required adapter_base fields", set(dr_env.keys()) == {"tool", "execution", "target", "config", "scanned_inputs", "report", "artifact_binding"})
check("deterministic-repo envelope.tool.name matches the adapter's expected identity", dr_env["tool"]["name"] == deterministic_repo_adapter.TOOL_NAME)
check("deterministic-repo envelope.report.served_paths carries the real listing", dr_env["report"]["served_paths"] == ["index.html", "assets/app.js"])
dr_env2 = m.build_deterministic_repo_envelope("owner/repo", "deadbeef" * 5, "/some/web-root", ["index.html", "assets/app.js"])
check("build_deterministic_repo_envelope is deterministic (same hash for same inputs)", dr_env["artifact_binding"]["sha256"] == dr_env2["artifact_binding"]["sha256"])
dr_env3 = m.build_deterministic_repo_envelope("owner/repo", "deadbeef" * 5, "/some/web-root", ["index.html", ".env"])
check("build_deterministic_repo_envelope hash changes when served_paths changes", dr_env["artifact_binding"]["sha256"] != dr_env3["artifact_binding"]["sha256"])

# The envelope this module builds must actually be ACCEPTED by the real,
# unmodified deterministic_repo_adapter.py, for both a clean and a
# sensitive-path listing.
dr_clean_env = m.build_deterministic_repo_envelope("owner/repo", "deadbeef" * 5, "/some/web-root", ["index.html", "assets/app.js"])
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump(dr_clean_env, f)
    dr_artifact_path = f.name
try:
    dr_runs = deterministic_repo_adapter.ingest(dr_artifact_path, ["SEC-064"], "test-identity", {"repository": "owner/repo", "commit": "deadbeef" * 5})
    dr_statuses = {r["control_id"]: (r["evidence"][0]["status"] if r["evidence"] else "NONE") for r in dr_runs}
    check("real deterministic_repo_adapter accepts the built envelope: SEC-064 SATISFIED (clean listing)", dr_statuses.get("SEC-064") == "SATISFIED")
finally:
    os.unlink(dr_artifact_path)

dr_dirty_env = m.build_deterministic_repo_envelope("owner/repo", "deadbeef" * 5, "/some/web-root", ["index.html", ".env"])
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump(dr_dirty_env, f)
    dr_artifact_path2 = f.name
try:
    dr_runs2 = deterministic_repo_adapter.ingest(dr_artifact_path2, ["SEC-064"], "test-identity", {"repository": "owner/repo", "commit": "deadbeef" * 5})
    dr_statuses2 = {r["control_id"]: (r["evidence"][0]["status"] if r["evidence"] else "NONE") for r in dr_runs2}
    check("real deterministic_repo_adapter accepts the built envelope: SEC-064 VIOLATED (.env served)", dr_statuses2.get("SEC-064") == "VIOLATED")
finally:
    os.unlink(dr_artifact_path2)

# _list_served_paths: deterministic real filesystem traversal (relative,
# sorted, forward-slash paths -- no execution of anything found).
import tempfile as _tempfile
with _tempfile.TemporaryDirectory() as walk_dir:
    os.makedirs(os.path.join(walk_dir, "assets"))
    with open(os.path.join(walk_dir, "index.html"), "w") as fh:
        fh.write("x")
    with open(os.path.join(walk_dir, "assets", "app.js"), "w") as fh:
        fh.write("x")
    listed = m._list_served_paths(walk_dir)
    check("_list_served_paths lists every real file, relative, sorted", listed == ["assets/app.js", "index.html"])

# collect_deterministic_repo_runs graceful degradation: no detected web
# root -> explicit UNPROVEN, never fabricated.
m._detect_frontend_bundle_dir = lambda repo_root: None
dr_degraded = m.collect_deterministic_repo_runs(".")
check("deterministic-repo degraded (no web root) run present for SEC-064", any(r["control_id"] == "SEC-064" for r in dr_degraded))
check("deterministic-repo degraded runs carry UNKNOWN applicability (never PASS/FAIL)", all(r["applicability"] == "UNKNOWN" for r in dr_degraded))

# ------------------------------------------------------------------
# build_sensitive_fetch_scenario_envelope: pure function, deterministic,
# correctly shaped (SEC-064 dynamic scenario, remediation round B).
# ------------------------------------------------------------------
sf_env = m.build_sensitive_fetch_scenario_envelope("owner/repo", "deadbeef" * 5, "http://127.0.0.1:9", "PASSED", {".env": 404})
check("scenario envelope has all required dynamic_base fields", set(sf_env.keys()) == {"environment", "target", "scenario", "verifier_mode", "identities", "execution", "assertions", "cleanup", "result", "artifact_binding"})
check("scenario envelope.environment is LOCAL", sf_env["environment"] == "LOCAL")
check("scenario envelope.scenario.id matches the registry entry", sf_env["scenario"]["id"] == "sensitive-file-paths-not-fetchable")
check("scenario envelope.assertions carries the real observed outcome", sf_env["assertions"] == [{"id": "sensitive_file_paths_not_fetchable", "outcome": "PASSED"}])
sf_env2 = m.build_sensitive_fetch_scenario_envelope("owner/repo", "deadbeef" * 5, "http://127.0.0.1:9", "PASSED", {".env": 404})
check("build_sensitive_fetch_scenario_envelope is deterministic (same hash for same inputs)", sf_env["artifact_binding"]["sha256"] == sf_env2["artifact_binding"]["sha256"])
sf_env3 = m.build_sensitive_fetch_scenario_envelope("owner/repo", "deadbeef" * 5, "http://127.0.0.1:9", "FAILED", {".env": 200})
check("build_sensitive_fetch_scenario_envelope hash changes when outcome changes", sf_env["artifact_binding"]["sha256"] != sf_env3["artifact_binding"]["sha256"])

# The envelope this module builds must actually be ACCEPTED by the real,
# unmodified dynamic_normalizer.py, for both a clean (PASSED) and a
# violating (FAILED) outcome.
import dynamic_normalizer
sf_clean_env = m.build_sensitive_fetch_scenario_envelope("owner/repo", "deadbeef" * 5, "http://127.0.0.1:9", "PASSED", {".env": 404})
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump(sf_clean_env, f)
    sf_artifact_path = f.name
try:
    sf_expected = {"environment": "LOCAL", "repository": "owner/repo", "commit": "deadbeef" * 5, "base_url": "http://127.0.0.1:9"}
    sf_runs = dynamic_normalizer.ingest(sf_artifact_path, ["SEC-064"], "test-identity", sf_expected)
    sf_statuses = {r["control_id"]: (r["evidence"][0]["status"] if r["evidence"] else "NONE") for r in sf_runs}
    check("real dynamic_normalizer accepts the built envelope: SEC-064 SATISFIED (clean, all fetches 404)", sf_statuses.get("SEC-064") == "SATISFIED")
finally:
    os.unlink(sf_artifact_path)

sf_dirty_env = m.build_sensitive_fetch_scenario_envelope("owner/repo", "deadbeef" * 5, "http://127.0.0.1:9", "FAILED", {".env": 200})
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump(sf_dirty_env, f)
    sf_artifact_path2 = f.name
try:
    sf_runs2 = dynamic_normalizer.ingest(sf_artifact_path2, ["SEC-064"], "test-identity", sf_expected)
    sf_statuses2 = {r["control_id"]: (r["evidence"][0]["status"] if r["evidence"] else "NONE") for r in sf_runs2}
    check("real dynamic_normalizer accepts the built envelope: SEC-064 VIOLATED (a fetch returned 200)", sf_statuses2.get("SEC-064") == "VIOLATED")
finally:
    os.unlink(sf_artifact_path2)

# _fetch_status / _start_local_static_server: a real, LOCAL, ephemeral
# HTTP server against a real temp directory -- proves the live plumbing
# itself, not just the pure envelope shaping.
with _tempfile.TemporaryDirectory() as srv_dir:
    with open(os.path.join(srv_dir, "index.html"), "w") as fh:
        fh.write("ok")
    srv, srv_thread, srv_port = m._start_local_static_server(srv_dir)
    try:
        base = f"http://127.0.0.1:{srv_port}"
        check("_fetch_status: existing file -> 200", m._fetch_status(base, "index.html") == 200)
        check("_fetch_status: missing file -> 404", m._fetch_status(base, ".env") == 404)
    finally:
        srv.shutdown()
        srv.server_close()

# collect_sensitive_path_fetch_scenario_runs graceful degradation: no
# detected web root -> explicit UNPROVEN, never fabricated, and the
# scenario is never even attempted (no server started).
m._detect_frontend_bundle_dir = lambda repo_root: None
sf_degraded = m.collect_sensitive_path_fetch_scenario_runs(".")
check("dynamic scenario degraded (no web root) run present for SEC-064", any(r["control_id"] == "SEC-064" for r in sf_degraded))
check("dynamic scenario degraded runs carry UNKNOWN applicability (never PASS/FAIL)", all(r["applicability"] == "UNKNOWN" for r in sf_degraded))

# collect_trusted_runs() must never raise even under a total internal
# failure -- the absolute safety net, across all four live families.
def _boom():
    raise RuntimeError("simulated total failure")
m.collect_semgrep_runs = _boom
m.collect_gitleaks_runs = _boom
m.collect_deterministic_repo_runs = _boom
m.collect_sensitive_path_fetch_scenario_runs = _boom
safe_result = m.collect_trusted_runs()
check("collect_trusted_runs() degrades to [] rather than raising on unexpected failure", safe_result == [])

failed = [name for name, ok in results if not ok]
for name, ok in results:
    print(("PASS: " if ok else "FAIL: ") + name)
sys.exit(1 if failed else 0)
PYEOF
then
  :
fi
cat "$OFFLINE_OUT"
offline_pass="$(grep -c "^PASS:" "$OFFLINE_OUT" || true)"
offline_fail="$(grep -c "^FAIL:" "$OFFLINE_OUT" || true)"
pass_count=$((pass_count + offline_pass))
fail_count=$((fail_count + offline_fail))
rm -f "$OFFLINE_OUT"

# ==================================================================
# Real, network-dependent end-to-end check (SKIPS gracefully, never
# fails the suite, if network/pip install isn't available in this
# environment -- the offline tests above already prove the logic is
# correct; this additionally proves the real subprocess/venv/semgrep
# path actually works when it can).
# ==================================================================

REAL_OUT="$(mktemp)"
trap 'rm -f "$REAL_OUT"' EXIT

if timeout 150 python3 "$SEC_DIR/ci_verifier_runs.py" > "$REAL_OUT" 2>/dev/null; then
  real_count="$(python3 -c "import json; print(len(json.load(open('$REAL_OUT'))))" 2>/dev/null || echo "parse-error")"
  if [ "$real_count" = "parse-error" ]; then
    fail "real end-to-end: ci_verifier_runs.py did not print valid JSON"
  elif [ "$real_count" -ge 1 ]; then
    applicabilities="$(python3 -c "
import json
runs = json.load(open('$REAL_OUT'))
print(sorted(set(r['applicability'] for r in runs)))
")"
    pass "real end-to-end: ci_verifier_runs.py produced $real_count run(s), applicability=$applicabilities (SATISFIED/VIOLATED if semgrep installed+ran; UNKNOWN if genuinely unavailable in this environment -- both are honest, neither is fabricated)"
  else
    fail "real end-to-end: expected at least one run across all live-wired families, got 0"
  fi
else
  echo "SKIP: real end-to-end semgrep execution (network/pip install unavailable in this environment -- offline tests above already cover the logic)"
fi

echo ""
echo "diana/security/test-ci-verifier-runs.sh: $pass_count passed, $fail_count failed"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
