#!/usr/bin/env python3
"""Conservative static audit for unguarded financial side-effect call sites.

This is a heuristic safety net, not proof that all external effects are discovered.
It scans Python source for known order/bet/transfer submission calls and requires
the containing function to invoke execute_financial_action_guarded. Human review
and runtime integration tests remain required.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", ".venv", "venv", "__pycache__", "tests"}
EXCLUDED_FILES = {
    "financial_execution_guard.py",
    "financial_approval_supabase_adapter.py",
}
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


def function_nodes(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            yield node


def audit_file(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        return [f"{path.relative_to(ROOT)}: unable to audit ({type(exc).__name__})"]

    findings: list[str] = []
    functions = list(function_nodes(tree))
    for fn in functions:
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
        names = {call_name(n) for n in calls}
        sinks = sorted(names & SIDE_EFFECT_NAMES)
        if not sinks:
            continue
        if not (names & GUARD_NAMES):
            line = getattr(fn, "lineno", 1)
            findings.append(
                f"{path.relative_to(ROOT)}:{line}: {getattr(fn, 'name', '<lambda>')} "
                f"contains possible financial side effect(s) {', '.join(sinks)} "
                "without execute_financial_action_guarded in the same function"
            )
    return findings


def main() -> int:
    files = []
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.name in EXCLUDED_FILES or path == Path(__file__):
            continue
        files.append(path)

    findings = [finding for path in sorted(files) for finding in audit_file(path)]
    if findings:
        print("FAIL: possible unguarded financial side-effect call sites found:")
        print("\n".join(f"- {item}" for item in findings))
        return 1

    print(f"PASS: no recognized unguarded financial side-effect call sites found in {len(files)} Python files.")
    print("LIMITATION: static name-based scan only; it does not prove production wiring or detect every indirect/dynamic side effect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
