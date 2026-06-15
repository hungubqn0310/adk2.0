"""
Unified Product Search Tool - Consolidates multiple product search tools into one.

This tool replaces:
- ProductSearchTool (normal search)
- ProductSearchByFullnameTool (fullname search)
- GetProductsDiscountTool (discount/promo search)
- BestSellingProductsTool (bestseller search)

Benefits:
- Simpler for LLM to use (1 tool instead of 4)
- Cleaner input schema (removed redundant params like language, sorting, paging)
- Easier to maintain (centralized search logic)
"""

import inspect
import logging
from types import FunctionType
from typing import Any, Optional, Annotated

import requests
from google.adk.agents import InvocationContext
from google.adk.tools import FunctionTool, ToolContext
from google.genai import types
from pydantic import Field

from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.shared.schema import (
    MagentoMainCategories,
    MagentoProductFields,
    MagentoProductSortOptions,
    MagentoProductSortDirection,
)
from mmvn_b2c_agent.tools.cng.common import (
    process_product_search_data,
    process_product_search_data_optimized,
    save_search_result_to_session_state,
)
from mmvn_b2c_agent.tools.utils import make_graphql_request_async

logger = logging.getLogger(__name__)

# Fields to retrieve for all search types (normal, discount, bestseller)
# Includes full price info with discount details and promotion info
PRODUCT_FIELDS_TO_GET = [
    MagentoProductFields.SKU,
    MagentoProductFields.NAME,
    MagentoProductFields.PRICE_WITH_DISCOUNT,
    MagentoProductFields.UNIT,
    MagentoProductFields.PRODUCT_TYPE,  # mm_product_type: 'F' = Fresh (step 0.5), 'N' = Normal (step 1)
    MagentoProductFields.NEED_AGE_VERIFICATION,
    MagentoProductFields.PROMO_INFO,
]


def to_adk_func(fn):
    """Helper to remove tool_context parameter from function signature for ADK."""
    sig = inspect.signature(fn)
    ignore_params = ("tool_context", "input_stream")
    new_params = [p for name, p in sig.parameters.items() if name not in ignore_params]
    new_fn = FunctionType(
        fn.__code__,
        fn.__globals__,
        fn.__name__,
        fn.__defaults__,
        fn.__closure__,
    )
    new_fn.__signature__ = sig.replace(parameters=new_params)
    new_fn.__doc__ = fn.__doc__
    new_fn.__annotations__ = fn.__annotations__
    return new_fn


class TypedFunctionTool(FunctionTool):
    """Custom FunctionTool that properly handles tool_context parameter."""

    def _get_declaration(self):
        new_func = to_adk_func(self.func)
        fn_decl = types.FunctionDeclaration.from_callable_with_api_option(
            callable=new_func, api_option=self._api_variant.value
        )
        return fn_decl


async def search_products_async(
    tool_context: ToolContext,
    language: Annotated[
        str,
        Field(description="Detected language from the user's input or chat context."),
    ],
    keyword: Annotated[
        str,
        Field(
            description="Generated keyword in the same language as the user's query."
        ),
    ],
    keyword_in_vietnamese: Annotated[
        str,
        Field(
            description="Vietnamese equivalent or translation of the generated keyword, used for search."
        ),
    ],
    search_type: Annotated[
        str,
        Field(
            default="normal",
            description="""
Type of product search (valid values: "normal", "discount", "bestseller", "search_by_fullname"):
- "normal": Standard product search by keyword 
- "discount": Products with promotions/discounts only
- "bestseller": Best-selling products sorted by sales volume
- "search_by_fullname": Search by EXACT product name as provided by user.
  Use this when user provides a complete product name from their shopping list, file, or system
  (e.g., "DAU TAY HAN QUOC NK (250G/HOP)", "SUA TUOI VINAMILK 100% 1L").
  This mode keeps the keyword EXACTLY as provided without any conversion or translation.
        """,
        ),
    ] = "normal",
    category: Annotated[
        Optional[list[str]],
        Field(
            description="""Optional category filter using Vietnamese category names.
Valid categories:
- "thực phẩm tươi sống" (fresh food: meat, fish, vegetables, fruits)
- "đồ hộp - đồ khô" (canned/dry goods: noodles, rice)
- "dầu ăn - gia vị - nước chấm" (cooking oil, spices, sauces)
- "bơ - trứng - sữa" (butter, eggs, milk)
- "nước giải khát" (beverages)
- "đồ uống đóng hộp" (canned drinks)
- "bánh kẹo các loại" (candy, snacks)
- "đồ ăn chế biến" (processed food)
- "đồ gia dụng" (household items)
- "thiết bị gia dụng - điện tử" (appliances, electronics)
- "chăm sóc cá nhân" (personal care)
- "vệ sinh nhà cửa" (household cleaning)
- "đồ uống có cồn" (alcoholic beverages)
- "thực phẩm đông lạnh" (frozen food)
- "chăm sóc thú cưng" (pet care)
- "thực phẩm chức năng" (supplements)

Use this to filter out unwanted products. Example: "tôm" might return both "tôm tươi" and "bánh phồng tôm",
so use ["thực phẩm tươi sống"] to get only fresh shrimp.
"""
        ),
    ] = None,
    price_min: Annotated[
        Optional[float], Field(description="Minimum price filter (optional)")
    ] = None,
    price_max: Annotated[
        Optional[float], Field(description="Maximum price filter (optional)")
    ] = None,
) -> dict[str, Any]:
    """
    Search for products in the MM Mega Market Vietnam catalog.

    This unified tool handles all product search types:
    - Normal search: Find products by keyword
    - Discount search: Find products with promotions/discounts
    - Bestseller search: Find most popular products by sales volume

    Features:
    - Vietnamese keyword search
    - Optional category filtering
    - Optional price range filtering
    - Automatic optimization based on search type

    Returns product details including SKU, name, price, unit, and promotion info.

    IMPORTANT:
    - All search keywords MUST be in Vietnamese
    - SKU values must NEVER be fabricated
    - These searches are performed for the USER
    """
    # Normalize and validate search_type to prevent MALFORMED_FUNCTION_CALL
    search_type_normalized = (search_type or "normal").lower().strip()
    if search_type_normalized not in {"normal", "discount", "bestseller", "search_by_fullname"}:
        logger.warning(f"[search_products_async] Invalid search_type '{search_type}', defaulting to 'normal'")
        search_type_normalized = "normal"

    # Build args dict from parameters
    args = {
        "language": language,
        "keyword": keyword,
        "keyword_in_vietnamese": keyword_in_vietnamese,
        "search_type": search_type_normalized,
        "category": category,
        "price_min": price_min,
        "price_max": price_max,
    }

    # Validate and normalize category input from list[str] to list[MagentoMainCategories]
    if "category" in args and args["category"]:
        normalized_categories = []

        # Common typo corrections - map to exact enum values
        typo_corrections = {
            "dấu ấn - gia vị": "dầu ăn - gia vị - nước chấm",
            "dầu ăn - gia vị": "dầu ăn - gia vị - nước chấm",
            "dau an - gia vi": "dầu ăn - gia vị - nước chấm",
            "dầu ăn": "dầu ăn - gia vị - nước chấm",
            "gia vị": "dầu ăn - gia vị - nước chấm",
        }

        for cate_input in args["category"]:
            if isinstance(cate_input, str):
                cate_lower = cate_input.lower().strip()

                # Apply typo corrections (exact match first, then substring)
                if cate_lower in typo_corrections:
                    cate_lower = typo_corrections[cate_lower]
                    logger.info(f"[search_products_async] Auto-corrected category typo: '{cate_input}' → '{cate_lower}'")

                # Try to match with enum values
                matched = False
                for cat_enum in MagentoMainCategories:
                    if cat_enum.value == cate_lower:
                        normalized_categories.append(cat_enum)
                        matched = True
                        break

                if not matched:
                    # Category string not found in enum
                    logger.warning(f"[search_products_async] Category '{cate_input}' not found in MagentoMainCategories")
            elif isinstance(cate_input, MagentoMainCategories):
                normalized_categories.append(cate_input)
        args["category"] = normalized_categories if normalized_categories else None

    logger.info(f"[search_products_async] Search params (normalized): {args}")

    # Get session data
    if isinstance(tool_context, InvocationContext):
        state = tool_context.session.state
    else:
        state = tool_context.state
    magento_session_data = state.get("state", {}).get("magento_session_data", {})
    store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
    base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
    is_production = "online.mmvietnam.com" in base_url

    # Determine search parameters based on search_type (use normalized value)
    if search_type_normalized == "discount":
        page_size = 50
    elif search_type_normalized == "search_by_fullname":
        page_size = 7  # Smaller page size for exact name search
    else:
        page_size = 12

    # Set sorting based on search type
    if search_type_normalized == "bestseller":
        sort_by = "ecom_qty_ordered"  # Sort by sales volume
        sort_direction = "DESC"
    else:
        sort_by = MagentoProductSortOptions.relevance.name
        sort_direction = MagentoProductSortDirection.DESC.value

    # Build GraphQL query with product fields (same for all search types)
    graphql_fields_to_get = "\n".join(
        prod_field.graphql_query for prod_field in PRODUCT_FIELDS_TO_GET
    )
    fragment = f"""
        fragment ProductFragment on ProductInterface {{
            {graphql_fields_to_get}
        }}
    """

    # Build query based on environment and search type
    # For search_by_fullname, we need EXACT match, not smart/semantic search
    # On staging, asm_uid/phone_number params trigger smart search, so we skip them for fullname search
    use_simple_query = is_production or search_type_normalized == "search_by_fullname"

    if use_simple_query:
        # Simple query without asm_uid/phone_number - for production OR fullname search
        # This avoids triggering smart search on staging when we need exact match
        graphql_query = (
            """
            query UnifiedProductSearch(
                $currentPage: Int = 1
                $inputText: String!
                $pageSize: Int = 12
                $filters: ProductAttributeFilterInput!
                $sort: ProductAttributeSortInput
            ) {
                products(
                    currentPage: $currentPage
                    pageSize: $pageSize
                    search: $inputText
                    filter: $filters
                    sort: $sort
                ) {
                    items {
                        ...ProductFragment
                    }
                    page_info {
                        total_pages
                    }
                    total_count
                }
            }
        """
            + fragment
        )
    else:
        # Staging query with asm_uid/phone_number for smart search
        graphql_query = (
            """
            query UnifiedProductSearch(
                $currentPage: Int = 1
                $inputText: String!
                $pageSize: Int = 12
                $filters: ProductAttributeFilterInput!
                $sort: ProductAttributeSortInput
                $asmUid: String
                $phoneNumber: String
            ) {
                products(
                    currentPage: $currentPage
                    pageSize: $pageSize
                    search: $inputText
                    filter: $filters
                    sort: $sort
                    asm_uid: $asmUid
                    phone_number: $phoneNumber
                ) {
                    items {
                        ...ProductFragment
                    }
                    is_use_smart_search
                    page_info {
                        total_pages
                    }
                    total_count
                }
            }
        """
            + fragment
        )

    # Build variables
    variables = {
        "currentPage": 1,  # Always fetch first page
        "pageSize": page_size,
        "inputText": keyword_in_vietnamese,
        "filters": {},
        "sort": {sort_by: sort_direction},
    }

    # Only add asm_uid/phone_number for staging with smart search (not fullname search)
    if not use_simple_query:
        variables["asmUid"] = ""
        variables["phoneNumber"] = ""

    # Add category filter
    if args["category"]:
        variables["filters"]["category_uid"] = {
            "in": [cate.name for cate in args["category"]]
        }

    # Add price filters (only for non-production)
    if not is_production:
        if price_min is not None:
            variables["filters"]["price"] = {"from": price_min}
        if price_max is not None:
            if "price" not in variables["filters"]:
                variables["filters"]["price"] = {}
            variables["filters"]["price"]["to"] = price_max

    # Make GraphQL request
    try:
        res = await make_graphql_request_async(
            graphql_query, variables, base_url, store_id
        )

        if res is None or not isinstance(res, dict):
            return {
                "success": False,
                "message": f"Search for '{keyword}' returned no response",
                "code": "NO_RESPONSE",
            }

        if "errors" in res:
            logger.error(
                f"[search_products_async] GraphQL errors: {res['errors']}"
            )
            return {
                "success": False,
                "message": f"Search returned GraphQL errors: {res['errors']}",
                "code": "GRAPHQL_ERROR",
            }

        data = res.get("data")
        if not data or not isinstance(data, dict):
            logger.error(
                f"[search_products_async] Invalid data response: {res}"
            )
            return {
                "success": False,
                "message": "Search returned invalid data",
                "code": "INVALID_RESPONSE",
            }

        products = data.get("products")
        if not products or not isinstance(products, dict):
            logger.error(
                f"[search_products_async] No products field in response"
            )
            return {
                "success": False,
                "message": "Search returned no products field",
                "code": "INVALID_RESPONSE",
            }

        items = products.get("items")
        if not items:
            return {
                "success": False,
                "message": f"No products found for '{keyword}'",
                "instruction_for_agent": "Try different keywords or remove category filters",
                "code": "NO_PRODUCTS",
            }

    except requests.RequestException as e:
        logger.error(f"[search_products_async] HTTP error: {str(e)}")
        return {
            "success": False,
            "message": f"Search returned HTTP error: {str(e)}",
            "code": "HTTP_ERROR",
        }

    # Process results based on search_type (use normalized value)
    if search_type_normalized == "discount":
        # Filter to only products with promotions
        processed_prod_data = process_product_search_data(
            items, base_url, filter_only_discounted=True
        )

        if not processed_prod_data:
            return {
                "success": False,
                "message": f"No products with promotions found for '{keyword}'",
                "instruction_for_agent": "Search returned products but none had valid promotions. Try different keywords.",
                "code": "NO_PROMOTION_PRODUCTS",
            }

        save_search_result_to_session_state(args, processed_prod_data, tool_context)

        return {
            "success": True,
            "data": processed_prod_data,
            "total_count": len(processed_prod_data),
            "original_total_count": products.get("total_count", 0),
            "message": f"Found {len(processed_prod_data)} products with promotions (out of {products.get('total_count', 0)} total)",
        }
    else:
        # Normal or bestseller search - use optimized processing
        processed_prod_data = process_product_search_data_optimized(items)
        save_search_result_to_session_state(args, processed_prod_data, tool_context)

        return {
            "success": True,
            "data": processed_prod_data,
            "total_count": products.get("total_count", 0),
            "search_type": search_type_normalized,
        }

class UnifiedProductSearchTool(TypedFunctionTool):
    """Wrapper class to maintain backward compatibility with existing code."""

    def __init__(self):
        super().__init__(func=search_products_async)

        # Log the function declaration to help debug MALFORMED_FUNCTION_CALL errors
        try:
            declaration = self._get_declaration()
            logger.info(f"[UnifiedProductSearchTool] Function declaration:")
            logger.info(f"  Name: {declaration.name}")
            logger.info(f"  Description: {declaration.description}")
            if hasattr(declaration, 'parameters') and declaration.parameters:
                logger.info(f"  Parameters schema: {declaration.parameters}")
        except Exception as e:
            logger.error(f"[UnifiedProductSearchTool] Failed to get declaration: {e}")
