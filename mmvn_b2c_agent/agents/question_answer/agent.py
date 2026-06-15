"""
Question answering agent for handling MM general information queries.
"""
from google.adk.agents import Agent
from google.genai import types

from mmvn_b2c_agent.agents.question_answer.prompts import (
    QUESTION_ANSWER_AGENT_DESCRIPTION,
    QUESTION_ANSWER_AGENT_INSTRUCTION
)
from mmvn_b2c_agent.agents.question_answer.schema import QuestionAnswerSetResponse
from mmvn_b2c_agent.shared.callbacks import handle_malformed_response, handle_raw_audio_input, strip_old_image_data
from mmvn_b2c_agent.shared.constants import MODEL_GEMINI_3_1_FLASH_LITE, DEFAULT_RETRY_OPTION, GEMINI_BASE_URL
from mmvn_b2c_agent.shared.safety import content_safety_guardrail, SAFETY_FILTER_CONFIG
from mmvn_b2c_agent.tools.cng.product.product_search import get_all_categories
from mmvn_b2c_agent.tools.delivery_address.get_delivery_address import add_delivery_address
from mmvn_b2c_agent.tools.location import get_nearest_store_from_address, check_freeship_eligibility, get_store_list_by_region
from mmvn_b2c_agent.tools.location.get_current_store import get_current_store
from mmvn_b2c_agent.tools.mcard.get_mcrad_point import get_mcard_loyalty_points
from mmvn_b2c_agent.tools.rag import get_mm_info_by_rag
from mmvn_b2c_agent.tools.rate_limit.rate_limit import rate_limit_callback
from mmvn_b2c_agent.tools.store.change_store import TriggerChangeStoreTool, ConfirmStoreChangedTool
from mmvn_b2c_agent.tools.user_account.user_account import view_account_info, register_account
from mmvn_b2c_agent.tools.whistlist.add_to_wishlist import TriggerAddToWishlistTool

question_answer_agent = Agent(
    name="question_answer_agent",
    model=MODEL_GEMINI_3_1_FLASH_LITE,
    description=QUESTION_ANSWER_AGENT_DESCRIPTION,
    static_instruction=types.Content(parts=[types.Part(text=QUESTION_ANSWER_AGENT_INSTRUCTION)]),
    tools=[
        get_mm_info_by_rag,
        add_delivery_address,
        get_nearest_store_from_address,
        get_store_list_by_region,
        check_freeship_eligibility,
        TriggerChangeStoreTool,
        ConfirmStoreChangedTool,
        TriggerAddToWishlistTool,
        get_all_categories,
        get_current_store,
        view_account_info,
        register_account,
        get_mcard_loyalty_points,
        QuestionAnswerSetResponse(),
    ],
    output_key="final_response",
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,
        safety_settings=SAFETY_FILTER_CONFIG,
        http_options=types.HttpOptions(
            api_version='v1alpha',
            base_url=GEMINI_BASE_URL,
            retry_options=DEFAULT_RETRY_OPTION
        ),
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.ANY
            )
        ),
    ),
    before_model_callback=[handle_raw_audio_input, strip_old_image_data, content_safety_guardrail, rate_limit_callback],
    after_model_callback=[handle_malformed_response],
)
