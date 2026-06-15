"""
Admin Config API — hot-reload system configuration stored in SQLite.
Covers: Budget, Rate Limit, Token Limit, Notification thresholds, Prometheus alerts.
"""

import asyncio
import os
import json
import logging
import uuid
from typing import Optional
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

import httpx
import yaml

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import create_engine, text
from mmvn_b2c_agent.shared.config_service import config_service
from mmvn_b2c_agent.shared.budget_monitor import check_and_notify_budget
from mmvn_b2c_agent.api.dashboard_auth import require_permission

logger = logging.getLogger(__name__)

_SESSION_URI = os.getenv("SESSION_SERVICE_URI", "sqlite:///./data/sessions.db")

# ---------------------------------------------------------------------------
# API Key encryption helpers
# ---------------------------------------------------------------------------
_ENC_KEY = os.getenv("API_KEYS_ENCRYPTION_KEY", "")
_fernet: Fernet | None = None

if _ENC_KEY:
    try:
        _fernet = Fernet(_ENC_KEY.encode())
    except Exception as e:
        logger.warning("API_KEYS_ENCRYPTION_KEY invalid, keys will be stored plaintext: %s", e)
else:
    logger.warning("API_KEYS_ENCRYPTION_KEY not set, keys will be stored plaintext")


def _encrypt_key_value(value: str) -> str:
    if _fernet is None:
        return value
    return _fernet.encrypt(value.encode()).decode()


def _decrypt_key_value(value: str) -> str:
    if _fernet is None:
        return value
    # Graceful fallback: if value is already plaintext (migration), return as-is
    try:
        return _fernet.decrypt(value.encode()).decode()
    except InvalidToken:
        return value

admin_config_router = APIRouter(prefix="/admin/config", tags=["admin/config"])


def _get_engine():
    return create_engine(_SESSION_URI)


def _init_config_table():
    engine = _get_engine()
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT 1 FROM system_config LIMIT 1"))
        except Exception:
            conn.rollback()
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS system_config (
                    key        VARCHAR(128) PRIMARY KEY,
                    value      TEXT         NOT NULL,
                    updated_at TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.commit()
            logger.info("[admin_config] Created system_config table")


def _get_config(key: str, default=None):
    engine = _get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT value FROM system_config WHERE key = :key"),
            {"key": key},
        ).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except Exception:
        return row[0]


def _set_config(key: str, value):
    engine = _get_engine()
    raw = json.dumps(value)
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO system_config (key, value, updated_at)
            VALUES (:key, :value, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE
                SET value = :value, updated_at = CURRENT_TIMESTAMP
        """), {"key": key, "value": raw})
        conn.commit()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class BudgetConfig(BaseModel):
    monthly_budget_usd: float = 2000.0
    rate_limit_per_user: int = 60          # requests / minute
    notify_threshold_pct: float = 80.0    # % budget → alert


class TokenConfig(BaseModel):
    token_hard_limit: int = 100000
    token_per_minutes_limit: int = 200000
    compact_context_threshold_pct: float = 90.0   # % of hard limit → trigger compact


class SystemConfig(BaseModel):
    budget: BudgetConfig
    tokens: TokenConfig


class SaveResponse(BaseModel):
    success: bool
    message: str
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Alert threshold models
# ---------------------------------------------------------------------------

class AlertThreshold(BaseModel):
    enabled: bool = True
    threshold_m: int          # millions of tokens (e.g. 250 → 250,000,000)
    severity: str             # "warning" | "critical" | "emergency"
    label: str                # human-readable label


class AlertThresholdsConfig(BaseModel):
    warning1:  AlertThreshold = AlertThreshold(enabled=True, threshold_m=250,  severity="warning",   label="Warning 1")
    warning2:  AlertThreshold = AlertThreshold(enabled=True, threshold_m=500,  severity="warning",   label="Warning 2")
    critical:  AlertThreshold = AlertThreshold(enabled=True, threshold_m=750,  severity="critical",  label="Critical")
    emergency: AlertThreshold = AlertThreshold(enabled=True, threshold_m=1000, severity="emergency", label="Emergency (Block)")


_PROMETHEUS_ALERTS_PATH = os.getenv(
    "PROMETHEUS_ALERTS_PATH",
    str(Path(__file__).resolve().parents[2] / "prometheus-alerts.yml"),
)
_PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")


def _build_alert_rule(name: str, t: AlertThreshold) -> dict:
    tokens = t.threshold_m * 1_000_000
    rule: dict = {
        "alert": f"TokenUsage{name}_{t.threshold_m}M",
        "expr": f'sum(increase(mmvn_llm_tokens_total{{billing_month=~".+"}}[30d])) > {tokens}',
        "for": "30s" if t.severity == "emergency" else "1m",
        "labels": {
            "severity": t.severity,
            "token_threshold": f"{t.threshold_m}M",
            "team": "admin",
        },
        "annotations": {
            "summary": f"Token usage vượt {t.threshold_m} triệu tokens",
            "description": f"Tổng token đã sử dụng vượt ngưỡng {t.threshold_m}M tokens.",
        },
    }
    if t.severity in ("critical", "emergency"):
        rule["labels"]["action_required"] = "true"
    if t.severity == "emergency":
        rule["labels"]["auto_block"] = "true"
        rule["annotations"]["description"] += " Cần chặn service ngay lập tức."
    return rule


def _regenerate_prometheus_alerts(cfg: AlertThresholdsConfig) -> None:
    thresholds = [
        ("Warning", cfg.warning1),
        ("Warning", cfg.warning2),
        ("Critical", cfg.critical),
        ("Emergency", cfg.emergency),
    ]
    rules = [_build_alert_rule(name, t) for name, t in thresholds if t.enabled]

    # Load existing file to preserve non-token_usage_alerts groups
    alerts_path = Path(_PROMETHEUS_ALERTS_PATH)
    existing: dict = {}
    if alerts_path.exists():
        with alerts_path.open() as f:
            existing = yaml.safe_load(f) or {}

    groups = [g for g in existing.get("groups", []) if g.get("name") != "token_usage_alerts"]
    if rules:
        groups.insert(0, {
            "name": "token_usage_alerts",
            "interval": "30s",
            "rules": rules,
        })

    with alerts_path.open("w") as f:
        yaml.dump({"groups": groups}, f, allow_unicode=True, sort_keys=False)
    logger.info("[admin_config] Regenerated %s", alerts_path)


async def _reload_prometheus() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{_PROMETHEUS_URL}/-/reload")
            if resp.status_code == 200:
                logger.info("[admin_config] Prometheus reloaded successfully")
                return True
            logger.warning("[admin_config] Prometheus reload returned %d", resp.status_code)
            return False
    except Exception as exc:
        logger.warning("[admin_config] Prometheus reload failed (non-fatal): %s", exc)
        return False


# ---------------------------------------------------------------------------
# API Key models
# ---------------------------------------------------------------------------

_PROXY_SECRET = os.getenv("PROXY_INTERNAL_SECRET", "")


class APIKeyCreate(BaseModel):
    label: str
    value: str
    priority: int = 1


_VALID_KEY_STATUSES = {"active", "disabled", "limited", "dead"}


class APIKeyUpdate(BaseModel):
    status: str   # "active" | "disabled" | "limited" | "dead"

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in _VALID_KEY_STATUSES:
            raise ValueError(f"status must be one of {_VALID_KEY_STATUSES}")
        return v


class APIKeyResponse(BaseModel):
    id: str
    label: str
    prefix: str   # masked: first 8 chars + ***
    status: str
    priority: int


def _load_api_keys() -> list[dict]:
    """Load API keys, decrypting values on the fly."""
    raw = _get_config("api_keys")
    if not isinstance(raw, list):
        return []
    return [{**k, "value": _decrypt_key_value(k["value"])} if k.get("value") else k for k in raw]


def _save_api_keys(keys: list[dict]) -> None:
    """Save API keys, encrypting values on the fly."""
    encrypted = [{**k, "value": _encrypt_key_value(k["value"])} if k.get("value") else k for k in keys]
    _set_config("api_keys", encrypted)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_init_config_table()


@admin_config_router.get("", response_model=SystemConfig)
async def get_config():
    """Return current system config (budget + token settings)."""
    budget_raw = _get_config("budget") or {}
    token_raw  = _get_config("tokens") or {}
    return SystemConfig(
        budget=BudgetConfig(**budget_raw) if budget_raw else BudgetConfig(),
        tokens=TokenConfig(**token_raw)   if token_raw  else TokenConfig(),
    )


@admin_config_router.post("/budget", response_model=SaveResponse)
async def save_budget(body: BudgetConfig):
    """Save budget & rate-limit settings, then immediately check threshold."""
    try:
        _set_config("budget", body.model_dump())
        config_service.invalidate()
        # Fire-and-forget: check threshold after config is live
        asyncio.get_event_loop().run_in_executor(None, check_and_notify_budget)
        return SaveResponse(success=True, message="Đã lưu cài đặt ngân sách")
    except Exception as exc:
        logger.error("save_budget error: %s", exc)
        return SaveResponse(success=False, message="Lỗi lưu cài đặt", error_message=str(exc))


@admin_config_router.post("/tokens", response_model=SaveResponse)
async def save_tokens(body: TokenConfig):
    """Save token limit & compact context settings."""
    try:
        _set_config("tokens", body.model_dump())
        config_service.invalidate()
        return SaveResponse(success=True, message="Đã lưu cài đặt token")
    except Exception as exc:
        logger.error("save_tokens error: %s", exc)
        return SaveResponse(success=False, message="Lỗi lưu cài đặt", error_message=str(exc))


# ---------------------------------------------------------------------------
# API Key endpoints (dashboard: masked; proxy: full values via shared secret)
# ---------------------------------------------------------------------------

@admin_config_router.get("/apikeys", response_model=list[APIKeyResponse])
async def list_apikeys(_user: dict = Depends(require_permission("config"))):
    """List API keys with masked values for dashboard display."""
    keys = _load_api_keys()
    return [
        APIKeyResponse(
            id=k["id"],
            label=k["label"],
            prefix=k["value"][:8] + "***" if len(k.get("value", "")) >= 8 else "***",
            status=k.get("status", "active"),
            priority=k.get("priority", 1),
        )
        for k in keys
    ]


@admin_config_router.get("/apikeys/active-keys")
async def get_active_keys(x_proxy_secret: str = Header(default="")):
    """Internal endpoint for proxy: returns full key values of active keys.
    Protected by PROXY_INTERNAL_SECRET header.
    """
    if not _PROXY_SECRET or x_proxy_secret != _PROXY_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    keys = _load_api_keys()
    active = [k["value"] for k in keys if k.get("status", "active") == "active" and k.get("value")]
    return {"keys": active}


@admin_config_router.post("/apikeys", response_model=SaveResponse)
async def add_apikey(body: APIKeyCreate, _user: dict = Depends(require_permission("config"))):
    """Add a new API key."""
    keys = _load_api_keys()
    if any(k.get("value") == body.value for k in keys):
        raise HTTPException(status_code=409, detail="API Key này đã tồn tại trong hệ thống")
    try:
        keys.append({
            "id": str(uuid.uuid4()),
            "label": body.label,
            "value": body.value,
            "status": "active",
            "priority": body.priority,
        })
        _save_api_keys(keys)
        return SaveResponse(success=True, message="Đã thêm API Key")
    except Exception as exc:
        logger.error("add_apikey error: %s", exc)
        return SaveResponse(success=False, message="Lỗi thêm key", error_message=str(exc))


class APIKeyReorder(BaseModel):
    ids: list[str]  # ordered list of key IDs, index 0 = priority 1


@admin_config_router.put("/apikeys/reorder", response_model=SaveResponse)
async def reorder_apikeys(body: APIKeyReorder, _user: dict = Depends(require_permission("config"))):
    """Reorder API keys by updating their priority based on the given ID order."""
    try:
        keys = _load_api_keys()
        index = {k["id"]: k for k in keys}
        if set(body.ids) != set(index.keys()):
            return SaveResponse(success=False, message="Danh sách ID không khớp")
        new_keys = [{**index[id_], "priority": i + 1} for i, id_ in enumerate(body.ids)]
        _save_api_keys(new_keys)
        return SaveResponse(success=True, message="Đã cập nhật thứ tự")
    except Exception as exc:
        logger.error("reorder_apikeys error: %s", exc)
        return SaveResponse(success=False, message="Lỗi cập nhật thứ tự", error_message=str(exc))


@admin_config_router.put("/apikeys/{key_id}", response_model=SaveResponse)
async def update_apikey_status(key_id: str, body: APIKeyUpdate, _user: dict = Depends(require_permission("config"))):
    """Toggle status of an API key."""
    try:
        keys = _load_api_keys()
        updated = False
        new_keys = []
        for k in keys:
            if k["id"] == key_id:
                new_keys.append({**k, "status": body.status})
                updated = True
            else:
                new_keys.append(k)
        if not updated:
            return SaveResponse(success=False, message="Không tìm thấy key")
        _save_api_keys(new_keys)
        return SaveResponse(success=True, message="Đã cập nhật trạng thái")
    except Exception as exc:
        logger.error("update_apikey error: %s", exc)
        return SaveResponse(success=False, message="Lỗi cập nhật", error_message=str(exc))


@admin_config_router.delete("/apikeys/{key_id}", response_model=SaveResponse)
async def delete_apikey(key_id: str, _user: dict = Depends(require_permission("config"))):
    """Remove an API key."""
    try:
        keys = _load_api_keys()
        new_keys = [k for k in keys if k["id"] != key_id]
        if len(new_keys) == len(keys):
            return SaveResponse(success=False, message="Không tìm thấy key")
        _save_api_keys(new_keys)
        return SaveResponse(success=True, message="Đã xóa API Key")
    except Exception as exc:
        logger.error("delete_apikey error: %s", exc)
        return SaveResponse(success=False, message="Lỗi xóa key", error_message=str(exc))


# ---------------------------------------------------------------------------
# Semantic Search model config endpoints
# ---------------------------------------------------------------------------

class SemanticSearchModelConfig(BaseModel):
    model_text: str = "gemini-3.1-flash-lite-preview"   # text + image queries
    model_voice: str = "gemini-3-flash-preview"         # voice + file queries


@admin_config_router.get("/semantic-search", response_model=SemanticSearchModelConfig)
async def get_semantic_search_config(_user: dict = Depends(require_permission("config"))):
    """Return current semantic search model config."""
    raw = _get_config("semantic_search_models") or {}
    return SemanticSearchModelConfig(**raw) if raw else SemanticSearchModelConfig()


@admin_config_router.post("/semantic-search", response_model=SaveResponse)
async def save_semantic_search_config(body: SemanticSearchModelConfig, _user: dict = Depends(require_permission("config"))):
    """Save semantic search model config."""
    try:
        _set_config("semantic_search_models", body.model_dump())
        config_service.invalidate()
        return SaveResponse(success=True, message="Đã lưu cài đặt Semantic Search")
    except Exception as exc:
        logger.error("save_semantic_search_config error: %s", exc)
        return SaveResponse(success=False, message="Lỗi lưu cài đặt", error_message=str(exc))


# ---------------------------------------------------------------------------
# RAG Embedding model config endpoints
# ---------------------------------------------------------------------------

_RAG_EMBEDDING_MODELS = {
    "gemini-embedding-001":        768,
    "gemini-embedding-2":          768,
    "gemini-embedding-2-preview":  768,
    "text-embedding-004":          768,
}

_RAG_CONFIG_KEY = "rag_config"
_RAG_DEFAULT_MODEL = "gemini-embedding-001"
_RAG_DEFAULT_DIM = 768


class RAGEmbeddingConfig(BaseModel):
    embedding_model: str = _RAG_DEFAULT_MODEL
    embedding_dim: int = _RAG_DEFAULT_DIM


@admin_config_router.get("/rag", response_model=RAGEmbeddingConfig)
async def get_rag_config(_user: dict = Depends(require_permission("config"))):
    """Return current RAG embedding model config."""
    raw = _get_config(_RAG_CONFIG_KEY) or {}
    return RAGEmbeddingConfig(**raw) if raw else RAGEmbeddingConfig()


@admin_config_router.post("/rag", response_model=SaveResponse)
async def save_rag_config(body: RAGEmbeddingConfig, _user: dict = Depends(require_permission("config"))):
    """Save RAG embedding model config and reset the RAG singleton."""
    if body.embedding_model not in _RAG_EMBEDDING_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported embedding model. Available: {list(_RAG_EMBEDDING_MODELS)}",
        )
    try:
        body = RAGEmbeddingConfig(
            embedding_model=body.embedding_model,
            embedding_dim=_RAG_EMBEDDING_MODELS[body.embedding_model],
        )
        _set_config(_RAG_CONFIG_KEY, body.model_dump())
        # Reset RAG singleton so next call uses the new model
        from mmvn_b2c_agent.tools.rag import rag_tool
        rag_tool._rag_instance = None
        return SaveResponse(success=True, message="Đã lưu cài đặt RAG Embedding. Cần Re-index để áp dụng.")
    except Exception as exc:
        logger.error("save_rag_config error: %s", exc)
        return SaveResponse(success=False, message="Lỗi lưu cài đặt", error_message=str(exc))


# ---------------------------------------------------------------------------
# Alert threshold endpoints
# ---------------------------------------------------------------------------

@admin_config_router.get("/alerts", response_model=AlertThresholdsConfig)
async def get_alert_thresholds(_user: dict = Depends(require_permission("config"))):
    """Return current Prometheus alert threshold config."""
    raw = _get_config("alert_thresholds") or {}
    if not raw:
        return AlertThresholdsConfig()
    try:
        return AlertThresholdsConfig(**raw)
    except Exception:
        return AlertThresholdsConfig()


@admin_config_router.post("/alerts", response_model=SaveResponse)
async def save_alert_thresholds(body: AlertThresholdsConfig, _user: dict = Depends(require_permission("config"))):
    """Save alert thresholds, regenerate prometheus-alerts.yml and hot-reload Prometheus."""
    try:
        _set_config("alert_thresholds", body.model_dump())
        _regenerate_prometheus_alerts(body)
        reloaded = await _reload_prometheus()
        msg = "Đã lưu và tái tạo prometheus-alerts.yml"
        if reloaded:
            msg += " (Prometheus đã reload)"
        else:
            msg += " (Prometheus reload thủ công nếu cần)"
        return SaveResponse(success=True, message=msg)
    except Exception as exc:
        logger.error("save_alert_thresholds error: %s", exc)
        return SaveResponse(success=False, message="Lỗi lưu cấu hình alert", error_message=str(exc))
