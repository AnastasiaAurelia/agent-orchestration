#!/usr/bin/env python3
"""Build deterministic Diana Gate input from a pull-request event."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

BEGIN = "<!-- DIANA:EVIDENCE"
END = "DIANA:EVIDENCE -->"
EVIDENCE_FIELDS = {"dod", "verification", "preflight", "risk", "human_only_conditions"}

# Longest adapter reason that may reach a GitHub annotation. A reason is a
# structural description -- marker counts, field names, a JSON position -- and
# never repository content, so this is a formatting bound, not a redaction.
MAX_REASON_CHARS = 400


def safe_reason(text: Any) -> str:
    """One printable line, safe to hand to a GitHub workflow command.

    Two jobs. Annotations are line-oriented, so an embedded newline would end
    the command and the rest of the text would be echoed as raw log output; and
    the text can quote field names taken from a PULL-REQUEST BODY, which is
    untrusted input, so a body must not be able to inject a workflow command
    such as `::add-mask::` or `::error::` of its own. Control characters and
    `::` are therefore neutralised rather than escaped.
    """
    collapsed = " ".join(str(text).split())
    printable = "".join(c if c.isprintable() else " " for c in collapsed)
    neutralised = printable.replace("::", ":.:")
    if len(neutralised) > MAX_REASON_CHARS:
        neutralised = neutralised[:MAX_REASON_CHARS - 3] + "..."
    return neutralised or "no reason recorded"


def parse_evidence(body: Any) -> dict[str, Any]:
    """The evidence block, or a ValueError naming exactly what was wrong.

    Every refusal here used to collapse into one sentence, and the sentence was
    then discarded by the gate. Six different author mistakes -- no block, two
    blocks, bad JSON, a missing field, an unknown field, an empty body -- were
    indistinguishable in CI, so a contributor had nothing to act on. The
    refusals are unchanged; only their reasons are now specific.

    Nothing here echoes the body. A reason names marker counts, key names and a
    JSON position, never the surrounding document.
    """
    if not isinstance(body, str) or not body.strip():
        raise ValueError(
            "pull request body is empty, so it carries no Diana evidence block; "
            f"add one delimited by {BEGIN} ... {END}")
    begins, ends = body.count(BEGIN), body.count(END)
    if begins != 1 or ends != 1:
        raise ValueError(
            f"pull request body must contain exactly one Diana evidence block, but it "
            f"has {begins} opening and {ends} closing marker(s); expected exactly one "
            f"{BEGIN} and one {END} (a marker quoted in prose or a template counts)")
    raw = body.split(BEGIN, 1)[1].split(END, 1)[0].strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Position only. `exc.doc` holds the block's text and is deliberately
        # not interpolated.
        raise ValueError(
            "the Diana evidence block is not valid JSON: "
            f"{exc.msg} at line {exc.lineno} column {exc.colno}") from None
    if not isinstance(value, dict):
        raise ValueError(
            f"the Diana evidence block must be a JSON object, not {type(value).__name__}")
    missing = sorted(EVIDENCE_FIELDS - set(value))
    unexpected = sorted(str(key) for key in set(value) - EVIDENCE_FIELDS)
    if missing or unexpected:
        raise ValueError(
            f"the Diana evidence block has the wrong fields: missing={missing} "
            f"unexpected={unexpected}; expected exactly {sorted(EVIDENCE_FIELDS)}")
    return value


# Added, Copied, Modified, Renamed -- and Deleted.
#
# `D` was missing, and its absence was a fail-OPEN hole rather than a cosmetic
# gap: the gate decides that a change needs human review by matching
# `diff.files` against its own REVIEW_PATHS, so a path the gate never sees is a
# path the gate cannot protect. A pull request that DELETED `.github/CODEOWNERS`
# or `diana/gate/diana-gate.py` while touching one ordinary file was measured
# reaching the gate as that ordinary file alone -- risk SAFE, no sensitive path,
# decision PASS. Removing a protected file must be at least as reviewable as
# editing it.
DIFF_FILTER = "ACMRD"


def changed_files(base: str, head: str) -> list[str]:
    if not base or not head:
        raise ValueError(
            "base/head SHA missing: DIANA_BASE_SHA and DIANA_HEAD_SHA must both be set "
            "from the pull_request event")
    output = subprocess.check_output(
        ["git", "diff", "--name-only", f"--diff-filter={DIFF_FILTER}", "-z", base, head]
    )
    files = [item.decode("utf-8") for item in output.split(b"\0") if item]
    if not files:
        raise ValueError(
            f"the pull request diff is empty between {base[:12]} and {head[:12]}; "
            "the gate has nothing to evaluate")
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
        #
        # AND SAY SO. This step used to fail silently: it exited 0, printed
        # nothing, and showed green in the Actions UI, while the only record of
        # what actually went wrong sat in a JSON field the gate then discarded.
        # A real pull request was blocked for a day by "input fields or version
        # are invalid" with the true reason -- a missing evidence block --
        # written down nowhere a human could see it.
        #
        # The exit code stays 0 on purpose: a non-zero exit here would abort the
        # job before the gate runs, and the required check would have no result
        # document at all. The gate still owns the decision; this only makes the
        # reason visible.
        reason = safe_reason(exc)
        result = {"version": 0, "adapter_error": reason}
        print(f"::error title=Diana Gate input::{reason}", file=sys.stderr)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            try:
                with open(summary, "a", encoding="utf-8") as handle:
                    handle.write("## Diana Gate input\n\nCould not build gate input "
                                 f"from this pull request:\n\n> {reason}\n\n")
            except OSError:
                pass
    Path(args.output).write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
