from collections import defaultdict
import time

from pydantic import ValidationError

from Common.database import json_value
from Shop.catalog import CATEGORY_NAMES, CatalogUnavailable, ProductCatalog


class MySQLProductCatalog(ProductCatalog):
    """The MySQL catalog is authoritative; files supply only image bytes."""
    def __init__(self, database, data_dir, cache_seconds=30):
        super().__init__(data_dir)
        self.database = database
        self.cache_seconds = cache_seconds
        self._loaded_at = 0

    def ensure_loaded(self):
        if self._products is not None and time.monotonic() - self._loaded_at < self.cache_seconds:
            return
        with self._lock:
            if self._products is not None and time.monotonic() - self._loaded_at < self.cache_seconds:
                return
            with self.database.cursor() as cursor:
                cursor.execute("SELECT code,name FROM product_categories ORDER BY sort_order,code")
                categories = cursor.fetchall()
                cursor.execute("SELECT * FROM products ORDER BY sort_order,id")
                products = cursor.fetchall()
                cursor.execute("SELECT * FROM product_variants ORDER BY product_id,position")
                variants = defaultdict(list)
                for row in cursor.fetchall():
                    variants[row["product_id"]].append(row)
                cursor.execute("SELECT * FROM product_images ORDER BY product_id,position")
                images = defaultdict(list)
                for row in cursor.fetchall():
                    images[row["product_id"]].append(row)
            try:
                category_names = {item["code"]: item["name"] for item in categories}
                if set(category_names) != set(CATEGORY_NAMES):
                    raise ValueError("Unexpected product categories")
                rows = []
                for product in products:
                    row = dict(product)
                    identifier = row["id"]
                    row["category"] = row["category_code"]
                    row["fetched_at"] = row["fetched_at_original"]
                    for key in ("attributes", "keywords", "source_image_urls"):
                        row[key] = json_value(row[key])
                    row["variants"] = variants[identifier]
                    gallery = images[identifier]
                    row["images"] = [item["storage_path"] for item in gallery]
                    primary = next(item for item in gallery if item["position"] == row["main_image_position"])
                    row["image"] = primary["storage_path"]
                    row["image_metadata"] = [dict(path=item["storage_path"], source_url=item["source_url"],
                                                  width=item["width"], height=item["height"], sha256=item["sha256"])
                                             for item in gallery]
                    rows.append(row)
                self._install(rows)
                self._category_names = category_names
            except (OSError, KeyError, StopIteration, TypeError, ValueError, ValidationError) as error:
                raise CatalogUnavailable("Unable to load the MySQL agricultural catalog") from error
            self._loaded_at = time.monotonic()
