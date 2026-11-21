"""Server-side Supabase data-plane client for Verox. Never use in browser code."""
from __future__ import annotations
import hashlib, json, os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
import httpx

MAX_PAYLOAD_BYTES = 180_000
_FORBIDDEN = {"authorization", "cookie", "set-cookie", "password", "secret", "token", "api_key", "apikey", "service_role_key", "session"}

class DataPlaneConfigurationError(RuntimeError): pass
class DataPlaneError(RuntimeError): pass

def _credentials():
    base = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not key:
        raise DataPlaneConfigurationError("Server-side persistence requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")
    if not base.startswith("https://"):
        raise DataPlaneConfigurationError("Supabase URL must use HTTPS.")
    return base, key

def _sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 8: return "[depth-limited]"
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            norm = str(key).strip().lower().replace("-", "_")
            if norm in _FORBIDDEN or any(term in norm for term in ("credential", "private_key", "access_token")): continue
            out[str(key)[:100]] = _sanitize(item, depth + 1)
        return out
    if isinstance(value, list): return [_sanitize(x, depth + 1) for x in value[:200]]
    if isinstance(value, str): return value[:20_000]
    if value is None or isinstance(value, (bool, int, float)): return value
    return str(value)[:2_000]

class SupabaseDataPlane:
    """REST client using a server-only service-role secret."""
    def __init__(self, *, base_url=None, service_key=None):
        if base_url is None or service_key is None:
            env_base, env_key = _credentials()
            base_url, service_key = base_url or env_base, service_key or env_key
        if not base_url.startswith("https://") or not service_key:
            raise DataPlaneConfigurationError("HTTPS URL and server-side key are required.")
        self.base_url, self.service_key = base_url.rstrip("/"), service_key

    def _request(self, method, table, *, params=None, body=None, prefer=None):
        headers = {"apikey": self.service_key, "Authorization": f"Bearer {self.service_key}", "Content-Type": "application/json", "Accept": "application/json"}
        if prefer: headers["Prefer"] = prefer
        try:
            with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
                response = client.request(method, f"{self.base_url}/rest/v1/{quote(table, safe='')}", params=params, json=body, headers=headers)
                response.raise_for_status()
                return response.json() if response.content else None
        except httpx.HTTPError as exc:
            raise DataPlaneError(f"Supabase data-plane request failed ({type(exc).__name__}).") from None

    def register_source(self, source):
        source_id, url = str(source.get("id", "")).strip(), str(source.get("url", "")).strip()
        if not source_id or not url.startswith("https://"): raise ValueError("A registered source ID and HTTPS URL are required.")
        record = {"source_id": source_id, "domain": source.get("domain", "communities"), "name": str(source.get("name", source_id))[:200], "canonical_url": url, "source_kind": "website", "access_mode": "public_only", "status": "catalog_only", "trust_level": "unrated", "enabled": False, "metadata": _sanitize({"kind": source.get("kind"), "notes": source.get("notes")}), "updated_at": datetime.now(timezone.utc).isoformat()}
        return self._request("POST", "verox_sources", params={"on_conflict": "source_id"}, body=record, prefer="resolution=merge-duplicates,return=representation")

    def save_observation(self, *, source_id, text, url, content_type, source_published_at=None, metadata=None):
        if not source_id or not url.startswith("https://"): raise ValueError("A source ID and HTTPS evidence URL are required.")
        payload = _sanitize({"text": str(text)[:18_000], "url": url, "metadata": metadata or {}})
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > MAX_PAYLOAD_BYTES: raise ValueError("Observation payload exceeds the size limit.")
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        record = {"source_id": source_id, "observed_at": datetime.now(timezone.utc).isoformat(), "source_published_at": source_published_at, "content_type": str(content_type)[:120], "content_hash": digest, "payload": payload, "verification_status": "unverified"}
        rows = self._request("POST", "verox_observations", params={"on_conflict": "source_id,content_hash"}, body=record, prefer="resolution=ignore-duplicates,return=representation")
        observation_id = rows[0].get("id") if rows and isinstance(rows[0], dict) else None
        if not observation_id:
            existing = self._request("GET", "verox_observations", params={"select":"id", "source_id":f"eq.{source_id}", "content_hash":f"eq.{digest}", "limit":"1"})
            observation_id = existing[0].get("id") if existing and isinstance(existing[0], dict) else None
        return {"content_hash": digest, "observation_id": observation_id, "stored": bool(rows), "verification_status": "unverified"}

    def save_strategy_hypotheses(self, *, domain, source_id, source_url, captured_at, observation_id, strategy_map):
        """Persist platform-specific hypotheses as candidates, never as validated revenue."""
        allowed_domains = {"sports_horse_betting", "trading", "ecommerce", "microtasks_services", "communities", "public_sector"}
        if domain not in allowed_domains:
            domain = "communities"
        mechanisms = strategy_map.get("observed_mechanisms", []) if isinstance(strategy_map, dict) else []
        saved = []
        for item in mechanisms[:30]:
            if not isinstance(item, dict) or not item.get("hypothesis_key") or not item.get("hypothesis"):
                continue
            record = {
                "domain": domain,
                "hypothesis_key": str(item["hypothesis_key"])[:200],
                "statement": str(item["hypothesis"])[:2000],
                "status": "candidate",
                "sample_size": 0,
                "metrics": _sanitize({
                    "mechanism_id": item.get("mechanism_id"),
                    "platform": item.get("platform"),
                    "strategy": item.get("strategy"),
                    "metrics_to_measure": item.get("metrics_to_measure", []),
                    "evidence_needed": item.get("evidence_needed", []),
                    "risk_notes": item.get("risk_notes"),
                    "observed_signals": item.get("observed_signals", []),
                    "confidence": "low",
                    "revenue_confirmed": False,
                }),
                "evidence_summary": _sanitize({
                    "source_id": source_id,
                    "source_url": source_url,
                    "captured_at": captured_at,
                    "observation_id": observation_id,
                    "evidence_quality": strategy_map.get("evidence_quality", {}),
                    "verification_status": "unverified",
                }),
                "validation_window": {
                    "required_next_step": "collect_independent_or_time_series_evidence",
                    "status": "not_started",
                },
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            rows = self._request(
                "POST",
                "verox_hypotheses",
                params={"on_conflict": "hypothesis_key"},
                body=record,
                prefer="resolution=merge-duplicates,return=representation",
            )
            if rows and isinstance(rows, list) and isinstance(rows[0], dict):
                saved.append(str(rows[0].get("id", "")))
            else:
                saved.append(str(item["hypothesis_key"]))
        return {"candidate_count": len(saved), "saved_ids": saved[:30], "validated_revenue_count": 0}

    def create_analysis_run(self, *, domain, subject_key=None, observation_ids=None, prompt_version="verox-research-v1"):
        record = {"domain": domain, "subject_key": (subject_key or "")[:200] or None, "status": "queued", "prompt_version": prompt_version[:100], "input_observation_ids": (observation_ids or [])[:200], "findings": {}, "metrics": {}}
        return self._request("POST", "verox_analysis_runs", body=record, prefer="return=representation")
