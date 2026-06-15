"""
Budget monitor — checks current month's USD cost vs notify_threshold_pct,
then fires an alert to Alertmanager which handles SMTP routing.

Env:
    ALERTMANAGER_URL  (default: http://alertmanager:9093)
"""

import json
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import create_engine, text

from mmvn_b2c_agent.shared.config_service import config_service
from mmvn_b2c_agent.shared.pricing import calc_cost
from mmvn_b2c_agent.shared.alert_mailer import fire_budget_threshold_alert

logger = logging.getLogger(__name__)

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
_SESSION_URI = os.getenv("SESSION_SERVICE_URI", "sqlite:///./data/sessions.db")
_ALERTMANAGER_URL = os.getenv("ALERTMANAGER_URL", "http://alertmanager:9093")


def _calc_current_month_cost(month: str) -> float:
    try:
        engine = create_engine(_SESSION_URI)
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT model,
                       COALESCE(SUM(input_tokens), 0)  AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(cached_tokens), 0) AS cached_tokens
                FROM token_usage
                WHERE billing_month = :month
                GROUP BY model
            """), {"month": month}).fetchall()
        total = 0.0
        for r in rows:
            total += calc_cost(r.input_tokens, r.output_tokens, r.cached_tokens, r.model or "")
        return total
    except Exception as exc:
        logger.error("[budget_monitor] Failed to query token_usage: %s", exc)
        return 0.0


def _notified_key(month: str, threshold_pct: float) -> str:
    return f"budget_alert_sent_{month}_{threshold_pct:.0f}"


def _was_notified(month: str, threshold_pct: float) -> bool:
    try:
        engine = create_engine(_SESSION_URI)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT value FROM system_config WHERE key = :key"
            ), {"key": _notified_key(month, threshold_pct)}).fetchone()
        return row is not None
    except Exception:
        return False


def _mark_notified(month: str, threshold_pct: float) -> None:
    try:
        engine = create_engine(_SESSION_URI)
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO system_config (key, value, updated_at)
                VALUES (:key, :value, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE
                    SET value = :value, updated_at = CURRENT_TIMESTAMP
            """), {"key": _notified_key(month, threshold_pct), "value": "true"})
            conn.commit()
    except Exception as exc:
        logger.error("[budget_monitor] Failed to mark notified: %s", exc)


def _fire_alertmanager(current_cost: float, budget: float, threshold_pct: float, month: str) -> bool:
    pct_used = current_cost / budget * 100 if budget > 0 else 0
    severity = "critical" if pct_used >= 100 else "warning"
    now_iso = datetime.now(timezone.utc).isoformat()

    alert = [{
        "labels": {
            "alertname": "BudgetThresholdExceeded",
            "severity": severity,
            "billing_month": month,
            "team": "admin",
        },
        "annotations": {
            "summary": f"Chi phí tháng {month} đã vượt {threshold_pct:.0f}% ngân sách",
            "description": (
                f"Chi phí hiện tại: ${current_cost:.4f} / ${budget:.2f} "
                f"({pct_used:.1f}%). Ngưỡng cảnh báo: {threshold_pct:.0f}%."
            ),
        },
        "startsAt": now_iso,
    }]

    try:
        resp = httpx.post(
            f"{_ALERTMANAGER_URL}/api/v2/alerts",
            json=alert,
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info("[budget_monitor] Alert fired to Alertmanager for month=%s cost=%.4f", month, current_cost)
            return True
        logger.warning("[budget_monitor] Alertmanager returned %d: %s", resp.status_code, resp.text)
        return False
    except Exception as exc:
        logger.error("[budget_monitor] Failed to reach Alertmanager: %s", exc)
        return False


def check_and_notify_budget() -> None:
    """Check current month's spend vs threshold, fire Alertmanager alert if exceeded."""
    budget = config_service.monthly_budget_usd
    threshold_pct = config_service.notify_threshold_pct
    month = datetime.now(tz=_VN_TZ).strftime("%Y-%m")

    if budget <= 0:
        return

    current_cost = _calc_current_month_cost(month)
    threshold_usd = budget * threshold_pct / 100.0

    logger.info(
        "[budget_monitor] month=%s cost=%.4f threshold=%.4f (%.0f%% of $%.2f)",
        month, current_cost, threshold_usd, threshold_pct, budget,
    )

    if current_cost < threshold_usd:
        return

    if _was_notified(month, threshold_pct):
        logger.debug("[budget_monitor] Already notified for %s @ %.0f%% — skipping", month, threshold_pct)
        return

    fired = _fire_alertmanager(current_cost, budget, threshold_pct, month)
    if fired:
        _mark_notified(month, threshold_pct)
    # Also send directly to dashboard admin emails (independent of Alertmanager)
    fire_budget_threshold_alert(current_cost, budget, threshold_pct, month)
