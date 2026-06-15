import json
import logging
import traceback
import requests
from typing import Optional, Any
from google.adk.tools import ToolContext

from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.tools.cng.cart.common import save_cart_to_state
from mmvn_b2c_agent.tools.cng.common import process_cart_data
from mmvn_b2c_agent.tools.utils import make_graphql_request_async

logger = logging.getLogger(__name__)

async def view_cart(tool_context: Optional[ToolContext] = None) -> dict[str, Any]:
    """
    View the current contents of the USER's shopping cart.

    This tool retrieves all items in the user's cart, including product details,
    quantities, prices, discounts, and cart totals. The cart belongs to the USER -
    you are viewing it on their behalf.

    Args:
        tool_context: Tool execution context (automatically provided)

    Returns:
        dict with:
            - success (bool): Whether the operation succeeded
            - data (dict): Cart information including items, subtotal, discounts, grand total
            - message (str): Error message if failed
            - instruction_for_agent (str): Guidance for the agent on how to respond to the user

    Important:
        - Always refer to it as "your cart" or "the user's cart", NOT "my cart" or "the agent's cart"
        - Cart item IDs (uid) are needed for update/remove operations
        - Use this tool to get current cart state before modifying items
    """
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
        graphql_query = """
            query GetCartInfo($cartId: String!) {
                cart(cart_id: $cartId) {
                    id
                    total_summary_quantity_including_config
                    items {
                        uid
                        quantity
                        comment
                        product {
                            id
                            uid
                            sku
                            name
                            ecom_name
                            art_no
                            canonical_url
                            small_image { url }
                            mm_brand
                            categories {
                                uid
                                name
                            }
                            price_range {
                                maximum_price {
                                    final_price { value currency }
                                    regular_price { value currency }
                                }
                            }
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
        """

        variables = {"cartId": magento_cart_id}

        try:
            res = await make_graphql_request_async(
                graphql_query,
                variables,
                base_url,
                store_id,
                auth_token=signin_token or None,
            )
            # res = await response.json()

            if not res.get("data"):
                logger.error(f"Cannot get cart's detail:\n{json.dumps(res, indent=4)}")
                tool_context.state['last_cart_error_response'] = {'error': 'Cannot get cart detail', 'response': res}
                return {
                    "success": False,
                    "message": "API error: Back end error invalid data format",
                    "instruction_for_agent": "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'.",
                    "code": "INVALID_RESPONSE"
                }

            elif not res.get("data").get("cart", {}):
                logger.error(f"Empty cart data:\n{json.dumps(res, indent=4)}")
                tool_context.state['last_cart_error_response'] = {'error': 'Empty cart', 'response': res}
                return {
                    "success": False,
                    "message": "Cannot get cart's detail.",
                    "instruction_for_agent": "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'.",
                    "code": "INVALID_RESPONSE: EMPTY_CART_DATA"
                }

        except requests.RequestException as e:
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "message": f"HTTP error: {str(e)}",
                "instruction_for_agent": "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'.",
                "code": "HTTP_ERROR"
            }

        # Format data returned to AI
        cart_data = res.get("data").get("cart", {})
        processed_cart_data = process_cart_data(cart_data)
        await save_cart_to_state(cart_data, processed_cart_data, tool_context)

        result: dict[str, Any] = {
            "success": True,
            "data": processed_cart_data,
            "instruction_for_agent": "Set display mode to cart if needed, show the cart detail and process to check out cta buttons."
        }
        if not cart_data.get('items'):
            result['instruction_for_agent'] = "Set display mode to cart if needed, hide the show cart detail and process to check out cta buttons."
        return result

    except Exception as e:
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "instruction_for_agent": "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'.",
            "code": "UNEXPECTED_ERROR",
        }

async def get_cart_shipping_cost(tool_context: Optional[ToolContext] = None) -> dict[str, Any]:
    """
    Get shipping cost information for the USER's shopping cart.

    This tool provides guidance about shipping costs for items in the user's cart.
    The cart belongs to the USER - you are checking shipping costs on their behalf.

    Note: Shipping costs are calculated at checkout, not during cart browsing.
    This function directs users to the checkout page where final shipping costs
    are calculated based on delivery address and shipping method.

    Args:
        tool_context: Tool execution context (automatically provided)

    Returns:
        dict with:
            - success (bool): Always False (shipping cost not available pre-checkout)
            - instruction_for_agent (str): Message to inform user about checkout

    Important:
        - Always refer to it as "your cart" or "the user's cart", NOT "my cart" or "the agent's cart"
        - Shipping costs require delivery address - only available at checkout
        - Provide checkout URL link for user convenience
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                "code": "MISSING_TOOL_CONTEXT",
            }

        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        magento_cart_id = magento_session_data.get("magento_cart_id") or ""
        magento_cart_id = magento_cart_id.strip('"')

        if not magento_cart_id:
            return {
                "success": False,
                "message": "Magento cart ID is missing in session data",
                "instruction_for_agent": "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'.",
                "code": "MISSING_CART_ID",
            }

        checkout_url = f"{base_url}/checkout"
        return {
            "success": False,
            "instruction_for_agent": f"Politely inform the user that the shipping cost will be calculated at checkout. "
                                     f"The user can check the shipping cost by clicking the checkout now button "
                                     f"or go to the checkout page"
                                     f"Set display mode to cart if needed, must show process to check out cta buttons.",
        }

    except Exception as e:
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "code": "UNEXPECTED_ERROR",
        }
