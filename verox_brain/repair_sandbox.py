"""Validate proposed code patches in a disposable, offline-by-default sandbox.

Only a narrow set of repository paths can be patched. The patch itself is data:
it is never interpreted as a shell command. Checks use fixed argv, fixed timeouts,
and a temporary copy; production files and the current branch are never modified.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_PATCH_BYTES = 250_000
ALLOWED_PREFIXES = ("verox_brain/", "api/")
DIFF_PATH = re.compile(r"^diff --git a/(.+) b/(.+)$", re.MULTILINE)
SECRET_PATH_PARTS = {".env", ".venv", "venv", ".git", "__pycache__", "node_modules"}


class PatchRejected(ValueError):
    """A patch is outside the explicitly permitted sandbox scope."""


def _validate_patch(patch: str) -> list[str]:
    if not patch.strip():
        raise PatchRejected("empty_patch")
    if len(patch.encode("utf-8")) > MAX_PATCH_BYTES:
        raise PatchRejected("patch_too_large")
    paths: list[str] = []
    for old, new in DIFF_PATH.findall(patch):
        if old == "/dev/null" or new == "/dev/null":
            raise PatchRejected("file_creation_or_deletion_not_allowed")
        for candidate in (old, new):
            pure = PurePosixPath(candidate)
            if pure.is_absolute() or ".." in pure.parts or not candidate.startswith(ALLOWED_PREFIXES):
                raise PatchRejected("path_outside_allowlist")
            if any(part in SECRET_PATH_PARTS for part in pure.parts):
                raise PatchRejected("sensitive_or_generated_path")
            if pure.suffix != ".py":
                raise PatchRejected("file_extension_not_allowlisted")
        if old != new:
            raise PatchRejected("rename_not_allowed")
        paths.append(new)
    if "--- /dev/null" in patch or "+++ /dev/null" in patch:
        raise PatchRejected("file_creation_or_deletion_not_allowed")
    if "GIT binary patch" in patch or "rename from " in patch or "rename to " in patch:
        raise PatchRejected("binary_patch_or_rename_not_allowed")
    if not paths:
        raise PatchRejected("unrecognized_unified_diff")
    if len(set(paths)) != len(paths):
        raise PatchRejected("duplicate_patch_path")
    return paths


def _copy_safe_tree(destination: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = set()
        for name in names:
            if name in SECRET_PATH_PARTS or name.startswith(".env") or name in {".pytest_cache", ".mypy_cache", "dist", "build"}:
                ignored.add(name)
        return ignored
    shutil.copytree(ROOT, destination, ignore=ignore, symlinks=False)


def _run(argv: list[str], cwd: Path, timeout: int = 90) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            check=False, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        combined = (result.stdout + "\n" + result.stderr)[-4000:]
        return {"return_code": result.returncode, "duration_seconds": round(time.monotonic() - started, 3),
                "summary": combined, "passed": result.returncode == 0}
    except subprocess.TimeoutExpired:
        return {"return_code": None, "duration_seconds": round(time.monotonic() - started, 3),
                "summary": "fixed validation command timed out", "passed": False}
    except OSError as exc:
        return {"return_code": 127, "duration_seconds": round(time.monotonic() - started, 3),
                "summary": f"command launch failed: {type(exc).__name__}", "passed": False}


def validate_patch(patch: str) -> dict[str, Any]:
    """Apply a reviewed-format diff only to a temporary copy and run fixed checks."""
    paths = _validate_patch(patch)
    with tempfile.TemporaryDirectory(prefix="verox-patch-lab-") as tmp:
        work = Path(tmp) / "repo"
        _copy_safe_tree(work)
        patch_file = Path(tmp) / "candidate.patch"
        patch_file.write_text(patch, encoding="utf-8")
        checks: list[dict[str, Any]] = []
        checks.append({"name": "temporary_repository_init", **_run(["git", "init", "-q"], work)})
        if not checks[-1]["passed"]:
            return {"schema_version": 1, "status": "rejected", "changed_paths": paths, "checks": checks, "production_modified": False, "patch_applied": False}
        # git apply is invoked with fixed arguments; patch content is never executed.
        checks.append({"name": "patch_check", **_run(["git", "apply", "--check", str(patch_file)], work)})
        if not checks[-1]["passed"]:
            return {"schema_version": 1, "status": "rejected", "changed_paths": paths, "checks": checks,
                    "production_modified": False, "patch_applied": False}
        checks.append({"name": "apply_to_temporary_copy", **_run(["git", "apply", str(patch_file)], work)})
        if not checks[-1]["passed"]:
            return {"schema_version": 1, "status": "rejected", "changed_paths": paths, "checks": checks,
                    "production_modified": False, "patch_applied": False}
        python_paths = [str(work / path) for path in paths if path.endswith(".py")]
        if python_paths:
            checks.append({"name": "compile_changed_python", **_run(
                [os.environ.get("PYTHON", "python"), "-m", "py_compile", *python_paths], work)})
        # Run repository-owned tests from the temporary copy; proposed patches
        # cannot alter tests, CI workflows, dependencies, or configuration.
        checks.append({"name": "repository_unit_tests", **_run(
            [os.environ.get("PYTHON", "python"), "-m", "unittest", "discover", "-s", "tests", "-v"],
            work, timeout=180)})
        passed = all(item["passed"] for item in checks)
        return {
            "schema_version": 1, "status": "validated_in_sandbox" if passed else "needs_review",
            "changed_paths": paths, "checks": checks,
            "production_modified": False, "patch_applied": True,
            "automatic_merge": False, "automatic_deploy": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a proposed Verox patch in a temporary copy.")
    parser.add_argument("--patch-file", required=True, help="Path to a unified diff file.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        patch = Path(args.patch_file).read_text(encoding="utf-8")
        report = validate_patch(patch)
    except (OSError, PatchRejected) as exc:
        report = {"schema_version": 1, "status": "rejected", "reason": str(exc),
                  "production_modified": False, "patch_applied": False}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "validated_in_sandbox" else 1


if __name__ == "__main__":
    raise SystemExit(main())
