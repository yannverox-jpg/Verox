"""Tool synthesis lifecycle with conservative static checks and honest capability states.

Generated Python is untrusted. AST checks are not a security boundary. This module
never executes generated code; runtime execution must happen in an OS/container sandbox.
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


@dataclass(frozen=True)
class RuntimeEvidence:
    """Structured report produced by an external sandbox/test runner."""
    runner_id: str
    sandboxed: bool
    tests_run: int
    tests_passed: int
    source_hash: str
    metrics: dict[str, float | int | str]

    def validate(self, expected_hash: str) -> None:
        if not self.runner_id.strip():
            raise ValueError("A runtime runner identifier is required")
        if not self.sandboxed:
            raise ValueError("Generated tools must be tested in an isolated sandbox")
        if self.tests_run < 1 or self.tests_passed != self.tests_run:
            raise ValueError("At least one test must run and every test must pass")
        if self.source_hash != expected_hash:
            raise ValueError("Runtime evidence does not match the validated source hash")
        if not self.metrics:
            raise ValueError("Measured runtime metrics are required")


class ToolSynthesizer:
    BLOCKED_CALLS = {"exec", "eval", "compile", "__import__", "open", "input", "breakpoint"}
    BLOCKED_NAMES = {"os", "sys", "subprocess", "socket", "pathlib", "shutil", "ctypes", "importlib"}

    def __init__(self, self_model: SelfModel, memory: MemoryStore,
                 code_generator: Callable[[str, str], str] | None = None) -> None:
        self.self_model = self_model
        self.memory = memory
        self.code_generator = code_generator
        self._validated_source: dict[str, str] = {}
        self._validated_versions: dict[str, int] = {}

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
        if not specification.strip():
            raise ValueError("Tool specification must not be empty")
        if self.code_generator is None:
            self.memory.record_event("tool_synthesis_blocked", {
                "tool_name": tool_name,
                "reason": "No code-generation provider configured; no code was fabricated.",
                "specification": specification,
            })
            return False

        source = self.code_generator(tool_name, specification)
        if not isinstance(source, str):
            raise TypeError("Code-generation provider must return Python source as a string")
        result = self.validate_source(tool_name, source)
        version = 1 + max(
            (item["version"] for item in self.memory.list_tools() if item["name"] == tool_name),
            default=0,
        )
        self.memory.record_tool(tool_name, version, source, result.source_hash,
                                "draft" if result.valid else "failed", {
            "valid": result.valid, "errors": list(result.errors),
            "function_names": list(result.function_names),
            "note": "Static validation only; runtime tests and isolated execution are still required.",
        })
        if not result.valid:
            logger.warning("Tool %s failed static validation: %s", tool_name, result.errors)
            return False

        self._validated_source[tool_name] = source
        self._validated_versions[tool_name] = version
        self.memory.record_event("tool_static_validation_passed", {
            "tool_name": tool_name, "version": version, "source_hash": result.source_hash,
        })
        return True

    def mark_available(self, tool_name: str, evidence: RuntimeEvidence) -> None:
        source = self._validated_source.get(tool_name)
        version = self._validated_versions.get(tool_name)
        if source is None or version is None:
            raise ValueError("Tool has not passed static validation in this process")
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        evidence.validate(digest)
        validation = {
            "runner_id": evidence.runner_id,
            "sandboxed": evidence.sandboxed,
            "tests_run": evidence.tests_run,
            "tests_passed": evidence.tests_passed,
            "source_hash": evidence.source_hash,
            "metrics": evidence.metrics,
        }
        self.memory.record_tool(tool_name, version, source, digest, "available", validation)
        self.self_model.acquire_capability(tool_name, f"Validated by {evidence.runner_id}", {
            "source_hash": digest, "version": version, "validation": validation,
        })
