from typing import Optional, Any
from pydantic import BaseModel, Field


class ProductSearchInputSchema(BaseModel):
    query: list[str]
    limit: Optional[int] = 10
    # included_fields: list[str] = ["name", "sku", "in_stock", "description", "price"]


class SuggestedAction(BaseModel):
    display_text: str = Field(
        description="The shortened text to display for the suggested action button. "
                    "Formatted in Markdown. Must only be one line. "
                    "Example: 'Add product *A* to cart.'"
    )
    message_for_llm: str = Field(
        description="The actual(full) message to send to the LLM if the user clicks the button."
                    "Example: 'Add product A(SKU: prod_a_001) to cart.'"
    )


class ProductSearchOutputSchema(BaseModel):
    user_language: str = Field(
        description="The language the user actually TYPED in their question (ISO code, e.g. 'vi', 'en'). "
                    "IMPORTANT: determine this ONLY from text the user typed. If the user only uploaded an image/file and typed no text, set 'vi' (Vietnamese) — do NOT infer the language from text printed on the image/product packaging (e.g. English words like 'Ensure', 'Vanilla' on a product photo are NOT the user's language). "
                    "The response message MUST be in this language."
    )
    message: str = Field(
        description="A short, friendly, engaging and helpful message, informing the user of the search result, written in `user_language` (default Vietnamese). "
                    "Vietnamese example: 'Dạ, em xin giới thiệu các sản phẩm sau ạ:'. English example (only when the user typed English): 'Here are some products matched your description:'.\n"
                    "DO NOT show product information, list product,.. in this `message` field, that is the job of `product_skus` field."
    )
    product_skus: list[str] = Field(
        description="List the relevant product sku for the front end to show to user. ALL PRODUCT INFORMATION MUST GO INTO THIS FIELD."
                    ""
    )
    exact_match_found: bool = Field(
        default=True,
        description="Set to False if the search results are substitute/alternative products because the exact item requested by the user does not exist in the store. "
                    "Example: user asks for 'thịt chó' but only dog food products are available — set False. "
                    "Set to True when the products shown directly match what the user asked for."
    )

    # # CTA buttons
    # show_cart_detail_cta_button: bool = Field(default=False,
    #                                           description="Whether to show the cart detail button. Do NOT show if the cart is empty.")
    # show_proceed_to_checkout_cta_button: bool = Field(default=False,
    #                                                   description="Whether to show the proceed to checkout button. Do NOT show if the cart is empty.")
    # Use list to allow multiple buttons to be suggested at once.
    # cta_button_choice: Optional[list[CtaButtonChoice]] = Field(
    #     default=[],
    #     description="If the query is related to cart or checkout, suggest one or more of the following buttons."
    #                 "Do not suggest the show_cart_button if the cart is empty."
    # )
