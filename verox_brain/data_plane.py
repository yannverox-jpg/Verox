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

    def _get_proof_recorder(self):
        # Lazy import avoids making the legacy persistence path depend on A2A types.
        from verox_brain.server_proof_recorder import RestServerProofRecorder

        recorder = getattr(self, "_proof_recorder", None)
        if recorder is None:
            recorder = RestServerProofRecorder(
                rest_get=lambda table, params: self._request("GET", table, params=params) or [],
                rest_post=lambda table, body: self._request(
                    "POST", table, body=body, prefer="return=representation"
                ) or [],
            )
            self._proof_recorder = recorder
        return recorder

    def save_observation(
        self, *, source_id, text, url, content_type,
        source_published_at=None, metadata=None, proof_context=None
    ):
        """Persist an unverified observation; optionally require server proof and read-back.

        The historical return shape is preserved. With proof_context, a verified_evidence
        object is added only after both observation and runtime-event read-backs succeed.
        """
        if not source_id or not isinstance(url, str) or not url.startswith("https://"):
            raise ValueError("A source ID and HTTPS evidence URL are required.")
        payload = _sanitize({"text": str(text)[:18_000], "url": url, "metadata": metadata or {}})
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(serialized.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("Observation payload exceeds the size limit.")
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        record = {
            "source_id": source_id,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "source_published_at": source_published_at,
            "content_type": str(content_type)[:120],
            "content_hash": digest,
            "payload": payload,
            "verification_status": "unverified",
        }
        rows = self._request(
            "POST", "verox_observations",
            params={"on_conflict": "source_id,content_hash"},
            body=record,
            prefer="resolution=ignore-duplicates,return=representation",
        )
        persisted = rows[0] if rows and isinstance(rows[0], dict) else None
        deduplicated = persisted is None
        if persisted is None:
            existing = self._request(
                "GET", "verox_observations",
                params={
                    "select": "id,source_id,content_hash,verification_status,payload",
                    "source_id": f"eq.{source_id}",
                    "content_hash": f"eq.{digest}",
                    "limit": "2",
                },
            ) or []
            if len(existing) != 1 or not isinstance(existing[0], dict):
                raise DataPlaneError("Could not uniquely resolve the persisted observation.")
            persisted = existing[0]

        observation_id = persisted.get("id")
        if not observation_id:
            raise DataPlaneError("Persisted observation did not return its UUID.")
        if proof_context is not None:
            from verox_brain.server_proof_recorder import (
                ProofIntegrityError,
                compute_canonical_sha256,
            )
            try:
                observation_id = str(__import__("uuid").UUID(str(observation_id)))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ProofIntegrityError("Persisted observation ID is not a valid UUID.") from exc
            if str(persisted.get("source_id")) != str(source_id):
                raise ProofIntegrityError("Persisted observation source_id mismatch.")
            if persisted.get("verification_status") != "unverified":
                raise ProofIntegrityError("Persisted observation must remain unverified.")
            if str(persisted.get("content_hash", "")).lower() != digest.lower():
                raise ProofIntegrityError("Persisted observation content_hash mismatch.")
            stored_payload = persisted.get("payload") or {}
            if isinstance(stored_payload, str):
                stored_payload = json.loads(stored_payload)
            if not isinstance(stored_payload, dict) or stored_payload.get("url") != url:
                raise ProofIntegrityError("Persisted observation URL mismatch.")
            if compute_canonical_sha256(stored_payload) != digest:
                raise ProofIntegrityError("Persisted observation payload hash mismatch.")
            verified_evidence = self._get_proof_recorder().record_and_verify(
                ctx=proof_context,
                observation_id=observation_id,
                source_id=str(source_id),
                url=url.strip(),
                canonical_data=payload,
                source_published_at=source_published_at,
            )
            return {
                "content_hash": digest,
                "observation_id": observation_id,
                "stored": not deduplicated,
                "verification_status": "unverified",
                "verified_evidence": verified_evidence,
            }
        # Keep the exact historical result shape for callers not requesting proof.
        return {
            "content_hash": digest,
            "observation_id": observation_id,
            "stored": not deduplicated,
            "verification_status": "unverified",
        }

    def create_analysis_run(self, *, domain, subject_key=None, observation_ids=None, prompt_version="verox-research-v1"):
        record = {"domain": domain, "subject_key": (subject_key or "")[:200] or None, "status": "queued", "prompt_version": prompt_version[:100], "input_observation_ids": (observation_ids or [])[:200], "findings": {}, "metrics": {}}
        return self._request("POST", "verox_analysis_runs", body=record, prefer="return=representation")
