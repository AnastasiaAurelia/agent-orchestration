#!/usr/bin/env python3
"""Render a Diana Gate JSON result as a GitHub step summary."""

import json
import sys

# A summary is the only place many readers look, so it must render even when the
# gate could not. An unreadable or shapeless result document is reported AS a
# failure rather than as a traceback -- the required check has already failed
# closed by then, and a stack trace in the summary hides that from the reader.
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        result = json.load(handle)
    if not isinstance(result, dict) or not isinstance(result.get("decision"), str):
        raise ValueError("gate result is not a decision document")
except (OSError, IndexError, ValueError, json.JSONDecodeError) as exc:
    print("## Diana Gate")
    print()
    print("Decision: **FAIL**")
    print()
    print(f"The gate produced no readable result document: {exc}")
    raise SystemExit(0)

print("## Diana Gate")
print()
print(f"Decision: **{result['decision']}**")
if result["decision"] == "REQUIRE_HUMAN":
    print()
    print(
        "This check succeeds; merge is blocked by the independent required "
        "human/code-owner review rule, not by this check."
    )
elif result["decision"] == "FAIL":
    print()
    print("This check fails; merge is blocked by the required Diana Gate status.")
if result.get("checks"):
    print("\nChecks:")
    for check in result["checks"]:
        print(f"- `{check['id']}`: {check['result']}")
if result.get("reasons"):
    print("\nReasons:")
    for reason in result["reasons"]:
        print(f"- {reason}")
