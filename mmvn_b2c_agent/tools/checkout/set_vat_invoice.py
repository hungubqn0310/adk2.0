"""
Set VAT Invoice Tool

Set thông tin hóa đơn VAT cho cart.
API: setVatInformationOnCart mutation

Usage - Multi-turn conversation:
1. User: "em muốn xuất hóa đơn VAT"
   → Agent: "Dạ, để xuất hóa đơn VAT, anh/chị vui lòng cho em biết:
              • Tên công ty
              • Mã số thuế
              • Địa chỉ công ty"

2. User: "Công ty ABC, MST 0123456789, địa chỉ Hà Nội"
   → Agent calls set_vat_invoice(company_name="Công ty ABC", ...)
   → Agent: "Dạ, em đã lưu thông tin hóa đơn VAT ạ."
"""

import logging
from typing import Optional, Dict, Any
from google.adk.tools import ToolContext

from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.tools.utils import make_graphql_request_async

logger = logging.getLogger(__name__)


async def set_vat_invoice(
    company_name: Optional[str] = None,
    company_vat_number: Optional[str] = None,
    company_address: Optional[str] = None,
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """
    Set thông tin hóa đơn VAT cho cart.

    CRITICAL: Multi-turn conversation required! Do NOT try to extract all 3 fields from one user message.
    You MUST ask user for each field separately:
    1. First, ask for company name
    2. Then, ask for tax code (mã số thuế)
    3. Finally, ask for company address
    Only call this tool when you have collected ALL 3 pieces of information from user.

    Args:
        company_name: Tên công ty (bắt buộc) - Ask user: "Anh/chị cho em biết tên công ty ạ?"
        company_vat_number: Mã số thuế (bắt buộc) - Ask user: "Mã số thuế của công ty là gì ạ?"
        company_address: Địa chỉ công ty (bắt buộc) - Ask user: "Địa chỉ công ty là gì ạ?"
        tool_context: Tool context for session state access

    Returns:
        dict: {
            "success": bool,
            "message": str,
            "instruction_for_agent": str,
            "data": {  // Nếu thành công
                "customer_vat_id": int,
                "company_name": str,
                "company_vat_number": str,
                "company_address": str
            }
        }

    Multi-turn Logic:
        - Nếu thiếu thông tin → return instruction yêu cầu user cung cấp
        - Nếu đủ thông tin → call API
    """
    try:
        if not tool_context:
            return {
                "success": False,
                "message": "Tool context is missing",
                "instruction_for_agent": "Tell user: 'Em không thể cập nhật thông tin hóa đơn lúc này, anh/chị thử lại sau ạ.'",
                "code": "MISSING_TOOL_CONTEXT"
            }

        # Check if all required info is provided
        missing_fields = []
        if not company_name or not company_name.strip():
            missing_fields.append("Tên công ty")
        if not company_vat_number or not company_vat_number.strip():
            missing_fields.append("Mã số thuế")
        if not company_address or not company_address.strip():
            missing_fields.append("Địa chỉ công ty")

        if missing_fields:
            # Return instruction to ask for missing info
            missing_str = ", ".join(missing_fields)
            return {
                "success": False,
                "message": f"Missing required fields: {missing_str}",
                "instruction_for_agent": f"Tell user: 'Dạ, để xuất hóa đơn VAT, anh/chị vui lòng cho em biết thêm:\n• {chr(10).join('• ' + field for field in missing_fields)}'",
                "code": "MISSING_VAT_INFO"
            }

        # Trim inputs
        company_name = company_name.strip()
        company_vat_number = company_vat_number.strip()
        company_address = company_address.strip()

        # Get session data
        magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
        store_id = magento_session_data.get("store_id", DEFAULT_MMVN_STORE_ID)
        base_url = magento_session_data.get("base_url", DEFAULT_MMVN_STORE_URL).rstrip("/")
        signin_token = (magento_session_data.get("signin_token") or "").strip('"')
        magento_cart_id = (magento_session_data.get("magento_cart_id") or "").strip('"')

        if not magento_cart_id:
            return {
                "success": False,
                "message": "Cart ID missing",
                "instruction_for_agent": "Tell user: 'Dạ, hiện tại giỏ hàng của anh/chị đang trống, anh chị muốn tìm kiếm sản phẩm nào bên em ạ.'",
                "code": "MISSING_CART_ID"
            }

        # Call setVatInformationOnCart mutation
        # Note: customer_vat_id = null for first-time VAT invoice (theo API doc)
        mutation = """
            mutation SetVATInvoice(
                $cartId: String!,
                $companyName: String!,
                $companyVatNumber: String!,
                $companyAddress: String!
            ) {
                setVatInformationOnCart(
                    input: {
                        cart_id: $cartId
                        vat_address: {
                            customer_vat_id: null
                            company_name: $companyName
                            company_vat_number: $companyVatNumber
                            company_address: $companyAddress
                        }
                    }
                ) {
                    cart {
                        id
                        vat_address {
                            customer_vat_id
                            company_name
                            company_vat_number
                            company_address
                        }
                    }
                }
            }
        """

        variables = {
            "cartId": magento_cart_id,
            "companyName": company_name,
            "companyVatNumber": company_vat_number,
            "companyAddress": company_address
        }

        res = await make_graphql_request_async(
            mutation,
            variables,
            base_url,
            store_id,
            auth_token=signin_token or None
        )

        if not res or not res.get("data"):
            logger.error(f"Failed to set VAT invoice: {res}")
            error_message = "Unknown error"
            if res and res.get("errors"):
                error_message = res["errors"][0].get("message", "Unknown error")

            return {
                "success": False,
                "message": f"API error: {error_message}",
                "instruction_for_agent": f"Tell user: 'Em không thể lưu thông tin hóa đơn VAT lúc này. Lỗi: {error_message}'",
                "code": "API_ERROR"
            }

        # Success
        vat_data = res["data"]["setVatInformationOnCart"]["cart"]["vat_address"]

        logger.info(f"Successfully set VAT invoice for cart {magento_cart_id}: {vat_data}")

        return {
            "success": True,
            "message": "VAT invoice info set successfully",
            "instruction_for_agent": f"Tell user: 'Dạ, em đã lưu thông tin hóa đơn VAT:\n"
                                     f"• Tên công ty: {vat_data['company_name']}\n"
                                     f"• Mã số thuế: {vat_data['company_vat_number']}\n"
                                     f"• Địa chỉ: {vat_data['company_address']}'",
            "data": vat_data
        }

    except Exception as e:
        logger.error(f"Error in set_vat_invoice: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "instruction_for_agent": "Tell user: 'Em không thể cập nhật thông tin hóa đơn VAT lúc này, anh/chị thử lại sau ạ.'",
            "code": "UNEXPECTED_ERROR"
        }
