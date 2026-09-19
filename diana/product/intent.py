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


def _write_paths(repo_root, exclusions) -> tuple[list[str], list[str]]:
    """(write roots, paths withheld by an exclusion). Repo-relative, ordered."""
    root = Path(repo_root)
    blocked = _excluded_names(exclusions)
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
    write_paths, withheld = _write_paths(repo_root, exclusions)
    commands = _cat.commands_for(repo_root)

    if workflow == "BOUNDED_REMEDIATION":
        if not write_paths:
            raise _ref.Refused(
                _ref.NO_WRITABLE_SCOPE,
                "no source directory this policy can make writable exists here "
                f"(looked for {list(_cat.WRITE_ROOT_CANDIDATES)})"
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
        if root not in resolved.parents and resolved != root:
            raise _ref.Refused(
                _ref.WRITE_PATH_OUTSIDE_REPO, f"{rel!r} resolves outside the repository")
    if not isinstance(intent["items"], list) or not intent["items"]:
        raise _ref.Refused(_ref.INTENT_MALFORMED, "items must be a non-empty list")
