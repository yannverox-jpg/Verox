"""Shared verification for the short-lived owner session cookie."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os


def is_valid_owner_session(request) -> bool:
    """Verify the signed, 15-minute owner session without exposing its secret."""
    secret = os.getenv("VEROX_SESSION_SECRET", "").strip()
    if len(secret) < 32:
        return False

    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token:
        from http.cookies import SimpleCookie

        cookie = SimpleCookie()
        try:
            cookie.load(request.headers.get("cookie", ""))
            morsel = cookie.get("verox_owner_session")
            token = morsel.value if morsel else ""
        except Exception:
            return False

    if not token or "." not in token:
        return False
    body, supplied = token.rsplit(".", 1)
    try:
        expected = base64.urlsafe_b64encode(
            hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
        ).decode("ascii").rstrip("=")
        if not hmac.compare_digest(expected, supplied):
            return False
        payload = json.loads(
            base64.urlsafe_b64decode(body + "=" * ((4 - len(body) % 4) % 4))
        )
        now = int(__import__("time").time())
        issued = int(payload.get("iat", 0)) if isinstance(payload, dict) else 0
        expires = int(payload.get("exp", 0)) if isinstance(payload, dict) else 0
        return issued <= now < expires and expires - issued <= 900
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeError):
        return False
