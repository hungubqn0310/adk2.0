import json
import logging
import traceback
from typing import Optional, Any
from google.adk.tools import ToolContext

from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_URL

logger = logging.getLogger(__name__)

async def add_delivery_address(tool_context: Optional[ToolContext] = None) -> dict[str, Any]:
    """
    Guide user to add a new delivery address or redirect to login if not authenticated.

    This tool checks if the user is logged in (has signin_token). If not logged in,
    it provides instructions to redirect the user to the login page. If logged in,
    it directs them to the address management page.

    Args:
        tool_context: Tool execution context (automatically provided)

    Returns:
        dict with:
            - success (bool): Whether the operation succeeded
            - login_required (bool): True if user needs to login first
            - message (str): Status message
            - instruction_for_agent (str): Guidance for the agent on how to respond
            - show_signin_for_address_cta_button (bool): True if should show signin button for address management
            - login_url (str): Login page URL if login is required
            - address_management_url (str): Address management page URL if logged in

    Important:
        - Always refer to addresses as "your delivery address"
        - signin_token is required to manage addresses
        - Provide clear navigation instructions with links
        - When login_required=True, also sets show_signin_for_address_cta_button=True
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                "instruction_for_agent": "Inform the user 'Hiện em không thể hỗ trợ thêm địa chỉ nhận hàng, anh/chị thử lại sau ít phút nhé.'",
                "code": "MISSING_TOOL_CONTEXT"
            }

        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        signin_token = magento_session_data.get("signin_token") or ""
        signin_token = signin_token.strip('"')

        # Kiểm tra signin_token
        if not signin_token:
            # CHƯA ĐĂNG NHẬP - Hiển thị hướng dẫn đăng nhập
            login_url = f"{base_url}/sign-in?redirect={base_url}/address-book"
            return {
                "success": False,
                "login_required": True,
                "message": "User is not logged in",
                "instruction_for_agent": (
                    f"Tell the user to sign in to manage delivery addresses. "
                    f"Set show_signin_for_address_cta_button=True in your response."
                ),
                "code": "NOT_LOGGED_IN",
                "login_url": login_url,
                "show_signin_for_address_cta_button": True,
            }

        address_management_url = f"{base_url}/address-book?add=true"
        
        return {
            "success": True,
            "login_required": False,
            "message": "User is logged in",
            "instruction_for_agent": (
                f"Tell the user EXACTLY this message:\n\n"
                f"Anh/Chị vui lòng truy cập vào [Quản lý địa chỉ]({address_management_url}) để thêm địa chỉ nhận hàng mong muốn nhé. "
            ),
            "address_management_url": address_management_url
        }

    except Exception as e:
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "instruction_for_agent": "Inform the user 'Hiện em không thể hỗ trợ thêm địa chỉ nhận hàng, anh/chị thử lại sau ít phút nhé.'",
            "code": "UNEXPECTED_ERROR"
        }