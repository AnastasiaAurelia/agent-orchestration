#!/usr/bin/env python3
"""Reduce Diana Preflight output to the exact shape diana-gate.py accepts.

Reads preflight.py's JSON result (stdin or a file argument) and writes the
`preflight` array diana-gate.py's input schema expects — id, applicable,
severity, result only. This is the only integration point between the two
components; neither is modified to know about the other's internals.

Usage:
    python3 preflight.py REPO_ROOT | python3 reduce_for_gate.py
"""

from __future__ import annotations

import json
import sys


def reduce_checks(preflight_result: dict) -> list[dict]:
    checks = preflight_result.get("checks")
    if not isinstance(checks, list):
        raise ValueError("preflight result has no checks array")
    reduced = []
    for check in checks:
        reduced.append({
            "id": check["id"],
            "applicable": check["applicable"],
            "severity": check["severity"],
            "result": check["result"],
        })
    return reduced


def main() -> int:
    raw = sys.stdin.read() if len(sys.argv) == 1 else open(sys.argv[1], encoding="utf-8").read()
    try:
        preflight_result = json.loads(raw)
        if "error" in preflight_result:
            raise ValueError(f"preflight did not complete: {preflight_result['error']}")
        reduced = reduce_checks(preflight_result)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(reduced, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
