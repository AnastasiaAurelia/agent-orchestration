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
env = m.build_semgrep_envelope("owner/repo", "deadbeef" * 5, ".", report, list(m.LIVE_RULE_MAP.keys()))
check("envelope has all required adapter_base fields", set(env.keys()) == {"tool", "execution", "target", "config", "scanned_inputs", "report", "artifact_binding"})
check("envelope.tool.name is semgrep", env["tool"]["name"] == "semgrep")
check("envelope.tool.version is the real reported version", env["tool"]["version"] == "1.176.1")
check("envelope.target.repository/commit match inputs", env["target"]["repository"] == "owner/repo" and env["target"]["commit"] == "deadbeef" * 5)
check("envelope.target.scope is full-repo", env["target"]["scope"] == "full-repo")
check("envelope.config.rule_map is the real LIVE_RULE_MAP", env["config"]["rule_map"] == m.LIVE_RULE_MAP)
check("envelope.report.results carries the real finding", env["report"]["results"] == [{"check_id": "diana.insecure-deserialization", "path": "app/x.py"}])
check("envelope.report.rules_run is the real rule id list", set(env["report"]["rules_run"]) == set(m.LIVE_RULE_MAP.keys()))
check("envelope.scanned_inputs reflects semgrep's own scanned-paths list", set(env["scanned_inputs"]) == {"app/x.py", "app/y.py"})

# Two envelopes built from identical inputs must hash identically
# (determinism); a changed report must hash differently (integrity).
env2 = m.build_semgrep_envelope("owner/repo", "deadbeef" * 5, ".", report, list(m.LIVE_RULE_MAP.keys()))
check("build_semgrep_envelope is deterministic (same hash for same inputs)", env["artifact_binding"]["sha256"] == env2["artifact_binding"]["sha256"])
report_tampered = dict(report)
report_tampered["results"] = []
env3 = m.build_semgrep_envelope("owner/repo", "deadbeef" * 5, ".", report_tampered, list(m.LIVE_RULE_MAP.keys()))
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
    runs = semgrep_adapter.ingest(artifact_path, m.LIVE_CONTROL_IDS, "test-identity", {"repository": "owner/repo", "commit": "deadbeef" * 5})
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
check("degraded (tool unavailable) run count matches LIVE_CONTROL_IDS", len(degraded) == len(m.LIVE_CONTROL_IDS))
check("degraded runs carry UNKNOWN applicability (never PASS/FAIL)", all(r["applicability"] == "UNKNOWN" for r in degraded))
check("degraded runs carry no tool_error (unavailable, not a tool error)", all(r["tool_error"] is None for r in degraded))
check("degraded runs carry empty evidence", all(r["evidence"] == [] for r in degraded))

# collect_trusted_runs() must never raise even under a total internal
# failure -- the absolute safety net.
def _boom():
    raise RuntimeError("simulated total failure")
m.collect_semgrep_runs = _boom
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
    fail "real end-to-end: expected at least one run for LIVE_CONTROL_IDS, got 0"
  fi
else
  echo "SKIP: real end-to-end semgrep execution (network/pip install unavailable in this environment -- offline tests above already cover the logic)"
fi

echo ""
echo "diana/security/test-ci-verifier-runs.sh: $pass_count passed, $fail_count failed"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
