#!/usr/bin/env python3
"""Diana M7: natural language -> a closed Intent schema (M7-D3 … M7-D6).

**Intent is a request, never a grant.** Every field here is validated against
the frozen catalogues before it can become authority, and two fields that would
matter most are simply absent: there is no `risk` and no `depth`, so there is no
channel in which one could be proposed. Depth comes from the certified workflow
class (M1 D13) and risk from the granted envelope (M1 D12), exactly as before.

## Why classification is deterministic rather than a model call

M1's `route()` is a keyword rule for a stated reason: letting a model choose the
workflow class would hand it the depth channel M1 D14 denies it. M7 scales that
rule to two certified classes rather than replacing it with a model. A model may
still supply an Intent -- the schema is the validation boundary either way --
but the normal path needs no model to decide what a request IS.

## Why ambiguity refuses instead of choosing

M7-D10. A request that reads as both a review and a repair is not resolved by
picking the broader one, and not by proposing their union. Widening to cover
ambiguity is the failure a product layer invites, and the only safe answer is to
say which readings were possible and stop.

## How an exclusion becomes denied authority

`remediate.build_contract` -- the builder `approve()` itself uses -- does not
expose `denied_subpaths`, and M7-REG-2 forbids changing it. So an exclusion is
enforced by REMOVING paths from the write roots rather than by adding a denial
rule: an excluded directory is dropped, and a root containing one is expanded to
its other children. The effect is the same authority reduction, expressed
through the exact builder the run will use -- which is what keeps the proposal a
true prediction (M7-E1-D4).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import catalogue as _cat  # noqa: E402
import refusal as _ref  # noqa: E402

INTENT_KEYS = ("workflow", "goal", "write_paths", "commands", "items", "exclusions")

# Deterministic vocabularies. Disjoint by construction: a word that would mean
# both a repair and a review would make every request ambiguous.
_REPAIR_ACTIONS = ("fix", "repair", "patch", "remediate", "correct", "unbreak")
_REVIEW_ACTIONS = ("check", "review", "audit", "inspect", "assess", "scan")
_REVIEW_SUBJECTS = ("security", "vulnerability", "vulnerabilities", "xss", "insecure")


def _words(text: str) -> set[str]:
    return set("".join(c.lower() if c.isalnum() else " " for c in text).split())


def _exclusions(goal: str) -> tuple[str, ...]:
    """Terms the user excluded, found only AFTER an exclusion marker.

    The marker requirement is what stops "fix auth" being read as excluding
    auth: a term only counts when something introduced it as an exclusion.
    """
    lowered = goal.lower()
    found: list[str] = []
    for marker in _cat.EXCLUSION_MARKERS:
        start = 0
        while True:
            at = lowered.find(marker, start)
            if at < 0:
                break
            span = lowered[at + len(marker):]
            # A marker governs until a clause that clearly ends it.
            for stop in (". ", "; ", " then ", " and then "):
                cut = span.find(stop)
                if cut >= 0:
                    span = span[:cut]
            for term in _cat.EXCLUSION_TERMS:
                if term in _words(span) and term not in found:
                    found.append(term)
            start = at + len(marker)
    return tuple(found)


def _excluded_names(exclusions) -> set[str]:
    """Directory names an exclusion forbids, derived from the frozen table."""
    names: set[str] = set()
    for term in exclusions:
        names.add(term)
        for pattern in _cat.EXCLUSION_TERMS.get(term, ()):
            cleaned = pattern.strip("*/").split("/")[0]
            if cleaned and not cleaned.startswith("."):
                names.add(cleaned)
    return names


# --- explicit path references --------------------------------------------
#
# The derivation used to read the repository for four fixed directory names and
# nothing else, so a repository shaped any other way had no writable scope --
# and a request that NAMED its own path was answered with a different, narrower
# scope without saying so. Both halves are fixed here: a named path is the
# primary source of write authority, and a named path policy will not grant is
# REFUSED rather than quietly replaced.
#
# Nothing about the authority model changes. A path named here is a *request*,
# exactly as every other Intent field is; it is validated against the same
# frozen policy before it can become a write root, the resulting scope is still
# built by `remediate.build_contract`, still narrowed to the reviewer's frozen
# envelope, and still enforced by `read_scope.decide` at the dispatch boundary.

_BARE_FILE = re.compile(_cat.BARE_FILE_PATTERN)


def _path_tokens(goal: str) -> list[str]:
    """Whitespace tokens, trimmed of the punctuation prose wraps paths in.

    A trailing full stop is sentence punctuation rather than an extension, so it
    is removed -- `lib/rules.mjs.` at the end of a sentence is `lib/rules.mjs`.
    """
    tokens: list[str] = []
    for raw in goal.split():
        # Alternating, until stable: prose wraps a path in BOTH kinds of
        # punctuation -- "`app/main.py`." ends with a backtick behind a full
        # stop, and one pass of each would leave the backtick attached.
        token = raw
        while True:
            trimmed = token.strip(_cat.PATH_TRIM_CHARS).rstrip(".")
            if trimmed == token:
                break
            token = trimmed
        # A backslash is never a separator in a repository-relative POSIX path,
        # but normalising it here means `..\..\etc` is still SEEN as traversal
        # and refused, rather than slipping past the check as opaque prose.
        token = token.replace("\\", "/")
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _is_unsafe_shape(token: str) -> str:
    """A shape that is unmistakably a path AND unmistakably out of bounds."""
    if token.startswith(("/", "~")) or re.match(r"^[A-Za-z]:(/|$)", token):
        return "is an absolute path; write scope is repository-relative only"
    if ".." in token.split("/"):
        return "contains a '..' component; write scope may not traverse upward"
    return ""


def _is_path_reference(token: str, root: Path) -> bool:
    """Is this token a PATH REFERENCE at all? Deterministic and conservative.

    Syntax first, repository evidence second, and never evidence alone. A bare
    word that happens to match a directory must NOT become authority -- "fix the
    parser in compiler" does not grant `compiler/` -- because that would let the
    shape of the filesystem, rather than the request, decide what is writable.

    A token is a reference when, in order:

      1. it has an unsafe shape (absolute, or a `..` component). Always a
         reference, so it is REFUSED rather than mistaken for prose.
      2. it is written with an explicit `./` prefix. The author has said "this
         is a path", which is how a not-yet-existing file is named.
      2b. it has path syntax and names something policy never grants (`.git`,
         `.github/...`, Diana's own enforcement surface, `.env*`). Always a
         reference, so naming one is refused rather than read as prose.
      3. it contains `/` AND either it exists in the repository or its parent
         directory does. This is what excludes prose like `and/or` or `24/7`,
         whose first component names nothing.
      4. it contains no `/`, has a `name.ext` shape, AND exists. Existence is
         required here because `name.ext` alone also describes `e.g` and `3.11`.

    A directory must therefore be written with a slash (`packages/core/` or
    `packages/core`) to be named. That is the deliberate resolution of "explicit
    directories are supported" against "a matching word is not a request".
    """
    if _is_unsafe_shape(token):
        return True
    if token.startswith("./"):
        return True
    # A path policy will NEVER grant is always a reference, whether or not it
    # exists here. Otherwise naming `.github/workflows/ci.yml` in a repository
    # that has no `.github` would be read as prose and answered with some other
    # scope -- which is the silent substitution this whole derivation exists to
    # remove. It must be refused, and a refusal requires being seen first.
    if "/" in token or _BARE_FILE.match(token):
        if _cat.is_forbidden_write(token.strip("/")):
            return True
    relative = token.rstrip("/")
    if "/" in token:
        if not relative:
            return True          # a bare "/" -- unsafe, judged as a reference
        candidate = root / relative
        return candidate.exists() or candidate.parent.is_dir()
    return bool(_BARE_FILE.match(token)) and (root / token).exists()


def _validate_explicit(token: str, root: Path, blocked: set) -> str:
    """One named path -> its repo-relative form, or Refused. Never dropped."""
    unsafe = _is_unsafe_shape(token)
    if unsafe:
        raise _ref.Refused(
            _ref.WRITE_PATH_OUTSIDE_REPO, f"{token!r} {unsafe}")
    relative = token
    while relative.startswith("./"):
        relative = relative[2:]
    relative = relative.strip("/")
    if not relative:
        raise _ref.Refused(
            _ref.WRITE_PATH_OUTSIDE_REPO,
            f"{token!r} names the repository root; a bounded repair is never granted "
            "the whole repository as its write scope")
    if _cat.is_forbidden_write(relative):
        raise _ref.Refused(
            _ref.WRITE_PATH_FORBIDDEN,
            f"{relative!r} is a path no proposal may make writable")
    # Symlink escape is caught HERE and not by the string form: a path that is
    # repository-relative as written can still resolve outside, and only the
    # resolved location decides.
    resolved = (root / relative).resolve()
    if resolved == root or root not in resolved.parents:
        raise _ref.Refused(
            _ref.WRITE_PATH_OUTSIDE_REPO,
            f"{relative!r} resolves to {resolved} which is not inside the repository")
    hit = [part for part in relative.split("/") if part in blocked]
    if hit:
        raise _ref.Refused(
            _ref.WRITE_PATH_EXCLUDED,
            f"{relative!r} was named as a path to repair and also excluded by this "
            f"request ({', '.join(sorted(set(hit)))}); Diana does not choose between "
            "the two. Ask for one.")
    return relative


def _canonical_paths(paths: list[str]) -> list[str]:
    """Sorted, de-duplicated, and reduced to the paths that are not covered.

    Granting both `services/api` and `services/api/server.ts` grants exactly
    `services/api`, so the narrower entry is dropped: the same authority stated
    once. Sorting makes the result a function of the SET of named paths rather
    than of the order they appeared in the sentence, so two requests naming the
    same paths produce the same digest.
    """
    unique = sorted(set(paths))
    return [p for p in unique
            if not any(p.startswith(other + "/") for other in unique if other != p)]


def _explicit_write_paths(goal: str, root: Path, blocked: set) -> list[str]:
    """Every path the request named, validated. Refuses; never silently drops."""
    named = [t for t in _path_tokens(goal) if _is_path_reference(t, root)]
    return _canonical_paths([_validate_explicit(t, root, blocked) for t in named])


def _write_paths(repo_root, exclusions, goal: str = "") -> tuple[list[str], list[str]]:
    """(write roots, paths withheld by an exclusion). Repo-relative, ordered.

    A request that names its own paths gets those paths and nothing else. Only
    a request naming none falls back to the frozen candidate directories.
    """
    root = Path(repo_root).resolve()
    blocked = _excluded_names(exclusions)
    explicit = _explicit_write_paths(goal, root, blocked)
    if explicit:
        # `withheld` stays empty on this path by construction: an explicitly
        # named path that an exclusion blocks is a refusal above, not a note.
        return explicit, []
    roots: list[str] = []
    withheld: list[str] = []
    for candidate in _cat.WRITE_ROOT_CANDIDATES:
        directory = root / candidate
        if not directory.is_dir():
            continue
        if candidate in blocked:
            withheld.append(candidate)
            continue
        children = sorted(c.name for c in directory.iterdir() if c.is_dir())
        hit = [c for c in children if c in blocked]
        if not hit:
            roots.append(candidate)
            continue
        # The root contains something excluded: expand to its other children so
        # the exclusion is a real reduction in authority, not a note.
        withheld.extend(f"{candidate}/{c}" for c in hit)
        roots.extend(f"{candidate}/{c}" for c in children if c not in blocked)
    return roots, withheld


def classify(goal: str, repo_root) -> dict:
    """Natural language -> a validated Intent, or raise Refused."""
    if not isinstance(goal, str) or not goal.strip():
        raise _ref.Refused(_ref.INTENT_MALFORMED, "the goal is empty")
    words = _words(goal)
    wants_repair = bool(words & set(_REPAIR_ACTIONS))
    wants_review = bool(words & set(_REVIEW_ACTIONS)) and bool(words & set(_REVIEW_SUBJECTS))

    if wants_repair and wants_review:
        raise _ref.Refused(
            _ref.INTENT_AMBIGUOUS,
            "this reads as both a repair and a security review; it is not resolved by "
            "choosing the broader one. Ask for one: a repair (\"fix …\") or a review "
            "(\"review the security of …\")")
    if not wants_repair and not wants_review:
        raise _ref.Refused(
            _ref.INTENT_UNROUTABLE,
            "no certified workflow matches this request. Certified: a bounded repair "
            "(\"fix …\"), or a read-only security review (\"review the security of …\")")

    workflow = "BOUNDED_REMEDIATION" if wants_repair else "ADVISORY_SECURITY_REVIEW"
    exclusions = _exclusions(goal)
    write_paths, withheld = _write_paths(repo_root, exclusions, goal)
    commands = _cat.commands_for(repo_root)

    if workflow == "BOUNDED_REMEDIATION":
        if not write_paths:
            raise _ref.Refused(
                _ref.NO_WRITABLE_SCOPE,
                "this request names no path to repair, and no fallback source "
                f"directory exists here (looked for {list(_cat.WRITE_ROOT_CANDIDATES)}). "
                "Name the file or directory to change, for example 'app/main.py' or "
                "'packages/core/'"
                + (f"; excluded by your request: {withheld}" if withheld else ""))
        if not commands:
            raise _ref.Refused(
                _ref.NO_VERIFICATION_COMMAND,
                "no verification command in the frozen catalogue applies to this "
                "repository, so a repair could not be verified")

    intent = {
        "workflow": workflow,
        "goal": goal.strip(),
        "write_paths": write_paths,
        "commands": list(commands),
        "items": [{"id": "item-1", "task": goal.strip(), "depends_on": []}],
        "exclusions": list(exclusions),
    }
    validate(intent, repo_root)
    intent["_withheld"] = withheld     # view-model only; not part of the digest
    return intent


def validate(intent: object, repo_root) -> None:
    """Closed schema plus policy. An unknown field or value is a refusal."""
    if not isinstance(intent, dict):
        raise _ref.Refused(_ref.INTENT_MALFORMED, "intent is not an object")
    keys = set(intent) - {"_withheld"}
    missing = sorted(set(INTENT_KEYS) - keys)
    extra = sorted(keys - set(INTENT_KEYS))
    if extra:
        # M7-D3: an unknown field is refused, not recorded. `risk` and `depth`
        # land here, which is the whole point -- there is no channel for either.
        raise _ref.Refused(
            _ref.INTENT_UNKNOWN_FIELD,
            f"intent carries field(s) no schema allows: {extra}"
            + (" — risk and depth are derived, never proposed"
               if {"risk", "depth"} & set(extra) else ""))
    if missing:
        raise _ref.Refused(_ref.INTENT_MALFORMED, f"intent is missing {missing}")
    if intent["workflow"] not in _cat.CERTIFIED_WORKFLOWS:
        raise _ref.Refused(
            _ref.WORKFLOW_NOT_CERTIFIED,
            f"{intent['workflow']!r} is not a certified workflow class "
            f"{list(_cat.CERTIFIED_WORKFLOWS)}")
    if not isinstance(intent["goal"], str) or not intent["goal"].strip():
        raise _ref.Refused(_ref.INTENT_MALFORMED, "goal must be a non-empty string")

    allowed_commands = set(_cat.commands_for(repo_root))
    for command in intent["commands"]:
        if command not in allowed_commands:
            raise _ref.Refused(
                _ref.COMMAND_NOT_IN_CATALOGUE,
                f"{command!r} is not a command this repository's frozen catalogue "
                "offers; it is refused, never approximately matched")

    root = Path(repo_root).resolve()
    for rel in intent["write_paths"]:
        if _cat.is_forbidden_write(rel):
            raise _ref.Refused(
                _ref.WRITE_PATH_FORBIDDEN,
                f"{rel!r} is a path no proposal may make writable")
        resolved = (root / rel).resolve()
        if root not in resolved.parents:
            # `resolved == root` lands here too, and deliberately: a write scope
            # that IS the repository is not a bounded scope, and accepting it
            # here would let a forged intent trade a named path for the whole
            # tree without leaving the repository at all.
            raise _ref.Refused(
                _ref.WRITE_PATH_OUTSIDE_REPO,
                f"{rel!r} resolves to {resolved}, which is not a bounded path inside "
                "the repository")
    if not isinstance(intent["items"], list) or not intent["items"]:
        raise _ref.Refused(_ref.INTENT_MALFORMED, "items must be a non-empty list")
