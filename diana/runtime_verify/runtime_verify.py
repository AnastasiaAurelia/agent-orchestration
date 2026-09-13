#!/usr/bin/env python3
"""Diana M3: bounded runtime/browser verification (spec: HERMES-RUNTIME-M3.md).

## What this module is for

A static finding says a source reaches a sink. A runtime verification says a
payload delivered through that source actually executed in a real browser. The
second **strengthens** the first and may never replace it (M3-D5): the scanner
still owns `findings[]` (M1 D1), this module owns `runtime-verification.json`,
and the two documents never merge.

## Where authority lives

The Node driver (`driver.mjs`) runs the browser and reports OBSERVATIONS. It is
the component that touches untrusted page content, so it is deliberately not the
component that decides what that content proved (M3-D7). Diana derives the
outcome here, from primitives, and a driver that tries to report an `outcome`,
`depth`, `severity` or `rule_id` trips the closed schema and yields
`INCONCLUSIVE` rather than having its claim honoured.

## Why the ExecutionContract is untouched

The contract governs what *Hermes* may do, and M3 changes that by nothing
(M3-D2). The browser is Diana's own capability, exactly as the deterministic
scanner is -- and the scanner has never appeared in the contract either. `D2` is
therefore a property of this verification, recorded here, derived from an
M3-owned workflow map Hermes cannot reach (M3-D3). M1 D13 and D14 stand
unweakened: Hermes still has no channel to depth.

## Why INCONCLUSIVE exists

"We could not test it" and "we tested it and it did not fire" are different
facts, and folding the first into the second would quietly manufacture
reassurance (M3-D6). Any launch failure, timeout, navigation error, driver crash
or unparseable output lands in `INCONCLUSIVE`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "runtime"))
sys.path.insert(0, str(_HERE.parent / "advisory"))
import contract as _contract  # noqa: E402

DOCUMENT_TYPE = "RUNTIME_VERIFICATION"
SCHEMA_VERSION = 1

# M3-D3: an M3-OWNED certified workflow map. Deliberately not merged into
# contract.WORKFLOW_DEPTH -- that map governs what Hermes is authorized to do,
# and M3 authorizes Hermes for nothing new. Hermes cannot name a key here.
RUNTIME_WORKFLOW_DEPTH = {"RUNTIME_VERIFIED_SECURITY_REVIEW": "D2"}
RUNTIME_WORKFLOW = "RUNTIME_VERIFIED_SECURITY_REVIEW"

REPRODUCED = "REPRODUCED"
NOT_REPRODUCED = "NOT_REPRODUCED"
INCONCLUSIVE = "INCONCLUSIVE"
OUTCOMES = frozenset({REPRODUCED, NOT_REPRODUCED, INCONCLUSIVE})

# Closed schema for driver output (M3-D7). Unknown keys are a violation, not a
# curiosity to be logged: an anomaly record is itself a side channel (M1 D14).
DRIVER_KEYS = frozenset({
    "driver_version", "runtime_envelope", "probes", "proxy_ledger",
    "static_server_log", "browser_processes", "driver_error", "stderr_tail",
})
# Anything the driver must NEVER speak about, at any nesting depth: Diana-owned
# verdict fields, plus every Security Track field. The Security Track half
# matters because those keys are also rejected by `validate_record`; catching
# them here turns a hostile driver into a clean INCONCLUSIVE instead of an
# uncaught error raised half-way through assembling the record.
DRIVER_FORBIDDEN_KEYS = frozenset(
    {"outcome", "depth", "risk", "severity", "rule_id", "finding"}
    | {"control_id", "applicability", "verifier", "evidence", "tool_error", "observed_at"}
)

REPRO_OBSERVATION_KEYS = frozenset({
    "injected_node_count", "handler_fired", "sink_html", "source_value", "final_url",
    "webrtc_available",
})

RECORD_KEYS = (
    "document_type",
    "schema_version",
    "run_id",
    "contract_digest",
    "workflow",
    "depth",
    "target",
    "finding_ref",
    "runtime_envelope",
    "delivery",
    "outcome",
    "outcome_basis",
    "observations",
    "proxy_ledger",
    "inconclusive_reason",
)

# Mirrored from evidence_model.ALLOWED_RUN_FIELDS so this document can be proven
# structurally incompatible with the Security Track from both directions
# (M3-D15, following M1 D35).
SECURITY_TRACK_FIELDS = frozenset(
    {"control_id", "applicability", "verifier", "evidence", "tool_error", "observed_at"}
)

DEFAULT_PAYLOAD = '<img src=x onerror="window.__diana_rv_fired=1">'
HANDLER_FLAG = "__diana_rv_fired"
# The payload's own signature, so a reproduction is counted wherever the
# target's code happened to place it -- `document.write` does not write into
# any particular container.
PROBE_SELECTOR = 'img[src="x"]'
SINK_SELECTOR = "#greeting"

# M3-D12: the payload must travel through the channel the FINDING names, not
# through whichever channel happens to be convenient. Delivering a `window.name`
# payload and then reporting on a `location.hash` finding would be verifying a
# different claim than the one made.
#
# `window.name` is delivered by an init script because it is opener-controlled
# and receives no URL encoding (F9). The URL-borne channels are delivered in the
# URL, which is exactly why they are subject to Chromium's percent-encoding and
# why `location.hash` does not reproduce (F8).
DELIVERY_INIT_SCRIPT = "init_script"
DELIVERY_URL = "url"
SOURCE_DELIVERY = {
    "window.name": (DELIVERY_INIT_SCRIPT, None),
    "location.hash": (DELIVERY_URL, "#"),
    "location.search": (DELIVERY_URL, "?"),
}

MCP_CLI_CANDIDATES = (
    os.environ.get("DIANA_PLAYWRIGHT_MCP_CLI", ""),
    str(Path.home() / ".npm/_npx/9833c18b2d85bc59/node_modules/@playwright/mcp/cli.js"),
)


class RuntimeVerifyError(ValueError):
    """The runtime record does not satisfy its own schema."""


# --- environment -----------------------------------------------------------

def find_mcp_cli() -> str | None:
    """Locate @playwright/mcp. Absent it, M3 reports INCONCLUSIVE, never a
    simulated browser -- a carried assumption, stated rather than papered over."""
    for candidate in MCP_CLI_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    base = Path.home() / ".npm" / "_npx"
    if base.is_dir():
        for entry in sorted(base.iterdir()):
            cli = entry / "node_modules" / "@playwright" / "mcp" / "cli.js"
            if cli.is_file():
                return str(cli)
    return None


def node_available() -> bool:
    return shutil.which("node") is not None


# --- driver invocation -----------------------------------------------------

def run_driver(config: dict, *, deadline_s: float, mode: str = "verify") -> dict:
    """Run the Node driver and return its parsed output, or a driver_error dict.

    Every failure mode here -- missing node, non-zero exit, unparseable stdout,
    a driver that outlived its own deadline -- produces a structured error that
    becomes INCONCLUSIVE. None of them can produce NOT_REPRODUCED.
    """
    if not node_available():
        return {"driver_error": "node is not available on PATH"}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(config, handle)
        config_path = handle.name
    try:
        proc = subprocess.run(
            ["node", str(_HERE / "driver.mjs"), mode, config_path],
            capture_output=True, text=True, timeout=deadline_s, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"driver_error": "driver exceeded its own wall-clock deadline"}
    except OSError as exc:
        return {"driver_error": f"driver could not be started: {exc}"}
    finally:
        try:
            os.unlink(config_path)
        except OSError:
            pass
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        return {"driver_error": f"driver produced no output (rc={proc.returncode}); "
                                f"stderr={(proc.stderr or '')[-300:]}"}
    try:
        return json.loads(line[-1])
    except json.JSONDecodeError as exc:
        return {"driver_error": f"driver output was not JSON: {exc}"}


# --- closed-schema validation of driver output (M3-D7) ---------------------

def _forbidden_keys(node, path="$", forbidden=DRIVER_FORBIDDEN_KEYS):
    hits = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in forbidden:
                hits.append(f"{path}.{key}")
            hits += _forbidden_keys(value, f"{path}.{key}", forbidden)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            hits += _forbidden_keys(value, f"{path}[{i}]", forbidden)
    return hits


def validate_driver_output(raw: object) -> str | None:
    """Return None when the driver output is well-formed, else a reason string.

    A reason string always becomes INCONCLUSIVE. The driver is the component
    that handled untrusted page content, so its output is treated as data from
    an untrusted producer, not as a report from a peer.
    """
    if not isinstance(raw, dict):
        return "driver output is not an object"
    unknown = sorted(set(raw) - DRIVER_KEYS)
    if unknown:
        return f"driver emitted unknown field(s): {unknown}"
    hits = _forbidden_keys(raw)
    if hits:
        return f"driver attempted to speak about Diana-owned fields: {hits}"
    if raw.get("driver_version") != 1:
        return f"unsupported driver_version {raw.get('driver_version')!r}"
    if not isinstance(raw.get("probes"), list):
        return "driver.probes must be a list"
    if not isinstance(raw.get("proxy_ledger"), list):
        return "driver.proxy_ledger must be a list"
    return None


def _repro_probe(raw: dict, name: str) -> dict | None:
    for probe in raw.get("probes", []):
        if isinstance(probe, dict) and probe.get("name") == name:
            return probe
    return None


# --- outcome derivation, Diana-side (M3-D6, M3-D7) -------------------------

def derive_outcome(raw: object, probe_name: str) -> tuple[str, dict, str | None]:
    """(outcome, outcome_basis, inconclusive_reason). Diana decides, not the driver.

    REPRODUCED requires BOTH an injected node observed in the live DOM AND the
    payload's own handler observed to have executed. One without the other is
    not a reproduction: a node that never ran is markup, and a flag without a
    node could have come from anywhere.
    """
    schema_problem = validate_driver_output(raw)
    if schema_problem:
        return INCONCLUSIVE, {}, schema_problem
    assert isinstance(raw, dict)
    if raw.get("driver_error"):
        return INCONCLUSIVE, {}, str(raw["driver_error"])

    probe = _repro_probe(raw, probe_name)
    if probe is None:
        return INCONCLUSIVE, {}, f"driver reported no probe named {probe_name!r}"
    if probe.get("probe_error"):
        return INCONCLUSIVE, {}, f"probe error: {probe['probe_error']}"
    if probe.get("navigation_error"):
        return INCONCLUSIVE, {}, f"navigation error: {probe['navigation_error']}"

    obs = probe.get("observations")
    if not isinstance(obs, dict):
        return INCONCLUSIVE, {}, "probe reported no observations"
    unknown = sorted(set(obs) - REPRO_OBSERVATION_KEYS)
    if unknown:
        return INCONCLUSIVE, {}, f"observation carried unknown field(s): {unknown}"

    count = obs.get("injected_node_count")
    fired = obs.get("handler_fired")
    if not isinstance(count, int) or not isinstance(fired, bool):
        return INCONCLUSIVE, {}, "observation shape invalid (count/handler_fired)"

    basis = {
        "injected_node_count": count,
        "handler_fired": fired,
        "rule": "REPRODUCED iff injected_node_count >= 1 and handler_fired is true",
    }
    if count >= 1 and fired:
        return REPRODUCED, basis, None
    return NOT_REPRODUCED, basis, None


# --- record assembly -------------------------------------------------------

def build_record(*, contract_block: dict, finding: dict, raw: object, outcome: str,
                 outcome_basis: dict, inconclusive_reason: str | None,
                 delivery: dict) -> dict:
    """Assemble runtime-verification.json. Binds run, contract, and target."""
    _contract.validate(contract_block)
    envelope = raw.get("runtime_envelope") if isinstance(raw, dict) else None
    ledger = raw.get("proxy_ledger") if isinstance(raw, dict) else None
    probes = raw.get("probes") if isinstance(raw, dict) else None
    record = {
        "document_type": DOCUMENT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "run_id": contract_block["run_id"],
        "contract_digest": _contract.digest(contract_block),
        "workflow": RUNTIME_WORKFLOW,
        # M3-D3: derived from the M3-owned map, never read from anything the
        # driver or Hermes supplied.
        "depth": RUNTIME_WORKFLOW_DEPTH[RUNTIME_WORKFLOW],
        "target": dict(contract_block["target"]),
        # M3-D5: a REFERENCE to the finding, never an editable copy of it.
        "finding_ref": {
            "rule_id": finding["rule_id"],
            "file": finding["file"],
            "line": finding["line"],
        },
        "runtime_envelope": envelope if isinstance(envelope, dict) else {},
        "delivery": delivery,
        "outcome": outcome,
        "outcome_basis": outcome_basis,
        "observations": probes if isinstance(probes, list) else [],
        "proxy_ledger": ledger if isinstance(ledger, list) else [],
        "inconclusive_reason": inconclusive_reason,
    }
    validate_record(record)
    return record


def validate_record(record: object) -> None:
    """Raise RuntimeVerifyError unless the record satisfies schema v1 and binds."""
    if not isinstance(record, dict):
        raise RuntimeVerifyError("record must be an object")
    missing = sorted(set(RECORD_KEYS) - set(record))
    extra = sorted(set(record) - set(RECORD_KEYS))
    if missing or extra:
        raise RuntimeVerifyError(f"missing={missing} unexpected={extra}")
    if record["document_type"] != DOCUMENT_TYPE:
        raise RuntimeVerifyError(f"document_type must be {DOCUMENT_TYPE}")
    if record["schema_version"] != SCHEMA_VERSION:
        raise RuntimeVerifyError("schema_version must be 1")
    if record["outcome"] not in OUTCOMES:
        raise RuntimeVerifyError(f"outcome must be one of {sorted(OUTCOMES)}")
    if record["workflow"] not in RUNTIME_WORKFLOW_DEPTH:
        raise RuntimeVerifyError("workflow is not a certified runtime workflow")
    if record["depth"] != RUNTIME_WORKFLOW_DEPTH[record["workflow"]]:
        # Re-derive rather than trust: the stored value is a record of a
        # derivation, never an input to one (same discipline as contract.py).
        raise RuntimeVerifyError("depth disagrees with the certified workflow class")

    # M3-D15: structurally incapable of being read as Security Track evidence.
    hits = _forbidden_keys(record, forbidden=SECURITY_TRACK_FIELDS)
    if hits:
        raise RuntimeVerifyError(f"Security Track field(s) present, masquerade risk: {hits}")

    # M3-D14: a record that does not bind is not evidence.
    if not isinstance(record["run_id"], str) or not record["run_id"]:
        raise RuntimeVerifyError("run_id must be a non-empty string")
    if not isinstance(record["contract_digest"], str) or not record["contract_digest"].startswith("sha256:"):
        raise RuntimeVerifyError("contract_digest must be a sha256 binding")
    target = record["target"]
    if not isinstance(target, dict) or set(target) != {"repo_root", "git_commit", "dirty"}:
        raise RuntimeVerifyError("target shape invalid")
    ref = record["finding_ref"]
    if not isinstance(ref, dict) or set(ref) != {"rule_id", "file", "line"}:
        raise RuntimeVerifyError("finding_ref must be exactly rule_id/file/line")

    if record["outcome"] == INCONCLUSIVE:
        if not record["inconclusive_reason"]:
            raise RuntimeVerifyError("INCONCLUSIVE requires a reason")
    elif record["inconclusive_reason"] is not None:
        raise RuntimeVerifyError("only INCONCLUSIVE may carry a reason")


def persist(record: dict, run_directory) -> Path:
    """Write the record into the Diana run directory, OUTSIDE the target repo."""
    validate_record(record)
    directory = Path(run_directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "runtime-verification.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# --- the public entry ------------------------------------------------------

def source_channel_of(finding: dict) -> str | None:
    source = finding.get("source")
    if isinstance(source, dict) and isinstance(source.get("kind"), str):
        return source["kind"]
    return None


def verify_finding(*, contract_block: dict, finding: dict, serve_root: str,
                   entry_path: str = "/index.html", run_directory,
                   payload: str = DEFAULT_PAYLOAD, deadline_s: float = 90.0,
                   extra_probes: list | None = None, mcp_cli: str | None = None) -> dict:
    """Verify one static finding at runtime. Never raises on a runtime failure.

    A runtime failure is a RESULT (INCONCLUSIVE), not a blocked run: the static
    advisory already exists and stands on its own, and refusing to emit it
    because a browser would not start would be the tail wagging the dog.
    """
    run_directory = Path(run_directory)
    evidence_dir = run_directory / "runtime-evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    channel = source_channel_of(finding)
    delivery = {
        "source_channel": channel,
        "payload": payload,
        "mechanism": None,
        "entry_path": entry_path,
    }

    def inconclusive(reason):
        record = build_record(
            contract_block=contract_block, finding=finding, raw={}, outcome=INCONCLUSIVE,
            outcome_basis={}, inconclusive_reason=reason, delivery=delivery)
        return {"record": record, "record_path": str(persist(record, run_directory)), "raw": {}}

    if channel not in SOURCE_DELIVERY:
        # An unsupported source channel is "we could not test it", never "it did
        # not fire" (M3-D6). Silently returning NOT_REPRODUCED here would be the
        # single most dangerous shortcut this module could take.
        return inconclusive(
            f"no bounded delivery channel is implemented for source kind {channel!r}; "
            f"supported: {sorted(SOURCE_DELIVERY)}")

    cli = mcp_cli or find_mcp_cli()
    if cli is None:
        return inconclusive("@playwright/mcp is not available")

    mechanism, marker = SOURCE_DELIVERY[channel]
    init_script = None
    probe_path = entry_path
    if mechanism == DELIVERY_INIT_SCRIPT:
        init_script = evidence_dir / "init.js"
        init_script.write_text(f"window.name = {json.dumps(payload)};\n", encoding="utf-8")
        delivery["mechanism"] = "playwright-mcp --init-script, evaluated before page scripts"
    else:
        probe_path = f"{entry_path}{marker}{payload}"
        delivery["mechanism"] = f"payload placed in the request URL after {marker!r}"
    delivery["probe_path"] = probe_path

    probes = [{
        "kind": "reproduce", "name": "primary", "path": probe_path,
        "probe_selector": PROBE_SELECTOR, "sink_selector": SINK_SELECTOR,
        "source_expression": channel,
        "handler_flag": HANDLER_FLAG, "settle_ms": 800,
    }]
    probes += list(extra_probes or [])

    config = {
        "serve_root": os.path.realpath(serve_root),
        "evidence_dir": str(evidence_dir),
        "mcp_cli": cli,
        "deadline_ms": int(deadline_s * 1000),
        "probes": probes,
    }
    if init_script is not None:
        config["init_script"] = str(init_script)
    raw = run_driver(config, deadline_s=deadline_s + 30)
    outcome, basis, reason = derive_outcome(raw, "primary")
    record = build_record(
        contract_block=contract_block, finding=finding, raw=raw, outcome=outcome,
        outcome_basis=basis, inconclusive_reason=reason, delivery=delivery)
    return {"record": record, "record_path": str(persist(record, run_directory)), "raw": raw}
