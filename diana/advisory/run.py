#!/usr/bin/env python3
"""Diana: the D1 advisory run (spec step 9, D9, D14, D37).

Wires the whole M1 path: route the request, profile the repository, build the
contract, install and PROVE the two boundaries, scan Diana-side, let Hermes look
around inside the envelope, and assemble the artifact.

## Order matters, and it is the order of a fail-closed system

Nothing touches Hermes until the contract exists and is exactly SAFE/D1, and
Hermes receives no input until both patches have been proven live by behavioral
probes on the real worker path. A failure at any step raises `Blocked`, and a
blocked run produces NO advisory artifact -- only a run record -- even when the
deterministic findings would have been perfectly sound (D37). Emitting an
advisory from a run whose envelope went unverified is precisely the
"artifact from an unproven process" pattern Diana's Security Track exists to
reject, and doing it in the proof-of-concept would teach the wrong reflex.

## Who does what

The scanner is Diana-side and Hermes cannot execute it (D11). `findings[]` would
therefore be computable with Hermes switched off entirely -- which is the honest
shape of M1: the security delta is deterministic Python, and Hermes is the
runtime substrate under test. What Hermes contributes is
`unverified_observations[]`: ungraded, severity-less, and incapable of changing
any acceptance result.

## Routing, without a slash command

M1's entry point is a natural-language request, not `/command`. Routing is a
deterministic keyword rule, not a model call: the workflow class determines the
depth (D13), so letting a model choose it would hand Hermes the depth channel
that D14 denies it. An unroutable request blocks rather than guessing.

## Turn drivers

The turn driver is how Hermes actually gets used, and it is injectable so the
acceptance suite can drive a SCRIPTED turn -- one that deliberately emits a
non-allowed tool name to prove the executor refuses it, rather than relying on
the model politely not asking. `scripted_turn_driver` performs real tool calls
through `model_tools.handle_function_call`, the same entry a live model reaches.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "runtime"))
sys.path.insert(0, str(_HERE.parent / "profile"))
sys.path.insert(0, str(_HERE.parent / "adapters"))

import artifact as _artifact  # noqa: E402
import blocking  # noqa: E402
import contract as _contract  # noqa: E402
import dom_scan as _dom_scan  # noqa: E402
import hermes as _hermes  # noqa: E402
import hermes_patches as _patches  # noqa: E402
import repo_profile as _repo_profile  # noqa: E402
import selftest as _selftest  # noqa: E402

WORKFLOW = "ADVISORY_SECURITY_REVIEW"
ALLOWED_TOOLS = ("read_file", "search_files")

# Deterministic routing. Both groups must be present: "security" alone could be
# a request to change something, and "check" alone could be about anything.
_SUBJECT_WORDS = ("security", "vulnerability", "vulnerabilities", "xss", "insecure")
_ACTION_WORDS = ("check", "review", "audit", "inspect", "assess", "scan")
# Words that mean the request is NOT a read-only advisory review. Routing must
# not quietly turn a "fix this" into a "look at this".
_DISQUALIFYING_WORDS = ("fix", "patch", "remediate", "repair", "refactor", "deploy", "release", "merge")


def route(task: str) -> str | None:
    """Map a natural-language request to a certified workflow, or None."""
    if not isinstance(task, str):
        return None
    words = set("".join(c.lower() if c.isalnum() else " " for c in task).split())
    if words & set(_DISQUALIFYING_WORDS):
        return None
    if words & set(_SUBJECT_WORDS) and words & set(_ACTION_WORDS):
        return WORKFLOW
    return None


def git_state(repo_root: str) -> tuple[str, bool]:
    """(commit, dirty). A dirty target is allowed and recorded, never a blocker."""
    def _git(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", "-C", repo_root, *args],
                                 capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout if out.returncode == 0 else None

    head = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain")
    return (head.strip() if head else "unknown"), bool(status and status.strip())


def scripted_turn_driver(contract_block: dict, probe_tree: dict | None = None) -> list[dict]:
    """Drive Hermes through the real execution path without a model call.

    Every call below goes through `model_tools.handle_function_call`, the proven
    sole entry that a live model's tool calls also reach -- so the refusals
    observed here are the same refusals a real turn would meet. The deliberate
    non-allowed call is the point: it proves the executor refuses, rather than
    resting on the model choosing not to ask.
    """
    if str(_patches.HERMES_HOME) not in sys.path:
        sys.path.insert(0, str(_patches.HERMES_HOME))
    import model_tools as mt

    root = contract_block["target"]["repo_root"]
    inventory = contract_block["repo_profile"].get("inventory", [])
    observations: list[dict] = []

    listing = str(mt.handle_function_call(
        "search_files", {"pattern": "*.js", "path": root, "target": "files"}))
    if "diana:" in listing:
        raise blocking.Blocked(
            blocking.CAPABILITY_PATCH_NOT_LIVE,
            "an allowed tool was refused during the run: " + listing[:200],
        )

    for rel in inventory[:8]:
        if not rel.endswith((".js", ".mjs", ".html", ".htm")):
            continue
        body = str(mt.handle_function_call("read_file", {"path": os.path.join(root, rel)}))
        if "diana:" in body:
            continue
        if "innerHTML" in body or "document.write" in body:
            observations.append({
                "note": "this file assigns to an HTML-parsing sink; the deterministic scanner "
                        "owns whether that is a finding",
                "file": rel,
            })

    # Deliberate probe: a non-allowed tool, issued from inside the run.
    refused = str(mt.handle_function_call("write_file", {
        "path": os.path.join(root, "diana-should-never-exist.txt"), "content": "x"}))
    if "diana:" not in refused:
        raise blocking.Blocked(
            blocking.CAPABILITY_PATCH_NOT_LIVE,
            "a non-envelope tool was NOT refused during the run",
        )
    return observations


def null_turn_driver(contract_block: dict, probe_tree: dict | None = None) -> list[dict]:
    """No Hermes turn at all. Proves findings[] never depended on Hermes (D1)."""
    return []


def execute(
    *,
    task: str,
    repo_root: str,
    runs_base: str | None = None,
    env: dict | None = None,
    config: dict | None = None,
    hermes_home: str | None = None,
    turn_driver=scripted_turn_driver,
    run_id: str | None = None,
    created_at: str | None = None,
) -> dict:
    """Run the D1 advisory review. Returns the run record; raises Blocked."""
    repo_root = os.path.realpath(os.path.expanduser(repo_root))

    workflow = route(task)
    if workflow is None:
        # No certified workflow means no certified depth (D13), and M1 executes
        # only SAFE/D1 (D15), so this is a refusal rather than a best effort.
        raise blocking.Blocked(
            blocking.CONTRACT_NOT_SAFE_D1,
            f"request {task!r} does not route to a certified M1 workflow",
        )

    read_scope_block = {
        "allowed_roots": [repo_root],
        "denied_subpaths": list(_contract.DEFAULT_DENIED_SUBPATHS),
    }

    try:
        profile_block = _repo_profile.profile(repo_root, read_scope_block)
    except Exception as exc:
        raise blocking.Blocked(
            blocking.REPO_PROFILE_FAILED, f"{type(exc).__name__}: {exc}"
        ) from None

    commit, dirty = git_state(repo_root)
    contract_block = _contract.build(
        task=task, repo_root=repo_root, git_commit=commit, dirty=dirty,
        repo_profile=profile_block, workflow=workflow,
        allowed_tools=ALLOWED_TOOLS, run_id=run_id, created_at=created_at,
    )
    contract_path, digest = _contract.persist(contract_block, base=runs_base)
    run_directory = contract_path.parent

    # Bind the run: per-run path plus env-carried run_id and digest. Hermes has
    # no write tool and no shell, so what this actually defends against is a
    # stale contract from an earlier run and two concurrent runs crossing wires.
    os.environ["DIANA_RUN_ID"] = contract_block["run_id"]
    os.environ["DIANA_CONTRACT_DIGEST"] = digest
    _contract.load_and_verify(contract_path, contract_block["run_id"], digest)

    # Everything provable without importing Hermes is proven FIRST. Importing
    # model_tools with HERMES_SAFE_MODE unset runs plugin discovery and loads
    # plugin modules in-process, so checking safe mode afterwards would order
    # the control behind the risk it guards.
    _hermes.check_pre_import(repo_root=repo_root, env=env, hermes_home=hermes_home)

    _patches.install_confinement(read_scope_block)
    _patches.install_capability(ALLOWED_TOOLS)

    probe_tree = _selftest.build_probe_tree(str(run_directory / "selftest"))
    probe_scope = {
        "allowed_roots": [probe_tree["repo"], repo_root],
        "denied_subpaths": list(_contract.DEFAULT_DENIED_SUBPATHS),
    }
    _patches.install_confinement(probe_scope)
    _hermes.check(
        repo_root=repo_root, env=env, config=config, hermes_home=hermes_home,
        probe_tree=probe_tree, require_patches=True,
    )
    # Narrow back to the contract's own scope for the run itself: the probe tree
    # was a pre-flight fixture, not part of the review's authorized surface.
    _patches.install_confinement(read_scope_block)

    try:
        scan_result = _dom_scan.scan(repo_root, _repo_profile.scannable(profile_block))
    except Exception as exc:
        raise blocking.Blocked(blocking.SCANNER_RAISED, f"{type(exc).__name__}: {exc}") from None

    try:
        raw_observations = turn_driver(contract_block, probe_tree) if turn_driver else []
    except blocking.Blocked:
        record = getattr(turn_driver, "record", None)
        if isinstance(record, dict):
            (run_directory / "turn-record.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        raise
    observations = _artifact.validate_observations(raw_observations)

    document = _artifact.build(
        contract_block, scan_result, profile_block["categories"], observations
    )
    artifact_path = _artifact.persist(document, run_directory)

    # M2-D5: a live turn records provider, model and the observed tool-call
    # sequence in a SEPARATE file. Neither frozen schema may gain a field, so
    # the driver exposes `.record` and the run persists it beside the artifact.
    turn_record_path = None
    record = getattr(turn_driver, "record", None)
    if isinstance(record, dict):
        turn_record_path = run_directory / "turn-record.json"
        turn_record_path.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    return {
        "run_id": contract_block["run_id"],
        "run_directory": str(run_directory),
        "contract_path": str(contract_path),
        "contract_digest": digest,
        "artifact_path": str(artifact_path),
        "turn_record_path": str(turn_record_path) if turn_record_path else None,
        "document": document,
    }


def record_blocked(exc, runs_base: str | None = None, run_id: str | None = None) -> Path:
    """Write the run record for a blocked run. Never an advisory artifact (D37)."""
    import json
    import uuid

    directory = _contract.run_dir(run_id or str(uuid.uuid4()), runs_base)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / "blocked.json"
    path.write_text(json.dumps(exc.as_record(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
