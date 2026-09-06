#!/usr/bin/env python3
"""Diana's sole Agent Orchestrator (AO) integration boundary.

No other Diana file may construct a raw `ao` command. Everything this
adapter needs from AO goes through AO's public CLI subcommands
(`ao version`, `ao doctor --json`, `ao spawn`, `ao session get`,
`ao session kill`), never AO's private daemon HTTP API (`/api/v1/...`) or
its private SQLite database. GitHub PR/CI/review state is `gh`'s job, not
this adapter's; browser verification is Playwright MCP's job, not AO's
Electron browser.

Codex (or any harness other than claude-code) cannot be spawned through this
adapter: `spawn()` hardcodes `--harness claude-code`. This is deliberate -
AO + Codex autonomous writes are not certified (see MEMORY.md section 5) and
this boundary must not grow a way around that.

Usage:
    python3 ao.py check   [--ao-bin PATH] [--ao-home PATH]
    python3 ao.py spawn   --project ID --name NAME --prompt TEXT [--branch B] [--ao-bin PATH]
    python3 ao.py status  --session ID [--project ID] [--ao-bin PATH]
    python3 ao.py stop    --session ID [--project ID] [--ao-bin PATH]

Each subcommand prints one JSON object to stdout and exits 0 on success,
1 on failure. No subcommand's success/failure is ever decided by an LLM.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

PINNED_VERSION = "0.12.10"
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+")


def resolve_ao_bin(explicit: str | None) -> str | None:
    """Find the `ao` CLI binary. Never guesses at ephemeral AppImage mount
    paths under /tmp - those exist only while AO's Electron shell happens to
    be running under this exact process tree, so trusting them would make
    the adapter depend on private runtime internals instead of a supported
    entry point. Callers that only have a mount path must pass --ao-bin or
    set DIANA_AO_BIN explicitly.
    """
    for candidate in (explicit, os.environ.get("DIANA_AO_BIN")):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("ao")


def resolve_ao_home(explicit: str | None) -> str:
    return explicit or os.environ.get("DIANA_AO_HOME") or os.path.expanduser("~/.ao")


def run_ao(ao_bin: str, args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [ao_bin, *args], capture_output=True, text=True, timeout=timeout
    )


def ok(action: str, **fields) -> dict:
    return {"ok": True, "action": action, **fields}


def fail(action: str, code: str, detail: str) -> dict:
    return {"ok": False, "action": action, "error": code, "detail": detail}


def detect_version(ao_bin: str, ao_home: str) -> dict:
    """Return {"value": str|None, "source": str, "cli_raw": str}.

    `ao version` is AO's public/supported version command, so it is tried
    first. At the currently pinned AO build this prints the placeholder
    string "dev" instead of a real release version, so this falls back to
    AO's own on-disk install-state record (~/.ao/app-state.json). That file
    is AO's own recorded installation metadata, not its private runtime
    SQLite database (ao.db) and not a call to its private daemon HTTP API -
    it is the same signal used to manually verify the AO version during
    Phase 4/6. If neither source yields a usable version, that is reported
    honestly rather than assumed.
    """
    result = run_ao(ao_bin, ["version"], timeout=15)
    cli_raw = (result.stdout or "").strip()
    if SEMVER_RE.match(cli_raw):
        return {"value": cli_raw, "source": "ao version", "cli_raw": cli_raw}

    state_path = os.path.join(ao_home, "app-state.json")
    if os.path.isfile(state_path):
        try:
            with open(state_path, encoding="utf-8") as f:
                data = json.load(f)
            value = data.get("version")
            if value:
                return {
                    "value": value,
                    "source": (
                        f"{state_path} (AO's recorded install state; "
                        f"`ao version` printed non-version string {cli_raw!r})"
                    ),
                    "cli_raw": cli_raw,
                }
        except (json.JSONDecodeError, OSError):
            pass

    return {"value": None, "source": None, "cli_raw": cli_raw}


def cmd_check(args: argparse.Namespace) -> dict:
    ao_bin = resolve_ao_bin(args.ao_bin)
    if not ao_bin:
        return fail(
            "check", "ao_missing",
            "ao CLI binary not found (checked --ao-bin, DIANA_AO_BIN, then PATH)",
        )

    ao_home = resolve_ao_home(args.ao_home)
    version = detect_version(ao_bin, ao_home)
    if not version["value"]:
        return fail(
            "check", "version_unknown",
            f"ao version printed {version['cli_raw']!r} and no readable "
            f"app-state.json was found under {ao_home}",
        )
    if version["value"] != PINNED_VERSION:
        return fail(
            "check", "version_incompatible",
            f"installed AO version {version['value']!r} (via {version['source']}) "
            f"does not match the pinned {PINNED_VERSION!r}. Diana does not "
            f"auto-upgrade or auto-downgrade AO.",
        )

    try:
        doctor = run_ao(ao_bin, ["doctor", "--json"], timeout=15)
    except (subprocess.SubprocessError, OSError) as exc:
        return fail("check", "ao_runtime_unavailable", f"ao doctor did not run: {exc}")

    try:
        doctor_data = json.loads(doctor.stdout or "{}")
    except json.JSONDecodeError:
        return fail(
            "check", "ao_runtime_unavailable",
            f"ao doctor --json produced unparseable output: {doctor.stdout!r}",
        )

    daemon_ready = any(
        c.get("section") == "Core" and c.get("name") == "daemon" and c.get("level") == "PASS"
        for c in doctor_data.get("checks", [])
    )
    if not daemon_ready:
        return fail(
            "check", "ao_runtime_unavailable",
            "ao doctor reports the AO daemon is not ready",
        )

    return ok(
        "check",
        ao_bin=ao_bin,
        version=version["value"],
        version_source=version["source"],
        pinned_version=PINNED_VERSION,
    )


def cmd_spawn(args: argparse.Namespace) -> dict:
    ao_bin = resolve_ao_bin(args.ao_bin)
    if not ao_bin:
        return fail("spawn", "ao_missing", "ao CLI binary not found")

    cli_args = [
        "spawn",
        "--project", args.project,
        "--harness", "claude-code",  # hardcoded: this adapter never spawns Codex
        "--kind", "worker",
        "--mode", "chat",
        "--name", args.name,
        "--prompt", args.prompt,
    ]
    if args.branch:
        cli_args += ["--branch", args.branch]

    try:
        result = run_ao(ao_bin, cli_args, timeout=120)
    except (subprocess.SubprocessError, OSError) as exc:
        return fail("spawn", "spawn_failed", str(exc))

    if result.returncode != 0:
        return fail(
            "spawn", "spawn_failed",
            (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}",
        )

    match = re.search(r"spawned session (\S+)", result.stdout)
    if not match:
        return fail("spawn", "spawn_unparseable", result.stdout.strip())

    return ok("spawn", session_id=match.group(1), raw=result.stdout.strip())


def cmd_status(args: argparse.Namespace) -> dict:
    ao_bin = resolve_ao_bin(args.ao_bin)
    if not ao_bin:
        return fail("status", "ao_missing", "ao CLI binary not found")

    cli_args = ["session", "get", args.session, "--json"]
    if args.project:
        cli_args += ["--project", args.project]

    try:
        result = run_ao(ao_bin, cli_args, timeout=30)
    except (subprocess.SubprocessError, OSError) as exc:
        return fail("status", "status_failed", str(exc))

    if result.returncode != 0:
        return fail(
            "status", "status_failed",
            (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}",
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return fail("status", "status_unparseable", result.stdout.strip())

    return ok("status", session=data.get("session", data))


def cmd_stop(args: argparse.Namespace) -> dict:
    ao_bin = resolve_ao_bin(args.ao_bin)
    if not ao_bin:
        return fail("stop", "ao_missing", "ao CLI binary not found")

    cli_args = ["session", "kill", args.session]
    if args.project:
        cli_args += ["--project", args.project]

    try:
        result = run_ao(ao_bin, cli_args, timeout=30)
    except (subprocess.SubprocessError, OSError) as exc:
        return fail("stop", "stop_failed", str(exc))

    if result.returncode != 0:
        return fail(
            "stop", "stop_failed",
            (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}",
        )

    return ok("stop", raw=result.stdout.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check_p = sub.add_parser("check", help="Verify AO is present and version-compatible")
    check_p.add_argument("--ao-bin")
    check_p.add_argument("--ao-home")
    check_p.set_defaults(func=cmd_check)

    spawn_p = sub.add_parser("spawn", help="Spawn a claude-code worker session")
    spawn_p.add_argument("--project", required=True)
    spawn_p.add_argument("--name", required=True)
    spawn_p.add_argument("--prompt", required=True)
    spawn_p.add_argument("--branch")
    spawn_p.add_argument("--ao-bin")
    spawn_p.set_defaults(func=cmd_spawn)

    status_p = sub.add_parser("status", help="Fetch one session's status")
    status_p.add_argument("--session", required=True)
    status_p.add_argument("--project")
    status_p.add_argument("--ao-bin")
    status_p.set_defaults(func=cmd_status)

    stop_p = sub.add_parser("stop", help="Terminate one session")
    stop_p.add_argument("--session", required=True)
    stop_p.add_argument("--project")
    stop_p.add_argument("--ao-bin")
    stop_p.set_defaults(func=cmd_stop)

    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    result = args.func(args)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
