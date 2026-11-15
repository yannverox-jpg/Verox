"""Authenticated, read-only HTTP bridge from Verox to a local OpenAI-compatible model.

Run on the device hosting LM Studio, or on a PC that can reach PocketPal on the LAN.
Expose it remotely only through a private/authenticated tunnel. Never bind this service
to 0.0.0.0 unless protected by a trusted network boundary.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import httpx

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("VeroxModelBridge")
UPSTREAM = os.getenv("MODEL_UPSTREAM_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
UPSTREAM_KEY = os.getenv("MODEL_UPSTREAM_API_KEY", "").strip()
BRIDGE_KEY = os.getenv("MODEL_BRIDGE_API_KEY", "").strip()
HOST = os.getenv("MODEL_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.getenv("MODEL_BRIDGE_PORT", "8787"))
ALLOWED_PATHS = {"/v1/models", "/v1/chat/completions"}


class Handler(BaseHTTPRequestHandler):
    server_version = "VeroxModelBridge/1.0"

    def log_message(self, fmt, *args):
        # Do not log request bodies, prompts, authorization headers or model output.
        logger.info("%s %s", self.command, self.path.split("?")[0])

    def _reply(self, status: int, body: bytes, content_type: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = "Bearer " + BRIDGE_KEY
        return bool(BRIDGE_KEY) and hmac.compare_digest(supplied, expected)

    def do_GET(self):
        path = urlparse(self.path).path
        if path != "/health":
            if path not in ALLOWED_PATHS:
                return self._reply(404, b'{"error":"route not allowed"}')
            if not self._authorized():
                return self._reply(401, b'{"error":"unauthorized"}')
            return self._forward("GET", path, b"")

        status = 200 if BRIDGE_KEY and UPSTREAM.startswith(("http://", "https://")) else 503
        return self._reply(status, json.dumps({
            "ok": status == 200,
            "upstream_configured": bool(UPSTREAM),
            "authentication_configured": bool(BRIDGE_KEY),
            "allowed_paths": sorted(ALLOWED_PATHS),
        }).encode())

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ALLOWED_PATHS or path != "/v1/chat/completions":
            return self._reply(404, b'{"error":"route not allowed"}')
        if not self._authorized():
            return self._reply(401, b'{"error":"unauthorized"}')
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._reply(400, b'{"error":"invalid content length"}')
        if length < 1 or length > 1_000_000:
            return self._reply(413, b'{"error":"request size must be 1..1000000 bytes"}')
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
                return self._reply(400, b'{"error":"messages array required"}')
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._reply(400, b'{"error":"invalid JSON"}')
        # This bridge forwards model inference only; it never executes tool calls.
        return self._forward("POST", path, body)

    def _forward(self, method: str, path: str, body: bytes):
        headers = {"Accept": "application/json"}
        if UPSTREAM_KEY:
            headers["Authorization"] = "Bearer " + UPSTREAM_KEY
        if method == "POST":
            headers["Content-Type"] = "application/json"
        try:
            with httpx.Client(timeout=90.0, follow_redirects=False) as client:
                response = client.request(method, UPSTREAM + path, content=body or None, headers=headers)
            safe_headers = response.headers.get("content-type", "application/json")
            return self._reply(response.status_code, response.content, safe_headers)
        except Exception as exc:
            logger.warning("Upstream unavailable (%s)", type(exc).__name__)
            return self._reply(502, b'{"error":"model upstream unavailable"}')


if __name__ == "__main__":
    if not BRIDGE_KEY:
        raise SystemExit("MODEL_BRIDGE_API_KEY is required; refusing to start without authentication.")
    if not UPSTREAM.startswith(("http://", "https://")):
        raise SystemExit("MODEL_UPSTREAM_BASE_URL must be an HTTP(S) URL.")
    logger.info("Bridge listening on %s:%s; upstream=%s", HOST, PORT, UPSTREAM)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
