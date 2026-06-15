import json
import logging
import re
from typing import Optional, Dict, Any
import requests
from google.adk.tools import ToolContext

from mmvn_b2c_agent.tools.utils import make_graphql_request

logger = logging.getLogger("google_adk." + __name__)

# Constants
DEFAULT_MMVN_STORE_URL = "https://b2c-mmpro.izysync.com"


def get_current_store_from_state(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Lấy thông tin cửa hàng hiện tại từ state (magento_session_data).
    
    Args:
        tool_context (ToolContext): The context in which the tool operates.
    
    Returns:
        Dict[str, Any]: A dictionary containing:
            - success (bool): Whether the operation was successful
            - current_store (dict): Current store info {store_id, base_url, magento_cart_id}
            - error (str): Error message if operation failed
    """
    print(
        f"{'-' * 80}\n"
        f"TOOL CALLED: get_current_store_from_state\n"
        f"{'-' * 80}\n"
    )
    
    try:
        # Lấy magento_session_data từ tool_context.state
        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        
        if not magento_session_data:
            print(f"No magento_session_data found in state")
            return {
                "success": False,
                "error": f"No magento_session_data found in state",
                "instruction_for_agent": "Please ensure the user has a valid session"
            }
        
        # Lấy store_id từ state
        store_id = magento_session_data.get("store_id")
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        magento_cart_id = magento_session_data.get("magento_cart_id") or ""
        magento_cart_id = magento_cart_id.strip('"')
        
        if not store_id:
            print(f"No store_id found in magento_session_data")
            return {
                "success": False,
                "error": f"No store_id found in magento_session_data",
                "instruction_for_agent": "Please ask user to select a store first"
            }
        
        current_store = {
            "store_id": store_id,
            "base_url": base_url,
            "magento_cart_id": magento_cart_id
        }
        
        print(f"Current store from state:")
        print(f"  - Store ID: {current_store['store_id']}")
        print(f"  - Base URL: {current_store['base_url']}")
        print(f"  - Cart ID: {current_store['magento_cart_id']}")
        
        return {
            "success": True,
            "current_store": current_store
        }
    
    except Exception as e:
        logger.error(f"Error in get_current_store_from_state: {str(e)}")
        return {
            "success": False,
            "error": f"Unexpected error: {str(e)}"
        }


def get_current_store(
    tool_context: ToolContext,
    base_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    Lấy store_id từ state và so sánh với API để lấy thông tin đầy đủ.
    
    Args:
        tool_context (ToolContext): The context in which the tool operates.
        base_url (str): Base URL for GraphQL API (optional, lấy từ state nếu không cung cấp)
    
    Returns:
        Dict[str, Any]: Store info from state + full info from API
    """
    print(
        f"{'-' * 80}\n"
        f"TOOL CALLED: get_current_store\n"
        f"{'-' * 80}\n"
    )
    
    try:
        # Lấy store từ state
        state_result = get_current_store_from_state(tool_context)
        
        if not state_result.get("success"):
            return state_result
        
        current_store = state_result.get("current_store", {})
        store_id = current_store.get("store_id")
        api_base_url = base_url or current_store.get("base_url", DEFAULT_MMVN_STORE_URL)
        
        if not store_id:
            return {
                "success": False,
                "error": "No store_id found in state"
            }
        
        # Query API để lấy danh sách tất cả stores
        query = """
        query {
            storeList {
                code
                name
            }
        }
        """
        
        print(f"Calling API with base_url: {api_base_url}")
        response = make_graphql_request(query, {}, base_url=api_base_url)
        print(f"API response status: {response.status_code if hasattr(response, 'status_code') else 'N/A'}")

        # Kiểm tra status_code
        if not (200 <= response.status_code < 300):
            logger.error(f"GraphQL API returned non-200 status: {response.status_code}")
            try:
                error_details = response.json()
            except requests.exceptions.JSONDecodeError:
                error_details = response.text
            
            # Vẫn return store từ state nếu API error
            return {
               "success": True,
               "current_store": current_store,
               "source": "state",
               "api_error": f"API request failed with status {response.status_code}",
               "api_full_response": error_details
            }

        api_data = response.json()
        
        if "errors" in api_data:
            # Log error chi tiết
            error_msg = api_data.get("errors", [{}])[0].get("message", "Unknown error")
            print(f"API error: {error_msg}")
            logger.error(f"GraphQL error: {api_data.get('errors')}")
            # Vẫn return store từ state nếu API error
            return {
                "success": True,
                "current_store": current_store,
                "source": "state",
                "api_error": error_msg,
                "api_full_response": api_data
            }
        
        store_list = api_data.get("data", {}).get("storeList", [])
        
        # Debug: Print all store codes from API
        print(f"Store codes from API: {[str(s.get('code')) for s in store_list]}")
        
        # So sánh store_id từ state với API
        # Extract số từ store_id (e.g., "b2c_10013_vi" -> "10013")
        # FIXED: Use pattern to match digits after "b2c_" prefix
        store_id_match = re.search(r'b2c_(\d+)', str(store_id))
        if not store_id_match:
            # Fallback: try to find any sequence of 5+ digits
            store_id_match = re.search(r'(\d{5,})', str(store_id))
        store_id_number = store_id_match.group(1) if store_id_match else str(store_id)
        
        print(f"Extracted store_id_number: {store_id_number} (type: {type(store_id_number)})")
        
        matching_store = None
        for store in store_list:
            # Convert to string and strip whitespace for comparison
            store_code = str(store.get("code", "")).strip()
            
            print(f"Comparing: '{store_code}' == '{store_id_number}'")
            
            # Compare as strings with explicit conversion
            if store_code == str(store_id_number).strip():
                matching_store = {
                    "code": store.get("code"),
                    "name": store.get("name"),
                    "is_active": store.get("is_active", True),  # Default to True if not present
                    "store_id_from_state": store_id,
                    "base_url": api_base_url,
                    "magento_cart_id": current_store.get("magento_cart_id")
                }
                break
        
        if matching_store:
            print(f"Store matched in API: {matching_store['name']}")
            return {
                "success": True,
                "current_store": matching_store,
                "source": "state + API"
            }
        else:
            # Nếu không match, return store từ state với debug info
            print(f"Store ID '{store_id}' (extracted: '{store_id_number}') not found in API")
            print(f"Available store codes: {[str(s.get('code')) for s in store_list]}")
            return {
                "success": True,
                "current_store": current_store,
                "source": "state",
                "warning": f"Store ID '{store_id}' not found in API store list",
                "debug_info": {
                    "extracted_number": store_id_number,
                    "available_codes": [str(s.get("code")) for s in store_list]
                }
            }
    
    except Exception as e:
        logger.error(f"Error in get_current_store: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        # Trả về store từ state nếu có lỗi không mong muốn
        if 'state_result' in locals() and state_result.get("success"):
            return {
                "success": True,
                "current_store": state_result.get("current_store"),
                "source": "state",
                "error": f"Unexpected error during API check: {str(e)}"
            }
        
        return {
            "success": False,
            "error": f"Unexpected error: {str(e)}"
        }


def update_store_in_state(
    tool_context: ToolContext,
    store_code: str,
    base_url: str = DEFAULT_MMVN_STORE_URL
) -> Dict[str, Any]:
    """
    Cập nhật store_id trong state bằng cách tìm kiếm từ API.
    
    Args:
        tool_context (ToolContext): The context in which the tool operates.
        store_code (str): Store code to search for (e.g., '10013')
        base_url (str): Base URL for GraphQL API
    
    Returns:
        Dict[str, Any]: Updated store info
    """
    print(
        f"{'-' * 80}\n"
        f"TOOL CALLED: update_store_in_state\n"
        f"store_code: {store_code}\n"
        f"{'-' * 80}\n"
    )
    
    try:
        query = """
        query {
            storeList {
                code
                name
                is_active
            }
        }
        """
        
        response = make_graphql_request(query, {}, base_url=base_url)

        # Kiểm tra status_code
        if not (200 <= response.status_code < 300):
            logger.error(f"GraphQL API returned non-200 status: {response.status_code}")
            try:
                error_details = response.json()
            except requests.exceptions.JSONDecodeError:
                error_details = response.text
            return {
                "success": False,
                "error": f"API request failed with status {response.status_code}",
                "details": error_details
            }
            
        api_data = response.json()
        
        if "errors" in api_data:
            return {
                "success": False,
                "error": "Failed to fetch store list from API",
                "details": api_data["errors"]
            }
        
        store_list = api_data.get("data", {}).get("storeList", [])
        
        # Tìm store theo code
        target_store = None
        for store in store_list:
            if str(store.get("code", "")).strip() == str(store_code).strip():
                target_store = store
                break
        
        if not target_store:
            return {
                "success": False,
                "error": f"Store with code '{store_code}' not found",
                "available_stores": [
                    {
                        "code": s.get("code"),
                        "name": s.get("name")
                    } for s in store_list
                ]
            }
        
        # Format store data cho state update (với format b2c_XXXXX_vi)
        store_code_formatted = f"b2c_{target_store.get('code')}_vi"
        new_store_data = {
            "store_id": store_code_formatted,
            "base_url": base_url
        }
        
        return {
            "success": True,
            "message": "Store found - update state with this data:",
            "store_data": new_store_data,
            "store_info": {
                "code": target_store.get("code"),
                "name": target_store.get("name"),
                "is_active": target_store.get("is_active")
            }
        }
    
    except Exception as e:
        logger.error(f"Error in update_store_in_state: {str(e)}")
        return {
            "success": False,
            "error": f"Unexpected error: {str(e)}"
        }


def get_all_stores_list(
    tool_context: ToolContext,
    base_url: str = DEFAULT_MMVN_STORE_URL
) -> Dict[str, Any]:
    """
    Lấy danh sách tất cả stores từ API.
    
    Args:
        tool_context (ToolContext): The context in which the tool operates.
        base_url (str): Base URL for GraphQL API
    
    Returns:
        Dict[str, Any]: List of all stores
    """
    print(
        f"{'-' * 80}\n"
        f"TOOL CALLED: get_all_stores_list\n"
        f"{'-' * 80}\n"
    )
    
    try:
        query = """
        query {
            storeList {
                code
                name
            }
        }
        """
        
        response = make_graphql_request(query, {}, base_url=base_url)
        
        # Kiểm tra status_code
        if not (200 <= response.status_code < 300):
            logger.error(f"GraphQL API returned non-200 status: {response.status_code}")
            try:
                error_details = response.json()
            except requests.exceptions.JSONDecodeError:
                error_details = response.text
            return {
                "success": False,
                "error": f"API request failed with status {response.status_code}",
                "details": error_details
            }

        api_data = response.json()
        
        if "errors" in api_data:
            return {
                "success": False,
                "error": "Failed to fetch store list",
                "details": api_data["errors"]
            }
        
        store_list = api_data.get("data", {}).get("storeList", [])
        
        return {
            "success": True,
            "stores": store_list,
            "total_stores": len(store_list)
        }
    
    except Exception as e:
        logger.error(f"Error in get_all_stores_list: {str(e)}")
        return {
            "success": False,
            "error": f"Unexpected error: {str(e)}"
        }