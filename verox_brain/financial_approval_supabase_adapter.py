"""Server-only Supabase adapter for one-time human financial approvals (T6)."""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from verox_brain.financial_execution_guard import (
    FinancialAuthorizationError,
    FinancialOperationIntent,
    ServerHumanApprovalGuard,
)


def _resolve_server_rest_config(data_plane: Any) -> tuple[str, str]:
    # Match the actual SupabaseDataPlane attributes; never accept browser/client config.
    base_url = str(getattr(data_plane, "base_url", "") or os.getenv("SUPABASE_URL", "")).strip().rstrip("/")
    service_key = str(
        getattr(data_plane, "service_key", "")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    ).strip()
    if not base_url.startswith("https://"):
        raise FinancialAuthorizationError("T6 server Supabase HTTPS configuration unavailable.")
    if not service_key:
        raise FinancialAuthorizationError("T6 server service-role credential unavailable.")
    return base_url, service_key


class SupabaseFinancialApprovalAdapter:
    """PostgREST/RPC adapter. Keep the instance exclusively in trusted backend code."""

    def __init__(
        self,
        data_plane: Any,
        *,
        timeout_seconds: float = 8.0,
        source_label: str = "verox_financial_executor",
        agent_name: str = "financial_execution_guard",
        stage_name: str = "financial_execution_guard",
    ) -> None:
        self._data_plane = data_plane
        self._timeout = float(timeout_seconds)
        if self._timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._source_label = source_label
        self._agent_name = agent_name
        self._stage_name = stage_name

    def _request(self, method: str, path: str, *, params=None, body=None, prefer: str | None = None) -> Any:
        base_url, service_key = _resolve_server_rest_config(self._data_plane)
        url = f"{base_url}/rest/v1/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        payload = None if body is None else json.dumps(
            body, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        request = urllib.request.Request(
            url, data=payload, method=method.upper(), headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw.strip() else None
        except urllib.error.HTTPError as exc:
            # Do not expose response bodies: proxies/servers can echo sensitive headers.
            raise FinancialAuthorizationError(
                f"T6 Supabase request failed with HTTP {exc.code} on {path}."
            ) from None
        except Exception as exc:
            # Exception strings may contain URLs or request details; expose only the type.
            raise FinancialAuthorizationError(
                f"T6 Supabase transport failed on {path} ({type(exc).__name__})."
            ) from None

    def fetch_approval(self, authorization_id: str, operation_fingerprint: str) -> list[dict[str, Any]]:
        clean_id = str(authorization_id or "").strip()
        if not clean_id:
            return []
        result = self._request(
            "GET",
            "verox_financial_authorizations",
            params={
                "select": "authorization_id,operation_fingerprint,status,approved_by,approved_at_utc,expires_at_utc,consumed_at_utc",
                "authorization_id": f"eq.{clean_id}",
                "limit": "2",
            },
        )
        if not isinstance(result, list):
            raise FinancialAuthorizationError("T6 approval lookup returned an invalid response.")
        return result

    def consume_approval(
        self,
        authorization_id: str,
        operation_fingerprint: str,
        *,
        intent: FinancialOperationIntent,
    ) -> bool:
        rpc = getattr(self._data_plane, "rpc", None)
        if not callable(rpc):
            raise FinancialAuthorizationError("T6 dedicated Supabase RPC transport unavailable.")
        try:
            result = rpc(
                "consume_human_financial_authorization",
                params={
                    "p_authorization_id": str(authorization_id).strip(),
                    "p_operation_fingerprint": str(operation_fingerprint).strip().lower(),
                    "p_run_id": str(intent.run_id).strip(),
                    "p_event_id": str(intent.event_id).strip(),
                    "p_action": str(intent.operation_type).strip(),
                    "p_target": str(intent.target).strip(),
                    "p_domain": str(intent.domain).strip(),
                    "p_source": self._source_label,
                    "p_agent": self._agent_name,
                    "p_stage": self._stage_name,
                },
                timeout=self._timeout,
            )
        except Exception as exc:
            # Keep transport details and credentials out of caller-visible errors.
            raise FinancialAuthorizationError(
                f"T6 atomic RPC transport failed ({type(exc).__name__})."
            ) from None
        row = result[0] if isinstance(result, list) and result else result
        if not isinstance(row, dict):
            raise FinancialAuthorizationError("T6 atomic RPC returned an invalid response.")
        return (
            row.get("consumed") is True
            and str(row.get("operation_fingerprint") or "").strip().lower()
            == str(operation_fingerprint).strip().lower()
            and row.get("reason") == "consumed_atomically"
        )

    def record_audit_event(self, audit_payload: dict[str, Any]) -> None:
        # The success audit is written atomically by the RPC. The guard now propagates
        # any refusal-audit write failure as FinancialAuthorizationError (fail-closed).
        if str(audit_payload.get("status") or "").lower() == "authorized":
            return
        source_event_id = str(audit_payload.get("event_id") or "").strip()
        run_id = str(audit_payload.get("run_id") or "").strip()
        if not source_event_id or not run_id:
            raise FinancialAuthorizationError("T6 refusal audit lacks required identifiers.")
        inserted = self._request(
            "POST",
            "nexus_runtime_events",
            prefer="return=representation",
            body={
                "event_id": "evt_guard_" + hashlib.sha256(
                    (run_id + ":" + source_event_id + ":" + str(audit_payload.get("status"))).encode()
                ).hexdigest()[:32],
                "run_id": run_id,
                "task_id": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": self._source_label,
                "agent": self._agent_name,
                "stage": self._stage_name,
                "status": str(audit_payload.get("status") or "blocked"),
                "action": str(audit_payload.get("action") or "financial_action"),
                "target": str(audit_payload.get("target") or "unknown"),
                "proof": {},
                "metadata": audit_payload.get("metadata") or {},
            },
        )
        if not isinstance(inserted, list) or len(inserted) != 1:
            raise FinancialAuthorizationError("T6 refusal audit insert was not confirmed.")

    def build_guard(self, intent: FinancialOperationIntent) -> ServerHumanApprovalGuard:
        return ServerHumanApprovalGuard(
            fetch_approval=self.fetch_approval,
            consume_approval=lambda authorization_id, fingerprint: self.consume_approval(
                authorization_id, fingerprint, intent=intent
            ),
            record_audit_event=self.record_audit_event,
        )
