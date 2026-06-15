"""
API endpoint for message feedback (thumbs up/down).
Stores feedback in custom_metadata of the last model event in an invocation.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

SESSION_SERVICE_URI = os.getenv("SESSION_SERVICE_URI", "sqlite:///./data/sessions.db")

feedback_router = APIRouter(tags=["feedback"])


class FeedbackRequest(BaseModel):
    invocation_id: str
    rating: Literal["up", "down"]
    comment: Optional[str] = None


class FeedbackResponse(BaseModel):
    success: bool
    invocation_id: str
    rating: Optional[str]   # None khi đã xóa feedback (toggle off)
    action: Literal["saved", "updated", "removed"]


def _get_db_engine():
    return create_engine(SESSION_SERVICE_URI)


def _extract_feedback_rating(meta) -> Optional[str]:
    """Lấy rating từ custom_metadata (có thể là array hoặc object)."""
    if isinstance(meta, list):
        for item in meta:
            if isinstance(item, dict) and "feedback" in item:
                return item["feedback"].get("rating")
    elif isinstance(meta, dict):
        return meta.get("feedback", {}).get("rating")
    return None


def _remove_feedback(meta) -> any:
    """Xóa key 'feedback' khỏi custom_metadata."""
    if isinstance(meta, list):
        return [
            {k: v for k, v in item.items() if k != "feedback"} if isinstance(item, dict) else item
            for item in meta
        ]
    elif isinstance(meta, dict):
        return {k: v for k, v in meta.items() if k != "feedback"}
    return meta


def _set_feedback(meta, feedback_payload: dict) -> any:
    """Ghi feedback vào custom_metadata, giữ nguyên cấu trúc array/object."""
    if isinstance(meta, list):
        updated = False
        result = []
        for item in meta:
            if isinstance(item, dict) and "feedback" in item:
                result.append({**item, "feedback": feedback_payload})
                updated = True
            else:
                result.append(item)
        if not updated:
            # Thêm vào phần tử dict cuối cùng, hoặc append object mới
            for i in range(len(result) - 1, -1, -1):
                if isinstance(result[i], dict):
                    result[i] = {**result[i], "feedback": feedback_payload}
                    updated = True
                    break
            if not updated:
                result.append({"feedback": feedback_payload})
        return result
    elif isinstance(meta, dict):
        return {**meta, "feedback": feedback_payload}
    return {"feedback": feedback_payload}


@feedback_router.post(
    "/apps/{app_name}/users/{user_id}/sessions/{session_id}/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_200_OK,
)
async def submit_feedback(
    app_name: str,
    user_id: str,
    session_id: str,
    body: FeedbackRequest,
    response: Response,
):
    """
    Submit thumbs up/down feedback for an AI response. Supports toggle:
    - Ấn lần đầu: lưu feedback
    - Ấn lại cùng rating: xóa feedback (toggle off)
    - Ấn rating khác: cập nhật sang rating mới
    """
    engine = _get_db_engine()

    with engine.connect() as conn:
        # Fetch the last model event: id, current custom_metadata, response text
        result = conn.execute(
            text("""
                SELECT id,
                       custom_metadata,
                       (SELECT string_agg(p->>'text', '')
                        FROM jsonb_array_elements(content->'parts') p
                        WHERE p ? 'text') AS response_text
                FROM events
                WHERE app_name = :app_name
                  AND user_id = :user_id
                  AND session_id = :session_id
                  AND invocation_id = :invocation_id
                  AND content->>'role' = 'model'
                ORDER BY timestamp DESC
                LIMIT 1
            """),
            {
                "app_name": app_name,
                "user_id": user_id,
                "session_id": session_id,
                "invocation_id": body.invocation_id,
            },
        )
        row = result.fetchone()

        if not row:
            response.status_code = status.HTTP_404_NOT_FOUND
            return {"success": False, "invocation_id": body.invocation_id, "rating": None, "action": "removed"}

        event_id = row.id

        # custom_metadata có thể là array hoặc object — đọc existing feedback từ Python
        raw_meta = row.custom_metadata  # đã là dict/list do SQLAlchemy parse JSONB
        existing_rating = _extract_feedback_rating(raw_meta)

        # Toggle: ấn lại cùng rating → xóa feedback
        if existing_rating == body.rating:
            new_meta = _remove_feedback(raw_meta)
            conn.execute(
                text("""
                    UPDATE events
                    SET custom_metadata = CAST(:meta AS jsonb)
                    WHERE id = :event_id AND app_name = :app_name
                      AND user_id = :user_id AND session_id = :session_id
                """),
                {"meta": json.dumps(new_meta), "event_id": event_id,
                 "app_name": app_name, "user_id": user_id, "session_id": session_id},
            )
            conn.commit()
            logger.info(f"Feedback removed for invocation {body.invocation_id}")
            return FeedbackResponse(success=True, invocation_id=body.invocation_id, rating=None, action="removed")

        # Lưu mới hoặc cập nhật sang rating khác
        action = "updated" if existing_rating else "saved"
        feedback_payload = {
            "rating": body.rating,
            "comment": body.comment,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        if row.response_text:
            feedback_payload["response_text"] = row.response_text

        new_meta = _set_feedback(raw_meta, feedback_payload)
        conn.execute(
            text("""
                UPDATE events
                SET custom_metadata = CAST(:meta AS jsonb)
                WHERE id = :event_id AND app_name = :app_name
                  AND user_id = :user_id AND session_id = :session_id
            """),
            {"meta": json.dumps(new_meta, ensure_ascii=False), "event_id": event_id,
             "app_name": app_name, "user_id": user_id, "session_id": session_id},
        )
        conn.commit()

    logger.info(f"Feedback '{body.rating}' {action} for invocation {body.invocation_id}")
    return FeedbackResponse(success=True, invocation_id=body.invocation_id, rating=body.rating, action=action)
