import logging
from typing import Optional

from google.adk.tools import ToolContext
from mmvn_b2c_agent.tools.cng.cart.cart_view import view_cart
from mmvn_b2c_agent.tools.location.location_tool import (
    get_nearest_store_from_address,
    _get_delivery_policy_info,
)

logger = logging.getLogger("google_adk." + __name__)


async def check_freeship_eligibility(
    address: str,
    tool_context: ToolContext,
) -> dict:
    """
    Get cart total and nearest store information for checking free shipping eligibility.

    **USE THIS TOOL WHEN:**
    - User asks "có freeship không?", "có được miễn phí giao hàng không?"
    - User asks "từ địa chỉ X có được freeship không?"
    - User wants to know if their CURRENT CART qualifies for free delivery

    **This tool ONLY retrieves data:**
    1. Current cart total (giá trị đơn hàng)
    2. Nearest store name and location
    3. Distance from address to store (khoảng cách)
    4. Whether cart contains ice cream or frozen cake (Kem & Bánh đông lạnh)
       - Detected by checking: Category = "Thực phẩm đông lạnh" AND name contains "kem"/"bánh"
       - Does NOT include other frozen foods (frozen meat, seafood, vegetables)

    **After calling this tool, YOU (agent) must:**
    1. Use get_mm_info_by_rag or get_all_mm_data to get delivery policy (chính sách giao hàng)
    2. Read and understand the policy for that specific store
    3. Compare cart total vs minimum order requirement
    4. Compare distance vs free delivery radius
    5. **IMPORTANT**: If cart has ice cream/frozen cake (has_ice_cream_or_frozen_cake=True):
       - Distance MUST be <= 7km (no delivery beyond 7km for ice cream/frozen cake)
       - Customer MUST pay in advance (thanh toán trước)
       - Minimum order still applies (600k or 300k depending on store)
    6. Tell customer if they get free shipping or not, and explain why

    Args:
        address (str): The customer's delivery address (full address including street, district, ward, city).
                      If empty or not provided, tool will guide agent to ask for address.
        tool_context (ToolContext): The context containing session data with cart information

    Returns:
        dict: Raw data for agent to analyze:
            - success (bool): Whether data retrieval was successful
            - cart_total (float): Current cart grand total in VND (giá trị đơn hàng)
            - cart_total_formatted (str): Formatted string like "932,402₫"
            - nearest_store_name (str): Name of nearest store (e.g. "MM Mega Market Hà Đông")
            - nearest_store_address (str): Full address of the store
            - distance_km (float): Distance in kilometers (khoảng cách)
            - distance_text (str): Human readable distance (e.g. "4 km")
            - instruction_for_agent (str): Instructions on what to do next

    Example workflow:
        User: "Tôi ở 170 Đê La Thành có được freeship không?"

        Step 1: Agent calls check_freeship_eligibility(address="170 Đê La Thành")
        Returns: {
            "cart_total": 932402.0,
            "nearest_store_name": "MM Supermarket Thanh Xuân",
            "distance_km": 5.0
        }

        Step 2: Agent calls get_mm_info_by_rag(query="chính sách giao hàng Thanh Xuân")
        Gets policy: "Miễn phí từ 300.000₫ trong 7km..."

        Step 3: Agent analyzes:
        - Cart 932,402₫ >= 300,000₫ ✓
        - Distance 5km <= 7km ✓
        - Conclusion: Được freeship!

        Step 4: Agent responds to user with clear explanation
    """
    print(
        f"{'-' * 80}\n"
        f"TOOL CALLED: check_freeship_eligibility\n"
        f"address: {address}\n"
        f"{'-' * 80}\n"
    )

    # Step 0: Check if address is provided
    if not address or address.strip() == "":
        return {
            "success": False,
            "missing_info": "address",
            "message": "Thiếu thông tin địa chỉ giao hàng.",
            "instruction_for_agent": (
                "Khách hàng chưa cung cấp địa chỉ giao hàng. "
                "Hãy hỏi khách hàng: 'Anh/chị vui lòng cho em biết địa chỉ giao hàng để em kiểm tra chính sách freeship ạ. "
                "Anh/chị cung cấp địa chỉ cụ thể (số nhà, đường, phường/xã, quận/huyện, tỉnh/thành phố) để em kiểm tra chính xác nhất nhé.'"
            )
        }

    # Step 1: Get cart data by calling view_cart tool
    cart_result = await view_cart(tool_context)

    if not cart_result.get("success"):
        return {
            "success": False,
            "cart_total": 0.0,
            "freeship_eligible": False,
            "freeship_status": "cart_error",
            "message": cart_result.get("message", "Không thể truy cập giỏ hàng"),
            "instruction_for_agent": cart_result.get("instruction_for_agent",
                "Không thể truy cập giỏ hàng. Vui lòng thử lại sau."
            )
        }

    cart_data = cart_result.get("data", {})
    cart_total_str = cart_data.get("cart_grand_total", "0 VND")
    try:
        cart_total = float(cart_total_str.split()[0]) if cart_total_str else 0.0
        print(f"DEBUG: Extracted cart_total: {cart_total}")
    except (ValueError, IndexError) as e:
        logger.error(f"Failed to parse cart_total_str '{cart_total_str}': {e}")
        cart_total = 0.0

    # Step 1.5: Check if cart contains ice cream or frozen cake products (Kem & Bánh đông lạnh)
    # These products require special delivery conditions (prepayment + max 7km delivery)
    cart_items = cart_data.get("items", [])
    has_ice_cream_or_frozen_cake = False
    frozen_food_category = "Thực phẩm đông lạnh"
    ice_cream_cake_keywords = ["kem", "ice cream", "bánh"]

    for item in cart_items:
        product = item.get("product", {})
        categories = product.get("categories", [])
        product_name = product.get("name", "").lower()

        # Check if product is in frozen food category
        is_frozen_food = any(frozen_food_category.lower() in str(cat).lower() for cat in categories)

        # Check if product name contains ice cream or cake keywords
        contains_ice_cream_or_cake = any(keyword in product_name for keyword in ice_cream_cake_keywords)

        # Must satisfy BOTH conditions
        if is_frozen_food and contains_ice_cream_or_cake:
            has_ice_cream_or_frozen_cake = True
            print(f"DEBUG: Found ice cream/frozen cake product: {product.get('name')}")
            break

    print(f"DEBUG: has_ice_cream_or_frozen_cake: {has_ice_cream_or_frozen_cake}")

    # Step 1.6: Check if cart contains bulky items (Hàng cồng kềnh)
    # These products may require special delivery arrangements
    has_bulky_items = False
    bulky_item_keywords = [
        "tủ lạnh", "máy giặt", "máy lạnh", "điều hòa",
        "tivi", "tv", "lò vi sóng",
        "xe đẩy", "ghế massage", "máy chạy bộ",
        "nệm", "giường", "tủ quần áo"
    ]

    for item in cart_items:
        product = item.get("product", {})
        product_name = product.get("name", "").lower()

        # Check if product name contains bulky item keywords
        if any(keyword in product_name for keyword in bulky_item_keywords):
            has_bulky_items = True
            print(f"DEBUG: Found bulky item: {product.get('name')}")
            break

    print(f"DEBUG: has_bulky_items: {has_bulky_items}")

    if cart_total <= 0:
        return {
            "success": False,
            "cart_total": 0.0,
            "freeship_eligible": False,
            "freeship_status": "no_cart",
            "message": (
                "Giỏ hàng trống. "
                "Vui lòng thêm sản phẩm vào giỏ hàng trước khi kiểm tra freeship."
            ),
            "instruction_for_agent": (
                "Khách hàng chưa có sản phẩm trong giỏ hàng. "
                "Hãy giúp khách tìm và thêm sản phẩm vào giỏ hàng trước."
            )
        }

    # Step 2: Get nearest store based on address
    try:
        store_result = get_nearest_store_from_address(address, tool_context)

        if not store_result.get("success"):
            return {
                "success": False,
                "cart_total": cart_total,
                "cart_total_formatted": f"{cart_total:,.0f}₫",
                "message": store_result.get("message", "Không tìm thấy địa chỉ"),
                "instruction_for_agent": (
                    store_result.get("instruction_for_agent", "") +
                    "\n\nNote: Cart information retrieved but store location not found."
                ),
                "region": store_result.get("region"),
                "store_locator_link": store_result.get("store_locator_link"),
            }

        nearest_store = store_result.get("nearest_store")

        if not nearest_store:
            return {
                "success": False,
                "cart_total": cart_total,
                "cart_total_formatted": f"{cart_total:,.0f}₫",
                "message": "Không tìm thấy cửa hàng gần địa chỉ của bạn.",
                "instruction_for_agent": "Could not find nearest store. Ask user for more specific address."
            }

        # Step 3: Extract and return raw data
        distance_km_raw = nearest_store.get('distance', 0.0)
        # Convert distance to float if it's a string
        try:
            distance_km = float(distance_km_raw) if distance_km_raw else 0.0
        except (ValueError, TypeError):
            logger.warning(f"Invalid distance value: {distance_km_raw}, defaulting to 0.0")
            distance_km = 0.0

        # Build special notes for ice cream or frozen cake
        frozen_food_notes = ""
        if has_ice_cream_or_frozen_cake:
            frozen_food_notes = (
                "\n\n⚠️ SPECIAL CONDITION - ICE CREAM/FROZEN CAKE DETECTED:\n"
                "Cart contains ice cream or frozen cake products (Kem & Bánh đông lạnh).\n"
                "Apply these special rules:\n"
                "- Customer MUST pay in advance (thanh toán trước)\n"
                "- Delivery ONLY within 7km (no exception)\n"
                "- Minimum order: 600k for regular stores, 300k for Thanh Xuân/Hưng Phú stores\n"
                "- If distance > 7km: Cannot deliver ice cream/frozen cake, inform customer"
            )

        delivery_policy = store_result.get("delivery_policy", {})
        min_order = delivery_policy.get("min_order_free_delivery", 600000)
        free_radius_km = delivery_policy.get("free_delivery_radius_km", 7)

        return {
            "success": True,
            "cart_total": cart_total,
            "cart_total_formatted": f"{cart_total:,.0f}₫",
            "has_ice_cream_or_frozen_cake": has_ice_cream_or_frozen_cake,
            "has_bulky_items": has_bulky_items,
            "nearest_store_name": nearest_store['name'],
            "nearest_store_address": nearest_store['address'],
            "distance_km": distance_km,
            "distance_text": nearest_store['distance_text'],
            "delivery_policy": delivery_policy,
            "instruction_for_agent": (
                f"Data retrieved successfully:\n"
                f"- Cart total: {cart_total:,.0f}₫\n"
                f"- Nearest store: {nearest_store['name']}\n"
                f"- Distance: {distance_km}km\n"
                f"- Min order for free delivery: {min_order:,.0f}₫\n"
                f"- Free delivery radius: {free_radius_km}km\n"
                f"- Contains ice cream/frozen cake: {'YES' if has_ice_cream_or_frozen_cake else 'NO'}"
                f"{frozen_food_notes}\n\n"
                f"Delivery policy already included in 'delivery_policy' field. Use it directly:\n"
                f"1. Compare cart_total ({cart_total:,.0f}₫) vs min_order ({min_order:,.0f}₫)\n"
                f"2. Compare distance ({distance_km}km) vs free_delivery_radius ({free_radius_km}km)\n"
                f"3. If ice cream/frozen cake: check distance <= 7km AND inform customer about advance payment requirement\n"
                f"4. Tell user if they qualify for free shipping and explain clearly why/why not"
            )
        }

    except Exception as e:
        logger.error(f"Error checking freeship eligibility: {e}", exc_info=True)
        return {
            "success": False,
            "cart_total": cart_total,
            "cart_total_formatted": f"{cart_total:,.0f}₫",
            "message": f"Đã xảy ra lỗi: {str(e)}",
            "instruction_for_agent": "System error occurred. Ask user to try again or contact support.",
        }
