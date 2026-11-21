"""Bounded self-diagnosis and recovery loop for Verox.

The runtime only executes fixed, repository-owned checks. Model output is advisory:
it is never executed as a shell command or applied as a code patch. This makes the
repair loop safe to run unattended while preserving an auditable failure report.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
MAX_ATTEMPTS = 2
CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("compile_core", (sys.executable, "-m", "py_compile",
        "api/index.py", "api/browser_jobs.py", "api/research.py",
        "verox_brain/data_plane.py", "verox_brain/browser_worker.py",
        "verox_brain/model_client.py", "verox_brain/behavioral_analysis.py",
        "verox_brain/research_pipeline.py", "verox_brain/self_repair.py", "verox_brain/recon.py", "verox_brain/causal_lab.py", "verox_brain/repair_sandbox.py", "verox_brain/platform_probe.py")),
    ("test_data_plane", (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_data_plane.py", "-v")),
    ("test_browser_policy", (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_browser_worker.py", "-v")),
    ("test_behavioral_analysis", (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_behavioral_analysis.py", "-v")),
    ("test_research_pipeline", (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_research_pipeline.py", "-v")),
    ("test_self_repair", (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_self_repair.py", "-v")),
    ("test_repository_recon", (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_recon.py", "-v")),
    ("test_causal_lab", (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_causal_lab.py", "-v")),
    ("test_repair_sandbox", (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_repair_sandbox.py", "-v")),
    ("test_platform_probe", (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_platform_probe.py", "-v")),
)

@dataclass
class CheckResult:
    name: str
    status: str
    attempt: int
    duration_seconds: float
    return_code: int | None
    summary: str


def classify_failure(output: str, return_code: int | None) -> str:
    """Return a deterministic, secret-free failure category."""
    text = (output or "").lower()
    if "modulenotfounderror" in text or "no module named" in text:
        return "dependency_missing"
    if "syntaxerror" in text or "indentationerror" in text:
        return "python_syntax_error"
    if "assertionerror" in text or "failed" in text:
        return "test_failure"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "connection" in text or "network" in text or "http 429" in text or "http 5" in text:
        return "external_service_or_network"
    if return_code is None:
        return "process_timeout"
    return "unknown_failure"


def _safe_summary(output: str, limit: int = 1600) -> str:
    """Avoid printing environment values or request headers in CI logs."""
    lines = []
    for line in (output or "").splitlines()[-20:]:
        low = line.lower()
        if any(marker in low for marker in ("authorization:", "bearer ", "api_key=", "service_role_key", "password=")):
            lines.append("[redacted sensitive diagnostic line]")
        else:
            lines.append(line[:300])
    return "\n".join(lines)[-limit:]


def _run_check(name: str, argv: Sequence[str], *, timeout: int = 120) -> tuple[int | None, str, float]:
    start = time.monotonic()
    try:
        proc = subprocess.run(
            list(argv), cwd=ROOT, capture_output=True, text=True,
            timeout=timeout, check=False, env=os.environ.copy(),
        )
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return proc.returncode, output, round(time.monotonic() - start, 3)
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return None, output + "\ncheck timed out", round(time.monotonic() - start, 3)
    except OSError as exc:
        return 127, f"Process launch failed: {type(exc).__name__}", round(time.monotonic() - start, 3)


def run_recovery_cycle(*, max_attempts: int = MAX_ATTEMPTS, retry_delay: float = 1.0) -> dict:
    """Run fixed checks, retry failures once, and emit actionable diagnosis.

    No package installation, arbitrary shell, source mutation, external write, or
    production deployment is performed by this routine.
    """
    attempts = max(1, min(int(max_attempts), 3))
    results: list[CheckResult] = []
    unresolved: list[dict] = []
    for name, argv in CHECKS:
        final_result = None
        for attempt in range(1, attempts + 1):
            code, output, duration = _run_check(name, argv)
            status = "passed" if code == 0 else "failed"
            summary = "check passed" if code == 0 else _safe_summary(output)
            final_result = CheckResult(name, status, attempt, duration, code, summary)
            results.append(final_result)
            if code == 0:
                break
            if attempt < attempts:
                time.sleep(max(0.0, min(retry_delay, 5.0)))
        if final_result and final_result.status != "passed":
            category = classify_failure(final_result.summary, final_result.return_code)
            unresolved.append({
                "check": name,
                "category": category,
                "attempts": final_result.attempt,
                "next_action": _next_action(category),
                "evidence": final_result.summary,
            })

    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "bounded_diagnostic_recovery",
        "status": "healthy" if not unresolved else "needs_attention",
        "checks_passed": sum(r.status == "passed" for r in results if r.attempt == 1 or r.status == "passed"),
        "checks_failed": len(unresolved),
        "attempts": [asdict(r) for r in results],
        "unresolved": unresolved,
        "safety": {
            "read_only_external_access": True,
            "source_mutation": False,
            "arbitrary_shell": False,
            "automatic_dependency_install": False,
            "secrets_in_report": False,
        },
    }
    return report


def _next_action(category: str) -> str:
    actions = {
        "dependency_missing": "Compare the missing import with pinned requirements; install only after dependency review.",
        "python_syntax_error": "Prepare a minimal patch, run compilation and the full targeted test suite before proposing it.",
        "test_failure": "Inspect the failing assertion and related implementation; create a minimal patch and rerun the test.",
        "timeout": "Inspect the slow check and set a bounded timeout or remove the blocking operation.",
        "process_timeout": "Inspect process health and resource limits; do not loop indefinitely.",
        "external_service_or_network": "Check service status and retry budget; preserve the failure as unverified.",
        "unknown_failure": "Inspect the captured diagnostic and create a minimal, test-backed remediation proposal.",
    }
    return actions.get(category, actions["unknown_failure"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Verox's bounded self-diagnostic cycle.")
    parser.add_argument("--attempts", type=int, default=MAX_ATTEMPTS, help="Attempts per fixed check (1-3).")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable report.")
    args = parser.parse_args()
    report = run_recovery_cycle(max_attempts=args.attempts)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Verox self-diagnostic: {report['status']}")
        print(f"Checks en échec non résolus: {report['checks_failed']}")
        for item in report["unresolved"]:
            print(f"- {item['check']}: {item['category']} — {item['next_action']}")
    return 0 if report["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
