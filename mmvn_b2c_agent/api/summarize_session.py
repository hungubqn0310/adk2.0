import time
import traceback
import logging
import uuid
import asyncio
import os
from typing import Optional, Any

from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.models import Gemini
from google.genai.errors import ServerError

import mmvn_b2c_agent
from mmvn_b2c_agent.telemetry.otel_metrics import get_metrics
from fastapi import APIRouter, HTTPException
from google.adk.errors.already_exists_error import AlreadyExistsError
from google.adk.sessions import BaseSessionService
from google.adk.events.event_actions import EventCompaction, EventActions
from fastapi import FastAPI, Request, Response, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)
DEFAULT_SUMMARIZE_MODEL = "gemini-3-flash-preview"
# Separate API key for summarization to avoid rate limits on main chatbot
GOOGLE_GEMINI_BASE_URL = os.getenv("GOOGLE_GEMINI_BASE_URL")
SUMMARIZE_SESSION_SYSTEM_PROMPT = """
Summarize the following e-commerce conversation. Follow these rules:

LANGUAGE: Detect the dominant language by counting user messages. Write the ENTIRE summary in that language ONLY. If unclear, use Vietnamese. DO NOT explain your language detection - just write the summary in the correct language.

PRONOUN: When writing in Vietnamese, always refer to the customer as "Anh/Chị" (formal you). Never use "bạn", "khách hàng", or other pronouns.

CONTENT: Include only key actions - product searches, cart changes, order status, user info (email/phone/address/name). Skip greetings and small talk. Maximum 5 lines.

FORMATTING: Use markdown **bold** for important information:
- Order numbers (mã đơn hàng): **#101000002403**
- Email addresses: **user@example.com**
- Phone numbers: **0901234567**
- Names: **Nguyễn Văn A**
- Product names: **Gạo ST25**
- SKUs: **441976_24419765**
- Quantities: **2 sản phẩm**, **3 items**
- Prices/totals: **150,000đ**, **$100**
- Addresses: **123 Đường ABC, Quận 1**

CONVERSATION:
{conversation_history}

SUMMARY:
"""

def normalize_event_compaction(events):
    """
    Normalize events to ensure compaction objects are properly typed.
    Converts dict compaction to EventCompaction objects if needed.
    """
    normalized_events = []
    for event in events:
        if event.actions and event.actions.compaction:
            # Check if compaction is a dict instead of EventCompaction object
            if isinstance(event.actions.compaction, dict):
                # Convert dict to EventCompaction object
                compaction_dict = event.actions.compaction
                event.actions.compaction = EventCompaction(
                    start_timestamp=compaction_dict.get('start_timestamp') or compaction_dict.get('startTimestamp'),
                    end_timestamp=compaction_dict.get('end_timestamp') or compaction_dict.get('endTimestamp'),
                    compacted_content=compaction_dict.get('compacted_content') or compaction_dict.get('compactedContent'),
                )
        normalized_events.append(event)
    return normalized_events


class SummarizeAndCreateSessionRequest(BaseModel):
    old_session_id: str
    new_session_id: Optional[str] = None
    state: Optional[dict[str, Any]]


def setup_summarize_session_api(session_service: BaseSessionService):
    router = APIRouter()

    @router.post("/apps/{app_name}/users/{user_id}/summarize_and_create_session")
    async def summarize_session(app_name: str, user_id: str,
                                body: SummarizeAndCreateSessionRequest,
                                request: Request, response: Response):
        """
        Update the session state for a given app, user, and session ID.
        """
        try:
            if not body.new_session_id:
                body.new_session_id = str(uuid.uuid4())
            old_session = await session_service.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=body.old_session_id
            )
            if not old_session:
                response.status_code = status.HTTP_404_NOT_FOUND
                return {"success": False, "error_message": "Session not found"}

            # try to create a new session
            try:
                new_session = await session_service.create_session(
                    app_name=app_name,
                    user_id=user_id,
                    state=body.state,
                    session_id=body.new_session_id,
                )
            except AlreadyExistsError:
                response.status_code = status.HTTP_409_CONFLICT
                return {"success": False, "error_message": f"Session already exists: {body.new_session_id}"}

            # try to summarize old session events
            # Normalize events to handle dict compaction objects from database
            normalized_events = normalize_event_compaction(old_session.events)

            # Use dedicated API key for summarization if available
            summarizer = mmvn_b2c_agent.app.events_compaction_config.summarizer if mmvn_b2c_agent.app.events_compaction_config else None
            if not summarizer:
                # Create dedicated LLM instance with separate API key if configured
                summarize_llm = Gemini(
                    model=DEFAULT_SUMMARIZE_MODEL,
                    base_url=GOOGLE_GEMINI_BASE_URL,
                )

                summarizer = LlmEventSummarizer(
                    llm=summarize_llm,
                    prompt_template=SUMMARIZE_SESSION_SYSTEM_PROMPT
                )
            summarize_event = await summarizer.maybe_summarize_events(
                events=normalized_events
            )

            try:
                usage = getattr(summarize_event, 'usage_metadata', None)
                if usage:
                    get_metrics().record_tokens(
                        input_tokens=getattr(usage, 'prompt_token_count', 0) or 0,
                        output_tokens=getattr(usage, 'candidates_token_count', 0) or 0,
                        model=DEFAULT_SUMMARIZE_MODEL,
                        cached_tokens=getattr(usage, 'cached_content_token_count', 0) or 0,
                        agent_name="summarize_session",
                        session_id=body.old_session_id,
                        user_id=user_id,
                    )
            except Exception as _metrics_err:
                logger.debug(f"Failed to record summarize metrics: {_metrics_err}")

            await session_service.append_event(session=new_session, event=summarize_event)

            # Check if user is in checkout flow (3-step popup workflow)
            in_checkout_flow = body.state.get('in_checkout_flow', False) if body.state else False
            checkout_continuation_message = None

            if in_checkout_flow:
                logger.info(f"Checkout flow detected in new session {body.new_session_id}")
                checkout_stage = body.state.get('checkout_stage', 'unknown')
                checkout_step_number = body.state.get('checkout_step_number', 0)

                # Generate continuation message based on checkout step (3-step popup workflow)
                step_messages = {
                    'main_info': 'Anh/Chị vui lòng tiếp tục nhập thông tin nhận hàng và địa chỉ giao hàng',
                    'additional_info': 'Anh/Chị vui lòng tiếp tục nhập thông tin bổ sung (ghi chú, MCard, hóa đơn)',
                    'payment': 'Anh/Chị vui lòng tiếp tục chọn phương thức thanh toán',
                }

                checkout_continuation_message = step_messages.get(checkout_stage, 'Anh/Chị vui lòng tiếp tục thanh toán')
                logger.info(f"Continuation message for step '{checkout_stage}' (step {checkout_step_number}/3): {checkout_continuation_message}")

            # Refresh session to get latest state
            final_session = await session_service.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=body.new_session_id
            )

            # Note: The frontend should check final_session.state.in_checkout_flow
            # and auto-send the continuation message if True
            if checkout_continuation_message:
                logger.info(f"Frontend should auto-send message: '{checkout_continuation_message}'")

            return final_session
        except Exception as e:
            logger.error(
                "Internal server error during session creation: %s", e, exc_info=True
            )
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"success": False, "error_message": "Internal server error during session creation"}

    return router
