"""Restricted subprocess runner for small, pure generated Python functions.

This is a defense-in-depth runner, not a substitute for a hardened OS/container
sandbox. It accepts a deliberately small Python subset, runs with isolated Python
mode, no imports, no filesystem/network APIs, a timeout, and resource limits where
the operating system supports them.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_ALLOWED_NODES = (
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Expr,
    ast.Assign, ast.AnnAssign, ast.If, ast.IfExp, ast.Compare, ast.BoolOp,
    ast.And, ast.Or, ast.Not, ast.UnaryOp, ast.USub, ast.UAdd, ast.BinOp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot,
    ast.In, ast.NotIn, ast.Name, ast.Load, ast.Store, ast.Constant, ast.List,
    ast.Tuple, ast.Dict, ast.Set, ast.Subscript, ast.Slice, ast.ListComp,
    ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension, ast.For,
    ast.Break, ast.Continue, ast.Pass, ast.Call, ast.keyword,
)
_ALLOWED_CALLS = {"len", "min", "max", "sum", "sorted", "str", "int", "float",
                  "bool", "abs", "round", "enumerate", "range", "all", "any"}
_SAFE_BUILTINS = {name: getattr(__builtins__, name) for name in ()}

@dataclass(frozen=True)
class SandboxResult:
    success: bool
    output: Any = None
    error: str | None = None
    duration_ms: float = 0.0
    source_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

class SandboxRunner:
    def __init__(self, timeout_seconds: float = 3.0, memory_limit_mb: int = 128) -> None:
        if timeout_seconds <= 0 or memory_limit_mb < 32:
            raise ValueError("Timeout must be positive and memory limit at least 32 MB")
        self.timeout_seconds = timeout_seconds
        self.memory_limit_mb = memory_limit_mb

    def validate(self, source: str, function_name: str) -> tuple[bool, list[str]]:
        errors: list[str] = []
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return False, [f"Syntax error: {exc}"]
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        if not any(fn.name == function_name for fn in functions):
            errors.append(f"Expected a top-level function named {function_name!r}")
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED_NODES):
                errors.append(f"Disallowed syntax node: {type(node).__name__}")
                break
            if isinstance(node, ast.Attribute):
                errors.append("Attribute access is not permitted")
                break
            if isinstance(node, ast.Name) and node.id.startswith("__"):
                errors.append("Dunder names are not permitted")
                break
            if isinstance(node, ast.Call) and not (
                isinstance(node.func, ast.Name) and node.func.id in _ALLOWED_CALLS
            ):
                errors.append("Only approved pure built-in function calls are permitted")
                break
        return not errors, errors

    def run(self, source: str, function_name: str, args: list[Any] | None = None,
            kwargs: dict[str, Any] | None = None) -> SandboxResult:
        import hashlib
        import time
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        valid, errors = self.validate(source, function_name)
        if not valid:
            return SandboxResult(False, error="; ".join(errors), source_hash=digest)
        payload = json.dumps({"source": source, "function": function_name,
                              "args": args or [], "kwargs": kwargs or {}})
        runner = r'''
import ast, json, sys
data = json.loads(sys.stdin.read())
safe = {"len": len, "min": min, "max": max, "sum": sum, "sorted": sorted,
        "str": str, "int": int, "float": float, "bool": bool, "abs": abs,
        "round": round, "enumerate": enumerate, "range": range, "all": all, "any": any}
namespace = {"__builtins__": safe}
exec(compile(data["source"], "<generated-tool>", "exec"), namespace, namespace)
result = namespace[data["function"]](*data["args"], **data["kwargs"])
print(json.dumps({"result": result}, ensure_ascii=False, allow_nan=False))
'''
        start = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="verox-tool-") as tempdir:
            env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}
            kwargs_run: dict[str, Any] = {}
            if os.name == "posix":
                def limit_resources() -> None:
                    try:
                        import resource
                        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
                        memory = self.memory_limit_mb * 1024 * 1024
                        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
                        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
                        resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))
                    except (ImportError, OSError, ValueError):
                        pass
                kwargs_run["preexec_fn"] = limit_resources
            try:
                completed = subprocess.run(
                    [sys.executable, "-I", "-S", "-c", runner],
                    input=payload, text=True, capture_output=True, timeout=self.timeout_seconds,
                    cwd=tempdir, env=env, check=False, **kwargs_run,
                )
                duration = (time.perf_counter() - start) * 1000
                if completed.returncode != 0:
                    return SandboxResult(False, error=(completed.stderr.strip() or "Subprocess failed"),
                                         duration_ms=duration, source_hash=digest)
                result = json.loads(completed.stdout)
                return SandboxResult(True, output=result.get("result"),
                                     duration_ms=duration, source_hash=digest)
            except subprocess.TimeoutExpired:
                return SandboxResult(False, error="Execution timed out", 
                                     duration_ms=(time.perf_counter() - start) * 1000, source_hash=digest)
            except (json.JSONDecodeError, OSError) as exc:
                return SandboxResult(False, error=f"{type(exc).__name__}: {exc}",
                                     duration_ms=(time.perf_counter() - start) * 1000, source_hash=digest)
