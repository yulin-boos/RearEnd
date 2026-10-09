"""MySQL checks require a separately provisioned disposable test database."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from Chat.mysql_store import MySQLChatStore
from Common.config import PROJECT_ROOT, Settings
from Common.database import Database, json_value, utc_datetime
from Common.errors import ChatError, DatabaseError, UserError
from Knowledge.mysql_store import MySQLKnowledgeStore
from Knowledge.schemas import KnowledgeDraft
from Users.mysql_store import MySQLUserStore
from Users.schemas import RegisterRequest
from Users.security import token_hash, verify_password
from API.main import create_app
from tests.test_api import image_bytes
from tests.test_chat import completion, make_app, recognize
from tests.test_knowledge import draft_data, handlers
from tests.test_products import SOURCE_FIELDS

pytestmark = pytest.mark.mysql
TEST_DATABASE = os.getenv("MYSQL_TEST_DB", "")
PASSWORD = "mysql-integration-2026"


@pytest.fixture(scope="module")
def settings(tmp_path_factory):
    if not TEST_DATABASE.startswith("plant_health_test_"):
        pytest.skip("MYSQL_TEST_DB must name a disposable plant_health_test_* database")
    directory = tmp_path_factory.mktemp("mysql")
    return Settings.from_env().model_copy(update={
        "db_name": TEST_DATABASE, "result_dir": directory / "results",
        "deepseek_api_key": Settings(deepseek_api_key="test-key-not-real").deepseek_api_key,
        "typesafe_api_key": Settings(typesafe_api_key="typesafe-test-not-real").typesafe_api_key,
    })


@pytest.fixture
def database(settings):
    instance = Database(settings)
    instance.verify_schema()
    try:
        yield instance
    finally:
        instance.close()


def register(client):
    response = client.post("/api/v1/auth/register", json={"username": "test_" + uuid4().hex[:20], "password": PASSWORD})
    assert response.status_code == 201, response.text
    return response.json()


def bearer(auth):
    return {"Authorization": "Bearer " + auth["access_token"]}


def test_accounts_hashes_profile_logout_and_restart(settings, database):
    with TestClient(make_app(settings, lambda request: httpx.Response(200, json=completion()))) as client:
        auth = register(client)
        username = auth["user"]["username"]
        assert client.get("/health").json()["storage"] == {"backend": "mysql", "connected": True}
        assert client.patch("/api/v1/users/me", headers=bearer(auth), json={"nickname": "数据库用户"}).status_code == 200
        duplicate = client.post("/api/v1/auth/register", json={"username": username.upper(), "password": PASSWORD})
        assert duplicate.status_code == 409
        with database.cursor() as cursor:
            cursor.execute("SELECT password_hash FROM users WHERE id=%s", (auth["user"]["id"],))
            encoded = cursor.fetchone()["password_hash"]
            assert encoded != PASSWORD and verify_password(PASSWORD, encoded)
            cursor.execute("SELECT token_hash FROM auth_sessions WHERE user_id=%s", (auth["user"]["id"],))
            assert cursor.fetchone()["token_hash"] == token_hash(auth["access_token"])
    with TestClient(make_app(settings, lambda request: httpx.Response(200, json=completion()))) as client:
        assert client.get("/api/v1/users/me", headers=bearer(auth)).json()["nickname"] == "数据库用户"
        login = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD}).json()
        assert client.post("/api/v1/auth/logout", headers=bearer(auth)).status_code == 204
        assert client.get("/api/v1/users/me", headers=bearer(auth)).status_code == 401
        assert client.get("/api/v1/users/me", headers=bearer(login)).status_code == 200


def test_recognition_candidates_context_and_ownership(settings, database):
    with TestClient(make_app(settings, lambda request: httpx.Response(200, json=completion()))) as client:
        owner, other = register(client), register(client)
        response = client.post("/api/v1/recognize", headers=bearer(owner), files={"file": ("leaf.png", image_bytes())},
                               data={"top_k": 1, "auto_analyze": "false"})
        assert response.status_code == 200, response.text
        data = response.json()
        identifier = data["request_id"]
        assert len(data["predictions"]) == 1
        assert client.get(data["result_image_url"], headers=bearer(owner)).status_code == 200
        for headers in ({}, bearer(other)):
            assert client.get(data["result_image_url"], headers=headers).status_code == 404
        with database.cursor() as cursor:
            cursor.execute("SELECT * FROM recognitions WHERE id=%s", (identifier,))
            record = cursor.fetchone()
            assert record["user_id"] == owner["user"]["id"] and record["requested_top_k"] == 1
            assert record["image_width"] == 64 and json_value(record["leaf_check"])["is_leaf"] is True
            cursor.execute("SELECT COUNT(*) AS total FROM recognition_predictions WHERE recognition_id=%s", (identifier,))
            assert cursor.fetchone()["total"] == 5
            cursor.execute("SELECT recognition_id,user_id FROM conversations WHERE id=%s", (identifier,))
            assert cursor.fetchone() == {"recognition_id": identifier, "user_id": owner["user"]["id"]}
        followup = client.post("/api/v1/chat", headers=bearer(owner), json={"recognition_id": identifier, "message": "怎么观察叶片？"})
        assert followup.status_code == 200, followup.text
        with database.cursor() as cursor:
            cursor.execute("SELECT * FROM chat_messages WHERE conversation_id=%s ORDER BY id", (identifier,))
            messages = cursor.fetchall()
            assert [row["role"] for row in messages] == ["user", "assistant"]
            assert json_value(messages[1]["guard_checks"])["question"]["model"]
            assert json_value(messages[1]["usage_json"])["total_tokens"] == 120


def test_general_chat_history_survives_restart(settings, database):
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=completion())
    with TestClient(make_app(settings, handler)) as client:
        identifier = client.post("/api/v1/chat/sessions").json()["session_id"]
        first = client.post("/api/v1/chat", json={"recognition_id": identifier, "message": "植物叶片发黄怎么办？"})
        assert first.status_code == 200, first.text
    with TestClient(make_app(settings, handler)) as client:
        second = client.post("/api/v1/chat", json={"recognition_id": identifier, "message": "浇水后盆土很湿"})
        assert second.status_code == 200 and second.json()["history_length"] == 4
        assert captured[-1]["messages"][1]["content"] == "植物叶片发黄怎么办？"
        with database.cursor() as cursor:
            cursor.execute("SELECT recognition_id,kind FROM conversations WHERE id=%s", (identifier,))
            assert cursor.fetchone() == {"recognition_id": None, "kind": "general"}


def test_concurrent_revision_writes_one_complete_turn(settings, database):
    store = MySQLChatStore(database, settings)
    identifier = uuid4().hex
    store.create(identifier, {"conversation_type": "general"})
    def attempt(index):
        try:
            return store.append_turn(identifier, 0, "问题" + str(index), "回答" + str(index))
        except ChatError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert 2 in results and "conversation_changed" in results
    assert len(store.get(identifier)["history"]) == 2


def test_concurrent_registration_and_knowledge_dedup(settings, database):
    users = MySQLUserStore(database, settings.auth_session_ttl_seconds)
    body = RegisterRequest(username="test_" + uuid4().hex[:20], password=PASSWORD)
    def register_once(_):
        try:
            users.register(body)
            return "created"
        except UserError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(register_once, range(2))) == ["created", "username_taken"]
    draft = KnowledgeDraft(**{**draft_data(), "question": "唯一测试问句" + uuid4().hex})
    store = MySQLKnowledgeStore(database)
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = list(pool.map(lambda _: store.save(draft, uuid4().hex, "test", "test"), range(2)))
    assert saved[0][0] == saved[1][0] and sorted(created for _, created in saved) == [False, True]
    assert store.get(saved[0][0]).answer == draft.answer


def test_knowledge_capture_links_reuse_and_expiry(settings, database):
    card = {**draft_data(), "question": "集成知识问句" + uuid4().hex}
    deepseek, jev, _ = handlers(card=card)
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        response = recognize(client)
        assert response.status_code == 200, response.text
        data = response.json()
        activity = data["chat"]["knowledge"]
        assert activity["status"] == "stored", activity
        entry_id = activity["entry_id"]
        assert client.get("/api/v1/knowledge", params={"query": "苹果通风"}).status_code == 200
        with database.cursor() as cursor:
            cursor.execute("SELECT relation_type FROM message_knowledge_links WHERE knowledge_id=%s", (entry_id,))
            assert "source" in [row["relation_type"] for row in cursor.fetchall()]
            cursor.execute("UPDATE conversations SET expires_at=%s WHERE id=%s", (utc_datetime(time.time() - 1), data["request_id"]))
        client.app.state.chat.store.cleanup()
        assert client.get("/api/v1/knowledge/" + entry_id).status_code == 200
        with database.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM recognitions WHERE id=%s", (data["request_id"],))
            assert cursor.fetchone()["total"] == 0
            cursor.execute("SELECT COUNT(*) AS total FROM chat_messages WHERE conversation_id=%s", (data["request_id"],))
            assert cursor.fetchone()["total"] == 0
    deepseek, jev, _ = handlers(card=card, direct=0.99)
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        reused = recognize(client).json()
        assert reused["chat"]["source"] == "knowledge"
        with database.cursor() as cursor:
            cursor.execute("""SELECT relation_type FROM message_knowledge_links l JOIN chat_messages m ON m.id=l.message_id
                WHERE m.conversation_id=%s""", (reused["request_id"],))
            assert "direct" in [row["relation_type"] for row in cursor.fetchall()]


def test_mysql_catalog_preserves_all_source_fields_and_images(settings):
    source = json.loads((PROJECT_ROOT / "Shop/data/products.json").read_text(encoding="utf-8"))
    with TestClient(make_app(settings, lambda request: httpx.Response(200, json=completion()))) as client:
        response = client.get("/api/v1/products", params={"limit": 200})
        assert response.status_code == 200, response.text
        actual = response.json()
        assert actual["total"] == 200
        for row, original in zip(actual["products"], source):
            for field in SOURCE_FIELDS:
                assert row[field] == original[field], (row["id"], field)
            assert row["image_metadata"] == original["image_metadata"]
        assert sum(row["count"] for row in client.get("/api/v1/products/categories").json()) == 200
        assert client.get("/api/v1/products", params={"q": "滴灌", "category": "irrigation"}).json()["total"] > 0
        assert client.get("/api/v1/products", params={"q": "虫"}).json()["total"] > 0
        for row in actual["products"]:
            for url in row["images"]:
                assert client.get(url).status_code == 200


def test_catalog_updates_use_mysql_and_outages_are_sanitized(settings, database, monkeypatch):
    with TestClient(make_app(settings, lambda request: httpx.Response(200, json=completion()))) as client:
        catalog = client.app.state.product_catalog
        original = client.get("/api/v1/products", params={"limit": 1}).json()["products"][0]
        categories = client.get("/api/v1/products/categories").json()
        category = next(item for item in categories if item["id"] == original["category"])
        with database.cursor() as cursor:
            cursor.execute("UPDATE products SET name=%s WHERE id=%s", ("数据库即时更新测试", original["id"]))
            cursor.execute("UPDATE product_categories SET name=%s WHERE code=%s", ("数据库分类名称", category["id"]))
        catalog.cache_seconds = 0
        try:
            assert client.get("/api/v1/products/" + original["id"]).json()["name"] == "数据库即时更新测试"
            changed = client.get("/api/v1/products/categories").json()
            assert next(item for item in changed if item["id"] == category["id"])["name"] == "数据库分类名称"
        finally:
            with database.cursor() as cursor:
                cursor.execute("UPDATE products SET name=%s WHERE id=%s", (original["name"], original["id"]))
                cursor.execute("UPDATE product_categories SET name=%s WHERE code=%s", (category["name"], category["id"]))
        @contextmanager
        def unavailable():
            raise DatabaseError()
            yield
        monkeypatch.setattr(catalog.database, "cursor", unavailable)
        failed = client.get("/api/v1/products")
        assert failed.status_code == 503 and failed.json()["error"]["code"] == "database_unavailable"
        assert settings.db_password.get_secret_value() not in failed.text


def test_optional_activity_failure_preserves_committed_reply(settings, database, monkeypatch):
    with TestClient(make_app(settings, lambda request: httpx.Response(200, json=completion()))) as client:
        identifier = client.post("/api/v1/chat/sessions").json()["session_id"]
        def fail_metadata(*args):
            raise DatabaseError()
        monkeypatch.setattr(client.app.state.chat.store, "finish_turn", fail_metadata)
        reply = client.post("/api/v1/chat", json={"recognition_id": identifier, "message": "叶片为什么发黄？"})
        assert reply.status_code == 200, reply.text
        assert reply.json()["knowledge"]["error_code"] == "chat_metadata_write_failed"
        with database.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM chat_messages WHERE conversation_id=%s", (identifier,))
            assert cursor.fetchone()["total"] == 2


def test_closed_idle_connection_is_replaced(database):
    # Reproduce a server/network disconnect between two store operations.
    connection = database._idle.get_nowait()
    connection.close()
    database._idle.put(connection)
    database.check()
    database.check()


def test_guidance_state_messages_and_restart_are_atomic(settings, database):
    from tests.test_guidance import draft, guided_completion, question
    def handler(request):
        payload = json.loads(request.content)
        source = json.loads(payload["messages"][0]["content"].split("本轮引导数据 JSON：\n")[1])
        questions = [question("onset")] if source["answered_information"] else None
        return httpx.Response(200, json=guided_completion(draft(questions)))
    with TestClient(make_app(settings, handler)) as client:
        owner = register(client)
        response = client.post("/api/v1/recognize", headers=bearer(owner), files={"file": ("leaf.png", image_bytes())},
                               data={"auto_analyze": "false", "confidence": 0.9, "save_result": "false"})
        assert response.status_code == 200, response.text
        identifier = response.json()["request_id"]
        first = client.post("/api/v1/chat/guidance", headers=bearer(owner), json={"recognition_id": identifier})
        assert first.status_code == 200, first.text
        second = client.post("/api/v1/chat/guidance", headers=bearer(owner), json={"recognition_id": identifier,
            "revision": 1, "answers": {"crop": "苹果", "symptoms": "褐色斑点"}})
        assert second.status_code == 200, second.text
        result = second.json()
        with database.cursor() as cursor:
            cursor.execute("SELECT context_snapshot,revision FROM conversations WHERE id=%s", (identifier,))
            row = cursor.fetchone()
            assert row["revision"] == 2 and json_value(row["context_snapshot"])["guidance"] == result
            cursor.execute("SELECT role,guard_checks,knowledge_activity FROM chat_messages WHERE conversation_id=%s ORDER BY id", (identifier,))
            rows = cursor.fetchall()
            assert [row["role"] for row in rows] == ["user", "assistant", "user", "assistant"]
            assert json_value(rows[-1]["guard_checks"])["reply"]["model"]
            assert json_value(rows[-1]["knowledge_activity"])["status"] == "skipped"
        # A stale writer must roll back its message pair and new context together.
        with pytest.raises(ChatError):
            client.app.state.chat.store.append_turn(identifier, 0, "过期补充", "过期引导", owner["user"]["id"],
                                                     {"guidance_state": {"incorrect": True}})
        assert client.get("/api/v1/chat/guidance/" + identifier, headers=bearer(owner)).json() == result
        assert client.get("/api/v1/chat/guidance/" + identifier).status_code == 404
    with TestClient(make_app(settings, handler)) as client:
        assert client.get("/api/v1/chat/guidance/" + identifier, headers=bearer(owner)).json() == result
        conflict = client.post("/api/v1/chat/guidance", headers=bearer(owner), json={"recognition_id": identifier,
            "revision": 1, "answers": {"onset": "三天"}})
        assert conflict.status_code == 409
        with database.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM chat_messages WHERE conversation_id=%s", (identifier,))
            assert cursor.fetchone()["total"] == 4


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("YOLO_TEST_REAL_MODEL") != "1", reason="Set YOLO_TEST_REAL_MODEL=1 for local weights")
def test_real_models_save_mysql_recognition(settings, database):
    real_settings = settings.model_copy(update={"deepseek_api_key": None, "typesafe_api_key": None})
    fixtures = PROJECT_ROOT / "tests/fixtures/leaf_gate"
    with TestClient(create_app(real_settings)) as client:
        health = client.get("/health").json()
        assert health["storage"] == {"backend": "mysql", "connected": True}
        assert health["class_count"] == 38 and health["leaf_check"]["enabled"]
        leaf = fixtures / "leaf_Apple___healthy_0.jpg"
        response = client.post("/api/v1/recognize", files={"file": (leaf.name, leaf.read_bytes())},
                               data={"top_k": 5, "auto_analyze": "false"})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["leaf_check"]["is_leaf"] and len(result["predictions"]) == 5
        assert client.get(result["result_image_url"]).status_code == 200
        with database.cursor() as cursor:
            cursor.execute("SELECT model_name,leaf_check FROM recognitions WHERE id=%s", (result["request_id"],))
            record = cursor.fetchone()
            assert record["model_name"] == "best.pt" and json_value(record["leaf_check"])["is_leaf"]
            cursor.execute("SELECT COUNT(*) AS total FROM recognition_predictions WHERE recognition_id=%s", (result["request_id"],))
            assert cursor.fetchone()["total"] == 5
        for sample in json.loads((fixtures / "sources.json").read_text(encoding="utf-8")):
            if not sample["expected_leaf"]:
                rejected = client.post("/api/v1/recognize", files={"file": (sample["name"], (fixtures / sample["name"]).read_bytes())},
                                       data={"auto_analyze": "false", "save_result": "false"})
                assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "not_leaf"
