#!/usr/bin/env python3
"""Fail-closed AST inventory of recognized financial side-effect call sites.

The current repository intentionally contains no live trading/payment adapter.
Any recognized direct side effect in production Python must therefore fail CI
until it is reviewed and implemented behind the one-time human approval entry
point. This is a conservative heuristic, not a proof against dynamic dispatch,
aliases, reflection, or effects hidden inside third-party libraries.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", ".venv", "venv", "__pycache__", "tests"}
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


def audit_file(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        return [f"{path.relative_to(ROOT)}: unable to audit ({type(exc).__name__})"]

    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node)
        if name in SIDE_EFFECT_NAMES:
            findings.append(
                f"{path.relative_to(ROOT)}:{node.lineno}: recognized financial side effect "
                f"{name} is forbidden until a reviewed guarded adapter is explicitly allowlisted"
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
        print("FAIL: financial side-effect call sites require explicit guarded-adapter review:")
        print("\n".join(f"- {item}" for item in findings))
        return 1

    print(f"PASS: no recognized direct financial side-effect calls in {len(files)} production Python files.")
    print("LIMITATION: static scan cannot prove safety for dynamic/aliased calls or third-party effects.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
