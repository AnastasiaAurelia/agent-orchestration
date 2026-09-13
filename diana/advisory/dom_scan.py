#!/usr/bin/env python3
"""Diana advisory: DOM-XSS-001, the one rule M1 ships (spec steps 4, D3-D8, C3).

This module is the ONLY producer of graded findings (D1). Hermes never emits a
finding; whatever it says lands in a structurally separate, severity-less array
that cannot affect pass/fail. The measurable security delta over Diana's
baseline comes from here, and M1's honest thesis is that a proportional D1
artifact can be produced under a proven envelope -- not that an agent found a
bug.

## What it proves, and what it refuses to claim

A finding requires the source expression to appear **syntactically inside the
sink's assignment or call expression, in the same statement** (`flow: DIRECT`,
D4). `const h = location.hash; el.innerHTML = h;` is NOT reported: that needs
variable tracking, which is machinery that would itself need testing, and its
absence is declared in `limitations[]` rather than discovered later by a user.

There is no payload string and no browser proof (D3). A static scanner emitting
`#<img src=x onerror=alert(1)>` would be emitting an assertion dressed as
evidence; runtime reproduction is what distinguishes D2 from D1.

## Why a lexer rather than regex (D6)

`// el.innerHTML = location.hash` and `log("el.innerHTML = location.hash")` must
not produce findings, and a comment reading `// sanitized with DOMPurify` must
not suppress one. Both demand knowing whether an offset is code, so the source
is first masked by a character-level state machine -- comments and string bodies
become spaces, preserving every offset and newline -- and all matching happens
on the masked text. Pattern matching *within known code* is then sound.

## Sanitizers (D5)

The allowlist is exactly `DOMPurify.sanitize`, and suppression is by ENCLOSING
CALL EXPRESSION, never by a name appearing somewhere on the line.
`encodeURIComponent` is deliberately absent: it is a URL encoder, not an HTML
sanitizer, and trusting it is a context confusion. `escapeHtml` is absent
because it is an unbound name that could be anything. The opposite error is
accepted and stated: `mySanitize(location.hash)` is still reported.

Suppressed candidates stay VISIBLE with file, line and reason -- never a silent
drop, and never inside `findings[]`.
"""

from __future__ import annotations

import os
import re
from pathlib import PurePosixPath

RULE_ID = "DOM-XSS-001"
SEVERITY_HIGH = "HIGH"

# Spec C3: 512,000 bytes, aligned with Hermes's own _LARGE_FILE_HINT_BYTES
# (tools/file_tools.py:116) so the two components agree on what "large" means.
MAX_FILE_BYTES = 512_000

SCANNABLE_SUFFIXES = frozenset({".js", ".mjs", ".html", ".htm"})

# Closed source set (D8). `window.` is optional on the location/name forms.
SOURCE_PATTERNS = (
    ("location.hash", r"(?<![\w$.])(?:window\.)?location\.hash\b"),
    ("location.search", r"(?<![\w$.])(?:window\.)?location\.search\b"),
    ("location.href", r"(?<![\w$.])(?:window\.)?location\.href\b"),
    ("document.URL", r"(?<![\w$.])document\.URL\b"),
    ("document.referrer", r"(?<![\w$.])document\.referrer\b"),
    ("window.name", r"(?<![\w$.])window\.name\b"),
)
_SOURCE_RE = re.compile("|".join(f"(?P<g{i}>{p})" for i, (_, p) in enumerate(SOURCE_PATTERNS)))
_SOURCE_KINDS = [kind for kind, _ in SOURCE_PATTERNS]

# Closed sink set (D8). eval/Function are code injection, a different rule
# family, and are NOT_CHECKED in M1.
_ASSIGN_SINK_RE = re.compile(r"\.(innerHTML|outerHTML)\s*(\+?=)(?!=)")
_CALL_SINK_RE = re.compile(r"(?<![\w$.])document\.(write|writeln)\s*\(|\.(insertAdjacentHTML)\s*\(")

SANITIZER_ALLOWLIST = ("DOMPurify.sanitize",)
_CALLEE_RE = re.compile(r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*$")

GLOBAL_LIMITATIONS = (
    "DOM-XSS-001 reports only syntactically DIRECT source-to-sink flows; a source assigned to a "
    "variable before reaching a sink is not analyzed.",
    "Template-literal flows are NOT_ANALYZED; a sink assigned a template literal is recorded as an "
    "unsupported construct rather than reported as a finding.",
    "Sanitizer recognition is limited to DOMPurify.sanitize; a custom or renamed sanitizer wrapping a "
    "source will still be reported as a finding.",
    "Evidence is static only: no payload was constructed and no browser execution was attempted.",
    "Only .js, .mjs, .html and .htm files are analyzed; HTML is analyzed via inline <script> blocks only.",
)


# --- lexical masking (D6) -------------------------------------------------

class _Masked:
    """Source with comments and string bodies blanked, offsets preserved."""

    def __init__(self, text: str, masked: str, templates: list[tuple[int, int, str]]) -> None:
        self.text = text
        self.masked = masked
        self.templates = templates
        self._line_starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                self._line_starts.append(i + 1)

    def line_of(self, offset: int) -> int:
        low, high = 0, len(self._line_starts) - 1
        while low < high:
            mid = (low + high + 1) // 2
            if self._line_starts[mid] <= offset:
                low = mid
            else:
                high = mid - 1
        return low + 1

    def line_text(self, line: int) -> str:
        start = self._line_starts[line - 1]
        end = self.text.find("\n", start)
        return self.text[start: end if end != -1 else len(self.text)]


_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%~^<>") | {"\n"}


def mask(text: str) -> _Masked:
    """Blank comments and string bodies; record template-literal spans.

    Template bodies are masked too, so a source inside one can never be matched
    as a DIRECT flow -- but the span and its raw text are kept, because meeting
    one is a fact the report must state rather than pass over.
    """
    out = list(text)
    templates: list[tuple[int, int, str]] = []
    i, n = 0, len(text)
    prev_significant = "\n"

    def blank(start: int, end: int) -> None:
        for k in range(start, min(end, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            j = text.find("\n", i)
            j = n if j == -1 else j
            blank(i, j); i = j; continue
        if ch == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            blank(i, j); i = j; continue
        if ch == "/" and prev_significant in _REGEX_PRECEDERS:
            # Regex literal, not division. Getting this wrong can only blank
            # code (a missed finding), never invent one.
            j, escaped, closed = i + 1, False, False
            in_class = False
            while j < n:
                c = text[j]
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == "[":
                    in_class = True
                elif c == "]":
                    in_class = False
                elif c == "/" and not in_class:
                    closed = True; j += 1; break
                elif c == "\n":
                    break
                j += 1
            if closed:
                blank(i, j); prev_significant = "/"; i = j; continue
        if ch in ("'", '"'):
            j, escaped = i + 1, False
            while j < n:
                c = text[j]
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == ch:
                    j += 1; break
                elif c == "\n":
                    break
                j += 1
            blank(i + 1, j - 1 if j <= n else j)
            prev_significant = '"'
            i = j; continue
        if ch == "`":
            j, escaped, depth = i + 1, False, 0
            while j < n:
                c = text[j]
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == "$" and j + 1 < n and text[j + 1] == "{":
                    depth += 1; j += 1
                elif c == "}" and depth:
                    depth -= 1
                elif c == "`" and not depth:
                    j += 1; break
                j += 1
            templates.append((i, min(j, n), text[i: min(j, n)]))
            blank(i + 1, j - 1 if j <= n else j)
            prev_significant = "`"
            i = j; continue
        if not ch.isspace():
            prev_significant = ch
        elif ch == "\n":
            prev_significant = "\n"
        i += 1
    return _Masked(text, "".join(out), templates)


# --- statement / argument extents -----------------------------------------

def _statement_end(masked: str, start: int) -> int:
    """End of the statement beginning at `start`: the first top-level `;`, or
    end of line when the statement is unterminated."""
    depth = 0
    for i in range(start, len(masked)):
        c = masked[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return i
            depth -= 1
        elif c == ";" and depth == 0:
            return i
        elif c == "\n" and depth == 0:
            return i
    return len(masked)


def _balanced_args(masked: str, open_paren: int) -> int:
    depth = 0
    for i in range(open_paren, len(masked)):
        c = masked[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
    return len(masked)


def _enclosing_sanitizer(masked: str, offset: int, lower_bound: int) -> str | None:
    """Name of the innermost call enclosing `offset`, if it is allowlisted.

    Walks outward through balanced parens so the check is about *enclosure*, not
    about a sanitizer name merely appearing nearby.
    """
    i, depth = offset - 1, 0
    while i >= lower_bound:
        c = masked[i]
        if c == ")":
            depth += 1
        elif c == "(":
            if depth == 0:
                callee = _CALLEE_RE.search(masked[lower_bound:i])
                if callee and callee.group(1) in SANITIZER_ALLOWLIST:
                    return callee.group(1)
                return None
            depth -= 1
        i -= 1
    return None


def _sources_in(masked: str, start: int, end: int) -> list[tuple[str, int, str]]:
    found = []
    for m in _SOURCE_RE.finditer(masked, start, end):
        idx = next(i for i in range(len(_SOURCE_KINDS)) if m.group(f"g{i}") is not None)
        found.append((_SOURCE_KINDS[idx], m.start(), m.group(0)))
    return found


# --- HTML inline script extraction ----------------------------------------

_SCRIPT_OPEN_RE = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)
_SCRIPT_CLOSE_RE = re.compile(r"</script\s*>", re.IGNORECASE)


def _inline_scripts(text: str) -> list[tuple[int, str]]:
    """(absolute offset, body) for each inline <script> block.

    A `<script src=...>` block has no body to analyze here; external scripts are
    their own category and are NOT_CHECKED in M1.
    """
    blocks = []
    for m in _SCRIPT_OPEN_RE.finditer(text):
        if re.search(r"\bsrc\s*=", m.group(1), re.IGNORECASE):
            continue
        close = _SCRIPT_CLOSE_RE.search(text, m.end())
        end = close.start() if close else len(text)
        blocks.append((m.end(), text[m.end():end]))
    return blocks


# --- the rule -------------------------------------------------------------

def scan_text(source: str, rel_path: str, line_offset: int = 0) -> tuple[list[dict], list[dict], list[dict]]:
    """Analyze one unit of JavaScript. Returns (findings, suppressed, unsupported)."""
    view = mask(source)
    masked = view.masked
    findings: list[dict] = []
    suppressed: list[dict] = []
    unsupported: list[dict] = []

    def record(sink_kind: str, sink_start: int, span_start: int, span_end: int) -> None:
        sink_line = view.line_of(sink_start) + line_offset
        sink_text = view.text[sink_start: _statement_end(masked, sink_start)].strip()

        # A template literal anywhere in the sink's value is NOT_ANALYZED (D4),
        # and is reported as an encountered construct rather than passed over.
        for t_start, t_end, raw in view.templates:
            if t_start < span_end and t_end > span_start:
                if _SOURCE_RE.search(raw):
                    unsupported.append({
                        "file": rel_path,
                        "line": view.line_of(t_start) + line_offset,
                        "construct": "template_literal",
                        "reason": "template-literal flows are NOT_ANALYZED in M1",
                    })
                return

        for kind, offset, text_ in _sources_in(masked, span_start, span_end):
            sanitizer = _enclosing_sanitizer(masked, offset, span_start)
            if sanitizer:
                suppressed.append({
                    "file": rel_path,
                    "line": view.line_of(offset) + line_offset,
                    "rule_id": RULE_ID,
                    "reason": f"enclosed in {sanitizer}",
                })
                continue
            findings.append({
                "rule_id": RULE_ID,
                "severity": SEVERITY_HIGH,
                "file": rel_path,
                "line": sink_line,
                "source": {"kind": kind, "line": view.line_of(offset) + line_offset, "text": text_},
                "sink": {"kind": sink_kind, "line": sink_line, "text": sink_text},
                "flow": "DIRECT",
                "code_excerpt": view.line_text(view.line_of(sink_start)),
            })

    for m in _ASSIGN_SINK_RE.finditer(masked):
        rhs_start = m.end()
        record(m.group(1), m.start(), rhs_start, _statement_end(masked, rhs_start))

    for m in _CALL_SINK_RE.finditer(masked):
        kind = f"document.{m.group(1)}" if m.group(1) else m.group(2)
        open_paren = masked.index("(", m.start(), m.end())
        record(kind, m.start(), open_paren + 1, _balanced_args(masked, open_paren))

    return findings, suppressed, unsupported


def scan_file(repo_root: str, rel_path: str) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Analyze one repo-relative file. Returns (findings, suppressed, unsupported, issues).

    Every read failure becomes a concrete `scan_issues[]` entry rather than a
    silent skip: a file that could not be analyzed must force INCOMPLETE, not
    disappear from the report.
    """
    absolute = os.path.join(repo_root, rel_path)
    try:
        size = os.stat(absolute).st_size
    except OSError as exc:
        return [], [], [], [{"file": rel_path, "kind": "READ_FAILED", "detail": exc.strerror or str(exc)}]
    if size > MAX_FILE_BYTES:
        return [], [], [], [{
            "file": rel_path, "kind": "FILE_TOO_LARGE",
            "detail": f"{size} bytes exceeds the {MAX_FILE_BYTES}-byte scanner ceiling",
        }]
    try:
        raw = open(absolute, "rb").read()
    except OSError as exc:
        return [], [], [], [{"file": rel_path, "kind": "READ_FAILED", "detail": exc.strerror or str(exc)}]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [], [], [], [{"file": rel_path, "kind": "DECODE_FAILED", "detail": f"not valid UTF-8: {exc.reason}"}]

    suffix = PurePosixPath(rel_path).suffix.lower()
    if suffix in (".html", ".htm"):
        findings: list[dict] = []
        suppressed: list[dict] = []
        unsupported: list[dict] = []
        for offset, body in _inline_scripts(text):
            line_offset = text.count("\n", 0, offset)
            f, s, u = scan_text(body, rel_path, line_offset)
            findings += f; suppressed += s; unsupported += u
        return findings, suppressed, unsupported, []
    return (*scan_text(text, rel_path), [])


def scan(repo_root: str, rel_paths: list[str]) -> dict:
    """Analyze an ordered inventory. Output order is deterministic."""
    findings: list[dict] = []
    suppressed: list[dict] = []
    unsupported: list[dict] = []
    issues: list[dict] = []
    for rel in sorted(rel_paths):
        if PurePosixPath(rel).suffix.lower() not in SCANNABLE_SUFFIXES:
            continue
        f, s, u, i = scan_file(repo_root, rel)
        findings += f; suppressed += s; unsupported += u; issues += i
    key = lambda d: (d.get("file", ""), d.get("line", 0), d.get("rule_id", ""), d.get("kind", ""))
    return {
        "findings": sorted(findings, key=key),
        "suppressed": sorted(suppressed, key=key),
        "unsupported_constructs": sorted(unsupported, key=key),
        "scan_issues": sorted(issues, key=key),
        "limitations": list(GLOBAL_LIMITATIONS),
    }
