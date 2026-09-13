#!/usr/bin/env python3
"""Diana: deterministic repository profile (spec step 3, D31-D33).

Two jobs, both deliberately unambitious:

1. **Supply the file inventory.** Hermes has no `list_dir` tool -- its core set
   is `read_file` and `search_files` (`model_tools.py:611 _READ_SEARCH_TOOLS`) --
   and `terminal` is off the M1 envelope, so Hermes cannot enumerate the tree
   itself. Diana therefore hands it a filtered, repo-relative inventory inside
   the contract (D31). The inventory obeys exactly the same read scope as the
   read tools, carries NO file contents, and never names a denied path.

2. **Decide category applicability**, generously toward UNKNOWN (D33).

The applicability rule is the load-bearing one, and it runs the opposite way
from intuition: `NOT_APPLICABLE` must be EARNED by deterministic evidence of
absence, and additionally requires that the scoped inventory was established
COMPLETE and that the claim is limited to code in the reviewed repository. If
completeness or scope is uncertain, the answer stays `APPLICABILITY_UNKNOWN`.

The cost is visible and correct: on a tiny static fixture most categories read
`APPLICABILITY_UNKNOWN`, and the report looks less impressive than one that
confidently dismisses them. Diana's own Security Track already demonstrates what
over-claimed applicability costs -- 75/75 UNPROVEN with 37 of those merely
`UNKNOWN`. A report that admits what it does not know is the product here.

`inventory_complete` means "everything IN SCOPE was enumerated". A path the read
scope deliberately excludes -- `.env`, `.git/`, or a symlink whose target leaves
the repository -- does not make the inventory incomplete, because the claim being
made is explicitly limited to code in the reviewed repository. Only a genuine
enumeration failure (an unreadable directory, an unresolvable entry) clears it.
Treating policy exclusions as incompleteness would force every repository with a
`.env` into UNKNOWN for everything, which is conservatism past the point of
usefulness. Both kinds of exclusion are recorded in `inventory_notes`.

Output is deterministic: sorted inventory, repo-relative POSIX paths, no
timestamps, no absolute paths, no host-specific content.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runtime"))
import read_scope as _read_scope  # noqa: E402

# Category status vocabulary (D32). These are CATEGORY-level answers; concrete
# constructs the scanner met but could not analyze live in a separate array on
# the artifact, because they are a different axis entirely.
CHECKED = "CHECKED"
NOT_CHECKED = "NOT_CHECKED"
NOT_APPLICABLE = "NOT_APPLICABLE"
APPLICABILITY_UNKNOWN = "APPLICABILITY_UNKNOWN"

# Extensions the one M1 rule can actually read (D7/D8).
CLIENT_SIDE_EXTS = frozenset({".js", ".mjs", ".html", ".htm"})

# Positive evidence that server-side code exists. Presence of any of these makes
# server-side categories UNKNOWN rather than NOT_APPLICABLE.
SERVER_SIDE_EXTS = frozenset(
    {".py", ".rb", ".php", ".java", ".go", ".rs", ".cs", ".kt", ".scala", ".ex", ".erl", ".pl", ".ts"}
)

# A package manifest means a backend could be present even with no server-side
# extension in the tree, so it forces UNKNOWN.
BACKEND_MARKER_NAMES = frozenset({"package.json", "requirements.txt", "pyproject.toml", "Gemfile", "go.mod", "Cargo.toml", "composer.json", "pom.xml"})

DB_MARKER_EXTS = frozenset({".sql"})


def _walk(repo_root: str, scope: dict) -> tuple[list[str], bool, list[str]]:
    """Enumerate in-scope files. Returns (inventory, complete, notes).

    `complete` is False whenever any part of the tree could not be fully
    enumerated. Completeness is a precondition for any NOT_APPLICABLE claim, so
    an enumeration that quietly skipped a directory must not be reported as a
    complete picture.
    """
    root = _read_scope.canonicalize(repo_root)
    inventory: list[str] = []
    complete = True
    notes: list[str] = []

    denied_by_rule = 0

    def on_error(exc: OSError) -> None:
        nonlocal complete
        complete = False
        notes.append(f"could not enumerate {getattr(exc, 'filename', '?')}: {exc.strerror}")

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error, followlinks=False):
        # Prune denied directories so we never even descend into them; the same
        # scope decision the read tools apply, applied here.
        kept = []
        for name in sorted(dirnames):
            if _read_scope.is_allowed(os.path.join(dirpath, name), scope):
                kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            absolute = os.path.join(dirpath, name)
            allowed, why = _read_scope.decide(absolute, scope)
            if not allowed:
                # A policy exclusion is NOT an enumeration failure: the scoped
                # inventory can still be complete while deliberately omitting a
                # denied path. Escapes are named individually because "a file
                # here points outside the repository" is something a reader
                # should see; routine .env/.git denials are only counted, or the
                # notes would drown the report.
                if "outside every allowed root" in why:
                    notes.append(f"{PurePosixPath(os.path.relpath(absolute, root)).as_posix()} "
                                 "resolves outside the repository root and was excluded")
                else:
                    denied_by_rule += 1
                continue
            try:
                relative = os.path.relpath(_read_scope.canonicalize(absolute), root)
            except (OSError, ValueError):
                complete = False
                notes.append(f"could not resolve {name}")
                continue
            if relative.startswith(".."):
                notes.append(f"{name} resolves outside the repository root and was excluded")
                continue
            inventory.append(PurePosixPath(relative).as_posix())

    if denied_by_rule:
        notes.append(f"{denied_by_rule} path(s) excluded by read_scope.denied_subpaths")
    return sorted(set(inventory)), complete, sorted(set(notes))


def _extensions(inventory: list[str]) -> set[str]:
    return {PurePosixPath(p).suffix.lower() for p in inventory}


def _basenames(inventory: list[str]) -> set[str]:
    return {PurePosixPath(p).name for p in inventory}


def _coverage(inventory: list[str], complete: bool) -> list[dict]:
    """Category applicability. NOT_APPLICABLE must be earned (D33)."""
    exts = _extensions(inventory)
    names = _basenames(inventory)
    has_client = bool(exts & CLIENT_SIDE_EXTS)
    has_server_ext = bool(exts & SERVER_SIDE_EXTS)
    has_backend_marker = bool(names & BACKEND_MARKER_NAMES)
    has_db_marker = bool(exts & DB_MARKER_EXTS)
    server_side_possible = has_server_ext or has_backend_marker or has_db_marker

    def unknown_unless(condition: bool, reason_yes: str, reason_no: str) -> tuple[str, str]:
        """NOT_APPLICABLE only when the inventory is complete AND the evidence
        of absence is positive; otherwise UNKNOWN."""
        if complete and condition:
            return NOT_APPLICABLE, reason_yes
        if not complete:
            return APPLICABILITY_UNKNOWN, "scoped inventory could not be established complete"
        return APPLICABILITY_UNKNOWN, reason_no

    coverage: list[dict] = []

    # dom_xss -- the one rule M1 ships.
    if has_client:
        coverage.append({"category": "dom_xss", "status": CHECKED, "rule_ids": ["DOM-XSS-001"]})
    else:
        status, reason = unknown_unless(
            True,
            "no HTML or JavaScript file in a complete scoped inventory",
            "no client-side file found",
        )
        coverage.append({"category": "dom_xss", "status": status, "reason": reason})

    # Categories with no detector at D1. Present and explained, never silently
    # absent -- a reader must be able to tell "looked and found nothing" from
    # "did not look" (D7/D32).
    for category, note in (
        ("secrets_in_source", "no detector implemented at D1"),
        ("external_scripts", "no detector implemented at D1"),
        ("code_injection", "eval/Function out of M1 scope"),
        ("event_handler_injection", "out of M1 scope; argument-position analysis not implemented"),
    ):
        coverage.append({"category": category, "status": NOT_CHECKED, "reason": note})

    # Server-side categories: dismissible only on positive evidence of absence.
    status, reason = unknown_unless(
        not server_side_possible,
        "no server-side source, package manifest, or SQL artifact in a complete scoped inventory",
        "a package manifest or server-side source is present, so a backend cannot be ruled out",
    )
    coverage.append({"category": "sqli", "status": status, "reason": reason})

    status, reason = unknown_unless(
        not server_side_possible,
        "no server-side source, package manifest, or SQL artifact in a complete scoped inventory",
        "a package manifest or server-side source is present, so a backend cannot be ruled out",
    )
    coverage.append({"category": "ssrf", "status": status, "reason": reason})

    # A static inventory can never rule out a hosted backend reached over the
    # network, so this one is UNKNOWN unconditionally and says why.
    coverage.append({
        "category": "authn_authz",
        "status": APPLICABILITY_UNKNOWN,
        "reason": "static inventory cannot rule out a hosted backend reached at runtime",
    })

    return sorted(coverage, key=lambda c: c["category"])


def profile(repo_root: str, read_scope_block: dict) -> dict:
    """Deterministic profile for `repo_root`, confined to `read_scope_block`."""
    inventory, complete, notes = _walk(repo_root, read_scope_block)
    return {
        "inventory": inventory,
        "inventory_complete": complete,
        "inventory_notes": notes,
        "scope_note": "applicability claims are limited to code in the reviewed repository",
        "extensions": sorted(_extensions(inventory)),
        "categories": _coverage(inventory, complete),
    }


def scannable(profile_block: dict) -> list[str]:
    """Repo-relative files the D1 rule can read, in deterministic order."""
    return [
        p for p in profile_block.get("inventory", [])
        if PurePosixPath(p).suffix.lower() in CLIENT_SIDE_EXTS
    ]
