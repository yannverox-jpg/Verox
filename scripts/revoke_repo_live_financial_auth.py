#!/usr/bin/env python3
"""Audit and safely disable repository-configured live financial execution.

This tool edits only explicit live-execution configuration flags in repository
files. SQL migrations/RPCs and remote services are audit-only: rewriting migration
history cannot revoke privileges already granted in a deployed Supabase database.
Use the generated report to review and revoke deployed grants through an
authenticated, reviewed database change.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKIP_DIRS = {
    ".git", ".github", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".venv", "venv", "node_modules", "dist", "build",
}
TEXT_EXTENSIONS = {
    ".py", ".json", ".yaml", ".yml", ".toml", ".env", ".sql", ".txt",
    ".md", ".html", ".js", ".ts", ".ini", ".cfg", ".conf",
}
LIVE_FLAG_KEYS = {
    "live_transactions_authorized",
    "live_transactions_enabled",
    "real_transactions_enabled",
    "live_trading_enabled",
    "real_money_enabled",
    "live_execution_enabled",
    "allow_real_transactions",
    "allow_live_orders",
    "verox_live_transactions_authorized",
    "live_orders_enabled",
}
ENV_KEYS = {
    "LIVE_TRADING_ENABLED",
    "REAL_TRANSACTIONS_ENABLED",
    "VEROX_LIVE_TRANSACTIONS_AUTHORIZED",
    "ALLOW_LIVE_ORDERS",
    "LIVE_TRANSACTIONS_ENABLED",
    "REAL_MONEY_ENABLED",
    "LIVE_EXECUTION_ENABLED",
}
EXECUTION_MODE_KEY = "VEROX_EXECUTION_MODE"
SQL_RE = re.compile(
    r"\b(grant|revoke|create\s+(or\s+replace\s+)?function|"
    r"security\s+definer|execute\s+on\s+function|"
    r"financial[_ ]approval|live[_ ]trading|place_order|"
    r"submit_order|withdraw|transfer)\b",
    re.IGNORECASE,
)
ASSIGNMENT_RE = re.compile(
    r"^(?P<prefix>\s*(?:(?:export)\s+)?[\\"']?"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_-]*)[\\"']?\s*[:=]\s*)"
    r"(?P<value>.*?)(?P<newline>\r?\n)?$"
)
JSON_TRUE_KEYS = {key for key in LIVE_FLAG_KEYS}


def _is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() in {"true", "yes", "1", "on"})


def _revoke_json(value: Any, changes: list[str], prefix: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in list(value.items()):
            key_lower = str(key).lower()
            if key_lower in JSON_TRUE_KEYS and _is_true(child):
                value[key] = False
                changes.append(f"{prefix}.{key}: true -> false")
            else:
                _revoke_json(child, changes, f"{prefix}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _revoke_json(child, changes, f"{prefix}[{index}]")


def _line_key_value_rewrite(text: str, suffix: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    output: list[str] = []
    for line_no, line in enumerate(text.splitlines(keepends=True), 1):
        match = ASSIGNMENT_RE.match(line)
        if not match:
            output.append(line)
            continue
        key = match.group("key").strip("\"'").lower()
        current = match.group("value").strip().strip("\"'").lower()
        target: str | None = None
        if key in LIVE_FLAG_KEYS or key.upper() in ENV_KEYS:
            target = "false"
        elif key.upper() == EXECUTION_MODE_KEY:
            target = "dry_run_only"
        if target is None or current == target:
            output.append(line)
            continue
        newline = match.group("newline") or ""
        output.append(f"{match.group('prefix')}{target}{newline}")
        changes.append(f"line {line_no}: {match.group('key')} -> {target}")
    return "".join(output), changes


def process_file(path: Path, apply: bool, backup: bool) -> dict[str, Any]:
    ext = path.suffix.lower()
    if ext not in TEXT_EXTENSIONS and not path.name.lower().startswith(".env"):
        return {"path": str(path), "action": "skipped", "reason": "unsupported file type"}
    try:
        original = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return {"path": str(path), "action": "skipped", "reason": f"unreadable text file: {exc}"}

    # SQL is deliberately audit-only: migrations may already have been applied
    # remotely, and blindly rewriting them cannot revoke existing database grants.
    if ext == ".sql":
        matches = [
            {"line": number, "topic": match.group(0).lower()}
            for number, line in enumerate(original.splitlines(), 1)
            for match in [SQL_RE.search(line)]
            if match
        ]
        return {
            "path": str(path), "action": "audit_only",
            "reason": "SQL/RPC requires a reviewed database-specific migration",
            "matches": matches,
        }

    updated = original
    changes: list[str] = []
    if ext == ".json":
        try:
            parsed = json.loads(original)
        except json.JSONDecodeError as exc:
            return {"path": str(path), "action": "skipped", "reason": f"invalid JSON: {exc}"}
        _revoke_json(parsed, changes)
        if changes:
            updated = json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"
    else:
        updated, changes = _line_key_value_rewrite(original, ext)

    if not changes:
        return {"path": str(path), "action": "unchanged", "changes": []}
    if apply:
        if backup:
            backup_path = path.with_name(path.name + ".bak")
            shutil.copy2(path, backup_path)
        path.write_text(updated, encoding="utf-8")
    return {
        "path": str(path),
        "action": "modified" if apply else "would_modify",
        "changes": changes,
        "backup_created": bool(apply and backup),
    }


def scan_repo(root: Path, apply: bool, backup: bool) -> dict[str, Any]:
    root = root.resolve()
    report: list[dict[str, Any]] = []
    scanned = 0
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for filename in sorted(filenames):
            path = Path(current_root) / filename
            scanned += 1
            result = process_file(path, apply=apply, backup=backup)
            if result["action"] != "unchanged" and result["action"] != "skipped":
                report.append(result)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repository_root": str(root),
        "mode": "apply" if apply else "dry_run",
        "scanned_files": scanned,
        "files_requiring_review_or_change": len(report),
        "repository_config_live_execution_flags": "set false where recognized and applied",
        "sql_and_remote_services": "audit only; no remote changes performed",
        "files": report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit Verox for live financial execution flags and safely disable recognized repository settings."
    )
    parser.add_argument("--root", type=Path, default=Path("."),
                        help="Repository root (default: current directory).")
    parser.add_argument("--apply", action="store_true",
                        help="Write recognized configuration changes; default is dry-run.")
    parser.add_argument("--no-backup", action="store_true",
                        help="Do not create .bak backups when --apply is used.")
    args = parser.parse_args()
    if not args.root.is_dir():
        parser.error(f"repository root is not a directory: {args.root}")
    result = scan_repo(args.root, apply=args.apply, backup=not args.no_backup)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
