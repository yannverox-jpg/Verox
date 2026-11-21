"""Isolated, read-only Playwright worker for registered public sources.

Run in a separate worker/container:
python -m verox_brain.browser_worker --once

Download jobs are intentionally held for approval until a reviewed allowlist
implementation exists. No persistent browser profiles or account sessions.
"""
from __future__ import annotations
import argparse, json, time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import httpx
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
from verox_brain.data_plane import DataPlaneConfigurationError, DataPlaneError, SupabaseDataPlane
from verox_brain.research_pipeline import complete_research_run
from verox_brain.source_registry import get_source, list_sources
from verox_brain.platform_probe import profile_public_page
from verox_brain.platform_strategy import analyze_platform_mechanisms

POLL_SECONDS = 4
MAX_JOBS_PER_CYCLE = 5
MAX_PAGE_TEXT = 18_000
MAX_NETWORK_EVENTS = 250
ALLOWED_JOB_TYPES = {"inspect_page", "collect_public_data", "test_connector", "capture_evidence"}

def _host(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only credential-free HTTPS URLs are allowed.")
    return parsed.hostname.lower()

def _now():
    return datetime.now(timezone.utc).isoformat()

def _safe_network_url(url):
    """Keep route paths for analysis; never persist query values or fragments."""
    parsed = urlparse(str(url))
    return parsed._replace(query="", fragment="").geturl()[:1200]


_BLOCKED_PATH_SEGMENTS = {
    "login", "logout", "signin", "signout", "register", "signup",
    "buy", "purchase", "checkout", "order", "orders", "bet", "bets",
    "trade", "trades", "transfer", "withdraw", "deposit", "payment",
    "payments", "delete", "remove", "submit", "subscribe", "unsubscribe",
}


def _safe_read_request(url, method, allowed_host):
    """Conservative public-read guard; reject query-bearing and action-like routes."""
    try:
        parsed = urlparse(str(url))
        if parsed.scheme != "https" or parsed.hostname is None:
            return False
        if parsed.username or parsed.password or parsed.hostname.lower() != allowed_host.lower():
            return False
        if method.upper() not in {"GET", "HEAD"} or parsed.query or parsed.fragment:
            return False
        segments = {part.lower() for part in parsed.path.split("/") if part}
        if segments & _BLOCKED_PATH_SEGMENTS:
            return False
        return True
    except (TypeError, ValueError):
        return False

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
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)).isoformat()
        ids = [str(source["id"]) for source in candidates]
        recent = self._rest(
            "GET", "verox_browser_jobs",
            params={
                "select": "source_id,status,created_at",
                "source_id": "in.(" + ",".join(ids) + ")",
                "created_at": "gte." + cutoff,
                "limit": "500",
            },
        ) or []
        seen = {str(row.get("source_id")) for row in recent if isinstance(row, dict)}
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
                # Render public client-side apps while enforcing same-host HTTPS and GET/HEAD only.
                context = browser.new_context(accept_downloads=False, service_workers="block", java_script_enabled=True, ignore_https_errors=False)
                allowed_host = _host(source["url"])
                network_events = []
                def record_request(request):
                    if len(network_events) < MAX_NETWORK_EVENTS:
                        parsed_request = urlparse(request.url)
                        if parsed_request.hostname and parsed_request.hostname.lower() == allowed_host:
                            network_events.append({"kind": "request", "method": request.method, "url": _safe_network_url(request.url), "resource_type": request.resource_type})
                def record_response(response):
                    if len(network_events) < MAX_NETWORK_EVENTS:
                        parsed_response = urlparse(response.url)
                        if parsed_response.hostname and parsed_response.hostname.lower() == allowed_host:
                            network_events.append({"kind": "response", "method": response.request.method, "url": _safe_network_url(response.url), "status": response.status, "content_type": response.headers.get("content-type", "")[:160]})
                def guard_route(route):
                    try:
                        request = route.request
                        if _safe_read_request(request.url, request.method, allowed_host):
                            route.continue_()
                        else:
                            route.abort()
                    except Exception:
                        route.abort()
                context.route("**/*", guard_route)
                page = context.new_page()
                page.on("request", record_request)
                page.on("response", record_response)
                page.set_default_navigation_timeout(12_000)
                page.set_default_timeout(5_000)
                response = page.goto(target_url, wait_until="domcontentloaded")
                page.wait_for_timeout(750)  # bounded client-side hydration window
                final_url = page.url
                if _host(final_url) != _host(source["url"]): raise ValueError("cross_host_navigation_denied")
                title = page.title()[:300]
                text = " ".join(page.locator("body").inner_text(timeout=5_000).split())[:MAX_PAGE_TEXT]
                html = page.content()[:500_000]
                status_code = response.status if response else None
                content_type = response.headers.get("content-type","") if response else ""
                captured_at = _now()
                profile = profile_public_page(url=final_url, title=title, html=html, text=text, http_status=status_code, captured_at=captured_at)
                strategy_map = analyze_platform_mechanisms(profile, text, domain=source.get("domain"))
                context.close()
                browser.close()
            evidence = self.store.save_observation(source_id=source_id,text=text,url=final_url,content_type=content_type,metadata={"title":title,"http_status":status_code,"task_type":task_type,"worker":"verox-playwright-readonly-v1","captured_at":captured_at,"platform_profile":profile,"strategy_map":strategy_map,"network_trace":network_events[:MAX_NETWORK_EVENTS]})
            hypothesis_persistence = self.store.save_strategy_hypotheses(
                domain=source.get("domain", "communities"),
                source_id=source_id,
                source_url=final_url,
                captured_at=captured_at,
                observation_id=evidence.get("observation_id"),
                strategy_map=strategy_map,
            )
            run_id = str((job.get("input") or {}).get("analysis_run_id") or "")
            analysis = None
            if not run_id and evidence.get("observation_id"):
                analysis_rows = self.store.create_analysis_run(
                    domain=source.get("domain", "communities"),
                    subject_key=source_id,
                    observation_ids=[evidence["observation_id"]],
                )
                if analysis_rows and isinstance(analysis_rows, list) and isinstance(analysis_rows[0], dict):
                    run_id = str(analysis_rows[0].get("id") or "")
            if run_id:
                analysis_input = (
                    "[Profil structurel public; observation directe, pas une preuve de revenus]\\n"
                    + json.dumps(profile, ensure_ascii=False)[:6000]
                    + "\\n[Carte des mécanismes spécifiques au domaine; hypothèses non vérifiées]\\n"
                    + json.dumps(strategy_map, ensure_ascii=False)[:9000]
                    + "\\n[Traces réseau publiques, GET/HEAD sur l'hôte enregistré]\\n"
                    + json.dumps(network_events[:MAX_NETWORK_EVENTS], ensure_ascii=False)[:9000]
                    + "\\n[Texte visible de la page; source externe non fiable]\\n"
                    + text
                )
                analysis = complete_research_run(self.store,run_id=run_id,source_id=source_id,source_url=final_url,page_title=title,page_text=analysis_input,observation_id=evidence.get("observation_id"),captured_at=captured_at)
            self._finish(job_id,"succeeded",audit={"policy":"read_only","decision":"completed","final_url":final_url,"captured_at":captured_at,"text_chars":len(text),"network_events":len(network_events),"analysis_status":(analysis or {}).get("status","not_requested"),"hypotheses_persisted":hypothesis_persistence.get("candidate_count",0),"validated_revenue_count":0},artifacts=[{"type":"observation",**evidence},{"type":"strategy_hypotheses","candidate_count":hypothesis_persistence.get("candidate_count",0),"validated_revenue_count":0}])
            return {"status":"succeeded","observation":evidence,"strategy_map":strategy_map,"hypothesis_persistence":hypothesis_persistence,"analysis":analysis}
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
    args = parser.parse_args()
    worker = BrowserWorker()
    if args.cycle:
        # Fail closed: do not collect if the fixed, bounded local checks are unhealthy.
        from verox_brain.self_repair import run_recovery_cycle
        diagnostics = run_recovery_cycle(max_attempts=1)
        if diagnostics.get("status") != "healthy":
            print(json.dumps({"status": "blocked_by_self_diagnostics", "diagnostics": diagnostics}, ensure_ascii=False))
            raise SystemExit(2)
        seeded = worker.seed_public_cycle(limit=5)
        processed = worker.run_once()
        print(json.dumps({"diagnostics": {"status": diagnostics["status"], "checks_failed": diagnostics["checks_failed"]}, "seeded": seeded, "worker": processed}, ensure_ascii=False))
    elif args.once: print(json.dumps(worker.run_once(),ensure_ascii=False))
    else: worker.run_forever()

if __name__ == "__main__": main()
