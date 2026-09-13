#!/usr/bin/env python3
"""Diana: the two in-process Hermes patches that make an M1 run bounded.

Spec D18-D22, C1. These two patches plus the version pin are ONE trusted
computing base unit, and a behavioral self-test -- not their mere installation --
is what proves they are live (D26, D27).

## Why patches rather than configuration

Both boundaries M1 needs are absent from Hermes by construction, not by
oversight:

* **Confinement.** `tools/file_tools_paths._resolve_path_for_task` returns
  absolute inputs "resolved-but-unanchored", and `_authoritative_workspace_root`
  only anchors RELATIVE paths and is explicitly best-effort. Nothing checks a
  root, so `read_file("/home/u/.ssh/id_rsa")` is simply read. Diana must CREATE
  this boundary (D20).

* **Capability.** `tools/registry.py:86 discover_builtin_tools` imports every
  self-registering module, so the registry holds `write_file`, `terminal`,
  `execute_code` and `delegate_task` no matter what the model is shown, and
  `registry.dispatch` resolves by name with a fall back to global. Hiding tools
  from the model schema is cosmetic: if the model emits `write_file` -- by
  hallucination, injection, or a stale trajectory -- the handler runs (D21/D22).

## Why NOT the pre_tool_call hook

`agent/tool_executor.py:622 _pre_tool_block` wraps hook dispatch in
`except Exception: return None` under the docstring "Hook failures never block".
A hook that is internally fail-closed still yields a fail-open system, because
the failure that matters is the one preventing a verdict. And
`set_thread_tool_whitelist` is `threading.local()` while tools dispatch on
`DaemonThreadPoolExecutor` workers with no contextvars propagation, so it is
inert on the execution path -- it is deliberately NOT used here (D24).

## Why capability enforcement needs TWO entries, not one

`handle_function_call` is the sole entry for *registry* dispatch, but it is NOT
the only way a tool runs. The agent loop resolves some tools to INLINE
executors that never reach it:

* `agent/inline_tool_executors.py:153` `INLINE_TOOL_EXECUTORS` maps 13 names --
  including **`delegate_task`** -- to callables invoked directly.
* Concurrent path: `agent/agent_runtime_helpers.py:2265` takes the inline
  branch and only falls through to `handle_function_call` in its `else`.
* Sequential path: `agent/tool_executor.py:1498 _resolve_sequential_dispatch`
  has its own inline branch, a dedicated `delegate_task` branch, a
  context-engine branch, and a memory-provider branch.

Both paths funnel through **`_dispatch_authorized_once`**
(`agent/tool_executor.py:639`, called at `:724` via a module-global name), which
receives the `execute` callable for EVERY branch. Diana therefore guards there
too, by SUBSTITUTING `execute` rather than short-circuiting: Hermes keeps its
start-order gate, spinner and terminal-hook bookkeeping -- the file warns that
`begin_execution` must advance on every path or later workers wedge -- and only
the thing that would have run is replaced by a refusal.

## Why handle_function_call rather than registry.dispatch

`registry.dispatch` is not the sole choke point: `model_tools.py:827` routes any
connector-prefixed name to `dispatch_connector_call` BEFORE reaching it, on a
name-prefix claim that the source itself calls "a claim, not a guarantee".
`handle_function_call` is the proven single entry -- `_execute_tool` has one call
site, `registry.dispatch` one production call site, and the connector branch
re-enters through it.

## Forward compatibility

The dispatch boundary is where bounded writes, bounded shell, and test execution
get adjudicated in later milestones: `allowed_tools` grows into per-tool argument
and path policy consulted at this same choke point. The boundary is not replaced
later, it gets a richer policy -- which is what makes unattended bounded
execution reachable without granting unrestricted authority.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runtime"))
import blocking  # noqa: E402
import read_scope as _read_scope  # noqa: E402

HERMES_HOME = os.environ.get("DIANA_HERMES_HOME", str(Path.home() / ".hermes" / "hermes-agent"))

# Spec C1 -- the mandatory M1 pin.
PINNED_VERSION = "0.21.1"
PINNED_COMMIT = "b8e8639445bd6f05a8141abcea7ae2aa8279f2b7"

_STATE = {"confinement": None, "capability": None, "dispatch": None}

REFUSAL = ('{"error": "diana: tool %s is not in the execution contract '
           'capability envelope; refused before dispatch"}')


class ScopeDenied(Exception):
    """Raised by the confinement wrapper when a path is outside the contract.

    NOT an OSError, ValueError or RuntimeError -- deliberately. `search_tool`
    (`tools/file_tools.py:953-960`) wraps its `_resolve_path_for_task` call in
    `except (OSError, ValueError, RuntimeError)` and falls back to the
    UNRESOLVED path, so a `PermissionError` denial would be swallowed and the
    search would proceed unconfined. Diana's behavioral self-test caught exactly
    that, which is the whole reason the self-test drives real tools instead of
    the wrapper. Choosing a type outside that tuple keeps the denial propagating
    through every file tool that shares the choke point.
    """


# --- version pin (C1, D27) -------------------------------------------------

def hermes_version(home: str | None = None) -> str | None:
    init = Path(home or HERMES_HOME) / "hermes_cli" / "__init__.py"
    try:
        for line in init.read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def hermes_commit(home: str | None = None) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(home or HERMES_HOME), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


# --- patch 1: confinement (D20) -------------------------------------------

def install_confinement(read_scope_block: dict):
    """Wrap `_resolve_path_for_task` so no in-scope check can be bypassed.

    Module-global, so it holds on every pool worker thread -- unlike the
    thread-local whitelist, which does not. Denial raises `ScopeDenied`, whose
    type is chosen so `search_tool`'s fail-open `except (OSError, ValueError,
    RuntimeError)` cannot absorb it; the agent sees a refusal, never the file.
    """
    if str(HERMES_HOME) not in sys.path:
        sys.path.insert(0, str(HERMES_HOME))
    import tools.file_tools_paths as fp

    original = getattr(fp, "_diana_original_resolve", None) or fp._resolve_path_for_task

    def guarded(filepath, task_id="default"):
        resolved = original(filepath, task_id)
        allowed, why = _read_scope.decide(str(resolved), read_scope_block)
        if not allowed:
            raise ScopeDenied(f"diana: read outside contract read_scope refused ({why})")
        return resolved

    guarded.__name__ = "_resolve_path_for_task"
    guarded.__doc__ = (original.__doc__ or "") + "\n\nWrapped by Diana: contract read_scope enforced."
    fp._diana_original_resolve = original
    fp._resolve_path_for_task = guarded

    # `tools.file_tools` binds the name at import time, so patching only the
    # defining module would leave a stale reference and silently unconfine every
    # read. This is exactly the failure mode the behavioral self-test exists to
    # catch, and rebinding every importer closes it.
    rebound = []
    for mod_name in ("tools.file_tools", "tools.file_tools_read_tracking",
                     "tools.file_tools_write_guards", "tools.code_execution_env",
                     "tools.mcp_tool_config"):
        module = sys.modules.get(mod_name)
        if module is not None and hasattr(module, "_resolve_path_for_task"):
            setattr(module, "_resolve_path_for_task", guarded)
            rebound.append(mod_name)
    _STATE["confinement"] = {"read_scope": read_scope_block, "rebound": rebound}
    return guarded


def confinement_live() -> bool:
    module = sys.modules.get("tools.file_tools_paths")
    return bool(module and getattr(module, "_resolve_path_for_task", None) is not None
                and hasattr(module, "_diana_original_resolve"))


# --- patch 2: capability (D21, D22) ---------------------------------------

def install_capability(allowed_tools) -> None:
    """Refuse any tool outside the contract envelope BEFORE its handler runs.

    The invariant is `tool_name in allowed_tools or the call is blocked before
    its handler executes`. The global registry may keep all 88 tools; their
    existence is irrelevant if dispatch cannot reach them outside the contract.
    """
    if str(HERMES_HOME) not in sys.path:
        sys.path.insert(0, str(HERMES_HOME))
    import model_tools as mt

    allowed = frozenset(allowed_tools)
    original = getattr(mt, "_diana_original_handle", None) or mt.handle_function_call

    def guarded(function_name, function_args, *args, **kwargs):
        if function_name not in allowed:
            # Fail closed on unknown names too: a tool Hermes gains in a future
            # version is denied by set membership, with no special case.
            return REFUSAL % function_name
        return original(function_name, function_args, *args, **kwargs)

    guarded.__name__ = "handle_function_call"
    guarded.__doc__ = (original.__doc__ or "") + "\n\nWrapped by Diana: capability envelope enforced."
    mt._diana_original_handle = original
    mt.handle_function_call = guarded

    rebound = []
    for mod_name in ("agent.tool_executor", "agent.agent_runtime_helpers", "model_tools_connectors"):
        module = sys.modules.get(mod_name)
        if module is not None and hasattr(module, "handle_function_call"):
            setattr(module, "handle_function_call", guarded)
            rebound.append(mod_name)
    _STATE["capability"] = {"allowed_tools": sorted(allowed), "rebound": rebound}
    _install_dispatch_guard(allowed)


def _install_dispatch_guard(allowed) -> None:
    """Guard the agent loop's common dispatch funnel (both executor paths).

    Without this, every tool in `INLINE_TOOL_EXECUTORS` -- `delegate_task`
    among them -- executes without ever passing `handle_function_call`.
    """
    import agent.tool_executor as te

    original = getattr(te, "_diana_original_dispatch", None) or te._dispatch_authorized_once

    def guarded_dispatch(agent_, state, ref, *, execute, **kwargs):
        name = getattr(ref, "name", None)
        if name not in allowed:
            def refused(_args, _name=name):
                return REFUSAL % _name
            return original(agent_, state, ref, execute=refused, **kwargs)
        return original(agent_, state, ref, execute=execute, **kwargs)

    guarded_dispatch.__name__ = "_dispatch_authorized_once"
    te._diana_original_dispatch = original
    te._dispatch_authorized_once = guarded_dispatch
    _STATE["dispatch"] = {"allowed_tools": sorted(allowed)}


def capability_live() -> bool:
    """Both capability entries must be live; one alone leaves a real bypass."""
    model_tools_mod = sys.modules.get("model_tools")
    executor = sys.modules.get("agent.tool_executor")
    return bool(
        model_tools_mod and hasattr(model_tools_mod, "_diana_original_handle")
        and executor and hasattr(executor, "_diana_original_dispatch")
    )


def uninstall() -> None:
    """Restore Hermes. Used by tests that must prove a patch was load-bearing."""
    fp = sys.modules.get("tools.file_tools_paths")
    if fp is not None and hasattr(fp, "_diana_original_resolve"):
        fp._resolve_path_for_task = fp._diana_original_resolve
        del fp._diana_original_resolve
        for mod_name in ("tools.file_tools", "tools.file_tools_read_tracking",
                         "tools.file_tools_write_guards", "tools.code_execution_env",
                         "tools.mcp_tool_config"):
            module = sys.modules.get(mod_name)
            if module is not None and hasattr(module, "_resolve_path_for_task"):
                setattr(module, "_resolve_path_for_task", fp._resolve_path_for_task)
    te = sys.modules.get("agent.tool_executor")
    if te is not None and hasattr(te, "_diana_original_dispatch"):
        te._dispatch_authorized_once = te._diana_original_dispatch
        del te._diana_original_dispatch
    mt = sys.modules.get("model_tools")
    if mt is not None and hasattr(mt, "_diana_original_handle"):
        mt.handle_function_call = mt._diana_original_handle
        del mt._diana_original_handle
        for mod_name in ("agent.tool_executor", "agent.agent_runtime_helpers", "model_tools_connectors"):
            module = sys.modules.get(mod_name)
            if module is not None and hasattr(module, "handle_function_call"):
                setattr(module, "handle_function_call", mt.handle_function_call)
    _STATE["confinement"] = None
    _STATE["capability"] = None
    _STATE["dispatch"] = None
