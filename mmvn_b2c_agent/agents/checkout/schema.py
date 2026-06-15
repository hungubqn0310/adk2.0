"""
Response schemas and output formatter tool for Checkout Agent.

This module provides:
- CheckoutResponse: Schema for checkout agent responses
- CheckoutResponseFinal: Final formatted response for frontend
- CheckoutSetResponse: Output formatter tool for checkout agent
"""

import logging
from typing import Any, Optional
from pydantic import BaseModel, Field
from google.adk.tools import BaseTool, ToolContext
from google.genai import types
from typing_extensions import override

from mmvn_b2c_agent.agents.cng.schema import DisplayMode
import mmvn_b2c_agent.tools.cng.common

get_current_cart_from_session_state = mmvn_b2c_agent.tools.cng.common.get_current_cart_from_session_state

logger = logging.getLogger(__name__)


class CheckoutResponse(BaseModel):
    """Response schema for checkout agent."""

    language: str = Field(
        description="Detected language of user's input (vi, en, ko, ja, zh, etc.)"
    )

    display_mode: DisplayMode = Field(
        default=DisplayMode.CHECKOUT,
        description="Display mode for checkout responses. Always 'checkout' for this agent."
    )

    message: str = Field(
        description="The response message to display to user. "
                    "Example: 'Dạ, anh/chị vui lòng điền thông tin giao hàng ạ.'"
    )

    # Checkout popup button flag
    show_checkout_popup_button: bool = Field(
        default=False,
        description="Whether to show checkout popup button. "
                    "TRUE when user wants to checkout/place order and cart is not empty."
    )

    # Auto-open checkout popup (without requiring button click)
    auto_open_checkout_popup: bool = Field(
        default=False,
        description="Auto-open checkout popup without requiring button click. "
                    "TRUE ONLY when: 1) in_checkout_flow=true AND 2) user explicitly asks to open popup "
                    "(e.g., 'mở popup', 'mở form', 'điền thông tin giao hàng', 'open popup'). "
                    "When TRUE, FE will automatically display checkout popup."
    )

    # Cart detail button
    show_cart_detail_cta_button: bool = Field(
        default=False,
        description="Whether to show 'Xem chi tiết giỏ hàng' button."
    )

    # Proceed to checkout button
    show_proceed_to_checkout_cta_button: bool = Field(
        default=False,
        description="Whether to show 'Thanh toán ngay' button."
    )

    # Preview order button
    show_preview_order_cta_button: bool = Field(
        default=False,
        description="Whether to show 'Xem trước đơn hàng' button. Show when user has filled delivery info."
    )

    # Confirm button
    show_confirm_cta_button: bool = Field(
        default=False,
        description="Whether to show 'Xác nhận' button. Show when user has filled delivery info and ready to proceed."
    )

    # Check order button (after order placed)
    show_check_order_cta_button: bool = Field(
        default=False,
        description="Whether to show 'Kiểm tra đơn hàng' button. Show after order is placed successfully."
    )

    # Reorder button (after order placed)
    show_reorder_cta_button: bool = Field(
        default=False,
        description="Whether to show 'Đặt lại' button. Show after order is placed successfully."
    )

    # Checkout step information (for multi-step checkout)
    checkout_step: Optional[str] = Field(
        default=None,
        description="Current checkout step name (e.g., 'main_info', 'payment', etc.)"
    )

    checkout_step_number: Optional[int] = Field(
        default=None,
        description="Current step number (1, 2, 3, etc.)"
    )

    checkout_total_steps: int = Field(
        default=1,
        description="Total number of checkout steps"
    )


class CheckoutResponseFinal(BaseModel):
    """Final formatted response for frontend."""

    language: str
    display_mode: DisplayMode = DisplayMode.CHECKOUT
    message: str

    # Cart data for display_mode: cart
    cart_data: Optional[dict] = None

    # Checkout popup button
    show_checkout_popup_button: bool = False

    # Auto-open checkout popup
    auto_open_checkout_popup: bool = False

    # Cart detail and checkout buttons
    show_cart_detail_cta_button: bool = False
    show_proceed_to_checkout_cta_button: bool = False

    # Preview and confirm buttons
    show_preview_order_cta_button: bool = False
    show_confirm_cta_button: bool = False

    # Order completion buttons
    show_check_order_cta_button: bool = False
    show_reorder_cta_button: bool = False

    # Checkout step info
    checkout_step: Optional[str] = None
    checkout_step_number: Optional[int] = None
    checkout_total_steps: int = 1

    @classmethod
    async def format_output(cls, initial_output: CheckoutResponse, tool_context: ToolContext) -> "CheckoutResponseFinal":
        """
        Format output for checkout agent.

        Fetches cart data from session state when display_mode is CART or CHECKOUT.
        """
        # Fetch cart data from session state
        cart_data = None
        current_cart_state = get_current_cart_from_session_state(tool_context)
        if current_cart_state:
            cart_data = current_cart_state.get('cart_raw_data')

        return cls(
            language=initial_output.language,
            display_mode=initial_output.display_mode,
            message=initial_output.message,
            cart_data=cart_data,
            show_checkout_popup_button=initial_output.show_checkout_popup_button,
            auto_open_checkout_popup=initial_output.auto_open_checkout_popup,
            show_cart_detail_cta_button=initial_output.show_cart_detail_cta_button,
            show_proceed_to_checkout_cta_button=initial_output.show_proceed_to_checkout_cta_button,
            show_preview_order_cta_button=initial_output.show_preview_order_cta_button,
            show_confirm_cta_button=initial_output.show_confirm_cta_button,
            show_check_order_cta_button=initial_output.show_check_order_cta_button,
            show_reorder_cta_button=initial_output.show_reorder_cta_button,
            checkout_step=initial_output.checkout_step,
            checkout_step_number=initial_output.checkout_step_number,
            checkout_total_steps=initial_output.checkout_total_steps,
        )

    def model_dump(self, *args, **kwargs) -> dict[str, Any]:
        """Ensure all fields return proper values (no None for booleans)."""
        data = super().model_dump(*args, **kwargs)

        # Ensure boolean fields are never None
        if data.get("show_checkout_popup_button") is None:
            data["show_checkout_popup_button"] = False
        if data.get("auto_open_checkout_popup") is None:
            data["auto_open_checkout_popup"] = False
        if data.get("show_cart_detail_cta_button") is None:
            data["show_cart_detail_cta_button"] = False
        if data.get("show_proceed_to_checkout_cta_button") is None:
            data["show_proceed_to_checkout_cta_button"] = False
        if data.get("show_preview_order_cta_button") is None:
            data["show_preview_order_cta_button"] = False
        if data.get("show_confirm_cta_button") is None:
            data["show_confirm_cta_button"] = False
        if data.get("show_check_order_cta_button") is None:
            data["show_check_order_cta_button"] = False
        if data.get("show_reorder_cta_button") is None:
            data["show_reorder_cta_button"] = False

        return data


class CheckoutSetResponse(BaseTool):
    """Output formatter tool for checkout agent."""

    def __init__(self):
        self.output_schema = CheckoutResponse
        self.name = 'set_model_response'
        self.description = (
            "Format and output the checkout response in the specified structured format. "
            "ALWAYS use this tool to format your final response."
        )
        super().__init__(
            name=self.name,
            description=self.description,
            is_long_running=False
        )

    @override
    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description="""Format and output the checkout response in structured format.

🚨 **CRITICAL: This tool ENDS the conversation turn** 🚨

After calling this tool, you MUST STOP immediately. Do NOT:
- Call any other tools
- Output any text
- Call this tool again

This is your FINAL action in this turn. The system will handle the response delivery.

Use this tool to structure your checkout response with all required fields populated correctly.""",
            parameters_json_schema=self.output_schema.model_json_schema(),
        )

    @override
    async def run_async(
            self, *, args: dict[str, Any], tool_context: ToolContext
    ) -> dict[str, Any]:
        """
        Process the checkout agent's response and return the validated dict.

        Args:
            args: The structured response data matching the output schema.
            tool_context: Tool execution context.

        Returns:
            The validated response as dict.
        """
        try:
            # Validate input against schema
            validated_response = CheckoutResponse.model_validate(args)

            # Format output (pass tool_context to fetch cart data)
            final_response = await CheckoutResponseFinal.format_output(validated_response, tool_context)

            logger.info(
                f"Checkout response formatted: "
                f"show_popup={final_response.show_checkout_popup_button}, "
                f"step={final_response.checkout_step}"
            )

            # Reset checkout state when order is completed
            # Order completion is indicated by: show_check_order_cta_button=True OR show_reorder_cta_button=True
            if final_response.show_check_order_cta_button or final_response.show_reorder_cta_button:
                logger.info("Order completed - resetting checkout state for new orders")
                tool_context.state['in_checkout_flow'] = False
                tool_context.state['checkout_stage'] = None
                tool_context.state['checkout_step_number'] = None

            # Return the validated dict directly
            return final_response.model_dump()

        except Exception as e:
            logger.error(f"Error formatting checkout response: {e}", exc_info=True)

            # Return safe default on error
            return {
                "language": args.get("language", "vi"),
                "message": args.get("message", ""),
                "cart_data": None,
                "show_checkout_popup_button": False,
                "auto_open_checkout_popup": False,
                "show_cart_detail_cta_button": False,
                "show_proceed_to_checkout_cta_button": False,
                "show_preview_order_cta_button": False,
                "show_confirm_cta_button": False,
                "show_check_order_cta_button": False,
                "show_reorder_cta_button": False,
                "checkout_step": None,
                "checkout_step_number": None,
                "checkout_total_steps": 1,
            }
