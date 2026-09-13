# Hermes Runtime — Milestone 1 (FROZEN)

Status: **normative and frozen**. This document is the source of truth for Milestone 1 and remains normative
after implementation begins: every checkpoint diff is reviewed against it.

Architectural decisions **D1-D38 are frozen** as of commit `2b26f7bfcaa41b3b068635bb207ca807b2967964`.
They may not be changed, reopened, reinterpreted, or weakened by implementation work. Only the
**Implementation constants** section below may be extended, and only with values that implement an
existing decision rather than alter one. Revising D1-D38 requires a new design round, not an edit.

Branch: `feature/hermes-runtime`.
Architecture frozen: 2026-09-13.
Implementation constants finalized: 2026-09-13.

---

## Product North Star

> The final Diana × Hermes architecture must support **unattended bounded execution**: a user can approve a defined execution envelope once, leave the run unattended for hours, and return to completed work or explicit blocked items without granting Hermes unrestricted authority.

M1's `SAFE`/`D1`, read-only, two-tool envelope is a **proof of the enforcement model, not the final capability ceiling**. The enforcement boundary chosen here — policy consulted at the single tool-dispatch entry point — is expected to be extended in later milestones from tool-name allowlisting to per-tool argument, path, and command policy, covering bounded writes, shell/test execution, and autonomous multi-hour work. The boundary does not get replaced; it gets a richer policy.

M1's purpose is **not** to prove the whole Hermes migration works. It is to determine whether one D1 read-only advisory security review can be made **measurably better** than Diana's current behavior **without weakening Diana's invariants**.

---

## Empirical Phase 0 baseline

Tiny static HTML/CSS/JS fixture containing `innerHTML = location.hash`:

- **Diana Preflight** — 11 checks: 5 PASS, 5 SKIP, blocker on missing build/test evidence.
- **Diana Gate** — `FAIL`, reason: `blocker preflight failed: build-test-evidence-present`.
- **The planted DOM XSS is not identified.**

Security Track against Diana itself: 75/75 controls `UNPROVEN`, reducer `REQUIRE_HUMAN` (37 applicability `UNKNOWN`, 33 no verification runs, 5 partial evidence / missing another required verifier). Repo applicability alone cannot make the Security Track PASS.

The D1 advisory review is deliberately **not** Diana Gate evidence, **not** a Security Track bundle, **not** release certification, and **not** PR evidence. It is a separate proportional advisory artifact.

---

## Decision log

Only decisions actually resolved.

| # | Decision | Resolution |
|---|---|---|
| D1 | Who finds the XSS | **Two channels, separately graded.** Deterministic Diana-side scanner owns `findings[]` and is the only graded producer. Hermes output lands in `unverified_observations[]` — **no severity field**, ungraded, cannot affect PASS/FAIL. |
| D2 | M1's thesis | **Not** "Hermes finds bugs Diana misses." The security delta comes from deterministic Python; Hermes is the **runtime/orchestration substrate** under test — contract enforcement, read-only behavior, NL routing. |
| D3 | Evidence bar | `rule_id`, `severity`, `file`, `line` (sink, 1-indexed), `source{kind,line,text}`, `sink{kind,line,text}`, `flow`, `code_excerpt` (verbatim). **No payload string. No browser/runtime proof** — those define D2+. An unexecuted payload must never be presented as evidence. |
| D4 | Flow analysis | **DIRECT only** — source expression syntactically inside the sink's assignment statement. `flow: "DIRECT"` in schema v1 for extension. Indirect/dataflow is a declared limitation. |
| D5 | Sanitizers | Closed allowlist = **`DOMPurify.sanitize` only**. Suppression by **enclosing call expression**, never by name-on-line. `encodeURIComponent`/`encodeURI` rejected (URL encoders, not HTML sanitizers); `escapeHtml` rejected (unbound name, no deterministic proof of implementation). Suppressed candidates stay **visible** with file/line/reason. |
| D6 | Lexing | Real lexical state distinguishing code from comments and quoted strings. **Not regex.** Template literals: `NOT_ANALYZED`, pinned by fixture. |
| D7 | Rule inventory | **One rule, `DOM-XSS-001`.** Secrets, external scripts, `eval`/`Function`, event-handler injection → `NOT_CHECKED` with reasons, never silently absent. |
| D8 | Closure | Sources: `location.hash`/`search`/`href`, `document.URL`, `document.referrer`, `window.name`. Sinks: `.innerHTML`, `.outerHTML`, `document.write(`, `document.writeln(`, `.insertAdjacentHTML(`. |
| D9 | Mutation | **Strictly read-only.** Hermes never writes the artifact; Diana's outer process constructs and persists it **outside** the target repo. Target repo byte- and git-state-identical before/after. |
| D10 | Tool surface | Closed structured-read allowlist: **`read_file`, `search_files`** — matching Hermes's own `_READ_SEARCH_TOOLS` (`model_tools.py:611`). **No shell**, no write/patch, no git, no network, no browser, no `execute_code`, no `delegate_task`. Unknown/future tool names denied. Deciding read-only-ness of shell commands is an unwinnable parsing game and is deleted from M1. |
| D11 | Scanner location | **Diana-side.** Hermes cannot execute it. |
| D12 | Risk authority | `risk` is a **pure function of the granted capability envelope**, never of task intent. Read-only + no network + no shell + no delegation ⇒ `SAFE`. |
| D13 | Depth authority | `depth` comes from the **certified workflow class**. `ADVISORY_SECURITY_REVIEW` ⇒ `D1`. A read-only envelope alone could serve D0 or D1, so capability does not determine depth. |
| D14 | Hermes's say | **None.** No proposal channel for `risk` or `depth`. Hermes's structured output schema is **closed**; unknown fields **fail validation** rather than being recorded as anomalies (an anomaly log is a side channel). |
| D15 | Depth mismatch | M1 executes **only** `SAFE`/`D1`. Anything else ⇒ **BLOCKED**, never downgraded, never attempted. |
| D16 | Contract fields | 11 keys. **Deleted:** `mutation_policy`, `network_policy` (derivable from `allowed_tools`; duplicating creates a disagreement surface), `evidence_requirements` (drift-prone documentation nothing reads). **Merged:** `goal`+`intent` → non-enforcing `task`. **Added:** `read_scope`. `repo_profile` embedded once. `target.dirty` allowed and recorded. |
| D17 | Threat model | **Egress-by-transcript is in scope** for unintended reads outside the authorized target and denied sensitive subpaths. Enforcement after canonicalization and symlink resolution, **not** string-prefix. Explicitly **out of scope**: secrets embedded in otherwise-authorized source (needs a separate redaction / secret-classification subsystem). |
| D18 | Integration mode | **In-process embedding.** Prevention cannot rest on Hermes's `pre_tool_call` dispatch, which is fail-open by design (`agent/tool_executor.py:636`, *"Hook failures never block"*). |
| D19 | Prevention boundary | Enforcement at the **real tool boundary, before bytes are returned** — not post-hoc. Detection after bytes reach the model is not prevention. |
| D20 | Confinement patch | **`tools/file_tools_paths._resolve_path_for_task`**, single choke point, module-global (thread-safe). Not duplicated at tool entry points. Host resolution already dereferences symlinks (`_anchor`, `:141-159`); the workspace root is **not** a confinement boundary — absolute inputs are returned *resolved-but-unanchored*, so confinement must be created, not merely hardened. |
| D21 | Capability patch | **`model_tools.handle_function_call`** — proven sole entry (`_execute_tool` one call site `:947`; `registry.dispatch` one production call site `:830`; `entry.handler` only at `tools/registry.py:820,822`). `registry.dispatch` alone is **insufficient**: the connector branch (`model_tools.py:827`) routes around it on a name-prefix claim (`tools/tool_gateway/names.py:71` — *"A claim, not a guarantee"*). The `tool_search` unwrap bypasses Hermes's internal scope check, not this function, and re-enters at `tool_executor.py:1535` with the real tool name. |
| D22 | Invariant | `tool_name ∈ contract.capability_envelope.allowed_tools` **or the call is blocked before its handler executes.** The global registry may keep every tool; existence is irrelevant if dispatch cannot reach them outside the contract. |
| D23 | Safe mode | **`HERMES_SAFE_MODE=1`** — inverting the original control. It removes plugin discovery (`hermes_cli/plugins.py:1210`), user shell hooks (`agent/shell_hooks.py:145`), MCP config (`tools/mcp_tool_config.py:325`), and outbound webhooks (`agent/outbound_webhooks.py:78`) from the TCB. The Diana plugin / `pre_tool_call` hook is **removed from M1 entirely**. |
| D24 | Thread whitelist | **`set_thread_tool_whitelist` deleted from M1.** `threading.local()` (`hermes_cli/plugins.py:1753`) + pool-worker dispatch (`agent/tool_executor.py:1332`, and `:856` for the sequential path) + no `contextvars` propagation ⇒ inert on the execution path. Recording it as defense-in-depth would misrepresent a dead mechanism as a control. |
| D25 | Reconciliation | **Deferred.** Introduced to detect failure of a lifecycle hook M1 no longer has; it cannot prevent an out-of-scope read. No second monkeypatch. The session-DB cancellation-retrievability question is consequently moot for M1. |
| D26 | Self-test | **Hard gate.** Runs through the **real agent/executor path on the actual pool-worker thread**, using a scripted/mocked model turn that emits disallowed names — never by direct function call, never relying on the model politely not asking. Any deviation ⇒ BLOCKED, no Hermes invocation, no artifact. |
| D27 | Version pin | Mandatory, but the **behavioral self-test is the enforcement tripwire** — an upstream rename breaks it loudly instead of silently disabling confinement. |
| D28 | `approvals.mode` | **Demoted to recorded context.** With only two confined read tools reachable, approval behavior is not on the security path and must not be mislabeled as a control. |
| D29 | `.hermes.md` / `AGENTS.override.md` | **Context-integrity / reproducibility** controls, not capability controls. Still **BLOCK** if present — they cannot expand the capability envelope but can alter run semantics. |
| D30 | Subagents | `delegate_task` is off the allowlist ⇒ denied by the same rule, **no special case**. `subagent_auto_approve` subsumed. (`hermes_cli/config_defaults.py:1264` independently notes subagent threads always resolve approvals non-interactively.) |
| D31 | Directory listing | No `list_dir` tool exists in Hermes. **`repo_profile.py` supplies a filtered repo-relative file inventory** in the contract — obeying the same read scope, **no file contents**, no denied paths. |
| D32 | Status vocabulary | **Two arrays, two axes.** `coverage[]` = per-category `CHECKED \| NOT_CHECKED \| NOT_APPLICABLE \| APPLICABILITY_UNKNOWN`. `unsupported_constructs[]` = concrete constructs **encountered this run**, with file+line. Global detector limits live in `limitations[]`. No `PARTIALLY_ANALYZED`. |
| D33 | `NOT_APPLICABLE` | Must be **earned** by deterministic evidence, **and** requires that the scoped inventory be established complete with the claim limited to code in the reviewed repo. If completeness or scope is uncertain ⇒ `APPLICABILITY_UNKNOWN`. |
| D34 | Artifact shape | **Deleted:** `checks_performed`, `skipped` (subsumed by `coverage[]`), top-level `evidence` (evidence is a property of a finding, not a peer). **Added:** `scan_issues[]`. Contract embedded verbatim once + `contract_digest`. No disclaimer field, no Markdown representation in M1. |
| D35 | Anti-masquerade | **Structural, not naming.** `diana/security/evidence_model.py:163` `ALLOWED_RUN_FIELDS` is an allowlist — an `ADVISORY_SECURITY_REVIEW` fed to it must classify `MALFORMED`, asserted by test. |
| D36 | Outcome semantics | `COMPLETE` **iff** `scan_issues[]` **and** `unsupported_constructs[]` are both empty. `INCOMPLETE` iff the trusted run completed correctly but one or more concrete in-scope items could not be analyzed. `BLOCKED` is **never a document value** — a blocked run produces no artifact, only a Diana run record. |
| D37 | Failure semantics | Any TCB/process failure ⇒ **BLOCKED, no artifact**, even when `findings[]` would have been sound. Shipping an advisory from a run with an unverified envelope is the "artifact from an unproven process" pattern the Security Track exists to reject. |
| D38 | "Evidence insufficient" | **Structurally impossible** for `findings[]` — the match that produces a finding produces its evidence. |

---

## Frozen scope

**In**

1. Branch `feature/hermes-runtime`.
2. Recorded empirical Phase 0 baseline on the tiny fixture.
3. `diana/adapters/hermes.py` — fail-closed `check()` over the final TCB list.
4. `diana/adapters/hermes_patches.py` — the two patches + self-test harness.
5. `ExecutionContract` (11 keys) + canonical JSON serialization + digest.
6. `diana/profile/repo_profile.py` — deterministic, generous with `APPLICABILITY_UNKNOWN`.
7. `diana/advisory/dom_scan.py` — lexer + `DOM-XSS-001`.
8. `diana/advisory/artifact.py` — `ADVISORY_SECURITY_REVIEW` v1.
9. Fixture repo (8 cases) + 8 preflight-failure fixtures + acceptance suite.
10. `"Check security project ini"` routes end-to-end against the fixture.

**Out**

Slash command, `ship.py` gate, Security Track bundle, PR, actor/reviewer ceremony, Markdown renderer, mutation of any kind, subagents, shell, network, secrets / external-script / `eval` detectors, dataflow analysis, payload or runtime proof, per-call reconciliation, AO changes.

---

## Trusted computing base

Each failure ⇒ **BLOCKED with its own specific reason code**.

| Control | Evidence anchor |
|---|---|
| Hermes reachable | — |
| Hermes **version pin** matches | `hermes-agent 0.21.1` @ `b8e8639445bd6f05a8141abcea7ae2aa8279f2b7` - see Implementation constants |
| `HERMES_SAFE_MODE == 1` | `plugins.py:1210`, `shell_hooks.py:145`, `mcp_tool_config.py:325`, `outbound_webhooks.py:78` |
| `background_review.enabled == false` | default is **`True`** — `hermes_cli/config_defaults.py:744` |
| side-question auxiliary execution disabled | `agent/side_question.py:111-127` |
| no `.hermes.md` | context integrity |
| no `AGENTS.override.md` | context integrity |
| **capability patch live** — `model_tools.handle_function_call` | proven behaviorally |
| **confinement patch live** — `_resolve_path_for_task` | proven behaviorally |
| contract digest + `run_id` bind | `~/.diana/runs/<run_id>/` (0700), `contract.json` (0600) |
| derived contract is exactly `SAFE`/`D1` | D12 / D13 / D15 |

**Two monkeypatches + version pin, as one unit.** Recorded context only: `approvals.mode`.

---

## ExecutionContract v1

```json
{
  "contract_version": 1,
  "run_id": "<uuid>",
  "created_at": "<iso8601>",
  "workflow": "ADVISORY_SECURITY_REVIEW",
  "depth": "D1",
  "risk": "SAFE",
  "task": "Check security project ini",
  "target": {"repo_root": "...", "git_commit": "...", "dirty": false},
  "capability_envelope": {"allowed_tools": ["read_file", "search_files"]},
  "read_scope": {"allowed_roots": ["..."], "denied_subpaths": [".git/", ".env", ".env.*"]},
  "repo_profile": {"inventory": ["..."], "categories": []}
}
```

- Stored at `~/.diana/runs/<run_id>/contract.json` (dir `0700`, file `0600`).
- `DIANA_RUN_ID` and `DIANA_CONTRACT_DIGEST` carried in the environment.
- Canonical JSON serialization defined before hashing, so digest verification is deterministic.
- `allowed_tools` is the **single source of truth** for the capability envelope.
- `task` is recorded for provenance and is **explicitly non-enforcing**.

---

## ADVISORY_SECURITY_REVIEW v1

```json
{
  "document_type": "ADVISORY_SECURITY_REVIEW",
  "schema_version": 1,
  "run_id": "...",
  "outcome": "COMPLETE | INCOMPLETE",
  "contract": {},
  "contract_digest": "sha256:...",
  "findings": [{
    "rule_id": "DOM-XSS-001",
    "severity": "HIGH",
    "file": "app.js",
    "line": 14,
    "source": {"kind": "location.hash", "line": 14, "text": "location.hash"},
    "sink":   {"kind": "innerHTML", "line": 14, "text": "el.innerHTML = location.hash"},
    "flow": "DIRECT",
    "code_excerpt": "  el.innerHTML = location.hash;"
  }],
  "suppressed": [
    {"file": "app.js", "line": 9, "rule_id": "DOM-XSS-001", "reason": "enclosed in DOMPurify.sanitize"}
  ],
  "coverage": [
    {"category": "dom_xss", "status": "CHECKED", "rule_ids": ["DOM-XSS-001"]},
    {"category": "secrets_in_source", "status": "NOT_CHECKED", "reason": "no detector implemented at D1"},
    {"category": "external_scripts", "status": "NOT_CHECKED", "reason": "no detector implemented at D1"},
    {"category": "code_injection", "status": "NOT_CHECKED", "reason": "eval/Function out of M1 scope"},
    {"category": "sqli", "status": "NOT_APPLICABLE", "reason": "no server-side code or DB client in a complete scoped inventory"},
    {"category": "authn_authz", "status": "APPLICABILITY_UNKNOWN", "reason": "static inventory cannot rule out a hosted backend"}
  ],
  "unsupported_constructs": [
    {"file": "app.js", "line": 22, "construct": "template_literal", "reason": "template-literal flows are NOT_ANALYZED in M1"}
  ],
  "scan_issues": [
    {"file": "...", "kind": "READ_FAILED | DECODE_FAILED | FILE_TOO_LARGE", "detail": "..."}
  ],
  "limitations": ["..."],
  "unverified_observations": []
}
```

Contains **no** `control_id`, `applicability`, `verifier`, `evidence`, `tool_error`, or `observed_at` key at any nesting level. `unverified_observations[]` carries **no severity field**.

---

## Implementation constants

Values that implement frozen decisions. Adding or correcting a constant here is a documentation change;
it must never be used to alter D1-D38.

### C1 - Hermes version pin (implements D27)

| Field | Value |
|---|---|
| Package | `hermes-agent` |
| Version | `0.21.1` (`pyproject.toml:5`, `hermes_cli/__init__.py:6`) |
| Git commit | `b8e8639445bd6f05a8141abcea7ae2aa8279f2b7` |
| Branch at pin time | `main`, working tree clean |
| Install path | `~/.hermes/hermes-agent` |

`check()` compares **both** the declared version string and the resolved git commit. Either mismatching is a
distinct BLOCKED reason code. Per D27 the pin is mandatory but the behavioral self-test remains the actual
enforcement tripwire: an upstream rename of a patch target breaks the self-test loudly even when the pin matches.

### C2 - `read_scope` matching semantics (implements D17, D20)

All matching happens on the **fully canonicalized absolute path** (`os.path.realpath`, symlinks dereferenced),
never on the path as supplied. String-prefix comparison is prohibited.

**Allowed-root test.** A candidate path is in scope iff, after canonicalization, it equals one of
`allowed_roots` or is a descendant of one, compared **path-component-wise** on the canonicalized forms.
Component-wise comparison is required so that `/home/u/repo-evil` is not treated as inside `/home/u/repo`.

**Denied-subpath rules.** Each entry of `denied_subpaths` is exactly one of two forms, decided by whether it
ends with `/`:

| Form | Example | Semantics |
|---|---|---|
| Directory rule - ends with `/` | `.git/` | Denied if **any** path component of the canonicalized path, relative to its matched allowed root, is exactly equal to the rule with the trailing `/` removed. Applies at any depth, so nested `.git` directories (submodules) are denied too. |
| Name-glob rule - contains no `/` | `.env`, `.env.*` | `fnmatch.fnmatchcase` is applied to **each** path component of the relative path independently. Denied if any component matches. `.env` matches a component named exactly `.env`; `.env.*` matches `.env.local`, `.env.production`, and not `.env`. |

An entry containing an interior `/` is a configuration error and is rejected at contract construction, so the
two forms above are exhaustive.

**Evaluation order and defaults.**

1. Canonicalize. If canonicalization raises, the path is **denied**.
2. If the canonicalized path is not under any allowed root, **deny**. This is what makes a symlink whose target
   escapes the root deny rather than allow - the escape is visible only after canonicalization.
3. If any denied-subpath rule matches, **deny**. Deny always wins over allow.
4. Otherwise, allow.

Matching is byte-exact and case-sensitive (POSIX). For a path that does not exist, canonicalization resolves the
existing ancestor chain; if that resolution fails or escapes an allowed root, the path is denied. `read_file` and
`search_files` obey exactly the same rules, per D17.

### C3 - `FILE_TOO_LARGE` threshold (implements D36 `scan_issues[]`)

**Threshold: 512,000 bytes**, measured as `os.stat().st_size` on disk **before** any decode attempt.

The enum is retained rather than removed, and the value is taken from code evidence rather than invented:
Hermes's own large-file constant is `_LARGE_FILE_HINT_BYTES = 512_000` (`tools/file_tools.py:116`), the point at
which Hermes tells a caller a file is large; its per-read budget is `_DEFAULT_MAX_READ_CHARS = 100_000`
(`tools/file_tools.py:47`). Aligning Diana's scanner ceiling with Hermes's own notion of "large" keeps the two
components' file-size semantics consistent.

A threshold is justified on scanner grounds independently: `dom_scan.py` is a whole-file lexer (D6), and minified
or bundled JavaScript routinely exceeds several hundred kilobytes on a single line, where lexing cost is high and
the result is not meaningfully analyzable.

Behavior: a file strictly greater than the threshold is **not scanned**, produces exactly one `scan_issues[]`
entry of kind `FILE_TOO_LARGE` carrying the observed byte size, and therefore forces `outcome: INCOMPLETE` per
D36. It is never silently skipped and never reported as analyzed.

---

## Fixture matrix — 8 cases, one rule

| # | Fixture | Expected |
|---|---|---|
| 1 | `el.innerHTML = location.hash` | `DOM-XSS-001` **HIGH**, sink line cited · `COMPLETE` |
| 2 | `document.write(location.search)` | `DOM-XSS-001` **HIGH** (proves closure is not one hardcoded pair) · `COMPLETE` |
| 3 | `el.textContent = location.hash` | no finding (sink not in set) · `COMPLETE` |
| 4 | `el.innerHTML = "<b>Hello</b>"` | no finding (no source) · `COMPLETE` |
| 5 | `// el.innerHTML = location.hash` | no finding (comment) · `COMPLETE` |
| 6 | `log("el.innerHTML = location.hash")` | no finding (string literal) · `COMPLETE` |
| 7 | `el.innerHTML = DOMPurify.sanitize(location.hash)` | **suppressed** with file+line+reason, **not** in `findings[]` · `COMPLETE` |
| 8 | ``el.innerHTML = `${location.hash}` `` | no finding · **`INCOMPLETE`** · exactly one `unsupported_constructs[]` entry at the expected file/line |

Fixture 8 pins the declared template-literal limitation by test, so documentation cannot silently go stale.

---

## Preflight-failure matrix — 8 cases

Each must produce **BLOCKED with its own specific reason code**; a single over-broad check passing all eight proves nothing about any of them.

1. `HERMES_SAFE_MODE != 1`
2. `background_review.enabled == true`
3. side-question auxiliary execution enabled
4. `.hermes.md` present
5. `AGENTS.override.md` present
6. Hermes version-pin mismatch
7. capability patch not live
8. confinement patch not live

---

## Acceptance criteria

**Runtime safety**

1. All 8 preflight-failure fixtures ⇒ BLOCKED, each with its distinct reason code; the clean config passes.
2. Self-test through the **real agent loop on the actual pool-worker path**: `read_file` allowed executes · `search_files` allowed executes · crafted `write_file` blocked **before handler execution** · unknown synthetic tool name blocked · allowed `read_file` outside `allowed_roots` blocked by confinement · readable Diana-created sentinel outside roots denied · in-repo symlink escaping the root denied · `.env` denied · `search_files` in-scope allow and out-of-scope deny.
3. Repo immutability: SHA-256 of every path plus `git status --porcelain`, byte-identical before/after.
4. Artifact persisted **outside** the target repository.

**Non-execution — instrumented, not inferred**

5. Spies installed at the actual call sites in `diana/gate/*`, `diana/ship/*`, the Security Track bundle builder, and `evidence_model.record_*` record **zero invocations**. Non-execution must not be inferred from the absence of resulting files.
6. No `git` / `gh` mutation subprocess is spawned.

**Detection**

7. All 8 fixtures produce exactly their expected findings, suppressions, outcomes, and `unsupported_constructs[]` entries, with exact pinned severity.
8. Determinism: across two runs, `findings`, `suppressed`, `coverage`, `unsupported_constructs`, `scan_issues`, `limitations`, and deterministic `repo_profile` content are **byte-identical**. `contract_digest` **differs between runs and validly recomputes for each** — it covers `run_id` and `created_at`, so a stable digest would be a defect. (Equivalent alternative: normalize `run_id`, `created_at`, `contract.run_id`, `contract.created_at`, then recompute the normalized digest before comparison.)

**Integrity**

9. `contract_digest` recomputes exactly from the embedded contract under the canonical serialization.
10. The artifact fed to `evidence_model` classifies `MALFORMED`.
11. Hermes structured output containing an unknown field fails validation rather than producing an anomaly record.

**Delta**

12. Same fixture, both paths, asserted:
    - existing Diana path ⇒ Gate `FAIL: blocker preflight failed: build-test-evidence-present`, DOM XSS **not** reported;
    - M1 D1 advisory path ⇒ `DOM-XSS-001`, `HIGH`, exact expected sink line, no Gate, no Security Track, no PR.

**Regression**

13. Existing AO path and existing regression suite pass unchanged; `git diff` touches only new M1 paths.

---

## Carried assumptions — not yet empirically verified

Each blocks implementation step 9; none blocks steps 1–8.

- Diana can embed Hermes in-process and drive a scripted turn.
- Both patches can be installed ahead of the import order that binds those names.
- `background_review` and side-question execution are disableable by configuration rather than only by patch.

Steps 6 and 7 self-tests will confirm the second.

---

## Implementation order

1. Pin the Hermes version; record the Phase 0 baseline as a test fixture (the "before" half of AC-12).
2. `ExecutionContract` + canonical serialization + digest + run directory.
3. `repo_profile.py` — inventory and `coverage[]`, positive-evidence rule for `NOT_APPLICABLE`.
4. `dom_scan.py` — lexer + `DOM-XSS-001` + the 8 fixtures. **Fully green before any Hermes code**, since AC-7 and AC-8 do not depend on the runtime.
5. `artifact.py` + AC-9 and AC-10 (structural incompatibility, digest integrity).
6. Confinement patch + its self-test slice.
7. Capability patch at `handle_function_call` + its self-test slice.
8. `hermes.py check()` + the 8 preflight-failure fixtures.
9. In-process run wiring, closed Hermes output schema, `unverified_observations[]`.
10. AC-3, AC-4, AC-5, AC-6, AC-12, AC-13 end-to-end.
