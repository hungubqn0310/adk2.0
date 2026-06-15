import logging
import mmvn_b2c_agent.tools.cng.cart as cart_tools
from typing import Any, Tuple, Optional
from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)


def get_qty_step_from_product_type(product_type: Optional[str]) -> float:
    """
    Get quantity step based on mm_product_type.

    Args:
        product_type: 'F' for Fresh (step 0.5), 'N' for Normal (step 1), or None

    Returns:
        float: 0.5 for Fresh products, 1.0 for Normal/unknown products
    """
    if product_type == 'F':
        return 0.5
    return 1.0


def round_quantity_to_step(quantity: float, step: float) -> float:
    """
    Round quantity to nearest multiple of step.

    For step = 0.5:
        0.00 - 0.24 → 0
        0.25 - 0.74 → 0.5
        0.75 - 1.24 → 1.0
        etc.

    For step = 1.0:
        0.00 - 0.49 → 0
        0.50 - 1.49 → 1
        etc.

    Args:
        quantity: Original quantity (can be float)
        step: Quantity step (0.5 or 1.0)

    Returns:
        float: Rounded quantity (multiple of step)
    """
    if step <= 0:
        step = 1.0

    # Round to nearest multiple of step
    rounded = round(quantity / step) * step

    # Ensure we don't return 0 if user requested something positive
    # (minimum is the step itself if quantity > 0)
    if quantity > 0 and rounded <= 0:
        rounded = step

    # Round to avoid floating point precision issues (e.g., 0.5000000001)
    return round(rounded, 2)


def normalize_quantity(quantity: float, product_type: Optional[str]) -> Tuple[float, float, bool]:
    """
    Normalize quantity based on product type.

    Args:
        quantity: User requested quantity
        product_type: 'F' for Fresh, 'N' for Normal, or None

    Returns:
        Tuple of (normalized_quantity, step, was_rounded)
        - normalized_quantity: Quantity rounded to valid step
        - step: The quantity step used (0.5 or 1.0)
        - was_rounded: True if quantity was changed during normalization
    """
    step = get_qty_step_from_product_type(product_type)
    normalized = round_quantity_to_step(quantity, step)
    was_rounded = abs(normalized - quantity) > 0.001  # Allow small float precision diff

    logger.info(f"[QUANTITY] Normalized: {quantity} → {normalized} (product_type={product_type}, step={step}, rounded={was_rounded})")

    return normalized, step, was_rounded


async def cart_item_id_exists(
        cart_item_id: int | str,
        tool_context: ToolContext
):
    """
    Check if a cart item ID exists in the USER's shopping cart.

    This helper function verifies whether a given cart item ID is present in the
    user's cart. The cart belongs to the USER - you are checking it on their behalf.

    Args:
        cart_item_id: The cart item unique identifier to look for (int or str)
        tool_context: Tool execution context (automatically provided)

    Returns:
        dict: Cart item details if found
        None: If cart item ID does not exist in cart
        dict: Error response if cart cannot be accessed

    Important:
        - This is an internal helper function used by other cart tools
        - Always refer to it as "the user's cart", NOT "the agent's cart"
        - Cart item ID is different from product SKU
    """
    if not tool_context:
        return {
            "success": False,
            "message": "Tool context is missing",
            "code": "MISSING_TOOL_CONTEXT"
        }
    cart_detail = await cart_tools.view_cart(tool_context)
    if not cart_detail.get("success"):
        return cart_detail
    cart_items = cart_detail.get("data", {}).get("items", [])
    for item in cart_items:
        if str(item.get("id")) == str(cart_item_id):
            return item
    return None


async def get_cart_item_id_from_sku(
        sku: str,
        tool_context: ToolContext
) -> dict[str, Any]:
    """
    Get the cart item ID from the USER's shopping cart based on product SKU.

    This helper function finds the cart item unique identifier (uid) by searching
    the user's cart for a product with the specified SKU. The cart belongs to the
    USER - you are searching it on their behalf.

    Args:
        sku: Product SKU code (format: two integers with underscore, e.g., "441976_24419765")
        tool_context: Tool execution context (automatically provided)

    Returns:
        dict with:
            - success (bool): Whether the operation succeeded
            - cart_item_id (str): The cart item unique identifier if found
            - message (str): Error message if failed
            - instruction_for_agent (str): Agent instructions if multiple items found
            - found_cart_items (list): Details of duplicate items if multiple found

    Important:
        - This is an internal helper function used by update/remove operations
        - Always refer to it as "the user's cart", NOT "the agent's cart"
        - If multiple items with same SKU exist, returns details for user to choose
        - Cart item ID (uid) is different from product SKU
    """
    if not tool_context:
        return {
            "success": False,
            "message": "Tool context is missing",
            "code": "MISSING_TOOL_CONTEXT"
        }
    cart_detail = await cart_tools.view_cart(tool_context)
    if not cart_detail.get('success'):
        return cart_detail
    # try to get the cart row id from the cart detail and sku
    cart_data = cart_detail.get('data', {})
    found_cart_item_ids = []
    found_cart_items = []
    for row in cart_data.get('items', []):
        if row.get('product', {}).get('sku') == sku:
            found_cart_item_ids.append(row.get('id'))
            found_cart_items.append(row)
    if not found_cart_item_ids:
        return {
            "success": False,
            "message": f"Product with SKU {sku} not found in cart.",
            "code": "PRODUCT_NOT_IN_CART"
        }
    if len(found_cart_item_ids) > 1:
        return {
            "success": False,
            "message": f"Multiple items with SKU {sku} found in cart. Cannot determine which one to select.",
            "instruction_for_agent": "Inform the user that multiple items with the same SKU are found in the cart, then provide them with the needed duplicate lines's details (row id, product name, quantity, price, ...) and ask them to specify which one to select. ALso present them the choice of selecting all.",
            "found_cart_items": found_cart_items,
        }
    cart_item_id = found_cart_item_ids[0]
    return {
        "success": True,
        "cart_item_id": cart_item_id,
    }

async def save_cart_to_state(cart_raw_data: dict[str, Any],
                             processed_cart_data: dict[str, Any],
                             tool_context: ToolContext) -> None:
    """
    Save the USER's cart data to the tool context state.

    This helper function persists the user's cart information in the session state
    for future tool calls. The cart belongs to the USER - you are storing their
    cart data on their behalf.

    Args:
        cart_raw_data: Raw cart data from GraphQL response
        processed_cart_data: Formatted cart data ready for AI consumption
        tool_context: Tool execution context (automatically provided)

    Returns:
        None: This function updates the state in-place

    Important:
        - This is an internal helper function used after cart operations
        - Always refer to it as "the user's cart data", NOT "the agent's cart data"
        - Stores both raw and processed versions for different use cases
        - Includes invocation_id to track when data was last updated
    """
    if not tool_context:
        return
    if not tool_context.state:
        tool_context.state['state'] = {}
    tool_context.state['state']['current_cart_data'] = {
        'cart_raw_data': cart_raw_data,
        'processed_cart_data': processed_cart_data,
        'invocation_id': tool_context.invocation_id
    }
