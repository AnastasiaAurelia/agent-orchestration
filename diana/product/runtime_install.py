#!/usr/bin/env python3
"""Install/upgrade/verify/rollback the governed Diana runtime.

This manager is separate from root install.sh, which remains the portable
project-facing Layer 2 installer.

Security properties:
- only a deterministic Diana-managed prefix and Diana-owned launcher are mutated;
- source runtime files must be clean and commit-identifiable before install;
- every installed release is content-hashed and the manifest is authenticated by
  a local trust key stored OUTSIDE the writable runtime prefix;
- current/previous activation uses atomic symlink replacement plus a transition
  marker so a crash cannot be mistaken for a completed upgrade/rollback;
- ambiguous or partially transitioned state fails closed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE_ROOT = HERE.parent.parent
ADAPTERS = SOURCE_ROOT / "diana" / "adapters"
if str(ADAPTERS) not in sys.path:
    sys.path.insert(0, str(ADAPTERS))

MANIFEST = "release-manifest.json"
TRANSITION = "transition.json"
MANAGER_SCHEMA = 2
ALLOWED_PREFIX_ENTRIES = {"releases", "current", "previous", ".staging", TRANSITION}


class ManagedRuntimeError(Exception):
    pass


def _version(root: Path = SOURCE_ROOT) -> str:
    try:
        value = (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ManagedRuntimeError(f"VERSION is unreadable: {exc}") from exc
    if (
        not value
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
        or any(ord(ch) < 32 for ch in value)
    ):
        raise ManagedRuntimeError(f"invalid VERSION value: {value!r}")
    return value


def _git_commit(root: Path = SOURCE_ROOT) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManagedRuntimeError(f"source git identity unavailable: {exc}") from exc
    value = proc.stdout.strip()
    if (
        proc.returncode != 0
        or len(value) != 40
        or any(ch not in "0123456789abcdefABCDEF" for ch in value)
    ):
        raise ManagedRuntimeError("source git identity unavailable or malformed")
    return value.lower()


def _source_clean(root: Path = SOURCE_ROOT) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--",
             "diana", "diana-do", "VERSION", "runtime-install.sh"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and not proc.stdout.strip()


def _certified_identities() -> list[dict]:
    import hermes_patches
    return [dict(x) for x in hermes_patches.CERTIFIED_IDENTITIES]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _runtime_files(root: Path) -> list[Path]:
    files = [root / "diana-do", root / "VERSION", root / "runtime-install.sh"]
    files += sorted(p for p in (root / "diana").rglob("*") if p.is_file())
    return files


def _manifest_payload(doc: dict) -> bytes:
    payload = {k: v for k, v in doc.items() if k != "auth"}
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sign_manifest(doc: dict, key: bytes) -> dict:
    signed = dict(doc)
    signed["auth"] = {
        "type": "hmac-sha256",
        "value": hmac.new(key, _manifest_payload(doc), hashlib.sha256).hexdigest(),
    }
    return signed


def _verify_manifest_auth(doc: dict, key: bytes) -> None:
    auth = doc.get("auth")
    if not isinstance(auth, dict) or set(auth) != {"type", "value"}:
        raise ManagedRuntimeError("release manifest authentication is absent or malformed")
    if auth.get("type") != "hmac-sha256" or not isinstance(auth.get("value"), str):
        raise ManagedRuntimeError("release manifest authentication scheme is unsupported")
    expected = hmac.new(key, _manifest_payload(doc), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(auth["value"], expected):
        raise ManagedRuntimeError("release manifest authentication mismatch")


def _manifest_for(
    staging: Path, *, version: str, source_commit: str, trust_key: bytes
) -> dict:
    files = {}
    for path in _runtime_files(staging):
        rel = path.relative_to(staging).as_posix()
        files[rel] = {"sha256": _sha256(path), "size": path.stat().st_size}
    doc = {
        "runtime_manager_schema": MANAGER_SCHEMA,
        "diana_version": version,
        "source_commit": source_commit,
        "supported_systems": ["Linux"],
        "supported_python": ["3.11"],
        "certified_hermes_identities": _certified_identities(),
        "files": files,
    }
    return _sign_manifest(doc, trust_key)


def _default_prefix() -> Path:
    return Path(os.environ.get(
        "DIANA_RUNTIME_PREFIX", str(Path.home() / ".local" / "share" / "diana")
    )).expanduser()


def _default_bin_dir() -> Path:
    return Path(os.environ.get(
        "DIANA_RUNTIME_BIN", str(Path.home() / ".local" / "bin")
    )).expanduser()


def _default_trust_file() -> Path:
    return Path(os.environ.get(
        "DIANA_RUNTIME_TRUST_FILE",
        str(Path.home() / ".local" / "state" / "diana" / "runtime-trust.key"),
    )).expanduser()


def _validate_prefix(prefix: Path) -> Path:
    raw = prefix.expanduser()
    if raw.exists() and raw.is_symlink():
        raise ManagedRuntimeError(f"refusing symlink runtime prefix {raw}")
    prefix = raw.resolve()
    if prefix == Path("/") or len(prefix.parts) < 3:
        raise ManagedRuntimeError(f"refusing unsafe runtime prefix {prefix}")
    return prefix


def _validate_bin_dir(bin_dir: Path) -> Path:
    raw = bin_dir.expanduser()
    if raw.exists() and raw.is_symlink():
        raise ManagedRuntimeError(f"refusing symlink runtime bin directory {raw}")
    return raw.resolve()


def _validate_trust_file(trust_file: Path, prefix: Path) -> Path:
    raw = trust_file.expanduser()
    if raw.is_symlink():
        raise ManagedRuntimeError(f"runtime trust key must not be a symlink: {raw}")
    # The trust anchor must not be forgeable by an actor limited to the runtime
    # prefix. This does not claim protection from a same-user compromise that can
    # also read/write the trust-key path.
    resolved = raw.resolve(strict=False)
    try:
        resolved.relative_to(prefix)
    except ValueError:
        pass
    else:
        raise ManagedRuntimeError("runtime trust key must live outside the runtime prefix")
    return resolved


def _load_trust_key(trust_file: Path, *, create: bool) -> bytes:
    if trust_file.exists() or trust_file.is_symlink():
        if trust_file.is_symlink() or not trust_file.is_file():
            raise ManagedRuntimeError(f"runtime trust key is not a regular file: {trust_file}")
        st = trust_file.stat()
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            raise ManagedRuntimeError("runtime trust key is not owned by the current user")
        if stat.S_IMODE(st.st_mode) & 0o077:
            raise ManagedRuntimeError("runtime trust key permissions must not grant group/other access")
        key = trust_file.read_bytes()
        if len(key) != 32:
            raise ManagedRuntimeError("runtime trust key has invalid length")
        return key
    if not create:
        raise ManagedRuntimeError(f"runtime trust key does not exist: {trust_file}")
    parent = trust_file.parent
    if parent.exists():
        if parent.is_symlink() or not parent.is_dir():
            raise ManagedRuntimeError(f"runtime trust-key parent is not a regular directory: {parent}")
    else:
        parent.mkdir(parents=True, mode=0o700)
    key = secrets.token_bytes(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(trust_file, flags, 0o600)
    try:
        os.write(fd, key)
        os.fsync(fd)
    finally:
        os.close(fd)
    return key


def _release_id(version: str, commit: str) -> str:
    return f"{version}+{commit[:12]}"


def _readlink_abs(path: Path) -> Path | None:
    if not path.is_symlink():
        return None
    target = Path(os.readlink(path))
    if not target.is_absolute():
        target = path.parent / target
    return target.resolve(strict=False)


def _atomic_symlink(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.parent / f".{link.name}.tmp-{os.getpid()}"
    try:
        tmp.unlink(missing_ok=True)
        tmp.symlink_to(target)
        os.replace(tmp, link)
    finally:
        tmp.unlink(missing_ok=True)


def _copy_source(staging: Path) -> None:
    shutil.copy2(SOURCE_ROOT / "diana-do", staging / "diana-do")
    os.chmod(staging / "diana-do", 0o755)
    shutil.copy2(SOURCE_ROOT / "VERSION", staging / "VERSION")
    shutil.copy2(SOURCE_ROOT / "runtime-install.sh", staging / "runtime-install.sh")
    os.chmod(staging / "runtime-install.sh", 0o755)
    shutil.copytree(SOURCE_ROOT / "diana", staging / "diana", symlinks=False)


def _verify_release(release: Path, trust_key: bytes, *, require_identity_name: bool = True) -> dict:
    if not release.is_dir() or release.is_symlink():
        raise ManagedRuntimeError(f"release is not a regular directory: {release}")
    manifest_path = release / MANIFEST
    if manifest_path.is_symlink():
        raise ManagedRuntimeError("release manifest must not be a symlink")
    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManagedRuntimeError(f"release manifest unreadable: {exc}") from exc
    required = {
        "runtime_manager_schema", "diana_version", "source_commit",
        "supported_systems", "supported_python",
        "certified_hermes_identities", "files", "auth",
    }
    if set(doc) != required or doc["runtime_manager_schema"] != MANAGER_SCHEMA:
        raise ManagedRuntimeError("release manifest schema mismatch")
    _verify_manifest_auth(doc, trust_key)
    files = doc["files"]
    if not isinstance(files, dict) or not files:
        raise ManagedRuntimeError("release manifest carries no files")
    commit = doc.get("source_commit")
    if (
        not isinstance(commit, str)
        or len(commit) != 40
        or any(ch not in "0123456789abcdef" for ch in commit)
    ):
        raise ManagedRuntimeError("release manifest source commit is malformed")
    expected_rid = _release_id(str(doc.get("diana_version")), commit)
    if require_identity_name and release.name != expected_rid:
        raise ManagedRuntimeError(
            f"release directory identity mismatch: {release.name} != {expected_rid}"
        )
    for rel, meta in files.items():
        if not isinstance(rel, str) or rel.startswith("/") or ".." in Path(rel).parts:
            raise ManagedRuntimeError(f"unsafe manifest path {rel!r}")
        if not isinstance(meta, dict) or set(meta) != {"sha256", "size"}:
            raise ManagedRuntimeError(f"malformed file metadata for {rel}")
        path = release / rel
        if not path.is_file() or path.is_symlink():
            raise ManagedRuntimeError(f"required runtime file missing or symlinked: {rel}")
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(release.resolve())
        except (OSError, ValueError):
            raise ManagedRuntimeError(f"runtime file escapes release directory: {rel}") from None
        if path.stat().st_size != meta.get("size") or _sha256(path) != meta.get("sha256"):
            raise ManagedRuntimeError(f"runtime file integrity mismatch: {rel}")
    return doc


def _transition_path(prefix: Path) -> Path:
    return prefix / TRANSITION


def _assert_no_transition(prefix: Path) -> None:
    marker = _transition_path(prefix)
    if marker.exists() or marker.is_symlink():
        raise ManagedRuntimeError(
            f"incomplete runtime transition detected at {marker}; refuse ambiguous state"
        )


def _write_transition(prefix: Path, document: dict) -> None:
    marker = _transition_path(prefix)
    if marker.exists() or marker.is_symlink():
        raise ManagedRuntimeError("another runtime transition is already recorded")
    tmp = prefix / f".{TRANSITION}.tmp-{os.getpid()}"
    tmp.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, marker)


def _clear_transition(prefix: Path) -> None:
    _transition_path(prefix).unlink(missing_ok=True)


def _ensure_launcher(bin_dir: Path, prefix: Path) -> None:
    launcher = bin_dir / "diana-do"
    expected = prefix / "current" / "diana-do"
    if launcher.exists() or launcher.is_symlink():
        target = _readlink_abs(launcher)
        if target is None or target != expected.resolve(strict=False):
            raise ManagedRuntimeError(
                f"refusing to overwrite unmanaged launcher {launcher}"
            )
    _atomic_symlink(expected, launcher)


def _verify_active(
    prefix: Path, bin_dir: Path, trust_key: bytes, *, allow_transition: bool
) -> dict:
    if not allow_transition:
        _assert_no_transition(prefix)
    current = prefix / "current"
    if not current.is_symlink():
        raise ManagedRuntimeError("no managed current runtime")
    release = _readlink_abs(current)
    if release is None or release.parent != (prefix / "releases").resolve():
        raise ManagedRuntimeError("current link escapes the managed releases directory")
    doc = _verify_release(release, trust_key)
    launcher = bin_dir / "diana-do"
    expected = (prefix / "current" / "diana-do").resolve(strict=False)
    if _readlink_abs(launcher) != expected:
        raise ManagedRuntimeError("launcher is absent or not Diana-managed")
    return doc


def install(
    prefix: Path, bin_dir: Path, trust_file: Path, *, require_existing: bool
) -> dict:
    prefix = _validate_prefix(prefix)
    bin_dir = _validate_bin_dir(bin_dir)
    trust_file = _validate_trust_file(trust_file, prefix)
    prefix.mkdir(parents=True, exist_ok=True)
    _assert_no_transition(prefix)

    staging_parent = prefix / ".staging"
    if staging_parent.exists() and any(staging_parent.iterdir()):
        raise ManagedRuntimeError(
            f"stale partial staging state exists at {staging_parent}; refuse ambiguous upgrade"
        )

    releases = prefix / "releases"
    current = prefix / "current"
    previous = prefix / "previous"
    if require_existing and not current.is_symlink():
        raise ManagedRuntimeError("upgrade requested but no managed current runtime exists")
    if not require_existing and current.exists() and not current.is_symlink():
        raise ManagedRuntimeError("current runtime path exists but is not a managed symlink")

    version = _version()
    commit = _git_commit()
    if not _source_clean():
        raise ManagedRuntimeError(
            "runtime-bearing source files are dirty; commit/review the exact source before installation"
        )
    trust_key = _load_trust_key(trust_file, create=True)
    rid = _release_id(version, commit)
    destination = releases / rid

    if destination.exists():
        _verify_release(destination, trust_key)
    else:
        releases.mkdir(parents=True, exist_ok=True)
        staging_parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f"{rid}-", dir=staging_parent))
        try:
            _copy_source(staging)
            manifest = _manifest_for(
                staging, version=version, source_commit=commit, trust_key=trust_key
            )
            (staging / MANIFEST).write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.chmod(staging / MANIFEST, 0o600)
            _verify_release(staging, trust_key, require_identity_name=False)
            os.replace(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
            if staging_parent.exists() and not any(staging_parent.iterdir()):
                staging_parent.rmdir()

    old = _readlink_abs(current) if current.is_symlink() else None
    if require_existing and old == destination.resolve():
        raise ManagedRuntimeError("incoming runtime is already active")

    transition = {
        "operation": "upgrade" if require_existing else "install",
        "old_current": str(old) if old else None,
        "new_current": str(destination.resolve()),
        "old_previous": str(_readlink_abs(previous)) if previous.is_symlink() else None,
    }
    _write_transition(prefix, transition)
    try:
        if old is not None:
            _atomic_symlink(old, previous)
        _atomic_symlink(destination, current)
        _ensure_launcher(bin_dir, prefix)
        doc = _verify_active(prefix, bin_dir, trust_key, allow_transition=True)
    except Exception:
        if old is not None:
            _atomic_symlink(old, current)
        elif current.is_symlink():
            current.unlink()
        # Keep the marker if restoration itself cannot be proven; otherwise
        # remove it so a handled failure does not brick a known-good install.
        try:
            if old is not None:
                _verify_release(old, trust_key)
            _clear_transition(prefix)
        except Exception:
            pass
        raise
    _clear_transition(prefix)
    return doc


def verify(prefix: Path, bin_dir: Path, trust_file: Path) -> dict:
    prefix = _validate_prefix(prefix)
    bin_dir = _validate_bin_dir(bin_dir)
    trust_file = _validate_trust_file(trust_file, prefix)
    trust_key = _load_trust_key(trust_file, create=False)
    return _verify_active(prefix, bin_dir, trust_key, allow_transition=False)


def rollback(prefix: Path, bin_dir: Path, trust_file: Path) -> dict:
    prefix = _validate_prefix(prefix)
    bin_dir = _validate_bin_dir(bin_dir)
    trust_file = _validate_trust_file(trust_file, prefix)
    _assert_no_transition(prefix)
    trust_key = _load_trust_key(trust_file, create=False)

    current = prefix / "current"
    previous = prefix / "previous"
    cur = _readlink_abs(current)
    prev = _readlink_abs(previous)
    if cur is None or prev is None:
        raise ManagedRuntimeError("rollback requires verified current and previous releases")
    releases = (prefix / "releases").resolve()
    if cur.parent != releases or prev.parent != releases:
        raise ManagedRuntimeError("rollback link escapes managed releases directory")
    _verify_release(cur, trust_key)
    _verify_release(prev, trust_key)

    _write_transition(prefix, {
        "operation": "rollback",
        "old_current": str(cur),
        "new_current": str(prev),
        "old_previous": str(prev),
        "new_previous": str(cur),
    })
    try:
        _atomic_symlink(prev, current)
        _atomic_symlink(cur, previous)
        _ensure_launcher(bin_dir, prefix)
        doc = _verify_active(prefix, bin_dir, trust_key, allow_transition=True)
    except Exception:
        # Restore the pre-rollback pair when the exception is catchable. A hard
        # crash leaves TRANSITION behind and every later command refuses.
        try:
            _atomic_symlink(cur, current)
            _atomic_symlink(prev, previous)
            _verify_active(prefix, bin_dir, trust_key, allow_transition=True)
            _clear_transition(prefix)
        except Exception:
            pass
        raise
    _clear_transition(prefix)
    return doc


def uninstall(prefix: Path, bin_dir: Path, trust_file: Path) -> None:
    prefix = _validate_prefix(prefix)
    bin_dir = _validate_bin_dir(bin_dir)
    trust_file = _validate_trust_file(trust_file, prefix)

    launcher = bin_dir / "diana-do"
    expected = (prefix / "current" / "diana-do").resolve(strict=False)
    if launcher.exists() or launcher.is_symlink():
        if _readlink_abs(launcher) != expected:
            raise ManagedRuntimeError(f"refusing to remove unmanaged launcher {launcher}")

    if not prefix.exists():
        if launcher.exists() or launcher.is_symlink():
            launcher.unlink()
        return

    _assert_no_transition(prefix)
    trust_key = _load_trust_key(trust_file, create=False)
    unexpected = {p.name for p in prefix.iterdir()} - ALLOWED_PREFIX_ENTRIES
    if unexpected:
        raise ManagedRuntimeError(
            f"refusing to remove prefix containing unmanaged entries: {sorted(unexpected)}"
        )
    staging = prefix / ".staging"
    if staging.exists() and any(staging.iterdir()):
        raise ManagedRuntimeError("refusing uninstall with partial staging state")
    releases = prefix / "releases"
    if releases.exists():
        for release in releases.iterdir():
            _verify_release(release, trust_key)

    if launcher.exists() or launcher.is_symlink():
        launcher.unlink()
    for link in (prefix / "current", prefix / "previous"):
        if link.exists() or link.is_symlink():
            if not link.is_symlink():
                raise ManagedRuntimeError(f"unmanaged runtime entry: {link}")
            link.unlink()
    if staging.exists():
        staging.rmdir()
    if releases.exists():
        shutil.rmtree(releases)
    prefix.rmdir()
    # Deliberately retain the trust key. It is outside the runtime prefix and
    # may authenticate another managed prefix; deleting it here would be an
    # authority-expanding side effect outside uninstall's managed scope.


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="runtime-install")
    parser.add_argument(
        "command", choices=("install", "upgrade", "verify", "rollback", "uninstall")
    )
    parser.add_argument("--prefix", type=Path, default=_default_prefix())
    parser.add_argument("--bin-dir", type=Path, default=_default_bin_dir())
    parser.add_argument("--trust-file", type=Path, default=_default_trust_file())
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            doc = install(args.prefix, args.bin_dir, args.trust_file, require_existing=False)
        elif args.command == "upgrade":
            doc = install(args.prefix, args.bin_dir, args.trust_file, require_existing=True)
        elif args.command == "verify":
            doc = verify(args.prefix, args.bin_dir, args.trust_file)
        elif args.command == "rollback":
            doc = rollback(args.prefix, args.bin_dir, args.trust_file)
        else:
            uninstall(args.prefix, args.bin_dir, args.trust_file)
            print("UNINSTALLED")
            return 0
    except ManagedRuntimeError as exc:
        print(f"REFUSED  {exc}", file=sys.stderr)
        return 3
    print(json.dumps(doc, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
