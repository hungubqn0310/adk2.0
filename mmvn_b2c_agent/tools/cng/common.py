import copy
import datetime
import json
from typing import Any, Optional

from google.adk.agents import InvocationContext

import mmvn_b2c_agent.shared.constants
import mmvn_b2c_agent.shared.schema as mmvn_schema
from pydantic import BaseModel
from google.adk.tools import ToolContext


def format_date_to_ddmmyyyy(date_str: Optional[str]) -> Optional[str]:
    """
    Format date from YYYY-MM-DD to DD/MM/YYYY with leading zeros

    Supports multiple input formats:
    - YYYY-MM-DD (e.g., "2025-08-31")
    - YYYY-MM-DD HH:MM:SS (e.g., "2025-08-31 14:30:00")
    - DD/MM/YYYY (already formatted, returns as-is if valid)

    Args:
        date_str: Date string in various formats

    Returns:
        Formatted date string DD/MM/YYYY with leading zeros (e.g., "01/11/2025", "31/08/2025")
        Returns original string if parsing fails
    """
    if not date_str:
        return date_str

    # Strip whitespace
    date_str = date_str.strip()

    # If date contains time component (space), extract only the date part
    if " " in date_str:
        date_str = date_str.split(" ")[0]

    # Try multiple date formats
    date_formats = [
        "%Y-%m-%d",      # YYYY-MM-DD
        "%d/%m/%Y",      # DD/MM/YYYY (already formatted)
        "%Y/%m/%d",      # YYYY/MM/DD
    ]

    for fmt in date_formats:
        try:
            date_obj = datetime.datetime.strptime(date_str, fmt)
            # Always return in DD/MM/YYYY format with leading zeros
            return date_obj.strftime("%d/%m/%Y")
        except (ValueError, AttributeError):
            continue

    # If all formats fail, return original string
    return date_str


def process_product_search_data(
        products_raw_data: list[dict],
        base_url: str = mmvn_b2c_agent.shared.constants.DEFAULT_MMVN_STORE_URL,
        filter_only_discounted: bool = False
) -> list[dict]:
    """Process raw product data from Magento API to a more AI-friendly format.

    Args:
        products_raw_data: Raw product data from Magento API
        base_url: Base URL of the Magento store
        filter_only_discounted: If True, only return products that have valid discount information

    Returns:
        List of processed product dictionaries
    """
    processed_products = copy.deepcopy(products_raw_data)
    for prod in processed_products:
        # Turn the price dict into a string
        if "price_range" in prod:
            # price_amount = prod.get("price", {}).get("regularPrice", {}).get("amount", {}).get("value")
            # price_currency = prod.get("price", {}).get("regularPrice", {}).get("amount", {}).get("currency")
            # price_str = f"{price_amount} {price_currency}" if price_amount and price_currency else "N/A"
            # prod["price"] = price_str

            final_price = prod.get("price_range", {}).get("maximum_price", {}).get("final_price", {}).get("value")
            final_price_currency = prod.get("price_range", {}).get("maximum_price", {}).get("final_price", {}).get("currency")
            final_price_str = f"{final_price} {final_price_currency}" if final_price and final_price_currency else "N/A"
            prod["price"] = prod["final_price"] = final_price_str

            # Extract discount information
            discount_info = prod.get("price_range", {}).get("maximum_price", {}).get("discount", {})
            discounted_amount = discount_info.get("amount_off")
            discount_percent = discount_info.get("percent_off")

            if discounted_amount:
                prod["discounted_amount"] = f"{discounted_amount} {final_price_currency}"
            if discount_percent:
                prod["discount_percent"] = f"{discount_percent}%"

        # Use ecom_name if available, otherwise fallback to name
        final_name = prod.get('ecom_name') or prod.get('name') or ""
        prod.pop('name', None)
        prod.pop('ecom_name', None)
        prod['name'] = final_name

        # rename 'unit_ecom' to 'unit'
        if 'unit_ecom' in prod:
            prod['unit'] = prod.pop('unit_ecom', '')

        # rename 'mm_product_type' to 'product_type' and add qty_step
        # 'F' = Fresh products (step 0.5), 'N' = Normal products (step 1)
        if 'mm_product_type' in prod:
            product_type = prod.pop('mm_product_type', 'N')
            prod['product_type'] = product_type
            prod['qty_step'] = 0.5 if product_type == 'F' else 1.0
        else:
            prod['qty_step'] = 1.0  # Default to integer step

        # rename 'is_alcohol' to 'need_age_verification'
        if 'is_alcohol' in prod:
            prod['need_age_verification'] = prod.pop('is_alcohol', False)

        # get the full product URL from canonical_url
        if "canonical_url" in prod:
            canonical_url = prod.pop('canonical_url')
            prod["product_url"] = f"{base_url}/{canonical_url}"


        # unpack description html
        if 'description' in prod:
            prod['description'] = prod.get('description', {}).get('html', '')
        if 'short_description' in prod:
            prod['short_description'] = prod.get('short_description', {}).get('html', '')

        # unpack categories, keep only the category names
        if prod.get('categories') and isinstance(prod['categories'], list):
            category_names = [cat.get('name') for cat in prod['categories'] if cat.get('name')]
            prod['categories'] = category_names

        # handle images
        if 'small_image' in prod:
            prod['small_image'] = prod['small_image'].get('url')
        if 'image' in prod:
            prod['image'] = prod['image'].get('url')
        if 'media_gallery' in prod:
            image_urls = [img.get('url') for img in prod['media_gallery'] if img.get('url')]
            prod['media_gallery'] = image_urls

        # Process DNR price tiers
        if 'dnr_price' in prod:
            dnr_price = prod.pop('dnr_price')
            if dnr_price:
                prod['dnr_info'] = dnr_price

        # Remove unused dnr fields
        prod.pop('dnr_price_search_page', None)
        prod.pop('dnr_promotion', None)

    if filter_only_discounted:
        def has_valid_promotion(prod):
            # Check if has direct discount (amount_off > 0 or percent_off > 0)
            if 'discounted_amount' in prod and prod['discounted_amount']:
                return True
            if 'discount_percent' in prod and prod['discount_percent']:
                return True

            # Check if has valid dnr_info (non-empty list)
            if 'dnr_info' in prod and isinstance(prod['dnr_info'], list) and len(prod['dnr_info']) > 0:
                return True

            return False

        processed_products = [
            prod for prod in processed_products
            if has_valid_promotion(prod)
        ]

    return processed_products


def process_product_search_data_optimized(
        products_raw_data: list[dict],
) -> list[dict]:
    """Process raw product search data with optimized response structure.

    This is specifically for product search tools to return cleaner, more compact data.
    Removes nested structures like price_range after extracting values.
    Also removes duplicate fields and null/empty values to minimize token usage.

    Args:
        products_raw_data: Raw product data from Magento API
        base_url: Base URL of the Magento store
        filter_only_discounted: If True, only return products that have valid discount information

    Returns:
        List of processed product dictionaries with optimized structure
    """
    processed_products = copy.deepcopy(products_raw_data)
    for prod in processed_products:
        # Turn the price dict into a string and keep price_range structure
        if "price_range" in prod:
            maximum_price = prod.get("price_range", {}).get("maximum_price", {})
            final_price = maximum_price.get("final_price", {}).get("value")
            final_price_currency = maximum_price.get("final_price", {}).get("currency")
            final_price_str = f"{final_price} {final_price_currency}" if final_price and final_price_currency else "N/A"
            prod["price"] = prod["final_price"] = final_price_str

            # Extract regular_price (original price before discount)
            regular_price = maximum_price.get("regular_price", {}).get("value")
            if regular_price and final_price_currency:
                prod["regular_price"] = f"{regular_price} {final_price_currency}"

            # Extract discount information
            discount_info = maximum_price.get("discount", {})
            discounted_amount = discount_info.get("amount_off")
            discount_percent = discount_info.get("percent_off")

            if discounted_amount:
                prod["discounted_amount"] = f"{discounted_amount} {final_price_currency}"
            if discount_percent:
                prod["discount_percent"] = f"{discount_percent}%"

        # Use ecom_name if available, otherwise fallback to name
        final_name = prod.get('ecom_name') or prod.get('name') or ""
        prod.pop('name', None)
        prod.pop('ecom_name', None)
        prod['name'] = final_name

        # rename 'unit_ecom' to 'unit'
        if 'unit_ecom' in prod:
            prod['unit'] = prod.pop('unit_ecom', '')

        # rename 'mm_product_type' to 'product_type' and add qty_step
        # 'F' = Fresh products (step 0.5), 'N' = Normal products (step 1)
        if 'mm_product_type' in prod:
            product_type = prod.pop('mm_product_type', 'N')
            prod['product_type'] = product_type
            prod['qty_step'] = 0.5 if product_type == 'F' else 1.0
        else:
            prod['qty_step'] = 1.0  # Default to integer step

        # rename 'is_alcohol' to 'need_age_verification', only keep if True
        if 'is_alcohol' in prod:
            need_verification = prod.pop('is_alcohol', False)
            if need_verification:
                prod['need_age_verification'] = need_verification

        # Remove canonical_url
        prod.pop('canonical_url', None)

        # unpack description html
        if 'description' in prod:
            prod['description'] = prod.get('description', {}).get('html', '')
        if 'short_description' in prod:
            prod['short_description'] = prod.get('short_description', {}).get('html', '')

        # unpack categories, keep only the category names
        if prod.get('categories') and isinstance(prod['categories'], list):
            category_names = [cat.get('name') for cat in prod['categories'] if cat.get('name')]
            prod['categories'] = category_names

        # handle images
        if 'small_image' in prod:
            prod['small_image'] = prod['small_image'].get('url')
        if 'image' in prod:
            prod['image'] = prod['image'].get('url')
        if 'media_gallery' in prod:
            image_urls = [img.get('url') for img in prod['media_gallery'] if img.get('url')]
            prod['media_gallery'] = image_urls

        # Process DNR price tiers
        if 'dnr_price' in prod:
            promo_info = prod.pop('dnr_price')
            if promo_info and isinstance(promo_info, list) and len(promo_info) > 0:
                # Only keep first 2 tiers to save tokens
                prod['dnr_info'] = promo_info[:2]
            elif promo_info:
                prod['dnr_info'] = promo_info

        # Remove unused dnr fields
        prod.pop('dnr_price_search_page', None)
        prod.pop('dnr_promotion', None)

        # Remove null, empty string, and empty dict/list values to optimize response
        keys_to_remove = []
        for key, value in prod.items():
            if value is None or value == '' or value == {} or value == []:
                keys_to_remove.append(key)
        for key in keys_to_remove:
            prod.pop(key, None)
    return processed_products


def process_cart_data(raw_cart_data: dict) -> dict:
    """Process raw cart data from Magento API to a more AI-friendly format."""
    if not raw_cart_data:
        return {}
    processed_cart = copy.deepcopy(raw_cart_data)
    processed_cart.pop('id', '')

    # process cart prices
    if 'prices' in processed_cart:
        cart_subtotal_including_tax = processed_cart['prices'].get('subtotal_including_tax') or {}
        cart_grand_total = processed_cart['prices'].get('grand_total') or {}
        cart_discounts = processed_cart['prices'].get('discounts') or []

        if 'total_summary_quantity_including_config' in processed_cart:
            processed_cart['unique_product_count'] = processed_cart.pop('total_summary_quantity_including_config')

        if 'subtotal_including_tax' in processed_cart.get('prices', {}):
            cart_subtotal_including_tax = (f"{cart_subtotal_including_tax.get('value', '')} "
                                           f"{cart_subtotal_including_tax.get('currency', '')}")
            processed_cart['cart_subtotal_including_tax'] = cart_subtotal_including_tax
            processed_cart['prices'].pop('subtotal_including_tax', None)
        if 'grand_total' in processed_cart.get('prices', {}):
            cart_grand_total = f"{cart_grand_total.get('value', '')} {cart_grand_total.get('currency', '')}"
            processed_cart['cart_grand_total'] = cart_grand_total
            processed_cart['prices'].pop('grand_total', None)
        if 'discounts' in processed_cart.get('prices', {}):
            cart_discounts = [
                f"{disc.get('label', '')}: {disc.get('amount', {}).get('value', '')} {disc.get('amount', {}).get('currency', '')}"
                for disc in cart_discounts
            ] or None
            processed_cart['cart_discounts'] = cart_discounts
            processed_cart['prices'].pop('discounts', None)

        processed_cart.pop('prices', None)  # remove prices if exists and not needed

    # process cart items
    for item in processed_cart.get('items', []):
        # process product info
        item['id'] = item.pop('uid', '')
        if 'product' in item:
            product_code = item['product'].pop('name', '')
            product_name = item['product'].pop('ecom_name', '')
            # if product_code:
            #     item['product'] ['product_code'] = product_code
            item['product']['name'] = product_name or product_code or ''

            # unpack categories, keep only the category names
            if item['product'].get('categories') and isinstance(item['product']['categories'], list):
                category_names = [cat.get('name') for cat in item['product']['categories'] if cat.get('name')]
                item['product']['categories'] = category_names

            if 'dnr_price' in item['product']:
                available_promo = item['product'].pop('dnr_price')
                item['product']['available_promo'] = available_promo
        # process item prices
        if 'prices' in item:
            price_per_item_including_tax = item['prices'].get('price_including_tax') or {}
            row_applied_discount = item['prices'].get('discounts') or []
            row_total_including_tax = item['prices'].get('row_total_including_tax') or {}
            row_total_discounts = item['prices'].get('total_item_discount') or {}

            if 'price_including_tax' in item.get('prices', {}):
                price_per_item_including_tax = (f"{price_per_item_including_tax.get('value', '')} "
                                                f"{price_per_item_including_tax.get('currency', '')}")
                item['price_per_item_including_tax'] = price_per_item_including_tax
                item['prices'].pop('price_per_item_including_tax', None)
            if 'discounts' in item.get('prices', {}):
                row_applied_discount = [
                    f"{disc.get('label', '')}: {disc.get('amount', {}).get('value', '')} {disc.get('amount', {}).get('currency', '')}"
                    for disc in row_applied_discount
                ] or None
                item['row_applied_discount'] = row_applied_discount
                item['prices'].pop('discounts', None)
            if 'row_total_including_tax' in item.get('prices', {}):
                row_total_including_tax = (f"{row_total_including_tax.get('value', '')} "
                                           f"{row_total_including_tax.get('currency', '')}")
                item['row_total_including_tax'] = row_total_including_tax
                item['prices'].pop('row_total_including_tax', None)
            if 'total_item_discount' in item.get('prices', {}):
                row_total_discounts = (f"{row_total_discounts.get('value', '')} "
                                       f"{row_total_discounts.get('currency', '')}")
                item['row_total_discounts'] = row_total_discounts
                item['prices'].pop('total_item_discount', None)
            item.pop('prices', None)
    # processed_cart['rows'] = processed_cart.pop('items', [])

    return processed_cart


def save_search_result_to_session_state(
        args: BaseModel | str,
        search_result: list | dict,
        tool_context: ToolContext,
):
    """Add search result products to the session state for context."""
    if isinstance(args, BaseModel):
        search_key = json.dumps(args.model_dump(), sort_keys=True)
    else:
        search_key = str(args)

    # Calculate product count
    if isinstance(search_result, list):
        product_count = len(search_result)
    elif isinstance(search_result, dict):
        # For dict responses (like error responses), count products in 'data' field if exists
        product_count = len(search_result.get('data', []))
    else:
        product_count = 0

    delta = {
        search_key: {
            "invocation_id": tool_context.invocation_id,
            "search_date": datetime.datetime.now().isoformat(),
            "result": search_result,
            "product_count": product_count
        }
    }
    if isinstance(tool_context, InvocationContext):
        state = tool_context.session.state
    else:
        state = tool_context.state
    if not state.get('search_result_history'):
        state['search_result_history'] = delta
    else:
        # Handle both dict and ToolContext.state types
        if hasattr(state, 'to_dict'):
            pending_delta = state.to_dict()
        else:
            pending_delta = dict(state)

        if 'search_result_history' not in pending_delta:
            pending_delta['search_result_history'] = {}
        pending_delta['search_result_history'].update(delta)
        state.update(pending_delta)

    return delta

def get_product_details_from_search_history(
        sku: str,
        tool_context: ToolContext | dict,
):
    """Retrieve product details from previous search results in session state."""
    if isinstance(tool_context, ToolContext):
        state = tool_context.state
    elif isinstance(tool_context, dict):
        state = tool_context
    else:
        raise TypeError("tool_context must be a ToolContext or dict")
    if 'search_result_history' not in state:
        return None
    # sort search history by search date descending
    sorted_history = sorted(
        list(state['search_result_history'].values()),
        key=lambda item: item.get('search_date', ''),
        reverse=True
    )
    for search_data in sorted_history:
        # limit to the search results more recent than 6h
        search_date = datetime.datetime.fromisoformat(search_data.get('search_date'))
        if (datetime.datetime.now() - search_date).total_seconds() > 6 * 60 * 60:
            continue

        result = search_data.get('result', [])
        # Handle new format: result can be array (success) or dict (error)
        if isinstance(result, dict):
            # Error case: result = {success: false, code: "NO_PRODUCTS", data: [], ...}
            # Skip error responses, they don't contain products
            continue

        # Success case: result is array of products
        for prod in result:
            if prod.get('sku') == sku:
                return prod
    return None


def get_current_cart_from_session_state(
        tool_context: ToolContext,
) -> dict | None:
    """Retrieve current cart data from session state."""
    assert isinstance(tool_context, ToolContext)

    invocation_id = tool_context.invocation_id
    current_cart = tool_context.state.get('state', {}).get('current_cart_data', {})
    # If a cart data exists and matches the current invocation ID,
    # this meant a cart tool retrieved it just now.
    if current_cart.get('invocation_id') == invocation_id:
        return current_cart
    return {}
def process_order_data(raw_order: dict, include_all_items: bool = False) -> dict:
    """Chuẩn hóa dữ liệu 1 đơn hàng từ GraphQL sang format hiển thị chung.

    Args:
        raw_order: Dữ liệu đơn hàng thô từ GraphQL
        include_all_items: Nếu True, trả về tất cả sản phẩm trong đơn hàng.
                          Nếu False (mặc định), chỉ trả về sản phẩm đầu tiên để hiển thị tượng trưng.

    Returns:
        dict: Dữ liệu đơn hàng đã được xử lý
    """

    if not raw_order:
        return {}

    total_info = raw_order.get("total", {})
    items_data = raw_order.get("items", [])
    first_item = items_data[0] if items_data else None
    total_quantity = sum(item.get("quantity_ordered", 0) for item in items_data)
    total_items = len(items_data)

    # Xử lý sản phẩm đầu tiên (hiển thị tượng trưng)
    item_info = None
    if first_item:
        # Get image URL from product data
        product_data = first_item.get("product") or {}
        image_url = None
        if product_data:
            # Prefer small_image, fallback to thumbnail
            small_image = product_data.get("small_image", {})
            thumbnail = product_data.get("thumbnail", {})
            image_url = small_image.get("url") or thumbnail.get("url")

        # Ưu tiên lấy ecom_name từ product, nếu không có thì lấy product_name
        display_name = product_data.get("ecom_name") if product_data else None
        if not display_name:
            display_name = first_item.get("product_name")

        item_info = {
            "product_name": display_name,
            "sku": first_item.get("product_sku"),
            "quantity": first_item.get("quantity_ordered"),
            "price": first_item.get("product_sale_price", {}).get("value"),
            "currency": first_item.get("product_sale_price", {}).get("currency", "VND"),
            "image_url": image_url
        }

    all_items = None
    if include_all_items and items_data:
        all_items = []
        for item in items_data:
            product_data = item.get("product") or {}
            image_url = None
            if product_data:
                small_image = product_data.get("small_image", {})
                thumbnail = product_data.get("thumbnail", {})
                image_url = small_image.get("url") or thumbnail.get("url")

            # Ưu tiên lấy ecom_name từ product, nếu không có thì lấy product_name
            display_name = product_data.get("ecom_name") if product_data else None
            if not display_name:
                display_name = item.get("product_name")

            all_items.append({
                "product_name": display_name,
                "sku": item.get("product_sku"),
                "quantity": item.get("quantity_ordered"),
                "price": item.get("product_sale_price", {}).get("value"),
                "currency": item.get("product_sale_price", {}).get("currency", "VND"),
                "image_url": image_url,
                "product_url_key": item.get("product_url_key")
            })

    payment_method_info = {}
    payment_methods_list = raw_order.get("payment_methods", [])
    if payment_methods_list and isinstance(payment_methods_list, list):
        payment_method_info = {
            "name": payment_methods_list[0].get("name"),
            "type": payment_methods_list[0].get("type")
        }

    # Format delivery_information dates
    delivery_info = raw_order.get("delivery_information")
    if delivery_info:
        delivery_info = delivery_info.copy()
        if delivery_info.get("delivery_date"):
            delivery_info["delivery_date"] = format_date_to_ddmmyyyy(delivery_info["delivery_date"])

    result = {
        "order_number": raw_order.get("number"),
        "order_date": format_date_to_ddmmyyyy(raw_order.get("order_date")),
        "email": raw_order.get("email"),
        "status": raw_order.get("status"),
        "status_code": raw_order.get("status_code"),
        "state": raw_order.get("state"),
        "shipping_address": raw_order.get("shipping_address"),
        "delivery_status": raw_order.get("delivery_status"),
        "delivery_information": delivery_info,
        "shipping_code": raw_order.get("shipping_code"),
        "payment_method": payment_method_info,
        "grand_total": total_info.get("grand_total", {}).get("value"),
        "currency": total_info.get("grand_total", {}).get("currency", "VND"),
        "subtotal": total_info.get("subtotal", {}).get("value"),
        "shipping": total_info.get("total_shipping", {}).get("value"),
        "tax": total_info.get("total_tax", {}).get("value"),
        "discounts": [
            {
                "label": d.get("label", ""),
                "amount": d.get("amount", {}).get("value", 0)
            }
            for d in total_info.get("discounts", [])
        ],
        "total_quantity": total_quantity,
        "total_items": total_items,
        "item": item_info
    }
    if all_items is not None:
        result["all_items"] = all_items

    return result
