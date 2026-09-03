# Memory

Short, project-specific notes worth carrying across sessions — not a system,
just files.

Canonical project memory has four file owners:

- `decisions.md` — one line per non-obvious decision and why (e.g. "chose
  polling over websockets: infra doesn't support sticky sessions yet").
- `known-issues.md` — things that are broken or deliberately deferred, so
  they don't get "rediscovered" and re-litigated every session.
- `advisor-log.md` — one entry per manual advisor consult offered via
  `plan-review.md` (including declined ones), created or updated
  immediately when `plan-review.md` reminds you to — not left for later.
  Per entry: date, which trigger fired, choice (none/opus/fable), one-line
  context, and outcome:
    - `plan-changed` — approach after the consult is materially different.
    - `plan-confirmed` — approach didn't change, but the consult exposed a
      risk, validated a genuinely uncertain assumption, or prevented
      further investigation — say which in the note. A restatement of
      what was already known is `no-value`, not this.
    - `no-value` — generic, already-known, or a "confirmation" that
      didn't meet the `plan-confirmed` bar above.
    - `not-applied` — guidance wasn't followed; note why (appeared
      incorrect / incompatible with constraints / executor failed to
      follow it / decision changed before implementation / outcome still
      unknown) without asserting it was good advice unless later evidence
      confirms that.
  Exists to let Opus and Fable be compared on real occasions later — not
  to justify using either by default.
- `learned-rules.md` — only durable behavioral rules explicitly approved by
  a human. Diana does not infer or write rules here autonomously.

Rules:

- Only write here what isn't already obvious from reading the code or git
  log. Don't duplicate the commit history or the README.
- Put each fact in exactly one owner file; link to it rather than copying it
  into `AGENTS.md`, `CLAUDE.md`, loop state, or worker-specific memory.
- Check this directory at the start of a session if it's non-empty.
- Update it when you learn something that would otherwise cost another
  investigation next time — not after every task.
- Empty owner files are valid; they make ownership explicit without inventing
  project history.

Loop execution state/history remains in project-root `STATE.md`, `RUN_LOG.md`,
and `BUDGET.md`. External worker/session telemetry belongs to its execution
runtime, not this directory.
