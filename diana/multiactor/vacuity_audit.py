#!/usr/bin/env python3
"""M6: an AST vacuity audit of an acceptance suite.

M5's independent review found assertions that reported green while asserting
nothing. This runs over the embedded Python of a suite and flags the shapes that
failure took, so vacuity is caught by a tool rather than by a reviewer's
patience.

What it flags, and why each one is a way to pass without testing:

* a literal `True`/truthy constant reached as a condition -- passes always;
* `A or True` / `... else True` -- a fallback that swallows the real result;
* `all(...)`/`any(...)` over a possibly-empty literal -- `all([])` is True;
* a condition that mentions no name bound from system output -- an assertion
  about the harness rather than about the system;
* a comparison whose both sides are literals -- arithmetic, not evidence;
* `in` against a source file the suite itself wrote -- a self-source grep
  proves an author typed something, never that a control runs.

It is deliberately noisy: a finding is a question to answer, and the answer is
recorded in `M6-AUDIT.md` rather than by silencing the tool.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ASSERT_FUNCS = {"check", "falsify"}
TRUTHY_CONSTANTS = (True, 1)
# Calls to these prove nothing about the system: they are pure transformations of
# values the suite already had. A condition whose only call is one of these is
# still a static assertion.
PURE_BUILTINS = {"set", "list", "tuple", "dict", "frozenset", "sorted", "len",
                 "str", "int", "bool", "float", "any", "all", "range", "abs"}


def embedded_python(path: Path) -> str:
    """Extract the `<<'PY' ... PY` heredoc body from a suite shell script."""
    text = path.read_text()
    match = re.search(r"<<'PY'\n(.*)\nPY\n", text, re.S)
    return match.group(1) if match else text


def _observes_system(node: ast.AST, dynamic: set[str]) -> bool:
    """True when the condition actually looks at something the system produced.

    Either it names a value bound from system output, or it CALLS something that
    is not a pure value transform. `set(A) == set(B)` over two module constants
    is a static pin, not an observation, and is meant to be flagged.
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in dynamic:
            return True
        if isinstance(sub, ast.Call):
            func = sub.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name not in PURE_BUILTINS:
                return True
    return False


def audit(source: str) -> list[dict]:
    tree = ast.parse(source)
    findings: list[dict] = []

    # Names bound anywhere in the suite from a call (system output) or a loop.
    dynamic: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(v, (ast.Call, ast.Subscript, ast.Attribute, ast.DictComp,
                               ast.ListComp, ast.SetComp))
                for v in ast.walk(node.value)):
            for target in node.targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        dynamic.add(name.id)
        elif isinstance(node, (ast.For, ast.comprehension)):
            target = node.target
            for name in ast.walk(target):
                if isinstance(name, ast.Name):
                    dynamic.add(name.id)
        elif isinstance(node, ast.FunctionDef):
            dynamic.add(node.name)

    # Calls made INSIDE the check/falsify helpers are the helpers' own plumbing,
    # not assertions about the system.
    helper_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ASSERT_FUNCS:
            helper_lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ASSERT_FUNCS):
            continue
        if node.lineno in helper_lines:
            continue
        if len(node.args) < 2:
            findings.append({"line": node.lineno, "kind": "no-condition",
                             "detail": f"{node.func.id}() called without a condition"})
            continue
        label = node.args[0]
        cond = node.args[1]
        label_text = ast.literal_eval(label) if isinstance(label, ast.Constant) else "<dynamic>"

        if isinstance(cond, ast.Constant) and cond.value in TRUTHY_CONSTANTS:
            findings.append({"line": node.lineno, "kind": "literal-true",
                             "detail": f"condition is the constant {cond.value!r}",
                             "label": label_text})
            continue

        for sub in ast.walk(cond):
            if isinstance(sub, ast.BoolOp) and isinstance(sub.op, ast.Or):
                for value in sub.values:
                    if isinstance(value, ast.Constant) and value.value in TRUTHY_CONSTANTS:
                        findings.append({"line": node.lineno, "kind": "or-true",
                                         "detail": "an `or <truthy literal>` fallback",
                                         "label": label_text})
            if isinstance(sub, ast.IfExp) and isinstance(sub.orelse, ast.Constant) \
                    and sub.orelse.value in TRUTHY_CONSTANTS:
                findings.append({"line": node.lineno, "kind": "else-true",
                                 "detail": "an `else <truthy literal>` fallback",
                                 "label": label_text})
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                    and sub.func.id in {"all", "any"} and sub.args:
                arg = sub.args[0]
                if isinstance(arg, (ast.List, ast.Tuple, ast.Set)) and not arg.elts:
                    findings.append({"line": node.lineno, "kind": "empty-quantifier",
                                     "detail": f"{sub.func.id}() over an empty literal",
                                     "label": label_text})
            if isinstance(sub, ast.Compare) and isinstance(sub.left, ast.Constant) \
                    and all(isinstance(c, ast.Constant) for c in sub.comparators):
                findings.append({"line": node.lineno, "kind": "literal-comparison",
                                 "detail": "both sides of the comparison are literals",
                                 "label": label_text})

        if not _observes_system(cond, dynamic):
            findings.append({"line": node.lineno, "kind": "no-system-observation",
                             "detail": "condition references no name bound from system output",
                             "label": label_text})

        source_seg = ast.get_source_segment(source, cond) or ""
        if re.search(r"\bin\s+\w*(source|joined|text|src_text)\b", source_seg):
            findings.append({"line": node.lineno, "kind": "possible-self-source-grep",
                             "detail": "condition greps text the suite itself read",
                             "label": label_text})
    return findings


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]] or [
        Path(__file__).resolve().parent / "test-m6-multiactor.sh"]
    total = 0
    for path in paths:
        findings = audit(embedded_python(path))
        total += len(findings)
        print(f"\n=== {path.name}: {len(findings)} finding(s) ===")
        for f in findings:
            print(f"  line {f['line']:>4}  {f['kind']:<26} {f.get('label', '')[:78]}")
            print(f"            {f['detail']}")
    print(f"\nTOTAL: {total} finding(s) to answer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
