"""
Runtime config service — reads from DB with TTL cache, falls back to constants.py defaults.

Usage:
    from mmvn_b2c_agent.shared.config_service import config_service

    limit = config_service.token_warning_limit
"""

import json
import logging
import os
import time
from typing import Any

from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

# Defaults mirrored from constants.py — only used when DB has no value
_DEFAULTS: dict[str, Any] = {
    "token_warning_limit": 41000,
    "token_hard_limit": 100000,
    "token_per_minutes_limit": 200000,
    "max_user_message_length": 200000,
    "rate_limit_per_user": 60,
    "monthly_budget_usd": 2000.0,
    "notify_threshold_pct": 80.0,
    "compact_context_threshold_pct": 90.0,
    "semantic_search_model_text": "gemini-3.1-flash-lite-preview",
    "semantic_search_model_voice": "gemini-3-flash-preview",
}

_CACHE_TTL_SECONDS = 60


class _ConfigService:
    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}
        self._cache_ts: float = 0.0
        self._uri = os.getenv("SESSION_SERVICE_URI", "sqlite:///./data/sessions.db")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_from_db(self) -> dict[str, Any]:
        try:
            engine = create_engine(self._uri)
            with engine.connect() as conn:
                budget_row = conn.execute(
                    text("SELECT value FROM system_config WHERE key = 'budget'")
                ).fetchone()
                token_row = conn.execute(
                    text("SELECT value FROM system_config WHERE key = 'tokens'")
                ).fetchone()
                ss_row = conn.execute(
                    text("SELECT value FROM system_config WHERE key = 'semantic_search_models'")
                ).fetchone()

            result: dict[str, Any] = {}

            if budget_row:
                budget = json.loads(budget_row[0])
                result["rate_limit_per_user"] = budget.get("rate_limit_per_user", _DEFAULTS["rate_limit_per_user"])
                result["monthly_budget_usd"] = budget.get("monthly_budget_usd", _DEFAULTS["monthly_budget_usd"])
                result["notify_threshold_pct"] = budget.get("notify_threshold_pct", _DEFAULTS["notify_threshold_pct"])

            if token_row:
                tokens = json.loads(token_row[0])
                result["token_warning_limit"] = tokens.get("token_limit_per_user", _DEFAULTS["token_warning_limit"])
                result["token_hard_limit"] = tokens.get("token_hard_limit", _DEFAULTS["token_hard_limit"])
                result["token_per_minutes_limit"] = tokens.get("token_per_minutes_limit", _DEFAULTS["token_per_minutes_limit"])
                result["compact_context_threshold_pct"] = tokens.get("compact_context_threshold_pct", _DEFAULTS["compact_context_threshold_pct"])

            if ss_row:
                ss = json.loads(ss_row[0])
                result["semantic_search_model_text"] = ss.get("model_text", _DEFAULTS["semantic_search_model_text"])
                result["semantic_search_model_voice"] = ss.get("model_voice", _DEFAULTS["semantic_search_model_voice"])

            return result

        except Exception as exc:
            logger.warning("ConfigService: failed to load from DB, using defaults. reason=%s", exc)
            return {}

    def _refresh_if_needed(self) -> None:
        now = time.monotonic()
        if now - self._cache_ts > _CACHE_TTL_SECONDS:
            db_values = self._load_from_db()
            self._cache = {**_DEFAULTS, **db_values}
            self._cache_ts = now
            logger.debug("ConfigService: cache refreshed — %s", self._cache)

    def get(self, key: str) -> Any:
        self._refresh_if_needed()
        return self._cache.get(key, _DEFAULTS.get(key))

    def invalidate(self) -> None:
        """Force next read to reload from DB (call after saving new config)."""
        self._cache_ts = 0.0

    # ------------------------------------------------------------------
    # Typed properties (avoids magic strings at call sites)
    # ------------------------------------------------------------------

    @property
    def token_warning_limit(self) -> int:
        return int(self.get("token_warning_limit"))

    @property
    def token_hard_limit(self) -> int:
        return int(self.get("token_hard_limit"))

    @property
    def token_per_minutes_limit(self) -> int:
        return int(self.get("token_per_minutes_limit"))

    @property
    def max_user_message_length(self) -> int:
        return int(self.get("max_user_message_length"))

    @property
    def rate_limit_per_user(self) -> int:
        return int(self.get("rate_limit_per_user"))

    @property
    def monthly_budget_usd(self) -> float:
        return float(self.get("monthly_budget_usd"))

    @property
    def notify_threshold_pct(self) -> float:
        return float(self.get("notify_threshold_pct"))

    @property
    def compact_context_threshold_pct(self) -> float:
        return float(self.get("compact_context_threshold_pct"))

    @property
    def semantic_search_model_text(self) -> str:
        return str(self.get("semantic_search_model_text"))

    @property
    def semantic_search_model_voice(self) -> str:
        return str(self.get("semantic_search_model_voice"))


# Singleton — import and use directly
config_service = _ConfigService()
