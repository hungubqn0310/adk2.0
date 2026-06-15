import csv
import io
import os
from typing import Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Query, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, text

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

def _vn_now() -> datetime:
    return datetime.now(tz=_VN_TZ)

# Cấu hình chuẩn cho PostgreSQL
SESSION_SERVICE_URI = os.getenv("SESSION_SERVICE_URI", "postgresql+psycopg2://user:pass@localhost:5432/db")

metrics_dashboard_router = APIRouter(prefix="/metrics", tags=["metrics"])

from mmvn_b2c_agent.shared.pricing import calc_cost as _calc_cost

# 1. KHỞI TẠO ENGINE 1 LẦN DUY NHẤT (GLOBAL)
engine = create_engine(SESSION_SERVICE_URI)

def get_db_connection():
    """FastAPI Dependency để dùng chung connection pool"""
    with engine.connect() as conn:
        yield conn

# ──────────────────────────── Models ────────────────────────────
class TokenMetrics(BaseModel):
    billing_month: str
    total_tokens: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    total_requests: int

class AgentMetrics(BaseModel):
    agent_name: str
    model_name: str
    total_tokens: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int

class ModelMetrics(BaseModel):
    model_name: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int

class MetricsDashboard(BaseModel):
    current_month: TokenMetrics
    agents_breakdown: list[AgentMetrics]
    models_breakdown: list[ModelMetrics]
    all_months: list[TokenMetrics]
    users_in_period: int = 0
    sessions_in_period: int = 0
    avg_cost_per_session: float = 0.0
    sessions_with_cost: int = 0

class DailyCostPoint(BaseModel):
    date: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int

class UserStatsResponse(BaseModel):
    total_users: int
    change_percent: float
    period: str

class SessionStatsResponse(BaseModel):
    total_sessions: int
    change_percent: float
    period: str

class ErrorBreakdown(BaseModel):
    system_error: int
    timeout: int
    no_match: int
    other: int

class TopError(BaseModel):
    message: str
    count: int

class AgentErrorStats(BaseModel):
    agent_name: str
    error_count: int
    error_rate: float

class ErrorMetricsResponse(BaseModel):
    total_errors: int
    total_turns: int
    error_rate: float
    error_breakdown: ErrorBreakdown
    top_errors: list[TopError]
    errors_by_agent: list[AgentErrorStats]

class SLOMetrics(BaseModel):
    availability: float
    error_rate: float
    stability_score: float
    total_turns: int
    total_errors: int
    timeout_rate: float
    system_error_rate: float
    period_days: int

class SessionCostItem(BaseModel):
    session_id: str
    user_id: Optional[str]
    cost: float
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    started_at: Optional[str] = None

# ──────────────────────────── Endpoints ────────────────────────────

@metrics_dashboard_router.get("/dashboard", response_model=MetricsDashboard)
async def get_metrics_dashboard(
    month: Optional[str] = Query(None, description="Billing month (YYYY-MM), defaults to current month"),
    start_date: Optional[str] = Query(None, description="Ngày bắt đầu (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Ngày kết thúc (YYYY-MM-DD)"),
    conn = Depends(get_db_connection)
):
    if start_date and end_date:
        _start = datetime.strptime(start_date, "%Y-%m-%d").date()
        _end = datetime.strptime(end_date, "%Y-%m-%d").date()
        token_where = "(recorded_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)"
        session_where = "(create_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh')::date BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)"
        params: dict = {"start_date": _start, "end_date": _end}
        period_label = f"{start_date} → {end_date}"
    else:
        if not month:
            month = _vn_now().strftime("%Y-%m")
        token_where = "billing_month = :month"
        session_where = "TO_CHAR(create_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh', 'YYYY-MM') = :month"
        params = {"month": month}
        period_label = month

    # Current period totals
    row = conn.execute(text(f"""
        SELECT
            COALESCE(SUM(input_tokens), 0)  AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
            COUNT(*)                         AS total_requests
        FROM token_usage
        WHERE {token_where}
    """), params).fetchone()

    current_month = TokenMetrics(
        billing_month=period_label,
        total_tokens=(row.input_tokens + row.output_tokens),
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cached_tokens=row.cached_tokens,
        total_requests=row.total_requests,
    )

    # Breakdown by agent + model
    agent_rows = conn.execute(text(f"""
        SELECT
            agent, model,
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
            COALESCE(SUM(input_tokens + output_tokens), 0) AS total_tokens
        FROM token_usage
        WHERE {token_where}
        GROUP BY agent, model
        ORDER BY total_tokens DESC
    """), params).fetchall()

    agents_breakdown = [AgentMetrics(agent_name=r.agent, model_name=r.model, total_tokens=r.total_tokens, input_tokens=r.input_tokens, output_tokens=r.output_tokens, cached_tokens=r.cached_tokens) for r in agent_rows]

    # Breakdown by model
    model_rows = conn.execute(text(f"""
        SELECT
            model,
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(cached_tokens), 0) AS cached_tokens
        FROM token_usage
        WHERE {token_where}
        GROUP BY model
        ORDER BY (SUM(input_tokens) + SUM(output_tokens)) DESC
    """), params).fetchall()

    models_breakdown = [ModelMetrics(model_name=r.model, input_tokens=r.input_tokens, output_tokens=r.output_tokens, cached_tokens=r.cached_tokens) for r in model_rows]

    # All months summary (always unfiltered)
    month_rows = conn.execute(text("""
        SELECT
            billing_month,
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
            COUNT(*) AS total_requests
        FROM token_usage
        GROUP BY billing_month
        ORDER BY billing_month DESC
    """)).fetchall()

    all_months = [TokenMetrics(billing_month=r.billing_month, total_tokens=(r.input_tokens + r.output_tokens), input_tokens=r.input_tokens, output_tokens=r.output_tokens, cached_tokens=r.cached_tokens, total_requests=r.total_requests) for r in month_rows]

    # Per-session cost
    session_token_rows = conn.execute(text(f"""
        SELECT session_id, model,
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(cached_tokens), 0) AS cached_tokens
        FROM token_usage
        WHERE {token_where} AND session_id IS NOT NULL
        GROUP BY session_id, model
    """), params).fetchall()

    session_costs: dict[str, float] = {}
    for r in session_token_rows:
        cost = _calc_cost(r.input_tokens, r.output_tokens, r.cached_tokens, r.model)
        session_costs[r.session_id] = session_costs.get(r.session_id, 0.0) + cost

    sessions_with_cost = len(session_costs)
    avg_cost_per_session = (sum(session_costs.values()) / sessions_with_cost) if sessions_with_cost > 0 else 0.0

    # Users and sessions
    users_row = conn.execute(text(f"""
        SELECT COUNT(DISTINCT user_id) FROM sessions
        WHERE {session_where}
    """), params).scalar()

    sessions_row = conn.execute(text(f"""
        SELECT COUNT(*) FROM sessions
        WHERE {session_where}
    """), params).scalar()

    return MetricsDashboard(
        current_month=current_month,
        agents_breakdown=agents_breakdown,
        models_breakdown=models_breakdown,
        all_months=all_months,
        users_in_period=users_row or 0,
        sessions_in_period=sessions_row or 0,
        avg_cost_per_session=avg_cost_per_session,
        sessions_with_cost=sessions_with_cost,
    )


@metrics_dashboard_router.get("/monthly/{month}", response_model=TokenMetrics)
async def get_monthly_metrics(month: str, conn = Depends(get_db_connection)):
    row = conn.execute(text("""
        SELECT
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
            COUNT(*) AS total_requests
        FROM token_usage
        WHERE billing_month = :month
    """), {"month": month}).fetchone()

    return TokenMetrics(
        billing_month=month,
        total_tokens=(row.input_tokens + row.output_tokens),
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cached_tokens=row.cached_tokens,
        total_requests=row.total_requests,
    )


@metrics_dashboard_router.get("/stats/cost-over-time", response_model=list[DailyCostPoint])
async def get_cost_over_time(
    days: int = Query(30, description="Number of days to look back"),
    start_date: Optional[str] = Query(None, description="Ngày bắt đầu (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Ngày kết thúc (YYYY-MM-DD)"),
    conn = Depends(get_db_connection)
):
    if start_date and end_date:
        rows = conn.execute(text("""
            SELECT
                DATE(recorded_at AT TIME ZONE 'Asia/Ho_Chi_Minh') AS day,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cached_tokens), 0) AS cached_tokens
            FROM token_usage
            WHERE (recorded_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date
                  BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)
            GROUP BY day
            ORDER BY day ASC
        """), {
            "start_date": datetime.strptime(start_date, "%Y-%m-%d").date(),
            "end_date": datetime.strptime(end_date, "%Y-%m-%d").date(),
        }).fetchall()
    else:
        rows = conn.execute(text("""
            SELECT
                DATE(recorded_at AT TIME ZONE 'Asia/Ho_Chi_Minh') AS day,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cached_tokens), 0) AS cached_tokens
            FROM token_usage
            WHERE recorded_at >= NOW() - INTERVAL '1 day' * :days
            GROUP BY day
            ORDER BY day ASC
        """), {"days": days}).fetchall()

    return [DailyCostPoint(date=str(r[0]), input_tokens=r[1], output_tokens=r[2], cached_tokens=r[3]) for r in rows]


@metrics_dashboard_router.get("/stats/users", response_model=UserStatsResponse)
async def get_total_users(
    start_date: Optional[str] = Query(None, description="Ngày bắt đầu (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Ngày kết thúc (YYYY-MM-DD)"),
    conn = Depends(get_db_connection)
):
    if start_date and end_date:
        start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()

        total_users = conn.execute(text("""
            SELECT COUNT(DISTINCT user_id) FROM sessions
            WHERE (create_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh')::date
                  BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)
        """), {"start_date": start_dt, "end_date": end_dt}).scalar() or 0

        duration = (end_dt - start_dt).days + 1
        prev_end = start_dt - timedelta(days=1)
        prev_start = prev_end - timedelta(days=duration - 1)

        prev_users = conn.execute(text("""
            SELECT COUNT(DISTINCT user_id) FROM sessions
            WHERE (create_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh')::date
                  BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)
        """), {"start_date": prev_start, "end_date": prev_end}).scalar() or 0

        change_percent = ((total_users - prev_users) / prev_users * 100) if prev_users > 0 else (100.0 if total_users > 0 else 0.0)
        period_label = f"{start_date} → {end_date}"
    else:
        current_month = _vn_now().strftime("%Y-%m")
        prev_month = (_vn_now().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

        total_users = conn.execute(text("SELECT COUNT(DISTINCT user_id) FROM sessions")).scalar() or 0
        current_month_users = conn.execute(text("""
            SELECT COUNT(DISTINCT user_id) FROM sessions
            WHERE TO_CHAR(create_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh', 'YYYY-MM') = :month
        """), {"month": current_month}).scalar() or 0

        prev_month_users = conn.execute(text("""
            SELECT COUNT(DISTINCT user_id) FROM sessions
            WHERE TO_CHAR(create_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh', 'YYYY-MM') = :month
        """), {"month": prev_month}).scalar() or 0

        change_percent = ((current_month_users - prev_month_users) / prev_month_users * 100) if prev_month_users > 0 else (100.0 if current_month_users > 0 else 0.0)
        period_label = current_month

    return UserStatsResponse(total_users=total_users, change_percent=round(change_percent, 1), period=period_label)


@metrics_dashboard_router.get("/stats/sessions", response_model=SessionStatsResponse)
async def get_total_sessions(
    start_date: Optional[str] = Query(None, description="Ngày bắt đầu (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Ngày kết thúc (YYYY-MM-DD)"),
    conn = Depends(get_db_connection)
):
    if start_date and end_date:
        start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()

        total_sessions = conn.execute(text("""
            SELECT COUNT(*) FROM sessions
            WHERE (create_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh')::date
                  BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)
        """), {"start_date": start_dt, "end_date": end_dt}).scalar() or 0

        duration = (end_dt - start_dt).days + 1
        prev_end = start_dt - timedelta(days=1)
        prev_start = prev_end - timedelta(days=duration - 1)

        prev_sessions = conn.execute(text("""
            SELECT COUNT(*) FROM sessions
            WHERE (create_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh')::date
                  BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)
        """), {"start_date": prev_start, "end_date": prev_end}).scalar() or 0

        change_percent = ((total_sessions - prev_sessions) / prev_sessions * 100) if prev_sessions > 0 else (100.0 if total_sessions > 0 else 0.0)
        period_label = f"{start_date} → {end_date}"
    else:
        current_month = _vn_now().strftime("%Y-%m")
        prev_month = (_vn_now().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

        total_sessions = conn.execute(text("SELECT COUNT(*) FROM sessions")).scalar() or 0
        current_month_sessions = conn.execute(text("""
            SELECT COUNT(*) FROM sessions
            WHERE TO_CHAR(create_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh', 'YYYY-MM') = :month
        """), {"month": current_month}).scalar() or 0

        prev_month_sessions = conn.execute(text("""
            SELECT COUNT(*) FROM sessions
            WHERE TO_CHAR(create_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh', 'YYYY-MM') = :month
        """), {"month": prev_month}).scalar() or 0

        change_percent = ((current_month_sessions - prev_month_sessions) / prev_month_sessions * 100) if prev_month_sessions > 0 else (100.0 if current_month_sessions > 0 else 0.0)
        period_label = current_month

    return SessionStatsResponse(total_sessions=total_sessions, change_percent=round(change_percent, 1), period=period_label)


@metrics_dashboard_router.get("/stats/errors", response_model=ErrorMetricsResponse)
async def get_error_metrics(
    days: int = Query(30, description="Number of days to look back"),
    start_date: Optional[str] = Query(None, description="Ngày bắt đầu (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Ngày kết thúc (YYYY-MM-DD)"),
    conn = Depends(get_db_connection)
):
    # Xóa bỏ các nhánh check SQLite thừa, tập trung viết chuẩn PostgreSQL
    if start_date and end_date:
        base_where = "(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh')::date BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)"
        params: dict = {
            "start_date": datetime.strptime(start_date, "%Y-%m-%d").date(),
            "end_date": datetime.strptime(end_date, "%Y-%m-%d").date(),
        }
    else:
        base_where = "timestamp >= NOW() - INTERVAL '1 day' * :days"
        params = {"days": days}

    error_case_sql = """
        CASE
            WHEN event_data->>'error_code' = 'timeout' THEN 'timeout'
            WHEN event_data->>'error_code' = 'no_match' THEN 'no_match'
            WHEN event_data->>'error_code' = 'malformed_function_call' THEN 'system_error'
            WHEN event_data->>'error_code' LIKE '%timeout%' OR event_data->>'error_message' LIKE '%timeout%' THEN 'timeout'
            WHEN event_data->>'error_code' LIKE '%not_found%' OR event_data->>'error_code' LIKE '%no_match%'
                 OR event_data->>'error_message' LIKE '%not found%' OR event_data->>'error_message' LIKE '%no match%' THEN 'no_match'
            WHEN event_data->>'error_code' IS NOT NULL OR event_data->>'error_message' IS NOT NULL THEN 'system_error'
            ELSE 'other'
        END
    """

    total_turns = conn.execute(text(f"SELECT COUNT(*) FROM events WHERE event_data->>'author' != 'system' AND {base_where}"), params).scalar() or 0
    total_errors = conn.execute(text(f"SELECT COUNT(*) FROM events WHERE (event_data->>'error_code' IS NOT NULL OR event_data->>'error_message' IS NOT NULL OR (event_data->>'interrupted')::boolean = TRUE) AND {base_where}"), params).scalar() or 0
    error_rate = (total_errors / total_turns) if total_turns > 0 else 0.0

    breakdown_rows = conn.execute(text(f"""
        SELECT {error_case_sql} AS error_type, COUNT(*) AS cnt
        FROM events
        WHERE (event_data->>'error_code' IS NOT NULL OR event_data->>'error_message' IS NOT NULL OR (event_data->>'interrupted')::boolean = TRUE) AND {base_where}
        GROUP BY error_type
    """), params).fetchall()

    breakdown = {"system_error": 0, "timeout": 0, "no_match": 0, "other": 0}
    for row in breakdown_rows:
        if row[0] in breakdown: breakdown[row[0]] = row[1]

    top_rows = conn.execute(text(f"""
        SELECT event_data->>'error_message' AS error_message, COUNT(*) AS cnt FROM events
        WHERE event_data->>'error_message' IS NOT NULL AND {base_where}
        GROUP BY error_message ORDER BY cnt DESC LIMIT 10
    """), params).fetchall()

    agent_turns = {row[0]: row[1] for row in conn.execute(text(f"SELECT app_name, COUNT(*) FROM events WHERE event_data->>'author' != 'system' AND {base_where} GROUP BY app_name"), params).fetchall()}

    agent_error_rows = conn.execute(text(f"""
        SELECT app_name, COUNT(*) AS cnt FROM events
        WHERE (event_data->>'error_code' IS NOT NULL OR event_data->>'error_message' IS NOT NULL OR (event_data->>'interrupted')::boolean = TRUE)
        AND {base_where} AND app_name IS NOT NULL
        GROUP BY app_name ORDER BY cnt DESC
    """), params).fetchall()

    errors_by_agent = [AgentErrorStats(agent_name=r[0], error_count=r[1], error_rate=round(r[1] / agent_turns.get(r[0], 1), 4)) for r in agent_error_rows]

    return ErrorMetricsResponse(
        total_errors=total_errors, total_turns=total_turns, error_rate=round(error_rate, 4),
        error_breakdown=ErrorBreakdown(**breakdown),
        top_errors=[TopError(message=r[0], count=r[1]) for r in top_rows],
        errors_by_agent=errors_by_agent
    )


@metrics_dashboard_router.get("/stats/slo", response_model=SLOMetrics)
async def get_slo_metrics(
    days: int = Query(30, description="Number of days to look back"),
    start_date: Optional[str] = Query(None, description="Ngày bắt đầu (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Ngày kết thúc (YYYY-MM-DD)"),
    conn = Depends(get_db_connection)
):
    if start_date and end_date:
        base_where = "(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh')::date BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)"
        params: dict = {
            "start_date": datetime.strptime(start_date, "%Y-%m-%d").date(),
            "end_date": datetime.strptime(end_date, "%Y-%m-%d").date(),
        }
        period_days = (datetime.strptime(end_date, "%Y-%m-%d").date() - datetime.strptime(start_date, "%Y-%m-%d").date()).days + 1
    else:
        base_where = "timestamp >= NOW() - INTERVAL '1 day' * :days"
        params = {"days": days}
        period_days = days
    
    error_case_sql = """
        CASE
            WHEN event_data->>'error_code' = 'timeout' THEN 'timeout'
            WHEN event_data->>'error_code' LIKE '%timeout%' OR event_data->>'error_message' LIKE '%timeout%' THEN 'timeout'
            WHEN event_data->>'error_code' = 'malformed_function_call' THEN 'system_error'
            WHEN event_data->>'error_code' IS NOT NULL OR event_data->>'error_message' IS NOT NULL THEN 'system_error'
            WHEN (event_data->>'interrupted')::boolean = TRUE THEN 'system_error'
            ELSE 'other'
        END
    """

    total_turns = conn.execute(text(f"SELECT COUNT(*) FROM events WHERE event_data->>'author' != 'system' AND {base_where}"), params).scalar() or 0
    total_errors = conn.execute(text(f"SELECT COUNT(*) FROM events WHERE (event_data->>'error_code' IS NOT NULL OR event_data->>'error_message' IS NOT NULL OR (event_data->>'interrupted')::boolean = TRUE) AND {base_where}"), params).scalar() or 0

    breakdown_rows = conn.execute(text(f"""
        SELECT {error_case_sql} AS error_type, COUNT(*) AS cnt
        FROM events
        WHERE (event_data->>'error_code' IS NOT NULL OR event_data->>'error_message' IS NOT NULL OR (event_data->>'interrupted')::boolean = TRUE) AND {base_where}
        GROUP BY error_type
    """), params).fetchall()

    breakdown = {"system_error": 0, "timeout": 0, "other": 0}
    for row in breakdown_rows:
        if row[0] in breakdown: breakdown[row[0]] = row[1]

    availability = ((total_turns - total_errors) / total_turns * 100) if total_turns > 0 else 100.0
    error_rate_pct = (total_errors / total_turns * 100) if total_turns > 0 else 0.0
    timeout_rate = (breakdown["timeout"] / total_turns * 100) if total_turns > 0 else 0.0
    system_error_rate = (breakdown["system_error"] / total_turns * 100) if total_turns > 0 else 0.0

    stability_score = (availability * 0.6 + (100 - timeout_rate) * 0.2 + (100 - system_error_rate) * 0.2)

    return SLOMetrics(
        availability=round(availability, 2), error_rate=round(error_rate_pct, 2),
        stability_score=round(stability_score, 2), total_turns=total_turns,
        total_errors=total_errors, timeout_rate=round(timeout_rate, 2),
        system_error_rate=round(system_error_rate, 2), period_days=period_days
    )


@metrics_dashboard_router.get("/stats/session-costs", response_model=list[SessionCostItem])
async def get_session_costs(
    month: Optional[str] = Query(None, description="Billing month (YYYY-MM), defaults to current month"),
    start_date: Optional[str] = Query(None, description="Ngày bắt đầu (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Ngày kết thúc (YYYY-MM-DD)"),
    limit: int = Query(20, description="Max sessions to return"),
    conn = Depends(get_db_connection)
):
    if start_date and end_date:
        _start = datetime.strptime(start_date, "%Y-%m-%d").date()
        _end = datetime.strptime(end_date, "%Y-%m-%d").date()
        token_where = "(recorded_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)"
        params: dict = {"start_date": _start, "end_date": _end}
    else:
        if not month:
            month = _vn_now().strftime("%Y-%m")
        token_where = "billing_month = :month"
        params = {"month": month}

    rows = conn.execute(text(f"""
        SELECT
            session_id,
            user_id,
            model,
            COALESCE(SUM(input_tokens), 0)  AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
            MIN(recorded_at AT TIME ZONE 'Asia/Ho_Chi_Minh') AS started_at
        FROM token_usage
        WHERE {token_where} AND session_id IS NOT NULL
        GROUP BY session_id, user_id, model
    """), params).fetchall()

    # Aggregate per session across models
    session_map: dict[str, dict] = {}
    for r in rows:
        sid = r.session_id
        cost = _calc_cost(r.input_tokens, r.output_tokens, r.cached_tokens, r.model)
        if sid not in session_map:
            session_map[sid] = {
                "session_id": sid,
                "user_id": r.user_id,
                "cost": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "_started_at_raw": r.started_at,
            }
        session_map[sid]["cost"] += cost
        session_map[sid]["input_tokens"] += r.input_tokens
        session_map[sid]["output_tokens"] += r.output_tokens
        session_map[sid]["cached_tokens"] += r.cached_tokens
        if r.started_at and (session_map[sid]["_started_at_raw"] is None or r.started_at < session_map[sid]["_started_at_raw"]):
            session_map[sid]["_started_at_raw"] = r.started_at

    for s in session_map.values():
        raw = s.pop("_started_at_raw")
        s["started_at"] = raw.strftime("%d/%m/%Y %H:%M") if raw else None

    sorted_sessions = sorted(session_map.values(), key=lambda x: x["cost"], reverse=True)
    return [SessionCostItem(**s) for s in sorted_sessions[:limit]]

@metrics_dashboard_router.get("/export/sessions")
def export_sessions_csv(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date:   Optional[str] = Query(None, description="YYYY-MM-DD"),
    month:      Optional[str] = Query(None, description="YYYY-MM"),
    limit:      int           = Query(10000, ge=1, le=100000),
    conn=Depends(get_db_connection),
):
    """Export raw session data as CSV."""
    # Resolve date range
    if month:
        try:
            dt = datetime.strptime(month, "%Y-%m")
        except ValueError:
            dt = _vn_now().replace(day=1)
        start_date = dt.strftime("%Y-%m-01")
        import calendar
        last_day = calendar.monthrange(dt.year, dt.month)[1]
        end_date = dt.strftime(f"%Y-%m-{last_day:02d}")
    elif not start_date or not end_date:
        now = _vn_now()
        end_date   = now.strftime("%Y-%m-%d")
        start_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")

    sql = text("""
    WITH session_base AS (
        SELECT
            s.id            AS session_id,
            s.user_id,
            s.create_time,
            s.state
        FROM sessions s
        WHERE s.create_time >= :start_ts
          AND s.create_time <  :end_ts
    ),
    session_duration AS (
        SELECT
            session_id,
            EXTRACT(EPOCH FROM (MAX(timestamp) - MIN(timestamp)))::int AS duration_seconds,
            COUNT(*) FILTER (WHERE event_data->>'author' = 'user') AS turn_count
        FROM events
        GROUP BY session_id
    ),
    session_language AS (
        SELECT DISTINCT ON (session_id)
            session_id,
            language_code
        FROM events
        WHERE event_data->>'author' = 'user'
          AND language_code IS NOT NULL
        ORDER BY session_id, timestamp DESC
    ),
    session_multimedia AS (
        SELECT
            e.session_id,
            BOOL_OR(
                EXISTS (
                    SELECT 1 FROM jsonb_array_elements(e.event_data->'content'->'parts') AS p
                    WHERE p->>'inline_data' IS NOT NULL
                      AND (p->'inline_data'->>'mime_type') LIKE 'audio/%'
                )
            ) AS has_audio,
            BOOL_OR(
                EXISTS (
                    SELECT 1 FROM jsonb_array_elements(e.event_data->'content'->'parts') AS p
                    WHERE p->>'inline_data' IS NOT NULL
                      AND (p->'inline_data'->>'mime_type') NOT LIKE 'audio/%'
                )
            ) AS has_file
        FROM events e
        WHERE e.event_data->>'author' = 'user'
          AND e.event_data->'content' IS NOT NULL
        GROUP BY e.session_id
    ),
    session_orders AS (
        SELECT
            e.session_id,
            TRUE AS has_order,
            STRING_AGG(
                DISTINCT (p->'function_response'->'response'->>'order_number'),
                ', '
            ) AS order_ids
        FROM events e,
             LATERAL jsonb_array_elements(e.event_data->'content'->'parts') AS p
        WHERE p->'function_response'->>'name' = 'show_payment_methods'
          AND (p->'function_response'->'response'->>'order_number') IS NOT NULL
        GROUP BY e.session_id
    ),
    session_feedback AS (
        SELECT
            session_id,
            COUNT(*) FILTER (WHERE event_data->'custom_metadata'->>'feedback' = 'thumbs_up')   AS thumbs_up,
            COUNT(*) FILTER (WHERE event_data->'custom_metadata'->>'feedback' = 'thumbs_down')  AS thumbs_down,
            COUNT(*) FILTER (WHERE event_data->'custom_metadata'->>'feedback' IS NOT NULL)       AS total_feedback
        FROM events
        GROUP BY session_id
    ),
    session_errors AS (
        SELECT
            session_id,
            COUNT(*) AS total_events,
            COUNT(*) FILTER (WHERE event_data->>'error_code' IS NOT NULL) AS error_events
        FROM events
        GROUP BY session_id
    ),
    invocation_times AS (
        SELECT
            session_id,
            invocation_id,
            MIN(timestamp) FILTER (WHERE event_data->>'author' = 'user') AS user_ts,
            MIN(timestamp) FILTER (
                WHERE event_data->>'author' NOT IN ('user', 'system')
                  AND NOT (
                        event_data->'content' IS NOT NULL
                    AND jsonb_array_length(event_data->'content'->'parts') > 0
                    AND (event_data->'content'->'parts'->0)->>'functionResponse' IS NOT NULL
                  )
            ) AS agent_ts
        FROM events
        GROUP BY session_id, invocation_id
    ),
    session_response_time AS (
        SELECT
            session_id,
            AVG(
                EXTRACT(EPOCH FROM (agent_ts - user_ts))
            )::numeric(10,2) AS avg_response_seconds
        FROM invocation_times
        WHERE user_ts IS NOT NULL
          AND agent_ts IS NOT NULL
          AND agent_ts > user_ts
          AND EXTRACT(EPOCH FROM (agent_ts - user_ts)) <= 300
        GROUP BY session_id
    ),
    session_tokens AS (
        SELECT
            session_id,
            SUM(input_tokens)   AS total_input_tokens,
            SUM(output_tokens)  AS total_output_tokens,
            SUM(cached_tokens)  AS total_cached_tokens,
            SUM(input_tokens + output_tokens + cached_tokens) AS total_tokens,
            COUNT(*)            AS turn_rows,
            STRING_AGG(DISTINCT model, ', ' ORDER BY model) AS models_used
        FROM token_usage
        GROUP BY session_id
    )
    SELECT
        sb.session_id,
        CASE WHEN sb.user_id = 'user' THEN 'Guest' ELSE 'Logged-in' END   AS customer_type,
        sb.user_id,
        sb.create_time,
        COALESCE(sl.language_code, 'unknown')                              AS language,
        COALESCE(sd.duration_seconds, 0)                                   AS duration_seconds,
        COALESCE(sd.turn_count, 0)                                         AS turn_count,
        COALESCE(sm.has_audio, FALSE)                                      AS has_audio,
        COALESCE(sm.has_file, FALSE)                                       AS has_file,
        COALESCE(so.has_order, FALSE)                                      AS has_order,
        COALESCE(so.order_ids, '')                                         AS order_ids,
        COALESCE(sf.thumbs_up, 0)                                          AS thumbs_up,
        COALESCE(sf.thumbs_down, 0)                                        AS thumbs_down,
        COALESCE(se.total_events, 0)                                       AS total_events,
        COALESCE(se.error_events, 0)                                       AS error_events,
        CASE
            WHEN COALESCE(se.total_events, 0) > 0
            THEN ROUND(se.error_events::numeric / se.total_events * 100, 2)
            ELSE 0
        END                                                                AS error_rate_pct,
        COALESCE(srt.avg_response_seconds, 0)                              AS avg_response_seconds,
        COALESCE(st.total_input_tokens, 0)                                 AS input_tokens,
        COALESCE(st.total_output_tokens, 0)                                AS output_tokens,
        COALESCE(st.total_cached_tokens, 0)                                AS cached_tokens,
        COALESCE(st.total_tokens, 0)                                       AS total_tokens,
        CASE
            WHEN COALESCE(sd.turn_count, 0) > 0
            THEN ROUND(COALESCE(st.total_tokens, 0)::numeric / sd.turn_count, 1)
            ELSE 0
        END                                                                AS avg_tokens_per_turn,
        COALESCE(st.models_used, 'unknown')                                AS model_name,
        COALESCE(sb.state->'state'->'magento_session_data'->>'store_id', 'unknown') AS store_id
    FROM session_base sb
    LEFT JOIN session_duration    sd  ON sd.session_id  = sb.session_id
    LEFT JOIN session_language    sl  ON sl.session_id  = sb.session_id
    LEFT JOIN session_multimedia  sm  ON sm.session_id  = sb.session_id
    LEFT JOIN session_orders      so  ON so.session_id  = sb.session_id
    LEFT JOIN session_feedback    sf  ON sf.session_id  = sb.session_id
    LEFT JOIN session_errors      se  ON se.session_id  = sb.session_id
    LEFT JOIN session_response_time srt ON srt.session_id = sb.session_id
    LEFT JOIN session_tokens      st  ON st.session_id  = sb.session_id
    WHERE COALESCE(sd.turn_count, 0) > 0  
    ORDER BY sb.create_time DESC
    LIMIT :limit
    """)
    #AND st.session_id IS NOT NULL thêm vào dưới where nếu muốn lọc các session có token
    start_ts = f"{start_date} 00:00:00"
    end_ts   = f"{end_date} 23:59:59"
    rows = conn.execute(sql, {"start_ts": start_ts, "end_ts": end_ts, "limit": limit}).fetchall()

    def generate_csv():
        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_ALL)
        writer.writerow([
            "Session ID",
            "Loại khách hàng",
            "User ID",
            "Thời gian bắt đầu",
            "Ngôn ngữ",
            "Thời lượng (giây)",
            "Số lượt chat",
            "Có âm thanh",
            "Có file/ảnh",
            "Có đơn hàng",
            "Mã đơn hàng",
            "CSAT Tích cực",
            "CSAT Tiêu cực",
            "Tổng sự kiện",
            "Sự kiện lỗi",
            "Tỷ lệ lỗi (%)",
            "Tg phản hồi TB (giây)",
            "Input Tokens",
            "Output Tokens",
            "Cached Tokens",
            "Tổng Tokens",
            "Tokens TB/lượt",
            "Model",
            "Store ID",
        ])
        yield output.getvalue()

        for row in rows:
            output = io.StringIO()
            writer = csv.writer(output, quoting=csv.QUOTE_ALL)
            create_time = row.create_time
            if hasattr(create_time, "strftime"):
                # Convert UTC → Vietnam time (UTC+7)
                if create_time.tzinfo is None:
                    create_time = create_time.replace(tzinfo=ZoneInfo("UTC"))
                create_time_str = create_time.astimezone(_VN_TZ).strftime("%Y-%m-%d %H:%M:%S")
            else:
                create_time_str = str(create_time)
            writer.writerow([
                row.session_id,
                row.customer_type,
                row.user_id,
                create_time_str,
                row.language,
                row.duration_seconds,
                row.turn_count,
                "Có" if row.has_audio   else "Không",
                "Có" if row.has_file    else "Không",
                "Có" if row.has_order   else "Không",
                row.order_ids,
                row.thumbs_up,
                row.thumbs_down,
                row.total_events,
                row.error_events,
                row.error_rate_pct,
                row.avg_response_seconds,
                row.input_tokens,
                row.output_tokens,
                row.cached_tokens,
                row.total_tokens,
                row.avg_tokens_per_turn,
                row.model_name,
                row.store_id,
            ])
            yield output.getvalue()

    filename = f"sessions_{start_date}_{end_date}.csv"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "text/csv; charset=utf-8-sig",
    }
    return StreamingResponse(generate_csv(), media_type="text/csv; charset=utf-8-sig", headers=headers)


@metrics_dashboard_router.get("/export/sessions/preview")
def preview_sessions(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date:   Optional[str] = Query(None, description="YYYY-MM-DD"),
    month:      Optional[str] = Query(None, description="YYYY-MM"),
    limit:      int           = Query(100, ge=1, le=1000),
    conn=Depends(get_db_connection),
):
    """Preview session export data as JSON (max 1000 rows)."""
    import calendar as _cal
    if month:
        try:
            dt = datetime.strptime(month, "%Y-%m")
        except ValueError:
            dt = _vn_now().replace(day=1)
        start_date = dt.strftime("%Y-%m-01")
        last_day = _cal.monthrange(dt.year, dt.month)[1]
        end_date = dt.strftime(f"%Y-%m-{last_day:02d}")
    elif not start_date or not end_date:
        now = _vn_now()
        end_date   = now.strftime("%Y-%m-%d")
        start_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")

    sql = text("""
    WITH session_base AS (
        SELECT s.id AS session_id, s.user_id, s.create_time, s.state
        FROM sessions s
        WHERE s.create_time >= :start_ts AND s.create_time < :end_ts
    ),
    session_duration AS (
        SELECT session_id,
               EXTRACT(EPOCH FROM (MAX(timestamp) - MIN(timestamp)))::int AS duration_seconds,
               COUNT(*) FILTER (WHERE event_data->>'author' = 'user') AS turn_count
        FROM events GROUP BY session_id
    ),
    session_language AS (
        SELECT DISTINCT ON (session_id) session_id, language_code
        FROM events WHERE event_data->>'author' = 'user' AND language_code IS NOT NULL
        ORDER BY session_id, timestamp DESC
    ),
    session_multimedia AS (
        SELECT e.session_id,
               BOOL_OR(EXISTS (SELECT 1 FROM jsonb_array_elements(e.event_data->'content'->'parts') AS p WHERE p->>'inline_data' IS NOT NULL AND (p->'inline_data'->>'mime_type') LIKE 'audio/%')) AS has_audio,
               BOOL_OR(EXISTS (SELECT 1 FROM jsonb_array_elements(e.event_data->'content'->'parts') AS p WHERE p->>'inline_data' IS NOT NULL AND (p->'inline_data'->>'mime_type') NOT LIKE 'audio/%')) AS has_file
        FROM events e WHERE e.event_data->>'author' = 'user' AND e.event_data->'content' IS NOT NULL GROUP BY e.session_id
    ),
    session_orders AS (
        SELECT e.session_id, TRUE AS has_order,
               STRING_AGG(DISTINCT (p->'function_response'->'response'->>'order_number'), ', ') AS order_ids
        FROM events e, LATERAL jsonb_array_elements(e.event_data->'content'->'parts') AS p
        WHERE p->'function_response'->>'name' = 'show_payment_methods'
          AND (p->'function_response'->'response'->>'order_number') IS NOT NULL
        GROUP BY e.session_id
    ),
    session_feedback AS (
        SELECT session_id,
               COUNT(*) FILTER (WHERE event_data->'custom_metadata'->>'feedback' = 'thumbs_up')  AS thumbs_up,
               COUNT(*) FILTER (WHERE event_data->'custom_metadata'->>'feedback' = 'thumbs_down') AS thumbs_down
        FROM events GROUP BY session_id
    ),
    session_errors AS (
        SELECT session_id, COUNT(*) AS total_events,
               COUNT(*) FILTER (WHERE event_data->>'error_code' IS NOT NULL) AS error_events
        FROM events GROUP BY session_id
    ),
    invocation_times AS (
        SELECT session_id, invocation_id,
               MIN(timestamp) FILTER (WHERE event_data->>'author' = 'user') AS user_ts,
               MIN(timestamp) FILTER (WHERE event_data->>'author' NOT IN ('user','system') AND NOT (event_data->'content' IS NOT NULL AND jsonb_array_length(event_data->'content'->'parts') > 0 AND (event_data->'content'->'parts'->0)->>'functionResponse' IS NOT NULL)) AS agent_ts
        FROM events GROUP BY session_id, invocation_id
    ),
    session_response_time AS (
        SELECT session_id,
               AVG(EXTRACT(EPOCH FROM (agent_ts - user_ts)))::numeric(10,2) AS avg_response_seconds
        FROM invocation_times
        WHERE user_ts IS NOT NULL AND agent_ts IS NOT NULL AND agent_ts > user_ts
          AND EXTRACT(EPOCH FROM (agent_ts - user_ts)) <= 300
        GROUP BY session_id
    ),
    session_tokens AS (
        SELECT session_id,
               SUM(input_tokens) AS total_input_tokens,
               SUM(output_tokens) AS total_output_tokens,
               SUM(cached_tokens) AS total_cached_tokens,
               SUM(input_tokens + output_tokens + cached_tokens) AS total_tokens,
               STRING_AGG(DISTINCT model, ', ' ORDER BY model) AS models_used
        FROM token_usage GROUP BY session_id
    )
    SELECT
        sb.session_id,
        CASE WHEN sb.user_id = 'user' THEN 'Guest' ELSE 'Logged-in' END AS customer_type,
        sb.user_id,
        sb.create_time,
        COALESCE(sl.language_code, 'unknown') AS language,
        COALESCE(sd.duration_seconds, 0) AS duration_seconds,
        COALESCE(sd.turn_count, 0) AS turn_count,
        COALESCE(sm.has_audio, FALSE) AS has_audio,
        COALESCE(sm.has_file, FALSE) AS has_file,
        COALESCE(so.has_order, FALSE) AS has_order,
        COALESCE(so.order_ids, '') AS order_ids,
        COALESCE(sf.thumbs_up, 0) AS thumbs_up,
        COALESCE(sf.thumbs_down, 0) AS thumbs_down,
        COALESCE(se.total_events, 0) AS total_events,
        COALESCE(se.error_events, 0) AS error_events,
        CASE WHEN COALESCE(se.total_events,0) > 0 THEN ROUND(se.error_events::numeric/se.total_events*100,2) ELSE 0 END AS error_rate_pct,
        COALESCE(srt.avg_response_seconds, 0) AS avg_response_seconds,
        COALESCE(st.total_input_tokens, 0) AS input_tokens,
        COALESCE(st.total_output_tokens, 0) AS output_tokens,
        COALESCE(st.total_cached_tokens, 0) AS cached_tokens,
        COALESCE(st.total_tokens, 0) AS total_tokens,
        CASE WHEN COALESCE(sd.turn_count,0) > 0 THEN ROUND(COALESCE(st.total_tokens,0)::numeric/sd.turn_count,1) ELSE 0 END AS avg_tokens_per_turn,
        COALESCE(st.models_used, 'unknown') AS model_name,
        COALESCE(sb.state->'state'->'magento_session_data'->>'store_id', 'unknown') AS store_id
    FROM session_base sb
    LEFT JOIN session_duration    sd  ON sd.session_id  = sb.session_id
    LEFT JOIN session_language    sl  ON sl.session_id  = sb.session_id
    LEFT JOIN session_multimedia  sm  ON sm.session_id  = sb.session_id
    LEFT JOIN session_orders      so  ON so.session_id  = sb.session_id
    LEFT JOIN session_feedback    sf  ON sf.session_id  = sb.session_id
    LEFT JOIN session_errors      se  ON se.session_id  = sb.session_id
    LEFT JOIN session_response_time srt ON srt.session_id = sb.session_id
    LEFT JOIN session_tokens      st  ON st.session_id  = sb.session_id
    WHERE COALESCE(sd.turn_count, 0) > 0
    ORDER BY sb.create_time DESC
    LIMIT :limit
    """)
    #      AND st.session_id IS NOT NULL cho ở dưới WHERE COALESCE(sd.turn_count, 0) > 0 nếu filter theo lượng sd token
    start_ts = f"{start_date} 00:00:00"
    end_ts   = f"{end_date} 23:59:59"
    rows = conn.execute(sql, {"start_ts": start_ts, "end_ts": end_ts, "limit": limit}).fetchall()

    return {
        "total": len(rows),
        "start_date": start_date,
        "end_date": end_date,
        "rows": [
            {
                "session_id": r.session_id,
                "customer_type": r.customer_type,
                "user_id": r.user_id,
                "create_time": (
                    r.create_time.replace(tzinfo=ZoneInfo("UTC")).astimezone(_VN_TZ).strftime("%Y-%m-%d %H:%M:%S")
                    if hasattr(r.create_time, "strftime") and r.create_time.tzinfo is None
                    else r.create_time.astimezone(_VN_TZ).strftime("%Y-%m-%d %H:%M:%S")
                    if hasattr(r.create_time, "strftime")
                    else str(r.create_time)
                ),
                "language": r.language,
                "duration_seconds": r.duration_seconds,
                "turn_count": r.turn_count,
                "has_audio": r.has_audio,
                "has_file": r.has_file,
                "has_order": r.has_order,
                "order_ids": r.order_ids,
                "thumbs_up": r.thumbs_up,
                "thumbs_down": r.thumbs_down,
                "total_events": r.total_events,
                "error_events": r.error_events,
                "error_rate_pct": float(r.error_rate_pct),
                "avg_response_seconds": float(r.avg_response_seconds),
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cached_tokens": r.cached_tokens,
                "total_tokens": r.total_tokens,
                "avg_tokens_per_turn": float(r.avg_tokens_per_turn),
                "model_name": r.model_name,
                "store_id": r.store_id,
            }
            for r in rows
        ],
    }
