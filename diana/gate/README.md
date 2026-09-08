# Diana Gate

`diana-gate.py` is a deterministic local merge-boundary check. It makes no LLM
calls. It reads one versioned JSON evidence file and emits one JSON result.

Exit codes:

- `0`: `PASS`
- `1`: `FAIL` (including malformed input; fail closed)
- `2`: `REQUIRE_HUMAN`

Input version 1 requires exactly these top-level fields:

```json
{
  "version": 1,
  "dod": {"present": true, "evidence": ["acceptance criteria checked"]},
  "verification": {"present": true, "evidence": ["tests passed"]},
  "preflight": [
    {"id": "web-only", "applicable": false, "severity": "BLOCKER", "result": "SKIP"}
  ],
  "diff": {"risk": "SAFE", "files": ["src/example.py"]},
  "human_only_conditions": []
}
```

Risk and `human_only_conditions` are classifications produced before the gate.
The gate validates them, applies sensitive-path escalation, checks evidence and
blocker preflight results, and produces the deterministic final decision. It is
a merge-boundary control, not execution isolation.

The `preflight` array is normally produced by
[`diana/preflight`](../preflight/README.md), reduced to this schema by
`diana/preflight/reduce_for_gate.py`. Preflight detects quality/safety
conditions; this gate decides whether the supplied evidence is allowed to
proceed. The two stay separate components on purpose.

`REVIEW_PATHS`/`REVIEW_PREFIXES` (deterministic sensitive-path escalation
to `REQUIRE_HUMAN` regardless of self-declared `risk`) additionally cover
the Security Track's own enforcement surface as of Security Phase 5 (see
[`diana/security/README.md`](../security/README.md#security-phase-5----security-gate--ci-integration)):
the catalog, evidence model, bundle/reducer policy, and the Gate/CI wiring
that runs them.

## Security Phase 5: `combine` mode

`diana-gate.py combine GATE_RESULT.json SECURITY_RESULT.json` is a
second, purely additive CLI mode -- `evaluate()` and the input schema
above are completely unchanged. It combines an already-computed Gate
decision with an independently-computed Security decision
(`{"decision": "PASS"|"REQUIRE_HUMAN"|"FAIL"|"SKIPPED_BOOTSTRAP",
"reasons": [...]}`, produced by
[`diana/ci/run-security-gate.py`](../ci/run-security-gate.py)) via
`combine_with_security()`:

    existing FAIL or security FAIL              -> final FAIL
    otherwise existing or security REQUIRE_HUMAN -> final REQUIRE_HUMAN
    otherwise                                    -> final PASS

`"SKIPPED_BOOTSTRAP"` (the Security Phase 5 PR's own bootstrap case) is a
pure pass-through: the existing Gate decision is returned unchanged. Same
exit-code convention as above. See `diana/security/README.md`'s "Security
Phase 5" section for the full trust-boundary design.
