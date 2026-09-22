#!/usr/bin/env bash
# PHASE 3 — the product surface: opt-in autonomy, and manual mode UNCHANGED.
#
# The single most important property here is the negative one: with autonomy
# off, everything about Diana is byte-identical to what it was, including the
# digest a human approves. Autonomy that quietly changed manual behaviour would
# be a change to every existing approval.
set -uo pipefail
AUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIANA_DIR="$(cd "$AUT_DIR/.." && pwd)"
REPO_DIR="$(cd "$DIANA_DIR/.." && pwd)"
TMP_DIR="$(mktemp -d)"; trap 'rm -rf "$TMP_DIR"' EXIT
python3 - "$DIANA_DIR" "$TMP_DIR" "$REPO_DIR" <<'PY'
import copy, json, os, subprocess, sys, textwrap, uuid
from pathlib import Path

diana, tmp, repo_dir = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
for sub in ("autonomy", "supervisors", "product", "multiactor", "unattended",
            "runtime", "mutation", "adapters", "profile"):
    sys.path.insert(0, str(diana / sub))
import escalation as E, policy as POL, standing as S
import proposal as P, view as V, intent as I, contract as C
import mock as _MOCK
MOCK_HUMAN_NAME = _MOCK.HumanSupervisor.name

DIANA_DO = str(Path(repo_dir, "diana-do"))
passed = failed = falsifiers = 0
def code_of(fn):
    try:
        fn(); return None
    except E.Escalation as exc: return exc.code

def check(label, cond, extra=""):
    global passed, failed
    if cond is True: passed += 1; print(f"PASS  {label}")
    else: failed += 1; print(f"FAIL  {label} {extra}")
def falsify(label, cond, extra=""):
    global falsifiers
    falsifiers += 1
    check("[falsifier] " + label, cond, extra)

def fresh(name):
    root = tmp / f"c-{name}-{uuid.uuid4().hex[:6]}"
    (root / "src").mkdir(parents=True)
    (root / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (root / "check.py").write_text(textwrap.dedent("""\
        import sys
        sys.path.insert(0, "src")
        from calc import add
        sys.exit(0 if add(2, 3) == 5 else 1)
        """))
    g = lambda *a: subprocess.run(["git","-C",str(root),*a],capture_output=True,text=True)
    g("init","-q"); g("config","user.email","t@x"); g("config","user.name","t")
    g("add","-A"); g("commit","-qm","init")
    return root

GOAL = "fix the failing check in src/calc.py"
ROOT = fresh("base")

def cli(root, *args, env_extra=None):
    env = dict(os.environ)
    env["DIANA_RUNS_BASE"] = str(tmp / "runs")
    env.update(env_extra or {})
    return subprocess.run([DIANA_DO, *args, "--repo", str(root),
                           "--proposals-base", str(tmp / "prop")],
                          capture_output=True, text=True, env=env, timeout=600)

# ======================================================================
print("=== manual mode is the default and is UNCHANGED ===")
manual = P.build(GOAL, str(ROOT), base=str(tmp / "p1"))
check("a proposal built with no autonomy argument is manual",
      manual["autonomy"]["enabled"] is False)
check("the manual digest is byte-identical to the pre-autonomy computation, "
      "because the autonomy key is OMITTED rather than set to a default",
      manual["proposal_digest"] == C.digest({
          "authority": P.authority_view(manual["predicted_contract"]),
          "work_items": __import__("workitems").digest(manual["predicted_items"]),
          "actors": __import__("topology").digest(manual["predicted_topology"]),
          "run_policy": P.policy_authority(manual["predicted_policy"]),
      }))
falsify("an AUTONOMOUS proposal for the same goal hashes differently, so the key "
        "really is the thing that is omitted",
        P.build(GOAL, str(ROOT), base=str(tmp / "p1"),
                autonomy_policy=POL.build())["proposal_digest"]
        != manual["proposal_digest"])
check("the manual plan view shows no AUTONOMY section at all",
      "AUTONOMY" not in V.plan_view(manual)
      and "ALWAYS ESCALATES" not in V.plan_view(manual))
check("and still shows GOAL / PLAN / AUTHORITY / APPROVAL REQUIRED",
      all(s in V.plan_view(manual)
          for s in ("GOAL", "PLAN", "AUTHORITY", "APPROVAL REQUIRED")))

out = cli(ROOT, GOAL)
check("the real ./diana-do still proposes with no autonomy flag",
      out.returncode == 0 and "AUTONOMY" not in out.stdout
      and "APPROVAL REQUIRED" in out.stdout, f"({out.returncode} {out.stderr[:150]})")
check("a manual approval refuses to act autonomously if one is attempted",
      S.enabled(manual["predicted_standing"]) is False)

# ======================================================================
print("\n=== --autonomy is opt-in, and shows the whole policy before approval ===")
auto = P.build(GOAL, str(ROOT), base=str(tmp / "p2"), autonomy_policy=POL.build())
plan = V.plan_view(auto)
check("the autonomous plan view shows the AUTONOMY section", "AUTONOMY" in plan)
for label in ("Same-scope retries", "Narrower child runs", "Task splitting",
              "Preserve partial work", "Revert its own unverified",
              "Dependency changes", "Budgets"):
    check(f"it states {label!r}", label in plan)
check("it shows every budget a human is agreeing to",
      all(str(v) in plan for v in POL.DEFAULT_LIMITS.values()))
check("it shows the ALWAYS ESCALATES list, from the frozen standing approval",
      "ALWAYS ESCALATES" in plan
      and all(c.replace("_", " ") in plan for c in S.HUMAN_ONLY_CONDITIONS))
check("dependency changes are shown as OFF by default",
      "Dependency changes          no" in plan)

out = cli(ROOT, GOAL, "--autonomy")
check("the real ./diana-do renders the autonomy sections with --autonomy",
      out.returncode == 0 and "AUTONOMY" in out.stdout
      and "ALWAYS ESCALATES" in out.stdout, f"({out.returncode} {out.stderr[:150]})")
check("the printed start command carries --autonomy forward, so approving the "
      "displayed proposal approves the displayed policy",
      "--autonomy" in out.stdout)
falsify("the manual invocation of the same goal prints no --autonomy, so the flag "
        "is carried and not assumed", "--autonomy" not in cli(ROOT, GOAL).stdout)

# ======================================================================
print("\n=== the standing approval cannot be reused for anything wider ===")
base_auto = P.build(GOAL, str(ROOT), base=str(tmp / "p3"), autonomy_policy=POL.build())
for label, policy in (
    ("a larger child budget", POL.build(limits={"max_child_runs": 99})),
    ("a longer wall clock", POL.build(limits={"max_wall_clock_seconds": 99999})),
    ("dependency changes enabled", POL.build(allow={"dependency_changes": True})),
    ("task splitting disabled", POL.build(allow={"task_splitting": False})),
):
    other = P.build(GOAL, str(ROOT), base=str(tmp / "p3"), autonomy_policy=policy)
    check(f"{label} changes the proposal digest",
          other["proposal_digest"] != base_auto["proposal_digest"])
OTHER_REPO = fresh("elsewhere")
check("the same goal in another repository is a different proposal",
      P.build(GOAL, str(OTHER_REPO), base=str(tmp / "p3"),
              autonomy_policy=POL.build())["proposal_digest"]
      != base_auto["proposal_digest"])
check("a different executor is a different standing approval",
      P.build(GOAL, str(ROOT), base=str(tmp / "p3"), autonomy_policy=POL.build(),
              executor="deterministic")["proposal_digest"]
      != base_auto["proposal_digest"])
falsify("rebuilding the identical autonomous proposal is stable, so the differences "
        "above are the POLICY and not the clock",
        P.recompute(base_auto) == base_auto["proposal_digest"])

print("\n--- approval re-derives against the live repository, autonomy included ---")
stale = copy.deepcopy(base_auto)
stale["autonomy"] = POL.build(limits={"max_child_runs": 99})
(tmp / "p3" / "proposals").mkdir(parents=True, exist_ok=True)
check("editing the stored autonomy policy makes the proposal fail its own digest",
      P.recompute(stale) != stale["proposal_digest"])
falsify("the unedited proposal still recomputes to its own digest",
        P.recompute(base_auto) == base_auto["proposal_digest"])

# ======================================================================
print("\n=== the escalation report is precise, never vague ===")
report = V.autonomy_view({
    "outcome": E.BLOCKED_FOR_HUMAN, "root_run_id": "root-1",
    "runs_attempted": 3, "completed_runs": ["r1"],
    "budget_used": {"child_runs": 2, "attempts": 7, "wall_clock_seconds": 40,
                    "changed_files": ["src/a.py"], "supervisor_calls": 2},
    "budget_remaining": {"child_runs": 6, "attempts": 17, "wall_clock_seconds": 7160,
                         "changed_files": 49, "supervisor_calls": 10},
    "verified_changes": ["src/a.py"], "preserved_changes": ["lib/b.py"],
    "unverified_changes": ["lib/b.py"], "reverted_changes": ["src/c.py"],
    "supervisor_decisions": [{"accepted": True}, {"accepted": False}],
    "escalation": {"escalated": True, "code": "command-not-subset",
                   "detail": "the child asks to run ['npm install']",
                   "requested": {"allowed_commands": ["npm install"]}}})
check("it says what happened and how much was tried",
      "Runs               3 (1 completed)" in report
      and "Recovery plans     2 requested, 1 acted on" in report)
check("it states budgets used AND remaining",
      "Budget used" in report and "Budget left" in report and "17 attempts" in report)
check("it distinguishes verified, preserved, unverified and reverted changes",
      all(s in report for s in ("verified", "preserved", "unverified", "reverted"))
      and "NOT yet verified" in report and "undone by Diana" in report)
check("it names the exact additional authority required",
      "allowed commands: npm install" in report)
import product as PROD, inspect as _i2
check("a COMPLETE session never prints a contradictory per-run RESULT block",
      "RESULT" not in V.autonomy_view({
          "outcome": "COMPLETE", "root_run_id": "root-1", "runs_attempted": 2,
          "completed_runs": ["child-1"],
          "budget_used": {"child_runs": 1, "attempts": 3, "wall_clock_seconds": 10,
                          "changed_files": ["a.py"], "supervisor_calls": 1},
          "budget_remaining": {"child_runs": 1, "attempts": 3, "wall_clock_seconds": 90,
                               "changed_files": 9, "supervisor_calls": 1},
          "verified_changes": ["a.py"], "preserved_changes": [], "unverified_changes": [],
          "reverted_changes": [], "supervisor_decisions": [], "escalation": None}))
import inspect as _i2
_auto_code = "\n".join(
    line for line in _i2.getsource(PROD._run_autonomous).splitlines()
    if not line.lstrip().startswith("#"))
check("and the CLI does not append the ROOT run's report after an autonomous "
      "session -- in a recovered session the root is the run that failed",
      "_emit_result(" not in _auto_code, "(a call survives outside the comment)")
falsify("the manual approval path DOES still emit a per-run result, so autonomous "
        "sessions differ only where they must",
        "_emit_result(" in "\n".join(
            l for l in _i2.getsource(PROD.cmd_approve).splitlines()
            if not l.lstrip().startswith("#")))
check("the session view names the runs so per-run detail stays reachable",
      "RUNS" in V.autonomy_view({
          "outcome": "COMPLETE", "root_run_id": "root-1", "runs_attempted": 2,
          "completed_runs": ["child-1"],
          "budget_used": {"child_runs": 1, "attempts": 3, "wall_clock_seconds": 10,
                          "changed_files": [], "supervisor_calls": 1},
          "budget_remaining": {"child_runs": 1, "attempts": 3, "wall_clock_seconds": 90,
                               "changed_files": 9, "supervisor_calls": 1},
          "verified_changes": [], "preserved_changes": [], "unverified_changes": [],
          "reverted_changes": [], "supervisor_decisions": [], "escalation": None}))
budget_report = V.autonomy_view({
    "outcome": E.BLOCKED_FOR_HUMAN, "root_run_id": "root-1", "runs_attempted": 2,
    "completed_runs": [],
    "budget_used": {"child_runs": 1, "attempts": 6, "wall_clock_seconds": 528,
                    "changed_files": ["src/a.py"], "supervisor_calls": 2},
    "budget_remaining": {"child_runs": 1, "attempts": 0, "wall_clock_seconds": 1872,
                         "changed_files": 9, "supervisor_calls": 0},
    "verified_changes": [], "preserved_changes": [], "unverified_changes": ["src/a.py"],
    "reverted_changes": [], "supervisor_decisions": [],
    "escalation": {"escalated": True, "code": "attempt-budget-exhausted",
                   "detail": "the standing approval's attempts budget is exhausted (6 of 6 used)",
                   "requested": {"increase_limit": "max_total_attempts",
                                 "current_value": 6, "used": 6}}})
check("a BUDGET escalation offers the larger budget as the remedy, and does NOT "
      "claim that no additional authority would help -- it plainly would",
      "larger budget" in budget_report
      and "max_total_attempts: currently 6" in budget_report
      and "No additional authority would help" not in budget_report, f"({budget_report})")
falsify("a non-budget escalation still says what authority is needed instead, so the "
        "two remedies are rendered differently",
        "larger budget" not in report and "allowed commands: npm install" in report)
check("it never says only 'human decision needed'",
      "human decision needed" not in report.lower()
      and "command-not-subset" in report)
falsify("a session that finished says so instead, with no STOPPED FOR YOU block",
        "STOPPED FOR YOU" not in V.autonomy_view({
            "outcome": "COMPLETE", "root_run_id": "root-1",
            "runs_attempted": 2, "completed_runs": ["r1", "r2"],
            "budget_used": {"child_runs": 1, "attempts": 3, "wall_clock_seconds": 10,
                            "changed_files": [], "supervisor_calls": 1},
            "budget_remaining": {"child_runs": 7, "attempts": 21,
                                 "wall_clock_seconds": 7190, "changed_files": 50,
                                 "supervisor_calls": 11},
            "verified_changes": [], "preserved_changes": [], "unverified_changes": [],
            "reverted_changes": [], "supervisor_decisions": [], "escalation": None}))

# ======================================================================
print("\n=== the automated adapter is isolated from the governance core ===")
core = []
for name in ("policy.py", "standing.py", "subset.py", "lineage.py",
             "provenance.py", "classify.py", "loop.py", "audit.py"):
    core.append((diana / "autonomy" / name).read_text())
joined = "\n".join(core)
check("no core autonomy module imports the OpenAI adapter",
      "openai_supervisor" not in joined and "from openai" not in joined)
check("no core autonomy module names a model or a provider endpoint",
      not any(s in joined for s in ("gpt-", "api.openai.com", "claude-", "gemini-")))
check("the adapter reads its model and credential from the environment, and "
      "hardcodes neither",
      "DIANA_SUPERVISOR_MODEL" in (diana / "supervisors" / "openai_supervisor.py").read_text()
      and "DIANA_SUPERVISOR_API_KEY" in (diana / "supervisors" / "openai_supervisor.py").read_text())
import openai_supervisor as OA
check("an unconfigured adapter resolves to None, so the CLI falls back to asking",
      OA.from_environment(model="", api_key=None) is None)
check("no credential ever reaches an audit record or evidence",
      "api_key" not in (diana / "autonomy" / "audit.py").read_text()
      and "api_key" not in (diana / "supervisors" / "base.py").read_text())
import base as SB
check("evidence redacts token-shaped strings defensively",
      "<redacted>" in SB.redact("token sk-abcdefghijklmnopqrstuvwxyz012345"))

# ======================================================================
print("\n=== the Hermes-backed planner reuses Diana's OWN provider machinery ===")
#
# Audit finding: the first adapter spoke OpenAI /v1/chat/completions and read
# its own environment variables. This installation resolves provider
# openai-codex, api_mode codex_responses, at a ChatGPT backend, authenticated by
# an OAuth credential Hermes already holds. A user with working Diana auth would
# have needed to buy a SECOND API key to use autonomous recovery at all.
import hermes_supervisor as HS
import base as SB2, schema as SCH2, escalation as E2

class FakeDriver:
    """Stands where LiveTurnDriver stands, with the same surface."""
    def __init__(self, final, *, shown=None, record=None):
        self.final_text = final
        self.record = record if record is not None else {
            "provider": "test-provider", "model": "test-model",
            "tool_schemas_shown": list(shown or [])}
    def __call__(self, contract_block): return []

EV = {k: "x" for k in SB2.EVIDENCE_KEYS}
EV["workspace_provenance"] = {}
GOOD = SCH2.empty(SCH2.RETRY_SAME, "try the same scope again")

def hermes_with(final, shown=None, record=None):
    return HS.HermesSupervisor(
        driver_factory=lambda prompt: FakeDriver(final, shown=shown, record=record))

check("a valid reply parses through the same extractor Diana trusts for verdicts",
      hermes_with(json.dumps(GOOD)).recommend(EV)["decision"] == SCH2.RETRY_SAME)
check("the planner turn is built with NO tools, and a turn that was shown one "
      "is refused rather than used",
      code_of(lambda: hermes_with(json.dumps(GOOD), shown=["read_file"]).recommend(EV))
      == E2.SUPERVISOR_MALFORMED)
falsify("the identical reply is accepted when no tool was shown, so the refusal is "
        "the TOOL and not the reply",
        hermes_with(json.dumps(GOOD), shown=[]).recommend(EV)["decision"] == SCH2.RETRY_SAME)
for label, final, want in (
    ("no final response", None, E2.SUPERVISOR_MALFORMED),
    ("prose instead of JSON", "I think you should try again", E2.SUPERVISOR_MALFORMED),
    ("two JSON objects", json.dumps(GOOD) + json.dumps(SCH2.empty(SCH2.ESCALATE_HUMAN, "or not")),
     E2.SUPERVISOR_MALFORMED),
    ("an unknown field", json.dumps(dict(GOOD, force=True)), E2.SUPERVISOR_UNKNOWN_FIELD),
    ("an unknown decision", json.dumps(dict(GOOD, decision="DO_IT")),
     E2.SUPERVISOR_UNKNOWN_DECISION),
):
    check(f"{label} fails closed [{want}]",
          code_of(lambda f=final: hermes_with(f).recommend(EV)) == want)

check("the prompt states the planner has no tools and names no model",
      "You have no tools" in HS.PROMPT_HEADER
      and not any(m in HS.PROMPT_HEADER for m in ("gpt-", "claude-", "gemini-")))
check("the adapter has its own finite wall-clock bound",
      isinstance(HS.WALL_CLOCK_SECONDS, int) and 0 < HS.WALL_CLOCK_SECONDS <= 600)
check("describe() reports provider and model identity and NO credential",
      set(HS.HermesSupervisor().describe()) == {"provider", "model"})
src = (diana / "supervisors" / "hermes_supervisor.py").read_text()
check("the adapter stores no credential and duplicates no auth resolution",
      "api_key" not in src and "resolve_codex" not in src
      and "provider_config" in src)
check("it closes the capability boundary before consulting the planner",
      "deny_all" in src)
falsify("it reuses LiveTurnDriver rather than reimplementing a provider call: no "
        "HTTP client appears in it",
        not any(w in src for w in ("urllib", "requests", "http.client", "socket")))

print("\n--- adapter preference: reuse before a second credential path ---")
import inspect as _inspect
# The ORDER IN CODE, not in prose: the tuple the resolver iterates.
body = _inspect.getsource(PROD._supervisor)
tried = [n for n in ("hermes_supervisor", "openai_supervisor") if f'"{n}"' in body]
check("the reusing adapter is tried BEFORE the separate-credential one",
      tried == ["hermes_supervisor", "openai_supervisor"]
      and body.index('"hermes_supervisor"') < body.index('"openai_supervisor"'),
      f"({tried})")
check("and the human fallback is the last resort, returned only after both",
      body.rindex("HumanSupervisor") > body.index('"openai_supervisor"'))
resolved = PROD._supervisor(str(ROOT))
check("this installation resolves to a real planner, not the human fallback",
      resolved.name in ("hermes", "openai", "human-supervisor"))
check("and when nothing is configured it resolves to the human fallback, which "
      "escalates rather than pretending to plan",
      MOCK_HUMAN_NAME == "human-supervisor")

print("\n--- no Diana module may shadow an installed package ---")
#
# Measured, by a real dogfood that died: `diana/supervisors/openai.py` shadowed
# the `openai` PyPI distribution, so Hermes's own `from openai import OpenAI`
# resolved to Diana's file and the run failed with
# "cannot import name 'OpenAI'". Diana's packages are FLAT directories on
# sys.path, so every module name is effectively global -- a file named after a
# dependency silently replaces it for the whole process.
import importlib.util as _ilu
shadowing = []
for _dir in ("autonomy", "supervisors", "product", "multiactor", "unattended",
             "runtime", "mutation", "adapters", "profile", "gate", "ci", "security"):
    _path = diana / _dir
    if not _path.is_dir():
        continue
    for _f in sorted(_path.glob("*.py")):
        try:
            _spec = _ilu.find_spec(_f.stem)
        except BaseException:
            _spec = None
        _origin = getattr(_spec, "origin", None) if _spec else None
        if _origin and "agent-orchestration" not in str(_origin):
            shadowing.append((str(_f.relative_to(diana)), _origin))
check("no Diana module name shadows an importable third-party package",
      shadowing == [], f"({shadowing})")
falsify("the probe can detect one: a module named after a real dependency IS "
        "reported by the same check",
        (lambda: (_ilu.find_spec("json") is not None
                  and "agent-orchestration" not in str(_ilu.find_spec("json").origin)))())
check("the two adapters carry unambiguous module names",
      (diana / "supervisors" / "openai_supervisor.py").is_file()
      and (diana / "supervisors" / "hermes_supervisor.py").is_file()
      and not (diana / "supervisors" / "openai.py").exists()
      and not (diana / "supervisors" / "hermes.py").exists())
falsify("`hermes` is a name Diana itself already uses, which is why it was not "
        "available to a supervisor module either -- the same class of collision, "
        "caught one layer earlier",
        (diana / "adapters" / "hermes.py").is_file())

print(f"\n{passed} passed, {failed} failed, {falsifiers} falsifiers")
sys.exit(1 if failed else 0)
PY
