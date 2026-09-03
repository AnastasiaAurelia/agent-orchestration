#!/usr/bin/env python3
"""Render a Diana Gate JSON result as a GitHub step summary."""

import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    result = json.load(handle)

print("## Diana Gate (advisory)")
print()
print(f"Decision: **{result['decision']}**")
if result.get("checks"):
    print("\nChecks:")
    for check in result["checks"]:
        print(f"- `{check['id']}`: {check['result']}")
if result.get("reasons"):
    print("\nReasons:")
    for reason in result["reasons"]:
        print(f"- {reason}")
