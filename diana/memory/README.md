# Memory

Short, project-specific notes worth carrying across sessions — not a system,
just files.

Two files, created on demand, not up front:

- `decisions.md` — one line per non-obvious decision and why (e.g. "chose
  polling over websockets: infra doesn't support sticky sessions yet").
- `known-issues.md` — things that are broken or deliberately deferred, so
  they don't get "rediscovered" and re-litigated every session.

Rules:

- Only write here what isn't already obvious from reading the code or git
  log. Don't duplicate the commit history or the README.
- Check this directory at the start of a session if it's non-empty.
- Update it when you learn something that would otherwise cost another
  investigation next time — not after every task.
- If it's still empty after a few weeks of real use, that's fine — it means
  the code and commits were self-explanatory enough not to need it.
