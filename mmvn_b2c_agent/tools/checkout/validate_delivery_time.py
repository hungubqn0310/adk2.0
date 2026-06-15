"""
Validate Delivery Time Tool

Kiểm tra xem khung giờ giao hàng đã chọn còn available hay không.
Dùng khi:
- User quay lại sau khi out popup (in_checkout_flow=true)
- Trước khi show payment links

Logic:
1. Lấy delivery_date và delivery_time_id từ M2 cart
2. Call getTimeInterval API để lấy available slots cho ngày đó
3. Check xem time_interval_id còn trong list available không
4. Return is_valid: true/false + message
"""

import logging
from typing import Optional, Dict, Any
from google.adk.tools import ToolContext

from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.tools.utils import make_graphql_request_async

logger = logging.getLogger(__name__)


async def validate_delivery_time(
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """
    Validate xem khung giờ giao hàng đã chọn còn available không.

    Args:
        tool_context: Tool context for session state access

    Returns:
        dict: {
            "success": bool,
            "is_valid": bool,  // Khung giờ còn available không
            "message": str,  // Message cho user nếu invalid
            "data": {  // Chi tiết nếu cần
                "delivery_date": str,
                "time_interval_id": int,
                "selected_time_label": str,
                "available_slots": [...]  // Danh sách slots còn available
            }
        }
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "is_valid": False,
                "message": "Tool context is missing",
                "code": "MISSING_TOOL_CONTEXT"
            }

        # Get session data
        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        signin_token = (magento_session_data.get("signin_token") or "").strip('"')
        magento_cart_id = (magento_session_data.get("magento_cart_id") or "").strip('"')

        if not magento_cart_id:
            return {
                "success": False,
                "is_valid": False,
                "message": "Cart ID missing",
                "code": "MISSING_CART_ID"
            }

        # Step 1: Get cart with delivery info (including from/to for time range comparison)
        cart_query = """
            query GetCartDeliveryInfo($cartId: String!) {
                cart(cart_id: $cartId) {
                    id
                    delivery_date {
                        date
                        time_interval_id
                        comment
                        from
                        to
                    }
                }
            }
        """

        cart_res = await make_graphql_request_async(
            cart_query,
            {"cartId": magento_cart_id},
            base_url,
            store_id,
            auth_token=signin_token or None
        )

        if not cart_res or not cart_res.get("data", {}).get("cart"):
            logger.error(f"Cannot get cart delivery info: {cart_res}")
            return {
                "success": False,
                "is_valid": False,
                "message": "Không thể kiểm tra thông tin giao hàng",
                "code": "CART_API_ERROR"
            }

        cart_data = cart_res["data"]["cart"]

        # Get delivery info from cart
        delivery_date_obj = cart_data.get("delivery_date")

        delivery_date = None
        time_interval_id = None
        selected_from = None
        selected_to = None

        # Get from delivery_date field
        if delivery_date_obj:
            delivery_date = delivery_date_obj.get("date")
            time_interval_id = delivery_date_obj.get("time_interval_id")
            selected_from = delivery_date_obj.get("from")
            selected_to = delivery_date_obj.get("to")

        # If no delivery time set yet, it's NOT valid (user needs to select)
        if not delivery_date or time_interval_id is None:
            logger.info("No delivery time set in cart yet - needs selection")
            return {
                "success": True,
                "is_valid": False,  # ✅ Changed from True to False
                "message": "Anh/chị chưa chọn thời gian giao hàng",
                "instruction_for_agent": (
                    "User has NOT selected delivery time yet. "
                    "Tell user to go back to checkout popup and select delivery date & time. "
                    "They can call show_checkout_step(step='main_info') to reopen the popup."
                ),
                "code": "NO_DELIVERY_TIME_SET"
            }

        # Step 2: Get available time slots for the selected date
        time_slots_query = """
            query GetTimeIntervals($scheduleId: Int!, $date: String!) {
                getTimeInterval(schedule_id: $scheduleId, date: $date) {
                    time_interval_id
                    from
                    to
                    label
                }
            }
        """

        # schedule_id = 2 (theo API doc)
        slots_res = await make_graphql_request_async(
            time_slots_query,
            {"scheduleId": 2, "date": delivery_date},
            base_url,
            store_id,
            auth_token=signin_token or None
        )

        if not slots_res or not slots_res.get("data"):
            logger.error(f"Cannot get time intervals: {slots_res}")
            return {
                "success": False,
                "is_valid": False,
                "message": "Không thể kiểm tra khung giờ giao hàng",
                "code": "TIME_SLOTS_API_ERROR"
            }

        available_slots = slots_res["data"].get("getTimeInterval", [])

        # Step 3: Check if selected time slot is still available
        # IMPORTANT: Compare by time range (from/to), NOT by time_interval_id
        # Because different Magento instances may have different IDs for the same time slot
        is_valid = False
        matched_slot = None

        # First try: match by ID (fastest, works when same database)
        for slot in available_slots:
            if slot.get("time_interval_id") == time_interval_id:
                is_valid = True
                matched_slot = slot
                logger.info(f"Delivery time matched by ID: {time_interval_id}")
                break

        # Second try: match by time range (from/to) if ID doesn't match
        # This handles cross-database scenarios where IDs differ but time ranges are same
        if not is_valid and selected_from and selected_to:
            for slot in available_slots:
                slot_from = str(slot.get("from", ""))
                slot_to = str(slot.get("to", ""))
                if slot_from == str(selected_from) and slot_to == str(selected_to):
                    is_valid = True
                    matched_slot = slot
                    logger.info(f"Delivery time matched by time range: {selected_from}-{selected_to} "
                               f"(cart ID={time_interval_id}, available ID={slot.get('time_interval_id')})")
                    break

        if is_valid:
            logger.info(f"Delivery time for {delivery_date} is valid")
            return {
                "success": True,
                "is_valid": True,
                "message": "Khung giờ giao hàng vẫn còn available",
                "data": {
                    "delivery_date": delivery_date,
                    "time_interval_id": time_interval_id,
                    "matched_slot": matched_slot
                }
            }
        else:
            logger.warning(f"Delivery time {time_interval_id} ({selected_from}-{selected_to}) for {delivery_date} is NO LONGER available. "
                          f"Available slots: {[(s.get('time_interval_id'), s.get('from'), s.get('to')) for s in available_slots]}")
            return {
                "success": True,
                "is_valid": False,
                "message": "Khung giờ giao hàng anh/chị đã chọn không còn available. Vui lòng chọn lại khung giờ giao hàng ạ.",
                "instruction_for_agent": "Inform user that their selected delivery time is no longer available. "
                                         "Call show_checkout_step(step='main_info') to let them choose a new delivery time.",
                "data": {
                    "delivery_date": delivery_date,
                    "time_interval_id": time_interval_id,
                    "selected_time_range": f"{selected_from}-{selected_to}",
                    "available_slots": available_slots
                }
            }

    except Exception as e:
        logger.error(f"Error in validate_delivery_time: {e}", exc_info=True)
        return {
            "success": False,
            "is_valid": False,
            "message": "Lỗi khi kiểm tra khung giờ giao hàng",
            "code": "UNEXPECTED_ERROR"
        }
