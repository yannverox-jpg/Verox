"""Isolated, read-only Playwright worker for registered public sources.

Run in a separate worker/container:
python -m verox_brain.browser_worker --once

Download jobs are intentionally held for approval until a reviewed allowlist
implementation exists. No persistent browser profiles or account sessions.
"""
from __future__ import annotations
import argparse, json, os, time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import httpx
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
from verox_brain.data_plane import DataPlaneConfigurationError, DataPlaneError, SupabaseDataPlane
from verox_brain.research_pipeline import complete_research_run
from verox_brain.observation_quality import validate_observation
from verox_brain.source_registry import get_source, list_sources

POLL_SECONDS = 4
MAX_JOBS_PER_CYCLE = 3
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
        if method in {"POST", "PATCH"}: headers["Prefer"] = "return=representation"
        try:
            with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
                response = client.request(method, f"{self.store.base_url}/rest/v1/{path}", params=params, json=body, headers=headers)
                response.raise_for_status()
                return response.json() if response.content else None
        except httpx.HTTPError as exc:
            raise DataPlaneError(f"Browser job store failed ({type(exc).__name__}).") from None

    def _call_brain(self, payload):
        token = os.getenv("VEROX_INTERNAL_TASK_TOKEN", "").strip()
        endpoint = os.getenv("VEROX_BRAIN_ANALYZE_URL", "https://verox-brain.vercel.app/api/analyze-observation").strip()
        if not token:
            raise RuntimeError("ai_preflight_missing_internal_token")
        if not endpoint.startswith("https://"):
            raise RuntimeError("ai_endpoint_must_use_https")
        try:
            with httpx.Client(timeout=35, follow_redirects=False, trust_env=False) as client:
                response = client.post(endpoint, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"ai_endpoint_http_{exc.response.status_code}") from None
        except (httpx.HTTPError, ValueError):
            raise RuntimeError("ai_endpoint_unavailable_or_invalid_response") from None
        if not isinstance(data, dict) or data.get("model_ready") is not True:
            raise RuntimeError("ai_model_not_ready")
        if payload.get("action") == "analyze" and not isinstance(data.get("analysis"), dict) or (payload.get("action") == "analyze" and not all(data.get(k) for k in ("analysis", "model_used", "provider_used"))):
            raise RuntimeError("ai_analysis_incomplete")
        return data

    def preflight_ai(self):
        result = self._call_brain({"action": "preflight"})
        if result.get("status") != "ready":
            raise RuntimeError("ai_preflight_failed")
        return {"status": "ready", "provider": result.get("provider_used"), "model": result.get("model_used")}

    def claim_next(self):
        jobs = self._rest("GET", "verox_browser_jobs", params={"select":"id,task_type,source_id,policy_mode,input,requested_capabilities", "status":"eq.queued", "order":"created_at.asc", "limit":"10"}) or []
        for job in jobs:
            if not isinstance(job, dict) or not job.get("id"): continue
            claimed = self._rest("PATCH", "verox_browser_jobs", params={"id":f"eq.{job['id']}", "status":"eq.queued"}, body={"status":"running", "started_at":_now(), "audit":{"worker":"verox-playwright-readonly-v1", "claimed_at":_now()}})
            if claimed: return claimed[0]
        return None

    def _finish(self, job_id, status, *, audit, error=None, artifacts=None):
        self._rest("PATCH", "verox_browser_jobs", params={"id":f"eq.{job_id}"}, body={"status":status, "completed_at":_now(), "audit":audit, "artifact_refs":artifacts or [], "error":error})

    def seed_public_cycle(self, *, limit=5, cooldown_minutes=60):
        """Queue a bounded rotation of registered public sources for background research."""
        registry = list_sources()
        candidates = [
            source for source in registry.get("sources", [])
            if source.get("access") in {"web", "public_web", "web_or_api"}
        ]
        if not candidates:
            return {"queued": 0, "sources": []}
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(minutes=max(cooldown_minutes, 240))).isoformat()
        ids = [str(source["id"]) for source in candidates]
        recent = self._rest(
            "GET", "verox_browser_jobs",
            params={
                "select": "source_id,status,created_at,audit",
                "source_id": "in.(" + ",".join(ids) + ")",
                "created_at": "gte." + cutoff,
                "limit": "500",
            },
        ) or []
        seen = set()
        for row in recent:
            if not isinstance(row, dict) or not row.get("source_id"):
                continue
            created_raw = row.get("created_at")
            try:
                created = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            audit = row.get("audit") if isinstance(row.get("audit"), dict) else {}
            try:
                source_cooldown = max(cooldown_minutes, min(240, int(audit.get("cooldown_minutes", 0) or 0)))
            except (TypeError, ValueError):
                source_cooldown = cooldown_minutes
            if now - created < timedelta(minutes=source_cooldown):
                seen.add(str(row["source_id"]))
        queued = []
        for source in candidates:
            if source["id"] in seen:
                continue
            self.store.register_source(source)
            record = {
                "task_type": "collect_public_data",
                "source_id": source["id"],
                "status": "queued",
                "policy_mode": "read_only",
                "requested_capabilities": ["navigate", "read_text"],
                "input": {"url": source["url"], "request_reason": "scheduled_public_research_cycle"},
                "artifact_refs": [],
                "audit": {"created_by": "scheduled_worker", "policy": "read_only", "cycle": "public_source_rotation"},
            }
            rows = self._rest("POST", "verox_browser_jobs", body=record)
            if rows:
                queued.append(source["id"])
                seen.add(source["id"])
            if len(queued) >= max(1, min(int(limit), 5)):
                break
        return {"queued": len(queued), "sources": queued, "cooldown_minutes": cooldown_minutes}

    def run_job(self, job):
        job_id, task_type, source_id = str(job.get("id","")), str(job.get("task_type","")), str(job.get("source_id") or "")
        source = get_source(source_id)
        if task_type not in ALLOWED_JOB_TYPES:
            status = "needs_approval" if task_type == "download_public_file" else "failed"
            self._finish(job_id, status, error="job_type_not_allowlisted", audit={"policy":"read_only", "decision":"denied"})
            return {"status":"denied", "reason":"job_type_not_allowlisted"}
        if not source or source.get("access") not in {"web", "public_web", "web_or_api"}:
            self._finish(job_id,"failed",error="source_not_public_allowlisted",audit={"policy":"read_only","decision":"denied"})
            return {"status":"denied","reason":"source_not_public_allowlisted"}
        target_url = str((job.get("input") or {}).get("url") or source["url"])
        try:
            if _host(target_url) != _host(source["url"]): raise ValueError("target_host_not_registered")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(accept_downloads=False, service_workers="block", java_script_enabled=False, ignore_https_errors=False)
                allowed_host = _host(source["url"])
                def guard_route(route):
                    try:
                        request_url = route.request.url
                        parsed_request = urlparse(request_url)
                        if parsed_request.scheme == "https" and parsed_request.hostname and parsed_request.hostname.lower() == allowed_host:
                            route.continue_()
                        else:
                            route.abort()
                    except Exception:
                        route.abort()
                context.route("**/*", guard_route)
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
            captured_at = _now()
            quality = validate_observation(source_id, status_code, title, text)
            evidence = self.store.save_observation(
                source_id=source_id, text=quality.clean_text or text, url=final_url,
                content_type=content_type,
                metadata={"title":title,"http_status":status_code,"task_type":task_type,
                          "worker":"verox-playwright-readonly-v1","captured_at":captured_at,
                          "collection_status":quality.status,"quality_reason":quality.reason,
                          "cooldown_minutes":quality.cooldown_minutes},
            )
            run_id = str((job.get("input") or {}).get("analysis_run_id") or "")
            analysis = None
            if quality.status != "collected_valid":
                if run_id:
                    self.store._request("PATCH", "verox_analysis_runs",
                        params={"id":f"eq.{run_id}"},
                        body={"status":"failed","input_observation_ids":[evidence["observation_id"]] if evidence.get("observation_id") else [],
                              "findings":{"collection_status":quality.status,"quality_reason":quality.reason},
                              "metrics":{"text_chars":len(quality.clean_text),"model_available":False},
                              "error":quality.reason,"completed_at":captured_at})
                self._finish(job_id,"succeeded",audit={"policy":"read_only","decision":"captured_but_rejected","final_url":final_url,
                    "captured_at":captured_at,"text_chars":len(quality.clean_text),"collection_status":quality.status,
                    "quality_reason":quality.reason,"cooldown_minutes":quality.cooldown_minutes,"analysis_status":"failed_quality_gate"},
                    artifacts=[{"type":"observation",**evidence}])
                return {"status":"rejected_quality","collection_status":quality.status,"reason":quality.reason,"observation":evidence}

            if not run_id and evidence.get("observation_id"):
                analysis_rows = self.store.create_analysis_run(
                    domain=source.get("domain", "communities"),
                    subject_key=source_id,
                    observation_ids=[evidence["observation_id"]],
                )
                if analysis_rows and isinstance(analysis_rows, list) and isinstance(analysis_rows[0], dict):
                    run_id = str(analysis_rows[0].get("id") or "")
            if run_id:
                try:
                    remote = self._call_brain({"action":"analyze","source_id":source_id,"source_url":final_url,
                        "title":title,"text":quality.clean_text,"observation_id":evidence.get("observation_id")})
                    analysis = complete_research_run(self.store,run_id=run_id,source_id=source_id,source_url=final_url,
                        page_title=title,page_text=quality.clean_text,observation_id=evidence.get("observation_id"),
                        captured_at=captured_at,model_result=remote)
                    if (
                        analysis.get("status") == "succeeded"
                        and analysis.get("model")
                        and evidence.get("observation_id")
                        and run_id
                        and analysis.get("is_actionable") is True
                        and isinstance(analysis.get("confidence"), (int, float))
                        and not isinstance(analysis.get("confidence"), bool)
                        and float(analysis["confidence"]) >= 0.6
                    ):
                        department = source.get("domain", "microtasks_services")
                        if department not in {"microtasks_services", "trading", "sports_horse_betting", "ecommerce"}:
                            department = "microtasks_services"
                        try:
                            self.store.upsert_ai_verified_opportunity(
                                title=analysis["title"], summary=analysis["summary"], source_name=source.get("name", source_id),
                                source_url=final_url, department=department, observation_id=evidence["observation_id"],
                                analysis_run_id=run_id, model_used=analysis["model"], confidence_score=analysis["confidence"],
                                is_actionable=True, score=float(analysis["confidence"]) * 100,
                            )
                            analysis["opportunity_status"] = "ai_verified"
                        except Exception as exc:
                            # Keep a successful analysis successful if the separate opportunity write fails.
                            analysis["opportunity_status"] = "not_promoted"
                            analysis["opportunity_error"] = str(exc)[:180] if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__
                    elif analysis.get("status") == "succeeded":
                        analysis["opportunity_status"] = "not_promoted_quality_gate"
                except Exception as exc:
                    safe_error = str(exc)[:180] if isinstance(exc, RuntimeError) else f"ai_analysis_failed:{type(exc).__name__}"
                    self.store._request("PATCH","verox_analysis_runs",params={"id":f"eq.{run_id}"},
                        body={"status":"failed","input_observation_ids":[evidence["observation_id"]] if evidence.get("observation_id") else [],
                              "metrics":{"text_chars":len(quality.clean_text),"model_available":False},
                              "error":safe_error,"completed_at":_now()})
                    analysis = {"status":"failed","model_available":False,"error":safe_error}
            self._finish(job_id,"succeeded",audit={"policy":"read_only","decision":"completed","final_url":final_url,
                "captured_at":captured_at,"text_chars":len(quality.clean_text),"collection_status":quality.status,
                "analysis_status":(analysis or {}).get("status","not_requested")},artifacts=[{"type":"observation",**evidence}])
            return {"status":"succeeded","collection_status":quality.status,"observation":evidence,"analysis":analysis}
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
    parser.add_argument("--cycle",action="store_true",help="Queue a bounded rotation of public sources, then process it")
    parser.add_argument("--preflight",action="store_true",help="Verify the central Vercel model before browser work")
    args = parser.parse_args()
    worker = BrowserWorker()
    if args.preflight:
        print(json.dumps(worker.preflight_ai(), ensure_ascii=False))
    elif args.cycle:
        # Fail closed before queuing or opening Chromium when the central model is unavailable.
        preflight = worker.preflight_ai()
        seeded = worker.seed_public_cycle(limit=3)
        processed = worker.run_once()
        print(json.dumps({"preflight":preflight,"seeded": seeded, "worker": processed}, ensure_ascii=False))
    elif args.once: print(json.dumps(worker.run_once(),ensure_ascii=False))
    else: worker.run_forever()

if __name__ == "__main__": main()
