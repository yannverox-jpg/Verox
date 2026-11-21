"""Bounded read-only retrieval for allowlisted public source pages."""
from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

import httpx

from verox_brain.source_registry import get_source

MAX_RESPONSE_BYTES = 750_000
MAX_EXTRACTED_CHARS = 18_000
TIMEOUT_SECONDS = 8.0
USER_AGENT = "VeroxReadOnlyResearch/1.0 (+public-source research; no automated actions)"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg", "nav", "footer"}:
            self.skip_depth += 1
        elif tag.lower() in {"p", "div", "br", "li", "h1", "h2", "h3", "tr"} and not self.skip_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg", "nav", "footer"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            clean = " ".join(data.split())
            if clean:
                self.parts.append(clean)

    def text(self) -> str:
        return " ".join(self.parts).replace("\\n ", "\n").strip()[:MAX_EXTRACTED_CHARS]


def fetch_registered_source(source_id: str) -> dict[str, Any]:
    """Fetch a registered public HTTPS page using GET only, with strict bounds.

    No arbitrary URLs, credentials, cookies, redirects, form submissions, or
    persistent writes are accepted by this function.
    """
    source = get_source(source_id)
    if not source:
        raise ValueError("unknown_source")
    if source["access"] not in {"public_web", "web", "web_or_api", "web_or_account", "web_or_app"}:
        raise PermissionError("source_requires_account_or_explicit_access")
    url = source["url"]
    if not url.startswith("https://"):
        raise ValueError("https_required")
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"}
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    return {"source_id": source_id, "source_name": source["name"], "url": url,
                            "status": "redirect_not_followed", "http_status": response.status_code,
                            "content_type": response.headers.get("content-type", "")[:120],
                            "text": "", "truncated": False}
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if not any(kind in content_type for kind in ("text/html", "text/plain", "application/xhtml+xml")):
                    return {"source_id": source_id, "source_name": source["name"], "url": url,
                            "status": "unsupported_content_type", "http_status": response.status_code,
                            "content_type": content_type[:120], "text": "", "truncated": False}
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    if len(chunks) + len(chunk) > MAX_RESPONSE_BYTES:
                        remaining = MAX_RESPONSE_BYTES - len(chunks)
                        if remaining > 0:
                            chunks.extend(chunk[:remaining])
                        break
                    chunks.extend(chunk)
                raw = bytes(chunks).decode(response.encoding or "utf-8", errors="replace")
                if "text/html" in content_type or "xhtml" in content_type:
                    parser = _TextExtractor()
                    parser.feed(raw)
                    extracted = parser.text()
                else:
                    extracted = " ".join(raw.split())[:MAX_EXTRACTED_CHARS]
                return {"source_id": source_id, "source_name": source["name"], "url": url,
                        "status": "retrieved_public_page", "http_status": response.status_code,
                        "content_type": content_type[:120], "text": extracted,
                        "truncated": len(chunks) >= MAX_RESPONSE_BYTES or len(extracted) >= MAX_EXTRACTED_CHARS,
                        "read_only": True, "persistent_storage": False}
    except httpx.HTTPError as exc:
        return {"source_id": source_id, "source_name": source["name"], "url": url,
                "status": "fetch_error", "error_type": type(exc).__name__, "text": "",
                "read_only": True}
