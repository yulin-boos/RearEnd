from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ProductCategory = Literal["insect", "disease", "biological", "tools", "irrigation", "fertilizer", "supplies"]
ProductSort = Literal["default", "price_asc", "price_desc"]


class ProductVariant(BaseModel):
    name: str
    price: float = Field(gt=0, allow_inf_nan=False)
    price_max: float = Field(gt=0, allow_inf_nan=False)
    unit: str
    price_text: str
    min_order: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    min_order_text: str


class ProductImageMetadata(BaseModel):
    path: str
    source_url: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgriProduct(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^hn-[0-9]+$")
    source_id: str = Field(pattern=r"^[0-9]+$")
    name: str = Field(min_length=1)
    title: str = Field(min_length=1)
    category: ProductCategory
    spec: str = Field(min_length=1)
    price: float = Field(gt=0, allow_inf_nan=False)
    price_max: float = Field(gt=0, allow_inf_nan=False)
    currency: Literal["CNY"]
    unit: str = Field(min_length=1)
    min_order: float = Field(gt=0, allow_inf_nan=False)
    min_order_text: str = Field(min_length=1)
    price_note: str
    description: str
    description_kind: str
    source_description: str
    attributes: dict[str, str]
    variants: list[ProductVariant]
    variant_scope: str
    source_url: str
    source_platform: str
    seller: str
    shipping_from: str
    fetched_at: str
    source_image_urls: list[str]
    image_metadata: list[ProductImageMetadata]
    image: str
    images: list[str] = Field(min_length=1)
    image_scope: str
    keywords: list[str]

    @model_validator(mode="after")
    def valid_price_and_identity(self):
        if self.price_max < self.price or self.id != "hn-" + self.source_id:
            raise ValueError("Invalid source identity or quoted price range")
        if self.image not in self.images:
            raise ValueError("The main product image must belong to the gallery")
        if self.source_url != "https://www.cnhnb.com/gongying/" + self.source_id + "/":
            raise ValueError("Unexpected source product page")
        return self


class ProductPage(BaseModel):
    products: list[AgriProduct]
    total: int
    offset: int
    limit: int


class CategoryCount(BaseModel):
    id: ProductCategory
    name: str
    count: int
