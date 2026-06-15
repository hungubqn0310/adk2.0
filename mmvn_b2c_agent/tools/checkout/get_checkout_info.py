"""
Get Checkout Info Tool

Tool để lấy thông tin checkout đã lưu của người dùng.
Khi người dùng hỏi các câu như:
- "Mã số thuế của tôi là gì?"
- "Email của tôi là gì?"
- "Số điện thoại của tôi là gì?"
- "Địa chỉ giao hàng của tôi?"
- "Thông tin hóa đơn VAT?"

Bot sẽ gọi tool này để lấy thông tin đã lưu từ session state.
"""

import logging
from typing import Optional, Dict, Any
from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)


async def get_my_checkout_info(
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """
    Lấy thông tin checkout đã lưu của người dùng.

    Tool này trả về thông tin mà người dùng đã nhập trong popup checkout,
    bao gồm: tên người nhận, email, số điện thoại, địa chỉ giao hàng,
    thông tin hóa đơn VAT (mã số thuế, tên công ty, địa chỉ công ty), v.v.

    Sử dụng khi người dùng hỏi về thông tin cá nhân đã nhập như:
    - "Mã số thuế của tôi là gì?" / "What's my tax code?"
    - "Email đặt hàng của tôi?" / "What's my email?"
    - "Số điện thoại giao hàng?" / "My delivery phone number?"
    - "Địa chỉ giao hàng của tôi?" / "My delivery address?"
    - "Thông tin hóa đơn VAT?" / "My VAT invoice info?"

    Args:
        tool_context: Tool context for session state access

    Returns:
        dict: {
            "success": bool,
            "message": str,
            "instruction_for_agent": str,
            "data": {  // Nếu có thông tin
                "recipient_name": str,
                "email": str,
                "phone": str,
                "street": str,
                "city_name": str,
                "ward_name": str,
                "district_name": str,
                "delivery_date": str,
                "delivery_time_label": str,
                "note": str,
                "mcard_number": str,
                "call_before_delivery": bool,
                "issue_vat_invoice": bool,
                // VAT info (nếu có)
                "company_name": str,
                "company_vat_number": str,  // Mã số thuế
                "company_address": str
            }
        }
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                "instruction_for_agent": "Tell user: 'Em không thể truy xuất thông tin lúc này, anh/chị thử lại sau ạ.'",
                "code": "MISSING_TOOL_CONTEXT"
            }

        # Try to get checkout info from ROOT level first (preferred)
        checkout_info = tool_context.state.get('guest_checkout_info', {})

        # Fallback to nested state if not found at root
        if not checkout_info:
            nested_state = tool_context.state.get('state', {})
            if isinstance(nested_state, dict):
                checkout_info = nested_state.get('guest_checkout_info', {})

        # Also get individual fields from root level as backup
        email = tool_context.state.get('guest_user_email') or checkout_info.get('email')
        phone = tool_context.state.get('guest_user_phone') or checkout_info.get('phone')

        if email and 'email' not in checkout_info:
            checkout_info['email'] = email
        if phone and 'phone' not in checkout_info:
            checkout_info['phone'] = phone

        if not checkout_info:
            return {
                "success": False,
                "message": "No checkout info found",
                "instruction_for_agent": "Tell user: 'Anh/chị chưa nhập thông tin giao hàng. "
                                         "Anh/chị vui lòng tiến hành thanh toán để nhập thông tin ạ.'",
                "code": "NO_CHECKOUT_INFO"
            }

        # Build response message with markdown formatting for better display
        info_parts = []

        if checkout_info.get('recipient_name'):
            info_parts.append(f"**Tên người nhận:** {checkout_info['recipient_name']}")

        if checkout_info.get('email'):
            info_parts.append(f"**Email:** {checkout_info['email']}")

        if checkout_info.get('phone'):
            info_parts.append(f"**Số điện thoại:** {checkout_info['phone']}")

        # Address parts
        address_parts = []
        if checkout_info.get('street'):
            address_parts.append(checkout_info['street'])
        if checkout_info.get('ward_name'):
            address_parts.append(checkout_info['ward_name'])
        if checkout_info.get('district_name'):
            address_parts.append(checkout_info['district_name'])
        if checkout_info.get('city_name'):
            address_parts.append(checkout_info['city_name'])

        if address_parts:
            info_parts.append(f"**Địa chỉ:** {', '.join(address_parts)}")

        if checkout_info.get('delivery_date'):
            delivery_info = checkout_info['delivery_date']
            if checkout_info.get('delivery_time_label'):
                delivery_info += f" ({checkout_info['delivery_time_label']})"
            info_parts.append(f"**Ngày giao hàng:** {delivery_info}")

        if checkout_info.get('note'):
            info_parts.append(f"**Ghi chú:** {checkout_info['note']}")

        if checkout_info.get('mcard_number'):
            info_parts.append(f"**Số thẻ MCard:** {checkout_info['mcard_number']}")

        # VAT invoice info - format as separate block
        vat_info_parts = []
        if checkout_info.get('issue_vat_invoice') or checkout_info.get('company_vat_number'):
            if checkout_info.get('company_name'):
                vat_info_parts.append(f"**Tên công ty:** {checkout_info['company_name']}")
            if checkout_info.get('company_vat_number'):
                vat_info_parts.append(f"**Mã số thuế:** {checkout_info['company_vat_number']}")
            if checkout_info.get('company_address'):
                vat_info_parts.append(f"**Địa chỉ công ty:** {checkout_info['company_address']}")

        # Combine all parts with double newline for clear separation
        info_message = "\n\n".join(info_parts) if info_parts else ""

        # Add VAT section with header if exists
        if vat_info_parts:
            vat_section = "**Thông tin hóa đơn VAT:**\n\n" + "\n\n".join(vat_info_parts)
            if info_message:
                info_message += "\n\n---\n\n" + vat_section
            else:
                info_message = vat_section

        if not info_message:
            info_message = "Không có thông tin"

        logger.info(f"Retrieved checkout info: {list(checkout_info.keys())}")

        return {
            "success": True,
            "message": "Checkout info retrieved successfully",
            "instruction_for_agent": f"Dạ, thông tin của anh/chị như sau:\n\n{info_message}",
            "data": checkout_info
        }

    except Exception as e:
        logger.error(f"Error in get_my_checkout_info: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "instruction_for_agent": "Tell user: 'Em không thể truy xuất thông tin lúc này, anh/chị thử lại sau ạ.'",
            "code": "UNEXPECTED_ERROR"
        }
