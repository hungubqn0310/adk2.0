"""
Tool to get products with discounts/promotions
"""
import logging
import traceback
from typing import Any, Optional

import requests
from google.adk.tools import BaseTool, ToolContext
from google.genai import types
from pydantic import BaseModel, Field
from typing_extensions import override

from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.shared.schema import (
    MagentoMainCategories,
    MagentoProductFields,
    MagentoProductSortOptions,
    MagentoProductSortDirection,
)
from mmvn_b2c_agent.tools.cng.common import process_product_search_data, save_search_result_to_session_state
from mmvn_b2c_agent.tools.utils import make_graphql_request_async

logger = logging.getLogger(__name__)

DEFAULT_FIELDS_TO_GET = [
    MagentoProductFields.SKU,
    MagentoProductFields.NAME,
    # MagentoProductFields.ART_NO,
    # MagentoProductFields.URL,
    MagentoProductFields.STOCK_STATUS,
    MagentoProductFields.PRICE_WITH_DISCOUNT,  # Use PRICE_WITH_DISCOUNT to get discount info
    MagentoProductFields.UNIT,
    MagentoProductFields.NEED_AGE_VERIFICATION,
    MagentoProductFields.PROMO_INFO,
]


class GetProductsDiscountInput(BaseModel):
    """Input parameters for getting discount products"""

    language: str = Field(
        description="Detected language from the user's input or chat context."
    )

    keyword: str = Field(
        description="Keyword in the same language as the user's query (for transparency)."
    )

    keyword_in_vietnamese: str = Field(
        description="Vietnamese keyword used for database search."
    )
    category: Optional[list[MagentoMainCategories]] = Field(
        default=None,
        description='Optional category to filter discount products'
    )
    # fields_to_get: list[MagentoProductFields] = Field(
    #     default=DEFAULT_FIELDS_TO_GET,
    #     description='List of product fields to get from the API.'
    # )
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    sort_by: Optional[MagentoProductSortOptions] = Field(
        default=MagentoProductSortOptions.relevance,
        description='Sort option for the results'
    )
    sort_direction: Optional[MagentoProductSortDirection] = Field(
        default=MagentoProductSortDirection.DESC,
        description='Sort direction'
    )
    page: int = Field(
        default=1,
        description='Page number for pagination'
    )
    page_size: int = Field(
        default=50,
        description='Number of products per page (default 50 to increase chance of finding promotions)'
    )


class GetProductsDiscountTool(BaseTool):
    """
    Get products with discounts or promotions from MM Mega Market Vietnam catalog.

    This tool searches specifically for products that have:
    - Direct price discounts (amount_off > 0)
    - Promotional deals (promo_info)
    - Special events (promotion_event)
    - Great deals
    - Free gifts

    Features:
    - Keyword-based search (optional, Vietnamese)
    - Category filtering
    - Price range filtering
    - Sorting by various attributes
    - Pagination support
    - Automatically filters to only return products with valid promotions
    """

    def __init__(
            self,
            *,
            is_long_running: bool = False,
            custom_metadata: Optional[dict[str, Any]] = None,
    ):
        self.name = 'get_products_discount'
        self.description = "Get products with discounts or promotions"
        super().__init__(
            name=self.name,
            description=self.description,
            is_long_running=is_long_running,
            custom_metadata=custom_metadata
        )

    @override
    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description="""
Get products with discounts or promotions from MM Mega Market Vietnam catalog.

This tool specifically searches for products that have valid promotions:
- Direct price discounts
- Promotional deals (buy X get discount)
- Special events
- Great deals
- Free gifts

Features:
- Optional keyword search (must be in Vietnamese if provided)
- Category filtering (auto-fallback if category UIDs invalid)
- Price range filtering
- Sorting by various attributes
- Pagination support (default 50 items for better promotion coverage)
- Automatically filters to only return products with valid promotions

Returns only products with valid promotion information.

IMPORTANT: When user asks for discount/promotion products, call this tool ONCE with broad keyword.
Do NOT call multiple times with variations unless specifically needed.
Present ALL products returned, not just selected ones, to give user full visibility of available promotions.""",
            parameters_json_schema=GetProductsDiscountInput.model_json_schema(),
        )

    @override
    async def run_async(self, *, args: dict[str, Any], tool_context: ToolContext) -> Any:
        # Validate input - convert category strings to enum
        if 'category' in args and args['category'] is not None:
            converted_categories = []
            for cate_input in args['category']:
                # If it's already an enum, keep it
                if isinstance(cate_input, MagentoMainCategories):
                    converted_categories.append(cate_input)
                # If it's a string, try to find matching enum by value (case-insensitive)
                elif isinstance(cate_input, str):
                    cate_lower = cate_input.lower()
                    for cat_enum in MagentoMainCategories:
                        if cat_enum.value == cate_lower:
                            converted_categories.append(cat_enum)
                            break
            args['category'] = converted_categories if converted_categories else None

        try:
            args = GetProductsDiscountInput.model_validate(args)
            logger.info(f"[GetProductsDiscountTool] Searching discount products: {args}")
        except Exception as e:
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "message": f"Invalid args input: {str(e)}",
                "code": "INVALID_INPUT"
            }
        # Get session data - handle both nested and direct structure
        state_data = tool_context.state if hasattr(tool_context, 'state') else {}

        # Try direct structure first (real agent context)
        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")

        logger.info(f"[GetProductsDiscountTool] Session: store_id={store_id}, base_url={base_url}, magento_data={bool(magento_session_data)}")

        # Build GraphQL query
        # graphql_fields_to_get = '\n'.join(prod_field.graphql_query for prod_field in args.fields_to_get)
        graphql_fields_to_get = '\n'.join(prod_field.graphql_query for prod_field in DEFAULT_FIELDS_TO_GET)
        fragment = f"""
            fragment ProductFragment on ProductInterface {{
                {graphql_fields_to_get}
            }}
        """

        graphql_query = """
            query ProductSearch(
                $search: String!
                $filter: ProductAttributeFilterInput!
                $sort: ProductAttributeSortInput
                $currentPage: Int
                $pageSize: Int
            ) {
                products(
                    search: $search
                    filter: $filter
                    sort: $sort
                    currentPage: $currentPage
                    pageSize: $pageSize
                ) {
                    items {
                        ...ProductFragment
                    }
                    total_count
                    page_info {
                        total_pages
                    }
                }
            }
        """ + fragment

        # Build variables
        keyword_to_use = args.keyword_in_vietnamese
        variables = {
            "search": keyword_to_use if keyword_to_use else "",
            "filter": {"category_uid": {"in": []}},  
            "sort": {args.sort_by.name: args.sort_direction.value},
            "currentPage": args.page,
            "pageSize": args.page_size
        }

        # Add category filter if specified
        if args.category:
            variables["filter"]["category_uid"]["in"] = [
                cate.name for cate in args.category
            ]

        # Add price filters (only for non-online stores)
        if 'online.mmvietnam.com' not in base_url:
            if args.price_min is not None:
                variables["filter"]["price"] = {"from": args.price_min}
            if args.price_max is not None:
                if "price" not in variables["filter"]:
                    variables["filter"]["price"] = {}
                variables["filter"]["price"]["to"] = args.price_max

        # Make the request
        try:
            logger.info(f"[GetProductsDiscountTool] GraphQL variables: {variables}")
            res = await make_graphql_request_async(graphql_query, variables, base_url, store_id)

            if res is None or not isinstance(res, dict):
                return {
                    "success": False,
                    "message": f"Search for discount products returned no response",
                    "code": "NO_RESPONSE"
                }

            data = res.get("data")
            if not data or not isinstance(data, dict):
                logger.error(f"[GetProductsDiscountTool] API returned invalid data. Response: {res}")
                return {
                    "success": False,
                    "message": f"Search for discount products returned API error: empty data",
                    "code": "INVALID_RESPONSE"
                }

            products = data.get("products")
            if not products or not isinstance(products, dict):
                logger.error(f"[GetProductsDiscountTool] API returned no products field. Data: {data}")

                # If category filter was used and query failed, retry without category
                if args.category and variables["filter"]["category_uid"]["in"]:
                    logger.info(f"[GetProductsDiscountTool] Retrying without category filter (category UIDs may not be valid for this store)")
                    variables["filter"]["category_uid"]["in"] = []
                    res = await make_graphql_request_async(graphql_query, variables, base_url, store_id)

                    if res and isinstance(res, dict):
                        data = res.get("data")
                        if data and isinstance(data, dict):
                            products = data.get("products")
                            if products and isinstance(products, dict):
                                logger.info(f"[GetProductsDiscountTool] Retry successful without category filter")
                            else:
                                return {
                                    "success": False,
                                    "message": f"Search for discount products returned API error: no products field (even without category filter)",
                                    "code": "INVALID_RESPONSE"
                                }
                        else:
                            return {
                                "success": False,
                                "message": f"Search for discount products returned API error: empty data (retry failed)",
                                "code": "INVALID_RESPONSE"
                            }
                    else:
                        return {
                            "success": False,
                            "message": f"Search for discount products returned no response (retry failed)",
                            "code": "NO_RESPONSE"
                        }
                else:
                    return {
                        "success": False,
                        "message": f"Search for discount products returned API error: no products field",
                        "code": "INVALID_RESPONSE"
                    }

            items = products.get("items")
            if not items:
                return {
                    "success": False,
                    "message": f"No discount products found at page {args.page}",
                    "instruction_for_agent": "Try different search criteria or check next pages.",
                    "code": "NO_PRODUCTS"
                }

        except requests.RequestException as e:
            return {
                "success": False,
                "message": f"Search for discount products returned HTTP error: {str(e)}",
                "code": "HTTP_ERROR"
            }

        # Process and FILTER to only include products with valid promotions
        processed_prod_data = process_product_search_data(
            items,
            base_url,
            filter_only_discounted=True  # This is the key - only return products with valid promotions
        )

        save_search_result_to_session_state(args, processed_prod_data, tool_context)

        # If filtering resulted in no products, provide helpful message
        if not processed_prod_data:
            return {
                "success": False,
                "message": f"No products with valid promotions found at page {args.page}",
                "instruction_for_agent": "The search returned products but none had valid promotions. Try different search criteria or check next pages.",
                "code": "NO_PROMOTION_PRODUCTS"
            }

        return {
            "success": True,
            "data": processed_prod_data,
            "total_count": len(processed_prod_data),  # Return count of products with promotions
            "original_total_count": products.get("total_count", 0),  # Total before filtering
            "message": f"Found {len(processed_prod_data)} products with valid promotions (out of {products.get('total_count', 0)} total products)"
        }
