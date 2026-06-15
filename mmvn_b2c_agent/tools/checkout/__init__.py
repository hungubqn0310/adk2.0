"""
Checkout tools for single-popup checkout flow with additional info via chat.

NEW TOOLS:
- ShowCheckoutStepTool: Show checkout popup (single popup with all fields)
- validate_delivery_time: Check if delivery time is still available
- set_delivery_comment: Set delivery comment via chat
- set_call_before_delivery: Enable/disable call before delivery
- set_mcard: Set MCard number
- set_vat_invoice: Set VAT invoice info (multi-turn)
- show_payment_methods: Display payment methods with links
- get_my_checkout_info: Get saved checkout info (email, phone, VAT, address, etc.)
"""

from google.adk.tools import FunctionTool, LongRunningFunctionTool

from .show_checkout_step import ShowCheckoutStepTool
from .validate_delivery_time import validate_delivery_time
from .set_delivery_comment import set_delivery_comment
from .set_call_before_delivery import set_call_before_delivery
from .set_mcard import set_mcard
from .set_vat_invoice import set_vat_invoice
from .show_payment_methods import show_payment_methods
from .get_checkout_info import get_my_checkout_info

# Wrap async functions as FunctionTools
ValidateDeliveryTimeTool = FunctionTool(validate_delivery_time)
SetDeliveryCommentTool = FunctionTool(set_delivery_comment)
SetCallBeforeDeliveryTool = FunctionTool(set_call_before_delivery)
SetMCardTool = FunctionTool(set_mcard)
SetVATInvoiceTool = FunctionTool(set_vat_invoice)
# Use LongRunningFunctionTool to get invocationId and wait for place order completion
ShowPaymentMethodsTool = LongRunningFunctionTool(show_payment_methods)
GetMyCheckoutInfoTool = FunctionTool(get_my_checkout_info)

__all__ = [
    # Legacy tool (refactored to single popup)
    "ShowCheckoutStepTool",

    # New tools
    "ValidateDeliveryTimeTool",
    "SetDeliveryCommentTool",
    "SetCallBeforeDeliveryTool",
    "SetMCardTool",
    "SetVATInvoiceTool",
    "ShowPaymentMethodsTool",
    "GetMyCheckoutInfoTool",
]
