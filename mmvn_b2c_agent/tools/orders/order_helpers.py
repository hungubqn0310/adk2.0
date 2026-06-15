import logging
from typing import Optional, Any
from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)


ORDER_STATE_KEY = 'last_order_result'

async def save_order_result_to_state(
    order_result_data: dict[str, Any],
    tool_context: ToolContext
) -> None:
    """
    Save the USER's latest order query result to the tool context state.

    This helper function persists the user's order query results
    (which could be a single order or a paginated list of orders)
    in the session state. This data is intended to be read by
    the CngSetResponse tool for formatting the final output.

    Args:
        order_result_data: The processed order data (e.g., from process_order_data).
        tool_context: Tool execution context (automatically provided).

    Returns:
        None: This function updates the state in-place.
    """
    if not tool_context:
        logger.warning("Tool context is missing in save_order_result_to_state.")
        return

    # Ensure tool_context.state exists
    if not hasattr(tool_context, 'state') or tool_context.state is None:
        tool_context.state = {}

    # Ensure 'state' key exists within tool_context.state
    if 'state' not in tool_context.state:
        tool_context.state['state'] = {}

    # Get invocation_id safely
    invocation_id = getattr(tool_context, 'invocation_id', None)
    logger.warning(f"[DEBUG] save_order_result_to_state: invocation_id={invocation_id}")

    # Direct assignment (like cart code) - ADK State object handles persistence automatically
    tool_context.state['state'][ORDER_STATE_KEY] = {
        'data': order_result_data,
        'invocation_id': invocation_id
    }

    logger.warning(f"[DEBUG] save_order_result_to_state: Direct assignment completed successfully")

    logger.info(f"Saved order data to state (key: {ORDER_STATE_KEY}).")


async def get_last_order_result_from_state(
    tool_context: ToolContext
) -> Optional[dict[str, Any]]:
    """
    Get the USER's last order query result from the tool context state.
    
    This helper reads the 'last_order_result' object, which contains
    the data and metadata (like invocation_id) of the last order query.
    
    LƯU Ý: Hàm này chỉ LẤY dữ liệu, không XÓA.

    Args:
        tool_context: Tool execution context.
    
    Returns:
        Optional[dict[str, Any]]: The wrapper dict {'data': ..., 'invocation_id': ...} or None.
    """
    if not tool_context or not tool_context.state:
        return None
    
    # Trả về toàn bộ đối tượng (wrapper)
    return tool_context.state.get('state', {}).get(ORDER_STATE_KEY)


async def consume_last_order_result_from_state(
    tool_context: ToolContext
) -> Optional[dict[str, Any]]:
    """
    Get AND CLEAR the USER's last order query result from the state.

    This function reads the order data and then immediately clears it
    from the state to prevent stale data from being shown in
    subsequent, unrelated responses.
    
    Đây là hàm mà `CngSetResponse.format_output` NÊN sử dụng.

    Args:
        tool_context: Tool execution context.
        
    Returns:
        Optional[dict[str, Any]]: Chỉ trả về phần 'data' của kết quả đơn hàng, hoặc None.
    """
    if not tool_context or not hasattr(tool_context, 'state') or not tool_context.state:
        return None

    state = tool_context.state.get('state', {})
    order_state_object = state.get(ORDER_STATE_KEY)

    if order_state_object:

        if 'state' in tool_context.state:
            tool_context.state['state'][ORDER_STATE_KEY] = None
        logger.info(f"Consumed and cleared order data from state (key: {ORDER_STATE_KEY}).")
        return order_state_object.get('data')

    return None