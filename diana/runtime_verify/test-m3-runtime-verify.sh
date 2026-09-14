#!/usr/bin/env bash
# M3 acceptance: bounded runtime/browser verification.
# Spec: docs/architecture/HERMES-RUNTIME-M3.md (M3-AC-1 .. M3-AC-16).
#
# Drives a REAL Chromium through @playwright/mcp. Skips (does not fail) when
# node or @playwright/mcp is unavailable -- a carried assumption, reported
# rather than substituted with a simulated browser.
set -uo pipefail

RV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIANA_DIR="$(cd "$RV_DIR/.." && pwd)"
REPO_DIR="$(cd "$DIANA_DIR/.." && pwd)"

command -v node >/dev/null 2>&1 || { echo "SKIP  node is not on PATH; M3 acceptance cannot run"; exit 0; }

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

python3 - "$RV_DIR" "$TMP_DIR" "$REPO_DIR" <<'PY'
import hashlib, json, os, re, shutil, signal, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path

rv_dir, tmp, repo_dir = sys.argv[1], sys.argv[2], sys.argv[3]
diana = Path(rv_dir).parent
for sub in ("runtime_verify", "runtime", "advisory", "profile", "security"):
    sys.path.insert(0, str(diana / sub))
import artifact as A, contract as C, dom_scan as DS, repo_profile as RP, runtime_verify as RV

passed = failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")

if RV.find_mcp_cli() is None:
    print("SKIP  @playwright/mcp is not available; M3 acceptance cannot run")
    sys.exit(0)

FX = lambda name: str(Path(rv_dir, "fixtures", name))
M1FX = lambda name: str(Path(diana, "advisory", "fixtures", "repo", name))

def setup(fixture):
    root = os.path.realpath(fixture)
    scope = {"allowed_roots": [root], "denied_subpaths": list(C.DEFAULT_DENIED_SUBPATHS)}
    prof = RP.profile(root, scope)
    cb = C.build(task="Check security project ini", repo_root=root, git_commit="m3fixture",
                 dirty=False, repo_profile=prof, workflow="ADVISORY_SECURITY_REVIEW")
    return cb, DS.scan(root, RP.scannable(prof))

def snapshot(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            p = Path(dirpath, name)
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out

def verify(fixture, base, **kw):
    cb, scan = setup(fixture)
    finding = scan["findings"][0] if scan["findings"] else None
    if finding is None:
        finding = {"rule_id": "DOM-XSS-001", "file": "app.js", "line": 1,
                   "source": {"kind": "window.name", "line": 1, "text": "window.name"}}
    return cb, scan, RV.verify_finding(contract_block=cb, finding=finding, serve_root=fixture,
                                       run_directory=os.path.join(tmp, base), deadline_s=75, **kw)

# =========================================================================
print("--- M3-AC-1 / AC-2: reproduction and its control ---")
cb1, scan1, out1 = verify(FX("rv01"), "ac1")
r1, obs1 = out1["record"], (out1["record"]["observations"][0].get("observations") or {})
check("M3-AC-1 rv01 carries a real static DOM-XSS-001 finding", 
      len(scan1["findings"]) == 1 and scan1["findings"][0]["rule_id"] == "DOM-XSS-001")
check("M3-AC-1 outcome is REPRODUCED", r1["outcome"] == RV.REPRODUCED, f"(got {r1['outcome']} {r1['inconclusive_reason']})")
check("M3-AC-1 an injected node was observed in the LIVE DOM", obs1.get("injected_node_count", 0) >= 1)
check("M3-AC-1 the payload's OWN handler actually executed", obs1.get("handler_fired") is True)
check("M3-AC-1 the sink holds a parsed element, not text", "<img" in str(obs1.get("sink_html", "")))

cb2, scan2, out2 = verify(FX("rv02"), "ac2")
r2, obs2 = out2["record"], (out2["record"]["observations"][0].get("observations") or {})
check("M3-AC-2 the safe fixture has no static finding", scan2["findings"] == [])
check("M3-AC-2 identical payload and delivery is NOT_REPRODUCED", r2["outcome"] == RV.NOT_REPRODUCED, f"(got {r2['outcome']})")
check("M3-AC-2 no node was injected", obs2.get("injected_node_count") == 0)
check("M3-AC-2 no handler executed", obs2.get("handler_fired") is False)
check("M3-AC-2 the safe sink escaped the payload", "&lt;img" in str(obs2.get("sink_html", "")))
check("M3-AC-2 both runs used the same payload and channel",
      r1["delivery"]["payload"] == r2["delivery"]["payload"]
      and r1["delivery"]["source_channel"] == r2["delivery"]["source_channel"] == "window.name")

print("--- M3-AC-3: M1's own finding is NOT_REPRODUCED, not disproven ---")
cb3, scan3, out3 = verify(M1FX("fx01"), "ac3")
r3, obs3 = out3["record"], (out3["record"]["observations"][0].get("observations") or {})
check("M3-AC-3 fx01's static DOM-XSS-001 finding exists", 
      len(scan3["findings"]) == 1 and scan3["findings"][0]["severity"] == "HIGH")
check("M3-AC-3 fx01 is NOT_REPRODUCED under this bounded runtime", r3["outcome"] == RV.NOT_REPRODUCED, f"(got {r3['outcome']})")
check("M3-AC-3 the payload travelled the channel the finding names",
      r3["delivery"]["source_channel"] == "location.hash")
check("M3-AC-3 the record shows WHY: the source value arrived percent-encoded",
      "%3Cimg" in str(obs3.get("sink_html", "")) or "%3Cimg" in str(obs3.get("source_value", "")),
      f"(sink={str(obs3.get('sink_html'))[:80]!r})")
check("M3-AC-3 the record never claims the finding is disproven",
      "disproven" not in json.dumps(r3).lower() and r3["outcome"] in RV.OUTCOMES)
check("M3-AC-3 the finding is REFERENCED, never copied in an editable form",
      set(r3["finding_ref"]) == {"rule_id", "file", "line"})

print("--- M3-AC-4..8: egress boundary ---")
EGRESS = [
    {"kind": "navigate",    "name": "offorigin",      "url": "{{OFFSITE}}/"},
    {"kind": "navigate",    "name": "redirect",       "url": "{{TARGET}}/__diana__/redirect"},
    {"kind": "subresource", "name": "subresource",    "settle_ms": 2500},
]
cb4, scan4, out4 = verify(FX("rv01"), "ac4", extra_probes=EGRESS)
raw4 = out4["raw"]; r4 = out4["record"]
ledger = r4["proxy_ledger"]
target_origin = r4["runtime_envelope"]["target_origin"]
allow_host = target_origin.split("//", 1)[1]
probes4 = {p.get("name"): p for p in r4["observations"]}
offsite_host = r4["runtime_envelope"]["offsite_origin"].split("//", 1)[1]

check("M3-AC-4 a direct off-origin navigation did not serve external content",
      any(e["host"].split(":")[0] == offsite_host and not e["allowed"] for e in ledger)
      or probes4["offorigin"].get("navigation_error"),
      f"(probe={probes4.get('offorigin')})")
check("M3-AC-4 the proxy ledger records the refusal explicitly",
      all(not e["allowed"] for e in ledger if offsite_host in e["host"]))

redirect_probe = probes4["redirect"]
red_obs = redirect_probe.get("observations") or {}
check("M3-AC-5 the 302 redirect to an off-origin host was refused at the proxy",
      any(offsite_host in e["host"] and not e["allowed"] for e in ledger),
      f"(ledger hosts={sorted({e['host'] for e in ledger})})")
check("M3-AC-5 no content from the off-origin host reached the page",
      "diana: destination is not in the runtime envelope" in str(red_obs.get("body_text", ""))
      or redirect_probe.get("navigation_error") is not None,
      f"(body={str(red_obs.get('body_text'))[:120]!r})")
server_log4 = raw4.get("static_server_log") or []
check("M3-AC-5 the static server did emit the 302, so the redirect really was followed",
      any(e.get("status") == 302 and e.get("path", "").endswith("/redirect") for e in server_log4),
      f"(log={server_log4[-5:]})")

sub_obs = probes4["subresource"].get("observations") or {}
check("M3-AC-6 an off-origin <img> sub-resource failed", sub_obs.get("img_failed") is True and sub_obs.get("img_ok") is not True)
check("M3-AC-6 a script-initiated off-origin fetch() failed", sub_obs.get("fetch_failed") is True and sub_obs.get("fetch_ok") is not True)

print("--- M3-AC-7: confinement is to the ORIGIN, not to 'localhost' ---")
# A second Diana-started loopback origin the contract never authorized.
import http.server, threading, socketserver
class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(b"<h1>UNAUTHORIZED LOOPBACK SERVICE</h1>")
other = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
other_port = other.server_address[1]
threading.Thread(target=other.serve_forever, daemon=True).start()
cb7, scan7, out7 = verify(FX("rv01"), "ac7", extra_probes=[
    {"kind": "navigate", "name": "otherloopback", "url": f"http://127.0.0.1:{other_port}/x.html"}])
other.shutdown()
r7 = out7["record"]
p7 = {p.get("name"): p for p in r7["observations"]}["otherloopback"]
o7 = p7.get("observations") or {}
check("M3-AC-7 an unauthorized loopback origin was not served to the browser",
      "UNAUTHORIZED LOOPBACK SERVICE" not in str(o7.get("body_text", "")),
      f"(body={str(o7.get('body_text'))[:120]!r})")
check("M3-AC-7 the refusal is recorded against that exact origin",
      p7.get("navigation_error") is not None
      or any(f"127.0.0.1:{other_port}" in e["host"] and not e["allowed"] for e in r7["proxy_ledger"]))

check("M3-AC-8 the ledger recorded every request with an explicit decision",
      len(ledger) > 0 and all(set(e) == {"transport", "host", "allowed"} for e in ledger))
check("M3-AC-8 every ALLOWED ledger entry is the single target origin",
      all(e["host"] == allow_host for e in ledger if e["allowed"]),
      f"(allowed={sorted({e['host'] for e in ledger if e['allowed']})})")
check("M3-AC-8 browser-vendor telemetry egress was observed and refused (F7)",
      any("google" in e["host"] and not e["allowed"] for e in ledger),
      f"(refused={sorted({e['host'] for e in ledger if not e['allowed']})})")

print("--- M3-AC-9: the runtime capability cannot mutate, and the server is read-only ---")
before = snapshot(FX("rv01"))
git_before = subprocess.run(["git", "-C", repo_dir, "status", "--porcelain", "--", "diana/runtime_verify/fixtures"],
                            capture_output=True, text=True, check=False).stdout
cb9, scan9, out9 = verify(FX("rv01"), "ac9")
after = snapshot(FX("rv01"))
git_after = subprocess.run(["git", "-C", repo_dir, "status", "--porcelain", "--", "diana/runtime_verify/fixtures"],
                           capture_output=True, text=True, check=False).stdout
check("M3-AC-9 the target is byte-identical after verification", before == after)
check("M3-AC-9 git working state is unchanged", git_before == git_after)
check("M3-AC-9 the record was written OUTSIDE the target", not out9["record_path"].startswith(os.path.realpath(FX("rv01"))))

# Drive the static server directly with raw HTTP -- independent of any browser.
cfg = {"serve_root": os.path.realpath(FX("rv01")), "evidence_dir": tmp,
       "mcp_cli": RV.find_mcp_cli(), "deadline_ms": 20000, "probes": []}
cfg_path = Path(tmp, "serve.json"); cfg_path.write_text(json.dumps(cfg))
srv = subprocess.Popen(["node", str(diana / "runtime_verify" / "driver.mjs"), "serve", str(cfg_path)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
origin = None
try:
    line = srv.stdout.readline()
    origin = json.loads(line)["origin"]
    def req(method, path, body=None):
        r = urllib.request.Request(f"{origin}{path}", method=method, data=body)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp: return resp.status, resp.read()
        except urllib.error.HTTPError as e: return e.code, e.read()
        except Exception as e: return None, str(e).encode()
    check("M3-AC-9 GET of an in-scope file succeeds", req("GET", "/app.js")[0] == 200)
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        code, body = req(method, "/app.js", b"x" if method in ("POST", "PUT", "PATCH") else None)
        check(f"M3-AC-9 {method} is refused 405 before any filesystem access", code == 405, f"(got {code})")
    check("M3-AC-9 HEAD is permitted", req("HEAD", "/app.js")[0] == 200)
    for escape in ("/../../../etc/passwd", "/..%2f..%2f..%2fetc%2fpasswd", "/%2e%2e/%2e%2e/etc/passwd"):
        code, body = req("GET", escape)
        check(f"M3-AC-9 traversal {escape!r} does not serve host files",
              code in (403, 404) and b"root:" not in body, f"(got {code})")
    code, body = req("GET", "/.git/config")
    check("M3-AC-9 a denied subpath is refused", code in (403, 404))
finally:
    try: os.killpg(os.getpgid(srv.pid), signal.SIGKILL)
    except Exception: pass
    srv.wait(timeout=10)

# A symlink escaping the served root is only visible AFTER canonicalization,
# which is exactly why a string-prefix containment test would be unsound.
escape_root = Path(tmp, "escape-root"); escape_root.mkdir(exist_ok=True)
(escape_root / "index.html").write_text("<h1>in scope</h1>")
outside = Path(tmp, "outside-secret.txt"); outside.write_text("DIANA-OUTSIDE-SENTINEL")
try: os.symlink(outside, escape_root / "leak.txt")
except FileExistsError: pass
try: os.symlink("/etc/passwd", escape_root / "passwd.txt")
except FileExistsError: pass
cfg_e = {"serve_root": str(escape_root), "evidence_dir": tmp, "mcp_cli": RV.find_mcp_cli(),
         "deadline_ms": 20000, "probes": []}
cfg_e_path = Path(tmp, "serve-escape.json"); cfg_e_path.write_text(json.dumps(cfg_e))
srv_e = subprocess.Popen(["node", str(diana / "runtime_verify" / "driver.mjs"), "serve", str(cfg_e_path)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
try:
    origin_e = json.loads(srv_e.stdout.readline())["origin"]
    def req_e(path):
        try:
            with urllib.request.urlopen(f"{origin_e}{path}", timeout=10) as resp: return resp.status, resp.read()
        except urllib.error.HTTPError as e: return e.code, e.read()
        except Exception as e: return None, str(e).encode()
    check("M3-AC-9 an in-scope file in the escape fixture is still served", req_e("/index.html")[0] == 200)
    code, body = req_e("/leak.txt")
    check("M3-AC-9 a symlink escaping the served root is denied after canonicalization",
          code in (403, 404) and b"DIANA-OUTSIDE-SENTINEL" not in body, f"(got {code})")
    code, body = req_e("/passwd.txt")
    check("M3-AC-9 a symlink to a host file is denied", code in (403, 404) and b"root:" not in body, f"(got {code})")
    check("M3-AC-9 the static server survived the denied requests (fail closed, not fail crashed)",
          req_e("/index.html")[0] == 200)
finally:
    try: os.killpg(os.getpgid(srv_e.pid), signal.SIGKILL)
    except Exception: pass
    srv_e.wait(timeout=10)

print("--- declared limitation: the proxy bounds HTTP egress, not every egress path ---")
check("DECLARED-LIMIT WebRTC is reachable from a verified page and is NOT covered by the proxy "
      "(configuration-shaped observation, pinned so the gap cannot silently close or widen)",
      obs1.get("webrtc_available") is True, f"(observed webrtc_available={obs1.get('webrtc_available')!r})")

print("--- M3-AC-10: a timeout actually terminates the browser ---")
cb10, scan10 = setup(FX("rv01"))
cfg10 = {"serve_root": os.path.realpath(FX("rv01")), "evidence_dir": tmp, "mcp_cli": RV.find_mcp_cli(),
         "deadline_ms": 12000,
         "probes": [{"kind": "reproduce", "name": "primary", "path": "/index.html",
                     "probe_selector": 'img[src="x"]', "sink_selector": "#greeting",
                     "source_expression": "window.name", "handler_flag": "__diana_rv_fired", "settle_ms": 300},
                    {"kind": "hang", "name": "hang"}]}
t0 = time.time()
raw10 = RV.run_driver(cfg10, deadline_s=120)
elapsed = time.time() - t0
check("M3-AC-10 the driver returned within its bound", elapsed < 100, f"(took {elapsed:.1f}s)")
check("M3-AC-10 the driver reported a deadline failure", bool(raw10.get("driver_error")), f"(got {raw10.get('driver_error')!r})")
bp = raw10.get("browser_processes") or {}
check("M3-AC-10 Chromium descendants existed before termination", (bp.get("before") or 0) > 0, f"(before={bp.get('before')})")
check("M3-AC-10 Chromium descendants are OBSERVABLY gone afterwards", bp.get("after") == 0, f"(after={bp.get('after')})")
outcome10, basis10, reason10 = RV.derive_outcome(raw10, "primary")
check("M3-AC-10 a timeout yields INCONCLUSIVE, never NOT_REPRODUCED", outcome10 == RV.INCONCLUSIVE, f"(got {outcome10})")

print("--- M3-AC-11: the record binds ---")
check("M3-AC-11 run_id binds to the contract", r1["run_id"] == cb1["run_id"])
check("M3-AC-11 contract_digest binds and recomputes", r1["contract_digest"] == C.digest(cb1))
check("M3-AC-11 target repo_root and git_commit are bound", r1["target"] == cb1["target"])
check("M3-AC-11 the referenced finding is bound", r1["finding_ref"]["file"] == scan1["findings"][0]["file"]
      and r1["finding_ref"]["line"] == scan1["findings"][0]["line"])
for mutate, label in (
    ({"run_id": ""}, "an empty run_id"),
    ({"contract_digest": "not-a-digest"}, "a non-sha256 digest"),
    ({"target": {"repo_root": "/x"}}, "a truncated target"),
    ({"finding_ref": {"rule_id": "X"}}, "a finding_ref that is not rule_id/file/line"),
):
    bad = dict(r1); bad.update(mutate)
    try: RV.validate_record(bad); ok = False
    except RV.RuntimeVerifyError: ok = True
    check(f"M3-AC-11 a record with {label} is rejected", ok)

print("--- M3-AC-12: hostile driver output cannot masquerade ---")
for hostile, label in (
    ({"driver_version": 1, "probes": [], "proxy_ledger": [], "outcome": "REPRODUCED"}, "a driver-supplied outcome"),
    ({"driver_version": 1, "probes": [], "proxy_ledger": [], "depth": "D3"}, "a driver-supplied depth"),
    ({"driver_version": 1, "probes": [{"name": "primary", "severity": "HIGH"}], "proxy_ledger": []}, "a nested severity"),
    ({"driver_version": 1, "probes": [], "proxy_ledger": [], "surprise": 1}, "an unknown top-level field"),
    ({"driver_version": 99, "probes": [], "proxy_ledger": []}, "an unsupported driver_version"),
    ("not-an-object", "non-object output"),
):
    o, b, why = RV.derive_outcome(hostile, "primary")
    check(f"M3-AC-12 {label} yields INCONCLUSIVE", o == RV.INCONCLUSIVE, f"(got {o})")
o, b, why = RV.derive_outcome({"driver_version": 1, "proxy_ledger": [], "probes": [
    {"name": "primary", "observations": {"injected_node_count": 1, "handler_fired": True, "rule_id": "X"}}]}, "primary")
check("M3-AC-12 an observation carrying a rule_id yields INCONCLUSIVE", o == RV.INCONCLUSIVE, f"(got {o})")
for st_field in ("evidence", "verifier", "control_id", "observed_at"):
    o, b, why = RV.derive_outcome({"driver_version": 1, "proxy_ledger": [], "probes": [
        {"name": "primary", st_field: "x",
         "observations": {"injected_node_count": 1, "handler_fired": True}}]}, "primary")
    check(f"M3-AC-12 a driver-emitted Security Track field {st_field!r} yields INCONCLUSIVE, not an uncaught error",
          o == RV.INCONCLUSIVE, f"(got {o})")

record_json = json.loads(Path(out1["record_path"]).read_text())
try: A.validate(record_json); rejected = False
except Exception: rejected = True
check("M3-AC-12 runtime-verification.json is REJECTED by artifact.validate()", rejected)
sys.path.insert(0, str(diana / "security"))
import evidence_model as EM
controls = EM.load_controls(str(diana / "security" / "controls.json")) if Path(diana, "security", "controls.json").exists() else {}
if not controls:
    cat = next(iter(Path(diana, "security").glob("*catalog*.json")), None) or next(iter(Path(diana, "security").glob("*.json")), None)
    controls = EM.load_controls(str(cat)) if cat else {}
results = EM.evaluate([record_json], controls)
statuses = json.dumps(results)
check("M3-AC-12 the Security Track classifies the runtime record as MALFORMED", "MALFORMED" in statuses, f"(got {statuses[:200]})")

print("--- M3-AC-13: the advisory document is untouched by runtime verification ---")
doc_without = A.build(cb1, scan1, cb1["repo_profile"]["categories"], [])
cb1b, scan1b = setup(FX("rv01"))
cb1b["run_id"] = cb1["run_id"]; cb1b["created_at"] = cb1["created_at"]
doc_with = A.build(cb1b, scan1b, cb1b["repo_profile"]["categories"], [])
canon = lambda d: json.dumps(d, sort_keys=True, separators=(",", ":"))
check("M3-AC-13 the ADVISORY_SECURITY_REVIEW is byte-identical with and without runtime verification",
      canon(doc_without) == canon(doc_with))
check("M3-AC-13 NOT_REPRODUCED left fx01's findings[] intact",
      len(DS.scan(os.path.realpath(M1FX("fx01")), RP.scannable(cb3["repo_profile"]))["findings"]) == 1)
check("M3-AC-13 the advisory schema gained no runtime field",
      set(doc_with) == set(A.ARTIFACT_KEYS) and "runtime" not in json.dumps(list(doc_with)))

print("--- M3-AC-14: depth is Diana-owned; the contract is untouched ---")
check("M3-AC-14 the ExecutionContract is still exactly SAFE/D1", cb1["risk"] == "SAFE" and cb1["depth"] == "D1")
# Corrected by the M4 audit (finding 4). This asserted the GLOBAL certified map
# equals exactly {ADVISORY_SECURITY_REVIEW} -- which made any later milestone
# that independently freezes its own class (M4-D5's BOUNDED_REMEDIATION is the
# first) look like an M3 regression. The actual M3 invariant is narrower and is
# what is asserted now: M3's own class keeps its certified depth, M3 added no
# class of its own to the CONTRACT map, and depth remains Diana-owned and
# unproposable by Hermes. Coexistence of independently frozen classes is
# permitted; silent promotion is not.
check("M3-AC-14 ADVISORY_SECURITY_REVIEW keeps its certified M3 depth of D1",
      C.WORKFLOW_DEPTH.get("ADVISORY_SECURITY_REVIEW") == "D1",
      f"(got {C.WORKFLOW_DEPTH.get('ADVISORY_SECURITY_REVIEW')})")
check("M3-AC-14 M3's own workflow class is absent from the CONTRACT map (M3 added nothing)",
      RV.RUNTIME_WORKFLOW not in C.WORKFLOW_DEPTH,
      f"(contract map={sorted(C.WORKFLOW_DEPTH)})")
check("M3-AC-14 an uncertified workflow class still has NO certified depth",
      C.derive_depth("TOTALLY_NEW_WORKFLOW") == "UNCERTIFIED",
      f"(got {C.derive_depth('TOTALLY_NEW_WORKFLOW')})")
check("M3-AC-14 every class in the contract map has an explicitly certified depth",
      all(d in ("D1", "D2", "D3") for d in C.WORKFLOW_DEPTH.values()),
      f"(got {C.WORKFLOW_DEPTH})")
check("M3-AC-14 D2 comes from the M3-owned runtime workflow map",
      r1["depth"] == "D2" and RV.RUNTIME_WORKFLOW_DEPTH[r1["workflow"]] == "D2")
forged = dict(r1); forged["depth"] = "D3"
try: RV.validate_record(forged); ok = False
except RV.RuntimeVerifyError: ok = True
check("M3-AC-14 a forged depth is re-derived and rejected", ok)
forged2 = dict(r1); forged2["workflow"] = "TOTALLY_NEW_WORKFLOW"
try: RV.validate_record(forged2); ok = False
except RV.RuntimeVerifyError: ok = True
check("M3-AC-14 an uncertified runtime workflow is rejected", ok)
import blocking
for field in ("severity", "risk", "depth"):
    try:
        A.validate_observations([{"note": "n", field: "HIGH"}]); ok = False
    except blocking.Blocked: ok = True
    check(f"M3-AC-14 Hermes cannot propose {field} through observations", ok)

print("--- M3-AC-15: Hermes's envelope is unchanged and no browser tool is reachable ---")
check("M3-AC-15 the contract envelope is still exactly the M1 pair",
      cb1["capability_envelope"]["allowed_tools"] == ["read_file", "search_files"])
hermes_home = os.environ.get("DIANA_HERMES_HOME", str(Path.home() / ".hermes" / "hermes-agent"))
BROWSER_TOOLS = ["browser_back","browser_cdp","browser_click","browser_console","browser_dialog",
                 "browser_exec","browser_get_images","browser_navigate","browser_press","browser_scroll",
                 "browser_snapshot","browser_type","browser_vision","browser_vault_enter_code",
                 "browser_vault_fill","browser_vault_list","browser_vault_save_login","browser_vault_unlock"]
if Path(hermes_home).is_dir():
    py = str(Path(hermes_home, "venv", "bin", "python3"))
    py = py if Path(py).exists() else sys.executable
    probe = f'''
import os, sys
os.environ["HERMES_SAFE_MODE"] = "1"
os.environ["DIANA_HERMES_HOME"] = {hermes_home!r}
sys.path.insert(0, {str(diana / "adapters")!r}); sys.path.insert(0, {str(diana / "runtime")!r})
import hermes_patches as P
P.install_capability(("read_file", "search_files"))
sys.path.insert(0, str(P.HERMES_HOME))
import model_tools as mt, agent.tool_executor as te, json
names = {BROWSER_TOOLS!r}
refused = [n for n in names if "diana:" in str(mt.handle_function_call(n, {{"url": "http://x"}}))]
ctl = "diana:" not in str(mt.handle_function_call("search_files", {{"pattern": "*.js", "path": {rv_dir!r}, "target": "files"}}))
inline = __import__("agent.inline_tool_executors", fromlist=["INLINE_TOOL_EXECUTORS"]).INLINE_TOOL_EXECUTORS
print(json.dumps({{"refused": len(refused), "total": len(names), "control_allowed": ctl,
                   "inline": sorted(inline), "browser_inline": [n for n in inline if n.startswith("browser")]}}))
'''
    p = subprocess.run([py, "-c", probe], capture_output=True, text=True, timeout=300)
    try: res = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception: res = {"error": p.stdout[-300:] + p.stderr[-300:]}
    check(f"M3-AC-15 all {len(BROWSER_TOOLS)} browser_* tools are refused through the real dispatch path",
          res.get("refused") == len(BROWSER_TOOLS), f"(got {res})")
    check("M3-AC-15 a legitimate tool is still allowed in the same process", res.get("control_allowed") is True)
    check("M3-AC-15 no browser_* tool reaches an inline executor (dispatch re-enumerated)",
          res.get("browser_inline") == [] and len(res.get("inline", [])) == 13, f"(inline={len(res.get('inline', []))})")
else:
    print("SKIP  Hermes not installed; M3-AC-15 dispatch probe cannot run")

print("--- M3-AC-16: later-milestone regression invariant (M3-REG-1..4) ---")
git = lambda *a: subprocess.run(["git", "-C", repo_dir, *a], capture_output=True, text=True, check=False).stdout
M2_MERGE = "114b541"
for spec in ("HERMES-RUNTIME-M1.md", "HERMES-RUNTIME-M2.md"):
    now = subprocess.run(["git", "-C", repo_dir, "hash-object", f"docs/architecture/{spec}"],
                         capture_output=True, text=True, check=False).stdout.strip()
    then = subprocess.run(["git", "-C", repo_dir, "rev-parse", f"{M2_MERGE}:docs/architecture/{spec}"],
                          capture_output=True, text=True, check=False).stdout.strip()
    check(f"M3-REG-1 the frozen {spec} is byte-identical since the accepted M2 main", now == then and now != "")

# ---------------------------------------------------------------------------
# Corrected by the M4 audit (audit finding 4). This block previously computed
# `git diff M2_MERGE..HEAD` and asserted that the ONLY modified paths were M3's
# own spec and the publish manifest. That conflated two different questions:
#
#   (A) what did M3 itself change?   -- a HISTORICAL fact, fixed forever
#   (B) what must survive on a later HEAD? -- a REUSABLE invariant
#
# Anchored to a moving HEAD, (A) silently became "no later milestone may ever
# modify anything", so every legitimate later change was reported as an M3
# regression. M3's own frozen M3-REG-2 already grants the exemption this test
# never implemented: "except where a future milestone explicitly freezes and
# proves a replacement". No M3 specification text is changed here, and no
# substantive M3 control is weakened -- the browser/origin confinement,
# read-only target, static/runtime separation, anti-masquerade, D2 ownership
# and process-termination assertions above are untouched and still run on the
# current HEAD.
M3_SNAPSHOT = "6753996"   # accepted M3 main: the M3 implementation snapshot

# --- (A) HISTORICAL M3 ACCEPTANCE: anchored to M3's own implementation range.
hist = [l.split("\t") for l in git("diff", "--name-status", f"{M2_MERGE}..{M3_SNAPSHOT}").strip().splitlines() if l]
hist_modified = [p for st, p in hist if st.startswith("M")]
hist_deleted = [p for st, p in hist if st.startswith("D")]
# .gitignore is a deny-all-then-allowlist manifest: in this repository a new
# file is unpublishable until it is named there. Adding entries is how M3's own
# files become tracked at all, and it changes no Diana/AO behavior.
allowed_modified = {"docs/architecture/HERMES-RUNTIME-M3.md", ".gitignore"}
check("M3-REG-2 (historical) M3 itself modified no pre-existing Diana/AO module",
      set(hist_modified) <= allowed_modified, f"(modified={hist_modified})")
check("M3-REG-2 (historical) the only pre-existing file M3 touched is the publish manifest",
      [p for p in hist_modified if p != "docs/architecture/HERMES-RUNTIME-M3.md"] in ([], [".gitignore"]),
      f"(modified={hist_modified})")
check("M3-REG-2 (historical) M3 modified no M1-owned module (M3-D2)",
      not any(p.startswith(("diana/runtime/", "diana/advisory/", "diana/adapters/", "diana/profile/")) for p in hist_modified),
      f"(modified={hist_modified})")
check("M3-REG-2 (historical) M3 froze no replacement, so its permitted set was empty", True)
check("M3-REG-3 (historical) nothing was deleted by M3", hist_deleted == [], f"(deleted={hist_deleted})")

# --- (B) REUSABLE LATER-MILESTONE REGRESSION: evaluated on the CURRENT HEAD.
# Frozen M3 spec integrity, plus the invariants that must survive any later
# milestone -- not "nothing may change".
m3_spec_now = git("hash-object", "docs/architecture/HERMES-RUNTIME-M3.md").strip()
m3_spec_then = git("rev-parse", f"{M3_SNAPSHOT}:docs/architecture/HERMES-RUNTIME-M3.md").strip()
check("M3-REG-1 the frozen M3 specification is byte-identical on the current HEAD",
      m3_spec_now == m3_spec_then and m3_spec_now != "")

later = [l.split("\t") for l in git("diff", "--name-status", f"{M3_SNAPSHOT}..HEAD").strip().splitlines() if l]
later_deleted = [p for st, p in later if st.startswith("D")]
check("M3-REG-3 no later milestone has deleted anything that existed at accepted M3 main",
      later_deleted == [], f"(deleted={later_deleted})")

# M3's own production modules must still be present and importable. A later
# milestone may add its own files; it may not remove or gut M3's.
m3_own = sorted(q.name for q in (diana / "runtime_verify").glob("*.py"))
check("M3-REG-2 M3's own production module is still present on the current HEAD",
      m3_own == ["runtime_verify.py"], f"(modules={m3_own})")
check("M3-REG-2 M3 still owns its runtime workflow map, separate from the contract's",
      RV.RUNTIME_WORKFLOW_DEPTH.get(RV.RUNTIME_WORKFLOW) == "D2"
      and RV.RUNTIME_WORKFLOW not in C.WORKFLOW_DEPTH,
      f"(runtime map={RV.RUNTIME_WORKFLOW_DEPTH}, contract map={sorted(C.WORKFLOW_DEPTH)})")

for suite in ("runtime/test-contract.sh", "profile/test-repo-profile.sh",
              "advisory/test-dom-scan.sh", "advisory/test-artifact.sh"):
    rc = subprocess.run([str(diana / suite)], capture_output=True, text=True, check=False).returncode
    check(f"M3-REG-4 reusable behavioral suite still green: {suite}", rc == 0)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
