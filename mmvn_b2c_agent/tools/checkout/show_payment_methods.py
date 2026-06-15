"""
Show Payment Methods Tool

Hiển thị danh sách phương thức thanh toán với links đến checkout page.

Workflow:
1. Validate delivery time còn available
2. Nếu invalid → yêu cầu chọn lại thời gian
3. Nếu valid → hiển thị payment methods với link

Usage:
- User: "thanh toán thôi"
- User: "xem phương thức thanh toán"
- User: "tôi muốn thanh toán"
"""

import logging
from typing import Optional, Dict, Any
from google.adk.tools import ToolContext

from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.tools.checkout.validate_delivery_time import validate_delivery_time

logger = logging.getLogger(__name__)


async def show_payment_methods(
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """
    Hiển thị payment methods với links đến checkout page.

    Steps:
    1. Validate delivery time (call validate_delivery_time)
    2. Nếu invalid → yêu cầu chọn lại
    3. Nếu valid → format message với payment links

    Args:
        tool_context: Tool context for session state access

    Returns:
        dict: {
            "success": bool,
            "message": str,  // Message cho user
            "instruction_for_agent": str,
            "payment_methods": [  // Danh sách payment methods
                {
                    "code": "momo",
                    "label": "Ví MoMo",
                    "url": "{store_url}/checkout"
                },
                ...
            ]
        }
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                "instruction_for_agent": "Tell user: 'Em không thể hiển thị phương thức thanh toán lúc này, anh/chị thử lại sau ạ.'",
                "code": "MISSING_TOOL_CONTEXT"
            }

        # Step 1: Validate delivery time
        validation_result = await validate_delivery_time(tool_context)

        if not validation_result.get("success"):
            # Validation API failed
            return {
                "success": False,
                "message": "Cannot validate delivery time",
                "instruction_for_agent": validation_result.get("instruction_for_agent") or "Tell user: 'Em không thể kiểm tra thông tin giao hàng, anh/chị thử lại sau ạ.'",
                "code": "VALIDATION_FAILED"
            }

        if not validation_result.get("is_valid"):
            # Delivery time is no longer available
            return {
                "success": False,
                "message": validation_result.get("message", "Delivery time invalid"),
                "instruction_for_agent": validation_result.get("instruction_for_agent") or "Tell user to select new delivery time",
                "code": "DELIVERY_TIME_INVALID"
            }

        # Step 2: Get function_call_id from tool_context (LongRunningFunctionTool)
        # This is the ID that FE uses to send back functionResponse
        function_call_id = getattr(tool_context, 'function_call_id', None)

        if not function_call_id:
            logger.error("function_call_id not found - tool should be LongRunningFunctionTool")
            return {
                "success": False,
                "message": "System error - missing function call ID",
                "instruction_for_agent": "Tell user: 'Em không thể hiển thị phương thức thanh toán lúc này, anh/chị thử lại sau ạ.'",
                "code": "MISSING_FUNCTION_CALL_ID"
            }

        # Step 3: Get checkout URL with id (from show_payment_methods tool)
        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")

        # Base checkout URL with id parameter
        base_checkout_url = f"{base_url}/checkout?step=2&chatbot=true&id={function_call_id}"

        # Step 4: Define payment methods (each with payment_method parameter)
        payment_methods = [
            {
                "code": "momo_wallet",
                "label": "• Thanh toán bằng Momo",
                "url": f"{base_checkout_url}&payment_method=momo_wallet"
            },
            {
                "code": "zalopay",
                "label": "• Thanh toán bằng ZaloPay",
                "url": f"{base_checkout_url}&payment_method=zalopay"
            },
            {
                "code": "vnpay",
                "label": "• Thanh toán bằng thẻ ngân hàng",
                "url": f"{base_checkout_url}&payment_method=vnpay"
            },
            {
                "code": "cashondelivery",
                "label": "• Thanh toán khi nhận hàng",
                "url": f"{base_checkout_url}&payment_method=cashondelivery"
            }
        ]

        # Step 5: Format message - Link embedded directly in label text
        # ✅ Format: "[Label](url)" - Markdown link format
        message_lines = ["Dạ, bước cuối cùng rồi ạ. Anh/Chị vui lòng chọn các PTTT dưới đây để thực hiện thanh toán nhé:\n"]
        for method in payment_methods:
            # Each line: "[Label](url)" - link embedded in text
            message_lines.append(f"[{method['label']}]({method['url']})")

        # Use double newline for proper markdown line breaks
        message = "\n\n".join(message_lines)

        logger.info(f"Payment methods displayed with base URL: {base_checkout_url}")
        logger.info(f"Waiting for place order completion with id: {function_call_id}")

        return {
            "status": "pending",  # Wait for FE to send functionResponse after place order
            "message": message,
            "instruction_for_agent": (
                f"Display the payment methods message to user. "
                f"Each payment method line contains a clickable link to {base_checkout_url} with id={function_call_id} and payment_method parameter. "
                f"User can click any link to go to checkout page step 2 and complete payment. "
                f"After user completes order, FE will send functionResponse with order details (order_id, order_number, grand_total, payment_method)."
            ),
            "payment_methods": payment_methods,
            "checkout_url": base_checkout_url,
            "id": function_call_id
        }

    except Exception as e:
        logger.error(f"Error in show_payment_methods: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "instruction_for_agent": "Tell user: 'Em không thể hiển thị phương thức thanh toán lúc này, anh/chị thử lại sau ạ.'",
            "code": "UNEXPECTED_ERROR"
        }
