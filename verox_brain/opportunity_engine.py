"""Public-source opportunity discovery primitives for Verox.

Feeds are configured by the operator in VEROX_OPPORTUNITY_FEEDS as a JSON
array of objects: {name, url, department, keywords}. Only HTTPS feed URLs are
accepted. The scanner never follows arbitrary URLs supplied by an HTTP caller.
It ranks leads for human/commercial follow-up; it does not claim that a lead is
a confirmed buyer or that revenue has been earned.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
import logging
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode, urlparse

logger = logging.getLogger("verox.opportunity_engine")

MAX_FEEDS = 12
MAX_BYTES_PER_FEED = 1_000_000
MAX_ITEMS_PER_FEED = 80
DEFAULT_TIMEOUT_SECONDS = 6
USER_AGENT = "VeroxOpportunityResearch/1.0 (+public-feed-reader)"


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    department: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class Opportunity:
    title: str
    summary: str
    source_name: str
    source_url: str
    department: str
    published_at: str | None
    matched_keywords: tuple[str, ...]
    score: int
    status: str = "detected_unverified"


def load_feeds(raw: str | None = None) -> list[Feed]:
    """Load operator-configured feeds; fail closed on malformed configuration."""
    value = raw if raw is not None else os.getenv("VEROX_OPPORTUNITY_FEEDS", "")
    if not value.strip():
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("VEROX_OPPORTUNITY_FEEDS must be valid JSON.") from exc
    if not isinstance(data, list):
        raise ValueError("VEROX_OPPORTUNITY_FEEDS must be a JSON array.")
    if len(data) > MAX_FEEDS:
        raise ValueError(f"At most {MAX_FEEDS} feeds may be configured.")
    feeds: list[Feed] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()[:120]
        url = str(item.get("url", "")).strip()
        department = str(item.get("department", "microtasks_services")).strip().lower()
        parsed = urlparse(url)
        if not name or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            continue
        if department not in {"microtasks_services", "trading", "sports_horse_betting", "ecommerce"}:
            department = "microtasks_services"
        keywords = item.get("keywords", [])
        if not isinstance(keywords, list):
            keywords = []
        clean_keywords = tuple(dict.fromkeys(
            str(k).strip().lower()[:80] for k in keywords if str(k).strip()
        ))[:40]
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        feeds.append(Feed(name, url, department, clean_keywords))
    return feeds


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _child(element: ET.Element, *names: str) -> ET.Element | None:
    for child in element.iter():
        if child is element:
            continue
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in names:
            return child
    return None


def _item_link(item: ET.Element) -> str:
    for child in list(item):
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local == "link":
            href = child.attrib.get("href", "").strip()
            value = href or _text(child)
            if value.startswith("https://"):
                return value[:2000]
    return ""


def _published(item: ET.Element) -> str | None:
    node = _child(item, "pubdate", "published", "updated", "date")
    raw = _text(node)
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError, OverflowError):
        try:
            from datetime import datetime
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return raw[:80]


def _keyword_matches(keyword: str, text: str) -> bool:
    # Normalize direct callers as well as feed text; short tokens such as AI
    # need boundaries to avoid matching inside unrelated words like paid.
    keyword = keyword.strip().lower()
    text = text.lower()
    if len(keyword) <= 3 and keyword.isalnum():
        return re.search(r"\b" + re.escape(keyword) + r"\b", text) is not None
    return keyword in text


def parse_feed(feed: Feed, body: bytes) -> list[Opportunity]:
    """Parse RSS or Atom without executing or rendering feed content."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    candidates = [
        node for node in root.iter()
        if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
    ][:MAX_ITEMS_PER_FEED]
    output: list[Opportunity] = []
    for item in candidates:
        title = _text(_child(item, "title"))[:300]
        summary = _text(_child(item, "description", "summary", "content"))[:1500]
        link = _item_link(item)
        if not title or not link:
            continue
        searchable = f"{title} {summary}".lower()
        matched = tuple(keyword for keyword in feed.keywords if _keyword_matches(keyword, searchable))
        # This is a transparent triage score, not a probability of making money.
        score = min(100, len(matched) * 18 + (12 if any(
            term in searchable for term in ("request for proposal", "rfp", "integration",
                                              "automation", "api", "vendor", "contract")
        ) else 0))
        output.append(Opportunity(
            title=title,
            summary=summary,
            source_name=feed.name,
            source_url=link,
            department=feed.department,
            published_at=_published(item),
            matched_keywords=matched,
            score=score,
        ))
    return output


def scan_world_bank_procurement(timeout: int = DEFAULT_TIMEOUT_SECONDS) -> tuple[list[Opportunity], str | None]:
    """Read public World Bank procurement notices from its no-key search API."""
    if os.getenv("VEROX_WORLD_BANK_PROCUREMENT_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return [], None
    params = {
        "format": "json", "rows": "50", "os": "0",
        "fl": "id,url,notice_type,publication_date,deadline_date,bid_description,procurement_category,procurement_method,country_name",
        "qterm": "software OR digital OR cloud OR automation OR cybersecurity OR consulting OR information technology",
    }
    url = "https://search.worldbank.org/api/v2/procnotices?" + urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=max(1, min(timeout, 10))) as response:
            final = urlparse(response.geturl())
            if final.scheme != "https" or final.hostname != "search.worldbank.org":
                return [], "unsafe_redirect"
            body = response.read(MAX_BYTES_PER_FEED + 1)
        if len(body) > MAX_BYTES_PER_FEED:
            return [], "response_too_large"
        payload = json.loads(body.decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, UnicodeDecodeError):
        return [], "fetch_failed"
    if not isinstance(payload, dict):
        return [], "invalid_response"
    raw_items = payload.get("procnotices", payload.get("procurement_notices", []))
    if isinstance(raw_items, dict):
        items = list(raw_items.values())
    elif isinstance(raw_items, list):
        items = raw_items
    else:
        items = []
    output: list[Opportunity] = []
    keywords = ("software", "digital", "cloud", "automation", "cybersecurity", "consulting", "information technology", "api", "data", "system")
    for item in items[:MAX_ITEMS_PER_FEED]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("bid_description") or item.get("title") or "").strip()[:300]
        link = str(item.get("url") or item.get("notice_url") or "").strip()
        parsed_link = urlparse(link)
        if not title or parsed_link.scheme != "https" or not parsed_link.hostname:
            continue
        detail = " ".join(str(item.get(k) or "") for k in ("notice_type", "procurement_category", "procurement_method", "country_name"))
        searchable = f"{title} {detail}".lower()
        matched = tuple(k for k in keywords if _keyword_matches(k, searchable))
        if not matched:
            continue
        score = min(100, len(matched) * 18 + 20)
        published = str(item.get("publication_date") or "").strip()[:80] or None
        deadline = str(item.get("deadline_date") or "").strip()
        summary = " — ".join(part for part in (detail.strip(), f"Date limite : {deadline}" if deadline else "") if part)[:1500]
        output.append(Opportunity(title=title, summary=summary, source_name="World Bank Procurement Notices", source_url=link[:2000], department="microtasks_services", published_at=published, matched_keywords=matched, score=score))
    return output, None

def scan_feeds(
    feeds: list[Feed] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    *,
    run_id: str | None = None,
    data_plane: Any | None = None,
    source_ids_by_feed_url: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Fetch configured public feeds and return deduplicated, ranked leads.

    A failed feed is reported by name and error class only. Response bodies,
    credentials, and exception strings are not returned or logged.
    """
    proof_mode = any(value is not None for value in (run_id, data_plane, source_ids_by_feed_url))
    if proof_mode and (not str(run_id or "").strip() or data_plane is None or source_ids_by_feed_url is None):
        raise ValueError("Proof mode requires the real run_id, data_plane, and registered source IDs by feed URL.")
    clean_run_id = str(run_id or "").strip()
    configured = feeds if feeds is not None else load_feeds()
    if not configured:
        return {
            "status": "not_configured",
            "sources_configured": 0,
            "sources_fetched": 0,
            "leads_detected": 0,
            "leads": [],
            "source_errors": [],
            "revenue_confirmed": False,
            "disclaimer": "Aucune source configurée; aucun revenu n'est confirmé.",
            "scanned_at_unix": int(time.time()),
        }
    leads: list[Opportunity] = []
    errors: list[dict[str, str]] = []
    fetched = 0
    proof_recorded = 0
    proof_blocked = 0
    for feed in configured[:MAX_FEEDS]:
        request = urllib.request.Request(feed.url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
        try:
            with urllib.request.urlopen(request, timeout=max(1, min(timeout, 10))) as response:
                final_url = response.geturl()
                final = urlparse(final_url)
                if final.scheme != "https" or not final.hostname:
                    errors.append({"source": feed.name, "error": "unsafe_redirect"})
                    continue
                body = response.read(MAX_BYTES_PER_FEED + 1)
                if len(body) > MAX_BYTES_PER_FEED:
                    errors.append({"source": feed.name, "error": "feed_too_large"})
                    continue
            # Execute XML parsing explicitly so a passing check reflects a real parser result.
            try:
                ET.fromstring(body)
                rss_xml_parsed = True
            except ET.ParseError:
                rss_xml_parsed = False
            fetched += 1
            feed_leads = parse_feed(feed, body)
            if not proof_mode:
                leads.extend(feed_leads)
                continue

            source_id = str((source_ids_by_feed_url or {}).get(feed.url, "")).strip()
            if not source_id or not rss_xml_parsed:
                proof_blocked += len(feed_leads)
                errors.append({
                    "source": feed.name,
                    "error": "proof_source_id_missing" if not source_id else "rss_xml_parse_failed",
                })
                continue

            # Only XML parsing is claimed here. Publication-date checks are deliberately
            # omitted because _published currently preserves unparseable date strings.
            from verox_brain.server_proof_recorder import (
                ExecutedCaptureCheck,
                ObservationProofContext,
                ProofIntegrityError,
            )
            for lead in feed_leads:
                event_id = str(uuid.uuid4())
                context = ObservationProofContext(
                    run_id=clean_run_id,
                    event_id=event_id,
                    agent="discovery_agent",
                    stage="capture",
                    action="capture_rss_item",
                    target=lead.source_url,
                    evidence_type="rss_feed",
                    executed_checks=[
                        ExecutedCaptureCheck(
                            check_name="rss_xml_parsed",
                            passed=True,
                            detail="ET.fromstring succeeded on the fetched response body",
                        )
                    ],
                    source_label="opportunity_engine",
                )
                try:
                    saved = data_plane.save_observation(
                        source_id=source_id,
                        text=(lead.title + "\n" + lead.summary)[:18_000],
                        url=lead.source_url,
                        content_type="application/rss+xml",
                        source_published_at=lead.published_at,
                        metadata={
                            "feed_name": feed.name,
                            "feed_url": feed.url,
                            "department": lead.department,
                            "matched_keywords": list(lead.matched_keywords),
                            "score": lead.score,
                        },
                        proof_context=context,
                    )
                    if not isinstance(saved, dict) or saved.get("verified_evidence") is None:
                        raise ProofIntegrityError("Server proof was not returned after persistence.")
                except Exception as exc:
                    # Fail closed and surface the failure to the caller; never silently fall back.
                    logger.error(
                        "RSS capture proof failed (run_id=%s, event_id=%s, error_type=%s)",
                        clean_run_id, event_id, type(exc).__name__,
                    )
                    raise
                proof_recorded += 1
                leads.append(lead)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            errors.append({"source": feed.name, "error": "fetch_failed"})
    # Procurement notices are demand signals, unlike product-announcement feeds.
    procurement_leads, procurement_error = scan_world_bank_procurement(timeout=timeout)
    enabled = os.getenv("VEROX_WORLD_BANK_PROCUREMENT_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
    if procurement_error is None and enabled:
        fetched += 1
        leads.extend(procurement_leads)
    elif procurement_error:
        errors.append({"source": "World Bank Procurement Notices", "error": procurement_error})
    deduped: dict[str, Opportunity] = {}
    for lead in leads:
        key = re.sub(r"\s+", " ", lead.title.lower()).strip() + "|" + lead.source_url.lower()
        current = deduped.get(key)
        if current is None or lead.score > current.score:
            deduped[key] = lead
    ranked = sorted((lead for lead in deduped.values() if lead.score > 0), key=lambda lead: (lead.score, lead.published_at or ""), reverse=True)
    return {
        "status": "completed" if not errors else ("partial" if fetched else "unavailable"),
        "sources_configured": len(configured),
        "sources_fetched": fetched,
        "leads_detected": len(ranked),
        "leads": [asdict(lead) for lead in ranked[:250]],
        **({
            "proof_mode": True,
            "proofs_recorded": proof_recorded,
            "proofs_blocked": proof_blocked,
        } if proof_mode else {}),
        "source_errors": errors,
        "revenue_confirmed": False,
        "disclaimer": "Les pistes sont détectées, non validées commercialement. Aucun revenu n'est confirmé par ce scan.",
        "scanned_at_unix": int(time.time()),
    }
