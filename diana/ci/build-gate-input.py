#!/usr/bin/env python3
"""Build deterministic Diana Gate input from a pull-request event."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

BEGIN = "<!-- DIANA:EVIDENCE"
END = "DIANA:EVIDENCE -->"
EVIDENCE_FIELDS = {"dod", "verification", "preflight", "risk", "human_only_conditions"}


def parse_evidence(body: Any) -> dict[str, Any]:
    if not isinstance(body, str) or body.count(BEGIN) != 1 or body.count(END) != 1:
        raise ValueError("PR body must contain exactly one Diana evidence block")
    raw = body.split(BEGIN, 1)[1].split(END, 1)[0].strip()
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != EVIDENCE_FIELDS:
        raise ValueError("Diana evidence fields are invalid")
    return value


def changed_files(base: str, head: str) -> list[str]:
    if not base or not head:
        raise ValueError("base/head SHA missing")
    output = subprocess.check_output(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", "-z", base, head]
    )
    files = [item.decode("utf-8") for item in output.split(b"\0") if item]
    if not files:
        raise ValueError("pull request diff is empty")
    return files


def build(event: Any, files: list[str]) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, dict):
        raise ValueError("pull_request event data missing")
    evidence = parse_evidence(pull_request.get("body"))
    return {
        "version": 1,
        "dod": evidence["dod"],
        "verification": evidence["verification"],
        "preflight": evidence["preflight"],
        "diff": {"risk": evidence["risk"], "files": files},
        "human_only_conditions": evidence["human_only_conditions"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("event")
    parser.add_argument("output")
    parser.add_argument("--files-json", help="test-only changed-file array")
    args = parser.parse_args()

    try:
        event = json.loads(Path(args.event).read_text(encoding="utf-8"))
        if args.files_json is not None:
            files = json.loads(args.files_json)
            if not isinstance(files, list):
                raise ValueError("--files-json must be an array")
        else:
            files = changed_files(os.environ.get("DIANA_BASE_SHA", ""), os.environ.get("DIANA_HEAD_SHA", ""))
        result = build(event, files)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, subprocess.SubprocessError) as exc:
        # Emit invalid versioned input so the gate, rather than this adapter,
        # records the fail-closed decision in its normal result format.
        result = {"version": 0, "adapter_error": str(exc)}
    Path(args.output).write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
