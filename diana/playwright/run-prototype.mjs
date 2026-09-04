#!/usr/bin/env node
// Diana Phase 3 prototype driver.
//
// Proves the actual Playwright MCP integration end to end, headlessly and
// deterministically: spawns the real `@playwright/mcp` server over its
// stdio JSON-RPC transport (the same transport Claude Code and other MCP
// clients use), drives it through a local fixture app (navigate, fill,
// click, navigate again), and reports what happened as JSON.
//
// This script is a regression-test harness only. It is not part of
// /review's runtime path — when /review actually runs, the coding agent's
// own MCP client calls these same tools directly. This script exists so
// the integration can be proven and re-verified without a live agent
// session (CASE B/C/D/E in diana/playwright/README.md).
//
// Only ever navigates to a server this script itself starts on
// 127.0.0.1 — never a live external site (CASE E: isolation).
//
// Requires Node >= 20 on PATH (Playwright's own hard requirement, not a
// Diana-added one — see README.md "Prerequisites").

import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize, resolve } from "node:path";
import readline from "node:readline";

const MIME = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript" };

function startStaticServer(rootDir) {
  return new Promise((resolve, reject) => {
    const server = createServer(async (req, res) => {
      try {
        const urlPath = new URL(req.url, "http://localhost").pathname;
        const safePath = normalize(urlPath).replace(/^(\.\.[/\\])+/, "");
        const filePath = join(rootDir, safePath === "/" ? "/index.html" : safePath);
        const body = await readFile(filePath);
        res.writeHead(200, { "Content-Type": MIME[extname(filePath)] || "application/octet-stream" });
        res.end(body);
      } catch {
        res.writeHead(404);
        res.end("not found");
      }
    });
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

class McpClient {
  constructor(child) {
    this.child = child;
    this.pending = new Map();
    this.nextId = 0;
    this.rl = readline.createInterface({ input: child.stdout });
    this.rl.on("line", (line) => this.onLine(line));
  }

  onLine(line) {
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      return; // ignore non-JSON stdout noise, per spec servers must not emit any, but be defensive
    }
    if (msg.id !== undefined && this.pending.has(msg.id)) {
      const { resolve, reject } = this.pending.get(msg.id);
      this.pending.delete(msg.id);
      if (msg.error) reject(new Error(`MCP error ${msg.error.code}: ${msg.error.message}`));
      else resolve(msg.result);
    }
  }

  request(method, params, timeoutMs = 20000) {
    const id = ++this.nextId;
    const line = JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n";
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`MCP request timed out: ${method}`));
      }, timeoutMs);
      this.pending.set(id, {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      this.child.stdin.write(line);
    });
  }

  notify(method, params) {
    this.child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method, params }) + "\n");
  }

  async initialize() {
    const result = await this.request("initialize", {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: { name: "diana-playwright-prototype", version: "0.0.1" },
    });
    this.notify("notifications/initialized", {});
    return result;
  }

  async callTool(name, args) {
    const result = await this.request("tools/call", { name, arguments: args });
    if (result?.isError) {
      throw new Error(`tool ${name} reported an error: ${JSON.stringify(result.content)}`);
    }
    return result;
  }

  toolText(result) {
    return (result?.content ?? [])
      .filter((c) => c.type === "text")
      .map((c) => c.text)
      .join("\n");
  }
}

async function main() {
  const [, , fixtureDir, evidenceDir, personName] = process.argv;
  if (!fixtureDir || !evidenceDir) {
    console.error("usage: run-prototype.mjs FIXTURE_DIR EVIDENCE_DIR [name]");
    process.exit(2);
  }
  const name = personName || "Ada";

  const server = await startStaticServer(fixtureDir);
  const port = server.address().port;
  const baseUrl = `http://127.0.0.1:${port}`; // localhost only — CASE E

  const child = spawn(
    "npx",
    ["-y", "@playwright/mcp@latest", "--headless", "--isolated", "--output-dir", evidenceDir],
    { stdio: ["pipe", "pipe", "pipe"] },
  );
  const stderrLines = [];
  child.stderr.on("data", (d) => stderrLines.push(d.toString()));

  const client = new McpClient(child);
  const report = { fixtureDir, baseUrl, steps: [] };

  try {
    await client.initialize();
    report.steps.push("initialized");

    await client.callTool("browser_navigate", { url: `${baseUrl}/index.html` });
    report.steps.push("navigated:index.html");

    await client.callTool("browser_type", { target: "#name", element: "name input", text: name });
    report.steps.push("filled:name");

    await client.callTool("browser_click", { target: "#continue", element: "Continue button" });
    report.steps.push("clicked:continue");

    await client.callTool("browser_wait_for", { text: "Welcome" });

    const snapshot = client.toolText(await client.callTool("browser_snapshot", {}));
    report.steps.push("snapshot:welcome");

    // Pass an absolute path: relative filenames were observed resolving
    // against the MCP server's own CWD, not --output-dir (README's "prefer
    // relative file names to stay within the output directory" did not
    // hold for this tool/version — documented in README.md limitations).
    const screenshotPath = join(resolve(evidenceDir), "welcome.png");
    const screenshotResult = await client.callTool("browser_take_screenshot", {
      filename: screenshotPath,
      fullPage: true,
    });
    report.screenshot = screenshotPath;
    void screenshotResult;
    report.steps.push("screenshot:welcome.png");

    const expected = `Welcome, ${name}!`;
    report.expected_text = expected;
    report.snapshot_excerpt = snapshot.slice(0, 2000);
    report.matches_expected_name = snapshot.includes(expected);

    // Second navigation/interaction: use the back link to prove multi-view
    // navigation (view 2 -> view 1), not just the one forward hop.
    await client.callTool("browser_click", { target: "#back", element: "Back link" });
    await client.callTool("browser_wait_for", { text: "Sign up" });
    report.steps.push("navigated-back:index.html");

    console.log(JSON.stringify(report, null, 2));
    process.exitCode = 0;
  } catch (err) {
    report.error = String(err?.message ?? err);
    report.stderr = stderrLines.join("").slice(-2000);
    console.log(JSON.stringify(report, null, 2));
    process.exitCode = 1;
  } finally {
    try { child.stdin.end(); } catch { /* already closed */ }
    setTimeout(() => { try { child.kill(); } catch { /* already dead */ } }, 1000);
    server.close();
  }
}

main();
