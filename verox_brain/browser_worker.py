"""Isolated, read-only Playwright worker for registered public sources.

Run in a separate worker/container, never in the user's personal browser:
python -m verox_brain.browser_worker --once

Download jobs are intentionally held for approval until a reviewed allowlist
implementation exists. No persistent browser profiles or account sessions.
"""
from __future__ import annotations
import argparse, json, time
from datetime import datetime, timezone
from urllib.parse import urlparse
import httpx
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
from verox_brain.data_plane import DataPlaneConfigurationError, DataPlaneError, SupabaseDataPlane
from verox_brain.source_registry import get_source

POLL_SECONDS = 4
MAX_JOBS_PER_CYCLE = 5
MAX_PAGE_TEXT = 18_000
ALLOWED_JOB_TYPES = {"inspect_page", "collect_public_data", "test_connector", "capture_evidence"}

def _host(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only credential-free HTTPS URLs are allowed.")
    return parsed.hostname.lower()

def _now():
    return datetime.now(timezone.utc).isoformat()

class BrowserWorker:
    def __init__(self, store=None):
        self.store = store or SupabaseDataPlane()

    def _rest(self, method, path, *, params=None, body=None):
        headers = {"apikey": self.store.service_key, "Authorization": f"Bearer {self.store.service_key}", "Content-Type": "application/json", "Accept": "application/json"}
        if method == "PATCH": headers["Prefer"] = "return=representation"
        try:
            with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
                response = client.request(method, f"{self.store.base_url}/rest/v1/{path}", params=params, json=body, headers=headers)
                response.raise_for_status()
                return response.json() if response.content else None
        except httpx.HTTPError as exc:
            raise DataPlaneError(f"Browser job store failed ({type(exc).__name__}).") from None

    def claim_next(self):
        jobs = self._rest("GET", "verox_browser_jobs", params={"select":"id,task_type,source_id,policy_mode,input,requested_capabilities","status":"eq.queued","order":"created_at.asc","limit":"10"}) or []
        for job in jobs:
            if not isinstance(job, dict) or not job.get("id"): continue
            claimed = self._rest("PATCH", "verox_browser_jobs", params={"id":f"eq.{job['id']}","status":"eq.queued"}, body={"status":"running","started_at":_now(),"audit":{"worker":"verox-playwright-readonly-v1","claimed_at":_now()}})
            if claimed: return claimed[0]
        return None

    def _finish(self, job_id, status, *, audit, error=None, artifacts=None):
        self._rest("PATCH", "verox_browser_jobs", params={"id":f"eq.{job_id}"}, body={"status":status,"completed_at":_now(),"audit":audit,"artifact_refs":artifacts or [],"error":error})

    def run_job(self, job):
        job_id, task_type, source_id = str(job.get("id","")), str(job.get("task_type","")), str(job.get("source_id") or "")
        source = get_source(source_id)
        if task_type not in ALLOWED_JOB_TYPES:
            status = "needs_approval" if task_type == "download_public_file" else "failed"
            self._finish(job_id, status, error="job_type_not_allowlisted", audit={"policy":"read_only","decision":"denied"})
            return {"status":"denied","reason":"job_type_not_allowlisted"}
        if not source or source.get("access") not in {"web","public_web","web_or_api"}:
            self._finish(job_id,"failed",error="source_not_public_allowlisted",audit={"policy":"read_only","decision":"denied"})
            return {"status":"denied","reason":"source_not_public_allowlisted"}
        target_url = str((job.get("input") or {}).get("url") or source["url"])
        try:
            if _host(target_url) != _host(source["url"]): raise ValueError("target_host_not_registered")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(accept_downloads=False, service_workers="block", java_script_enabled=True, ignore_https_errors=False)
                page = context.new_page()
                page.set_default_navigation_timeout(12_000)
                page.set_default_timeout(5_000)
                response = page.goto(target_url, wait_until="domcontentloaded")
                final_url = page.url
                if _host(final_url) != _host(source["url"]): raise ValueError("cross_host_navigation_denied")
                title = page.title()[:300]
                text = " ".join(page.locator("body").inner_text(timeout=5_000).split())[:MAX_PAGE_TEXT]
                status_code = response.status if response else None
                content_type = response.headers.get("content-type","") if response else ""
                context.close()
                browser.close()
            evidence = self.store.save_observation(source_id=source_id,text=text,url=final_url,content_type=content_type,metadata={"title":title,"http_status":status_code,"task_type":task_type,"worker":"verox-playwright-readonly-v1"})
            self._finish(job_id,"succeeded",audit={"policy":"read_only","decision":"completed","final_url":final_url,"captured_at":_now(),"text_chars":len(text)},artifacts=[{"type":"observation",**evidence}])
            return {"status":"succeeded","observation":evidence}
        except (ValueError, PlaywrightTimeoutError) as exc:
            safe_error = str(exc)[:120] if isinstance(exc, ValueError) else "navigation_timeout"
            self._finish(job_id,"failed",error=safe_error,audit={"policy":"read_only","decision":"failed"})
            return {"status":"failed","reason":safe_error}
        except Exception as exc:
            safe_error = f"worker_error:{type(exc).__name__}"
            self._finish(job_id,"failed",error=safe_error,audit={"policy":"read_only","decision":"failed"})
            return {"status":"failed","reason":safe_error}

    def run_once(self):
        results = []
        for _ in range(MAX_JOBS_PER_CYCLE):
            job = self.claim_next()
            if not job: break
            results.append(self.run_job(job))
        return {"processed":len(results),"results":results}

    def run_forever(self):
        while True:
            try: self.run_once()
            except (DataPlaneConfigurationError, DataPlaneError): pass
            time.sleep(POLL_SECONDS)

def main():
    parser = argparse.ArgumentParser(description="Verox read-only browser worker")
    parser.add_argument("--once",action="store_true",help="Process up to five queued jobs and exit")
    args = parser.parse_args()
    worker = BrowserWorker()
    if args.once: print(json.dumps(worker.run_once(),ensure_ascii=False))
    else: worker.run_forever()

if __name__ == "__main__":
    main()
