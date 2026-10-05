import asyncio
import json
import sqlite3
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from Chat.store import ChatError, ChatStore
from Chat.service import ChatService
from Common.config import Settings
from Chat.deepseek import DeepSeekClient
from Jev.guard import JevGuard
from Knowledge.schemas import FOCUS_INSTRUCTIONS
from API.main import create_app
from Common.schemas import Prediction
from tests.test_api import StubRecognizer, image_bytes


class CandidateRecognizer(StubRecognizer):
    def predict(self, image, top_k):
        names = ["Apple___Apple_scab", "Apple___Black_rot", "Apple___Cedar_apple_rust",
                 "Apple___healthy", "Potato___Early_blight", "Tomato___Late_blight"]
        scores = [0.5, 0.2, 0.12, 0.1, 0.06, 0.02]
        from Visual.recognition.labels import LabelCatalog
        catalog = LabelCatalog()
        return [Prediction(**catalog.describe(i, name).model_dump(), confidence=score)
                for i, (name, score) in enumerate(zip(names, scores))][:top_k], 1.0


def completion(reply="候选结果有不确定性，请补充叶片症状。", finish_reason="stop"):
    return {"choices": [{"message": {"role": "assistant", "content": reply}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}


def jev_result(relevance=0.99, off_topic=0.01, override=0.01):
    return {"model": "jev-1.13.0", "answers": {
        "plant_health_relevant": {"type": "noul", "noul": relevance},
        "off_topic_task": {"type": "noul", "noul": off_topic},
        "instruction_override": {"type": "noul", "noul": override},
        "knowledge_value": {"type": "noul", "noul": 0.01},
        "knowledge_grounded": {"type": "noul", "noul": 0.99},
        "knowledge_private": {"type": "noul", "noul": 0.01},
        "knowledge_focus": {"type": "choice", "choice": "none", "confidence": 0.99,
                            "probabilities": {name: float(name == "none") for name in FOCUS_INSTRUCTIONS}},
    }, "usage": {"input_tokens": 100, "output_tokens": 20}}


@pytest.fixture
def settings(tmp_path):
    return Settings(result_dir=tmp_path / "results", chat_db_path=tmp_path / "chat.sqlite3",
                    knowledge_db_path=tmp_path / "knowledge.sqlite3",
                    deepseek_api_key="test-key-not-real", typesafe_api_key="typesafe-test-not-real", cors_origins=["null"])


def make_app(settings, handler, recognizer=CandidateRecognizer, jev_handler=None):
    transport = httpx.MockTransport(handler)
    jev_transport = httpx.MockTransport(jev_handler or (lambda request: httpx.Response(200, json=jev_result())))
    return create_app(settings, recognizer, lambda config: DeepSeekClient(config, transport),
                      lambda config: JevGuard(config, jev_transport))


def recognize(client, **parameters):
    return client.post("/api/v1/recognize", headers={"Origin": "null"},
                       files={"file": ("leaf.png", image_bytes())},
                       data={"top_k": 1, "save_result": "false", **parameters})


def test_auto_analysis_includes_candidates_and_followups(settings):
    captured = []
    def handler(request):
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key-not-real"
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=completion())

    with TestClient(make_app(settings, handler)) as client:
        response = recognize(client, confidence=0.9)
        assert response.status_code == 200
        data = response.json()
        assert len(data["predictions"]) == 1
        assert data["chat"]["status"] == "ready" and data["chat"]["reply"]
        assert captured[0]["model"] == "deepseek-flash"
        assert captured[0]["thinking"] == {"type": "disabled"}
        system = captured[0]["messages"][0]["content"]
        context = json.loads(system.split("本次识别结果 JSON：\n", 1)[1])
        assert len(context["candidates"]) == 5
        assert context["candidates"][0]["confidence"] == 0.5
        assert context["candidates"][0]["condition"] == "黑星病"
        assert context["is_confident"] is False and context["confidence_threshold"] == 0.9
        assert context["leaf_check"]["passed"] is True
        assert "确诊概率" in system
        response = client.post("/api/v1/chat", headers={"Origin": "null"},
                               json={"recognition_id": data["request_id"], "message": "叶片斑点正在扩散，怎么办？"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "null"
        assert response.json()["history_length"] == 4
        messages = captured[1]["messages"]
        assert messages[0]["content"] == system
        assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
        assert messages[-1]["content"] == "叶片斑点正在扩散，怎么办？"


def test_no_key_preserves_recognition_and_stores_context(settings):
    settings = settings.model_copy(update={"deepseek_api_key": None})
    def handler(request):
        raise AssertionError("No API request without a key")
    with TestClient(make_app(settings, handler)) as client:
        response = recognize(client)
        assert response.status_code == 200
        data = response.json()
        assert data["chat"]["status"] == "not_configured"
        assert data["chat"]["error"]["code"] == "deepseek_not_configured"
        assert client.get("/health").json()["deepseek"]["configured"] is False
        assert len(client.app.state.chat.store.get(data["request_id"])["context"]["candidates"]) == 5
        assert client.post("/api/v1/chat", json={"recognition_id": data["request_id"], "message": "病因是什么？"}).status_code == 503


def test_nonleaf_never_calls_deepseek_or_creates_conversation(settings):
    class RejectingRecognizer(CandidateRecognizer):
        def check_leaf(self, image):
            return super().check_leaf(image).model_copy(update={"is_leaf": False})
        def predict(self, image, top_k):
            raise AssertionError("Disease inference on rejected input")
    def handler(request):
        raise AssertionError("DeepSeek must not receive rejected input")
    with TestClient(make_app(settings, handler, RejectingRecognizer)) as client:
        assert recognize(client).json()["error"]["code"] == "not_leaf"
        with sqlite3.connect(settings.chat_db_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0


def test_api_failure_preserves_recognition_and_failed_turn_is_not_saved(settings):
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        if len(captured) == 1:
            return httpx.Response(503, json={"error": {"message": "test-key-not-real must never leak"}})
        return httpx.Response(200, json=completion())
    with TestClient(make_app(settings, handler)) as client:
        data = recognize(client).json()
        assert data["chat"]["status"] == "error"
        assert data["top_prediction"]["class_name"] == "Apple___Apple_scab"
        assert "test-key-not-real" not in json.dumps(data)
        assert client.app.state.chat.store.get(data["request_id"])["history"] == []
        response = client.post("/api/v1/chat", json={"recognition_id": data["request_id"]})
        assert response.status_code == 200
        assert len(captured[1]["messages"]) == 2


def test_skipped_auto_analysis_and_history_survive_restart(settings):
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=completion())
    with TestClient(make_app(settings, handler)) as client:
        data = recognize(client, auto_analyze="false").json()
        assert data["chat"]["status"] == "skipped" and captured == []
        assert client.post("/api/v1/chat", json={"recognition_id": data["request_id"], "message": "叶片发黄"}).status_code == 200
    with TestClient(make_app(settings, handler)) as client:
        response = client.post("/api/v1/chat", json={"recognition_id": data["request_id"], "message": "已经持续三天"})
        assert response.status_code == 200
        assert [message["content"] for message in captured[1]["messages"][1:]][0] == "叶片发黄"
        assert response.json()["history_length"] == 4


@pytest.mark.parametrize("extra", [{"history": []}, {"predictions": []}, {"role": "system"}, {"message": "   "}])
def test_client_cannot_replace_context_or_roles(settings, extra):
    def handler(request):
        raise AssertionError("Invalid requests must not reach the provider")
    with TestClient(make_app(settings, handler)) as client:
        assert client.post("/api/v1/chat", json={"recognition_id": "a" * 32, **extra}).status_code == 422


def test_expired_conversation_is_rejected_before_provider_request(settings):
    def handler(request):
        raise AssertionError("Expired sessions must not reach the provider")
    with TestClient(make_app(settings, handler)) as client:
        data = recognize(client, auto_analyze="false").json()
        with sqlite3.connect(settings.chat_db_path) as connection:
            connection.execute("UPDATE conversations SET expires_at = ?", (time.time() - 1,))
        response = client.post("/api/v1/chat", json={"recognition_id": data["request_id"], "message": "继续"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "conversation_not_found"


@pytest.mark.parametrize("upstream,expected", [(401, "deepseek_auth_failed"), (403, "deepseek_auth_failed"),
    (402, "deepseek_insufficient_balance"), (429, "deepseek_rate_limited"), (500, "deepseek_unavailable"), (400, "deepseek_unavailable")])
def test_provider_errors_do_not_expose_raw_response(settings, upstream, expected):
    async def scenario():
        def handler(request):
            return httpx.Response(upstream, json={"error": "test-key-not-real"})
        client = DeepSeekClient(settings, httpx.MockTransport(handler))
        try:
            with pytest.raises(ChatError) as error:
                await client.complete([{"role": "user", "content": "test"}])
            assert error.value.code == expected
            assert "test-key-not-real" not in str(error.value)
        finally:
            await client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("mode,code", [("timeout", "deepseek_timeout"), ("network", "deepseek_connection_failed"),
    ("invalid", "deepseek_invalid_response"), ("empty", "deepseek_invalid_response")])
def test_timeout_network_and_invalid_response(settings, mode, code):
    async def scenario():
        def handler(request):
            if mode == "timeout": raise httpx.ReadTimeout("private upstream error", request=request)
            if mode == "network": raise httpx.ConnectError("private upstream error", request=request)
            return httpx.Response(200, json={} if mode == "invalid" else completion(reply=""))
        client = DeepSeekClient(settings, httpx.MockTransport(handler))
        try:
            with pytest.raises(ChatError) as error:
                await client.complete([{"role": "user", "content": "test"}])
            assert error.value.code == code
            assert "private upstream error" not in str(error.value)
        finally: await client.close()
    asyncio.run(scenario())


def test_store_keeps_complete_turns_and_rejects_stale_revision(settings):
    store = ChatStore(settings.chat_db_path, 60, 2)
    store.initialize()
    identifier = "a" * 32
    store.create(identifier, {"candidates": []})
    for revision in range(3): store.append_turn(identifier, revision, f"question-{revision}", f"reply-{revision}")
    history = store.get(identifier)["history"]
    assert [message["content"] for message in history] == ["question-1", "reply-1", "question-2", "reply-2"]
    with pytest.raises(ChatError, match="已更新"):
        store.append_turn(identifier, 0, "stale", "must not be stored")
    assert store.get(identifier)["history"] == history


def test_secret_is_not_in_settings_repr_or_dump(settings):
    assert "test-key-not-real" not in repr(settings)
    assert "deepseek_api_key" not in settings.model_dump()
    assert "typesafe-test-not-real" not in repr(settings)
    assert "typesafe_api_key" not in settings.model_dump()


@pytest.mark.parametrize("payload", [[], {"choices": []}, {"choices": "invalid"},
    {"choices": [None]}, {"choices": [{"message": None}]}, {**completion(), "usage": ["invalid"]}])
def test_malformed_provider_response_preserves_recognition(settings, payload):
    with TestClient(make_app(settings, lambda request: httpx.Response(200, json=payload))) as client:
        response = recognize(client)
        assert response.status_code == 200
        data = response.json()
        assert data["top_prediction"]["confidence"] == 0.5
        assert data["chat"]["error"]["code"] == "deepseek_invalid_response"
        assert client.app.state.chat.store.get(data["request_id"])["history"] == []


def test_truncated_reply_is_available_for_followup(settings):
    with TestClient(make_app(settings, lambda request: httpx.Response(200, json=completion(finish_reason="length")))) as client:
        data = recognize(client).json()
        assert data["chat"]["status"] == "ready" and data["chat"]["truncated"] is True
        response = client.post("/api/v1/chat", json={"recognition_id": data["request_id"], "message": "请继续"})
        assert response.status_code == 200
        assert response.json()["truncated"] is True and response.json()["history_length"] == 4


def test_same_conversation_rejects_overlap_without_second_provider_call(settings):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        requests = []
        async def handler(request):
            requests.append(request)
            entered.set()
            await release.wait()
            return httpx.Response(200, json=completion())
        guard = JevGuard(settings, httpx.MockTransport(lambda request: httpx.Response(200, json=jev_result())))
        service = ChatService(settings, DeepSeekClient(settings, httpx.MockTransport(handler)), guard)
        await service.initialize()
        identifier = "a" * 32
        service.store.create(identifier, {"candidates": []})
        task = asyncio.create_task(service.reply(identifier, "第一次提问"))
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            with pytest.raises(ChatError) as error:
                await service.reply(identifier, "重叠提问")
            assert error.value.code == "chat_busy" and error.value.status_code == 409
            assert len(requests) == 1
            release.set()
            assert (await task).history_length == 2
            assert service.store.get(identifier)["history"][0]["content"] == "第一次提问"
        finally:
            release.set()
            await task
            await service.close()
    asyncio.run(scenario())
