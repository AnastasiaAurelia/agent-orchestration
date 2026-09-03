#!/usr/bin/env python3
"""Synthetic advisory-CI classification matrix."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = Path(__file__).with_name("build-gate-input.py")
GATE = ROOT / "diana/gate/diana-gate.py"
spec = importlib.util.spec_from_file_location("diana_ci_builder", BUILDER_PATH)
assert spec and spec.loader
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def body(evidence: dict) -> str:
    return f"Summary\n\n<!-- DIANA:EVIDENCE\n{json.dumps(evidence)}\nDIANA:EVIDENCE -->"


BASE = {
    "dod": {"present": True, "evidence": ["acceptance checked"]},
    "verification": {"present": True, "evidence": ["tests passed"]},
    "preflight": [],
    "risk": "SAFE",
    "human_only_conditions": [],
}


def run(name: str, evidence: dict | None, files: list[str], expected: str, expected_check: str = "") -> None:
    event = {"pull_request": {"body": body(evidence) if evidence is not None else "missing"}}
    try:
        gate_input = builder.build(event, files)
    except (ValueError, json.JSONDecodeError) as exc:
        gate_input = {"version": 0, "adapter_error": str(exc)}
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
        json.dump(gate_input, handle)
        handle.flush()
        proc = subprocess.run(["python3", str(GATE), handle.name], text=True, capture_output=True)
    result = json.loads(proc.stdout)
    assert result["decision"] == expected, (name, result)
    if expected_check:
        assert {"id": expected_check, "result": "SKIP"} in result["checks"], result
    expected_exit = {"PASS": 0, "FAIL": 1, "REQUIRE_HUMAN": 2}[expected]
    assert proc.returncode == expected_exit, (name, proc.returncode)
    print(f"PASS {name}: {expected}")


run("safe", dict(BASE), ["src/app.py"], "PASS")
missing_dod = dict(BASE, dod={"present": False, "evidence": []})
run("missing-dod", missing_dod, ["src/app.py"], "FAIL")
blocker = dict(BASE, preflight=[{"id": "secret-scan", "applicable": True, "severity": "BLOCKER", "result": "FAIL"}])
run("blocker-preflight", blocker, ["src/app.py"], "FAIL")
human = dict(BASE, risk="HUMAN_ONLY", human_only_conditions=["production_deploy"])
run("human-only", human, ["infra/production/release.yml"], "REQUIRE_HUMAN")
irrelevant = dict(BASE, preflight=[{"id": "stripe", "applicable": False, "severity": "BLOCKER", "result": "SKIP"}])
run("irrelevant-stack", irrelevant, ["src/app.py"], "PASS", "stripe")
run("malformed-evidence", None, ["src/app.py"], "FAIL")
run("nearby-safe", dict(BASE), ["tests/fixtures/production-deploy-example.txt"], "PASS")
run("sensitive-path-escalation", dict(BASE), [".github/workflows/deploy.yml"], "REQUIRE_HUMAN")

print("MATRIX false_positives=0 false_negatives=0 cases=8")
