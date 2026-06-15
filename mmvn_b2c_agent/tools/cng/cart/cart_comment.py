import logging
import traceback
from typing import Optional, Dict, Any

import requests
from google.adk.tools import ToolContext

from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.tools.cng.cart.common import save_cart_to_state, get_cart_item_id_from_sku
from mmvn_b2c_agent.tools.cng.common import process_cart_data
from mmvn_b2c_agent.tools.utils import make_graphql_request

logger = logging.getLogger("google_adk." + __name__)


# ... (Các class Exception giữ nguyên) ...

class NoSuggestedLocationError(Exception):
    """
    Exception raised when no suggested location is found for a given address.
    This is used to indicate that the address provided does not match any known locations, or if no nearby stores are found.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def __str__(self):
        return f"NoSuggestedLocationError: {self.message}"


class NoStoreNearLocationError(Exception):
    """
    Exception raised when no store is found near the given location.
    This exception indicates that the system could not identify any nearby stores
    based on the provided address or coordinates.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def __str__(self):
        return f"NoStoreNearLocationError: {self.message}"


class MagentoAPIError(Exception):
    """
    Exception raised when there is an error with the Magento API.
    This can be used to indicate issues such as invalid responses or errors in the request.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def __str__(self):
        return f"MagentoAPIError: {self.message}"


# HÀM NÀY KHÔNG ĐỔI
async def update_comment_on_cart_item(
        tool_context: Optional[ToolContext],
        cart_item_uid: str,
        comment: str
) -> Dict[str, Any]:
    """
    Cập nhật ghi chú (comment) cho một sản phẩm cụ thể (cart item) trong giỏ hàng CỦA USER.
    Tự động lấy cart_id và token từ session.

    Args:
        tool_context (ToolContext): Tool execution context (automatically provided).
        cart_item_uid (str): UID của sản phẩm trong giỏ hàng (ví dụ: "NDIyMjIxOQ==").
        comment (str): Nội dung ghi chú muốn cập nhật.

    Returns:
        Dict[str, Any]: Một dictionary chứa trạng thái success và
                       dữ liệu giỏ hàng đã cập nhật hoặc thông báo lỗi.
    """

    # --- THÊM LOGIC LẤY DỮ LIỆU TỪ SESSION ---
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. "
                                         "If the user asks again, retry this tool.",
                "code": "MISSING_TOOL_CONTEXT"
            }

        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        signin_token = magento_session_data.get("signin_token") or ""
        signin_token = signin_token.strip('"')
        magento_cart_id = magento_session_data.get("magento_cart_id") or ""
        magento_cart_id = magento_cart_id.strip('"')

        if not magento_cart_id:
            return {
                "success": False,
                "message": "Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.",
                'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. "
                                         "If the user asks again, retry this tool.",
                "code": "MISSING_CART_ID",
            }
    except Exception as e:
        logger.error(f"Error accessing tool_context state: {e}")
        return {"success": False, "message": f"Unexpected error accessing session: {e}", "code": "STATE_ACCESS_ERROR"}
    # --- KẾT THÚC LOGIC LẤY DỮ LIỆU TỪ SESSION ---

    print(
        f"{'-' * 80}\n"
        f"TOOL CALLED: update_comment_on_cart_item\n"
        f"cart_id (from session): {magento_cart_id}, cart_item_uid: {cart_item_uid}, comment: {comment}\n"
        f"{'-' * 80}\n"
    )

    mutation_query = """
    mutation UpdateComment($cart_id: String!, $cart_item_uid: ID!, $comment: String!) {
      updateCommentOnCartItem(input: {
        cart_id: $cart_id
        cart_item_uid: $cart_item_uid
        comment: $comment
      }) {
        cart {
            id
            total_summary_quantity_including_config
            items {
                uid
                quantity
                comment 
                product {
                    sku
                    name
                    ecom_name
                    art_no
                    dnr_price {
                        event_name
                        promo_amount
                        promo_label
                        promo_type
                        promo_value
                        qty
                    }
                }
                prices {
                    price_including_tax { value currency }
                    discounts {
                        label
                        amount { value currency }
                    }
                    row_total_including_tax { value currency }
                    total_item_discount { value currency }
                }
            }
            prices {
                subtotal_including_tax { value currency }
                subtotal_with_discount_excluding_tax { value currency }
                discounts {
                    label
                    amount { value currency }
                }
                grand_total { value currency }
            }
        }
      }
    }
    """

    variables = {
        "cart_id": magento_cart_id,
        "cart_item_uid": cart_item_uid,
        "comment": comment
    }

    try:

        response = make_graphql_request(
            mutation_query,
            variables,
            base_url,
            store_id,
            auth_token=signin_token or None,
        )

        response.raise_for_status()
        response_data = response.json()

        if 'errors' in response_data:
            logger.error(f"GraphQL errors: {response_data['errors']}")
            raise MagentoAPIError(f"GraphQL error occurred: {response_data['errors'][0]['message']}")

        if 'data' not in response_data or 'updateCommentOnCartItem' not in response_data['data'] or not response_data['data']['updateCommentOnCartItem'].get('cart'):
            logger.error(f"Invalid response from server: 'updateCommentOnCartItem.cart' not found. "
                         f"Response: {response_data}")
            raise MagentoAPIError("Invalid response from the server. "
                                  "'updateCommentOnCartItem.cart' not found in response data.")

        updated_cart_raw = response_data['data']['updateCommentOnCartItem']['cart']

        processed_cart_data = process_cart_data(updated_cart_raw)
        await save_cart_to_state(updated_cart_raw, processed_cart_data, tool_context)

        return {
            "success": True,
            "data": processed_cart_data,
            "instruction_for_agent": "Set display mode to cart if needed, show the cart detail and process to check out cta buttons."
        }


    except requests.exceptions.RequestException as e:
        logger.error(f"HTTP Request failed for update_comment_on_cart_item: {e}")
        return {
            "success": False,
            "message": f"Network error (Không thể kết nối tới {base_url}): {e}",
            "code": "HTTP_ERROR"
        }
    except MagentoAPIError as e:
        logger.error(f"API error in update_comment_on_cart_item: {e}")
        return {"success": False, "message": str(e), "code": "MAGENTO_API_ERROR"}
    except Exception as e:
        logger.error(f"Unexpected error in update_comment_on_cart_item: {e}")
        return {"success": False, "message": f"An unexpected error occurred: {e}", "code": "UNEXPECTED_ERROR"}


async def update_comment_on_cart_item_with_sku(
        sku: str,
        comment: str,
        tool_context: Optional[ToolContext],
):
    """
    Find the cart item UID by SKU and update its comment in the USER's shopping cart.
    Args:
        sku (str): SKU of the product in the cart. Format example: "123_456"
        comment (str): The comment to set for the cart item.
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
                "code": "MISSING_TOOL_CONTEXT"
            }
        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        magento_cart_id = magento_session_data.get("magento_cart_id") or ""
        magento_cart_id = magento_cart_id.strip('"')
        if not magento_cart_id:
            return {
                "success": False,
                "message": "Magento cart ID is missing in session data",
                'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
                "code": "MISSING_CART_ID",
            }

        # get cart item ID from SKU
        cart_item_id_res = await get_cart_item_id_from_sku(sku, tool_context)
        if not cart_item_id_res.get("success"):
            return cart_item_id_res

        cart_item_id = cart_item_id_res.get("cart_item_id")
        return await update_comment_on_cart_item(cart_item_uid=cart_item_id, comment=comment, tool_context=tool_context)

    except Exception as e:
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
            "code": "UNEXPECTED_ERROR"
        }


# HÀM ĐÃ ĐƯỢC SỬA - PHIÊN BẢN CẢI TIẾN
async def remove_comment_from_cart_item(
        cart_item_uid: str,
        tool_context: Optional[ToolContext],
) -> Dict[str, Any]:
    """
    Remove comment from a cart item in the USER's shopping cart.
    Args:
        cart_item_uid (str): UID of cart item(NOT SKU). Format example: "NDIyMjIxOQ==".
    """

    # --- LẤY DỮ LIỆU TỪ SESSION ---
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. "
                                         "If the user asks again, retry this tool.",
                "code": "MISSING_TOOL_CONTEXT"
            }

        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        signin_token = magento_session_data.get("signin_token") or ""
        signin_token = signin_token.strip('"')
        magento_cart_id = magento_session_data.get("magento_cart_id") or ""
        magento_cart_id = magento_cart_id.strip('"')

        if not magento_cart_id:
            return {
                "success": False,
                "message": "Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.",
                'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. "
                                         "If the user asks again, retry this tool.",
                "code": "MISSING_CART_ID",
            }

    except Exception as e:
        logger.error(f"Error accessing tool_context state: {e}")
        return {"success": False, "message": f"Unexpected error accessing session: {e}", "code": "STATE_ACCESS_ERROR"}
    # --- KẾT THÚC LẤY DỮ LIỆU TỪ SESSION ---

    print(
        f"{'-' * 80}\n"
        f"TOOL CALLED: remove_comment_from_cart_item\n"
        f"cart_id (from session): {magento_cart_id}, cart_item_uid: {cart_item_uid}\n"
        f"{'-' * 80}\n"
    )

    # ===== BẮT ĐẦU: LẤY GIỎ HÀNG MỚI NHẤT ĐỂ KIỂM TRA COMMENT =====
    try:
        query_current_cart = """
        query GetCart($cart_id: String!) {
          cart(cart_id: $cart_id) {
            id
            items {
              uid
              comment
              product {
                sku
                name
              }
            }
          }
        }
        """

        logger.info(f"Fetching current cart to check comment status for item {cart_item_uid}")

        check_response = make_graphql_request(
            query_current_cart,
            {"cart_id": magento_cart_id},
            base_url,
            store_id,
            auth_token=signin_token or None,
        )

        check_response.raise_for_status()
        check_data = check_response.json()

        # Kiểm tra response hợp lệ
        if 'errors' not in check_data and 'data' in check_data and check_data['data'].get('cart'):
            current_cart = check_data['data']['cart']
            current_items = current_cart.get('items', [])

            # Tìm item cần xóa comment
            item_to_check = None
            for item in current_items:
                if item.get("uid") == cart_item_uid:
                    item_to_check = item
                    break

            # Nếu tìm thấy item
            if item_to_check:
                current_comment = (item_to_check.get("comment") or "").strip()

                # Nếu comment đã rỗng/null, trả về lỗi ngay
                if not current_comment:
                    product_name = item_to_check.get("product", {}).get("name", "")
                    logger.info(f"Item {cart_item_uid} ({product_name}) has no comment to remove.")
                    return {
                        "success": False,
                        "message": "Sản phẩm này chưa có ghi chú.",
                        "instruction_for_agent": "Inform the user 'Dạ, sản phẩm này chưa có ghi chú để xóa ạ.'",
                        "code": "COMMENT_ALREADY_EMPTY"
                    }
                else:
                    logger.info(f"Item {cart_item_uid} has comment: '{current_comment}'. Proceeding with removal.")
            else:
                logger.warning(f"Item {cart_item_uid} not found in current cart. Proceeding anyway...")

    except Exception as e:
        logger.warning(f"Could not pre-check cart comment status: {e}. Proceeding with removal anyway...")
        # Nếu không check được, vẫn tiếp tục (fallback)
    # ===== KẾT THÚC: KIỂM TRA COMMENT =====

    # Định nghĩa GraphQL mutation
    mutation_query = """
    mutation RemoveComment($cart_id: String!, $cart_item_uid: ID!) {
      removeCommentFromCartItem(input: {
        cart_id: $cart_id
        cart_item_uid: $cart_item_uid
      }) {
        cart {
            id
            total_summary_quantity_including_config
            items {
                uid
                quantity
                comment
                product {
                    sku
                    name
                    ecom_name
                    art_no
                    dnr_price {
                        event_name
                        promo_amount
                        promo_label
                        promo_type
                        promo_value
                        qty
                    }
                }
                prices {
                    price_including_tax { value currency }
                    discounts {
                        label
                        amount { value currency }
                    }
                    row_total_including_tax { value currency }
                    total_item_discount { value currency }
                }
            }
            prices {
                subtotal_including_tax { value currency }
                subtotal_with_discount_excluding_tax { value currency }
                discounts {
                    label
                    amount { value currency }
                }
                grand_total { value currency }
            }
        }
      }
    }
    """

    variables = {
        "cart_id": magento_cart_id,
        "cart_item_uid": cart_item_uid,
    }

    try:
        # Gọi API
        response = make_graphql_request(
            mutation_query,
            variables,
            base_url,
            store_id,
            auth_token=signin_token or None,
        )

        response.raise_for_status()
        response_data = response.json()

        # Xử lý lỗi GraphQL
        if 'errors' in response_data:
            logger.error(f"GraphQL errors: {response_data['errors']}")
            raise MagentoAPIError(f"GraphQL error occurred: {response_data['errors'][0]['message']}")

        # Xử lý lỗi cấu trúc response
        if 'data' not in response_data or 'removeCommentFromCartItem' not in response_data['data'] or not \
                response_data['data']['removeCommentFromCartItem'].get('cart'):
            logger.error(f"Invalid response from server: 'removeCommentFromCartItem.cart' not found. "
                         f"Response: {response_data}")
            raise MagentoAPIError("Invalid response from the server. "
                                  "'removeCommentFromCartItem.cart' not found in response data.")

        # Lấy giỏ hàng đã cập nhật
        updated_cart_raw = response_data['data']['removeCommentFromCartItem']['cart']

        # Xử lý và lưu giỏ hàng mới vào state
        processed_cart_data = process_cart_data(updated_cart_raw)
        await save_cart_to_state(updated_cart_raw, processed_cart_data, tool_context)

        # Trả về success
        return {
            "success": True,
            "data": processed_cart_data,
            "instruction_for_agent": "Set display mode to cart if needed, show the cart detail and process to check out cta buttons."
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"HTTP Request failed for remove_comment_from_cart_item: {e}")
        return {
            "success": False,
            "message": f"Network error (Không thể kết nối tới {base_url}): {e}",
            "code": "HTTP_ERROR"
        }
    except MagentoAPIError as e:
        logger.error(f"API error in remove_comment_from_cart_item: {e}")
        return {"success": False, "message": str(e), "code": "MAGENTO_API_ERROR"}
    except Exception as e:
        logger.error(f"Unexpected error in remove_comment_from_cart_item: {e}")
        return {"success": False, "message": f"An unexpected error occurred: {e}", "code": "UNEXPECTED_ERROR"}


async def remove_comment_from_cart_item_with_sku(
        sku: str,
        tool_context: Optional[ToolContext],
):
    """
    Find the cart item UID by SKU and remove its comment in the USER's shopping cart.
    Args:
        sku (str): SKU of the product in the cart. Format example: "123_456"
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
                "code": "MISSING_TOOL_CONTEXT"
            }
        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        magento_cart_id = magento_session_data.get("magento_cart_id") or ""
        magento_cart_id = magento_cart_id.strip('"')
        if not magento_cart_id:
            return {
                "success": False,
                "message": "Magento cart ID is missing in session data",
                'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
                "code": "MISSING_CART_ID",
            }

        # get cart item ID from SKU
        cart_item_id_res = await get_cart_item_id_from_sku(sku, tool_context)
        if not cart_item_id_res.get("success"):
            return cart_item_id_res

        cart_item_id = cart_item_id_res.get("cart_item_id")
        return await remove_comment_from_cart_item(cart_item_uid=cart_item_id,
                                                   tool_context=tool_context)

    except Exception as e:
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
            "code": "UNEXPECTED_ERROR"
        }
