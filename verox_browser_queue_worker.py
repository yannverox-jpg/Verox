"""Vercel Queues consumer that dispatches research jobs to the private Playwright service."""
from __future__ import annotations

import re
from typing import TypedDict
from urllib.parse import urljoin

import httpx
from vercel.queue import subscribe

TOPIC = "verox-browser-jobs"


class BrowserMessage(TypedDict):
    job_id: str


@subscribe(topic=TOPIC, max_attempts=3, max_concurrency=2)
async def process_browser_job(message: BrowserMessage) -> None:
    job_id = str(message.get("job_id", "")).strip()
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", job_id):
        raise ValueError("Queue message has no valid browser job id")

    import os
    base = os.getenv("VEROX_PLAYWRIGHT_WORKER_URL", "").strip()
    if not base:
        raise RuntimeError("Vercel service binding VEROX_PLAYWRIGHT_WORKER_URL is missing")

    endpoint = urljoin(base.rstrip("/") + "/", "internal/jobs/run")
    async with httpx.AsyncClient(timeout=httpx.Timeout(210.0, connect=10.0), trust_env=False) as client:
        response = await client.post(endpoint, json={"job_id": job_id})
        response.raise_for_status()
        outcome = response.json()
        if not isinstance(outcome, dict) or outcome.get("status") not in {
            "succeeded", "failed", "denied", "already_claimed_or_missing"
        }:
            raise RuntimeError("Playwright service returned an invalid job status")
