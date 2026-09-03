# Diana Risk Tiers

> POLICY / CLASSIFICATION — NOT YET HARD ENFORCEMENT

This provider-neutral policy classifies proposed actions and changes. A human
review requirement is not the same as a prohibition on agent execution. Runtime
permissions, sandboxing, hooks, the Diana Gate, branch protection, and human
review remain separate controls.

When several tiers apply, use the highest tier. Ambiguous production or external
impact is `HUMAN_ONLY` until a human resolves it.

## SAFE

Reversible, local work within the explicitly authorized repository scope:

- read, inspect, and search
- normal tests and static analysis
- documentation or source edits with no sensitive operational effect
- reversible local Git operations that do not rewrite shared history

SAFE work may be executed by an appropriately permissioned workflow. It still
requires Definition-of-Done and verification evidence before passing a gate.

## CONSEQUENTIAL

Work with meaningful product, data, operational, financial, or communication
impact that an agent may prepare only through an explicit workflow. Human review
is required before the change takes effect or enters a protected branch.

Examples include schema changes that are demonstrably non-destructive, access-
control implementation, dependency changes with material runtime impact, and
drafts of consequential outbound communication.

Classification: `requires_human_review: true`, `autonomous_execution: only when
explicitly certified and scoped`.

## DANGEROUS

High-blast-radius or difficult-to-reverse work. An agent may inspect and propose
a plan or patch in an isolated fixture, but must not perform the real external
or shared-state action autonomously.

Examples include production deployment preparation, destructive migration
design, credential rotation planning, payment infrastructure modification, and
security-control modification.

Classification: `requires_human_review: true`, `autonomous_execution: false for
the consequential action`.

## HUMAN_ONLY

The following actions are a hard human execution floor. Agents may gather
evidence or prepare a reversible proposal, but may not autonomously execute the
real action:

| Category | Deterministic indicator examples |
|---|---|
| Production deployment | production deploy/release workflow or production environment mutation |
| Destructive database operation | `DROP`, `TRUNCATE`, destructive migration, or irreversible production schema/data mutation |
| Production data deletion | deletion or purge targeting production/shared customer data |
| Credential changes | key/token/secret rotation, replacement, revocation, or permission change |
| Payment/billing infrastructure | live payment configuration, billing rules, payout, or merchant-of-record changes |
| Security-control changes | disabling/bypassing authentication, authorization, audit, encryption, firewall, or security checks |
| Destructive shared Git | force-push, shared-history rewrite, or deletion of a shared protected branch |
| External publication | publishing/releasing externally or changing public production content |
| Consequential outbound communication | sending customer, legal, financial, incident, or public communications |
| Branch/CI protection | enabling, disabling, weakening, or bypassing protected-branch or required-check/review settings |

Classification: `requires_human_review: true`, `autonomous_execution: false`.

Protected-branch merge is always human-approved even when the underlying change
is SAFE. An agent may later prepare a branch, commit, or pull request only after
that execution path is independently certified.

## Gate mapping

- SAFE with complete valid evidence: eligible for `PASS`.
- CONSEQUENTIAL or DANGEROUS diff: `REQUIRE_HUMAN` unless a narrower rule fails.
- HUMAN_ONLY condition: `REQUIRE_HUMAN`; the external action itself remains
  forbidden to autonomous execution.
- Missing/malformed evidence or blocker preflight: `FAIL`.
- A non-applicable stack-specific check: `SKIP`, never `FAIL` merely because the
  technology is absent.
