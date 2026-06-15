import logging
import os
import json
import pickle # Cảnh báo: Cố gắng loại bỏ pickle trong tương lai
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
logger = logging.getLogger(__name__)

SESSION_SERVICE_URI = os.getenv("SESSION_SERVICE_URI", "postgresql://user:pass@localhost:5432/db")

# 1. Khởi tạo Engine một lần duy nhất (Global)
engine = create_engine(SESSION_SERVICE_URI)

metrics_search_quality_router = APIRouter(prefix="/metrics", tags=["metrics"])

def get_db_connection():
    """FastAPI Dependency để quản lý DB connection"""
    with engine.connect() as conn:
        yield conn

def _vn_now() -> datetime:
    return datetime.now(tz=_VN_TZ)

def _resolve_period(period: Optional[str]) -> str:
    return period or _vn_now().strftime("%Y-%m")

# ──────────────────────────── Models ────────────────────────────

class SearchKeywordItem(BaseModel):
    keyword: str
    search_type: str
    result_status: str
    product_count: int
    timestamp: str # Pydantic v2 hỗ trợ datetime rất tốt, có thể cân nhắc đổi sang datetime
    original_query: Optional[str] = None

class SearchQualityResponse(BaseModel):
    total_searches: int
    successful: int
    failed: int
    null_response: int
    success_rate: float
    keywords: list[SearchKeywordItem]
    period: str

# ──────────────────────────── Endpoint ────────────────────────────

@metrics_search_quality_router.get("/stats/search-quality", response_model=SearchQualityResponse)
async def get_search_quality(
    period: Optional[str] = Query(None, description="Tháng cần xem (YYYY-MM), mặc định tháng hiện tại"),
    start_date: Optional[str] = Query(None, description="Ngày bắt đầu (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Ngày kết thúc (YYYY-MM-DD)"),
    conn = Depends(get_db_connection)
):
    """
    Thống kê chất lượng tìm kiếm sản phẩm theo tháng hoặc khoảng ngày.
    """
    if start_date and end_date:
        from datetime import datetime as _dt
        _start = _dt.strptime(start_date, "%Y-%m-%d").date()
        _end = _dt.strptime(end_date, "%Y-%m-%d").date()
        date_filter_sem = "(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh')::date BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)"
        date_filter_evt = "(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh')::date BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)"
        params: dict = {"start_date": _start, "end_date": _end}
        period_label = f"{start_date} → {end_date}"
    else:
        current_month = _resolve_period(period)
        date_filter_sem = "TO_CHAR(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh', 'YYYY-MM') = :month"
        date_filter_evt = "TO_CHAR(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Ho_Chi_Minh', 'YYYY-MM') = :month"
        params = {"month": current_month}
        period_label = current_month

    keywords: list[SearchKeywordItem] = []

    # ── Nguồn 1: semantic_search_log ──
    try:
        sem_rows = conn.execute(text(f"""
            SELECT original_query, keywords, timestamp
            FROM semantic_search_log
            WHERE {date_filter_sem}
            ORDER BY timestamp DESC
        """), params).fetchall()
    except Exception as e:
        logger.error(f"Error fetching semantic_search_log: {e}")
        sem_rows = []

    # ── Nguồn 2: events table (chatbot search) ──
    try:
        evt_rows = conn.execute(text(f"""
            SELECT invocation_id, actions, timestamp
            FROM events
            WHERE author = 'product_search_tool_caller' AND actions IS NOT NULL
              AND {date_filter_evt}
        """), params).fetchall()
    except Exception as e:
        logger.error(f"Error fetching events: {e}")
        evt_rows = []

    # ── Nguồn 2b: last_response_product_count từ response_generator ──
    response_product_counts: dict[str, int] = {}
    try:
        rsp_rows = conn.execute(text(f"""
            SELECT invocation_id, actions
            FROM events
            WHERE author = 'product_search_response_generator' AND actions IS NOT NULL
              AND {date_filter_evt}
        """), params).fetchall()
        for row in rsp_rows:
            try:
                action_data = pickle.loads(row.actions)
                state_delta = getattr(action_data, 'state_delta', {}) or {}
                if 'last_response_product_count' in state_delta:
                    response_product_counts[row.invocation_id] = state_delta['last_response_product_count']
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Error fetching response_generator events: {e}")

    # Process semantic_search_log rows
    for row in sem_rows:
        try:
            kw_list = json.loads(row.keywords) if row.keywords else []
            if not kw_list:
                kw_list = ["—"]
            
            timestamp_str = row.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") if row.timestamp else ""
            
            for kw in kw_list:
                keywords.append(SearchKeywordItem(
                    keyword=kw,
                    search_type="semantic_search",
                    result_status="success",
                    product_count=1,
                    timestamp=timestamp_str,
                    original_query=row.original_query,
                ))
        except json.JSONDecodeError as e:
             logger.warning(f"JSON decode error in semantic_search_log: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error processing semantic row: {e}")

    # Process events rows (chatbot search)
    unique_searches: dict[str, dict] = {}
    for row in evt_rows:
        try:
            timestamp_str = row.timestamp.isoformat() if row.timestamp else ""
            action_data = pickle.loads(row.actions)
            
            state_delta = getattr(action_data, 'state_delta', {}) or getattr(action_data, 'state', {}) or {}
            if not isinstance(state_delta, dict):
                continue
                
            if 'last_search_queries' not in state_delta or 'last_search_product_count' not in state_delta:
                continue
                
            queries_str = state_delta['last_search_queries']
            product_count = state_delta.get('last_search_product_count', 0)
            
            if not isinstance(queries_str, str):
                continue
                
            queries = json.loads(queries_str)
            if not isinstance(queries, list):
                continue
                
            per_kw_counts = state_delta.get('last_search_per_kw_counts') or {}
            
            for query in queries:
                if not isinstance(query, dict):
                    continue
                kw = query.get("keyword_in_vietnamese") or query.get("keyword") or "—"
                s_type = query.get("search_type") or "semantic_search"
                unique_id = f"{row.invocation_id}_{kw}_{s_type}"
                
                if unique_id not in unique_searches:
                    kw_count = per_kw_counts.get(kw, product_count if not per_kw_counts else 0)
                    # Ưu tiên dùng số SP AI thực sự show (sau filter), nếu có
                    actual_count = response_product_counts.get(row.invocation_id, kw_count)
                    unique_searches[unique_id] = {
                        "keyword": kw,
                        "search_type": s_type,
                        "timestamp": timestamp_str,
                        "original_query": query.get("original_query"),
                        "result_status": "success" if actual_count > 0 else "no_products",
                        "product_count": actual_count,
                    }
        except pickle.UnpicklingError as e:
            logger.error(f"Security/Pickle error on invocation_id {row.invocation_id}: {e}")
        except Exception as e:
            logger.warning(f"Error processing event row {row.invocation_id}: {e}")

    for data in unique_searches.values():
        keywords.append(SearchKeywordItem(**data))

    # Đảm bảo handle trường hợp timestamp bị None khi sort
    keywords.sort(key=lambda x: x.timestamp or "", reverse=True)

    total = len(keywords)
    successful = sum(1 for k in keywords if k.result_status == "success")
    failed = sum(1 for k in keywords if k.result_status == "no_products")
    null_response = sum(1 for k in keywords if k.result_status == "error")
    rate = (successful / total * 100) if total > 0 else 0.0

    return SearchQualityResponse(
        total_searches=total,
        successful=successful,
        failed=failed,
        null_response=null_response,
        success_rate=round(rate, 2),
        keywords=keywords,
        period=period_label,
    )