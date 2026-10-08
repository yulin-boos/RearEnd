"""Public agriculture catalog HTTP contract; no model or external API calls required."""

from collections import Counter
from hashlib import sha256
from pathlib import Path
import json
import shutil

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from API.errors import register_error_handlers
from API.main import create_app
from API.routes import products
from Common.config import PROJECT_ROOT, Settings
from tests.test_api import StubRecognizer


DATA_DIR = PROJECT_ROOT / "Shop" / "data"
IMAGE_PREFIX = "/api/v1/products/images/"
CATEGORIES = {"insect", "disease", "biological", "tools", "irrigation", "fertilizer", "supplies"}
SOURCE_FIELDS = (
    "id", "source_id", "name", "title", "category", "spec", "price", "price_max", "currency",
    "unit", "min_order", "min_order_text", "price_note", "description", "description_kind",
    "source_description", "source_url", "source_platform", "seller", "shipping_from", "fetched_at",
    "attributes", "variants", "variant_scope", "source_image_urls", "image_scope", "keywords",
)


@pytest.fixture(scope="module")
def source_products():
    rows = json.loads((DATA_DIR / "products.json").read_text(encoding="utf-8"))
    assert len(rows) == 200, "The bundled graduation-project catalog must contain the verified 200 products"
    return rows


def make_settings(tmp_path, data_dir=DATA_DIR):
    return Settings(
        product_data_dir=data_dir,
        result_dir=tmp_path / "results",
        chat_db_path=tmp_path / "chat.sqlite3",
        knowledge_db_path=tmp_path / "knowledge.sqlite3",
    )


def make_catalog_client(settings):
    """A small HTTP app proves catalog requests work with no recognition/chat state."""
    application = FastAPI()
    application.state.settings = settings
    register_error_handlers(application)
    application.include_router(products.router)
    return TestClient(application)


@pytest.fixture
def client(tmp_path):
    with make_catalog_client(make_settings(tmp_path)) as http:
        yield http


def get_products(client, **params):
    response = client.get("/api/v1/products", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def searchable_text(row):
    return " ".join(str(row[key]) for key in ("name", "title", "spec", "description", "keywords")).casefold()


def test_full_catalog_preserves_real_source_fields_and_avoids_fake_business_data(client, source_products):
    data = get_products(client, limit=200)
    assert {key: data[key] for key in ("total", "limit", "offset")} == {"total": 200, "limit": 200, "offset": 0}
    assert [row["id"] for row in data["products"]] == [row["id"] for row in source_products]
    for actual, source in zip(data["products"], source_products):
        for field in SOURCE_FIELDS:
            assert actual[field] == source[field], (source["id"], field)
        assert actual["currency"] == "CNY"
        assert actual["image"] == IMAGE_PREFIX + Path(source["image"]).name
        assert actual["images"] == [IMAGE_PREFIX + Path(path).name for path in source["images"]]
        assert len(actual["image_metadata"]) == len(source["image_metadata"])
        for metadata, original in zip(actual["image_metadata"], source["image_metadata"]):
            assert {key: metadata[key] for key in ("source_url", "width", "height", "sha256")} == {
                key: original[key] for key in ("source_url", "width", "height", "sha256")
            }
        assert not {"rating", "stock", "sales", "originalPrice", "discountPercentage", "discount"}.intersection(actual)


def test_default_and_all_pages_have_no_skipped_or_duplicate_products(client, source_products):
    default = get_products(client)
    assert (default["total"], default["limit"], default["offset"], len(default["products"])) == (200, 24, 0, 24)
    collected = []
    for offset in range(0, 200, 24):
        page = get_products(client, limit=24, offset=offset)
        assert page["total"] == 200
        assert page["offset"] == offset
        assert len(page["products"]) == min(24, 200 - offset)
        collected.extend(row["id"] for row in page["products"])
    assert collected == [row["id"] for row in source_products]
    assert len(set(collected)) == 200
    beyond = get_products(client, limit=24, offset=250)
    assert beyond["products"] == [] and beyond["total"] == 200
    assert get_products(client, limit=1, offset=199)["products"][0]["id"] == source_products[-1]["id"]


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 201}, {"limit": "invalid"}, {"offset": -1},
    {"offset": "1.5"}, {"sort": "rating"}, {"category": "beauty"}, {"q": "x" * 201},
])
def test_invalid_query_parameters_are_validation_errors(client, params):
    response = client.get("/api/v1/products", params=params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_category_routes_precede_product_id_route_and_counts_are_real(client, source_products):
    response = client.get("/api/v1/products/categories")
    assert response.status_code == 200
    categories = response.json()
    assert len(categories) == 7
    assert {row["id"] for row in categories} == CATEGORIES
    assert {row["id"]: row["count"] for row in categories} == dict(Counter(row["category"] for row in source_products))
    assert all(isinstance(row["name"], str) and row["name"] for row in categories)
    assert sum(row["count"] for row in categories) == 200
    for category in categories:
        data = get_products(client, category=category["id"], limit=200)
        expected = [row["id"] for row in source_products if row["category"] == category["id"]]
        assert data["total"] == len(expected)
        assert [row["id"] for row in data["products"]] == expected


@pytest.mark.parametrize("query", ["虫", "滴灌"])
def test_search_accepts_single_chinese_character_and_real_keywords(client, source_products, query):
    data = get_products(client, q=query, limit=200)
    expected = {row["id"] for row in source_products if query.casefold() in searchable_text(row)}
    assert expected
    actual = {row["id"] for row in data["products"]}
    # Public category labels and seller names may add matches beyond product text.
    assert expected.issubset(actual)
    assert data["total"] == len(actual)
    assert all(row["category"] in CATEGORIES for row in data["products"])


def test_search_and_category_combine_before_pagination(client, source_products):
    canonical_matches = {row["id"] for row in source_products if row["category"] == "irrigation" and "滴灌" in searchable_text(row)}
    matching = get_products(client, category="irrigation", q="滴灌", limit=200)
    expected = matching["products"]
    assert canonical_matches.issubset({row["id"] for row in expected})
    assert all(row["category"] == "irrigation" for row in expected)
    data = get_products(client, category="irrigation", q="滴灌", limit=1, offset=1)
    assert data["total"] == len(expected) and len(expected) > 1
    assert [row["id"] for row in data["products"]] == [expected[1]["id"]]
    absent = get_products(client, q="不存在的农业产品XYZ123", limit=200)
    assert absent["total"] == 0 and absent["products"] == []


@pytest.mark.parametrize("sort,reverse", [("price_asc", False), ("price_desc", True)])
def test_sort_uses_collected_unit_quote_without_changing_units(client, source_products, sort, reverse):
    data = get_products(client, sort=sort, limit=200)
    quotes = [row["price"] for row in data["products"]]
    assert quotes == sorted([row["price"] for row in source_products], reverse=reverse)
    expected = sorted(source_products, key=lambda row: row["price"], reverse=reverse)
    page = get_products(client, sort=sort, limit=5, offset=7)
    assert [row["price"] for row in page["products"]] == [row["price"] for row in expected[7:12]]
    originals = {row["id"]: row for row in source_products}
    for row in data["products"]:
        assert (row["price"], row["price_max"], row["unit"], row["min_order"]) == tuple(originals[row["id"]][key] for key in ("price", "price_max", "unit", "min_order"))


def test_product_detail_retains_wholesale_minimum_range_and_source(client, source_products):
    wholesale = next(row for row in source_products if row["min_order"] >= 100)
    ranged = next(row for row in source_products if row["price_max"] > row["price"])
    for source in (wholesale, ranged, source_products[-1]):
        response = client.get("/api/v1/products/" + source["id"])
        assert response.status_code == 200
        actual = response.json()
        for field in SOURCE_FIELDS:
            assert actual[field] == source[field], (source["id"], field)
        assert actual["image"].startswith(IMAGE_PREFIX)
    missing = client.get("/api/v1/products/hn-999999999999999")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    invalid = client.get("/api/v1/products/invalid-product-id")
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "validation_error"


def test_all_562_published_image_paths_are_distinct_and_locally_readable(client, source_products):
    rows = get_products(client, limit=200)["products"]
    paths = [path for row in rows for path in row["images"]]
    assert len(paths) == 562 and len(set(paths)) == 562
    expected = {IMAGE_PREFIX + Path(path).name for row in source_products for path in row["images"]}
    assert set(paths) == expected
    hashes = {IMAGE_PREFIX + Path(meta["path"]).name: meta["sha256"] for row in source_products for meta in row["image_metadata"]}
    for path in paths:
        assert (DATA_DIR / "images" / path.removeprefix(IMAGE_PREFIX)).is_file()
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("image/"), path
        assert sha256(response.content).hexdigest() == hashes[path], path


@pytest.mark.parametrize("suffix,mime", [(".jpg", "image/jpeg"), (".png", "image/png"), (".webp", "image/webp")])
def test_images_serve_real_bytes_with_correct_type_and_published_hash(client, source_products, suffix, mime):
    metadata = next(meta for row in source_products for meta in row["image_metadata"] if Path(meta["path"]).suffix == suffix)
    response = client.get(IMAGE_PREFIX + Path(metadata["path"]).name)
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] == mime
    assert sha256(response.content).hexdigest() == metadata["sha256"]
    assert response.content == (DATA_DIR / metadata["path"]).read_bytes()


@pytest.mark.parametrize("filename", [
    "hn-999999999999999-01.jpg", "products.json", "..%2Fproducts.json", "%2E%2E%2Fproducts.json",
    "%252E%252E%252Fproducts.json", "..%5Cproducts.json", "%2Fetc%2Fpasswd", "C:%5CWindows%5Cwin.ini",
])
def test_image_path_traversal_and_non_catalog_files_are_rejected(client, filename):
    response = client.get(IMAGE_PREFIX + filename)
    assert response.status_code in (404, 422), (filename, response.status_code)
    assert response.headers.get("content-type", "").startswith("application/json")


def test_image_files_not_listed_in_catalog_are_not_public(tmp_path, source_products):
    directory = tmp_path / "catalog"
    directory.mkdir()
    (directory / "images").mkdir()
    selected = source_products[0]
    (directory / "products.json").write_text(json.dumps([selected], ensure_ascii=False), encoding="utf-8")
    for image_path in selected["images"]:
        shutil.copyfile(DATA_DIR / image_path, directory / image_path)
    # A file that matches the shape of a legitimate image still needs to be in the catalog whitelist.
    (directory / "images" / "hn-999999999999999-01.jpg").write_bytes(b"private file")
    with make_catalog_client(make_settings(tmp_path, directory)) as http:
        response = http.get(IMAGE_PREFIX + "hn-999999999999999-01.jpg")
        assert response.status_code in (404, 422)
        assert response.content != b"private file"


@pytest.mark.parametrize("path", ["/api/v1/products", "/api/v1/products/categories", "/api/v1/products/hn-4253818"])
def test_missing_catalog_is_unavailable_instead_of_successful_empty_data(tmp_path, path):
    with make_catalog_client(make_settings(tmp_path, tmp_path / "does-not-exist")) as http:
        response = http.get(path)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "product_catalog_unavailable"
        assert "products" not in response.json()


@pytest.mark.parametrize("payload", ["not json", "{}", "[]"])
def test_malformed_or_empty_catalog_is_unavailable(tmp_path, payload):
    directory = tmp_path / "broken"
    directory.mkdir()
    (directory / "products.json").write_text(payload, encoding="utf-8")
    with make_catalog_client(make_settings(tmp_path, directory)) as http:
        response = http.get("/api/v1/products")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "product_catalog_unavailable"


def test_create_app_registers_shop_routes_without_waiting_for_model_or_chat_initialization(tmp_path):
    def unavailable_service(settings):
        raise AssertionError("A catalog GET must not initialize the recognition service")

    app = create_app(make_settings(tmp_path), service_factory=unavailable_service)
    # Deliberately avoid entering TestClient's lifespan: catalog access is independent of ML startup.
    client = TestClient(app)
    try:
        data = get_products(client, limit=200)
        assert data["total"] == 200
    finally:
        client.close()


def test_create_app_shop_and_existing_routes_work_together(tmp_path):
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings, StubRecognizer)) as client:
        assert get_products(client)["total"] == 200
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/classes").status_code == 200
        schema = client.get("/openapi.json").json()
        assert {
            "/api/v1/products", "/api/v1/products/categories", "/api/v1/products/{product_id}",
            "/api/v1/products/images/{filename}", "/api/v1/recognize",
        }.issubset(schema["paths"])
