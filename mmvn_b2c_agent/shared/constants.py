"""
Constants for the multi-tool agent system.
"""
import os

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.google_llm import Gemini
from google.genai import types
from google.genai.types import HttpRetryOptions, ThinkingConfig
from mmvn_b2c_agent.shared.safety import SAFETY_FILTER_CONFIG

GEMINI_BASE_URL = os.getenv("GOOGLE_GEMINI_BASE_URL")

# default model configs
DEFAULT_RETRY_OPTION = HttpRetryOptions(initial_delay=0.25, attempts=5)
DEFAULT_GENERATE_CONTENT_CONFIG = types.GenerateContentConfig(
    temperature=0.4,
    safety_settings=SAFETY_FILTER_CONFIG,
    http_options=types.HttpOptions(
        api_version='v1alpha',
        base_url=GEMINI_BASE_URL,
        retry_options=DEFAULT_RETRY_OPTION
    )
)

# Define model constants
# TODO: separated keys for each agent, model and content types
MODEL_GEMINI_FLASH = Gemini(model="gemini-2.0-flash", retry_options=DEFAULT_RETRY_OPTION, base_url=GEMINI_BASE_URL)
MODEL_GEMINI_2_5_FLASH = Gemini(model="gemini-2.5-flash", retry_options=DEFAULT_RETRY_OPTION, base_url=GEMINI_BASE_URL)
MODEL_GEMINI_3_FLASH = Gemini(model="gemini-3-flash-preview", retry_options=DEFAULT_RETRY_OPTION, base_url=GEMINI_BASE_URL)
MODEL_GEMINI_FLASH_LIVE = "gemini-2.0-flash-live-001"  # Model that supports live-streaming
MODEL_GEMINI_FLASH_LITE = "gemini-2.0-flash-lite"  # Model Gemini 2.0 Flash Lite
MODEL_GEMINI_2_5_FLASH_LITE = Gemini(model="gemini-2.5-flash-lite", retry_options=DEFAULT_RETRY_OPTION, base_url=GEMINI_BASE_URL)
MODEL_GEMINI_3_1_FLASH_LITE = Gemini(model="gemini-3.1-flash-lite-preview", retry_options=DEFAULT_RETRY_OPTION, base_url=GEMINI_BASE_URL)
MODEL_GEMINI_PRO = "gemini-2.5-pro"  # Model Gemini Pro
MODEL_GPT_4O = "openai/gpt-4o"
MODEL_CLAUDE_SONNET = "anthropic/claude-3-sonnet-20240229"

def get_thinking_config(model, level: str | None = None) -> ThinkingConfig:
    """Return ThinkingConfig theo model.

    - gemini-3: dùng `thinking_level` (minimal/low/medium/high) — thinking_budget không áp
      dụng cho gemini-3. LƯU Ý: gemini-3 mặc định `high` nếu không set → chậm. Với các agent
      cơ học (query expansion, format output bằng forced function call) nên truyền
      `level="minimal"` để giảm latency.
    - gemini-2.x: dùng `thinking_budget` (-1 dynamic cho pro, 0 tắt cho flash).

    Args:
        model: model string hoặc Gemini object.
        level: override thinking_level cho gemini-3 ("minimal"/"low"/"medium"/"high").
               Bỏ qua với gemini-2.x. Mặc định: pro→'high', flash/lite→'low'.
    """
    model_name = (model if isinstance(model, str) else getattr(model, "model", "")).lower()
    is_pro = "pro" in model_name
    if "gemini-3" in model_name:
        chosen = level or ("high" if is_pro else "low")
        return ThinkingConfig(include_thoughts=True, thinking_level=chosen)
    return ThinkingConfig(include_thoughts=True, thinking_budget=-1 if is_pro else 0)


# Allowed preferences
ALLOWED_PREFERENCES = {
    "temperature_unit": ["Celsius", "Fahrenheit"]
}

# Safety constants
BLOCKED_KEYWORDS = ["offensive", "inappropriate", "harmful", "MMVN_CUSTOM_BLOCKED_KEYWORD"]

# DEFAULT_MMVN_STORE_URL = "https://b2c-mmpro.izysync.com" #test
DEFAULT_MMVN_STORE_URL = "https://online.mmvietnam.com/"
DEFAULT_MMVN_STORE_ID = 'b2c_10013_vi'

TOKEN_WARNING_LIMIT = 41000
TOKEN_HARD_LIMIT = 100000
TOKEN_PER_MINUTES_LIMIT = 200000  # max token per minute for a session
MAX_USER_MESSAGE_LENGTH = 200000  # max length of user message to avoid extreme cases


# Order Status Mapping - Based on MMVN B2C Frontend
# Format: {"Display Name": ["status_code1", "status_code2", ...]}
ORDER_STATUS_GROUPS = {
    "Đã huỷ đơn hàng": ["backorder_ccod", "canceled", "closed", "deleted_ccod"],
    "Đã giao hàng": ["complete", "completed_ccod"],
    "Đang xử lý": ["confirmed_ccod", "order_error", "processing"],
    "Đang giao hàng": ["invoiced_ccod", "in_shipment_ccod", "picked_ccod", "picking_ccod"],
    "Đã ghi nhận đơn hàng": ["pending", "pending_ccod"],
    "Chờ thanh toán": ["pending_payment"],
    "Chờ hủy": ["waiting_cancel"]
}

# Reverse mapping for quick lookup: status_code -> Display Name
STATUS_CODE_TO_DISPLAY = {}
for display_name, status_codes in ORDER_STATUS_GROUPS.items():
    for code in status_codes:
        STATUS_CODE_TO_DISPLAY[code] = display_name

# Completed/Closed status codes (orders that are finished)
COMPLETED_STATUS_CODES = ["complete", "completed_ccod", "backorder_ccod", "deleted_ccod", "closed", "canceled"]

# Active delivery status codes (orders currently being delivered)
DELIVERING_STATUS_CODES = ["invoiced_ccod", "in_shipment_ccod", "picked_ccod", "picking_ccod"]

# Order Status Filter Mapping - For frontend filtering
ORDER_STATUS_FILTER_MAP = {
    "complete,completed_ccod": ["complete", "completed_ccod"],
    "backorder_ccod,canceled,closed,deleted_ccod": ["backorder_ccod", "canceled", "closed", "deleted_ccod"],
    "confirmed_ccod,order_error,processing": ["confirmed_ccod", "order_error", "processing"],
    "invoiced_ccod,in_shipment_ccod,picked_ccod,picking_ccod": ["invoiced_ccod", "in_shipment_ccod", "picked_ccod", "picking_ccod"],
    "pending,pending_ccod": ["pending", "pending_ccod"],
    "pending_payment": ["pending_payment"],
    "waiting_cancel": ["waiting_cancel"]
}

# Reverse mapping: status_code -> filter string
STATUS_CODE_TO_FILTER = {}
for filter_string, status_codes in ORDER_STATUS_FILTER_MAP.items():
    for code in status_codes:
        STATUS_CODE_TO_FILTER[code] = filter_string
