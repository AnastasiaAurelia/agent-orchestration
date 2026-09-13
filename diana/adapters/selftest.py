#!/usr/bin/env python3
"""Diana: the behavioral self-test that gates every M1 run (spec D26, D27).

A patch that is installed is not a patch that is live. If an upstream rename or
a stale import binding makes `install_confinement` a no-op, every read is
unconfined **and everything looks normal** -- the same failure class as an absent
hook, at a boundary where after-the-fact detection is worthless.

So M1 does not trust installation. Before Hermes receives any input, Diana drives
the REAL tool entry points and observes what actually happens. Any deviation is
BLOCKED: no Hermes invocation, no advisory artifact.

Two properties make this test meaningful rather than ceremonial:

* **It uses the real tools, not the patched functions.** Calling
  `_resolve_path_for_task` directly would prove only that the wrapper works, not
  that `read_file` reaches it. A future tool path that bypasses the choke point,
  or a stale module-level import, is only visible from the outside.

* **It runs on the actual pool-worker path.** Hermes dispatches tools on
  `DaemonThreadPoolExecutor` workers (`agent/tool_executor.py:1332`, and `:856`
  even for the sequential path). A probe on the main thread would pass while
  enforcement was absent on workers -- which is precisely why the thread-local
  whitelist is not used as a control (D24).

This doubles as the version tripwire: an upstream rename of a patch target fails
these probes loudly instead of silently disabling confinement.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runtime"))
import blocking  # noqa: E402
import hermes_patches as _patches  # noqa: E402

DENIED_MARKER = "diana:"


def _on_worker(fn, *args, **kwargs):
    """Run `fn` on a Hermes pool worker, exactly where real tool calls land."""
    if str(_patches.HERMES_HOME) not in sys.path:
        sys.path.insert(0, str(_patches.HERMES_HOME))
    from tools.daemon_pool import DaemonThreadPoolExecutor

    executor = DaemonThreadPoolExecutor(max_workers=2)
    try:
        return executor.submit(fn, *args, **kwargs).result(timeout=60)
    finally:
        executor.shutdown(wait=False)


def _read(path: str) -> str:
    import tools.file_tools as ft
    return str(ft.read_file_tool(path=path))


def _search(pattern: str, path: str) -> str:
    import model_tools as mt
    return str(mt.handle_function_call(
        "search_files", {"pattern": pattern, "path": path, "target": "files"}
    ))


def _call(tool: str, args: dict) -> str:
    import model_tools as mt
    return str(mt.handle_function_call(tool, args))


def _denied(result: str) -> bool:
    return DENIED_MARKER in result


def build_probe_tree(root: str) -> dict:
    """Sentinels for the confinement probes, including two a naive
    implementation would wrongly allow: a readable file OUTSIDE the roots, and
    an in-repo symlink whose target escapes."""
    repo = Path(root, "repo")
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "app.js").write_text("var sentinel = 'in-scope';\n")
    (repo / ".env").write_text("SECRET=must-not-be-read\n")
    outside = Path(root, "outside")
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "secret.txt").write_text("diana-outside-sentinel\n")
    link = repo / "escape"
    if not link.exists() and not link.is_symlink():
        link.symlink_to(outside)
    return {
        "repo": str(repo),
        "in_scope": str(repo / "app.js"),
        "denied_env": str(repo / ".env"),
        "outside": str(outside / "secret.txt"),
        "symlink_escape": str(link / "secret.txt"),
    }


def confinement_probes(tree: dict) -> list[dict]:
    """AC-2, confinement half. Every probe runs on a pool worker."""
    checks = [
        ("read_file allows an in-scope sentinel",
         lambda: "in-scope" in _on_worker(_read, tree["in_scope"])),
        ("read_file denies a readable sentinel outside allowed_roots",
         lambda: _denied(_on_worker(_read, tree["outside"]))),
        ("read_file denies an in-repo symlink escaping the root",
         lambda: _denied(_on_worker(_read, tree["symlink_escape"]))),
        ("read_file denies .env inside the root",
         lambda: _denied(_on_worker(_read, tree["denied_env"]))),
        ("read_file denies an absolute path to a system file",
         lambda: _denied(_on_worker(_read, "/etc/passwd"))),
        ("search_files allows an in-scope directory",
         lambda: not _denied(_on_worker(_search, "*.js", tree["repo"]))),
        ("search_files denies an out-of-scope directory",
         lambda: _denied(_on_worker(_search, "*", str(Path(tree["outside"]).parent)))),
    ]
    return _run(checks)


def capability_probes(tree: dict) -> list[dict]:
    """AC-2, capability half. Proves refusal happens BEFORE the handler runs."""
    canary = str(Path(tree["repo"], "canary.txt"))
    checks = [
        ("read_file executes when allowed",
         lambda: "in-scope" in _on_worker(_call, "read_file", {"path": tree["in_scope"]})),
        ("search_files executes when allowed",
         lambda: not _denied(_on_worker(_call, "search_files",
                                        {"pattern": "*.js", "path": tree["repo"], "target": "files"}))),
        ("a crafted write_file call is blocked",
         lambda: _denied(_on_worker(_call, "write_file",
                                    {"path": canary, "content": "written"}))),
        ("write_file was blocked BEFORE its handler executed (no file created)",
         lambda: not Path(canary).exists()),
        ("terminal is blocked",
         lambda: _denied(_on_worker(_call, "terminal", {"command": "echo hi"}))),
        ("execute_code is blocked",
         lambda: _denied(_on_worker(_call, "execute_code", {"code": "print(1)"}))),
        ("delegate_task is blocked (no special case needed)",
         lambda: _denied(_on_worker(_call, "delegate_task", {"task": "anything"}))),
        ("an unknown synthetic tool name is blocked",
         lambda: _denied(_on_worker(_call, "diana_synthetic_future_tool", {}))),
        ("a connector-prefixed name cannot route around the boundary",
         lambda: _denied(_on_worker(_call, "connector__acme__send", {}))),
    ]
    return _run(checks)


def _run(checks) -> list[dict]:
    results = []
    for label, probe in checks:
        try:
            ok = bool(probe())
            detail = ""
        except Exception as exc:  # a probe that cannot run has not proven anything
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append({"probe": label, "ok": ok, "detail": detail})
    return results


def assert_all(results: list[dict], reason_code: str) -> None:
    """Any deviation blocks the run outright (D26)."""
    bad = [r for r in results if not r["ok"]]
    if bad:
        raise blocking.Blocked(
            reason_code,
            "; ".join(f"{r['probe']}{' -- ' + r['detail'] if r['detail'] else ''}" for r in bad),
        )
