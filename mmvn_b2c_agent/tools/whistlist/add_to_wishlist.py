"""
Add to Wishlist Tool - Trigger popup thêm sản phẩm vào wishlist cho user
Tool này sử dụng LongRunningFunctionTool để xử lý việc thêm sản phẩm vào wishlist
"""

import logging
from typing import Any, Dict, Optional
from google.adk.tools import LongRunningFunctionTool, ToolContext
from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_URL
import mmvn_b2c_agent.tools

logger = logging.getLogger(__name__)




def trigger_add_to_wishlist(
    sku: str,
    product_name: str = "",
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """
    Long-running, human-in-the-loop tool that opens a popup for user to add a product to wishlist.
    The user will need to complete the information in the popup.

    Args:
        sku: Product SKU to add to wishlist (REQUIRED - must come from recent search results)
        product_name: Product name to display (optional, for better UX)
        tool_context: Tool execution context (automatically provided)

    Returns:
        A dict indicating whether the popup is triggered and instructions for the agent.

    Important:
        - SKU is MANDATORY - this tool will fail if SKU is not provided
        - This tool only triggers the popup; the actual addition is handled by frontend
        - User must be logged in to add products to wishlist
        - SKU must come from product search results, never fabricate it
        - VALIDATION: Product must exist in search history (within last 6 hours) before adding to wishlist
        - If product not in search history, guide user to search for it first
        - Always refer to it as "your wishlist" or "the user's wishlist", NOT "my wishlist"

    Workflow:
        1. When user wants to add product to wishlist, agent MUST first search for the product
        2. Get SKU and product_name from search results
        3. Call this tool with both sku and product_name parameters
        4. Tool validates SKU exists in search history
        5. If validation passes, trigger popup for user to complete

    Example:
        User: "Thêm sản phẩm Heineken vào yêu thích"
        Agent workflow:
        Step 1: Search for "Heineken" using product search tool
        Step 2: Get SKU "123456_789" and name "Heineken Lon 330ml" from search results
        Step 3: Call trigger_add_to_wishlist(sku="123456_789", product_name="Heineken Lon 330ml")
    """
    # Validate tool context
    if not tool_context:
        return {
            "status": "error",
            "message": "Tool context is missing",
            "instruction_for_agent": (
                "Inform the user 'Có vẻ kết nối đang không ổn định lắm. "
                "Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'. "
                "If the user asks again, retry this tool."
            ),
            "code": "MISSING_TOOL_CONTEXT"
        }

    # Get session data
    magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
    base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
    signin_token = magento_session_data.get("signin_token", "")
    login_url = f"{base_url}/sign-in"

    # Check if user is logged in
    if not signin_token:
        logger.info("User not logged in. Cannot add to wishlist.")
        response = {
            "status": "error",
            "show_signin_button": True,
            "show_signin_for_wishlist_cta_button": True,
            "message": "User not logged in",
            "instruction_for_agent": (
                f"Tell the user: 'Anh/Chị cần đăng nhập để thêm sản phẩm vào danh sách yêu thích ạ. "
                f"Vui lòng chọn [Đăng nhập]({login_url}) để tiếp tục nhé.'"
            ),
            "code": "NOT_LOGGED_IN",
            "login_url": login_url
        }
        # Thêm sku và name vào response ngay cả khi chưa đăng nhập
        # để frontend có thể lưu và sử dụng sau khi user đăng nhập
        if sku:
            response["sku"] = sku
        if product_name:
            response["name"] = product_name
        return response

    # Validate that SKU is provided and exists in search history
    if not sku:
        logger.info("SKU not provided. Cannot add to wishlist without valid product.")
        return {
            "status": "error",
            "message": "Product SKU is required",
            "instruction_for_agent": (
                "SKU is missing. You must search for the product first to get its SKU, "
                "then call this tool with the SKU parameter. "
                "Guide the user: 'Em chưa tìm thấy sản phẩm cụ thể. "
                "Anh/Chị vui lòng cho em biết tên sản phẩm muốn thêm vào yêu thích, "
                "em sẽ tìm kiếm giúp anh/chị ạ.'"
            ),
            "code": "SKU_REQUIRED"
        }

    product_details = mmvn_b2c_agent.tools.get_product_details_from_search_history(sku, tool_context)
    if not product_details:
        logger.info(f"SKU {sku} not found in search history. User needs to search first.")
        return {
            "status": "error",
            "message": "Product not found in search history",
            "instruction_for_agent": (
                "The product is not in the search history. "
                "Guide the user to search for the product first: "
                "'Em chưa tìm thấy sản phẩm này trong kết quả tìm kiếm gần đây. "
                "Anh/Chị vui lòng cho em biết tên sản phẩm muốn thêm vào yêu thích, "
                "em sẽ tìm kiếm giúp anh/chị ạ.'"
            ),
            "code": "PRODUCT_NOT_IN_SEARCH_HISTORY"
        }

    # User is logged in, trigger popup
    logger.info(f"[WISHLIST] Triggering add to wishlist popup with SKU: {sku if sku else 'None'}, Product: {product_name if product_name else 'None'}")
    logger.debug(f"[WISHLIST] Full tool_context state: {tool_context.state}")

    response = {
        "status": "pending",
        "message": "Please complete the information in the popup to add product to your wishlist.",
        "instruction_for_agent": (
            "A popup has been triggered for the user to add the product to their wishlist. "
            "The agent must inform the user to complete the action in the popup. "
            "Tell the user: 'Em đã mở popup để anh/chị thêm sản phẩm vào danh sách yêu thích. "
            "Anh/Chị vui lòng điền thông tin trong popup nhé.'"
        )
    }

    if sku:
        response["sku"] = sku
    if product_name:
        response["name"] = product_name 

    return response




# Create tool instances
TriggerAddToWishlistTool = LongRunningFunctionTool(trigger_add_to_wishlist)
# Alias for backward compatibility (main tool to use)
AddToWishlistTool = TriggerAddToWishlistTool
