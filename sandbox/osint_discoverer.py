"""Brain-directed discovery of candidate platforms; discovery never logs in or acts.

The caller supplies the intent and search candidates. This module validates URLs and
collects public metadata only; it does not bypass access controls or CAPTCHA.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from html.parser import HTMLParser
import ipaddress
import socket
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


CATEGORIES = {"ecommerce", "crypto", "microtasks", "services", "sports", "horse_racing", "trading"}


def validate_public_url(url: str) -> str:
    """Require HTTPS and reject localhost/private/reserved IP destinations."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url is required")
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only public HTTPS URLs without embedded credentials are accepted")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("Local destinations are not permitted")
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise ValueError("Non-public IP destinations are not permitted")
    except ValueError as exc:
        if "not permitted" in str(exc):
            raise
        # A non-IP hostname is resolved below before fetching.
    else:
        return url.strip()
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ValueError("Hostname could not be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("Hostname resolves to a non-public address")
    return url.strip()


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.description = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "meta" and attributes.get("name", "").lower() == "description":
            self.description = attributes.get("content") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data.strip())


@dataclass(frozen=True)
class DiscoveryReport:
    target_name: str
    url: str
    category: str
    status: str
    access_method: str
    requires_auth: str
    read_only_feasible: bool
    risk_level: str
    http_status: int | None = None
    page_title: str = ""
    description: str = ""
    notes: str = ""


class OsintDiscoverer:
    """Discover and assess user/brain-proposed candidates without autonomous sign-in."""

    def __init__(self, cognitive_brain_client: Any = None, timeout_seconds: float = 8.0) -> None:
        self.brain = cognitive_brain_client
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 20.0))

    def analyze_target(self, target_name: str, target_url: str, category: str) -> dict[str, Any]:
        if not isinstance(target_name, str) or not target_name.strip():
            raise ValueError("target_name is required")
        if category not in CATEGORIES:
            raise ValueError(f"category must be one of {sorted(CATEGORIES)}")
        url = validate_public_url(target_url)
        report = DiscoveryReport(
            target_name=target_name.strip(), url=url, category=category, status="unknown",
            access_method="browser_or_documented_api", requires_auth="unknown",
            read_only_feasible=False, risk_level="unassessed",
            notes="Public metadata only; authentication, API permissions and terms require review.",
        )
        request = Request(url, headers={"User-Agent": "VeroxResearch/1.0 (+public metadata; contact site owner)"}, method="GET")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                content_type = response.headers.get("Content-Type", "")
                status = int(response.status)
                body = response.read(512_000).decode("utf-8", errors="replace") if "html" in content_type.lower() else ""
            parser = _MetadataParser()
            parser.feed(body)
            report = DiscoveryReport(
                **{**asdict(report), "status": "inspected" if status < 400 else "http_error",
                   "http_status": status, "page_title": " ".join(parser.title_parts).strip(),
                   "description": parser.description[:1000],
                   "notes": "Only public metadata fetched. No login, form submission, CAPTCHA bypass, or account action performed."}
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            report = DiscoveryReport(**{**asdict(report), "status": "unavailable", "notes": f"Public metadata fetch failed: {type(exc).__name__}."})
        result = asdict(report)
        if self.brain is not None and callable(getattr(self.brain, "evaluate_osint_report", None)):
            # Brain evaluation may annotate the report; it cannot override safety fields.
            evaluated = self.brain.evaluate_osint_report(dict(result))
            if isinstance(evaluated, dict):
                for key in ("target_name", "url", "category", "status", "http_status"):
                    evaluated[key] = result[key]
                result.update(evaluated)
                result["read_only_feasible"] = False
                result["risk_level"] = result["risk_level"] if result["risk_level"] in {"low", "medium", "high", "unassessed"} else "unassessed"
        return result

    def assess_candidates(self, intent: str, candidates: Iterable[dict[str, str]]) -> dict[str, Any]:
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError("intent is required")
        reports = []
        for candidate in candidates:
            reports.append(self.analyze_target(candidate["name"], candidate["url"], candidate["category"]))
        return {"intent": intent.strip(), "candidate_count": len(reports), "reports": reports}
