import asyncio
import json
import logging
import traceback
import requests
from typing_extensions import override
from typing import Optional, Any
from google.adk.tools import ToolContext, BaseTool
from google.genai import types
from pydantic import BaseModel, model_validator
from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.shared.schema import MagentoProductFields
from mmvn_b2c_agent.tools.cng.common import process_product_search_data, save_search_result_to_session_state
from mmvn_b2c_agent.tools.utils import make_graphql_request_async
from pydantic import Field
logger = logging.getLogger(__name__)
DEFAULT_FIELDS_TO_GET = [
    MagentoProductFields.SKU,
    MagentoProductFields.NAME,
    MagentoProductFields.STOCK_STATUS,
    MagentoProductFields.PRICE_WITH_DISCOUNT,  # Price with discount info for promotions
    MagentoProductFields.PRODUCT_TYPE,  # mm_product_type: 'F' = Fresh (step 0.5), 'N' = Normal (step 1)
    MagentoProductFields.NEED_AGE_VERIFICATION,
    MagentoProductFields.PROMO_INFO,  # Promotion information
    # MagentoProductFields.URL,
    # MagentoProductFields.DESCRIPTION,
]


class ProductDetailInput(BaseModel):
    sku: str = Field(
        description="sku of product, SKU format is two integers with underscore (e.g., `441976_24419765`)."
    )
    # fields_to_get: list[MagentoProductFields] = DEFAULT_FIELDS_TO_GET

    # check the fields to get and automatically add 'is_alcohol' if not present
    # @model_validator(mode="after")
    # def check_fields_to_get(self):
    #     if MagentoProductFields.NEED_AGE_VERIFICATION in self.fields_to_get:
    #         return self
    #     self.fields_to_get.append(MagentoProductFields.NEED_AGE_VERIFICATION)
    #     return self


class ProductDetailTool(BaseTool):
    """
    Get detailed information about a specific product for the USER.

    This tool retrieves comprehensive product details from the Magento e-commerce API
    using the product's SKU. The product information is retrieved for the USER - you
    are helping them learn more about a product they're interested in.

    Features:
    - Fetch detailed product information by SKU
    - Customizable fields (SKU, name, price, description, stock status, URL, etc.)
    - Returns formatted product data ready for display to user

    Default Fields Retrieved:
    - SKU: Product identifier
    - Name: Product name
    - Stock Status: In stock / Out of stock
    - URL: Product page URL
    - Price: Current price with currency (including discount info if available)
    - Description: Detailed product description
    - Promotion Info: Active promotions, deals, and special offers

    Requirements:
    - store_id and base_url must be set in session state
    - Valid SKU format: two integers with underscore (e.g., "441976_24419765")

    Important:
    - This retrieves details for a product the USER is interested in
    - SKU must come from product search results or user input - NEVER fabricate
    - Use this tool when user asks for more details about a specific product
    - Return information helps user make informed purchase decisions
    """

    def __init__(
            self,
            *,
            is_long_running: bool = False,
            custom_metadata: Optional[dict[str, Any]] = None,
    ):
        self.name = 'get_product_detail_async'
        self.description = ("Call Magento API to get a product's detailed information from its sku. "
                            "By default, will get sku, name, stock_status, url, price (with discount), "
                            "description and promotion info.")
        super().__init__(name=self.name, description=self.description,
                         is_long_running=is_long_running, custom_metadata=custom_metadata)

    @override
    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description="Call Magento API to get a product's detailed information",
            parameters_json_schema=ProductDetailInput.model_json_schema(),
        )

    @override
    async def run_async(self, *, args: dict[str, Any], tool_context: ToolContext) -> Any:
        try:
            try:
                args = ProductDetailInput.model_validate(args)
            except Exception as e:
                logger.error(traceback.format_exc())
                return {
                    "success": False,
                    "message": f"Invalid arguments: {str(e)}",
                    "instruction_for_agent": "Refer to the tool description and "
                                             "ensure the arguments are correct and valid.",
                    "code": "INVALID_ARGUMENTS"
                }
            sku = args.sku
            if not sku:
                return {
                    "success": False,
                    "message": "SKU is required",
                    "code": "MISSING_SKU"
                }
            magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
            store_id = magento_session_data.get('store_id', DEFAULT_MMVN_STORE_ID)
            base_url = magento_session_data.get('base_url', DEFAULT_MMVN_STORE_URL).rstrip('/')
            # graphql_fields_to_get = '\n'.join(prod_field.graphql_query for prod_field in args.fields_to_get)
            graphql_fields_to_get = '\n'.join(prod_field.graphql_query for prod_field in DEFAULT_FIELDS_TO_GET)
            fragment = f"""
                fragment ProductFragment on ProductInterface {{
                    {graphql_fields_to_get}
                }}
            """
            graphql_query = """
                query ProductSearch(
                    $filters: ProductAttributeFilterInput!
                    $asmUid: String
                    $phoneNumber: String
                ) {
                    products(
                        filter: $filters
                        asm_uid: $asmUid
                        phone_number: $phoneNumber
                    ) {
                        items {
                            ...ProductFragment
                        }
                    }
                }
            """ + fragment
            variables = {
                "filters": {
                    "sku": {
                        "eq": sku
                    }
                },
                "asmUid": "",
                "phoneNumber": ""
            }

            # make the request
            try:
                res = await make_graphql_request_async(graphql_query, variables, base_url, store_id)
                # res = await response.json()
                if not res or not res.get("data"):
                    logger.error(f"Cannot find product with sku {sku}. Full response:\n{json.dumps(res, indent=4)}\n\n"
                                 f"Query:\n{graphql_query}\n\nVariables:\n{json.dumps(variables)}")
                    return {
                        "success": False,
                        "message": "API error: empty data",
                        "code": "INVALID_RESPONSE"
                    }
                elif not res.get("data").get("products", {}).get("items"):
                    logger.error(f"Cannot find product with sku {sku}. Full response:\n{json.dumps(res, indent=4)}\n\n"
                                 f"Query:\n{graphql_query}\n\nVariables:\n{json.dumps(variables)}")
                    return {
                        "success": False,
                        "message": f"Cannot find product with sku {sku}.",
                        "instruction_for_agent": "Check if the SKU is correct. "
                                                 "A normal sku should be in this format: 441976_24419765. "
                                                 "If the SKU is correctly formatted, then the agent might "
                                                 "need to do a product search first. In this case, the product"
                                                 "search result message should inform the user whether or not "
                                                 "the product is found, then smoothly continue the task/conversation.",
                        "code": "NO_PRODUCTS"
                    }

            except requests.RequestException as e:
                logger.error(traceback.format_exc())
                return {
                    "success": False,
                    "message": f"HTTP error: {str(e)}",
                    "code": "HTTP_ERROR"
                }

            # format the data returned to AI.
            data = res["data"]["products"]["items"]
            processed_prod_data = process_product_search_data(data, base_url)
            save_search_result_to_session_state(args, processed_prod_data, tool_context)
            return {
                "success": True,
                "data": processed_prod_data,
                # "message": f"Found {len(data)} products"
            }
        except Exception as e:
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "message": f"Unexpected error: {str(e)}",
                "code": "UNEXPECTED_ERROR"
            }


if __name__ == '__main__':
    from types import SimpleNamespace

    dummy_tool_context = SimpleNamespace(state={})
    # noinspection PyTypeChecker
    res_data = asyncio.run(ProductDetailTool().run_async(args={
        "sku": "441976_24419765",
        # "fields_to_get": [
        #     "sku",
        #     "name"
        # ]
    }, tool_context=dummy_tool_context))
    print(json.dumps(res_data, indent=4, ensure_ascii=False))
