import logging
import traceback
from typing import Optional, Any
from google.adk.tools import ToolContext

from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.tools.utils import make_graphql_request_async

logger = logging.getLogger(__name__)


async def get_mcard_loyalty_points(tool_context: Optional[ToolContext] = None) -> dict[str, Any]:
    """
    Get MCard loyalty points for a logged-in customer.

    This tool retrieves the customer's loyalty points (điểm thành viên MCard).
    Requires the user to be logged in (have signin_token).

    Use this tool when user asks about:
    - "Xem điểm MCard"
    - "Kiểm tra điểm thành viên"
    - "Tôi có bao nhiêu điểm"
    - "Loyalty points"
    - "Điểm tích lũy"

    Args:
        tool_context: Tool execution context (automatically provided)

    Returns:
        dict with:
            - success (bool): Whether the operation succeeded
            - loyalty_points (int): Number of loyalty points
            - customer_info (dict): Customer basic info (id, firstname, email)
            - message (str): Status message
            - instruction_for_agent (str): Guidance for the agent on how to respond
            - show_signin_for_account_cta_button (bool): True if user needs to login

    Example:
        User: "Tôi có bao nhiêu điểm MCard?"
        Returns: {"success": True, "loyalty_points": 1250, ...}
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                "instruction_for_agent": "Respond in user's language: Inform the user that the connection seems unstable and ask them to try again.",
                "code": "MISSING_TOOL_CONTEXT"
            }

        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
        signin_token = magento_session_data.get("signin_token") or ""
        signin_token = signin_token.strip('"')

        # Check if user is logged in
        if not signin_token:
            login_url = f"{base_url}/sign-in"

            return {
                "success": False,
                "login_required": True,
                "instruction_for_agent": (
                    f"Tell the user in their language to click the **sign in** button to view their **MCard loyalty points**. "
                    f"Set show_signin_for_dashboard_cta_button=True in your response."
                ),
                "code": "NOT_LOGGED_IN",
                "login_url": login_url,
                "show_signin_for_dashboard_cta_button": True,
            }

        # GraphQL query to get customer loyalty points
        graphql_query = """
            query GetCustomerInformation {
                customer {
                    id
                    firstname
                    email
                    loyalty_points
                }
            }
        """

        variables = {}

        logger.info(f"Fetching MCard loyalty points for logged-in customer")

        res = await make_graphql_request_async(
            graphql_query,
            variables,
            base_url,
            store_id,
            auth_token=signin_token,
        )

        if not res:
            return {
                "success": False,
                "message": "No response from API",
                "instruction_for_agent": "Respond in user's language: Inform the user that the connection seems unstable and ask them to try again.",
                "code": "NO_RESPONSE"
            }

        if res.get("errors"):
            error_message = res.get("errors", [{}])[0].get("message", "Unknown error")
            logger.error(f"GraphQL returned errors: {error_message}")
            return {
                "success": False,
                "message": f"API error: {error_message}",
                "instruction_for_agent": "Respond in user's language: Inform the user that the connection seems unstable and ask them to try again.",
                "code": "GRAPHQL_ERROR"
            }

        customer_data = res.get("data", {}).get("customer")

        if not customer_data:
            return {
                "success": False,
                "message": "No customer data found",
                "instruction_for_agent": "Respond in user's language: Inform the user that the connection seems unstable and ask them to try again.",
                "code": "NO_CUSTOMER_DATA"
            }

        loyalty_points = customer_data.get("loyalty_points")
        # Ensure loyalty_points is always a valid number (default to 0 if None or invalid)
        if loyalty_points is None:
            loyalty_points = 0

        customer_info = {
            "id": customer_data.get("id"),
            "firstname": customer_data.get("firstname"),
            "email": customer_data.get("email")
        }

        return {
            "success": True,
            "loyalty_points": loyalty_points,
            "customer_info": customer_info,
            "instruction_for_agent": (
                f"Tell the user in their language that they currently have **{loyalty_points} points** in their MCard. "
                f"Display the points in a clear, friendly manner."
            ),
            "code": "SUCCESS"
        }

    except Exception as e:
        logger.error(f"Error fetching MCard loyalty points: {str(e)}")
        logger.error(f"Full traceback: {traceback.format_exc()}")
        return {
            "success": False,
            "message": f"Error fetching loyalty points: {str(e)}",
            "instruction_for_agent": "Respond in user's language: Inform the user that the connection seems unstable and ask them to try again.",
            "code": "API_ERROR",
            "error_details": str(e)
        }
