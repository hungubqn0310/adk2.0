import mmvn_b2c_agent.agents.cng.schema as cng_schema
from typing import Any, Optional
from google.adk.tools import BaseTool, ToolContext
from google.genai import types
from typing_extensions import override
from mmvn_b2c_agent.shared.constants import ORDER_STATUS_GROUPS

# MIME types that indicate file uploads
FILE_UPLOAD_MIME_TYPES = {
    # PDF
    'application/pdf',
    # Images
    'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/bmp',
    # Documents
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    # Spreadsheets
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    # Text
    'text/plain', 'text/csv',
}


def detect_file_upload(user_content: Optional[types.Content]) -> bool:
    """
    Detect if user uploaded a file (PDF, image, document, etc.) from user_content.

    Args:
        user_content: The user content from tool_context.user_content

    Returns:
        True if file upload detected, False otherwise
    """
    if not user_content or not user_content.parts:
        return False

    for part in user_content.parts:
        # Check inline_data (base64 encoded file)
        if hasattr(part, 'inline_data') and part.inline_data:
            mime_type = getattr(part.inline_data, 'mime_type', None)
            if mime_type and mime_type in FILE_UPLOAD_MIME_TYPES:
                return True

        # Check file_data (file reference)
        if hasattr(part, 'file_data') and part.file_data:
            mime_type = getattr(part.file_data, 'mime_type', None)
            if mime_type and mime_type in FILE_UPLOAD_MIME_TYPES:
                return True

    return False


class CngSetResponse(BaseTool):
    def __init__(self):
        self.output_schema = cng_schema.CngProductSearchAiResponse
        self.name = 'set_model_response'
        self.description = "Format and output the response in the specified structured format."
        super().__init__(name=self.name,
                         description=self.description,
                         is_long_running=False)

    @override
    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema=self.output_schema.model_json_schema(),
        )

    @override
    async def run_async(
            self, *, args: dict[str, Any], tool_context: ToolContext
    ) -> dict[str, Any]:
        """Process the model's response and return the validated dict.

        Args:
          args: The structured response data matching the output schema.
          tool_context: Tool execution context.

        Returns:
          The validated response as dict.
        """
        # Agent decides shipping context based on conversation flow, not message keywords
        is_shipping = args.get('_is_shipping', False)

        if is_shipping:
            args.pop('_is_shipping', None)
            result = cng_schema.CngProductSearchAiResponse.model_validate(args)

            # Check if there's order data in state to determine if we should show checkout button
            state_dict = tool_context.state.get('state', {}) if hasattr(tool_context, 'state') and tool_context.state else {}
            order_state_wrapper = state_dict.get('last_order_result')

            # Only show "Thanh toán ngay" button if:
            # 1. No order in context (user asking about shipping in general, not for a specific order)
            # 2. OR the order has status "pending_payment" (needs payment)
            show_checkout_button = True
            if order_state_wrapper:
                # Extract the actual order data from the wrapper
                order_data = order_state_wrapper.get('data') if isinstance(order_state_wrapper, dict) else None

                if order_data:
                    # Get order status from order data
                    order_status = None
                    if isinstance(order_data, dict):
                        order_status = order_data.get('status_code')

                    # Only show checkout button if order status is "pending_payment"
                    pending_payment_statuses = ORDER_STATUS_GROUPS.get("Chờ thanh toán", [])
                    if order_status and order_status not in pending_payment_statuses:
                        show_checkout_button = False

            return {
                'language': result.language,
                'display_mode': result.display_mode.value,
                'message': result.message,
                'product_skus': result.product_skus,
                'show_cart_detail_cta_button': False,
                'show_proceed_to_checkout_cta_button': show_checkout_button,
                # Order tracking CTA buttons
                'show_order_management_cta_button': False,
                'show_view_order_details_cta_button': False,
                'show_reorder_cta_button': False,
                'show_signin_for_order_cta_button': False,
                # Checkout popup fields
                'auto_open_checkout_popup': False,
                'cart_data': {},
                'product_data': [],
            }
        
        # Cho product search: dùng format_output bình thường
        validated_response = cng_schema.CngProductSearchAiResponse.model_validate(args)
        final_response = await cng_schema.CngProductSearchAiResponseFinal.format_output(
            validated_response, tool_context
        )

        # Get response dict
        result = final_response.model_dump()

        # Code-level detection: Override is_file_upload_response if file detected
        user_content = getattr(tool_context, 'user_content', None)
        if detect_file_upload(user_content):
            result['is_file_upload_response'] = True

        return result