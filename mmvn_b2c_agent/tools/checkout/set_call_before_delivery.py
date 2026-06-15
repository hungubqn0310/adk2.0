"""
Set Call Before Delivery Tool

Bật/tắt chức năng "gọi trước khi giao hàng".
API: setCallBeforeDeliveryOnCart mutation

Usage:
- User chat: "gọi trước khi giao"
- User chat: "nhớ gọi điện trước khi giao hàng nhé"
- User chat: "không cần gọi trước"
"""

import logging
from typing import Optional, Dict, Any
from google.adk.tools import ToolContext

from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.tools.utils import make_graphql_request_async

logger = logging.getLogger(__name__)


async def set_call_before_delivery(
    enabled: bool,
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """
    Bật/tắt "gọi trước khi giao hàng" cho cart.

    Args:
        enabled: True = bật, False = tắt
                 IMPORTANT: Khi user nói "gọi trước khi giao", "nhớ gọi trước", "gọi điện trước"
                           → enabled=True
                           Khi user nói "không cần gọi", "đừng gọi"
                           → enabled=False
        tool_context: Tool context for session state access

    Returns:
        dict: {
            "success": bool,
            "message": str,
            "instruction_for_agent": str,
            "data": {
                "is_call_before_delivery": bool
            }
        }
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                "instruction_for_agent": "Tell user: 'Em không thể cập nhật thông tin lúc này, anh/chị thử lại sau ạ.'",
                "code": "MISSING_TOOL_CONTEXT"
            }

        # Get session data
        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        signin_token = (magento_session_data.get("signin_token") or "").strip('"')
        magento_cart_id = (magento_session_data.get("magento_cart_id") or "").strip('"')

        if not magento_cart_id:
            return {
                "success": False,
                "message": "Cart ID missing",
                "instruction_for_agent": "Tell user: 'Dạ, hiện tại giỏ hàng của anh/chị đang trống, anh chị muốn tìm kiếm sản phẩm nào bên em ạ.'",
                "code": "MISSING_CART_ID"
            }

        # Call setCallBeforeDeliveryOnCart mutation
        mutation = """
            mutation SetCallBeforeDelivery($cartId: String!, $enabled: Boolean!) {
                setCallBeforeDeliveryOnCart(
                    input: {
                        cart_id: $cartId
                        is_call_before_delivery: $enabled
                    }
                ) {
                    cart {
                        id
                        is_call_before_delivery
                    }
                }
            }
        """

        variables = {
            "cartId": magento_cart_id,
            "enabled": enabled
        }

        res = await make_graphql_request_async(
            mutation,
            variables,
            base_url,
            store_id,
            auth_token=signin_token or None
        )

        if not res or not res.get("data"):
            logger.error(f"Failed to set call_before_delivery: {res}")
            error_message = "Unknown error"
            if res and res.get("errors"):
                error_message = res["errors"][0].get("message", "Unknown error")

            return {
                "success": False,
                "message": f"API error: {error_message}",
                "instruction_for_agent": f"Tell user: 'Em không thể cập nhật thông tin lúc này. Lỗi: {error_message}'",
                "code": "API_ERROR"
            }

        # Success
        updated_value = res["data"]["setCallBeforeDeliveryOnCart"]["cart"]["is_call_before_delivery"]

        logger.info(f"Successfully set call_before_delivery={updated_value} for cart {magento_cart_id}")

        if updated_value:
            message_to_user = "Dạ, em đã lưu yêu cầu gọi điện trước khi giao hàng ạ."
        else:
            message_to_user = "Dạ, em đã tắt tính năng gọi điện trước khi giao hàng ạ."

        return {
            "success": True,
            "message": f"Call before delivery set to: {updated_value}",
            "instruction_for_agent": f"Tell user: '{message_to_user}'",
            "data": {
                "is_call_before_delivery": updated_value
            }
        }

    except Exception as e:
        logger.error(f"Error in set_call_before_delivery: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "instruction_for_agent": "Tell user: 'Em không thể cập nhật thông tin lúc này, anh/chị thử lại sau ạ.'",
            "code": "UNEXPECTED_ERROR"
        }
