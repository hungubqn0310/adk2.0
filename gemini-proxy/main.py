import asyncio
import json
import logging
import os
import smtplib
import threading
import time
from datetime import datetime
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import psycopg2
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import Response, StreamingResponse
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

TARGET_BASE_URL = os.environ.get("TARGET_BASE_URL", "https://generativelanguage.googleapis.com")
DATABASE_URL = os.environ.get("SESSION_SERVICE_URI", "")

# ---------------------------------------------------------------------------
# Shared HTTP client (persistent) — reuse connections + fail fast on connect.
# Trước đây mỗi request tạo AsyncClient mới với timeout=120 áp cho CẢ connect,
# nên khi mạng blip, connect treo lâu rồi retry vòng key → 1 call mất ~15s.
# ---------------------------------------------------------------------------
# connect ngắn để fail nhanh (rồi retry nhanh); read dài cho LLM stream.
_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)
_HTTP_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=30.0)
# local_address="0.0.0.0" ép bind IPv4 → tránh treo do IPv6 hỏng trong Docker.
# retries=2: tự retry ở tầng connect khi "All connection attempts failed".
_HTTP_TRANSPORT = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=2)
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT, limits=_HTTP_LIMITS, transport=_HTTP_TRANSPORT
        )
    return _http_client


@app.on_event("shutdown")
async def _close_http_client():
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()

# ---------------------------------------------------------------------------
# Fernet decryption for API keys stored encrypted in DB
# ---------------------------------------------------------------------------
_ENC_KEY = os.environ.get("API_KEYS_ENCRYPTION_KEY", "")
_fernet = None
if _ENC_KEY:
    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(_ENC_KEY.encode())
    except Exception as _e:
        logger.warning("[proxy] API_KEYS_ENCRYPTION_KEY invalid, will use values as-is: %s", _e)


def _decrypt_key_value(value: str) -> str:
    if _fernet is None:
        return value
    try:
        return _fernet.decrypt(value.encode()).decode()
    except Exception:
        return value  # plaintext fallback (pre-encryption migration)


# ---------------------------------------------------------------------------
# Alert mailer — inline (no mmvn_b2c_agent dependency)
# ---------------------------------------------------------------------------
_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
_COOLDOWN_SECONDS = 15 * 60  # 15 minutes
_DASHBOARD_URL = os.environ.get("DASHBOARD_BASE_URL", "")
_SYSTEM_NAME = os.environ.get("DASHBOARD_SYSTEM_NAME", "AI Chatbot B2C MMVN")

_SMTP_HOST = os.environ.get("SMTP_HOST", "")
_SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
_SMTP_USER = os.environ.get("SMTP_USER", "")
_SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
_SMTP_FROM = os.environ.get("SMTP_FROM", _SMTP_USER)

_alert_lock = threading.Lock()


def _db_conn():
    dsn = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
    return psycopg2.connect(dsn, connect_timeout=5)


def _get_admin_emails() -> list[str]:
    if not DATABASE_URL:
        fallback = os.environ.get("SMTP_FROM", "")
        return [fallback] if fallback else []
    try:
        conn = _db_conn()
        cur = conn.cursor()
        cur.execute("SELECT email FROM dashboard_users WHERE email IS NOT NULL AND email != ''")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception as exc:
        logger.error("[alert_mailer] Failed to get admin emails: %s", exc)
        fallback = os.environ.get("SMTP_FROM", "")
        return [fallback] if fallback else []


def _was_recently_alerted(cooldown_key: str) -> bool:
    if not DATABASE_URL:
        return False
    try:
        conn = _db_conn()
        cur = conn.cursor()
        cur.execute("SELECT value FROM system_config WHERE key = %s", (cooldown_key,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return False
        last_ts = float(row[0])
        return (time.time() - last_ts) < _COOLDOWN_SECONDS
    except Exception:
        return False


def _mark_alerted(cooldown_key: str) -> None:
    if not DATABASE_URL:
        return
    try:
        conn = _db_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO system_config (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (cooldown_key, str(time.time())),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.error("[alert_mailer] Failed to mark alerted: %s", exc)


def _send_emails(recipients: list[str], subject: str, body: str, is_html: bool = False) -> None:
    if not _SMTP_HOST or not _SMTP_USER:
        logger.warning("[alert_mailer] SMTP not configured — cannot send alert email")
        return
    for to_addr in recipients:
        try:
            msg = MIMEText(body, "html" if is_html else "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = _SMTP_FROM
            msg["To"] = to_addr
            with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(_SMTP_USER, _SMTP_PASSWORD)
                smtp.sendmail(_SMTP_FROM, [to_addr], msg.as_string())
            logger.info("[alert_mailer] Alert email sent to %s", to_addr)
        except Exception as exc:
            logger.error("[alert_mailer] Failed to send email to %s: %s", to_addr, exc)


def _fire_blocker_alert(alert_key: str, reason: str) -> None:
    with _alert_lock:
        if _was_recently_alerted(alert_key):
            logger.debug("[alert_mailer] %s in cooldown — skipping", alert_key)
            return
        admins = _get_admin_emails()
        if not admins:
            logger.warning("[alert_mailer] No admin emails found — cannot send blocker alert (%s)", alert_key)
            return
        now_str = datetime.now(tz=_VN_TZ).strftime("%H:%M, %d/%m/%Y")
        subject = "[CẢNH BÁO KHẨN] Sự cố gián đoạn hệ thống AI Chatbot - Lỗi Blocker"
        body = (
            f"Dear Admin,<br><br>"
            f"Hệ thống <strong>{_SYSTEM_NAME}</strong> vừa ghi nhận một sự cố nghiêm trọng (lỗi Blocker) "
            f"khiến AI Chatbot hiện không thể hoạt động và phản hồi khách hàng.<br><br>"
            f"Chi tiết sự cố được hệ thống ghi nhận:<br>"
            f"<ul>"
            f"<li><strong>Thời gian xảy ra lỗi:</strong> {now_str}</li>"
            f"<li><strong>Nguyên nhân lỗi:</strong> {reason}</li>"
            f"</ul>"
            f"Trân trọng"
        )
        _send_emails(admins, subject, body, is_html=True)
        _mark_alerted(alert_key)


def fire_all_keys_disabled_alert() -> None:
    _fire_blocker_alert("alert_cooldown_all_keys_disabled", "Không có API key nào đang active")


def fire_no_active_keys_alert() -> None:
    _fire_blocker_alert("alert_cooldown_no_active_keys", "Không có API key nào đang active")


def _all_keys_intentionally_disabled() -> bool:
    """True when every configured key is explicitly disabled by admin."""
    if not DATABASE_URL:
        return False
    try:
        conn = _db_conn()
        cur = conn.cursor()
        cur.execute("SELECT value FROM system_config WHERE key = 'api_keys'")
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return False
        keys = json.loads(row[0])
        return bool(keys) and all(k.get("status") == "disabled" for k in keys)
    except Exception:
        return False


def _fire_alert_async(fn) -> None:
    threading.Thread(target=fn, daemon=True).start()


_key_lock = asyncio.Lock()
_current_key_index = 0

_PROXY_API_KEY_NAMES = ["api_key", "api-key", "key", "x-goog-api-key"]

# ---------------------------------------------------------------------------
# TTL cache for API keys — avoids opening a new DB connection on every request
# ---------------------------------------------------------------------------
_keys_cache: list[str] = []
_keys_cache_ts: float = 0.0
_KEYS_CACHE_TTL = float(os.environ.get("PROXY_KEYS_CACHE_TTL", "30"))  # seconds


def _load_keys_from_db() -> list[str]:
    """Open one psycopg2 connection, fetch keys, close immediately."""
    if not DATABASE_URL:
        logger.warning("[proxy] SESSION_SERVICE_URI not set, falling back to API_KEYS env")
        raw = os.environ.get("API_KEYS", "")
        return [k.strip() for k in raw.split(";") if k.strip()]

    try:
        dsn = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
        conn = psycopg2.connect(dsn, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT value FROM system_config WHERE key = 'api_keys'")
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return []
        keys = json.loads(row[0])
        return [
            _decrypt_key_value(k["value"])
            for k in keys
            if k.get("status", "active") == "active" and k.get("value")
        ]
    except Exception as exc:
        logger.error("[proxy] Failed to load keys from DB: %s", exc)
        return []


def _get_active_keys() -> list[str]:
    """Return cached active keys, refreshing from DB at most once per TTL window."""
    global _keys_cache, _keys_cache_ts
    if time.monotonic() - _keys_cache_ts > _KEYS_CACHE_TTL:
        _keys_cache = _load_keys_from_db()
        _keys_cache_ts = time.monotonic()
        logger.debug("[proxy] Keys cache refreshed: %d active key(s)", len(_keys_cache))
    return _keys_cache


async def _next_key(active_keys: list[str]) -> str:
    global _current_key_index
    async with _key_lock:
        key = active_keys[_current_key_index % len(active_keys)]
        _current_key_index = (_current_key_index + 1) % len(active_keys)
        return key


def _strip_request_headers(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in ("host", "content-length")}


def _strip_response_headers(headers) -> dict:
    # 'server'/'date'/'connection' do uvicorn tự thêm → nếu forward upstream sẽ TRÙNG,
    # client aiohttp của ADK 2.0 reject ("Duplicate 'Server' header").
    skip = {
        "content-encoding", "content-length", "transfer-encoding",
        "server", "date", "connection", "keep-alive",
    }
    return {k: v for k, v in headers.items() if k.lower() not in skip}


def _inject_key(params: dict, headers: dict, api_key: str) -> tuple[dict, dict]:
    params = params.copy()
    headers = headers.copy()
    replaced = False
    for name in _PROXY_API_KEY_NAMES:
        if name in params:
            params[name] = api_key
            replaced = True
        if name in headers:
            headers[name] = api_key
            replaced = True
    if not replaced:
        params["key"] = api_key
    return params, headers


class TestKeyRequest(BaseModel):
    value: str


@app.post("/test-key")
async def test_key(body: TestKeyRequest):
    """Test if a Gemini API key is valid by calling the models list endpoint."""
    key = body.value.strip()
    if not key:
        return {"success": False, "message": "API Key không được để trống"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{TARGET_BASE_URL}/v1beta/models",
                params={"key": key},
            )
        if resp.status_code == 200:
            return {"success": True, "message": "API Key hợp lệ"}
        if resp.status_code == 400:
            return {"success": False, "message": "API Key không hợp lệ"}
        if resp.status_code == 403:
            return {"success": False, "message": "API Key bị từ chối quyền truy cập"}
        if resp.status_code == 429:
            return {"success": False, "message": "API Key đang bị rate limit"}
        return {"success": False, "message": f"Lỗi từ Google: HTTP {resp.status_code}"}
    except httpx.TimeoutException:
        return {"success": False, "message": "Timeout — không kết nối được Google"}
    except Exception as exc:
        logger.error("[proxy] test_key error: %s", exc)
        return {"success": False, "message": "Lỗi kiểm tra key"}


@app.get("/")
async def index():
    active_keys = _get_active_keys()
    return {"message": "Gemini API Proxy (key rotation)", "active_keys": len(active_keys)}


@app.get("/models")
async def list_models():
    """Return Gemini models that support generateContent. Uses one active key directly."""
    active_keys = _get_active_keys()
    if not active_keys:
        raise HTTPException(status_code=503, detail="No active Gemini API keys configured.")
    key = active_keys[0]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{TARGET_BASE_URL}/v1beta/models",
                params={"key": key},
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        data = resp.json()
        models = [
            m for m in data.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        return {"models": models}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[proxy] list_models error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch model list")


@app.get("/embedding-models")
async def list_embedding_models():
    """Return Gemini models that support embedContent. Uses one active key directly."""
    active_keys = _get_active_keys()
    if not active_keys:
        raise HTTPException(status_code=503, detail="No active Gemini API keys configured.")
    key = active_keys[0]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{TARGET_BASE_URL}/v1beta/models",
                params={"key": key},
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        data = resp.json()
        models = [
            m for m in data.get("models", [])
            if "embedContent" in m.get("supportedGenerationMethods", [])
        ]
        return {"models": models}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[proxy] list_embedding_models error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch embedding model list")


@app.api_route(
    "/v1beta/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"],
)
async def proxy_streaming(path: str, request: Request):
    """Streaming proxy for /v1beta/* paths."""
    active_keys = _get_active_keys()
    if not active_keys:
        if _all_keys_intentionally_disabled():
            _fire_alert_async(fire_all_keys_disabled_alert)
        else:
            _fire_alert_async(fire_no_active_keys_alert)
        raise HTTPException(status_code=503, detail="No active Gemini API keys configured.")

    target_url = f"{TARGET_BASE_URL}/v1beta/{path}"
    query_params = dict(request.query_params)
    headers = _strip_request_headers(dict(request.headers))
    req_body = await request.body()

    client = _get_http_client()
    for attempt in range(len(active_keys)):
        api_key = await _next_key(active_keys)
        req_params, req_headers = _inject_key(query_params, headers, api_key)

        try:
            proxy_resp = await client.send(
                client.build_request(
                    method=request.method,
                    url=target_url,
                    headers=req_headers,
                    params=req_params,
                    content=req_body,
                ),
                stream=True,
            )
        except httpx.RequestError as exc:
            logger.warning("[proxy] Request error key ...%s: %s", api_key[-8:], exc)
            continue

        if proxy_resp.status_code == 429:
            await proxy_resp.aclose()
            logger.warning("[proxy] 429 key ...%s (attempt %d/%d)", api_key[-8:], attempt + 1, len(active_keys))
            continue

        resp_headers = _strip_response_headers(proxy_resp.headers)

        # Chỉ đóng response, KHÔNG đóng client dùng chung (persistent, tái sử dụng).
        async def _stream(resp=proxy_resp):
            try:
                async for chunk in resp.aiter_bytes(chunk_size=1024):
                    yield chunk
            finally:
                await resp.aclose()

        return StreamingResponse(
            content=_stream(),
            status_code=proxy_resp.status_code,
            headers=resp_headers,
            media_type=proxy_resp.headers.get("content-type"),
        )

    raise HTTPException(status_code=429, detail="All Gemini API keys hit rate limit.")


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"],
)
async def proxy(path: str, request: Request):
    """General proxy for all other paths."""
    active_keys = _get_active_keys()
    if not active_keys:
        if _all_keys_intentionally_disabled():
            _fire_alert_async(fire_all_keys_disabled_alert)
        else:
            _fire_alert_async(fire_no_active_keys_alert)
        raise HTTPException(status_code=503, detail="No active Gemini API keys configured.")

    target_url = f"{TARGET_BASE_URL}/{path}"
    query_params = dict(request.query_params)
    headers = _strip_request_headers(dict(request.headers))
    req_body = await request.body()

    client = _get_http_client()
    for attempt in range(len(active_keys)):
        api_key = await _next_key(active_keys)
        req_params, req_headers = _inject_key(query_params, headers, api_key)

        try:
            proxy_resp = await client.request(
                method=request.method,
                url=target_url,
                headers=req_headers,
                params=req_params,
                content=req_body,
            )

            if proxy_resp.status_code == 429:
                logger.warning("[proxy] 429 key ...%s (attempt %d/%d)", api_key[-8:], attempt + 1, len(active_keys))
                continue

            resp_headers = _strip_response_headers(proxy_resp.headers)
            return Response(
                content=proxy_resp.content,
                status_code=proxy_resp.status_code,
                headers=resp_headers,
                media_type=proxy_resp.headers.get("content-type"),
            )
        except httpx.RequestError as exc:
            logger.warning("[proxy] Request error key ...%s: %s", api_key[-8:], exc)
            continue

    raise HTTPException(status_code=429, detail="All Gemini API keys hit rate limit.")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 16801))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
