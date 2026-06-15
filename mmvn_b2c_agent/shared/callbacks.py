"""
Callbacks for the B2C agent.
"""
import logging
from google.genai import types as genai_types
from google.genai.errors import ServerError
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.adk.models.llm_request import LlmRequest

logger = logging.getLogger(__name__)


def debug_log_llm_request(
        callback_context: CallbackContext, llm_request: LlmRequest
) -> None:
    """
    Log LLM request details để debug - đặc biệt hữu ích cho MALFORMED_FUNCTION_CALL.
    """
    import json

    logger.info("=" * 80)
    logger.info("LLM REQUEST DEBUG (before_model_callback)")
    logger.info("=" * 80)
    logger.info(f"Session: {callback_context.session.id if callback_context.session else 'N/A'}")

    # Log tools/functions được declare
    if hasattr(llm_request.config, 'tools') and llm_request.config.tools:
        logger.info(f"Number of tools declared: {len(llm_request.config.tools)}")
        for idx, tool in enumerate(llm_request.config.tools):
            if hasattr(tool, 'function_declarations'):
                logger.info(f"Tool {idx} has {len(tool.function_declarations)} function declarations")
                for func in tool.function_declarations:
                    logger.info(f"  - Function: {func.name}")
                    # Log parameters schema để check xem có đúng format không
                    if hasattr(func, 'parameters') and func.parameters:
                        try:
                            logger.info(f"    Parameters: {func.parameters}")
                        except Exception as e:
                            logger.warning(f"    Cannot serialize parameters: {e}")

    # Log tool_config
    if hasattr(llm_request.config, 'tool_config') and llm_request.config.tool_config:
        logger.info(f"Tool config: {llm_request.config.tool_config}")

    # Log conversation history length và content
    if llm_request.contents:
        logger.info(f"Conversation has {len(llm_request.contents)} messages")
        # Log last few messages để debug
        for i, content in enumerate(llm_request.contents[-3:]):  # Last 3 messages
            role = content.role if hasattr(content, 'role') else 'unknown'
            parts_summary = []
            if hasattr(content, 'parts') and content.parts:
                for part in content.parts:
                    if hasattr(part, 'text') and part.text:
                        text_preview = part.text[:200] + "..." if len(part.text) > 200 else part.text
                        parts_summary.append(f"text: {text_preview}")
                    if hasattr(part, 'function_call') and part.function_call:
                        parts_summary.append(f"function_call: {part.function_call.name}")
                    if hasattr(part, 'function_response') and part.function_response:
                        parts_summary.append(f"function_response: {part.function_response.name}")
                    if hasattr(part, 'inline_data') and part.inline_data:
                        mime = getattr(part.inline_data, 'mime_type', 'unknown')
                        parts_summary.append(f"inline_data: {mime}")
            logger.info(f"  Message {len(llm_request.contents) - 3 + i}: role={role}, parts={parts_summary}")

    # Estimate total content size
    try:
        total_chars = 0
        for content in llm_request.contents:
            if hasattr(content, 'parts') and content.parts:
                for part in content.parts:
                    if hasattr(part, 'text') and part.text:
                        total_chars += len(part.text)
        logger.info(f"Estimated total text chars: {total_chars} (~{total_chars // 4} tokens)")
    except Exception as e:
        logger.warning(f"Cannot estimate content size: {e}")

    logger.info("=" * 80)


def handle_malformed_response(
        callback_context: CallbackContext, llm_response: LlmResponse
) -> None:
    """
    Detect and handle malformed responses from the LLM.
    """
    callback_context.state['input_token_count'] = llm_response.usage_metadata.prompt_token_count
    if llm_response.error_code == genai_types.FinishReason.MALFORMED_FUNCTION_CALL.value:
        # Log chi tiết để debug
        logger.error("=" * 80)
        logger.error("MALFORMED_FUNCTION_CALL DETECTED")
        logger.error("=" * 80)

        # Log raw response để xem structure
        logger.error(f"Full LLM Response: {llm_response}")
        logger.error(f"Response content: {llm_response.content}")
        logger.error(f"Response parts: {llm_response.content.parts if llm_response.content else 'N/A'}")
        logger.error(f"Error code: {llm_response.error_code}")
        logger.error(f"Finish reason: {llm_response.finish_reason}")

        # Log request context để biết đã gửi gì
        logger.error(f"Session ID: {callback_context.session.id if callback_context.session else 'N/A'}")
        logger.error(f"State: {callback_context.state}")

        logger.error("=" * 80)

        raise ServerError(code=500, response="Malformed function call detected in LLM response.",
                          response_json={
                              "message": "Malformed function call detected in LLM response.",
                              "code": 500,
                              "status": "INTERNAL"
                          })
    if not llm_response.content:
        logger.error("Empty LLM response.")
        raise ServerError(code=500, response="Empty content in LLM response.",
                          response_json={
                              "message": "Empty content in LLM response.",
                              "code": 500,
                              "status": "INTERNAL"
                          })
    pass
# def dynamic_instruction(callback_context: CallbackContext, llm_request: LlmRequest):
#     """
#     Dynamically update the system instruction based on the current state.
#     This allows for more flexible and context-aware instructions.
#     """
#     for part in llm_request.contents[-1].parts:
#         if getattr(part.inline_data, 'mime_type', "") == 'audio/mp4':
#             # This is not the correct way to set system prompt, as it will also overwrite other ADK specific parts.
#             llm_request.config.system_instruction = ROOT_AGENT_INSTRUCTION_WITH_AUDIO
#         else:
#             llm_request.config.system_instruction = ROOT_AGENT_INSTRUCTION


def extract_email_from_user_function_response(callback_context: CallbackContext, llm_request: LlmRequest):
    """
    Extract email from functionResponse sent by frontend (user role).
    This handles cases like:
    - show_payment_methods response with order completion data
    - show_checkout_step response with checkout form data

    IMPORTANT: This runs in before_model_callback to capture email BEFORE model processes.
    Frontend sends functionResponse as user message, so we need to extract email here.
    """
    try:
        if not llm_request.contents:
            return

        # Check last user message for functionResponse
        last_content = llm_request.contents[-1]
        if not last_content.parts or last_content.role != 'user':
            return

        for part in last_content.parts:
            # Check if this is a functionResponse
            if not hasattr(part, 'function_response') or not part.function_response:
                continue

            func_response = part.function_response
            func_name = func_response.name if hasattr(func_response, 'name') else None
            response_data = func_response.response if hasattr(func_response, 'response') else {}

            if not isinstance(response_data, dict):
                continue

            # Handle show_payment_methods (order completion from FE)
            if func_name == 'show_payment_methods':
                order_number = response_data.get('order_number')
                email = response_data.get('email')
                status = response_data.get('status')

                logger.info(f"[BEFORE_MODEL] Detected show_payment_methods functionResponse: "
                           f"order={order_number}, email={email}, status={status}")

                if email:
                    # Save at ROOT level (won't be overwritten by frontend state updates)
                    callback_context.state['guest_user_email'] = email
                    logger.info(f"[BEFORE_MODEL] Saved guest email to ROOT state: {email}")

                    # Also save in nested state as backup
                    current_state = callback_context.state.get('state', {})
                    if isinstance(current_state, dict):
                        current_state['guest_user_email'] = email
                        callback_context.state['state'] = current_state

                if order_number:
                    # Save at ROOT level
                    callback_context.state['last_order_number'] = order_number
                    logger.info(f"[BEFORE_MODEL] Saved order_number to ROOT state: {order_number}")

                    # Save order-to-email mapping for multi-order tracking
                    if email:
                        order_email_map = callback_context.state.get('order_email_map', {})
                        order_email_map[order_number] = email
                        callback_context.state['order_email_map'] = order_email_map
                        logger.info(f"[BEFORE_MODEL] Saved order-email mapping: {order_number} -> {email}")

            # Handle show_checkout_step (checkout form completion from FE)
            elif func_name == 'show_checkout_step':
                status = response_data.get('status')
                fields = response_data.get('fields', {})

                if status == 'done' and isinstance(fields, dict):
                    email = fields.get('email')
                    phone = fields.get('phone')

                    logger.info(f"[BEFORE_MODEL] Detected show_checkout_step functionResponse: "
                               f"status={status}, email={email}, phone={phone}")

                    if email:
                        callback_context.state['guest_user_email'] = email
                        logger.info(f"[BEFORE_MODEL] Saved guest email from checkout: {email}")

                    if phone:
                        callback_context.state['guest_user_phone'] = phone
                        logger.info(f"[BEFORE_MODEL] Saved guest phone from checkout: {phone}")

                    # Save all checkout info to state for later retrieval
                    # This allows bot to answer questions like "mã số thuế của tôi là gì?"
                    checkout_info = {}
                    checkout_fields_to_save = [
                        'recipient_name', 'email', 'phone', 'street',
                        'city_name', 'ward_name', 'district_name',
                        'delivery_date', 'delivery_time_label',
                        'note', 'mcard_number', 'call_before_delivery',
                        'issue_vat_invoice',
                        # VAT invoice fields
                        'company_name', 'company_vat_number', 'company_address'
                    ]
                    for field in checkout_fields_to_save:
                        value = fields.get(field)
                        if value is not None and value != '':
                            checkout_info[field] = value

                    if checkout_info:
                        # Save at ROOT level
                        callback_context.state['guest_checkout_info'] = checkout_info
                        logger.info(f"[BEFORE_MODEL] Saved checkout info to ROOT state: {list(checkout_info.keys())}")

                        # Also save in nested state
                        current_state = callback_context.state.get('state', {})
                        if isinstance(current_state, dict):
                            current_state['guest_user_email'] = email
                            current_state['guest_checkout_info'] = checkout_info
                            callback_context.state['state'] = current_state

    except Exception as e:
        logger.warning(f"[BEFORE_MODEL] Failed to extract email from functionResponse: {e}")


def inject_current_time_to_context(callback_context: CallbackContext, llm_request: LlmRequest):
    """
    Inject current_time from state into the LLM request context.
    This allows the agent to know the current date/time for date-related queries.
    """
    try:
        # Get current_time from state - try root level first
        current_time = callback_context.state.get('current_time')

        if not current_time:
            # Fallback 1: try nested in magento_session_data
            current_time = callback_context.state.get('magento_session_data', {}).get('current_time')

        if not current_time:
            # Fallback 2: try deeply nested path (legacy)
            current_time = callback_context.state.get('state', {}).get('current_time')

        if current_time:
            # Add current_time as a user message part (context)
            # This makes it visible to the agent without modifying system instruction
            context_text = f"\n\n[SYSTEM CONTEXT - Current Time]: {current_time}\nNote: When user mentions 'hôm nay'/'today', use the date from this current_time field."

            # Append to the last user message
            if llm_request.contents and llm_request.contents[-1].parts:
                # Add as additional context to last message
                last_content = llm_request.contents[-1]
                if last_content.parts and hasattr(last_content.parts[0], 'text'):
                    original_text = last_content.parts[0].text
                    last_content.parts[0].text = original_text + context_text
                    logger.info(f"Injected current_time into context: {current_time}")
    except Exception as e:
        logger.warning(f"Failed to inject current_time into context: {e}")
        # Don't fail the request if this fails
        pass


# MIME types that indicate file uploads (lowercase for case-insensitive matching)
FILE_UPLOAD_MIME_TYPES = {
    'application/pdf',
    'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/bmp',
    'image/jpg',  # Some systems use jpg instead of jpeg
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'text/plain', 'text/csv',
    'application/octet-stream',  # Generic binary, often used for unknown files
}

# MIME types that Gemini does NOT support as inline_data / file_data —
# sending these will cause a 400 "Unsupported MIME type" error.
# Gemini supports: image/*, application/pdf, audio/*, video/*, text/*
GEMINI_UNSUPPORTED_INLINE_MIME_TYPES = {
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/octet-stream',
}

# MIME types that indicate audio uploads (voice messages sent without STT)
AUDIO_MIME_TYPES = {
    'audio/webm',
    'audio/webm;codecs=opus',
    'audio/mp3',
    'audio/mpeg',
    'audio/wav',
    'audio/ogg',
    'audio/mp4',
    'audio/aac',
    'audio/flac',
    'audio/x-wav',
    'audio/x-m4a',
}

# Keywords that indicate comparison intent
COMPARISON_KEYWORDS = ["so sánh", "compare", "so sanh", "đối chiếu", "check giá", "kiểm tra giá"]

# Keywords that indicate file analysis intent (describe file content)
ANALYSIS_KEYWORDS = ["phân tích", "analyze", "phân tích file", "xem file", "file này có gì", "nội dung file"]

# Keywords that should NOT trigger checkout when file is uploaded
CHECKOUT_KEYWORDS_TO_BLOCK = ["đặt hàng", "mua", "thanh toán", "checkout", "order", "buy", "đặt lại", "reorder"]

# Keywords that should NOT trigger order tracking when file is uploaded
ORDER_TRACKING_KEYWORDS_TO_BLOCK = ["kiểm tra đơn hàng", "đơn hàng", "order", "tracking", "kiểm tra đơn", "check order"]

# Keywords that indicate product search intent (NOT comparison)
PRODUCT_SEARCH_KEYWORDS = ["tìm", "tìm kiếm", "search", "find", "kiểm tra"]


def _is_file_mime_type(mime_type: str) -> bool:
    """Check if mime_type is a file upload type (case-insensitive)."""
    if not mime_type:
        return False
    return mime_type.lower() in FILE_UPLOAD_MIME_TYPES


def _is_audio_mime_type(mime_type: str) -> bool:
    """Check if mime_type is an audio type (case-insensitive)."""
    if not mime_type:
        return False
    mime_lower = mime_type.lower()
    # Check exact match first
    if mime_lower in AUDIO_MIME_TYPES:
        return True
    # Check if it starts with audio/ (covers audio/webm;codecs=opus etc.)
    if mime_lower.startswith('audio/'):
        return True
    return False


def handle_raw_audio_input(callback_context: CallbackContext, llm_request: LlmRequest) -> LlmResponse | None:
    """
    Detect raw audio input (voice sent without STT conversion) and return
    a polite response asking user to speak again.

    This prevents sending raw audio to Gemini which may cause:
    - 503 Service Unavailable (model overloaded)
    - MALFORMED_FUNCTION_CALL errors
    - Incorrect responses

    IMPORTANT: This callback removes audio data from llm_request.contents
    by clearing inline_data to prevent large audio blobs from being sent to Gemini.

    Returns:
        LlmResponse if audio detected in CURRENT message (short-circuits the model call)
        None if no audio in current message (continues to model, but historical audio is cleaned)
    """
    try:
        if not llm_request.contents:
            return None

        # Track if current message (last content) has audio
        current_message_has_audio = False
        current_audio_mime_type = None

        # Check ALL messages in conversation history for audio
        # Clean up any audio to prevent 503 errors on subsequent requests
        for content_idx, content in enumerate(llm_request.contents):
            if not content.parts:
                continue

            is_last_content = (content_idx == len(llm_request.contents) - 1)

            for part_idx, part in enumerate(content.parts):
                audio_detected = False
                mime_type = None

                # Check for inline_data with audio mime type
                if hasattr(part, 'inline_data') and part.inline_data:
                    mime_type = getattr(part.inline_data, 'mime_type', None)
                    if _is_audio_mime_type(mime_type):
                        audio_detected = True
                        logger.info(f"[AUDIO_INPUT] Found audio inline_data in content[{content_idx}], part[{part_idx}]: {mime_type}")

                # Check for file_data with audio mime type
                if hasattr(part, 'file_data') and part.file_data:
                    mime_type = getattr(part.file_data, 'mime_type', None)
                    if _is_audio_mime_type(mime_type):
                        audio_detected = True
                        logger.info(f"[AUDIO_INPUT] Found audio file_data in content[{content_idx}], part[{part_idx}]: {mime_type}")

                if audio_detected:
                    # Track if this is the current user message
                    if is_last_content:
                        current_message_has_audio = True
                        current_audio_mime_type = mime_type

                    # Replace the audio part with a text placeholder
                    # Protobuf objects are often immutable, so we replace the entire part
                    try:
                        content.parts[part_idx] = genai_types.Part(text="[Voice message]")
                        logger.info(f"[AUDIO_INPUT] Replaced audio part at content[{content_idx}], part[{part_idx}] with placeholder")
                    except Exception as e:
                        logger.error(f"[AUDIO_INPUT] Could not replace part at content[{content_idx}], part[{part_idx}]: {e}")

        # If current message doesn't have audio, allow request to proceed
        # (historical audio has been cleaned up)
        if not current_message_has_audio:
            return None

        # Current message has audio - return response asking user to speak again
        logger.info(f"[AUDIO_INPUT] Returning 'please speak again' response for audio: {current_audio_mime_type}")

        # Return plain text response (like safety callback does)
        response_message = "Xin lỗi, em không nhận diện được yêu cầu. Anh/Chị vui lòng nói lại yêu cầu khác để em hỗ trợ tốt hơn nhé."

        return LlmResponse(
            content=genai_types.Content(
                role="model",
                parts=[genai_types.Part(text=response_message)]
            ),
            usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
                prompt_token_count=0,
                candidates_token_count=0,
                total_token_count=0
            )
        )

    except Exception as e:
        logger.error(f"[AUDIO_INPUT] Error detecting audio input: {e}")
        return None


# MIME types for image data that should be stripped from older conversation turns
IMAGE_MIME_TYPES = {
    'image/jpeg', 'image/jpg', 'image/png', 'image/gif',
    'image/webp', 'image/bmp', 'image/svg+xml',
}


def _is_image_mime_type(mime_type: str) -> bool:
    """Check if mime_type is an image type."""
    if not mime_type:
        return False
    return mime_type.lower() in IMAGE_MIME_TYPES


def strip_old_image_data(callback_context: CallbackContext, llm_request: LlmRequest) -> LlmResponse | None:
    """
    Strip base64 image data from OLDER conversation turns to reduce context size.

    Large images (e.g. 4MB base64) accumulate in conversation history and get
    re-sent to LLM on every call, causing severe latency (40s+ per call).

    This callback:
    - Keeps the LAST user message intact (current turn may need image for processing)
    - Replaces image inline_data/file_data in all older messages with a text placeholder
    """
    try:
        if not llm_request.contents:
            return None

        # Strategy: find the LAST content that has an image.
        # - If it's also the last content overall OR appears after (or is) the last real user message
        #   → it's from the current turn → preserve it
        # - Otherwise → it's old → strip everything
        #
        # In sub-agents, transfer events (role=user, text="For context: ...") appear AFTER the
        # real user message, so we can't just check "last user message". Instead, we check if
        # any content in the CURRENT invocation has an image by looking at the last few contents.

        # Find the last content index that has an image (searching backwards)
        last_image_idx = -1
        for idx in range(len(llm_request.contents) - 1, -1, -1):
            content = llm_request.contents[idx]
            if not content.parts:
                continue
            for part in content.parts:
                has_img = False
                if hasattr(part, 'inline_data') and part.inline_data:
                    if _is_image_mime_type(getattr(part.inline_data, 'mime_type', None)):
                        has_img = True
                if not has_img and hasattr(part, 'file_data') and part.file_data:
                    if _is_image_mime_type(getattr(part.file_data, 'mime_type', None)):
                        has_img = True
                if has_img:
                    last_image_idx = idx
                    break
            if last_image_idx >= 0:
                break

        # Determine if this image is from the current turn or an old turn.
        # Heuristic: if there are model responses AFTER the image content, it's from a previous turn.
        preserve_image_idx = -1
        if last_image_idx >= 0:
            has_model_response_after = False
            for idx in range(last_image_idx + 1, len(llm_request.contents)):
                if getattr(llm_request.contents[idx], 'role', None) == 'model':
                    has_model_response_after = True
                    break
            if not has_model_response_after:
                # No model response after image → image is from current turn → preserve
                preserve_image_idx = last_image_idx

        total_stripped = 0
        total_bytes_saved = 0

        for content_idx, content in enumerate(llm_request.contents):
            if not content.parts:
                continue

            # Skip the last content - always keep intact
            is_last_content = (content_idx == len(llm_request.contents) - 1)
            # Only preserve image if current turn has image
            is_preserved_image = (content_idx == preserve_image_idx)
            if is_last_content or is_preserved_image:
                continue

            for part_idx, part in enumerate(content.parts):
                mime_type = None
                data_size = 0

                # Check inline_data
                if hasattr(part, 'inline_data') and part.inline_data:
                    mime_type = getattr(part.inline_data, 'mime_type', None)
                    if _is_image_mime_type(mime_type):
                        data_bytes = getattr(part.inline_data, 'data', None)
                        if data_bytes:
                            data_size = len(data_bytes) if isinstance(data_bytes, (str, bytes)) else 0

                # Check file_data
                if not mime_type and hasattr(part, 'file_data') and part.file_data:
                    mime_type = getattr(part.file_data, 'mime_type', None)
                    if _is_image_mime_type(mime_type):
                        data_size = 1  # file_data is a reference, not large but still strip

                if mime_type and _is_image_mime_type(mime_type):
                    try:
                        content.parts[part_idx] = genai_types.Part(
                            text=f"[Image: {mime_type}]"
                        )
                        total_stripped += 1
                        total_bytes_saved += data_size
                        logger.info(
                            f"[STRIP_IMAGE] Replaced image at content[{content_idx}], "
                            f"part[{part_idx}] ({mime_type}, ~{data_size // 1024}KB) with placeholder"
                        )
                    except Exception as e:
                        logger.error(
                            f"[STRIP_IMAGE] Could not replace part at content[{content_idx}], "
                            f"part[{part_idx}]: {e}"
                        )

        if total_stripped > 0:
            logger.info(
                f"[STRIP_IMAGE] Stripped {total_stripped} image(s), "
                f"saved ~{total_bytes_saved // 1024}KB from context. "
                f"last_image_idx={last_image_idx}, preserve_idx={preserve_image_idx}"
            )

    except Exception as e:
        logger.error(f"[STRIP_IMAGE] Error stripping image data: {e}")

    return None


# When the user uploads a file/image and types NO text, the only readable words are
# whatever is printed on the file/image (often English product packaging like "Ensure",
# "Vanilla", "850g"). The LLM otherwise treats that as the user's language and replies
# in English, even though the user is a Vietnamese MMVN customer. Force Vietnamese
# deterministically — do not rely on the prompt/schema, which loses to the model's prior.
_NO_TEXT_LANGUAGE_DIRECTIVE = """

[SYSTEM CONTEXT - RESPONSE LANGUAGE (HIGHEST PRIORITY)]:
The user typed NO text this turn — they only attached a file/image. Therefore:
- Respond in VIETNAMESE and set the `language` / `user_language` field to "vi".
- Text printed on the image or product packaging (even if it is English, e.g. "Ensure", "Vanilla", "850g") is NOT the user's language and MUST be ignored for language selection.
- NEVER reply in English (or any non-Vietnamese language) just because the product/image text is in that language."""


def inject_file_upload_context(callback_context: CallbackContext, llm_request: LlmRequest):
    """
    Detect file upload in user message and inject context to force product search behavior.
    This ensures agent ALWAYS calls cng_product_search_tool for file uploads.

    CRITICAL: This callback MUST successfully inject context for file uploads.
    If injection fails, the agent might call wrong tools (checkout, order tracking).
    """
    try:
        if not llm_request.contents:
            logger.debug("inject_file_upload_context: No contents in request")
            return

        # STEP 1 (UNCONDITIONAL): Process unsupported MIME types from ALL contents.
        # For docx files: extract text and replace binary with text part so the LLM
        # can read actual file content. For other unsupported types: strip.
        # Must run before any detection/early-return logic because sub-agents
        # rebuild llm_request.contents from session history — the docx bytes live
        # in an earlier message, not in contents[-1].
        _DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        _XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        total_stripped = 0
        stripped_from_last = False
        stripped_last_types = []
        converted_last_types = []  # MIME types converted to text (not stripped)
        extracted_file_texts = []  # actual extracted text, persisted to state for follow-up turns
        last_idx = len(llm_request.contents) - 1

        for idx, content in enumerate(llm_request.contents):
            if not content.parts:
                continue
            filtered_parts = []
            parts_changed = False
            for part in content.parts:
                part_mime = None
                if hasattr(part, 'inline_data') and part.inline_data:
                    part_mime = getattr(part.inline_data, 'mime_type', None)
                elif hasattr(part, 'file_data') and part.file_data:
                    part_mime = getattr(part.file_data, 'mime_type', None)
                if part_mime and part_mime.lower() in GEMINI_UNSUPPORTED_INLINE_MIME_TYPES:
                    parts_changed = True
                    extracted_text = None
                    raw = (getattr(part.inline_data, 'data', None)
                           if hasattr(part, 'inline_data') and part.inline_data else None)
                    if part_mime.lower() == _DOCX_MIME and raw:
                        try:
                            import docx2txt
                            import io as _io
                            text = docx2txt.process(_io.BytesIO(raw))
                            extracted_text = text.strip() if text else None
                        except Exception as _ex:
                            logger.warning(f"[FILE_UPLOAD] docx2txt extraction failed: {_ex}")
                    elif part_mime.lower() == _XLSX_MIME and raw:
                        try:
                            import zipfile
                            import io as _io
                            import xml.etree.ElementTree as _ET
                            _NS = {'ss': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                            zf = zipfile.ZipFile(_io.BytesIO(raw))
                            shared = []
                            if 'xl/sharedStrings.xml' in zf.namelist():
                                tree = _ET.parse(zf.open('xl/sharedStrings.xml'))
                                for si in tree.findall('.//ss:si', _NS):
                                    shared.append(''.join(t.text or '' for t in si.findall('.//ss:t', _NS)))
                            rows = []
                            for name in sorted(zf.namelist()):
                                if name.startswith('xl/worksheets/sheet') and name.endswith('.xml'):
                                    tree = _ET.parse(zf.open(name))
                                    for row in tree.findall('.//ss:row', _NS):
                                        cells = []
                                        for c in row.findall('ss:c', _NS):
                                            t = c.get('t')
                                            # inlineStr cells store their value in <is><t>…</t></is>
                                            # and have NO <v> element. Handle them BEFORE the
                                            # `v is None` guard below, otherwise every text cell
                                            # written by openpyxl/pandas/many exporters is skipped
                                            # and the whole sheet extracts as empty.
                                            if t == 'inlineStr':
                                                is_el = c.find('.//ss:t', _NS)
                                                val = is_el.text if is_el is not None else None
                                                if val:
                                                    cells.append(val)
                                                continue
                                            v = c.find('ss:v', _NS)
                                            if v is None or v.text is None:
                                                continue
                                            if t == 's':
                                                val = shared[int(v.text)] if int(v.text) < len(shared) else ''
                                            else:
                                                val = v.text
                                            if val:
                                                cells.append(val)
                                        if cells:
                                            rows.append('\t'.join(cells))
                            extracted_text = '\n'.join(rows) if rows else None
                        except Exception as _ex:
                            logger.warning(f"[FILE_UPLOAD] xlsx extraction failed: {_ex}")
                    if extracted_text:
                        filtered_parts.append(genai_types.Part(
                            text=f"[Nội dung file tải lên]\n{extracted_text}"
                        ))
                        extracted_file_texts.append(extracted_text)
                        logger.info(f"[FILE_UPLOAD] Converted {part_mime} to text ({len(extracted_text)} chars)")
                        if idx == last_idx:
                            converted_last_types.append(part_mime)
                    else:
                        logger.info(f"[FILE_UPLOAD] Stripping unsupported MIME type: {part_mime}")
                        total_stripped += 1
                        if idx == last_idx:
                            stripped_from_last = True
                            stripped_last_types.append(part_mime)
                else:
                    filtered_parts.append(part)
            if parts_changed:
                content.parts = filtered_parts
        if total_stripped:
            logger.info(f"[FILE_UPLOAD] Stripped {total_stripped} unsupported part(s) across all contents")

        # Persist extracted file content to state. The frontend often does NOT resend
        # the file bytes on follow-up turns, so the [Nội dung file tải lên] block would
        # otherwise vanish — leaving the agent with nothing to read and making it grab
        # generic words like "sản phẩm" as a search keyword. Saving it lets us re-inject
        # the real content on later turns. (8000 char cap to bound token cost.)
        if extracted_file_texts and callback_context.state is not None:
            combined = "\n\n".join(extracted_file_texts)
            callback_context.state['_last_file_content'] = combined[:8000]
            logger.info(f"[FILE_UPLOAD] Saved file content to state ({len(combined)} chars)")

        # Keep the file's actual content visible to the LLM on EVERY turn — not only the
        # turn it was uploaded. Frontends usually drop the file bytes on follow-up turns,
        # so the [Nội dung file tải lên] block vanishes and the LLM loses context; it then
        # grabs generic words like "sản phẩm" as a search keyword. Re-injecting the saved
        # content whenever it is missing lets the LLM always read the file and reason
        # correctly (e.g. "this is an SRS, there are no products to search").
        if callback_context.state is not None and llm_request.contents:
            saved_content = callback_context.state.get('_last_file_content')
            file_in_convo = bool(callback_context.state.get('_file_in_conversation'))
            if saved_content and file_in_convo:
                already_present = any(
                    hasattr(p, 'text') and p.text and '[Nội dung file tải lên]' in p.text
                    for c in llm_request.contents if c.parts
                    for p in c.parts
                )
                if not already_present:
                    target = llm_request.contents[-1]
                    if target.parts is None:
                        target.parts = []
                    target.parts.insert(0, genai_types.Part(
                        text=f"[Nội dung file tải lên]\n{saved_content}"
                    ))
                    logger.info(f"[FILE_UPLOAD] Re-injected saved file content from state "
                                f"({len(saved_content)} chars) — keeping file context visible")

        # Check last user message for file upload
        last_content = llm_request.contents[-1]
        if not last_content.parts:
            if not stripped_from_last:
                logger.debug("inject_file_upload_context: No parts in last content")
                return
            # All parts were unsupported and couldn't be converted — add placeholder
            last_content.parts = [genai_types.Part(text="[User uploaded a document/file]")]
            logger.info("[FILE_UPLOAD] Added placeholder text after stripping all parts from last content")

        # Pre-populate from the processing pass so detection loop can extend these
        all_file_types = list(stripped_last_types) + list(converted_last_types)
        has_file_upload = stripped_from_last or bool(converted_last_types)
        has_text_content = False
        # Tracks whether a Gemini-readable file (image/pdf/text/csv) is present in the
        # last message. docx/xlsx are NOT native — they only count as readable content
        # if they were successfully extracted into a [Nội dung file tải lên] block
        # (i.e. appear in converted_last_types).
        has_native_file = False
        # Images are native files Gemini can SEE directly. When a user uploads a product
        # photo (often with no text), we must search the product shown immediately instead
        # of asking what they want — there is nothing to clarify, the intent is obvious.
        has_image_file = False
        file_type = all_file_types[0] if all_file_types else None
        file_types_found = all_file_types

        # First pass: detect all file uploads and text content
        for part in last_content.parts:
            # Check for inline_data (base64 encoded file)
            if hasattr(part, 'inline_data') and part.inline_data:
                mime_type = getattr(part.inline_data, 'mime_type', None)
                if _is_file_mime_type(mime_type):
                    has_file_upload = True
                    has_native_file = True
                    if _is_image_mime_type(mime_type):
                        has_image_file = True
                    file_type = mime_type
                    file_types_found.append(mime_type)
                    logger.info(f"[FILE_UPLOAD] Detected inline_data file: {mime_type}")
                elif mime_type:
                    # Log unknown mime types for debugging
                    logger.warning(f"[FILE_UPLOAD] Unknown MIME type (not in whitelist): {mime_type}")

            # Check for file_data (file reference)
            if hasattr(part, 'file_data') and part.file_data:
                mime_type = getattr(part.file_data, 'mime_type', None)
                if _is_file_mime_type(mime_type):
                    has_file_upload = True
                    has_native_file = True
                    if _is_image_mime_type(mime_type):
                        has_image_file = True
                    file_type = mime_type
                    file_types_found.append(mime_type)
                    logger.info(f"[FILE_UPLOAD] Detected file_data file: {mime_type}")
                elif mime_type:
                    logger.warning(f"[FILE_UPLOAD] Unknown MIME type (file_data): {mime_type}")

            # Check for text content
            if hasattr(part, 'text') and part.text and part.text.strip():
                # Don't count system context or converted file content as user text
                text_content = part.text.strip()
                if not text_content.startswith("[SYSTEM CONTEXT") and not text_content.startswith("[Nội dung file tải lên]"):
                    has_text_content = True

        # If no file uploaded in THIS turn, check whether the conversation has a
        # PENDING empty file that the user is now trying to act on. Typical flow:
        # user uploads a blank docx (turn 1 → we ask to clarify) then clicks a
        # suggestion like "so sánh giá" / "tìm sản phẩm trong file" (turn 2). The
        # file lives in history now, so has_file_upload is False here — but there is
        # still NO readable content to search. Block it deterministically instead of
        # letting the agent hallucinate products from its prompt examples.
        if not has_file_upload:
            file_in_convo = (
                callback_context.state is not None
                and bool(callback_context.state.get('_file_in_conversation'))
            )
            if file_in_convo:
                user_text_all = " ".join(
                    (p.text or "").lower()
                    for p in last_content.parts
                    if hasattr(p, 'text') and p.text
                ).strip()
                # Any search / comparison / analysis intent counts as "acting on the file"
                # when a file exists in the conversation. Use substring keyword matching so
                # ALL phrasings are caught — "tìm sp", "tìm sản phẩm", "tìm", "so sánh giá",
                # "phân tích nội dung file"... The injected context below still searches a
                # user-named product if one is given, so a fresh concrete search is NOT lost.
                file_action_intent = (
                    'file' in user_text_all
                    or any(kw in user_text_all
                           for kw in (COMPARISON_KEYWORDS + ANALYSIS_KEYWORDS + PRODUCT_SEARCH_KEYWORDS))
                )
                file_readable = bool(callback_context.state.get('_uploaded_file_readable'))
                context = None
                if file_action_intent and not file_readable:
                    # Empty / unreadable file → there is nothing to act on. Ask to re-upload.
                    context = """

[SYSTEM CONTEXT - FILE UPLOAD - CLARIFY INTENT]:
The file the user uploaded earlier has NO readable content (empty file or text could not be extracted).
ACTION REQUIRED (follow in order):
1. IF the user's CURRENT message names a specific product (e.g. "tìm sữa Vinamilk") → search for THAT product normally. The empty file is irrelevant.
2. OTHERWISE (generic request like "tìm sp", "so sánh giá", referring to the file): Do NOT call cng_product_search_tool — there is no file content. Tell the user (Vietnamese) the file appears empty / unreadable and ask them to re-upload a valid file or type the product names directly.
3. NEVER show promotional products or guess ANY products. The example products in your prompt are NOT in the file.
INTENT: CLARIFY (referenced file has no readable content)."""
                elif file_action_intent and file_readable:
                    # File HAS content → force the agent to read the ACTUAL content block.
                    # The content may or may not contain products (it could be an SRS,
                    # report, contract...). Never invent products from prompt examples.
                    context = """

[SYSTEM CONTEXT - FILE FOLLOW-UP - READ FILE CONTENT]:
The user is acting on a file uploaded earlier in this conversation.
ACTION REQUIRED (follow in order):
1. IF the user's CURRENT message names a specific product (e.g. "tìm sữa Vinamilk") → search for THAT product normally.
2. OTHERWISE (generic request like "tìm sp", "tìm sản phẩm", "so sánh giá"): FIRST locate the `[Nội dung file tải lên]` block in the conversation and READ its actual content, then search / compare ONLY the products that ACTUALLY appear in that block.
3. IF neither the message nor the `[Nội dung file tải lên]` block contains any real product name (e.g. the file is a report, SRS, contract, slide, or general document) → DO NOT call any search function. Call set_model_response to (1) briefly DESCRIBE in Vietnamese what the file is, and (2) explain it contains no products to search/compare, then ask the user to type product names or upload a product list.
4. NEVER use a generic word like "sản phẩm"/"sp" as a keyword, NEVER use example product names from your prompt, and NEVER invent products — search ONLY what is really in the file.
INTENT: PROCESS FILE FROM ITS ACTUAL CONTENT."""
                if context:
                    # The actual file content is already re-injected unconditionally above
                    # (kept visible every turn). Here we only append the guidance context —
                    # to a NON-content text part so we never corrupt the content block.
                    appended = False
                    for p in last_content.parts:
                        if (hasattr(p, 'text') and p.text is not None
                                and not (p.text or "").startswith("[Nội dung file tải lên]")):
                            p.text = (p.text or "") + context
                            appended = True
                            break
                    if not appended:
                        last_content.parts.append(genai_types.Part(text=context))
                    logger.info(f"[FILE_UPLOAD] Injected follow-up file context "
                                f"(readable={file_readable}, intent={file_action_intent})")
            logger.debug("inject_file_upload_context: No file upload in current message")
            return

        # Set state flag to indicate file upload was detected (backup signal)
        if callback_context.state is not None:
            callback_context.state['_file_upload_detected'] = True
            callback_context.state['_file_upload_types'] = file_types_found
            logger.info(f"[FILE_UPLOAD] Set state flag: _file_upload_detected=True, types={file_types_found}")

        # Determine whether the LLM actually has file content to work with:
        # - a native file (image/pdf/text/csv) Gemini can read directly, OR
        # - a docx/xlsx that was successfully extracted into a [Nội dung file tải lên] block.
        # If NEITHER, the file is empty / unreadable (e.g. blank docx) → there is nothing
        # to search. We MUST ask the user instead of guessing products / showing promos.
        has_readable_content = has_native_file or bool(converted_last_types)
        logger.info(f"[FILE_UPLOAD] has_readable_content={has_readable_content} "
                    f"(native_file={has_native_file}, extracted_text={bool(converted_last_types)})")
        if callback_context.state is not None:
            # Remember, across turns, that a file exists in this conversation and whether
            # it had readable content. The FOLLOW-UP turn (user clicks "tìm sản phẩm" /
            # "so sánh giá" after uploading) carries no file itself, so these flags let us
            # force the agent to read the ACTUAL [Nội dung file tải lên] block instead of
            # hallucinating products from prompt examples.
            callback_context.state['_file_in_conversation'] = True
            callback_context.state['_uploaded_file_readable'] = has_readable_content

        # Find or create text part to append context
        # IMPORTANT: A part can only have ONE of: text, inline_data, file_data
        # Check that part has text AND does NOT have inline_data/file_data
        text_part_found = False
        for part in last_content.parts:
            # Check if this is a text-only part (no inline_data or file_data)
            has_inline_data = hasattr(part, 'inline_data') and part.inline_data
            has_file_data = hasattr(part, 'file_data') and part.file_data
            has_text = hasattr(part, 'text') and part.text is not None

            # Only process text-only parts (not file parts)
            # Skip converted file content — don't treat extracted file text as user intent
            if has_text and not has_inline_data and not has_file_data:
                if (part.text or "").startswith("[Nội dung file tải lên]"):
                    continue
                text_part_found = True
                user_text = (part.text or "").lower()

                # Detect intents
                has_comparison_intent = any(kw in user_text for kw in COMPARISON_KEYWORDS)
                has_analysis_intent = any(kw in user_text for kw in ANALYSIS_KEYWORDS)
                has_search_intent = any(kw in user_text for kw in PRODUCT_SEARCH_KEYWORDS)
                has_checkout_keywords = any(kw in user_text for kw in CHECKOUT_KEYWORDS_TO_BLOCK)
                has_order_tracking_keywords = any(kw in user_text for kw in ORDER_TRACKING_KEYWORDS_TO_BLOCK)

                # Build context message based on intent priority:
                # 1. Analysis ("phân tích") → describe file content
                # 2. Comparison ("so sánh") → compare prices
                # 3. Search ("tìm", "kiểm tra") → simple product search
                # 4. No text → DEFAULT to price comparison

                if not has_readable_content:
                    # File uploaded but produced NO readable content (blank docx/xlsx,
                    # or a file whose text could not be extracted). There is nothing to
                    # search — DO NOT guess products or show promotions. Ask the user.
                    context = f"""

[SYSTEM CONTEXT - FILE UPLOAD - CLARIFY INTENT]:
User uploaded a file ({file_type}) but it has NO readable content (empty file or text could not be extracted).
ACTION REQUIRED:
1. Do NOT call cng_product_search_tool — there is no content to search.
2. Do NOT show promotional products or guess any products.
3. Do NOT transfer to checkout_agent or call order tracking tools.
4. Respond in ONE flowing Vietnamese sentence (NO bullet points, NO line breaks). Use exactly: "Em đã nhận được file của anh/chị rồi ạ. Anh/chị muốn em so sánh giá với MM Mega Market, tìm kiếm sản phẩm trong file, hay phân tích nội dung file ạ?"
INTENT: CLARIFY (file has no readable content)."""
                    if not part.text:
                        part.text = ""
                elif has_text_content:
                    if has_analysis_intent:
                        # User wants to analyze/understand file content
                        context = f"""

[SYSTEM CONTEXT - FILE UPLOAD WITH ANALYSIS INTENT]:
User uploaded a file ({file_type}) and wants to ANALYZE its content.
ACTION REQUIRED:
1. You MUST call `cng_product_search_tool` to process the file
2. The response MUST describe what the file contains:
   - Document type (invoice, receipt, shopping list, etc.)
   - Date (if available)
   - List of products with names, prices, quantities
   - Total amount (if available)
3. DO NOT just search products - DESCRIBE the file content first
4. Ask if user wants to find these products at MM Mega Market
INTENT: FILE ANALYSIS (describe file content)."""
                    elif has_comparison_intent:
                        # User wants price comparison
                        context = f"""

[SYSTEM CONTEXT - FILE UPLOAD WITH COMPARISON INTENT]:
User uploaded a file ({file_type}) with COMPARISON intent.
ACTION REQUIRED:
1. You MUST call `cng_product_search_tool` to search products in the file
2. The response MUST include detailed price comparison:
   - Price in file vs Price at MM Mega Market for EACH product
3. DO NOT transfer to checkout_agent
4. DO NOT call order tracking tools
INTENT: PRICE COMPARISON."""
                    elif has_search_intent:
                        # User explicitly wants to search/find products
                        context = f"""

[SYSTEM CONTEXT - FILE UPLOAD WITH SEARCH INTENT]:
User uploaded a file ({file_type}) and wants to SEARCH for products.
ACTION REQUIRED:
1. You MUST call `cng_product_search_tool` to search products in the file
2. The response should list found products (simple listing, NOT comparison)
3. DO NOT transfer to checkout_agent
4. DO NOT call order tracking tools
INTENT: PRODUCT SEARCH (simple listing)."""
                    elif has_order_tracking_keywords:
                        # User said order tracking words but uploaded file - BLOCK order tracking
                        context = f"""

[SYSTEM CONTEXT - FILE UPLOAD PRIORITY]:
User uploaded a file ({file_type}) with text containing order tracking keywords.
CRITICAL: File upload OVERRIDES order tracking intent.
ACTION REQUIRED:
1. You MUST call `cng_product_search_tool` to search products in the file
2. DO NOT call check_my_orders or order tracking tools
3. DO NOT transfer to checkout_agent
4. DEFAULT to price comparison format
INTENT: PRICE COMPARISON (file upload overrides order tracking)."""
                    elif has_checkout_keywords:
                        # User said checkout/order words but uploaded file - BLOCK checkout
                        context = f"""

[SYSTEM CONTEXT - FILE UPLOAD PRIORITY]:
User uploaded a file ({file_type}) with text containing checkout keywords.
IMPORTANT: File upload OVERRIDES checkout intent.
ACTION REQUIRED:
1. You MUST call `cng_product_search_tool` to search products in the file
2. DO NOT transfer to checkout_agent
3. DO NOT call order tracking tools
4. DEFAULT to price comparison format
INTENT: PRICE COMPARISON (file upload overrides checkout)."""
                    else:
                        # User typed text alongside the file but no keyword matched — execute directly
                        context = f"""

[SYSTEM CONTEXT - FILE UPLOAD WITH USER REQUEST]:
User uploaded a file ({file_type}) and typed a request. Execute the request directly.
ACTION REQUIRED:
1. Call cng_product_search_tool to process the file according to the user's request.
2. DO NOT transfer to checkout_agent.
3. DO NOT call order tracking tools.
INTENT: EXECUTE USER REQUEST (user specified what they want)."""
                elif has_image_file:
                    # Image uploaded with no text — the agent can SEE the image directly.
                    # Search the product shown immediately; do NOT ask the user to clarify.
                    context = f"""

[SYSTEM CONTEXT - IMAGE UPLOAD - AUTO SEARCH]:
User uploaded an image ({file_type}) with no text message. You can SEE the image directly.
ACTION REQUIRED:
1. Look at the uploaded image and identify the product(s) shown in it.
2. Call cng_product_search_tool to search those product(s) at MM Mega Market (default: price comparison format).
3. DO NOT ask the user what they want and DO NOT show a clarify message — the intent is to find the product in the image.
4. DO NOT transfer to checkout_agent or call order tracking tools.
5. Only if the image clearly shows NO identifiable product at all, briefly say so in Vietnamese and ask what product they are looking for.
INTENT: AUTO SEARCH PRODUCT FROM IMAGE.""" + _NO_TEXT_LANGUAGE_DIRECTIVE
                    if not part.text:
                        part.text = ""
                else:
                    # No user text — let agent detect intent from file content
                    context = f"""

[SYSTEM CONTEXT - FILE UPLOAD - AUTO DETECT INTENT]:
User uploaded a file ({file_type}) without any text message.
ACTION REQUIRED:
1. Read the [Nội dung file tải lên] block in the conversation to examine the file content.
2. If the file content contains product names, food items, SKUs, a shopping list, or a price list:
   → Call cng_product_search_tool to search for those products (default: price comparison format).
3. If the file content does NOT contain products (e.g., it is a report, SRS, contract, or general document):
   → Do NOT call any tool. Respond in ONE flowing Vietnamese sentence (NO bullet points, NO line breaks). Use exactly: "Em đã nhận được file của anh/chị rồi ạ. Anh/chị muốn em so sánh giá với MM Mega Market, tìm kiếm sản phẩm trong file, hay phân tích nội dung file ạ?"
INTENT: AUTO-DETECT from file content.""" + _NO_TEXT_LANGUAGE_DIRECTIVE
                    if not part.text:
                        part.text = ""

                # Universal PRODUCT GATE: the downstream search agent is forced
                # (function_calling mode=ANY) and can NEVER say "no products" — it will
                # search whatever it can, even the literal word "sản phẩm". So the
                # decision to search-or-not MUST be made here, before the search tool is
                # invoked. For any readable file + user request, require the agent to first
                # verify the file actually lists products; if not, answer directly.
                # Images have no [Nội dung file tải lên] text block — the agent reads the
                # picture directly, so point the gate at the image instead of a text block.
                if has_readable_content and has_text_content:
                    source = ("the uploaded image (look at the picture directly)"
                              if has_image_file
                              else "the `[Nội dung file tải lên]` block")
                    guard = f"""

[SYSTEM CONTEXT - READ & UNDERSTAND THE FILE FIRST (overrides the ACTION below)]:
The downstream search tool is FORCED to search and can never say "no products", so YOU must decide here.
STEP 1 — READ {source} and identify what this file actually is:
  (a) a product / shopping list / invoice / receipt / price list (has product names, SKUs, or prices), OR
  (b) a general document (report, SRS, specification, contract, slide, plan, CV, ...) with NO products.
STEP 2 — Decide:
  - IF (a): perform the ACTION below by calling cng_product_search_tool, using ONLY the product names that actually appear in {source}.
  - IF (b): DO NOT call cng_product_search_tool and DO NOT transfer to a forced search. Instead call set_model_response to (1) briefly DESCRIBE in Vietnamese what the file is, and (2) explain it contains no products to search/compare, then ask the user to type product names or upload a product list.
NEVER use a generic word like "sản phẩm" / "sp" / "hàng" as a search keyword, and NEVER invent products."""
                    context = guard + context

                # Append context to text part
                original_text = part.text or ""
                part.text = original_text + context

                # Validate injection succeeded
                if "[SYSTEM CONTEXT" in part.text:
                    logger.info(f"[FILE_UPLOAD] Successfully injected context: has_text={has_text_content}, "
                               f"comparison={has_comparison_intent}, checkout_blocked={has_checkout_keywords}, "
                               f"order_tracking_blocked={has_order_tracking_keywords}, file_type={file_type}")
                else:
                    logger.error(f"[FILE_UPLOAD] FAILED to inject context - text doesn't contain marker!")

                break

        if not text_part_found:
            logger.warning(f"[FILE_UPLOAD] No text part found in message, attempting to add one")
            try:
                if has_image_file:
                    # Image-only upload (no text part at all) — the agent can SEE the image.
                    # Search the product shown directly instead of asking the user.
                    context = f"""
[SYSTEM CONTEXT - IMAGE UPLOAD - AUTO SEARCH]:
User uploaded an image ({file_type}) with no text message. You can SEE the image directly.
ACTION REQUIRED:
1. Look at the uploaded image and identify the product(s) shown in it.
2. Call cng_product_search_tool to search those product(s) at MM Mega Market (default: price comparison format).
3. DO NOT ask the user what they want and DO NOT show a clarify message — the intent is to find the product in the image.
4. DO NOT transfer to checkout_agent or call order tracking tools.
5. Only if the image clearly shows NO identifiable product at all, briefly say so in Vietnamese and ask what product they are looking for.
INTENT: AUTO SEARCH PRODUCT FROM IMAGE.""" + _NO_TEXT_LANGUAGE_DIRECTIVE
                elif bool(converted_last_types):
                    # docx/xlsx uploaded with NO accompanying text. Extraction succeeded, so
                    # the ONLY text part is the `[Nội dung file tải lên]` block — which the
                    # loop above skips. Without this branch we would wrongly fall through to
                    # CLARIFY even though the file HAS readable content. Auto-detect from the
                    # extracted content instead (same as the no-text branch for native files).
                    context = f"""
[SYSTEM CONTEXT - FILE UPLOAD - AUTO DETECT INTENT]:
User uploaded a file ({file_type}) without any text message. Its content was extracted into the [Nội dung file tải lên] block.
ACTION REQUIRED:
1. READ the [Nội dung file tải lên] block in the conversation and identify what this file actually is.
2. If it contains product names, food items, SKUs, a shopping list, or a price list:
   → Call cng_product_search_tool to search ONLY the products that actually appear in the block (default: price comparison format).
3. If it does NOT contain real products (e.g. it is an import template with only column headers, a report, SRS, contract, or general document):
   → Do NOT call any tool and NEVER invent products. Respond in ONE flowing Vietnamese sentence (NO bullet points, NO line breaks). Use exactly: "Em đã nhận được file của anh/chị rồi ạ. Anh/chị muốn em so sánh giá với MM Mega Market, tìm kiếm sản phẩm trong file, hay phân tích nội dung file ạ?"
4. NEVER use a generic word like "sản phẩm"/"sp" as a search keyword and NEVER use example products from your prompt.
INTENT: AUTO-DETECT from extracted file content.""" + _NO_TEXT_LANGUAGE_DIRECTIVE
                else:
                    context = f"""
[SYSTEM CONTEXT - FILE UPLOAD - CLARIFY INTENT]:
User uploaded a file ({file_type}) without any text message.
ACTION REQUIRED:
1. Do NOT call cng_product_search_tool yet
2. Do NOT transfer to checkout_agent
3. Do NOT call order tracking tools
4. Respond in ONE flowing Vietnamese sentence (NO bullet points, NO line breaks). Use exactly: "Em đã nhận được file của anh/chị rồi ạ. Anh/chị muốn em so sánh giá với MM Mega Market, tìm kiếm sản phẩm trong file, hay phân tích nội dung file ạ?"
INTENT: CLARIFY (ask user before acting).""" + _NO_TEXT_LANGUAGE_DIRECTIVE
                new_part = genai_types.Part(text=context)
                last_content.parts.append(new_part)
                logger.info(f"[FILE_UPLOAD] Added new text part "
                            f"(image_auto_search={has_image_file})")
            except Exception as add_error:
                logger.error(f"[FILE_UPLOAD] Failed to add text part: {add_error}")

    except Exception as e:
        # Log the error with full traceback for debugging
        import traceback
        logger.error(f"[FILE_UPLOAD] CRITICAL: Failed to inject file upload context: {e}")
        logger.error(f"[FILE_UPLOAD] Traceback: {traceback.format_exc()}")
        # Set state flag anyway to signal that file was detected but injection failed
        if callback_context.state is not None:
            callback_context.state['_file_upload_detected'] = True
            callback_context.state['_file_upload_injection_failed'] = True


def inject_order_completion_instruction(callback_context: CallbackContext, llm_request: LlmRequest):
    """
    Detect functionResponse for checkout-related tools and inject clear instruction
    for the agent to respond correctly.

    Handles:
    1. show_payment_methods - order completion (success/pending/failed)
    2. show_checkout_step - checkout popup actions (done/cancelled)

    This fixes the issue where agent doesn't respond after receiving functionResponse.
    """
    try:
        if not llm_request.contents:
            return

        # Check last message for functionResponse
        last_content = llm_request.contents[-1]
        if not last_content.parts:
            return

        instruction = None

        for part in last_content.parts:
            # Check for functionResponse
            if hasattr(part, 'function_response') and part.function_response:
                func_response = part.function_response
                func_name = getattr(func_response, 'name', '')
                response_data = getattr(func_response, 'response', {})

                if not isinstance(response_data, dict):
                    continue

                status = response_data.get('status')

                # Handle show_payment_methods functionResponse (order completion)
                if func_name == 'show_payment_methods':
                    order_number = response_data.get('order_number')
                    email = response_data.get('email')

                    # Save email and order_number to session state for later use (e.g., order tracking)
                    # IMPORTANT: Save at ROOT level to prevent frontend from overwriting
                    # Frontend often sends state.state with only magento_session_data, which overwrites nested values
                    if email:
                        # Save at ROOT level (primary) - won't be overwritten by frontend
                        callback_context.state['guest_user_email'] = email
                        logger.info(f"[ORDER_COMPLETION] Saved guest email to ROOT state: {email}")

                        # Also save in nested state as backup (may be overwritten by frontend)
                        current_state = callback_context.state.get('state', {})
                        if isinstance(current_state, dict):
                            current_state['guest_user_email'] = email
                            callback_context.state['state'] = current_state

                    if order_number:
                        # Save at ROOT level
                        callback_context.state['last_order_number'] = order_number
                        logger.info(f"[ORDER_COMPLETION] Saved order_number to ROOT state: {order_number}")

                        # Save order-to-email mapping for multi-order tracking
                        # This allows tracking different orders with different emails
                        if email:
                            order_email_map = callback_context.state.get('order_email_map', {})
                            order_email_map[order_number] = email
                            callback_context.state['order_email_map'] = order_email_map
                            logger.info(f"[ORDER_COMPLETION] Saved order-email mapping: {order_number} -> {email}")

                    # Reset checkout flow state when order is placed (any status)
                    # This ensures user can start fresh checkout for new order
                    if status in ('success', 'pending', 'failed'):
                        callback_context.state['in_checkout_flow'] = False
                        callback_context.state['checkout_stage'] = None
                        callback_context.state['checkout_step_number'] = None
                        logger.info(f"[ORDER_COMPLETION] Reset checkout state: in_checkout_flow=False, status={status}")

                    if status and order_number:
                        # This is an order completion response - inject instruction
                        if status == 'success':
                            instruction = f"""

[SYSTEM CONTEXT - ORDER COMPLETION]:
This is the functionResponse for show_payment_methods with order completion.
Order #{order_number} has been placed SUCCESSFULLY.

YOU MUST:
1. Call set_checkout_response IMMEDIATELY
2. Use message: "Đặt hàng thành công\\n\\nMã đơn hàng: **#{order_number}**\\n\\nTrong quá trình mua hàng quý khách có trở ngại gì thì vui lòng liên hệ hotline **1800 646878** để được hỗ trợ!"
3. Set show_check_order_cta_button=True
4. Set show_reorder_cta_button=True

DO NOT:
- Display payment methods again
- Repeat old messages from history
- Output any text without calling set_checkout_response first
"""
                        elif status == 'pending':
                            instruction = f"""

[SYSTEM CONTEXT - ORDER PENDING]:
This is the functionResponse for show_payment_methods with order pending.
Order #{order_number} is being processed.

YOU MUST:
1. Call set_checkout_response IMMEDIATELY
2. Use message: "Đơn hàng của Anh/Chị đang được xử lý.\\n\\nMã đơn hàng: **#{order_number}**\\n\\nTrong quá trình mua hàng quý khách có trở ngại gì thì vui lòng liên hệ hotline **1800 646878** để được hỗ trợ!"
3. Set show_check_order_cta_button=True

DO NOT:
- Display payment methods again
- Repeat old messages from history
"""
                        else:
                            instruction = f"""

[SYSTEM CONTEXT - ORDER FAILED]:
This is the functionResponse for show_payment_methods with order failure.
Order placement FAILED (status={status}).

YOU MUST:
1. Call set_checkout_response IMMEDIATELY
2. Use message: "Đặt hàng không thành công.\n\nMã đơn hàng: #{order_number}\n\nTrong quá trình mua hàng quý khách có trở lại gì thì vui lòng liên hệ hotline **1800 646878** để được hỗ trợ!"
3. Set show_checkout_popup_button=False

DO NOT:
- Display payment methods again
- Repeat old messages from history
"""

                # Handle show_checkout_step functionResponse (checkout popup actions)
                elif func_name == 'show_checkout_step':
                    completed_step = response_data.get('completed_step', 'main_info')

                    # Save checkout form data to session state when user completes the popup
                    # This allows chatbot to:
                    # 1. Remember user info for future checkouts (prefill)
                    # 2. Use email for order tracking
                    # 3. Confirm delivery info with user
                    if status == 'done':
                        # Initialize state['state'] if not exists
                        if 'state' not in callback_context.state:
                            callback_context.state['state'] = {}

                        # Define checkout form fields to save
                        checkout_fields = [
                            # Required fields
                            'recipient_name',
                            'email',
                            'phone',
                            'street',
                            'city_name',
                            'ward_name',
                            'delivery_date',
                            'delivery_time_label',
                            # Optional fields
                            'note',
                            'mcard_number',
                            'call_before_delivery',
                            'issue_vat_invoice',
                            # VAT invoice fields (when issue_vat_invoice=true)
                            'company_name',
                            'company_vat_number',
                            'company_address',
                        ]

                        # Extract form data from response
                        # FE sends fields inside "fields" object
                        fields_data = response_data.get('fields', {})

                        checkout_info = {}
                        for field in checkout_fields:
                            value = fields_data.get(field)
                            if value is not None and value != '':
                                checkout_info[field] = value

                        # Save checkout info if we have any data
                        if checkout_info:
                            # IMPORTANT: Save critical fields at ROOT level to prevent frontend overwrite
                            # Frontend often sends state.state with only magento_session_data
                            if checkout_info.get('email'):
                                callback_context.state['guest_user_email'] = checkout_info['email']
                                logger.info(f"[CHECKOUT_STEP] Saved guest email to ROOT: {checkout_info['email']}")
                            if checkout_info.get('phone'):
                                callback_context.state['guest_user_phone'] = checkout_info['phone']
                                logger.info(f"[CHECKOUT_STEP] Saved guest phone to ROOT: {checkout_info['phone']}")

                            # Also save full checkout info in nested state (backup, may be overwritten)
                            current_state = callback_context.state.get('state', {})
                            if not isinstance(current_state, dict):
                                current_state = {}
                            current_state['guest_checkout_info'] = checkout_info
                            if checkout_info.get('email'):
                                current_state['guest_user_email'] = checkout_info['email']
                            if checkout_info.get('phone'):
                                current_state['guest_user_phone'] = checkout_info['phone']
                            callback_context.state['state'] = current_state

                            logger.info(f"[CHECKOUT_STEP] Saved checkout form data to state: {list(checkout_info.keys())}")

                        # User completed the checkout popup step
                        instruction = f"""

[SYSTEM CONTEXT - CHECKOUT STEP COMPLETED - CRITICAL]:
This is the functionResponse for show_checkout_step with status="done".
User has SUCCESSFULLY completed the checkout popup and filled in all delivery information.
Completed step: {completed_step}.

🚨 CRITICAL - YOU MUST DO THIS:
1. Call show_payment_methods tool to display payment options
2. OR call set_checkout_response with confirmation message

CORRECT RESPONSE EXAMPLE:
- Message: "Cảm ơn Anh/Chị đã điền thông tin giao hàng. Anh/Chị vui lòng chọn phương thức thanh toán bên dưới để hoàn tất đơn hàng nhé!"
- show_confirm_cta_button=True OR show_preview_order_cta_button=True

🚫 ABSOLUTELY DO NOT:
- DO NOT repeat "Nhập thông tin đặt hàng" message - user ALREADY filled the form!
- DO NOT show checkout popup again - user ALREADY completed it!
- DO NOT copy messages from conversation history
- DO NOT stay silent

The user has COMPLETED the form. Move FORWARD to payment, not backward!
"""
                    elif status == 'cancelled':
                        # User cancelled/closed the checkout popup
                        instruction = """

[SYSTEM CONTEXT - CHECKOUT POPUP CANCELLED]:
This is the functionResponse for show_checkout_step with status="cancelled".
User has CANCELLED or CLOSED the checkout popup.

YOU MUST:
1. Call set_checkout_response IMMEDIATELY
2. Use EXACT message: "Em đã hủy popup cho anh/chị. Anh/chị có thể tiếp tục mua sắm hoặc yêu cầu thanh toán lại bất kỳ lúc nào ạ."
3. Set show_checkout_popup_button=False
4. Set show_cart_detail_cta_button=False

DO NOT:
- Ask user why they cancelled
- Repeat checkout prompts immediately
- Stay silent without responding
- Show popup button immediately after cancel
"""
                    elif status == 'error':
                        error_message = response_data.get('error_message', 'Unknown error')
                        instruction = f"""

[SYSTEM CONTEXT - CHECKOUT STEP ERROR]:
This is the functionResponse for show_checkout_step with status="error".
Error occurred: {error_message}

YOU MUST:
1. Call set_checkout_response IMMEDIATELY
2. Use message: "Có lỗi xảy ra khi xử lý thông tin. Anh/Chị vui lòng thử lại hoặc liên hệ hotline **1800 646878** để được hỗ trợ."
3. Set show_checkout_popup_button=True

DO NOT:
- Repeat old messages from history
- Stay silent without responding
"""

        # Inject instruction if we have one
        if instruction:
            # Try to append instruction to an existing text part or create new one
            instruction_added = False
            for p in last_content.parts:
                if hasattr(p, 'text') and p.text is not None:
                    p.text = (p.text or "") + instruction
                    instruction_added = True
                    break

            if not instruction_added:
                # Create new text part with instruction
                from google.genai import types as genai_types
                new_part = genai_types.Part(text=instruction)
                last_content.parts.append(new_part)

            logger.info(f"[CHECKOUT_CALLBACK] Injected instruction for functionResponse")

    except Exception as e:
        logger.warning(f"[CHECKOUT_CALLBACK] Failed to inject instruction: {e}")
