#!/usr/bin/env python3
"""Diana runtime: read-scope confinement, spec constant C2.

Hermes has NO read confinement of its own. `tools/file_tools_paths.py`'s
`_resolve_path_for_task` returns absolute inputs "resolved-but-unanchored", and
`_authoritative_workspace_root` is explicitly best-effort and only anchors
*relative* paths -- so `read_file(path="~/.ssh/id_rsa")` is read with no root
check anywhere. This module is the boundary Diana *creates* (spec D20), not a
hardening of one that already exists.

The threat it addresses is egress-by-transcript (D17): every byte a read tool
returns goes into a transcript sent to a model provider, so a read-only envelope
is not by itself a harmless one.

Matching rules (C2), all on the FULLY CANONICALIZED absolute path -- string
prefix comparison is prohibited:

  * allowed root  -- path equals a root or is a descendant, compared
    component-wise, so `/home/u/repo-evil` is not inside `/home/u/repo`.
  * `.git/`-style  -- directory rule, denied if ANY relative component equals
    the rule minus its trailing slash (so nested submodule `.git` dirs too).
  * `.env`/`.env.*` -- name-glob rule, fnmatchcase against EACH relative
    component independently.

Deny always wins, and every failure path denies.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path, PurePosixPath

__all__ = ["ScopeError", "validate_read_scope", "canonicalize", "decide", "is_allowed"]


class ScopeError(ValueError):
    """A read_scope that cannot be interpreted deterministically."""


def validate_read_scope(read_scope: object) -> tuple[list[str], list[str]]:
    """Validate and normalize a read_scope block, or raise ScopeError.

    An entry containing an interior '/' is a configuration error (C2): the two
    supported forms are exhaustive, so anything else is rejected here rather
    than silently matching nothing at read time.
    """
    if not isinstance(read_scope, dict):
        raise ScopeError("read_scope must be an object")
    roots = read_scope.get("allowed_roots")
    denied = read_scope.get("denied_subpaths", [])
    if not isinstance(roots, list) or not roots:
        raise ScopeError("read_scope.allowed_roots must be a non-empty list")
    if not all(isinstance(r, str) and r for r in roots):
        raise ScopeError("read_scope.allowed_roots entries must be non-empty strings")
    if not isinstance(denied, list):
        raise ScopeError("read_scope.denied_subpaths must be a list")
    for entry in denied:
        if not isinstance(entry, str) or not entry:
            raise ScopeError("denied_subpaths entries must be non-empty strings")
        body = entry[:-1] if entry.endswith("/") else entry
        if "/" in body:
            raise ScopeError(
                f"denied_subpaths entry {entry!r} has an interior '/'; "
                "only directory rules ('name/') and name globs ('name', 'name.*') are supported"
            )
        if not body:
            raise ScopeError(f"denied_subpaths entry {entry!r} is empty once its trailing '/' is removed")
    return [str(r) for r in roots], [str(d) for d in denied]


def canonicalize(path: str) -> str:
    """Absolute, symlink-free path. Never raises; unresolvable input is returned
    as an absolute path that will fail the allowed-root test and therefore deny.

    `os.path.realpath` resolves the existing ancestor chain for a path that does
    not exist yet, which is the C2-specified behavior for missing files.
    """
    try:
        return os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))
    except (OSError, ValueError, TypeError):
        return "\x00unresolvable"


def _relative_components(candidate: str, root: str) -> list[str] | None:
    """Components of `candidate` beneath `root`, or None when not beneath it.

    Component-wise, never a string prefix test: `/home/u/repo-evil` must not be
    treated as living inside `/home/u/repo`.
    """
    cand_parts = PurePosixPath(candidate).parts
    root_parts = PurePosixPath(root).parts
    if len(cand_parts) < len(root_parts):
        return None
    if cand_parts[: len(root_parts)] != root_parts:
        return None
    return list(cand_parts[len(root_parts):])


def _denied_by(components: list[str], denied: list[str]) -> str | None:
    for rule in denied:
        if rule.endswith("/"):
            name = rule[:-1]
            if any(part == name for part in components):
                return rule
        else:
            if any(fnmatch.fnmatchcase(part, rule) for part in components):
                return rule
    return None


def decide(path: str, read_scope: dict) -> tuple[bool, str]:
    """Return (allowed, reason). Every failure path denies (C2 step 1)."""
    try:
        roots, denied = validate_read_scope(read_scope)
    except ScopeError as exc:
        return False, f"read_scope invalid: {exc}"

    candidate = canonicalize(path)
    if candidate == "\x00unresolvable":
        return False, "path could not be canonicalized"

    for root in roots:
        canon_root = canonicalize(root)
        components = _relative_components(candidate, canon_root)
        if components is None:
            continue
        hit = _denied_by(components, denied)
        if hit is not None:
            return False, f"denied_subpaths rule {hit!r} matched"
        return True, f"within allowed root {canon_root}"

    # Reached when the canonicalized path lies outside every root. A symlink
    # pointing out of the repo lands here: the escape is only visible after
    # canonicalization, which is why prefix matching would be unsound.
    return False, "outside every allowed root after canonicalization"


def is_allowed(path: str, read_scope: dict) -> bool:
    return decide(path, read_scope)[0]
