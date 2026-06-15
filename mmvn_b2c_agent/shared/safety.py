"""
Safety callbacks for the multi-tool agent system.
"""
import datetime
from typing import Optional, Dict, Any
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.tool_context import ToolContext
from google.genai import types
import mmvn_b2c_agent.shared.constants
from google.genai.types import SafetySetting, HarmBlockThreshold, HarmCategory

SAFETY_FILTER_CONFIG = [
    SafetySetting(threshold=HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                  category=cate)
    for cate in [
        HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        HarmCategory.HARM_CATEGORY_HARASSMENT,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
    ]
]


def content_safety_guardrail(
        callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """
    Inspects the user message for inappropriate content.
    Blocks requests containing specific keywords.
    
    Args:
        callback_context: Context providing access to agent info and session state
        llm_request: The request about to be sent to the LLM
        
    Returns:
        LlmResponse if the request should be blocked, None to allow it
    """
    # Extract the text from the latest user message
    callback_context.state['current_time'] = datetime.datetime.now().isoformat()
    last_user_message_text = ""
    if llm_request.contents:
        for content in reversed(llm_request.contents):
            if content.role == 'user' and content.parts:
                if content.parts[0].text:
                    last_user_message_text = content.parts[0].text
                    if callback_context.state.get("history"):
                        # Append to existing history if available
                        callback_context.state["history"].append(content.parts[-1].text)
                    else:
                        # Initialize history if not present
                        callback_context.state["history"] = [content.parts[-1].text]
                    break
    
    # fixme: for front end to debug, remove once front end complete their solution to catch unexpected error from adk
    if last_user_message_text.upper() == 'THIS_MESSAGE_WILL_TRIGGER_ERROR_ON_BACK_END_ADK-ALSKDJFHG_852369741':
        raise Exception("Intentionally raised error for front end testing")

    # Check for blocked keywords
    for keyword in mmvn_b2c_agent.shared.constants.BLOCKED_KEYWORDS:
        if keyword.lower() in last_user_message_text.lower():
            # Record the block in session state
            callback_context.state["safety_block_triggered"] = True

            # Return a response to block the request
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(
                        text="Em không thể xử lý các yêu cầu có nội dung không phù hợp. "
                             "Nếu anh/chị cho rằng đây là một sai sót, vui lòng liên hệ bộ phận hỗ trợ của chúng tôi."
                    )],
                )
            )

    # Allow the request to proceed
    return None
