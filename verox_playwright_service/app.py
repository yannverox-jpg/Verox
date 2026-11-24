"""Private Vercel container endpoint for queued, public, read-only browser jobs.

This service has no public rewrite. Only the Verox Vercel service binding can reach it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from verox_brain.browser_worker import BrowserWorker
from verox_brain.data_plane import SupabaseDataPlane

MAX_BODY = 2_000


def _run(job_id: str) -> dict:
    store = SupabaseDataPlane()
    worker = BrowserWorker(store)
    jobs = worker._rest(
        "GET",
        "verox_browser_jobs",
        params={
            "id": f"eq.{job_id}",
            "status": "eq.queued",
            "select": "id,task_type,source_id,policy_mode,input,requested_capabilities",
            "limit": "1",
        },
    ) or []
    if not jobs:
        return {"status": "already_claimed_or_missing"}
    now = datetime.now(timezone.utc).isoformat()
    claimed = worker._rest(
        "PATCH",
        "verox_browser_jobs",
        params={"id": f"eq.{job_id}", "status": "eq.queued"},
        body={
            "status": "running",
            "started_at": now,
            "audit": {"worker": "verox-playwright-readonly-v1", "runtime": "vercel-container", "claimed_at": now},
        },
    ) or []
    if not claimed:
        return {"status": "already_claimed_or_missing"}
    result = worker.run_job(claimed[0])
    return {
        "status": str(result.get("status", "unknown")),
        "analysis_run_id": str((claimed[0].get("input") or {}).get("analysis_run_id", ""))[:64],
        "reason": str(result.get("reason", ""))[:160],
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/internal/jobs/run":
            self._json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > MAX_BODY:
                self._json(400, {"error": "invalid_body_size"})
                return
            payload = json.loads(self.rfile.read(length))
            job_id = str(payload.get("job_id", "")).strip()
            import re
            if not re.fullmatch(r"[0-9a-fA-F-]{36}", job_id):
                self._json(400, {"error": "invalid_job_id"})
                return
            result = _run(job_id)
            self._json(200, result)
        except Exception as exc:
            self._json(503, {"error": f"worker_unavailable:{type(exc).__name__}"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))), Handler).serve_forever()
