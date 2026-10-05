#!/usr/bin/env python3
"""Install/upgrade/verify/rollback the governed Diana runtime.

This manager is separate from root install.sh, which remains the portable
project-facing Layer 2 installer.

The manager is stdlib-only and modifies only a deterministic Diana-managed
prefix plus one Diana-owned launcher symlink.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
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
MANAGER_SCHEMA = 1
ALLOWED_PREFIX_ENTRIES = {"releases", "current", "previous"}


class ManagedRuntimeError(Exception):
    pass


def _version(root: Path = SOURCE_ROOT) -> str:
    try:
        value = (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ManagedRuntimeError(f"VERSION is unreadable: {exc}") from exc
    if not value or "/" in value or value in {".", ".."}:
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
    if proc.returncode != 0 or len(value) != 40:
        raise ManagedRuntimeError("source git identity unavailable or malformed")
    return value


def _source_clean(root: Path = SOURCE_ROOT) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--",
             "diana", "diana-do", "VERSION"],
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
    files = [root / "diana-do", root / "VERSION"]
    files += sorted(p for p in (root / "diana").rglob("*") if p.is_file())
    return files


def _manifest_for(staging: Path, *, version: str, source_commit: str) -> dict:
    files = {}
    for path in _runtime_files(staging):
        rel = path.relative_to(staging).as_posix()
        files[rel] = {"sha256": _sha256(path), "size": path.stat().st_size}
    return {
        "runtime_manager_schema": MANAGER_SCHEMA,
        "diana_version": version,
        "source_commit": source_commit,
        "supported_systems": ["Linux"],
        "supported_python": ["3.11"],
        "certified_hermes_identities": _certified_identities(),
        "files": files,
    }


def _default_prefix() -> Path:
    return Path(os.environ.get(
        "DIANA_RUNTIME_PREFIX", str(Path.home() / ".local" / "share" / "diana")
    )).expanduser()


def _default_bin_dir() -> Path:
    return Path(os.environ.get(
        "DIANA_RUNTIME_BIN", str(Path.home() / ".local" / "bin")
    )).expanduser()


def _validate_prefix(prefix: Path) -> Path:
    prefix = prefix.resolve()
    if prefix == Path("/") or len(prefix.parts) < 3:
        raise ManagedRuntimeError(f"refusing unsafe runtime prefix {prefix}")
    return prefix


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
    shutil.copytree(SOURCE_ROOT / "diana", staging / "diana", symlinks=False)


def _verify_release(release: Path) -> dict:
    manifest_path = release / MANIFEST
    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManagedRuntimeError(f"release manifest unreadable: {exc}") from exc
    required = {
        "runtime_manager_schema", "diana_version", "source_commit",
        "supported_systems", "supported_python",
        "certified_hermes_identities", "files",
    }
    if set(doc) != required or doc["runtime_manager_schema"] != MANAGER_SCHEMA:
        raise ManagedRuntimeError("release manifest schema mismatch")
    files = doc["files"]
    if not isinstance(files, dict) or not files:
        raise ManagedRuntimeError("release manifest carries no files")
    for rel, meta in files.items():
        if not isinstance(rel, str) or rel.startswith("/") or ".." in Path(rel).parts:
            raise ManagedRuntimeError(f"unsafe manifest path {rel!r}")
        path = release / rel
        if not path.is_file() or path.is_symlink():
            raise ManagedRuntimeError(f"required runtime file missing or symlinked: {rel}")
        if path.stat().st_size != meta.get("size") or _sha256(path) != meta.get("sha256"):
            raise ManagedRuntimeError(f"runtime file integrity mismatch: {rel}")
    return doc


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


def install(prefix: Path, bin_dir: Path, *, require_existing: bool) -> dict:
    prefix = _validate_prefix(prefix)
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
    rid = _release_id(version, commit)
    destination = releases / rid
    if destination.exists():
        _verify_release(destination)
    else:
        releases.mkdir(parents=True, exist_ok=True)
        staging_parent = prefix / ".staging"
        staging_parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f"{rid}-", dir=staging_parent))
        try:
            _copy_source(staging)
            manifest = _manifest_for(staging, version=version, source_commit=commit)
            (staging / MANIFEST).write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _verify_release(staging)
            os.replace(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
            if staging_parent.exists() and not any(staging_parent.iterdir()):
                staging_parent.rmdir()

    old = _readlink_abs(current) if current.is_symlink() else None
    if require_existing and old == destination.resolve():
        raise ManagedRuntimeError("incoming runtime is already active")
    if old is not None:
        _atomic_symlink(old, previous)
    _atomic_symlink(destination, current)
    try:
        _ensure_launcher(bin_dir, prefix)
        doc = verify(prefix, bin_dir)
    except Exception:
        if old is not None:
            _atomic_symlink(old, current)
        raise
    return doc


def verify(prefix: Path, bin_dir: Path) -> dict:
    prefix = _validate_prefix(prefix)
    current = prefix / "current"
    if not current.is_symlink():
        raise ManagedRuntimeError("no managed current runtime")
    release = _readlink_abs(current)
    if release is None or release.parent != (prefix / "releases").resolve():
        raise ManagedRuntimeError("current link escapes the managed releases directory")
    doc = _verify_release(release)
    launcher = bin_dir / "diana-do"
    expected = (prefix / "current" / "diana-do").resolve(strict=False)
    if _readlink_abs(launcher) != expected:
        raise ManagedRuntimeError("launcher is absent or not Diana-managed")
    return doc


def rollback(prefix: Path, bin_dir: Path) -> dict:
    prefix = _validate_prefix(prefix)
    current = prefix / "current"
    previous = prefix / "previous"
    cur = _readlink_abs(current)
    prev = _readlink_abs(previous)
    if cur is None or prev is None:
        raise ManagedRuntimeError("rollback requires verified current and previous releases")
    releases = (prefix / "releases").resolve()
    if cur.parent != releases or prev.parent != releases:
        raise ManagedRuntimeError("rollback link escapes managed releases directory")
    _verify_release(cur)
    _verify_release(prev)
    _atomic_symlink(prev, current)
    _atomic_symlink(cur, previous)
    _ensure_launcher(bin_dir, prefix)
    return verify(prefix, bin_dir)


def uninstall(prefix: Path, bin_dir: Path) -> None:
    prefix = _validate_prefix(prefix)
    launcher = bin_dir / "diana-do"
    expected = (prefix / "current" / "diana-do").resolve(strict=False)
    if launcher.exists() or launcher.is_symlink():
        if _readlink_abs(launcher) != expected:
            raise ManagedRuntimeError(f"refusing to remove unmanaged launcher {launcher}")
        launcher.unlink()

    if not prefix.exists():
        return
    unexpected = {p.name for p in prefix.iterdir()} - ALLOWED_PREFIX_ENTRIES
    if unexpected:
        raise ManagedRuntimeError(
            f"refusing to remove prefix containing unmanaged entries: {sorted(unexpected)}"
        )
    releases = prefix / "releases"
    if releases.exists():
        for release in releases.iterdir():
            if not release.is_dir() or release.is_symlink():
                raise ManagedRuntimeError(f"unmanaged release entry: {release}")
            _verify_release(release)
    for link in (prefix / "current", prefix / "previous"):
        if link.exists() or link.is_symlink():
            if not link.is_symlink():
                raise ManagedRuntimeError(f"unmanaged runtime entry: {link}")
            link.unlink()
    if releases.exists():
        shutil.rmtree(releases)
    prefix.rmdir()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="runtime-install")
    parser.add_argument("command", choices=("install", "upgrade", "verify", "rollback", "uninstall"))
    parser.add_argument("--prefix", type=Path, default=_default_prefix())
    parser.add_argument("--bin-dir", type=Path, default=_default_bin_dir())
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            doc = install(args.prefix, args.bin_dir, require_existing=False)
        elif args.command == "upgrade":
            doc = install(args.prefix, args.bin_dir, require_existing=True)
        elif args.command == "verify":
            doc = verify(args.prefix, args.bin_dir)
        elif args.command == "rollback":
            doc = rollback(args.prefix, args.bin_dir)
        else:
            uninstall(args.prefix, args.bin_dir)
            print("UNINSTALLED")
            return 0
    except ManagedRuntimeError as exc:
        print(f"REFUSED  {exc}", file=sys.stderr)
        return 3
    print(json.dumps(doc, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
