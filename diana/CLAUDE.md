# Diana Claude Code Integration

Follow the canonical engineering policy in the project-root `AGENTS.md`.
This file adds only Claude Code integration details and does not replace or
duplicate that policy.

For non-trivial work, use Diana's workflow in order: `plan-review`,
`research-first`, `/fix` or the smallest sufficient implementation, relevant
tests, `/review`, then `/ship` for the evidence-backed handoff.

## Where things live

- `skills/plan-review.md` — one-pass multi-lens plan sanity check.
- `skills/research-first.md` — inspection discipline before fixing/answering.
- `commands/fix.md`, `commands/review.md`, `commands/ship.md` — slash commands
  for the corresponding loop stages.
- `hooks/` — the safety hook (destructive command warnings) and the cost log.
- `memory/` — canonical project decisions and known issues when installed as
  part of the project. Live loop state remains in root `STATE.md`,
  `RUN_LOG.md`, and `BUDGET.md`.

## Claude-specific boundaries

The `check-careful.sh` PreToolUse hook warns about recognized destructive shell
patterns. Do not route around it. It is a guardrail, not a sandbox or complete
execution-isolation boundary. If it warns, stop and explain the risk rather
than retrying an equivalent command.

Claude model selection remains user/provider configuration. Model choice never
weakens approval, review, actor/verifier separation, or the protected-branch
human merge floor defined by `AGENTS.md`.
