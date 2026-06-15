"""
Schema definitions for Question Answer Agent responses.

This module contains Pydantic models for structuring responses from the
question_answer agent, including support for customer support CTA buttons.
It also includes the output formatter tool for the question_answer agent.
"""

import logging
from typing import Any, Optional

from google.adk.tools import BaseTool, ToolContext
from google.genai import types
from pydantic import BaseModel, Field
from typing_extensions import override

logger = logging.getLogger(__name__)


class QuestionAnswerAiResponse(BaseModel):
    """
    Response schema for the question_answer agent.

    This schema defines the structure for responses from the question_answer agent,
    which handles company/policy information queries for MM Mega Market Vietnam.

    Attributes:
        language: Detected language of the user's query (e.g., 'vi', 'en', 'ko', 'ja', 'zh')
        message: The complete response message to show to the user
        show_cart_detail_cta_button: Whether to show cart detail button (default: False)
        show_proceed_to_checkout_cta_button: Whether to show checkout button (default: False)
        show_support_cta_button: Whether to show customer support button (default: False)
        show_signin_cta_button: Whether to show sign in button (default: False)

    Important:
        - show_support_cta_button should be True when users need staff assistance:
          * Order cancellation requests ("hủy đơn", "cancel order")
          * Lost invoice/receipt for COMPLETED orders ("mất hóa đơn", "lost receipt")
          * Change delivery address AFTER order placed
          * Refund or return requests ("hoàn tiền", "trả hàng")
          * Order status inquiries ("đơn hàng của tôi", "order status")
          * Complaint or issue resolution ("khiếu nại", "complaint")
          * Complex questions requiring human assistance
          * Any question directing users to contact customer service

        - show_proceed_to_checkout_cta_button should be True for:
          * Invoice issuance requests for NEW/FUTURE orders ("xuất hóa đơn", "lấy hóa đơn", "issue invoice")
          * Questions about how to get an invoice ("làm sao để có hóa đơn")
          * VAT invoice requests ("hóa đơn VAT", "hóa đơn đỏ")
          * Company invoice requests ("hóa đơn công ty")
          * Tax invoice inquiries ("hóa đơn thuế")

        - show_signin_cta_button should be True for:
          * User is NOT logged in (no signin_token) trying to access account features
          * View account information ("xem thông tin tài khoản", "view account info")
          * Add/edit delivery address ("thêm địa chỉ giao hàng", "add delivery address")
          * Any action requiring authentication

        - All buttons should be False for general information queries that can be answered directly
        - Only one should be True at a time - provide clear action path

    Example:
        >>> response = QuestionAnswerAiResponse(
        ...     language="vi",
        ...     message="Dạ, để hủy đơn hàng, anh/chị vui lòng liên hệ CSKH...",
        ...     show_support_cta_button=True
        ... )
    """
    language: str = Field(
        description="Must be Detect the language from the user's CURRENT message/question. "
                    "This MUST match the language the user is using RIGHT NOW in their input. "
                    "Examples: 'vi' (Vietnamese), 'en' (English), 'ko' (Korean), 'ja' (Japanese), 'zh' (Chinese). or another language "
                    "Your response message MUST be in this detected language."
    )
    message: str = Field(
        description="The complete, helpful response message to show to the user. "
                    "This message MUST be in the SAME language as the 'language' field above. "
                    "If language='en', write in English. If language='vi', write in Vietnamese. If language='ko', write in Korean or another language. "
                    "Should be friendly and informative. "
                    "Use polite pronouns: 'em' (I/me), 'anh/chị' (you) for Vietnamese."
    )
    show_cart_detail_cta_button: bool = Field(
        default=False,
        description="Whether to show the cart detail button. "
                    "Generally False for question_answer agent as it doesn't handle cart operations."
    )
    show_proceed_to_checkout_cta_button: bool = Field(
        default=False,
        description="Whether to show the proceed to checkout button. "
                    "Set to True when the user asks about invoice issuance for FUTURE orders "
                    "(e.g., 'xuất hóa đơn', 'lấy hóa đơn', 'issue invoice', 'how to get invoice', "
                    "'hóa đơn VAT', 'hóa đơn đỏ', 'company invoice'). "
                    "This guides users to complete checkout where they can request invoice."
    )
    show_support_cta_button: bool = Field(
        default=False,
        description="Whether to show the customer support button. "
                    "Set to True when the user asks for information or actions that require "
                    "staff support, such as: "
                    "- Order cancellation ('hủy đơn', 'cancel order') "
                    "- Lost invoice/receipt for COMPLETED orders ('mất hóa đơn', 'lost receipt') "
                    "- Change delivery address AFTER order placed ('đổi địa chỉ sau khi đặt đơn') "
                    "- Refund or return requests ('hoàn tiền', 'trả hàng', 'refund', 'return') "
                    "- Order status inquiries ('đơn hàng của tôi', 'order status', 'track order') "
                    "- Complaints or issues ('khiếu nại', 'complaint', 'report problem') "
                    "- Complex questions needing human assistance "
                    "- Any question where you direct users to contact customer service. "
                    "Do NOT show this button for general information queries that can be "
                    "answered directly without staff involvement."
    )
    show_signin_for_account_cta_button: bool = Field(
        default=False,
        description="Whether to show the Sign In button for account information access. "
                    "Set to True when the user is NOT logged in and tries to: "
                    "- View account information ('xem thông tin tài khoản', 'view account info') "
                    "- Edit account information ('chỉnh sửa thông tin tài khoản', 'edit account') "
                    "After sign-in, user will be redirected to /account-information page."
    )
    show_signin_for_address_cta_button: bool = Field(
        default=False,
        description="Whether to show the Sign In button for address management. "
                    "Set to True when the user is NOT logged in and tries to: "
                    "- Add delivery address ('thêm địa chỉ giao hàng', 'add delivery address') "
                    "- Manage delivery addresses ('quản lý địa chỉ', 'manage addresses') "
                    "After sign-in, user will be redirected to /address-book page."
    )
    show_signin_for_wishlist_cta_button: bool = Field(
        default=False,
        description="Whether to show the Sign In button for wishlist management. "
                    "Set to True when the user is NOT logged in and tries to: "
                    "- Add product to wishlist ('thêm vào yêu thích', 'add to wishlist') "
                    "- View wishlist ('xem danh sách yêu thích', 'view wishlist') "
                    "After sign-in, user can add products to their wishlist."
    )
    show_signin_for_dashboard_cta_button: bool = Field(
        default=False,
        description="Whether to show the Sign In button for dashboard/MCard access. "
                    "Set to True when the user is NOT logged in and tries to: "
                    "- View MCard points ('xem điểm MCard', 'check loyalty points') "
                    "- Check MCard balance ('kiểm tra điểm', 'điểm tích lũy') "
                    "- Access customer dashboard features "
                    "After sign-in, user will be redirected to dashboard page."
    )

    def model_dump(self, *args, **kwargs):
        """
        Ensure CTA buttons return proper boolean values.

        Returns:
            dict: Serialized model with guaranteed boolean values for CTA fields.
        """
        data = super().model_dump(*args, **kwargs)
        if data.get("show_cart_detail_cta_button") is None:
            data["show_cart_detail_cta_button"] = False
        if data.get("show_proceed_to_checkout_cta_button") is None:
            data["show_proceed_to_checkout_cta_button"] = False
        if data.get("show_support_cta_button") is None:
            data["show_support_cta_button"] = False
        if data.get("show_signin_for_account_cta_button") is None:
            data["show_signin_for_account_cta_button"] = False
        if data.get("show_signin_for_address_cta_button") is None:
            data["show_signin_for_address_cta_button"] = False
        if data.get("show_signin_for_wishlist_cta_button") is None:
            data["show_signin_for_wishlist_cta_button"] = False
        if data.get("show_signin_for_dashboard_cta_button") is None:
            data["show_signin_for_dashboard_cta_button"] = False
        return data


class QuestionAnswerAiResponseFinal(BaseModel):
    """
    Final response schema for the question_answer agent after formatting.

    This schema is used for the final output that gets sent to the frontend.
    It mirrors QuestionAnswerAiResponse but can include additional formatting.

    Attributes:
        language: Detected language of the user's query
        message: The complete response message
        show_cart_detail_cta_button: Whether to show cart detail button
        show_proceed_to_checkout_cta_button: Whether to show checkout button
        show_support_cta_button: Whether to show customer support button
    """
    language: str
    message: str
    show_cart_detail_cta_button: bool = False
    show_proceed_to_checkout_cta_button: bool = False
    show_support_cta_button: bool = False
    show_signin_for_account_cta_button: bool = False
    show_signin_for_address_cta_button: bool = False
    show_signin_for_wishlist_cta_button: bool = False
    show_signin_for_dashboard_cta_button: bool = False

    @classmethod
    async def format_output(
        cls,
        initial_output: QuestionAnswerAiResponse | dict,
    ) -> "QuestionAnswerAiResponseFinal":
        """
        Format the output for the question_answer agent.

        This method converts the initial response to the final output format.
        For question_answer agent, the formatting is simple as there are no
        product or cart details to look up.

        Args:
            initial_output: The initial response from the agent (either Pydantic model or dict)

        Returns:
            QuestionAnswerAiResponseFinal: The formatted final response

        Example:
            >>> response = QuestionAnswerAiResponse(
            ...     language="vi",
            ...     message="Dạ, để hủy đơn...",
            ...     show_support_cta_button=True
            ... )
            >>> final = await QuestionAnswerAiResponseFinal.format_output(response)
        """
        # Handle both dict and Pydantic model inputs
        if isinstance(initial_output, dict):
            lang = initial_output.get('language', 'vi')
            msg = initial_output.get('message', '')
            show_cart = initial_output.get('show_cart_detail_cta_button', False)
            show_checkout = initial_output.get('show_proceed_to_checkout_cta_button', False)
            show_support = initial_output.get('show_support_cta_button', False)
            show_signin_account = initial_output.get('show_signin_for_account_cta_button', False)
            show_signin_address = initial_output.get('show_signin_for_address_cta_button', False)
            show_signin_wishlist = initial_output.get('show_signin_for_wishlist_cta_button', False)
            show_signin_dashboard = initial_output.get('show_signin_for_dashboard_cta_button', False)
        else:
            lang = initial_output.language
            msg = initial_output.message
            show_cart = initial_output.show_cart_detail_cta_button
            show_checkout = initial_output.show_proceed_to_checkout_cta_button
            show_support = initial_output.show_support_cta_button
            show_signin_account = initial_output.show_signin_for_account_cta_button
            show_signin_address = initial_output.show_signin_for_address_cta_button
            show_signin_wishlist = initial_output.show_signin_for_wishlist_cta_button
            show_signin_dashboard = initial_output.show_signin_for_dashboard_cta_button

        return cls(
            language=lang,
            message=msg,
            show_cart_detail_cta_button=show_cart,
            show_proceed_to_checkout_cta_button=show_checkout,
            show_support_cta_button=show_support,
            show_signin_for_account_cta_button=show_signin_account,
            show_signin_for_address_cta_button=show_signin_address,
            show_signin_for_wishlist_cta_button=show_signin_wishlist,
            show_signin_for_dashboard_cta_button=show_signin_dashboard,
        )

    def model_dump(self, *args, **kwargs):
        """
        Ensure CTA buttons return proper boolean values.

        Returns:
            dict: Serialized model with guaranteed boolean values for CTA fields.
        """
        data = super().model_dump(*args, **kwargs)
        if data.get("show_cart_detail_cta_button") is None:
            data["show_cart_detail_cta_button"] = False
        if data.get("show_proceed_to_checkout_cta_button") is None:
            data["show_proceed_to_checkout_cta_button"] = False
        if data.get("show_support_cta_button") is None:
            data["show_support_cta_button"] = False
        if data.get("show_signin_for_account_cta_button") is None:
            data["show_signin_for_account_cta_button"] = False
        if data.get("show_signin_for_address_cta_button") is None:
            data["show_signin_for_address_cta_button"] = False
        if data.get("show_signin_for_wishlist_cta_button") is None:
            data["show_signin_for_wishlist_cta_button"] = False
        if data.get("show_signin_for_dashboard_cta_button") is None:
            data["show_signin_for_dashboard_cta_button"] = False
        return data


class QuestionAnswerSetResponse(BaseTool):
    """
    Format and output question_answer agent responses in structured format.

    This tool allows the question_answer agent to return structured responses
    with customer support CTA buttons and other interactive elements.

    The tool validates the response against QuestionAnswerAiResponse schema
    and formats it for frontend display.

    Important:
        - Set show_support_cta_button=True when users need staff assistance
        - Set show_proceed_to_checkout_cta_button=True for invoice-related queries
        - Only one CTA button should be True at a time for clear user action path

    Example:
        >>> tool = QuestionAnswerSetResponse()
        >>> result = await tool.run_async(
        ...     args={
        ...         "language": "vi",
        ...         "display_mode": "simple_text",
        ...         "message": "Dạ, để hủy đơn hàng...",
        ...         "show_support_cta_button": True
        ...     },
        ...     tool_context=context
        ... )
    """

    def __init__(self):
        """Initialize the QuestionAnswerSetResponse tool."""
        self.output_schema = QuestionAnswerAiResponse
        self.name = 'set_model_response'#
        self.description = "Format and output the question answer response in the specified structured format with support button controls."
        super().__init__(
            name=self.name,
            description=self.description,
            is_long_running=False
        )

    @override
    def _get_declaration(self) -> types.FunctionDeclaration:
        """
        Get the function declaration for this tool.

        Returns:
            types.FunctionDeclaration: The function declaration with schema
        """
        return types.FunctionDeclaration(
            name=self.name,
            description="""Format and output the response for the question_answer agent.

Use this tool to structure your final response with the appropriate CTA buttons.

**When to set show_support_cta_button=True:**
- Order cancellation requests ("hủy đơn", "cancel order")
- Lost invoice/receipt for COMPLETED orders ("mất hóa đơn", "lost receipt")
- Change delivery address AFTER order placed ("đổi địa chỉ sau khi đặt đơn")
- Refund or return requests ("hoàn tiền", "trả hàng", "refund", "return")
- Order status inquiries ("đơn hàng của tôi", "order status", "track order")
- Complaints or issues ("khiếu nại", "complaint", "report problem")
- Complex questions needing human assistance
- Any question where you direct users to contact customer service

**When to set show_proceed_to_checkout_cta_button=True:**
- Invoice issuance requests for NEW/FUTURE orders ("xuất hóa đơn", "lấy hóa đơn", "issue invoice")
- Questions about how to get an invoice ("làm sao để có hóa đơn")
- VAT invoice requests ("hóa đơn VAT", "hóa đơn đỏ")
- Company invoice requests ("hóa đơn công ty")
- Tax invoice inquiries ("hóa đơn thuế")

**When to set show_signin_for_account_cta_button=True:**
- User is NOT logged in (no signin_token) and tries to view/edit account information
- View account information ("xem thông tin tài khoản", "view account info")
- Edit account information ("chỉnh sửa thông tin tài khoản", "edit account")
- After sign-in, user will be redirected to /account-information page

**When to set show_signin_for_address_cta_button=True:**
- User is NOT logged in (no signin_token) and tries to manage delivery addresses
- Add delivery address ("thêm địa chỉ giao hàng", "add delivery address")
- Manage delivery addresses ("quản lý địa chỉ", "manage addresses")
- After sign-in, user will be redirected to /address-book page

**When to set show_signin_for_wishlist_cta_button=True:**
- User is NOT logged in (no signin_token) and tries to manage wishlist
- Add product to wishlist ("thêm vào yêu thích", "add to wishlist")
- View wishlist ("xem danh sách yêu thích", "view wishlist")
- After sign-in, user can add products to their wishlist

**When to set show_signin_for_dashboard_cta_button=True:**
- User is NOT logged in (no signin_token) and tries to access dashboard features
- View MCard loyalty points ("xem điểm MCard", "check loyalty points")
- Check MCard balance ("kiểm tra điểm", "điểm tích lũy")
- After sign-in, user will be redirected to dashboard page

**When to set all buttons=False:**
- General information queries that can be answered directly
- Questions fully answerable using tools without human assistance

**Important:** Only one CTA button should be True at a time for clear user action path.
""",
            parameters_json_schema=self.output_schema.model_json_schema(),
        )

    @override
    async def run_async(
        self, *, args: dict[str, Any], tool_context: ToolContext
    ) -> dict[str, Any]:
        """
        Process the question_answer agent's response and return the validated dict.

        This method validates the input against the schema and formats it
        for frontend display.

        Args:
            args: The structured response data matching the output schema
            tool_context: Tool execution context (automatically provided)

        Returns:
            dict: The validated and formatted response with all CTA fields

        Raises:
            ValidationError: If args don't match the expected schema

        Example:
            >>> result = await tool.run_async(
            ...     args={
            ...         "language": "vi",
            ...         "display_mode": "simple_text",
            ...         "message": "Dạ, để hủy đơn...",
            ...         "show_support_cta_button": True
            ...     },
            ...     tool_context=context
            ... )
        """
        try:
            # Validate input against schema
            validated_response = QuestionAnswerAiResponse.model_validate(args)

            # Format output
            final_response = await QuestionAnswerAiResponseFinal.format_output(
                validated_response
            )

            # Return the formatted dict
            result = final_response.model_dump()

            logger.info(
                f"Question answer response formatted: "
                f"language={result['language']}, "
                f"show_support={result['show_support_cta_button']}, "
                f"show_checkout={result['show_proceed_to_checkout_cta_button']}, "
                f"show_signin_account={result['show_signin_for_account_cta_button']}, "
                f"show_signin_address={result['show_signin_for_address_cta_button']}, "
                f"show_signin_wishlist={result['show_signin_for_wishlist_cta_button']}, "
                f"show_signin_dashboard={result['show_signin_for_dashboard_cta_button']}"
            )

            return result

        except Exception as e:
            logger.error(f"Error formatting question answer response: {e}")
            # Return a default error response
            return {
                "language": args.get("language", "vi"),
                "message": args.get("message", ""),
                "show_cart_detail_cta_button": False,
                "show_proceed_to_checkout_cta_button": False,
                "show_support_cta_button": False,
                "show_signin_for_account_cta_button": False,
                "show_signin_for_address_cta_button": False,
                "show_signin_for_wishlist_cta_button": False,
                "show_signin_for_dashboard_cta_button": False,
            }
