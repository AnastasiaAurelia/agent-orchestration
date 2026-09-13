# Hermes Runtime — Milestone 3: D2 Runtime / Browser Verification (FROZEN)

Status: **normative and frozen**. Source of truth for M3; remains normative after implementation.

Architectural decisions **M3-D1 … M3-D16 are frozen**. M1's D1–D38 and M2's D1–D14 remain frozen and
unmodified; where M3 appears to disagree with either, the earlier milestone wins and the M3 text is
the defect.

Branch: `feature/hermes-runtime-verification`, based on accepted M1+M2 `main` (`114b541`).
Roadmap: [`HERMES-ROADMAP.md`](HERMES-ROADMAP.md).
Prior: [`HERMES-RUNTIME-M1.md`](HERMES-RUNTIME-M1.md), [`HERMES-RUNTIME-M2.md`](HERMES-RUNTIME-M2.md).

---

## Thesis

> Prove that Diana can add bounded runtime/browser verification to an existing static advisory
> finding **without weakening the M1/M2 enforcement boundary** and **without granting general browser
> or network authority**.

M3 remains **read-only with respect to the target repository**.

Not in M3: `write_file`, `patch`, shell, arbitrary network access, subagents/`delegate_task`,
unattended mutation, AO replacement, per-tool argument policy.

---

## Phase 0 — empirical findings

Re-established on the accepted M1+M2 base. A **preserved M3 draft written before M2 was merged**
asserted five findings; each was rechecked rather than inherited. Three survived, **two did not and
are corrected here**. A flag existing is not evidence.

| # | Finding | vs. draft |
|---|---|---|
| **F1** | `diana/playwright/` contains a working prototype: `run-prototype.mjs` spawns `@playwright/mcp` over stdio JSON-RPC, starts its own static server on `127.0.0.1`, and never navigates to a live external site. `evidence/` holds real reports and screenshots from real Chromium runs. | confirmed |
| **F2** | Node **v26.8.2**; Playwright browsers present (`chromium-1228`, `chromium_headless_shell-1228`). The bare `playwright` npm package is **not** resolvable and Python `playwright` is absent in **both** interpreters (system and `~/.hermes/hermes-agent/venv`). `@playwright/mcp` **0.0.80** is present in the npx cache. The MCP route is the available one, not a preference. | confirmed |
| **F3** | `@playwright/mcp` 0.0.80's own `--help` states, for **both** `--allowed-origins` and `--blocked-origins`: *"**does not** serve as a security boundary and **does not** affect redirects."* The vendor disclaims the very property the draft relied on. `--allow-unrestricted-file-access` is real and M3 must never pass it; its default already restricts file access to workspace roots and blocks `file://` navigation. | **corrected** |
| **F4** | **`--allowed-origins` was behaviorally falsified as a sole boundary.** With it set to the Diana-started origin and no other control, a direct off-origin navigation is refused (`net::ERR_BLOCKED_BY_CLIENT`) — but a **302 redirect from the in-scope origin to an off-origin host was followed**: the document's `location.href` became the external origin and the only reason no data returned was that the probe hostname does not resolve (`net::ERR_NAME_NOT_RESOLVED`). Against a host that resolves, this is an escape. The draft's F4 tested only the direct-navigation case and generalized from it. | **corrected** |
| **F5** | Hermes's registry contains **18** `browser_*` tools: `browser_back`, `browser_cdp`, `browser_click`, `browser_console`, `browser_dialog`, `browser_exec`, `browser_get_images`, `browser_navigate`, `browser_press`, `browser_scroll`, `browser_snapshot`, `browser_type`, `browser_vision`, and **five credential-bearing** `browser_vault_*` tools (`unlock`, `fill`, `list`, `save_login`, `enter_code`). Under the M1/M2 envelope **all 18 are refused** by set membership with no special case — verified behaviorally through `model_tools.handle_function_call`, with a legitimate `search_files` still allowed in the same process. | confirmed and enumerated |
| **F6** | **A Diana-owned egress proxy is a true choke point.** With `--proxy-server` pointed at a Diana process and `--proxy-bypass "<-loopback>"` (which cancels Chromium's implicit localhost bypass), **every** request traverses Diana's proxy — including loopback requests to the target itself, redirect targets, and page sub-resources. The redirect of F4 was refused at the proxy; `fetch()` and `<img>` to an off-origin host both failed. | new |
| **F7** | **Chromium initiates unsolicited external egress that neither flag prevents.** A single bounded verification produced proxy-observed CONNECT/GET attempts to `safebrowsingohttpgateway.googleapis.com`, `clients2.google.com`, `accounts.google.com` and `www.google.com`. Without the Diana proxy these leave the machine. This is browser-vendor telemetry, not agent action, and it is the strongest argument that origin *flags* are not an egress boundary. | new |
| **F8** | **`location.hash` does not carry an HTML-injecting payload in Chromium.** Navigating to `…/index.html#<img src=x onerror=…>` yields `location.hash === "#%3Cimg%20src=x%20onerror=…%3E"`: `<`, `>`, `"` and space are percent-encoded. `el.innerHTML = location.hash` therefore assigns inert text — observed `imgs: 0`, handler never fired, `innerHTML` literally `#%3Cimg…`. **M1 fixture `fx01`'s real `DOM-XSS-001` finding does NOT reproduce under this bounded runtime.** The draft's M3-AC-1 asserted the opposite and was never executed. | **corrects the draft's AC-1** |
| **F9** | **`window.name` is delivered verbatim and does reproduce.** `window.name` is in M1 D8's source closure and receives no URL encoding. With the payload delivered through `--init-script`, `el.innerHTML = window.name` produced a real `<img>` element in the live DOM (`imgs: 1`) and its `onerror` handler **actually executed** (`fired: true`), `innerHTML === '<img src="x" onerror="…">'`. The control — identical payload, identical delivery, `el.textContent = window.name` — produced `imgs: 0`, `fired: false`, `innerHTML === '&lt;img src=x…&gt;'`. | new |
| **F10** | **Termination requires a process group, not a child kill.** One MCP server owned **9** Chromium descendants. `child.kill()` on a non-detached child leaves them running; spawning detached and signalling the negative PID (`kill(-pid)`) took observed descendants from 9 to **0**. | new |
| **F11** | Dispatch surface re-enumerated from the pinned source, per the roadmap's invariant 2: `INLINE_TOOL_EXECUTORS` still holds exactly **13** names (`annotate_preview`, `clarify`, `delegate_task`, `desktop_preview`, `drive_preview`, `gui_tour`, `memory`, `message_agent`, `read_terminal`, `read_window_below`, `session_search`, `setup_mcp`, `todo_list`) — unchanged since M1's audit, and **no `browser_*` tool among them**. All 18 browser tools reach dispatch through `handle_function_call`, which Diana already guards. | confirmed |
| **F12** | Regression baseline on this branch before any M3 code: M1 suite **470 assertions, 9/9 suites green, all 13 AC PASS**; M2 suite **59 assertions green** with a live provider reachable. | new |

### What F3/F4/F8 change

The draft would have frozen a specification resting on a vendor flag its own vendor disclaims, and an
acceptance criterion (`fx01` reproduces) that is **empirically false**. Both are corrected below.

---

## Frozen decisions

**M3-D1 — The browser is a Diana-side verifier, not a Hermes tool.** Hermes's envelope stays exactly
`{read_file, search_files}`. No `browser_*` tool is added to `allowed_tools`, so **M3 introduces no
new Hermes dispatch surface**. Same shape as M1 D11, where the deterministic scanner is Diana-side
and Hermes cannot execute it.

**M3-D2 — The `ExecutionContract` does not change, at all.** The contract governs *what Hermes may
do*, and M3 changes that by nothing. `risk` stays `SAFE` and `depth` stays `D1` in the contract
because M1 D12 derives `risk` from the granted capability envelope and that envelope is unchanged.
`diana/runtime/contract.py` is **not modified by M3** — no new workflow, no relaxed `validate()`. The
deterministic scanner is Diana's capability and never appears in the contract either; the browser is
the same kind of thing.

**M3-D3 — `D2` is a property of the verification, not of the Hermes run.** M3 certifies one new
workflow class, `RUNTIME_VERIFIED_SECURITY_REVIEW → D2`, in an **M3-owned** depth map read only by
the runtime verifier, and records `depth: "D2"` in `runtime-verification.json`. M1 D13 (depth from a
certified workflow class) and M1 D14 (Hermes has no proposal channel) are unchanged and unweakened:
Hermes cannot reach this map, cannot name a workflow, and cannot promote itself to D2.

**M3-D4 — STATIC FINDING and RUNTIME VERIFICATION are separate documents.** The frozen
`ADVISORY_SECURITY_REVIEW` (13 keys) does not gain a field. Runtime results are written to a separate
`runtime-verification.json` in the Diana run directory, following the M2-D5 precedent. The static
finding is **immutable input** and is never rewritten.

**M3-D5 — Runtime verification may never overwrite, erase, or retroactively downgrade a static
finding.** A runtime record *references* a finding by `rule_id`, `file`, `line`; it never contains an
editable copy. A failed reproduction means **`NOT_REPRODUCED` under this bounded runtime** — never
"finding disproven". The deterministic scanner remains the sole owner of `findings[]` (M1 D1).
F8 makes this concrete rather than theoretical: M1's own `fx01` finding is real and does not
reproduce.

**M3-D6 — Three outcomes, and `INCONCLUSIVE` is a first-class result.** `REPRODUCED`,
`NOT_REPRODUCED`, `INCONCLUSIVE`. Browser launch failure, timeout, navigation error, driver crash, or
unparseable driver output is `INCONCLUSIVE` — never silently folded into `NOT_REPRODUCED`, because
"we could not test it" and "we tested it and it did not fire" are different facts.

**M3-D7 — Diana derives the outcome; the driver reports only observations.** The Node driver returns
primitive observations — injected-node count, whether the payload's own handler executed, the sink
element's resulting `innerHTML`, the navigation result, the proxy ledger. It **may not** name an
outcome. Diana computes `REPRODUCED` iff an injected node was observed in the live DOM **and** the
payload's handler executed. A driver-supplied `outcome`, `depth`, `severity` or `rule_id` field is a
schema violation that yields `INCONCLUSIVE`, not a value that is honoured. This mirrors M1 D11: the
judgment is Diana-side.

**M3-D8 — Egress is bounded by a Diana-owned proxy, which is the authoritative boundary.** Every
verification runs the browser behind a Diana process that refuses every request whose host:port is
not the single allowlisted target origin, with `--proxy-bypass "<-loopback>"` so loopback cannot
slip past it (F6). The proxy is Diana's own code, fails closed on any parse failure, and keeps an
**authoritative ledger** of every request it saw and its decision. Unlike a Hermes-supplied tool-call
ledger (M1 D25's problem), Diana wrote this one and it observes the enforcement point itself.

**M3-D9 — Vendor origin flags are defence in depth, never the boundary.** `--allowed-origins` is
still passed, because it produces a clean client-side refusal for direct navigation that the proxy
cannot (the proxy can only answer `403`, which the browser renders as a page). But per F3 it is
**vendor-disclaimed** and per F4 it is **behaviorally defeated by a redirect**, so no acceptance
criterion may rest on it alone. The browser also runs `--headless --isolated --block-service-workers`
and **never** `--allow-unrestricted-file-access`.

**M3-D10 — The enforcement claim is "no bytes crossed", not "the navigation errored".** F4 showed a
redirect whose navigation the tool reported as success while Diana's proxy refused the actual fetch.
Asserting on the tool's error status would therefore be asserting on the wrong thing. M3 asserts on
the Diana proxy ledger plus the observed page content: **no non-allowlisted origin was served, and
no content from one reached the page.**

**M3-D11 — The target is served read-only from an ephemeral loopback port.** Diana starts a static
server bound to `127.0.0.1:0` that serves only files inside the contract's `read_scope`, resolving
every request path with the same canonicalize-then-contain discipline as M1 C2. It implements `GET`
and `HEAD` only; every other method is refused before any filesystem access. There is no write path
to reach.

**M3-D12 — The attacker-controlled source value is delivered through the channel the finding names.**
Reproduction requires driving the finding's own source. Per F8/F9, `window.name` is the source in M1
D8's closure that survives into the page uninterpreted, and Diana sets it via `--init-script` before
the page's own scripts run. Diana supplies only the *source value*; the injected node appears because
the **target's own code** moves it into an HTML sink. The control in M3-AC-2 — identical payload,
identical delivery, safe sink, no injection — is what proves this is the target's behavior and not
Diana's.

**M3-D13 — Every runtime run is bounded and termination is proven by observation.** A wall-clock
deadline applies to the whole verification. Per F10, the browser is spawned **detached as a process
group leader** and terminated by signalling the process group; acceptance asserts the descendants are
**observably gone**, not that a kill was requested. Cleanup also stops the static server and the
proxy.

**M3-D14 — Evidence binds to target, run, and contract.** `runtime-verification.json` records
`run_id`, `contract_digest`, target `repo_root` and `git_commit`, the referenced finding's
`rule_id`/`file`/`line`, the exact URL and payload used, the `runtime_envelope`, the proxy ledger,
the observations, the derived outcome, and artefact paths. A record that does not bind is not
evidence.

**M3-D15 — Runtime output cannot masquerade as static or certified evidence.** `runtime-verification
.json` must be **rejected by `artifact.validate()`** and must classify **`MALFORMED`** under
`diana/security/evidence_model.py`, exactly as the advisory document must (M1 D35). Structural, not
by naming convention, and asserted by test. At D2 the payload **is** recorded — M1 D3 forbade payload
strings at D1 because nothing had run them, and a payload that actually executed in a real browser is
the opposite of an assertion dressed as evidence.

**M3-D16 — M3 carries its own later-milestone regression invariant**, per the M2-D14 pattern, rather
than reinterpreting any earlier milestone's historical evidence:

- **M3-REG-1** — the frozen M1 and M2 specifications remain **byte-identical**.
- **M3-REG-2** — pre-existing Diana/AO modules and behavior remain unchanged, **except where a future
  milestone explicitly freezes and proves a replacement**. M3 freezes no replacement, so its
  permitted-replacement set is **empty**. Per M3-D2, M3 additionally modifies **no M1-owned module**:
  its code is new files under `diana/runtime_verify/`.
- **M3-REG-3** — nothing is deleted by later work.
- **M3-REG-4** — all reusable M1 **and** M2 behavioral acceptance tests remain green.

---

## Acceptance criteria

M3 is accepted only if **all** hold, and **all 13 M1 criteria and all 13 M2 criteria remain green
unchanged**.

| # | Criterion |
|---|---|
| **M3-AC-1** | A planted DOM XSS is **REPRODUCED** in a real Chromium: an injected node is observed in the live DOM **and** the payload's own handler is observed to have executed. |
| **M3-AC-2** | The safe fixture — **identical payload, identical delivery, `textContent` sink** — is **NOT_REPRODUCED**, with no injected node and no handler execution, proving the harness does not fire spuriously and that the injection is the target's behavior. |
| **M3-AC-3** | M1's own `fx01` `DOM-XSS-001` finding is **NOT_REPRODUCED** under this bounded runtime (F8), and the record says exactly that — not that the finding is disproven. |
| **M3-AC-4** | A direct navigation to an origin outside the allowed target origin is blocked, and Diana's proxy ledger records the refusal. |
| **M3-AC-5** | A **302 redirect** from the in-scope origin to an off-origin host does **not** result in any content being served from that host: Diana's proxy refuses it and the page does not contain the external content. This is the case F4 proved `--allowed-origins` does not cover. |
| **M3-AC-6** | An arbitrary external network destination is blocked as a **sub-resource** (`<img>`) **and** as a script-initiated `fetch()` from an in-scope page, not only as a top-level navigation. |
| **M3-AC-7** | A second loopback origin that Diana did not authorize is not served to the browser, proving confinement is to the **target origin**, not merely to "localhost". |
| **M3-AC-8** | Diana's proxy ledger shows **every** request the browser made, including browser-vendor telemetry (F7), each with an explicit allow/refuse decision, and every allowed entry is the single target origin. |
| **M3-AC-9** | The runtime capability cannot mutate repository files: the target is byte- and git-state-identical after verification, and the static server refuses every non-`GET`/`HEAD` method **and** path traversal, before any filesystem access. |
| **M3-AC-10** | A timeout **actually terminates** browser activity: the observed Chromium descendants of the driver are gone afterwards, and the static server and proxy are stopped. |
| **M3-AC-11** | `runtime-verification.json` binds `run_id`, `contract_digest`, target `repo_root` and `git_commit`, and the referenced finding. A record failing to bind is rejected. |
| **M3-AC-12** | Malformed or hostile driver output cannot masquerade as evidence: a driver that reports an `outcome`, a `depth`, a `severity`, or unknown fields yields **`INCONCLUSIVE`**, and `runtime-verification.json` is **rejected by `artifact.validate()`** and classifies **`MALFORMED`** under `evidence_model`. |
| **M3-AC-13** | `NOT_REPRODUCED` never mutates, removes, or downgrades the static finding: the `ADVISORY_SECURITY_REVIEW` is **byte-identical** with and without runtime verification. |
| **M3-AC-14** | Depth `D2` comes only from the M3-owned certified workflow map; the `ExecutionContract` is still exactly `SAFE`/`D1`, and every channel Hermes has (observations, driver output) refuses an attempt to set depth, risk, or severity. |
| **M3-AC-15** | Hermes's envelope is unchanged — `{read_file, search_files}` — and **all 18** `browser_*` tools are refused through the real dispatch path, with a legitimate tool still allowed in the same process. |
| **M3-AC-16** | M3-REG-1…4 hold: M1 and M2 frozen specs byte-identical, no M1-owned or pre-existing module modified, nothing deleted, and all M1 and M2 suites green. |

---

## Carried assumptions

- Node ≥ 20 and Playwright browsers are present. Absent either, M3 acceptance **cannot run** and must
  report that rather than substituting a simulated browser.
- `@playwright/mcp` is resolved from the npx cache or fetched by `npx` on first use; this is
  Playwright's requirement, not Diana's.
- Reproduction proves a vulnerability **class** under **one** bounded configuration — one browser
  engine, one payload, one delivery channel. `NOT_REPRODUCED` is evidence about that configuration
  only, and never about the finding's validity (M3-D5).
- The proxy bounds **egress the browser initiates**. It is not a sandbox: it does not constrain what
  the Chromium process could do outside its network stack. M3 claims a network boundary, not
  containment.
