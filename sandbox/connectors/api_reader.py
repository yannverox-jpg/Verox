"""GET-only API reader for documented, authorized read endpoints."""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from sandbox.connectors.base_connector import BaseReadOnlyConnector
from sandbox.osint_discoverer import validate_public_url


class APIReadOnlyReader(BaseReadOnlyConnector):
    def __init__(self, platform_name: str, base_url: str, *, bearer_token_env: str | None = None, timeout_seconds: float = 10.0) -> None:
        super().__init__(platform_name, validate_public_url(base_url))
        self.bearer_token_env = bearer_token_env
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 20.0))

    def connect(self) -> bool:
        # A GET to the base URL is not assumed safe for every API; connectivity is
        # established by the first explicitly supplied documented read endpoint.
        self.is_connected = True
        return True

    def fetch_state(self, *, endpoint: str, query: dict[str, str] | None = None) -> dict[str, Any]:
        if not self.is_connected:
            raise ConnectionError("Connector is not connected")
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("A documented read endpoint is required")
        url = urljoin(self.base_url.rstrip("/") + "/", endpoint.lstrip("/"))
        base, target = urlparse(self.base_url), urlparse(url)
        if (base.scheme, base.hostname, base.port) != (target.scheme, target.hostname, target.port):
            raise ValueError("API endpoint must remain on the configured origin")
        if target.scheme != "https":
            raise ValueError("API endpoint must use HTTPS")
        if query:
            from urllib.parse import urlencode
            url += ("&" if "?" in url else "?") + urlencode(query)
        headers = {"Accept": "application/json", "User-Agent": "VeroxReadOnly/1.0"}
        if self.bearer_token_env:
            token = os.environ.get(self.bearer_token_env)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        request = Request(url, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(1_000_000)
                content_type = response.headers.get("Content-Type", "")
                if "json" not in content_type.lower():
                    raise ValueError("Read endpoint did not return a JSON content type")
                payload = json.loads(raw.decode("utf-8"))
                return {"platform": self.platform_name, "url": url, "http_status": response.status,
                        "observed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                        "source_type": "documented_api_get", "data": payload}
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ConnectionError(f"Read-only API request failed: {type(exc).__name__}") from exc

    def disconnect(self) -> None:
        self.is_connected = False
