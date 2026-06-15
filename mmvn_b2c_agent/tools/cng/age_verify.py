from typing import Optional, Any

from google.adk.tools import ToolContext


def age_verify(
        # steps_need_age_verification: str,
        tool_context: Optional[ToolContext] = None
) -> dict[str, Any]:
    """
    Some products may have age restrictions. If so, this tool is needed to verify the user's age.
    The agent should call this tool ONLY when other tool fails due to age restriction(code AGE_VERIFICATION_REQUIRED) and NEVER make assumptions based on product details or chat context.
    """
    # :param steps_need_age_verification: str, logs the tool calls that was failed due to age restriction. Example: "add_to_cart with sku 123_456", "view_product_details with sku 123_456"
    # """
    try:
        if not tool_context or not tool_context.state or 'age_verified' not in tool_context.state:
            age_verified = False
        else:
            age_verified = tool_context.state['age_verified']

        if age_verified:
            return {
                "status": "success",
                "message": "User's age has already been verified.",
                "code": "AGE_ALREADY_VERIFIED",
                "instruction_for_agent": "You can proceed with the user's request regarding age-restricted products. DO NOT mention this age verification step to the user.",
            }
        else:
            return {
                "status": "pending",
                "message": "User's action requires age verification.",
                "instruction_for_agent": "An age verification popup has been shown to the user. Inform the user that they need to complete the age verification to proceed."
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"An error occurred during age verification. {str(e)}",
            "code": "AGE_VERIFICATION_UNEXPECTED_ERROR",
            "instruction_for_agent": "Inform the user 'Hiện em không thể xác minh độ tuổi của anh/chị được, anh/chị thử lại sau ít phút nhé.'",
        }