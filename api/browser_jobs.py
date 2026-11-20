"""Protected API for queuing read-only browser jobs."""
from __future__ import annotations
import hmac, json, os
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from verox_brain.data_plane import DataPlaneConfigurationError, DataPlaneError, SupabaseDataPlane
from verox_brain.source_registry import get_source

MAX_BODY = 8_000
ALLOWED_TYPES = {"inspect_page", "collect_public_data", "test_connector", "capture_evidence", "download_public_file"}
PUBLIC_ACCESS = {"web", "public_web", "web_or_api"}

def _json(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)

def _authorized(request):
    expected = os.getenv("VEROX_INTERNAL_TASK_TOKEN", "").strip()
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))

def _read_body(request):
    size = int(request.headers.get("Content-Length", "0"))
    if size <= 0 or size > MAX_BODY: raise ValueError("invalid_body_size")
    payload = json.loads(request.rfile.read(size))
    if not isinstance(payload, dict): raise ValueError("invalid_body")
    return payload

def _public_job(row):
    if not isinstance(row, dict): return {}
    return {key: row.get(key) for key in ("id","task_type","source_id","status","policy_mode","created_at","started_at","completed_at","error","artifact_refs","audit")}

class handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Do not log request bodies, tokens, or job input.
        super().log_message(fmt, *args)

    def do_POST(self):
        if urlparse(self.path).path.rstrip("/") != "/api/browser/jobs":
            _json(self,404,{"error":"not_found"}); return
        if not _authorized(self):
            _json(self,401,{"error":"Authentification requise."}); return
        try:
            data = _read_body(self)
            task_type = str(data.get("task_type",""))
            source_id = str(data.get("source_id","")).strip()
            source = get_source(source_id)
            if task_type not in ALLOWED_TYPES:
                _json(self,400,{"error":"Type de tâche non autorisé."}); return
            if not source or source.get("access") not in PUBLIC_ACCESS:
                _json(self,403,{"error":"La source n'est pas autorisée pour la collecte publique."}); return
            target_url = str((data.get("input") or {}).get("url") or source["url"])
            parsed, canonical = urlparse(target_url), urlparse(source["url"])
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.hostname.lower() != canonical.hostname.lower():
                _json(self,400,{"error":"L'URL doit rester sur l'hôte HTTPS enregistré."}); return
            store = SupabaseDataPlane()
            store.register_source(source)
            record = {
                "task_type": task_type, "source_id": source_id, "status": "queued",
                "policy_mode": "read_only", "requested_capabilities": ["navigate","read_text"],
                "input": {"url": target_url, "request_reason": str(data.get("reason",""))[:500]},
                "artifact_refs": [], "audit": {"created_by":"protected_api","policy":"read_only"}
            }
            rows = store._request("POST","verox_browser_jobs",body=record,prefer="return=representation")
            if not rows:
                _json(self,503,{"error":"La tâche n'a pas pu être mise en file."}); return
            _json(self,202,{"job":_public_job(rows[0]),"message":"Tâche en file; aucun accès connecté ni aucune action externe ne sera exécuté."})
        except ValueError:
            _json(self,400,{"error":"Corps JSON invalide."})
        except DataPlaneConfigurationError:
            _json(self,503,{"error":"La persistance serveur n'est pas configurée."})
        except (DataPlaneError, Exception):
            _json(self,503,{"error":"Le service de mise en file est indisponible."})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != "/api/browser/jobs":
            _json(self,404,{"error":"not_found"}); return
        if not _authorized(self):
            _json(self,401,{"error":"Authentification requise."}); return
        job_id = (parse_qs(parsed.query).get("id") or [""])[0]
        if not job_id or len(job_id) > 64:
            _json(self,400,{"error":"Identifiant de tâche requis."}); return
        try:
            store = SupabaseDataPlane()
            rows = store._request("GET","verox_browser_jobs",params={"id":f"eq.{job_id}","select":"id,task_type,source_id,status,policy_mode,created_at,started_at,completed_at,error,artifact_refs,audit","limit":"1"})
            if not rows:
                _json(self,404,{"error":"Tâche introuvable."}); return
            _json(self,200,{"job":_public_job(rows[0])})
        except DataPlaneConfigurationError:
            _json(self,503,{"error":"La persistance serveur n'est pas configurée."})
        except Exception:
            _json(self,503,{"error":"Le service de suivi est indisponible."})
