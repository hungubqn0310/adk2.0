import asyncio
import json
import logging
import traceback

import requests
from typing_extensions import override
from typing import Optional, Any
from google.adk.tools import ToolContext, BaseTool
from google.genai import types
from pydantic import BaseModel, Field

from mmvn_b2c_agent.api.semantic_search import SemanticAiSearchQuery, do_semantic_search_async, SemanticAiSearchResult
from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.shared.schema import MagentoProductSortOptions, MagentoProductSortDirection, \
    MagentoMainCategories, MagentoProductFields, MMVN_MAIN_CATEGORY_MAP
from mmvn_b2c_agent.tools.cng.common import process_product_search_data, save_search_result_to_session_state
from mmvn_b2c_agent.tools.utils import make_graphql_request_async

logger = logging.getLogger(__name__)

DEFAULT_FIELDS_TO_GET = [
    MagentoProductFields.SKU,
    MagentoProductFields.NAME,
    # MagentoProductFields.ART_NO,
    # MagentoProductFields.URL,
    MagentoProductFields.STOCK_STATUS,
    MagentoProductFields.PRICE,
    MagentoProductFields.UNIT,
]


class BestSellingProductsInput(BaseModel):
    """Input for best-selling products search"""
    search: Optional[str] = Field(
        default=None,
        description='Search keyword for product name (e.g., "rượu", "mì", "bánh mì"). Returns products matching this keyword sorted by sales volume.'
    )
    category: Optional[list[MagentoMainCategories]] = Field(
        default=None,
        description='Optional category filter. ONLY use if user explicitly requests filtering by category. For general searches (e.g., "tìm rượu bán chạy"), leave this as None and rely on search keyword only.'
    )
    # fields_to_get: list[MagentoProductFields] = Field(
    #     default=DEFAULT_FIELDS_TO_GET,
    #     description='List of product fields to get from the API.'
    # )
    price_min: Optional[float] = Field(
        default=None,
        description='Minimum price filter (optional)'
    )
    price_max: Optional[float] = Field(
        default=None,
        description='Maximum price filter (optional)'
    )
    page: int = Field(
        default=1,
        description='Page number for pagination'
    )
    page_size: int = Field(
        default=12,
        description='Number of results per page (default: 12, max: 25)'
    )


class BestSellingProductsTool(BaseTool):
    """
    Get the best-selling products from MM Mega Market Vietnam catalog.

    This tool retrieves products sorted by sales volume (ecom_qty_ordered DESC).
    It helps find the most popular and fast-moving products in the catalog.

    Features:
    - Search by product name/keyword
    - Sort by sales volume (best-selling first)
    - Optional category filtering (ONLY use when user explicitly requests it)
    - Optional price range filtering
    - Pagination support
    - Returns product details including SKU, name, price, stock status, URL

    Requirements:
    - store_id and base_url must be set in session state
    - Returns up to 25 products per page

    Important:
    - These are products being retrieved for the USER
    - SKU values returned must NEVER be fabricated - only use what the API returns
    - Use returned SKU values for cart operations (add, update, remove)
    - For general searches, rely on search keyword only, do not auto-add category filters
    """

    def __init__(
            self,
            *,
            is_long_running: bool = False,
            custom_metadata: Optional[dict[str, Any]] = None,
    ):
        self.name = 'get_best_selling_products_async'
        self.description = "Get best-selling products from MM Mega Market Vietnam catalog"
        super().__init__(name=self.name, description=self.description,
                         is_long_running=is_long_running, custom_metadata=custom_metadata)

    @override
    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description="""
    Get the best-selling products from MM Mega Market Vietnam catalog.

    This tool retrieves products sorted by sales volume (highest to lowest).
    It helps find the most popular and fast-moving products in the catalog.

    Features:
    - Search by product name/keyword (e.g., "rượu", "mì", "bánh mì")
    - Sort by sales volume (best-selling first)
    - Optional category filtering (ONLY use if user explicitly requests it)
    - Optional price range filtering
    - Pagination support
    - Returns product details including SKU, name, price, stock status, URL

    Requirements:
    - store_id and base_url must be set in session state
    - Returns up to 25 products per page

    Important:
    - These are products being retrieved for the USER
    - SKU values returned must NEVER be fabricated - only use what the API returns
    - Use returned SKU values for cart operations (add, update, remove)
    - For general searches (e.g., "tìm rượu bán chạy"), use search keyword ONLY without category filter""",
            parameters_json_schema=BestSellingProductsInput.model_json_schema(),
        )

    @override
    async def run_async(self, *, args: dict[str, Any], tool_context: ToolContext) -> Any:
        # Validate the input args
        if 'category' in args and args['category']:
            args['category'] = [cate.lower() for cate in args['category']]
        
        try:
            args = BestSellingProductsInput.model_validate(args)
            print(args)
        except Exception as e:
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "message": f"Invalid args input: {str(e)}",
                "code": "INVALID_INPUT"
            }

        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        store_id = magento_session_data.get('store_id', DEFAULT_MMVN_STORE_ID)
        base_url = magento_session_data.get('base_url', DEFAULT_MMVN_STORE_URL).rstrip('/')

        # Validate page_size
        page_size = min(args.page_size, 25)  

        # construct the GraphQL query
        # graphql_fields_to_get = '\n'.join(prod_field.graphql_query for prod_field in args.fields_to_get)
        graphql_fields_to_get = '\n'.join(prod_field.graphql_query for prod_field in DEFAULT_FIELDS_TO_GET)
        fragment = f"""
            fragment ProductFragment on ProductInterface {{
                {graphql_fields_to_get}
            }}
        """
        graphql_query = """
            query BestSellingProducts(
                $currentPage: Int = 1
                $pageSize: Int = 12
                $filters: ProductAttributeFilterInput!
                $sort: ProductAttributeSortInput
                $asmUid: String
                $phoneNumber: String
            ) {
                products(
                    currentPage: $currentPage
                    pageSize: $pageSize
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
        """ + fragment

        # construct the variables with empty search (to get all products)
        variables = {
            "currentPage": args.page,
            "pageSize": page_size,
            "filters": {},
            "asmUid": "",
            "phoneNumber": "",
        }

        # Add search filter if provided
        if args.search:
            variables["filters"]["name"] = {
                "match": args.search
            }

        # Add category filter if provided
        if args.category:
            variables["filters"]["category_uid"] = {
                "in": [cate.name for cate in args.category]
            }

        # Add price filters if provided
        # todo: remove this check once price filter is pushed to online.mmvietnam.com
        if 'online.mmvietnam.com' not in base_url:
            if args.price_min is not None:
                variables["filters"]["price"] = {
                    "from": args.price_min
                }
            if args.price_max is not None:
                if "price" not in variables["filters"]:
                    variables["filters"]["price"] = {}
                variables["filters"]["price"]["to"] = args.price_max

        # Sort by best-selling (ecom_qty_ordered DESC)
        variables["sort"] = {
            "ecom_qty_ordered": "DESC"
        }

        # make the request
        try:
            res = await make_graphql_request_async(graphql_query, variables, base_url, store_id)

            # Check if response is None
            if res is None:
                return {
                    "success": False,
                    "message": "Best-selling products query failed: no response from API",
                    "code": "NO_RESPONSE"
                }

            # Check for GraphQL errors first
            if res.get("errors"):
                error_messages = [err.get("message", str(err)) for err in res["errors"]]
                return {
                    "success": False,
                    "message": f"Best-selling products query returned GraphQL errors: {'; '.join(error_messages)}",
                    "code": "GRAPHQL_ERROR",
                    "errors": res["errors"]
                }

            if not res.get("data"):
                return {
                    "success": False,
                    "message": "Best-selling products query returned API error: empty data",
                    "code": "INVALID_RESPONSE"
                }
            elif not res.get("data", {}).get("products", {}).get("items"):
                search_info = f" matching '{args.search}'" if args.search else ""
                return {
                    "success": False,
                    "message": f"Best-selling products{search_info} at page {args.page} with page size {page_size} has no products.",
                    "instruction_for_agent": "Try adjusting filters, search keyword, page number, or check if products are available.",
                    "code": "NO_PRODUCTS"
                }

        except requests.RequestException as e:
            return {
                "success": False,
                "message": f"Best-selling products query returned HTTP error: {str(e)}",
                "code": "HTTP_ERROR"
            }

        # format the data returned to AI
        data = res["data"]["products"]["items"]
        processed_prod_data = process_product_search_data(data, base_url)
        save_search_result_to_session_state(args, processed_prod_data, tool_context)
        
        return {
            "success": True,
            "data": processed_prod_data,
            "total_count": res["data"]["products"]["total_count"],
            "total_pages": res["data"]["products"]["page_info"]["total_pages"],
            "current_page": args.page,
            "page_size": page_size,
            "search_keyword": args.search,
        }


if __name__ == '__main__':
    from types import SimpleNamespace

    dummy_tool_context = SimpleNamespace(state={})
    
    # Test: Get best-selling products with search
    res = asyncio.run(BestSellingProductsTool().run_async(args={
        "search": "rượu",
        "page": 1,
        "page_size": 12
    }, tool_context=dummy_tool_context))
    print(json.dumps(res, indent=4, ensure_ascii=False))