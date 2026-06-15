"""
API endpoints for dashboard tracking metrics:
- Avg response time
- AI Order Conversion Rate (with sub-metrics: orders placed vs completed)
- Message feedback stats (thumbs up/down)
"""
import logging
import os
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

def _vn_now() -> datetime:
    return datetime.now(tz=_VN_TZ)

logger = logging.getLogger(__name__)

SESSION_SERVICE_URI = os.getenv("SESSION_SERVICE_URI", "sqlite:///./data/sessions.db")


def _make_async_uri(uri: str) -> str:
    """Convert a sync DB URI to its async-driver equivalent.

    PostgreSQL -> asyncpg, SQLite -> aiosqlite. create_async_engine requires
    an async driver, so a bare ``sqlite://`` or ``postgresql://`` URI would
    raise InvalidRequestError ("pysqlite is not async").
    """
    if uri.startswith("postgresql+psycopg2://"):
        return "postgresql+asyncpg://" + uri[len("postgresql+psycopg2://"):]
    if uri.startswith("postgresql+psycopg://"):
        return "postgresql+asyncpg://" + uri[len("postgresql+psycopg://"):]
    for prefix in ("postgresql://", "postgres://"):
        if uri.startswith(prefix):
            return "postgresql+asyncpg://" + uri[len(prefix):]
    if uri.startswith("sqlite://") and not uri.startswith("sqlite+"):
        return "sqlite+aiosqlite://" + uri[len("sqlite://"):]
    return uri  # already async or unknown driver


# Single async engine with connection pool — reused across all requests
_engine = create_async_engine(
    _make_async_uri(SESSION_SERVICE_URI),
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

metrics_tracking_router = APIRouter(prefix="/metrics", tags=["metrics"])


class ResponseTimeStatsResponse(BaseModel):
    """Thời gian phản hồi trung bình của AI"""
    avg_response_seconds: float
    avg_response_ms: float
    sample_count: int
    period: str


class ConversionStatsResponse(BaseModel):
    """Tỷ lệ chuyển đổi đơn hàng qua AI"""
    conversion_rate: float          # (orders_placed / total_active_sessions) * 100
    total_active_sessions: int
    orders_placed: int              # success + pending + failed (mọi lần cố đặt đơn)
    orders_completed: int           # success only (thanh toán online thành công)
    orders_failed: int              # failed (thanh toán thất bại)
    orders_placed_completion_rate: float  # (orders_completed / orders_placed) * 100
    period: str


class FeedbackItem(BaseModel):
    """Chi tiết một feedback"""
    invocation_id: str
    rating: str
    comment: Optional[str]
    response_text: Optional[str]
    submitted_at: Optional[str]
    session_id: str
    user_id: str


class FeedbackStatsResponse(BaseModel):
    """Thống kê feedback (like/dislike) của AI"""
    thumbs_up: int
    thumbs_down: int
    total: int
    satisfaction_rate: float        # (thumbs_up / total) * 100
    period: str
    items: list[FeedbackItem]


def _resolve_period(period: Optional[str]) -> tuple[str, str, str]:
    """Return (period_label, current_month, prev_month). Uses VN timezone for default."""
    current = period or _vn_now().strftime("%Y-%m")
    prev_dt = (datetime.strptime(current, "%Y-%m").replace(day=1) - timedelta(days=1))
    prev = prev_dt.strftime("%Y-%m")
    return current, current, prev


@metrics_tracking_router.get("/stats/response-time", response_model=ResponseTimeStatsResponse)
async def get_avg_response_time(
    period: Optional[str] = Query(None, description="Tháng cần xem (YYYY-MM), mặc định tháng hiện tại"),
    start_date: Optional[str] = Query(None, description="Ngày bắt đầu (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Ngày kết thúc (YYYY-MM-DD)"),
):
    """
    Thời gian phản hồi TB của AI.

    Tính từ timestamp khi user gửi tin nhắn (author='user', role='user', không phải function_response)
    đến timestamp của model event cuối cùng trong cùng invocation.
    Lọc bỏ các outlier > 300 giây.
    """
    if start_date and end_date:
        date_filter = "(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh')::date BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)"
        params: dict = {
            "start_date": datetime.strptime(start_date, "%Y-%m-%d").date(),
            "end_date": datetime.strptime(end_date, "%Y-%m-%d").date(),
        }
        period_label = f"{start_date} → {end_date}"
    else:
        current_month, _, _ = _resolve_period(period)
        date_filter = "TO_CHAR(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh', 'YYYY-MM') = :month"
        params = {"month": current_month}
        period_label = current_month

    async with _engine.connect() as conn:
        result = await conn.execute(text(f"""
            WITH invocation_times AS (
                SELECT
                    invocation_id,
                    MIN(timestamp) FILTER (
                        WHERE author = 'user'
                          AND content->>'role' = 'user'
                          AND NOT EXISTS (
                              SELECT 1
                              FROM jsonb_array_elements(content->'parts') p
                              WHERE p ? 'function_response'
                          )
                    ) AS user_start_ts,
                    MAX(timestamp) FILTER (
                        WHERE content->>'role' = 'model'
                    ) AS model_end_ts
                FROM events
                WHERE content IS NOT NULL
                  AND {date_filter}
                GROUP BY invocation_id
            )
            SELECT
                COUNT(*) FILTER (
                    WHERE user_start_ts IS NOT NULL
                      AND model_end_ts IS NOT NULL
                      AND model_end_ts > user_start_ts
                      AND EXTRACT(EPOCH FROM (model_end_ts - user_start_ts)) < 300
                ) AS sample_count,
                COALESCE(
                    AVG(EXTRACT(EPOCH FROM (model_end_ts - user_start_ts))) FILTER (
                        WHERE user_start_ts IS NOT NULL
                          AND model_end_ts IS NOT NULL
                          AND model_end_ts > user_start_ts
                          AND EXTRACT(EPOCH FROM (model_end_ts - user_start_ts)) < 300
                    ),
                    0
                ) AS avg_seconds
            FROM invocation_times
        """), params)

        row = result.fetchone()
        sample_count = row.sample_count or 0
        avg_seconds = float(row.avg_seconds or 0)

    return ResponseTimeStatsResponse(
        avg_response_seconds=round(avg_seconds, 3),
        avg_response_ms=round(avg_seconds * 1000, 1),
        sample_count=sample_count,
        period=period_label,
    )


@metrics_tracking_router.get("/stats/conversion", response_model=ConversionStatsResponse)
async def get_conversion_rate(
    period: Optional[str] = Query(None, description="Tháng cần xem (YYYY-MM), mặc định tháng hiện tại"),
    start_date: Optional[str] = Query(None, description="Ngày bắt đầu (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Ngày kết thúc (YYYY-MM-DD)"),
):
    """
    Tỷ lệ chuyển đổi đơn hàng qua AI.

    Công thức: (orders_placed / total_active_sessions) * 100

    Sub-metric - Tỷ lệ đơn hàng thành công:
      - orders_placed  = mọi lần cố đặt đơn (status: success + pending + failed)
      - orders_completed = thanh toán online xác nhận thành công (status: success)
      - orders_placed_completion_rate = (orders_completed / orders_placed) * 100

    Nguồn dữ liệu: events.content của show_payment_methods functionResponse từ FE.
    """
    if start_date and end_date:
        session_date_filter = "(s.create_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh')::date BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)"
        event_date_filter = "(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh')::date BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)"
        params: dict = {
            "start_date": datetime.strptime(start_date, "%Y-%m-%d").date(),
            "end_date": datetime.strptime(end_date, "%Y-%m-%d").date(),
        }
        period_label = f"{start_date} → {end_date}"
    else:
        current_month, _, _ = _resolve_period(period)
        session_date_filter = "TO_CHAR(s.create_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh', 'YYYY-MM') = :month"
        event_date_filter = "TO_CHAR(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh', 'YYYY-MM') = :month"
        params = {"month": current_month}
        period_label = current_month

    async with _engine.connect() as conn:
        # Total active sessions in period (sessions with at least one real user text message)
        active_result = await conn.execute(text(f"""
            SELECT COUNT(DISTINCT s.id)
            FROM sessions s
            WHERE {session_date_filter}
              AND EXISTS (
                  SELECT 1
                  FROM events e
                  WHERE e.app_name = s.app_name
                    AND e.user_id = s.user_id
                    AND e.session_id = s.id
                    AND e.content IS NOT NULL
                    AND e.content->>'role' = 'user'
                    AND e.author = 'user'
                    AND NOT EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(e.content->'parts') p
                        WHERE p ? 'function_response'
                    )
              )
        """), params)
        total_active_sessions = active_result.scalar() or 0

        # Order stats from show_payment_methods functionResponse events
        order_result = await conn.execute(text(f"""
            SELECT
                part->'function_response'->'response'->>'status' AS order_status,
                COUNT(*) AS cnt
            FROM events,
                 jsonb_array_elements(content->'parts') AS part
            WHERE content IS NOT NULL
              AND {event_date_filter}
              AND part->'function_response'->>'name' = 'show_payment_methods'
              AND part->'function_response'->'response'->>'order_number' IS NOT NULL
            GROUP BY order_status
        """), params)

        orders_placed = 0
        orders_completed = 0
        orders_failed = 0
        for row in order_result:
            status = (row.order_status or "").strip().lower()
            count = row.cnt or 0
            if status in ("success", "done"):
                orders_completed += count
                orders_placed += count
            elif status == "pending":
                orders_placed += count
            elif status in ("fail", "failed"):
                orders_failed += count
                orders_placed += count

    conversion_rate = (orders_placed / total_active_sessions * 100) if total_active_sessions > 0 else 0.0
    placed_completion_rate = (orders_completed / orders_placed * 100) if orders_placed > 0 else 0.0

    return ConversionStatsResponse(
        conversion_rate=round(conversion_rate, 2),
        total_active_sessions=total_active_sessions,
        orders_placed=orders_placed,
        orders_completed=orders_completed,
        orders_failed=orders_failed,
        orders_placed_completion_rate=round(placed_completion_rate, 2),
        period=period_label,
    )


@metrics_tracking_router.get("/stats/feedback", response_model=FeedbackStatsResponse)
async def get_feedback_stats(
    period: Optional[str] = Query(None, description="Tháng cần xem (YYYY-MM), mặc định tháng hiện tại"),
    start_date: Optional[str] = Query(None, description="Ngày bắt đầu (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Ngày kết thúc (YYYY-MM-DD)"),
):
    """
    Thống kê feedback (like/dislike) của AI theo tháng hoặc khoảng ngày.

    Đọc từ custom_metadata->>'feedback' của các model events.
    """
    if start_date and end_date:
        date_filter = "(e.timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh')::date BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)"
        params: dict = {
            "start_date": datetime.strptime(start_date, "%Y-%m-%d").date(),
            "end_date": datetime.strptime(end_date, "%Y-%m-%d").date(),
        }
        period_label = f"{start_date} → {end_date}"
    else:
        current_month, _, _ = _resolve_period(period)
        date_filter = "TO_CHAR(e.timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh', 'YYYY-MM') = :month"
        params = {"month": current_month}
        period_label = current_month

    async with _engine.connect() as conn:
        result = await conn.execute(text(f"""
            SELECT
                e.invocation_id,
                e.session_id,
                e.user_id,
                e.custom_metadata->'feedback'->>'rating'       AS rating,
                e.custom_metadata->'feedback'->>'comment'      AS comment,
                e.custom_metadata->'feedback'->>'response_text' AS response_text,
                e.custom_metadata->'feedback'->>'submitted_at' AS submitted_at
            FROM events e
            WHERE e.custom_metadata ? 'feedback'
              AND {date_filter}
            ORDER BY e.timestamp DESC
        """), params)

        rows = result.fetchall()

    thumbs_up = sum(1 for r in rows if r.rating == "up")
    thumbs_down = sum(1 for r in rows if r.rating == "down")
    total = len(rows)
    satisfaction_rate = (thumbs_up / total * 100) if total > 0 else 0.0

    items = [
        FeedbackItem(
            invocation_id=r.invocation_id,
            rating=r.rating or "",
            comment=r.comment,
            response_text=r.response_text,
            submitted_at=r.submitted_at,
            session_id=r.session_id,
            user_id=r.user_id,
        )
        for r in rows
    ]

    return FeedbackStatsResponse(
        thumbs_up=thumbs_up,
        thumbs_down=thumbs_down,
        total=total,
        satisfaction_rate=round(satisfaction_rate, 2),
        period=period_label,
        items=items,
    )


class SessionsOverTimeResponse(BaseModel):
    """Sessions per day over time"""
    days: list[dict]
    period_days: int


class TopicItem(BaseModel):
    topic: str
    count: int


class TopicsStatsResponse(BaseModel):
    """Top topics from function calls"""
    topics: list[TopicItem]
    period: str


# Function name → topic category mapping
_TOPIC_RULES: list[tuple[list[str], str]] = [
    (["search_product", "get_product", "product_search", "get_categor", "get_all_categor", "product_detail", "age_verify"], "Tìm sản phẩm"),
    (["get_store", "get_current_store", "get_all_store", "get_nearest_store", "find_nearest_store", "update_store", "confirm_store", "trigger_change_store", "store_hours", "store_info", "select_store", "check_store_selection"], "Cửa hàng"),
    (["promo", "coupon", "discount", "mcard", "promotion", "voucher", "free_ship", "freeship"], "Khuyến mãi"),
    (["cart", "wishlist", "add_to_cart", "remove_from_cart", "view_cart", "clear_cart", "update_cart"], "Giỏ hàng"),
    (["order", "checkout", "payment", "reorder", "track", "delivery", "vat_invoice"], "Đơn hàng"),
    (["customer_care", "customer_support", "redirect_customer", "complaint", "mmvn_redirect"], "CSKH"),
    (["get_mm_info", "get_all_faq", "faq", "rag", "get_all_mm_data", "search_knowledge"], "Thông tin / FAQ"),
    (["view_account", "register_account", "get_user_info", "get_customer_address", "add_delivery_address"], "Tài khoản"),
]


def _classify_function(func_name: str) -> str:
    name_lower = func_name.lower()
    for keywords, topic in _TOPIC_RULES:
        if any(kw in name_lower for kw in keywords):
            return topic
    return "Khác"


@metrics_tracking_router.get("/stats/sessions-over-time", response_model=SessionsOverTimeResponse)
async def get_sessions_over_time(
    days: Optional[int] = Query(7, description="Số ngày cần xem (7 hoặc 30)", ge=1, le=90),
    start_date: Optional[str] = Query(None, description="Ngày bắt đầu (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Ngày kết thúc (YYYY-MM-DD)"),
):
    """Sessions per day."""
    vn_now = _vn_now()
    if start_date and end_date:
        s_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        e_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        num_days = max(1, (e_date - s_date).days + 1)
        
        vn_start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        vn_end = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        sql_start_date = vn_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        sql_end_date = vn_end.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        
        time_filter = "create_time BETWEEN :start_date AND :end_date"
        params = {"start_date": sql_start_date, "end_date": sql_end_date}
    else:
        num_days = days or 7
        vn_start = (vn_now - timedelta(days=num_days)).replace(hour=0, minute=0, second=0, microsecond=0)
        sql_start_date = vn_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        time_filter = "create_time >= :start_date"
        params = {"start_date": sql_start_date}

    async with _engine.connect() as conn:
        result = await conn.execute(text(f"""
            SELECT
                DATE(create_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh') AS day,
                COUNT(*) AS sessions
            FROM sessions
            WHERE {time_filter}
            GROUP BY DATE(create_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh')
            ORDER BY day
        """), params)
        rows = result.fetchall()

    # Fill missing days with 0 — use VN dates
    day_map = {str(r.day): int(r.sessions) for r in rows}
    all_days = []
    
    if start_date and end_date:
        for i in range(num_days):
            day = s_date + timedelta(days=i)
            all_days.append({"date": str(day), "sessions": day_map.get(str(day), 0)})
    else:
        for i in range(num_days):
            day = (vn_now - timedelta(days=num_days - 1 - i)).date()
            all_days.append({"date": str(day), "sessions": day_map.get(str(day), 0)})

    return SessionsOverTimeResponse(days=all_days, period_days=num_days)


@metrics_tracking_router.get("/stats/topics", response_model=TopicsStatsResponse)
async def get_topics_stats(
    period: Optional[str] = Query(None, description="Tháng cần xem (YYYY-MM), mặc định tháng hiện tại"),
    days: Optional[int] = Query(None, description="Số ngày gần đây (ưu tiên hơn period nếu cung cấp)", ge=1, le=90),
    start_date: Optional[str] = Query(None, description="Ngày bắt đầu (YYYY-MM-DD), ưu tiên cao nhất"),
    end_date: Optional[str] = Query(None, description="Ngày kết thúc (YYYY-MM-DD)"),
):
    """Top topics from function calls in events, categorized by tool name."""
    if start_date and end_date:
        s_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        e_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        time_filter = "(e.timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh')::date BETWEEN :start_date AND :end_date"
        params: dict = {"start_date": s_date, "end_date": e_date}
        period_label = f"{start_date}_{end_date}"
    elif days is not None:
        start_dt = (_vn_now() - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        time_filter = "e.timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh' >= :start_dt"
        params = {"start_dt": start_dt}
        period_label = f"last_{days}_days"
    else:
        current_month, _, _ = _resolve_period(period)
        time_filter = "TO_CHAR(e.timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh', 'YYYY-MM') = :month"
        params = {"month": current_month}
        period_label = current_month

    async with _engine.connect() as conn:
        result = await conn.execute(text(f"""
            WITH function_calls AS (
                SELECT
                    e.invocation_id,
                    part->'function_call'->>'name' AS func_name
                FROM events e,
                     jsonb_array_elements(e.content->'parts') AS part
                WHERE e.content IS NOT NULL
                  AND part ? 'function_call'
                  AND {time_filter}
            ),
            agent_calls AS (
                SELECT
                    e.invocation_id,
                    CASE
                        WHEN e.author = 'product_search_tool_caller' THEN 'search_product'
                        ELSE e.author
                    END AS func_name
                FROM events e
                WHERE e.author IN ('product_search_tool_caller')
                  AND {time_filter}
            )
            SELECT func_name, COUNT(DISTINCT invocation_id) AS invocation_count
            FROM (
                SELECT * FROM function_calls
                UNION ALL
                SELECT * FROM agent_calls
            ) combined
            WHERE func_name NOT IN ('set_model_response', 'transfer_to_agent', 'fallback_agent')
            GROUP BY func_name
            ORDER BY invocation_count DESC
        """), params)
        rows = result.fetchall()

    # Aggregate by topic
    topic_counts: dict[str, int] = {}
    for row in rows:
        if not row.func_name:
            continue
        topic = _classify_function(row.func_name)
        topic_counts[topic] = topic_counts.get(topic, 0) + int(row.invocation_count)

    # Sort by count desc
    topics = sorted(
        [TopicItem(topic=k, count=v) for k, v in topic_counts.items()],
        key=lambda x: x.count,
        reverse=True,
    )

    return TopicsStatsResponse(topics=topics, period=period_label)


class LangItem(BaseModel):
    lang: str    # ISO 639-1 code, e.g. "vi", "en", "zh-cn"
    count: int


class InputChannelsResponse(BaseModel):
    """Thống kê kênh nhập liệu của người dùng"""
    voice_invocations: int
    file_invocations: int
    text_invocations: int
    total_invocations: int
    languages: list[LangItem]   # full breakdown, sorted by count desc
    period: str




@metrics_tracking_router.get("/stats/input-channels", response_model=InputChannelsResponse)
async def get_input_channels(
    period: Optional[str] = Query(None, description="Tháng cần xem (YYYY-MM), mặc định tháng hiện tại"),
    days: Optional[int] = Query(None, description="Số ngày gần đây (ưu tiên hơn period nếu cung cấp)", ge=1, le=90),
    start_date: Optional[str] = Query(None, description="Ngày bắt đầu (YYYY-MM-DD), ưu tiên cao nhất"),
    end_date: Optional[str] = Query(None, description="Ngày kết thúc (YYYY-MM-DD)"),
):
    """
    Thống kê kênh nhập liệu và ngôn ngữ của người dùng.
    """
    if start_date and end_date:
        s_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        e_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        time_filter = "(e.timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh')::date BETWEEN :start_date AND :end_date"
        params: dict = {"start_date": s_date, "end_date": e_date}
        period_label = f"{start_date}_{end_date}"
    elif days is not None:
        start_dt = (_vn_now() - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        time_filter = "e.timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh' >= :start_dt"
        params = {"start_dt": start_dt}
        period_label = f"last_{days}_days"
    else:
        current_month, _, _ = _resolve_period(period)
        time_filter = "TO_CHAR(e.timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh', 'YYYY-MM') = :month"
        params = {"month": current_month}
        period_label = current_month

    async with _engine.connect() as conn:
        # Voice: invocations where user sent audio inline_data
        voice_result = await conn.execute(text(f"""
            SELECT COUNT(DISTINCT e.invocation_id)
            FROM events e,
                 jsonb_array_elements(e.content->'parts') AS part
            WHERE e.content IS NOT NULL
              AND e.content->>'role' = 'user'
              AND e.author = 'user'
              AND part ? 'inline_data'
              AND part->'inline_data'->>'mime_type' LIKE 'audio/%'
              AND {time_filter}
        """), params)
        voice_invocations = voice_result.scalar() or 0

        # File/image: invocations where user sent non-audio inline_data
        file_result = await conn.execute(text(f"""
            SELECT COUNT(DISTINCT e.invocation_id)
            FROM events e,
                 jsonb_array_elements(e.content->'parts') AS part
            WHERE e.content IS NOT NULL
              AND e.content->>'role' = 'user'
              AND e.author = 'user'
              AND part ? 'inline_data'
              AND part->'inline_data'->>'mime_type' NOT LIKE 'audio/%'
              AND {time_filter}
        """), params)
        file_invocations = file_result.scalar() or 0

        # Text-only: user messages with no inline_data at all
        text_result = await conn.execute(text(f"""
            SELECT COUNT(DISTINCT e.invocation_id)
            FROM events e
            WHERE e.content IS NOT NULL
              AND e.content->>'role' = 'user'
              AND e.author = 'user'
              AND NOT EXISTS (
                  SELECT 1 FROM jsonb_array_elements(e.content->'parts') p WHERE p ? 'inline_data'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM jsonb_array_elements(e.content->'parts') p WHERE p ? 'function_response'
              )
              AND {time_filter}
        """), params)
        text_invocations = text_result.scalar() or 0

        # Language breakdown from pre-computed language_code column (O(1) SQL, no Python loop)
        lang_result = await conn.execute(text(f"""
            SELECT language_code, COUNT(DISTINCT e.invocation_id) AS cnt
            FROM events e
            WHERE e.author = 'user'
              AND e.language_code IS NOT NULL
              AND {time_filter}
            GROUP BY e.language_code
            ORDER BY cnt DESC
        """), params)
        lang_rows = lang_result.fetchall()

    total_invocations = voice_invocations + file_invocations + text_invocations

    languages = [LangItem(lang=r.language_code, count=int(r.cnt)) for r in lang_rows]

    return InputChannelsResponse(
        voice_invocations=voice_invocations,
        file_invocations=file_invocations,
        text_invocations=text_invocations,
        total_invocations=total_invocations,
        languages=languages,
        period=period_label,
    )
