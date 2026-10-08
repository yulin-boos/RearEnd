"""Read the collected public quotes without inventing commerce statistics."""
from __future__ import annotations

import json
from pathlib import Path
import re
from threading import Lock

from pydantic import ValidationError

from Shop.schemas import AgriProduct, CategoryCount, ProductPage

CATEGORY_NAMES = {
    "insect": "虫害防治", "disease": "病害防治", "biological": "生物防治",
    "tools": "农业工具", "irrigation": "灌溉用品", "fertilizer": "肥料营养", "supplies": "种植耗材",
}
IMAGE_NAME = re.compile(r"^hn-[0-9]+-[0-9]{2}\.(?:jpg|jpeg|png|webp|gif)$")
IMAGE_URL_PREFIX = "/api/v1/products/images/"


class CatalogUnavailable(RuntimeError):
    pass


class ProductCatalog:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve()
        self._products: tuple[AgriProduct, ...] | None = None
        self._by_id: dict[str, AgriProduct] = {}
        self._images: dict[str, Path] = {}
        self._category_names = dict(CATEGORY_NAMES)
        self._lock = Lock()

    def ensure_loaded(self):
        if self._products is not None:
            return
        with self._lock:
            if self._products is not None:
                return
            try:
                rows = json.loads((self.data_dir / "products.json").read_text(encoding="utf-8"))
                self._install(rows)
            except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError, ValidationError) as error:
                raise CatalogUnavailable("Unable to load the agricultural catalog") from error

    def _install(self, rows):
        if not isinstance(rows, list) or not rows:
            raise ValueError("The product catalog is empty or is not an array")
        products, by_id, images = [], {}, {}
        image_dir = (self.data_dir / "images").resolve()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid catalog item")
            row = row.copy()
            identifier = row["id"]
            if identifier in by_id:
                raise ValueError("Duplicate product id")
            main = row["image"]
            gallery = row["images"]
            if not isinstance(gallery, list) or main not in gallery:
                raise ValueError("Invalid product gallery")
            urls = []
            for relative in gallery:
                if not isinstance(relative, str) or not relative.startswith("images/"):
                    raise ValueError("Invalid local product image")
                filename = relative.removeprefix("images/")
                if not IMAGE_NAME.fullmatch(filename) or not filename.startswith(identifier + "-"):
                    raise ValueError("Unexpected image filename")
                path = (image_dir / filename).resolve()
                if path.parent != image_dir or not path.is_file():
                    raise ValueError("Missing or unsafe product image")
                images[filename] = path
                urls.append(IMAGE_URL_PREFIX + filename)
            row["image"] = IMAGE_URL_PREFIX + main.removeprefix("images/")
            row["images"] = urls
            product = AgriProduct.model_validate(row)
            products.append(product)
            by_id[product.id] = product
        self._by_id, self._images, self._products = by_id, images, tuple(products)

    def list(self, *, limit=24, offset=0, q=None, category=None, sort="default") -> ProductPage:
        self.ensure_loaded()
        products = list(self._products or ())
        if category:
            products = [product for product in products if product.category == category]
        query = (q or "").strip().casefold()
        if query:
            terms = query.split()
            def matches(product):
                searchable = " ".join([
                    product.name, product.title, product.spec, product.description,
                    self._category_names[product.category], product.seller, *product.keywords,
                ]).casefold()
                return all(term in searchable for term in terms)
            products = [product for product in products if matches(product)]
        if sort != "default":
            products = sorted(products, key=lambda product: product.price, reverse=sort == "price_desc")
        return ProductPage(products=products[offset:offset + limit], total=len(products), offset=offset, limit=limit)

    def categories(self) -> list[CategoryCount]:
        self.ensure_loaded()
        return [CategoryCount(id=identifier, name=name,
                              count=sum(product.category == identifier for product in self._products or ()))
                for identifier, name in self._category_names.items()]

    def get(self, identifier: str) -> AgriProduct | None:
        self.ensure_loaded()
        return self._by_id.get(identifier)

    def get_image(self, filename: str) -> Path | None:
        self.ensure_loaded()
        if not IMAGE_NAME.fullmatch(filename):
            return None
        path = self._images.get(filename)
        if path is None or not path.is_file() or path.resolve().parent != (self.data_dir / "images").resolve():
            return None
        return path
