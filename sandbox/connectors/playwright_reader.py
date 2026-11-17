"""Playwright page reader for public or already-authorized sessions.

This adapter never fills forms, clicks controls, submits orders, or bypasses bot
protections. Authentication should use a platform-approved read-only account/session.
"""
from __future__ import annotations

from typing import Any

from sandbox.connectors.base_connector import BaseReadOnlyConnector
from sandbox.osint_discoverer import validate_public_url


class PlaywrightReadOnlyReader(BaseReadOnlyConnector):
    def __init__(self, platform_name: str, base_url: str, *, headless: bool = True, timeout_ms: int = 15000) -> None:
        super().__init__(platform_name, validate_public_url(base_url))
        self.headless = headless
        self.timeout_ms = max(1000, min(int(timeout_ms), 30000))
        self._playwright = None
        self._browser = None
        self._page = None

    def connect(self) -> bool:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Install the declared Playwright dependency and browser binaries first") from exc
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=self.headless)
            context = self._browser.new_context(viewport={"width": 1280, "height": 800})
            self._page = context.new_page()
            self._page.set_default_timeout(self.timeout_ms)
            response = self._page.goto(self.base_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            if response is not None and response.status >= 400:
                raise ConnectionError(f"Target returned HTTP {response.status}")
            self.is_connected = True
            return True
        except Exception:
            self.disconnect()
            raise

    def fetch_state(self, *, selectors: dict[str, str] | None = None) -> dict[str, Any]:
        if not self.is_connected or self._page is None:
            raise ConnectionError("Connector is not connected")
        data: dict[str, Any] = {"title": self._page.title()}
        for key, selector in (selectors or {}).items():
            if not isinstance(key, str) or not key.strip() or not isinstance(selector, str) or not selector.strip():
                raise ValueError("Selectors must be non-empty strings")
            data[key] = self._page.locator(selector).all_text_contents()
        return {"platform": self.platform_name, "url": self._page.url,
                "source_type": "playwright_read_only", "observed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                "data": data}

    def disconnect(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            self._browser = None
            if self._playwright is not None:
                self._playwright.stop()
            self._playwright = None
            self._page = None
            self.is_connected = False
