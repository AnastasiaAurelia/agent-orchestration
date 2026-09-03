# Agent Orchestration

A reusable, provider-neutral engineering policy and workflow base with Claude
Code integration.

This repo installs a small, portable policy and Claude Code workflow into any
project so compatible agents share the same engineering constitution while
Claude can use a repeatable workflow:

```text
plan -> inspect -> fix -> test -> review -> ship
```

It is intentionally not a giant AgentOps platform, not a multi-agent
marketplace, not a router service, and not a dashboard. The goal is simple:
make agentic project work disciplined and verifiable inside every repo.

## Why This Exists

Starting every new project from scratch wastes time. Most projects need the
same operating rules:

* understand the task before editing
* inspect code before guessing
* avoid unnecessary abstractions
* make the smallest safe change
* run relevant tests/checks
* review the diff before shipping
* block dangerous commands
* keep a clear handoff

This repo packages those rules into a reusable base.

## What It Solves

Agent Orchestration helps prevent common agentic coding failures:

* confident guesses made before reading the code
* broad rewrites when a small patch would work
* missing test or verification evidence
* unrelated cleanup mixed into a fix
* accidental destructive shell commands
* unclear final handoffs
* prompt-folder bloat that becomes hard to install, audit, or remove

## Folder Structure

```text
diana/
├── CLAUDE.md
├── skills/
│   ├── plan-review.md
│   ├── research-first.md
│   └── minimal-solution.md
├── commands/
│   ├── fix.md
│   ├── review.md
│   └── ship.md
├── hooks/
│   ├── hooks.json
│   ├── check-careful.sh
│   └── cost-report.md
└── memory/
    └── README.md

install.sh
verify.sh
uninstall.sh
AGENTS.md
```

## Core Files

### `AGENTS.md`

The canonical provider-neutral engineering policy: inspect before inferring,
define Definition of Done, reuse before build, make surgical changes, and
verify before claiming completion. It also establishes the protected-branch
human merge floor and distinguishes instruction/hook guardrails from execution
isolation.

### `diana/CLAUDE.md`

Claude-specific integration that references `AGENTS.md` and exposes the default
loop:

```text
task -> inspect -> plan -> implement -> test -> review -> ship
```

It also sets non-negotiables:

* no guessing before inspection
* no unrelated rewrites
* smallest safe change first
* no ship without test/check result
* destructive commands must be gated

### `skills/plan-review.md`

A compact planning review skill inspired by role-based review chains.

It checks:

* framing
* architecture and edge cases
* user/developer friction
* risk

It ends with a verdict:

```text
proceed / proceed with changes / stop and ask
```

### `skills/research-first.md`

Forces Claude to read the actual error, code, docs, or repo context before
forming a hypothesis.

It separates:

```text
observed facts
inferred causes
unknowns
```

### `skills/minimal-solution.md`

A minimal-change skill that prevents overengineering.

It asks:

1. Does this need to exist?
2. Can existing code solve it?
3. Can the standard library solve it?
4. Can the platform/framework solve it?
5. Can an already-installed dependency solve it?
6. Can one small change solve it?
7. Only then write new code.

It does not allow skipping validation, error handling, security, accessibility,
or explicit requirements.

## Commands

### `/fix`

Use for bugs, failing tests, runtime errors, or broken behavior.

Expected loop:

```text
inspect -> root cause -> smallest patch -> test -> repeat if needed
```

The command should report:

* root cause
* files changed
* fix applied
* commands run
* result
* remaining risks

### `/review`

Reviews the current diff without fixing it.

It checks:

* bugs
* broken assumptions
* overengineering
* missing tests
* security risks
* unclear naming
* unrelated changes

Overengineering findings use concise tags:

```text
delete:
stdlib:
native:
yagni:
shrink:
```

### `/ship`

Final handoff before considering work complete.

It reports:

* goal
* implementation summary
* changed files
* tests/checks run
* known limitations
* final SHIP / NO-SHIP verdict

It should default to `NO-SHIP` if no test/check was actually run.

## Hooks

### `check-careful.sh`

A safety guard for dangerous shell commands.

It warns or asks before commands like:

* `rm -rf`
* `DROP TABLE`
* `TRUNCATE`
* `git push --force`
* `git reset --hard`
* `git checkout .`
* `kubectl delete`
* `docker rm -f`
* `docker system prune`

### `cost-report.md`

A command for reporting session/token/cost logs if the Stop hook is available
in the local Claude Code environment.

## Install

From this repo:

```bash
./install.sh /path/to/your/project
```

Or install into the current directory:

```bash
cd /path/to/your/project
/path/to/agent-orchestration/install.sh
```

The installer:

* creates `.claude/` folders if needed
* installs Diana commands and skills
* installs the safety hook
* installs the safety hook into committed `.claude/settings.json`
* keeps the optional cost hook in `.claude/settings.local.json`
* appends marked Diana sections into root `AGENTS.md` and `CLAUDE.md`
* backs up existing files before overwriting
* is safe to run repeatedly

## Verify

```bash
./verify.sh /path/to/your/project
```

This checks whether Diana is installed correctly.

It verifies:

* required files exist
* `check-careful.sh` is executable
* portable and local hook configs exist in their intended scopes
* root `AGENTS.md` and `CLAUDE.md` contain their Diana sections

## Uninstall

```bash
./uninstall.sh /path/to/your/project
```

The uninstaller removes only Diana-installed files and Diana hook entries. It
does not delete user-created `.claude` files or the whole `.claude` folder.

## Example Workflow

After installing Diana into a project, use Claude Code like this:

```text
Use Diana protocol.

Run /fix on this bug:

[paste error or broken behavior]

Rules:
- inspect before editing
- state root cause in one sentence before patching
- use minimal-solution
- make the smallest safe change
- run the relevant test/check
- do not call it done without test result
```

Then:

```text
Run /review on the current diff.
```

Then:

```text
Run /ship.
```

## Safety Notes

* The hook is a guardrail, not a replacement for judgment.
* The installer backs up overwritten files with `.bak.<timestamp>` suffixes.
* The uninstaller removes only Diana-managed files and settings entries.
* Do not commit scratch repos, reference repos, generated backups, `node_modules`,
  `.env` files, or secrets.
* Do not force push this repository unless you explicitly intend to rewrite
  public history.

## Current Limitations

The Stop hook cost logging depends on the local Claude Code environment and
Node availability. If `/cost-report` is empty, check whether Node is on `PATH`
and whether the Stop hook is firing in your actual Claude Code session.

The hook command patterns are intentionally conservative. They catch common
dangerous commands, but they are not a full shell parser.

## v0.1 Philosophy

This project exists to avoid prompt-folder bloat.

Do not add:

* dashboards
* agent marketplaces
* multi-tool adapters
* router services
* huge benchmark harnesses
* unnecessary plugin systems

The base should stay small enough to understand, install, verify, and remove
safely.

v0.1 is focused on one thing:

```text
Make Claude plan, fix, review, and ship more reliably inside any project.
```

<!-- diana-phase1g-test-a: disposable marker, safe to remove -->
