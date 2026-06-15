"""
Change Store Tool - Trigger popup đổi kho cho user
Tool này chỉ trả signal để frontend hiện popup, không xử lý logic
"""

from google.adk.tools import FunctionTool, LongRunningFunctionTool
from google.adk.tools import FunctionTool
from typing import Dict, Any


def trigger_change_store() -> Dict[str, Any]:
    """
    Long-running, human-in-the-loop tool that open a popup for user to change store.
    The user will need to fill in their address information in the popup to change store.

    Returns:
        indicated whether the popup is triggered.
    """
    return {
        "status": "pending",
        "message": "Please fill in your information in the popup to change store.",
        "instruction_for_agent": "A `change store` popup has been triggered for the user to fill in their address information. The agent must inform the user to complete the information in the popup."
    }


def confirm_store_changed(store_name: str) -> Dict[str, Any]:
    """
    Nhận thông báo từ frontend rằng user đã đổi kho thành công.
    Tool này chỉ để format message xác nhận.
    
    Args:
        store_name: Tên kho mà user đã chọn (VD: "MM An Phú, Thành phố Thủ Đức...")
        
    Returns:
        Message xác nhận để hiển thị trong chat
        
    Example:
        confirm_store_changed("MM An Phú, Thành phố Thủ Đức...")
        => {
            "success": True,
            "message": "Em đã đổi địa chỉ giao hàng sang MM An Phú..."
        }
    """
    return {
        "success": True,
        "message": f"Em đã đổi địa chỉ giao hàng sang {store_name} cho anh/chị rồi ạ!",
        "store_name": store_name
    }


TriggerChangeStoreTool = LongRunningFunctionTool(trigger_change_store)
ConfirmStoreChangedTool = FunctionTool(confirm_store_changed)
ChangeStoreTool = TriggerChangeStoreTool

