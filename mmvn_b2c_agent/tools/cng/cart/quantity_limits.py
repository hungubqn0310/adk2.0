import logging
import traceback
from typing import Any, Optional
from google.adk.tools import ToolContext, BaseTool
from google.genai import types
from pydantic import BaseModel, Field
from typing_extensions import override
from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.tools.utils import make_graphql_request_async

logger = logging.getLogger(__name__)


class ProductQuantityLimitsInput(BaseModel):
    """Input schema for getting product quantity limits"""
    skus: list[str] = Field(
        description="List of product SKUs to check quantity limits for."
    )


class ProductQuantityLimitsTool(BaseTool):
    """
    Get min/max quantity limits for products in the user's cart.

    This tool retrieves quantity restrictions for products based on:
    - Current quantities in cart
    - Daily purchase limits per product
    - Store-specific quantity rules

    Used to validate if user can add more items to cart before attempting to add.

    Returns:
    - max_qty: Maximum quantity allowed (considering current cart + daily limit)
    - min_qty: Minimum quantity required per purchase
    """

    def __init__(
        self,
        *,
        is_long_running: bool = False,
        custom_metadata: Optional[dict[str, Any]] = None,
    ):
        self.name = "get_product_quantity_limits_async"
        self.description = "Get min/max quantity limits for products to validate cart additions"
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
            description="Get product quantity limits (min/max qty allowed) for cart validation",
            parameters_json_schema=ProductQuantityLimitsInput.model_json_schema(),
        )

    @override
    async def run_async(self, *, args: dict[str, Any], tool_context: ToolContext) -> Any:
        try:
            # Validate input
            try:
                args = ProductQuantityLimitsInput.model_validate(args)
            except Exception as e:
                logger.error(traceback.format_exc())
                return {
                    "success": False,
                    "message": f"Invalid arguments: {str(e)}",
                    "code": "INVALID_ARGUMENTS"
                }

            # Get session data - safely handle None state
            tool_state = tool_context.state if tool_context.state is not None else {}
            magento_session_data = tool_state.get("state", {}).get("magento_session_data", {})
            store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
            base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
            magento_cart_id = magento_session_data.get("magento_cart_id") or ""
            magento_cart_id = magento_cart_id.strip('"')

            if not magento_cart_id:
                return {
                    "success": False,
                    "message": "Cart ID not found in session",
                    "code": "MISSING_CART_ID"
                }

            # Build GraphQL query
            graphql_query = """
                query GetProductQuantityLimits($input: ProductQuantityLimitsInput!) {
                    getProductQuantityLimits(input: $input) {
                        items {
                            sku
                            max_qty
                            min_qty
                        }
                    }
                }
            """

            variables = {
                "input": {
                    "skus": args.skus,
                    "cart_id": magento_cart_id,
                }
            }

            # Make GraphQL request
            try:
                res = await make_graphql_request_async(graphql_query, variables, base_url, store_id)

                if res is None:
                    return {
                        "success": False,
                        "message": "No response from API",
                        "code": "NO_RESPONSE"
                    }

                if not res.get("data"):
                    logger.error(f"Empty data in response: {res}")
                    return {
                        "success": False,
                        "message": "API returned empty data",
                        "code": "INVALID_RESPONSE",
                    }

                limits_data = res.get("data", {}).get("getProductQuantityLimits", {})
                if not limits_data or not limits_data.get("items"):
                    return {
                        "success": False,
                        "message": "No quantity limits found for the provided SKUs",
                        "code": "NO_LIMITS_FOUND",
                    }

                # Format response
                items = limits_data.get("items", [])
                limits_dict = {}
                for item in items:
                    sku = item.get("sku")
                    if sku:
                        limits_dict[sku] = {
                            "max_qty": item.get("max_qty"),
                            "min_qty": item.get("min_qty"),
                        }

                return {
                    "success": True,
                    "data": limits_dict,
                    "message": f"Found quantity limits for {len(limits_dict)} products"
                }

            except Exception as e:
                logger.error(traceback.format_exc())
                return {
                    "success": False,
                    "message": f"GraphQL request failed: {str(e)}",
                    "code": "HTTP_ERROR",
                }

        except Exception as e:
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "message": f"Unexpected error: {str(e)}",
                "code": "UNEXPECTED_ERROR"
            }


if __name__ == '__main__':
    import asyncio
    from types import SimpleNamespace

    dummy_tool_context = SimpleNamespace(state={
        "state": {
            "magento_session_data": {
                "store_id": "10010",
                "base_url": "https://b2c-mmpro.izysync.com/",
                "magento_cart_id": "test_cart_id_123"
            }
        }
    })

    res_data = asyncio.run(ProductQuantityLimitsTool().run_async(
        args={"skus": ["350508_23505087", "222643_22226433"]},
        tool_context=dummy_tool_context
    ))
    import json
    print(json.dumps(res_data, indent=4, ensure_ascii=False))
