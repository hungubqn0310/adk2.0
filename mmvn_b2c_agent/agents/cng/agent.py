"""
CNG (Click and Get) agent definition for e-commerce functionality.
"""
from enum import Enum

from google.adk.agents import Agent
from google.adk.planners import BuiltInPlanner
from google.genai import types


from mmvn_b2c_agent.agents.cng.prompts import CNG_AGENT_INSTRUCTION, CNG_AGENT_DESCRIPTION
from mmvn_b2c_agent.agents.cng_product.agent import cng_product_search_workflow_agent
from mmvn_b2c_agent.agents.question_answer import question_answer_agent
from mmvn_b2c_agent.agents.checkout import checkout_agent
from mmvn_b2c_agent.shared.callbacks import handle_malformed_response, inject_current_time_to_context, inject_file_upload_context, extract_email_from_user_function_response, handle_raw_audio_input, strip_old_image_data
from mmvn_b2c_agent.shared.constants import DEFAULT_RETRY_OPTION, MODEL_GEMINI_3_FLASH, GEMINI_BASE_URL, get_thinking_config
from mmvn_b2c_agent.shared.safety import content_safety_guardrail, SAFETY_FILTER_CONFIG
from mmvn_b2c_agent.tools.cng.age_verify import age_verify
from mmvn_b2c_agent.tools.cng.cart.cart_add import add_product_to_cart
from mmvn_b2c_agent.tools.cng.cart.cart_comment import (
    update_comment_on_cart_item_with_sku,
    remove_comment_from_cart_item_with_sku
)
from mmvn_b2c_agent.tools.cng.cart.cart_remove import remove_product_sku_from_cart, remove_everything_from_cart
from mmvn_b2c_agent.tools.cng.cart.cart_update import update_cart_with_product_sku
from mmvn_b2c_agent.tools.cng.cart.cart_view import view_cart, get_cart_shipping_cost
from mmvn_b2c_agent.tools.cng.cart.checkout import checkout_cart, get_checkout_details
from mmvn_b2c_agent.tools.cng.product import (
    ProductDetailTool,
)
from mmvn_b2c_agent.tools.output_formater import CngSetResponse
from mmvn_b2c_agent.tools.rate_limit.rate_limit import rate_limit_callback
from mmvn_b2c_agent.tools.shipping.shiping import shipping_cart
from mmvn_b2c_agent.tools.orders.check_orders import check_my_orders
planner = BuiltInPlanner(
    thinking_config=get_thinking_config(MODEL_GEMINI_3_FLASH)
)


class CtaButtonChoice(Enum):
    SHOW_CART = 'show_cart_button'
    PROCEED_TO_CHECKOUT = 'proceed_to_checkout_button'
    CONTINUE_SHOPPING = 'continue_shopping_button'


def answer_faq_question():
    return {
        "message": "Please process to the FAQ page.",
        "instruction_for_agent": "Politely inform the user that you cant answer that question"
                                 " and redirect them to the FAQ page for more information.",
    }


# Define the CNG agent
cng_agent = Agent(
    name="cng_agent",
    model=MODEL_GEMINI_3_FLASH,
    description=CNG_AGENT_DESCRIPTION,
    # instruction=CNG_AGENT_INSTRUCTION,
    static_instruction=types.Content(parts=[types.Part(text=CNG_AGENT_INSTRUCTION)]),
    tools=[
        # ProductSearchTool(),
        check_my_orders,
        ProductDetailTool(),
        CngSetResponse(),
        view_cart,
        get_cart_shipping_cost,
        add_product_to_cart,
        remove_product_sku_from_cart,
        remove_everything_from_cart,
        update_cart_with_product_sku,
        checkout_cart,
        get_checkout_details,
        update_comment_on_cart_item_with_sku,
        remove_comment_from_cart_item_with_sku,
        # shipping_cart,
        age_verify
    ],
    sub_agents=[cng_product_search_workflow_agent, question_answer_agent, checkout_agent],
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
                mode=types.FunctionCallingConfigMode.ANY  # Force the model to only generate function calls
            )
        ),
    ),
    before_model_callback=[handle_raw_audio_input, strip_old_image_data, content_safety_guardrail, rate_limit_callback, extract_email_from_user_function_response, inject_current_time_to_context, inject_file_upload_context],
    after_model_callback=[handle_malformed_response],
    planner=planner,
    # planner=CngPlaner(),
    # output_schema=CngProductSearchAiResponse
)
