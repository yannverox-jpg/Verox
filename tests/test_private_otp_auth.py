import base64
import hashlib
import hmac
import struct
import time
from types import SimpleNamespace

from api import index as auth_api


def _totp(secret: str, timestamp: int | None = None) -> str:
    key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
    counter = int((timestamp if timestamp is not None else time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{number:06d}"


def test_totp_accepts_current_code_and_rejects_bad_code(monkeypatch):
    secret = base64.b32encode(b"0123456789abcdef0123").decode().rstrip("=")
    monkeypatch.setenv("VEROX_TOTP_SECRET", secret)
    assert auth_api._valid_totp(_totp(secret))
    assert not auth_api._valid_totp("00000")
    assert not auth_api._valid_totp("abcdef")


def test_totp_fails_closed_without_configured_secret(monkeypatch):
    monkeypatch.delenv("VEROX_TOTP_SECRET", raising=False)
    assert not auth_api._valid_totp("123456")


def test_signed_session_is_valid_and_tampering_is_rejected(monkeypatch):
    monkeypatch.setenv("VEROX_SESSION_SECRET", "unit-test-session-secret-that-is-long-enough")
    token = auth_api._issue_private_session()
    request = SimpleNamespace(headers={"authorization": f"Bearer {token}"})
    assert auth_api._private_session_valid(request)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    request.headers["authorization"] = f"Bearer {tampered}"
    assert not auth_api._private_session_valid(request)


def test_session_cookie_is_read_and_requires_signature(monkeypatch):
    monkeypatch.setenv("VEROX_SESSION_SECRET", "unit-test-session-secret-that-is-long-enough")
    token = auth_api._issue_private_session()
    request = SimpleNamespace(headers={"cookie": f"other=x; verox_owner_session={token}"})
    assert auth_api._private_session_valid(request)
    request.headers["cookie"] = "verox_owner_session=forged.token"
    assert not auth_api._private_session_valid(request)
