#!/usr/bin/env python3
"""Deterministic, applicability-aware Diana Preflight v0.

Scans a repository directory for a small catalog of high-value quality/safety
conditions and emits one JSON result per check. Makes no LLM calls and no
network calls. Preflight *detects* conditions; it does not decide whether a
change is allowed to merge — that remains Diana Gate's job (see
diana/gate/diana-gate.py and diana/preflight/README.md).

Output schema (stdout, one JSON object):

    {
      "version": 1,
      "checks": [
        {
          "id": str,
          "category": str,
          "severity": "BLOCKER" | "WARNING",
          "check_type": "DETERMINISTIC",
          "evidence_required": bool,
          "auto_fixable": bool,
          "applicable": bool,
          "result": "PASS" | "FAIL" | "SKIP",
          "detail": [str, ...]
        },
        ...
      ]
    }

Exit codes:

- 0: the scan ran to completion. Individual checks may have FAILed — that is
  data in the result, not a tool failure.
- 1: the tool itself could not run (bad usage, missing/unreadable repo path,
  unexpected internal error). Fail closed: no findings are emitted, only an
  "error" field, so a caller cannot mistake "could not scan" for "scanned
  clean."
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

IGNORED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".next",
    "dist", "build", ".turbo", ".cache", "coverage",
}
MAX_FILE_BYTES = 300_000

FRONTEND_FRAMEWORK_DEPS = {
    "react", "next", "vue", "nuxt", "svelte", "vite", "gatsby",
    "@angular/core", "solid-js", "astro",
}
FRONTEND_SOURCE_EXTS = {".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".html"}
TEST_DIR_NAMES = {"test", "tests", "__tests__", "__mocks__"}
TEST_FILENAME_RE = re.compile(r"^(test_.+|.+_test)\.py$")
# Genuine shell test-runner filename conventions only: test-*.sh, test_*.sh,
# *-test.sh, *_test.sh. A hyphen/underscore separator is required next to
# "test" so lookalikes that merely contain the substring (contest.sh,
# latest.sh, testdata.sh, specification.sh) do not qualify.
SHELL_TEST_RUNNER_RE = re.compile(r"^(test[-_].+|.+[-_]test)\.sh$")

SAFE_FILENAME_SUFFIXES = (
    ".example", ".sample", ".template", ".dist", ".test",
)
SUSPICIOUS_FILENAME_PATTERNS = [
    re.compile(r"^\.env(\..+)?$"),
    re.compile(r".*\.pem$"),
    re.compile(r".*\.key$"),
    re.compile(r"^id_(rsa|dsa|ecdsa|ed25519)$"),
    re.compile(r".*service[-_]?account.*\.json$"),
    re.compile(r"^credentials\.(json|ya?ml)$"),
    re.compile(r"^secrets\.(json|ya?ml)$"),
]

CLIENT_SECRET_ENV_RE = re.compile(
    r"\b(NEXT_PUBLIC_|VITE_|REACT_APP_|PUBLIC_|GATSBY_)[A-Z0-9_]*"
    r"(SECRET|PRIVATE|TOKEN|PASSWORD|APIKEY|API_KEY)[A-Z0-9_]*\b"
)
HARDCODED_SECRET_RES = [
    re.compile(r"sk_live_[A-Za-z0-9]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN(?: RSA)? PRIVATE KEY-----"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
]

LOCALHOST_STAGING_RES = [
    re.compile(r"https?://localhost(:\d+)?"),
    re.compile(r"https?://127\.0\.0\.1(:\d+)?"),
    re.compile(r"https?://[a-z0-9.-]*staging[a-z0-9.-]*\.\w+"),
]

STRIPE_DEP_NAMES = {"stripe"}

# Web-delivered images/fonts/video committed directly into the repo (not
# inside an ignored build/vendor dir - see IGNORED_DIRS) above this size
# generally have not been optimized for delivery. 1,000,000 bytes (~1 MB) is
# a deliberately conservative threshold: common web-performance guidance
# (e.g. web.dev's advice that a compressed hero image typically targets well
# under a few hundred KB) treats a single raw asset at or above 1 MB as
# needing compression/optimization before shipping, regardless of format.
# This is a quality/performance signal, not a security one - see its
# WARNING severity in CATALOG.
STATIC_ASSET_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif",
    ".mp4", ".mov", ".webm",
    ".woff", ".woff2", ".ttf", ".otf",
}
MAX_ASSET_BYTES = 1_000_000

OS_CRUFT_FILENAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}

NPM_LOCKFILE_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml"}

NEXT_APP_NOT_FOUND_PATHS = {
    "app/not-found.tsx", "app/not-found.jsx", "app/not-found.ts", "app/not-found.js",
}
NEXT_PAGES_404_PATHS = {
    "pages/404.tsx", "pages/404.jsx", "pages/404.ts", "pages/404.js",
}
STATIC_404_PATHS = {"public/404.html", "404.html"}


class InvalidInput(ValueError):
    pass


@dataclass
class Ctx:
    root: Path
    files: list[str]                      # repo-relative POSIX paths
    text_cache: dict[str, Optional[str]] = field(default_factory=dict)
    package_json_raw: Optional[str] = None
    package_json: Optional[dict] = None
    package_json_error: Optional[str] = None

    def text(self, rel: str) -> Optional[str]:
        if rel in self.text_cache:
            return self.text_cache[rel]
        path = self.root / rel
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                value = None
            else:
                value = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            value = None
        self.text_cache[rel] = value
        return value

    def files_with_ext(self, exts: set[str]) -> list[str]:
        return [f for f in self.files if Path(f).suffix in exts]

    def size(self, rel: str) -> Optional[int]:
        try:
            return (self.root / rel).stat().st_size
        except OSError:
            return None

    def top_level_dirs(self) -> set[str]:
        return {Path(f).parts[0] for f in self.files if len(Path(f).parts) > 1}

    def is_test_path(self, rel: str) -> bool:
        path = Path(rel)
        dir_parts = path.parts[:-1]
        if any(part in TEST_DIR_NAMES for part in dir_parts):
            return True
        name = path.name
        # *.test.* / *.spec.* (e.g. config.test.ts, config.spec.tsx) — the
        # marker must be its own dot-separated segment, not a substring of
        # an ordinary word (e.g. "specification.ts" or "contest/config.ts"
        # must NOT match).
        segments = name.split(".")
        if len(segments) >= 3 and segments[-2] in {"test", "spec"}:
            return True
        if TEST_FILENAME_RE.match(name):
            return True
        return False

    def is_shell_test_runner(self, rel: str) -> bool:
        if not SHELL_TEST_RUNNER_RE.match(Path(rel).name):
            return False
        text = self.text(rel) or ""
        first_line = text.splitlines()[0] if text else ""
        return _shebang_is_shell(first_line)


def _shebang_is_shell(first_line: str) -> bool:
    """True if a #! line's actual interpreter is a shell (bash, sh, dash,
    zsh, ksh, ...), including the common `#!/usr/bin/env bash` form. Checked
    by interpreter basename rather than a `sh` substring/word-boundary regex,
    since "bash" has no word boundary before its trailing "sh"."""
    if not first_line.startswith("#!"):
        return False
    tokens = first_line[2:].split()
    if not tokens:
        return False
    interpreter = Path(tokens[0]).name
    if interpreter == "env" and len(tokens) > 1:
        interpreter = Path(tokens[1]).name
    return interpreter.endswith("sh")


def build_ctx(root: Path) -> Ctx:
    files: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in IGNORED_DIRS for part in rel_parts):
            continue
        files.append(path.relative_to(root).as_posix())
    ctx = Ctx(root=root, files=files)
    if "package.json" in files:
        raw = ctx.text("package.json")
        ctx.package_json_raw = raw
        if raw is not None:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    ctx.package_json = parsed
                else:
                    ctx.package_json_error = "package.json top level is not an object"
            except json.JSONDecodeError as exc:
                ctx.package_json_error = f"package.json is not valid JSON: {exc}"
    return ctx


def is_frontend(ctx: Ctx) -> bool:
    if ctx.package_json is not None:
        deps = {}
        deps.update(ctx.package_json.get("dependencies") or {})
        deps.update(ctx.package_json.get("devDependencies") or {})
        if FRONTEND_FRAMEWORK_DEPS & set(deps):
            return True
    for candidate in ("index.html", "public/index.html", "src/index.html", "app/index.html"):
        if candidate in ctx.files:
            return True
    return False


def stripe_detected(ctx: Ctx) -> bool:
    if ctx.package_json is not None:
        deps = {}
        deps.update(ctx.package_json.get("dependencies") or {})
        deps.update(ctx.package_json.get("devDependencies") or {})
        if STRIPE_DEP_NAMES & set(deps):
            return True
    for rel in ("requirements.txt", "pyproject.toml"):
        if rel in ctx.files:
            text = ctx.text(rel) or ""
            if "stripe" in text.lower():
                return True
    return False


def convex_detected(ctx: Ctx) -> bool:
    return any(Path(f).parts[0] == "convex" for f in ctx.files)


def uses_next(ctx: Ctx) -> bool:
    if ctx.package_json is None:
        return False
    deps = {}
    deps.update(ctx.package_json.get("dependencies") or {})
    deps.update(ctx.package_json.get("devDependencies") or {})
    return "next" in deps


def is_routed_web_app(ctx: Ctx) -> bool:
    """A multi-page/routed site, where a custom 404 convention actually
    applies - narrower than is_frontend so a plain single-file demo or a
    component library (which has no routing at all) correctly SKIPs. Only
    two conventions are recognized deliberately, to keep false positives
    low: a Next.js app with a pages/ or app/ router directory, or a classic
    public/index.html-rooted static site (CRA/Vite-style), which is the
    layout convention most static hosts' own 404.html applies to."""
    if not is_frontend(ctx):
        return False
    if uses_next(ctx) and ({"pages", "app"} & ctx.top_level_dirs()):
        return True
    return "public/index.html" in ctx.files


def has_static_binary_assets(ctx: Ctx) -> bool:
    return bool(ctx.files_with_ext(STATIC_ASSET_EXTS))


def has_dependency_manifest(ctx: Ctx) -> bool:
    """npm/yarn/pnpm (any package.json, even unparseable - the lockfile
    question doesn't depend on package.json's content) or genuine Poetry
    usage (a [tool.poetry] table, not just any pyproject.toml - many
    non-Poetry Python projects use pyproject.toml only for pytest/build
    config, like this repository's own backend-only fixture)."""
    if "package.json" in ctx.files:
        return True
    if "pyproject.toml" in ctx.files:
        return "[tool.poetry]" in (ctx.text("pyproject.toml") or "")
    return False


def has_test_evidence(ctx: Ctx) -> tuple[bool, list[str]]:
    detail: list[str] = []
    if ctx.package_json_error is not None:
        return False, [ctx.package_json_error, "cannot confirm package.json test script"]
    if ctx.package_json is not None:
        test_script = (ctx.package_json.get("scripts") or {}).get("test")
        if isinstance(test_script, str) and test_script.strip() and "no test specified" not in test_script.lower():
            return True, [f'package.json scripts.test = "{test_script}"']
    if "pyproject.toml" in ctx.files:
        text = ctx.text("pyproject.toml") or ""
        if "pytest" in text.lower():
            return True, ["pyproject.toml declares pytest configuration"]
    if any(Path(f).name.startswith("test_") or Path(f).name.endswith("_test.py") for f in ctx.files):
        return True, ["Python test files found under the repository"]
    if "Makefile" in ctx.files:
        text = ctx.text("Makefile") or ""
        if re.search(r"^test:", text, re.MULTILINE):
            return True, ["Makefile declares a test target"]
    for f in ctx.files:
        if f.startswith(".github/workflows/"):
            text = ctx.text(f) or ""
            if re.search(r"\b(pytest|npm test|npm run test|go test|cargo test|yarn test|pnpm test)\b", text):
                return True, [f"{f} runs a recognized test command"]
    shell_test_runners = sorted(f for f in ctx.files if ctx.is_shell_test_runner(f))
    if shell_test_runners:
        return True, [f"shell test runner found: {f}" for f in shell_test_runners]
    return False, detail or ["no recognized build/test entrypoint found"]


def check_exposed_secret_files(ctx: Ctx) -> tuple[str, list[str]]:
    hits = []
    for rel in ctx.files:
        name = Path(rel).name
        if any(name.endswith(suffix) for suffix in SAFE_FILENAME_SUFFIXES):
            continue
        if any(pattern.match(name) for pattern in SUSPICIOUS_FILENAME_PATTERNS):
            hits.append(rel)
    if hits:
        return "FAIL", [f"suspicious secret/config file committed: {rel}" for rel in hits]
    return "PASS", ["no suspicious secret/config filenames found"]


def check_frontend_secret_leakage(ctx: Ctx) -> tuple[str, list[str]]:
    hits = []
    for rel in ctx.files_with_ext(FRONTEND_SOURCE_EXTS):
        if ctx.is_test_path(rel):
            continue
        text = ctx.text(rel)
        if not text:
            continue
        if CLIENT_SECRET_ENV_RE.search(text):
            hits.append(f"{rel}: client-exposed env var name looks like a secret")
            continue
        for pattern in HARDCODED_SECRET_RES:
            if pattern.search(text):
                hits.append(f"{rel}: hardcoded secret-shaped literal found")
                break
    if hits:
        return "FAIL", hits
    return "PASS", ["no client-exposed secret patterns found in frontend source"]


def check_localhost_staging_residue(ctx: Ctx) -> tuple[str, list[str]]:
    hits = []
    for rel in ctx.files_with_ext(FRONTEND_SOURCE_EXTS | {".env", ".json", ".yaml", ".yml"}):
        if ctx.is_test_path(rel):
            continue
        text = ctx.text(rel)
        if not text:
            continue
        for pattern in LOCALHOST_STAGING_RES:
            if pattern.search(text):
                hits.append(f"{rel}: hardcoded localhost/staging URL residue")
                break
    if hits:
        return "FAIL", hits
    return "PASS", ["no hardcoded localhost/staging URL residue found"]


def check_accidental_noindex(ctx: Ctx) -> tuple[str, list[str]]:
    hits = []
    for rel in ctx.files:
        if ctx.is_test_path(rel):
            continue
        ext = Path(rel).suffix
        name = Path(rel).name
        if ext not in {".html", ".ts", ".tsx", ".js", ".jsx"} and name != "robots.txt":
            continue
        text = ctx.text(rel)
        if not text:
            continue
        if name == "robots.txt":
            if re.search(r"^\s*Disallow:\s*/\s*$", text, re.MULTILINE) and not re.search(
                r"^\s*Allow:", text, re.MULTILINE
            ):
                hits.append(f"{rel}: robots.txt disallows the entire site")
            continue
        if "noindex" in text.lower():
            hits.append(f"{rel}: contains 'noindex'")
    if hits:
        return "FAIL", hits
    return "PASS", ["no accidental noindex/robots blockage found"]


def check_build_test_evidence(ctx: Ctx) -> tuple[str, list[str]]:
    ok, detail = has_test_evidence(ctx)
    return ("PASS" if ok else "FAIL"), detail


def check_stripe_webhook_verification(ctx: Ctx) -> tuple[str, list[str]]:
    findings: list[str] = []
    failed = False
    webhook_files = [
        f for f in ctx.files_with_ext(FRONTEND_SOURCE_EXTS | {".py"})
        if "webhook" in Path(f).name.lower()
    ]
    for rel in webhook_files:
        text = ctx.text(rel) or ""
        if "stripe" not in text.lower():
            continue
        verified = "constructevent" in text.lower() or "construct_event" in text.lower()
        if not verified:
            failed = True
            findings.append(f"{rel}: Stripe webhook handler without signature verification")
        else:
            findings.append(f"{rel}: Stripe webhook handler verifies signature")
    if not webhook_files:
        findings.append("Stripe detected; no webhook handler found to check")
    return ("FAIL" if failed else "PASS"), findings


def check_convex_auth_config(ctx: Ctx) -> tuple[str, list[str]]:
    has_auth_config = any(
        Path(f).parts[:1] == ("convex",) and Path(f).stem == "auth.config"
        for f in ctx.files
    )
    if not has_auth_config:
        return "FAIL", ["Convex functions present without convex/auth.config.*"]
    return "PASS", ["convex/auth.config.* present"]


def check_oversized_static_assets(ctx: Ctx) -> tuple[str, list[str]]:
    hits = []
    for rel in ctx.files_with_ext(STATIC_ASSET_EXTS):
        n = ctx.size(rel)
        if n is not None and n > MAX_ASSET_BYTES:
            hits.append(f"{rel}: {n} bytes exceeds the {MAX_ASSET_BYTES} byte threshold")
    if hits:
        return "FAIL", hits
    return "PASS", [f"no committed static asset exceeds the {MAX_ASSET_BYTES} byte threshold"]


def check_dependency_lockfile_present(ctx: Ctx) -> tuple[str, list[str]]:
    if "package.json" in ctx.files:
        found = NPM_LOCKFILE_NAMES & set(ctx.files)
        if found:
            return "PASS", [f"lockfile present: {sorted(found)[0]}"]
        return "FAIL", [
            "package.json present without a committed "
            "package-lock.json/yarn.lock/pnpm-lock.yaml"
        ]
    if "poetry.lock" in ctx.files:
        return "PASS", ["poetry.lock present"]
    return "FAIL", ["pyproject.toml declares [tool.poetry] without a committed poetry.lock"]


def check_os_cruft_files_committed(ctx: Ctx) -> tuple[str, list[str]]:
    hits = [rel for rel in ctx.files if Path(rel).name in OS_CRUFT_FILENAMES]
    if hits:
        return "FAIL", [f"OS-generated cruft file committed: {rel}" for rel in hits]
    return "PASS", ["no OS-generated cruft files found"]


def check_missing_404_page(ctx: Ctx) -> tuple[str, list[str]]:
    found = (NEXT_APP_NOT_FOUND_PATHS | NEXT_PAGES_404_PATHS | STATIC_404_PATHS) & set(ctx.files)
    if found:
        return "PASS", [f"custom 404/not-found page present: {sorted(found)[0]}"]
    return "FAIL", [
        "routed web app detected without a recognized custom 404/not-found page "
        "(app/not-found.*, pages/404.*, or public/404.html|404.html)"
    ]


@dataclass
class CheckSpec:
    id: str
    category: str
    severity: str
    check_type: str
    evidence_required: bool
    auto_fixable: bool
    applicable_when: Callable[[Ctx], bool]
    run: Callable[[Ctx], tuple[str, list[str]]]


CATALOG: list[CheckSpec] = [
    CheckSpec(
        id="exposed-secret-config-files",
        category="security",
        severity="BLOCKER",
        check_type="DETERMINISTIC",
        evidence_required=True,
        auto_fixable=False,
        applicable_when=lambda ctx: True,
        run=check_exposed_secret_files,
    ),
    CheckSpec(
        id="frontend-client-secret-leakage",
        category="security",
        severity="BLOCKER",
        check_type="DETERMINISTIC",
        evidence_required=True,
        auto_fixable=False,
        applicable_when=is_frontend,
        run=check_frontend_secret_leakage,
    ),
    CheckSpec(
        id="localhost-staging-url-residue",
        category="quality",
        severity="BLOCKER",
        check_type="DETERMINISTIC",
        evidence_required=True,
        auto_fixable=False,
        applicable_when=is_frontend,
        run=check_localhost_staging_residue,
    ),
    CheckSpec(
        id="accidental-noindex",
        category="findability",
        severity="BLOCKER",
        check_type="DETERMINISTIC",
        evidence_required=True,
        auto_fixable=False,
        applicable_when=is_frontend,
        run=check_accidental_noindex,
    ),
    CheckSpec(
        id="build-test-evidence-present",
        category="process",
        severity="BLOCKER",
        check_type="DETERMINISTIC",
        evidence_required=True,
        auto_fixable=False,
        applicable_when=lambda ctx: True,
        run=check_build_test_evidence,
    ),
    CheckSpec(
        id="stripe-webhook-signature-verification",
        category="security",
        severity="BLOCKER",
        check_type="DETERMINISTIC",
        evidence_required=True,
        auto_fixable=False,
        applicable_when=stripe_detected,
        run=check_stripe_webhook_verification,
    ),
    CheckSpec(
        id="convex-auth-config-present",
        category="security",
        severity="BLOCKER",
        check_type="DETERMINISTIC",
        evidence_required=True,
        auto_fixable=False,
        applicable_when=convex_detected,
        run=check_convex_auth_config,
    ),
    CheckSpec(
        id="oversized-static-assets",
        category="quality",
        severity="WARNING",
        check_type="DETERMINISTIC",
        evidence_required=True,
        auto_fixable=False,
        applicable_when=has_static_binary_assets,
        run=check_oversized_static_assets,
    ),
    CheckSpec(
        id="dependency-lockfile-present",
        category="process",
        severity="WARNING",
        check_type="DETERMINISTIC",
        evidence_required=True,
        auto_fixable=False,
        applicable_when=has_dependency_manifest,
        run=check_dependency_lockfile_present,
    ),
    CheckSpec(
        id="os-cruft-files-committed",
        category="quality",
        severity="WARNING",
        check_type="DETERMINISTIC",
        evidence_required=True,
        auto_fixable=False,
        applicable_when=lambda ctx: True,
        run=check_os_cruft_files_committed,
    ),
    CheckSpec(
        id="missing-404-page-evidence",
        category="findability",
        severity="WARNING",
        check_type="DETERMINISTIC",
        evidence_required=True,
        auto_fixable=False,
        applicable_when=is_routed_web_app,
        run=check_missing_404_page,
    ),
]


def run_catalog(root: Path) -> dict:
    ctx = build_ctx(root)
    results = []
    for spec in CATALOG:
        applicable = bool(spec.applicable_when(ctx))
        if not applicable:
            results.append({
                "id": spec.id,
                "category": spec.category,
                "severity": spec.severity,
                "check_type": spec.check_type,
                "evidence_required": spec.evidence_required,
                "auto_fixable": spec.auto_fixable,
                "applicable": False,
                "result": "SKIP",
                "detail": ["not applicable to this repository"],
            })
            continue
        result, detail = spec.run(ctx)
        if result not in {"PASS", "FAIL"}:
            raise InvalidInput(f"check {spec.id} returned invalid result {result!r}")
        results.append({
            "id": spec.id,
            "category": spec.category,
            "severity": spec.severity,
            "check_type": spec.check_type,
            "evidence_required": spec.evidence_required,
            "auto_fixable": spec.auto_fixable,
            "applicable": True,
            "result": result,
            "detail": detail,
        })
    return {"version": 1, "checks": results}


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"version": 1, "error": "usage: preflight.py REPO_ROOT"}, sort_keys=True))
        return 1
    root = Path(sys.argv[1])
    try:
        if not root.is_dir():
            raise InvalidInput(f"repo root is not a directory: {root}")
        result = run_catalog(root)
    except (OSError, InvalidInput) as exc:
        print(json.dumps({"version": 1, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
