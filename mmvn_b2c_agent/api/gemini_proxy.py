"""
Gemini API proxy with round-robin key rotation.
Active keys are managed via the admin dashboard (stored in system_config table).
Mount at /gemini-proxy so the chatbot points GOOGLE_API_BASE_URL there.
"""

import asyncio
import logging
import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response, StreamingResponse

from mmvn_b2c_agent.api.admin_config import _load_api_keys
from mmvn_b2c_agent.shared.alert_mailer import fire_all_keys_429_alert, fire_all_keys_disabled_alert, fire_no_active_keys_alert

logger = logging.getLogger(__name__)

TARGET_BASE_URL = os.getenv(
    "GEMINI_TARGET_BASE_URL", "https://generativelanguage.googleapis.com"
)

gemini_proxy_router = APIRouter(prefix="/gemini-proxy", tags=["gemini-proxy"])

_key_lock = asyncio.Lock()
_current_key_index = 0

_PROXY_API_KEY_NAMES = ["api_key", "api-key", "key", "x-goog-api-key"]


def _get_active_keys() -> list[str]:
    keys = _load_api_keys()
    return [k["value"] for k in keys if k.get("status", "active") == "active" and k.get("value")]


def _all_keys_intentionally_disabled() -> bool:
    """True when every configured key is explicitly disabled by admin (not a system fault)."""
    keys = _load_api_keys()
    if not keys:
        return False
    return all(k.get("status") == "disabled" for k in keys)


async def _next_key(active_keys: list[str]) -> str:
    global _current_key_index
    async with _key_lock:
        key = active_keys[_current_key_index % len(active_keys)]
        _current_key_index = (_current_key_index + 1) % len(active_keys)
        return key


def _strip_headers(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in ("host", "content-length")}


def _strip_response_headers(headers) -> dict:
    skip = {"content-encoding", "content-length", "transfer-encoding"}
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


@gemini_proxy_router.get("")
async def proxy_info():
    active_keys = _get_active_keys()
    return {"message": "Gemini API proxy", "active_keys": len(active_keys)}


@gemini_proxy_router.api_route(
    "/v1beta/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"],
)
async def proxy_streaming(path: str, request: Request):
    """Streaming proxy for /v1beta/* — handles SSE / large responses."""
    active_keys = _get_active_keys()
    if not active_keys:
        if _all_keys_intentionally_disabled():
            asyncio.get_event_loop().run_in_executor(None, fire_all_keys_disabled_alert)
        else:
            asyncio.get_event_loop().run_in_executor(None, fire_no_active_keys_alert)
        raise HTTPException(status_code=503, detail="No active Gemini API keys configured.")

    target_url = f"{TARGET_BASE_URL}/v1beta/{path}"
    query_params = dict(request.query_params)
    headers = _strip_headers(dict(request.headers))
    req_body = await request.body()

    for attempt in range(len(active_keys)):
        api_key = await _next_key(active_keys)
        req_params, req_headers = _inject_key(query_params, headers, api_key)

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    method=request.method,
                    url=target_url,
                    headers=req_headers,
                    params=req_params,
                    content=req_body,
                ) as proxy_resp:
                    if proxy_resp.status_code == 429:
                        logger.warning(
                            "[gemini-proxy] 429 on key ...%s (attempt %d/%d)",
                            api_key[-8:], attempt + 1, len(active_keys),
                        )
                        continue

                    resp_headers = _strip_response_headers(proxy_resp.headers)

                    async def _stream():
                        async for chunk in proxy_resp.aiter_bytes(chunk_size=1024):
                            yield chunk

                    return StreamingResponse(
                        content=_stream(),
                        status_code=proxy_resp.status_code,
                        headers=resp_headers,
                        media_type=proxy_resp.headers.get("content-type"),
                    )
        except httpx.RequestError as exc:
            logger.warning("[gemini-proxy] request error key ...%s: %s", api_key[-8:], exc)
            continue

    asyncio.get_event_loop().run_in_executor(None, fire_all_keys_429_alert)
    raise HTTPException(status_code=429, detail="All Gemini API keys hit rate limit.")


@gemini_proxy_router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"],
)
async def proxy(path: str, request: Request):
    """General proxy for all other Gemini API paths."""
    active_keys = _get_active_keys()
    if not active_keys:
        if _all_keys_intentionally_disabled():
            asyncio.get_event_loop().run_in_executor(None, fire_all_keys_disabled_alert)
        else:
            asyncio.get_event_loop().run_in_executor(None, fire_no_active_keys_alert)
        raise HTTPException(status_code=503, detail="No active Gemini API keys configured.")

    target_url = f"{TARGET_BASE_URL}/{path}"
    query_params = dict(request.query_params)
    headers = _strip_headers(dict(request.headers))
    req_body = await request.body()

    async with httpx.AsyncClient(timeout=60.0) as client:
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
                    logger.warning(
                        "[gemini-proxy] 429 on key ...%s (attempt %d/%d)",
                        api_key[-8:], attempt + 1, len(active_keys),
                    )
                    continue

                resp_headers = _strip_response_headers(proxy_resp.headers)
                return Response(
                    content=proxy_resp.content,
                    status_code=proxy_resp.status_code,
                    headers=resp_headers,
                    media_type=proxy_resp.headers.get("content-type"),
                )
            except httpx.RequestError as exc:
                logger.warning("[gemini-proxy] request error key ...%s: %s", api_key[-8:], exc)
                continue

    asyncio.get_event_loop().run_in_executor(None, fire_all_keys_429_alert)
    raise HTTPException(status_code=429, detail="All Gemini API keys hit rate limit.")
