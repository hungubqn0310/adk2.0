import asyncio
import json
import logging
import traceback
import requests
from google.genai import types
from typing_extensions import override
from typing import Optional, Any
from google.adk.tools import ToolContext, BaseTool
from pydantic import BaseModel

from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.tools.cng.cart.cart_view import view_cart
from mmvn_b2c_agent.tools.cng.cart.common import cart_item_id_exists, get_cart_item_id_from_sku, save_cart_to_state
from mmvn_b2c_agent.tools.cng.common import process_cart_data, get_current_cart_from_session_state
from mmvn_b2c_agent.tools.cng.cart.common import (
    cart_item_id_exists,
    get_cart_item_id_from_sku,
    save_cart_to_state,
)
from mmvn_b2c_agent.tools.cng.common import process_cart_data
from mmvn_b2c_agent.tools.utils import make_graphql_request_async

logger = logging.getLogger(__name__)


async def _remove_cart_item(cart_item_id: str, tool_context: ToolContext, removed_item_info: dict = None) -> dict:
    """Remove a cart item by its ID. Assumes the cart item ID exists.

    Args:
        cart_item_id: The cart item ID to remove
        tool_context: Tool context
        removed_item_info: Optional dict with product info of the item being removed (for tracking)
    """

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
            "message": "Magento cart ID is missing in session data",
            'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
            "code": "MISSING_CART_ID",
        }

    graphql_query = """
        mutation RemoveItemFromCart($cartId: String!, $cartItemId: ID!) {
            removeItemFromCart(input: { cart_id: $cartId, cart_item_uid: $cartItemId }) {
                cart {
                    id
                    total_quantity
                    items {
                        uid
                        quantity
                        product {
                            id
                            uid
                            name
                            ecom_name
                            sku
                            art_no
                            canonical_url
                            small_image { url }
                            mm_brand
                            categories { uid name }
                            price_range {
                                maximum_price {
                                    final_price { value currency }
                                    regular_price { value currency }
                                }
                            }
                        }
                        prices {
                            price_including_tax { value currency }
                            row_total_including_tax { value currency }
                            total_item_discount { value currency }
                        }
                    }
                    prices {
                        subtotal_including_tax { value currency }
                        subtotal_with_discount_excluding_tax { value currency }
                        grand_total { value currency }
                    }
                }
            }
        }
    """

    variables = {
        "cartId": magento_cart_id,
        "cartItemId": cart_item_id
    }

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
            logger.error(f"Cannot remove product from cart. \n"
                         f"Query:\n{graphql_query}\n\n"
                         f"Variables:\n{json.dumps(variables)}\n\n"
                         f"Full response:\n{json.dumps(res, indent=4)}\n\n")
            return {
                "success": False,
                "message": "API error: Back end error invalid data format",
                'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
                "code": "INVALID_RESPONSE"
            }
        elif not res.get("data").get("removeItemFromCart", {}).get("cart", {}):
            logger.error(f"Cannot remove product from cart. \n"
                         f"Query:\n{graphql_query}\n\n"
                         f"Variables:\n{json.dumps(variables)}\n\n"
                         f"Full response:\n{json.dumps(res, indent=4)}\n\n")
            return {
                "success": False,
                "message": f"Cannot remove product from cart.",
                'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
            }
    except requests.RequestException as e:
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"HTTP error: {str(e)}",
            'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
            "code": "HTTP_ERROR"
        }

    # format the data returned to AI.
    cart_data = res.get("data").get("removeItemFromCart", {}).get("cart", {})
    processed_cart_data = process_cart_data(cart_data)
    await save_cart_to_state(cart_data, processed_cart_data, tool_context)

    # Build tracking data for FE
    tracking_items = []
    if removed_item_info:
        tracking_item = {
            "action": "remove",
            "type": "product",
            "id": removed_item_info.get('id'),
            "name": removed_item_info.get('name'),
            "sku": removed_item_info.get('sku'),
            "page_url": removed_item_info.get('page_url'),
            "image_url": removed_item_info.get('image_url'),
            "price": removed_item_info.get('price'),
            "original_price": removed_item_info.get('original_price'),
            "main_category": removed_item_info.get('main_category'),
            "category_level_1": removed_item_info.get('category_level_1'),
            "category_level_2": removed_item_info.get('category_level_2'),
            "brand": removed_item_info.get('brand'),
            "quantity": removed_item_info.get('quantity')
        }
        tracking_items.append(tracking_item)

    return {
        "success": True,
        "data": processed_cart_data,
        "tracking": {
            "items": tracking_items
        },
        "instruction_for_agent": "Set display mode to cart if needed, show the cart detail and process to check out cta buttons."
    }


async def remove_cart_item(cart_item_id: str, tool_context: ToolContext) -> Any:
    """
    Remove a specific item from the USER's shopping cart by its cart item ID.

    The cart belongs to the USER - you are managing it on their behalf.

    Args:
        cart_item_id: Unique identifier for the cart item (get this from view_cart results)
        tool_context: Tool execution context (automatically provided)

    Returns:
        dict with:
            - success (bool): Whether the operation succeeded
            - data (dict): Updated cart information if successful
            - message (str): Error message if failed
            - instruction_for_agent (str): Guidance for the agent on how to respond to the user

    Important:
        - NEVER fabricate cart_item_id - must come from view_cart tool results
        - Always refer to it as "your cart" or "the user's cart", NOT "my cart" or "the agent's cart"
        - Cart item ID is different from product SKU
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
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        magento_cart_id = magento_session_data.get("magento_cart_id") or ""
        magento_cart_id = magento_cart_id.strip('"')
        if not magento_cart_id:
            return {
                "success": False,
                "message": "Magento cart ID is missing in session data",
                'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
                "code": "MISSING_CART_ID",
            }
        # check if the cart item id exists in the cart
        found_cart_item_id = await cart_item_id_exists(cart_item_id, tool_context)
        if not found_cart_item_id:
            return {
                "success": False,
                "message": f"Cart item with ID {cart_item_id} not found.",
                'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
            }

        # Get item info before removing for tracking
        removed_item_info = None
        cart_state = tool_context.state.get('state', {}).get('current_cart_data', {}).get('cart_raw_data', {})
        if cart_state and cart_state.get("items"):
            for item in cart_state["items"]:
                if item.get("uid") == cart_item_id:
                    item_product = item.get("product", {})
                    categories_raw = item_product.get('categories', [])
                    categories = [cat.get('name') for cat in categories_raw if cat.get('name')] if categories_raw else []
                    price_range = item_product.get('price_range', {}).get('maximum_price', {})
                    canonical_url = item_product.get('canonical_url')
                    small_image = item_product.get('small_image', {})

                    # Build unique ID from art_no + store_code
                    art_no = item_product.get('art_no')
                    store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
                    # Extract store_code from store_id (e.g., 'b2c_10013_vi' -> '10013')
                    store_code = store_id.replace('b2c_', '').replace('_vi', '') if store_id else ''
                    tracking_id = f"{art_no}_{store_code}" if art_no and store_code else (item_product.get('id') or item_product.get('uid'))

                    removed_item_info = {
                        "id": tracking_id,
                        "name": item_product.get('ecom_name') or item_product.get('name'),
                        "sku": item_product.get('sku'),
                        "page_url": f"{base_url}/{canonical_url}" if canonical_url else None,
                        "image_url": small_image.get('url') if isinstance(small_image, dict) else small_image,
                        "price": price_range.get('final_price', {}).get('value'),
                        "original_price": price_range.get('regular_price', {}).get('value'),
                        "main_category": categories[0] if len(categories) > 0 else None,
                        "category_level_1": categories[0] if len(categories) > 0 else None,
                        "category_level_2": categories[1] if len(categories) > 1 else None,
                        "brand": item_product.get('mm_brand'),
                        "quantity": item.get('quantity')
                    }
                    break

        return await _remove_cart_item(cart_item_id, tool_context, removed_item_info)

    except Exception as e:
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
            "code": "UNEXPECTED_ERROR"
        }


async def remove_product_sku_from_cart(sku: str, tool_context: ToolContext) -> Any:
    """
    Remove a product from the USER's shopping cart by its SKU.

    This is a convenience function that finds the cart item ID from the SKU,
    then removes it. The cart belongs to the USER - you are managing it on their behalf.

    Args:
        sku: Product SKU code (format: two integers with underscore, e.g., "441976_24419765")
        tool_context: Tool execution context (automatically provided)

    Returns:
        dict with:
            - success (bool): Whether the operation succeeded
            - data (dict): Updated cart information if successful
            - message (str): Error message if failed
            - instruction_for_agent (str): Guidance for the agent on how to respond to the user

    Important:
        - NEVER fabricate SKU - must come from product search results or user input
        - Always refer to it as "your cart" or "the user's cart", NOT "my cart" or "the agent's cart"
        - If product not in cart, inform user politely
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
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
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

        # Get item info before removing for tracking
        removed_item_info = None
        cart_state = tool_context.state.get('state', {}).get('current_cart_data', {}).get('cart_raw_data', {})
        if cart_state and cart_state.get("items"):
            for item in cart_state["items"]:
                item_product = item.get("product", {})
                if item_product.get("sku") == sku:
                    categories_raw = item_product.get('categories', [])
                    categories = [cat.get('name') for cat in categories_raw if cat.get('name')] if categories_raw else []
                    price_range = item_product.get('price_range', {}).get('maximum_price', {})
                    canonical_url = item_product.get('canonical_url')
                    small_image = item_product.get('small_image', {})

                    # Build unique ID from art_no + store_code
                    art_no = item_product.get('art_no')
                    store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
                    # Extract store_code from store_id (e.g., 'b2c_10013_vi' -> '10013')
                    store_code = store_id.replace('b2c_', '').replace('_vi', '') if store_id else ''
                    tracking_id = f"{art_no}_{store_code}" if art_no and store_code else (item_product.get('id') or item_product.get('uid'))

                    removed_item_info = {
                        "id": tracking_id,
                        "name": item_product.get('ecom_name') or item_product.get('name'),
                        "sku": sku,
                        "page_url": f"{base_url}/{canonical_url}" if canonical_url else None,
                        "image_url": small_image.get('url') if isinstance(small_image, dict) else small_image,
                        "price": price_range.get('final_price', {}).get('value'),
                        "original_price": price_range.get('regular_price', {}).get('value'),
                        "main_category": categories[0] if len(categories) > 0 else None,
                        "category_level_1": categories[0] if len(categories) > 0 else None,
                        "category_level_2": categories[1] if len(categories) > 1 else None,
                        "brand": item_product.get('mm_brand'),
                        "quantity": item.get('quantity')
                    }
                    break

        return await _remove_cart_item(cart_item_id, tool_context, removed_item_info)

    except Exception as e:
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
            "code": "UNEXPECTED_ERROR"
        }


async def remove_everything_from_cart(tool_context: ToolContext) -> dict:
    """
    Remove all items from the USER's shopping cart (clear the entire cart).

    This tool removes every item from the user's cart, effectively emptying it.
    The cart belongs to the USER - you are clearing it on their behalf.

    This function:
    1. Retrieves current cart state
    2. Iterates through all cart items
    3. Removes each item one by one
    4. Returns the final empty cart state

    Args:
        tool_context: Tool execution context (automatically provided)

    Returns:
        dict with:
            - success (bool): Whether the operation succeeded
            - data (dict): Updated (empty) cart information if successful
            - message (str): Error message if failed
            - instruction_for_agent (str): Guidance for the agent on how to respond to the user

    Important:
        - Always refer to it as "your cart" or "the user's cart", NOT "my cart" or "the agent's cart"
        - This is a destructive operation - confirm with user before executing if not explicitly requested
        - If cart is already empty, inform user gracefully
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

        # get cart item IDs
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        cart_detail = get_current_cart_from_session_state(tool_context).get('cart_raw_data')
        if not cart_detail:
            # if not, call view cart tool to get the latest cart data, this will also update the session state
            await view_cart(tool_context=tool_context)
            cart_detail = get_current_cart_from_session_state(tool_context).get('cart_raw_data')
        if not cart_detail:
            return {
                "success": False,
                "message": "Cannot access cart data",
                'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
            }

        cart_items = cart_detail.get("items", [])
        cart_item_ids = [item.get("uid") for item in cart_items]
        if len(cart_item_ids) < 1:
            return {
                "success": True,
                "message": "Cart is already empty",
                "tracking": {
                    "items": []
                },
                'instruction_for_agent': "Inform the user that their cart is already empty.",
            }

        # Collect all items info for tracking before removing
        store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
        # Extract store_code from store_id (e.g., 'b2c_10013_vi' -> '10013')
        store_code = store_id.replace('b2c_', '').replace('_vi', '') if store_id else ''
        all_removed_items_info = []
        for item in cart_items:
            item_product = item.get("product", {})
            categories_raw = item_product.get('categories', [])
            categories = [cat.get('name') for cat in categories_raw if cat.get('name')] if categories_raw else []
            price_range = item_product.get('price_range', {}).get('maximum_price', {})
            canonical_url = item_product.get('canonical_url')
            small_image = item_product.get('small_image', {})

            # Build unique ID from art_no + store_code
            art_no = item_product.get('art_no')
            tracking_id = f"{art_no}_{store_code}" if art_no and store_code else (item_product.get('id') or item_product.get('uid'))

            all_removed_items_info.append({
                "action": "remove",
                "type": "product",
                "id": tracking_id,
                "name": item_product.get('ecom_name') or item_product.get('name'),
                "sku": item_product.get('sku'),
                "page_url": f"{base_url}/{canonical_url}" if canonical_url else None,
                "image_url": small_image.get('url') if isinstance(small_image, dict) else small_image,
                "price": price_range.get('final_price', {}).get('value'),
                "original_price": price_range.get('regular_price', {}).get('value'),
                "main_category": categories[0] if len(categories) > 0 else None,
                "category_level_1": categories[0] if len(categories) > 0 else None,
                "category_level_2": categories[1] if len(categories) > 1 else None,
                "brand": item_product.get('mm_brand'),
                "quantity": item.get('quantity')
            })

        signin_token = magento_session_data.get("signin_token") or ""
        signin_token = signin_token.strip('"')

        graphql_query = """
            mutation RemoveAllCartItems($cartId: String!) {
                removeAllCartItems(input: { cart_id: $cartId }) {
                    success
                }
            }
        """
        try:
            res = await make_graphql_request_async(
                graphql_query,
                {"cartId": magento_cart_id},
                base_url,
                store_id,
                auth_token=signin_token or None,
            )
        except Exception as e:
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "message": f"HTTP error: {str(e)}",
                'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
                "code": "HTTP_ERROR",
            }

        if not res or not res.get("data", {}).get("removeAllCartItems", {}).get("success"):
            logger.error(f"removeAllCartItems failed: {res}")
            return {
                "success": False,
                "message": "Cannot clear cart",
                'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
                "code": "INVALID_RESPONSE",
            }

        # Clear cart state
        tool_context.state['state']['current_cart_data'] = {
            'cart_raw_data': {'id': magento_cart_id, 'items': [], 'total_quantity': 0},
            'processed_cart_data': {'items': [], 'unique_product_count': 0, 'cart_subtotal_including_tax': '0 VND', 'cart_grand_total': '0 VND', 'cart_discounts': None},
            'invocation_id': tool_context.invocation_id
        }

        return {
            "success": True,
            "data": tool_context.state['state']['current_cart_data']['processed_cart_data'],
            "tracking": {"items": all_removed_items_info},
            "instruction_for_agent": "Cart cleared successfully. Inform the user their cart is now empty.",
        }
    except Exception as e:
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            'instruction_for_agent': "Inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
            "code": "UNEXPECTED_ERROR"
        }