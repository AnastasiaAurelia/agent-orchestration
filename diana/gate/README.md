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

## Security Phase 5: a separate check, not a code change here

`evaluate()`, this file's input schema, and its whole CLI behavior are
**completely unchanged** by Security Phase 5 -- there is no "combine"
mode and no Security-specific branching in this file beyond the
`REVIEW_PATHS` addition above. Security evidence is evaluated by an
entirely separate required CI check
(`.github/workflows/diana-security-gate.yml`, see
[`diana/ci/README.md`](../ci/README.md)) running independent, separate
code (`diana/security/security_bundle.py`/`security_reducer.py`).
GitHub branch protection requiring BOTH checks reproduces the intended
"existing FAIL or security FAIL blocks merge; either check being
REQUIRE_HUMAN still leaves the required-review rule blocking merge;
both clean allows merge" combination without any code here needing to
know the Security Gate exists at all. See `diana/security/README.md`'s
"Security Phase 5" section for the full trust-boundary design and why
this separation (rather than one workflow computing both) is the point.
