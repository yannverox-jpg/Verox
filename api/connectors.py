"""Read-only, secret-safe view of Verox's central connector registry."""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

import httpx

MAX_ROWS = 200


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Do not log request headers or secret-bearing configuration.
        return

    def do_GET(self):
        if urlparse(self.path).path.rstrip("/") != "/api/connectors":
            _json(self, 404, {"error": "not_found"})
            return

        base_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        key = (
            os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
            or os.getenv("SUPABASE_SECRET_KEY", "").strip()
        )
        if not base_url.startswith("https://") or not key:
            _json(self, 503, {"error": "connector_registry_not_configured"})
            return

        params = {
            "select": "connector_id,connector_type,status,permission_profile,capabilities,last_tested_at,last_test_result,updated_at",
            "order": "connector_id.asc",
            "limit": str(MAX_ROWS),
        }
        try:
            with httpx.Client(timeout=8.0, follow_redirects=False, trust_env=False) as client:
                response = client.get(
                    f"{base_url}/rest/v1/verox_connector_registry",
                    params=params,
                    headers={
                        "apikey": key,
                        "Authorization": f"Bearer {key}",
                        "Accept": "application/json",
                    },
                )
                response.raise_for_status()
                rows = response.json()
            if not isinstance(rows, list):
                raise ValueError("invalid_registry_response")

            connectors = []
            for row in rows[:MAX_ROWS]:
                if not isinstance(row, dict):
                    continue
                result = row.get("last_test_result")
                safe_result = {}
                if isinstance(result, dict):
                    for field in ("verified_live", "reason", "http_status", "latency_ms", "tested_at"):
                        value = result.get(field)
                        if isinstance(value, (str, int, float, bool)) or value is None:
                            safe_result[field] = value
                connectors.append({
                    "connector_id": str(row.get("connector_id", ""))[:120],
                    "connector_type": str(row.get("connector_type", ""))[:40],
                    "status": str(row.get("status", "catalog_only"))[:40],
                    "permission_profile": str(row.get("permission_profile", "read_only"))[:40],
                    "capabilities": row.get("capabilities") if isinstance(row.get("capabilities"), list) else [],
                    "last_tested_at": row.get("last_tested_at"),
                    "last_test_result": safe_result,
                    "updated_at": row.get("updated_at"),
                })
            _json(self, 200, {
                "ok": True,
                "count": len(connectors),
                "connectors": connectors,
                "note": "catalog_only/configured status is not proof of a live connection; verified requires a successful connector-specific test.",
            })
        except httpx.HTTPStatusError as exc:
            _json(self, 503, {"error": "connector_registry_unavailable", "upstream_status": exc.response.status_code})
        except (httpx.HTTPError, ValueError, TypeError):
            _json(self, 503, {"error": "connector_registry_unavailable"})


def do_GET(self):
    handler.do_GET(self)
