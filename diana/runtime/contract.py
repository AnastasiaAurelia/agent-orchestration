#!/usr/bin/env python3
"""Diana runtime: ExecutionContract v1 (spec: "ExecutionContract v1", D12-D16).

The contract is the object that makes a Hermes run *bounded*. It is built by
Diana before the run, never negotiated with Hermes, and it is the single source
of truth for what the run may do.

Two authority rules are load-bearing and implemented here rather than described
anywhere else:

  * `risk` is a pure function of the GRANTED CAPABILITY ENVELOPE, never of task
    intent (D12). Intent is exactly what a confused or adversarial agent would
    misreport; capability is what it cannot touch. Read-only + no network + no
    shell + no delegation => SAFE, because no reachable action is unsafe.
  * `depth` comes from the CERTIFIED WORKFLOW CLASS (D13), not from the
    envelope -- a read-only envelope could serve D0 or D1, so capability alone
    cannot decide it.

Hermes has no proposal channel for either (D14), so under-classification is
impossible by construction rather than by validation.

M1 executes ONLY SAFE/D1. Anything else is BLOCKED, never downgraded and never
attempted (D15): M1 does not know how to perform D2+, so meeting a situation
that needs it is a refusal, not a best effort.

`allowed_tools` is the single source of truth for the envelope. `mutation_policy`
and `network_policy` are deliberately absent (D16) -- both are derivable from it,
and storing them separately would create a disagreement surface in a system whose
whole claim is fail-closed.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blocking  # noqa: E402
import read_scope as _read_scope  # noqa: E402

CONTRACT_VERSION = 1

# Hermes's own read/search pair, `model_tools.py:611 _READ_SEARCH_TOOLS`. The
# M1 envelope is exactly this set; there is no list_dir tool in Hermes, which is
# why repo_profile.py must supply the file inventory instead (D31).
KNOWN_READ_ONLY_TOOLS = frozenset({"read_file", "search_files"})

# Workflow class -> depth (D13). A workflow absent from this map has no
# certified depth and therefore cannot be executed.
WORKFLOW_DEPTH = {"ADVISORY_SECURITY_REVIEW": "D1"}

M1_RISK = "SAFE"
M1_DEPTH = "D1"

CONTRACT_KEYS = (
    "contract_version",
    "run_id",
    "created_at",
    "workflow",
    "depth",
    "risk",
    "task",
    "target",
    "capability_envelope",
    "read_scope",
    "repo_profile",
)

DEFAULT_DENIED_SUBPATHS = (".git/", ".env", ".env.*")


# --- canonical serialization (spec C1/C2 prerequisite, AC-9) ---

def canonical_json(contract: dict) -> bytes:
    """The ONE canonical serialization used for hashing and for digest checks.

    Sorted keys, no insignificant whitespace, UTF-8. Defined before hashing so
    digest verification is deterministic across processes and machines; any
    other spelling of the same document must hash identically or the digest is
    not a binding.
    """
    return json.dumps(
        contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(contract: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(contract)).hexdigest()


# --- derivation (D12, D13) ---

def derive_risk(capability_envelope: dict) -> str:
    """risk <- capability envelope. Never task intent (D12)."""
    if not isinstance(capability_envelope, dict):
        return "UNKNOWN"
    tools = capability_envelope.get("allowed_tools")
    if not isinstance(tools, list) or not tools:
        return "UNKNOWN"
    if not all(isinstance(t, str) for t in tools):
        return "UNKNOWN"
    if set(tools) <= KNOWN_READ_ONLY_TOOLS:
        return "SAFE"
    return "ELEVATED"


def derive_depth(workflow: str) -> str:
    """depth <- certified workflow class. Never the envelope, never Hermes (D13)."""
    return WORKFLOW_DEPTH.get(workflow, "UNCERTIFIED")


# --- construction & validation ---

def build(
    *,
    task: str,
    repo_root: str,
    git_commit: str,
    dirty: bool,
    repo_profile: dict,
    workflow: str = "ADVISORY_SECURITY_REVIEW",
    allowed_tools: tuple[str, ...] = ("read_file", "search_files"),
    denied_subpaths: tuple[str, ...] = DEFAULT_DENIED_SUBPATHS,
    run_id: str | None = None,
    created_at: str | None = None,
) -> dict:
    """Build a contract and prove it is exactly SAFE/D1, or raise Blocked."""
    envelope = {"allowed_tools": sorted(allowed_tools)}
    root = _read_scope.canonicalize(repo_root)
    contract = {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id or str(uuid.uuid4()),
        "created_at": created_at
        or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "workflow": workflow,
        "depth": derive_depth(workflow),
        "risk": derive_risk(envelope),
        # Recorded for provenance and EXPLICITLY non-enforcing (D16): nothing
        # reads it to make a decision, so it cannot become load-bearing later
        # by accident.
        "task": task,
        "target": {"repo_root": root, "git_commit": git_commit, "dirty": bool(dirty)},
        "capability_envelope": envelope,
        "read_scope": {
            "allowed_roots": [root],
            "denied_subpaths": list(denied_subpaths),
        },
        "repo_profile": repo_profile,
    }
    validate(contract)
    return contract


def validate(contract: object) -> None:
    """Raise Blocked unless the contract is well-formed and exactly SAFE/D1."""
    if not isinstance(contract, dict):
        raise blocking.Blocked(blocking.CONTRACT_MALFORMED, "contract is not an object")

    keys = set(contract)
    missing = sorted(set(CONTRACT_KEYS) - keys)
    extra = sorted(keys - set(CONTRACT_KEYS))
    if missing or extra:
        raise blocking.Blocked(
            blocking.CONTRACT_MALFORMED,
            f"missing={missing} unexpected={extra}",
        )
    if contract["contract_version"] != CONTRACT_VERSION:
        raise blocking.Blocked(
            blocking.CONTRACT_MALFORMED,
            f"contract_version {contract['contract_version']!r} != {CONTRACT_VERSION}",
        )
    if not isinstance(contract["task"], str):
        raise blocking.Blocked(blocking.CONTRACT_MALFORMED, "task must be a string")
    if not isinstance(contract["repo_profile"], dict):
        raise blocking.Blocked(blocking.CONTRACT_MALFORMED, "repo_profile must be an object")

    target = contract["target"]
    if not isinstance(target, dict) or set(target) != {"repo_root", "git_commit", "dirty"}:
        raise blocking.Blocked(blocking.CONTRACT_MALFORMED, "target shape invalid")
    if not isinstance(target["dirty"], bool):
        # dirty targets are allowed and recorded (D16); the value must still be
        # a real boolean so the record cannot be ambiguous.
        raise blocking.Blocked(blocking.CONTRACT_MALFORMED, "target.dirty must be a boolean")

    envelope = contract["capability_envelope"]
    if not isinstance(envelope, dict) or set(envelope) != {"allowed_tools"}:
        raise blocking.Blocked(
            blocking.CONTRACT_MALFORMED,
            "capability_envelope must contain exactly allowed_tools",
        )

    try:
        _read_scope.validate_read_scope(contract["read_scope"])
    except _read_scope.ScopeError as exc:
        raise blocking.Blocked(blocking.READ_SCOPE_MALFORMED, str(exc)) from None

    # Re-derive rather than trust what is written down: the stored values are a
    # record of a derivation, never an input to one.
    risk = derive_risk(envelope)
    depth = derive_depth(contract["workflow"])
    if contract["risk"] != risk or contract["depth"] != depth:
        raise blocking.Blocked(
            blocking.CONTRACT_MALFORMED,
            f"stored risk/depth ({contract['risk']}/{contract['depth']}) "
            f"disagree with derived ({risk}/{depth})",
        )
    if risk != M1_RISK or depth != M1_DEPTH:
        # Never downgrade, never attempt (D15).
        raise blocking.Blocked(
            blocking.CONTRACT_NOT_SAFE_D1,
            f"derived {risk}/{depth}; M1 executes only {M1_RISK}/{M1_DEPTH}",
        )


# --- run directory (spec: TCB "contract digest + run_id bind") ---

def run_dir(run_id: str, base: str | None = None) -> Path:
    root = Path(base) if base else Path.home() / ".diana" / "runs"
    return root / run_id


def persist(contract: dict, base: str | None = None) -> tuple[Path, str]:
    """Write the contract to its per-run directory; return (path, digest).

    Per-run directory 0700, contract.json 0600, outside the target repository.
    Per-run paths also make a stale contract from an earlier run unusable and
    let concurrent runs coexist without a lock.
    """
    validate(contract)
    directory = run_dir(contract["run_id"], base)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / "contract.json"
    payload = canonical_json(contract)
    with open(path, "wb") as handle:
        handle.write(payload)
    os.chmod(path, 0o600)
    return path, digest(contract)


def load_and_verify(path: str | Path, expected_run_id: str, expected_digest: str) -> dict:
    """Read a persisted contract back, proving it is the one bound to this run.

    Binding is env-carried run_id + digest against a per-run path. Hermes has no
    write tool and no shell, so it cannot touch either; what this actually
    defends is a stale contract left over from an earlier run and two concurrent
    runs crossing wires.
    """
    try:
        raw = Path(path).read_bytes()
        contract = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise blocking.Blocked(blocking.CONTRACT_MALFORMED, f"unreadable contract: {exc}") from None
    validate(contract)
    actual = digest(contract)
    if actual != expected_digest:
        raise blocking.Blocked(
            blocking.CONTRACT_DIGEST_MISMATCH, f"{actual} != {expected_digest}"
        )
    if contract["run_id"] != expected_run_id:
        raise blocking.Blocked(
            blocking.CONTRACT_RUN_ID_MISMATCH,
            f"{contract['run_id']} != {expected_run_id}",
        )
    return contract
