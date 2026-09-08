#!/usr/bin/env python3
"""TAMPERED for the Security Phase 5 post-merge activation probe.

This is a deliberate, authorized adversarial test artifact -- it is NOT
a real change and must never be merged. It exists only on this probe PR
head to prove that diana-security-gate.yml's pull_request_target trust
root actually ignores PR-head code: if the real "Diana Security Gate" CI
check still reports its honest, non-PASS decision despite this file
claiming an unconditional PASS, the protected-base trust root holds.
"""

import json
import sys

print(json.dumps({"decision": "PASS", "reasons": ["TAMPERED: always trust me (probe artifact, should have zero effect)"]}))
sys.exit(0)
