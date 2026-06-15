"""
Set MCard Tool

Set mã thành viên MCard cho cart.
API: setCustomerNoOnCart mutation

Usage:
- User chat: "mã mcard 2221000035830016"
- User chat: "mã thẻ thành viên 123456789"
- User chat: "customer number: 2221000035830016"
"""

import logging
from typing import Optional, Dict, Any
from google.adk.tools import ToolContext

from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.tools.utils import make_graphql_request_async

logger = logging.getLogger(__name__)


async def set_mcard(
    customer_no: str,
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """
    Set mã thành viên MCard cho cart.

    Args:
        customer_no: Mã MCard (VD: "2221000035830016")
        tool_context: Tool context for session state access

    Returns:
        dict: {
            "success": bool,
            "message": str,
            "instruction_for_agent": str,
            "data": {
                "customer_no": str
            }
        }
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                "instruction_for_agent": "Tell user: 'Em không thể cập nhật mã MCard lúc này, anh/chị thử lại sau ạ.'",
                "code": "MISSING_TOOL_CONTEXT"
            }

        # Validate customer_no format
        if not customer_no or not customer_no.strip():
            return {
                "success": False,
                "message": "Invalid MCard number",
                "instruction_for_agent": "Tell user: 'Mã MCard không hợp lệ. Vui lòng kiểm tra lại ạ.'",
                "code": "INVALID_MCARD"
            }

        customer_no = customer_no.strip()

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
                "instruction_for_agent": "Tell user: ' Dạ, hiện tại giỏ hàng của anh/chị đang trống, anh chị muốn tìm kiếm sản phẩm nào bên em ạ.'",
                "code": "MISSING_CART_ID"
            }

        # Call setCustomerNoOnCart mutation
        mutation = """
            mutation SetMCard($cartId: String!, $customerNo: String!) {
                setCustomerNoOnCart(
                    input: {
                        cart_id: $cartId
                        customer_no: $customerNo
                    }
                ) {
                    cart {
                        id
                        customer_no
                    }
                }
            }
        """

        variables = {
            "cartId": magento_cart_id,
            "customerNo": customer_no
        }

        res = await make_graphql_request_async(
            mutation,
            variables,
            base_url,
            store_id,
            auth_token=signin_token or None
        )

        if not res or not res.get("data"):
            logger.error(f"Failed to set MCard: {res}")
            error_message = "Unknown error"
            if res and res.get("errors"):
                error_message = res["errors"][0].get("message", "Unknown error")

            return {
                "success": False,
                "message": f"API error: {error_message}",
                "instruction_for_agent": f"Tell user: 'Em không thể cập nhật mã MCard lúc này. Lỗi: {error_message}'",
                "code": "API_ERROR"
            }

        # Success - Extract updated customer_no with null-safe checks
        data = res.get("data") or {}
        mcard_response = data.get("setCustomerNoOnCart", {}) or {}
        cart_data_from_response = mcard_response.get("cart", {}) or {}

        if not cart_data_from_response:
            logger.warning(f"Mutation succeeded but cart is null in response: {res}")
            # Use the customer_no we sent since we can't verify from response
            updated_customer_no = customer_no
        else:
            updated_customer_no = cart_data_from_response.get("customer_no", customer_no)

        logger.info(f"Successfully set MCard {updated_customer_no} for cart {magento_cart_id}")

        return {
            "success": True,
            "message": f"MCard set: {updated_customer_no}",
            "instruction_for_agent": f"Tell user: 'Dạ, em đã lưu mã MCard {updated_customer_no} ạ.'",
            "data": {
                "customer_no": updated_customer_no
            }
        }

    except Exception as e:
        logger.error(f"Error in set_mcard: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "instruction_for_agent": "Tell user: 'Em không thể cập nhật mã MCard lúc này, anh/chị thử lại sau ạ.'",
            "code": "UNEXPECTED_ERROR"
        }
