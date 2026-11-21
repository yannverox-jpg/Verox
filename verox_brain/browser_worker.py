"""Isolated, read-only Playwright worker for registered public sources.

Run in a separate worker/container:
python -m verox_brain.browser_worker --once

Public reference downloads are bounded, stored privately as inert artifacts, and never executed.
No persistent browser profiles or account sessions.
"""
from __future__ import annotations
import argparse, json, time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
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


def _safe_read_request(url, method, allowed_host, resource_type="document"):
    """Allow public HTTPS reads across CDNs and query-based APIs; never submit mutations.

    The registered source controls the initial target. Public assets and API reads may
    use other HTTPS hosts, as modern sites routinely depend on CDNs and API subdomains.
    URL query values are allowed for search/pagination but are redacted from persisted logs.
    """
    try:
        parsed = urlparse(str(url))
        if parsed.scheme != "https" or parsed.hostname is None:
            return False
        if parsed.username or parsed.password or method.upper() not in {"GET", "HEAD"}:
            return False
        import ipaddress
        import socket
        try:
            addresses = [ipaddress.ip_address(parsed.hostname)]
        except ValueError:
            try:
                addresses = [
                    ipaddress.ip_address(item[4][0])
                    for item in socket.getaddrinfo(parsed.hostname, None, type=socket.SOCK_STREAM)
                ]
            except OSError:
                return False
        if not addresses or any(not address.is_global for address in addresses):
            return False
        return True
    except (TypeError, ValueError):
        return False


def _is_registered_public_url(url, registry=None):
    """Top-level navigation/download target must match a registered public source."""
    try:
        host = _host(url)
        sources = registry if registry is not None else list_sources().get("sources", [])
        return any(
            source.get("access") in {"web", "public_web", "web_or_api"}
            and _host(str(source.get("url", ""))) == host
            for source in sources
        )
    except (TypeError, ValueError):
        return False

def _registered_source_for_url(url, registry=None):
    """Return the public registry entry that owns this hostname, if any."""
    try:
        host = _host(url)
        sources = registry if registry is not None else list_sources().get("sources", [])
        for source in sources:
            if source.get("access") not in {"web", "public_web", "web_or_api"}:
                continue
            if _host(str(source.get("url", ""))) == host:
                return source
    except (TypeError, ValueError):
        return None
    return None


def _safe_public_link_url(url):
    """Preserve useful public search/pagination parameters while dropping likely secrets."""
    parsed = urlparse(str(url))
    sensitive = {"token", "access_token", "api_key", "apikey", "key", "secret", "password",
                 "session", "sessionid", "cookie", "authorization", "auth", "code", "signature",
                 "sig", "email", "phone", "tracking_id", "ref", "campaign", "gclid", "fbclid"}
    kept = []
    for pair in parsed.query.split("&") if parsed.query else []:
        name = pair.split("=", 1)[0].lower()
        if name not in sensitive and not name.startswith("utm_") and not any(part in name for part in ("token", "secret", "password", "session", "signature")):
            kept.append(pair)
    return parsed._replace(query="&".join(kept), fragment="").geturl()[:1200]


def _extract_public_links(raw_links, base_url, limit=50):
    """Extract public-link leads without navigating to or trusting them."""
    found, seen = [], set()
    for item in raw_links or []:
        if len(found) >= max(1, min(int(limit), 100)) or not isinstance(item, dict):
            break
        href = str(item.get("href", "")).strip()
        if not href:
            continue
        try:
            absolute = urljoin(base_url, href)
            parsed = urlparse(absolute)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                continue
            safe_url = _safe_public_link_url(absolute)
            if safe_url in seen:
                continue
            seen.add(safe_url)
            found.append({
                "url": safe_url,
                "host": parsed.hostname.lower(),
                "label": " ".join(str(item.get("label", "")).split())[:180],
                "registered_public_source": _is_registered_public_url(absolute),
                "follow_policy": "eligible_for_next_cycle" if _is_registered_public_url(absolute) else "lead_only_review_registry",
            })
        except (TypeError, ValueError):
            continue
    return found


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
                "requested_capabilities": ["navigate", "read_text", "discover_public_links", "inspect_public_api_docs", "download_public_references"],
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

    def _known_public_sources(self):
        """Merge the curated registry with Supabase catalog entries without reviving disabled sources."""
        curated = {str(item.get("id")): dict(item) for item in list_sources().get("sources", []) if item.get("id")}
        rows = self._rest("GET", "verox_sources", params={
            "select": "source_id,domain,name,canonical_url,access_mode,status,trust_level,enabled,metadata",
            "limit": "1000",
        }) or []
        for row in rows:
            if not isinstance(row, dict) or not row.get("source_id"):
                continue
            source_id = str(row["source_id"])
            if row.get("status") in {"disabled", "blocked"} or row.get("access_mode") != "public_only":
                curated.pop(source_id, None)
                continue
            curated[source_id] = {
                "id": source_id, "name": str(row.get("name") or source_id),
                "domain": str(row.get("domain") or "communities"),
                "kind": "discovered_public_site", "url": str(row.get("canonical_url") or ""),
                "access": "public_web", "notes": "Supabase public source catalog entry",
            }
        return [source for source in curated.values() if source.get("access") in {"web", "public_web", "web_or_api"}]

    def _resolve_source(self, source_id):
        """Resolve curated or discovered sources while honoring database disable/block status."""
        source_id = str(source_id or "")
        rows = self._rest("GET", "verox_sources", params={
            "select": "source_id,domain,name,canonical_url,access_mode,status,trust_level,enabled,metadata",
            "source_id": "eq." + source_id, "limit": "1",
        }) or []
        if rows and isinstance(rows[0], dict):
            row = rows[0]
            if row.get("status") in {"disabled", "blocked"} or row.get("access_mode") != "public_only":
                return None
            return {
                "id": str(row.get("source_id")), "name": str(row.get("name") or source_id),
                "domain": str(row.get("domain") or "communities"), "kind": "discovered_public_site",
                "url": str(row.get("canonical_url") or ""), "access": "public_web",
                "notes": "Supabase public source catalog entry",
            }
        return get_source(source_id)

    def _queue_discovered_links(self, source, discovered_links, *, limit=3):
        """Queue bounded page reads and inert file downloads under the owning source."""
        from pathlib import PurePosixPath
        from urllib.parse import unquote
        download_suffixes = {".pdf", ".md", ".txt", ".json", ".yaml", ".yml", ".toml",
                            ".csv", ".xml", ".zip", ".whl", ".tgz", ".gz", ".py", ".js"}
        eligible = []
        known_sources = self._known_public_sources()
        for link in discovered_links:
            url = str(link.get("url", ""))
            target_source = _registered_source_for_url(url, known_sources)
            if not url:
                continue
            if not target_source:
                # Automatically catalog a newly discovered public HTTPS host as an unverified
                # source candidate. The resulting job remains bounded and read-only.
                try:
                    host = _host(url)
                except ValueError:
                    continue
                import hashlib
                source_id = "discovered_" + hashlib.sha256(host.encode()).hexdigest()[:16]
                target_source = {
                    "id": source_id, "name": host[:200],
                    "domain": source.get("domain", "communities"),
                    "kind": "discovered_public_site", "url": url,
                    "access": "public_web",
                    "notes": "Unverified public source discovered from " + str(source.get("id", "unknown"))[:100],
                }
                try:
                    self.store.register_source(target_source)
                except Exception:
                    continue
            if target_source["id"] == source.get("id") and url == source.get("url"):
                continue
            suffix = PurePosixPath(unquote(urlparse(url).path)).suffix.lower()
            task_type = "download_public_file" if suffix in download_suffixes else "collect_public_data"
            eligible.append((url, target_source, task_type))
        unique = []
        seen_urls = set()
        for url, target_source, task_type in eligible:
            if url not in seen_urls:
                seen_urls.add(url)
                unique.append((url, target_source, task_type))
        unique = unique[:max(0, min(int(limit), 3))]
        if not unique:
            return {"queued": 0, "urls": []}
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        queued = []
        for url, target_source, task_type in unique:
            # Ensure the destination source exists in Supabase before creating its FK-linked job.
            self.store.register_source(target_source)
            recent = self._rest("GET", "verox_browser_jobs", params={
                "select": "id,status,created_at,input",
                "source_id": "eq." + str(target_source["id"]),
                "created_at": "gte." + cutoff,
                "limit": "500",
            }) or []
            seen = {
                str((row.get("input") or {}).get("url", ""))
                for row in recent if isinstance(row, dict) and isinstance(row.get("input"), dict)
            }
            if url in seen:
                continue
            record = {
                "task_type": task_type,
                "source_id": target_source["id"],
                "status": "queued",
                "policy_mode": "read_only",
                "requested_capabilities": ["navigate", "read_text", "discover_public_links"] if task_type != "download_public_file" else ["download_public_reference", "store_inert_artifact"],
                "input": {"url": url, "request_reason": "bounded_registered_public_link_followup"},
                "artifact_refs": [],
                "audit": {"created_by": "browser_worker", "policy": "read_only", "parent_source_id": source["id"], "artifact_policy": "never_execute" if task_type == "download_public_file" else None},
            }
            rows = self._rest("POST", "verox_browser_jobs", body=record)
            if rows:
                queued.append({"source_id": target_source["id"], "url": url, "task_type": task_type})
        return {"queued": len(queued), "urls": queued}
    def _download_public_file(self, job_id, source, target_url):
        """Store a bounded public artifact as inert data; never install or execute it."""
        from pathlib import PurePosixPath
        from urllib.parse import unquote
        allowed_suffixes = {".pdf", ".md", ".txt", ".json", ".yaml", ".yml", ".toml",
                            ".csv", ".xml", ".zip", ".whl", ".tgz", ".gz", ".py", ".js"}
        max_bytes = 20 * 1024 * 1024
        try:
            parsed = urlparse(target_url)
            suffix = PurePosixPath(unquote(parsed.path)).suffix.lower()
            if suffix not in allowed_suffixes:
                raise ValueError("file_extension_not_allowlisted")
            chunks = []
            total_bytes = 0
            with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
                with client.stream("GET", target_url, headers={"Accept": "application/pdf, application/json, text/plain, application/zip, application/octet-stream"}) as response:
                    if response.is_redirect:
                        raise ValueError("download_redirect_requires_registered_target")
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "application/octet-stream").split(";")[0][:120]
                    declared_size = response.headers.get("content-length")
                    if declared_size and declared_size.isdigit() and int(declared_size) > max_bytes:
                        raise ValueError("download_exceeds_20mb_limit")
                    for chunk in response.iter_bytes(64 * 1024):
                        total_bytes += len(chunk)
                        if total_bytes > max_bytes:
                            raise ValueError("download_exceeds_20mb_limit")
                        chunks.append(chunk)
            content = b"".join(chunks)
            import hashlib
            digest = hashlib.sha256(content).hexdigest()
            filename = PurePosixPath(unquote(parsed.path)).name[:120] or ("artifact" + suffix)
            object_path = f"public-references/{source['id']}/{digest[:16]}-{filename}"
            storage_url = f"{self.store.base_url}/storage/v1/object/verox-library/{object_path}"
            headers = {"apikey": self.store.service_key, "Authorization": f"Bearer {self.store.service_key}",
                       "Content-Type": content_type, "x-upsert": "false"}
            with httpx.Client(timeout=25, follow_redirects=False, trust_env=False) as client:
                stored = client.post(storage_url, content=content, headers=headers)
                if stored.status_code == 409:
                    storage_status = "already_exists"
                else:
                    stored.raise_for_status()
                    storage_status = "stored"
            artifact = {"source_id": source["id"], "url": _safe_network_url(target_url),
                        "object_path": object_path, "bucket": "verox-library", "sha256": digest,
                        "size_bytes": len(content), "content_type": content_type,
                        "downloaded_at": _now(), "execution_policy": "never_execute"}
            self._finish(job_id, "completed", audit={"policy":"public_read_only", "decision":"stored_inert_artifact",
                "size_bytes":len(content), "sha256":digest, "storage_status":storage_status,
                "execution_policy":"never_execute"}, artifacts=[artifact])
            return {"status":"completed", "artifact":artifact}
        except ValueError as exc:
            reason = str(exc)[:120]
            self._finish(job_id, "failed", error=reason, audit={"policy":"public_read_only", "decision":"denied"})
            return {"status":"failed", "reason":reason}
        except httpx.HTTPError as exc:
            reason = f"public_download_failed:{type(exc).__name__}"
            self._finish(job_id, "failed", error=reason, audit={"policy":"public_read_only", "decision":"failed"})
            return {"status":"failed", "reason":reason}

    def run_job(self, job):
        job_id, task_type, source_id = str(job.get("id","")), str(job.get("task_type","")), str(job.get("source_id") or "")
        source = self._resolve_source(source_id)
        allowed_types = ALLOWED_JOB_TYPES | {"download_public_file"}
        if task_type not in allowed_types:
            self._finish(job_id, "failed", error="job_type_not_allowlisted", audit={"policy":"read_only", "decision":"denied"})
            return {"status":"denied", "reason":"job_type_not_allowlisted"}
        if not source or source.get("access") not in {"web", "public_web", "web_or_api"}:
            self._finish(job_id,"failed",error="source_not_public_allowlisted",audit={"policy":"read_only","decision":"denied"})
            return {"status":"denied","reason":"source_not_public_allowlisted"}
        target_url = str((job.get("input") or {}).get("url") or source["url"])
        try:
            if not _is_registered_public_url(target_url) and _host(target_url) != _host(source["url"]): raise ValueError("target_host_not_registered")
            if task_type == "download_public_file":
                return self._download_public_file(job_id, source, target_url)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                # Render public client-side apps; allow HTTPS CDNs/API hosts, but never submit writes.
                context = browser.new_context(accept_downloads=True, service_workers="block", java_script_enabled=True, ignore_https_errors=False)
                allowed_host = _host(source["url"])
                network_events = []
                def record_request(request):
                    if len(network_events) < MAX_NETWORK_EVENTS:
                        parsed_request = urlparse(request.url)
                        if parsed_request.hostname:
                            network_events.append({"kind": "request", "method": request.method, "url": _safe_network_url(request.url), "resource_type": request.resource_type, "host": parsed_request.hostname.lower()})
                def record_response(response):
                    if len(network_events) < MAX_NETWORK_EVENTS:
                        parsed_response = urlparse(response.url)
                        if parsed_response.hostname:
                            network_events.append({"kind": "response", "method": response.request.method, "url": _safe_network_url(response.url), "status": response.status, "content_type": response.headers.get("content-type", "")[:160], "host": parsed_response.hostname.lower()})
                def guard_route(route):
                    try:
                        request = route.request
                        if _safe_read_request(request.url, request.method, allowed_host, request.resource_type):
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
                # Public sites often redirect to country domains; allow HTTPS redirects only.
                _host(final_url)
                title = page.title()[:300]
                text = " ".join(page.locator("body").inner_text(timeout=5_000).split())[:MAX_PAGE_TEXT]
                raw_links = page.locator("a[href]").evaluate_all(
                    "(els) => els.slice(0, 300).map(a => ({href: a.href, label: (a.innerText || a.getAttribute('aria-label') || '').slice(0, 180)}))"
                )
                discovered_links = _extract_public_links(raw_links, final_url, limit=50)
                # Feed registered public destinations back into the queue. Unknown hosts remain
                # leads for later registry review; each cycle adds at most three follow-up pages.
                queued_followups = self._queue_discovered_links(source, discovered_links, limit=3)
                html = page.content()[:500_000]
                status_code = response.status if response else None
                content_type = response.headers.get("content-type","") if response else ""
                captured_at = _now()
                profile = profile_public_page(url=final_url, title=title, html=html, text=text, http_status=status_code, captured_at=captured_at)
                strategy_map = analyze_platform_mechanisms(profile, text, domain=source.get("domain"))
                context.close()
                browser.close()
            evidence = self.store.save_observation(source_id=source_id,text=text,url=final_url,content_type=content_type,metadata={"title":title,"http_status":status_code,"task_type":task_type,"worker":"verox-playwright-readonly-v1","captured_at":captured_at,"platform_profile":profile,"strategy_map":strategy_map,"discovered_links":discovered_links,"queued_followups":queued_followups,"network_trace":network_events[:MAX_NETWORK_EVENTS]})
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
                    + "\\n[Liens publics découverts; les hôtes non enregistrés restent des pistes et ne sont pas suivis automatiquement]\\n"
                    + json.dumps(discovered_links, ensure_ascii=False)[:7000]
                    + "\\n[Traces réseau publiques, requêtes GET/HEAD]\\n"
                    + json.dumps(network_events[:MAX_NETWORK_EVENTS], ensure_ascii=False)[:9000]
                    + "\\n[Texte visible de la page; source externe non fiable]\\n"
                    + text
                )
                analysis = complete_research_run(self.store,run_id=run_id,source_id=source_id,source_url=final_url,page_title=title,page_text=analysis_input,observation_id=evidence.get("observation_id"),captured_at=captured_at)
            self._finish(job_id,"succeeded",audit={"policy":"read_only","decision":"completed","final_url":final_url,"captured_at":captured_at,"text_chars":len(text),"network_events":len(network_events),"queued_followups":queued_followups.get("queued",0),"analysis_status":(analysis or {}).get("status","not_requested"),"hypotheses_persisted":hypothesis_persistence.get("candidate_count",0),"validated_revenue_count":0},artifacts=[{"type":"observation",**evidence},{"type":"strategy_hypotheses","candidate_count":hypothesis_persistence.get("candidate_count",0),"validated_revenue_count":0}])
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
