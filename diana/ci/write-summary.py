#!/usr/bin/env python3
"""Render a Diana Gate JSON result as a GitHub step summary."""

import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    result = json.load(handle)

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
