#!/usr/bin/env bash
# CP3: DOM-XSS-001 -- the frozen 8-fixture matrix, lexer behavior, C3 ceiling.
set -euo pipefail

ADV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

python3 - "$ADV_DIR" "$TMP_DIR" <<'PY'
import json, os, sys
from pathlib import Path

adv_dir, tmp = sys.argv[1], sys.argv[2]
sys.path.insert(0, adv_dir)
sys.path.insert(0, str(Path(adv_dir).parent / "profile"))
sys.path.insert(0, str(Path(adv_dir).parent / "runtime"))
import dom_scan as DS
import repo_profile as RP

passed = failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")

def scan_fixture(fid):
    root = os.path.join(adv_dir, "fixtures", "repo", fid)
    prof = RP.profile(root, {"allowed_roots": [root], "denied_subpaths": [".git/", ".env", ".env.*"]})
    return DS.scan(root, RP.scannable(prof))

def outcome(result):
    # D36: COMPLETE iff scan_issues AND unsupported_constructs are both empty.
    return "COMPLETE" if not result["scan_issues"] and not result["unsupported_constructs"] else "INCOMPLETE"

# ===== the frozen fixture matrix =====
exp = json.loads(Path(adv_dir, "fixtures", "expectations.json").read_text())
check("matrix is exactly 8 fixtures", len(exp["fixtures"]) == 8)
check("matrix ships exactly one rule", exp["rule_id"] == "DOM-XSS-001")

for fx in exp["fixtures"]:
    fid, case = fx["id"], fx["case"]
    r = scan_fixture(fid)
    check(f"{fid} finding count -- {case}", len(r["findings"]) == len(fx["findings"]),
          f"(got {len(r['findings'])}, want {len(fx['findings'])})")
    for got, want in zip(r["findings"], fx["findings"]):
        check(f"{fid} finding file", got["file"] == want["file"], f"(got {got['file']})")
        check(f"{fid} finding sink line pinned to {want['line']}", got["line"] == want["line"], f"(got {got['line']})")
        check(f"{fid} severity pinned to {want['severity']}", got["severity"] == want["severity"], f"(got {got['severity']})")
        check(f"{fid} source kind {want['source_kind']}", got["source"]["kind"] == want["source_kind"])
        check(f"{fid} sink kind {want['sink_kind']}", got["sink"]["kind"] == want["sink_kind"])
        check(f"{fid} flow DIRECT", got["flow"] == want["flow"])
        check(f"{fid} evidence carries a verbatim code excerpt",
              want["source_kind"].split(".")[-1] in got["code_excerpt"])
        check(f"{fid} no payload string in evidence", "payload" not in got)
        check(f"{fid} no runtime/browser proof in evidence",
              not any(k in got for k in ("browser", "runtime_proof", "reproduction")))
    check(f"{fid} suppressed count", len(r["suppressed"]) == len(fx["suppressed"]),
          f"(got {len(r['suppressed'])})")
    for got, want in zip(r["suppressed"], fx["suppressed"]):
        check(f"{fid} suppressed stays visible with file/line/reason",
              got["file"] == want["file"] and got["line"] == want["line"] and got["reason"] == want["reason"],
              f"(got {got})")
    check(f"{fid} unsupported_constructs count",
          len(r["unsupported_constructs"]) == len(fx["unsupported_constructs"]))
    for got, want in zip(r["unsupported_constructs"], fx["unsupported_constructs"]):
        check(f"{fid} unsupported construct pinned to file+line",
              got["file"] == want["file"] and got["line"] == want["line"]
              and got["construct"] == want["construct"], f"(got {got})")
    check(f"{fid} scan_issues count", len(r["scan_issues"]) == len(fx["scan_issues"]))
    check(f"{fid} outcome {fx['outcome']} (D36)", outcome(r) == fx["outcome"], f"(got {outcome(r)})")

# fx07's suppressed entry must never leak into findings
check("suppressed candidates never appear in findings[]",
      scan_fixture("fx07")["findings"] == [])
# fx08 pins the declared limitation by location, not by prose
check("template-literal limitation is pinned by a located test, not prose",
      scan_fixture("fx08")["unsupported_constructs"][0]["line"] == 4)

# ===== lexer behavior (D6) =====
def only(src):
    f, s, u = DS.scan_text(src, "t.js")
    return len(f), len(s), len(u)

check("line comment cannot produce a finding", only("// el.innerHTML = location.hash;\n")[0] == 0)
check("block comment cannot produce a finding", only("/* el.innerHTML = location.hash; */\n")[0] == 0)
check("double-quoted string cannot produce a finding", only('x("el.innerHTML = location.hash");\n')[0] == 0)
check("single-quoted string cannot produce a finding", only("x('el.innerHTML = location.hash');\n")[0] == 0)
check("escaped quote does not end the string early",
      only('x("a\\" el.innerHTML = location.hash");\n')[0] == 0)
check("a sanitizer named in a comment does not suppress a real finding",
      only("// sanitized with DOMPurify\nel.innerHTML = location.hash;\n")[0] == 1)
check("a sanitizer named in a string does not suppress a real finding",
      only('var n = "DOMPurify.sanitize"; el.innerHTML = location.hash;\n')[0] == 1)
check("regex literal containing the pattern is not code",
      only("var re = /el.innerHTML = location.hash/;\n")[0] == 0)
check("division is not mistaken for a regex literal",
      only("var a = b / c; el.innerHTML = location.hash;\n")[0] == 1)
check("code after a block comment is still analyzed",
      only("/* note */ el.innerHTML = location.hash;\n")[0] == 1)
check("multi-line block comment does not swallow later code",
      only("/*\n a\n*/\nel.innerHTML = location.hash;\n")[0] == 1)

# ===== closure (D8) =====
for src_expr in ("location.hash", "location.search", "location.href",
                 "document.URL", "document.referrer", "window.name"):
    check(f"source in closure: {src_expr}", only(f"el.innerHTML = {src_expr};\n")[0] == 1)
for sink in ("el.innerHTML = {s};", "el.outerHTML = {s};", "document.write({s});",
             "document.writeln({s});", "el.insertAdjacentHTML('beforeend', {s});"):
    check(f"sink in closure: {sink.split('=')[0].strip().split('(')[0]}",
          only(sink.format(s="location.hash") + "\n")[0] == 1)
check("window.location.hash is recognized", only("el.innerHTML = window.location.hash;\n")[0] == 1)
check("innerHTML += is an assignment sink", only("el.innerHTML += location.hash;\n")[0] == 1)
check("innerHTML == is NOT an assignment", only("if (el.innerHTML == location.hash) {}\n")[0] == 0)
check("eval is out of scope (code injection, a separate rule family)",
      only("eval(location.hash);\n")[0] == 0)
check("setAttribute handler injection is out of scope",
      only("el.setAttribute('onclick', location.hash);\n")[0] == 0)
check("a lookalike identifier is not a source",
      only("el.innerHTML = mylocation.hash;\n")[0] == 0)
check("a property named location on another object is not a source",
      only("el.innerHTML = cfg.location.hash;\n")[0] == 0)

# ===== DIRECT only (D4) =====
check("indirect flow through a variable is NOT reported",
      only("var h = location.hash;\nel.innerHTML = h;\n")[0] == 0)
check("a source in an unrelated earlier statement does not attach to a later sink",
      only("log(location.hash);\nel.innerHTML = 'safe';\n")[0] == 0)

# ===== sanitizers (D5) =====
check("DOMPurify.sanitize suppresses", only("el.innerHTML = DOMPurify.sanitize(location.hash);\n") == (0, 1, 0))
check("encodeURIComponent is NOT an HTML sanitizer and does not suppress",
      only("el.innerHTML = encodeURIComponent(location.hash);\n")[0] == 1)
check("escapeHtml is not trusted without proof of implementation",
      only("el.innerHTML = escapeHtml(location.hash);\n")[0] == 1)
check("a custom sanitizer is still reported (stated opposite error)",
      only("el.innerHTML = mySanitize(location.hash);\n")[0] == 1)
check("suppression is by enclosing call, not by a name elsewhere in the statement",
      only("el.innerHTML = DOMPurify.sanitize(a) + location.hash;\n")[0] == 1)

# ===== HTML inline scripts =====
html = "<html>\n<body>\n<script>\nel.innerHTML = location.hash;\n</script>\n</html>\n"
Path(tmp, "page.html").write_text(html)
f, s, u, i = DS.scan_file(tmp, "page.html")
check("inline <script> in HTML is analyzed", len(f) == 1)
check("inline <script> finding carries the HTML file's line number", f and f[0]["line"] == 4, f"(got {f[0]['line'] if f else None})")
Path(tmp, "ext.html").write_text('<script src="app.js"></script>\n')
check("<script src> has no body to analyze (external scripts are NOT_CHECKED)",
      DS.scan_file(tmp, "ext.html")[0] == [])

# ===== C3: FILE_TOO_LARGE, READ_FAILED, DECODE_FAILED =====
check("C3 threshold is 512000 bytes", DS.MAX_FILE_BYTES == 512_000)
big = Path(tmp, "big.js"); big.write_text("var x = 1;\n" * 60000)
check("oversized file exceeds the ceiling", big.stat().st_size > DS.MAX_FILE_BYTES)
issues = DS.scan_file(tmp, "big.js")[3]
check("oversized file yields exactly one FILE_TOO_LARGE issue",
      len(issues) == 1 and issues[0]["kind"] == "FILE_TOO_LARGE")
check("FILE_TOO_LARGE records the observed size", "bytes" in issues[0]["detail"])
Path(tmp, "bad.js").write_bytes(b"\xff\xfe el.innerHTML = location.hash;")
di = DS.scan_file(tmp, "bad.js")[3]
check("undecodable file yields DECODE_FAILED", len(di) == 1 and di[0]["kind"] == "DECODE_FAILED")
mi = DS.scan_file(tmp, "missing.js")[3]
check("unreadable file yields READ_FAILED", len(mi) == 1 and mi[0]["kind"] == "READ_FAILED")
check("a file that could not be analyzed is never silently skipped",
      all(x[0]["file"] for x in (issues, di, mi)))

# ===== determinism =====
a, b = scan_fixture("fx01"), scan_fixture("fx01")
check("scanner output is byte-identical across runs",
      json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True))
shuffled = list(reversed(RP.scannable(RP.profile(
    os.path.join(adv_dir, "fixtures", "repo", "fx01"),
    {"allowed_roots": [os.path.join(adv_dir, "fixtures", "repo", "fx01")], "denied_subpaths": []}))))
root01 = os.path.join(adv_dir, "fixtures", "repo", "fx01")
check("scanner output is independent of input file order",
      json.dumps(DS.scan(root01, shuffled), sort_keys=True) == json.dumps(a, sort_keys=True))

# ===== limitations are declared, not implied =====
lim = " ".join(DS.GLOBAL_LIMITATIONS).lower()
check("limitations declare the DIRECT-only recall gap", "direct" in lim and "variable" in lim)
check("limitations declare template literals NOT_ANALYZED", "template-literal" in lim)
check("limitations declare the narrow sanitizer allowlist", "dompurify.sanitize" in lim)
check("limitations declare that no payload or browser proof exists",
      "no payload" in lim and "browser" in lim)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
