import logging
import traceback
from typing import Optional, Any
from google.adk.tools import ToolContext
from mmvn_b2c_agent.tools.orders.order_helpers import save_order_result_to_state
from mmvn_b2c_agent.shared.constants import (
    DEFAULT_MMVN_STORE_ID,
    DEFAULT_MMVN_STORE_URL,
    COMPLETED_STATUS_CODES,
    DELIVERING_STATUS_CODES,
    STATUS_CODE_TO_FILTER
)
from mmvn_b2c_agent.tools.utils import make_graphql_request_async
from mmvn_b2c_agent.tools.cng.common import process_order_data
logger = logging.getLogger(__name__)


async def _get_logged_in_customer_order_by_number(
    tool_context: Optional[ToolContext] = None,
    order_number: str = "",
    base_url: str = "",
    store_id: str = "",
    signin_token: str = "",
    include_all_items: bool = False,
    create_date_from: str = "",
    create_date_to: str = ""
) -> dict[str, Any]:
    """
    Get specific order details for a logged-in customer by order number.

    Args:
        tool_context: Tool execution context
        order_number: Order number to fetch
        base_url: Magento base URL
        store_id: Store ID
        signin_token: Authentication token
        include_all_items: If True, return all items in the order. If False, return only first item for display.
        create_date_from: Filter start date (for frontend display)
        create_date_to: Filter end date (for frontend display)

    Returns:
        dict with order details or error
    """
    try:
        # GraphQL query to get a specific order by number
        graphql_query = """
            query GetCustomerOrders($filter: CustomerOrdersFilterInput, $pageSize: Int!) {
                customer {
                    id
                    firstname
                    email
                    custom_attributes(attributeCodes: ["company_user_phone_number"]) {
                        code
                        ... on AttributeValue {
                            value
                            
                        }
                        
                    }
                    orders(filter: $filter, pageSize: $pageSize) {
                        ...CustomerOrdersFragment
                        
                    }
                    
                }
            }

            fragment CustomerOrdersFragment on CustomerOrders {
                items {
                    id
                    number
                    order_date
                    customer_no
                    delivery_information {
                        delivery_date
                        delivery_from
                        delivery_to
                        
                    }
                    vat_information {
                        company_address
                        company_name
                        company_vat_number
                        customer_vat_id
                        
                    }
                    invoices {
                        id
                        
                    }
                    items {
                        id
                        product_name
                        product_sale_price {
                            currency
                            value
                        }
                        product_sku
                        product_url_key
                        selected_options {
                            label
                            value
                        }
                        quantity_ordered
                        product {
                            id
                            uid
                            unit_ecom
                            ecom_name
                            thumbnail {
                                url
                            }
                            small_image {
                                url
                                __typename
                            }
                            dnr_price {
                                qty
                                promo_label
                                promo_type
                                promo_amount
                                promo_value
                                event_id
                                event_name
                            }
                        }
                    }
                    billing_address {
                        firstname
                        country_code
                        city
                        district
                        ward
                        street
                        telephone
                        
                    }
                    payment_methods {
                        name
                        type
                        additional_data {
                            name
                            value
                            
                        }
                        
                    }
                    shipments {
                        id
                        tracking {
                            number
                            
                        }
                        
                    }
                    shipping_address {
                        firstname
                        country_code
                        city
                        district
                        ward
                        street
                        telephone
                        
                    }
                    shipping_method
                    status
                    status_code
                    state
                    total {
                        discounts {
                            label
                            amount {
                                currency
                                value
                                
                            }
                            
                        }
                        grand_total {
                            currency
                            value
                            
                        }
                        subtotal {
                            currency
                            value
                            
                        }
                        total_shipping {
                            currency
                            value
                            
                        }
                        total_tax {
                            currency
                            value
                            
                        }
                        
                    }
                    
                }
                page_info {
                    current_page
                    total_pages
                    
                }
                total_count
                
            }
        """

        # Build filter - match curl format exactly
        filter_obj = {
            "number": {"eq": order_number},
            "createDateFrom": {"gteq": ""},
            "createDateTo": {"lteq": ""},
            "status": {"eq": ""}
        }

        variables = {
            "pageSize": 1,
            "filter": filter_obj
        }

        logger.info(f"Fetching order {order_number} for logged-in customer")

        res = await make_graphql_request_async(
            graphql_query,
            variables,
            base_url,
            store_id,
            auth_token=signin_token,
        )

        if not res:
            order_history_url = f"{base_url}/order-history"
            return {
                "success": False,
                "message": "No response from API",
                "instruction_for_agent": f"Tell the user: 'Hiện chưa thể xem thông tin đơn hàng này. Anh/Chị vui lòng chọn [Quản lý đơn hàng]({order_history_url}) để kiểm tra thông tin đơn hàng và lộ trình giao hàng giúp em nhé.'",
                "code": "NO_RESPONSE",
                "order_history_url": order_history_url
            }

        if res.get("errors"):
            error_message = res.get("errors", [{}])[0].get("message", "Unknown error")
            logger.error(f"GraphQL returned errors: {error_message}")
            order_history_url = f"{base_url}/order-history"
            return {
                "success": False,
                "message": f"API error: {error_message}",
                "instruction_for_agent": f"Tell the user: 'Hiện chưa thể xem thông tin đơn hàng này. Anh/Chị vui lòng chọn [Quản lý đơn hàng]({order_history_url}) để kiểm tra thông tin đơn hàng và lộ trình giao hàng giúp em nhé.'",
                "code": "GRAPHQL_ERROR",
                "order_history_url": order_history_url
            }

        orders_data = res.get("data", {}).get("customer", {}).get("orders", {})
        items = orders_data.get("items", [])

        if not items:
            # Không tìm thấy đơn hàng cụ thể, kiểm tra xem user có đơn hàng nào không
            logger.info(f"Order {order_number} not found, checking if customer has any orders")

            # Query đếm tổng số đơn hàng của user
            check_orders_query = """
                query GetCustomerOrders($pageSize: Int!) {
                    customer {
                        orders(pageSize: $pageSize) {
                            total_count
                            items {
                                id
                                number
                                order_date
                                customer_no
                                status
                                status_code
                                state
                                delivery_information {
                                    delivery_date
                                    delivery_from
                                    delivery_to

                                }
                                items {
                                    id
                                    product_name
                                    product_sku
                                    quantity_ordered
                                    product_sale_price {
                                        currency
                                        value

                                    }
                                    product {
                                        id
                                        uid
                                        thumbnail {
                                            url
                                        }
                                        small_image {
                                            url
                                        }
                                    }

                                }
                                shipping_address {
                                    city
                                    country_code
                                    firstname
                                    postcode
                                    region
                                    street
                                    telephone

                                }
                                payment_methods {
                                    name
                                    type

                                }
                                total {
                                    discounts {
                                        label
                                        amount {
                                            currency
                                            value

                                        }

                                    }
                                    grand_total {
                                        currency
                                        value

                                    }
                                    subtotal {
                                        currency
                                        value

                                    }
                                    total_shipping {
                                        currency
                                        value

                                    }
                                    total_tax {
                                        currency
                                        value

                                    }

                                }

                            }
                            page_info {
                                current_page
                                total_pages
                                
                            }
                            
                        }
                        
                    }
                }
            """

            check_orders_variables = {
                "pageSize": 50
            }

            check_orders_res = await make_graphql_request_async(
                check_orders_query,
                check_orders_variables,
                base_url,
                store_id,
                auth_token=signin_token,
            )

            order_history_url = f"{base_url}/order-history"

            if check_orders_res and not check_orders_res.get("errors"):
                check_orders_data = check_orders_res.get("data", {}).get("customer", {}).get("orders", {})
                total_count = check_orders_data.get("total_count", 0)
                all_items = check_orders_data.get("items", [])

                # Trường hợp 1: User chưa có đơn hàng nào
                if total_count == 0:
                    return {
                        "success": False,
                        "message": f"Order {order_number} not found and customer has no orders",
                        "instruction_for_agent": (
                            f"Tell the user: '**Mã đơn hàng** không tồn tại. Anh/chị vui lòng nhập lại hoặc "
                            f"chọn [Quản lý đơn hàng]({order_history_url}) để xem danh sách đơn hàng.'"
                        ),
                        "code": "ORDER_NOT_FOUND_NO_ORDERS",
                        "order_history_url": order_history_url
                    }
                if all_items:
                    processed_orders_list = [process_order_data(order, include_all_items=False) for order in all_items]
                    processed_result = {
                        "items": processed_orders_list,
                        "page_info": check_orders_data.get("page_info"),
                        "total_count": total_count
                    }
                    await save_order_result_to_state(processed_result, tool_context)

                    return {
                        "success": False,
                        "message": f"Order {order_number} not found, but found {total_count} other orders",
                        "order_data": processed_result,
                        "instruction_for_agent": (
                            f"Tell the user: '**Mã đơn hàng** không tồn tại."
                            f"Dưới đây là các đơn hàng của Anh/Chị:'"
                        ),
                        "code": "ORDER_NOT_FOUND_SHOW_ALL"
                    }

            # Fallback: Nếu không lấy được thông tin
            return {
                "success": False,
                "message": f"Order {order_number} not found",
                "instruction_for_agent": (
                    f"Tell the user: '**Mã đơn hàng** không tồn tại. Anh/chị vui lòng nhập lại hoặc "
                    f"chọn [Quản lý đơn hàng]({order_history_url}) để xem danh sách đơn hàng.'"
                ),
                "code": "ORDER_NOT_FOUND",
                "order_history_url": order_history_url
            }
        order_data = items[0]
        processed_order_info = process_order_data(order_data, include_all_items=include_all_items)

        order_status = processed_order_info.get("status", "")
        status_code = processed_order_info.get("status_code", "")
        state = processed_order_info.get("state", "")

        is_completed = status_code in COMPLETED_STATUS_CODES

        # Xác định filter status dựa trên trạng thái đơn hàng cho frontend
        order_status_filter = STATUS_CODE_TO_FILTER.get(status_code, "")

        # Thêm order_status_filter và date filters vào processed_order_info trước khi lưu vào state
        processed_order_info['order_status_filter'] = order_status_filter
        processed_order_info['start_date'] = create_date_from if create_date_from else None
        processed_order_info['end_date'] = create_date_to if create_date_to else None

        await save_order_result_to_state(processed_order_info, tool_context)

        if include_all_items:
            message = f"Dưới đây là các sản phẩm trong đơn hàng **{order_number}** của anh/chị ạ."
        elif is_completed:
            message = f"Đơn hàng của Anh/Chị đang ở trạng thái: {order_status}. Anh/Chị vui lòng chọn **Xem chi tiết** để kiểm tra thông tin đơn hàng giúp em nhé."
        else:
            message = f"Đơn hàng của Anh/Chị đang ở trạng thái: {order_status}. Anh/Chị vui lòng chọn **Xem chi tiết** để kiểm tra thông tin đơn hàng và lộ trình giao hàng giúp em nhé."

        return {
            "success": True,
            "order_data": processed_order_info,
            "message": message,
            "instruction_for_agent": (
                f"Display the order information to the user with the message already provided in the message field."
            ),
            "code": "SUCCESS",
            "order_status_filter": order_status_filter
        }

    except Exception as e:
        logger.error(f"Error fetching order {order_number}: {str(e)}")
        logger.error(f"Full traceback: {traceback.format_exc()}")
        order_history_url = f"{base_url}/order-history"
        return {
            "success": False,
            "message": f"Error fetching order: {str(e)}",
            "instruction_for_agent": f"Tell the user: 'Hiện chưa thể xem thông tin đơn hàng này. Anh/Chị vui lòng chọn [Quản lý đơn hàng]({order_history_url}) để kiểm tra thông tin đơn hàng và lộ trình giao hàng giúp em nhé.'",
            "code": "API_ERROR",
            "error_details": str(e),
            "order_history_url": order_history_url
        }




async def check_my_orders(
    tool_context: Optional[ToolContext] = None,
    order_number: str = "",
    email: str = "",
    create_date_from: str = "",
    create_date_to: str = "",
    status: str = "",
    current_page: int = 1,
    page_size: int = 10,
    include_all_items: bool = False
) -> dict[str, Any]:
    """
    Check order information for both logged-in customers and guest users.

    This unified tool handles ALL order queries:
    - Logged-in users: Returns order history with optional filters (status, date)
    - Guest users: Requires order_number + email to track specific order

    Args:
        tool_context: Tool execution context (automatically provided)
        order_number: Order number (e.g., "101000002403") - required for guests, optional for logged-in
        email: Email used during checkout - required for guest order tracking
        create_date_from: Start date filter "YYYY-MM-DD" (logged-in only)
        create_date_to: End date filter "YYYY-MM-DD" (logged-in only)
        status: Filter by status using friendly names or codes (logged-in only):
            - "đang giao" or "delivering" → invoiced_ccod,in_shipment_ccod,picked_ccod,picking_ccod
            - "đã giao" or "delivered" → complete,completed_ccod
            - "đã hủy" or "canceled" → backorder_ccod,canceled,closed,deleted_ccod
            - "đang xử lý" or "processing" → confirmed_ccod,order_error,processing
            - "đã ghi nhận đơn hàng" or "pending" → pending,pending_ccod
            - "chờ thanh toán" or "awaiting_payment" → pending_payment
            - "chờ hủy" or "waiting_cancel" → waiting_cancel
        current_page: Page number (default: 1, logged-in only)
        page_size: Orders per page (default: 10, logged-in only)
        include_all_items: Return all products or just first one (default: False)

    Returns:
        dict with success, order_data, message, instruction_for_agent

    Examples:
        "Xem đơn hàng của tôi" → check_my_orders()
        "Xem đơn 101000002403" → check_my_orders(order_number="101000002403")
        "Đơn hàng đang giao" → check_my_orders(status="đang giao")
        "Đơn hàng đã giao" → check_my_orders(status="đã giao")
        "Đơn hàng đã hủy" → check_my_orders(status="đã hủy")
        "Đơn hàng hôm nay" → check_my_orders(create_date_from="2025-11-26", create_date_to="2025-11-26")
        "Đơn từ 1/11 đến 5/11" → check_my_orders(create_date_from="2025-11-01", create_date_to="2025-11-05")
        Guest: "Kiểm tra đơn 191000000069 email abc@example.com" → check_my_orders(order_number="191000000069", email="abc@example.com")
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                "instruction_for_agent": "Inform the user 'Hiện em không truy cập được thông tin đơn hàng, anh/chị thử lại sau ít phút nhé.'",
                "code": "MISSING_TOOL_CONTEXT"
            }

        # Safely access tool_context.state with proper None checks
        if not hasattr(tool_context, 'state') or tool_context.state is None:
            return {
                "success": False,
                "message": "Tool context state is missing",
                "instruction_for_agent": "Inform the user 'Hiện em không truy cập được thông tin đơn hàng, anh/chị thử lại sau ít phút nhé.'",
                "code": "MISSING_TOOL_CONTEXT_STATE"
            }

        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
        signin_token = magento_session_data.get("signin_token") or ""
        signin_token = signin_token.strip('"')
        is_logged_in = bool(signin_token)

        # Map friendly status names to actual status codes
        if status:
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
            status = status_mapping.get(status.lower(), status)

        if is_logged_in:
            if order_number:
                return await _get_logged_in_customer_order_by_number(
                    tool_context=tool_context,
                    order_number=order_number,
                    base_url=base_url,
                    store_id=store_id,
                    signin_token=signin_token,
                    include_all_items=include_all_items,
                    create_date_from=create_date_from,
                    create_date_to=create_date_to
                )

            graphql_query = """
                query GetCustomerOrders($currentPage: Int!, $pageSize: Int!, $filter: CustomerOrdersFilterInput) {
                customer {
                    orders(currentPage: $currentPage, pageSize: $pageSize, filter: $filter) {
                        items {
                            id
                            number
                            order_date
                            status
                            status_code
                            state
                            delivery_information {
                                delivery_date
                                delivery_from
                                delivery_to

                            }
                            invoices {
                                id

                            }
                            items {
                                id
                                product_name
                                product_sale_price {
                                    currency
                                    value

                                }
                                product_sku
                                product_url_key
                                selected_options {
                                    label
                                    value

                                }
                                quantity_ordered
                                product {
                                    id
                                    uid
                                    ecom_name
                                    thumbnail {
                                        url
                                    }
                                    small_image {
                                        url
                                        __typename
                                    }
                                }

                            }
                            billing_address {
                                city
                                country_code
                                firstname
                                postcode
                                region
                                street
                                telephone
                                
                            }
                            payment_methods {
                                name
                                type
                                additional_data {
                                    name
                                    value
                                    
                                }
                                
                            }
                            shipments {
                                id
                                tracking {
                                    number
                                    
                                }
                                
                            }
                            shipping_address {
                                city
                                country_code
                                firstname
                                postcode
                                region
                                street
                                telephone
                                
                            }
                            shipping_method
                            total {
                                discounts {
                                    amount {
                                        currency
                                        value
                                        
                                    }
                                    
                                }
                                grand_total {
                                    currency
                                    value
                                    
                                }
                                subtotal {
                                    currency
                                    value
                                    
                                }
                                total_shipping {
                                    currency
                                    value
                                    
                                }
                                total_tax {
                                    currency
                                    value
                                    
                                }
                                
                            }
                            
                        }
                        page_info {
                            current_page
                            total_pages
                            
                        }
                        total_count
                        
                    }
                    
                }
            }
            """
            # Build filter object - always include all fields like the curl example
            # Format dates to YYYY-MM-DD (remove time part if present)
            if create_date_from and " " in create_date_from:
                create_date_from = create_date_from.split(" ")[0]
            if create_date_to and " " in create_date_to:
                create_date_to = create_date_to.split(" ")[0]

            # Build filter with all fields (matching curl format exactly)
            # Note: Use "match": "" for general queries (not searching specific order number)
            filter_obj = {
                "number": {"match": ""},
                "createDateFrom": {"gteq": create_date_from if create_date_from else ""},
                "createDateTo": {"lteq": create_date_to if create_date_to else ""},
                "status": {"eq": status if status else ""}
            }

            # Build variables with filter always included
            variables = {
                "currentPage": current_page,
                "pageSize": page_size,
                "filter": filter_obj
            }

            try:
                logger.info(f"Calling GraphQL API - base_url: {base_url}, store_id: {store_id}, has_token: {bool(signin_token)}")

                res = await make_graphql_request_async(
                    graphql_query,
                    variables,
                    base_url,
                    store_id,
                    auth_token=signin_token,
                )

                if not res:
                    logger.error(f"No response from GraphQL API")
                    order_history_url = f"{base_url}/order-history"
                    return {
                        "success": False,
                        "message": "No response from API",
                        "instruction_for_agent": f"Tell the user: 'Hiện chưa thể xem thông tin đơn hàng này. Anh/Chị vui lòng chọn [Quản lý đơn hàng]({order_history_url}) để kiểm tra thông tin đơn hàng và lộ trình giao hàng giúp em nhé.'",
                        "code": "NO_RESPONSE",
                        "order_history_url": order_history_url
                    }

                # Check for GraphQL errors (even if data exists)
                if res.get("errors"):
                    error_message = res.get("errors", [{}])[0].get("message", "Unknown error")
                    order_history_url = f"{base_url}/order-history"
                    return {
                        "success": False,
                        "message": f"API error: {error_message}",
                        "instruction_for_agent": f"Tell the user: 'Hiện chưa thể xem thông tin đơn hàng này. Anh/Chị vui lòng chọn [Quản lý đơn hàng]({order_history_url}) để kiểm tra thông tin đơn hàng và lộ trình giao hàng giúp em nhé.'",
                        "code": "GRAPHQL_ERROR",
                        "order_history_url": order_history_url
                    }

                if not res.get("data"):
                    logger.error(f"No data in response")
                    order_history_url = f"{base_url}/order-history"
                    return {
                        "success": False,
                        "message": "No data in API response",
                        "instruction_for_agent": f"Tell the user: 'Hiện chưa thể xem thông tin đơn hàng này. Anh/Chị vui lòng chọn [Quản lý đơn hàng]({order_history_url}) để kiểm tra thông tin đơn hàng và lộ trình giao hàng giúp em nhé.'",
                        "code": "INVALID_RESPONSE",
                        "order_history_url": order_history_url
                    }

                # Safely navigate nested structure
                customer_data = res.get("data", {}).get("customer")
                if not customer_data:
                    logger.error(f"No customer data in response")
                    order_history_url = f"{base_url}/order-history"
                    return {
                        "success": False,
                        "message": "No customer data in API response",
                        "instruction_for_agent": f"Tell the user: 'Hiện chưa thể xem thông tin đơn hàng này. Anh/Chị vui lòng chọn [Quản lý đơn hàng]({order_history_url}) để kiểm tra thông tin đơn hàng và lộ trình giao hàng giúp em nhé.'",
                        "code": "INVALID_CUSTOMER_DATA",
                        "order_history_url": order_history_url
                    }

                orders_data = customer_data.get("orders", {})
                total_count = orders_data.get("total_count", 0)
                processed_orders_list = [process_order_data(order, include_all_items=include_all_items) for order in orders_data.get("items", [])]
                order_status_filter = ""

                # Only set order_status_filter when user explicitly filters by status
                # Do NOT auto-set from first order when filtering by date only
                if status:
                    if "," in status:
                        order_status_filter = status
                    else:
                        # Status là single code, cần map
                        order_status_filter = STATUS_CODE_TO_FILTER.get(status, "")

                processed_result = {
                    "items": processed_orders_list,
                    "page_info": orders_data.get("page_info"),
                    "total_count": total_count,
                    "order_status_filter": order_status_filter,
                    "start_date": create_date_from if create_date_from else None,
                    "end_date": create_date_to if create_date_to else None
                }
                await save_order_result_to_state(processed_result, tool_context)

                # Kiểm tra nếu không có đơn hàng nào
                if total_count == 0:
                    return {
                        "success": True,
                        "order_data": processed_result,
                        "message": "Dạ, anh/chị chưa có đơn hàng nào ạ. Anh/chị có muốn em tìm kiếm sản phẩm nào không ạ?",
                        "instruction_for_agent": "Tell the user they have no orders yet and offer to help them find products.",
                        "order_status_filter": order_status_filter
                    }

                # Tạo message với tổng số đơn hàng
                if total_count == 1:
                    message = "Dạ Anh/Chị có 1 đơn hàng. Vui lòng chọn **Xem chi tiết** để kiểm tra thông tin đơn hàng và lộ trình giao hàng nhé."
                    instruction = "Display the order information to the user in a clear and organized manner. Include order number, date, status, items, shipping info, and total."
                else:
                    # Khi có filter theo status (đang giao, đã hủy...), hiển thị total_count của trạng thái đó
                    # Khi không có filter, hiển thị tất cả đơn hàng
                    message = f"Dạ Anh/Chị có tổng **{total_count} đơn hàng**. Vui lòng chọn **Xem chi tiết** để kiểm tra thông tin đơn hàng và lộ trình giao hàng nhé."
                    instruction = (
                        "Display the order information to the user in a clear and organized manner. "
                        "IMPORTANT: If the user asks about delivery time/shipping information (e.g., 'khi nào giao tới', 'bao giờ nhận hàng') "
                        "and there are multiple orders, you MUST ask the user which specific order they want to check. "
                        "Do NOT automatically pick one order to show delivery details. "
                        "Example response: 'Dạ, anh/chị có nhiều đơn hàng. Anh/chị vui lòng cho em biết muốn xem thông tin giao hàng của đơn nào ạ?'"
                    )

                return {
                    "success": True,
                    "order_data": processed_result, # Trả về đối tượng đã xử lý
                    "message": message,
                    "instruction_for_agent": instruction,
                    "order_status_filter": order_status_filter
                }

            except Exception as e:
                logger.error(f"Error fetching customer orders: {str(e)}")
                logger.error(f"Full traceback: {traceback.format_exc()}")
                order_history_url = f"{base_url}/order-history"
                return {
                    "success": False,
                    "message": f"Error fetching orders: {str(e)}",
                    "instruction_for_agent": f"Tell the user: 'Hiện chưa thể xem thông tin đơn hàng này. Anh/Chị vui lòng chọn [Quản lý đơn hàng]({order_history_url}) để kiểm tra thông tin đơn hàng và lộ trình giao hàng giúp em nhé.'",
                    "code": "API_ERROR",
                    "error_details": str(e),
                    "order_history_url": order_history_url
                }
        else:
            # Normalize inputs (convert string "None" to empty string)
            order_number = order_number if order_number and order_number != "None" else ""
            email = email if email and email != "None" else ""

            # Lấy email từ state - ƯU TIÊN order_email_map hơn email do LLM truyền vào
            # Lý do: LLM có thể tự động điền email sai từ context (conversation history)
            logger.warning(f"[DEBUG] Attempting to retrieve email. email={email}, order_number={order_number}, tool_context.state={tool_context.state is not None if tool_context else None}")
            if order_number and tool_context.state is not None:
                try:
                    logger.warning(f"[DEBUG] tool_context.state contents: {tool_context.state}")

                    # Try multiple sources for email (priority order):
                    # 1. order_email_map[order_number] - HIGHEST PRIORITY, override LLM-provided email
                    # 2. Email do LLM truyền vào (nếu không có trong order_email_map)
                    # 3. ROOT level guest_user_email (latest email)
                    # 4. Nested state.state.guest_user_email (backup)
                    # 5. Nested state.state.guest_checkout_info.email (backup)

                    # Priority 1: Check order-specific email mapping - ALWAYS OVERRIDE LLM email if found
                    order_email_map = tool_context.state.get('order_email_map', {})
                    mapped_email = order_email_map.get(order_number, '')
                    if mapped_email:
                        if email and email != mapped_email:
                            logger.warning(f"[DEBUG] OVERRIDING LLM-provided email '{email}' with order_email_map email '{mapped_email}' for order {order_number}")
                        else:
                            logger.warning(f"[DEBUG] Found email in order_email_map for {order_number}: {mapped_email}")
                        email = mapped_email  # Always use mapped email if available
                    else:
                        logger.warning(f"[DEBUG] No email in order_email_map for {order_number}, LLM email: {email}")

                    # Priority 2-5: Only if no email yet (neither from LLM nor order_email_map)
                    if not email:
                        # Priority 2: ROOT level guest_user_email (latest email)
                        saved_email = tool_context.state.get('guest_user_email', '')
                        logger.warning(f"[DEBUG] ROOT level guest_user_email: {saved_email}")

                        # Priority 3: Nested state
                        if not saved_email:
                            state_data = tool_context.state.get('state', {})
                            saved_email = state_data.get('guest_user_email', '')
                            logger.warning(f"[DEBUG] Nested state guest_user_email: {saved_email}")

                        # Priority 4: guest_checkout_info
                        if not saved_email:
                            state_data = tool_context.state.get('state', {})
                            guest_checkout_info = state_data.get('guest_checkout_info', {})
                            saved_email = guest_checkout_info.get('email', '')
                            logger.warning(f"[DEBUG] Retrieved email from guest_checkout_info: {saved_email}")

                        if saved_email:
                            email = saved_email
                            logger.warning(f"[DEBUG] Using fallback email from state: {email}")

                    logger.warning(f"[DEBUG] Final email to use: {email}")
                except Exception as e:
                    logger.error(f"[DEBUG] Failed to retrieve email from state: {e}")
            else:
                logger.warning(f"[DEBUG] Skipped retrieve - email={bool(email)}, order_number={bool(order_number)}, state_exists={tool_context.state is not None if tool_context else False}")

            # If no order info provided, ask for it with specific guidance
            if not order_number or not email:
                order_tracking_url = f"{base_url}/order-tracking"
                login_url = f"{base_url}/sign-in"
                register_url = f"{base_url}/create-account"

                # Xác định message dựa vào case
                if not order_number and not email:
                    message = (
                        f"Anh/chị vui lòng nhập **Mã đơn hàng** và **Email** thanh toán để em hỗ trợ tìm kiếm nhé. "
                        f"Hoặc Anh/Chị chọn [Đăng nhập]({login_url}) để xem danh sách đơn hàng hiện có."
                    )
                    code = "MISSING_BOTH"
                elif not order_number:
                    message = (
                        f"Chưa có thông tin **mã đơn hàng**. "
                        f"Anh/chị vui lòng nhập **Mã đơn hàng** và **Email** thanh toán để em hỗ trợ tìm kiếm nhé. "
                        f"Hoặc Anh/Chị chọn [Đăng nhập]({login_url}) để xem danh sách đơn hàng hiện có."
                    )
                    code = "MISSING_ORDER_NUMBER"
                else:  # not email
                    message = (
                        f"Chưa có thông tin email. "
                        f"Anh/chị vui lòng nhập **Mã đơn hàng** và **Email** thanh toán để em hỗ trợ tìm kiếm nhé. "
                        f"Hoặc Anh/Chị chọn [Đăng nhập]({login_url}) để xem danh sách đơn hàng hiện có."
                    )
                    code = "MISSING_EMAIL"

                return {
                    "success": False,
                    "login_required": True,
                    "show_signin_for_order_cta_button": True,
                    "message": message,
                    "instruction_for_agent": (
                        f"Tell the user: '{message}'. "
                        f"If user explicitly says they don't have an account, call register_account tool instead."
                    ),
                    "code": code,
                    "order_tracking_url": order_tracking_url,
                    "login_url": login_url,

                }
            return await track_guest_order(
                tool_context=tool_context,
                order_number=order_number,
                email=email,
                include_all_items=include_all_items
            )

    except Exception as e:
        logger.error(traceback.format_exc())
        # Try to get base_url for order history link
        try:
            if tool_context and hasattr(tool_context, 'state') and tool_context.state is not None:
                magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
                base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
            else:
                base_url = DEFAULT_MMVN_STORE_URL.rstrip("/")
            order_history_url = f"{base_url}/order-history"
        except:
            order_history_url = f"{DEFAULT_MMVN_STORE_URL.rstrip('/')}/order-history"

        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "instruction_for_agent": f"Tell the user: 'Hiện chưa thể xem thông tin đơn hàng này. Anh/Chị vui lòng chọn [Quản lý đơn hàng]({order_history_url}) để kiểm tra thông tin đơn hàng và lộ trình giao hàng giúp em nhé.'",
            "code": "UNEXPECTED_ERROR",
            "order_history_url": order_history_url
        }


async def track_guest_order(
    tool_context: Optional[ToolContext] = None,
    order_number: str = "",
    email: str = "",
    include_all_items: bool = False
) -> dict[str, Any]:
    """
    Track guest order using order number and email via GraphQL API.

    This tool allows guest customers to check their order status by providing
    their order number and the email address used during checkout.

    Args:
        tool_context: Tool execution context (automatically provided)
        order_number: Order number to track (required, e.g., "191000000069")
        email: Email address used during checkout (required, e.g., "customer@example.com")
        include_all_items: If True, return all products in the order. If False (default), return only first product for display.

    Returns:
        dict with:
            - success (bool): Whether the order was found
            - order_data (dict): Order information if found
            - message (str): Status message
            - instruction_for_agent (str): Guidance for the agent on how to respond
            - show_signin_for_order_cta_button (bool): When True, display sign-in button for guest users to access their orders

    Example:
        User: "Kiểm tra đơn hàng 191000000069 với email abc@example.com"
        Agent calls: track_guest_order(order_number="191000000069", email="abc@example.com")
    """
    try:
        # Normalize inputs (convert string "None" to empty string)
        order_number = order_number if order_number and order_number != "None" else ""
        email = email if email and email != "None" else ""

        # Validate inputs with specific guidance
        if not order_number or not email:
            # Get base_url for login/register links
            if tool_context and hasattr(tool_context, 'state') and tool_context.state is not None:
                magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
                base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
            else:
                base_url = DEFAULT_MMVN_STORE_URL.rstrip("/")

            login_url = f"{base_url}/sign-in"
            register_url = f"{base_url}/create-account"

            # Xác định message dựa vào case
            if not order_number and not email:
                message = (
                    f"Anh/chị vui lòng nhập **Mã đơn hàng** và **Email** thanh toán để em hỗ trợ tìm kiếm nhé. "
                    f"Hoặc Anh/Chị chọn [Đăng nhập]({login_url}) để xem danh sách đơn hàng hiện có."
                )
                code = "MISSING_BOTH"
            elif not order_number:
                message = (
                    f"Chưa có thông tin **mã đơn hàng**. "
                    f"Anh/chị vui lòng nhập **Mã đơn hàng** và **Email** thanh toán để em hỗ trợ tìm kiếm nhé. "
                    f"Hoặc Anh/Chị chọn [Đăng nhập]({login_url}) để xem danh sách đơn hàng hiện có."
                )
                code = "MISSING_ORDER_NUMBER"
            else:  # not email
                message = (
                    f"Chưa có thông tin email. "
                    f"Anh/chị vui lòng nhập **Mã đơn hàng** và **Email** thanh toán để em hỗ trợ tìm kiếm nhé. "
                    f"Hoặc Anh/Chị chọn [Đăng nhập]({login_url}) để xem danh sách đơn hàng hiện có."
                )
                code = "MISSING_EMAIL"

            return {
                "success": False,
                "show_signin_for_order_cta_button": True,
                "message": message,
                "instruction_for_agent": (
                    f"Tell the user: '{message}'. "
                    f"If user explicitly says they don't have an account, call register_account tool instead."
                ),
                "code": code,
                "login_url": login_url,

            }

        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                "instruction_for_agent": "Inform the user 'Hiện em không tra cứu được đơn hàng, anh/chị thử lại sau ít phút nhé.'",
                "code": "MISSING_TOOL_CONTEXT"
            }

        # Safely access tool_context.state with proper None checks
        if not hasattr(tool_context, 'state') or tool_context.state is None:
            return {
                "success": False,
                "message": "Tool context state is missing",
                "instruction_for_agent": "Inform the user 'Hiện em không tra cứu được đơn hàng, anh/chị thử lại sau ít phút nhé.'",
                "code": "MISSING_TOOL_CONTEXT_STATE"
            }

        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        query = """
        query orderTracking($order_number: String!, $email: String!) {
            orderTracking(order_number: $order_number, email: $email) {
                id
                email
                ...CustomerOrderFragment
                
            }
        }

        fragment CustomerOrderFragment on CustomerOrder {
            id
            number
            order_date
            customer_no
            is_sync_ccod
            shipping_code
            delivery_code
            delivery_status
            invoices {
                id
                
            }
            delivery_information {
                delivery_date
                delivery_from
                delivery_to
                
            }
            vat_information {
                company_address
                company_name
                company_vat_number
                customer_vat_id
                
            }
            items {
                id
                product_name
                product_sale_price {
                    currency
                    value
                    
                }
                product_sku
                product_url_key
                selected_options {
                    label
                    value
                    
                }
                quantity_ordered
                product {
                    id
                    uid
                    unit_ecom
                    ecom_name
                    is_alcohol
                    thumbnail {
                        url

                    }
                    small_image {
                        url
                        __typename
                    }
                    canonical_url
                    dnr_price {
                        qty
                        promo_label
                        promo_type
                        promo_amount
                        promo_value
                        event_id
                        event_name

                    }

                }
                
            }
            promotion_message
            billing_address {
                firstname
                country_code
                city
                district
                ward
                street
                telephone
                
            }
            payment_methods {
                name
                type
                additional_data {
                    name
                    value
                    
                }
                
            }
            shipments {
                id
                tracking {
                    number
                    
                }
                
            }
            shipping_address {
                firstname
                country_code
                city
                district
                ward
                street
                telephone
                
            }
            shipping_method
            status
            status_code
            state
            total {
                discounts {
                    label
                    amount {
                        currency
                        value
                        
                    }
                    
                }
                grand_total {
                    currency
                    value
                    
                }
                base_total_after_discount {
                    currency
                    value
                    
                }
                subtotal {
                    currency
                    value
                    
                }
                total_shipping {
                    currency
                    value
                    
                }
                total_tax {
                    currency
                    value
                    
                }
                
            }
            
        }
        """

        variables = {
            "order_number": order_number,
            "email": email
        }
        try:
            result = await make_graphql_request_async(
                query=query,
                variables=variables,
                base_url=base_url,
                store_id=magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID),
                max_retries=3
            )
        except Exception as e:
            logger.error(f"GraphQL request failed: {str(e)}")
            return {
                "success": False,
                "message": f"API request failed: {str(e)}",
                "instruction_for_agent": (
                    "Tell the user: 'Hiện em không tra cứu được đơn hàng, "
                    "anh/chị vui lòng kiểm tra lại thông tin hoặc thử lại sau ít phút nhé.'"
                ),
                "code": "API_ERROR"
            }

        # Check for GraphQL errors
        if not result:
            order_history_url = f"{base_url}/order-history"
            return {
                "success": False,
                "message": "No response from API",
                "instruction_for_agent": f"Tell the user: 'Hiện chưa thể xem thông tin đơn hàng này. Anh/Chị vui lòng chọn [Quản lý đơn hàng]({order_history_url}) để kiểm tra thông tin đơn hàng và lộ trình giao hàng giúp em nhé.'",
                "code": "NO_RESPONSE",
                "order_history_url": order_history_url
            }

        if "errors" in result:
            error_messages = [err.get("message", "") for err in result["errors"]]
            logger.error(f"GraphQL errors: {error_messages}")

            magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
            base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
            order_tracking_url = f"{base_url}/order-tracking"
            login_url = f"{base_url}/sign-in"

            return {
                "success": False,
                "show_signin_for_order_cta_button": True,
                "message": f"GraphQL errors: {', '.join(error_messages)}",
                "instruction_for_agent": (
                    "Tell the user: '**Mã đơn hàng** hoặc **email** không đúng. Anh/Chị vui lòng nhập lại **Mã đơn hàng** và **Email thanh toán** chính xác. "
                    f"Hoặc chọn [Đăng nhập]({login_url}) để xem danh sách đơn hàng hiện có.\n\n"
                ),
                "code": "GRAPHQL_ERROR",
                "order_tracking_url": order_tracking_url,
                "login_url": login_url,

            }

        order_data = result.get("data", {}).get("orderTracking")

        if not order_data:
            magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
            base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
            order_tracking_url = f"{base_url}/order-tracking"
            login_url = f"{base_url}/sign-in"

            return {
                "success": False,
                "show_signin_for_order_cta_button": True,
                "message": "Order not found",
                "instruction_for_agent": (
                    "Tell the user: '**Mã đơn hàng** hoặc **email** không đúng. Anh/Chị vui lòng nhập lại **Mã đơn hàng** và **Email thanh toán** chính xác. "
                    f"Hoặc chọn [Đăng nhập]({login_url}) để xem danh sách đơn hàng hiện có.\n\n"
                ),
                "code": "ORDER_NOT_FOUND",
                "order_tracking_url": order_tracking_url,
                "login_url": login_url,

            }

        processed_order_info = process_order_data(order_data, include_all_items=include_all_items)
        # Guest order tracking doesn't use date filters
        processed_order_info['start_date'] = None
        processed_order_info['end_date'] = None

        # Save both order data AND email in a SINGLE state update to avoid conflicts
        logger.warning(f"[DEBUG] Attempting to save order data and email. tool_context={tool_context is not None}, email={email}")
        if tool_context:
            try:
                # Save order result to state
                await save_order_result_to_state(processed_order_info, tool_context)

                # Now save email to state
                # IMPORTANT: Save at ROOT level to prevent frontend from overwriting
                # Frontend often sends state.state with only magento_session_data
                if email:
                    logger.warning(f"[DEBUG] tool_context.state before email save: {tool_context.state}")

                    # Save at ROOT level (primary) - won't be overwritten by frontend
                    tool_context.state['guest_user_email'] = email
                    logger.warning(f"[DEBUG] Saved guest email to ROOT state: {email}")

                    # Save order-to-email mapping for multi-order tracking
                    # This allows tracking different orders with different emails
                    if order_number:
                        order_email_map = tool_context.state.get('order_email_map', {})
                        order_email_map[order_number] = email
                        tool_context.state['order_email_map'] = order_email_map
                        logger.warning(f"[DEBUG] Saved order-email mapping: {order_number} -> {email}")

                    # Also save in nested state as backup (may be overwritten by frontend)
                    if 'state' not in tool_context.state:
                        tool_context.state['state'] = {}
                    tool_context.state['state']['guest_user_email'] = email

                    logger.warning(f"[DEBUG] Successfully saved guest email to both ROOT and nested state: {email}")
                    logger.warning(f"[DEBUG] tool_context.state after email save: {tool_context.state}")
                    logger.info(f"Saved guest user email to state for future queries")
                else:
                    logger.warning(f"[DEBUG] Skipped email save - email is empty")
            except Exception as e:
                logger.error(f"[DEBUG] Failed to save order/email to state: {e}")
                logger.error(f"[DEBUG] Exception traceback: {traceback.format_exc()}")
        else:
            logger.warning(f"[DEBUG] Skipped save - tool_context is None")

        order_status = processed_order_info.get("status", "")
        status_code = processed_order_info.get("status_code", "")

        # Kiểm tra xem đơn hàng đã hoàn thành chưa
        is_completed = status_code in COMPLETED_STATUS_CODES

        # Xác định filter status dựa trên trạng thái đơn hàng cho frontend
        order_status_filter = STATUS_CODE_TO_FILTER.get(status_code, "")

        if include_all_items:
            message = f"Dưới đây là các sản phẩm trong đơn hàng **{order_number}** của anh chị ạ."
        elif is_completed:
            message = f"Đơn hàng của Anh/Chị đang ở trạng thái: {order_status}. Anh/Chị vui lòng chọn **Xem chi tiết** để kiểm tra thông tin đơn hàng giúp em nhé."
        else:
            message = f"Đơn hàng của Anh/Chị đang ở trạng thái: {order_status}. Anh/Chị vui lòng chọn **Xem chi tiết** để kiểm tra thông tin đơn hàng và lộ trình giao hàng giúp em nhé."

        return {
            "success": True,
            "order_data": processed_order_info,
            "raw_data": order_data,
            "message": message,
            "instruction_for_agent": (
                f"Display the order information to the user with the message already provided in the message field."
            ),
            "code": "SUCCESS",
            "order_status_filter": order_status_filter
        }
        
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}\n{traceback.format_exc()}")
        # Try to get base_url for order history link
        try:
            if tool_context and hasattr(tool_context, 'state') and tool_context.state is not None:
                magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
                base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
            else:
                base_url = DEFAULT_MMVN_STORE_URL.rstrip("/")
            order_history_url = f"{base_url}/order-history"
        except:
            order_history_url = f"{DEFAULT_MMVN_STORE_URL.rstrip('/')}/order-history"

        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "instruction_for_agent": f"Tell the user: 'Hiện chưa thể xem thông tin đơn hàng này. Anh/Chị vui lòng chọn [Quản lý đơn hàng]({order_history_url}) để kiểm tra thông tin đơn hàng và lộ trình giao hàng giúp em nhé.'",
            "code": "UNEXPECTED_ERROR",
            "order_history_url": order_history_url
        }