import asyncio
import json
import logging
import traceback
import requests
import mmvn_b2c_agent.tools
from google.genai import types
from typing_extensions import override
from typing import Optional, Any
from google.adk.tools import ToolContext, BaseTool
from pydantic import BaseModel
import re
from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.tools.cng.cart.common import save_cart_to_state, normalize_quantity
from mmvn_b2c_agent.tools.cng.common import process_cart_data
from mmvn_b2c_agent.tools.cng.product import ProductDetailTool
from mmvn_b2c_agent.tools.cng.cart.quantity_limits import ProductQuantityLimitsTool
from mmvn_b2c_agent.tools.utils import make_graphql_request_async

logger = logging.getLogger(__name__)


async def add_product_to_cart(
    sku: str,
    quantity: float = 1.0,
    tool_context: Optional[ToolContext] = None
) -> dict[str, Any]:
    """
    Add a product to the USER's shopping cart (not the agent's cart).

    This tool adds products to the user's cart based on their SKU and desired quantity.
    The cart belongs to the USER - you are managing it on their behalf.

    Quantity is automatically rounded based on product type:
    - Fresh products (mm_product_type='F'): step 0.5 (e.g., 1.3 → 1.5, 1.7 → 1.5)
    - Normal products (mm_product_type='N'): step 1 (e.g., 1.3 → 1, 1.7 → 2)

    Args:
        sku: Product SKU code (format: two integers with underscore, e.g., "441976_24419765")
        quantity: Number of items to add (default: 1, can be float for fresh products)
        tool_context: Tool execution context (automatically provided)

    Returns:
        dict with:
            - success (bool): Whether the operation succeeded
            - data (dict): Updated cart information if successful
            - message (str): Error message if failed
            - instruction_for_agent (str): Guidance for the agent on how to respond to the user

    Important:
        - NEVER fabricate SKU values - they must come from product search results or user input
        - Always refer to it as "your cart" or "the user's cart", NOT "my cart" or "the agent's cart"
        - If adding fails, inform the user politely and suggest they try again
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                'instruction_for_agent': "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'. If the user asks again, retry this tool.",
                "code": "MISSING_TOOL_CONTEXT"
            }

        # DEBUG: Log state type
        logger.info(f"[DEBUG add_to_cart] tool_context.state type: {type(tool_context.state)}")

        # Try nested access first (state.state.magento_session_data)
        magento_session_data = (tool_context.state.get('state') or {}).get("magento_session_data", {})

        # Fallback: Try direct access (state.magento_session_data)
        if not magento_session_data:
            magento_session_data = tool_context.state.get("magento_session_data", {})

        logger.info(f"[DEBUG add_to_cart] magento_session_data: {magento_session_data}")
        store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        magento_cart_id = magento_session_data.get("magento_cart_id") or ""
        magento_cart_id = magento_cart_id.strip('"')
        signin_token = magento_session_data.get("signin_token") or ""
        signin_token = signin_token.strip('"')


        if not magento_cart_id:
            return {
                "success": False,
                "message": "Magento cart ID is missing in session data",
                'instruction_for_agent': "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'. If the user asks again, retry this tool.",
                "code": "MISSING_CART_ID",
            }

        # verify sku format
        if not isinstance(sku, str) or '_' not in sku and not all(part.isdigit() for part in sku.split('_')):
            return {
                "success": False,
                "message": "Invalid SKU format. SKU must be in the format 'number_number', e.g., '441976_24419765'.",
                "instruction_for_agent": "The SKU provided is invalid. Please ensure the SKU is correct and try again. NOTE: Do NOT fabricate SKU values; they must come from product search results or inferred from user input.",
                "code": "INVALID_SKU_FORMAT",
            }

        # check for age verification state
        age_verified = tool_context.state.get('age_verified', False)
        # check if the product needs age verification
        product_details = mmvn_b2c_agent.tools.get_product_details_from_search_history(sku, tool_context)
        if not product_details:
            logger.info(f"SKU {sku} not found in search history, fetching details.")
            try:
                get_detail_tool = ProductDetailTool()
                await get_detail_tool.run_async(args={"sku": sku}, tool_context=tool_context)
                product_details = mmvn_b2c_agent.tools.get_product_details_from_search_history(sku, tool_context)
                # If still None after fetching, set default
                if not product_details:
                    product_details = {'need_age_verification': False}
            except Exception as e:
                logger.error(f"add_product_to_cart failed to get product details for sku {sku}: "
                             f"{str(e)} {traceback.format_exc()}")
                product_details = {'need_age_verification': False}
        need_age_verified = product_details.get('need_age_verification', False)
        if need_age_verified and not age_verified:
            return {
                "success": False,
                "message": "Product requires age verification.",
                "code": "AGE_VERIFICATION_REQUIRED",
                "instruction_for_agent": "<EXTREMELY_IMPORTANT>Trigger the age verification tool for the user before doing anything else.</EXTREMELY_IMPORTANT>",
            }

        # --- Normalize quantity based on product type ---
        # Fresh products (mm_product_type='F') use step 0.5, Normal products use step 1
        product_type = product_details.get('product_type') or product_details.get('mm_product_type')
        logger.info(f"[ADD_TO_CART] product_details keys: {list(product_details.keys()) if product_details else 'None'}")
        logger.info(f"[ADD_TO_CART] product_type from details: {product_type}")

        original_quantity = quantity
        quantity, qty_step, was_rounded = normalize_quantity(quantity, product_type)

        logger.info(f"[ADD_TO_CART] Quantity: {original_quantity} → {quantity} "
                   f"(product_type={product_type}, step={qty_step}, rounded={was_rounded})")

        # --- Check quantity limits before adding ---
        # Run view_cart and quantity_limits in parallel so current_qty is available for
        # display messages without adding a sequential round trip.
        quantity_limit_info = None

        try:
            from mmvn_b2c_agent.tools.cng.cart.cart_view import view_cart
            quantity_limits_tool = ProductQuantityLimitsTool()
            _, limits_result = await asyncio.gather(
                view_cart(tool_context=tool_context),
                quantity_limits_tool.run_async(args={"skus": [sku]}, tool_context=tool_context),
            )

            logger.info(f"Quantity limits result for SKU {sku}: {limits_result}")

            if limits_result and limits_result.get("success") and limits_result.get("data"):
                limits = limits_result["data"].get(sku, {})
                max_qty = limits.get("max_qty")

                if max_qty is not None:
                    # IMPORTANT: max_qty from API is already the REMAINING quantity allowed
                    # Backend calculated: remaining = original_limit - current_qty_in_cart
                    # So we should NOT subtract current_qty again!

                    # Get current quantity in cart (for display message only)
                    cart_state = tool_context.state.get('state', {}).get('current_cart_data', {}).get('processed_cart_data', {})
                    current_qty = 0
                    if cart_state and cart_state.get("items"):
                        for item in cart_state["items"]:
                            # SKU is nested in product object
                            item_sku = item.get("product", {}).get("sku") if isinstance(item.get("product"), dict) else item.get("sku")
                            if item_sku == sku:
                                current_qty = item.get("quantity", 0)
                                break

                    # Check if quantity to add exceeds remaining limit
                    if quantity > max_qty:
                        # Calculate original limit: original = current + remaining
                        original_max_qty = current_qty + max_qty

                        # Get product name for better message
                        product_name = ""
                        try:
                            product_details = mmvn_b2c_agent.tools.get_product_details_from_search_history(sku, tool_context)
                            if product_details:
                                product_name = product_details.get('ecom_name') or product_details.get('name', '')
                        except:
                            pass

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

                        # Otherwise, add the maximum allowed quantity and inform user
                        # Continue with the reduced quantity instead of returning error
                        logger.info(f"[ADD_TO_CART] Reducing quantity from {quantity} to {max_qty} (remaining limit) for SKU {sku}")
                        original_requested_qty = quantity
                        quantity = max_qty  # Reduce to max allowed

                        # Store limit info to include in success response later
                        quantity_limit_info = {
                            "current_qty": current_qty,
                            "daily_limit": original_max_qty,
                            "remaining_qty": max_qty,
                            "requested_qty": original_requested_qty,
                            "product_name": product_name,
                            "added_qty": max_qty,
                            "not_added_qty": original_requested_qty - max_qty,
                            "partial_add": True
                        }
        except Exception as e:
            # Log error but don't block add to cart if quantity check fails
            logger.warning(f"Quantity limit check failed for SKU {sku}: {str(e)}")

        # --- Build GraphQL mutation ---
        graphql_query = """
            mutation AddProductsToCart($cartId: String!, $items: [CartItemInput!]!) {
                addProductsToCart(
                    cartId: $cartId,
                    use_art_no: true,
                    cartItems: $items
                ) {
                    cart {
                        id
                        total_summary_quantity_including_config
                        items {
                            uid
                            quantity
                            line_item_is_ai
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
                                price_including_tax {
                                    value
                                    currency
                                }
                                discounts {
                                    applied_to
                                    label
                                    amount {
                                        value
                                        currency
                                    }
                                }
                                row_total_including_tax {
                                    value
                                    currency
                                }
                                total_item_discount {
                                    value
                                    currency
                                }
                            }
                        }
                        prices {
                            subtotal_including_tax { value currency }
                            subtotal_with_discount_excluding_tax { value currency }
                            discounts { label amount { value currency } }
                            grand_total { value currency }
                        }
                    }
                    user_errors {
                        code
                        message
                    }
                }
            }
        """

        variables = {
            "cartId": magento_cart_id,
            "items": [{"sku": sku, "quantity": quantity, "line_item_is_ai": True}],
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
            if not res or not res.get("data"):
                logger.error(f"Cannot add product(s) to cart. \n"
                             f"Query:\n{graphql_query}\n\n"
                             f"Variables:\n{json.dumps(variables)}\n\n"
                             f"Full response:\n{json.dumps(res, indent=4)}\n\n")
                return {
                    "success": False,
                    "message": "API error: Back end error invalid data format",
                    'instruction_for_agent': "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'. If the user asks again, retry this tool.",
                    "code": "INVALID_RESPONSE"
                }

            # Check for GraphQL errors first
            graphql_errors = res.get("errors", [])
            if graphql_errors:
                error_message = graphql_errors[0].get("message", "Unknown GraphQL error")
                logger.error(f"GraphQL error when adding product to cart: {error_message}\n"
                             f"Query:\n{graphql_query}\n\n"
                             f"Variables:\n{json.dumps(variables)}\n\n"
                             f"Full response:\n{json.dumps(res, indent=4)}\n\n")

                # Check for specific error types
                if "Could not find a cart" in error_message:
                    return {
                        "success": False,
                        "message": "Cart not found",
                        "code": "CART_NOT_FOUND",
                        'instruction_for_agent': "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'. If the user asks again, retry this tool.",
                    }
                else:
                    return {
                        "success": False,
                        "message": f"GraphQL error: {error_message}",
                        "code": "GRAPHQL_ERROR",
                        'instruction_for_agent': "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'. If the user asks again, retry this tool.",
                    }

            # Safely navigate nested response structure
            data = res.get("data") or {}
            add_products_result = data.get("addProductsToCart") or {}
            cart = add_products_result.get("cart")

            if not cart:
                logger.error(f"Cannot add product(s) to cart (cart is None). "
                             f"Query:\n{graphql_query}\n\n"
                             f"Variables:\n{json.dumps(variables)}\n\n"
                             f"Full response:\n{json.dumps(res, indent=4)}\n\n")
                return {
                    "success": False,
                    "message": f"Cannot add product(s) to cart.",
                    'instruction_for_agent': "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'. If the user asks again, retry this tool.",
                }
        except requests.RequestException as e:
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "message": f"HTTP error: {str(e)}",
                'instruction_for_agent': "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'. If the user asks again, retry this tool.",
                "code": "HTTP_ERROR"
            }

        # Check for user errors (e.g., maximum quantity exceeded)
        user_errors = add_products_result.get("user_errors", [])

        # If there are user errors about max quantity, return error
        if user_errors:
            error_messages = [err.get("message", "") for err in user_errors]
            error_codes = [err.get("code", "") for err in user_errors]

            # Still save cart data even if there's an error (cart state might have changed)
            cart_data = cart or {}
            if cart_data:
                processed_cart_data = process_cart_data(cart_data)
                await save_cart_to_state(cart_data, processed_cart_data, tool_context)

            # Enhanced error message formatting for quantity limits
            units_pattern = r'(?:sản\s+phẩm|chai|thùng|hộp|kg|gam|lít|ml|cái|chiếc|bao|gói|lon)'
            formatted_error_messages = []
            quantity_limit_data = None  # Store structured data for multilingual formatting

            for msg in error_messages:
                # Check if this is a quantity limit error (English or Vietnamese)
                is_qty_limit_error = (
                    ("only" in msg.lower() and "quantity" in msg.lower()) or  # English: "only X quantity"
                    ("tối đa" in msg.lower() and "sản phẩm" in msg.lower()) or  # Vietnamese: "tối đa X sản phẩm"
                    ("giới hạn" in msg.lower())  # Vietnamese: "giới hạn"
                )

                if is_qty_limit_error:
                    # Extract daily limit from error message
                    # Try multiple patterns to match different formats:
                    # English: "You can buy only 10 quantity of..." → extract "10"
                    # Vietnamese: "Bạn chỉ có thể mua tối đa 10 sản phẩm..." → extract "10"
                    # Vietnamese: "...trên mỗi hóa đơn" or "...trên 1 hóa đơn"
                    patterns = [
                        r'only (\d+) quantity',           # English: "only 10 quantity"
                        r'tối đa (\d+)\s*sản phẩm',      # Vietnamese: "tối đa 10 sản phẩm"
                        r'mua (\d+)\s*sản phẩm',         # Vietnamese: "mua 10 sản phẩm"
                        r'(\d+)\s*sản phẩm.*trên',       # Vietnamese: "10 sản phẩm...trên mỗi hóa đơn"
                        r'giới hạn.*?(\d+)',             # Vietnamese: "giới hạn...10"
                        r'maximum.*?(\d+)',              # English: "maximum 10"
                    ]

                    limit_match = None
                    for pattern in patterns:
                        limit_match = re.search(pattern, msg, re.IGNORECASE)
                        if limit_match:
                            logger.info(f"Matched pattern '{pattern}' in message: {msg}")
                            break

                    if limit_match and cart_data:
                        daily_limit = int(limit_match.group(1))

                        # Get current quantity from cart for this SKU
                        current_qty = 0
                        product_name = ""
                        items = cart_data.get("items", [])
                        for item in items:
                            item_product = item.get("product", {})
                            if item_product.get("sku") == sku:
                                current_qty = item.get("quantity", 0)
                                product_name = item_product.get("ecom_name") or item_product.get("name", "")
                                break

                        # Calculate remaining quantity allowed
                        remaining_qty = daily_limit - current_qty

                        # Store structured data for agent to format in user's language
                        quantity_limit_data = {
                            "current_qty": current_qty,
                            "daily_limit": daily_limit,
                            "remaining_qty": remaining_qty,
                            "product_name": product_name,
                            "is_limit_reached": remaining_qty <= 0
                        }

                        # Add placeholder message (will be replaced by agent)
                        formatted_msg = "[QUANTITY_LIMIT_MESSAGE]"
                        formatted_error_messages.append(formatted_msg)
                        logger.info(f"Quantity limit data: current={current_qty}, limit={daily_limit}, remaining={remaining_qty}")
                    else:
                        # Fallback: Cannot extract limit from message
                        # Try to get current qty from cart and use generic data
                        logger.warning(f"Cannot extract quantity limit from message: {msg}")

                        current_qty = 0
                        product_name = ""
                        items = cart_data.get("items", [])
                        for item in items:
                            item_product = item.get("product", {})
                            if item_product.get("sku") == sku:
                                current_qty = item.get("quantity", 0)
                                product_name = item_product.get("ecom_name") or item_product.get("name", "")
                                break

                        if current_qty > 0:
                            # Have current qty, store partial data
                            quantity_limit_data = {
                                "current_qty": current_qty,
                                "daily_limit": None,  # Unknown
                                "remaining_qty": None,  # Unknown
                                "product_name": product_name,
                                "is_limit_reached": None  # Unknown
                            }
                            formatted_msg = "[QUANTITY_LIMIT_MESSAGE]"
                        else:
                            # No current qty info, use original message with bold formatting
                            formatted_msg = re.sub(rf'(tối đa \d+(?:\s*{units_pattern})?)', r'**\1**', msg, flags=re.IGNORECASE)
                            formatted_msg = re.sub(r'(maximum \d+(?:\s*(?:product|bottle|box|kg|gram|liter|ml|piece|pack|can))?)', r'**\1**', formatted_msg, flags=re.IGNORECASE)

                        formatted_error_messages.append(formatted_msg)
                else:
                    # Other error types: use existing bold formatting
                    formatted_msg = re.sub(rf'(tối đa \d+(?:\s*{units_pattern})?)', r'**\1**', msg, flags=re.IGNORECASE)
                    formatted_msg = re.sub(r'(maximum \d+(?:\s*(?:product|bottle|box|kg|gram|liter|ml|piece|pack|can))?)', r'**\1**', formatted_msg, flags=re.IGNORECASE)
                    formatted_error_messages.append(formatted_msg)

            formatted_message = formatted_error_messages[0] if formatted_error_messages else "Lỗi không xác định"

            # Build response with quantity limit data if available
            response = {
                "success": False,
                "message": "; ".join(formatted_error_messages),
                "user_errors": user_errors,
                "code": "MAX_QUANTITY_EXCEEDED" if any("only" in msg.lower() and "quantity" in msg.lower() for msg in error_messages) else error_codes[0] if error_codes else "USER_ERROR",
            }

            # Add structured data for multilingual formatting
            if quantity_limit_data:
                response["data"] = quantity_limit_data
                response["instruction_for_agent"] = "Format quantity limit message in user's detected language using data fields: current_qty, daily_limit, remaining_qty, product_name, is_limit_reached"
            else:
                response["instruction_for_agent"] = f"Thông báo cho người dùng: {formatted_message}. IMPORTANT: Use display_mode='markdown' to show the formatted message with bold text."

            return response

        # format the data returned to AI.
        cart_data = cart or {}
        processed_cart_data = process_cart_data(cart_data)
        await save_cart_to_state(cart_data, processed_cart_data, tool_context)

        # Build tracking data for FE - get from cart response (more reliable)
        tracking_items = []
        added_item = None
        for item in cart_data.get('items', []):
            item_product = item.get('product', {})
            if item_product.get('sku') == sku:
                added_item = item
                break

        if added_item:
            item_product = added_item.get('product', {})

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
                "action": "add",
                "type": "product",
                "id": tracking_id,
                "name": item_product.get('ecom_name') or item_product.get('name'),
                "sku": sku,
                "page_url": page_url,
                "image_url": image_url,
                "price": price_value,
                "original_price": original_price_value,
                "main_category": main_category,
                "category_level_1": category_level_1,
                "category_level_2": category_level_2,
                "brand": item_product.get('mm_brand'),
                "quantity": quantity
            }
            tracking_items.append(tracking_item)

        # Check if this was a partial add due to quantity limits
        if quantity_limit_info and quantity_limit_info.get("partial_add"):
            return {
                "success": True,
                "data": processed_cart_data,
                "partial_add": True,
                "quantity_limit_info": quantity_limit_info,
                "tracking": {
                    "items": tracking_items
                },
                "instruction_for_agent": (
                    "IMPORTANT: This was a PARTIAL add. Respond in the user's detected language. "
                    "MUST use first-person voice (e.g. 'Em đã thêm' in Vietnamese, 'I have added' in English — never passive/impersonal). "
                    f"Tell user: added {quantity_limit_info['added_qty']} items (max allowed), "
                    f"could not add {quantity_limit_info['not_added_qty']} items due to daily limit of {quantity_limit_info['daily_limit']}. "
                    "Show cart and checkout buttons."
                )
            }

        return {
            "success": True,
            "data": processed_cart_data,
            "tracking": {
                "items": tracking_items
            },
            "instruction_for_agent": (
                "<MANDATORY_NEXT_STEP>You MUST call `set_model_response` immediately after receiving this response. "
                "Do NOT output empty text or stop without calling set_model_response. "
                "Parameters: display_mode='cart', cart_data=<use the `data` field from this response>, "
                "show_cart_detail_cta_button=True, show_proceed_to_checkout_cta_button=True, "
                "message=<confirmation in user's language, e.g. 'Em đã thêm sản phẩm vào giỏ hàng rồi ạ!'>"
                "</MANDATORY_NEXT_STEP>"
            )
        }

    except Exception as e:
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            'instruction_for_agent': "Inform the user 'Có vẻ kết nối đang không ổn định lắm. Anh/chị vui lòng bắt đầu cuộc trò chuyện mới để tiếp tục đoạn hội thoại'. If the user asks again, retry this tool.",
            "code": "UNEXPECTED_ERROR",
        }
