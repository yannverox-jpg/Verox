"""Tool synthesis lifecycle with static validation and measured isolated tests.

Generated Python is untrusted. It is never executed in the main process. The built-in
runner permits only a small pure-Python subset and runs in a resource-limited child
process; high-risk workloads still require a hardened OS/container sandbox.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Callable

from .memory_store import MemoryStore
from .self_model import SelfModel
from .sandbox.execution_environment import SandboxRunner

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    source_hash: str
    errors: tuple[str, ...]
    function_names: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeEvidence:
    """Measured report generated from actual isolated test executions."""
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
            raise ValueError("Generated tools must be tested in an isolated runner")
        if self.tests_run < 1 or self.tests_passed != self.tests_run:
            raise ValueError("At least one test must run and every test must pass")
        if self.source_hash != expected_hash:
            raise ValueError("Runtime evidence does not match the validated source hash")
        if not self.metrics:
            raise ValueError("Measured runtime metrics are required")


class ToolSynthesizer:
    def __init__(self, self_model: SelfModel, memory: MemoryStore,
                 code_generator: Callable[[str, str], str] | None = None,
                 sandbox_runner: SandboxRunner | None = None) -> None:
        self.self_model = self_model
        self.memory = memory
        self.code_generator = code_generator
        self.sandbox_runner = sandbox_runner or SandboxRunner()
        self._validated_source: dict[str, str] = {}
        self._validated_versions: dict[str, int] = {}

    def diagnose_missing_capability(self, required_capability: str) -> bool:
        return not self.self_model.has_capability(required_capability)

    def validate_source(self, tool_name: str, source: str) -> ValidationResult:
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        valid, errors = self.sandbox_runner.validate(source, tool_name)
        try:
            import ast
            tree = ast.parse(source)
            functions = tuple(node.name for node in tree.body if isinstance(node, ast.FunctionDef))
        except SyntaxError:
            functions = ()
        return ValidationResult(valid, digest, tuple(errors), functions)

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
            "note": "Static validation only; runtime tests are still required.",
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

    def validate_in_sandbox(self, tool_name: str, test_cases: list[dict[str, Any]]) -> dict[str, Any]:
        """Run explicit input/expected-output cases and register only fully passing tools.

        Each test case is {"args": [...], "kwargs": {...}, "expected": value}.
        No tool is marked available when a test fails or no test cases are provided.
        """
        source = self._validated_source.get(tool_name)
        version = self._validated_versions.get(tool_name)
        if source is None or version is None:
            raise ValueError("Tool has not passed static validation in this process")
        if not test_cases:
            raise ValueError("At least one test case is required")

        results = []
        total_duration = 0.0
        for index, case in enumerate(test_cases):
            if not isinstance(case, dict) or "expected" not in case:
                raise ValueError(f"Test case {index} must include expected output")
            run = self.sandbox_runner.run(
                source, tool_name, args=case.get("args", []), kwargs=case.get("kwargs", {})
            )
            total_duration += run.duration_ms
            passed = run.success and run.output == case["expected"]
            results.append({
                "index": index, "passed": passed, "actual": run.output,
                "expected": case["expected"], "error": run.error,
                "duration_ms": run.duration_ms,
            })

        passed_count = sum(1 for item in results if item["passed"])
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        evidence = RuntimeEvidence(
            runner_id="cognitive_brain.sandbox.SandboxRunner",
            sandboxed=True,
            tests_run=len(results),
            tests_passed=passed_count,
            source_hash=digest,
            metrics={"total_duration_ms": round(total_duration, 3),
                     "average_duration_ms": round(total_duration / len(results), 3),
                     "test_cases": len(results)},
        )
        if passed_count == len(results):
            self.mark_available(tool_name, evidence)
        else:
            self.memory.record_tool(tool_name, version, source, digest, "failed", {
                "tests_run": len(results), "tests_passed": passed_count, "results": results,
                "runner_id": evidence.runner_id,
            })
            self.memory.record_event("tool_runtime_validation_failed", {
                "tool_name": tool_name, "version": version, "results": results,
            })
        return {"tool_name": tool_name, "version": version, "tests_run": len(results),
                "tests_passed": passed_count, "available": passed_count == len(results),
                "results": results, "metrics": evidence.metrics}

    def mark_available(self, tool_name: str, evidence: RuntimeEvidence) -> None:
        source = self._validated_source.get(tool_name)
        version = self._validated_versions.get(tool_name)
        if source is None or version is None:
            raise ValueError("Tool has not passed static validation in this process")
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        evidence.validate(digest)
        validation = {
            "runner_id": evidence.runner_id, "sandboxed": evidence.sandboxed,
            "tests_run": evidence.tests_run, "tests_passed": evidence.tests_passed,
            "source_hash": evidence.source_hash, "metrics": evidence.metrics,
        }
        self.memory.record_tool(tool_name, version, source, digest, "available", validation)
        self.self_model.acquire_capability(tool_name, f"Validated by {evidence.runner_id}", {
            "source_hash": digest, "version": version, "validation": validation,
        })
