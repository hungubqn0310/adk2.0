import json
import logging
import traceback
from typing import Optional, Any
from google.adk.tools import ToolContext

from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.tools.utils import make_graphql_request_async

logger = logging.getLogger(__name__)

async def get_customer_addresses(tool_context: Optional[ToolContext] = None) -> dict[str, Any]:
    """
    Get delivery addresses of the USER.

    This tool retrieves the user's addresses from two possible sources:
    1. For logged-in users: Fetches shipping addresses from cart via GraphQL
    2. For guest users: Retrieves address from magento_session_data (M2_VENIA_BROWSER_PERSISTENCE__customer_address)

    The addresses belong to the USER - you are viewing them on their behalf.

    Args:
        tool_context: Tool execution context (automatically provided)

    Returns:
        dict with:
            - success (bool): Whether the operation succeeded
            - data (dict): Address information
                For logged-in users:
                    - email (str): Customer email
                    - shipping_addresses (list): List of shipping address objects
                    - total_addresses (int): Number of addresses
                    - source (str): "backend" 
                For guest users:
                    - address (dict): Guest address with:
                        - address: Street address
                        - city_code: City code
                        - ward_code: Ward code
                        - address_details: Full formatted address
                    - source (str): "session"
            - message (str): Error message if failed
            - instruction_for_agent (str): Guidance for the agent on how to respond to the user

    Important:
        - Always refer to them as "your address" or "the user's address", NOT "my address"
        - For logged-in users, can have multiple shipping addresses
        - For guest users, only one temporary address from session
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                'instruction_for_agent': "Inform the user 'Hiện em không truy cập được thông tin địa chỉ, anh/chị thử lại sau ít phút nhé.'. "
                                         "If the user asks again, retry this tool.",
                "code": "MISSING_TOOL_CONTEXT"
            }

        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        signin_token = magento_session_data.get("signin_token") or ""
        signin_token = signin_token.strip('"')
        magento_cart_id = magento_session_data.get("magento_cart_id") or ""
        magento_cart_id = magento_cart_id.strip('"')

        # Case 1: User is logged in - fetch from backend using cart query
        if signin_token and magento_cart_id:
            graphql_query = """
                query GetShippingInformation($cartId: String!) {
                    cart(cart_id: $cartId) {
                        id
                        ...ShippingInformationFragment
                        __typename
                    }
                }
                
                fragment ShippingInformationFragment on Cart {
                    id
                    email
                    shipping_addresses {
                        customer_address_id
                        is_new_administrative
                        city
                        country {
                            code
                            label
                            __typename
                        }
                        firstname
                        street
                        telephone
                        city_code
                        district
                        district_code
                        ward
                        ward_code
                        __typename
                    }
                    delivery_date {
                        date
                        time_interval_id
                        comment
                        from
                        to
                        __typename
                    }
                    vat_address {
                        customer_vat_id
                        company_name
                        company_vat_number
                        company_address
                        __typename
                    }
                    __typename
                }
            """

            variables = {"cartId": magento_cart_id}

            try:
                res = await make_graphql_request_async(
                    graphql_query,
                    variables,
                    base_url,
                    store_id,
                    auth_token=signin_token,
                )

                if not res.get("data"):
                    logger.error(f"Cannot get cart shipping addresses:\n{json.dumps(res, indent=4)}")
                    tool_context.state['last_address_error_response'] = {'error': 'Cannot get addresses', 'response': res}
                    return {
                        "success": False,
                        "message": "API error: Backend error invalid data format",
                        "instruction_for_agent": "Inform the user 'Hiện em không truy cập được thông tin địa chỉ, anh/chị thử lại sau ít phút nhé.'.",
                        "code": "INVALID_RESPONSE"
                    }

                cart_data = res.get("data", {}).get("cart")
                if cart_data is None:
                    logger.error(f"Empty cart data:\n{json.dumps(res, indent=4)}")
                    tool_context.state['last_address_error_response'] = {'error': 'Empty cart', 'response': res}
                    return {
                        "success": False,
                        "message": "Cannot get cart shipping addresses.",
                        "instruction_for_agent": "Inform the user 'Hiện em không truy cập được thông tin địa chỉ, anh/chị thử lại sau ít phút nhé.'.",
                        "code": "INVALID_RESPONSE: EMPTY_CART_DATA"
                    }

                shipping_addresses = cart_data.get("shipping_addresses", [])
                email = cart_data.get("email")
                delivery_date = cart_data.get("delivery_date")
                vat_address = cart_data.get("vat_address")
                
                # Process shipping addresses data for better readability
                processed_addresses = []
                for addr in shipping_addresses:
                    country = addr.get("country") or {}
                    
                    # Format full address details
                    street_parts = addr.get("street", [])
                    street_text = ", ".join(street_parts) if isinstance(street_parts, list) else str(street_parts)
                    
                    address_parts = [street_text]
                    if addr.get("ward"):
                        address_parts.append(addr.get("ward"))
                    if addr.get("district"):
                        address_parts.append(addr.get("district"))
                    if addr.get("city"):
                        address_parts.append(addr.get("city"))
                    
                    full_address = ", ".join(filter(None, address_parts))
                    
                    processed_addr = {
                        "customer_address_id": addr.get("customer_address_id"),
                        "firstname": addr.get("firstname"),
                        "telephone": addr.get("telephone"),
                        "street": street_parts,
                        "city": addr.get("city"),
                        "city_code": addr.get("city_code"),
                        "district": addr.get("district"),
                        "district_code": addr.get("district_code"),
                        "ward": addr.get("ward"),
                        "ward_code": addr.get("ward_code"),
                        "country_code": country.get("code"),
                        "country_label": country.get("label"),
                        "is_new_administrative": addr.get("is_new_administrative"),
                        "full_address": full_address
                    }
                    processed_addresses.append(processed_addr)

                # Clean delivery_date if all fields are null
                cleaned_delivery_date = None
                if delivery_date and any(delivery_date.get(k) for k in ['date', 'time_interval_id', 'comment', 'from', 'to']):
                    cleaned_delivery_date = {
                        "date": delivery_date.get("date"),
                        "time_interval_id": delivery_date.get("time_interval_id"),
                        "comment": delivery_date.get("comment"),
                        "from": delivery_date.get("from"),
                        "to": delivery_date.get("to")
                    }

                # Clean vat_address if all fields are null
                cleaned_vat_address = None
                if vat_address and any(vat_address.get(k) for k in ['customer_vat_id', 'company_name', 'company_vat_number', 'company_address']):
                    cleaned_vat_address = {
                        "customer_vat_id": vat_address.get("customer_vat_id"),
                        "company_name": vat_address.get("company_name"),
                        "company_vat_number": vat_address.get("company_vat_number"),
                        "company_address": vat_address.get("company_address")
                    }

                result: dict[str, Any] = {
                    "success": True,
                    "data": {
                        "email": email,
                        "shipping_addresses": processed_addresses,
                        "total_addresses": len(processed_addresses),
                        "source": "backend"
                    }
                }
                
                # Only include delivery_date and vat_address if they have data
                if cleaned_delivery_date:
                    result["data"]["delivery_date"] = cleaned_delivery_date
                if cleaned_vat_address:
                    result["data"]["vat_address"] = cleaned_vat_address
                
                if not processed_addresses:
                    result['instruction_for_agent'] = "Inform the user that they don't have any shipping addresses in their cart yet. " \
                                                       "Suggest them to add a delivery address at checkout."
                
                return result

            except Exception as e:
                logger.error(traceback.format_exc())
                return {
                    "success": False,
                    "message": f"HTTP error: {str(e)}",
                    "instruction_for_agent": "Inform the user 'Hiện em không truy cập được thông tin địa chỉ, anh/chị thử lại sau ít phút nhé.'.",
                    "code": "HTTP_ERROR"
                }

        # Case 2: Guest user - get address from magento_session_data
        else:
            customer_address_key = "M2_VENIA_BROWSER_PERSISTENCE__customer_address"
            customer_address_data = magento_session_data.get(customer_address_key)
            
            if not customer_address_data:
                return {
                    "success": False,
                    "message": "No address found in session",
                    "instruction_for_agent": "Inform the user that they don't have any address saved yet. "
                                             "They can add a delivery address at checkout.",
                    "code": "NO_GUEST_ADDRESS"
                }
            
            # Parse the address value
            try:
                # The value might be a dict with 'value' and 'timeStored'
                if isinstance(customer_address_data, dict) and 'value' in customer_address_data:
                    address_value = customer_address_data['value']
                else:
                    address_value = customer_address_data
                
                # Parse the JSON string if needed
                if isinstance(address_value, str):
                    address_obj = json.loads(address_value)
                else:
                    address_obj = address_value
                
                return {
                    "success": True,
                    "data": {
                        "address": {
                            "address": address_obj.get("address"),
                            "city_code": address_obj.get("city_code"),
                            "ward_code": address_obj.get("ward_code"),
                            "address_details": address_obj.get("address_details")
                        },
                        "source": "session"
                    },
                    "instruction_for_agent": "Present the user's temporary address from their current session. "
                                             "Remind them that logging in will allow them to save multiple addresses."
                }
                
            except (json.JSONDecodeError, AttributeError, KeyError) as e:
                logger.error(f"Error parsing address: {str(e)}\n{traceback.format_exc()}")
                return {
                    "success": False,
                    "message": f"Error parsing address data: {str(e)}",
                    "instruction_for_agent": "Inform the user 'Hiện em không đọc được thông tin địa chỉ, anh/chị vui lòng kiểm tra lại.'.",
                    "code": "PARSE_ERROR"
                }

    except Exception as e:
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "instruction_for_agent": "Inform the user 'Hiện em không truy cập được thông tin địa chỉ, anh/chị thử lại sau ít phút nhé.'.",
            "code": "UNEXPECTED_ERROR",
        }