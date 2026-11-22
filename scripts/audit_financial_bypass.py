#!/usr/bin/env python3
"""Conservative AST audit for likely unguarded financial side-effect call sites.

Heuristic only: indirect/dynamic calls and effects hidden in external libraries
cannot be proven safe by this scan. Concrete execution adapters need runtime tests.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", ".venv", "venv", "__pycache__", "tests"}
EXCLUDED_FILES = {"financial_execution_guard.py", "financial_approval_supabase_adapter.py"}
GUARD_NAMES = {"execute_financial_action_guarded"}
SIDE_EFFECT_NAMES = {
    "order_send", "place_order", "submit_order", "create_order",
    "place_bet", "submit_bet", "place_wager", "submit_wager",
    "transfer_funds", "send_transaction", "execute_transfer",
    "withdraw", "withdraw_funds", "send_money",
}


def call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id.lower()
    if isinstance(func, ast.Attribute):
        return func.attr.lower()
    return ""


class _DirectCallVisitor(ast.NodeVisitor):
    """Collect calls in one scope without attributing nested scopes to parents."""
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        name = call_name(node)
        if name:
            self.names.add(name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def _scope_calls(node: ast.AST) -> set[str]:
    visitor = _DirectCallVisitor()
    for child in ast.iter_child_nodes(node):
        visitor.visit(child)
    return visitor.names


def audit_file(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        try:
            label = str(path.relative_to(ROOT))
        except ValueError:
            label = str(path)
        return [f"{label}: unable to audit ({type(exc).__name__})"]

    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            names = _scope_calls(node)
            label = getattr(node, "name", "<lambda>")
            line = getattr(node, "lineno", 1)
        elif isinstance(node, ast.Module):
            names = _scope_calls(node)
            label = "<module>"
            line = 1
        else:
            continue

        sinks = sorted(names & SIDE_EFFECT_NAMES)
        if sinks and not (names & GUARD_NAMES):
            try:
                file_label = str(path.relative_to(ROOT))
            except ValueError:
                file_label = str(path)
            findings.append(
                f"{file_label}:{line}: {label} contains possible financial side effect(s) "
                f"{', '.join(sinks)} without execute_financial_action_guarded in the same scope"
            )
    return findings


def main() -> int:
    files = []
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.name in EXCLUDED_FILES or path.resolve() == Path(__file__).resolve():
            continue
        files.append(path)

    findings = [finding for path in sorted(files) for finding in audit_file(path)]
    if findings:
        print("FAIL: possible unguarded financial side-effect call sites found:")
        print("\n".join(f"- {item}" for item in findings))
        return 1

    print(f"PASS: no recognized unguarded financial side-effect call sites found in {len(files)} Python files.")
    print("LIMITATION: static name-based scan only; indirect/dynamic side effects and real adapter wiring require separate verification.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
