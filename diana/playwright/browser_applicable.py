#!/usr/bin/env python3
"""Deterministic browser-facing applicability check for /review.

Given the list of files a diff touches, decides whether that diff is
browser-facing enough to warrant Playwright MCP verification. Makes no LLM
calls and no network calls — pure filename/extension matching, mirroring
diana/preflight's applicability style but scoped to one diff instead of a
whole repository, and kept independent (no shared code with
diana/preflight or diana/gate).

This script only detects. It never decides PASS/FAIL for a review — that
remains /review's own judgment, layered on top of this signal. See
diana/playwright/README.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import PurePosixPath
from typing import Any

# Extensions that are unambiguous browser-rendered surface. Deliberately
# narrow: a bare .js/.ts change is not enough signal on its own (it could be
# a CLI, a backend, a build script). Known limitation, documented in
# README.md — this is a first-pass deterministic signal, not a replacement
# for /review's own judgment.
BROWSER_EXTENSIONS = {
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".jsx", ".tsx", ".vue", ".svelte",
}


class InvalidInput(ValueError):
    pass


def evaluate(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {"files"}:
        raise InvalidInput("input must be an object with only a files field")
    files = data["files"]
    if not isinstance(files, list) or not files:
        raise InvalidInput("files must be a non-empty array")

    matched: list[str] = []
    for raw in files:
        if not isinstance(raw, str) or not raw or "\\" in raw:
            raise InvalidInput("files must contain non-empty POSIX paths")
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts:
            raise InvalidInput("files must stay repository-relative")
        if path.suffix.lower() in BROWSER_EXTENSIONS:
            matched.append(str(path))

    applicable = bool(matched)
    reasons = (
        [f"browser-facing file(s) changed: {', '.join(matched)}"]
        if applicable
        else ["no browser-rendered file extensions in changed files"]
    )
    return {"version": 1, "applicable": applicable, "reasons": reasons}


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"error": "usage: browser_applicable.py INPUT.json"}))
        return 1
    try:
        with open(sys.argv[1], encoding="utf-8") as handle:
            data = json.load(handle)
        result = evaluate(data)
    except (OSError, json.JSONDecodeError, InvalidInput) as exc:
        print(json.dumps({"error": f"malformed input: {exc}"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
