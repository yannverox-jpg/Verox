"""Repository reconnaissance for Verox's engineering runtime.

Builds a deterministic inventory of Python modules, imports, API routes,
environment-variable references, workflow commands, and declared dependencies.
It performs static analysis only: it never imports project modules or executes
repository code.
"""
from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache"}
ENV_PATTERN = re.compile(r"os\.(?:environ\[\s*['\"]([^'\"]+)['\"]\s*\]|getenv\(\s*['\"]([^'\"]+)['\"])")
ROUTE_PATTERN = re.compile(r"@(?:app|router)\.(get|post|put|patch|delete|options|head)\(\s*['\"]([^'\"]+)['\"]", re.I)
WORKFLOW_PATTERN = re.compile(r"run:\s*(.+)$", re.M)


def _python_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if not any(part in EXCLUDED_DIRS for part in p.parts)
    )


def _declared_dependencies(root: Path) -> set[str]:
    dependencies: set[str] = set()
    requirements = root / "requirements.txt"
    if requirements.exists():
        for line in requirements.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "-")):
                name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip()
                if name:
                    dependencies.add(re.sub(r"[-_.]+", "-", name).lower())
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            for item in data.get("project", {}).get("dependencies", []):
                name = re.split(r"[<>=!~\[]", item, maxsplit=1)[0].strip()
                if name:
                    dependencies.add(re.sub(r"[-_.]+", "-", name).lower())
        except (tomllib.TOMLDecodeError, OSError):
            pass
    return dependencies


def inspect_repository(root: Path | str = ROOT) -> dict[str, Any]:
    root = Path(root).resolve()
    files = _python_files(root)
    module_paths = {
        ".".join(path.relative_to(root).with_suffix("").parts)
        for path in files
    }
    top_level_modules = {name.split(".", 1)[0] for name in module_paths}
    imports: list[dict[str, Any]] = []
    parse_errors: list[dict[str, str]] = []
    routes: list[dict[str, str]] = []
    env_names: set[str] = set()
    for path in files:
        rel = str(path.relative_to(root))
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError as exc:
            parse_errors.append({"file": rel, "error": f"syntax_error_line_{exc.lineno or 0}"})
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({"file": rel, "module": alias.name, "line": node.lineno})
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append({"file": rel, "module": node.module, "line": node.lineno})
        for match in ENV_PATTERN.finditer(source):
            env_names.add(match.group(1) or match.group(2))
        for match in ROUTE_PATTERN.finditer(source):
            routes.append({"file": rel, "method": match.group(1).upper(), "path": match.group(2)})

    declared = _declared_dependencies(root)
    import_roots = {row["module"].split(".", 1)[0] for row in imports}
    known_stdlib = set(getattr(__import__("sys"), "stdlib_module_names", set()))
    external_roots = sorted(
        name for name in import_roots
        if name not in known_stdlib and name not in top_level_modules
    )
    normalized_declared = {re.sub(r"[-_.]+", "-", x).lower() for x in declared}
    # Mapping from common import names to distribution names.
    aliases = {"yaml": "pyyaml", "cv2": "opencv-python", "PIL": "pillow", "bs4": "beautifulsoup4"}
    undeclared = sorted(
        name for name in external_roots
        if re.sub(r"[-_.]+", "-", aliases.get(name, name)).lower() not in normalized_declared
    )
    workflows = []
    workflow_dir = root / ".github" / "workflows"
    if workflow_dir.exists():
        for path in sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml")):
            content = path.read_text(encoding="utf-8", errors="replace")
            workflows.append({
                "file": str(path.relative_to(root)),
                "run_steps": [line.strip() for line in WORKFLOW_PATTERN.findall(content)],
                "referenced_paths": sorted(set(re.findall(r"(?:python\s+-m\s+([\w.]+)|(?:python|pytest)\s+([\w./*-]+\.py))", content))),
            })
    return {
        "schema_version": 1,
        "root": str(root),
        "counts": {
            "python_files": len(files),
            "imports": len(imports),
            "routes": len(routes),
            "environment_variables": len(env_names),
            "parse_errors": len(parse_errors),
            "external_import_roots": len(external_roots),
            "possibly_undeclared_dependencies": len(undeclared),
        },
        "python_files": [str(path.relative_to(root)) for path in files],
        "imports": imports,
        "routes": sorted(routes, key=lambda x: (x["file"], x["path"], x["method"])),
        "environment_variables": sorted(env_names),
        "declared_dependencies": sorted(declared),
        "external_import_roots": external_roots,
        "possibly_undeclared_dependencies": undeclared,
        "parse_errors": parse_errors,
        "workflows": workflows,
        "limitations": [
            "Static inventory only; it does not prove runtime reachability or dependency compatibility.",
            "Possibly undeclared imports require review because import names and package distribution names can differ.",
            "Environment-variable names are listed, but values are never read or reported.",
        ],
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Static reverse-engineering inventory for Verox.")
    parser.add_argument("--json", action="store_true", help="Emit full JSON inventory.")
    args = parser.parse_args()
    report = inspect_repository()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"counts": report["counts"], "possibly_undeclared_dependencies": report["possibly_undeclared_dependencies"], "parse_errors": report["parse_errors"]}, ensure_ascii=False, indent=2))
    return 1 if report["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
