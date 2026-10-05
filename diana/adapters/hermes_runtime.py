#!/usr/bin/env python3
"""Diana: Hermes's own supported runtime activation (replaces the obsolete
repo-local ``$HERMES_HOME/venv`` assumption).

## What changed under Diana

Current Hermes is PM-managed: its dependency interpreter lives under
``~/.hermes/installs/<install-hash>/environments/<env-hash>/venv`` -- a path
keyed by two opaque, per-machine hashes (``pm.environments.install_key`` is a
hash of the checkout's own resolved path). Diana must never hardcode or guess
that path: it is not portable across machines, profiles, or even two checkouts
of the same user.

Hermes already exposes the one supported answer to "which interpreter does
checkout X actually run on" -- ``pm.environments.project_python``, the exact
call ``hermes_cli/source_build.py`` uses internally to re-invoke itself. This
module is a thin, fail-closed wrapper around that call: Diana consults exactly
the mechanism Hermes uses on itself, rather than inventing a second one.

## Why this is safe to run under a bare ``python3``

``pm/environments.py`` documents itself as stdlib-plus-``hermes_constants``
only, specifically so environment selection works *before* any dependency from
that environment has been imported. Resolving the runtime therefore never
needs Hermes's own dependency venv just to go and find Hermes's dependency
venv -- a bare, ambient ``python3`` with ``HERMES_HOME`` on ``sys.path`` is
enough, and this module adds nothing beyond that.

## Fail-closed contract

Every failure mode -- Hermes unreachable, ``pm`` not importable, no committed
dependency environment, a committed environment whose interpreter does not
exist on disk -- raises :class:`RuntimeUnavailable` (or, from the CLI, prints
to stderr and exits non-zero) rather than silently falling back to an ambient
``python3``. A fallback would reintroduce exactly the failure this module
exists to prevent: Diana loading Hermes's source tree under an interpreter
that does not carry Hermes's own dependencies, and misreading the resulting
``ModuleNotFoundError`` cascade as a Hermes defect instead of a Diana one.
"""

from __future__ import annotations

import sys
from pathlib import Path


class RuntimeUnavailable(Exception):
    """Hermes's bootstrap/runtime could not be established. Always fail closed."""


def resolve_python(hermes_home: str) -> Path:
    """The interpreter Hermes's own PM selected for the checkout at *hermes_home*.

    Raises :class:`RuntimeUnavailable` for every failure mode; never returns a
    guessed or ambient interpreter.
    """
    home = Path(hermes_home)
    if not (home / "model_tools.py").is_file() or not (home / "hermes_cli").is_dir():
        raise RuntimeUnavailable(f"no Hermes installation at {home}")
    if str(home) not in sys.path:
        sys.path.insert(0, str(home))
    try:
        from pm.environments import project_python
        from pm.paths import repo_root
    except Exception as exc:  # noqa: BLE001 - any import failure is fail-closed
        raise RuntimeUnavailable(
            "Hermes's PM runtime module (pm.environments) is not importable from "
            f"{home}: {type(exc).__name__}: {exc}"
        ) from exc
    try:
        python = project_python(repo_root())
    except Exception as exc:  # noqa: BLE001 - a resolver failure is fail-closed too
        raise RuntimeUnavailable(
            f"Hermes's PM runtime could not resolve an interpreter for {home}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not python.is_file():
        raise RuntimeUnavailable(
            f"Hermes's PM runtime names an interpreter that does not exist: {python}"
        )
    return python


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: hermes_runtime.py <hermes-home>", file=sys.stderr)
        return 2
    try:
        python = resolve_python(argv[0])
    except RuntimeUnavailable as exc:
        print(f"hermes_runtime: {exc}", file=sys.stderr)
        return 1
    print(python)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
