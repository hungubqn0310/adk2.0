import json
import os
import logging
from fastmcp import FastMCP
from fastmcp.prompts import Message
from mcp.types import PromptMessage, TextContent
from typing import Dict, Any, Optional, List
import httpx
from dotenv import load_dotenv
import sys
import importlib.util
# Load environment variables from .env file
env_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env")
load_dotenv(env_path)

def _get_delivery_policy_info(store_name: str = None) -> dict:
    """Delivery policy based on store name (inlined to avoid package import in MCP context)."""
    special_stores = ["hưng phú", "thanh xuân"]
    is_special = store_name and any(s in store_name.lower() for s in special_stores)
    if is_special:
        return {
            "min_order_free_delivery": 300000,
            "free_delivery_radius_km": 7,
            "base_delivery_fee": 30000,
            "extra_km_fee": 6000,
            "max_delivery_radius_km": 15,
            "formatted_text": (
                "**Chính sách giao hàng tại trung tâm này:**\n"
                "- Miễn phí giao hàng cho đơn hàng từ 300.000₫ trong phạm vi 7km\n"
                "- Trên 7km: 6.000₫/km (tối đa 15km)\n"
                "- Đơn hàng dưới 300.000₫: 30.000₫ cho 7km đầu + 6.000₫/km tiếp theo (tối đa 15km)\n"
                "- Đơn hàng Kem & Bánh đông lạnh: Tối thiểu 300.000₫, thanh toán trước, chỉ giao trong 7km\n"
                "- Hàng nặng/cồng kềnh (>0.34m³ hoặc >90kg): Phụ thu 140.000₫ cho khoảng cách 7-10km"
            ),
        }
    return {
        "min_order_free_delivery": 600000,
        "free_delivery_radius_km": 7,
        "base_delivery_fee": 30000,
        "extra_km_fee": 5000,
        "max_delivery_radius_km": 15,
        "formatted_text": (
            "**Chính sách giao hàng:**\n"
            "- Miễn phí giao hàng cho đơn hàng từ 600.000₫ trong phạm vi 7km\n"
            "- Trên 7km: 5.000₫/km (tối đa 15km)\n"
            "- Đơn hàng dưới 600.000₫: 30.000₫ cho 7km đầu + 5.000₫/km tiếp theo (tối đa 15km)\n"
            "- Đơn hàng Kem & Bánh đông lạnh: Tối thiểu 600.000₫, thanh toán trước, chỉ giao trong 7km\n"
            "- Hàng nặng/cồng kềnh (>0.34m³ hoặc >90kg): Phụ thu 140.000₫ cho khoảng cách 7-10km\n\n"
            "**Lưu ý:**\n"
            "- Khách hàng nhận hàng tại cổng/sảnh/khu vực nhận hàng của tòa nhà\n"
            "- Khách hàng quận 7 đặt tại MM An Phú: Phụ thu thêm 12.000₫\n"
            "- Đơn hàng trên 20 triệu cần chuyển khoản trước"
        ),
    }

DEFAULT_MMVN_STORE_URL = os.getenv("DEFAULT_MMVN_STORE_URL", "https://b2c-mmpro.izysync.com")
DEFAULT_MMVN_STORE_ID = os.getenv("DEFAULT_MMVN_STORE_ID", "b2c_10010_vi")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
_cart_id_cache: Optional[str] = None

mcp = FastMCP(
    name="MM Mega Market Vietnam Tools"
)
_rag_instance = None

# Global state for selected store (MCP doesn't have built-in state management)
_selected_store = {
    "store_id": DEFAULT_MMVN_STORE_ID,
    "base_url": DEFAULT_MMVN_STORE_URL,
    "store_code": None,
    "store_name": None,
    "is_default": True
}

# Global state for user authentication
_user_auth = {
    "is_logged_in": False,
    "token": None,
    "email": None,
    "location": {
        "region_id": None,
        "city": None,
        "city_code": None,
        "district": None,
        "district_code": None,
        "ward": None,
        "ward_code": None,
        "address": None,
        "store_view_code": None
    }
}

# Order status constants - Use these exact values for filtering orders
ORDER_STATUS_CANCELED = "backorder_ccod,canceled,closed,deleted_ccod"
ORDER_STATUS_DELIVERED = "complete,completed_ccod"
ORDER_STATUS_DELIVERING = "invoiced_ccod,in_shipment_ccod,picked_ccod,picking_ccod"
ORDER_STATUS_PENDING = "pending,pending_ccod"
ORDER_STATUS_PROCESSING = "confirmed_ccod,order_error,processing"
ORDER_STATUS_WAITING_CANCEL = "waiting_cancel"
ORDER_STATUS_AWAITING_PAYMENT = "pending_payment"


def _get_rag_instance():
    """Lazy-load RAG instance to avoid import-time initialization issues."""
    global _rag_instance

    if _rag_instance is None:
        rag_file_path = os.path.join(os.path.dirname(__file__), "..", "rag", "rag.py")
        rag_file_path = os.path.abspath(rag_file_path)

        spec = importlib.util.spec_from_file_location("rag_module", rag_file_path)
        rag_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rag_module)

        GemmaQdrantRAG = rag_module.GemmaQdrantRAG
        RETRIEVER_MODES = rag_module.RETRIEVER_MODES

        qdrant_url = os.getenv("QDRANT_URL", "http://mmvn-qdrant:6333")
        qdrant_api_key = os.getenv("QDRANT_API_KEY", None)
        gemini_api_key = os.getenv("GOOGLE_API_KEY", None)
        collection_name = os.getenv("RAG_COLLECTION_NAME", "mmvn_rag_agent")
        input_dir = os.getenv("RAG_INPUT_DIR", "/opt/app/data/documents")

        logger.info(f"Initializing RAG: URL={qdrant_url}, collection={collection_name}")

        _rag_instance = GemmaQdrantRAG(
            name="mm_vietnam_rag",
            description="RAG system for MM Vietnam knowledge base",
            input_dir=input_dir,
            collection_name=collection_name,
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key,
            gemini_api_key=gemini_api_key,
            embedding_model="gemini-embedding-001",
            embedding_dim=768,
            retriever_mode=RETRIEVER_MODES.SIMPLE_VECTOR,
            chunk_size=1024,
            chunk_overlap=128,
            similarity_top_k=5,
        )

        try:
            _rag_instance.insert_documents(force_reindex=False)
        except Exception as e:
            logger.warning(f"Could not index documents: {e}")

    return _rag_instance


@mcp.resource("store://current")
def get_current_selected_store() -> str:
    """
    MCP Resource: Current selected store information.
    This resource is automatically updated when user selects a store via select_store tool.
    """
    global _selected_store
    return json.dumps(_selected_store, indent=2, ensure_ascii=False)


@mcp.resource("user://auth")
def get_current_user_auth() -> str:
    """
    MCP Resource: Current user authentication information.
    This resource is automatically updated when user logs in via login_user tool.
    """
    global _user_auth
    # Return auth info but mask the token for security
    auth_info = _user_auth.copy()
    if auth_info.get("token"):
        auth_info["token"] = f"{auth_info['token'][:20]}...***MASKED***"
    return json.dumps(auth_info, indent=2, ensure_ascii=False)


@mcp.resource("skill://introduction")
def get_skill_introduction() -> str:
    """
    MCP Resource: Introduction and capability overview of MM Mega Market Vietnam ordering system.
    This provides a comprehensive overview of what this agent can do.
    """
    return """# MM Mega Market Vietnam Ordering Skill

## Overview
Complete e-commerce ordering system for MM Mega Market Vietnam (online.mmvietnam.com).
This skill enables full shopping capabilities including product search, cart management, order tracking,
and customer support for Vietnam's leading wholesale retailer.

## Core Capabilities

### 1. Authentication & User Management
- User login/logout with email and password
- Session management with token-based authentication
- User profile and location information retrieval
- Required for: order tracking, order history, personalized shopping

### 2. Store Management & Location Services
- Select specific MM Vietnam store locations
- Find nearest stores based on customer address
- Store-specific pricing and inventory
- Multi-store support across Vietnam
- Default store: Hanoi (b2c_10010_vi)

### 3. Product Discovery & Search
- **Smart product search with Vietnamese language support**
- Search by keywords, categories, price ranges
- Filter by: promotions, bestsellers, discounts
- Browse hierarchical category structure
- Product information: SKU, name, price, images, stock status, promotions
- **CRITICAL**: Always translate English queries to Vietnamese for search

### 4. Shopping Cart Management
- Add products to cart (requires SKU and quantity)
- View cart contents with full details
- Update product quantities
- Remove items from cart
- Add special instructions/notes to cart items
- Remove notes from cart items
- Cart persistence across sessions

### 5. Order Management & Tracking
- View order history (requires authentication)
- Track specific orders by order ID
- Order status monitoring
- Detailed order information: items, pricing, shipping, payment method
- Filter orders by status, date range

### 6. Knowledge Base & Customer Support
- RAG-powered search across MM Vietnam documentation
- Answer questions about: policies, services, products, promotions
- Store policies: delivery, returns, warranty, privacy
- M-Card membership benefits and rewards program
- Quality standards and purchasing guides

## Key Features

### State Management
- **store://current** - Currently selected store (auto-updated)
- **user://auth** - Authentication status (auto-updated)
- **skill://introduction** - This capability overview

### Shopping Workflow
```
1. (Optional) Login → select_store → search_products
2. Add products to cart → view_cart → add notes
3. Customer completes checkout on website
4. Track orders → view order history
```

### Best Practices
1. **Vietnamese Search**: Always translate to Vietnamese before searching products
2. **Authentication Check**: Verify login status before order operations
3. **Fresh Cart UIDs**: Get cart_item_uid from view_cart before updates
4. **Error Handling**: Check 'success' field in all responses
5. **Store Context**: Verify correct store selection for location-specific operations

## Available Tools (17 total)

### Authentication (3 tools)
- login_user, logout_user, get_user_info

### Store Management (3 tools)
- select_store, check_store_selection, get_nearest_store

### Product Discovery (3 tools)
- search_products, get_product_categories, search_knowledge_base

### Shopping Cart (7 tools)
- add_product_to_cart, view_cart, update_cart_item
- remove_item_from_cart, clear_cart, add_product_note_to_cart, remove_product_note_from_cart

### Order Management (2 tools)
- check_my_orders, track_order

## Available Prompts (7 specialized scenarios)

### 1. "Hỗ trợ khách hàng MM Mega Market"
Main customer support prompt with comprehensive guidelines for:
- Product consultation and search assistance
- Cart management (add, remove, update with proper linking)
- Order tracking and status inquiries
- Policy guidance (delivery, returns, warranty, M-Card, quality standards)
- Store selection support
- Communication principles and workflow

### 2. "Tư vấn mua sắm" (Shopping Advisor)
Personalized shopping consultation with parameters:
- customer_needs: Customer requirements description
- budget: Optional budget range
- preferences: Optional preferences/requirements
Provides structured product recommendations with price comparison and promotions

### 3. "Xử lý khiếu nại đơn hàng" (Order Complaint Handler)
Order issue resolution with parameters:
- order_number: Order ID to investigate
- issue_description: Problem description
Guides through complaint verification, policy check, and solution proposal

### 4. "Giới thiệu chương trình khuyến mãi" (Promotion Introduction)
Promotion and deals showcase:
- Search promotional information from knowledge base
- Find discounted products in promotion category (UID: MjUxNzE=)
- Present program details: name, duration, conditions
- Suggest hot deals and usage instructions

### 5. "Hướng dẫn đăng ký thẻ M-Card" (M-Card Registration Guide)
M-Card loyalty program assistance:
- M-Card benefits and privileges
- Registration process and requirements
- Card tiers and conditions
- Points accumulation and redemption

### 6. "Tìm cửa hàng gần nhất" (Find Nearest Store)
Location-based store finder with parameter:
- address: Customer's address or location
Workflow: search → display results by distance → confirm selection → apply store

### 7. "Hỗ trợ quản lý giỏ hàng" (Cart Management Guide)
Comprehensive cart management instructions:
- View cart with full details (products with links, SKU, quantities, prices)
- Add products (always search first for accurate SKU)
- Update quantities and remove items
- Best practices for cart operations with proper formatting

## Use Cases
- Complete shopping assistance from search to checkout
- Product recommendations and discovery
- Cart management and order modifications
- Order tracking and customer support
- Store location services
- Policy and FAQ inquiries
- M-Card program information

## Technical Details
- API Base URL: Configurable per store (default: https://b2c-mmpro.izysync.com)
- Authentication: Token-based (Bearer)
- Language: Vietnamese (primary), English (supported)
- Response Format: JSON with standardized success/error structure
- RAG System: Gemini + Qdrant for knowledge base

## Important Notes
- Most tools work without authentication, but login provides better experience
- Product search requires Vietnamese keywords for best results
- Cart operations need cart_item_uid from view_cart
- Order tracking requires user authentication
- Store selection affects pricing and inventory availability

## Detailed References

For in-depth information about specific tool categories, refer to these detailed resources:

- **skill://auth-reference** - Complete authentication workflows, error codes, and security best practices
- **skill://cart-reference** - Cart management including display formats, UID handling, and note management
- **skill://product-reference** - Product search with Vietnamese translation guide, filtering options, and category browsing
- **skill://order-reference** - Order tracking, status values, display requirements, and pagination handling

These references provide comprehensive documentation including:
- Detailed parameter descriptions and return formats
- Step-by-step usage examples
- Error handling strategies
- Display format requirements
- Best practices and common pitfalls

This skill provides comprehensive e-commerce capabilities for MM Mega Market Vietnam's online platform.
"""


@mcp.resource("skill://auth-reference")
def get_auth_reference() -> str:
    """
    Detailed reference for Authentication tools including workflows, error handling, and best practices.
    """
    return """# Authentication Tools Reference

## Overview
Authentication tools manage user login/logout and session state for MM Vietnam.

---

## login_user

### Purpose
Authenticate user with email and password to access personalized features.

### Parameters
- `email` (required): User email address
- `password` (required): User password
- `base_url` (optional): API endpoint (defaults to selected store)
- `store_id` (optional): Store ID (defaults to selected store)

### Returns
```json
{
  "success": true,
  "token": "eyJ0eXAi...***MASKED***",
  "location": {
    "city": "Hanoi",
    "district": "Ba Dinh",
    "ward": "Dien Bien",
    "address": "123 Main St"
  },
  "message": "Login successful",
  "code": "LOGIN_SUCCESS"
}
```

### State Changes
- Updates `user://auth` resource
- Stores authentication token globally
- Enables authenticated API calls

### Error Codes
- `MISSING_EMAIL`: Email not provided
- `MISSING_PASSWORD`: Password not provided
- `AUTHENTICATION_FAILED`: Invalid credentials
- `JSON_PARSE_ERROR`: API response invalid
- `HTTP_ERROR`: Network/connection error

---

## logout_user

### Purpose
End current user session and clear authentication state.

### Returns
```json
{
  "success": true,
  "message": "Đã đăng xuất tài khoản customer@example.com",
  "code": "LOGOUT_SUCCESS"
}
```

### State Changes
- Clears `user://auth` resource
- Removes authentication token
- Resets user location data
- Cart remains accessible

### Error Codes
- `NOT_LOGGED_IN`: No active user session

---

## get_user_info

### Purpose
Check current authentication status and retrieve user information.

### Returns

**When Logged In:**
```json
{
  "success": true,
  "is_logged_in": true,
  "email": "customer@example.com",
  "location": {
    "city": "Hanoi",
    "district": "Ba Dinh",
    "ward": "Dien Bien",
    "address": "123 Main St"
  },
  "message": "User customer@example.com is logged in",
  "code": "USER_INFO_RETRIEVED"
}
```

**When Not Logged In:**
```json
{
  "success": false,
  "is_logged_in": false,
  "message": "No user is currently logged in",
  "instruction_for_agent": "User needs to login first using login_user tool",
  "code": "NOT_LOGGED_IN"
}
```

---

## Authentication Best Practices

### 1. Check Before Order Operations
Always verify authentication before:
- `check_my_orders`
- `track_order`
- Checkout operations

### 2. Handle Login Failures Gracefully
```
result = login_user(email, password)
if not result["success"]:
    # Inform user of specific error
    # Suggest password reset or account creation
```

### 3. Session Persistence
- Authentication persists across tool calls
- Use `get_user_info` to verify active session
- Re-login if session expired

### 4. Security Considerations
- Token is masked in responses for security
- Never log or display full authentication token
- Token automatically included in authenticated API calls

### 5. Location Data
- User location retrieved during login
- Used for delivery address suggestions
- Helpful for store selection and shipping calculations
"""


@mcp.resource("skill://cart-reference")
def get_cart_reference() -> str:
    """
    Detailed reference for Shopping Cart tools including display formats and UIDs handling.
    """
    return """# Shopping Cart Tools Reference

## Overview
Cart tools enable adding products, managing quantities, viewing cart, and adding special instructions.

---

## add_product_to_cart

### Purpose
Add products to shopping cart, creating cart if needed.

### Parameters
- `sku` (required): Product SKU from search results
- `quantity` (required): Number of items to add
- `cart_id` (optional): Existing cart ID (auto-created if not provided)

### Returns
Complete cart with all items, prices, and totals.

### Critical Rules
- **ALWAYS** search_products() first to get correct SKU
- **NEVER** guess or infer SKU values
- Cart ID is automatically cached for future operations

### Error Codes
- `MISSING_SKU`: SKU not provided
- `MISSING_QUANTITY`: Quantity not provided
- `INVALID_QUANTITY`: Quantity must be positive integer
- `PRODUCT_NOT_FOUND`: SKU doesn't exist
- `OUT_OF_STOCK`: Product unavailable
- `CART_ERROR`: Failed to create/access cart

---

## view_cart

### Purpose
Display complete cart contents including products, quantities, prices, and totals.

### Returns Full Cart Data Including
- Cart ID and total quantity
- Each item with: uid, product details, quantity, prices, notes
- Subtotal and grand total

### Display Format Requirements
```
🛒 GIỎ HÀNG

1. [Product Name](url) (SKU: SKU001)
   Số lượng: 2 x 125,000 VND = 250,000 VND
   📝 Ghi chú: [note if exists]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tổng số lượng: 5 sản phẩm
Tổng cộng: 355,000 VND
```

### Critical Requirements
- **MUST** display product names as clickable markdown links
- **MUST** show SKU for each product
- **MUST** display notes prominently when present
- URL format: {base_url}/product/{url_key}

---

## update_cart_item

### Purpose
Change quantity of specific cart item or remove by setting quantity to 0.

### Parameters
- `cart_item_uid` (required): Item UID from view_cart
- `new_quantity` (required): New quantity (0 to remove)

### Critical Rules
- **MUST** get fresh cart_item_uid from view_cart before each update
- UIDs may change after cart modifications
- Set quantity to 0 to remove item

### Error Codes
- `MISSING_CART_ITEM_UID`: UID not provided
- `MISSING_QUANTITY`: Quantity not provided
- `INVALID_QUANTITY`: Quantity must be non-negative integer
- `ITEM_NOT_FOUND`: UID doesn't exist in cart

---

## remove_item_from_cart

### Purpose
Permanently remove a single product from cart.

### Parameters
- `cart_item_uid` (required): Item UID from view_cart

---

## clear_cart

### Purpose
Remove ALL items from cart (clear entire cart).

### Parameters
None required. Uses cached cart_id automatically.

### Usage
Use when customer wants to:
- Empty their entire cart
- Start fresh with a new cart
- Remove all items at once

---

## add_product_note_to_cart

### Purpose
Add special instructions or comments to specific cart items.

### Parameters
- `cart_item_uid` (required): Item UID from view_cart
- `note` (required): Note/comment text

### Use Cases
- Delivery instructions: "Giao hàng buổi sáng, trước 9h"
- Product preferences: "Chọn trái cây tươi, còn xanh"
- Special requests: "Đóng gói riêng từng sản phẩm"

### Display Requirements
After adding note, MUST show note prominently when displaying cart.

---

## remove_product_note_from_cart

### Purpose
Remove notes/comments from cart items.

### Parameters
- `cart_item_uid` (required): Item UID from view_cart

---

## Cart Management Best Practices

### 1. Always Get Fresh cart_item_uid
```
# ✓ Correct: Get fresh UIDs before updates
cart = view_cart()
item_uid = cart["data"]["items"][0]["uid"]
update_cart_item(cart_item_uid=item_uid, new_quantity=5)

# ✗ Wrong: Reuse old UIDs
# Old UIDs may become invalid after cart changes
```

### 2. Verify Operations
After cart operations, always view cart to confirm changes.

### 3. Cart Persistence
- Cart persists across sessions
- Cart ID cached automatically
- No need to pass cart_id explicitly after creation

### 4. Display Guidelines
- Always show product names as markdown links
- Include SKU for reference
- Display prices with currency format
- Show notes prominently when present
- Calculate and display totals clearly
"""


@mcp.resource("skill://product-reference")
def get_product_reference() -> str:
    """
    Detailed reference for Product Discovery tools including Vietnamese translation requirements.
    """
    return """# Product Discovery Tools Reference

## Overview
Product tools enable search, browsing, and information lookup across MM Vietnam catalog.

---

## search_products

### Purpose
Search for products using Vietnamese keywords with advanced filtering options.

### Critical Requirements
⚠️ **MUST translate English queries to Vietnamese before searching**

### Parameters

**Required:**
- `keyword_in_vietnamese` (string): Vietnamese search term for database query

**Optional Filters:**
- `search_type`: "normal" (default), "discount", or "bestseller"
- `category`: List of category UIDs from get_product_categories
- `price_min`, `price_max`: Price range in VND
- `sort_by`: "relevance", "ecom_name", "mm_sale_price_include_vat", "popular", "ecom_qty_ordered"
- `sort_direction`: "ASC" or "DESC"
- `page`, `page_size`: Pagination

### Returns
- `total_count`: Total matching products
- `items`: Array of products with: SKU, name, price, images, stock status, promotions
- Each product includes `url_key` for building product links

### Search Type Examples

**Normal Search:**
```
search_products(keyword_in_vietnamese="gạo")
```

**Discount/Promotion Products:**
```
search_products(
  keyword_in_vietnamese="gạo",
  search_type="discount"
)
# Returns only products with active promotions
# Automatically uses page_size=50
```

**Bestsellers:**
```
search_products(
  keyword_in_vietnamese="gạo",
  search_type="bestseller"
)
# Automatically sorts by ecom_qty_ordered DESC
```

### Vietnamese Translation Guide

| English | Vietnamese | Category |
|---------|-----------|----------|
| rice | gạo | Food |
| meat | thịt | Food |
| fish | cá | Food |
| vegetable | rau | Food |
| fruit | trái cây | Food |
| milk | sữa | Dairy |
| beer | bia | Beverages |
| wine | rượu vang | Beverages |
| water | nước | Beverages |
| coffee | cà phê | Beverages |
| tea | trà | Beverages |
| noodles | mì | Food |
| bread | bánh mì | Food |
| egg | trứng | Food |
| oil | dầu ăn | Cooking |
| shampoo | dầu gội | Personal Care |
| soap | xà phòng | Personal Care |

---

## get_product_categories

### Purpose
Retrieve complete category hierarchy for browsing and filtering.

### Returns
Nested tree structure of all categories with UIDs and names.

### Usage
1. Get all categories
2. Extract category UID for desired category
3. Use in search_products() category filter

---

## search_knowledge_base

### Purpose
Query MM Vietnam's RAG-powered knowledge base for information about policies, services, and general inquiries.

### Parameters
- `query` (required): Question or search term (can be English or Vietnamese)
- `top_k` (optional): Number of results, default 5

### Use Cases
- Store policies: "return policy", "chính sách hoàn trả"
- Product information: "organic certification", "how to store wine"
- General inquiries: "membership benefits", "delivery areas"

---

## Product Search Best Practices

### 1. Always Translate to Vietnamese
```
# User query: "I want chicken"
# ✓ Correct:
search_products(keyword_in_vietnamese="thịt gà")

# ✗ Wrong:
search_products(keyword_in_vietnamese="chicken")
```

### 2. Present Results Clearly
```
Found 245 products for "gạo":

🛒 [Gạo ST25 túi 5kg](url)
   SKU: SKU001 | Art No: ART001
   Giá: 125,000 VND/túi (Giảm 16.67%)
   Tình trạng: Còn hàng
   🎁 Giảm giá đặc biệt
```

### 3. Display Product Links
- Always show product names as markdown links
- URL format: {base_url}/product/{url_key}
- Include SKU and Art No for reference

### 4. Handle Empty Results
- Try broader search terms
- Suggest alternatives
- Check spelling (Vietnamese diacritics important)

### 5. Store Context
- Verify correct store selected
- Prices and inventory vary by store
- Use `check_store_selection` to confirm
"""


@mcp.resource("skill://order-reference")
def get_order_reference() -> str:
    """
    Detailed reference for Order Management tools including tracking and authentication requirements.
    """
    return """# Order Management Tools Reference

## Overview
Order tools enable tracking order history and monitoring delivery status. **Authentication required** for all order operations.

---

## check_my_orders

### Purpose
Retrieve complete order history for authenticated user.

### Authentication Required
⚠️ User **MUST** be logged in via `login_user` before calling this tool.

### Parameters
- `page` (optional): Page number, default 1
- `page_size` (optional): Orders per page, default 10

### Returns
List of orders with:
- Order number and ID
- Order date and status
- Grand total
- Shipping address
- Items ordered
- Payment totals

### Display Format
```
📦 ĐƠN HÀNG CỦA BẠN

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Đơn hàng #000001234
Ngày đặt: 20/11/2024 10:30
Trạng thái: Đang xử lý
Tổng tiền: 1,250,000 VND

Sản phẩm:
• Gạo ST25 5kg (x2) - 250,000 VND
• Sữa tươi Vinamilk 1L (x3) - 105,000 VND
```

### Order Status Values
- **"Pending"** - Chờ xác nhận
- **"Processing"** - Đang xử lý
- **"Preparing"** - Đang chuẩn bị hàng
- **"Shipping"** - Đang giao hàng
- **"Complete"** - Đã giao thành công
- **"Canceled"** - Đã hủy
- **"Refunded"** - Đã hoàn tiền

### Error Codes
- `NOT_LOGGED_IN`: User not authenticated
- `NO_ORDERS`: No orders found
- `HTTP_ERROR`: Network error

---

## track_order

### Purpose
Get detailed tracking information for specific order.

### Authentication Required
⚠️ User **MUST** be logged in via `login_user` before calling this tool.

### Parameters
- `order_id` (required): Order ID (encoded format like "MQ==")
- `order_number` (optional): Human-readable order number for display

### Returns
Detailed order tracking including:
- Order status and dates
- Shipping address (full details)
- Carrier and tracking number
- Estimated delivery date
- Items with shipped/invoiced quantities
- Shipment records
- Complete payment breakdown

### Display Format
```
📦 THEO DÕI ĐƠN HÀNG #000001234

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Thông tin đơn hàng
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ngày đặt: 20/11/2024 10:30
Trạng thái: Đang giao hàng
Dự kiến giao: 22/11/2024

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Thông tin vận chuyển
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Đơn vị: Giao Hang Nhanh
Mã vận đơn: GHN123456789

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Địa chỉ giao hàng
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Người nhận: Nguyen Van A
Địa chỉ: 123 Main St, Ward, District, Hanoi
Điện thoại: 0901234567

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sản phẩm
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Gạo ST25 5kg (SKU: SKU001)
   Đã đặt: 2
   Đã giao: 2
   Giá: 125,000 VND x 2 = 250,000 VND

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tổng thanh toán
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tạm tính: 1,000,000 VND
Phí vận chuyển: 50,000 VND
Thuế VAT: 200,000 VND
Tổng cộng: 1,250,000 VND
```

### Critical Display Requirements
When showing order details, MUST include:
- Full shipping address (street, ward, district, city)
- All payment components with correct titles:
  * "Tạm tính" (subtotal)
  * "Phí vận chuyển" (shipping)
  * "Thuế VAT" (tax)
  * "Giảm giá" (discounts if any)
  * "Tổng cộng" (grand total)

### Error Codes
- `NOT_LOGGED_IN`: User not authenticated
- `MISSING_ORDER_ID`: Order ID not provided
- `ORDER_NOT_FOUND`: Order doesn't exist or doesn't belong to user
- `HTTP_ERROR`: Network error

---

## Order Management Best Practices

### 1. Always Check Authentication
```
# ✓ Correct: Check before order operations
user_info = get_user_info()
if not user_info["is_logged_in"]:
    print("Please login first")
    login_user(email, password)

orders = check_my_orders()
```

### 2. Handle Pagination
```
# Get first page
page1 = check_my_orders(page=1, page_size=10)
total_orders = page1["total_count"]

# Calculate total pages
total_pages = (total_orders + 9) // 10

# Get more if needed
if total_pages > 1:
    page2 = check_my_orders(page=2)
```

### 3. Extract Order IDs Correctly
```
orders = check_my_orders()
for order in orders["data"]["orders"]:
    # Use encoded order_id for track_order
    order_id = order["order_id"]  # "MQ=="

    # Use order_number for display
    order_number = order["order_number"]  # "000001234"

    # Track order
    track_order(order_id=order_id, order_number=order_number)
```

### 4. Store Context
- Orders are store-specific
- Select correct store before viewing orders
- Use `check_store_selection` to verify
- Change store with `select_store` if needed
"""


@mcp.tool()
async def login_user(
    email: str,
    password: str,
    base_url: Optional[str] = None,
    store_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Login user with email and password to get authentication token and user location.

    This tool authenticates the user and stores the token and location information
    in the global state. The token will be used for authenticated API calls.

    Args:
        email: User email address (required, e.g., "user@example.com")
        password: User password (required)
        base_url: Base API URL (optional, uses selected store if not provided)
        store_id: Store ID (optional, uses selected store if not provided)

    Returns:
        Dictionary containing:
        - success: Boolean indicating if login was successful
        - token: Authentication token (masked in response for security)
        - location: User's location information (city, district, ward, address)
        - message: Status message
        - code: Response code

    Example:
        login_user(email="user@example.com", password="password123")
    """
    global _selected_store, _user_auth

    # Use selected store if not explicitly provided
    if store_id is None:
        store_id = _selected_store["store_id"]
    if base_url is None:
        base_url = _selected_store["base_url"]

    # Validate inputs
    if not email or not email.strip():
        return {
            "success": False,
            "message": "Email is required",
            "code": "MISSING_EMAIL"
        }

    if not password or not password.strip():
        return {
            "success": False,
            "message": "Password is required",
            "code": "MISSING_PASSWORD"
        }

    email = email.strip().lower()

    # GraphQL mutation for login
    graphql_mutation = """
        mutation SignIn($email: String!, $password: String!) {
            generateCustomerTokenV2(email: $email, password: $password) {
                token
                location_user {
                    region_id
                    city
                    city_code
                    district
                    district_code
                    ward
                    ward_code
                    address
                    store_view_code
                    __typename
                }
                __typename
            }
        }
    """

    variables = {
        "email": email,
        "password": password
    }

    payload = {
        "operationName": "SignIn",
        "query": graphql_mutation,
        "variables": variables
    }

    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:139.0) Gecko/20100101 Firefox/139.0',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Content-Type': 'application/json',
        'Store': store_id,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            graphql_url = f"{base_url.rstrip('/')}/graphql"
            response = await client.post(
                graphql_url,
                headers=headers,
                content=json.dumps(payload)
            )

            # Parse response
            try:
                res = response.json()
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Failed to parse JSON response: {str(e)}",
                    "code": "JSON_PARSE_ERROR"
                }

            # Check for GraphQL errors
            if "errors" in res:
                error_messages = [err.get("message", "") for err in res.get("errors", [])]
                return {
                    "success": False,
                    "message": f"Login failed: {', '.join(error_messages)}",
                    "code": "AUTHENTICATION_FAILED",
                    "errors": error_messages
                }

            # Check data
            data = res.get("data", {}).get("generateCustomerTokenV2")
            if not data:
                return {
                    "success": False,
                    "message": "Invalid email or password",
                    "code": "INVALID_CREDENTIALS"
                }

            token = data.get("token")
            location_user = data.get("location_user", {})

            if not token:
                return {
                    "success": False,
                    "message": "Failed to retrieve authentication token",
                    "code": "NO_TOKEN"
                }

            # Save guest cart ID before updating auth state
            global _cart_id_cache
            guest_cart_id = _cart_id_cache

            # Update global auth state
            _user_auth["is_logged_in"] = True
            _user_auth["token"] = token
            _user_auth["email"] = email

            # Update location information
            if location_user:
                _user_auth["location"]["region_id"] = location_user.get("region_id")
                _user_auth["location"]["city"] = location_user.get("city")
                _user_auth["location"]["city_code"] = location_user.get("city_code")
                _user_auth["location"]["district"] = location_user.get("district")
                _user_auth["location"]["district_code"] = location_user.get("district_code")
                _user_auth["location"]["ward"] = location_user.get("ward")
                _user_auth["location"]["ward_code"] = location_user.get("ward_code")
                _user_auth["location"]["address"] = location_user.get("address")
                _user_auth["location"]["store_view_code"] = location_user.get("store_view_code")

                # If user has a preferred store_view_code, update the selected store
                if location_user.get("store_view_code"):
                    store_view_code = location_user["store_view_code"]
                    _selected_store["store_id"] = store_view_code
                    logger.info(f"Updated selected store to user's preferred store: {store_view_code}")

            logger.info(f"User logged in successfully: {email}")

            # Merge guest cart into customer cart if guest had items
            customer_cart_id = None
            if guest_cart_id:
                logger.info(f"Attempting to merge guest cart {guest_cart_id} into customer cart")
                merge_result = await _merge_carts(
                    guest_cart_id=guest_cart_id,
                    customer_token=token,
                    base_url=base_url,
                    store_id=store_id
                )
                if merge_result.get("success"):
                    customer_cart_id = merge_result.get("cart_id")
                    _cart_id_cache = customer_cart_id
                    logger.info(f"Cart merged successfully. New customer cart ID: {customer_cart_id}")
                else:
                    logger.warning(f"Failed to merge carts: {merge_result.get('message')}")
            else:
                # No guest cart, just get/create customer cart
                logger.info("No guest cart to merge, getting customer cart")
                try:
                    # Get customer cart
                    headers_with_auth = {
                        'Store': store_id,
                        'Content-Type': 'application/json',
                        'Authorization': f'Bearer {token}'
                    }
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        graphql_url = f"{base_url.rstrip('/')}/graphql"
                        response = await client.post(
                            graphql_url,
                            headers=headers_with_auth,
                            data=json.dumps({"query": "{ customerCart { id } }"})
                        )
                        res = response.json()
                        customer_cart_id = res.get("data", {}).get("customerCart", {}).get("id")
                        if customer_cart_id:
                            _cart_id_cache = customer_cart_id
                            logger.info(f"Retrieved customer cart ID: {customer_cart_id}")
                except Exception as e:
                    logger.warning(f"Failed to get customer cart: {e}")

            # Prepare location display
            location_display = {}
            if location_user:
                if location_user.get("city"):
                    location_display["city"] = location_user["city"]
                if location_user.get("district"):
                    location_display["district"] = location_user["district"]
                if location_user.get("ward"):
                    location_display["ward"] = location_user["ward"]
                if location_user.get("address"):
                    location_display["address"] = location_user["address"]

            # Build success message
            success_message = f"Đăng nhập thành công với email {email}"
            if customer_cart_id:
                if guest_cart_id:
                    success_message += f". Giỏ hàng của bạn đã được đồng bộ."
                success_message += f" (Cart ID: {customer_cart_id})"

            return {
                "success": True,
                "token_preview": f"{token[:20]}...***MASKED***",
                "email": email,
                "location": location_display,
                "full_location_saved": bool(location_user),
                "customer_cart_id": customer_cart_id,
                "guest_cart_id": guest_cart_id,
                "cart_merged": bool(guest_cart_id and customer_cart_id),
                "message": success_message,
                "instruction_for_agent": (
                    f"User has been authenticated successfully. "
                    f"Token and location information have been saved to global state. "
                    f"Customer cart ID has been updated to {customer_cart_id}. "
                    f"Future API calls can now use the authentication token for authenticated operations."
                ),
                "code": "LOGIN_SUCCESS"
            }

    except httpx.RequestError as e:
        return {
            "success": False,
            "message": f"HTTP request error: {str(e)}",
            "code": "HTTP_ERROR"
        }
    except Exception as e:
        logger.error(f"Error in login_user: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "code": "UNKNOWN_ERROR"
        }


@mcp.tool()
async def logout_user() -> Dict[str, Any]:
    """
    Logout the current user and clear authentication state.

    This tool clears the stored authentication token and user information.

    Returns:
        Dictionary containing:
        - success: Boolean indicating operation status
        - message: Status message
        - code: Response code
    """
    global _user_auth

    if not _user_auth.get("is_logged_in"):
        return {
            "success": False,
            "message": "No user is currently logged in",
            "code": "NOT_LOGGED_IN"
        }

    logged_out_email = _user_auth.get("email")

    # Reset auth state
    _user_auth["is_logged_in"] = False
    _user_auth["token"] = None
    _user_auth["email"] = None
    _user_auth["location"] = {
        "region_id": None,
        "city": None,
        "city_code": None,
        "district": None,
        "district_code": None,
        "ward": None,
        "ward_code": None,
        "address": None,
        "store_view_code": None
    }

    logger.info(f"User logged out: {logged_out_email}")

    return {
        "success": True,
        "message": f"Đã đăng xuất tài khoản {logged_out_email}",
        "code": "LOGOUT_SUCCESS"
    }


@mcp.tool()
async def get_user_info() -> Dict[str, Any]:
    """
    Get current logged-in user information.

    Returns the current authentication status and user information stored in global state.

    Returns:
        Dictionary containing:
        - success: Boolean indicating if user is logged in
        - is_logged_in: Boolean authentication status
        - email: User email (if logged in)
        - location: User location information (if available)
        - message: Status message
        - code: Response code
    """
    global _user_auth

    if not _user_auth.get("is_logged_in"):
        return {
            "success": False,
            "is_logged_in": False,
            "message": "No user is currently logged in",
            "instruction_for_agent": "User needs to login first using login_user tool",
            "code": "NOT_LOGGED_IN"
        }

    location_info = {}
    if _user_auth.get("location"):
        loc = _user_auth["location"]
        if loc.get("city"):
            location_info["city"] = loc["city"]
        if loc.get("district"):
            location_info["district"] = loc["district"]
        if loc.get("ward"):
            location_info["ward"] = loc["ward"]
        if loc.get("address"):
            location_info["address"] = loc["address"]

    return {
        "success": True,
        "is_logged_in": True,
        "email": _user_auth.get("email"),
        "location": location_info if location_info else None,
        "message": f"User {_user_auth.get('email')} is logged in",
        "code": "USER_INFO_RETRIEVED"
    }


@mcp.tool()
async def search_products(
    keyword_in_vietnamese: str,
    language: str = "vi",
    keyword: Optional[str] = None,
    search_type: str = "normal",
    category: Optional[List[str]] = None,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    sort_by: str = "relevance",
    sort_direction: str = "DESC",
    page: int = 1,
    page_size: int = 12,
    base_url: Optional[str] = None,
    store_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Search for products in MM Mega Market Vietnam catalog using GraphQL API.

    The tool automatically uses the currently selected store (via select_store tool).
    If customer hasn't selected a store yet, it uses the default store.

    Args:
        keyword_in_vietnamese: Vietnamese keyword for product search (required, used for database query).
        language: User session language (default: "vi").
        keyword: Keyword in user's language (for display, defaults to keyword_in_vietnamese).
        search_type: Type of search - "normal" (default), "discount" (only products with promotions), or "bestseller" (best-selling products).
        category: List of category UIDs to filter (optional).
        price_min: Minimum price filter (optional).
        price_max: Maximum price filter (optional).
        sort_by: Sort field (relevance, ecom_name, mm_sale_price_include_vat, popular, ecom_qty_ordered).
        sort_direction: Sort direction (ASC or DESC).
        page: Page number (default: 1).
        page_size: Number of products per page (default: 12, 50 for discount search).
        base_url: Base API URL (optional, uses selected store if not provided).
        store_id: Store ID (optional, uses selected store if not provided).

    Returns:
        Dictionary containing search results with success status, product data, and metadata.
    """
    global _selected_store

    # Use selected store if not explicitly provided
    if store_id is None:
        store_id = _selected_store["store_id"]
    if base_url is None:
        base_url = _selected_store["base_url"]

    # If keyword is not provided, use keyword_in_vietnamese
    if keyword is None:
        keyword = keyword_in_vietnamese

    # Adjust parameters based on search_type and filters
    if search_type == "discount":
        # For discount search, fetch more products to filter
        page_size = max(page_size, 50)
    elif price_min is not None or price_max is not None:
        # For price filter, fetch more products since API filter may not work
        page_size = max(page_size, 50)

    if search_type == "bestseller":
        # For bestseller, sort by sales volume
        sort_by = "ecom_qty_ordered"
        sort_direction = "DESC"

    # Prepare GraphQL query
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
                    sku
                    ecom_name
                    art_no
                    url_key
                    stock_status
                    unit_ecom
                    small_image {
                        url
                    }
                    price_range {
                        maximum_price {
                            final_price {
                                currency
                                value
                            }
                            regular_price {
                                currency
                                value
                            }
                            discount {
                                amount_off
                                percent_off
                            }
                        }
                    }
                    dnr_price_search_page {
                        event_id
                        event_name
                    }
                    dnr_promotion {
                        great_deal {
                            price
                            old_price
                        }
                        free_gift {
                            product {
                                sku
                                name
                            }
                        }
                    }
                }
                page_info {
                    total_pages
                }
                total_count
            }
        }
    """

    # Prepare variables
    variables = {
        "currentPage": page,
        "pageSize": page_size,
        "inputText": keyword_in_vietnamese,
        "filters": {},
        "sort": {}
    }
    if category:
        variables["filters"]["category_uid"] = {
            "in": category
        }
    # NOTE: Price filter is NOT sent to API because it doesn't work correctly
    # Price filtering is done client-side after fetching results

    # Add sort
    variables["sort"][sort_by] = sort_direction

    # Prepare payload
    payload = {
        "query": graphql_query,
        "variables": variables
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:139.0) Gecko/20100101 Firefox/139.0',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Content-Type': 'application/json',
        'Store': store_id,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            graphql_url = f"{base_url.rstrip('/')}/graphql"
            response = await client.post(
                graphql_url,
                headers=headers,
                content=json.dumps(payload)
            )

            # Parse response
            try:
                res = response.json()
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Failed to parse JSON response: {str(e)}",
                    "code": "JSON_PARSE_ERROR"
                }

            # Check for GraphQL errors
            if "errors" in res:
                return {
                    "success": False,
                    "message": f"GraphQL errors for keyword '{keyword}': {res['errors']}",
                    "code": "GRAPHQL_ERROR"
                }

            # Check data
            data = res.get("data")
            if not data or not isinstance(data, dict):
                return {
                    "success": False,
                    "message": f"Search result of '{keyword}' returned invalid data",
                    "code": "INVALID_RESPONSE"
                }

            products = data.get("products")
            if not products or not isinstance(products, dict):
                return {
                    "success": False,
                    "message": f"Search result of '{keyword}' returned no products field",
                    "code": "INVALID_RESPONSE"
                }

            items = products.get("items")
            if not items:
                return {
                    "success": False,
                    "message": f"No products found for '{keyword}' at page {page}",
                    "code": "NO_PRODUCTS"
                }

            # Process and format product data
            processed_products = []
            for item in items:
                # Format product URL correctly
                product_url = None
                if item.get("url_key"):
                    url_key = item.get("url_key")
                    # Add .html extension if not present
                    if not url_key.endswith('.html'):
                        url_key = f"{url_key}.html"
                    product_url = f"{base_url}/product/{url_key}"

                product = {
                    "sku": item.get("sku"),
                    "name": item.get("ecom_name"),
                    "art_no": item.get("art_no"),
                    "url": product_url,
                    "stock_status": item.get("stock_status"),
                    "price": item.get("mm_sale_price_include_vat"),
                    "unit": item.get("unit_ecom"),
                    "need_age_verification": item.get("need_age_verification"),
                }

                # Add image if available
                if item.get("small_image") and item["small_image"].get("url"):
                    product["image_url"] = item["small_image"]["url"]

                # Add price range if available
                if item.get("price_range"):
                    max_price = item["price_range"].get("maximum_price", {})
                    final_price = max_price.get("final_price", {})
                    regular_price = max_price.get("regular_price", {})
                    discount = max_price.get("discount", {})

                    product["price_details"] = {
                        "currency": final_price.get("currency", "VND"),
                        "final_price": final_price.get("value"),
                        "regular_price": regular_price.get("value"),
                        "discount_amount": discount.get("amount_off"),
                        "discount_percent": discount.get("percent_off"),
                    }
                    product["price"] = final_price.get("value")

                # Process promotion event from search page
                if item.get("dnr_price_search_page"):
                    dnr_price_search = item["dnr_price_search_page"]
                    if dnr_price_search:
                        product["promotion_event"] = {
                            "event_id": dnr_price_search.get("event_id"),
                            "event_name": dnr_price_search.get("event_name")
                        }

                # Process detailed promotion info (great deal, free gift)
                if item.get("dnr_promotion"):
                    dnr_promotion = item["dnr_promotion"]
                    if dnr_promotion:
                        # Handle great deal promotion
                        if dnr_promotion.get("great_deal"):
                            great_deal = dnr_promotion["great_deal"]
                            # great_deal can be either dict or list
                            if isinstance(great_deal, dict):
                                if great_deal.get("price") or great_deal.get("old_price"):
                                    product["great_deal"] = {
                                        "price": great_deal.get("price"),
                                        "old_price": great_deal.get("old_price")
                                    }
                            elif isinstance(great_deal, list) and len(great_deal) > 0:
                                # If it's a list, take the first item
                                first_deal = great_deal[0]
                                if isinstance(first_deal, dict) and (first_deal.get("price") or first_deal.get("old_price")):
                                    product["great_deal"] = {
                                        "price": first_deal.get("price"),
                                        "old_price": first_deal.get("old_price")
                                    }

                        # Handle free gift promotion
                        if dnr_promotion.get("free_gift"):
                            free_gift = dnr_promotion["free_gift"]
                            # free_gift can be either dict or list
                            if isinstance(free_gift, dict):
                                if free_gift.get("product"):
                                    product["free_gift"] = {
                                        "sku": free_gift["product"].get("sku"),
                                        "name": free_gift["product"].get("name")
                                    }
                            elif isinstance(free_gift, list) and len(free_gift) > 0:
                                # If it's a list, take the first item
                                first_gift = free_gift[0]
                                if isinstance(first_gift, dict) and first_gift.get("product"):
                                    product["free_gift"] = {
                                        "sku": first_gift["product"].get("sku"),
                                        "name": first_gift["product"].get("name")
                                    }

                processed_products.append(product)

            # Client-side price filtering (API price filter may not work reliably)
            if price_min is not None or price_max is not None:
                original_count = len(processed_products)

                def price_in_range(prod):
                    price = prod.get("price") or prod.get("price_details", {}).get("final_price")
                    if price is None:
                        return False
                    if price_min is not None and price < price_min:
                        return False
                    if price_max is not None and price > price_max:
                        return False
                    return True

                processed_products = [p for p in processed_products if price_in_range(p)]
                logger.info(f"Price filter applied: {original_count} -> {len(processed_products)} products (range: {price_min}-{price_max})")

            # Filter for discount search - only keep products with valid promotions
            if search_type == "discount":
                def has_valid_promotion(prod):
                    # Check discount from price_details
                    if prod.get("price_details", {}).get("discount_amount") and prod["price_details"]["discount_amount"] > 0:
                        return True
                    if prod.get("price_details", {}).get("discount_percent") and prod["price_details"]["discount_percent"] > 0:
                        return True
                    # Check dnr_info
                    if prod.get("dnr_info"):
                        return True
                    return False

                original_count = len(processed_products)
                processed_products = [p for p in processed_products if has_valid_promotion(p)]

                if not processed_products:
                    return {
                        "success": False,
                        "message": f"No products with promotions found for '{keyword}' (searched {original_count} products)",
                        "code": "NO_PROMOTION_PRODUCTS",
                        "original_total_count": products.get("total_count", 0),
                    }

                return {
                    "success": True,
                    "data": processed_products,
                    "total_count": len(processed_products),
                    "original_total_count": products.get("total_count", 0),
                    "current_page": page,
                    "keyword": keyword,
                    "keyword_in_vietnamese": keyword_in_vietnamese,
                    "language": language,
                    "search_type": search_type,
                    "message": f"Found {len(processed_products)} products with promotions (out of {original_count} searched)",
                }

            # Normal or bestseller search
            return {
                "success": True,
                "data": processed_products,
                "total_count": products.get("total_count", 0),
                "total_pages": products.get("page_info", {}).get("total_pages", 0),
                "current_page": page,
                "keyword": keyword,
                "keyword_in_vietnamese": keyword_in_vietnamese,
                "language": language,
                "search_type": search_type,
            }

    except httpx.RequestError as e:
        logger.error(f"HTTP request error in search_products: {type(e).__name__}: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"HTTP request error: {type(e).__name__}: {str(e)}",
            "code": "HTTP_ERROR"
        }
    except Exception as e:
        logger.error(f"Unexpected error in search_products: {type(e).__name__}: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Unexpected error: {type(e).__name__}: {str(e)}",
            "code": "UNKNOWN_ERROR"
        }


async def _merge_carts(
    guest_cart_id: str,
    customer_token: str,
    base_url: str = DEFAULT_MMVN_STORE_URL,
    store_id: str = DEFAULT_MMVN_STORE_ID,
) -> Dict[str, Any]:
    """
    Internal helper to merge guest cart into customer cart after login.

    Args:
        guest_cart_id: The guest cart ID to merge from
        customer_token: Customer authentication token
        base_url: Base API URL
        store_id: Store ID

    Returns:
        Dictionary containing success status and merged cart_id
    """
    graphql_mutation = """
        mutation MergeCarts($sourceCartId: String!, $destinationCartId: String!) {
            mergeCarts(
                source_cart_id: $sourceCartId,
                destination_cart_id: $destinationCartId
            ) {
                id
                items {
                    uid
                    quantity
                }
            }
        }
    """

    # First, get customer cart ID (or create one)
    headers = {
        'Store': store_id,
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {customer_token}'
    }

    # Get customer cart query
    get_customer_cart_query = """
        {
            customerCart {
                id
            }
        }
    """

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            graphql_url = f"{base_url.rstrip('/')}/graphql"

            # Get customer cart ID
            response = await client.post(
                graphql_url,
                headers=headers,
                data=json.dumps({"query": get_customer_cart_query})
            )
            res = response.json()

            customer_cart_id = res.get("data", {}).get("customerCart", {}).get("id")

            if not customer_cart_id:
                # If no customer cart exists, create one
                create_cart_response = await client.post(
                    graphql_url,
                    headers=headers,
                    data=json.dumps({"query": "mutation { createEmptyCart }"})
                )
                create_res = create_cart_response.json()
                customer_cart_id = create_res.get("data", {}).get("createEmptyCart")

            if not customer_cart_id:
                return {
                    "success": False,
                    "message": "Failed to get or create customer cart",
                    "code": "NO_CUSTOMER_CART"
                }

            # Now merge the carts
            merge_payload = {
                "query": graphql_mutation,
                "variables": {
                    "sourceCartId": guest_cart_id,
                    "destinationCartId": customer_cart_id
                }
            }

            merge_response = await client.post(
                graphql_url,
                headers=headers,
                data=json.dumps(merge_payload)
            )
            merge_res = merge_response.json()

            if "errors" in merge_res:
                logger.warning(f"Cart merge failed: {merge_res['errors']}")
                # Even if merge fails, return customer cart ID
                return {
                    "success": True,
                    "cart_id": customer_cart_id,
                    "merged": False,
                    "message": "Using customer cart (merge failed)"
                }

            merged_cart = merge_res.get("data", {}).get("mergeCarts", {})
            merged_cart_id = merged_cart.get("id", customer_cart_id)

            logger.info(f"Successfully merged guest cart {guest_cart_id} into customer cart {merged_cart_id}")

            return {
                "success": True,
                "cart_id": merged_cart_id,
                "merged": True,
                "message": "Carts merged successfully"
            }

    except Exception as e:
        logger.error(f"Error merging carts: {e}")
        return {
            "success": False,
            "message": f"Cart merge error: {str(e)}",
            "code": "MERGE_ERROR"
        }


async def _create_cart_internal(
    base_url: str = DEFAULT_MMVN_STORE_URL,
    store_id: str = DEFAULT_MMVN_STORE_ID,
) -> Dict[str, Any]:
    """
    Internal helper function to create an empty shopping cart.
    Automatically uses customer token if user is logged in, otherwise creates guest cart.
    This is not exposed as an MCP tool - it's called automatically by other tools when needed.

    Args:
        base_url: Base API URL (default: https://b2c-mmpro.izysync.com).
        store_id: Store ID (default: "b2c_10010_vi").

    Returns:
        Dictionary containing success status and cart ID if successful.
    """
    global _user_auth

    # Check if user is logged in
    is_authenticated = _user_auth.get("is_logged_in", False)
    auth_token = _user_auth.get("token") if is_authenticated else None

    # Prepare GraphQL mutation
    graphql_mutation = """
        mutation {
            createEmptyCart
        }
    """

    # Prepare payload
    payload = {
        "query": graphql_mutation,
        "variables": {}
    }

    headers = {
        'Store': store_id,
        'Content-Type': 'application/json',
    }

    # Add authorization header if user is logged in
    if auth_token:
        headers['Authorization'] = f'Bearer {auth_token}'
        logger.info("Creating cart for authenticated user")
    else:
        logger.info("Creating cart for guest user")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            graphql_url = f"{base_url.rstrip('/')}/graphql"
            response = await client.post(
                graphql_url,
                headers=headers,
                content=json.dumps(payload)
            )

            # Parse response
            try:
                res = response.json()
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Failed to parse JSON response: {str(e)}",
                    "code": "JSON_PARSE_ERROR"
                }

            # Check for GraphQL errors
            if "errors" in res:
                return {
                    "success": False,
                    "message": f"GraphQL errors: {res['errors']}",
                    "code": "GRAPHQL_ERROR"
                }

            # Check data
            data = res.get("data")
            if not data or not isinstance(data, dict):
                return {
                    "success": False,
                    "message": "Invalid response data",
                    "code": "INVALID_RESPONSE"
                }

            cart_id = data.get("createEmptyCart")
            if not cart_id:
                return {
                    "success": False,
                    "message": "Failed to create cart - no cart ID returned",
                    "code": "NO_CART_ID"
                }

            # Cache the cart_id globally
            global _cart_id_cache
            _cart_id_cache = cart_id

            cart_type = "authenticated" if auth_token else "guest"
            logger.info(f"Cart created successfully: {cart_id} (type: {cart_type})")

            return {
                "success": True,
                "cart_id": cart_id,
                "cart_type": cart_type,
                "message": f"Cart created successfully for {cart_type} user."
            }

    except httpx.RequestError as e:
        return {
            "success": False,
            "message": f"HTTP request error: {str(e)}",
            "code": "HTTP_ERROR"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "code": "UNKNOWN_ERROR"
        }


async def _get_or_create_cart(
    base_url: str = DEFAULT_MMVN_STORE_URL,
    store_id: str = DEFAULT_MMVN_STORE_ID,
    cart_id: Optional[str] = None,
) -> str:
    """
    Get existing cart_id from cache or parameter, or create a new cart if needed.

    Args:
        base_url: Base API URL
        store_id: Store ID
        cart_id: Optional cart_id to use (if provided, skips creation)

    Returns:
        cart_id string

    Raises:
        Exception if cart creation fails
    """
    global _cart_id_cache

    # Priority: provided cart_id > cached cart_id > create new
    if cart_id:
        _cart_id_cache = cart_id
        return cart_id

    if _cart_id_cache:
        return _cart_id_cache

    # Create new cart
    result = await _create_cart_internal(base_url, store_id)
    if not result.get("success"):
        raise Exception(f"Failed to create cart: {result.get('message')}")

    return result["cart_id"]


@mcp.tool()
async def add_product_to_cart(
    sku: str,
    quantity: float = 1.0,
    cart_id: Optional[str] = None,
    base_url: str = DEFAULT_MMVN_STORE_URL,
    store_id: str = DEFAULT_MMVN_STORE_ID,
) -> Dict[str, Any]:
    """
    Add a product to the shopping cart.
    If no cart exists, a new cart will be created automatically.
    Automatically uses authentication token if user is logged in.

    Args:
        sku: Product SKU code (required, format: "441976_24419765").
        quantity: Number of items to add (default: 1.0, can be float for fresh products).
        cart_id: Optional cart ID. If not provided, uses cached cart or creates new one.
        base_url: Base API URL (default: https://b2c-mmpro.izysync.com).
        store_id: Store ID (default: "b2c_10010_vi").

    Returns:
        Dictionary containing success status, cart_id, and updated cart information.
    """
    global _user_auth

    try:
        # Get or create cart
        cart_id = await _get_or_create_cart(base_url, store_id, cart_id)
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to get/create cart: {str(e)}",
            "code": "CART_ERROR"
        }

    # Prepare GraphQL mutation
    graphql_mutation = """
        mutation AddProductsToCart($cartId: String!, $items: [CartItemInput!]!) {
            addProductsToCart(
                cartId: $cartId,
                use_art_no: true,
                cartItems: $items
            ) {
                cart {
                    id
                    total_summary_quantity_including_config
                    items {
                        uid
                        quantity
                        line_item_is_ai
                        product {
                            id
                            uid
                            name
                            ecom_name
                            sku
                            art_no
                            canonical_url
                            small_image { url }
                            mm_brand
                            categories { uid name }
                            price_range {
                                maximum_price {
                                    final_price { value currency }
                                    regular_price { value currency }
                                }
                            }
                            dnr_price {
                                event_name
                                promo_amount
                                promo_label
                                promo_type
                                promo_value
                                qty
                            }
                        }
                        prices {
                            price_including_tax { value currency }
                            discounts {
                                applied_to
                                label
                                amount { value currency }
                            }
                            row_total_including_tax { value currency }
                            total_item_discount { value currency }
                        }
                    }
                    prices {
                        subtotal_including_tax { value currency }
                        subtotal_with_discount_excluding_tax { value currency }
                        discounts { label amount { value currency } }
                        grand_total { value currency }
                    }
                }
                user_errors {
                    code
                    message
                }
            }
        }
    """

    variables = {
        "cartId": cart_id,
        "items": [{"sku": sku, "quantity": quantity, "line_item_is_ai": True}],
    }

    payload = {
        "query": graphql_mutation,
        "variables": variables
    }

    headers = {
        'Store': store_id,
        'Content-Type': 'application/json',
    }

    # Add authorization header if user is logged in
    if _user_auth.get("is_logged_in") and _user_auth.get("token"):
        headers['Authorization'] = f'Bearer {_user_auth["token"]}'
        logger.info(f"Adding product to cart for authenticated user: {_user_auth.get('email')}")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            graphql_url = f"{base_url.rstrip('/')}/graphql"
            response = await client.post(
                graphql_url,
                headers=headers,
                content=json.dumps(payload)
            )

            # Parse response
            try:
                res = response.json()
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Failed to parse JSON response: {str(e)}",
                    "code": "JSON_PARSE_ERROR"
                }

            # Check for GraphQL errors
            if "errors" in res:
                return {
                    "success": False,
                    "message": f"GraphQL errors: {res['errors']}",
                    "code": "GRAPHQL_ERROR"
                }

            # Check data
            data = res.get("data")
            if not data or not isinstance(data, dict):
                return {
                    "success": False,
                    "message": "Invalid response data",
                    "code": "INVALID_RESPONSE"
                }

            add_result = data.get("addProductsToCart", {})
            user_errors = add_result.get("user_errors", [])

            if user_errors:
                return {
                    "success": False,
                    "message": f"User errors: {user_errors}",
                    "user_errors": user_errors,
                    "code": "USER_ERROR"
                }

            cart_data = add_result.get("cart", {})
            if not cart_data:
                return {
                    "success": False,
                    "message": "Failed to add product to cart",
                    "code": "ADD_FAILED"
                }

            return {
                "success": True,
                "cart_id": cart_id,
                "data": cart_data,
                "message": f"Added {quantity} x {sku} to cart successfully",
                "instruction_for_agent": (
                    "When displaying cart after adding product, ALWAYS include SKU for each product. "
                    "Format: '[Product Name] (SKU: [sku]) - Số lượng: [quantity]'. "
                    "This helps users reference products when updating or removing items."
                )
            }

    except httpx.RequestError as e:
        return {
            "success": False,
            "message": f"HTTP request error: {str(e)}",
            "code": "HTTP_ERROR"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "code": "UNKNOWN_ERROR"
        }


async def _view_cart(
    cart_id: Optional[str] = None,
    base_url: str = DEFAULT_MMVN_STORE_URL,
    store_id: str = DEFAULT_MMVN_STORE_ID,
) -> Dict[str, Any]:
    global _user_auth

    try:
        cart_id = await _get_or_create_cart(base_url, store_id, cart_id)
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to get/create cart: {str(e)}",
            "code": "CART_ERROR"
        }

    graphql_query = """
        query GetCartInfo($cartId: String!) {
            cart(cart_id: $cartId) {
                id
                total_summary_quantity_including_config
                items {
                    uid
                    quantity
                    comment
                    product {
                        id
                        uid
                        sku
                        name
                        ecom_name
                        art_no
                        canonical_url
                        small_image { url }
                        mm_brand
                        categories { uid name }
                        price_range {
                            maximum_price {
                                final_price { value currency }
                                regular_price { value currency }
                            }
                        }
                        dnr_price {
                            event_name
                            promo_amount
                            promo_label
                            promo_type
                            promo_value
                            qty
                        }
                    }
                    prices {
                        price_including_tax { value currency }
                        discounts {
                            label
                            amount { value currency }
                        }
                        row_total_including_tax { value currency }
                        total_item_discount { value currency }
                    }
                }
                prices {
                    subtotal_including_tax { value currency }
                    subtotal_with_discount_excluding_tax { value currency }
                    discounts {
                        label
                        amount { value currency }
                    }
                    grand_total { value currency }
                }
            }
        }
    """

    variables = {"cartId": cart_id}
    payload = {"query": graphql_query, "variables": variables}
    headers = {'Store': store_id, 'Content-Type': 'application/json'}

    if _user_auth.get("is_logged_in") and _user_auth.get("token"):
        headers['Authorization'] = f'Bearer {_user_auth["token"]}'

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            graphql_url = f"{base_url.rstrip('/')}/graphql"
            response = await client.post(graphql_url, headers=headers, content=json.dumps(payload))

            try:
                res = response.json()
            except Exception as e:
                return {"success": False, "message": f"Failed to parse JSON response: {str(e)}", "code": "JSON_PARSE_ERROR"}

            if "errors" in res:
                return {"success": False, "message": f"GraphQL errors: {res['errors']}", "code": "GRAPHQL_ERROR"}

            data = res.get("data")
            if not data or not isinstance(data, dict):
                return {"success": False, "message": "Invalid response data", "code": "INVALID_RESPONSE"}

            cart_data = data.get("cart", {})
            if not cart_data:
                return {"success": False, "message": "Cart not found", "code": "CART_NOT_FOUND"}

            return {
                "success": True,
                "cart_id": cart_id,
                "data": cart_data,
                "message": "Cart retrieved successfully",
                "instruction_for_agent": (
                    "When displaying cart contents to the user, ALWAYS include the SKU for each product. "
                    "Format: '[Product Name] (SKU: [sku])'. "
                    "Example: 'Sốt thịt bò Golden Farm, chai 370g (SKU: 441976_24419765)'. "
                    "This helps users reference products when updating or removing items from cart."
                )
            }

    except httpx.RequestError as e:
        return {"success": False, "message": f"HTTP request error: {str(e)}", "code": "HTTP_ERROR"}
    except Exception as e:
        return {"success": False, "message": f"Unexpected error: {str(e)}", "code": "UNKNOWN_ERROR"}


@mcp.tool()
async def view_cart(
    cart_id: Optional[str] = None,
    base_url: str = DEFAULT_MMVN_STORE_URL,
    store_id: str = DEFAULT_MMVN_STORE_ID,
) -> Dict[str, Any]:
    """
    View the current contents of a shopping cart.
    If no cart exists, a new empty cart will be created automatically.
    Automatically uses authentication token if user is logged in.

    Args:
        cart_id: Optional cart ID. If not provided, uses cached cart or creates new one.
        base_url: Base API URL (default: https://b2c-mmpro.izysync.com).
        store_id: Store ID (default: "b2c_10010_vi").

    Returns:
        Dictionary containing cart_id, cart items, totals, and pricing information.
    """
    return await _view_cart(cart_id=cart_id, base_url=base_url, store_id=store_id)


@mcp.tool()
async def update_cart_item(
    cart_item_uid: str,
    quantity: float,
    cart_id: Optional[str] = None,
    base_url: str = DEFAULT_MMVN_STORE_URL,
    store_id: str = DEFAULT_MMVN_STORE_ID,
) -> Dict[str, Any]:
    """
    Update the quantity of an item in the shopping cart.
    Automatically uses authentication token if user is logged in.

    Args:
        cart_item_uid: Cart item unique ID from view_cart (required).
        quantity: New quantity (can be float for fresh products).
        cart_id: Optional cart ID. If not provided, uses cached cart.
        base_url: Base API URL (default: https://b2c-mmpro.izysync.com).
        store_id: Store ID (default: "b2c_10010_vi").

    Returns:
        Dictionary containing success status, cart_id, and updated cart information.
    """
    global _user_auth

    try:
        # Get or create cart
        cart_id = await _get_or_create_cart(base_url, store_id, cart_id)
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to get/create cart: {str(e)}",
            "code": "CART_ERROR"
        }

    # Prepare GraphQL mutation
    graphql_mutation = """
        mutation UpdateCartItems($cartId: String!, $items: [CartItemUpdateInput!]!) {
            updateCartItems(input: { cart_id: $cartId, cart_items: $items }) {
                cart {
                    id
                    total_summary_quantity_including_config
                    items {
                        uid
                        quantity
                        product {
                            id
                            uid
                            sku
                            name
                            ecom_name
                            art_no
                            canonical_url
                            small_image { url }
                            mm_brand
                            categories { uid name }
                            price_range {
                                maximum_price {
                                    final_price { value currency }
                                    regular_price { value currency }
                                }
                            }
                            dnr_price {
                                event_name
                                promo_amount
                                promo_label
                                promo_type
                                promo_value
                                qty
                            }
                        }
                        prices {
                            price_including_tax { value currency }
                            discounts {
                                label
                                amount { value currency }
                            }
                            row_total_including_tax { value currency }
                            total_item_discount { value currency }
                        }
                    }
                    prices {
                        subtotal_including_tax { value currency }
                        subtotal_with_discount_excluding_tax { value currency }
                        discounts {
                            label
                            amount { value currency }
                        }
                        grand_total { value currency }
                    }
                }
                user_errors {
                    code
                    message
                }
            }
        }
    """

    variables = {
        "cartId": cart_id,
        "items": [{"cart_item_uid": cart_item_uid, "quantity": quantity}],
    }

    payload = {
        "query": graphql_mutation,
        "variables": variables
    }

    headers = {
        'Store': store_id,
        'Content-Type': 'application/json',
    }

    # Add authorization header if user is logged in
    if _user_auth.get("is_logged_in") and _user_auth.get("token"):
        headers['Authorization'] = f'Bearer {_user_auth["token"]}'

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            graphql_url = f"{base_url.rstrip('/')}/graphql"
            response = await client.post(
                graphql_url,
                headers=headers,
                content=json.dumps(payload)
            )

            # Parse response
            try:
                res = response.json()
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Failed to parse JSON response: {str(e)}",
                    "code": "JSON_PARSE_ERROR"
                }

            # Check for GraphQL errors
            if "errors" in res:
                return {
                    "success": False,
                    "message": f"GraphQL errors: {res['errors']}",
                    "code": "GRAPHQL_ERROR"
                }

            # Check data
            data = res.get("data")
            if not data or not isinstance(data, dict):
                return {
                    "success": False,
                    "message": "Invalid response data",
                    "code": "INVALID_RESPONSE"
                }

            update_result = data.get("updateCartItems", {})
            user_errors = update_result.get("user_errors", [])

            if user_errors:
                return {
                    "success": False,
                    "message": f"User errors: {user_errors}",
                    "user_errors": user_errors,
                    "code": "USER_ERROR"
                }

            cart_data = update_result.get("cart", {})
            if not cart_data:
                return {
                    "success": False,
                    "message": "Failed to update cart item",
                    "code": "UPDATE_FAILED"
                }

            return {
                "success": True,
                "cart_id": cart_id,
                "data": cart_data,
                "message": f"Updated cart item to quantity {quantity}",
                "instruction_for_agent": (
                    "When displaying updated cart, ALWAYS include SKU for each product. "
                    "Format: '[Product Name] (SKU: [sku]) - Số lượng: [quantity]'. "
                    "This helps users reference products when updating or removing items."
                )
            }

    except httpx.RequestError as e:
        return {
            "success": False,
            "message": f"HTTP request error: {str(e)}",
            "code": "HTTP_ERROR"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "code": "UNKNOWN_ERROR"
        }


async def _remove_item_from_cart(
    cart_item_uid: str,
    cart_id: Optional[str] = None,
    base_url: str = DEFAULT_MMVN_STORE_URL,
    store_id: str = DEFAULT_MMVN_STORE_ID,
) -> Dict[str, Any]:
    global _user_auth

    try:
        cart_id = await _get_or_create_cart(base_url, store_id, cart_id)
    except Exception as e:
        return {"success": False, "message": f"Failed to get/create cart: {str(e)}", "code": "CART_ERROR"}

    graphql_mutation = """
        mutation RemoveItemFromCart($cartId: String!, $cartItemId: ID!) {
            removeItemFromCart(input: { cart_id: $cartId, cart_item_uid: $cartItemId }) {
                cart {
                    id
                    total_summary_quantity_including_config
                    items {
                        uid
                        quantity
                        product {
                            id
                            uid
                            name
                            ecom_name
                            sku
                            art_no
                            canonical_url
                            small_image { url }
                            mm_brand
                            categories { uid name }
                            price_range {
                                maximum_price {
                                    final_price { value currency }
                                    regular_price { value currency }
                                }
                            }
                            dnr_price {
                                event_name
                                promo_amount
                                promo_label
                                promo_type
                                promo_value
                                qty
                            }
                        }
                        prices {
                            price_including_tax { value currency }
                            discounts {
                                label
                                amount { value currency }
                            }
                            row_total_including_tax { value currency }
                            total_item_discount { value currency }
                        }
                    }
                    prices {
                        subtotal_including_tax { value currency }
                        subtotal_with_discount_excluding_tax { value currency }
                        discounts { label amount { value currency } }
                        grand_total { value currency }
                    }
                }
            }
        }
    """

    variables = {"cartId": cart_id, "cartItemId": cart_item_uid}
    payload = {"query": graphql_mutation, "variables": variables}
    headers = {'Store': store_id, 'Content-Type': 'application/json'}

    if _user_auth.get("is_logged_in") and _user_auth.get("token"):
        headers['Authorization'] = f'Bearer {_user_auth["token"]}'

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            graphql_url = f"{base_url.rstrip('/')}/graphql"
            response = await client.post(graphql_url, headers=headers, content=json.dumps(payload))

            try:
                res = response.json()
            except Exception as e:
                return {"success": False, "message": f"Failed to parse JSON response: {str(e)}", "code": "JSON_PARSE_ERROR"}

            if "errors" in res:
                return {"success": False, "message": f"GraphQL errors: {res['errors']}", "code": "GRAPHQL_ERROR"}

            data = res.get("data")
            if not data or not isinstance(data, dict):
                return {"success": False, "message": "Invalid response data", "code": "INVALID_RESPONSE"}

            remove_result = data.get("removeItemFromCart", {})
            cart_data = remove_result.get("cart", {})

            if not cart_data:
                return {"success": False, "message": "Failed to remove item from cart", "code": "REMOVE_FAILED"}

            return {
                "success": True,
                "cart_id": cart_id,
                "data": cart_data,
                "message": "Item removed from cart successfully",
                "instruction_for_agent": (
                    "When displaying cart after removing item, ALWAYS include SKU for remaining products. "
                    "Format: '[Product Name] (SKU: [sku]) - Số lượng: [quantity]'. "
                    "If cart is empty, inform user and suggest browsing products."
                )
            }

    except httpx.RequestError as e:
        return {"success": False, "message": f"HTTP request error: {str(e)}", "code": "HTTP_ERROR"}
    except Exception as e:
        return {"success": False, "message": f"Unexpected error: {str(e)}", "code": "UNKNOWN_ERROR"}


@mcp.tool()
async def remove_item_from_cart(
    cart_item_uid: str,
    cart_id: Optional[str] = None,
    base_url: str = DEFAULT_MMVN_STORE_URL,
    store_id: str = DEFAULT_MMVN_STORE_ID,
) -> Dict[str, Any]:
    """
    Remove an item from the shopping cart.
    Automatically uses authentication token if user is logged in.

    Args:
        cart_item_uid: Cart item unique ID from view_cart (required).
        cart_id: Optional cart ID. If not provided, uses cached cart.
        base_url: Base API URL (default: https://b2c-mmpro.izysync.com).
        store_id: Store ID (default: "b2c_10010_vi").

    Returns:
        Dictionary containing success status, cart_id, and updated cart information.
    """
    return await _remove_item_from_cart(
        cart_item_uid=cart_item_uid, cart_id=cart_id, base_url=base_url, store_id=store_id
    )


@mcp.tool()
async def clear_cart(
    cart_id: Optional[str] = None,
    base_url: str = DEFAULT_MMVN_STORE_URL,
    store_id: str = DEFAULT_MMVN_STORE_ID,
) -> Dict[str, Any]:
    """
    Remove all items from the shopping cart (clear entire cart).
    Automatically uses authentication token if user is logged in.

    Args:
        cart_id: Optional cart ID. If not provided, uses cached cart.
        base_url: Base API URL (default: https://b2c-mmpro.izysync.com).
        store_id: Store ID (default: "b2c_10010_vi").

    Returns:
        Dictionary containing success status and empty cart information.
    """
    global _user_auth

    try:
        cart_id = await _get_or_create_cart(base_url, store_id, cart_id)
    except Exception as e:
        return {"success": False, "message": f"Failed to get/create cart: {str(e)}", "code": "CART_ERROR"}

    graphql_mutation = """
        mutation removeAllItemsFromCart($cartId: String!) {
            removeAllCartItems(input: {cart_id: $cartId}) {
                success
            }
        }
    """

    payload = {"query": graphql_mutation, "variables": {"cartId": cart_id}}
    headers = {'Store': store_id, 'Content-Type': 'application/json'}

    if _user_auth.get("is_logged_in") and _user_auth.get("token"):
        headers['Authorization'] = f'Bearer {_user_auth["token"]}'

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            graphql_url = f"{base_url.rstrip('/')}/graphql"
            response = await client.post(graphql_url, headers=headers, content=json.dumps(payload))

            try:
                res = response.json()
            except Exception as e:
                return {"success": False, "message": f"Failed to parse JSON response: {str(e)}", "code": "JSON_PARSE_ERROR"}

            if "errors" in res:
                return {"success": False, "message": f"GraphQL errors: {res['errors']}", "code": "GRAPHQL_ERROR"}

            data = res.get("data", {})
            result = data.get("removeAllCartItems", {})

            if not result.get("success"):
                return {"success": False, "message": "Failed to clear cart", "code": "CLEAR_FAILED"}

            return {
                "success": True,
                "cart_id": cart_id,
                "message": "Cart cleared successfully.",
                "instruction_for_agent": (
                    "Inform user that all items have been removed from their cart. "
                    "Suggest browsing products or searching for items to add to cart."
                )
            }

    except httpx.RequestError as e:
        return {"success": False, "message": f"HTTP request error: {str(e)}", "code": "HTTP_ERROR"}
    except Exception as e:
        return {"success": False, "message": f"Unexpected error: {str(e)}", "code": "UNKNOWN_ERROR"}


@mcp.tool()
async def get_product_categories() -> Dict[str, Any]:
    """
    Get list of all available product categories in MM Mega Market Vietnam.

    Returns:
        Dictionary containing category list with UIDs and usage information.
    """

    # Full category mapping from MMVN_MAIN_CATEGORY_MAP
    categories = {
        "categories": {
            "MjUwOTg=": "Đồ gia dụng",
            "MjQ5NTc=": "Chăm sóc cá nhân",
            "MjQ4ODI=": "Bánh kẹo các loại",
            "MjUzOTM=": "Thực phẩm tươi sống",
            "MjUwMzE=": "Dầu ăn - Gia vị - Nước chấm",
            "MjUyMzQ=": "Đồ hộp - Đồ khô",
            "MjU0MzE=": "Vệ sinh nhà cửa",
            "MjQ5MjY=": "Bơ - Trứng - Sữa",
            "MjUzMjU=": "Nước giải khát",
            "MjUyODc=": "Đồ uống có cồn",
            "MjUzMDY=": "Đồ uống đóng hộp",
            "MjUzNjE=": "Thực phẩm đông lạnh",
            "MjUwODU=": "Đồ ăn chế biến",
            "MjUzNDU=": "Thiết bị gia dụng - Điện tử",
            "MjU1NzE=": "Khuyến mãi",
            "MjUwMjI=": "Chăm sóc thú cưng",
            "Mjc1ODk=": "Top Pick Tạp Hóa",
            "MjUzNTU=": "Thực phẩm chức năng",
            "MjU1NzU=": "Unilever",
            "MjU1ODc=": "Thương hiệu riêng",
            "Mjc3NTY=": "Anchor",
            "Mzc3Mjc=": "Rau củ quả - Trái cây - Hoa tươi",
        },
        "category_names": [
            "Đồ gia dụng",
            "Chăm sóc cá nhân",
            "Bánh kẹo các loại",
            "Thực phẩm tươi sống",
            "Dầu ăn - Gia vị - Nước chấm",
            "Đồ hộp - Đồ khô",
            "Vệ sinh nhà cửa",
            "Bơ - Trứng - Sữa",
            "Nước giải khát",
            "Đồ uống có cồn",
            "Đồ uống đóng hộp",
            "Thực phẩm đông lạnh",
            "Đồ ăn chế biến",
            "Thiết bị gia dụng - Điện tử",
            "Khuyến mãi",
            "Chăm sóc thú cưng",
            "Top Pick Tạp Hóa",
            "Thực phẩm chức năng",
            "Unilever",
            "Thương hiệu riêng",
            "Anchor",
            "Rau củ quả - Trái cây - Hoa tươi",
        ],
        "description": "Complete list of product categories in MM Mega Market Vietnam with their UIDs",
        "usage": "Use category UIDs (keys) in the 'category' parameter of search_products tool. Category names are provided for reference."
    }

    return categories


@mcp.tool()
async def track_order(
    order_number: str,
    email: str,
    base_url: Optional[str] = None,
    store_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Track guest order status using order number and email (no authentication required).

    This tool allows guest customers to check their order status without logging in.
    It retrieves order details including status, delivery information, items, and total.
    The tool automatically uses the currently selected store.

    ⚠️ IMPORTANT - DO NOT USE THIS TOOL FOR ORDER CANCELLATION:
    - This tool is ONLY for viewing/tracking order status
    - When customer wants to CANCEL an order, DO NOT call this tool
    - Instead, direct them to contact customer support via hotline 1800 646878 or email contactus@mmvietnam.com
    - MM Mega Market does NOT support self-service order cancellation

    Args:
        order_number: Order number to track (required, e.g., "191000000069" or "#191000000069")
        email: Email address used during checkout (required, e.g., "customer@example.com")
        base_url: Base API URL (optional, uses selected store if not provided)
        store_id: Store ID (optional, uses selected store if not provided)

    Returns:
        Dictionary containing:
        - success: Boolean indicating if order was found
        - data: Order details including status, items, shipping, payment info
        - message: Human-readable status message
        - code: Response code (SUCCESS, ORDER_NOT_FOUND, INVALID_CREDENTIALS, etc.)

    Example:
        track_order(order_number="191000000069", email="customer@example.com")
    """
    global _selected_store

    # Use selected store if not explicitly provided
    if store_id is None:
        store_id = _selected_store["store_id"]
    if base_url is None:
        base_url = _selected_store["base_url"]

    # Validate inputs
    if not order_number or not email:
        return {
            "success": False,
            "message": "Both order_number and email are required",
            "code": "MISSING_PARAMETERS"
        }

    # Remove # prefix if present
    order_number = order_number.strip().lstrip('#')
    email = email.strip().lower()

    # GraphQL query for order tracking
    graphql_query = """
        query orderTracking($order_number: String!, $email: String!) {
            orderTracking(order_number: $order_number, email: $email) {
                id
                number
                email
                order_date
                status
                status_code
                state
                delivery_information {
                    delivery_date
                    delivery_from
                    delivery_to
                }
                items {
                    id
                    product_name
                    product_sku
                    quantity_ordered
                    product_sale_price {
                        currency
                        value
                    }
                    product {
                        id
                        uid
                        ecom_name
                        unit_ecom
                        small_image {
                            url
                        }
                    }
                }
                shipping_address {
                    firstname
                    street
                    city
                    district
                    ward
                    telephone
                    country_code
                }
                billing_address {
                    firstname
                    street
                    city
                    district
                    ward
                    telephone
                    country_code
                }
                payment_methods {
                    name
                    type
                }
                shipping_method
                shipments {
                    id
                    tracking {
                        number
                    }
                }
                total {
                    subtotal {
                        currency
                        value
                    }
                    total_shipping {
                        currency
                        value
                    }
                    total_tax {
                        currency
                        value
                    }
                    grand_total {
                        currency
                        value
                    }
                    discounts {
                        label
                        amount {
                            currency
                            value
                        }
                    }
                }
            }
        }
    """

    variables = {
        "order_number": order_number,
        "email": email
    }

    payload = {
        "query": graphql_query,
        "variables": variables
    }

    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:139.0) Gecko/20100101 Firefox/139.0',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Content-Type': 'application/json',
        'Store': store_id,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            graphql_url = f"{base_url.rstrip('/')}/graphql"
            response = await client.post(
                graphql_url,
                headers=headers,
                content=json.dumps(payload)
            )

            # Parse response
            try:
                res = response.json()
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Failed to parse JSON response: {str(e)}",
                    "code": "JSON_PARSE_ERROR"
                }

            # Check for GraphQL errors
            if "errors" in res:
                error_messages = [err.get("message", "") for err in res.get("errors", [])]
                return {
                    "success": False,
                    "message": f"Invalid order number or email: {', '.join(error_messages)}",
                    "code": "INVALID_CREDENTIALS"
                }

            # Check data
            order_data = res.get("data", {}).get("orderTracking")
            if not order_data:
                return {
                    "success": False,
                    "message": f"Order '{order_number}' not found with the provided email",
                    "code": "ORDER_NOT_FOUND"
                }

            # Process order data
            processed_order = {
                "order_number": order_data.get("number"),
                "email": order_data.get("email"),
                "order_date": order_data.get("order_date"),
                "status": order_data.get("status"),
                "status_code": order_data.get("status_code"),
                "state": order_data.get("state"),
            }

            # Add delivery information
            if order_data.get("delivery_information"):
                delivery_info = order_data["delivery_information"]
                processed_order["delivery"] = {
                    "date": delivery_info.get("delivery_date"),
                    "time_from": delivery_info.get("delivery_from"),
                    "time_to": delivery_info.get("delivery_to"),
                }

            # Add items (simplified)
            items = order_data.get("items", [])
            processed_order["items_count"] = len(items)
            processed_order["items"] = []
            for item in items:
                # Get product name - prioritize ecom_name from product object
                product_name = item.get("product_name")
                if item.get("product") and item["product"].get("ecom_name"):
                    product_name = item["product"]["ecom_name"]

                processed_item = {
                    "name": product_name,
                    "sku": item.get("product_sku"),
                    "quantity": item.get("quantity_ordered"),
                }

                # Add price if available
                if item.get("product_sale_price"):
                    price_data = item["product_sale_price"]
                    processed_item["price"] = {
                        "currency": price_data.get("currency", "VND"),
                        "value": price_data.get("value")
                    }

                # Add product image if available
                if item.get("product") and item["product"].get("small_image"):
                    processed_item["image_url"] = item["product"]["small_image"].get("url")

                processed_order["items"].append(processed_item)

            # Add shipping address
            if order_data.get("shipping_address"):
                addr = order_data["shipping_address"]
                processed_order["shipping_address"] = {
                    "name": addr.get("firstname"),
                    "street": addr.get("street"),
                    "ward": addr.get("ward"),
                    "district": addr.get("district"),
                    "city": addr.get("city"),
                    "phone": addr.get("telephone"),
                }

            # Add payment method
            if order_data.get("payment_methods") and len(order_data["payment_methods"]) > 0:
                payment = order_data["payment_methods"][0]
                processed_order["payment_method"] = {
                    "name": payment.get("name"),
                    "type": payment.get("type")
                }

            # Add shipping method
            processed_order["shipping_method"] = order_data.get("shipping_method")

            # Add tracking number if available
            if order_data.get("shipments") and len(order_data["shipments"]) > 0:
                shipment = order_data["shipments"][0]
                if shipment.get("tracking"):
                    processed_order["tracking_number"] = shipment["tracking"].get("number")

            # Add total
            if order_data.get("total"):
                total = order_data["total"]
                processed_order["total"] = {
                    "currency": total.get("grand_total", {}).get("currency", "VND"),
                    "subtotal": total.get("subtotal", {}).get("value"),
                    "shipping": total.get("total_shipping", {}).get("value"),
                    "tax": total.get("total_tax", {}).get("value"),
                    "grand_total": total.get("grand_total", {}).get("value"),
                }

                # Add discounts if any
                if total.get("discounts"):
                    processed_order["total"]["discounts"] = [
                        {
                            "label": disc.get("label"),
                            "amount": disc.get("amount", {}).get("value")
                        }
                        for disc in total["discounts"]
                    ]

            return {
                "success": True,
                "data": processed_order,
                "message": f"Order '{order_number}' found. Status: {processed_order.get('status')}",
                "code": "SUCCESS",
                "instruction_for_agent": (
                    "Khi hiển thị địa chỉ giao hàng, PHẢI hiển thị ĐẦY ĐỦ theo thứ tự: "
                    "Số nhà/Đường (street), Phường/Xã (ward), Quận/Huyện (district), Thành phố (city). "
                    "KHÔNG BAO GIỜ bỏ qua thông tin Phường/Xã và Quận/Huyện."
                )
            }

    except httpx.RequestError as e:
        return {
            "success": False,
            "message": f"HTTP request error: {str(e)}",
            "code": "HTTP_ERROR"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "code": "UNKNOWN_ERROR"
        }


@mcp.tool()
async def check_my_orders(
    order_number: Optional[str] = None,
    create_date_from: Optional[str] = None,
    create_date_to: Optional[str] = None,
    status: Optional[str] = None,
    current_page: int = 1,
    page_size: int = 10,
    base_url: Optional[str] = None,
    store_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Check order history for logged-in customers (requires authentication).

    This tool retrieves order information for authenticated users only.
    Users must login first using login_user tool to get authentication token.
    The tool automatically uses the stored authentication token from global state.

    ⚠️ CRITICAL - DO NOT USE THIS TOOL FOR ORDER CANCELLATION:
    - This tool is ONLY for viewing/checking order history and status
    - When customer wants to CANCEL an order (keywords: "hủy đơn", "cancel order", "muốn hủy"), DO NOT call this tool
    - Instead, respond immediately: "Dạ, hiện tại hệ thống không hỗ trợ tự hủy đơn hàng ạ. Để hủy đơn, anh/chị vui lòng liên hệ bộ phận Chăm sóc Khách hàng qua hotline 1800 646878 hoặc email contactus@mmvietnam.com để được hỗ trợ ạ."
    - MM Mega Market does NOT support self-service order cancellation

    IMPORTANT - Status Filter Usage:
    When user asks about specific order status (đã hủy, đã giao, đang giao, etc.),
    you MUST use the predefined constants. DO NOT try to figure out status codes yourself.

    Use these EXACT values for status parameter:
    - Canceled/Rejected orders (đơn đã hủy, đơn bị hủy):
      status="backorder_ccod,canceled,closed,deleted_ccod"

    - Delivered orders (đơn đã giao, đơn đã nhận):
      status="complete,completed_ccod"

    - Delivering orders (đơn đang giao, đơn đang vận chuyển):
      status="invoiced_ccod,in_shipment_ccod,picked_ccod,picking_ccod"

    - Pending orders (đơn chờ xử lý, đơn đã ghi nhận):
      status="pending,pending_ccod"

    - Processing orders (đơn đang xử lý):
      status="confirmed_ccod,order_error,processing"

    - Waiting to cancel (đơn chờ hủy):
      status="waiting_cancel"

    - Awaiting payment (đơn chờ thanh toán):
      status="pending_payment"

    Args:
        order_number: Specific order number to search (optional, e.g., "101000002403")
                     If provided, returns details for this specific order.
                     If not provided, returns list of all orders with optional filters.
        create_date_from: Filter orders from this date. Format: "YYYY-MM-DD" (e.g., "2025-11-05")
                         Filters orders created on or after this date (optional)
        create_date_to: Filter orders to this date. Format: "YYYY-MM-DD" (e.g., "2025-11-05")
                       Filters orders created on or before this date (optional)
        status: Filter by order status - USE THE EXACT VALUES LISTED ABOVE
        current_page: Page number for pagination (default: 1)
        page_size: Number of orders per page (default: 10, max: 100)
        base_url: Base API URL (optional, uses selected store if not provided)
        store_id: Store ID (optional, uses selected store if not provided)

    Returns:
        Dictionary containing:
        - success: Boolean indicating if operation succeeded
        - data: Order information (single order or list of orders)
        - message: Status message
        - code: Response code (SUCCESS, NOT_LOGGED_IN, ORDER_NOT_FOUND, etc.)

    Examples:
        # Get all orders for logged-in user
        check_my_orders()

        # Get specific order
        check_my_orders(order_number="101000002403")

        # Get canceled orders (CORRECT WAY - use exact status value)
        check_my_orders(status="backorder_ccod,canceled,closed,deleted_ccod")

        # Get delivered orders
        check_my_orders(status="complete,completed_ccod")

        # Get all canceled orders with large page_size to avoid pagination
        check_my_orders(status="backorder_ccod,canceled,closed,deleted_ccod", page_size=100)
    """
    global _selected_store, _user_auth

    # Check if user is logged in
    if not _user_auth.get("is_logged_in") or not _user_auth.get("token"):
        return {
            "success": False,
            "message": "User must be logged in to check orders. Please use login_user tool first.",
            "instruction_for_agent": "User needs to login first using login_user tool to check their order history.",
            "code": "NOT_LOGGED_IN"
        }

    # Use selected store if not explicitly provided
    if store_id is None:
        store_id = _selected_store["store_id"]
    if base_url is None:
        base_url = _selected_store["base_url"]

    # Get authentication token
    auth_token = _user_auth.get("token")

    # Case 1: Get specific order by number
    if order_number:
        order_number = order_number.strip().lstrip('#')

        graphql_query = """
            query GetCustomerOrders($filter: CustomerOrdersFilterInput, $pageSize: Int!) {
                customer {
                    id
                    firstname
                    email
                    orders(filter: $filter, pageSize: $pageSize) {
                        items {
                            id
                            number
                            order_date
                            customer_no
                            delivery_information {
                                delivery_date
                                delivery_from
                                delivery_to
                            }
                            vat_information {
                                company_address
                                company_name
                                company_vat_number
                                customer_vat_id
                            }
                            invoices {
                                id
                            }
                            items {
                                id
                                product_name
                                product_sale_price {
                                    currency
                                    value
                                }
                                product_sku
                                product_url_key
                                selected_options {
                                    label
                                    value
                                }
                                quantity_ordered
                                product {
                                    id
                                    uid
                                    unit_ecom
                                    ecom_name
                                    thumbnail {
                                        url
                                    }
                                    small_image {
                                        url
                                    }
                                }
                            }
                            billing_address {
                                firstname
                                country_code
                                city
                                district
                                ward
                                street
                                telephone
                            }
                            payment_methods {
                                name
                                type
                                additional_data {
                                    name
                                    value
                                }
                            }
                            shipments {
                                id
                                tracking {
                                    number
                                }
                            }
                            shipping_address {
                                firstname
                                country_code
                                city
                                district
                                ward
                                street
                                telephone
                            }
                            shipping_method
                            status
                            status_code
                            state
                            total {
                                discounts {
                                    label
                                    amount {
                                        currency
                                        value
                                    }
                                }
                                grand_total {
                                    currency
                                    value
                                }
                                subtotal {
                                    currency
                                    value
                                }
                                total_shipping {
                                    currency
                                    value
                                }
                                total_tax {
                                    currency
                                    value
                                }
                            }
                        }
                        page_info {
                            current_page
                            total_pages
                        }
                        total_count
                    }
                }
            }
        """

        filter_obj = {
            "number": {"eq": order_number},
            "createDateFrom": {"gteq": ""},
            "createDateTo": {"lteq": ""},
            "status": {"eq": ""}
        }

        variables = {
            "pageSize": 1,
            "filter": filter_obj
        }

    # Case 2: Get list of orders with optional filters
    else:
        graphql_query = """
            query GetCustomerOrders($currentPage: Int!, $pageSize: Int!, $filter: CustomerOrdersFilterInput) {
                customer {
                    orders(currentPage: $currentPage, pageSize: $pageSize, filter: $filter) {
                        items {
                            id
                            number
                            order_date
                            status
                            status_code
                            state
                            delivery_information {
                                delivery_date
                                delivery_from
                                delivery_to
                            }
                            invoices {
                                id
                            }
                            items {
                                id
                                product_name
                                product_sale_price {
                                    currency
                                    value
                                }
                                product_sku
                                product_url_key
                                selected_options {
                                    label
                                    value
                                }
                                quantity_ordered
                                product {
                                    id
                                    uid
                                    ecom_name
                                    thumbnail {
                                        url
                                    }
                                    small_image {
                                        url
                                    }
                                }
                            }
                            billing_address {
                                city
                                district
                                ward
                                country_code
                                firstname
                                postcode
                                region
                                street
                                telephone
                            }
                            payment_methods {
                                name
                                type
                                additional_data {
                                    name
                                    value
                                }
                            }
                            shipments {
                                id
                                tracking {
                                    number
                                }
                            }
                            shipping_address {
                                city
                                district
                                ward
                                country_code
                                firstname
                                postcode
                                region
                                street
                                telephone
                            }
                            shipping_method
                            total {
                                discounts {
                                    amount {
                                        currency
                                        value
                                    }
                                }
                                grand_total {
                                    currency
                                    value
                                }
                                subtotal {
                                    currency
                                    value
                                }
                                total_shipping {
                                    currency
                                    value
                                }
                                total_tax {
                                    currency
                                    value
                                }
                            }
                        }
                        page_info {
                            current_page
                            total_pages
                        }
                        total_count
                    }
                }
            }
        """

        # Format dates if provided
        if create_date_from and " " in create_date_from:
            create_date_from = create_date_from.split(" ")[0]
        if create_date_to and " " in create_date_to:
            create_date_to = create_date_to.split(" ")[0]

        filter_obj = {
            "number": {"match": ""},
            "createDateFrom": {"gteq": create_date_from if create_date_from else ""},
            "createDateTo": {"lteq": create_date_to if create_date_to else ""},
            "status": {"eq": status if status else ""}
        }

        variables = {
            "currentPage": current_page,
            "pageSize": page_size,
            "filter": filter_obj
        }

    payload = {
        "query": graphql_query,
        "variables": variables
    }

    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:139.0) Gecko/20100101 Firefox/139.0',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Content-Type': 'application/json',
        'Store': store_id,
        'Authorization': f'Bearer {auth_token}',
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            graphql_url = f"{base_url.rstrip('/')}/graphql"
            response = await client.post(
                graphql_url,
                headers=headers,
                content=json.dumps(payload)
            )

            # Parse response
            try:
                res = response.json()
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Failed to parse JSON response: {str(e)}",
                    "code": "JSON_PARSE_ERROR"
                }

            # Check for GraphQL errors
            if "errors" in res:
                error_messages = [err.get("message", "") for err in res.get("errors", [])]
                return {
                    "success": False,
                    "message": f"API error: {', '.join(error_messages)}",
                    "code": "GRAPHQL_ERROR",
                    "errors": error_messages
                }

            # Extract order data
            customer_data = res.get("data", {}).get("customer")
            if not customer_data:
                return {
                    "success": False,
                    "message": "No customer data in response",
                    "code": "INVALID_RESPONSE"
                }

            orders_data = customer_data.get("orders", {})
            items = orders_data.get("items", [])
            total_count = orders_data.get("total_count", 0)

            # Case 1: Specific order requested
            if order_number:
                if not items:
                    return {
                        "success": False,
                        "message": f"Order '{order_number}' not found",
                        "code": "ORDER_NOT_FOUND"
                    }

                order = items[0]
                return {
                    "success": True,
                    "data": {
                        "order": order,
                        "order_number": order.get("number"),
                        "status": order.get("status"),
                        "status_code": order.get("status_code"),
                        "order_date": order.get("order_date"),
                        "total": order.get("total", {}),
                        "items_count": len(order.get("items", [])),
                        "delivery_info": order.get("delivery_information")
                    },
                    "message": f"Đơn hàng '{order_number}' - Trạng thái: {order.get('status')}",
                    "code": "SUCCESS",
                    "instruction_for_agent": (
                        "Khi hiển thị địa chỉ giao hàng (shipping_address), PHẢI hiển thị ĐẦY ĐỦ theo thứ tự: "
                        "Số nhà/Đường (street), Phường/Xã (ward), Quận/Huyện (district), Thành phố (city). "
                        "KHÔNG BAO GIỜ bỏ qua thông tin Phường/Xã và Quận/Huyện."
                    )
                }

            # Case 2: Order list requested
            if total_count == 0:
                # Build status description for no orders message
                status_desc = ""
                if status:
                    status_map = {
                        "backorder_ccod,canceled,closed,deleted_ccod": "đã hủy",
                        "complete,completed_ccod": "đã giao",
                        "invoiced_ccod,in_shipment_ccod,picked_ccod,picking_ccod": "đang giao",
                        "pending,pending_ccod": "chờ xử lý",
                        "confirmed_ccod,order_error,processing": "đang xử lý",
                        "waiting_cancel": "chờ hủy",
                        "pending_payment": "chờ thanh toán"
                    }
                    status_desc = f" {status_map.get(status, '')}"

                return {
                    "success": True,
                    "data": {
                        "orders": [],
                        "total_count": 0,
                        "page_info": orders_data.get("page_info")
                    },
                    "message": f"Anh/chị chưa có đơn hàng{status_desc} nào",
                    "code": "NO_ORDERS"
                }

            page_info = orders_data.get("page_info", {})
            current_page = page_info.get("current_page", 1)
            total_pages = page_info.get("total_pages", 1)

            instruction = (
                "Khi hiển thị địa chỉ giao hàng (shipping_address) của bất kỳ đơn hàng nào, "
                "PHẢI hiển thị ĐẦY ĐỦ theo thứ tự: Số nhà/Đường (street), Phường/Xã (ward), "
                "Quận/Huyện (district), Thành phố (city). KHÔNG BAO GIỜ bỏ qua thông tin Phường/Xã và Quận/Huyện."
            )

            # Build descriptive message based on status filter
            status_desc = ""
            if status:
                status_map = {
                    "backorder_ccod,canceled,closed,deleted_ccod": "đã hủy",
                    "complete,completed_ccod": "đã giao",
                    "invoiced_ccod,in_shipment_ccod,picked_ccod,picking_ccod": "đang giao",
                    "pending,pending_ccod": "chờ xử lý",
                    "confirmed_ccod,order_error,processing": "đang xử lý",
                    "waiting_cancel": "chờ hủy",
                    "pending_payment": "chờ thanh toán"
                }
                status_desc = f" {status_map.get(status, '')}"

            # Add warning if there are more pages
            if total_pages > current_page:
                instruction += (
                    f"\n\n⚠️ CẢNH BÁO: Đây chỉ là trang {current_page}/{total_pages} "
                    f"(hiển thị {len(items)}/{total_count} đơn hàng). "
                    "Nếu khách hàng yêu cầu THỐNG KÊ TỔNG QUAN hoặc XEM TẤT CẢ đơn hàng, "
                    "bạn PHẢI gọi lại tool với page_size lớn hơn hoặc lặp qua tất cả các trang để có số liệu chính xác!"
                )

            return {
                "success": True,
                "data": {
                    "orders": items,
                    "total_count": total_count,
                    "page_info": page_info,
                    "filters_applied": {
                        "date_from": create_date_from,
                        "date_to": create_date_to,
                        "status": status
                    }
                },
                "message": f"Tìm thấy tổng cộng {total_count} đơn hàng{status_desc}. Hiện đang hiển thị {len(items)} đơn hàng (trang {current_page}/{total_pages}).",
                "code": "SUCCESS",
                "instruction_for_agent": instruction
            }

    except httpx.RequestError as e:
        return {
            "success": False,
            "message": f"HTTP request error: {str(e)}",
            "code": "HTTP_ERROR"
        }
    except Exception as e:
        logger.error(f"Error in check_my_orders: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "code": "UNKNOWN_ERROR"
        }
@mcp.tool()
async def select_store(
    store_code: Optional[str] = None,
    base_url: str = DEFAULT_MMVN_STORE_URL,
    store_id: str = "b2c_10010_vi",
) -> Dict[str, Any]:
    """
    Request customer to select a warehouse/supermarket before performing other tasks.

    This tool manages store selection for the customer. When a store is selected,
    it is automatically saved and used by all other tools (search_products, track_order, etc.).

    Workflow:
    1. If store_code is provided: Validate and set the selected store (saves to global state)
    2. If store_code is None: Return list of available stores for customer to choose

    Args:
        store_code: Store code to select (optional, e.g., "10013", "10010")
                   If None, returns list of available stores
        base_url: Base API URL (default: https://b2c-mmpro.izysync.com)
        store_id: Store ID (default: "b2c_10010_vi")

    Returns:
        Dictionary containing:
        - success: Boolean indicating operation status
        - selected_store: Currently selected store info (if store_code provided and valid)
        - available_stores: List of all stores (if store_code is None or invalid)
        - message: Instruction for the agent or user
        - code: Response code (STORE_SELECTED, STORE_LIST_RETURNED, INVALID_STORE, etc.)

    Example:
        # First call - get available stores
        select_store()

        # Second call - select a specific store
        select_store(store_code="10013")
    """
    global _selected_store

    graphql_query = """
        query {
            storeList {
                code
                name
            }
        }
    """

    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:139.0) Gecko/20100101 Firefox/139.0',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Content-Type': 'application/json',
        'Store': store_id,
    }

    try:
        # Fetch store list from API
        async with httpx.AsyncClient(timeout=30.0) as client:
            graphql_url = f"{base_url.rstrip('/')}/graphql"
            response = await client.post(
                graphql_url,
                headers=headers,
                data=json.dumps({"query": graphql_query})
            )

            # Parse response
            try:
                res = response.json()
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Failed to parse JSON response: {str(e)}",
                    "code": "JSON_PARSE_ERROR"
                }

            # Check for GraphQL errors
            if "errors" in res:
                return {
                    "success": False,
                    "message": f"GraphQL errors: {res['errors']}",
                    "code": "GRAPHQL_ERROR"
                }

            # Extract store list
            store_list = res.get("data", {}).get("storeList", [])

            if not store_list:
                return {
                    "success": False,
                    "message": "No stores available",
                    "code": "NO_STORES_AVAILABLE"
                }

            # Format available stores
            available_stores = [
                {
                    "code": store.get("code"),
                    "name": store.get("name")
                }
                for store in store_list
            ]

            # Case 1: No store_code provided - return list of stores for selection
            if store_code is None:
                return {
                    "success": True,
                    "available_stores": available_stores,
                    "total_stores": len(available_stores),
                    "message": (
                        "Please select a store from the available list. "
                        "Call this tool again with the store_code parameter to select a store."
                    ),
                    "instruction_for_agent": (
                        "Ask the user to select their preferred store/warehouse from the list. "
                        "Once they choose, call select_store(store_code='XXXX') to set their selection."
                    ),
                    "code": "STORE_LIST_RETURNED"
                }

            # Case 2: store_code provided - validate and select the store
            store_code = str(store_code).strip()

            # Find the matching store
            selected_store = None
            for store in available_stores:
                if str(store.get("code", "")).strip() == store_code:
                    selected_store = store
                    break

            if not selected_store:
                return {
                    "success": False,
                    "available_stores": available_stores,
                    "message": f"Invalid store code '{store_code}'. Please select from the available stores.",
                    "instruction_for_agent": (
                        f"The store code '{store_code}' is not valid. "
                        "Show the user the list of available stores and ask them to choose again."
                    ),
                    "code": "INVALID_STORE_CODE"
                }

            # Format store_id for state (b2c_XXXXX_vi format)
            store_id_formatted = f"b2c_{selected_store['code']}_vi"

            # ✅ Update global state with selected store
            _selected_store["store_id"] = store_id_formatted
            _selected_store["base_url"] = base_url
            _selected_store["store_code"] = selected_store["code"]
            _selected_store["store_name"] = selected_store["name"]
            _selected_store["is_default"] = False

            logger.info(f"Store selected: {selected_store['name']} (code: {selected_store['code']})")

            return {
                "success": True,
                "selected_store": {
                    "code": selected_store["code"],
                    "name": selected_store["name"],
                    "store_id": store_id_formatted,
                    "base_url": base_url
                },
                "message": f"Cửa hàng '{selected_store['name']}' đã được chọn. Tất cả thao tác tìm kiếm sản phẩm và tra cứu đơn hàng sẽ sử dụng cửa hàng này.",
                "instruction_for_agent": (
                    f"Store '{selected_store['name']}' has been selected and saved. "
                    f"All subsequent operations (search_products, track_order, etc.) will automatically use this store. "
                    f"You can now proceed with product searches and other operations without specifying store_id."
                ),
                "code": "STORE_SELECTED"
            }

    except httpx.RequestError as e:
        return {
            "success": False,
            "message": f"HTTP request error: {str(e)}",
            "code": "HTTP_ERROR"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "code": "UNKNOWN_ERROR"
        }


@mcp.tool()
async def check_store_selection() -> Dict[str, Any]:
    """
    Check if customer has already selected a store/warehouse.

    This is a lightweight tool to verify store selection status without fetching the full store list.
    Use this before calling other product/order tools to ensure a store is selected.

    Returns:
        Dictionary containing:
        - success: Boolean indicating if operation succeeded
        - store_selected: Boolean indicating if a store is currently selected
        - current_store: Current store info (if selected)
        - message: Status message
        - code: Response code

    Example:
        result = check_store_selection()
        if not result["store_selected"]:
            # Call select_store() to let customer choose
            select_store()
    """
    # Note: This is a mock implementation for MCP context
    # In actual Google ADK implementation, this would check tool_context.state
    # For MCP, we return guidance to use select_store if unsure

    return {
        "success": True,
        "store_selected": False,
        "message": (
            "Unable to verify store selection in MCP context. "
            "If the customer hasn't selected a store yet, please call select_store() first."
        ),
        "instruction_for_agent": (
            "In the MCP server context, state management is handled externally. "
            "If you're unsure whether the customer has selected a store:\n"
            "1. Call select_store() without parameters to show available stores\n"
            "2. Ask the customer to choose their preferred store\n"
            "3. Call select_store(store_code='XXXX') with their selection\n"
            "4. Proceed with product searches using the selected store_id"
        ),
        "code": "STATE_CHECK_UNAVAILABLE_IN_MCP"
    }


@mcp.tool()
async def search_knowledge_base(
    query: str,
    language: str = "vi",
    top_k: int = 5,
) -> Dict[str, Any]:
    """
    Search MM Vietnam knowledge base using RAG (Retrieval-Augmented Generation).

    This tool retrieves relevant information from MM Vietnam's company documents including:
    - Company policies (delivery, return/exchange, privacy, payments)
    - Store information (locations, operating hours, contacts)
    - M-Card loyalty program details
    - Purchase procedures and guidelines
    - Terms of service and legal information
    - Any other MM Vietnam company information

    Args:
        query: The search query or question (in Vietnamese or English)
        language: The language for the response (default: "vi" for Vietnamese)
        top_k: Number of most relevant results to return (default: 5, max: 20)

    Returns:
        Dictionary containing:
        - success: Boolean indicating if search was successful
        - results: List of relevant information chunks with scores and metadata
        - count: Number of results found
        - query: The original query
        - language: The response language

    Example queries:
        - "Chính sách giao hàng của MM Việt Nam như thế nào?"
        - "Làm thế nào để đăng ký thẻ M-Card?"
        - "MM Vietnam có những phương thức thanh toán nào?"
    """
    logger.info(f"RAG search request: query='{query[:100]}...', language={language}, top_k={top_k}")

    if not query or not query.strip():
        return {
            "success": False,
            "results": [],
            "count": 0,
            "query": query,
            "language": language,
            "message": "Query cannot be empty",
            "code": "EMPTY_QUERY"
        }

    top_k = max(1, min(top_k, 20))

    try:
        rag = _get_rag_instance()
        results = rag.retrieve(query=query.strip(), top_k=top_k)

        logger.info(f"RAG search completed: found {len(results)} results")

        formatted_results = []
        for idx, result in enumerate(results, 1):
            formatted_result = {
                "rank": idx,
                "score": round(result.get("score", 0.0), 4),
                "text": result.get("text", ""),
                "source": result.get("metadata", {}).get("filename", "Unknown"),
                "chunk_id": result.get("metadata", {}).get("chunk_id"),
                "metadata": result.get("metadata", {})
            }
            formatted_results.append(formatted_result)

        if len(formatted_results) == 0:
            return {
                "success": True,
                "results": [],
                "count": 0,
                "query": query,
                "language": language,
                "message": f"No relevant information found for query: '{query}'",
                "code": "NO_RESULTS"
            }

        return {
            "success": True,
            "results": formatted_results,
            "count": len(formatted_results),
            "query": query,
            "language": language,
            "message": f"Found {len(formatted_results)} relevant results",
            "instruction": (
                f"Use the retrieved information to answer the user's question in {language}. "
                "Cite the source document when possible."
            )
        }

    except Exception as e:
        logger.error(f"Error in RAG search: {e}", exc_info=True)
        return {
            "success": False,
            "results": [],
            "count": 0,
            "query": query,
            "language": language,
            "message": f"Error searching knowledge base: {str(e)}",
            "code": "SEARCH_ERROR",
            "error": str(e)
        }


@mcp.prompt(title="Hỗ trợ khách hàng MM Mega Market")
def customer_support_prompt() -> list[PromptMessage]:
    """
    Prompt chính cho agent hỗ trợ khách hàng MM Mega Market Vietnam.
    """
    return [
        Message(role="user", content=
            "Bạn là trợ lý ảo thông minh của MM Mega Market Vietnam, chuyên hỗ trợ khách hàng mua sắm online.\n\n"
            "**Vai trò của bạn:**\n"
            "- Tư vấn sản phẩm và hỗ trợ tìm kiếm sản phẩm phù hợp\n"
            "- Quản lý giỏ hàng (thêm, xóa, cập nhật sản phẩm)\n"
            "- Tra cứu đơn hàng và trạng thái giao hàng\n"
            "- Tư vấn các chính sách (giao hàng miễn phí, đổi trả và bảo hành, bảo mật thông tin, điều khoản pháp lý), chương trình khuyến mãi và ấn phẩm khuyến mãi, thẻ thành viên MCard (quyền lợi, cách tích điểm), tiêu chuẩn chất lượng sản phẩm, hướng dẫn mua hàng và xuất hóa đơn\n"
            "- Hỗ trợ chọn cửa hàng/kho gần nhất\n\n"
            "**🚨 QUAN TRỌNG - Chính sách HỦY ĐƠN HÀNG (BẮT BUỘC TUÂN THỦ):**\n"
            "- MM Mega Market KHÔNG hỗ trợ khách hàng tự hủy đơn hàng qua hệ thống\n"
            "- Khi khách hàng muốn hủy đơn (từ khóa: 'hủy đơn', 'cancel order', 'tôi muốn hủy', 'hủy đơn hàng'):\n"
            "  + ❌ TUYỆT ĐỐI KHÔNG gọi bất kỳ tool nào (track_order, check_my_orders, search_knowledge_base)\n"
            "  + ❌ KHÔNG kiểm tra trạng thái đơn hàng\n"
            "  + ❌ KHÔNG phân tích xem đơn có thể hủy hay không\n"
            "  + ❌ KHÔNG hỏi thông tin đơn hàng (mã đơn, email)\n"
            "  + ❌ KHÔNG hướng dẫn cách hủy qua web\n"
            "  + ❌ KHÔNG nói 'có thể hủy trước khi xác nhận'\n"
            "  + ✅ Trả lời NGAY LẬP TỨC: 'Dạ, hiện tại hệ thống không hỗ trợ tự hủy đơn hàng ạ. Để hủy đơn, anh/chị vui lòng liên hệ bộ phận Chăm sóc Khách hàng qua hotline 1800 646878 hoặc email contactus@mmvietnam.com để được hỗ trợ ạ.'\n"
            "- Phân biệt: 'hủy đơn' (yêu cầu hủy) ≠ 'xem đơn đã hủy' (xem lịch sử)\n\n"
            "**Nguyên tắc giao tiếp:**\n"
            "1. Luôn thân thiện, nhiệt tình và chuyên nghiệp\n"
            "2. Sử dụng ngôn ngữ theo ngôn ngữ của khách hàng, dễ hiểu\n"
            "3. Xưng hô phù hợp: 'anh/chị' với khách hàng, 'em' với bản thân\n"
            "4. Luôn xác nhận lại thông tin quan trọng\n"
            "5. Cung cấp thông tin chính xác, đầy đủ\n\n"
            "**Quy trình làm việc:**\n"
            "- Khi khách hàng vào, hỏi xem có muốn chọn cửa hàng gần nhất không (nếu cần)\n"
            "- Lắng nghe nhu cầu và sử dụng tools phù hợp\n"
            "- Luôn kiểm tra kết quả trước khi trả lời khách hàng\n"
            "- Đề xuất sản phẩm liên quan hoặc khuyến mãi khi phù hợp\n\n"
            "**Hướng dẫn tra cứu đơn hàng:**\n"
            "1. **Khách hàng CHƯA đăng nhập:**\n"
            "   - Yêu cầu cung cấp: Mã đơn hàng + Email thanh toán\n"
            "   - Sử dụng tool: track_order(order_number, email)\n"
            "   - Câu trả lời mẫu: 'Để kiểm tra thông tin đơn hàng, Anh/Chị cho em biết Mã đơn hàng và Email thanh toán để em hỗ trợ tìm kiếm nhé. Nếu Anh/Chị đã có tài khoản, vui lòng **Đăng nhập** để xem danh sách đơn hàng của mình nhé.'\n\n"
            "2. **Khách hàng ĐÃ đăng nhập:**\n"
            "   - Sử dụng tool: check_my_orders() để xem tất cả đơn hàng\n"
            "   - Hoặc: check_my_orders(order_number='...') để xem đơn hàng cụ thể\n"
            "   - Có thể lọc theo ngày tạo, trạng thái đơn hàng\n"
            "   - **QUAN TRỌNG - Thống kê tổng quan:**\n"
            "     * Khi khách hàng hỏi về tổng quan đơn hàng (ví dụ: 'cho tôi xem tóm lược đơn hàng', 'có bao nhiêu đơn đang xử lý'),\n"
            "       PHẢI lấy TẤT CẢ đơn hàng bằng cách:\n"
            "       - Gọi check_my_orders(page_size=100) để lấy nhiều đơn hơn (nếu total_count <= 100)\n"
            "       - HOẶC lặp qua tất cả các trang nếu total_count > 100\n"
            "     * Ví dụ: Nếu response trả về total_count=30 nhưng page chỉ có 10 items, PHẢI gọi lại với page_size=30 hoặc lớn hơn\n"
            "     * KHÔNG BAO GIỜ thống kê dựa trên page đầu tiên mà không kiểm tra total_count và page_info!\n\n"
            "3. **Hiển thị thông tin đơn hàng:**\n"
            "   Khi hiển thị chi tiết đơn hàng, PHẢI bao gồm:\n"
            "   - Thông tin cơ bản: Mã đơn hàng, Ngày đặt, Trạng thái\n"
            "   - Địa chỉ giao hàng ĐẦY ĐỦ: Tên người nhận, Số điện thoại, Địa chỉ (Đường/Số nhà, Phường/Xã, Quận/Huyện, Thành phố)\n"
            "   - Danh sách sản phẩm: Tên (có link nếu có url_key), SKU, Số lượng, Đơn giá, Thành tiền\n"
            "   - Thông tin thanh toán với TITLE CHUẨN:\n"
            "     * 'Tạm tính' (subtotal)\n"
            "     * 'Phí vận chuyển' (total_shipping)\n"
            "     * 'Thuế VAT' (total_tax)\n"
            "     * 'Giảm giá' (discounts - nếu có)\n"
            "     * 'Tổng cộng' (grand_total)\n"
            "   - Phương thức thanh toán\n"
            "   - Thông tin vận chuyển (nếu có)\n\n"
            "4. **Lưu ý quan trọng:**\n"
            "   - Luôn kiểm tra trạng thái đăng nhập trước khi tra cứu đơn hàng\n"
            "   - Nếu tool trả về code 'NOT_LOGGED_IN', hướng dẫn khách hàng đăng nhập hoặc cung cấp mã đơn + email\n"
            "   - Với khách hàng guest, khuyến khích đăng nhập để quản lý đơn hàng dễ dàng hơn\n"
            "   - KHÔNG BAO GIỜ bỏ sót thông tin Phường/Xã trong địa chỉ\n\n"
            "**Hướng dẫn quản lý giỏ hàng:**\n"
            "1. **Hiển thị giỏ hàng:**\n"
            "   - Khi khách hàng hỏi 'giỏ hàng của tôi có gì?', sử dụng view_cart()\n"
            "   - LUÔN LUÔN hiển thị đầy đủ: Tên sản phẩm (có link), SKU, Số lượng, Giá/cái, Tổng tiền\n"
            "   - Format bảng: Sản phẩm | SKU | Số lượng | Giá/cái | Tổng\n"
            "   - Tên sản phẩm PHẢI là markdown link: [Tên sản phẩm](url_sản_phẩm)\n"
            "   - URL sản phẩm format: {base_url}/product/{url_key} (ví dụ: https://b2c-mmpro.izysync.com/product/ga-nuong-sot-tieu-toi-412624.html)\n"
            "   - SKU rất quan trọng để khách hàng tham khảo khi cần thêm/xóa sản phẩm\n\n"
            "2. **Hiển thị kết quả tìm kiếm sản phẩm:**\n"
            "   - LUÔN hiển thị tên sản phẩm dạng link: [Tên sản phẩm](url)\n"
            "   - Hiển thị SKU, giá, đơn vị, tình trạng kho\n"
            "   - URL đã có trong kết quả search_products()\n\n"
            "3. **Thêm sản phẩm vào giỏ:**\n"
            "   - LUÔN tìm kiếm sản phẩm trước bằng search_products() để lấy SKU chính xác\n"
            "   - KHÔNG BAO GIỜ tự đoán hoặc suy luận SKU\n"
            "   - Sau khi có SKU, dùng: add_product_to_cart(sku='...', quantity=...)\n\n"
            "4. **Cập nhật/Xóa sản phẩm:**\n"
            "   - Cần cart_item_uid từ kết quả view_cart()\n"
            "   - Cập nhật: update_cart_item(cart_item_uid='...', quantity=...)\n"
            "   - Xóa: remove_item_from_cart(cart_item_uid='...')\n\n"
            "5. **Quy trình chuẩn khi thêm sản phẩm:**\n"
            "   - Bước 1: search_products() để tìm và lấy SKU\n"
            "   - Bước 2: Hiển thị sản phẩm tìm được (bao gồm link, SKU) cho khách xác nhận\n"
            "   - Bước 3: add_product_to_cart(sku=..., quantity=...)\n"
            "   - Bước 4: Xác nhận thành công và hiển thị tổng giỏ hàng (có link sản phẩm)\n\n"
            "Hãy bắt đầu hỗ trợ khách hàng một cách chuyên nghiệp!"
        )
    ]


@mcp.prompt(title="Tư vấn mua sắm")
def shopping_advisor_prompt(
    customer_needs: str,
    budget: Optional[str] = None,
    preferences: Optional[str] = None
) -> list[PromptMessage]:
    """
    Prompt cho tình huống tư vấn mua sắm chi tiết.
    """
    messages = [
        Message(role="user", content=
            f"Khách hàng cần tư vấn mua sắm với thông tin sau:\n\n"
            f"**Nhu cầu:** {customer_needs}\n"
        )
    ]

    if budget:
        messages.append(Message(role="user", content=f"**Ngân sách:** {budget}"))

    if preferences:
        messages.append(Message(role="user", content=f"**Sở thích/Yêu cầu:** {preferences}"))

    messages.append(
        Message(role="assistant", content=
            "Dạ, em đã hiểu nhu cầu của anh/chị. Để tư vấn chính xác nhất, em sẽ:\n"
            "1. Tìm kiếm các sản phẩm phù hợp trong hệ thống\n"
            "2. So sánh giá cả và chất lượng\n"
            "3. Kiểm tra khuyến mãi hiện có\n"
            "4. Đề xuất những sản phẩm tốt nhất cho anh/chị\n\n"
            "Em bắt đầu tìm kiếm ngay ạ!"
        )
    )

    return messages


@mcp.prompt(title="Xử lý khiếu nại đơn hàng")
def order_complaint_handler(
    order_number: str,
    issue_description: str
) -> list[PromptMessage]:
    """
    Prompt cho việc xử lý khiếu nại về đơn hàng.
    """
    return [
        Message(role="user", content=
            f"Khách hàng có khiếu nại về đơn hàng:\n\n"
            f"**Mã đơn hàng:** {order_number}\n"
            f"**Vấn đề:** {issue_description}"
        ),
        Message(role="assistant", content=
            "Dạ, em rất xin lỗi vì sự bất tiện này. Em sẽ:\n"
            "1. Kiểm tra ngay trạng thái đơn hàng\n"
            "2. Xác minh thông tin vấn đề\n"
            "3. Tra cứu chính sách đổi trả/hoàn tiền\n"
            "4. Đưa ra giải pháp cụ thể cho anh/chị\n\n"
            "Anh/chị vui lòng cung cấp thêm email đã dùng khi đặt hàng để em tra cứu ạ."
        )
    ]


@mcp.prompt(title="Giới thiệu chương trình khuyến mãi")
def promotion_introduction() -> str:
    """
    Prompt để giới thiệu chương trình khuyến mãi và ưu đãi.
    """
    return (
        "Hãy giới thiệu các chương trình khuyến mãi hiện có tại MM Mega Market Vietnam.\n\n"
        "**Yêu cầu:**\n"
        "- Tìm kiếm thông tin khuyến mãi từ knowledge base\n"
        "- Sử dụng category 'Khuyến mãi' (UID: MjUxNzE=) để tìm sản phẩm đang giảm giá\n"
        "- Trình bày rõ ràng: tên chương trình, thời gian, điều kiện áp dụng\n"
        "- Đề xuất sản phẩm hot deal nếu có\n"
        "- Hướng dẫn cách tham gia/sử dụng ưu đãi\n\n"
        "Giọng điệu: Nhiệt tình, hấp dẫn nhưng vẫn chuyên nghiệp."
    )


@mcp.prompt(title="Hướng dẫn đăng ký thẻ M-Card")
def mcard_registration_guide() -> list[PromptMessage]:
    """
    Prompt hướng dẫn đăng ký và sử dụng thẻ M-Card.
    """
    return [
        Message(role="user", content=
            "Khách hàng muốn biết về chương trình thẻ M-Card và cách đăng ký."
        ),
        Message(role="assistant", content=
            "Dạ, thẻ M-Card là chương trình khách hàng thân thiết của MM Mega Market mang lại nhiều ưu đãi.\n\n"
            "Em sẽ tìm kiếm thông tin chi tiết về:\n"
            "- Quyền lợi của thẻ M-Card\n"
            "- Cách thức đăng ký\n"
            "- Điều kiện và hạng thẻ\n"
            "- Cách tích điểm và đổi quà\n\n"
            "Anh/chị cho em một chút thời gian tra cứu thông tin chính xác nhất ạ."
        )
    ]


@mcp.prompt(title="Tìm cửa hàng gần nhất")
def find_nearest_store_prompt(address: str) -> str:
    """
    Prompt để hỗ trợ tìm cửa hàng gần nhất.
    """
    return (
        f"Khách hàng muốn tìm cửa hàng MM Mega Market gần địa chỉ: {address}\n\n"
        "**Quy trình:**\n"
        "1. Sử dụng tool get_nearest_store() để tìm cửa hàng gần nhất\n"
        "2. Hiển thị danh sách cửa hàng theo thứ tự khoảng cách\n"
        "3. Đề xuất cửa hàng gần nhất với thông tin: tên, địa chỉ, khoảng cách\n"
        "4. Hỏi khách có muốn chọn cửa hàng này để mua sắm không\n"
        "5. Nếu đồng ý, gọi select_store() để lưu lựa chọn\n\n"
        "Lưu ý: Luôn xác nhận địa chỉ với khách trước khi tìm kiếm."
    )


@mcp.prompt(title="Hỗ trợ quản lý giỏ hàng")
def cart_management_guide() -> str:
    """
    Prompt hướng dẫn quản lý giỏ hàng.
    """
    return (
        "Hướng dẫn khách hàng quản lý giỏ hàng:\n\n"
        "**Các thao tác có thể thực hiện:**\n"
        "1. Xem giỏ hàng hiện tại: view_cart()\n"
        "   - LUÔN LUÔN hiển thị đầy đủ: Tên sản phẩm (có link), SKU, Số lượng, Giá/cái, Tổng tiền\n"
        "   - Format dạng bảng: Sản phẩm | SKU | Số lượng | Giá/cái | Tổng\n"
        "   - Tên sản phẩm phải là markdown link: [Tên sản phẩm](url)\n"
        "2. Thêm sản phẩm: add_product_to_cart(sku, quantity)\n"
        "   - LUÔN search_products() trước để lấy SKU chính xác\n"
        "   - KHÔNG tự đoán SKU\n"
        "3. Cập nhật số lượng: update_cart_item(cart_item_uid, quantity)\n"
        "4. Xóa sản phẩm: remove_item_from_cart(cart_item_uid)\n\n"
        "**Lưu ý:**\n"
        "- Luôn hiển thị link sản phẩm và SKU trong giỏ hàng để khách dễ tham khảo\n"
        "- Luôn hiển thị tổng tiền sau mỗi thao tác\n"
        "- Xác nhận với khách trước khi thêm/xóa sản phẩm\n"
        "- Kiểm tra tồn kho trước khi thêm số lượng lớn\n"
        "- Đề xuất sản phẩm liên quan hoặc combo tiết kiệm\n\n"
        "Hãy hỗ trợ khách hàng một cách nhiệt tình và chính xác!"
    )


@mcp.tool()
async def get_nearest_store(
    address: str,
    base_url: str = DEFAULT_MMVN_STORE_URL,
    store_id: str = DEFAULT_MMVN_STORE_ID,
) -> Dict[str, Any]:
    """
    Get nearest MM Mega Market store based on customer's address.

    This tool helps customers find the closest store to their location. When a nearby store
    is found, the agent should ask the customer if they want to select it as their shopping store.

    Args:
        address: Full customer address including street, ward, city (e.g., "170 Đề La Thành, phường Láng, Hà Nội"). No district needed — Vietnam removed that admin level in 2025.
        base_url: Base API URL (default: https://b2c-mmpro.izysync.com)
        store_id: Store ID for Magento Store header (default: b2c_10010_vi)

    Returns:
        Dictionary containing:
        - success: Boolean indicating if stores were found
        - stores: List of nearby stores sorted by distance
        - nearest_store: The closest store details
        - message: Human-readable message
        - instruction_for_agent: Instructions for the agent on next steps
        - delivery_policy: Delivery policy information for the nearest store

    Example:
        get_nearest_store(address="170 Đề La Thành, Đống Đa, Hà Nội")
    """
    logger.info(f"Finding nearest store for address: {address}")

    if not address or not address.strip():
        return {
            "success": False,
            "message": "Anh/chị vui lòng cung cấp địa chỉ để tìm cửa hàng gần nhất",
            "code": "EMPTY_ADDRESS"
        }

    # Fix 2: include Store header so Magento routes to the correct store context
    gql_headers = {
        'Content-Type': 'application/json',
        'Store': store_id,
    }

    try:
        # Step 1: Get suggested locations from address
        suggest_query = """
        query GetSuggestedLocation($address: String!){
            suggestLocation(address: $address) {
                address
                city
                city_code
                district
                district_code
                ward
                ward_code
            }
        }
        """

        async with httpx.AsyncClient(timeout=30.0) as client:
            graphql_url = f"{base_url.rstrip('/')}/graphql"

            # Get suggested location
            response = await client.post(
                graphql_url,
                headers=gql_headers,
                json={"query": suggest_query, "variables": {"address": address}}
            )

            suggest_data = response.json()

            if "errors" in suggest_data or not suggest_data.get("data", {}).get("suggestLocation"):
                return {
                    "success": False,
                    "message": f"Không tìm thấy địa chỉ: {address}. Vui lòng cung cấp địa chỉ cụ thể hơn.",
                    "instruction_for_agent": (
                        "Không thể xác định vị trí từ địa chỉ. Yêu cầu khách cung cấp địa chỉ đầy đủ hơn "
                        "bao gồm số nhà, đường, phường/xã và tỉnh/thành phố. "
                        "Lưu ý: KHÔNG hỏi quận/huyện vì Việt Nam đã bỏ cấp hành chính này từ 2025."
                    ),
                    "code": "ADDRESS_NOT_FOUND"
                }

            suggest_location = suggest_data["data"]["suggestLocation"]
            best_address = next((loc for loc in suggest_location if
                               loc.get('city_code') and loc.get('ward_code')), None)

            if not best_address:
                return {
                    "success": False,
                    "message": f"Không tìm thấy địa chỉ chính xác: {address}",
                    "instruction_for_agent": (
                        "Không tìm thấy địa chỉ chính xác. Yêu cầu khách cung cấp đủ: "
                        "số nhà/đường, phường/xã, tỉnh/thành phố. "
                        "KHÔNG hỏi quận/huyện — Việt Nam đã bỏ cấp hành chính này từ 2025."
                    ),
                    "code": "INCOMPLETE_ADDRESS"
                }

            # Step 2: Find nearest stores
            store_query = """
            query GetNearestStore($street: String, $city: String!, $district: String, $ward: String!){
                storeView(
                    street: $street,
                    city: $city,
                    district: $district,
                    ward: $ward,
                    language: "vi",
                    website: "b2c"
                ) {
                    store_view_code {
                        distance
                        distance_text
                        priority
                        store_view_code
                        source_name
                    }
                    message
                    allow_selection
                }
            }
            """

            store_response = await client.post(
                graphql_url,
                headers=gql_headers,
                json={
                    "query": store_query,
                    "variables": {
                        "street": address,
                        "city": best_address.get('city_code'),
                        "district": best_address.get('district_code', ''),
                        "ward": best_address.get('ward_code')
                    }
                }
            )

            store_data = store_response.json()

            if "errors" in store_data:
                return {
                    "success": False,
                    "message": "Lỗi khi tìm kiếm cửa hàng",
                    "code": "API_ERROR"
                }

            store_view_data = store_data.get("data", {}).get("storeView", {})
            store_view_codes = store_view_data.get("store_view_code", [])
            allow_selection = store_view_data.get("allow_selection", True)
            api_message = store_view_data.get("message", "")

            if not store_view_codes:
                no_store_msg = api_message or f"Không tìm thấy cửa hàng gần địa chỉ: {address}"
                return {
                    "success": False,
                    "message": no_store_msg,
                    "allow_selection": allow_selection,
                    "instruction_for_agent": (
                        f"Dạ, {no_store_msg}. "
                        "Gợi ý khách:\n"
                        "- [MM Mega Market Miền Bắc](https://online.mmvietnam.com/store-locator?source=1)\n"
                        "- [MM Mega Market Miền Trung](https://online.mmvietnam.com/store-locator?source=2)\n"
                        "- [MM Mega Market Miền Nam](https://online.mmvietnam.com/store-locator?source=3)"
                    ),
                    "code": "NO_NEARBY_STORES"
                }

            # Step 3: Get detailed info for each store
            stores_info = []
            for store in store_view_codes:
                store_info_query = """
                query GetStoreInfo($store_view_code: String!){
                    storeInformation(store_view_code: $store_view_code){
                        address
                        name
                        source_code
                    }
                }
                """

                info_response = await client.post(
                    graphql_url,
                    headers=gql_headers,
                    json={
                        "query": store_info_query,
                        "variables": {"store_view_code": store['store_view_code']}
                    }
                )

                info_data = info_response.json()
                store_info = info_data.get("data", {}).get("storeInformation", {})

                if store_info:
                    store_code = store['store_view_code'].replace('b2c_', '').replace('_vi', '')
                    stores_info.append({
                        'code': store_code,
                        'name': store_info.get('name', 'Unknown Store'),
                        'address': store_info.get('address', 'No address'),
                        'distance': store['distance'],
                        'distance_text': store['distance_text'],
                        'priority': store.get('priority'),
                        'source_name': store.get('source_name', ''),
                        'store_view_code': store['store_view_code']
                    })

            if not stores_info:
                return {
                    "success": False,
                    "message": "Không lấy được thông tin cửa hàng",
                    "code": "STORE_INFO_ERROR"
                }

            stores_info.sort(key=lambda s: float(s['distance']) if s['distance'] else float('inf'))

            stores_formatted = []
            for idx, store in enumerate(stores_info, 1):
                stores_formatted.append(
                    f"{idx}. **{store['name']}**\n"
                    f"   - Địa chỉ: {store['address']}\n"
                    f"   - Khoảng cách: {store['distance_text']}\n"
                    f"   - Mã cửa hàng: {store['code']}"
                )

            nearest_store = stores_info[0]
            delivery_policy = _get_delivery_policy_info(nearest_store['name'])

            return {
                "success": True,
                "stores": stores_info,
                "stores_count": len(stores_info),
                "nearest_store": nearest_store,
                "stores_formatted": "\n\n".join(stores_formatted),
                "allow_selection": allow_selection,
                "delivery_policy": delivery_policy,
                "message": api_message or f"Tìm thấy {len(stores_info)} cửa hàng gần địa chỉ của bạn. Cửa hàng gần nhất là {nearest_store['name']}.",
                "instruction_for_agent": (
                    f"Đã tìm thấy {len(stores_info)} cửa hàng gần khách. "
                    f"Cửa hàng gần nhất: {nearest_store['name']} (mã: {nearest_store['code']}).\n\n"
                    f"**YÊU CẦU:** Hỏi khách có muốn chọn cửa hàng này không. Nếu khách đồng ý, "
                    f"gọi select_store(store_code=\"{nearest_store['code']}\") để lưu lựa chọn."
                ),
                "code": "SUCCESS"
            }

    except httpx.RequestError as e:
        return {
            "success": False,
            "message": f"Lỗi kết nối: {str(e)}",
            "code": "HTTP_ERROR"
        }
    except Exception as e:
        logger.error(f"Error in get_nearest_store: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Lỗi không xác định: {str(e)}",
            "code": "UNKNOWN_ERROR"
        }


@mcp.tool()
async def add_product_note_to_cart(
    cart_item_uid: str,
    note: str,
    cart_id: Optional[str] = None,
    base_url: str = DEFAULT_MMVN_STORE_URL,
    store_id: str = DEFAULT_MMVN_STORE_ID,
) -> Dict[str, Any]:
    """
    Add or update a note/comment for a specific product in the shopping cart.
    Automatically uses authentication token if user is logged in.

    Args:
        cart_item_uid: Cart item unique ID from view_cart (required).
        note: The note/comment content to add to the product.
        cart_id: Optional cart ID. If not provided, uses cached cart.
        base_url: Base API URL (default: https://b2c-mmpro.izysync.com).
        store_id: Store ID (default: "b2c_10010_vi").

    Returns:
        Dictionary containing success status, cart_id, and updated cart information.
    """
    global _user_auth

    try:
        # Get or create cart
        cart_id = await _get_or_create_cart(base_url, store_id, cart_id)
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to get/create cart: {str(e)}",
            "code": "CART_ERROR"
        }

    # Prepare GraphQL mutation
    graphql_mutation = """
        mutation UpdateComment($cartId: String!, $cartItemUid: ID!, $comment: String!) {
            updateCommentOnCartItem(input: {
                cart_id: $cartId
                cart_item_uid: $cartItemUid
                comment: $comment
            }) {
                cart {
                    id
                    total_summary_quantity_including_config
                    items {
                        uid
                        quantity
                        comment
                        product {
                            name
                            ecom_name
                            sku
                            art_no
                            url_key
                        }
                        prices {
                            price_including_tax { value currency }
                            row_total_including_tax { value currency }
                        }
                    }
                    prices {
                        subtotal_including_tax { value currency }
                        grand_total { value currency }
                    }
                }
            }
        }
    """

    variables = {
        "cartId": cart_id,
        "cartItemUid": cart_item_uid,
        "comment": note
    }

    payload = {
        "query": graphql_mutation,
        "variables": variables
    }

    headers = {
        'Store': store_id,
        'Content-Type': 'application/json',
    }

    # Add authorization header if user is logged in
    if _user_auth.get("is_logged_in") and _user_auth.get("token"):
        headers['Authorization'] = f'Bearer {_user_auth["token"]}'

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            graphql_url = f"{base_url.rstrip('/')}/graphql"
            response = await client.post(
                graphql_url,
                headers=headers,
                content=json.dumps(payload)
            )

            # Parse response
            try:
                res = response.json()
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Failed to parse JSON response: {str(e)}",
                    "code": "JSON_PARSE_ERROR"
                }

            # Check for GraphQL errors
            if "errors" in res:
                return {
                    "success": False,
                    "message": f"GraphQL errors: {res['errors']}",
                    "code": "GRAPHQL_ERROR"
                }

            # Check data
            data = res.get("data")
            if not data or not isinstance(data, dict):
                return {
                    "success": False,
                    "message": "Invalid response data",
                    "code": "INVALID_RESPONSE"
                }

            update_result = data.get("updateCommentOnCartItem", {})
            cart_data = update_result.get("cart", {})

            if not cart_data:
                return {
                    "success": False,
                    "message": "Failed to update product note",
                    "code": "UPDATE_FAILED"
                }

            return {
                "success": True,
                "cart_id": cart_id,
                "data": cart_data,
                "message": "Product note updated successfully",
                "instruction_for_agent": (
                    "When displaying cart after adding note, ALWAYS show the note for the product. "
                    "Format: '[Product Name] (SKU: [sku]) - Số lượng: [quantity] - Ghi chú: [comment]'. "
                    "Inform user the note has been added successfully."
                )
            }

    except httpx.RequestError as e:
        return {
            "success": False,
            "message": f"HTTP request error: {str(e)}",
            "code": "HTTP_ERROR"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "code": "UNKNOWN_ERROR"
        }


@mcp.tool()
async def remove_product_note_from_cart(
    cart_item_uid: str,
    cart_id: Optional[str] = None,
    base_url: str = DEFAULT_MMVN_STORE_URL,
    store_id: str = DEFAULT_MMVN_STORE_ID,
) -> Dict[str, Any]:
    """
    Remove the note/comment from a specific product in the shopping cart.
    Automatically uses authentication token if user is logged in.

    Args:
        cart_item_uid: Cart item unique ID from view_cart (required).
        cart_id: Optional cart ID. If not provided, uses cached cart.
        base_url: Base API URL (default: https://b2c-mmpro.izysync.com).
        store_id: Store ID (default: "b2c_10010_vi").

    Returns:
        Dictionary containing success status, cart_id, and updated cart information.
    """
    global _user_auth

    try:
        # Get or create cart
        cart_id = await _get_or_create_cart(base_url, store_id, cart_id)
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to get/create cart: {str(e)}",
            "code": "CART_ERROR"
        }

    # Prepare GraphQL mutation
    graphql_mutation = """
        mutation RemoveComment($cartId: String!, $cartItemUid: ID!) {
            removeCommentFromCartItem(input: {
                cart_id: $cartId
                cart_item_uid: $cartItemUid
            }) {
                cart {
                    id
                    total_summary_quantity_including_config
                    items {
                        uid
                        quantity
                        comment
                        product {
                            name
                            ecom_name
                            sku
                            art_no
                            url_key
                        }
                        prices {
                            price_including_tax { value currency }
                            row_total_including_tax { value currency }
                        }
                    }
                    prices {
                        subtotal_including_tax { value currency }
                        grand_total { value currency }
                    }
                }
            }
        }
    """

    variables = {
        "cartId": cart_id,
        "cartItemUid": cart_item_uid
    }

    payload = {
        "query": graphql_mutation,
        "variables": variables
    }

    headers = {
        'Store': store_id,
        'Content-Type': 'application/json',
    }

    # Add authorization header if user is logged in
    if _user_auth.get("is_logged_in") and _user_auth.get("token"):
        headers['Authorization'] = f'Bearer {_user_auth["token"]}'

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            graphql_url = f"{base_url.rstrip('/')}/graphql"
            response = await client.post(
                graphql_url,
                headers=headers,
                content=json.dumps(payload)
            )

            # Parse response
            try:
                res = response.json()
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Failed to parse JSON response: {str(e)}",
                    "code": "JSON_PARSE_ERROR"
                }

            # Check for GraphQL errors
            if "errors" in res:
                return {
                    "success": False,
                    "message": f"GraphQL errors: {res['errors']}",
                    "code": "GRAPHQL_ERROR"
                }

            # Check data
            data = res.get("data")
            if not data or not isinstance(data, dict):
                return {
                    "success": False,
                    "message": "Invalid response data",
                    "code": "INVALID_RESPONSE"
                }

            remove_result = data.get("removeCommentFromCartItem", {})
            cart_data = remove_result.get("cart", {})

            if not cart_data:
                return {
                    "success": False,
                    "message": "Failed to remove product note",
                    "code": "REMOVE_FAILED"
                }

            return {
                "success": True,
                "cart_id": cart_id,
                "data": cart_data,
                "message": "Product note removed successfully",
                "instruction_for_agent": (
                    "When displaying cart after removing note, confirm to user that the note has been removed. "
                    "Format: '[Product Name] (SKU: [sku]) - Số lượng: [quantity]'. "
                    "Inform user the note has been removed successfully."
                )
            }

    except httpx.RequestError as e:
        return {
            "success": False,
            "message": f"HTTP request error: {str(e)}",
            "code": "HTTP_ERROR"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "code": "UNKNOWN_ERROR"
        }


if __name__ == "__main__":
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8001"))
    mcp.run(transport="sse", host=host, port=port)
