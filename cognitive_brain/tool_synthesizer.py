"""Tool synthesis lifecycle with conservative static checks and honest capability states.

Generated Python is untrusted. The validator rejects common dangerous constructs, but
AST checks are not a security boundary. Do not execute generated code in the main process.
Production execution must use a separately configured OS/container sandbox.
"""
from __future__ import annotations

import ast
import hashlib
import logging
from dataclasses import dataclass
from typing import Callable

from .memory_store import MemoryStore
from .self_model import SelfModel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    source_hash: str
    errors: tuple[str, ...]
    function_names: tuple[str, ...]


class ToolSynthesizer:
    BLOCKED_CALLS = {"exec", "eval", "compile", "__import__", "open", "input", "breakpoint"}
    BLOCKED_NAMES = {"os", "sys", "subprocess", "socket", "pathlib", "shutil", "ctypes", "importlib"}

    def __init__(self, self_model: SelfModel, memory: MemoryStore,
                 code_generator: Callable[[str, str], str] | None = None) -> None:
        self.self_model = self_model
        self.memory = memory
        self.code_generator = code_generator
        self._validated_source: dict[str, str] = {}

    def diagnose_missing_capability(self, required_capability: str) -> bool:
        return not self.self_model.has_capability(required_capability)

    def validate_source(self, tool_name: str, source: str) -> ValidationResult:
        errors: list[str] = []
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return ValidationResult(False, digest, (f"Syntax error: {exc}",), ())

        functions = tuple(node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
        if tool_name not in functions:
            errors.append(f"Expected a top-level function named '{tool_name}'")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                errors.append("Imports are not permitted in the initial generated-tool profile")
                break
            if isinstance(node, ast.Name) and node.id in self.BLOCKED_NAMES:
                errors.append(f"Blocked name: {node.id}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in self.BLOCKED_CALLS:
                errors.append(f"Blocked call: {node.func.id}")
            if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                errors.append(f"Blocked dunder attribute: {node.attr}")
        return ValidationResult(not errors, digest, tuple(dict.fromkeys(errors)), functions)

    def design_and_synthesize_tool(self, tool_name: str, specification: str) -> bool:
        if not tool_name.isidentifier() or tool_name.startswith("_"):
            raise ValueError("Tool name must be a public Python identifier")
        if self.code_generator is None:
            self.memory.record_event("tool_synthesis_blocked", {
                "tool_name": tool_name,
                "reason": "No code-generation provider configured; no code was fabricated.",
                "specification": specification,
            })
            return False

        source = self.code_generator(tool_name, specification)
        result = self.validate_source(tool_name, source)
        version = 1 + max(
            (item["version"] for item in self.memory.list_tools() if item["name"] == tool_name),
            default=0,
        )
        status = "draft" if result.valid else "failed"
        self.memory.record_tool(tool_name, version, source, result.source_hash, status, {
            "valid": result.valid, "errors": list(result.errors),
            "function_names": list(result.function_names),
            "note": "Static validation only; runtime tests and isolated execution are still required.",
        })
        if not result.valid:
            logger.warning("Tool %s failed static validation: %s", tool_name, result.errors)
            return False

        self._validated_source[tool_name] = source
        self.memory.record_event("tool_static_validation_passed", {
            "tool_name": tool_name, "version": version, "source_hash": result.source_hash,
        })
        # Static validation alone does not grant the capability. A separate runtime
        # sandbox/test stage must call mark_available after successful measured tests.
        return True

    def mark_available(self, tool_name: str, evidence: str, version: int = 1) -> None:
        if tool_name not in self._validated_source:
            raise ValueError("Tool has not passed static validation in this process")
        if not evidence.strip():
            raise ValueError("Measured validation evidence is required")
        source = self._validated_source[tool_name]
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        self.memory.record_tool(tool_name, version, source, digest, "available", {
            "evidence": evidence, "runtime_validation": "reported_by_validation_caller",
        })
        self.self_model.acquire_capability(tool_name, evidence, {"source_hash": digest, "version": version})
