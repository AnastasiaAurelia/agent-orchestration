#!/usr/bin/env python3
"""Diana M7: the frozen catalogues intent is validated against (M7-D5, M7-D6).

Natural language may PROPOSE. These tables are what it is proposed *against*,
and nothing outside them can become authority however a request is phrased.

## Why a command catalogue rather than a command parser

M4 refused to parse shell for read-only-ness because that is an unwinnable
game, and M4's `allowed_commands` is an exact-match allowlist for the same
reason. A product layer that let an interpretation contribute a command string
would hand the parsing game back in a new place. So a command may enter
`allowed_commands` only by being IN this table, verbatim; anything else is
refused and never approximately matched.

## Why exclusions are patterns, not prose

"don't touch auth" has to become denied authority (M7-D6). A deterministic term
-> path-pattern table is what makes that possible: the mapping is fixed here, so
the same phrase always produces the same denial and an interpretation cannot
quietly drop one without the proposal digest changing.
"""

from __future__ import annotations

# Certified workflow classes. Depth comes from the class (M1 D13); this table
# does not carry depth and there is no field in which one could be proposed.
CERTIFIED_WORKFLOWS = ("BOUNDED_REMEDIATION", "ADVISORY_SECURITY_REVIEW")

# --- the frozen command catalogue (M7-D5) ---------------------------------
#
# Each entry is (exact command, the repo-relative evidence that makes it
# applicable). A command is proposable only when its evidence exists, so a
# proposal never grants a command the repository cannot even run.
COMMAND_CATALOGUE = (
    ("python3 check.py", "check.py"),
    ("python3 -m pytest -q", "tests"),
    ("python3 -m unittest discover -q", "tests"),
    ("npm test", "package.json"),
)

# --- write-root candidates ------------------------------------------------
#
# The FALLBACK write scope, used only when the request names no path of its own.
# Order is fixed so the derivation is reproducible.
#
# These were once the only source of write authority, and that made the product
# layer repository-shaped rather than repository-agnostic: a project whose code
# lives in a root-level file, or under a directory named anything else, could
# not be repaired at all. Worse, a request that explicitly named such a path got
# a DIFFERENT, narrower scope silently rather than the path it asked for or an
# honest refusal. An explicitly named path is now the primary source
# (`intent._explicit_write_paths`); this table is what remains when the request
# names none.
WRITE_ROOT_CANDIDATES = ("src", "lib", "tests", "test")

# --- explicit path syntax (frozen) ----------------------------------------
#
# A word that merely HAPPENS to match something on disk must never become
# authority: "fix the parser in compiler" must not grant `compiler/` just
# because that directory exists. So a token is treated as a path reference only
# by SYNTAX plus repository evidence, never by lookup alone. The rule is stated
# in full at `intent._is_path_reference`, and these are the characters it trims.
#
# Trimmed from either end before a token is judged: markdown, quoting and
# sentence punctuation surround paths in ordinary prose and are not part of them.
PATH_TRIM_CHARS = "()[]{}<>'\"`,;:!?"

# A bare (slash-free) token is a path reference only with this shape AND only
# when it exists: `name.ext`, extension 1-10 alphanumerics. Requiring existence
# is what keeps prose such as "e.g" or a version like "3.11" out of authority.
BARE_FILE_PATTERN = r"^[^/]+\.[A-Za-z0-9]{1,10}$"

# --- paths no proposal may ever make writable -----------------------------

#
# Mirrors the sensitive surfaces the Gate and ship.py already protect, plus
# Diana's own enforcement surface. A request naming one of these is refused
# rather than narrowed, so a user learns their exclusion was never the issue.
FORBIDDEN_WRITE_PREFIXES = (
    ".git/", ".github/", "diana/gate/", "diana/adapters/", "diana/governance/",
    "diana/security/", "diana/ci/", "diana/runtime/", "diana/unattended/",
    "diana/multiactor/", "diana/product/", "infra/production/",
    "migrations/production/",
)

# --- exclusion vocabulary (M7-D6) -----------------------------------------
#
# term -> denied subpath patterns, in `read_scope`/`write_scope`
# `denied_subpaths` form. Fixed so "don't touch auth" is always the same denial.
EXCLUSION_TERMS = {
    "auth": ("auth/", "*auth*"),
    "authentication": ("auth/", "*auth*"),
    "deploy": ("deploy/", ".github/workflows/", "infra/"),
    "deployment": ("deploy/", ".github/workflows/", "infra/"),
    "credentials": (".env", ".env.*", "secrets/"),
    "secrets": (".env", ".env.*", "secrets/"),
    "ci": (".github/workflows/",),
    "migrations": ("migrations/",),
    "infra": ("infra/",),
    "database": ("migrations/", "db/"),
}

# Phrases that introduce an exclusion. Matching is on the phrase, then on the
# terms that follow it, so "fix auth" is not mistaken for "don't touch auth".
EXCLUSION_MARKERS = (
    "don't touch", "do not touch", "dont touch", "without touching",
    "but not", "except", "leave alone", "stay out of", "don't modify",
    "do not modify", "dont modify", "avoid",
)


def commands_for(repo_root) -> tuple[str, ...]:
    """Exactly the catalogue commands whose evidence exists in this repository."""
    from pathlib import Path
    root = Path(repo_root)
    return tuple(cmd for cmd, evidence in COMMAND_CATALOGUE if (root / evidence).exists())


# Name globs no proposal may make writable, matched against EVERY path
# component independently -- the same rule shape `read_scope` uses for its
# `denied_subpaths`, so the proposal refuses to offer what the runtime would
# deny anyway. `contract.DEFAULT_DENIED_SUBPATHS` already stops a write to these
# at the enforcement boundary; refusing them here means a user is told, rather
# than handed a write root with a hole in it.
FORBIDDEN_WRITE_NAME_GLOBS = (".env", ".env.*")


def is_forbidden_write(rel_path: str) -> bool:
    """Is this repo-relative path one no proposal may make writable?

    Audit finding M7-A1: this used `lstrip("./")`, which strips those two
    CHARACTERS rather than a leading `./` prefix -- so `.github` became
    `github` and `.git` became `git`, and neither matched its own forbidden
    entry. Every dot-prefixed protected path was therefore proposable. The
    prefix is now removed as a prefix.
    """
    normalised = rel_path.replace("\\", "/").strip()
    while normalised.startswith("./"):
        normalised = normalised[2:]
    normalised = normalised.rstrip("/")
    if not normalised:
        return True
    normalised_dir = normalised + "/"
    if any(normalised_dir.startswith(prefix) or normalised == prefix.rstrip("/")
           for prefix in FORBIDDEN_WRITE_PREFIXES):
        return True
    import fnmatch
    return any(fnmatch.fnmatchcase(part, glob)
               for part in normalised.split("/")
               for glob in FORBIDDEN_WRITE_NAME_GLOBS)
