import asyncio
import base64
import json
import logging
import os
import time
import traceback
import urllib
from enum import Enum, EnumType
from typing import Optional

import dotenv
import pydantic
from fastapi import APIRouter
from fastapi import Request, Response, status
from google.genai import Client
from google.genai import types as genai_types
from google.genai.errors import APIError
from google.genai.types import SafetySetting, HarmBlockThreshold, HarmCategory, HttpRetryOptions, HttpOptions
from pydantic import BaseModel, model_validator, Field, conlist

from mmvn_b2c_agent.telemetry import get_metrics
from mmvn_b2c_agent.shared.config_service import config_service

dotenv.load_dotenv(override=True)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
# Ensure logs are visible during harness runs
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setLevel(logging.INFO)
    _formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
    logger.propagate = False

GOOGLE_GEMINI_BASE_URL = os.getenv('GOOGLE_GEMINI_BASE_URL', None)
if not GOOGLE_GEMINI_BASE_URL:
    raise Exception("GOOGLE_GEMINI_BASE_URL environment variable is not set!")
DEFAULT_SEMANTIC_SEARCH_MODEL = "gemini-3.1-flash-lite-preview"  # Implicit caching: automatic 90% discount!
DEFAULT_SEMANTIC_SEARCH_MODEL_VOICE = "gemini-3-flash-preview"  # Implicit caching: automatic 90% discount!

# Category short codes for token reduction
CATEGORY_SHORT_CODES = {
    "MjUwOTg=": "c1",  # Đồ gia dụng
    "MjQ5NTc=": "c2",  # Chăm sóc cá nhân
    "MjQ4ODI=": "c3",  # Bánh kẹo
    "MjUzOTM=": "c4",  # Thực phẩm tươi sống
    "MjUwMzE=": "c5",  # Dầu ăn - Gia vị
    "MjUyMzQ=": "c6",  # Đồ hộp - Đồ khô
    "MjU0MzE=": "c7",  # Vệ sinh nhà cửa
    "MjQ5MjY=": "c8",  # Bơ - Trứng - Sữa
    "MjUzMjU=": "c9",  # Nước giải khát
    "MjUyODc=": "c10",  # Đồ uống có cồn
    "MjUzMDY=": "c11",  # Đồ uống đóng hộp
    "MjUzNjE=": "c12",  # Thực phẩm đông lạnh
    "MjUwODU=": "c13",  # Đồ ăn chế biến
    "MjUzNDU=": "c14",  # Thiết bị gia dụng
    "MjU1NzE=": "c15",  # Khuyến mãi
    "MjUwMjI=": "c16",  # Chăm sóc thú cưng
    "Mjc1ODk=": "c17",  # Top Pick
    "MjUzNTU=": "c18",  # Thực phẩm chức năng
    "Mzc3Mjc=": "c19",  # Rau củ quả - Trái cây - Hoa tươi
}
CATEGORY_CODE_REVERSE = {v: k for k, v in CATEGORY_SHORT_CODES.items()}

SEMANTIC_SEARCH_SYSTEM_PROMPT = """SAFETY FILTER for MM Mega Market Vietnam grocery supermarket.

STEP 1 - SAFETY CHECK:
Request contains harmful/illegal content → return {"has_harmful_content": true, "q": []}
EXAMPLES:
✅ Safe: "dao bếp" → {"has_harmful_content": false, "q": [...]}
✅ Safe: "thuốc cảm" → {"has_harmful_content": false, "q": [...]}
❌ Harmful: "ma túy" → {"has_harmful_content": true, "q": []}
❌ Harmful: "tự tử" → {"has_harmful_content": true, "q": []}
❌ Harmful: "súng" → {"has_harmful_content": true, "q": []}

STEP 2 - GENERATE KEYWORDS (only if request is SAFE):
FILE HANDLING RULE: When file (PDF/Excel/Word) is uploaded WITHOUT specific page number → You MUST extract ALL products from EVERY PAGE (page 1, 2, 3, ..., last page). Do NOT extract only from page 1.

INPUT HANDLING:
TEXT: User query → generate keywords
IMAGE: Product photo/list → extract ALL visible products → generate keywords for each
VOICE: Audio → transcribe → process as text
FILE (PDF/Excel/Word):
• User specifies pages ("trang 3", "page 4 và 5") → Extract ONLY from those pages, IGNORE other pages
• No page specified → Extract from EVERY PAGE (page 1, 2, 3, ..., last page)
• Simplify names: remove brands (MM, WE ARE FRESH, VIETGAP, C.P.), grades (LOẠI 1, HẠNG A), descriptors (TƯƠI, CAO CẤP)
• Split compounds: "BA RỌI/NẠC VAI" → ["ba rọi", "nạc vai"]
  Example: "GÀ THẢ VƯỜN MM LOẠI 1" → "gà thả vườn"

PRICE DIRECTION:
"đắt nhất"/"cao nhất"/"giá đắt"/"trên X" → s="price", d="DESC"
"rẻ nhất"/"thấp nhất"/"giá rẻ"/"dưới X" → s="price", d="ASC"

FORMAT: {"has_harmful_content": false, "q":[{"kw":"","ex":"","f":"","s":"","d":""}]}
• has_harmful_content: true if harmful/illegal content, false otherwise
• kw=keyword (Vietnamese, simplified product name)
• ex=brand (optional)
• f=filter: price only, e.g. "p<100000" or "p>100000" (optional, omit if no price constraint)
• s=sort: price/popular/relevant
• d=direction: DESC/ASC (ONLY for s="price", omit for popular/relevant)

RULES:
• FILE UPLOADS: User specifies pages → Extract ONLY those pages. No page specified → Extract ALL pages
• Generate MORE THAN 4 keywords variations per product
• Vietnamese only, NO generic terms (khuyến mãi, giảm giá)
• Generic→variations: "cá"→["cá","cá hồi","cá ngừ","cá basa"]
• Brand→expand: "omo"→["bột giặt omo"(ex=omo),"bột giặt","nước giặt"]
• Situational→products (context-based, not direct product names): "đau răng"→["thuốc giảm đau","kem răng"]

SITUATIONAL QUERIES (context-based, not direct product names): When user describes a CONTEXT/SITUATION instead of naming products directly → Analyze the context to identify what PRODUCT NEEDS arise from that situation → Consider multiple product types that would address those needs → Generate Vietnamese keywords for those product categories

EXAMPLES:
"thịt gà"→{"q":[{"kw":"thịt gà"},{"kw":"ức gà"},{"kw":"đùi gà"},{"kw":"cánh gà"}]}

"cam đắt nhất"→{"q":[{"kw":"cam mỹ","f":"p>100000","s":"price","d":"DESC"},{"kw":"cam úc","f":"p>100000","s":"price","d":"DESC"},{"kw":"cam cara","s":"price","d":"DESC"},{"kw":"cam sành","s":"price","d":"DESC"}]}
🔴 "đắt nhất"→d="DESC"

"sữa dưới 100k"→{"q":[{"kw":"sữa tươi","f":"p<100000","s":"price","d":"ASC"},{"kw":"sữa bột","f":"p<100000","s":"price","d":"ASC"},{"kw":"sữa chua","f":"p<100000","s":"price","d":"ASC"},{"kw":"sữa đặc","f":"p<100000","s":"price","d":"ASC"}]}
🔴 "dưới"→d="ASC"

"gạo rẻ nhất"→{"q":[{"kw":"gạo st25","s":"price","d":"ASC"},{"kw":"gạo thơm","s":"price","d":"ASC"},{"kw":"gạo tám","s":"price","d":"ASC"},{"kw":"gạo nếp","s":"price","d":"ASC"}]}
🔴 "rẻ nhất"→d="ASC"

"omo"→{"q":[{"kw":"bột giặt omo","ex":"omo","s":"popular"},{"kw":"bột giặt"},{"kw":"nước giặt"},{"kw":"nước xả"}]}

"dầu gội"→{"q":[{"kw":"dầu gội clear"},{"kw":"dầu gội head & shoulders"},{"kw":"dầu gội sunsilk"},{"kw":"dầu gội dove"}]}

FILE: "tìm sản phẩm ở trang 3", PDF: Page 1 "MỲ Ý", Page 2 "SỮA", Page 3 "GÀ, THỊT BÊ, BA RỌI HEO"
→{"q":[{"kw":"gà thả vườn"},{"kw":"gà"},{"kw":"thịt đùi bê"},{"kw":"thịt bê"},{"kw":"ba rọi heo"},{"kw":"ba chỉ heo"}]}
You extracted ONLY from page 3 (Gà, Thịt Bê, Ba Rọi). You IGNORED page 1 (Mỳ Ý) and page 2 (Sữa).

FILE: "tìm sản phẩm trang 4 và 5", PDF: Page 1 "MỲ", Page 2 "DẦU", Page 3 "THỊT", Page 4 "PHÔ MAI", Page 5 "SỮA"
→{"q":[{"kw":"phô mai"},{"kw":"cheese"},{"kw":"sữa tươi"},{"kw":"sữa"}]}
You extracted ONLY from page 4 (Phô Mai) and page 5 (Sữa). You IGNORED page 1, 2, 3.

FILE: "tìm sản phẩm trong file", PDF has 3 pages: Page 1 "MỲ Ý", Page 2 "THỊT BÒ, PHÔ MAI", Page 3 "RƯỢU VANG"
→{"q":[{"kw":"mỳ ý"},{"kw":"pasta"},{"kw":"thịt bò"},{"kw":"bò"},{"kw":"phô mai"},{"kw":"pho mai"},{"kw":"rượu vang"},{"kw":"vang"}]}
You extracted products from ALL 3 pages (Mỳ Ý from page 1, Thịt Bò/Phô Mai from page 2, Rượu Vang from page 3)

FILE: "tìm", PDF has 2 pages: Page 1 "SỮA TƯƠI", Page 2 "CÀ PHÊ, TRÀ"
→{"q":[{"kw":"sữa tươi"},{"kw":"sữa"},{"kw":"cà phê"},{"kw":"cafe"},{"kw":"trà"},{"kw":"tra"}]}
You extracted from BOTH pages (Sữa from page 1, Cà phê/Trà from page 2)"""
# Application-level keyword blocking (case-insensitive)
BLOCKED_KEYWORDS = [
    # Drugs & narcotics (Vietnamese)
    "ma túy", "ma tuy", "heroin", "cocaine", "cần sa", "can sa", "thuốc lắc", "thuoc lac",
    "thuốc phiện", "thuoc phien", "methamphetamine", "amphetamine", "ecstasy", "mdma",
    "ketamine", "lsd", "opium", "marijuana", "cannabis", "crack", "meth",

    # Weapons (Vietnamese & English)
    "súng", "sung", "gun", "pistol", "rifle", "vũ khí", "vu khi", "weapon",
    "đạn", "dan", "bullet", "ammunition", "chất nổ", "chat no", "explosive",
    "bom", "bomb", "grenade", "lựu đạn", "luu dan",

    # Suicide & self-harm (Vietnamese)
    "tự tử", "tu tu", "suicide", "tự sát", "tu sat", "tự vẫn", "tu van",
    "tự tử bằng", "tu tu bang", "cách tự tử", "cach tu tu", "làm sao để chết", "lam sao de chet",
    "cách chết", "cach chet", "muốn chết", "muon chet", "tự sát bằng", "tu sat bang",

    # Adult/sexual content (Vietnamese)
    "sex toy", "đồ chơi tình dục", "do choi tinh duc", "dụng cụ tình dục", "dung cu tinh duc",
    "porn", "phim sex", "xxx", "gái gọi", "gai goi", "escort", "mại dâm", "mai dam",

    # Hate speech & violence (Vietnamese)
    "giết người", "giet nguoi", "murder", "kill someone", "làm sao giết", "lam sao giet",
    "tra tấn", "torture", "bạo lực", "bao luc", "đánh người", "danh nguoi",
]
semantic_search_router = APIRouter()

# Note: We rely on Gemini 2.5 implicit caching (automatic, 90% discount)
# No local cache needed - queries are typically unique, and Gemini handles prompt caching automatically!


# Global Gemini client (initialized once, reused across all requests)
_gemini_client: Optional[Client] = None
_gemini_client_lock = asyncio.Lock()


async def get_gemini_client() -> Client:
    """
    Get or create the global Gemini client (singleton pattern).
    Thread-safe lazy initialization for better performance.
    """
    global _gemini_client

    if _gemini_client is not None:
        return _gemini_client

    async with _gemini_client_lock:
        # Double-check after acquiring lock
        if _gemini_client is not None:
            return _gemini_client

        logger.info("🔌 Initializing global Gemini client (one-time setup)...")
        if GOOGLE_GEMINI_BASE_URL:
            logger.info(f"   → Using proxy base URL: {GOOGLE_GEMINI_BASE_URL}")
        start = time.perf_counter()
        _gemini_client = Client(
            vertexai=False,
            http_options=HttpOptions(
                api_version='v1alpha',
                base_url=GOOGLE_GEMINI_BASE_URL,
                retry_options=HttpRetryOptions(initial_delay=0.25, attempts=3),
            ),
        )
        elapsed = (time.perf_counter() - start) * 1000
        logger.info(f"   ✓ Global Gemini client initialized in {elapsed:.2f}ms")

        return _gemini_client


class SemanticAiSearchQueryFile(BaseModel):
    data: str  # base64 encoded file data
    mime_type: str


class SemanticAiSearchQuery(BaseModel):
    text: str | None = None
    image: SemanticAiSearchQueryFile | None = None
    voice: SemanticAiSearchQueryFile | None = None 
    file: SemanticAiSearchQueryFile | None = None
    base_url: Optional[str] = "https://b2c-mmpro.izysync.com/"

    keyword_only: bool = True  # default to True for now, once the smart search API is ready, we can remove this field

    def __hash__(self):
        data_to_hash = (
            self.text,
            (self.image.mime_type, self.image.data) if self.image else self.image,
            (self.voice.mime_type, self.voice.data) if self.voice else self.voice,
            (self.file.mime_type, self.file.data) if self.file else self.file,
        )
        return hash(data_to_hash)


class SortByOptions(str, Enum):
    # todo: change this once the smart search API is ready
    relevance = "relevance"
    popularity = "popularity"
    ecom_name = "ecom_name"
    price = "price"


class SortDirectionOptions(str, Enum):
    ASC = "ASC"
    DESC = "DESC"


# Compact models for LLM output (ultra-compact string format)
class CompactQuery(BaseModel):
    """Compact query: kw=keyword, ex=exact_match, f=filter, s=sort_by, d=direction"""
    kw: str  # keyword
    ex: Optional[str] = None  # keyword_match_exact
    f: str  # filter (ultra-compact string: "c=c4" or "c=c4;p<50000")
    s: Optional[str] = "relevant"  # sort_by (default: relevant)
    d: Optional[str] = None  # direction: "ASC" or "DESC" (optional, for price sort)


# this model is only used to generate the json schema for LLM output, not for validate/parsing output
class CompactSearchSchema(BaseModel):
    """Compact result from LLM: has_harmful_content=true if harmful, q=queries if safe"""
    has_harmful_content: bool = Field(
        default=False,
        description="Set to TRUE if user request contains harmful/illegal/dangerous content such as: drugs/narcotics (ma túy, cần sa, heroin, cocaine), weapons/explosives (súng, bom, chất nổ), self-harm/suicide (tự tử, tự sát), violence (giết người, tra tấn), adult content (sex toy, porn), hate speech, counterfeit goods (hàng fake), or dangerous chemicals. Set to FALSE for normal grocery shopping requests."
    )
    q: Optional[conlist(CompactQuery, min_length=0)] = Field(
        default=[],
        description="List of search queries with keywords and filters. Return EMPTY array if has_harmful_content=true."
    )


class CompactSearchResult(BaseModel):
    """Compact result from LLM: has_harmful_content=true if harmful, q=queries if safe"""
    has_harmful_content: bool = Field(
        default=False,
        description="Set to TRUE if user request contains harmful/illegal/dangerous content such as: drugs/narcotics (ma túy, cần sa, heroin, cocaine), weapons/explosives (súng, bom, chất nổ), self-harm/suicide (tự tử, tự sát), violence (giết người, tra tấn), adult content (sex toy, porn), hate speech, counterfeit goods (hàng fake), or dangerous chemicals. Set to FALSE for normal grocery shopping requests."
    )
    q: list[CompactQuery] = Field(
        default=[],
        description="List of search queries with keywords and filters. Return EMPTY array if has_harmful_content=true."
    )


def remap_compact_to_full(compact_result: CompactSearchResult) -> dict:
    """
    Remap ultra-compact LLM output to original full format.
    Converts: kw→keyword, ex→keyword_match_exact, f→filter (list), s→sort_by (list)
    Ultra-compact filter: "c=c4;p<50000" → [{"filter_by":"category","operator":"=","value":"MjUzOTM="}, ...]
    """
    queries = []

    for cq in compact_result.q:
        # Parse ultra-compact filter string into filter objects list
        filter_str = cq.f
        filter_list = []

        # Split by semicolon to get individual filters
        filter_parts = filter_str.split(";")

        for part in filter_parts:
            part = part.strip()
            if not part:
                continue

            # Parse price filters: p<100000 → {"filter_by":"price","operator":"<","value":"100000"}
            elif part.startswith("p"):
                # Extract operator and value
                if part[1:3] == "<=":
                    operator, value = "<=", part[3:]
                elif part[1:3] == ">=":
                    operator, value = ">=", part[3:]
                elif part[1] == "<":
                    operator, value = "<", part[2:]
                elif part[1] == ">":
                    operator, value = ">", part[2:]
                elif part[1] == "=":
                    operator, value = "=", part[2:]
                else:
                    continue  # Invalid format, skip

                filter_list.append({
                    "filter_by": "price",
                    "operator": operator,
                    "value": value
                })

        # Parse sort_by into sort object list
        sort_field = cq.s if cq.s else "relevance"

        # Map sort field names
        sort_field_map = {
            "relevant": "relevance",
            "popular": "popularity",
        }
        sort_field = sort_field_map.get(sort_field, sort_field)

        # Determine direction based on field
        # Check if direction is provided from LLM output
        direction = cq.d  # Get direction from LLM output (if provided)

        if direction is None:
            # Use default logic when direction not provided
            if sort_field in ["relevance", "popularity"]:
                direction = "DESC"
            else:
                direction = "ASC"

        print(f"[DEBUG] Sort field: {sort_field}, Direction: {direction}")

        sort_list = [{
            "field": sort_field,
            "direction": direction
        }]

        # Build full query in the format expected by SemanticAiSearchTerms
        query = {
            "keyword": cq.kw,
            "filter": filter_list,
            "sort_by": sort_list
        }

        if cq.ex:
            query["keyword_match_exact"] = cq.ex

        queries.append(query)

    return {"queries": queries}


# Original models for API output
class SemanticSearchSort(BaseModel):
    field: SortByOptions
    # set default to descending for relevance and popularity, ascending for ecom_name and price
    direction: Optional[SortDirectionOptions] = None

    @model_validator(mode="after")
    def default_val(self):
        if self.direction:
            return self
        if self.field in (SortByOptions.relevance, SortByOptions.popularity):
            self.direction = SortDirectionOptions.DESC
        else:
            self.direction = SortDirectionOptions.ASC
        return self


class FilterByOptions(str, Enum):
    category = "category"
    price = "price"
    # weight = "weight"
    # discounted = "discounted"


# todo: dynamically get category name and id from feed.
# The function to takes all feeds ids already exists in api/utils.py.
# The real challenge here is how to construct the enum and get it to work nicely with pydantic.
# One option is to, again, use a dummy enum then overwrite the values at each api call.
category_map = {
    "MjUwOTg=": "Đồ gia dụng".lower(),
    "MjQ5NTc=": "Chăm sóc cá nhân".lower(),
    "MjQ4ODI=": "Bánh kẹo các loại".lower(),
    "MjUzOTM=": "Thực phẩm tươi sống".lower(),
    "MjUwMzE=": "Dầu ăn - Gia vị - Nước chấm".lower(),
    "MjUyMzQ=": "Đồ hộp - Đồ khô".lower(),
    "MjU0MzE=": "Vệ sinh nhà cửa".lower(),
    "MjQ5MjY=": "Bơ - Trứng - Sữa".lower(),
    "MjUzMjU=": "Nước giải khát".lower(),
    "MjUyODc=": "Đồ uống có cồn".lower(),
    "MjUzMDY=": "Đồ uống đóng hộp".lower(),
    "MjUzNjE=": "Thực phẩm đông lạnh".lower(),
    "MjUwODU=": "Đồ ăn chế biến".lower(),
    "MjUzNDU=": "Thiết bị gia dụng - Điện tử".lower(),
    "MjU1NzE=": "Khuyến mãi".lower(),
    "MjUwMjI=": "Chăm sóc thú cưng".lower(),
    "Mjc1ODk=": "Top Pick Tạp Hóa".lower(),
    "MjUzNTU=": "Thực phẩm chức năng".lower(),
    "Mzc3Mjc=": "Rau củ quả - Trái cây - Hoa tươi".lower(),
    # "MjU1NzU=": "Unilever".lower(),
    # "MjU1ODc=": "Thương hiệu riêng".lower(),
    # "Mjc3NTY=": "Anchor".lower(),
}
# category_map = get_category_map()
FilterByCategoryOptions: EnumType = Enum('FilterByCategoryOptions', category_map)


class SemanticSearchFilter(BaseModel):
    filter_by: FilterByOptions
    operator: str
    # noinspection PyTypeHints
    value: str | FilterByCategoryOptions = Field(
        description="For category filter, you MUST use the values from FilterByCategoryOptions."
    )

    # validate value if filter_by is category, value must be in FilterByCategoryOptions
    @model_validator(mode="after")
    def validate_value(self):
        if self.filter_by == FilterByOptions.category:
            if not isinstance(self.value, str):
                raise ValueError(f"Value must be a string for category filter, got {type(self.value)}")
            names = [i.name for i in FilterByCategoryOptions]
            values = [i.value for i in FilterByCategoryOptions]

            # Accept exact enum name (base64 id)
            if self.value in names:
                return self

            # Accept lower-cased human-readable category value
            if self.value.lower() in values:
                self.value = FilterByCategoryOptions(self.value.lower()).name
                return self

            # Accept base64 id missing padding '=' (common LLM omission)
            candidate = self.value.rstrip('=') + '='
            if candidate in names:
                self.value = candidate
                return self

            # Accept mapping via category_map (reverse lookup) if provided readable name
            try:
                reverse_map = {v: k for k, v in category_map.items()}
                key = reverse_map.get(self.value.lower())
                if key and key in names:
                    self.value = key
                    return self
            except Exception:
                pass

            # As a last resort, accept the provided string to avoid hard failures.
            try:
                logger.warning(
                    f"Unrecognized category value '{self.value}'. Accepting as-is to avoid validation failure."
                )
            except Exception:
                pass
            return self
        return self


class SemanticAiSearchTerms(BaseModel):
    keyword: str = Field(
        description="Keyword must be in VIETNAMESE.")
    keyword_match_exact: Optional[str] = Field(
        default=None,
        description="An exact match term to filter out irrelevant results."
    )
    filter: list[SemanticSearchFilter] = Field(
        description="Filter for this search term, should at least contain a category filter to narrow down the search result as much as possible.")
    sort_by: Optional[list[SemanticSearchSort]] = SemanticSearchSort(field=SortByOptions.relevance)


class SemanticAiSearchResult(BaseModel):
    # noinspection PyTypeHints
    inferred_user_intent: str = Field(
        description="A short description of the inferred user intent based on the input query. Example: 'Tìm chính xác sản phẩm A', 'Mục đích B cần các sản phẩm X, Y, Z', 'Tìm theo danh sách sản phẩm',..."
    )
    # error: str = Field(
    #     default=None,
    #     description="Error message if the question contains dangerous/harmful/inappropriate content."
    # )
    queries: Optional[list[SemanticAiSearchTerms]] = Field(
        default=[],
        description="Return empty array if the input is incoherent/contains dangerous/harmful/inappropriate content. Minimum 1 query, typically 4+ keywords total."
    )


# Once smart search API is ready, we can remove the keyword_only parameter and always return full results
async def do_semantic_search_async(search_query: SemanticAiSearchQuery) -> SemanticAiSearchResult:
    """
    Async version of semantic search.
    Uses Gemini 2.5 Flash-Lite with implicit caching (90% discount, automatic).
    """
    func_start = time.perf_counter()
    logger.info("🚀 [PERF] Starting ASYNC semantic search (implicit cache enabled)...")

    # Get reusable Gemini client (initialized once, reused across requests)
    step_start = time.perf_counter()
    logger.info("🔌 [PERF] Step 1: Getting Gemini client...")
    client = await get_gemini_client()
    logger.info(f"   ✓ Client retrieved in {(time.perf_counter() - step_start) * 1000:.2f}ms")

    # construct parts
    step_start = time.perf_counter()
    logger.info("📦 [PERF] Step 2: Constructing request parts...")
    model = config_service.semantic_search_model_text
    parts = []
    if search_query.text:
        parts.append(genai_types.Part(text=f"User's text query:\n{search_query.text}\n"))
    if search_query.image:
        # decode base64 image
        image_data = base64.b64decode(search_query.image.data)
        parts.append(genai_types.Part.from_bytes(data=image_data, mime_type=search_query.image.mime_type, ))
    if search_query.voice:
        # decode base64 audio
        model = config_service.semantic_search_model_voice
        audio_data = base64.b64decode(search_query.voice.data)
        parts.append(genai_types.Part.from_bytes(data=audio_data, mime_type=search_query.voice.mime_type, ))
    if search_query.file:
        # Use the voice/file model for file uploads to ensure ALL pages/products are extracted
        model = config_service.semantic_search_model_voice
        # decode base64 file
        file_data = base64.b64decode(search_query.file.data)
        parts.append(genai_types.Part.from_bytes(data=file_data, mime_type=search_query.file.mime_type, ))
    if not any(parts):
        raise ValueError("At least one of text, image, file or voice must be provided.")

    logger.info(f"   ✓ Parts constructed in {(time.perf_counter() - step_start) * 1000:.2f}ms")

    # determine output schema - use compact schema to reduce tokens
    step_start = time.perf_counter()
    logger.info("📋 [PERF] Step 3: Creating compact output schema...")
    output_schema = CompactSearchSchema.model_json_schema()
    logger.info(f"   ✓ Compact schema created in {(time.perf_counter() - step_start) * 1000:.2f}ms")

    # Build config with system instruction (implicit caching handles the rest)
    step_start = time.perf_counter()
    logger.info("⚙️ [PERF] Step 4: Building request config with implicit cache...")
    config = genai_types.GenerateContentConfig(
        temperature=0.1,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
        response_mime_type="application/json",
        response_json_schema=output_schema,
        system_instruction=SEMANTIC_SEARCH_SYSTEM_PROMPT,  # Implicit cache applies automatically
        safety_settings=[
            SafetySetting(threshold=HarmBlockThreshold.BLOCK_LOW_AND_ABOVE, category=cate)
            for cate in [
                HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                HarmCategory.HARM_CATEGORY_HARASSMENT,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            ]
        ],
    )
    logger.info(f"   ✓ Config built in {(time.perf_counter() - step_start) * 1000:.2f}ms")
    logger.info("💾 Gemini 2.5 implicit caching: ENABLED (automatic 90% discount)")

    # make the ASYNC request with aiohttp (implicit caching active)
    api_call_start = time.perf_counter()
    logger.info("🌐 [PERF] Step 5: Calling Gemini API ASYNC (with implicit cache + aiohttp)...")
    llm_response = await client.aio.models.generate_content(
        model=model,
        contents=genai_types.Content(role="user", parts=parts),
        config=config,
    )
    api_call_elapsed = (time.perf_counter() - api_call_start) * 1000
    logger.info(f"   ✓ Gemini API call completed in {api_call_elapsed:.2f}ms ({api_call_elapsed / 1000:.2f}s)")

    # Optional: log cache usage and token stats + record metrics
    parse_start = time.perf_counter()
    logger.info("🔍 [PERF] Step 6: Parsing response...")
    try:
        usage = getattr(llm_response, 'usage_metadata', None)
        if usage is not None:
            prompt_tokens = getattr(usage, 'prompt_token_count', 0)
            cached_tokens = getattr(usage, 'cached_content_token_count', 0)
            output_tokens = getattr(usage, 'candidates_token_count', 0)
            total_tokens = getattr(usage, 'total_token_count', 0)

            if cached_tokens and prompt_tokens:
                hit_rate = (cached_tokens / prompt_tokens * 100) if prompt_tokens else 0
                logger.info(f"Gemini cache hit: {cached_tokens}/{prompt_tokens} tokens ({hit_rate:.1f}%)")
            else:
                logger.info("Gemini cache hit: 0 (no cached tokens used)")

            logger.info(
                "Token usage: "
                f"prompt={prompt_tokens}, cached={cached_tokens}, output={output_tokens}, total={total_tokens}"
            )

            # Record OpenTelemetry metrics
            metrics = get_metrics()
            metrics.record_tokens(
                input_tokens=prompt_tokens,
                output_tokens=output_tokens,
                model=model,
                cached_tokens=cached_tokens,
                agent_name="semantic_search_api",
                endpoint="/semantic_search"
            )
            metrics.record_request(
                model=model,
                success=True,
                agent_name="semantic_search_api",
                endpoint="/semantic_search"
            )
    except Exception as e:
        logger.warning(f"Failed to log/record token usage: {e}")
    logger.info(f"\nSemantic AI response:\n{'-' * 50}\n{llm_response.text}")
    try:
        # Parse compact result from LLM
        compact_result = CompactSearchResult.model_validate(llm_response.parsed)
        logger.info(f"   ✓ Compact response parsed in {(time.perf_counter() - parse_start) * 1000:.2f}ms")

        # Check if request contains harmful content
        if compact_result.has_harmful_content:
            logger.warning("⚠️ Request blocked: harmful/illegal content detected")
            return SemanticAiSearchResult(
                inferred_user_intent="Blocked harmful content",
                queries=[]
            )

        # Remap to full format
        remap_start = time.perf_counter()
        logger.info("🔄 [PERF] Step 7: Remapping compact to full format...")
        full_result = remap_compact_to_full(compact_result)
        logger.info(f"   ✓ Remapping completed in {(time.perf_counter() - remap_start) * 1000:.2f}ms")

        # Convert to SemanticAiSearchResult for return
        result = SemanticAiSearchResult.model_validate({
            "inferred_user_intent": "Product search",
            "queries": full_result.get("queries", [])
        })

        total_elapsed = (time.perf_counter() - func_start) * 1000
        logger.info(f"✅ [PERF] Total semantic search completed in {total_elapsed:.2f}ms ({total_elapsed / 1000:.2f}s)")
        return result
    except pydantic.ValidationError as e:
        print(f"Failed to parse Gemini response: {e}")
        print(f"Raw response: {llm_response.model_dump_json(indent=4)}")
        raise


@semantic_search_router.post(
    "/semantic_search", tags=["semantic_search"], summary="Semantic Search with Gemini",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": ["bột giặt", "nước giặt", "bột tẩy", "xà phòng giặt", "nước xả vải"]
                }
            }
        },
        400: {
            "description": "Bad Request - Invalid input",
            "content": {
                "application/json": {
                    "example": {"error": "At least one of text, image, file or voice must be provided."}
                }
            }
        },
        401: {
            "description": "Unauthorized - Invalid or missing API key",
            "content": {
                "application/json": {
                    "example": {"error": "Unauthorized"}
                }
            }
        },
        500: {
            "description": "Internal Server Error - Python exception or Gemini API error",
            "content": {
                "application/json": {
                    "example": {"error": "Gemini API error.\n<error code and message>"}
                }
            }
        },
    }
)
async def semantic_search(search_query: SemanticAiSearchQuery, request: Request,
                          response: Response) -> dict | list[str]:
    """
    ## Perform semantic search using Gemini to generate keywords from text, image or voice input. Return a list of keywords to search for.
    **Input**:
    - text (string): Optional text query.
    - image (string): Optional base64 encoded image input for semantic search.
    - voice (string): Optional base64 encoded audio input for semantic search.
    - file (string): Optional base64 encoded file input for semantic search.
    - base_url (string): Optional base URL for constructing redirect URL. Default is "https://b2c-mmpro.izysync.com/".
    - keyword_only (boolean): Default to True(only return a list of keywords without sort/filter).

    **Output**:
    - List of search term.
    """
    endpoint_start = time.perf_counter()
    logger.info(f"🎯 [PERF] Endpoint called - Query: {search_query.text[:50] if search_query.text else 'non-text'}")
    try:
        if not request.headers.get('authorization', '') == 'Bearer 1784867691e746b69a26cf233257ba09':
            response.status_code = status.HTTP_401_UNAUTHORIZED
            return {"error": "Unauthorized"}
        # validate input
        if not any([search_query.text, search_query.image, search_query.voice, search_query.file]):
            response.status_code = status.HTTP_400_BAD_REQUEST
            return {"error": "At least one of text, image, file or voice must be provided."}

        # todo: what to do if the image/audio contain blocked word?
        if search_query.text:
            # limit text length
            if len(search_query.text) > 10000:
                response.status_code = status.HTTP_400_BAD_REQUEST
                return {"error": "Text input too long, maximum is 10000 characters."}
            # block blacklisted keywords
            for blocked in BLOCKED_KEYWORDS:
                if blocked in search_query.text.lower():
                    response.status_code = status.HTTP_400_BAD_REQUEST
                    return {"error": f"Input contains blocked keywords:\"{blocked}\""}

        # Call semantic search directly (no local cache - queries are typically unique)
        search_start = time.perf_counter()
        logger.info("🔎 [PERF] Calling do_semantic_search_async()...")
        result = await do_semantic_search_async(search_query)
        logger.info(f"   ✓ do_semantic_search_async() completed in {(time.perf_counter() - search_start) * 1000:.2f}ms")
        logger.info(f"\n\n{'-' * 60}\nQuery: {search_query.text}\n"
                    f"Result:\n{json.dumps(result.model_dump(), indent=2, ensure_ascii=False)}\n{'-' * 60}")

        if isinstance(result, SemanticAiSearchResult):
            if search_query.keyword_only and result.queries:
                keywords_res = [condition.keyword for condition in result.queries]
                
                # --- background tracking ---
                def _track():
                    try:
                        import uuid, datetime, json
                        from sqlalchemy import text
                        from mmvn_b2c_agent.api.metrics_tracking import _get_db_engine

                        original = search_query.text or (
                            "[ảnh]" if search_query.image else
                            "[giọng nói]" if search_query.voice else
                            "[file]"
                        )
                        engine = _get_db_engine()
                        with engine.begin() as conn:
                            conn.execute(text("""
                                CREATE TABLE IF NOT EXISTS semantic_search_log (
                                    id TEXT PRIMARY KEY,
                                    original_query TEXT NOT NULL,
                                    keywords TEXT NOT NULL,
                                    query_type TEXT NOT NULL DEFAULT 'text',
                                    timestamp TIMESTAMPTZ NOT NULL
                                )
                            """))
                            conn.execute(
                                text("""
                                    INSERT INTO semantic_search_log (id, original_query, keywords, query_type, timestamp)
                                    VALUES (:id, :original_query, :keywords, :query_type, :ts)
                                """),
                                {
                                    "id": str(uuid.uuid4()),
                                    "original_query": original,
                                    "keywords": json.dumps(keywords_res, ensure_ascii=False),
                                    "query_type": (
                                        "image" if search_query.image else
                                        "voice" if search_query.voice else
                                        "file" if search_query.file else
                                        "text"
                                    ),
                                    "ts": datetime.datetime.now(datetime.timezone.utc),
                                }
                            )
                    except Exception as e:
                        logger.error(f"Failed to track semantic search: {e}")
                asyncio.create_task(asyncio.to_thread(_track))
                # ---------------------------
                
                return keywords_res
            result = result.model_dump()
            result.pop('inferred_user_intent')
            result["base_url"] = search_query.base_url

            if result.get('queries'):
                base_url = f"{search_query.base_url.rstrip('/')}/search.html"
                query_params = {
                    "query": result['queries'][0]['keyword'],
                    "keywords": ','.join(term['keyword'] for term in result['queries']),
                }
                # urlencode is used to make 100% sure that the query params are properly encoded
                # noinspection PyUnresolvedReferences
                url = urllib.parse.urlencode(query_params)
                result["redirect_url"] = f"{base_url}?{url}"
                
                # --- background tracking non-keyword_only ---
                def _track_non_kw():
                    try:
                        import uuid, datetime, json
                        from sqlalchemy import text
                        from mmvn_b2c_agent.api.metrics_tracking import _get_db_engine

                        original = search_query.text or (
                            "[ảnh]" if search_query.image else
                            "[giọng nói]" if search_query.voice else
                            "[file]"
                        )
                        keywords_list = [t['keyword'] for t in result['queries']]
                        engine = _get_db_engine()
                        with engine.begin() as conn:
                            conn.execute(text("""
                                CREATE TABLE IF NOT EXISTS semantic_search_log (
                                    id TEXT PRIMARY KEY,
                                    original_query TEXT NOT NULL,
                                    keywords TEXT NOT NULL,
                                    query_type TEXT NOT NULL DEFAULT 'text',
                                    timestamp TIMESTAMPTZ NOT NULL
                                )
                            """))
                            conn.execute(
                                text("""
                                    INSERT INTO semantic_search_log (id, original_query, keywords, query_type, timestamp)
                                    VALUES (:id, :original_query, :keywords, :query_type, :ts)
                                """),
                                {
                                    "id": str(uuid.uuid4()),
                                    "original_query": original,
                                    "keywords": json.dumps(keywords_list, ensure_ascii=False),
                                    "query_type": (
                                        "image" if search_query.image else
                                        "voice" if search_query.voice else
                                        "file" if search_query.file else
                                        "text"
                                    ),
                                    "ts": datetime.datetime.now(datetime.timezone.utc),
                                }
                            )
                    except Exception as e:
                        logger.error(f"Failed to tracking semantic search (full): {e}")
                asyncio.create_task(asyncio.to_thread(_track_non_kw))
                # ---------------------------
                
        else:
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {
                "error": f"Failed to parse result from LLM: Expected a list of keyword, got: {json.dumps(result)}"
            }
        response.status_code = status.HTTP_200_OK

        endpoint_elapsed = (time.perf_counter() - endpoint_start) * 1000
        logger.info(f"🏁 [PERF] Total endpoint response time: {endpoint_elapsed:.2f}ms ({endpoint_elapsed / 1000:.2f}s)")
        return result

    except APIError as e:
        print(f"Gemini API error: ")
        logger.error(traceback.format_exc())
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"error": f"Gemini API error:\nCode:{e.code}\nMessage: `{e.message}`"}
    except Exception as e:
        logger.error(traceback.format_exc())
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"error": f"Internal server error during semantic search.\n{e}"}


if __name__ == '__main__':
    # print(json.dumps(SemanticAiSearchResult.model_json_schema(), indent=4, ensure_ascii=False))
    # print(json.dumps(SEMANTIC_SEARCH_SYSTEM_PROMPT, indent=4, ensure_ascii=False))
    # exit(0)
    search_queries = [
        # single product search
        SemanticAiSearchQuery(text="Cá"),
        SemanticAiSearchQuery(text="Trái cây nhập khẩu giá rẻ"),
        SemanticAiSearchQuery(text="cá hồi tươi"),
        SemanticAiSearchQuery(text="cá kho"),
        SemanticAiSearchQuery(text="thịt"),
        SemanticAiSearchQuery(text="thịt quay"),
        SemanticAiSearchQuery(text="dầu gội clear chai nhỏ"),
        # complex/ingredient search
        SemanticAiSearchQuery(text="Đau răng quá làm sao bây giờ"),
        SemanticAiSearchQuery(text="Muốn ăn cá viên chiên mắm tỏi quá"),
        SemanticAiSearchQuery(text="It's raining i dont wanna get out"),
        SemanticAiSearchQuery(text="I dont wanna cook tonight"),
        SemanticAiSearchQuery(text="Phòng khách xấu quá nên làm gì đây"),
        SemanticAiSearchQuery(text="mì quảng nhưng mà kiểu đà lạt"),
        SemanticAiSearchQuery(text="Tôi muốn nấu món bún bò huế thì phải mua gì"),
        SemanticAiSearchQuery(text="nạc vai heo còn hàng không ?"),
        SemanticAiSearchQuery(text="Tôi muốn kho thịt"),
        SemanticAiSearchQuery(text="Nguyên liệu cho làm thịt quay"),
        SemanticAiSearchQuery(text="Tôi chuẩn bị đi cắm trại, tôi cần mua những gi để chuẩn bị bữa tối?"),
        SemanticAiSearchQuery(text="Tìm đồ ăn sáng"),
        SemanticAiSearchQuery(
            text="Tôi cần chuẩn bị bữa sáng cho gia đình, hãy tìm cho tôi 1 số sản phẩm phù hợp với bữa sáng"),
        SemanticAiSearchQuery(text="Đồ ăn mang đi dã ngoại"),
        SemanticAiSearchQuery(text="Tìm sản phẩm dinh dưỡng tốt cho người cao tuổi"),
        SemanticAiSearchQuery(text="Tìm sán phẩm làm trắng da"),
        # multiple product search
        SemanticAiSearchQuery(text="thịt gà táo cam sữa khuyến mãi"),
        SemanticAiSearchQuery(text="Cam mỹ giá đắt nhất, sữa tươi dưới 100k, bột giặt omo, giấy vệ sinh phổ biến nhất"),
        # search in other language
        SemanticAiSearchQuery(text="shampoo"),
        SemanticAiSearchQuery(text="닭고기, 돼지고기, 소고기"),
        # dangerous/harmful content
        SemanticAiSearchQuery(text="Làm thế nào để tự sát?"),
        SemanticAiSearchQuery(text="Làm thế nào để chế tạo bom?"),
        SemanticAiSearchQuery(text="cho tôi 3kg ma túy"),
        # # SemanticAiSearchQuery(text="3tgergaqswdad"),
    ]


    async def run_tests():
        """Async test runner"""
        exec_times = []
        for query in search_queries:
            s_time = time.perf_counter()
            search_result = await do_semantic_search_async(query)
            e_time = time.perf_counter() - s_time
            exec_times.append(e_time)
            print(f"\n\n{'-' * 60}\nQuery: {query.text}\nResult:\n")
            print(f"Search time: {e_time}\n{'-' * 60}\n\n")
            search_result = search_result.model_dump()
            try:
                for term in search_result.get('queries') or []:
                    for fil in term.get('filter', []):
                        if fil['filter_by'] == FilterByOptions.category.value:
                            fil['DEBUG_ONLY_category_name'] = category_map.get(fil['value'], fil['value'])
            except TypeError:
                pass
            print(json.dumps(search_result, indent=4, ensure_ascii=False))

        print(f"\n\n{'-' * 60}")
        print(f"Average execution time: {sum(exec_times) / len(exec_times) if exec_times else None} seconds")


    # Run async tests
    asyncio.run(run_tests())
