from typing import Optional
from google.adk.tools import ToolContext
from mmvn_b2c_agent.tools.cng.cart.cart_view import view_cart


async def checkout_cart(tool_context: Optional[ToolContext] = None):
    """
    Prepare the USER's shopping cart for checkout.

    This tool retrieves the user's cart details and provides checkout instructions.
    The cart belongs to the USER - you are preparing their checkout on their behalf.

    This function:
    1. Retrieves the checkout URL from the session
    2. Fetches current cart details using view_cart
    3. Validates cart is not empty
    4. Returns cart summary and checkout instructions for the agent

    Args:
        tool_context: Tool execution context (automatically provided)

    Returns:
        dict with:
            - cart_detail (dict): Complete cart information including items, prices, totals
            - instruction_for_agent (str): Instructions to guide user to checkout page

        OR if cart is empty:
            - message (str): Empty cart message
            - instruction_for_agent (str): Instructions to prompt user to add items

    Important:
        - Always refer to it as "your cart" or "the user's cart", NOT "my cart" or "the agent's cart"
        - Checkout happens on the e-commerce website, not within the agent
        - This tool prepares the user to navigate to the checkout page
        - Empty carts should not proceed to checkout
    """
    # get base url from tool context
    magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
    base_url = magento_session_data.get('base_url', 'https://online.mmvietnam.com').rstrip('/')
    checkout_url = f"{base_url}/checkout/"

    cart_detail = await view_cart(tool_context)
    if not cart_detail.get("success"):
        return cart_detail
    cart_items = cart_detail.get("data", {}).get("items", [])
    if not cart_items:
        return {
            "message": "Your cart is currently empty. Please add items to your cart before proceeding to checkout.",
            "instruction_for_agent": "Inform the user that their cart is empty and they need to "
                                     "add items before they can proceed to checkout. "
                                     "Set the display mode to cart if needed and show the continue shopping cta button.",
        }

    return {
        "cart_detail": cart_detail.get("data", {}),
        "instruction_for_agent": f"Inform the user about the cart's item count, "
                                 f"subtotal and show all cart-related cta button. "
                                 f"Then politely instruct the user to click on the "
                                 f"'checkout now'('Thanh toán ngay') button to proceed to checkout. "
                                 f"Set the display mode to cart if needed and show the continue shopping cta button.",
    }


async def get_checkout_details(tool_context: Optional[ToolContext] = None):
    """
    Get checkout details for the USER's shopping cart.

    This is an alias function that calls checkout_cart to retrieve the user's
    cart details and checkout information. The cart belongs to the USER - you
    are retrieving their checkout details on their behalf.

    Args:
        tool_context: Tool execution context (automatically provided)

    Returns:
        Same as checkout_cart - dict with cart details and checkout instructions

    Important:
        - Always refer to it as "your cart" or "the user's cart", NOT "my cart" or "the agent's cart"
        - See checkout_cart docstring for complete details
    """
    return checkout_cart(tool_context)
