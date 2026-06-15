import asyncio
import datetime
import os
import logging
import json
from typing import Optional

# OTel bug: Token created in one async context, detached in another (GeneratorExit on stream close).
# OTEL_SDK_DISABLED only disables the SDK tracer, not the API context module. Patch detach directly.
try:
    import opentelemetry.context as _otel_ctx
    _orig_detach = _otel_ctx.detach
    def _safe_detach(token):
        try:
            _orig_detach(token)
        except ValueError:
            pass
    _otel_ctx.detach = _safe_detach
except Exception:
    pass
from sqlalchemy import create_engine, text
from google.genai import types as genai_types
from google.genai.errors import ServerError

import dotenv
from google.adk.agents import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App
from google.adk.apps.app import EventsCompactionConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.models import LlmRequest, LlmResponse
from google.adk.plugins import BasePlugin

from mmvn_b2c_agent.agents.cng import cng_agent
from mmvn_b2c_agent.telemetry import get_metrics

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error recording helper — fills error_code / error_message / interrupted
# into the events table so that /metrics/stats/errors has real data.
# ---------------------------------------------------------------------------

_SESSION_URI = os.getenv("SESSION_SERVICE_URI", "sqlite:///./data/sessions.db")
_error_engine = None


def _get_error_engine():
    global _error_engine
    if _error_engine is None:
        _error_engine = create_engine(_SESSION_URI)
    return _error_engine


# ---------------------------------------------------------------------------
# Language detection helper — runs at message receipt, saves to events.language_code
# ---------------------------------------------------------------------------

# Track invocation IDs that have already been language-detected (bounded to 1000)
_lang_detected_invocations: set[str] = set()
_LANG_CACHE_MAX = 1000


def _detect_user_lang(text: str) -> str | None:
    """Detect language from user text. Returns ISO 639-1 code or None."""
    stripped = text.strip()

    # Unicode-based pre-detection for scripts that langdetect confuses
    has_chinese = any('\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf' for c in stripped)
    has_hiragana_katakana = any('\u3040' <= c <= '\u30ff' for c in stripped)
    has_hangul = any('\uac00' <= c <= '\ud7af' or '\u1100' <= c <= '\u11ff' for c in stripped)

    # Vietnamese-exclusive characters — not present in French/Spanish/Portuguese/Italian:
    # • Horn vowels: ơ Ơ ư Ư and their toned forms
    # • Crossed d: đ Đ
    # • Breve a: ă Ă and toned forms (ắằẵẳặ)
    # • Precomposed circumflex+tone: â/ê/ô base letters only exist without tones in French;
    #   toned forms (ầấẩẫậ, ềếểễệ, ồốổỗộ) are uniquely Vietnamese
    # • Dot-below vowels (ạẹịọụự…) and hook-above vowels (ảẻỉỏủ…) on Latin letters
    _VIET_MARKERS = set(
        'ơƠưƯđĐ'                          # horn + crossed-d (original set)
        'ăĂ'                               # breve a — not in French/Spanish
        'ắằẵẳặẮẰẴẲẶ'                      # ă + 5 tones
        'ớờỡởợỚỜỠỞỢ'                      # ơ + 5 tones
        'ứừữửựỨỪỮỬỰ'                      # ư + 5 tones
        'ầấẩẫậẦẤẨẪẬ'                      # â + 5 tones
        'ềếểễệỀẾỂỄỆ'                      # ê + 5 tones
        'ồốổỗộỒỐỔỖỘ'                      # ô + 5 tones
        'ạặậẹệịọộụựỵ'                     # dot-below vowels
        'ảẩẳẻểỉỏổủửỷ'                     # hook-above vowels
    )
    has_vietnamese = any(c in _VIET_MARKERS for c in stripped)

    is_cjk = has_chinese or has_hiragana_katakana or has_hangul
    if not stripped:
        return None
    if is_cjk:
        pass  # CJK scripts are identifiable even from 1 char
    elif has_vietnamese:
        # Vietnamese-exclusive chars found — safe to classify regardless of length
        return 'vi'
    elif stripped.isascii():
        # Pure ASCII short text (e.g. "hi", "ok", "yes") → English
        return 'en'
    elif len(stripped) < 6:
        # Non-ASCII, non-CJK, non-Vietnamese, too short for langdetect → skip
        return None

    # Japanese has hiragana/katakana; Chinese uses CJK without hangul/kana
    # Check script composition first to avoid langdetect zh↔ko confusion
    if has_hiragana_katakana:
        return 'ja'
    if has_hangul and not has_chinese:
        return 'ko'
    if has_chinese and not has_hangul:
        return 'zh-cn'

    try:
        from langdetect import detect_langs, DetectorFactory
        DetectorFactory.seed = 0
        results = detect_langs(stripped)
        if results and results[0].prob >= 0.7:
            return results[0].lang
    except Exception:
        pass
    return None


def _save_language_code(invocation_id: str, lang: str) -> None:
    """UPDATE events.language_code for user events in this invocation."""
    try:
        engine = _get_error_engine()
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE events
                SET language_code = :lang
                WHERE invocation_id = :inv_id
                  AND event_data->>'author' = 'user'
                  AND language_code IS NULL
            """), {"lang": lang, "inv_id": invocation_id})
    except Exception as e:
        logger.warning(f"[LangDetect] Failed to save language_code: {e}")


def record_error(
    app_name: str,
    user_id: str,
    session_id: str,
    invocation_id: str,
    error_code: str,
    error_message: str,
    interrupted: bool = False,
):
    """Insert an error record into the events table."""
    try:
        engine = _get_error_engine()
        timestamp = datetime.datetime.now(datetime.timezone.utc)
        event_id = f"err-{timestamp.timestamp()}-{invocation_id[:8]}"
        event_data = {
            "id": event_id,
            "author": "error_recorder",
            "error_code": error_code,
            "error_message": error_message[:1024],
            "interrupted": bool(interrupted),
            "invocation_id": invocation_id,
            "timestamp": timestamp.timestamp(),
            "actions": {"state_delta": {}, "artifact_delta": {}, "requested_auth_configs": {}, "requested_tool_confirmations": {}},
            "content": None,
        }
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO events (
                    id, app_name, user_id, session_id, invocation_id,
                    timestamp, event_data
                ) VALUES (
                    :id, :app_name, :user_id, :session_id, :invocation_id,
                    :timestamp, cast(:event_data as jsonb)
                )
                ON CONFLICT (id, app_name, user_id, session_id) DO NOTHING
            """), {
                "id": event_id,
                "app_name": app_name,
                "user_id": user_id,
                "session_id": session_id,
                "invocation_id": invocation_id,
                "timestamp": timestamp,
                "event_data": json.dumps(event_data),
            })
        logger.info(f"[ErrorRecorder] Recorded: code={error_code}, msg={error_message[:80]}")
    except Exception as e:
        logger.error(f"[ErrorRecorder] Failed to record error: {e}")

dotenv.load_dotenv(override=True)
disable_cache = os.environ.get('DISABLE_PROMPT_CACHE', '0') == '1'
root_agent = cng_agent
# This is required for ADK web UI to find the agent
agent = root_agent


# a2a_app = to_a2a(root_agent, port=8001)


class CountInvocationPlugin(BasePlugin):
    """A custom plugin that counts agent and tool invocations and tracks token usage."""

    def __init__(self) -> None:
        """Initialize the plugin with counters and metrics."""
        super().__init__(name='count_invocation')
        self.agent_count: int = 0
        self.tool_count: int = 0
        self.llm_request_count: int = 0

        # Initialize OpenTelemetry metrics
        self.metrics = get_metrics()

    _AGENT_STATUS_MAP: dict[str, str] = {
        'question_answer_agent': 'Đang tìm kiếm thông tin...',
        'cng_product_search_tool': 'Đang phân tích yêu cầu tìm kiếm...',
        'product_search_tool_caller': 'Đang xác định từ khóa tìm kiếm...',
        'product_search_response_generator': 'Đang tổng hợp kết quả...',
        'checkout_agent': 'Đang xử lý đặt hàng...',
    }

    async def before_agent_callback(
            self, *, agent: BaseAgent, callback_context: CallbackContext
    ) -> None:
        """Count agent runs."""
        self.agent_count += 1
        print(f'[Plugin] Agent run count: {self.agent_count}')
        callback_context.state['current_time'] = datetime.datetime.now().isoformat()
        status = self._AGENT_STATUS_MAP.get(getattr(agent, 'name', ''))
        if status:
            callback_context.state['ai_thinking_status'] = status

    async def before_model_callback(
            self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> None:
        """Count LLM requests and store model name."""
        self.llm_request_count += 1
        print(f'[Plugin] LLM request count: {self.llm_request_count}')

        # Store model name in context for later use
        if hasattr(llm_request, 'model') and llm_request.model:
            callback_context.state['model_name'] = llm_request.model
        elif hasattr(llm_request, 'model_name') and llm_request.model_name:
            callback_context.state['model_name'] = llm_request.model_name

        # Detect language from latest user message — runs once per invocation
        inv_id = callback_context.invocation_id
        if inv_id and inv_id not in _lang_detected_invocations:
            if len(_lang_detected_invocations) >= _LANG_CACHE_MAX:
                # Evict oldest entry to keep memory bounded
                _lang_detected_invocations.discard(next(iter(_lang_detected_invocations)))
            _lang_detected_invocations.add(inv_id)

            # Extract text from the latest non-function-response user message
            user_text = None
            for content in reversed(llm_request.contents or []):
                if getattr(content, 'role', None) != 'user':
                    continue
                parts = getattr(content, 'parts', []) or []
                if any(getattr(p, 'function_response', None) for p in parts):
                    continue
                texts = [p.text for p in parts if getattr(p, 'text', None)]
                if texts:
                    user_text = ' '.join(texts)
                    break

            if user_text:
                lang = await asyncio.to_thread(_detect_user_lang, user_text)
                if lang:
                    await asyncio.to_thread(_save_language_code, inv_id, lang)

    async def after_model_callback(
            self,
            *,
            callback_context: CallbackContext,
            llm_response: LlmResponse
    ) -> None:
        """Track token usage from LLM response and record errors."""
        try:
            # Lấy usage_metadata từ response (không phải .metadata)
            usage_metadata = llm_response.usage_metadata

            if usage_metadata:
                # Google ADK uses different field names
                input_tokens = getattr(usage_metadata, 'prompt_token_count', 0) or 0
                output_tokens = getattr(usage_metadata, 'candidates_token_count', 0) or 0
                cached_tokens = getattr(usage_metadata, 'cached_content_token_count', 0) or 0

                # Lấy model name từ context (đã lưu trong before_model_callback)
                model = callback_context.state.get('model_name', 'unknown')

                # Lấy agent name từ context
                agent_name = callback_context.agent_name or 'unknown'

                # Record metrics
                if input_tokens or output_tokens:
                    session = callback_context.session
                    session_id = getattr(session, 'id', None) if session else None
                    user_id = getattr(session, 'user_id', None) if session else None
                    # Fallback: try direct callback_context properties
                    if not session_id:
                        session_id = getattr(callback_context, 'invocation_id', None)
                    if not user_id:
                        user_id = getattr(callback_context, 'user_id', None)
                    print(f'[Metrics DEBUG] session={session}, session_id={session_id}, user_id={user_id}, ctx_user_id={getattr(callback_context, "user_id", None)}')
                    self.metrics.record_tokens(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        model=model,
                        cached_tokens=cached_tokens,
                        agent_name=agent_name,
                        session_id=session_id,
                        user_id=user_id,
                    )

                    print(f'[Metrics] Tokens - Input: {input_tokens}, Output: {output_tokens}, '
                          f'Cached: {cached_tokens}, Model: {model}, Agent: {agent_name}')

                # Record error from LLM response
                error_code = getattr(llm_response, 'error_code', None)
                finish_reason = getattr(llm_response, 'finish_reason', None)

                _NORMAL_FINISH = {'stop', 'STOP'}
                _finish_str = finish_reason.name if hasattr(finish_reason, 'name') else str(finish_reason) if finish_reason else None
                if error_code or (_finish_str and _finish_str not in _NORMAL_FINISH):
                    # Classify the error
                    if finish_reason == 'malformed_function_call':
                        ec = 'malformed_function_call'
                    elif finish_reason == 'max_tokens':
                        ec = 'timeout'
                    elif finish_reason == 'safety':
                        ec = 'safety_block'
                    else:
                        ec = 'system_error'

                    session = callback_context.session
                    app_name = getattr(session, 'app_name', 'unknown') if session else 'unknown'
                    user_id = getattr(session, 'user_id', 'unknown') if session else 'unknown'
                    session_id = getattr(session, 'id', 'unknown') if session else 'unknown'

                    record_error(
                        app_name=app_name,
                        user_id=user_id,
                        session_id=session_id,
                        invocation_id=callback_context.invocation_id or 'unknown',
                        error_code=ec,
                        error_message=f"LLM error: code={error_code}, finish_reason={finish_reason}",
                    )

        except Exception as e:
            # Record unexpected exceptions in callback itself
            logger.error(f'[Metrics] Error tracking tokens: {e}')
            try:
                session = callback_context.session
                app_name = getattr(session, 'app_name', 'unknown') if session else 'unknown'
                user_id = getattr(session, 'user_id', 'unknown') if session else 'unknown'
                session_id = getattr(session, 'id', 'unknown') if session else 'unknown'

                record_error(
                    app_name=app_name,
                    user_id=user_id,
                    session_id=session_id,
                    invocation_id=callback_context.invocation_id or 'unknown',
                    error_code='system_error',
                    error_message=f"Metrics callback exception: {str(e)[:200]}",
                )
            except Exception:
                pass

# SUMMARIZE_PROMPT_WITH_USER_LANGUAGE = (
#     'The following is a conversation history between a user and an AI'
#     ' agent. Please summarize the conversation, focusing on key'
#     ' information and decisions made, as well as any unresolved'
#     ' questions or tasks. The summary should be concise and capture the'
#     ' essence of the interaction, **written in the language of the last user\'s question**.\\n\\n{conversation_history}'
# )

app = App(
    name='mmvn_b2c_agent',
    root_agent=root_agent,
    plugins=[
        CountInvocationPlugin(),
        # ContextFilterPlugin(num_invocations_to_keep=3),
        # SaveFilesAsArtifactsPlugin(),
    ],
    # events_compaction_config=EventsCompactionConfig(
    #     # bỏ comment nếu summarize làm sai language.
    #     # summarizer=LlmEventSummarizer(llm=root_agent.canonical_model, prompt_template=SUMMARIZE_PROMPT_WITH_USER_LANGUAGE),
    #     compaction_interval=10,
    #     overlap_size=4,
    # ),
    # Enable automatic context caching with TTL and intervals
    context_cache_config=ContextCacheConfig(
        min_tokens=4096,  # Minimum tokens required before caching
        ttl_seconds=3600,  # 1 hour cache lifetime
        cache_intervals=5,  # Maximum invocations before cache invalidation
    ) if not disable_cache else None,
)
