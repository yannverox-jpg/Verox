"""Server-side API for Verox Android push notifications and human approval decisions."""
from __future__ import annotations
import base64, hashlib, hmac, json, os, re, secrets, time
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse
import httpx

MAX_BODY = 24_000
SESSION_COOKIE = "verox_approval_session"

def _json(h, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode()
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Content-Type-Options", "nosniff")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)

def _read(h):
    try: size = int(h.headers.get("Content-Length", "0"))
    except ValueError: size = 0
    if size <= 0 or size > MAX_BODY: raise ValueError("invalid_body_size")
    obj = json.loads(h.rfile.read(size))
    if not isinstance(obj, dict): raise ValueError("invalid_json")
    return obj

def _db(method, table, *, params=None, body=None, prefer="return=representation"):
    base = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base.startswith("https://") or not key:
        raise RuntimeError("approval_database_not_configured")
    with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as c:
        r = c.request(method, f"{base}/rest/v1/{table}", params=params, json=body,
          headers={"apikey":key,"Authorization":f"Bearer {key}","Content-Type":"application/json",
                   "Prefer":prefer})
        r.raise_for_status()
        if r.status_code == 204 or not r.content: return []
        return r.json()

def _b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _unb64u(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

def _totp_valid(code):
    secret = os.getenv("VEROX_TOTP_SECRET", "").strip().replace(" ", "").upper()
    if not secret or not re.fullmatch(r"[A-Z2-7]+=*", secret) or not re.fullmatch(r"\d{6}", str(code or "")):
        return False
    try:
        key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8), casefold=False)
    except Exception: return False
    import struct
    now = int(time.time()) // 30
    for step in (now-1, now, now+1):
        digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
        offset = digest[-1] & 0x0f
        number = (struct.unpack(">I", digest[offset:offset+4])[0] & 0x7fffffff) % 1_000_000
        if hmac.compare_digest(f"{number:06d}", str(code)): return True
    return False

def _session_sign(payload):
    secret = os.getenv("VEROX_SESSION_SECRET", "").encode()
    if len(secret) < 32: raise RuntimeError("session_secret_not_configured")
    encoded = _b64u(json.dumps(payload,separators=(",",":")).encode())
    sig = _b64u(hmac.new(secret, encoded.encode(), hashlib.sha256).digest())
    return encoded + "." + sig

def _session_valid(h):
    raw = h.headers.get("Cookie", "")
    token = ""
    for part in raw.split(";"):
        k, sep, v = part.strip().partition("=")
        if sep and k == SESSION_COOKIE: token = v
    try:
        encoded, sig = token.split(".", 1)
        secret = os.getenv("VEROX_SESSION_SECRET", "").encode()
        expected = _b64u(hmac.new(secret, encoded.encode(), hashlib.sha256).digest())
        if len(secret) < 32 or not hmac.compare_digest(sig, expected): return False
        payload = json.loads(_unb64u(encoded))
        return payload.get("sub") == "verox-human-operator" and int(payload.get("exp",0)) > int(time.time())
    except Exception: return False

def _cookie(h, token):
    h.send_header("Set-Cookie", f"{SESSION_COOKIE}={token}; Max-Age=900; Path=/; Secure; HttpOnly; SameSite=Strict")

def _json_with_cookie(h,status,payload,token):
    body=json.dumps(payload,ensure_ascii=False).encode()
    h.send_response(status)
    h.send_header("Content-Type","application/json; charset=utf-8")
    h.send_header("Cache-Control","no-store")
    h.send_header("X-Content-Type-Options","nosniff")
    _cookie(h,token)
    h.send_header("Content-Length",str(len(body)))
    h.end_headers()
    h.wfile.write(body)

def _vapid_send(subscription, payload):
    from pywebpush import webpush, WebPushException
    try:
        webpush(subscription_info=subscription, data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=os.getenv("VAPID_PRIVATE_KEY",""),
                vapid_claims={"sub":os.getenv("VAPID_SUBJECT","mailto:security@example.com")})
        return True
    except WebPushException as exc:
        status = getattr(getattr(exc,"response",None),"status_code",None)
        if status in (404,410):
            endpoint = subscription.get("endpoint")
            if endpoint:
                _db("PATCH","verox_approval_push_subscriptions",params={"endpoint":f"eq.{endpoint}"},body={"disabled_at":datetime.now(timezone.utc).isoformat()})
        return False
    except Exception: return False

class handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/api/approvals/vapid-public":
            key = os.getenv("VAPID_PUBLIC_KEY","").strip()
            if not key: _json(self,503,{"error":"push_not_configured"}); return
            _json(self,200,{"public_key":key}); return
        if path == "/api/approvals/requests":
            if not _session_valid(self): _json(self,401,{"error":"authentication_required"}); return
            try:
                rows = _db("GET","verox_human_approval_requests",params={
                  "select":"id,title,summary,action_type,target,operation_fingerprint,details,status,requested_by,created_at,expires_at",
                  "status":"eq.pending","expires_at":f"gt.{datetime.now(timezone.utc).isoformat()}",
                  "order":"created_at.desc","limit":"30"})
                _json(self,200,{"requests":rows}); return
            except Exception:
                _json(self,503,{"error":"approval_store_unavailable"}); return
        _json(self,404,{"error":"not_found"})

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        try: data = _read(self)
        except Exception: _json(self,400,{"error":"invalid_json"}); return

        if path == "/api/approvals/auth/otp":
            if not _totp_valid(data.get("code")): _json(self,401,{"error":"invalid_totp"}); return
            try:
                token = _session_sign({"sub":"verox-human-operator","exp":int(time.time())+900,"jti":secrets.token_hex(12)})
                _json_with_cookie(self,200,{"ok":True,"expires_in":900},token)
            except Exception: _json(self,503,{"error":"session_auth_not_configured"})
            return

        if path == "/api/approvals/push/subscribe":
            if not _session_valid(self): _json(self,401,{"error":"authentication_required"}); return
            sub = data.get("subscription")
            if not isinstance(sub,dict) or not str(sub.get("endpoint","")).startswith("https://") or not isinstance(sub.get("keys"),dict):
                _json(self,400,{"error":"invalid_subscription"}); return
            try:
                _db("POST","verox_approval_push_subscriptions",params={"on_conflict":"endpoint"},
                    body={"endpoint":sub["endpoint"],"subscription":sub,"last_seen_at":datetime.now(timezone.utc).isoformat(),"disabled_at":None},
                    prefer="resolution=merge-duplicates,return=representation")
                _json(self,201,{"ok":True}); return
            except Exception: _json(self,503,{"error":"subscription_store_unavailable"}); return

        if path == "/api/approvals/requests":
            expected = os.getenv("VEROX_INTERNAL_APPROVAL_TOKEN","").strip()
            supplied = self.headers.get("Authorization","").removeprefix("Bearer ").strip()
            if not expected or not supplied or not hmac.compare_digest(expected,supplied):
                _json(self,401,{"error":"internal_authentication_required"}); return
            title = str(data.get("title","")).strip()[:120]
            summary = str(data.get("summary","")).strip()[:240]
            action_type = str(data.get("action_type","")).strip()[:80]
            target = str(data.get("target","")).strip()[:240]
            fingerprint = str(data.get("operation_fingerprint","")).lower().strip()
            requested_by = str(data.get("requested_by","verox-runtime")).strip()[:100]
            details = data.get("details",{})
            try: ttl = max(60,min(1800,int(data.get("ttl_seconds",600))))
            except Exception: ttl = 600
            if not all([title,summary,action_type,target,requested_by]) or not re.fullmatch(r"[0-9a-f]{64}",fingerprint) or not isinstance(details,dict):
                _json(self,400,{"error":"invalid_approval_request"}); return
            now = datetime.now(timezone.utc)
            row = {"title":title,"summary":summary,"action_type":action_type,"target":target,
                   "operation_fingerprint":fingerprint,"requested_by":requested_by,"details":details,
                   "status":"pending","created_at":now.isoformat(),"expires_at":(now+timedelta(seconds=ttl)).isoformat()}
            try:
                created = _db("POST","verox_human_approval_requests",body=row)
                request_id = str(created[0]["id"])
                subs = _db("GET","verox_approval_push_subscriptions",params={"select":"subscription","disabled_at":"is.null","limit":"100"})
                vapid_ready = bool(os.getenv("VAPID_PUBLIC_KEY","").strip() and os.getenv("VAPID_PRIVATE_KEY","").strip())
                sent = 0
                if vapid_ready:
                    for item in subs:
                        if isinstance(item.get("subscription"),dict) and _vapid_send(item["subscription"],{
                          "request_id":request_id,"title":"Verox — Validation humaine requise",
                          "body":summary,"url":"/approvals.html?id="+request_id}): sent += 1
                _json(self,201,{"id":request_id,"status":"pending","expires_at":row["expires_at"],"notifications_sent":sent}); return
            except Exception: _json(self,503,{"error":"approval_request_store_unavailable"}); return

        match = re.fullmatch(r"/api/approvals/requests/([0-9a-fA-F-]{36})/decision",path)
        if match:
            if not _session_valid(self): _json(self,401,{"error":"authentication_required"}); return
            if not _totp_valid(data.get("code")): _json(self,401,{"error":"invalid_totp"}); return
            decision = str(data.get("decision","")).lower()
            if decision not in {"approve","reject"}: _json(self,400,{"error":"invalid_decision"}); return
            reason = str(data.get("reason","")).strip()[:500]
            now = datetime.now(timezone.utc)
            status = "approved" if decision == "approve" else "rejected"
            try:
                rows = _db("PATCH","verox_human_approval_requests",params={
                  "id":f"eq.{match.group(1)}","status":"eq.pending","expires_at":f"gt.{now.isoformat()}",
                  "select":"id,status,operation_fingerprint,action_type,target,details,expires_at"},
                  body={"status":status,"decided_by":"verox-human-operator","decision_reason":reason,
                        "decided_at":now.isoformat()})
                if not rows: _json(self,409,{"error":"request_expired_or_already_decided"}); return
                _json(self,200,{"ok":True,"request":rows[0]}); return
            except Exception: _json(self,503,{"error":"decision_store_unavailable"}); return

        _json(self,404,{"error":"not_found"})
