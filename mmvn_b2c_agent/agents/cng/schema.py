import mmvn_b2c_agent.tools
import mmvn_b2c_agent.agents.cng_product.schema as cng_product_schema
import mmvn_b2c_agent.tools.cng.cart as cart_tools
import mmvn_b2c_agent.tools.cng.common
import logging
from enum import Enum
from typing import Optional, Any
from google.adk.tools import ToolContext
from pydantic import BaseModel, Field

get_current_cart_from_session_state = mmvn_b2c_agent.tools.cng.common.get_current_cart_from_session_state

logger = logging.getLogger(__name__)


class ProductSkuNotFoundError(Exception):
    """Custom exception when a product SKU is not found in the search history"""
    pass


class CngProductSearchProductData(BaseModel):
    name: str
    sku: str
    art_no: Optional[str] = None
    price: Optional[str] = None
    unit: Optional[str] = None

    # Discount and promotion fields (optional, only present if product has promotions)
    regular_price: Optional[str] = None
    discounted_amount: Optional[str] = None
    discount_percent: Optional[str] = None
    dnr_info: Optional[list[dict[str, Any]]] = None


class CngCartItemData(BaseModel):
    id: str
    product: CngProductSearchProductData
    quantity: int
    price_per_item_including_tax: Optional[str | None]
    row_applied_discount: Optional[str | None]
    row_total_including_tax: Optional[str | None]
    row_total_discounts: Optional[str | None]


class CngCartData(BaseModel):
    items: list[CngCartItemData]
    total_quantity: Optional[float | None]
    total_summary_quantity_including_config: Optional[int | None]
    cart_subtotal_including_tax: Optional[str | None]
    cart_grand_total: Optional[str | None]
    cart_discounts: Optional[str | None]


class DisplayMode(str, Enum):
    SIMPLE_TEXT = 'simple_text'
    MARKDOWN = 'markdown'
    PRODUCT = 'product'
    CART = 'cart'
    FAQ = 'FAQ'
    ORDER = 'order'
    CHECKOUT = 'checkout'


class CngProductSearchAiResponse(BaseModel):
    language: str = Field(
        description="The language the user actually TYPED (ISO code, e.g. 'vi', 'en'). Determine ONLY from typed text. "
                    "If the user only uploaded an image/file and typed no text, set 'vi' (Vietnamese) — never infer the language from text printed on the image/product packaging (e.g. English words on a product photo are NOT the user's language). "
                    "The `message` MUST be written in this language."
    )
    display_mode: DisplayMode = Field(
        description="The display mode of the response based on the chat context and the expected output. "
                    "If the output is about product search results, use `product` display mode to show product details. "
                    "If the output is about cart details, use `cart` to show cart information. "
                    "Else, use `simple_text` for general text responses, in this mode, no product or cart details will be shown."
                    # "If `simple_text`, the response will be displayed as plain text, with the message only. If `product` or `cart`, the products or cart details will be displayed in the middle.",
    )
    message: str = Field(
        description="The opening part of the response: a friendly, informative, engaging and helpful opening line, written in `language` (default Vietnamese). "
                    "Vietnamese example: 'Dạ, em xin giới thiệu các sản phẩm sau ạ:'. English example (only when the user typed English): 'Here are some products matched your description:'. "
                    "Other examples: 'Product was added to your cart successfully...', 'Your cart is empty.'"
    )
    product_skus: list[str] = Field(
        default=[],
        description="The middle part of the response, containing sku of product(s) that will be show in product display mode, SKU format is two integers with underscore (e.g., `441976_24419765`)."
    )

    show_cart_detail_cta_button: bool = Field(default=False,
                                              description="Whether to show the cart detail button. Do NOT show if the cart is empty.")
    show_proceed_to_checkout_cta_button: bool = Field(default=False,
                                                      description="Whether to show the proceed to checkout button. Do NOT show if the cart is empty.")

    show_view_order_details_cta_button: bool = Field(
        default=False,
        description="Whether to show the 'View Order Details' button. TRUE when specific order is successfully found."
    )
    show_reorder_cta_button: bool = Field(
        default=False,
        description="Whether to show the 'Re-order' button. TRUE when order is successfully found AND status is 'complete' or 'delivered'."
    )
    show_signin_for_order_cta_button: bool = Field(
        default=False,
        description="Whether to show the 'Sign In' button. TRUE when guest user needs to login to view their orders."
    )
    show_register_cta_button: bool = Field(
        default=False,
        description="Whether to show the 'Register/Sign Up' button. TRUE when user explicitly says they don't have account or wants to create new account."
    )
    show_support_cta_button: bool = Field(
        default=False,
        description="Whether to show the 'Contact Support' button. TRUE when user needs staff assistance for: "
                    "order cancellation requests ('muốn hủy đơn', 'hủy đơn hàng', 'cancel my order'), "
                    "refund requests, complaints, or any issue requiring human support."
    )

    # Checkout multi-step popup fields
    show_checkout_popup_button: bool = Field(
        default=False,
        description="Whether to show checkout popup button. TRUE when user wants to checkout/place order."
    )
    auto_open_checkout_popup: bool = Field(
        default=False,
        description="Auto-open checkout popup without requiring button click. "
                    "TRUE ONLY when: 1) in_checkout_flow=true AND 2) user explicitly asks to open popup "
                    "(e.g., 'mở popup', 'mở form', 'điền thông tin giao hàng', 'open popup'). "
                    "When TRUE, FE will automatically display checkout popup."
    )
    checkout_step: Optional[str] = Field(
        default=None,
        description="Current checkout step: 'main_info' (single popup). Set when triggering checkout popup."
    )
    checkout_step_number: Optional[int] = Field(
        default=None,
        description="Current step number (always 1 for single popup). Set when triggering checkout popup."
    )
    checkout_total_steps: int = Field(
        default=1,
        description="Total number of checkout steps (single popup checkout)."
    )

    # File upload response flag
    is_file_upload_response: bool = Field(
        default=False,
        description="TRUE when this response is processing/answering about an uploaded file (PDF, image). "
                    "Set TRUE when user has uploaded a file and AI is responding to that file content."
    )


class CngProductSearchAiResponseFinal(BaseModel):
    language: str
    display_mode: DisplayMode
    message: str
    cart_data: Optional[CngCartData | dict] = None
    product_data: Optional[list[CngProductSearchProductData]] = []
    order_data: Optional[dict[str, Any]] = None

    show_cart_detail_cta_button: bool = False
    show_proceed_to_checkout_cta_button: bool = False

    # Order tracking CTA buttons
    show_order_management_cta_button: bool = False
    show_view_order_details_cta_button: bool = False
    show_reorder_cta_button: bool = False
    show_signin_for_order_cta_button: bool = False
    show_register_cta_button: bool = False
    show_support_cta_button: bool = False

    # Checkout popup fields (single popup)
    show_checkout_popup_button: bool = False
    auto_open_checkout_popup: bool = False
    checkout_step: Optional[str] = None
    checkout_step_number: Optional[int] = None
    checkout_total_steps: int = 1

    # File upload response flag
    is_file_upload_response: bool = False

    @classmethod
    def from_search_output(cls, initial_output: cng_product_schema.ProductSearchOutputSchema | dict[str, Any],
                           state: dict | ToolContext) -> "CngProductSearchAiResponseFinal":
        """Convert from initial output schema to final output schema by looking up product details."""
        product_details = []
        if isinstance(initial_output, BaseModel):
            initial_output = initial_output.model_dump()
        if initial_output.get('product_skus'):
            for sku in initial_output.get('product_skus'):
                product_data = mmvn_b2c_agent.tools.get_product_details_from_search_history(sku, state)
                if product_data:
                    product_details.append(product_data)
        return cls(
            language=initial_output.get('language') or initial_output.get('user_language', 'vi'),
            display_mode=DisplayMode.PRODUCT,
            message=initial_output.get('message'),
            product_data=product_details,
            show_cart_detail_cta_button=False,
            show_proceed_to_checkout_cta_button=False,
        )

    @classmethod
    async def format_output(cls,
                            initial_output: CngProductSearchAiResponse | cng_product_schema.ProductSearchOutputSchema |
                                            dict[str, Any],
                            tool_context: ToolContext) -> "CngProductSearchAiResponseFinal":
        """Format the output by looking up product details, cart details, and order details."""
        # Safely access order data from state
        state_dict = tool_context.state.get('state', {}) if hasattr(tool_context, 'state') and tool_context.state else {}
        order_data_from_state = state_dict.get('last_order_result')

        # Note: start_date and end_date are kept inside order_data.data
        # (same level as order_status_filter, page_info, total_count)

        # Clear order data after reading (if it exists)
        # COMMENTED OUT: This was breaking the order context reuse feature in check_orders.py:828-843
        # The reuse logic needs last_order_result to persist across multiple queries in the same session
        # if order_data_from_state and hasattr(tool_context, 'state') and tool_context.state and 'state' in tool_context.state:
        #     tool_context.state['state']['last_order_result'] = None
        mode = initial_output.get('display_mode') if isinstance(initial_output, dict) else initial_output.display_mode
        msg = initial_output.get('message') if isinstance(initial_output, dict) else initial_output.message
        lang = initial_output.get('language') if isinstance(initial_output, dict) else initial_output.language
        show_cart_btn = initial_output.get('show_cart_detail_cta_button') if isinstance(initial_output, dict) else initial_output.show_cart_detail_cta_button
        show_checkout_btn = initial_output.get('show_proceed_to_checkout_cta_button') if isinstance(initial_output, dict) else initial_output.show_proceed_to_checkout_cta_button

        # Extract order tracking CTA buttons
        show_order_mgmt_btn = initial_output.get('show_order_management_cta_button', False) if isinstance(initial_output, dict) else getattr(initial_output, 'show_order_management_cta_button', False)
        show_view_order_btn = initial_output.get('show_view_order_details_cta_button', False) if isinstance(initial_output, dict) else getattr(initial_output, 'show_view_order_details_cta_button', False)
        show_reorder_btn = initial_output.get('show_reorder_cta_button', False) if isinstance(initial_output, dict) else getattr(initial_output, 'show_reorder_cta_button', False)
        show_signin_order_btn = initial_output.get('show_signin_for_order_cta_button', False) if isinstance(initial_output, dict) else getattr(initial_output, 'show_signin_for_order_cta_button', False)
        show_register_btn = initial_output.get('show_register_cta_button', False) if isinstance(initial_output, dict) else getattr(initial_output, 'show_register_cta_button', False)
        show_support_btn = initial_output.get('show_support_cta_button', False) if isinstance(initial_output, dict) else getattr(initial_output, 'show_support_cta_button', False)

        # Extract checkout popup fields
        show_checkout_popup_btn = initial_output.get('show_checkout_popup_button', False) if isinstance(initial_output, dict) else getattr(initial_output, 'show_checkout_popup_button', False)
        auto_open_checkout = initial_output.get('auto_open_checkout_popup', False) if isinstance(initial_output, dict) else getattr(initial_output, 'auto_open_checkout_popup', False)
        checkout_step = initial_output.get('checkout_step', None) if isinstance(initial_output, dict) else getattr(initial_output, 'checkout_step', None)
        checkout_step_number = initial_output.get('checkout_step_number', None) if isinstance(initial_output, dict) else getattr(initial_output, 'checkout_step_number', None)
        checkout_total_steps = initial_output.get('checkout_total_steps', 3) if isinstance(initial_output, dict) else getattr(initial_output, 'checkout_total_steps', 3)

        # Extract file upload response flag
        is_file_upload_resp = initial_output.get('is_file_upload_response', False) if isinstance(initial_output, dict) else getattr(initial_output, 'is_file_upload_response', False)
        if mode == DisplayMode.SIMPLE_TEXT or mode == DisplayMode.FAQ or mode == DisplayMode.MARKDOWN:
            current_cart_state = get_current_cart_from_session_state(tool_context)
            cart_detail = current_cart_state.get('cart_raw_data') if current_cart_state else {}
            return cls(
                language=lang,
                display_mode=mode,
                message=msg,
                cart_data=cart_detail,
                order_data=order_data_from_state,
                show_cart_detail_cta_button=show_cart_btn if cart_detail.get('items') else False,
                show_proceed_to_checkout_cta_button=show_checkout_btn,
                # Order tracking CTA buttons
                show_order_management_cta_button=show_order_mgmt_btn,
                show_view_order_details_cta_button=show_view_order_btn,
                show_reorder_cta_button=show_reorder_btn,
                show_signin_for_order_cta_button=show_signin_order_btn,
                show_register_cta_button=show_register_btn,
                show_support_cta_button=show_support_btn,
                # Checkout popup fields
                show_checkout_popup_button=show_checkout_popup_btn,
                auto_open_checkout_popup=auto_open_checkout,
                checkout_step=checkout_step,
                checkout_step_number=checkout_step_number,
                checkout_total_steps=checkout_total_steps,
                # File upload response flag
                is_file_upload_response=is_file_upload_resp,
            )
        elif mode == DisplayMode.PRODUCT:
            final_response = cls.from_search_output(initial_output, tool_context)
            final_response.order_data = order_data_from_state
            # Set order tracking CTA buttons
            final_response.show_order_management_cta_button = show_order_mgmt_btn
            final_response.show_view_order_details_cta_button = show_view_order_btn
            final_response.show_reorder_cta_button = show_reorder_btn
            final_response.show_signin_for_order_cta_button = show_signin_order_btn
            final_response.show_register_cta_button = show_register_btn
            final_response.show_support_cta_button = show_support_btn
            # Set checkout popup fields
            final_response.show_checkout_popup_button = show_checkout_popup_btn
            final_response.auto_open_checkout_popup = auto_open_checkout
            final_response.checkout_step = checkout_step
            final_response.checkout_step_number = checkout_step_number
            final_response.checkout_total_steps = checkout_total_steps
            # Set file upload response flag
            final_response.is_file_upload_response = is_file_upload_resp
            return final_response
        elif mode == DisplayMode.CART:
            cart_detail = get_current_cart_from_session_state(tool_context).get('cart_raw_data')
            if not cart_detail:
                await cart_tools.view_cart()
                cart_detail = get_current_cart_from_session_state(tool_context).get('cart_raw_data')
            return cls(
                language=lang,
                display_mode=mode,
                message=msg,
                cart_data=cart_detail,
                order_data=order_data_from_state,
                show_cart_detail_cta_button=show_cart_btn,
                show_proceed_to_checkout_cta_button=show_checkout_btn,
                # Order tracking CTA buttons
                show_order_management_cta_button=show_order_mgmt_btn,
                show_view_order_details_cta_button=show_view_order_btn,
                show_reorder_cta_button=show_reorder_btn,
                show_signin_for_order_cta_button=show_signin_order_btn,
                show_register_cta_button=show_register_btn,
                show_support_cta_button=show_support_btn,
                # Checkout popup fields
                show_checkout_popup_button=show_checkout_popup_btn,
                auto_open_checkout_popup=auto_open_checkout,
                checkout_step=checkout_step,
                checkout_step_number=checkout_step_number,
                checkout_total_steps=checkout_total_steps,
                # File upload response flag
                is_file_upload_response=is_file_upload_resp,
            )

        elif mode == DisplayMode.ORDER:
            current_cart_state = get_current_cart_from_session_state(tool_context)
            cart_detail = current_cart_state.get('cart_raw_data') if current_cart_state else {}
            return cls(
                language=lang,
                display_mode=mode,
                message=msg,
                cart_data=cart_detail,
                order_data=order_data_from_state,
                show_cart_detail_cta_button=show_cart_btn if cart_detail.get('items') else False,
                show_proceed_to_checkout_cta_button=show_checkout_btn,
                # Order tracking CTA buttons
                show_order_management_cta_button=show_order_mgmt_btn,
                show_view_order_details_cta_button=show_view_order_btn,
                show_reorder_cta_button=show_reorder_btn,
                show_signin_for_order_cta_button=show_signin_order_btn,
                show_support_cta_button=show_support_btn,
                # Checkout popup fields
                show_checkout_popup_button=show_checkout_popup_btn,
                auto_open_checkout_popup=auto_open_checkout,
                checkout_step=checkout_step,
                checkout_step_number=checkout_step_number,
                checkout_total_steps=checkout_total_steps,
                # File upload response flag
                is_file_upload_response=is_file_upload_resp,
            )
        else:
            raise NotImplementedError(f"Invalid display mode: {mode}")

    def model_dump(self, *args, **kwargs):
        """Ensure CTA buttons return proper boolean values and date fields are safe."""
        data = super().model_dump(*args, **kwargs)
        if data.get("show_cart_detail_cta_button") is None:
            data["show_cart_detail_cta_button"] = False
        if data.get("show_proceed_to_checkout_cta_button") is None:
            data["show_proceed_to_checkout_cta_button"] = False
        # Order tracking CTA buttons
        if data.get("show_order_management_cta_button") is None:
            data["show_order_management_cta_button"] = False
        if data.get("show_view_order_details_cta_button") is None:
            data["show_view_order_details_cta_button"] = False
        if data.get("show_reorder_cta_button") is None:
            data["show_reorder_cta_button"] = False
        if data.get("show_signin_for_order_cta_button") is None:
            data["show_signin_for_order_cta_button"] = False
        if data.get("show_support_cta_button") is None:
            data["show_support_cta_button"] = False
        return data


class CngShippingAiResponseFinal(CngProductSearchAiResponseFinal):
    """
    Schema phản hồi cho module Shipping — kế thừa cấu trúc từ CngProductSearchAiResponseFinal.
    """
    pass


class CngOrderItemData(BaseModel):
    """Schema for individual order item"""
    id: str
    product: CngProductSearchProductData
    quantity: int
    price_per_item_including_tax: Optional[str | None]
    row_applied_discount: Optional[str | None]
    row_total_including_tax: Optional[str | None]
    row_total_discounts: Optional[str | None]


class CngOrderData(BaseModel):
    """Schema for order data - similar structure to cart but for completed orders"""
    order_id: str = Field(description="Unique order identifier")
    order_status: Optional[str | None] = Field(default=None, description="Order status: pending, processing, completed, cancelled, etc.")
    items: list[CngOrderItemData] = Field(description="List of items in the order")
    total_quantity: Optional[float | None] = Field(default=None, description="Total quantity of items in order")
    total_summary_quantity_including_config: Optional[int | None] = Field(default=None)
    order_subtotal_including_tax: Optional[str | None] = Field(default=None, description="Order subtotal including tax")
    order_grand_total: Optional[str | None] = Field(default=None, description="Order grand total")
    order_discounts: Optional[str | None] = Field(default=None, description="Total discounts applied")
    shipping_cost: Optional[str | None] = Field(default=None, description="Shipping cost")
    tax_amount: Optional[str | None] = Field(default=None, description="Tax amount")
    order_date: Optional[str | None] = Field(default=None, description="Order creation date")
    shipping_address: Optional[dict[str, Any] | None] = Field(default=None, description="Shipping address details")
    billing_address: Optional[dict[str, Any] | None] = Field(default=None, description="Billing address details")
    payment_method: Optional[str | None] = Field(default=None, description="Payment method used")