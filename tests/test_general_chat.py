import json
import sqlite3
import time

import httpx
from fastapi.testclient import TestClient

from tests.test_chat import completion, make_app, settings


def test_creating_general_sessions_never_calls_providers_or_fakes_recognition(settings):
    def unexpected_request(request):
        raise AssertionError("Creating a session must not contact any provider")

    with TestClient(make_app(settings, unexpected_request, jev_handler=unexpected_request)) as client:
        first = client.post("/api/v1/chat/sessions").json()
        second = client.post("/api/v1/chat/sessions").json()
        assert first["kind"] == "general" and len(first["session_id"]) == 32
        assert first["session_id"] != second["session_id"]
        session = client.app.state.chat.store.get(first["session_id"])
        assert session["history"] == []
        assert session["context"]["image_available"] is False
        assert "top_prediction" not in session["context"]
        assert "is_confident" not in session["context"]


def test_general_chat_preserves_history_and_uses_a_text_consultation_prompt(settings):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=completion("请补充植物种类和叶片症状。"))

    with TestClient(make_app(settings, handler)) as client:
        identifier = client.post("/api/v1/chat/sessions").json()["session_id"]
        first = client.post("/api/v1/chat", json={"recognition_id": identifier, "message": "植物叶片发黄怎么办？"})
        second = client.post("/api/v1/chat", json={"recognition_id": identifier, "message": "浇水后盆土一直很湿。"})
        assert first.status_code == second.status_code == 200
        assert second.json()["history_length"] == 4
        system = calls[0]["messages"][0]["content"]
        assert "没有图片识别结果" in system
        assert "本次识别结果 JSON" not in system
        assert [message["role"] for message in calls[1]["messages"]] == ["system", "user", "assistant", "user"]
        assert calls[1]["messages"][1]["content"] == "植物叶片发黄怎么办？"


def test_expired_general_session_remains_subject_to_existing_ttl(settings):
    def unexpected_request(request):
        raise AssertionError("Expired sessions must fail before contacting a provider")

    with TestClient(make_app(settings, unexpected_request, jev_handler=unexpected_request)) as client:
        identifier = client.post("/api/v1/chat/sessions").json()["session_id"]
        with sqlite3.connect(settings.chat_db_path) as connection:
            connection.execute("UPDATE conversations SET expires_at = ? WHERE recognition_id = ?", (time.time() - 1, identifier))
        response = client.post("/api/v1/chat", json={"recognition_id": identifier, "message": "怎么养护？"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "conversation_not_found"
