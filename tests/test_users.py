from concurrent.futures import ThreadPoolExecutor
import asyncio
import json
import sqlite3
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from Chat.store import ChatStore
from Chat.service import ChatService
from Chat.deepseek import DeepSeekClient
from Jev.guard import JevGuard
from Common.errors import ChatError, UserError
from Users.schemas import RegisterRequest
from Users.security import token_hash, verify_password
from Users.store import UserStore
from tests.test_api import image_bytes
from tests.test_chat import completion, jev_result, make_app, settings

PASSWORD = "plants-grow-2026"


@pytest.fixture
def client(settings):
    with TestClient(make_app(settings, lambda request: httpx.Response(200, json=completion()))) as http:
        yield http


def register(client, username="gardener", **fields):
    return client.post("/api/v1/auth/register", json={"username": username, "password": PASSWORD, **fields})


def bearer(auth):
    return {"Authorization": "Bearer " + auth["access_token"]}


def test_registration_login_and_database_hashes(client, settings):
    response = register(client, " Gardener ", nickname=" 种植用户 ")
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    auth = response.json()
    assert auth["user"]["username"] == "gardener"
    assert auth["user"]["nickname"] == "种植用户"
    assert auth["expires_at"] > time.time()
    assert "password" not in json.dumps(auth)
    assert client.get("/api/v1/users/me", headers=bearer(auth)).json() == auth["user"]
    with sqlite3.connect(settings.chat_db_path) as connection:
        encoded = connection.execute("SELECT password_hash FROM users").fetchone()[0]
        stored_token = connection.execute("SELECT token_hash FROM auth_sessions").fetchone()[0]
    assert encoded != PASSWORD and verify_password(PASSWORD, encoded)
    assert stored_token != auth["access_token"] and stored_token == token_hash(auth["access_token"])
    login = client.post("/api/v1/auth/login", json={"username": "GARDENER", "password": PASSWORD})
    assert login.status_code == 200
    assert login.json()["access_token"] != auth["access_token"]
    assert login.json()["user"]["id"] == auth["user"]["id"]


def test_duplicate_accounts_and_salted_passwords(client, settings):
    assert register(client).status_code == 201
    duplicate = register(client, "GARDENER")
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "username_taken"
    assert register(client, "other_user").status_code == 201
    with sqlite3.connect(settings.chat_db_path) as connection:
        hashes = [row[0] for row in connection.execute("SELECT password_hash FROM users")]
        assert connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 2
    assert hashes[0] != hashes[1]


def test_unknown_account_and_wrong_password_have_same_error(client):
    register(client)
    wrong = client.post("/api/v1/auth/login", json={"username": "gardener", "password": "wrong-password"})
    unknown = client.post("/api/v1/auth/login", json={"username": "unknown", "password": "wrong-password"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("authorization", [None, "Basic invalid", "Bearer", "Bearer invalid", "Bearer " + "x" * 200])
def test_profile_requires_a_valid_login(client, authorization):
    headers = {"Authorization": authorization} if authorization is not None else {}
    response = client.get("/api/v1/users/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_partial_profile_changes_are_scoped_and_persist(client):
    first = register(client).json()
    second = register(client, "other_user").json()
    response = client.patch("/api/v1/users/me", headers=bearer(first),
                            json={"nickname": " 我的菜园 ", "phone": "13800138000", "bio": " 学习种植 "})
    assert response.status_code == 200
    profile = response.json()
    assert (profile["nickname"], profile["phone"], profile["bio"]) == ("我的菜园", "13800138000", "学习种植")
    assert profile["id"] == first["user"]["id"]
    assert client.get("/api/v1/users/me", headers=bearer(second)).json() == second["user"]
    cleared = client.patch("/api/v1/users/me", headers=bearer(first), json={"phone": ""}).json()
    assert cleared["phone"] == "" and cleared["nickname"] == "我的菜园" and cleared["bio"] == "学习种植"
    assert client.get("/api/v1/users/me", headers=bearer(first)).json() == cleared


@pytest.mark.parametrize("body", [{}, {"nickname": " "}, {"phone": "not a phone"}, {"nickname": None},
                                  {"bio": "x" * 81}, {"id": "another-user"}, {"password": "replacement"}])
def test_profile_rejects_invalid_fields_and_account_changes(client, body):
    auth = register(client).json()
    assert client.patch("/api/v1/users/me", headers=bearer(auth), json=body).status_code == 422
    assert client.get("/api/v1/users/me", headers=bearer(auth)).json() == auth["user"]


@pytest.mark.parametrize("body", [{"username": "ab", "password": PASSWORD},
                                  {"username": "user'name", "password": PASSWORD},
                                  {"username": "gardener", "password": "short"},
                                  {"username": "gardener", "password": " " * 8},
                                  {"username": "gardener", "password": "x" * 129},
                                  {"username": "gardener", "password": PASSWORD, "role": "admin"}])
def test_registration_validates_input_without_echoing_passwords(client, body):
    response = client.post("/api/v1/auth/register", json=body)
    assert response.status_code == 422
    assert "input" not in response.json()["error"]["details"][0]


def test_password_unicode_and_spaces_are_preserved(client):
    password = "  叶片健康🌱2026  "
    auth = register(client, password=password).json()
    login = client.post("/api/v1/auth/login", json={"username": "gardener", "password": password})
    assert login.status_code == 200 and login.json()["user"]["id"] == auth["user"]["id"]
    assert client.post("/api/v1/auth/login", json={"username": "gardener", "password": password.strip()}).status_code == 401


def test_logout_revokes_only_current_session(client):
    first = register(client).json()
    second = client.post("/api/v1/auth/login", json={"username": "gardener", "password": PASSWORD}).json()
    assert client.post("/api/v1/auth/logout", headers=bearer(first)).status_code == 204
    assert client.get("/api/v1/users/me", headers=bearer(first)).status_code == 401
    assert client.get("/api/v1/users/me", headers=bearer(second)).status_code == 200


def test_expired_tokens_cannot_read_or_update_profiles(client, settings):
    auth = register(client).json()
    with sqlite3.connect(settings.chat_db_path) as connection:
        connection.execute("UPDATE auth_sessions SET expires_at = ?", (time.time() - 1,))
    assert client.get("/api/v1/users/me", headers=bearer(auth)).status_code == 401
    assert client.patch("/api/v1/users/me", headers=bearer(auth), json={"nickname": "expired"}).status_code == 401


def test_users_and_login_sessions_survive_app_restart(settings):
    def no_provider(request):
        raise AssertionError("Account operations must not use AI providers")

    with TestClient(make_app(settings, no_provider, jev_handler=no_provider)) as client:
        auth = register(client).json()
        profile = client.patch("/api/v1/users/me", headers=bearer(auth), json={"nickname": "重启测试"}).json()
    with TestClient(make_app(settings, no_provider, jev_handler=no_provider)) as client:
        assert client.get("/api/v1/users/me", headers=bearer(auth)).json() == profile
        assert client.post("/api/v1/auth/login", json={"username": "gardener", "password": PASSWORD}).status_code == 200


def test_owned_chat_rejects_other_users_and_anonymous_requests(client, settings):
    owner = register(client).json()
    other = register(client, "other_user").json()
    identifier = client.post("/api/v1/chat/sessions", headers=bearer(owner)).json()["session_id"]
    body = {"recognition_id": identifier, "message": "植物叶片发黄怎么办？"}
    for headers in [{}, bearer(other)]:
        assert client.post("/api/v1/chat", headers=headers, json=body).status_code == 404
    assert client.post("/api/v1/chat", headers=bearer(owner), json=body).status_code == 200
    with sqlite3.connect(settings.chat_db_path) as connection:
        assert connection.execute("SELECT user_id FROM conversations WHERE recognition_id = ?", (identifier,)).fetchone()[0] == owner["user"]["id"]
    client.post("/api/v1/auth/logout", headers=bearer(owner))
    assert client.post("/api/v1/chat", headers=bearer(owner), json=body).status_code == 401
    renewed = client.post("/api/v1/auth/login", json={"username": "gardener", "password": PASSWORD}).json()
    assert client.post("/api/v1/chat", headers=bearer(renewed), json=body).status_code == 200


def test_recognition_auto_analysis_and_result_images_use_the_owner(client):
    owner = register(client).json()
    other = register(client, "other_user").json()
    response = client.post("/api/v1/recognize", headers=bearer(owner), files={"file": ("leaf.png", image_bytes())})
    assert response.status_code == 200 and response.json()["chat"]["status"] == "ready"
    result = response.json()
    assert client.get(result["result_image_url"], headers=bearer(owner)).status_code == 200
    for headers in [{}, bearer(other)]:
        assert client.get(result["result_image_url"], headers=headers).status_code == 404
        assert client.post("/api/v1/chat", headers=headers,
                           json={"recognition_id": result["request_id"], "message": "如何养护？"}).status_code == 404


def test_invalid_authorization_cannot_create_anonymous_chat(client, settings):
    response = client.post("/api/v1/chat/sessions", headers={"Authorization": "Basic invalid"})
    assert response.status_code == 401
    with sqlite3.connect(settings.chat_db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0


def test_busy_private_chat_still_checks_ownership(settings):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json=completion())

        guard = JevGuard(settings, httpx.MockTransport(lambda request: httpx.Response(200, json=jev_result())))
        service = ChatService(settings, DeepSeekClient(settings, httpx.MockTransport(handler)), guard)
        await service.initialize()
        identifier = service.create_general("owner-id")
        task = asyncio.create_task(service.reply(identifier, "植物叶片发黄怎么办？", "owner-id"))
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            for user_id in [None, "other-id"]:
                with pytest.raises(ChatError) as error:
                    await service.reply(identifier, "如何养护？", user_id)
                assert error.value.status_code == 404
            with pytest.raises(ChatError) as error:
                await service.reply(identifier, "重复发送", "owner-id")
            assert error.value.status_code == 409
        finally:
            release.set()
            await task
            await service.close()

    asyncio.run(scenario())


def test_profile_patch_cors_preflight_allows_authorization(client):
    response = client.options("/api/v1/users/me", headers={"Origin": "null",
        "Access-Control-Request-Method": "PATCH", "Access-Control-Request-Headers": "authorization,content-type"})
    assert response.status_code == 200
    assert "PATCH" in response.headers["access-control-allow-methods"]


def test_migration_preserves_old_conversations_and_is_repeatable(settings):
    old_history = json.dumps([{"role": "user", "content": "旧聊天"}])
    with sqlite3.connect(settings.chat_db_path) as connection:
        connection.execute("""CREATE TABLE conversations (
            recognition_id TEXT PRIMARY KEY, context TEXT NOT NULL, history TEXT NOT NULL,
            revision INTEGER NOT NULL, expires_at REAL NOT NULL)""")
        connection.execute("INSERT INTO conversations VALUES (?, ?, ?, ?, ?)",
                           ("a" * 32, '{}', old_history, 2, time.time() - 1))
    store = ChatStore(settings.chat_db_path, 86400, 10)
    store.migrate()
    store.migrate()
    with sqlite3.connect(settings.chat_db_path) as connection:
        assert connection.execute("SELECT history, revision, user_id FROM conversations").fetchone() == (old_history, 2, None)


def test_concurrent_duplicate_registration_creates_one_user_and_session(settings):
    store = UserStore(settings.chat_db_path, 604800)
    store.initialize()
    body = RegisterRequest(username="gardener", password=PASSWORD)

    def attempt():
        try:
            store.register(body)
            return "registered"
        except UserError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ["registered", "username_taken"]
    with sqlite3.connect(settings.chat_db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 1
