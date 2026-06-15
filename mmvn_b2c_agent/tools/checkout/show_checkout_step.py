"""
Single Popup Checkout Tool

This tool triggers a single checkout popup with all required and optional fields.

NEW WORKFLOW (Single Popup):
- Popup: All fields (required: recipient, address, delivery time; optional: note, mcard, checkboxes)
- Additional info can be added via chat after popup submission
- Payment methods shown via separate tool (show_payment_methods)

Frontend handles:
- Display single popup with all fields
- Prefill data (logged-in: from M2, guest: from session)
- Send required fields to M2 API
- Send functionResponse to AI after submit

AI handles:
- Guide message for popup
- Trigger popup
- Receive functionResponse
- Handle additional info via chat (set_delivery_comment, set_call_before_delivery, etc.)
- Show payment methods via show_payment_methods tool
"""

import logging
from typing import Optional, Dict, Any, List
from google.adk.tools import ToolContext, LongRunningFunctionTool

logger = logging.getLogger(__name__)


async def show_checkout_step(
    step: str,
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """
    Trigger checkout popup (single popup with all fields).

    Args:
        step: Use "main_info" to show the checkout popup
              (Other step values are deprecated)
        tool_context: Tool context for session state access

    Returns:
        dict: {
            "status": "pending",  // Waits for functionResponse from FE
            "action_type": "show_checkout_popup",
            "step": "main_info",
            "step_number": 1,
            "total_steps": 1,
            "message": "Guide message for user",
            "fields": [...]  // All fields (required + optional)
        }

    Frontend Flow:
        1. Receive functionCall with popup info
        2. Show popup with all fields (popup has 2 buttons: "Xác nhận" & "Hủy")
        3. Prefill data:
           - Logged-in: Fetch from M2 default_address
           - Guest: Fetch from session (if available)
        4. User action:
           a) User clicks "Xác nhận":
              → Validate form
              → Send to M2
              → Send functionResponse(status: "done")
           b) User clicks "Hủy":
              → Send functionResponse(status: "cancelled")
              → AI will clear checkout state
        5. Optional fields can be filled via chat later (only if status="done")

    NEW WORKFLOW:
        - Only 1 popup (step="main_info")
        - Additional info via chat tools: set_delivery_comment, set_call_before_delivery, etc.
        - Payment methods shown via show_payment_methods tool
    """
    try:
        # Get session data
        magento_session_data = (tool_context.state.get('state') or {}).get("magento_session_data", {})
        if not magento_session_data:
            magento_session_data = tool_context.state.get("magento_session_data", {})

        cart_id = (magento_session_data.get("magento_cart_id") or "").strip('"')
        signin_token = (magento_session_data.get("signin_token") or "").strip('"')

        if not cart_id:
            return {
                "status": "error",
                "error": "NO_CART_ID",
                "message": "Dạ, hiện tại giỏ hàng của anh/chị đang trống, anh chị muốn tìm kiếm sản phẩm nào bên em ạ."
            }

        user_type = "logged_in" if signin_token else "guest"

        # Check if user is RE-OPENING checkout popup (they opened it before)
        is_reopen = tool_context.state.get("in_checkout_flow", False)
        delivery_time_warning = None

        if is_reopen:
            # Validate if previous delivery time selection is still available
            from .validate_delivery_time import validate_delivery_time
            validation = await validate_delivery_time(tool_context)

            # Only warn if delivery time was selected but is now EXPIRED
            # Don't warn if user hasn't selected yet (NO_DELIVERY_TIME_SET)
            if not validation.get("is_valid") and validation.get("code") != "NO_DELIVERY_TIME_SET":
                delivery_time_warning = (
                    "⚠️ Khung giờ giao hàng anh/chị đã chọn trước đó không còn khả dụng. "
                    "Vui lòng chọn lại khung giờ mới trong popup ạ."
                )
                logger.info(f"Delivery time validation on re-open: {validation.get('code')}")

        # Define step configurations (NEW: Single popup only)
        step_configs = {
            "main_info": {
                "step_number": 1,
                "total_steps": 1,
                "message_guest": """Để em có thể tạo đơn cho anh/chị, vui lòng kiểm tra và điền đầy đủ các thông tin sau nhé:

**• Thông tin nhận hàng (Người nhận - Email - SĐT)**

**• Địa chỉ nhận hàng (Tỉnh/TP - Quận/Huyện - Phường/Xã - Địa chỉ chi tiết)**

**• Ngày giao hàng & Khung giờ giao**

Có thể bổ sung (không bắt buộc):

**• Ghi chú cho đơn hàng**

**• Yêu cầu "Gọi trước khi giao"**

**• Yêu cầu xuất hóa đơn VAT**

**• Nhập mã MCard hoặc mã khách hàng (nếu có)**

Anh/Chị vui lòng bấm vào nút bên dưới để **Nhập thông tin đặt hàng** giúp em ạ.""",
                "message_logged_in": """Để em có thể tạo đơn cho anh/chị, vui lòng kiểm tra và điền đầy đủ các thông tin sau nhé:

**• Thông tin nhận hàng (Người nhận - Email - SĐT)**

**• Địa chỉ nhận hàng (Tỉnh/TP - Quận/Huyện - Phường/Xã - Địa chỉ chi tiết)**

**• Ngày giao hàng & Khung giờ giao**

Có thể bổ sung (không bắt buộc):

**• Ghi chú cho đơn hàng**

**• Yêu cầu "Gọi trước khi giao"**

**• Yêu cầu xuất hóa đơn VAT**

**• Nhập mã MCard hoặc mã khách hàng (nếu có)**

Anh/Chị vui lòng bấm vào nút bên dưới để **Nhập thông tin đặt hàng** giúp em ạ.""",
                "fields": [
                    # Required fields
                    "recipient_name",
                    "email",
                    "phone",
                    "street",
                    "city_code",
                    "ward_code",
                    "delivery_date",
                    "delivery_time_id",
                    # Optional fields
                    "note",
                    "mcard_number",
                    "call_before_delivery",
                    "issue_vat_invoice"
                ]
            }
        }

        if step not in step_configs:
            return {
                "status": "error",
                "error": "INVALID_STEP",
                "message": f"Invalid checkout step: {step}"
            }

        config = step_configs[step]

        # Select message based on user type
        message_key = f"message_{user_type}"
        message = config.get(message_key, config["message_guest"])

        # Mark checkout stage in state
        tool_context.state["in_checkout_flow"] = True
        tool_context.state["checkout_stage"] = step
        tool_context.state["checkout_step_number"] = config["step_number"]

        # Build response
        response = {
            "status": "pending",  # Wait for functionResponse from FE
            "display_mode": "checkout",  # Display mode for FE (consistent with other agents)
            "action_type": "show_checkout_popup",
            "step": step,
            "step_number": config["step_number"],
            "total_steps": config["total_steps"],
            "message": message,
            "fields": config["fields"],
            "user_type": user_type,
            "cart_id": cart_id,
            "show_checkout_popup_button": True,  # Signal FE to show button
            "delivery_time_warning": delivery_time_warning,  # Warning if delivery time expired
            "instruction_for_agent": (
                "A checkout popup has been triggered for the user to fill in delivery information. "
                "Inform the user to complete the required information in the popup. "
                f"Tell user: '{message}'"
                + (f" IMPORTANT: Also tell user: '{delivery_time_warning}'" if delivery_time_warning else "")
            )
        }

        logger.info(f"Checkout popup '{step}' triggered for cart {cart_id}, user_type: {user_type}")

        return response

    except Exception as e:
        logger.error(f"Error in show_checkout_step: {e}", exc_info=True)
        return {
            "status": "error",
            "error": "SYSTEM_ERROR",
            "message": "Có lỗi khi khởi tạo thanh toán. Vui lòng thử lại sau."
        }


# Export as LongRunningFunctionTool
ShowCheckoutStepTool = LongRunningFunctionTool(show_checkout_step)
