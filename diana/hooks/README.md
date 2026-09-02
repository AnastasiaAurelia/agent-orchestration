# Claude Code Hook Layout

Verified against the current Claude Code documentation on 2026-09-02:

- shared project settings belong in committed `.claude/settings.json`
- project-local settings belong in `.claude/settings.local.json`
- list-valued settings such as hooks merge across scopes
- all matching hooks run; identical settings handlers run once
- current `PreToolUse` decisions use
  `hookSpecificOutput.permissionDecision`

Sources:

- https://code.claude.com/docs/en/settings
- https://code.claude.com/docs/en/hooks

Diana therefore commits the destructive-command guardrail in shared settings
so Git worktrees inherit it. The optional cost log remains local. AO or user
hooks may coexist in local settings. Hooks are not a sandbox: a missing,
crashed, or timed-out command hook is not complete execution isolation.
