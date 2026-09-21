#!/usr/bin/env bash
# Regression coverage for the PRODUCTION Hermes/Reviewer path.
#
# Every case here exists because a real Diana run against a real repository
# failed in a way the accepted M2/M6/M7 suites did not cover. The run used an
# OAuth-backed provider, the builder completed inside its envelope, Diana's own
# deterministic verification passed, the reviewer turn ran for 99 seconds with
# no error and no timeout -- and the run stopped with `review-verdict-absent`,
# with nothing in the turn record able to say why.
#
#   A  an OAuth-backed provider has no environment API key and was unreachable.
#   B  the verdict was parsed out of a 2000-character BOUNDED OBSERVATION, so a
#      valid verdict longer than the bound arrived cut in half.
#   C  the reviewer turn was built with narrow=False, so a READ-ONLY actor was
#      shown `patch` and `write_file`.
#   D  the product CLI handed the production reviewer HARDCODED evidence:
#      paths_touched [], within_envelope None, verification_passed True.
#   E  the evidence, once real, resolved to `attempts[-1]` -- which during a
#      reviewer turn is the REVIEWER's own in-flight attempt.
#   F  the verdict was extracted with a greedy `\{.*\}`.
#   G  builder -> reviewer -> builder must still converge, and
#   H  every reviewer malfunction must still fail closed.
#
# No live model is used: the model call is replaced at Hermes's own `run_agent`
# seam, so Diana's real narrowing, threading, bounding, record-writing,
# projection installation, journal, reconciliation and verdict validation all
# run unmodified.
set -uo pipefail
MA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIANA_DIR="$(cd "$MA_DIR/.." && pwd)"
REPO_DIR="$(cd "$DIANA_DIR/.." && pwd)"
HERMES_HOME="${DIANA_HERMES_HOME:-$HOME/.hermes/hermes-agent}"
PY_BIN="python3"; [ -x "$HERMES_HOME/venv/bin/python3" ] && PY_BIN="$HERMES_HOME/venv/bin/python3"
[ -d "$HERMES_HOME" ] || { echo "SKIP  Hermes not installed at $HERMES_HOME"; exit 0; }
TMP_DIR="$(mktemp -d)"; trap 'rm -rf "$TMP_DIR"' EXIT
export HERMES_SAFE_MODE=1 DIANA_HERMES_HOME="$HERMES_HOME"
"$PY_BIN" - "$DIANA_DIR" "$TMP_DIR" "$REPO_DIR" "$HERMES_HOME" <<'PY'
import json, os, subprocess, sys, tempfile, textwrap, time, types, uuid
from pathlib import Path

diana, tmp, repo_dir, hermes_home = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
for sub in ("product", "multiactor", "unattended", "runtime", "mutation", "adapters", "profile"):
    sys.path.insert(0, str(Path(diana, sub)))
sys.path.insert(0, hermes_home)
import blocking, journal as J
import actors as A, executors as E, projection as P, verdict as VD
import hermes_live as HL, hermes_patches as HP
import product as PROD, recovery as RCV

passed = failed = falsifiers = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond is True: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")
def falsify(label, cond, extra=""):
    global falsifiers
    falsifiers += 1
    check("[falsifier] " + label, cond, extra)
def code_of(fn):
    try:
        fn(); return None
    except blocking.Blocked as exc:
        return exc.code

RUNS = Path(tempfile.mkdtemp(prefix="prod-reviewer-", dir=str(tmp)))
BROKEN = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b    # repaired\n"
FIXED2 = "def add(a, b):\n    return a + b    # repaired, again\n"

def fresh(name):
    root = tmp / f"r-{name}-{uuid.uuid4().hex[:6]}"
    (root / "src").mkdir(parents=True)
    (root / "src" / "calc.py").write_text(BROKEN)
    (root / "check.py").write_text(textwrap.dedent("""\
        import sys
        sys.path.insert(0, "src")
        from calc import add
        sys.exit(0 if add(2, 3) == 5 else 1)
        """))
    (root / ".gitignore").write_text("build/\n")
    g = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)
    g("init", "-q"); g("config", "user.email", "r@x"); g("config", "user.name", "r")
    g("add", "-A"); g("commit", "-qm", "init")
    return root

def verify(cb, item_id=None):
    return subprocess.run([sys.executable, "-B", "check.py"],
                          cwd=cb["target"]["repo_root"], capture_output=True).returncode == 0

def approve(root, **kw):
    kw.setdefault("allowed_commands", ("python3 check.py",))
    kw.setdefault("write_roots", (str(Path(root) / "src"),))
    kw.setdefault("max_attempts", 8); kw.setdefault("total_seconds", 1800)
    return A.approve(task="repair add so that add(2,3) == 5, and keep check.py passing",
                     repo_root=str(root), runs_base=str(RUNS), **kw)

def PASS_V(summary="the repair is present"):
    return {"decision": "PASS", "summary": summary, "findings": [],
            "dod_checks": [{"criterion": "add returns a + b", "result": "PASS",
                            "evidence": "src/calc.py line 2"}]}
def FAIL_V():
    return {"decision": "FAIL", "summary": "no regression test was added",
            "findings": [{"description": "no regression test", "evidence": "tests/ is absent"}],
            "dod_checks": [{"criterion": "a regression test exists", "result": "FAIL",
                            "evidence": "no test file was created"}]}

# ======================================================================
print("=== A — an OAuth-backed provider resolves through Hermes, and fails closed ===")

import hermes_cli.config as _hcfg
_REAL_LOADER = _hcfg.load_config_readonly
FAKE_HOME = str(tmp / "fake-hermes" / "hermes-agent")   # no .env anywhere near it
Path(FAKE_HOME).mkdir(parents=True, exist_ok=True)

def with_config(provider, model, fn):
    _hcfg.load_config_readonly = lambda *a, **k: {
        "model": {"provider": provider, "default": model, "base_url": ""}}
    try:
        return fn()
    finally:
        _hcfg.load_config_readonly = _REAL_LOADER

SECRET = "eyJhbGciOiJIUzI1NiJ9." + ("x" * 120) + ".signature-not-a-real-token"
calls = {"n": 0}
def resolver_ok():
    calls["n"] += 1
    return {"api_key": SECRET, "base_url": "https://chatgpt.com/backend-api/codex"}
def resolver_raises():
    calls["n"] += 1
    raise RuntimeError(f"no credentials; token was {SECRET}")
def resolver_empty():
    calls["n"] += 1
    return {"api_key": "   "}

_REAL_RESOLVER = HL.OAUTH_RESOLVERS["openai-codex"]
HL.OAUTH_RESOLVERS["openai-codex"] = resolver_ok
calls["n"] = 0
cfg = with_config("openai-codex", "gpt-5.6-sol",
                  lambda: HL.provider_config(FAKE_HOME))
check("A openai-codex with NO environment API key obtains its credential through "
      "Hermes's own auth resolver", calls["n"] == 1 and cfg["api_key"] == SECRET,
      f"(calls={calls['n']})")
check("A the RESOLVED credential is what the turn would use, and the resolver's "
      "base_url is adopted",
      cfg["provider"] == "openai-codex" and cfg["model"] == "gpt-5.6-sol"
      and cfg["base_url"] == "https://chatgpt.com/backend-api/codex")

HL.OAUTH_RESOLVERS["openai-codex"] = resolver_raises
calls["n"] = 0
raised = {}
def _grab():
    try:
        with_config("openai-codex", "gpt-5.6-sol", lambda: HL.provider_config(FAKE_HOME))
    except blocking.Blocked as exc:
        raised["code"], raised["detail"] = exc.code, exc.detail
_grab()
check("A a resolver that raises fails CLOSED with hermes-provider-unavailable",
      raised.get("code") == blocking.HERMES_PROVIDER_UNAVAILABLE, f"({raised.get('code')})")
check("A the refusal detail never carries the token, even when the underlying "
      "exception text did",
      SECRET not in raised.get("detail", "") and "<redacted>" in raised.get("detail", ""),
      f"({raised.get('detail')})")

HL.OAUTH_RESOLVERS["openai-codex"] = resolver_empty
raised.clear(); _grab()
check("A a resolver returning no usable credential fails closed too; Diana never "
      "substitutes a placeholder key",
      raised.get("code") == blocking.HERMES_PROVIDER_UNAVAILABLE, f"({raised.get('code')})")

calls["n"] = 0
HL.OAUTH_RESOLVERS["openai-codex"] = resolver_ok
key_code = code_of(lambda: with_config("some-keyed-provider", "m-1",
                                       lambda: HL.provider_config(FAKE_HOME)))
check("A provider validation is NOT weakened: an ordinary API-key provider with no "
      "key is still refused", key_code == blocking.HERMES_PROVIDER_UNAVAILABLE, f"({key_code})")
falsify("A and the OAuth resolver was never consulted for it, so the OAuth path is "
        "a closed per-provider map and not a general fallback", calls["n"] == 0,
        f"(calls={calls['n']})")
check("A the OAuth provider map is closed and explicit",
      tuple(HL.OAUTH_RESOLVERS) == ("openai-codex",), f"({tuple(HL.OAUTH_RESOLVERS)})")
HL.OAUTH_RESOLVERS["openai-codex"] = _REAL_RESOLVER

# ======================================================================
print("\n=== the model seam: Hermes's own run_agent, replaced. No live model. ===")

FAKE = {"replies": [], "prompts": [], "agents": [], "interrupted": False}
FAKE_KEY = "FAKE-API-KEY-abcdefghijklmnopqrstuvwxyz0123456789"

class FakeAgent:
    """Stands where Hermes's AIAgent stands. Diana's narrowing, bounding,
    threading and recording all run against it unmodified."""
    ALL_TOOLS = ("read_file", "write_file", "patch", "search_files", "terminal")

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.tools = [{"type": "function", "function": {"name": n}} for n in self.ALL_TOOLS]
        FAKE["agents"].append(self)

    def chat(self, message):
        FAKE["prompts"].append(message)
        reply = FAKE["replies"].pop(0) if FAKE["replies"] else ""
        return reply(message) if callable(reply) else reply

    def interrupt(self, **kwargs):
        FAKE["interrupted"] = True

_run_agent = types.ModuleType("run_agent")
_run_agent.AIAgent = FakeAgent
sys.modules["run_agent"] = _run_agent
HL.provider_config = lambda home=None: {
    "provider": "fake-provider", "model": "fake-model",
    "base_url": "https://example.invalid", "api_key": FAKE_KEY}

def scripted(*replies):
    FAKE["replies"] = list(replies)
    FAKE["prompts"] = []
    FAKE["agents"] = []

root_c = fresh("schema"); ap_c = approve(root_c)
CB = ap_c["contract"]

# ======================================================================
print("\n=== B — the verdict comes from the FULL final response, not the bound ===")

BIG = dict(PASS_V())
BIG["summary"] = "verified: " + ("the repair is present in src/calc.py. " * 120)
BIG["dod_checks"] = [{"criterion": f"criterion {i}", "result": "PASS",
                      "evidence": "src/calc.py " + "y" * 40} for i in range(12)]
BIG_TEXT = json.dumps(BIG)
check("B the fixture verdict really is longer than the observation bound",
      len(BIG_TEXT) > HL.MAX_OBSERVATION_CHARS, f"({len(BIG_TEXT)} chars)")

scripted(BIG_TEXT)
drv = HL.LiveTurnDriver(prompt="x", allowed_tools=P.REVIEWER_TOOLS)
observations = drv(CB)
note = observations[0]["note"]
check("B the persisted observation is still BOUNDED",
      len(note) <= HL.MAX_OBSERVATION_CHARS, f"({len(note)})")
check("B the complete final response is preserved in memory for the caller",
      drv.final_text == BIG_TEXT and len(drv.final_text) > HL.MAX_OBSERVATION_CHARS)
parsed_full, t_full = E.extract_verdict(drv.final_text)
parsed_note, t_note = E.extract_verdict(note)
check("B the verdict parses from the full final response",
      parsed_full == BIG and t_full["parse_status"] == E.PARSE_PARSED, f"({t_full})")
falsify("B the SAME verdict does NOT parse from the bounded observation -- this is the "
        "exact production failure, reproduced",
        parsed_note is None and t_note["parse_status"] == E.PARSE_UNBALANCED, f"({t_note})")
check("B the full response is NOT written into the turn record",
      BIG_TEXT not in json.dumps(drv.record)
      and "final" not in drv.record
      and drv.record["final_chars"] == len(BIG_TEXT) and drv.record["final_present"] is True,
      f"({sorted(drv.record)})")
check("B and no credential reaches the record either",
      FAKE_KEY not in json.dumps(drv.record) and "api_key" not in drv.record)

# ======================================================================
print("\n=== C — the reviewer is PRESENTED only its read-only schemas ===")

scripted(json.dumps(PASS_V()))
drv = HL.LiveTurnDriver(prompt="x", allowed_tools=P.REVIEWER_TOOLS)
drv(CB)
shown = drv.record["tool_schemas_shown"]
check("C the reviewer turn shows exactly read_file and search_files",
      shown == ["read_file", "search_files"], f"({shown})")
for hidden in ("write_file", "patch", "terminal"):
    check(f"C the reviewer turn does NOT show {hidden}", hidden not in shown)
agent_tools = sorted((t.get("function", t) or {}).get("name") for t in FAKE["agents"][0].tools)
check("C the agent handed to the model really carries only those two schemas",
      agent_tools == ["read_file", "search_files"], f"({agent_tools})")
check("C the presented set is DERIVED from the role's frozen envelope, so the two "
      "cannot drift apart",
      tuple(shown) == tuple(sorted(P.REVIEWER_TOOLS)))
scripted(json.dumps(PASS_V()))
wide = HL.LiveTurnDriver(prompt="x", narrow=False)
wide(CB)
wide_shown = wide.record["tool_schemas_shown"]
falsify("C a turn built the way the production reviewer WAS built shows exactly the four "
        "schemas the real run recorded, so the assertion above is about the fix and not "
        "about the fixture",
        {"patch", "read_file", "search_files", "write_file"} <= set(wide_shown),
        f"({wide_shown})")

print("--- and presentation is still NOT the security boundary ---")
def drive(tool, args=None):
    """Hermes's REAL dispatch funnel -- the one the inline-executor bypass is on."""
    import agent.tool_executor as te
    from agent.tool_guardrails import ToolCallGuardrailController
    ran = {"x": False}
    def sentinel(_a):
        ran["x"] = True; return '{"ok": "handler"}'
    ag = types.SimpleNamespace(
        _tool_guardrails=ToolCallGuardrailController(),
        _guardrail_block_result=lambda d: '{"error": "g"}',
        _checkpoint_mgr=types.SimpleNamespace(enabled=False), _current_tool=None,
        _touch_activity=lambda *a, **k: None, _turns_since_memory=0, _iters_since_skill=0,
        tool_progress_callback=None, tool_start_callback=None, quiet_mode=True,
        tool_progress_mode="off", verbose_logging=False, log_prefix_chars=80, log_prefix="")
    ref = te._ToolCallRef(tool, dict(args or {}), "t", "c1", [])
    st = te._ManagedToolResult(result=None, args=dict(args or {}), middleware_trace=[],
                               blocked=False, dispatched=False)
    r = te._dispatch_authorized_once(ag, st, ref, execute=sentinel, scope_block=None,
                                     display_index=None, begin_execution=None,
                                     authorization_gate=None)
    return {"executed": ran["x"], "result": str(r)}

A.install_projection("REVIEWER", CB, ap_c["topology"])
in_scope = str(Path(root_c) / "src" / "calc.py")
check("C under the REVIEWER projection read_file reaches its handler",
      drive("read_file", {"path": in_scope})["executed"] is True)
for tool, args in (("write_file", {"path": in_scope, "content": BROKEN}),
                   ("patch", {"path": in_scope, "old_string": "a", "new_string": "b"}),
                   ("terminal", {"command": "true", "workdir": str(root_c), "timeout": 5})):
    check(f"C {tool} does NOT reach its handler, whatever any schema said",
          drive(tool, args)["executed"] is False)
A.deny_all()

# ======================================================================
print("\n=== F — the verdict parser: deterministic, and closed in every direction ===")

ok = json.dumps(PASS_V())
cases = [
    ("a bare JSON object", ok, E.PARSE_PARSED, True),
    ("surrounding whitespace is harmless", "\n\n  " + ok + "  \n", E.PARSE_PARSED, True),
    ("one ```json fence", f"```json\n{ok}\n```", E.PARSE_PARSED, True),
    ("one bare ``` fence", f"```\n{ok}\n```", E.PARSE_PARSED, True),
    ("a fence with prose around it", f"Here it is:\n```json\n{ok}\n```\nDone.",
     E.PARSE_PARSED, True),
    ("a verdict longer than the observation bound", BIG_TEXT, E.PARSE_PARSED, True),
    ("no JSON at all", "The work looks fine to me.", E.PARSE_ABSENT, False),
    ("an empty final response", "", E.PARSE_ABSENT, False),
    ("truncated transport (opened, never closed)", ok[:len(ok) // 2],
     E.PARSE_UNBALANCED, False),
    ("malformed JSON", '{"decision": "PASS", "summary": nope,}', E.PARSE_MALFORMED, False),
    ("a balanced candidate that is not an object", "[1, 2, 3] {not json}",
     E.PARSE_MALFORMED, False),
    ("two different JSON objects", ok + "\n" + json.dumps(FAIL_V()), E.PARSE_AMBIGUOUS, False),
    ("a verdict plus an unrelated echoed object",
     '{"allowed_tools": ["read_file"]}\n' + ok, E.PARSE_AMBIGUOUS, False),
    ("two fences each holding an object",
     f"```json\n{ok}\n```\n```json\n{json.dumps(FAIL_V())}\n```", E.PARSE_AMBIGUOUS, False),
]
for label, text, want_status, want_verdict in cases:
    got, transport = E.extract_verdict(text)
    check(f"F {label} -> {want_status}",
          transport["parse_status"] == want_status and (got is not None) == want_verdict,
          f"({transport})")
same = ok + "\n\n" + ok
got, transport = E.extract_verdict(same)
check("F the SAME object repeated is one reading, not an ambiguity",
      got is not None and transport["parse_status"] == E.PARSE_PARSED, f"({transport})")
nested = json.dumps({"decision": "PASS", "summary": "s", "findings": [],
                     "dod_checks": [{"criterion": "c", "result": "PASS", "evidence": "e"}]})
got, transport = E.extract_verdict(nested)
check("F nested objects inside the verdict are not separate candidates",
      got is not None and transport["parsed_object_count"] == 1, f"({transport})")
spliced = '{"a": 1} some prose {"b": 2}'
check("F two unrelated objects are never SPLICED into one candidate, which is what a "
      "greedy regex did",
      E.extract_verdict(spliced)[1]["parsed_object_count"] == 2)
braced = json.dumps({"decision": "PASS", "summary": "contains } and { in a string",
                     "findings": [],
                     "dod_checks": [{"criterion": "c", "result": "PASS", "evidence": "e"}]})
got, _ = E.extract_verdict(braced)
check("F braces inside JSON strings do not break the scan", got is not None)

print("--- and verdict.py is untouched: the closed schema still decides ---")
check("F a PASS with a failing dod_check is still self-contradictory",
      code_of(lambda: VD.validate({"decision": "PASS", "summary": "s", "findings": [],
                                   "dod_checks": [{"criterion": "c", "result": "FAIL",
                                                   "evidence": "e"}]}))
      == blocking.REVIEW_VERDICT_SELF_CONTRADICTORY)
check("F a parsed object with an unknown key is still malformed",
      code_of(lambda: VD.validate(dict(PASS_V(), severity="low")))
      == blocking.REVIEW_VERDICT_MALFORMED)
check("F an absent verdict is still absent",
      code_of(lambda: VD.validate(None)) == blocking.REVIEW_VERDICT_ABSENT)
check("F the schema's key set has not been loosened",
      VD.VERDICT_KEYS == ("decision", "summary", "findings", "dod_checks")
      and VD.FINDING_KEYS == ("description", "evidence")
      and VD.CHECK_KEYS == ("criterion", "result", "evidence"))

# ======================================================================
print("\n=== D / E / G — the production wiring, driven end to end ===")

class SequenceBuilder(E._Backend):
    """One planned edit per attempt, so successive builds are distinguishable."""
    name = "sequence-builder"

    def __init__(self, plans):
        super().__init__()
        self.plans = list(plans)

    def _run(self, contract_block, item_id=None):
        if not self.plans:
            return None
        rel, content = self.plans.pop(0)
        path = Path(contract_block["target"]["repo_root"]) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return None


def production_run(root, plans, replies, *, verify_fn=verify, max_attempts=8, items=None):
    """A real `actors.execute`, with the PRODUCT's own reviewer backend.

    The reviewer comes from `product._backends`, so what is exercised is the
    production wiring and not a wiring the test invented.
    """
    appraisal = approve(root, max_attempts=max_attempts,
                        **({"items": items} if items else {}))
    run_directory = appraisal["run_directory"]
    loaded = RCV.load_run(run_directory)
    _builder, reviewer = PROD._backends("hermes", loaded["contract"], run_directory)
    scripted(*replies)
    blocked = None
    try:
        A.execute(run_directory, builder=SequenceBuilder(plans), reviewer=reviewer,
                  verify=verify_fn)
    except blocking.Blocked as exc:
        blocked = exc
    return {"rd": run_directory, "reviewer": reviewer, "blocked": blocked,
            "record": J.read(run_directory), "contract": loaded["contract"]}

root_d = fresh("prod")
run = production_run(
    root_d,
    plans=[("src/calc.py", FIXED), ("src/extra.py", "REGRESSION_NOTE = 1\n")],
    replies=[json.dumps(FAIL_V()), json.dumps(PASS_V())])

record = run["record"]
actors_seq = [a["actor"] for a in record["attempts"]]
evidence = run["reviewer"].evidence_seen

print("--- G: a reviewer FAIL costs another builder attempt, and PASS completes ---")
check("G builder -> reviewer -> builder -> reviewer is the sequence a rejection causes",
      actors_seq == ["BUILDER", "REVIEWER", "BUILDER", "REVIEWER"], f"({actors_seq})")
check("G the item is COMPLETE only after the reviewer PASSed",
      record["items"]["item-1"]["status"] == "COMPLETE"
      and record["terminal"]["outcome"] == J.COMPLETE,
      f"({record['items']['item-1']['status']}, {record['terminal']})")
check("G the run recorded both verdicts as durable evidence",
      sorted(q.name for q in Path(run["rd"]).glob("review-verdict-*.json"))
      == ["review-verdict-002.json", "review-verdict-004.json"])
first = json.loads((Path(run["rd"]) / "review-verdict-002.json").read_text())
second = json.loads((Path(run["rd"]) / "review-verdict-004.json").read_text())
check("G the first verdict is the rejection and it names its findings",
      first["verdict"]["decision"] == "FAIL"
      and first["verdict"]["findings"][0]["description"] == "no regression test")
falsify("G Diana's deterministic verification PASSED before the rejected review, so the "
        "second builder attempt was caused by the REVIEWER and not by a failing command",
        verify(run["contract"], "item-1") is True and first["verdict"]["decision"] == "FAIL")

print("--- D: the evidence is real, and is Diana's own measurement ---")
recon1 = json.loads((Path(run["rd"]) / "reconciliation-001.json").read_text())
recon3 = json.loads((Path(run["rd"]) / "reconciliation-003.json").read_text())
check("D the reviewer's evidence reports the paths the build ACTUALLY touched",
      evidence[0]["paths_touched"] == recon1["paths_touched"] == ["src/calc.py"],
      f"({evidence[0]['paths_touched']} vs {recon1['paths_touched']})")
check("D and the envelope result Diana measured, not a placeholder",
      evidence[0]["within_envelope"] is True
      and evidence[0]["paths_outside_write_scope"] == recon1["paths_outside_write_scope"])
check("D git status before/after come from the same reconciliation record",
      evidence[0]["git_status_before"] == recon1["git_status_before"]
      and evidence[0]["git_status_after"] == recon1["git_status_after"])
check("D verification_passed is Diana's own verification result",
      evidence[0]["verification_passed"] is True and verify(run["contract"], "item-1") is True)
check("D the task under review is the approved contract's task, verbatim",
      evidence[0]["task"] == run["contract"]["task"])
falsify("D none of the values the product used to hardcode survives: paths_touched was "
        "[], within_envelope was None, verification_passed was an unconditional True",
        all(e["paths_touched"] != [] for e in evidence)
        and all(e["within_envelope"] is not None for e in evidence))
prompts = [q for q in FAKE["prompts"]]
check("D the real touched path is actually put in front of the model",
      "src/calc.py" in prompts[0] and "src/extra.py" in prompts[1], f"({len(prompts)} prompts)")
check("D the reviewer is told that a passing verifier is not a completed task",
      "NOT evidence that the requested behaviour was implemented" in prompts[0])
check("D the reviewer is given the task text as the acceptance standard",
      run["contract"]["task"] in prompts[0])
check("D the reviewer is given the verification RESULT and told it is read-only; the "
      "command itself appears only as a fact about the BUILDER's envelope",
      "deterministic verification command: PASSED" in prompts[0]
      and "commands the builder was allowed: python3 check.py" in prompts[0]
      and "READ-ONLY" in prompts[0])

print("--- D: verification_passed follows the callback, in both directions ---")
build_ev = E.evidence_builder(run["rd"], lambda: J.read(run["rd"]),
                              lambda cb, item=None: False)
ev_false = build_ev(run["contract"], "item-1", reviewed_attempt=1)
build_ev_true = E.evidence_builder(run["rd"], lambda: J.read(run["rd"]),
                                   lambda cb, item=None: True)
ev_true = build_ev_true(run["contract"], "item-1", reviewed_attempt=1)
check("D a verification callback returning False yields verification_passed False",
      ev_false["verification_passed"] is False)
falsify("D and True yields True, so the field tracks the callback rather than a constant",
        ev_true["verification_passed"] is True)

print("--- E: the evidence is bound to the EXACT builder attempt under review ---")
check("E the first review saw attempt 1 and the second saw attempt 3",
      [e["reviewed_attempt"] for e in evidence] == [1, 3],
      f"({[e['reviewed_attempt'] for e in evidence]})")
check("E the second review's evidence is attempt 3's diff, not attempt 1's",
      evidence[1]["paths_touched"] == recon3["paths_touched"] == ["src/extra.py"],
      f"({evidence[1]['paths_touched']})")
falsify("E the two builds touched DIFFERENT paths, so the assertion above could have "
        "failed", recon1["paths_touched"] != recon3["paths_touched"])
check("E the attempt the verdict was ACCEPTED against is the attempt the reviewer was "
      "shown -- a reviewer cannot bless a build it never saw",
      [first["reviewed_attempt"], second["reviewed_attempt"]]
      == [e["reviewed_attempt"] for e in evidence])
records = [json.loads((Path(run["rd"]) / f"turn-record-{n:03d}.json").read_text())
           for n in (2, 4)]
check("E the reviewer turn record names the build it reviewed",
      [q["reviewed_attempt"] for q in records] == [1, 3])
falsify("E the REVIEWER's own attempts are 2 and 4, so resolving 'the latest attempt' "
        "would have produced the reviewer's own in-flight attempt and no evidence at all",
        [a["attempt"] for a in record["attempts"] if a["actor"] == "REVIEWER"] == [2, 4])

print("--- E: an attempt number that means something else resolves to a refusal ---")
for number, why in ((2, "a REVIEWER attempt"), (4, "a REVIEWER attempt"),
                    (99, "an attempt that does not exist"), (None, "no attempt at all")):
    code = code_of(lambda n=number: build_ev(run["contract"], "item-1", reviewed_attempt=n))
    check(f"E {why} ({number!r}) is refused, not silently substituted",
          code == blocking.REVIEW_VERDICT_WRONG_ACTOR, f"({code})")
check("E the number is re-validated against the DIGEST-COVERED journal, so a clean "
      "build resolves and nothing else does",
      A.clean_build_by_number(record, 1)["attempt"] == 1
      and A.clean_build_by_number(record, 2) is None)

print("--- E: in a multi-item run, each review sees its OWN item's build ---")
root_e = fresh("items")
run_e = production_run(
    root_e,
    plans=[("src/calc.py", FIXED), ("src/second.py", "SECOND = 1\n")],
    replies=[json.dumps(PASS_V()), json.dumps(PASS_V())],
    items=[{"id": "A", "task": "repair add", "depends_on": []},
           {"id": "B", "task": "add the second module", "depends_on": ["A"]}])
ev_e = run_e["reviewer"].evidence_seen
check("E each item's review received that item's own build",
      [e["item_id"] for e in ev_e] == ["A", "B"]
      and ev_e[0]["paths_touched"] == ["src/calc.py"]
      and ev_e[1]["paths_touched"] == ["src/second.py"],
      f"({[(e['item_id'], e['paths_touched']) for e in ev_e]})")
check("E and both items completed through their own reviewer PASS",
      run_e["record"]["terminal"]["outcome"] == J.COMPLETE)

# ======================================================================
print("\n=== H — every reviewer malfunction fails CLOSED ===")

def crash(_message):
    raise RuntimeError("the reviewer session died")

def stall(_message):
    time.sleep(3)
    return json.dumps(PASS_V())

MALFUNCTIONS = [
    ("the reviewer crashes", [crash], None, blocking.HERMES_TURN_FAILED),
    ("the reviewer returns no final response at all", [""], None, blocking.HERMES_TURN_FAILED),
    ("the reviewer returns prose with no JSON",
     ["Looks good to me, the change is fine."], blocking.REVIEW_VERDICT_ABSENT, None),
    ("the reviewer's JSON is truncated mid-object",
     [json.dumps(PASS_V())[: len(json.dumps(PASS_V())) // 2]],
     blocking.REVIEW_VERDICT_ABSENT, None),
    ("the reviewer's JSON is malformed",
     ['{"decision": "PASS", "summary": nope}'], blocking.REVIEW_VERDICT_ABSENT, None),
    ("the reviewer emits two contradictory objects",
     [json.dumps(PASS_V()) + "\n" + json.dumps(FAIL_V())],
     blocking.REVIEW_VERDICT_ABSENT, None),
    ("the reviewer's object is schema-invalid",
     ['{"decision": "PASS"}'], blocking.REVIEW_VERDICT_MALFORMED, None),
    ("the reviewer PASSes while a dod_check FAILs",
     [json.dumps({"decision": "PASS", "summary": "fine", "findings": [],
                  "dod_checks": [{"criterion": "c", "result": "FAIL", "evidence": "e"}]})],
     blocking.REVIEW_VERDICT_SELF_CONTRADICTORY, None),
    ("the reviewer smuggles a severity field",
     [json.dumps(dict(PASS_V(), severity="low"))], blocking.REVIEW_VERDICT_MALFORMED, None),
]
for label, replies, want_code, _turn_code in MALFUNCTIONS:
    root_h = fresh("h")
    out = production_run(root_h, plans=[("src/calc.py", FIXED)],
                         replies=list(replies) * 3, max_attempts=3)
    rec = out["record"]
    complete = (rec.get("terminal") or {}).get("outcome") == J.COMPLETE
    item_done = rec["items"]["item-1"]["status"] == "COMPLETE"
    check(f"H {label}: the item is NOT COMPLETE",
          complete is False and item_done is False,
          f"({rec.get('terminal')}, {rec['items']['item-1']['status']})")
    if want_code is not None:
        code = out["blocked"].code if out["blocked"] else None
        check(f"H {label}: and it stops with {want_code}", code == want_code, f"({code})")

print("--- H: a reviewer that exceeds its wall-clock bound ---")
REAL_CAP = HL.WALL_CLOCK_SECONDS
HL.WALL_CLOCK_SECONDS = 1
try:
    root_t = fresh("timeout")
    out = production_run(root_t, plans=[("src/calc.py", FIXED)],
                         replies=[stall] * 3, max_attempts=3)
finally:
    HL.WALL_CLOCK_SECONDS = REAL_CAP
rec = out["record"]
check("H a reviewer that times out never completes the item",
      (rec.get("terminal") or {}).get("outcome") != J.COMPLETE
      and rec["items"]["item-1"]["status"] != "COMPLETE",
      f"({rec.get('terminal')}, {rec['items']['item-1']['status']})")
check("H the timeout was recorded and the turn was interrupted, not abandoned",
      FAKE["interrupted"] is True)

print("--- H: a passing verifier and a clean diff never complete an item alone ---")
root_n = fresh("nogate")
appraisal = approve(root_n, max_attempts=2)
loaded = RCV.load_run(appraisal["run_directory"])
_b, silent = PROD._backends("hermes", loaded["contract"], appraisal["run_directory"])
scripted("", "", "")
nogate = None
try:
    A.execute(appraisal["run_directory"], builder=SequenceBuilder([("src/calc.py", FIXED)]),
              reviewer=silent, verify=verify)
except blocking.Blocked as exc:
    nogate = exc
rec = J.read(appraisal["run_directory"])
check("H the build was clean, inside the envelope, and Diana's verification passed",
      rec["attempts"][0]["within_envelope"] is True
      and rec["attempts"][0]["reconciled"] is True
      and verify(loaded["contract"], "item-1") is True)
falsify("H and the item is STILL not COMPLETE, because reviewer approval is not optional",
        rec["items"]["item-1"]["status"] != "COMPLETE"
        and (rec.get("terminal") or {}).get("outcome") != J.COMPLETE,
        f"({rec['items']['item-1']['status']})")

# ======================================================================
print("\n=== 6 — safe reviewer observability ===")
root_o = fresh("obs")
out = production_run(root_o, plans=[("src/calc.py", FIXED)],
                     replies=["I reviewed it and it seems fine."] * 3, max_attempts=3)
turn = json.loads((Path(out["rd"]) / "turn-record-002.json").read_text())
transport = turn.get("verdict_transport") or {}
check("6 the reviewer turn record says a final response WAS produced",
      transport.get("final_present") is True and transport.get("final_chars") > 0,
      f"({transport})")
check("6 and says exactly why no verdict came out of it",
      transport.get("parse_status") == E.PARSE_ABSENT
      and transport.get("candidate_count") == 0, f"({transport})")
check("6 the distinctions the real run could not make are now recorded",
      {E.PARSE_ABSENT, E.PARSE_UNBALANCED, E.PARSE_MALFORMED,
       E.PARSE_AMBIGUOUS, E.PARSE_PARSED} == {
          E.extract_verdict(t)[1]["parse_status"] for t in
          ("prose only", json.dumps(PASS_V())[:40], '{"a": nope}',
           json.dumps(PASS_V()) + json.dumps(FAIL_V()), json.dumps(PASS_V()))})
blob = json.dumps(turn)
check("6 the record carries no model transcript, no prompt and no credential",
      "I reviewed it and it seems fine." not in blob
      and FAKE_KEY not in blob and "api_key" not in blob
      and "NOT evidence that the requested behaviour" not in blob)
check("6 observability is non-authoritative: the run still stopped, and stopped for the "
      "verdict reason rather than for a transport one",
      out["blocked"] is not None and out["blocked"].code == blocking.REVIEW_VERDICT_ABSENT)

out_ok = production_run(fresh("obs2"), plans=[("src/calc.py", FIXED)],
                        replies=[json.dumps(PASS_V())])
turn_ok = json.loads((Path(out_ok["rd"]) / "turn-record-002.json").read_text())
falsify("6 a successful review records parse_status 'parsed' and the verdict's KEY NAMES "
        "only, so the field distinguishes success from every failure above",
        turn_ok["verdict_transport"]["parse_status"] == E.PARSE_PARSED
        and turn_ok["verdict_transport"]["verdict_keys"]
        == ["decision", "dod_checks", "findings", "summary"],
        f"({turn_ok['verdict_transport']})")

# ======================================================================
print("\n=== I — the BUILDER is told the approved task, not just 'make it pass' ===")
import remediation_driver as RDV

TASK = ("add a spread guard to the quoting path, add a bounds check to the sizing "
        "path, and add regression tests covering both")
cb_i = {"task": TASK, "target": {"repo_root": "/x/repo"},
        "capability_envelope": {
            "allowed_commands": ["npm test"],
            "allowed_tools": ["read_file", "search_files", "write_file", "patch", "terminal"],
            "write_scope": {"allowed_roots": ["/x/repo/src", "/x/repo/bidask.mjs"]},
            "command_policy": {"workdir_roots": ["/x/repo"], "max_timeout_s": 300}}}
brief = RDV.RemediationDriver()._build_prompt(cb_i)
check("I the approved task reaches the builder verbatim", TASK in brief)
check("I every part of it is asked for, not just a repair",
      "Implement every part of that task" in brief and "if it asks for tests, add them" in brief)
check("I the builder is told the verifier is necessary and NOT sufficient",
      "necessary and NOT sufficient" in brief
      and "a suite that was already passing will still pass" in brief)
check("I the approved write scope is stated",
      "/x/repo/src" in brief and "/x/repo/bidask.mjs" in brief)
check("I the command allowlist and terminal bounds are still stated (M4 ERRATA-002)",
      "You may ONLY run this exact command: npm test" in brief
      and "workdir='/x/repo'" in brief and "timeout of at most 300 seconds" in brief)
falsify("I the prompt no longer asserts a failing verification that may not exist, nor a "
        "language the repository may not be written in",
        "has a failing verification" not in brief and "Python project" not in brief)
check("I an explicit caller-supplied prompt still wins, so the seam is unchanged",
      RDV.RemediationDriver(prompt="literal")._build_prompt(cb_i) == "literal")

print(f"\n{passed} passed, {failed} failed, {falsifiers} falsifiers")
sys.exit(1 if failed else 0)
PY
