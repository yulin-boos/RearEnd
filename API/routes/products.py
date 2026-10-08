import logging
from Common.errors import DatabaseError
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse

from Shop.catalog import CatalogUnavailable, ProductCatalog
from Shop.schemas import AgriProduct, CategoryCount, ProductCategory, ProductPage, ProductSort

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/products", tags=["农业商品"])


def get_catalog(request: Request) -> ProductCatalog:
    catalog = getattr(request.app.state, "product_catalog", None)
    if catalog is None:
        if request.app.state.settings.database_backend == "mysql":
            raise DatabaseError()
        catalog = ProductCatalog(request.app.state.settings.product_data_dir)
        request.app.state.product_catalog = catalog
    try:
        catalog.ensure_loaded()
    except CatalogUnavailable as error:
        logger.error("Agricultural product catalog is unavailable", exc_info=error)
        raise HTTPException(503, {"code": "product_catalog_unavailable",
                                  "message": "农业商品数据暂不可用，请检查后端商品素材配置"}) from error
    return catalog


@router.get("", response_model=ProductPage, summary="农业商品列表、搜索和分页")
def product_list(catalog: Annotated[ProductCatalog, Depends(get_catalog)],
                 limit: Annotated[int, Query(ge=1, le=200)] = 24,
                 offset: Annotated[int, Query(ge=0)] = 0,
                 q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
                 category: ProductCategory | None = None,
                 sort: ProductSort = "default"):
    return catalog.list(limit=limit, offset=offset, q=q, category=category, sort=sort)


@router.get("/categories", response_model=list[CategoryCount], summary="农业商品分类与数量")
def product_categories(catalog: Annotated[ProductCatalog, Depends(get_catalog)]):
    return catalog.categories()


@router.get("/images/{filename}", summary="商品页对应的本地图片", response_class=FileResponse)
def product_image(filename: str, catalog: Annotated[ProductCatalog, Depends(get_catalog)]):
    path = catalog.get_image(filename)
    if path is None:
        raise HTTPException(404, "商品图片不存在")
    media_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                  ".webp": "image/webp", ".gif": "image/gif"}[path.suffix.lower()]
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/{product_id}", response_model=AgriProduct, summary="农业商品详情与报价来源")
def product_detail(product_id: Annotated[str, Path(pattern=r"^hn-[0-9]+$")],
                   catalog: Annotated[ProductCatalog, Depends(get_catalog)]):
    product = catalog.get(product_id)
    if product is None:
        raise HTTPException(404, "农业商品不存在")
    return product
