import json
import logging
import traceback
from typing import Optional, Any
from google.adk.tools import ToolContext

from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_URL

logger = logging.getLogger(__name__)


async def register_account(tool_context: Optional[ToolContext] = None) -> dict[str, Any]:
    """
    Guide user to register new account. Use when user says they don't have account.

    Args:
        tool_context: Tool execution context (automatically provided)

    Returns:
        dict with registration URL and instructions
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                "instruction_for_agent": "Inform the user 'Hiện em không truy cập được thông tin đăng ký, anh/chị thử lại sau ít phút nhé.'",
                "code": "MISSING_TOOL_CONTEXT"
            }

        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        register_url = f"{base_url}/create-account"
        login_url = f"{base_url}/sign-in"

        return {
            "success": True,
            "message": f"Anh/Chị vui lòng chọn [Đăng ký]({register_url}) để tạo tài khoản giúp em nhé.",
            "instruction_for_agent": (
                f"Tell the user to register new account. "
                f"Set show_register_cta_button=True in your response."
            ),
            "show_register_cta_button": True,
            "register_url": register_url,
            "login_url": login_url
        }

    except Exception as e:
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "instruction_for_agent": "Inform the user 'Hiện em không truy cập được thông tin đăng ký, anh/chị thử lại sau ít phút nhé.'",
            "code": "UNEXPECTED_ERROR"
        }

async def view_account_info(tool_context: Optional[ToolContext] = None) -> dict[str, Any]:
    """
    View the USER's account information or guide them to login if not authenticated.

    This tool checks if the user is logged in (has signin_token). If not logged in,
    it provides instructions to redirect the user to the login page.

    Use this tool when user asks about:
    - "Xem thông tin tài khoản"
    - "Chỉnh sửa thông tin tài khoản"
    - "Thay đổi thông tin cá nhân"
    - "Account information"
    - Profile editing queries

    Args:
        tool_context: Tool execution context (automatically provided)

    Returns:
        dict with:
            - success (bool): Whether the operation succeeded
            - login_required (bool): True if user needs to login first
            - message (str): Status message
            - instruction_for_agent (str): Guidance for the agent on how to respond
            - show_signin_for_account_cta_button (bool): True if should show signin button for account access
            - login_url (str): Login page URL if login is required
            - account_info_url (str): Account info page URL if logged in

    Important:
        - Always refer to it as "your account" or "the user's account"
        - signin_token is required to access account information
        - For registration queries, use the register_account() function instead
        - When login_required=True, also sets show_signin_for_account_cta_button=True
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                "instruction_for_agent": "Inform the user 'Hiện em không truy cập được thông tin tài khoản, anh/chị thử lại sau ít phút nhé.'",
                "code": "MISSING_TOOL_CONTEXT"
            }

        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        signin_token = magento_session_data.get("signin_token") or ""
        signin_token = signin_token.strip('"')

        # Kiểm tra signin_token
        if not signin_token:
            # CHƯA ĐĂNG NHẬP - Hiển thị hướng dẫn đăng nhập
            login_url = f"{base_url}/sign-in?redirect={base_url}/account-information"

            return {
                "success": False,
                "login_required": True,
                "message": "User is not logged in",
                "instruction_for_agent": (
                    f"Tell the user to sign in to access account information. "
                    f"Set show_signin_for_account_cta_button=True in your response."
                ),
                "code": "NOT_LOGGED_IN",
                "login_url": login_url,
                "show_signin_for_account_cta_button": True,
            }


        account_info_url = f"{base_url}/account-information"
        
        return {
            "success": True,
            "login_required": False,
            "message": "User is logged in",
            "instruction_for_agent": (
                f"Tell the user EXACTLY this message:\n\n"
                f"'Anh/chị vui lòng truy cập vào [Thông tin tài khoản]({account_info_url}) để chỉnh sửa thông tin tài khoản nhé "
            ),
            "account_info_url": account_info_url
        }

    except Exception as e:
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "instruction_for_agent": "Inform the user 'Hiện em không truy cập được thông tin tài khoản, anh/chị thử lại sau ít phút nhé.'",
            "code": "UNEXPECTED_ERROR"
        }