"""
Set Delivery Comment Tool

Set ghi chú giao hàng cho cart.
API: setShippingMethodsOnCart mutation với delivery_date.comment

Usage:
- User chat: "ghi chú: giao buổi sáng"
- User chat: "comment: please call before delivery"
"""

import logging
from typing import Optional, Dict, Any
from google.adk.tools import ToolContext

from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.tools.utils import make_graphql_request_async

logger = logging.getLogger(__name__)


async def set_delivery_comment(
    comment: str,
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """
    Set ghi chú giao hàng cho cart.

    Args:
        comment: Ghi chú từ user (VD: "giao buổi sáng", "gọi trước 30 phút")
        tool_context: Tool context for session state access

    Returns:
        dict: {
            "success": bool,
            "message": str,
            "instruction_for_agent": str
        }

    Note:
        - API yêu cầu có delivery_date.date và delivery_date.time_interval_id
        - Nếu chưa có → lấy từ cart hiện tại
        - Nếu cart chưa có delivery time → báo lỗi
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                "instruction_for_agent": "Tell user: 'Em không thể lưu ghi chú lúc này, anh/chị thử lại sau ạ.'",
                "code": "MISSING_TOOL_CONTEXT"
            }

        # Get session data - try nested 'state' first, then root level
        magento_session_data = (tool_context.state.get('state') or {}).get("magento_session_data", {})
        if not magento_session_data:
            magento_session_data = tool_context.state.get("magento_session_data", {})

        store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        signin_token = (magento_session_data.get("signin_token") or "").strip('"')
        magento_cart_id = (magento_session_data.get("magento_cart_id") or "").strip('"')

        logger.info(f"set_delivery_comment: cart_id={magento_cart_id}, store_id={store_id}, base_url={base_url}")

        if not magento_cart_id:
            return {
                "success": False,
                "message": "Cart ID missing",
                "instruction_for_agent": "Tell user: 'Dạ, hiện tại giỏ hàng của anh/chị đang trống, anh chị muốn tìm kiếm sản phẩm nào bên em ạ.'",
                "code": "MISSING_CART_ID"
            }

        # Step 1: Get current delivery_date, time_interval_id and shipping_method from cart
        cart_query = """
            query GetCartDeliveryInfo($cartId: String!) {
                cart(cart_id: $cartId) {
                    id
                    delivery_date {
                        date
                        time_interval_id
                        comment
                    }
                    shipping_addresses {
                        selected_shipping_method {
                            carrier_code
                            method_code
                        }
                        available_shipping_methods {
                            carrier_code
                            method_code
                        }
                    }
                }
            }
        """

        cart_res = await make_graphql_request_async(
            cart_query,
            {"cartId": magento_cart_id},
            base_url,
            store_id,
            auth_token=signin_token or None
        )

        # Null-safe checks to prevent "'NoneType' object has no attribute 'get'" error
        if not cart_res:
            logger.error(f"Cart query returned None")
            return {
                "success": False,
                "message": "Cart query failed",
                "instruction_for_agent": "Tell user: 'Em không thể lưu ghi chú lúc này, anh/chị thử lại sau ạ.'",
                "code": "CART_API_ERROR"
            }

        cart_data_wrapper = cart_res.get("data")
        if not cart_data_wrapper:
            logger.error(f"Cart response has no data: {cart_res}")
            return {
                "success": False,
                "message": "Cart response invalid",
                "instruction_for_agent": "Tell user: 'Em không thể lưu ghi chú lúc này, anh/chị thử lại sau ạ.'",
                "code": "CART_API_ERROR"
            }

        cart_data = cart_data_wrapper.get("cart")
        if not cart_data:
            logger.error(f"Cart not found in response: {cart_res}")
            return {
                "success": False,
                "message": "Cart not found",
                "instruction_for_agent": "Tell user: 'Em không thể lưu ghi chú lúc này, anh/chị thử lại sau ạ.'",
                "code": "CART_API_ERROR"
            }
        delivery_date_obj = cart_data.get("delivery_date")

        if not delivery_date_obj or not delivery_date_obj.get("date") or delivery_date_obj.get("time_interval_id") is None:
            return {
                "success": False,
                "message": "Delivery time not set yet",
                "instruction_for_agent": "Tell user: 'Anh/chị cần chọn thời gian giao hàng trước khi thêm ghi chú ạ. Vui lòng điền thông tin giao hàng trong popup.'",
                "code": "NO_DELIVERY_TIME_SET"
            }

        delivery_date = delivery_date_obj.get("date")
        time_interval_id = delivery_date_obj.get("time_interval_id")

        # Ensure time_interval_id is integer
        if time_interval_id is not None:
            time_interval_id = int(time_interval_id)

        # Get shipping method from cart
        shipping_addresses = cart_data.get("shipping_addresses", [])
        carrier_code = None
        method_code = None

        if shipping_addresses and len(shipping_addresses) > 0:
            # First try selected_shipping_method
            selected_method = shipping_addresses[0].get("selected_shipping_method")
            if selected_method:
                carrier_code = selected_method.get("carrier_code")
                method_code = selected_method.get("method_code")

            # If no selected method, use first available method
            if not carrier_code or not method_code:
                available_methods = shipping_addresses[0].get("available_shipping_methods", [])
                if available_methods and len(available_methods) > 0:
                    carrier_code = available_methods[0].get("carrier_code")
                    method_code = available_methods[0].get("method_code")
                    logger.info(f"set_delivery_comment: No selected_shipping_method, using first available: {carrier_code}/{method_code}")

        # Final check - if still no shipping method, return error
        if not carrier_code or not method_code:
            return {
                "success": False,
                "message": "No shipping method available",
                "instruction_for_agent": "Tell user: 'Anh/chị cần chọn phương thức giao hàng trước khi thêm ghi chú ạ. Vui lòng điền thông tin giao hàng trong popup.'",
                "code": "NO_SHIPPING_METHOD"
            }

        logger.info(f"set_delivery_comment: Got delivery_date={delivery_date}, time_interval_id={time_interval_id} (type: {type(time_interval_id).__name__}), carrier={carrier_code}, method={method_code}")

        # Step 2: Call setShippingMethodsOnCart with comment
        mutation = """
            mutation SetDeliveryComment($cartId: String!, $date: String!, $timeIntervalId: Int!, $comment: String!, $carrierCode: String!, $methodCode: String!) {
                setShippingMethodsOnCart(
                    input: {
                        cart_id: $cartId
                        shipping_methods: [
                            {
                                carrier_code: $carrierCode
                                method_code: $methodCode
                            }
                        ]
                        delivery_date: {
                            date: $date
                            time_interval_id: $timeIntervalId
                            comment: $comment
                        }
                    }
                ) {
                    cart {
                        id
                        delivery_date {
                            date
                            time_interval_id
                            comment
                        }
                    }
                }
            }
        """

        variables = {
            "cartId": magento_cart_id,
            "date": delivery_date,
            "timeIntervalId": time_interval_id,
            "comment": comment,
            "carrierCode": carrier_code,
            "methodCode": method_code
        }

        logger.info(f"set_delivery_comment: Calling mutation with variables={variables}")

        res = await make_graphql_request_async(
            mutation,
            variables,
            base_url,
            store_id,
            auth_token=signin_token or None
        )

        logger.info(f"set_delivery_comment: Mutation response={res}")

        if not res or not res.get("data"):
            logger.error(f"Failed to set delivery comment: {res}")
            error_message = "Unknown error"
            if res and res.get("errors"):
                error_message = res["errors"][0].get("message", "Unknown error")

            return {
                "success": False,
                "message": f"API error: {error_message}",
                "instruction_for_agent": f"Tell user: 'Em không thể lưu ghi chú lúc này. Lỗi: {error_message}'",
                "code": "API_ERROR"
            }

        # Success - Extract updated comment with null-safe checks
        data = res.get("data") or {}
        cart_response = data.get("setShippingMethodsOnCart", {}) or {}
        cart_data_from_response = cart_response.get("cart", {}) or {}
        delivery_date_response = cart_data_from_response.get("delivery_date")

        if not delivery_date_response:
            logger.warning(f"Mutation succeeded but delivery_date is null in response: {res}")
            # Use the comment we sent since we can't verify from response
            updated_comment = comment
        else:
            updated_comment = delivery_date_response.get("comment", comment)

        logger.info(f"Successfully set delivery comment: '{updated_comment}' for cart {magento_cart_id}")

        return {
            "success": True,
            "message": f"Delivery comment set: {updated_comment}",
            "instruction_for_agent": f"Tell user: 'Dạ, em đã lưu ghi chú \"{updated_comment}\" cho đơn hàng ạ.'",
            "data": {
                "comment": updated_comment,
                "delivery_date": delivery_date,
                "time_interval_id": time_interval_id
            }
        }

    except Exception as e:
        logger.error(f"Error in set_delivery_comment: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "instruction_for_agent": "Tell user: 'Em không thể lưu ghi chú lúc này, anh/chị thử lại sau ạ.'",
            "code": "UNEXPECTED_ERROR"
        }
