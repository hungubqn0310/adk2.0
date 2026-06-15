import asyncio
import json
import logging
import traceback

import requests
from google.adk.agents import InvocationContext
from typing_extensions import override
from typing import Optional, Any
from google.adk.tools import ToolContext, BaseTool
from google.genai import types
from pydantic import BaseModel, Field, conlist, model_validator

from mmvn_b2c_agent.api.semantic_search import SemanticAiSearchQuery, do_semantic_search_async, SemanticAiSearchResult
from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.shared.schema import MagentoProductSortOptions, MagentoProductSortDirection, \
    MagentoMainCategories, MagentoProductFields, MMVN_MAIN_CATEGORY_MAP
from mmvn_b2c_agent.tools.cng.common import process_product_search_data, process_product_search_data_optimized, save_search_result_to_session_state
from mmvn_b2c_agent.tools.utils import make_graphql_request_async

logger = logging.getLogger(__name__)

DEFAULT_FIELDS_TO_GET = [
    MagentoProductFields.SKU,
    MagentoProductFields.NAME,
    MagentoProductFields.PRICE,
    MagentoProductFields.UNIT,
    MagentoProductFields.PRODUCT_TYPE,  # mm_product_type: 'F' = Fresh (step 0.5), 'N' = Normal (step 1)
    MagentoProductFields.NEED_AGE_VERIFICATION,
    MagentoProductFields.PROMO_INFO,
    # MagentoProductFields.DESCRIPTION,
    # MagentoProductFields.STOCK_STATUS,
    # MagentoProductFields.ART_NO,
    # MagentoProductFields.URL,
]


class ProductSearchInput(BaseModel):
    language: str = Field(
        description="Detected language from the user's input or chat context."
    )

    keyword: str = Field(
        description="Generated keyword in the same language as the user's query."
    )

    keyword_in_vietnamese: str = Field(
        description="Vietnamese equivalent or translation of the generated keyword, used for search."
    )
    # noinspection PyTypeHints
    category: Optional[list[MagentoMainCategories]] = Field(
        default=None,
        description='Optional category to filter out. Use this if the keyword could potentially '
                    'result in unwanted products (e.g., "Tôm" could return both "tôm tươi" and "bánh phồng tôm", '
                    'so a "Thực phẩm tươi sống" category filter will be needed.)'
    )
    # fields_to_get: list[MagentoProductFields] = Field(
    #     default=DEFAULT_FIELDS_TO_GET,
    #     description='List of product fields to get from the API.'
    # )
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    sort_by: Optional[MagentoProductSortOptions] = None
    sort_direction: Optional[MagentoProductSortDirection] = None
    page: int = Field(
        default=1,
    )
    # limit: int = Field(
    #     default=12,
    #     description='Number of results to get from the API. Should always be 12.'
    # )

    # check the fields to get and automatically add 'is_alcohol' if not present
    # @model_validator(mode="after")
    # def check_fields_to_get(self):
    #     if MagentoProductFields.NEED_AGE_VERIFICATION in self.fields_to_get:
    #         return self
    #     self.fields_to_get.append(MagentoProductFields.NEED_AGE_VERIFICATION)
    #     return self


class ProductMultipleSearchInput(BaseModel):
    query_list: list[ProductSearchInput]


class ProductSearchTool(BaseTool):
    """
    Search for products in the MM Mega Market Vietnam catalog on behalf of the USER.

    This tool queries the Magento e-commerce API to find products matching the user's
    search criteria. The search is performed for the USER - you are helping them find
    products they are interested in.

    Features:
    - Keyword-based search (must be in Vietnamese)
    - Category filtering
    - Price range filtering
    - Sorting by various attributes (popular, price, name)
    - Pagination support
    - Returns product details including SKU, name, price, stock status, URL

    Requirements:
    - store_id and base_url must be set in session state
    - Search keywords MUST be in Vietnamese
    - Returns up to 12 products per page

    Important:
    - These are products the USER is searching for, NOT products you are searching for
    - SKU values returned must NEVER be fabricated - only use what the API returns
    - Use returned SKU values for cart operations (add, update, remove)
    """

    def __init__(
            self,
            *,
            is_long_running: bool = False,
            custom_metadata: Optional[dict[str, Any]] = None,
    ):
        self.name = 'search_products_async'
        self.description = "Call Magento API to get product"
        super().__init__(name=self.name, description=self.description,
                         is_long_running=is_long_running, custom_metadata=custom_metadata)

    @override
    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description="Search for products in the MM Mega Market Vietnam catalog on behalf of the USER. "
                        "This tool queries the Magento e-commerce API to find products matching the user's search criteria. "
                        "The search is performed for the USER - you are helping them find products they are interested in.",
            parameters_json_schema=ProductSearchInput.model_json_schema(),
        )

    @override
    async def run_async(self, *, args: dict[str, Any], tool_context: ToolContext) -> Any:
        # Validate the input args
        if 'category' in args:
            args['category'] = [cate.lower() for cate in args['category']]
        try:
            args = ProductSearchInput.model_validate(args)
            print(args)
        except Exception as e:
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "message": f"Invalid args input: {str(e)}",
                "code": "INVALID_INPUT"
            }
        if isinstance(tool_context, InvocationContext):
            state = tool_context.session.state
        else:
            state = tool_context.state

        magento_session_data = state.get('state', {}).get("magento_session_data", {})
        store_id = magento_session_data.get('store_id', DEFAULT_MMVN_STORE_ID)
        base_url = magento_session_data.get('base_url', DEFAULT_MMVN_STORE_URL).rstrip('/')
        # graphql_fields_to_get = '\n'.join(prod_field.graphql_query for prod_field in args.fields_to_get)
        graphql_fields_to_get = '\n'.join(prod_field.graphql_query for prod_field in DEFAULT_FIELDS_TO_GET)
        fragment = f"""
            fragment ProductFragment on ProductInterface {{
                {graphql_fields_to_get}
            }}
        """
        # Check if we're on production or test environment
        is_production = 'online.mmvietnam.com' in base_url

        if is_production:
            graphql_query = """
                query ProductSearch(
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
            """ + fragment
        else:
            graphql_query = """
                query ProductSearch(
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
            """ + fragment
        variables = {
            "currentPage": args.page,
            "pageSize": 12,
            "filters": {},
            "inputText": args.keyword_in_vietnamese, 
        }
        if not is_production:
            variables["asmUid"] = ""
            variables["phoneNumber"] = ""
        if args.category:
            variables["filters"]["category_uid"] = {
                "in": [
                    cate.name for cate in args.category
                ]
            }
        # todo: remove this check once price filter is pushed to online.mmvietnam.com
        if not is_production:
            if args.price_min is not None:
                variables["filters"]["price"] = {
                    "from": str(args.price_min)
                }
            if args.price_max is not None:
                if "price" not in variables["filters"]:
                    variables["filters"]["price"] = {}
                variables["filters"]["price"]["to"] = str(args.price_max)

        if not args.sort_by:
            args.sort_by = MagentoProductSortOptions.relevance
        if not args.sort_direction:
            if args.sort_by in [MagentoProductSortOptions.ecom_name, MagentoProductSortOptions.mm_sale_price_include_vat]:
                args.sort_direction = MagentoProductSortDirection.ASC
            else:
                args.sort_direction = MagentoProductSortDirection.DESC
        variables["sort"] = {
            args.sort_by.name: args.sort_direction.value
        }

        # make the request
        try:
            res = await make_graphql_request_async(graphql_query, variables, base_url, store_id)
            # res = await response.json()
            if res is None or not isinstance(res, dict):
                return {
                    "success": False,
                    "message": f"Search result of '{args.keyword}' returned no response",
                    "code": "NO_RESPONSE"
                }

            if "errors" in res:
                logger.error(f"[ProductSearchTool] GraphQL errors for keyword '{args.keyword}': {res['errors']}")
                return {
                    "success": False,
                    "message": f"Search result of '{args.keyword}' returned GraphQL errors: {res['errors']}",
                    "code": "GRAPHQL_ERROR"
                }

            data = res.get("data")
            if not data or not isinstance(data, dict):
                logger.error(f"[ProductSearchTool] API returned invalid data. Response: {res}")
                logger.error(f"[ProductSearchTool] Request URL: {base_url}/graphql, Store ID: {store_id}")
                logger.error(f"[ProductSearchTool] Query variables: {variables}")
                return {
                    "success": False,
                    "message": f"Search result of '{args.keyword}' returned API error: empty data",
                    "code": "INVALID_RESPONSE"
                }

            products = data.get("products")
            if not products or not isinstance(products, dict):
                logger.error(f"[ProductSearchTool] API returned no products field. Data: {data}")
                invalid_response = {
                    "success": False,
                    "message": f"Search result of '{args.keyword}' returned API error: no products field",
                    "code": "INVALID_RESPONSE",
                    "data": [],
                    "total_count": 0
                }
                save_search_result_to_session_state(args, invalid_response, tool_context)
                return invalid_response

            items = products.get("items")
            if not items:
                # Save NO_PRODUCTS result to session state so filter agent can see fallback searches were attempted
                no_products_response = {
                    "success": False,
                    "message": f"Search result of '{args.keyword}' at page {args.page} and limit {12} has no products.",
                    "instruction_for_agent": "Sometime a page can have no product. If this is the first page, "
                                             "check the next pages or increase the page size.",
                    "code": "NO_PRODUCTS",
                    "data": [],  # Empty data array for consistency
                    "total_count": 0
                }
                save_search_result_to_session_state(args, no_products_response, tool_context)
                return no_products_response

        except requests.RequestException as e:
            http_error_response = {
                "success": False,
                "message": f"Search result of '{args.keyword}' returned HTTP error: {str(e)}",
                "code": "HTTP_ERROR",
                "data": [],
                "total_count": 0
            }
            save_search_result_to_session_state(args, http_error_response, tool_context)
            return http_error_response

        # format the data returned to AI.
        processed_prod_data = process_product_search_data_optimized(items)
        save_search_result_to_session_state(args, processed_prod_data, tool_context)
        return {
            "success": True,
            "data": processed_prod_data,
            "total_count": products.get("total_count", 0),
            # "message": f"Found {len(items)} products"
        }


class ProductMultipleSearchTool(BaseTool):
    """
    Search for multiple different products simultaneously on behalf of the USER.

    This tool performs multiple product searches in parallel using async API calls.
    Each search in the query list is executed independently and results are aggregated.
    The searches are performed for the USER - you are helping them find multiple
    products they are interested in.

    Use Cases:
    - User asks for products from multiple categories (e.g., "fruits and vegetables")
    - User wants to compare similar products with different keywords
    - Semantic search generates multiple specific product queries

    Features:
    - Executes multiple searches concurrently for better performance
    - Each query can have different keywords, categories, filters, and pagination
    - Returns array of results corresponding to each query

    Important:
    - These are products the USER is searching for, NOT products you are searching for
    - All search keywords MUST be in Vietnamese
    - SKU values returned must NEVER be fabricated - only use what the API returns
    - Results maintain order corresponding to input query_list
    """

    def __init__(
            self,
            *,
            is_long_running: bool = False,
            custom_metadata: Optional[dict[str, Any]] = None,
    ):
        self.name = 'products_search_multiple_async'
        self.description = "Search multiple different products with many async API calls"
        super().__init__(name=self.name, description=self.description,
                         is_long_running=is_long_running, custom_metadata=custom_metadata)

    @override
    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description="Call multiple different Magento API to get products with different queries.",
            parameters_json_schema=ProductMultipleSearchInput.model_json_schema(),
        )

    @override
    async def run_async(self, *, args: dict[str, Any], tool_context: ToolContext) -> Any:
        try:
            ProductMultipleSearchInput.model_validate(args)
        except Exception as e:
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "message": f"Invalid input: {str(e)}",
                "code": "INVALID_INPUT"
            }
        result = []
        for query in args["query_list"]:
            single_search_tool = ProductSearchTool()
            query_result = await single_search_tool.run_async(args=query, tool_context=tool_context)
            result.append(query_result)
        return result


async def semantic_search_products(query: str):
    """
    Perform semantic search to find products based on natural language query.

    This function uses AI-powered semantic search to understand the USER's intent
    and find relevant products. The query is for the USER - you are helping them
    find products through intelligent search.

    Args:
        query: Natural language search query from the user

    Returns:
        SemanticAiSearchResult: Semantic search results with relevant products

    Important:
        - This searches for products the USER wants, NOT products you want
        - Query should be in the user's natural language
        - Results are AI-enhanced for better relevance
    """
    semantic_queries: SemanticAiSearchResult = await do_semantic_search_async(SemanticAiSearchQuery(text=query))

def get_all_categories() -> list[str]:
    """
    Get all available product categories in MM Mega Market Vietnam catalog.

    This helper function returns a complete list of main product categories
    available in the e-commerce system. These categories can be used to filter
    product searches.

    Returns:
        list[str]: List of all available category names in Vietnamese

    Important:
        - Categories are for filtering the USER's product searches
        - Category names are in Vietnamese
        - Use these categories with ProductSearchTool for filtered searches
    """
    return list(MMVN_MAIN_CATEGORY_MAP.values())
class ProductSearchByFullnameTool(BaseTool):
    """
    Search for products by exact full product name using GraphQL 'search' field.
    Use when the user provides a complete or nearly complete product name.
    """

    def __init__(
        self,
        *,
        is_long_running: bool = False,
        custom_metadata: Optional[dict[str, Any]] = None,
    ):
        self.name = "search_products_by_fullname_async"
        self.description = "Search for products by their full name using GraphQL search field"
        super().__init__(
            name=self.name,
            description=self.description,
            is_long_running=is_long_running,
            custom_metadata=custom_metadata,
        )

    @override
    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description="""
    Search for products by full name using GraphQL 'search' parameter.
    Use this when the user provides an exact product name like 'Nước ép dưa hấu, 300ml'.
            """,
            parameters_json_schema=ProductSearchInput.model_json_schema(),
        )

    @override
    async def run_async(self, *, args: dict[str, Any], tool_context: ToolContext) -> Any:
        # Validate input
        try:
            args = ProductSearchInput.model_validate(args)
        except Exception as e:
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "message": f"Invalid args input: {str(e)}",
                "code": "INVALID_INPUT",
            }

        magento_session_data = tool_context.state.get("state", {}).get("magento_session_data", {})
        store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")

        # graphql_fields_to_get = "\n".join(prod_field.graphql_query for prod_field in args.fields_to_get)
        graphql_fields_to_get = "\n".join(prod_field.graphql_query for prod_field in DEFAULT_FIELDS_TO_GET)
        fragment = f"""
            fragment ProductFragment on ProductInterface {{
                {graphql_fields_to_get}
            }}
        """
        graphql_query = """
            query ProductSearch(
                $currentPage: Int = 1
                $pageSize: Int = 7
                $inputText: String!
            ) {
                products(
                    currentPage: $currentPage
                    pageSize: $pageSize
                    search: $inputText
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

        variables = {
            "currentPage": args.page,
            "pageSize": 7,
            "inputText": args.keyword_in_vietnamese,
        }

        # Gọi request
        try:
            res = await make_graphql_request_async(graphql_query, variables, base_url, store_id)
            if res is None:
                return {
                    "success": False,
                    "message": f"Search by full name '{args.keyword}' returned no response",
                    "code": "NO_RESPONSE"
                }
            if not res.get("data"):
                return {
                    "success": False,
                    "message": f"Search by full name '{args.keyword}' returned empty data",
                    "code": "INVALID_RESPONSE",
                }
            elif not res.get("data", {}).get("products", {}).get("items"):
                return {
                    "success": False,
                    "message": f"No products found for '{args.keyword}'",
                    "code": "NO_PRODUCTS",
                }

        except requests.RequestException as e:
            return {
                "success": False,
                "message": f"GraphQL HTTP error: {str(e)}",
                "code": "HTTP_ERROR",
            }

        data = res["data"]["products"]["items"]
        processed_prod_data = process_product_search_data_optimized(data)
        save_search_result_to_session_state(args, processed_prod_data, tool_context)

        return {
            "success": True,
            "data": processed_prod_data,
            "total_count": res["data"]["products"]["total_count"],
        }

if __name__ == '__main__':
    from types import SimpleNamespace

    dummy_tool_context = SimpleNamespace(state={})
    # noinspection PyTypeChecker
    res = asyncio.run(ProductSearchTool().run_async(args={
        "query": "nấm",
        "category": [MagentoMainCategories('thực phẩm tươi sống'.lower()).value],
        # "price_max": 20000
    }, tool_context=dummy_tool_context))
    print(json.dumps(res, indent=4, ensure_ascii=False))
