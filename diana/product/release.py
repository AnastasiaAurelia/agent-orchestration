#!/usr/bin/env python3
"""Diana release identity and deterministic operator diagnostics.

This module is intentionally stdlib-only so `diana-do doctor` can diagnose a
broken Hermes installation before any governed execution interpreter exists.

It grants no authority and runs no model call.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import stat
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
# The Python that invokes release.py / diana-do diagnostics. A RANGE, not an
# exact pin: an ordinary compatible bootstrap-interpreter upgrade (3.11 ->
# 3.12/3.13/3.14) must not be indistinguishable from a genuinely unsupported
# one (<=3.10 or >=3.15). Mirrors the shape already used for the Hermes
# runtime interpreter below.
SUPPORTED_BOOTSTRAP_PYTHON_MIN = (3, 11)
SUPPORTED_BOOTSTRAP_PYTHON_MAX_EXCLUSIVE = (3, 15)
SUPPORTED_HERMES_RUNTIME_PYTHON_MIN = (3, 11)
SUPPORTED_HERMES_RUNTIME_PYTHON_MAX_EXCLUSIVE = (3, 15)
SUPPORTED_BOOTSTRAP_PYTHON_RANGE = (
    f">={SUPPORTED_BOOTSTRAP_PYTHON_MIN[0]}.{SUPPORTED_BOOTSTRAP_PYTHON_MIN[1]},"
    f"<{SUPPORTED_BOOTSTRAP_PYTHON_MAX_EXCLUSIVE[0]}.{SUPPORTED_BOOTSTRAP_PYTHON_MAX_EXCLUSIVE[1]}"
)
VERSION_FILE = DIANA_ROOT / "VERSION"
MANIFEST_NAME = "release-manifest.json"
MANAGER_SCHEMA = 2


def _default_trust_file() -> Path:
    return Path(os.environ.get(
        "DIANA_RUNTIME_TRUST_FILE",
        str(Path.home() / ".local" / "state" / "diana" / "runtime-trust.key"),
    )).expanduser()


def _manifest_payload(doc: dict) -> bytes:
    payload = {k: v for k, v in doc.items() if k != "auth"}
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_manifest_files(doc: dict) -> str | None:
    files = doc.get("files")
    if not isinstance(files, dict) or not files:
        return "manifest carries no runtime files"
    root = DIANA_ROOT.resolve()
    for rel, meta in files.items():
        if not isinstance(rel, str) or rel.startswith("/") or ".." in Path(rel).parts:
            return f"unsafe manifest path: {rel!r}"
        if not isinstance(meta, dict) or set(meta) != {"sha256", "size"}:
            return f"malformed file metadata: {rel}"
        path = DIANA_ROOT / rel
        if path.is_symlink() or not path.is_file():
            return f"required runtime file missing or symlinked: {rel}"
        try:
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError):
            return f"runtime file escapes installed release: {rel}"
        if path.stat().st_size != meta.get("size") or _sha256(path) != meta.get("sha256"):
            return f"runtime file integrity mismatch: {rel}"
    return None


def _authenticated_manifest() -> tuple[dict | None, str | None]:
    """Return an authenticated installed-release manifest, never an asserted one.

    The HMAC key lives outside the runtime prefix. This protects against an
    attacker limited to rewriting the installed runtime prefix; it does not
    claim protection from a same-user compromise that can also read the key.
    """
    manifest = DIANA_ROOT / MANIFEST_NAME
    if not manifest.exists():
        return None, None
    if manifest.is_symlink() or not manifest.is_file():
        return None, "manifest is not a regular file"
    try:
        doc = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"manifest unreadable: {exc}"
    if not isinstance(doc, dict) or doc.get("runtime_manager_schema") != MANAGER_SCHEMA:
        return None, "manifest schema mismatch"
    auth = doc.get("auth")
    if not isinstance(auth, dict) or set(auth) != {"type", "value"}:
        return None, "manifest authentication missing"
    if auth.get("type") != "hmac-sha256" or not isinstance(auth.get("value"), str):
        return None, "manifest authentication scheme unsupported"
    trust_file = _default_trust_file()
    try:
        if trust_file.is_symlink() or not trust_file.is_file():
            return None, "runtime trust key unavailable"
        st = trust_file.stat()
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            return None, "runtime trust key is not owned by the current user"
        if stat.S_IMODE(st.st_mode) & 0o077:
            return None, "runtime trust key permissions are not private"
        key = trust_file.read_bytes()
    except OSError as exc:
        return None, f"runtime trust key unreadable: {exc}"
    if len(key) != 32:
        return None, "runtime trust key length invalid"
    expected = hmac.new(key, _manifest_payload(doc), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(auth["value"], expected):
        return None, "manifest authentication mismatch"
    file_error = _verify_manifest_files(doc)
    if file_error:
        return None, file_error
    return doc, None


def diana_version() -> str:
    try:
        value = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return value or "unknown"


def source_commit() -> str | None:
    manifest_path = DIANA_ROOT / MANIFEST_NAME
    if manifest_path.exists() or manifest_path.is_symlink():
        data, _ = _authenticated_manifest()
        if data is None:
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


def source_tree_clean() -> bool | None:
    """Whether the Diana-managed source files are clean, or None if this is
    not a git checkout / git is unavailable (the same `None`-on-unknown
    contract as `source_commit()` -- doctor must not read an undetermined
    state as a false `True`).

    Mirrors `runtime_install._source_clean`'s scope (`diana`, `diana-do`,
    `VERSION` only -- not the whole tree, so unrelated dirty files such as
    docs-in-progress do not block a governed run) without importing that
    module, keeping this diagnostic path's only dependency on the stdlib
    `subprocess` call already used by `source_commit()` above.
    """
    if (DIANA_ROOT / MANIFEST_NAME).exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(DIANA_ROOT), "status", "--porcelain", "--",
             "diana", "diana-do", "VERSION", "runtime-install.sh"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return not proc.stdout.strip()


def release_identity() -> dict:
    """The Diana release identity a proposal's authority digest is bound to.

    Deliberately `{diana_version, source_commit}` only -- not the full
    `version_document()` (which also carries the certified-Hermes-identities
    list and supported-platform matrix). Binding to those too would make
    every OLD, already-approved proposal spuriously "stale" the moment this
    release certifies an additional Hermes identity or platform, even though
    neither changes what authority was actually granted. `diana_version`
    comes from the VERSION file directly (not git), matching
    `test-production-release.sh`'s own falsifier: rewriting VERSION on disk
    without a commit must already change this identity and therefore refuse
    reuse of a proposal digested under the old one.
    """
    return {"diana_version": diana_version(), "source_commit": source_commit()}


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


def _python_version(python: str) -> tuple[tuple[int, int] | None, str | None]:
    try:
        proc = subprocess.run(
            [python, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return None, detail or f"python version probe exited {proc.returncode}"
    try:
        major_s, minor_s = proc.stdout.strip().split(".", 1)
        return (int(major_s), int(minor_s)), None
    except (ValueError, AttributeError) as exc:
        return None, f"invalid python version output: {proc.stdout.strip()!r} ({exc})"


def _runtime_python_supported(version: tuple[int, int] | None) -> bool:
    return bool(
        version is not None
        and SUPPORTED_HERMES_RUNTIME_PYTHON_MIN <= version
        < SUPPORTED_HERMES_RUNTIME_PYTHON_MAX_EXCLUSIVE
    )


def _bootstrap_python_supported(version: tuple[int, int] | None) -> bool:
    return bool(
        version is not None
        and SUPPORTED_BOOTSTRAP_PYTHON_MIN <= version
        < SUPPORTED_BOOTSTRAP_PYTHON_MAX_EXCLUSIVE
    )


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
    """Raw, no-I/O classification only: "certified", "unlisted" or
    "unverifiable". Reuses `hermes_patches.identity_certified` instead of a
    second, drifting reimplementation of the same decision.
    """
    if not identity:
        return "unverifiable"
    import hermes_patches  # local Diana module, stdlib-only at import time

    return hermes_patches.identity_certified(identity)


def _run_compatibility_preflight(home: str, python: str) -> dict:
    """Run `compat_preflight.py` under the resolved Hermes-runtime Python.

    `release.py` runs under the BOOTSTRAP interpreter, never Hermes's own
    PM-managed one, so -- exactly like `_hermes_identity`/`_python_version`
    above -- the actual behavioral preflight has to be driven as a subprocess
    under the interpreter that can import Hermes's dependencies at all.
    """
    script = ADAPTERS / "compat_preflight.py"
    try:
        proc = subprocess.run(
            [python, str(script), home],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"result": "incompatible", "error": f"{type(exc).__name__}: {exc}"}
    try:
        report = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        report = None
    if not isinstance(report, dict) or report.get("result") not in ("compatible", "incompatible"):
        detail = (proc.stderr or proc.stdout).strip()[:400]
        return {
            "result": "incompatible",
            "error": detail or f"compatibility preflight exited {proc.returncode} with no parseable result",
        }
    return report


def _hermes_compatibility(identity: dict | None, home: str, python: str | None) -> dict:
    """The full identity decision: "certified", "compatible-unlisted",
    "incompatible" or "unverifiable". Only the "unlisted" raw classification
    triggers the bounded behavioral preflight -- a certified identity never
    pays that cost, and an unverifiable one cannot run it at all.
    """
    raw = _identity_state(identity)
    if raw != "unlisted":
        return {"state": raw, "preflight": None}
    if not python:
        return {
            "state": "incompatible",
            "preflight": {"result": "incompatible",
                          "error": "no resolved Hermes runtime Python to run the "
                                   "compatibility preflight under"},
        }
    report = _run_compatibility_preflight(home, python)
    state = "compatible-unlisted" if report.get("result") == "compatible" else "incompatible"
    return {"state": state, "preflight": report}


def version_document() -> dict:
    return {
        "release_schema_version": RELEASE_SCHEMA_VERSION,
        "diana_version": diana_version(),
        "source_commit": source_commit(),
        "supported_system": SUPPORTED_SYSTEM,
        # A RANGE string, not a single-version implication: 3.11 through 3.14
        # (exclusive of 3.15) are all supported bootstrap interpreters.
        "supported_bootstrap_python": SUPPORTED_BOOTSTRAP_PYTHON_RANGE,
        # Compatibility alias: historically this field meant the Python that
        # invokes release.py / diana-do diagnostics. It remains bootstrap-only.
        "supported_python": SUPPORTED_BOOTSTRAP_PYTHON_RANGE,
        "supported_hermes_runtime_python": ">=3.11,<3.15",
        "certified_hermes_identities": list(_certified_identities()),
    }


def doctor_document(hermes_home: str | None = None) -> tuple[dict, bool]:
    home = hermes_home or os.environ.get(
        "DIANA_HERMES_HOME", str(Path.home() / ".hermes" / "hermes-agent")
    )
    system = platform.system()
    bootstrap_py = (sys.version_info.major, sys.version_info.minor)
    hermes_python, runtime_error = _resolve_hermes_python(home)
    hermes_runtime_py = None
    hermes_runtime_py_error = None
    if hermes_python:
        hermes_runtime_py, hermes_runtime_py_error = _python_version(hermes_python)
    identity = None
    identity_error = None
    if hermes_python:
        identity, identity_error = _hermes_identity(hermes_python, home)
    compat = _hermes_compatibility(identity, home, hermes_python)
    identity_state = compat["state"]
    manifest_present = (DIANA_ROOT / MANIFEST_NAME).exists()
    authenticated_manifest, manifest_error = _authenticated_manifest()
    source_clean = source_tree_clean()
    source_authenticated = (
        authenticated_manifest is not None if manifest_present else source_clean is True
    )
    checks = {
        "platform_supported": system == SUPPORTED_SYSTEM,
        "bootstrap_python_supported": _bootstrap_python_supported(bootstrap_py),
        "hermes_runtime_python_supported": _runtime_python_supported(hermes_runtime_py),
        "diana_version_known": diana_version() != "unknown",
        "diana_source_known": source_commit() is not None,
        "diana_source_authenticated": source_authenticated,
        "hermes_home_present": Path(home).is_dir(),
        "hermes_runtime_resolved": hermes_python is not None,
        # Verifiable and compatibility-preflight are the two GATING facts.
        # "known certified" (hermes_identity_known, below) is informational
        # only -- an unlisted-but-proven-compatible identity must not refuse,
        # so it deliberately does not appear in `checks`.
        "hermes_identity_verifiable": identity_state != "unverifiable",
        "hermes_compatibility_preflight": identity_state in ("certified", "compatible-unlisted"),
    }
    safe = all(checks.values())
    return {
        "release_schema_version": RELEASE_SCHEMA_VERSION,
        "safe_to_start_governed_run": safe,
        "diana_version": diana_version(),
        "source_commit": source_commit(),
        "manifest_present": manifest_present,
        "manifest_authenticated": authenticated_manifest is not None if manifest_present else None,
        "manifest_error": manifest_error,
        "source_tree_clean": source_clean,
        "platform": {"actual": system, "supported": [SUPPORTED_SYSTEM]},
        "bootstrap_python": {
            "actual": f"{bootstrap_py[0]}.{bootstrap_py[1]}",
            "supported": SUPPORTED_BOOTSTRAP_PYTHON_RANGE,
        },
        # Backward-compatible alias for older consumers; explicitly bootstrap
        # Python, never the PM-managed Hermes execution interpreter.
        "python": {
            "actual": f"{bootstrap_py[0]}.{bootstrap_py[1]}",
            "supported": SUPPORTED_BOOTSTRAP_PYTHON_RANGE,
            "role": "bootstrap",
        },
        "hermes_home": home,
        "hermes_python": hermes_python,
        "hermes_runtime_python": {
            "actual": (
                f"{hermes_runtime_py[0]}.{hermes_runtime_py[1]}"
                if hermes_runtime_py is not None else None
            ),
            "supported": ">=3.11,<3.15",
            "error": hermes_runtime_py_error,
        },
        "hermes_runtime_error": runtime_error,
        "hermes_identity": identity,
        "hermes_identity_error": identity_error,
        # Machine-readable identity state: "certified", "compatible-unlisted",
        # "incompatible" or "unverifiable". Never overloads "certified" to
        # mean merely compatible.
        "hermes_identity_state": identity_state,
        "hermes_identity_known": identity_state == "certified",
        "hermes_compatibility_preflight_report": compat["preflight"],
        "checks": checks,
    }, safe


def print_human_doctor(doc: dict) -> None:
    for key, ok in doc["checks"].items():
        print(f"{'PASS' if ok else 'FAIL'}  {key}")
    # hermes_identity_known is deliberately NOT a gating check (see
    # doctor_document): an unlisted-but-proven-compatible identity must not
    # read as a failure, only as a WARN an operator can act on if they choose
    # to (e.g. by reviewing and adding it to CERTIFIED_IDENTITIES).
    identity = doc.get("hermes_identity") or {}
    if doc["hermes_identity_state"] == "compatible-unlisted":
        sha = identity.get("sha") or "unverifiable"
        print(f"WARN  hermes_identity_known            {sha} not in certified registry")
    print(f"INFO  diana_version={doc['diana_version']}")
    print(f"INFO  source_commit={doc['source_commit'] or 'unknown'}")
    print(f"INFO  platform={doc['platform']['actual']}")
    print(f"INFO  bootstrap_python={doc['bootstrap_python']['actual']}")
    print(f"INFO  hermes_home={doc['hermes_home']}")
    print(f"INFO  hermes_python={doc['hermes_python'] or 'unresolved'}")
    print(
        "INFO  hermes_runtime_python="
        f"{doc['hermes_runtime_python']['actual'] or 'unresolved'}"
    )
    print(f"INFO  hermes_identity_source={identity.get('source', 'unverifiable')}")
    print(f"INFO  hermes_identity_sha={identity.get('sha') or 'unverifiable'}")
    print(f"INFO  hermes_identity_state={doc['hermes_identity_state']}")
    if doc["hermes_identity_state"] == "incompatible":
        report = doc.get("hermes_compatibility_preflight_report") or {}
        failed = [c["check"] for c in report.get("checks", []) if not c.get("ok")]
        print(
            "INFO  hermes_compatibility_preflight_failed="
            f"{', '.join(failed) or report.get('error', 'unknown')}"
        )
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
