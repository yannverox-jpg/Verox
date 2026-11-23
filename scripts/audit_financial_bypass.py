#!/usr/bin/env python3
"""Conservative AST audit for recognized financial side-effect call sites.

A direct financial side effect must be preceded by the central guarded entry
point in the same lexical scope. Nested functions do not satisfy the parent
scope's guard requirement. This is a static heuristic, not proof against
dynamic dispatch, aliases, reflection, or effects hidden in third-party code.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", ".venv", "venv", "__pycache__", "tests"}
GUARD_NAME = "execute_financial_action_guarded"
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


class DirectCallVisitor(ast.NodeVisitor):
    """Collect calls in one scope without attributing nested scopes to parents."""
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def visit_Call(self, node: ast.Call) -> None:
        name = call_name(node)
        if name:
            self.calls.append((name, node.lineno))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def scope_calls(node: ast.AST) -> list[tuple[str, int]]:
    visitor = DirectCallVisitor()
    for child in ast.iter_child_nodes(node):
        visitor.visit(child)
    return visitor.calls


def label_for(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def audit_file(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        return [f"{label_for(path)}: unable to audit ({type(exc).__name__})"]

    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            calls = scope_calls(node)
            label = getattr(node, "name", "<lambda>")
            line = getattr(node, "lineno", 1)
        elif isinstance(node, ast.Module):
            calls = scope_calls(node)
            label = "<module>"
            line = 1
        else:
            continue

        guards = [call_line for name, call_line in calls if name == GUARD_NAME]
        sinks = [(name, call_line) for name, call_line in calls if name in SIDE_EFFECT_NAMES]
        for sink, sink_line in sinks:
            if not any(guard_line < sink_line for guard_line in guards):
                findings.append(
                    f"{label_for(path)}:{line}: {label} contains financial side effect "
                    f"{sink} at line {sink_line} without an earlier {GUARD_NAME} call "
                    "in the same scope"
                )
    return findings


def main() -> int:
    files = []
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        files.append(path)

    findings = [finding for path in sorted(files) for finding in audit_file(path)]
    if findings:
        print("FAIL: possible unguarded financial side-effect call sites found:")
        print("\n".join(f"- {item}" for item in findings))
        return 1

    print(f"PASS: no recognized unguarded financial side-effect call sites found in {len(files)} production Python files.")
    print("LIMITATION: static name-based scan only; indirect/dynamic side effects and real adapter wiring require separate verification.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
