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
