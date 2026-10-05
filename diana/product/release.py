#!/usr/bin/env python3
"""Diana release identity and deterministic operator diagnostics.

This module is intentionally stdlib-only so `diana-do doctor` can diagnose a
broken Hermes installation before any governed execution interpreter exists.

It grants no authority and runs no model call.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIANA_ROOT = HERE.parent.parent
ADAPTERS = DIANA_ROOT / "diana" / "adapters"
if str(ADAPTERS) not in sys.path:
    sys.path.insert(0, str(ADAPTERS))

RELEASE_SCHEMA_VERSION = 1
SUPPORTED_SYSTEM = "Linux"
SUPPORTED_PYTHON = (3, 11)
VERSION_FILE = DIANA_ROOT / "VERSION"
MANIFEST_NAME = "release-manifest.json"


def diana_version() -> str:
    try:
        value = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return value or "unknown"


def source_commit() -> str | None:
    manifest = DIANA_ROOT / MANIFEST_NAME
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        value = data.get("source_commit")
        return value if isinstance(value, str) and value else None
    try:
        proc = subprocess.run(
            ["git", "-C", str(DIANA_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else None


def _certified_identities() -> tuple[dict, ...]:
    import hermes_patches  # local Diana module, stdlib-only at import time
    return tuple(dict(x) for x in hermes_patches.CERTIFIED_IDENTITIES)


def _resolve_hermes_python(home: str) -> tuple[str | None, str | None]:
    try:
        import hermes_runtime
        python = hermes_runtime.resolve_python(home)
    except Exception as exc:  # diagnostic path must report, not disguise
        return None, f"{type(exc).__name__}: {exc}"
    return str(python), None


def _hermes_identity(python: str, home: str) -> tuple[dict | None, str | None]:
    probe = (
        "import sys,json;"
        f"sys.path.insert(0,{home!r});"
        "from hermes_cli.version_info import get_code_identity;"
        "print(json.dumps(get_code_identity()))"
    )
    try:
        proc = subprocess.run(
            [python, "-c", probe],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return None, detail or f"identity probe exited {proc.returncode}"
    try:
        identity = json.loads(proc.stdout.strip())
    except json.JSONDecodeError as exc:
        return None, f"invalid identity JSON: {exc}"
    if not isinstance(identity, dict):
        return None, "identity is not an object"
    return identity, None


def _identity_state(identity: dict | None) -> str:
    if not identity:
        return "unverifiable"
    source = identity.get("source")
    sha = identity.get("sha")
    if not source or source in {"unknown", "unreachable"} or not sha:
        return "unverifiable"
    return "ok" if any(
        item.get("source") == source and item.get("sha") == sha
        for item in _certified_identities()
    ) else "mismatch"


def version_document() -> dict:
    return {
        "release_schema_version": RELEASE_SCHEMA_VERSION,
        "diana_version": diana_version(),
        "source_commit": source_commit(),
        "supported_system": SUPPORTED_SYSTEM,
        "supported_python": f"{SUPPORTED_PYTHON[0]}.{SUPPORTED_PYTHON[1]}",
        "certified_hermes_identities": list(_certified_identities()),
    }


def doctor_document(hermes_home: str | None = None) -> tuple[dict, bool]:
    home = hermes_home or os.environ.get(
        "DIANA_HERMES_HOME", str(Path.home() / ".hermes" / "hermes-agent")
    )
    system = platform.system()
    py = (sys.version_info.major, sys.version_info.minor)
    hermes_python, runtime_error = _resolve_hermes_python(home)
    identity = None
    identity_error = None
    if hermes_python:
        identity, identity_error = _hermes_identity(hermes_python, home)
    identity_state = _identity_state(identity)
    checks = {
        "platform_supported": system == SUPPORTED_SYSTEM,
        "python_supported": py == SUPPORTED_PYTHON,
        "diana_version_known": diana_version() != "unknown",
        "diana_source_known": source_commit() is not None,
        "diana_source_immutable": source_tree_clean() is not False,
        "hermes_home_present": Path(home).is_dir(),
        "hermes_runtime_resolved": hermes_python is not None,
        "hermes_identity_certified": identity_state == "ok",
    }
    safe = all(checks.values())
    return {
        "release_schema_version": RELEASE_SCHEMA_VERSION,
        "safe_to_start_governed_run": safe,
        "diana_version": diana_version(),
        "source_commit": source_commit(),
        "platform": {"actual": system, "supported": [SUPPORTED_SYSTEM]},
        "python": {
            "actual": f"{py[0]}.{py[1]}",
            "supported": [f"{SUPPORTED_PYTHON[0]}.{SUPPORTED_PYTHON[1]}"],
        },
        "hermes_home": home,
        "hermes_python": hermes_python,
        "hermes_runtime_error": runtime_error,
        "hermes_identity": identity,
        "hermes_identity_error": identity_error,
        "hermes_identity_state": identity_state,
        "checks": checks,
    }, safe


def print_human_doctor(doc: dict) -> None:
    for key, ok in doc["checks"].items():
        print(f"{'PASS' if ok else 'FAIL'}  {key}")
    print(f"INFO  diana_version={doc['diana_version']}")
    print(f"INFO  source_commit={doc['source_commit'] or 'unknown'}")
    print(f"INFO  platform={doc['platform']['actual']}")
    print(f"INFO  python={doc['python']['actual']}")
    print(f"INFO  hermes_home={doc['hermes_home']}")
    print(f"INFO  hermes_python={doc['hermes_python'] or 'unresolved'}")
    identity = doc.get("hermes_identity") or {}
    print(f"INFO  hermes_identity_source={identity.get('source', 'unverifiable')}")
    print(f"INFO  hermes_identity_sha={identity.get('sha') or 'unverifiable'}")
    print(f"INFO  hermes_identity_state={doc['hermes_identity_state']}")
    print(
        "READY  governed-run bootstrap"
        if doc["safe_to_start_governed_run"]
        else "REFUSED  governed-run bootstrap"
    )


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "version"
    if command == "version":
        print(json.dumps(version_document(), sort_keys=True))
        return 0
    if command == "doctor":
        hermes_home = argv[1] if len(argv) > 1 else None
        doc, safe = doctor_document(hermes_home)
        print_human_doctor(doc)
        return 0 if safe else 3
    if command == "doctor-json":
        hermes_home = argv[1] if len(argv) > 1 else None
        doc, safe = doctor_document(hermes_home)
        print(json.dumps(doc, sort_keys=True))
        return 0 if safe else 3
    print("usage: release.py [version|doctor|doctor-json] [hermes-home]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
