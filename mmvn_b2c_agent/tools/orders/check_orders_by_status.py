"""
Wrapper functions for checking orders by status or date.
Makes it easier for the agent to query orders without complex parameters.
"""
import logging
from typing import Optional, Any
from google.adk.tools import ToolContext
from mmvn_b2c_agent.tools.orders.check_orders import check_my_orders

logger = logging.getLogger(__name__)


async def check_orders_by_status(
    tool_context: Optional[ToolContext] = None,
    status: str = "",
    current_page: int = 1,
    page_size: int = 10
) -> dict[str, Any]:
    """
    Check orders filtered by specific status.

    Use this tool when user TYPES TEXT asking about orders with specific status keywords.

    Điều kiện sử dụng: Người dùng phải GÕ TEXT hỏi về đơn hàng theo trạng thái.
    Khi người dùng upload file (PDF/Excel/Word/Image) mà KHÔNG gõ text → dùng cng_product_search_tool để tìm sản phẩm trong file, KHÔNG dùng tool này.

    Args:
        tool_context: Tool execution context (automatically provided)
        status: Order status filter. Use one of these values:
            - "chờ thanh toán" or "awaiting_payment" → Orders awaiting payment (pending_payment)
            - "đã ghi nhận đơn hàng" or "pending" → Orders confirmed/acknowledged (pending, pending_ccod)
            - "đang xử lý" or "processing" → Orders being processed (confirmed_ccod, processing)
            - "đang giao" or "delivering" → Orders being delivered (invoiced_ccod, in_shipment_ccod, picked_ccod, picking_ccod)
            - "đã giao" or "delivered" → Completed/delivered orders (complete, completed_ccod)
            - "đã hủy" or "canceled" → Canceled orders (backorder_ccod, canceled, closed, deleted_ccod)
            - "chờ hủy" or "waiting_cancel" → Orders waiting to be canceled (waiting_cancel)
        current_page: Page number for pagination (default: 1)
        page_size: Number of orders per page (default: 10)

    Returns:
        dict with order data filtered by the specified status

    Examples:
        User: "đơn hàng nào chờ thanh toán?"
        Agent: check_orders_by_status(status="chờ thanh toán")

        User: "có đơn đã ghi nhận chưa?"
        Agent: check_orders_by_status(status="đã ghi nhận đơn hàng")

        User: "tôi có đơn hàng nào đang giao không?"
        Agent: check_orders_by_status(status="đang giao")

        User: "có đơn đã giao chưa?"
        Agent: check_orders_by_status(status="đã giao")

        User: "đơn nào đã hủy?"
        Agent: check_orders_by_status(status="đã hủy")
    """
    # Map friendly status names to actual status codes
    status_mapping = {
        "delivering": "invoiced_ccod,in_shipment_ccod,picked_ccod,picking_ccod",
        "đang giao": "invoiced_ccod,in_shipment_ccod,picked_ccod,picking_ccod",
        "delivered": "complete,completed_ccod",
        "đã giao": "complete,completed_ccod",
        "canceled": "backorder_ccod,canceled,closed,deleted_ccod",
        "đã hủy": "backorder_ccod,canceled,closed,deleted_ccod",
        "processing": "confirmed_ccod,order_error,processing",
        "đang xử lý": "confirmed_ccod,order_error,processing",
        "pending": "pending,pending_ccod",
        "đã ghi nhận đơn hàng": "pending,pending_ccod",
        "awaiting_payment": "pending_payment",
        "chờ thanh toán": "pending_payment",
        "waiting_cancel": "waiting_cancel",
        "chờ hủy": "waiting_cancel",
    }

    # Get the actual status codes
    actual_status = status_mapping.get(status.lower(), status)

    logger.info(f"Checking orders by status: {status} -> {actual_status}")

    return await check_my_orders(
        tool_context=tool_context,
        status=actual_status,
        current_page=current_page,
        page_size=page_size
    )


async def check_orders_by_date(
    tool_context: Optional[ToolContext] = None,
    date_from: str = "",
    date_to: str = "",
    current_page: int = 1,
    page_size: int = 10
) -> dict[str, Any]:
    """
    Check orders filtered by date range.

    Use this tool when user TYPES TEXT asking about their orders from specific dates.

    Điều kiện sử dụng: Người dùng phải GÕ TEXT hỏi về đơn hàng theo ngày.
    Khi người dùng upload file (PDF/Excel/Word/Image) mà KHÔNG gõ text → dùng cng_product_search_tool để tìm sản phẩm trong file, KHÔNG dùng tool này.

    Args:
        tool_context: Tool execution context (automatically provided)
        date_from: Start date in YYYY-MM-DD format (e.g., "2025-11-10")
        date_to: End date in YYYY-MM-DD format (e.g., "2025-11-10")
            - If querying single date, set both date_from and date_to to same value
        current_page: Page number for pagination (default: 1)
        page_size: Number of orders per page (default: 10)

    Returns:
        dict with order data filtered by the specified date range

    Examples:
        User: "đơn hàng hôm nay"
        Agent: check_orders_by_date(date_from="2025-11-10", date_to="2025-11-10")

        User: "đơn hàng từ ngày 1/11 đến 5/11"
        Agent: check_orders_by_date(date_from="2025-11-01", date_to="2025-11-05")

        User: "đơn hàng ngày 31/10/2025"
        Agent: check_orders_by_date(date_from="2025-10-31", date_to="2025-10-31")
    """
    logger.info(f"Checking orders by date: from {date_from} to {date_to}")

    return await check_my_orders(
        tool_context=tool_context,
        create_date_from=date_from,
        create_date_to=date_to,
        current_page=current_page,
        page_size=page_size
    )
