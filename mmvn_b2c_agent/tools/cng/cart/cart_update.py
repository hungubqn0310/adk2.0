import asyncio
import json
import logging
import re
import traceback
import requests
from typing import Optional, Any
from google.adk.tools import ToolContext
from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.tools.cng.cart.cart_view import view_cart
from mmvn_b2c_agent.tools.cng.cart.common import (
    get_cart_item_id_from_sku,
    cart_item_id_exists,
    save_cart_to_state,
    normalize_quantity,
)
from mmvn_b2c_agent.tools.cng.common import process_cart_data
from mmvn_b2c_agent.tools.utils import make_graphql_request_async
from mmvn_b2c_agent.tools.cng.cart.quantity_limits import ProductQuantityLimitsTool
import mmvn_b2c_agent.tools

logger = logging.getLogger(__name__)

async def _update_cart_item_quantity(
    cart_item_id: int, quantity: float, tool_context: ToolContext, target_sku: str = None
) -> dict[str, Any]:
    """Update a cart item quantity by its ID.

    Quantity is automatically rounded based on product type:
    - Fresh products (mm_product_type='F'): step 0.5 (e.g., 1.3 → 1.5, 1.7 → 1.5)
    - Normal products (mm_product_type='N'): step 1 (e.g., 1.3 → 1, 1.7 → 2)
    """

    try:
        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        store_id = magento_session_data.get('store_id', DEFAULT_MMVN_STORE_ID)
        base_url = magento_session_data.get('base_url', DEFAULT_MMVN_STORE_URL).rstrip('/')
        signin_token = magento_session_data.get("signin_token") or ""
        signin_token = signin_token.strip('"')
        magento_cart_id = magento_session_data.get("magento_cart_id") or ""
        magento_cart_id = magento_cart_id.strip('"')
        if not magento_cart_id:
            return {
                "success": False,
                "message": "Magento cart ID is missing in session data",
                'instruction_for_agent': "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'. If the user asks again, retry this tool.",
                "code": "MISSING_CART_ID",
            }

        if quantity <= 0:
            return {
                "success": False,
                "message": "Invalid quantity provided.",
                "instruction_for_agent": (
                    "Ask the user if they want to remove the item from the cart instead. "
                    "If yes, call the remove-from-cart tool. "
                    "If no, ask them to provide a valid quantity greater than zero."
                ),
                "code": "INVALID_QUANTITY",
            }

        # --- Check quantity limits before updating (only if increasing quantity) ---
        # Only call view_cart if cart state not already populated (e.g. by cart_item_id_exists above)
        _existing_state = tool_context.state.get('state', {}).get('current_cart_data', {}).get('processed_cart_data', {})
        if not _existing_state or not _existing_state.get("items"):
            try:
                await view_cart(tool_context=tool_context)
                logger.info(f"Refreshed cart state before quantity validation for cart_item_id {cart_item_id}")
            except Exception as e:
                logger.warning(f"Failed to refresh cart before quantity check: {str(e)}")

        # Get current quantity and SKU from cart state
        cart_state = tool_context.state.get('state', {}).get('current_cart_data', {}).get('processed_cart_data', {})
        current_qty = 0
        sku = None
        product_name = ""
        product_type = None

        if cart_state and cart_state.get("items"):
            for item in cart_state["items"]:
                # Match by cart_item_id (uid)
                if item.get("uid") == cart_item_id:
                    current_qty = item.get("quantity", 0)
                    # SKU is nested in product object
                    product_obj = item.get("product", {}) if isinstance(item.get("product"), dict) else {}
                    sku = product_obj.get("sku") or item.get("sku")
                    product_name = product_obj.get("ecom_name") or product_obj.get("name", "")
                    product_type = product_obj.get("product_type") or product_obj.get("mm_product_type")
                    break

        # Keep original quantity - don't normalize, let API handle it
        original_quantity = quantity
        logger.info(f"[UPDATE_CART] Sending user-requested quantity={quantity} to API (no rounding)")

        # Calculate delta (positive = increasing, negative = decreasing)
        delta = quantity - current_qty

        if not sku:
            logger.warning(f"Could not find SKU for cart_item_id {cart_item_id} in cart state")
            # Continue without quantity check (will rely on M2 error handling)

        # Initialize quantity_limit_info for partial update tracking
        quantity_limit_info = None

        # Only check limits if quantity is increasing
        if sku and delta > 0:
            try:
                # Get quantity limits for this product
                quantity_limits_tool = ProductQuantityLimitsTool()
                limits_result = await quantity_limits_tool.run_async(
                    args={"skus": [sku]},
                    tool_context=tool_context
                )

                logger.info(f"Quantity limits result for SKU {sku}: {limits_result}")

                if limits_result and limits_result.get("success") and limits_result.get("data"):
                    limits = limits_result["data"].get(sku, {})
                    max_qty = limits.get("max_qty")

                    if max_qty is not None:
                        # IMPORTANT: max_qty from API is already the REMAINING quantity allowed
                        # Backend calculated: remaining = original_limit - current_qty_in_cart

                        # Check if delta exceeds remaining limit
                        if delta > max_qty:
                            # Calculate original limit: original = current + remaining
                            original_max_qty = current_qty + max_qty

                            # If max_qty <= 0, limit is already reached - cannot add any
                            if max_qty <= 0:
                                return {
                                    "success": False,
                                    "message": f"Quantity limit reached for product {sku}",
                                    "code": "QUANTITY_LIMIT_REACHED",
                                    "data": {
                                        "current_qty": current_qty,
                                        "daily_limit": original_max_qty,
                                        "remaining_qty": 0,
                                        "requested_qty": quantity,
                                        "product_name": product_name,
                                        "is_limit_reached": True,
                                        "added_qty": 0
                                    },
                                    "instruction_for_agent": "Inform user that limit is already reached. Format message in user's detected language using data fields: current_qty, daily_limit, product_name"
                                }

                            # Otherwise, update to the maximum allowed quantity and inform user
                            # Continue with the reduced quantity instead of returning error
                            max_allowed_quantity = current_qty + max_qty  # current + remaining = max allowed
                            logger.info(f"[UPDATE_CART] Reducing quantity from {quantity} to {max_allowed_quantity} (max allowed) for SKU {sku}")
                            original_requested_qty = quantity
                            quantity = max_allowed_quantity  # Reduce to max allowed

                            # Store limit info to include in success response later
                            quantity_limit_info = {
                                "current_qty": current_qty,
                                "daily_limit": original_max_qty,
                                "remaining_qty": max_qty,
                                "requested_qty": original_requested_qty,
                                "product_name": product_name,
                                "updated_to_qty": max_allowed_quantity,
                                "not_added_qty": original_requested_qty - max_allowed_quantity,
                                "partial_update": True
                            }
            except Exception as e:
                # Log error but don't block update if quantity check fails
                logger.warning(f"Quantity limit check failed for SKU {sku}: {str(e)}")

        graphql_query = """
            mutation UpdateCartItems($cartId: String!, $items: [CartItemUpdateInput!]!) {
                updateCartItems(input: { cart_id: $cartId, cart_items: $items }) {
                    cart {
                        id
                        total_summary_quantity_including_config
                        items {
                            uid
                            quantity
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
                                categories { uid name }
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
            }
        """

        variables = {
            "cartId": magento_cart_id,
            "items": [{"cart_item_uid": cart_item_id, "quantity": quantity}],
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
        except requests.RequestException as e:
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "message": f"HTTP error: {str(e)}",
                "code": "HTTP_ERROR",
            }

        if not res:
            logger.error("API request returned None")
            return {
                "success": False,
                "message": "Không lấy được thông tin giỏ hàng hiện tại.",
                "instruction_for_agent": "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'.",
                "code": "NO_RESPONSE",
            }

        # Check for GraphQL errors (errors field, not user_errors)
        graphql_errors = res.get("errors", [])
        if graphql_errors:
            error_messages = [err.get("message", "") for err in graphql_errors]
            logger.error(f"GraphQL errors:\n{json.dumps(graphql_errors, indent=4)}")

            # Check if it's a quantity limit error
            first_error = error_messages[0] if error_messages else ""
            if "only" in first_error.lower() and "quantity" in first_error.lower():
                return {
                    "success": False,
                    "message": first_error,
                    "code": "MAX_QUANTITY_EXCEEDED",
                    "instruction_for_agent": f"Inform user about quantity limit: {first_error}"
                }

            return {
                "success": False,
                "message": "; ".join(error_messages),
                "code": "GRAPHQL_ERROR",
                "instruction_for_agent": f"Inform user about error: {first_error}"
            }

        # Safely get data field
        data = res.get("data")
        if not data or not isinstance(data, dict):
            logger.error(f"Invalid data field in response:\n{json.dumps(res, indent=4)}")
            return {
                "success": False,
                "message": "Không lấy được thông tin giỏ hàng hiện tại.",
                "instruction_for_agent": "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'.",
                "code": "INVALID_DATA",
            }

        update_result = data.get("updateCartItems")
        if not update_result or not isinstance(update_result, dict) or not update_result.get("cart"):
            logger.error(f"Empty or invalid cart data:\n{json.dumps(res, indent=4)}")
            return {
                "success": False,
                "message": "Không lấy được thông tin giỏ hàng hiện tại.",
                "instruction_for_agent": "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'.",
                "code": "INVALID_RESPONSE",
            }

        # Save cart data and return success
        cart_data = update_result["cart"]
        processed_cart_data = process_cart_data(cart_data)
        await save_cart_to_state(cart_data, processed_cart_data, tool_context)

        # Find actual quantity from cart response for the updated item
        # Try to match by target_sku (from function param), sku (from cart state), or uid
        actual_quantity = None
        updated_product_name = None
        updated_item = None
        for item in cart_data.get("items", []):
            product_info = item.get("product", {})
            item_sku = product_info.get("sku")
            item_uid = item.get("uid")

            # Match by target_sku (preferred), then sku from cart state, then uid
            if (target_sku and item_sku == target_sku) or (sku and item_sku == sku) or (item_uid == cart_item_id):
                actual_quantity = item.get("quantity")
                updated_product_name = product_info.get("ecom_name") or product_info.get("name", "")
                updated_item = item
                logger.info(f"[UPDATE_CART] Found updated item: SKU={item_sku}, actual_qty={actual_quantity}, name={updated_product_name}")
                break

        # Build tracking data for FE
        tracking_items = []
        if updated_item:
            item_product = updated_item.get('product', {})

            # Extract category information from cart response
            categories_raw = item_product.get('categories', [])
            categories = [cat.get('name') for cat in categories_raw if cat.get('name')] if categories_raw else []
            main_category = categories[0] if len(categories) > 0 else None
            category_level_1 = categories[0] if len(categories) > 0 else None
            category_level_2 = categories[1] if len(categories) > 1 else None

            # Extract price information from cart response
            price_range = item_product.get('price_range', {}).get('maximum_price', {})
            price_value = price_range.get('final_price', {}).get('value')
            original_price_value = price_range.get('regular_price', {}).get('value')

            # Build page_url from canonical_url
            canonical_url = item_product.get('canonical_url')
            page_url = f"{base_url}/{canonical_url}" if canonical_url else None

            # Get image_url from small_image
            small_image = item_product.get('small_image', {})
            image_url = small_image.get('url') if isinstance(small_image, dict) else small_image

            # Build unique ID from art_no + store_code
            art_no = item_product.get('art_no')
            # Extract store_code from store_id (e.g., 'b2c_10013_vi' -> '10013')
            store_code = store_id.replace('b2c_', '').replace('_vi', '') if store_id else ''
            tracking_id = f"{art_no}_{store_code}" if art_no and store_code else (item_product.get('id') or item_product.get('uid'))

            tracking_item = {
                "action": "update",
                "type": "product",
                "id": tracking_id,
                "name": item_product.get('ecom_name') or item_product.get('name'),
                "sku": item_product.get('sku'),
                "page_url": page_url,
                "image_url": image_url,
                "price": price_value,
                "original_price": original_price_value,
                "main_category": main_category,
                "category_level_1": category_level_1,
                "category_level_2": category_level_2,
                "brand": item_product.get('mm_brand'),
                "quantity": actual_quantity
            }
            tracking_items.append(tracking_item)

        # Build instruction - tell user the ACTUAL quantity in cart (from cart response)
        # This ensures agent message matches what user sees in cart UI
        display_quantity = actual_quantity if actual_quantity is not None else original_quantity

        # Check if this was a partial update due to quantity limits
        if quantity_limit_info and quantity_limit_info.get("partial_update"):
            return {
                "success": True,
                "data": processed_cart_data,
                "actual_quantity": actual_quantity,
                "requested_quantity": original_quantity,
                "updated_product_name": updated_product_name,
                "partial_update": True,
                "quantity_limit_info": quantity_limit_info,
                "tracking": {
                    "items": tracking_items
                },
                "instruction_for_agent": (
                    "IMPORTANT: This was a PARTIAL update. Respond in the user's detected language. "
                    "MUST use first-person voice (e.g. 'Em đã cập nhật' in Vietnamese, 'I have updated' in English — never passive/impersonal). "
                    f"Tell user: updated quantity to {quantity_limit_info['updated_to_qty']} items (max allowed), "
                    f"could not add {quantity_limit_info['not_added_qty']} more items due to daily limit of {quantity_limit_info['daily_limit']}. "
                    "Show cart and checkout buttons."
                )
            }

        instruction = (
            "Cart updated successfully. "
            "Set the display mode to cart if needed and show them the updated cart details and present suitable CTA buttons. "
            f"Tell user the quantity has been updated to {display_quantity}. "
        )
        if updated_product_name:
            instruction += f"Product: {updated_product_name}. "

        return {
            "success": True,
            "data": processed_cart_data,
            "actual_quantity": actual_quantity,
            "requested_quantity": original_quantity,
            "updated_product_name": updated_product_name,
            "tracking": {
                "items": tracking_items
            },
            "instruction_for_agent": instruction,
        }

    except Exception as e:
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "code": "UNEXPECTED_ERROR",
        }


async def update_cart_with_row_id(
    cart_item_id: int, quantity: float, tool_context: ToolContext, target_sku: str = None
) -> dict[str, Any]:
    """
    Update the quantity of an item in the USER's shopping cart by its cart item ID.

    The cart belongs to the USER - you are managing it on their behalf.

    Quantity is automatically rounded based on product type:
    - Fresh products (mm_product_type='F'): step 0.5 (e.g., 1.3 → 1.5, 1.7 → 1.5)
    - Normal products (mm_product_type='N'): step 1 (e.g., 1.3 → 1, 1.7 → 2)

    Args:
        cart_item_id: Unique identifier for the cart item (get this from view_cart results)
        quantity: New quantity to set (can be float for fresh products)
        tool_context: Tool execution context (automatically provided)
        target_sku: Optional SKU for matching the updated item in response

    Returns:
        dict with:
            - success (bool): Whether the operation succeeded
            - data (dict): Updated cart information if successful
            - message (str): Error message if failed
            - instruction_for_agent (str): Guidance for the agent on how to respond to the user

    Important:
        - NEVER fabricate cart_item_id - must come from view_cart tool results
        - Always refer to it as "your cart" or "the user's cart", NOT "my cart" or "the agent's cart"
        - If quantity is 0 or negative, suggest removing the item instead
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                'instruction_for_agent': "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'. If the user asks again, retry this tool.",
                "code": "MISSING_TOOL_CONTEXT"
            }

        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        magento_cart_id = magento_session_data.get("magento_cart_id") or ""
        magento_cart_id = magento_cart_id.strip('"')
        if not magento_cart_id:
            return {
                "success": False,
                "message": "Magento cart ID is missing in session data",
                'instruction_for_agent': "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'. If the user asks again, retry this tool.",
                "code": "MISSING_CART_ID",
            }

        # Verify item id exists in cart
        found_cart_item = await cart_item_id_exists(cart_item_id, tool_context)
        if not found_cart_item:
            return {
                "success": False,
                "message": f"Cart item ID {cart_item_id} not found in cart.",
                'instruction_for_agent': "Double check and try to input a valid cart item id, or inform the user 'Hiện em không truy cập được giỏ hàng, anh/chị thử lại sau ít phút nhé.'. If the user asks again, retry this tool.",
                "code": "CART_ITEM_NOT_FOUND"
            }

        return await _update_cart_item_quantity(cart_item_id, quantity, tool_context, target_sku=target_sku)

    except Exception as e:
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            'instruction_for_agent': "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'. If the user asks again, retry this tool.",
            "code": "UNEXPECTED_ERROR"
        }


async def update_cart_with_product_sku(
    sku: str, quantity: float, tool_context: ToolContext
) -> dict[str, Any]:
    """
    Update the quantity of a product in the USER's shopping cart by its SKU.

    This is a convenience function that finds the cart item ID from the SKU,
    then updates its quantity. The cart belongs to the USER - you are managing it on their behalf.

    Quantity is automatically rounded based on product type:
    - Fresh products (mm_product_type='F'): step 0.5 (e.g., 1.3 → 1.5, 1.7 → 1.5)
    - Normal products (mm_product_type='N'): step 1 (e.g., 1.3 → 1, 1.7 → 2)

    Args:
        sku: Product SKU code (format: two integers with underscore, e.g., "441976_24419765")
        quantity: New quantity to set (can be float for fresh products)
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
        - If quantity is 0 or negative, suggest removing the item instead
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                'instruction_for_agent': "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'. If the user asks again, retry this tool.",
                "code": "MISSING_TOOL_CONTEXT"
            }

        cart_item_id_result = await get_cart_item_id_from_sku(sku, tool_context)
        if not cart_item_id_result.get("success"):
            return cart_item_id_result

        cart_item_id = cart_item_id_result.get("cart_item_id")
        return await update_cart_with_row_id(cart_item_id, quantity, tool_context, target_sku=sku)

    except Exception as e:
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            'instruction_for_agent': "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'. If the user asks again, retry this tool.",
            "code": "UNEXPECTED_ERROR"
        }