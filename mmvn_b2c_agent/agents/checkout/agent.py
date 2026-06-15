

"""
Checkout agent for handling order completion and payment.
"""
import logging
from google.adk.agents import Agent
from google.adk.planners import BuiltInPlanner
from google.genai import types
from mmvn_b2c_agent.shared.constants import MODEL_GEMINI_3_FLASH, get_thinking_config
from mmvn_b2c_agent.tools.checkout import (
    ShowCheckoutStepTool,
    ValidateDeliveryTimeTool,
    SetDeliveryCommentTool,
    SetCallBeforeDeliveryTool,
    SetMCardTool,
    SetVATInvoiceTool,
    ShowPaymentMethodsTool,
    GetMyCheckoutInfoTool,
)
from mmvn_b2c_agent.tools.cng.cart.cart_view import view_cart
from mmvn_b2c_agent.agents.checkout.schema import CheckoutSetResponse
from mmvn_b2c_agent.agents.checkout.prompts import (
    CHECKOUT_AGENT_INSTRUCTION,
    CHECKOUT_AGENT_DESCRIPTION,
)
from mmvn_b2c_agent.shared.callbacks import inject_file_upload_context, inject_order_completion_instruction, extract_email_from_user_function_response, handle_raw_audio_input, strip_old_image_data

logger = logging.getLogger(__name__)


checkout_agent = Agent(
    name="checkout_agent",
    model=MODEL_GEMINI_3_FLASH,
    description=CHECKOUT_AGENT_DESCRIPTION,
    static_instruction=types.Content(parts=[types.Part(text=CHECKOUT_AGENT_INSTRUCTION)]),
    tools=[
        # Cart tool to check cart before checkout
        view_cart,

        # Checkout tools
        ShowCheckoutStepTool,
        ValidateDeliveryTimeTool,
        SetDeliveryCommentTool,
        SetCallBeforeDeliveryTool,
        SetMCardTool,
        SetVATInvoiceTool,
        ShowPaymentMethodsTool,
        GetMyCheckoutInfoTool,

        # Output formatter tool (MANDATORY - agent must END with this tool)
        CheckoutSetResponse(),
    ],
    # Force agent to END with set_checkout_response tool (JSON ONLY output)
    output_key="set_checkout_response",
    # Force function calling mode (no plain text output)
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,  # Low temperature for deterministic behavior (prevents loop)
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.ANY
            )
        ),
    ),
    # Add callbacks:
    # - extract_email_from_user_function_response: Extract email from FE functionResponse (MUST run first)
    # - inject_file_upload_context: Detect file upload and force product search
    # - inject_order_completion_instruction: Inject instruction when receiving order completion functionResponse
    before_model_callback=[handle_raw_audio_input, strip_old_image_data, extract_email_from_user_function_response, inject_file_upload_context, inject_order_completion_instruction],
    planner=BuiltInPlanner(thinking_config=get_thinking_config(MODEL_GEMINI_3_FLASH)),
)
