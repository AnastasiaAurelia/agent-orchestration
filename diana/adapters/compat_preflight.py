#!/usr/bin/env python3
"""Diana: bounded, deterministic behavioral compatibility preflight for an
exact, verifiable Hermes identity that is NOT in
`hermes_patches.CERTIFIED_IDENTITIES` (spec: fix/behavioral-runtime-compatibility).

## Why this exists

`CERTIFIED_IDENTITIES` is deliberately a short, hand-reviewed list: adding a
SHA to it is a permanent trust decision that belongs in a reviewed repository
change, never something a running process can do to itself. But treating
every OTHER exact, verifiable SHA as an automatic hard refusal makes an
ordinary, compatible Hermes upgrade indistinguishable from a genuinely
incompatible one -- the environment is merely NEW, not wrong.

This module is the one bounded, deterministic proof of whether an unlisted
exact identity actually satisfies the integration contract Diana depends on.
It re-runs the SAME behavioral probes a certified identity is implicitly held
to -- confinement, capability, and the agent-loop dispatch funnel, all from
`diana/adapters/selftest.py` -- plus the structural integration seams Diana's
own adapters import directly (required module interfaces, PM runtime
resolution via `hermes_runtime.resolve_python`, the provider/runtime
resolution interface `hermes_live` depends on, and the reviewer read-only
projection function `hermes_live.narrow_tool_schemas`). It is NOT a second,
weaker reimplementation of those controls -- it drives the exact same
functions a real governed run drives.

## What a PASS means, and does not mean

A "compatible" result is a RUNTIME fact about one preflight run, scoped to the
exact SHA and this module's own `PREFLIGHT_VERSION`. It is never written back
to `hermes_patches.CERTIFIED_IDENTITIES` -- permanent certification stays an
explicit, reviewed repository change. Every governed run against an unlisted
identity re-proves compatibility; a stale result from a different SHA, or
from an older preflight version, authorizes nothing.

## Why this is the one place that imports Hermes without an identity pin yet

`hermes.check_pre_import`'s docstring is explicit that nothing before it
imports Hermes. Proving BEHAVIORAL compatibility is impossible without
exercising Hermes, so this module is the one deliberate, bounded, documented
exception: it is reached only for an identity that is already exact and
verifiable (never "unknown" or "unreachable"), it is time-bounded, it mutates
no permanent state, and it always restores Hermes's patch state afterward via
`hermes_patches.uninstall()` regardless of outcome.
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
RUNTIME = HERE.parent / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

import hermes_patches as _patches  # noqa: E402
import selftest as _selftest  # noqa: E402

# Bump whenever the checks below change what "compatible" proves. A cached or
# recorded result is bound to this number so a stale preflight from an older,
# weaker contract can never authorize a run under a newer one.
PREFLIGHT_VERSION = 1

# (module, required attributes) Diana's adapters import directly. Missing any
# of these means Diana's OWN integration code cannot run against this
# checkout, independent of whether the behavioral probes below would pass.
REQUIRED_INTERFACES = (
    ("tools.file_tools", ("read_file_tool", "search_tool")),
    ("tools.file_tools_paths", ("_resolve_path_for_task",)),
    ("agent.tool_executor", ("_dispatch_authorized_once",)),
    ("agent.inline_tool_executors", ("INLINE_TOOL_EXECUTORS",)),
    ("model_tools", ("handle_function_call",)),
    ("hermes_cli.version_info", ("get_code_identity",)),
    ("hermes_cli.config", ("load_config",)),
    ("hermes_cli.runtime_provider", ("resolve_runtime_provider",)),
    ("pm.environments", ("project_python",)),
    ("pm.paths", ("repo_root",)),
)


def _result(check: str, ok: bool, detail: str = "") -> dict:
    return {"check": check, "ok": bool(ok), "detail": detail}


def _check_required_interfaces(home: str) -> dict:
    """Run in a FRESH subprocess, never in-process.

    `sys.modules` caches by module NAME, not by the `sys.path` entry that
    resolved it: a process that already imported `tools.file_tools` (etc.)
    against one Hermes home would silently return that cached module for a
    SECOND, different home, turning a genuinely broken checkout into a false
    PASS. This is the same hazard `hermes_patches.hermes_identity`'s own
    docstring documents for `get_code_identity`; the fix here is identical --
    a fresh interpreter has no cache to alias.
    """
    import subprocess

    probe = (
        "import sys, json\n"
        "home = sys.argv[1]\n"
        "if home not in sys.path:\n"
        "    sys.path.insert(0, home)\n"
        "missing = []\n"
        "for mod_name, attrs in %r:\n"
        "    try:\n"
        "        mod = __import__(mod_name, fromlist=['_'])\n"
        "    except Exception as exc:\n"
        "        missing.append(f'{mod_name} ({type(exc).__name__}: {exc})')\n"
        "        continue\n"
        "    for attr in attrs:\n"
        "        if not hasattr(mod, attr):\n"
        "            missing.append(f'{mod_name}.{attr}')\n"
        "print(json.dumps(missing))\n"
    ) % (REQUIRED_INTERFACES,)
    try:
        proc = subprocess.run(
            [sys.executable, "-c", probe, str(home)],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _result("required-hermes-interfaces-present", False, f"{type(exc).__name__}: {exc}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[:300]
        return _result("required-hermes-interfaces-present", False,
                        detail or f"interface probe exited {proc.returncode}")
    try:
        missing = json.loads(proc.stdout.strip() or "[]")
    except json.JSONDecodeError:
        return _result("required-hermes-interfaces-present", False,
                        f"invalid interface probe output: {proc.stdout.strip()!r}")
    return _result("required-hermes-interfaces-present", not missing, "; ".join(missing))


def _check_pm_runtime(home: str) -> dict:
    """PM runtime resolution works, and never silently falls back to ambient Python.

    This preflight may itself be RUNNING under the resolved Hermes interpreter
    already (the in-process path from `hermes.py`'s real governed-run gate),
    so comparing against `sys.executable` would be a false positive there --
    the caller's own interpreter legitimately IS the resolved one in that
    case. The actual "no ambient fallback" property is structural: Hermes's
    PM always resolves to a path under its own managed installs/environments
    layout (`pm.environments.project_python`'s own convention), never a bare
    `python3`/`python` off the caller's `$PATH`.
    """
    try:
        import hermes_runtime

        python = hermes_runtime.resolve_python(home)
    except Exception as exc:  # noqa: BLE001 - fail-closed path, must report
        return _result("pm-runtime-resolution", False, f"{type(exc).__name__}: {exc}")
    if not python.is_file():
        return _result("pm-runtime-resolution", False, f"resolved interpreter does not exist: {python}")
    parts = python.parts
    if not ("installs" in parts and "environments" in parts and "venv" in parts):
        return _result(
            "pm-runtime-resolution", False,
            f"resolved interpreter is not under Hermes's PM-managed installs/environments "
            f"layout ({python}); this is the exact shape an ambient-Python fallback would lack",
        )
    return _result("pm-runtime-resolution", True, str(python))


def _check_provider_runtime_interface(home: str) -> dict:
    """The provider/runtime resolution interface `hermes_live` depends on is present.

    Also run in a fresh subprocess: `hermes_cli.config`/`hermes_cli.runtime_provider`
    are home-specific Hermes modules subject to the exact same `sys.modules`
    cross-home caching hazard as `_check_required_interfaces` above.
    """
    import subprocess

    probe = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(HERE)!r})\n"
        "home = sys.argv[1]\n"
        "if home not in sys.path:\n"
        "    sys.path.insert(0, home)\n"
        "missing = []\n"
        "try:\n"
        "    import hermes_live\n"
        "    missing = [n for n in ('provider_config', 'build_agent', 'narrow_tool_schemas', "
        "'ENVELOPE') if not hasattr(hermes_live, n)]\n"
        "    from hermes_cli.config import load_config\n"
        "    from hermes_cli.runtime_provider import resolve_runtime_provider\n"
        "except Exception as exc:\n"
        "    missing = [f'{type(exc).__name__}: {exc}']\n"
        "print(json.dumps(missing))\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", probe, str(home)],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _result("provider-runtime-interface-compatible", False, f"{type(exc).__name__}: {exc}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[:300]
        return _result("provider-runtime-interface-compatible", False,
                        detail or f"provider-interface probe exited {proc.returncode}")
    try:
        missing = json.loads(proc.stdout.strip() or "[]")
    except json.JSONDecodeError:
        return _result("provider-runtime-interface-compatible", False,
                        f"invalid provider-interface probe output: {proc.stdout.strip()!r}")
    return _result("provider-runtime-interface-compatible", not missing, "; ".join(missing))


def _check_reviewer_projection() -> dict:
    """`hermes_live.narrow_tool_schemas` actually narrows to the read-only envelope.

    Drives the REAL production function against a lightweight stand-in agent
    (a plain object carrying `.tools`) rather than a private reimplementation,
    and without needing a live model call or credentials -- the narrowing
    itself is pure and deterministic.
    """
    try:
        import hermes_live

        fake_schemas = [
            {"function": {"name": n}}
            for n in ("read_file", "search_files", "write_file", "patch", "terminal")
        ]
        fake_agent = types.SimpleNamespace(tools=list(fake_schemas))
        kept = hermes_live.narrow_tool_schemas(fake_agent, allowed=hermes_live.ENVELOPE)
    except Exception as exc:  # noqa: BLE001 - fail-closed path, must report
        return _result("reviewer-projection-read-only", False, f"{type(exc).__name__}: {exc}")
    expected = sorted(hermes_live.ENVELOPE)
    ok = kept == expected and not any(n in kept for n in ("write_file", "patch", "terminal"))
    return _result("reviewer-projection-read-only", ok, f"kept={kept}")


def _behavioral_probes(home: str, tmp_root: str) -> list[dict]:
    """The SAME confinement/capability/dispatch-funnel probes a certified
    identity is implicitly held to (`diana/adapters/selftest.py`), never a
    weaker reimplementation. Always restores Hermes's patch state afterward.

    `hermes_patches.HERMES_HOME` (and therefore `selftest.py`'s probes, which
    reference it directly rather than taking a `home` parameter) is a
    MODULE-LEVEL value fixed at import time from `DIANA_HERMES_HOME`. If this
    preflight's `home` differs from that value, every probe below would
    silently exercise the WRONG checkout. This temporarily repoints the
    module global for the duration of the probes and always restores it,
    even on failure.
    """
    results: list[dict] = []
    tree = _selftest.build_probe_tree(tmp_root)
    scope = {"allowed_roots": [tree["repo"]], "denied_subpaths": [".git/", ".env", ".env.*"]}
    original_home = _patches.HERMES_HOME
    _patches.HERMES_HOME = home
    try:
        _patches.install_confinement(scope)
        _patches.install_capability(("read_file", "search_files"))
        if not _patches.confinement_live():
            results.append(_result("confinement-patch-live", False, "install_confinement did not take"))
            return results
        if not _patches.capability_live():
            results.append(_result("capability-patch-live", False, "install_capability did not take"))
            return results
        results.append(_result("confinement-patch-live", True))
        results.append(_result("capability-patch-live", True))
        for probe in _selftest.confinement_probes(tree):
            results.append(_result(f"confinement: {probe['probe']}", probe["ok"], probe["detail"]))
        for probe in _selftest.capability_probes(tree):
            results.append(_result(f"capability: {probe['probe']}", probe["ok"], probe["detail"]))
        for probe in _selftest.dispatch_funnel_probes():
            results.append(_result(f"dispatch-funnel: {probe['probe']}", probe["ok"], probe["detail"]))
    except Exception as exc:  # noqa: BLE001 - fail-closed: an incompatible checkout can raise
        # here (e.g. a required module the patches themselves import is
        # missing) rather than returning cleanly; that is itself proof of
        # incompatibility, not a crash this preflight should propagate.
        results.append(_result("behavioral-probes-completed", False, f"{type(exc).__name__}: {exc}"))
    finally:
        # This preflight must never leave the process in a patched state for
        # whatever the caller does next; the real run (re-)installs its own
        # scope immediately after this preflight returns either way.
        _patches.uninstall()
        _patches.HERMES_HOME = original_home
    return results


def run(home: str | None = None) -> dict:
    """The bounded, deterministic compatibility preflight. Pass/fail only.

    Never promotes its own result into `hermes_patches.CERTIFIED_IDENTITIES`;
    permanent certification remains an explicit, reviewed repository change.
    """
    home = str(home or _patches.HERMES_HOME)
    checks = [
        _check_required_interfaces(home),
        _check_pm_runtime(home),
        _check_provider_runtime_interface(home),
        _check_reviewer_projection(),
    ]
    with tempfile.TemporaryDirectory(prefix="diana-compat-preflight-") as tmp:
        checks.extend(_behavioral_probes(home, tmp))
    failed = [c for c in checks if not c["ok"]]
    return {
        "preflight_version": PREFLIGHT_VERSION,
        "home": home,
        "result": "incompatible" if failed else "compatible",
        "checks": checks,
    }


def main(argv: list[str]) -> int:
    home = argv[0] if argv else None
    report = run(home)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["result"] == "compatible" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
