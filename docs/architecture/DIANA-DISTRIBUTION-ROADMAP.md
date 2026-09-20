# Track D — Distribution, Packaging and Productization (ROADMAP — NOT IMPLEMENTED)

Status: **planning only.** Nothing is packaged, released or installed by this document. No command is
added.

Goal: make Diana usable by someone who did not build it — **without weakening the architecture to
improve convenience.**

---

## Measured baseline

| | Finding |
|---|---|
| Packaging | **None.** No `pyproject.toml`, `setup.py`, `setup.cfg` or requirements file exists. |
| Runtime dependencies | The **Python standard library** plus the pinned Hermes installation. Diana's own modules import no third-party package. |
| Entry point | `./diana-do`, a root shell launcher that resolves the Hermes venv and execs the product CLI. Not on `PATH`. |
| Installed surface | `install.sh` copies **four** commands into a consumer project's `.claude/commands/` — `fix`, `review`, `ship`, `cost-report`. The product CLI does **not** travel. |
| Python | Exercised on 3.11. Floor not formally declared. |
| Linux-specific | `/proc` (process ownership, quiescence), `flock` (run lease), plus the browser/runtime integration. |

The single biggest friction is the last row of that table combined with the fourth: the product
entry point exists, works, and **cannot currently reach another machine or another project.**

---

## G1 — Installation

Evaluate empirically before choosing. The zero-third-party-dependency finding matters here: it makes
several options viable that would otherwise not be.

| Option | Note |
|---|---|
| Shell launcher (today) | Works; requires the checkout and manual `PATH` handling. |
| `pipx` / `uv tool` | Natural fit for a stdlib-only CLI; needs a package definition. |
| pip package | Same, with a wider blast radius for versioning. |
| Standalone executable | Removes the Python question; complicates the Hermes dependency, which is a separate install. |
| Container / devcontainer | Makes the Linux assumptions explicit rather than accidental — possibly the most honest option. |
| Homebrew | Only after platform support is proven, not before. |

**Do not decide before inspecting how Hermes itself is installed and pinned**, since Diana's preflight
requires an exact Hermes version and commit.

## G2 — Environment checks

One deterministic command should report: Python version compatibility; the Hermes installation and
its version pin; provider configuration; browser/runtime availability; `/proc` availability; `git`;
target-repository validity; and which optional capabilities are absent.

Prefer a **doctor/preflight** shape — deterministic, no model call, one line per fact, and explicit
about what is missing rather than failing opaquely. Diana already has this discipline in its own
preflight; this is the same idea aimed at the user's machine.

## G3 — CLI ergonomics

Today: `diana-do "<goal>"`, `approve`, `show`, `status`, `result`.

Commands worth **evaluating** — not adding in this phase: `resume`, `cancel`, `inspect`, `doctor`, and
whether the launcher should be named `diana`. Any new verb is subject to the same rule as a workflow
class: it must not become a way to reach authority that the existing verbs refuse. `continue` in
particular must never exist, because "continue" must never widen an envelope.

## G4 — Error UX

Normal users should see what happened, what Diana refused, and what action is needed. Expert mode
exposes the reason code, the contract, the journal and raw evidence. The refusal vocabulary already
distinguishes pre-authority refusals from run-blocking ones; the UX should preserve that distinction
rather than flatten it.

## G5 — Configuration

Decide what belongs in project config, user config and environment variables.

> **Security-relevant policy must not become user-editable without a governed model.** The command
> catalogue, forbidden write prefixes, certified workflow classes and role projections are policy, not
> preferences. A config file that could widen them would be the side door M7 exists to refuse.

## G6 — Cross-platform

Inventory before claiming anything:

- `/proc` — per-PID ownership and quiescence. No `/proc`, no quiescence proof, and the correct
  behaviour is to block rather than to weaken the check.
- `flock` — the run lease. Semantics vary on some network filesystems.
- Process ownership and signalling.
- Browser/runtime integration.

> **Do not claim macOS or Windows support until it is proven.** Today the honest statement is: Linux,
> exercised; anything else, unproven.

## G7 — Versioning and upgrade safety

Plan semantic versions; journal and proposal schema migrations; behaviour for runs created by an
older version; and compatibility between a frozen policy and a newer release.

> **The load-bearing property: an approval issued under an older, narrower policy must never become
> valid under a wider new release.** The proposal digest already binds the authority it describes;
> versioning must not create a path around that.

## G8 — Documentation

Quickstart · Architecture · Threat model · Workflow certification · Troubleshooting · Security
evidence · Contributor guide. The architecture and the four track roadmaps now exist; the rest do not.

---

## Non-goals

- Weakening any control to simplify installation.
- Shipping before platform assumptions are stated.
- Making policy user-editable.
- Adding CLI verbs in this planning phase.

## Stop conditions

Stop rather than proceed if: packaging would require relaxing the Hermes version pin; a platform port
would require weakening quiescence or the lease; or a configuration surface would let a user widen an
envelope.
