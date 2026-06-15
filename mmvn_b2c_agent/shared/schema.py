import copy

import mmvn_b2c_agent.shared.constants
from enum import Enum
from typing import Optional
from pydantic import BaseModel

MMVN_MAIN_CATEGORY_MAP = {
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
    "MjU1NzU=": "Unilever".lower(),
    "MjU1ODc=": "Thương hiệu riêng".lower(),
    "Mjc3NTY=": "Anchor".lower(),
    "Mzc3Mjc=": "Rau củ quả - Trái cây - Hoa tươi".lower(),
}
# category_map = get_category_map()
MagentoMainCategories: Enum = Enum('FilterByCategoryOptions', MMVN_MAIN_CATEGORY_MAP, type=str)


class MagentoProductSortOptions(str, Enum):
    ecom_name = "NAME"
    mm_sale_price_include_vat = "PRICE"
    relevance = "RELEVANCE"
    ecom_qty_ordered = "POPULAR"


class MagentoProductSortDirection(str, Enum):
    ASC = "ASC"
    DESC = "DESC"


class MagentoProductFields(str, Enum):
    # A list of AI-friendly product fields for the AI to choose from.
    # The product_graphql_field_map dict will map these fields to the actual GraphQL query.
    # This way, the AI will not have to deal with graphql.
    PRODUCT_ID = "id"
    PRODUCT_UID = "uid"
    ART_NO = "art_no"
    SKU = "sku"
    NAME = "name"
    PRICE = "price"
    PRICE_WITH_DISCOUNT = "price_with_discount"  # Price with discount info for promotion tools
    DESCRIPTION = "description"
    MM_BRAND = "mm_brand"
    NEED_AGE_VERIFICATION = "need_age_verification"
    CATEGORIES = "categories"
    STOCK_STATUS = "stock_status"
    UNIT = 'unit'
    PRODUCT_TYPE = 'product_type'  # mm_product_type: 'F' = Fresh (step 0.5), 'N' = Normal (step 1)
    URL = "url"
    SMALL_IMAGE = "small_image"
    RELATED_PRODUCTS = "related_products"
    PROMO_INFO = "promo_info"

    # Unused fields or fields that are too complex for the AI to handle.
    # PRICE_RANGE = "price_range"
    # REVIEWS = "reviews"
    # SIMILAR_PRODUCTS = "similar_products"
    # UPSELL_PRODUCTS = "upsell_products"
    # CROSSSELL_PRODUCTS = "crosssell_products"
    # THUMBNAIL = "thumbnail"
    # IMAGE = "image"
    # MEDIA_GALLERY = "media_gallery"

    @property
    def graphql_query(self):
        """Get the actual GraphQL query for this field."""
        return product_graphql_field_map.get(self) or self.value


product_graphql_field_map = {v: v.value for v in MagentoProductFields.__members__.values()}
product_graphql_field_map.update({
    # Special handling for fields that require sub-fields or other name.
    # format: "field_name": "graphql_query"
    MagentoProductFields.NAME: "ecom_name name",
    MagentoProductFields.UNIT: "unit_ecom",
    MagentoProductFields.PRODUCT_TYPE: "mm_product_type",
    MagentoProductFields.URL: "canonical_url",
    MagentoProductFields.PRICE: "price_range { maximum_price { final_price { currency value } } }",
    MagentoProductFields.PRICE_WITH_DISCOUNT: "price_range { maximum_price { final_price { currency value } regular_price { currency value } discount { amount_off percent_off } } }",
    MagentoProductFields.NEED_AGE_VERIFICATION: "is_alcohol",
    MagentoProductFields.CATEGORIES: "categories { uid name }",
    MagentoProductFields.DESCRIPTION: "description {html} short_description {html}",
    MagentoProductFields.SMALL_IMAGE: "small_image {url}",
    MagentoProductFields.RELATED_PRODUCTS: "related_products { sku name }",
    MagentoProductFields.PROMO_INFO: "dnr_price { event_id event_name promo_amount promo_label promo_type promo_value qty }",
    # MagentoProductFields.IMAGE: "image {url}",
    # MagentoProductFields.THUMBNAIL: "thumbnail {url}",
    # MagentoProductFields.MEDIA_GALLERY: "media_gallery { url label position }",
    # MagentoProductFields.SIMILAR_PRODUCTS: "similar_products { sku name }",
    # MagentoProductFields.UPSELL_PRODUCTS: "upsell_products { sku name }",
    # MagentoProductFields.CROSSSELL_PRODUCTS: "crosssell_products { sku name }",
    # MagentoProductFields.PRICE_RANGE: "price_range { maximum_price { final_price { currency value } discount { amount_off percent_off}}}"
    # MagentoProductFields.REVIEWS: "reviews { items { average_rating ratings_breakdown { name value } } }",
})
