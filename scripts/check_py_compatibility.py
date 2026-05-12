#!/usr/bin/env python3
"""
Basic Python 3.8-3.14 compatibility checks.

Checks:
- Syntax parse under selected feature versions (3.8 through 3.14)
- PEP604 union types in annotations (e.g., str | None) which require Python 3.10+
- match/case statements (Python 3.10+)
- Optional bytecode compilation via compileall (run under target Python in CI)
"""

from __future__ import annotations

import argparse
import ast
import compileall
import pathlib
import sys
from typing import Dict, Iterable, List, Tuple


def _iter_py_files(root: pathlib.Path) -> Iterable[pathlib.Path]:
    for path in root.rglob("*.py"):
        if path.name == "__init__.py":
            yield path
        elif path.is_file():
            yield path


def _find_pep604_and_match(nodes: ast.AST) -> List[Tuple[int, str]]:
    violations: List[Tuple[int, str]] = []
    for node in ast.walk(nodes):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annots = []
            if node.returns is not None:
                annots.append(node.returns)
            for arg in node.args.args + node.args.kwonlyargs:
                if arg.annotation is not None:
                    annots.append(arg.annotation)
            if node.args.vararg and node.args.vararg.annotation is not None:
                annots.append(node.args.vararg.annotation)
            if node.args.kwarg and node.args.kwarg.annotation is not None:
                annots.append(node.args.kwarg.annotation)
            for a in annots:
                if isinstance(a, ast.BinOp) and isinstance(a.op, ast.BitOr):
                    violations.append((a.lineno, "PEP604 union (use Optional/Union)"))
        if isinstance(node, ast.AnnAssign) and node.annotation is not None:
            a = node.annotation
            if isinstance(a, ast.BinOp) and isinstance(a.op, ast.BitOr):
                violations.append((a.lineno, "PEP604 union (use Optional/Union)"))
        if isinstance(node, ast.Match):
            violations.append((node.lineno, "match/case requires Python 3.10+"))
    return violations


def check_pep604_and_match(root: pathlib.Path) -> int:
    violations = 0
    for path in _iter_py_files(root):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError as e:
            print(f"{path}: SyntaxError at line {e.lineno}: {e.msg}")
            violations += 1
            continue

        file_violations = _find_pep604_and_match(tree)
        for lineno, msg in file_violations:
            print(f"{path}:{lineno}: {msg}")
        violations += len(file_violations)
    return violations


def check_syntax_by_version(root: pathlib.Path, targets: Dict[str, int]) -> int:
    violations = 0
    for path in _iter_py_files(root):
        source = path.read_text()
        for label, feature_version in targets.items():
            try:
                ast.parse(source, feature_version=feature_version)
            except SyntaxError as e:
                print(f"{path}:{e.lineno}: SyntaxError under Python {label}: {e.msg}")
                violations += 1
    return violations


def run_compileall(root: pathlib.Path) -> bool:
    return compileall.compile_dir(str(root), quiet=1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Python 3.8-3.14 compatibility.")
    parser.add_argument("path", nargs="?", default="sima_cli", help="Root path to scan.")
    parser.add_argument("--skip-compile", action="store_true", help="Skip compileall check.")
    parser.add_argument("--skip-syntax", action="store_true", help="Skip PEP604/match checks.")
    parser.add_argument(
        "--targets",
        default="3.8,3.9,3.10,3.11,3.12,3.13,3.14",
        help="Comma-separated Python versions to check syntax against.",
    )
    args = parser.parse_args()

    root = pathlib.Path(args.path)
    if not root.exists():
        print(f"Path does not exist: {root}")
        return 2

    targets = {}
    for t in [x.strip() for x in args.targets.split(",") if x.strip()]:
        if t in ("3.8", "3.9", "3.10", "3.11", "3.12", "3.13", "3.14"):
            targets[t] = int(t.split(".")[1])
        else:
            print(f"Unsupported target: {t}. Use 3.8 through 3.14.")
            return 2

    syntax_violations = check_syntax_by_version(root, targets)
    if syntax_violations:
        print(f"Found {syntax_violations} syntax issue(s) across targets.")
        return 1

    if not args.skip_syntax:
        violations = check_pep604_and_match(root)
        if violations and any(t in ("3.8", "3.9") for t in targets):
            print(f"Found {violations} Python 3.10+ syntax issue(s).")
            return 1

    if not args.skip_compile:
        ok = run_compileall(root)
        if not ok:
            print("compileall failed. Run this under each target Python to validate.")
            return 1

    print("Python compatibility checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
