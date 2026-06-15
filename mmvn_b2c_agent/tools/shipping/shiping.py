from typing import Optional
from google.adk.tools import ToolContext
from mmvn_b2c_agent.agents.cng.schema import (
    CngShippingAiResponseFinal,
    DisplayMode,
)
from mmvn_b2c_agent.tools.cng.common import get_current_cart_from_session_state


async def shipping_cart(tool_context: Optional[ToolContext] = None):
    """
    Get MM Mega Market Việt Nam shipping policy information.
    """
    shipping_message = (
        "Dạ, em xin thông báo thời gian giao hàng chung của MM Mega Market Việt Nam ạ:\n"
        "* Đơn hàng sẽ được giao trong vòng 4 giờ kể từ khi đặt hàng thành công.\n"
        "* Để nhận hàng trong ngày, anh/chị vui lòng đặt hàng trước 14:00 (2 giờ chiều).\n"
        "* Nếu đặt hàng sau 14:00, đơn hàng của anh/chị sẽ được giao vào ngày hôm sau.\n"
        "Để biết thời gian giao hàng chính xác nhất, vui lòng nhấn nút 'Thanh toán ngay' ở bên dưới để xác nhận địa chỉ ạ!"
    )

    # Lấy cart data từ session nếu có
    cart_data = None
    if tool_context:
        current_cart_state = get_current_cart_from_session_state(tool_context)
        if current_cart_state:
            cart_raw = current_cart_state.get('cart_raw_data', {})
            if cart_raw:
                cart_data = cart_raw

    response = CngShippingAiResponseFinal(
        language="vi",
        display_mode=DisplayMode.SIMPLE_TEXT,
        message=shipping_message,
        cart_data=cart_data,
        product_data=[],
        show_cart_detail_cta_button=False,
        show_proceed_to_checkout_cta_button=True, 
    )
    
    return response