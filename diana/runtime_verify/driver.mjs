#!/usr/bin/env node
// Diana M3 runtime-verification driver (spec: HERMES-RUNTIME-M3.md, M3-D7..M3-D13).
//
// This process owns three things and nothing else:
//
//   1. a read-only static server over the contract's read_scope (M3-D11),
//   2. a Diana-owned egress proxy that is THE network boundary (M3-D8),
//   3. a bounded, process-group-owned @playwright/mcp browser (M3-D9, M3-D13).
//
// It reports OBSERVATIONS ONLY. It must never name an outcome, a depth, a
// severity or a rule_id -- Diana derives those (M3-D7), and emitting one here is
// a schema violation on the Python side that yields INCONCLUSIVE rather than a
// value anyone honours. That asymmetry is deliberate: the component that runs
// untrusted page content is not the component that decides what it proved.

import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { execFileSync } from "node:child_process";
import { readFileSync, realpathSync } from "node:fs";
import { extname, join, resolve, sep } from "node:path";
import net from "node:net";
import readline from "node:readline";

const MIME = {
  ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json",
  ".svg": "image/svg+xml", ".png": "image/png", ".txt": "text/plain; charset=utf-8",
};

// Diana-owned harness endpoints. Reserved prefix, checked BEFORE the target
// tree, so a target file can never shadow one and a harness page is never
// mistaken for target content.
const HARNESS_PREFIX = "/__diana__/";

// The only expressions the driver will evaluate inside a page. These mirror the
// source kinds diana/advisory/dom_scan.py can emit. The Python side already
// constrains this, but `run_driver` is reachable on its own, so the driver fails
// closed here rather than trusting its caller to have done the check.
const ALLOWED_SOURCE_EXPRESSIONS = new Set([
  "window.name", "location.hash", "location.search", "location.href",
  "document.URL", "document.referrer",
]);

// ---------------------------------------------------------------------------
// 1. static server -- GET/HEAD only, canonicalize-then-contain (M3-D11, M1 C2)
// ---------------------------------------------------------------------------

function containedPath(serveRoot, urlPath) {
  // Decode, then resolve symlinks, then test containment on the CANONICAL
  // paths with the separator appended -- `/srv/repo-evil` does not start with
  // `/srv/repo/`, so the comparison lands on a component boundary rather than
  // on a bare string prefix. Mirrors diana/runtime/read_scope.py's discipline.
  //
  // Everything here is inside one try: a throw escaping this function would
  // escape the request handler and take the static server down, which is a
  // fail-OPEN outcome dressed as a crash. Every failure returns null == deny.
  try {
    const decoded = decodeURIComponent(urlPath);
    if (decoded.includes("\0")) return null;
    const rel = decoded.replace(/^\/+/, "");
    const candidate = resolve(serveRoot, rel);
    const canonical = realpathSync(candidate);
    const root = realpathSync(serveRoot);
    if (canonical !== root && !canonical.startsWith(root + sep)) return null;
  // Denied subpaths carried from the contract's read_scope, applied per
  // component so a nested .git or .env can never be served.
    const parts = canonical.slice(root.length).split(sep).filter(Boolean);
    if (parts.some((p) => p === ".git" || p === ".env" || p.startsWith(".env."))) return null;
    return canonical;
  } catch {
    return null;
  }
}

function startStaticServer(serveRoot, harness) {
  const log = [];
  const server = createServer((req, res) => {
    const method = req.method || "";
    let urlPath = "/";
    try { urlPath = new URL(req.url, "http://127.0.0.1").pathname; } catch { /* keep "/" */ }

    // Method check happens BEFORE any filesystem access: there must be no
    // write path to reach, not merely no write handler.
    if (method !== "GET" && method !== "HEAD") {
      log.push({ method, path: urlPath, status: 405 });
      res.writeHead(405, { Allow: "GET, HEAD", "Content-Type": "text/plain" });
      res.end("diana: static server implements GET and HEAD only");
      return;
    }

    if (urlPath.startsWith(HARNESS_PREFIX)) {
      const name = urlPath.slice(HARNESS_PREFIX.length);
      const page = harness[name];
      if (page === undefined) {
        log.push({ method, path: urlPath, status: 404 });
        res.writeHead(404, { "Content-Type": "text/plain" }); res.end("not found"); return;
      }
      if (page.redirectTo) {
        log.push({ method, path: urlPath, status: 302 });
        res.writeHead(302, { Location: page.redirectTo }); res.end(); return;
      }
      log.push({ method, path: urlPath, status: 200 });
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(method === "HEAD" ? undefined : page.body);
      return;
    }

    const target = containedPath(serveRoot, urlPath === "/" ? "/index.html" : urlPath);
    if (target === null) {
      log.push({ method, path: urlPath, status: 403 });
      res.writeHead(403, { "Content-Type": "text/plain" });
      res.end("diana: path outside the served read_scope");
      return;
    }
    let body;
    try { body = readFileSync(target); } catch {
      log.push({ method, path: urlPath, status: 404 });
      res.writeHead(404, { "Content-Type": "text/plain" }); res.end("not found"); return;
    }
    log.push({ method, path: urlPath, status: 200 });
    res.writeHead(200, { "Content-Type": MIME[extname(target)] || "application/octet-stream" });
    res.end(method === "HEAD" ? undefined : body);
  });
  return new Promise((ok, bad) => {
    server.on("error", bad);
    server.listen(0, "127.0.0.1", () => ok({ server, log }));
  });
}

// ---------------------------------------------------------------------------
// 2. Diana egress proxy -- explicit single-origin allowlist, fail closed (M3-D8)
// ---------------------------------------------------------------------------

function startProxy(allowedHostPort) {
  const ledger = [];
  const decide = (host) => host === allowedHostPort;

  const server = createServer((req, res) => {
    let host = "";
    // Any failure to understand the request is a REFUSAL, never a pass-through.
    try { host = new URL(req.url).host; } catch { host = ""; }
    const allowed = decide(host);
    ledger.push({ transport: "http", host: host || "<unparseable>", allowed });
    if (!allowed) {
      res.writeHead(403, { "Content-Type": "text/plain" });
      res.end("diana: destination is not in the runtime envelope");
      return;
    }
    const upstream = new URL(req.url);
    const conn = net.connect(Number(upstream.port || 80), upstream.hostname, () => {
      const headers = [`${req.method} ${upstream.pathname}${upstream.search} HTTP/1.1`,
        `Host: ${upstream.host}`, "Connection: close"];
      for (const [k, v] of Object.entries(req.headers)) {
        if (["host", "connection", "proxy-connection"].includes(k.toLowerCase())) continue;
        headers.push(`${k}: ${v}`);
      }
      conn.write(headers.join("\r\n") + "\r\n\r\n");
      req.pipe(conn);
      conn.pipe(res.socket);
    });
    conn.on("error", () => { try { res.writeHead(502); res.end("upstream error"); } catch { /* socket gone */ } });
  });

  // CONNECT is how the browser reaches anything over TLS. There is no TLS
  // destination inside the envelope (the target is plaintext loopback), so
  // every CONNECT is refused unconditionally -- including Chromium's own
  // telemetry, which is the traffic F7 recorded.
  server.on("connect", (req, socket) => {
    ledger.push({ transport: "connect", host: req.url || "<none>", allowed: false });
    try { socket.write("HTTP/1.1 403 Forbidden\r\n\r\n"); } catch { /* already gone */ }
    socket.destroy();
  });

  return new Promise((ok, bad) => {
    server.on("error", bad);
    server.listen(0, "127.0.0.1", () => ok({ server, ledger }));
  });
}

// ---------------------------------------------------------------------------
// 3. MCP client
// ---------------------------------------------------------------------------

class McpClient {
  constructor(child) {
    this.child = child; this.pending = new Map(); this.nextId = 0;
    readline.createInterface({ input: child.stdout }).on("line", (line) => {
      let msg; try { msg = JSON.parse(line); } catch { return; }
      if (msg.id === undefined || !this.pending.has(msg.id)) return;
      const { ok, bad } = this.pending.get(msg.id); this.pending.delete(msg.id);
      msg.error ? bad(new Error(msg.error.message)) : ok(msg.result);
    });
  }
  request(method, params, timeoutMs) {
    const id = ++this.nextId;
    return new Promise((ok, bad) => {
      const timer = setTimeout(() => { this.pending.delete(id); bad(new Error(`mcp timeout: ${method}`)); }, timeoutMs);
      this.pending.set(id, { ok: (v) => { clearTimeout(timer); ok(v); }, bad: (e) => { clearTimeout(timer); bad(e); } });
      this.child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
    });
  }
  async initialize(timeoutMs) {
    const r = await this.request("initialize", {
      protocolVersion: "2025-06-18", capabilities: {},
      clientInfo: { name: "diana-runtime-verify", version: "1" },
    }, timeoutMs);
    this.child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized", params: {} }) + "\n");
    return r;
  }
  async call(name, args, timeoutMs) {
    const r = await this.request("tools/call", { name, arguments: args }, timeoutMs);
    const text = (r?.content ?? []).filter((c) => c.type === "text").map((c) => c.text).join("\n");
    return { isError: !!r?.isError, text };
  }
}

// `browser_evaluate` returns prose with a "### Result" section; the JSON we
// asked for is inside it. Parsing defensively matters: an unreadable answer
// must become a driver error (-> INCONCLUSIVE), never a fabricated observation.
function evalResult(text) {
  const m = text.match(/### Result\s*\n([\s\S]*?)(?:\n### |$)/);
  if (!m) return null;
  try { return JSON.parse(m[1].trim()); } catch { return null; }
}

// ---------------------------------------------------------------------------
// process-group termination (M3-D13, F10)
// ---------------------------------------------------------------------------

function processTree() {
  try {
    const out = execFileSync("ps", ["-eo", "pid,ppid,comm", "--no-headers"], { encoding: "utf8" });
    const byParent = new Map();
    for (const line of out.trim().split("\n")) {
      const [pid, ppid, ...rest] = line.trim().split(/\s+/);
      if (!byParent.has(ppid)) byParent.set(ppid, []);
      byParent.get(ppid).push({ pid: Number(pid), comm: rest.join(" ") });
    }
    return byParent;
  } catch { return new Map(); }
}

function descendants(rootPid) {
  const tree = processTree(); const out = []; const stack = [String(rootPid)];
  const seen = new Set();
  while (stack.length) {
    const p = stack.pop();
    if (seen.has(p)) continue; seen.add(p);
    for (const child of tree.get(p) || []) { out.push(child); stack.push(String(child.pid)); }
  }
  return out;
}

function browserCount(list) { return list.filter((d) => /chrom|headless/i.test(d.comm)).length; }

function killGroup(pid) {
  try { process.kill(-pid, "SIGKILL"); return true; }
  catch { try { process.kill(pid, "SIGKILL"); return true; } catch { return false; } }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

const HARNESS_PAGES = (offsiteUrl) => ({
  "redirect": { redirectTo: offsiteUrl },
  "subresource.html": {
    body: `<!doctype html><meta charset="utf-8"><title>sub</title>
<img id="ext" src="${offsiteUrl}/pixel.png"
     onload="window.__diana_img_ok=true" onerror="window.__diana_img_failed=true">
<script>
fetch(${JSON.stringify(offsiteUrl + "/data.json")})
  .then(function(){ window.__diana_fetch_ok = true; })
  .catch(function(){ window.__diana_fetch_failed = true; });
</script>`,
  },
});

async function main() {
  const [, , mode, configPath] = process.argv;
  const cfg = JSON.parse(readFileSync(configPath, "utf8"));
  const offsite = cfg.offsite_origin || "http://diana-offsite.invalid";

  const { server: statics, log: serverLog } = await startStaticServer(cfg.serve_root, HARNESS_PAGES(offsite));
  const targetPort = statics.address().port;
  const targetOrigin = `http://127.0.0.1:${targetPort}`;

  if (mode === "serve") {
    // Used by acceptance to exercise the static server directly with raw HTTP,
    // independently of any browser.
    process.stdout.write(JSON.stringify({ origin: targetOrigin, port: targetPort }) + "\n");
    process.on("SIGTERM", () => { statics.close(); process.exit(0); });
    return;
  }

  const { server: proxy, ledger } = await startProxy(`127.0.0.1:${targetPort}`);
  const proxyPort = proxy.address().port;

  const deadlineMs = Number(cfg.deadline_ms || 90000);
  const callMs = Math.max(5000, Math.min(45000, deadlineMs));

  const args = [
    cfg.mcp_cli,
    "--headless", "--isolated", "--block-service-workers",
    "--proxy-server", `http://127.0.0.1:${proxyPort}`,
    "--proxy-bypass", "<-loopback>",
    "--allowed-origins", targetOrigin,
    "--output-dir", cfg.evidence_dir,
  ];
  if (cfg.init_script) args.push("--init-script", cfg.init_script);

  // detached: the browser and its 9-odd Chromium children become a process
  // GROUP we can signal as a unit. A plain child.kill() leaves them running.
  const child = spawn("node", args, { stdio: ["pipe", "pipe", "pipe"], detached: true });
  const stderr = [];
  child.stderr.on("data", (d) => stderr.push(d.toString()));

  const out = {
    driver_version: 1,
    runtime_envelope: {
      target_origin: targetOrigin,
      proxy_origin: `http://127.0.0.1:${proxyPort}`,
      mcp_args: args.slice(1).map((a) => (a === cfg.evidence_dir ? "<evidence_dir>" : a === cfg.init_script ? "<init_script>" : a)),
      deadline_ms: deadlineMs,
      harness_prefix: HARNESS_PREFIX,
      offsite_origin: offsite,
    },
    probes: [],
    proxy_ledger: [],
    static_server_log: [],
    browser_processes: { before: null, after: null },
    driver_error: null,
  };

  let timedOut = false;
  // Sample the live descendant count BEFORE the kill, not in the finally block:
  // by the time cleanup runs the deadline has already fired, so a count taken
  // there would be zero for the wrong reason and "the browser is gone" would
  // pass vacuously.
  const deadline = setTimeout(() => {
    timedOut = true;
    out.browser_processes.before = browserCount(descendants(child.pid));
    killGroup(child.pid);
  }, deadlineMs);

  const client = new McpClient(child);
  try {
    await client.initialize(callMs);

    for (const probe of cfg.probes || []) {
      const entry = { kind: probe.kind, name: probe.name || probe.kind };
      try {
        if (probe.kind === "reproduce") {
          const sourceExpr = probe.source_expression || "window.name";
          if (!ALLOWED_SOURCE_EXPRESSIONS.has(sourceExpr)) {
            throw new Error(`source expression not in the allowlist: ${sourceExpr}`);
          }
          const url = `${targetOrigin}${probe.path}`;
          entry.url = url;
          const nav = await client.call("browser_navigate", { url }, callMs);
          entry.navigation_error = nav.isError ? nav.text.slice(0, 300) : null;
          await new Promise((r) => setTimeout(r, Number(probe.settle_ms || 800)));
          // The source expression is the one the FINDING names, so the record
          // shows what the page actually received through that exact channel --
          // which is how F8's percent-encoding becomes visible as evidence
          // rather than as an unexplained non-reproduction.
          const ev = await client.call("browser_evaluate", {
            function: `() => {
              const sink = document.querySelector(${JSON.stringify(probe.sink_selector)});
              let sourceValue = null;
              try { sourceValue = String(eval(${JSON.stringify(sourceExpr)})); } catch (e) { sourceValue = "<unreadable: " + e.message + ">"; }
              return {
                injected_node_count: document.querySelectorAll(${JSON.stringify(probe.probe_selector)}).length,
                handler_fired: !!window[${JSON.stringify(probe.handler_flag)}],
                sink_html: sink ? String(sink.innerHTML).slice(0, 500)
                                : String(document.body ? document.body.innerHTML : "").slice(0, 500),
                source_value: sourceValue.slice(0, 500),
                final_url: String(location.href).slice(0, 500),
                webrtc_available: typeof RTCPeerConnection !== "undefined",
              };
            }`,
          }, callMs);
          const parsed = evalResult(ev.text);
          if (parsed === null) throw new Error("browser_evaluate result was not parseable");
          entry.observations = parsed;
        } else if (probe.kind === "navigate") {
          const url = probe.url.replace("{{OFFSITE}}", offsite).replace("{{TARGET}}", targetOrigin);
          entry.url = url;
          const nav = await client.call("browser_navigate", { url }, callMs);
          entry.navigation_error = nav.isError ? nav.text.slice(0, 300) : null;
          if (!nav.isError) {
            const ev = await client.call("browser_evaluate", {
              function: `() => ({ final_url: String(location.href).slice(0,500), body_text: String(document.body ? document.body.innerText : "").slice(0,300) })`,
            }, callMs);
            entry.observations = evalResult(ev.text);
          }
        } else if (probe.kind === "subresource") {
          const url = `${targetOrigin}${HARNESS_PREFIX}subresource.html`;
          entry.url = url;
          const nav = await client.call("browser_navigate", { url }, callMs);
          entry.navigation_error = nav.isError ? nav.text.slice(0, 300) : null;
          await new Promise((r) => setTimeout(r, Number(probe.settle_ms || 2500)));
          const ev = await client.call("browser_evaluate", {
            function: `() => ({ img_ok: !!window.__diana_img_ok, img_failed: !!window.__diana_img_failed,
                                fetch_ok: !!window.__diana_fetch_ok, fetch_failed: !!window.__diana_fetch_failed,
                                final_url: String(location.href).slice(0,500) })`,
          }, callMs);
          entry.observations = evalResult(ev.text);
        } else if (probe.kind === "hang") {
          // Deliberately outlive the deadline so termination can be observed.
          // Polls `timedOut` so the driver unwinds as soon as the deadline
          // fires, rather than sitting on a timer nobody is waiting for.
          const until = Date.now() + deadlineMs * 4;
          while (!timedOut && Date.now() < until) {
            await new Promise((r) => setTimeout(r, 200));
          }
        } else {
          throw new Error(`unknown probe kind: ${probe.kind}`);
        }
      } catch (err) {
        entry.probe_error = String(err?.message ?? err);
      }
      out.probes.push(entry);
      if (timedOut) break;
    }
  } catch (err) {
    out.driver_error = String(err?.message ?? err);
  } finally {
    clearTimeout(deadline);
    if (out.browser_processes.before === null) {
      out.browser_processes.before = browserCount(descendants(child.pid));
    }
    killGroup(child.pid);
    await new Promise((r) => setTimeout(r, 1200));
    out.browser_processes.after = browserCount(descendants(child.pid));
    statics.close();
    proxy.close();
  }

  if (timedOut && !out.driver_error) out.driver_error = "deadline exceeded; browser terminated";
  out.proxy_ledger = ledger;
  out.static_server_log = serverLog;
  if (stderr.length && out.driver_error) out.stderr_tail = stderr.join("").slice(-800);

  process.stdout.write(JSON.stringify(out) + "\n");
  process.exit(0);
}

main().catch((err) => {
  process.stdout.write(JSON.stringify({ driver_version: 1, driver_error: String(err?.message ?? err) }) + "\n");
  process.exit(0);
});
