import json
import logging
import os
import unicodedata
from typing import Optional

import google.genai as genai
import requests
from google.adk.tools import ToolContext
from google.genai.types import HttpOptions
from thefuzz import process, fuzz
from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.tools.utils import make_graphql_request

_GEMINI_BASE_URL = os.getenv("GOOGLE_GEMINI_BASE_URL")

logger = logging.getLogger("google_adk." + __name__)



def _get_magento_config(tool_context: ToolContext = None) -> tuple[str, str]:
    """
    Get base_url and store_id from magento_session_data in tool_context.

    Args:
        tool_context (ToolContext): The context containing session data

    Returns:
        tuple[str, str]: (base_url, store_id)
    """
    if tool_context is None:
        return DEFAULT_MMVN_STORE_URL, DEFAULT_MMVN_STORE_ID

    magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})

    base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
    store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID )

    return base_url, store_id


class NoSuggestedLocationError(Exception):
    """
    Exception raised when no suggested location is found for a given address.
    This is used to indicate that the address provided does not match any known locations, or if no nearby stores are found.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def __str__(self):
        return f"NoSuggestedLocationError: {self.message}"


class NoStoreNearLocationError(Exception):
    """
    Exception raised when no store is found near the given location.
    This exception indicates that the system could not identify any nearby stores
    based on the provided address or coordinates.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def __str__(self):
        return f"NoStoreNearLocationError: {self.message}"


class MagentoAPIError(Exception):
    """
    Exception raised when there is an error with the Magento API.
    This can be used to indicate issues such as invalid responses or errors in the request.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def __str__(self):
        return f"MagentoAPIError: {self.message}"


def create_customer_address_form(
        tool_context: ToolContext,
        city: Optional[str] = None,
        district: Optional[str] = None,
        street: Optional[str] = None,
        instructions: Optional[str] = None,
) -> str:
    """
    Create a form for the customer to fill out their address.

    Args:
        city (str): The default city for the address. Can be an empty string.
        district (str): The default district for the address. Can be an empty string.
        street (str): The default street for the address. Can be an empty string.
        tool_context (ToolContext): The context in which the tool operates.
        instructions (str): Instructions for processing the form. Can be an empty string.

    Returns:
        str: A JSON string containing the dictionary for the form.
    """
    print(
        f"{'-' * 80}\nTOOL CALLED: create_customer_address_form\ncity: {city}, district: {district}, street: {street}\n{'-' * 80}\n")
    form_request = {
        'city': '<city>' if not city else city,
        'district': '<district>' if not district else district,
        'street': '<street>' if not street else street,
    }

    tool_context.actions.skip_summarization = True
    tool_context.actions.escalate = True

    form_dict = {
        'type': 'form',
        'form': {
            'type': 'object',
            'properties': {
                'city': {
                    'type': 'string',
                    'description': 'City of the address',
                    'title': 'City',
                },
                'district': {
                    'type': 'string',
                    'description': 'District of the address',
                    'title': 'District',
                },
                'street': {
                    'type': 'string',
                    'description': 'Street of the address',
                    'title': 'Street',
                }
            },
            'required': list(form_request.keys()),
        },
        'form_data': form_request,
        'instructions': instructions,
    }
    return json.dumps(form_dict)


def _get_cities(tool_context: ToolContext = None) -> list[dict]:
    """Fetch the canonical list of Vietnamese cities/provinces from GraphQL."""
    base_url, _ = _get_magento_config(tool_context)
    query = """
        query{
            cities(country_id: "VN") { id name city_code }
        }
        """
    response = make_graphql_request(query, {}, base_url=base_url).json()
    if 'data' not in response or 'cities' not in response['data']:
        raise MagentoAPIError("Invalid response from the server. 'cities' not found in response data.")
    return response['data']['cities']


def _get_districts(city_code: str, tool_context: ToolContext = None) -> list[dict]:
    """Fetch the canonical list of districts for a city from GraphQL."""
    base_url, _ = _get_magento_config(tool_context)
    query = """
    query GetDistrict($city_code: String!){
        districts(city_code:$city_code){ id name district_code }
    }
    """
    response = make_graphql_request(query, {"city_code": city_code}, base_url=base_url).json()
    if 'data' not in response or 'districts' not in response['data']:
        raise MagentoAPIError("Invalid response from the server. 'districts' not found in response data.")
    return response['data']['districts']


def _get_wards(city_code: str, tool_context: ToolContext = None) -> list[dict]:
    """
    Fetch the canonical list of wards for a city from GraphQL.

    Uses city_code (not district_code) because wards(city_code) returns the correct
    ward_code format expected by the storeView API.
    """
    base_url, _ = _get_magento_config(tool_context)
    query = """
    query GetWard($city_code: String!){
        wards(city_code:$city_code){ id name ward_code }
    }
    """
    response = make_graphql_request(query, {"city_code": city_code}, base_url=base_url).json()
    if 'data' not in response or 'wards' not in response['data']:
        raise MagentoAPIError("Invalid response from the server. 'wards' not found in response data.")
    return response['data']['wards']


def _match_name(
        query: str,
        items: list[dict],
        name_key: str,
        code_key: str,
        unit_label: str,
        score_cutoff: int = 50,
) -> tuple[str, str]:
    """
    Match a single user-supplied name against a list of admin units (exact first,
    then fuzzy). Raises NoSuggestedLocationError when nothing matches well enough.
    """
    names = [it[name_key] for it in items]
    q = query.lower().strip()
    for it in items:
        if it[name_key].lower().strip() == q:
            return it[name_key], it[code_key]

    result = process.extractOne(query, names)
    if result is None:
        raise NoSuggestedLocationError(f"{unit_label} '{query}' not found. No options available to match against.")
    closest, score = result
    if score < score_cutoff:
        raise NoSuggestedLocationError(f"{unit_label} '{query}' not found. Did you mean '{closest}'?")
    code = next((it[code_key] for it in items if it[name_key].lower() == closest.lower()), None)
    return closest, code


def _fuzzy_search_city(city: str, tool_context: ToolContext = None):
    """Given a city name, get the closest matching city name and city code."""
    return _match_name(city, _get_cities(tool_context), 'name', 'city_code', 'City')


def _fuzzy_search_district(city_code: str, district: str, tool_context: ToolContext = None):
    """Given a city code and district name, get the closest matching district name and code."""
    return _match_name(district, _get_districts(city_code, tool_context), 'name', 'district_code', 'District')


def _fuzzy_search_ward(city_code: str, ward: str, tool_context: ToolContext = None):
    """Given a city code and ward name, get the closest matching ward name and code."""
    return _match_name(ward, _get_wards(city_code, tool_context), 'name', 'ward_code', 'Ward')


def _norm(s: str) -> str:
    """
    Normalize Vietnamese text for tolerant matching: lowercase, strip diacritics
    (tone + vowel marks) and fold 'đ' → 'd'. e.g. 'Ô Chợ Dừa' -> 'o cho dua'.
    """
    if not s:
        return ""
    s = s.lower().strip()
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return s.replace('đ', 'd')


def _best_match_in_address(
        address: str,
        items: list[dict],
        name_key: str,
        code_key: str,
        score_cutoff: int = 90,
        min_fuzzy_len: int = 6,
) -> tuple[Optional[str], Optional[str]]:
    """
    Find the admin unit (district/ward) whose name appears in a free-form address,
    WITHOUT requiring an explicit 'phường/quận/...' prefix. Accent-insensitive.

    Strategy:
      1. Exact normalized substring of the address → strongest signal (handles
         'ô chợ dừa' inside '170 đê la thành ô chợ dừa hà nội').
      2. Otherwise fuzzy partial_ratio, but only for names long enough to make a
         spurious short-name hit unlikely.

    On ties, prefer the longer (more specific) name. Returns (name, code) or
    (None, None) when nothing is confident enough.
    """
    addr = _norm(address)
    if not addr:
        return None, None

    best_item, best_score, best_len = None, 0, 0
    for it in items:
        name_norm = _norm(it[name_key])
        if not name_norm:
            continue
        if name_norm in addr:
            score = 100
        elif len(name_norm) >= min_fuzzy_len:
            score = fuzz.partial_ratio(name_norm, addr)
        else:
            continue
        if score > best_score or (score == best_score and len(name_norm) > best_len):
            best_item, best_score, best_len = it, score, len(name_norm)

    if best_item and best_score >= score_cutoff:
        return best_item[name_key], best_item[code_key]
    return None, None


def _resolve_freeform_address(
        address: str,
        tool_context: ToolContext = None,
) -> tuple[str, str, str]:
    """
    Resolve a free-form Vietnamese address into (city_name, city_code, ward_code)
    using an LLM to extract city/ward from the canonical admin-unit lists.

    Two-tier only (post-2025 reform): Tỉnh/Thành phố → Phường/Xã.
    Raises NoSuggestedLocationError if city or ward cannot be determined.
    """
    cities = _get_cities(tool_context)
    city_names = [c['name'] for c in cities]

    client = genai.Client(
        http_options=HttpOptions(base_url=_GEMINI_BASE_URL) if _GEMINI_BASE_URL else None,
    )

    # Step 1: detect city
    city_prompt = (
        f"Từ địa chỉ sau: \"{address}\"\n"
        f"Hãy xác định tỉnh/thành phố phù hợp nhất từ danh sách dưới đây.\n"
        f"Danh sách: {json.dumps(city_names, ensure_ascii=False)}\n"
        f"Chỉ trả về ĐÚNG tên trong danh sách, không thêm gì khác. "
        f"Nếu không xác định được, trả về \"UNKNOWN\"."
    )
    city_resp = client.models.generate_content(
        model="gemini-3.1-flash-lite-preview",
        contents=city_prompt,
    )
    city_name = city_resp.text.strip().strip('"').strip("'")
    city_item = next((c for c in cities if c['name'] == city_name), None)
    if not city_item:
        raise NoSuggestedLocationError(f"Cannot detect city from address: {address}")
    city_code = city_item['city_code']

    # Step 2: detect ward using the city's ward list
    wards = _get_wards(city_code, tool_context)
    ward_names = [w['name'] for w in wards]

    ward_prompt = (
        f"Từ địa chỉ sau: \"{address}\"\n"
        f"Tỉnh/thành phố là: {city_name}\n"
        f"Hãy xác định phường/xã phù hợp nhất từ danh sách dưới đây.\n"
        f"Danh sách: {json.dumps(ward_names, ensure_ascii=False)}\n"
        f"Chỉ trả về ĐÚNG tên trong danh sách, không thêm gì khác. "
        f"Nếu không xác định được, trả về \"UNKNOWN\"."
    )
    ward_resp = client.models.generate_content(
        model="gemini-3.1-flash-lite-preview",
        contents=ward_prompt,
    )
    ward_name = ward_resp.text.strip().strip('"').strip("'")
    ward_item = next((w for w in wards if w['name'] == ward_name), None)
    if not ward_item:
        raise NoSuggestedLocationError(f"Cannot detect ward from address: {address}")
    ward_code = ward_item['ward_code']

    logger.info(f"AI resolved '{address}' -> city='{city_name}', ward='{ward_name}'")
    return city_name, city_code, ward_code


def _build_store_success_result(stores_data: dict, region_name: str) -> dict:
    """Build the standard success payload (formatted stores + delivery policy)."""
    stores_text = []
    for idx, store in enumerate(stores_data['stores'], 1):
        stores_text.append(
            f"{idx}. **{store['name']}**\n"
            f"   - Địa chỉ: {store['address']}\n"
            f"   - Khoảng cách: {store['distance_text']}"
        )
    nearest_store_name = stores_data['stores'][0]['name'] if stores_data['stores'] else None
    delivery_policy = _get_delivery_policy_info(nearest_store_name)
    return {
        "success": True,
        "stores": stores_data['stores'],
        "stores_count": len(stores_data['stores']),
        "nearest_store": stores_data['stores'][0] if stores_data['stores'] else None,
        "stores_formatted": "\n\n".join(stores_text),
        "allow_selection": stores_data['allow_selection'],
        "message": stores_data['message'],
        "region": region_name,
        "store_locator_link": None,
        "instruction_for_agent": None,
        "delivery_policy": delivery_policy,
    }


def get_suggest_location(address: str, tool_context: ToolContext = None) -> list[dict[str, str]]:
    """
    Extract detailed address information from a given address string.

    Args:
        address (str): The full address string to be processed.
        tool_context (ToolContext): The context containing session data.

    Returns:
        dict[str, str]: A dictionary containing the extracted city, district, and ward.
    """
    base_url, _ = _get_magento_config(tool_context)

    query = """
    query GetSuggestedLocation($address: String!){
        suggestLocation(address: $address) {
            address
            city
            city_code
            district
            district_code
            ward
            ward_code
        }
    }
    """
    variables = {"address": address}
    suggest_location_response = make_graphql_request(query, variables, base_url=base_url)
    suggest_location_data = suggest_location_response.json()
    if 'data' not in suggest_location_data or 'suggestLocation' not in suggest_location_data['data']:
        # Raise the domain error so callers fall back to canonical admin-unit
        # matching instead of crashing on a malformed geocoder response.
        raise NoSuggestedLocationError(
            "Invalid response from the server. 'suggestLocation' not found in response data."
        )
    suggest_location = suggest_location_data['data']['suggestLocation']
    if not suggest_location:
        raise NoSuggestedLocationError(f"No suggested location found for address: {address}")
    return suggest_location


def get_nearest_store_from_city_district_ward(
        city: str,
        district: str,
        ward: str,
        tool_context: ToolContext = None,
) -> dict:
    """
    Get nearby stores based on the customer's city, district, and ward.
    Args:
        tool_context (ToolContext): The context in which the tool operates.
        city (str): The city name of the customer's address.
        district (str): The district name of the customer's address.
        ward (str): The ward name of the customer's address.
    Returns:
        dict: A dictionary containing success status and list of nearby stores with distance info.
    """
    # fuzzy search for city, district, and ward
    print(
        f"{'-' * 80}\n"
        f"TOOL CALLED: get_nearest_store_from_city_district_ward\n"
        f"city: {city}, district: {district}, ward: {ward}\n"
        f"{'-' * 80}\n"
    )
    if not city or not ward:
        return {
            "success": False,
            "message": "City and ward must be provided to find the nearest store.",
            "instruction_for_agent": (
                "Thiếu thông tin địa chỉ. Hãy hỏi khách cung cấp "
                "tỉnh/thành phố và phường/xã."
            ),
        }

    try:
        city_name, city_code = _fuzzy_search_city(city, tool_context)
        district_name, district_code = _fuzzy_search_district(city_code, district, tool_context)
        ward_name, ward_code = _fuzzy_search_ward(city_code, ward, tool_context)
    except (MagentoAPIError, NoSuggestedLocationError) as e:
        return {"success": False, "message": e.message}

    # Get nearby stores using the city, district, and ward codes
    try:
        stores_data = _get_nearest_store(city_code, district_code, ward_code, tool_context=tool_context)

        # Format stores information
        stores_text = []
        for idx, store in enumerate(stores_data['stores'], 1):
            store_text = (
                f"{idx}. **{store['name']}**\n"
                f"   - Địa chỉ: {store['address']}\n"
                f"   - Khoảng cách: {store['distance_text']}"
            )
            stores_text.append(store_text)

        return {
            "success": True,
            "stores": stores_data['stores'],
            "stores_count": len(stores_data['stores']),
            "nearest_store": stores_data['stores'][0] if stores_data['stores'] else None,
            "stores_formatted": "\n\n".join(stores_text),
            "allow_selection": stores_data['allow_selection'],
            "message": stores_data['message'],
        }
    except (MagentoAPIError, NoSuggestedLocationError) as e:
        return {"success": False, "message": e.message}


def _detect_region_from_city(city_name: str) -> tuple[str, int]:
    """
    Detect region (North/Central/South) from city name.
    Returns: (region_name, source_code)
    """
    # Define cities by region
    northern_cities = [
        "hà nội", "hải phòng", "quảng ninh", "bắc ninh", "hải dương", 
        "hưng yên", "thái bình", "nam định", "ninh bình", "hà nam",
        "vĩnh phúc", "phú thọ", "bắc giang", "lạng sơn", "thái nguyên",
        "yên bái", "tuyên quang", "lào cai", "điện biên", "lai châu",
        "sơn la", "hòa bình", "cao bằng", "bắc kạn", "hà giang"
    ]
    
    central_cities = [
        "thanh hóa", "nghệ an", "hà tĩnh", "quảng bình", "quảng trị",
        "thừa thiên huế", "huế", "đà nẵng", "quảng nam", "quảng ngãi",
        "bình định", "phú yên", "khánh hòa", "ninh thuận", "bình thuận",
        "kon tum", "gia lai", "đắk lắk", "đắk nông", "lâm đồng"
    ]
    
    southern_cities = [
        "hồ chí minh", "sài gòn", "tp hồ chí minh", "tp.hcm", "hcm",
        "bình dương", "đồng nai", "bà rịa vũng tàu", "vũng tàu",
        "long an", "tiền giang", "bến tre", "trà vinh", "vĩnh long",
        "đồng tháp", "an giang", "kiên giang", "cần thơ", "hậu giang",
        "sóc trăng", "bạc liêu", "cà mau", "tây ninh", "bình phước"
    ]
    
    city_lower = city_name.lower().strip()
    
    if any(city in city_lower for city in northern_cities):
        return ("Miền Bắc", 1)
    elif any(city in city_lower for city in central_cities):
        return ("Miền Trung", 2)
    elif any(city in city_lower for city in southern_cities):
        return ("Miền Nam", 3)
    else:
        return ("Unknown", 0)


def _get_delivery_policy_info(store_name: str = None) -> dict:
    """
    Get delivery policy information based on store name.

    Args:
        store_name (str): Name of the store to get specific policy

    Returns:
        dict: Dictionary containing delivery policy information
    """
    # Check if store is Hung Phu or Thanh Xuan (special pricing)
    special_stores = ["hưng phú", "thanh xuân"]
    is_special_store = False

    if store_name:
        store_lower = store_name.lower()
        is_special_store = any(special in store_lower for special in special_stores)

    if is_special_store:
        return {
            "min_order_free_delivery": 300000,
            "free_delivery_radius_km": 7,
            "base_delivery_fee": 30000,
            "extra_km_fee": 6000,
            "max_delivery_radius_km": 15,
            "formatted_text": (
                "**Chính sách giao hàng tại trung tâm này:**\n"
                "- Miễn phí giao hàng cho đơn hàng từ 300.000₫ trong phạm vi 7km\n"
                "- Trên 7km: 6.000₫/km (tối đa 15km)\n"
                "- Đơn hàng dưới 300.000₫: 30.000₫ cho 7km đầu + 6.000₫/km tiếp theo (tối đa 15km)\n"
                "- Đơn hàng Kem & Bánh đông lạnh: Tối thiểu 300.000₫, thanh toán trước, chỉ giao trong 7km\n"
                "- Hàng nặng/cồng kềnh (>0.34m³ hoặc >90kg): Phụ thu 140.000₫ cho khoảng cách 7-10km"
            )
        }
    else:
        return {
            "min_order_free_delivery": 600000,
            "free_delivery_radius_km": 7,
            "base_delivery_fee": 30000,
            "extra_km_fee": 5000,
            "max_delivery_radius_km": 15,
            "formatted_text": (
                "**Chính sách giao hàng:**\n"
                "- Miễn phí giao hàng cho đơn hàng từ 600.000₫ trong phạm vi 7km\n"
                "- Trên 7km: 5.000₫/km (tối đa 15km)\n"
                "- Đơn hàng dưới 600.000₫: 30.000₫ cho 7km đầu + 5.000₫/km tiếp theo (tối đa 15km)\n"
                "- Đơn hàng Kem & Bánh đông lạnh: Tối thiểu 600.000₫, thanh toán trước, chỉ giao trong 7km\n"
                "- Hàng nặng/cồng kềnh (>0.34m³ hoặc >90kg): Phụ thu 140.000₫ cho khoảng cách 7-10km\n\n"
                "**Lưu ý:**\n"
                "- Khách hàng nhận hàng tại cổng/sảnh/khu vực nhận hàng của tòa nhà\n"
                "- Khách hàng quận 7 đặt tại MM An Phú: Phụ thu thêm 12.000₫\n"
                "- Đơn hàng trên 20 triệu cần chuyển khoản trước"
            )
        }


def get_nearest_store_from_address(
        address: str,
        tool_context: ToolContext,
) -> dict:
    """
    Get the nearest store based on the customer's address.
    Args:
        tool_context (ToolContext): The context in which the tool operates.
        address (str): The full address of the customer, including street, city, district(optional), ward and other details.
    Returns:
        dict: A dictionary containing success status, nearest store info, delivery policy, or instructions for the agent.
    """
    print(
        f"{'-' * 80}\n"
        f"TOOL CALLED: get_nearest_store_from_address\n"
        f"customer_address: {address}\n"
        f"{'-' * 80}\n"
    )

    try:
        if not address:
            return {
                "success": False,
                "message": "Address must be provided to find the nearest store.",
                "instruction_for_agent": (
                    "Khách chưa cung cấp địa chỉ. Hãy hỏi khách địa chỉ cụ thể "
                    "(tỉnh/thành phố và phường/xã) để tìm cửa hàng gần nhất."
                ),
            }

        # get city, district, and ward codes from the full address
        suggest_location = get_suggest_location(address, tool_context)
        best_address = next((loc for loc in suggest_location if
                             loc.get('city_code') and loc.get('ward_code')
                             ), None)
        if not best_address:
            raise NoSuggestedLocationError(f"No suggested location found for address: {address}")

        city_code = best_address.get('city_code')
        district_code = best_address.get('district_code')
        ward_code = best_address.get('ward_code')
        city_name = best_address.get('city', '')

        stores_data = _get_nearest_store(city_code, district_code, ward_code, address, tool_context)

        region_name, _ = _detect_region_from_city(city_name if city_name else address)
        return _build_store_success_result(stores_data, region_name)

    except NoSuggestedLocationError:
        # Fallback: suggestLocation failed — use AI to detect city/ward directly.
        try:
            city_name_fb, city_code_fb, ward_code_fb = _resolve_freeform_address(address, tool_context)
        except (NoSuggestedLocationError, MagentoAPIError, ValueError):
            city_name_fb = city_code_fb = ward_code_fb = None

        if city_code_fb and ward_code_fb:
            # AI found city+ward — call storeView and surface the real API message.
            try:
                stores_data = _get_nearest_store(city_code_fb, None, ward_code_fb, address, tool_context)
                region_name, _ = _detect_region_from_city(city_name_fb)
                return _build_store_success_result(stores_data, region_name)
            except NoStoreNearLocationError as store_err:
                # storeView returned a specific message (e.g. "Khu vực này MM không hỗ trợ giao hàng")
                region_name, source_code = _detect_region_from_city(city_name_fb)
                if source_code > 0:
                    store_locator_link = f"https://online.mmvietnam.com/store-locator?source={source_code}"
                    return {
                        "success": False,
                        "message": store_err.message,
                        "region": region_name,
                        "store_locator_link": store_locator_link,
                        "instruction_for_agent": (
                            f"Dạ, {store_err.message}. Anh/chị có thể tham khảo danh sách cửa hàng tại "
                            f"[Cửa hàng MM Mega Market {region_name}]({store_locator_link}) ạ."
                        ),
                    }
                return {"success": False, "message": store_err.message,
                        "instruction_for_agent": f"Dạ, {store_err.message} ạ."}
            except (MagentoAPIError, ValueError):
                pass  # fall through to region fallback below

        # Detect region from address string
        region_name, source_code = _detect_region_from_city(address)
        
        if source_code > 0:
            store_locator_link = f"https://online.mmvietnam.com/store-locator?source={source_code}"
            return {
                "success": False,
                "message": f"Cannot find the specific address: {address}",
                "region": region_name,
                "store_locator_link": store_locator_link,
                "instruction_for_agent": (
                    f"Dạ, em chưa tìm thấy địa chỉ chính xác của anh/chị, nhưng địa chỉ này thuộc khu vực {region_name}. "
                    f"Anh/chị có thể tham khảo [Cửa hàng MM Mega Market {region_name}]({store_locator_link}). "
                    f"Hoặc anh/chị vui lòng cung cấp địa chỉ cụ thể hơn gồm tỉnh/thành phố và phường/xã để em tìm cửa hàng gần nhất ạ."
                ),
            }
        else:
            return {
                "success": False,
                "message": f"Cannot find address: {address}",
                "instruction_for_agent": (
                    f"Không xác định được khu vực từ địa chỉ. Vui lòng yêu cầu khách:\n"
                    f"1. Cung cấp địa chỉ cụ thể hơn với tỉnh/thành phố và phường/xã, HOẶC\n"
                    f"2. Chọn khu vực để xem cửa hàng:\n"
                    f"   - [MM Mega Market Miền Bắc](https://online.mmvietnam.com/store-locator?source=1)\n"
                    f"   - [MM Mega Market Miền Trung](https://online.mmvietnam.com/store-locator?source=2)\n"
                    f"   - [MM Mega Market Miền Nam](https://online.mmvietnam.com/store-locator?source=3)"
                ),
            }

    except NoStoreNearLocationError as e:
        # Detect region from address to provide appropriate store locator link
        try:
            suggest_location = get_suggest_location(address, tool_context)
            city_name = ""
            if suggest_location:
                city_name = suggest_location[0].get('city', '')
        except:
            city_name = ""
        
        region_name, source_code = _detect_region_from_city(city_name if city_name else address)
        
        if source_code > 0:
            store_locator_link = f"https://online.mmvietnam.com/store-locator?source={source_code}"
            return {
                "success": False,
                "message": e.message,
                "region": region_name,
                "store_locator_link": store_locator_link,
                "instruction_for_agent": (
                    f"Dạ, {e.message}. Anh/chị có thể tham khảo danh sách cửa hàng tại "
                    f"[Cửa hàng MM Mega Market {region_name}]({store_locator_link}) ạ."
                ),
            }
        else:
            return {
                "success": False,
                "message": e.message,
                "instruction_for_agent": (
                    f"Không tìm thấy cửa hàng gần vị trí. Vui lòng yêu cầu khách chọn khu vực:\n"
                    f"- [MM Mega Market Miền Bắc](https://online.mmvietnam.com/store-locator?source=1)\n"
                    f"- [MM Mega Market Miền Trung](https://online.mmvietnam.com/store-locator?source=2)\n"
                    f"- [MM Mega Market Miền Nam](https://online.mmvietnam.com/store-locator?source=3)"
                ),
            }

    except MagentoAPIError as e:
        return {
            "success": False,
            "message": e.message,
            "instruction_for_agent": (
                f"Có lỗi API xảy ra. Vui lòng yêu cầu khách chọn khu vực để xem cửa hàng:\n"
                f"- [MM Mega Market Việt Nam Miền Bắc](https://online.mmvietnam.com/store-locator?source=1)\n"
                f"- [MM Mega Market Việt Nam Miền Trung](https://online.mmvietnam.com/store-locator?source=2)\n"
                f"- [MM Mega Market Việt Nam Miền Nam](https://online.mmvietnam.com/store-locator?source=3)"
            ),
        }


def _get_nearest_store(
        city_code: str,
        district_code: str,
        ward_code: str,
        address: Optional[str] = None,
        tool_context: ToolContext = None,
) -> dict:
    """
    Get nearby stores based on the customer's address.
    This function is not intended to be called by the agent.
    Args:
        city_code (str): The code of the city.
        district_code (str): The code of the district.
        ward_code (str): The code of the ward.
        address (str, optional): The full street address for more accurate distance calculation.
        tool_context (ToolContext): The context containing session data.
    Returns:
        dict: Dictionary containing list of stores with distance info and metadata.
    """
    base_url, store_id = _get_magento_config(tool_context)

    query = """
        query GetNearestStore($street: String, $city: String!, $district: String, $ward: String!){
            storeView(
                street: $street,
                city: $city,
                district: $district,
                ward: $ward,
                language: "vi",
                website: "b2c"
            )
            {
                store_view_code {
                    distance
                    distance_text
                    priority
                    store_view_code
                    source_name
                }
                message
                allow_selection
            }
        }
        """
    variables = {
        "street": address or "",
        "city": city_code,
        "district": district_code or "",
        "ward": ward_code,
    }

    response = make_graphql_request(query, variables, base_url=base_url, store_id=store_id)
    res = response.json()

    # validate the response structure
    if 'data' not in res or 'storeView' not in res['data'] or 'store_view_code' not in res['data']['storeView']:
        print(f"{'-' * 80}\n"
              f"Invalid response from the server. 'storeView' or 'store_view_code' not found in response data.\n"
              f"Response: {res}\n"
              f"{'-' * 80}\n")
        raise MagentoAPIError("Invalid response from the server.")

    store_view_data = res['data']['storeView']
    store_view_codes = store_view_data['store_view_code']

    if not store_view_codes:
        api_message = store_view_data.get('message') or "No stores found near the provided address."
        logger.info(f"No stores for address: {address} — API says: {api_message}")
        raise NoStoreNearLocationError(api_message)

    # Get detailed information for all stores
    stores_info = []
    for store in store_view_codes:
        query = """
            query GetStoreInfo($store_view_code: String!){
                storeInformation(store_view_code: $store_view_code){
                    address
                    name
                    source_code
                }
            }
            """
        variables = {
            "store_view_code": store['store_view_code']
        }
        store_response = make_graphql_request(query, variables, base_url=base_url, store_id=store_id)
        store_info_res = store_response.json()

        if 'data' not in store_info_res or 'storeInformation' not in store_info_res['data']:
            print(f"{'-' * 80}\n"
                  f"Invalid response from Magento API for store {store['store_view_code']}.\n"
                  f"Response: {store_info_res}\n"
                  f"{'-' * 80}\n")
            continue

        store_info = store_info_res['data']['storeInformation']
        stores_info.append({
            'name': store_info.get('name', 'Unknown Store'),
            'address': store_info.get('address', 'No address available'),
            'distance': store['distance'],
            'distance_text': store['distance_text'],
            'priority': store['priority'],
            'store_view_code': store['store_view_code'],
            'source_name': store.get('source_name', ''),
        })

    stores_info.sort(key=lambda s: float(s['distance']) if s['distance'] else float('inf'))

    return {
        'stores': stores_info,
        'message': store_view_data.get('message'),
        'allow_selection': store_view_data.get('allow_selection', False),
    }


_REGION_SOURCE_TYPE = {
    "bắc": 1, "miền bắc": 1, "bac": 1, "north": 1,
    "nam": 2, "miền nam": 2, "nam": 2, "south": 2,
    "trung": 3, "miền trung": 3, "central": 3,
}
_SOURCE_TYPE_LABEL = {1: "Miền Bắc", 2: "Miền Nam", 3: "Miền Trung"}


def get_store_list_by_region(
        region: str,
        city: Optional[str] = None,
        tool_context: ToolContext = None,
) -> dict:
    """
    Get the list of MM Mega Market stores filtered by region and optionally by city.

    Args:
        region (str): Region name. Accepted values (case-insensitive):
            - "Miền Bắc" / "bắc" / "north" → Northern stores
            - "Miền Nam" / "nam" / "south"  → Southern stores
            - "Miền Trung" / "trung" / "central" → Central stores
            - "all" / "tất cả" → All stores across Vietnam
        city (str, optional): City/province name to further filter stores.
            e.g. "Hà Nội", "Hồ Chí Minh", "Đà Nẵng". If omitted, returns all stores in the region.
        tool_context (ToolContext): Tool execution context (automatically provided)

    Returns:
        dict with:
            - success (bool)
            - region (str): Resolved region label
            - stores (list): List of stores with name and street address
            - stores_formatted (str): Ready-to-display markdown list
            - instruction_for_agent (str): Guidance for the agent
    """
    # StoreLocators data is only accurate on the public production site.
    base_url = "https://online.mmvietnam.com"

    region_key = region.lower().strip()
    show_all = region_key in ("all", "tất cả", "tat ca", "")

    if show_all:
        source_type = None
        region_label = "toàn quốc"
    else:
        source_type = _REGION_SOURCE_TYPE.get(region_key)
        if source_type is None:
            return {
                "success": False,
                "instruction_for_agent": (
                    "Không xác định được vùng miền. Vui lòng hỏi khách muốn xem cửa hàng ở "
                    "Miền Bắc, Miền Trung hay Miền Nam."
                ),
            }
        region_label = _SOURCE_TYPE_LABEL[source_type]

    # Resolve city name → city_code if provided
    city_code = None
    city_label = None
    if city:
        try:
            cities = _get_cities(tool_context)
            _, city_code = _match_name(city, cities, 'name', 'city_code', 'City', score_cutoff=50)
            city_item = next((c for c in cities if c['city_code'] == city_code), None)
            city_label = city_item['name'] if city_item else city
        except (NoSuggestedLocationError, MagentoAPIError):
            city_code = None

    query = """
    query GetStoreLocators($store_source_type: Int, $store_city: Int) {
        StoreLocators(store_source_type: $store_source_type, store_city: $store_city) {
            name
            street
            latitude
            longitude
        }
    }
    """
    variables = {
        "store_source_type": source_type,
        "store_city": int(city_code) if city_code else None,
    }

    try:
        res = make_graphql_request(query, variables, base_url=base_url, store_id="b2c_10010_vi").json()
        stores = res.get("data", {}).get("StoreLocators") or []

        scope_label = city_label if city_label else region_label
        lines = []
        for i, s in enumerate(stores, 1):
            addr = s.get("street") or "Xem chi tiết trên website"
            lines.append(f"{i}. **{s['name']}**\n   {addr}")

        stores_formatted = "\n\n".join(lines) if lines else "Không có dữ liệu cửa hàng."

        return {
            "success": True,
            "region": region_label,
            "city": city_label,
            "stores": stores,
            "stores_count": len(stores),
            "stores_formatted": stores_formatted,
            "instruction_for_agent": (
                f"Dạ, hiện MM Mega Market có {len(stores)} cửa hàng tại {scope_label}. "
                f"Hãy hiển thị danh sách stores_formatted cho khách. "
                f"Cuối message hỏi khách có muốn tìm cửa hàng gần địa chỉ cụ thể không."
            ),
        }
    except Exception as e:
        logger.error(f"get_store_list_by_region error: {e}")
        return {
            "success": False,
            "instruction_for_agent": "Có lỗi khi lấy danh sách cửa hàng. Hãy thử lại sau.",
        }