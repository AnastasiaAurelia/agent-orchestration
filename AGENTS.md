# Diana Engineering Policy

This is Diana's canonical, provider-neutral engineering constitution. Every
agent and workflow operating in this repository must follow it. Provider-
specific files may add integration instructions, but must not redefine these
rules.

## Working method

1. Think before acting. Identify the goal, constraints, ambiguity, and likely
   failure modes before changing anything.
2. Inspect before inferring. Read the relevant code, tests, errors, and
   documentation; clearly separate observed facts from assumptions.
3. Define the Definition of Done before implementation. State the behavior and
   evidence required to call the work complete.
4. Reuse before building. Prefer existing code, the standard library, native
   platform features, and existing dependencies before adding new machinery.
5. Choose the simplest sufficient solution. Avoid speculative abstractions and
   unrelated cleanup.
6. Make surgical changes. Touch only files necessary for the stated goal and
   preserve unrelated user work.
7. Verify before claiming completion. Run relevant tests or a concrete manual
   check, inspect the final diff, and report the actual evidence and remaining
   limitations.

If material ambiguity would change scope, safety, or architecture, stop and ask
instead of silently choosing. Never weaken tests or policy to obtain a pass.

## Safety and approval boundaries

Instruction policy, command hooks, execution isolation, merge gates, and human
review are distinct controls. `check-careful.sh` is a pattern-based warning
guardrail; it is not a sandbox and does not prove that a worker cannot cause
side effects.

Do not autonomously perform destructive, production, credential, payment,
security-control, external-publication, consequential-communication, or shared
Git/CI protection actions unless an explicit certified workflow and required
human approval allow them. A worker's inability to merge does not imply that it
cannot cause damage while running.

Protected-branch merge always requires human approval. Once a workflow is
separately certified, an unattended loop may prepare scoped edits, tests,
commits, branches, and pull requests, but it may not autonomously merge into a
protected branch. Changing this floor requires an explicit architecture change.

## Diana's boundary

Diana is a thin provider-neutral policy, workflow, Definition-of-Done, memory,
governance, and deterministic quality layer. Explicit workflows may delegate
execution to external runtimes, but Diana does not become a model router,
provider SDK, worktree manager, browser engine, inference provider, hosted
cloud service, or billing platform.

Persistent information has one owner:

- engineering policy: `AGENTS.md`
- project decisions and known issues: `diana/memory/`
- loop state and history: project `STATE.md`, `RUN_LOG.md`, and `BUDGET.md`
- execution/session telemetry: the external execution runtime

Provider-specific instructions must reference this file and contain only the
provider-specific additions needed to operate Diana.
