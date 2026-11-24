"""Scheduled read-only prospect scan and Supabase persistence."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from verox_brain.data_plane import SupabaseDataPlane
from verox_brain.opportunity_engine import load_feeds, scan_feeds


def _record(lead: dict, observed_at: str) -> dict:
    title = str(lead.get("title") or "").strip()[:300]
    source_url = str(lead.get("source_url") or "").strip()[:2000]
    if not title or not source_url.startswith("https://"):
        raise ValueError("scanner produced an invalid opportunity record")
    dedupe_text = re.sub(r"\\s+", " ", title.lower()).strip() + "|" + source_url.lower()
    return {
        "dedupe_key": hashlib.md5(dedupe_text.encode("utf-8")).hexdigest(),
        "title": title,
        "summary": str(lead.get("summary") or "")[:1500],
        "source_name": str(lead.get("source_name") or "Unknown source")[:120],
        "source_url": source_url,
        "department": str(lead.get("department") or "microtasks_services"),
        "published_at": str(lead.get("published_at") or "")[:80] or None,
        "matched_keywords": list(lead.get("matched_keywords") or [])[:40],
        "score": max(0, min(100, int(lead.get("score") or 0))),
        # Do not send status: an existing human triage state must be preserved.
        "last_seen_at": observed_at,
        "updated_at": observed_at,
    }


def run_scan(store=None) -> dict:
    """Scan configured public feeds plus the built-in World Bank public API."""
    result = scan_feeds(load_feeds())
    leads = result.get("leads") or []
    records = [_record(item, datetime.now(timezone.utc).isoformat()) for item in leads]
    persisted = []
    if records:
        client = store or SupabaseDataPlane()
        persisted = client._request(
            "POST",
            "verox_opportunities",
            params={"on_conflict": "dedupe_key"},
            body=records,
            prefer="resolution=merge-duplicates,return=representation",
        ) or []
    return {
        "status": result.get("status", "unavailable"),
        "sources_fetched": int(result.get("sources_fetched", 0)),
        "leads_detected": len(leads),
        "leads_persisted": len(persisted),
        "source_errors": result.get("source_errors", []),
        "revenue_confirmed": False,
    }


if __name__ == "__main__":
    print(json.dumps(run_scan(), ensure_ascii=False))
