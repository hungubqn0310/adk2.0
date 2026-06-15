"""
Alert mailer — sends critical system alerts to admin users via AWS SES (SMTP interface).
Required env vars:
  SMTP_HOST     = email-smtp.ap-southeast-1.amazonaws.com
  SMTP_PORT     = 587
  SMTP_USER     = <SES SMTP username>
  SMTP_PASSWORD = <SES SMTP password>
  SMTP_FROM     = <verified sender address>

Admin emails are read from the dashboard_users table (role='admin').
A 15-minute cooldown per alert type is stored in system_config to prevent spam.
"""

import json
import logging
import os
import smtplib
import time
from datetime import datetime
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
_SESSION_URI = os.getenv("SESSION_SERVICE_URI", "sqlite:///./data/sessions.db")
_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "900"))  # 15 min default
_SYSTEM_NAME = os.getenv("DASHBOARD_SYSTEM_NAME", "AI Chatbot B2C MMVN")
_DASHBOARD_URL = os.getenv("DASHBOARD_BASE_URL", "")


def _get_admin_emails() -> list[str]:
    try:
        engine = create_engine(_SESSION_URI)
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT email FROM dashboard_users "
                "WHERE role = 'admin' AND email IS NOT NULL AND TRIM(email) != ''"
            )).fetchall()
        return [r[0] for r in rows]
    except Exception as exc:
        logger.error("[alert_mailer] Failed to query admin emails: %s", exc)
        return []


def _was_recently_alerted(alert_key: str) -> bool:
    try:
        engine = create_engine(_SESSION_URI)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT value FROM system_config WHERE key = :key"
            ), {"key": alert_key}).fetchone()
        if row is None:
            return False
        return (time.time() - float(row[0])) < _COOLDOWN_SECONDS
    except Exception:
        return False


def _mark_alerted(alert_key: str) -> None:
    try:
        engine = create_engine(_SESSION_URI)
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO system_config (key, value, updated_at)
                VALUES (:key, :value, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE
                    SET value = :value, updated_at = CURRENT_TIMESTAMP
            """), {"key": alert_key, "value": str(time.time())})
            conn.commit()
    except Exception as exc:
        logger.error("[alert_mailer] Failed to mark alerted: %s", exc)


def _send_emails(to_list: list[str], subject: str, body: str, is_html: bool = False) -> None:
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM", smtp_user)

    if not smtp_host or not smtp_user:
        logger.warning("[alert_mailer] SMTP not configured (SMTP_HOST/SMTP_USER missing) — alert skipped")
        return

    msg = MIMEText(body, "html" if is_html else "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = ", ".join(to_list)

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, to_list, msg.as_string())
        logger.info("[alert_mailer] Sent '%s' → %s", subject, to_list)
    except Exception as exc:
        logger.error("[alert_mailer] Failed to send email via SES SMTP: %s", exc)


def _fire_blocker_alert(alert_key: str, reason: str) -> None:
    """Shared template for all blocker-type alerts. Respects per-key cooldown."""
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


def fire_no_active_keys_alert() -> None:
    """Called when there are zero active Gemini API keys (chatbot fully down)."""
    _fire_blocker_alert(
        "alert_cooldown_no_active_keys",
        "Không có API key nào đang active",
    )


def fire_all_keys_disabled_alert() -> None:
    """Called when all Gemini API keys are intentionally disabled by admin."""
    _fire_blocker_alert(
        "alert_cooldown_all_keys_disabled",
        "Không có API key nào đang active",
    )


def fire_model_deprecated_alert() -> None:
    """Called when Google returns 400 due to deprecated model name or wrong model ID."""
    _fire_blocker_alert(
        "alert_cooldown_model_deprecated",
        "Lỗi 400 từ Google: model bị deprecated hoặc sai tên",
    )


def fire_network_error_alert() -> None:
    """Called when all Google API requests fail due to DNS/network timeout."""
    _fire_blocker_alert(
        "alert_cooldown_network_error",
        "Lỗi kết nối mạng tới Google: DNS hoặc timeout toàn bộ keys",
    )


def fire_budget_threshold_alert(current_cost: float, budget: float, threshold_pct: float, _month: str) -> None:
    """
    Called when current month's cost exceeds the configured notify_threshold_pct.
    Mirrors the Alertmanager path but sends directly to dashboard admin emails.
    """
    admins = _get_admin_emails()
    if not admins:
        logger.warning("[alert_mailer] No admin emails found — cannot send budget alert")
        return

    pct_used = current_cost / budget * 100 if budget > 0 else 0
    dashboard_line = (
        f'<strong>👉 <a href="{_DASHBOARD_URL}">{_DASHBOARD_URL}</a></strong>'
        if _DASHBOARD_URL else ""
    )

    subject = "Cảnh báo đạt ngưỡng ngân sách API Chatbot"
    body = (
        f"Dear Admin,<br><br>"
        f"Hệ thống <strong>{_SYSTEM_NAME}</strong> xin thông báo: Ngân sách sử dụng API Chatbot của dự án "
        f"đã đạt đến ngưỡng cảnh báo <strong>{pct_used:.1f}%</strong>.<br><br>"
        f"Dưới đây là thông tin tóm tắt về trạng thái ngân sách hiện tại:<br>"
        f"<strong>Tổng ngân sách:</strong> ${budget:.2f}<br>"
        f"<strong>Đã sử dụng:</strong> ${current_cost:.4f}<br>"
        f"<strong>Tỷ lệ đã sử dụng:</strong> {pct_used:.1f}%<br><br>"
        f"Để đảm bảo hệ thống AI Chatbot trên trang E-commerce tiếp tục hoạt động ổn định, "
        f"không làm gián đoạn trải nghiệm mua sắm và tư vấn cho khách hàng, bạn vui lòng "
        f"truy cập vào trang quản trị để kiểm tra chi tiết.<br>"
        f"{dashboard_line}<br><br>"
        f"Bạn có thể chủ động nạp thêm ngân sách hoặc điều chỉnh lại giới hạn API "
        f"trực tiếp trên hệ thống.<br><br>"
        f"Trân trọng,"
    )

    _send_emails(admins, subject, body, is_html=True)


def fire_token_threshold_alert(
    threshold_key: str,
    label: str,
    threshold_m: int,
    total_tokens: int,
    severity: str,
    month: str,
) -> None:
    """
    Called when total monthly token usage exceeds a configured alert threshold.
    One email per threshold per month (cooldown key scoped to month).
    """
    alert_key = f"alert_token_{threshold_key}_{month}"

    try:
        engine = create_engine(_SESSION_URI)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM system_config WHERE key = :key"),
                {"key": alert_key},
            ).fetchone()
        if row is not None:
            logger.debug("[alert_mailer] Token threshold %s already notified for %s — skipping", threshold_key, month)
            return
    except Exception:
        pass

    admins = _get_admin_emails()
    if not admins:
        logger.warning("[alert_mailer] No admin emails found — cannot send token threshold alert")
        return

    now_str = datetime.now(tz=_VN_TZ).strftime("%d/%m/%Y %H:%M:%S")
    total_m = total_tokens / 1_000_000

    subject = f"[MMVN Chatbot B2C] CẢNH BÁO Token ({severity.upper()}): Vượt ngưỡng {threshold_m}M tokens"
    body = (
        f"Xin chào Admin,\n\n"
        f"Hệ thống chatbot MMVN phát hiện cảnh báo token lúc {now_str} (GMT+7):\n\n"
        f"  - Mức cảnh báo: {label} ({severity.upper()})\n"
        f"  - Tổng token tháng {month}: {total_m:.2f}M / {threshold_m}M tokens\n\n"
        f"Hành động cần thực hiện:\n"
        f"  1. Vào Dashboard → Cài đặt → Ngưỡng cảnh báo Token.\n"
        f"  2. Kiểm tra lượng sử dụng tại Dashboard → Tổng quan.\n"
    )
    if severity == "emergency":
        body += f"  3. KHẨN CẤP: Hệ thống có thể tự động chặn chatbot khi đạt ngưỡng Emergency.\n"
    body += f"\nEmail này chỉ gửi một lần cho ngưỡng này trong tháng {month}.\n\n-- MMVN Chatbot System"

    _send_emails(admins, subject, body)

    try:
        engine = create_engine(_SESSION_URI)
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO system_config (key, value, updated_at)
                VALUES (:key, :value, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE
                    SET value = :value, updated_at = CURRENT_TIMESTAMP
            """), {"key": alert_key, "value": "true"})
            conn.commit()
    except Exception as exc:
        logger.error("[alert_mailer] Failed to mark token threshold notified: %s", exc)


def check_and_notify_token_thresholds() -> None:
    """
    Check current month's total token usage against each enabled alert threshold.
    Sends an email to dashboard admins when any threshold is crossed.
    Called fire-and-forget after each LLM usage record.
    """
    month = datetime.now(tz=_VN_TZ).strftime("%Y-%m")

    try:
        engine = create_engine(_SESSION_URI)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT COALESCE(SUM(input_tokens + output_tokens + cached_tokens), 0) "
                "FROM token_usage WHERE billing_month = :month"
            ), {"month": month}).fetchone()
        total_tokens = int(row[0]) if row else 0
    except Exception as exc:
        logger.error("[alert_mailer] Failed to query token_usage for threshold check: %s", exc)
        return

    try:
        engine = create_engine(_SESSION_URI)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT value FROM system_config WHERE key = 'alert_thresholds'"
            )).fetchone()
        thresholds_raw = json.loads(row[0]) if row else {}
    except Exception as exc:
        logger.error("[alert_mailer] Failed to load alert_thresholds config: %s", exc)
        return

    threshold_keys = ["warning1", "warning2", "critical", "emergency"]
    for key in threshold_keys:
        t = thresholds_raw.get(key)
        if not t or not t.get("enabled", True):
            continue
        threshold_tokens = t["threshold_m"] * 1_000_000
        if total_tokens >= threshold_tokens:
            fire_token_threshold_alert(
                threshold_key=key,
                label=t.get("label", key),
                threshold_m=t["threshold_m"],
                total_tokens=total_tokens,
                severity=t.get("severity", "warning"),
                month=month,
            )


def fire_all_keys_429_alert() -> None:
    """Called when every Gemini API key returns 429 (chatbot fully down)."""
    _fire_blocker_alert(
        "alert_cooldown_all_keys_429",
        "Tất cả keys bị lỗi 429",
    )
