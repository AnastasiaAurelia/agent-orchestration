"""Acceptance-only observations and safe mutation canaries for M5 review findings.

No production module imports this file. Falsifiers patch production boundaries
in memory and restore them with context managers; no source/contract is edited.
"""
import ast
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import subprocess
import tempfile
from unittest.mock import patch

import artifact
import evidence_model
import journal


def exact_outcome(result, state, reason):
    persisted = journal.read(Path(result["report_path"]).parent)
    return (result["outcome"] == state
            and result["reason_code"] == reason
            and result["record"]["state"] == state
            and persisted["state"] == state
            and persisted["terminal"]["reason_code"] == reason
            and result["report"]["outcome"] == state)


def safe_report(directory, result):
    """M5-D7/D17, AC-17/18: run record allowed, advisory deliverable forbidden.

    Observe files actually produced, and use both frozen validators. A mere
    document_type rename cannot turn a run report into advisory/evidence proof.
    """
    directory = Path(directory)
    report = json.loads((directory / "run-report.json").read_bytes())
    if (report != result["report"] or report["outcome"] != "BLOCKED"
            or report.get("document_type") != "UNATTENDED_RUN_REPORT"):
        return False
    if set(report) & set(evidence_model.ALLOWED_RUN_FIELDS):
        return False
    try:
        artifact.validate(report)
    except artifact.ArtifactError:
        pass
    else:
        return False
    if evidence_model._classify_run(report, None)["status"] != "MALFORMED":
        return False
    documents = list(directory.glob("*.json"))
    if not documents:
        return False
    for path in documents:
        doc = json.loads(path.read_bytes())
        if path.name == "advisory-security-review.json":
            return False
        if isinstance(doc, dict) and doc.get("document_type") == artifact.DOCUMENT_TYPE:
            return False
        try:
            artifact.validate(doc)
        except artifact.ArtifactError:
            continue
        return False
    return True


@contextmanager
def process_observer(root):
    """Observe real Popen calls. Refuse unsafe canaries BEFORE any side effect.

    Fixtures and Hermes import/setup occur outside this window. During it the
    scripted driver writes only its declared file; every process is Diana's.
    """
    observed = {"commands": [], "network": [], "refused": []}
    original = subprocess.Popen
    allowed = {("git", "-C", str(root), "rev-parse", "HEAD"),
               ("git", "-C", str(root), "status", "--porcelain")}

    def popen(args, *a, **kw):
        command = tuple(args) if isinstance(args, (list, tuple)) else args
        observed["commands"].append(command)
        if (not isinstance(command, tuple) or command not in allowed
                or kw.get("shell") or kw.get("executable")):
            observed["refused"].append(command)
            raise OSError("M5 acceptance canary: disallowed process intercepted")
        return original(args, *a, **kw)

    def connect(sock, address):
        observed["network"].append(address)
        raise OSError("M5 acceptance canary: network intercepted")

    with patch.object(subprocess, "Popen", popen), \
         patch.object(socket.socket, "connect", connect), \
         patch.object(socket.socket, "connect_ex", connect):
        yield observed


def only_read_git(observed, root):
    allowed = {("git", "-C", str(root), "rev-parse", "HEAD"),
               ("git", "-C", str(root), "status", "--porcelain")}
    commands = observed["commands"]
    return bool(commands) and set(commands) == allowed and not observed["network"] and not observed["refused"]


def external_calls(paths):
    """AST inventory of executable process/network call sites, resolving aliases.

    This is a bounded source inspection, not a general Python security analyzer.
    Dynamic code/imports are included as review-required surfaces. Behavioral
    interception complements it on the exercised approve/execute/recovery path.
    """
    hits = set()
    roots = {"subprocess", "socket", "requests", "httpx", "urllib", "http",
             "aiohttp", "github", "git", "gitlab"}
    for path in paths:
        tree = ast.parse(path.read_text())
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    aliases[alias.asname or alias.name.split('.')[0]] = alias.name
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    aliases[alias.asname or alias.name] = (node.module or '') + '.' + alias.name
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            first, *rest = name.split('.')
            resolved = '.'.join([aliases.get(first, first), *rest])
            root = resolved.split('.')[0]
            if ((root in roots and first in aliases)
                    or resolved in {"os.system", "os.popen", "eval", "exec", "__import__",
                                    "importlib.import_module"}
                    or resolved.startswith(("os.exec", "os.spawn", "os.posix_spawn"))):
                hits.add((path.name, resolved, ast.unparse(node.args[0]) if node.args else ""))
    return hits


def exact_git_calls(path):
    tree = ast.parse(path.read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    helper_calls = [n for n in calls if isinstance(n.func, ast.Name) and n.func.id == "git"]
    return (len(helper_calls) == 2
            and {tuple(ast.literal_eval(a) for a in n.args) for n in helper_calls}
            == {("rev-parse", "HEAD"), ("status", "--porcelain")})


def falsify_core(check, fresh, approve, fix_driver, done, U, R, RPT, J):
    """Run broken production behavior through the SAME observation predicates."""
    original_finish = U._finish
    for state, reason in (("COMPLETE", "work-finished"),
                          ("FAILED", "attempt-budget-exhausted"),
                          ("BLOCKED", "reconciliation-mismatch")):
        root = fresh("falsify-state")
        ap = approve(root, max_attempts=1)
        def driver(cb, item_id=None):
            if state == "BLOCKED":
                (root / "escape.txt").write_text("escape")
            elif state == "COMPLETE":
                fix_driver()(cb)
        def wrong_finish(*a, **kw):
            result = original_finish(*a, **kw)
            result["outcome"] = "COMPLETE" if state != "COMPLETE" else "FAILED"
            return result
        with patch.object(U, "_finish", wrong_finish):
            result = U.execute(ap["run_directory"], turn_driver=driver,
                               is_work_finished=lambda *a: state == "COMPLETE")
        check(f"FALSIFY AC-17 {state}: wrong returned state fails exact outcome",
              not exact_outcome(result, state, reason)
              and J.read(ap["run_directory"])["state"] == state)

    original_build, original_persist = RPT.build, RPT.persist
    for mutation in ("advisory-type", "advisory-file", "evidence-field", "validator-accepts"):
        root = fresh("falsify-report")
        ap = approve(root)
        def escape(cb, item_id=None):
            (root / "escape.txt").write_text("escape")
        def broken_build(*a, **kw):
            report = original_build(*a, **kw)
            if mutation == "advisory-type":
                report["document_type"] = artifact.DOCUMENT_TYPE
            if mutation == "evidence-field":
                report["control_id"] = "SEC-001"
            return report
        def broken_persist(report, directory):
            path = original_persist(report, directory)
            if mutation == "advisory-file":
                Path(directory, "advisory-security-review.json").write_text(json.dumps(report))
            return path
        with patch.object(RPT, "build", broken_build), patch.object(RPT, "persist", broken_persist):
            result = U.execute(ap["run_directory"], turn_driver=escape, is_work_finished=done)
        if mutation == "validator-accepts":
            with patch.object(artifact, "validate", return_value=None):
                accepted = safe_report(ap["run_directory"], result)
        else:
            accepted = safe_report(ap["run_directory"], result)
        check(f"FALSIFY AC-17/18 {mutation}: unsafe produced report rejected",
              result["outcome"] == "BLOCKED" and not accepted)

    original_observe = R.observe_target
    for command in (("git", "push"), ("git", "commit"), ("gh", "pr", "create"),
                    ("gh", "pr", "merge"), ("gh", "api", "repos/example"),
                    ("git", "status", "--porcelain", "--ignored"), ("network",)):
        root = fresh("falsify-outward")
        def broken_observe(target):
            try:
                if command == ("network",):
                    with socket.socket() as sock:
                        sock.connect(("127.0.0.1", 9))
                else:
                    argv = (["git", "-C", str(target), *command[1:]]
                            if command[0] == "git" else list(command))
                    subprocess.run(argv, check=False)
            except OSError:
                pass   # even a swallowed error must leave a failing observation
            return original_observe(target)
        with process_observer(root) as observed, patch.object(R, "observe_target", broken_observe):
            ap = approve(root, max_attempts=1)
            result = U.execute(ap["run_directory"], turn_driver=fix_driver(), is_work_finished=done)
        check(f"FALSIFY AC-20 {' '.join(command)}: outward canary rejected before effect",
              result["outcome"] == "COMPLETE" and not only_read_git(observed, root))

    # Temporary copies of production code exercise the AST observation itself;
    # the injected outward calls are never executed.
    source = Path(R.__file__).read_text()
    with tempfile.TemporaryDirectory() as temporary:
        candidate = Path(temporary, "recovery.py")
        candidate.write_text(source + "\nimport subprocess as child\nchild.run(['gh', 'pr', 'create'])\n")
        check("FALSIFY AC-20 AST: aliased outward call changes the reviewed call-site set",
              external_calls([candidate]) != {("recovery.py", "subprocess.run", "['git', '-C', repo_root, *args]")})
        candidate.write_text(source.replace('git("status", "--porcelain")', 'git("push")'))
        check("FALSIFY AC-20 AST: changed production Git operation is rejected",
              not exact_git_calls(candidate))


def no_turn_record(path):
    return not Path(path).exists()


def unchanged(path, before):
    return Path(path).read_bytes() == before


def reconciled_attempts(report, expected):
    attempts = report["attempts"]
    return len(attempts) == expected and all(a["within_envelope"] is True for a in attempts)


def command_executed(result):
    return result.get("exit_code") == 0 and "M5_COMMAND_EXECUTED" in result.get("output", "")


def falsify_observations(check, directory, result, command_result):
    """Corrupt/restore actual persisted observations; counterexamples must fail.

    These are output-boundary mutants, not claims that production has been
    changed. The production-boundary mutants for AC-17/20 run above separately.
    """
    import copy
    path = Path(directory, "contract.json")
    before = path.read_bytes()
    try:
        doc = json.loads(before)
        doc["task"] += " unauthorized replacement"
        path.write_text(json.dumps(doc))
        check("FALSIFY AC-21 saved-byte binding rejects a changed contract at the SAME path",
              not unchanged(path, before))
    finally:
        path.write_bytes(before)
    verifier = Path(directory, "verifier-canary.py")
    original = b"import sys\nsys.exit(1)\n"
    verifier.write_bytes(original)
    try:
        verifier.write_bytes(b"import sys\nsys.exit(0)\n# sys.exit(1)\n")
        check("FALSIFY AC-21 verifier-byte binding rejects a changed check retaining the old token",
              not unchanged(verifier, original))
    finally:
        verifier.unlink()
    bad = copy.deepcopy(result["report"])
    count = len(bad["attempts"])
    bad["attempts"] = []
    check("FALSIFY AC-4/21 empty reconciliation list cannot pass",
          count > 0 and not reconciled_attempts(bad, count))
    bad = copy.deepcopy(result["report"])
    bad["attempts"][0]["within_envelope"] = False
    check("FALSIFY AC-4/21 escaped attempt cannot pass",
          not reconciled_attempts(bad, count))
    bad_command = dict(command_result, exit_code=1)
    check("FALSIFY AC-19 non-Diana command failure cannot pass as execution",
          command_executed(command_result) and not command_executed(bad_command))
    record = Path(directory, "turn-record-001.json")
    try:
        record.write_text("{}")
        check("FALSIFY AC-21 leaked turn record fails the absence observation", not no_turn_record(record))
    finally:
        record.unlink()


def graph_precedes_execution(code, transitions, attempts):
    return code == "work-item-self-dependency" and transitions == 0 and attempts == 0


def resumed_without_replay(calls, directory, result):
    return (calls == ["Q"] and journal.read(directory)["items"]["P"]["status"] == "COMPLETE"
            and exact_outcome(result, "COMPLETE", "work-finished"))


def falsify_missing_schema(check, directory, result):
    fields = evidence_model.ALLOWED_RUN_FIELDS
    try:
        del evidence_model.ALLOWED_RUN_FIELDS
        try:
            safe_report(directory, result)
        except AttributeError as exc:
            refused = "ALLOWED_RUN_FIELDS" in str(exc)
        else:
            refused = False
        check("FALSIFY AC-18 missing frozen schema cannot take an else-True path", refused)
    finally:
        evidence_model.ALLOWED_RUN_FIELDS = fields


def live_denials_hold(refusals, root):
    return refusals == [True, True] and not (Path(root) / "forbidden.txt").exists()


def before_turn_crash(returncode, directory, source, before):
    import signal
    record = journal.read(directory)
    return (returncode == -signal.SIGKILL and record["state"] == "ARMED"
            and record["attempts"] == [] and unchanged(source, before))
