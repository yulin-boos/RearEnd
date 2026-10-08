from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3

from fastapi.testclient import TestClient
import httpx
import pytest

from Common.config import Settings
from Knowledge.schemas import FOCUS_INSTRUCTIONS, KnowledgeDraft
from Knowledge.store import KnowledgeStore
from tests.test_chat import completion, jev_result, make_app, recognize


@pytest.fixture
def settings(tmp_path):
    return Settings(result_dir=tmp_path / "results", chat_db_path=tmp_path / "chat.sqlite3",
                    knowledge_db_path=tmp_path / "knowledge.sqlite3", deepseek_api_key="test-key-not-real",
                    typesafe_api_key="typesafe-test-not-real", cors_origins=["null"])


def draft_data():
    return {"title": "苹果疑似黑星病的环境管理", "crop": "苹果", "condition": "黑星病（待核实）",
            "question": "苹果叶片出现疑似黑星病斑点时如何改善种植环境？",
            "answer": "减少叶面持续潮湿，改善通风，浇水尽量避免打湿叶片，并观察斑点是否扩散。仍需核实病因。",
            "applicability": "苹果叶片有斑点、种植环境潮湿时的初步管理。",
            "uncertainty": "仅供观察和管理参考，不能凭模型候选确诊黑星病。",
            "keywords": ["苹果", "黑星病", "叶片斑点", "通风", "潮湿"]}


def valuable(value=0.99, grounded=0.99, private=0.01, focus="management"):
    result = jev_result()
    for name, score in (("knowledge_value", value), ("knowledge_grounded", grounded), ("knowledge_private", private)):
        result["answers"][name]["noul"] = score
    result["answers"]["knowledge_focus"] = {"type": "choice", "choice": focus, "confidence": 0.99,
        "probabilities": {name: float(name == focus) for name in FOCUS_INSTRUCTIONS}}
    return result


def verification(grounded=0.99, reusable=0.99, private=0.01, duplicate=0.01):
    return {"model": "jev-1.13.0", "answers": {name: {"type": "noul", "noul": score} for name, score in
        (("entry_grounded", grounded), ("entry_reusable", reusable), ("entry_private", private), ("entry_duplicate", duplicate))}}


def rows(settings):
    with closing(sqlite3.connect(settings.knowledge_db_path)) as connection:
        return connection.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]


def handlers(card=None, assessment=None, verified=None, direct=0.01):
    requests = {"deepseek": [], "jev": []}
    def deepseek(request):
        body = json.loads(request.content)
        requests["deepseek"].append(body)
        reply = json.dumps(card or draft_data(), ensure_ascii=False) if "response_format" in body else draft_data()["answer"]
        return httpx.Response(200, json=completion(reply))
    def jev(request):
        body = json.loads(request.content)
        requests["jev"].append(body)
        if "entry_grounded" in body["questions"]:
            result = verified or verification()
        elif any(name.startswith("match_") for name in body["questions"]):
            result = {"model": "jev-1.13.0", "answers": {name: {"type": "noul", "noul": direct if name.startswith("direct_") else 0.99}
                                                       for name in body["questions"]}}
        else:
            result = assessment or valuable()
        return httpx.Response(200, json=result)
    return deepseek, jev, requests


@pytest.mark.parametrize("assessment", [valuable(value=0.01), valuable(grounded=0.4),
                                       valuable(private=0.99), valuable(focus="none")])
def test_no_value_no_extraction_and_no_knowledge_insert(settings, assessment):
    deepseek, jev, requests = handlers(assessment=assessment)
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        data = recognize(client).json()
        assert data["chat"]["status"] == "ready" and data["chat"]["knowledge"]["status"] == "skipped"
        assert len(requests["deepseek"]) == 1 and len(requests["jev"]) == 2
        assert rows(settings) == 0
        assert len(client.app.state.chat.store.get(data["request_id"])["history"]) == 2


def test_valuable_turn_is_structured_verified_stored_and_publicly_queryable(settings):
    deepseek, jev, requests = handlers()
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        data = recognize(client).json()
        activity = data["chat"]["knowledge"]
        assert activity["status"] == "stored" and activity["decision"]["focus"] == "management"
        assert rows(settings) == 1
        extractor = requests["deepseek"][1]
        assert extractor["response_format"] == {"type": "json_object"}
        assert "Jev" in extractor["messages"][1]["content"]
        assert "管理建议" in extractor["messages"][1]["content"]
        assert data["request_id"] not in extractor["messages"][1]["content"]
        assert "knowledge_entry" in requests["jev"][2]["state"]
        response = client.get("/api/v1/knowledge", params={"query": "苹果黑星病如何改善通风", "crop": "苹果"}, headers={"Origin": "null"})
        assert response.status_code == 200 and response.headers["access-control-allow-origin"] == "null"
        entry = response.json()[0]
        assert entry["id"] == activity["entry_id"] and entry["expert_verified"] is False
        assert entry["source_type"] == "ai_summary"
        assert data["request_id"] not in response.text
        assert client.get(f"/api/v1/knowledge/{entry['id']}").json() == entry
        assert client.get("/api/v1/knowledge", params={"query": "黑星病", "crop": "番茄"}).json() == []
        assert client.get("/api/v1/knowledge/" + "a" * 32).status_code == 404


def test_new_user_after_restart_retrieves_existing_knowledge_without_new_insert(settings):
    deepseek, jev, requests = handlers()
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        old = recognize(client).json()
        identifier = old["chat"]["knowledge"]["entry_id"]
    def retrieval_jev(request):
        body = json.loads(request.content)
        if "knowledge_value" in body["questions"]:
            assert body["state"]["reference_knowledge"][0]["id"] == identifier
            return httpx.Response(200, json=valuable(value=0.01))
        return jev(request)
    with TestClient(make_app(settings, deepseek, jev_handler=retrieval_jev)) as client:
        new = recognize(client, auto_analyze="false").json()
        assert new["request_id"] != old["request_id"]
        response = client.post("/api/v1/chat", json={"recognition_id": new["request_id"], "message": "苹果黑星病如何改善通风？"})
        data = response.json()
        assert response.status_code == 200 and data["knowledge"]["source_ids"] == [identifier]
        assert data["knowledge"]["status"] == "skipped" and rows(settings) == 1
        assert identifier in requests["deepseek"][-1]["messages"][0]["content"]
        assert "未经专家验证" in requests["deepseek"][-1]["messages"][0]["content"]
        assert len([body for body in requests["deepseek"] if "response_format" in body]) == 1


@pytest.mark.parametrize("card", [{**draft_data(), "sql": "DROP TABLE conversations"},
    {**draft_data(), "answer": ""}, {**draft_data(), "uncertainty": ""}, {**draft_data(), "keywords": []}])
def test_invalid_structured_entry_never_writes_and_preserves_chat(settings, card):
    deepseek, jev, _ = handlers(card=card)
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        data = recognize(client).json()
        assert data["chat"]["status"] == "ready"
        assert data["chat"]["knowledge"]["error_code"] == "knowledge_invalid_entry"
        assert rows(settings) == 0 and len(client.app.state.chat.store.get(data["request_id"])["history"]) == 2


@pytest.mark.parametrize("private", ["person@example.com", "13812345678", "sk-abcdefghijklmnopqrstuv", "test-key-not-real"])
def test_private_markers_are_not_shared_even_if_model_approves(settings, private):
    deepseek, jev, requests = handlers(card={**draft_data(), "answer": draft_data()["answer"] + private})
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        data = recognize(client).json()
        assert data["chat"]["knowledge"]["status"] == "rejected"
        assert rows(settings) == 0 and len(requests["jev"]) == 2


@pytest.mark.parametrize("verified", [verification(grounded=0.4), verification(reusable=0.4), verification(private=0.99)])
def test_jev_rejects_unfaithful_private_or_useless_summary(settings, verified):
    deepseek, jev, _ = handlers(verified=verified)
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        data = recognize(client).json()
        assert data["chat"]["knowledge"]["status"] == "rejected" and rows(settings) == 0


def test_semantic_duplicate_is_not_inserted(settings):
    deepseek, jev, _ = handlers()
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        assert recognize(client).json()["chat"]["knowledge"]["status"] == "stored"
    deepseek, jev, _ = handlers(card={**draft_data(), "question": "苹果疑似黑星病怎么改善环境？"}, verified=verification(duplicate=0.99))
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        data = recognize(client).json()
        assert data["chat"]["knowledge"]["status"] == "duplicate" and rows(settings) == 1


def test_exact_duplicates_and_concurrent_inserts_are_atomic(settings):
    store = KnowledgeStore(settings.knowledge_db_path)
    store.initialize()
    draft = KnowledgeDraft(**draft_data())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.save(draft, "a" * 32, "deepseek-test", "jev-test"), range(2)))
    assert results[0][0] == results[1][0] and sum(created for _, created in results) == 1
    assert rows(settings) == 1
    varied = draft.model_copy(update={"question": draft.question.replace("？", "?")})
    assert store.save(varied, "b" * 32, "deepseek-test", "jev-test") == (results[0][0], False)


def test_missing_value_answer_does_not_authorize_insert_or_break_reply(settings):
    assessment = valuable()
    assessment["answers"].pop("knowledge_grounded")
    deepseek, jev, requests = handlers(assessment=assessment)
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        data = recognize(client).json()
        assert data["chat"]["status"] == "ready"
        assert data["chat"]["knowledge"]["error_code"] == "knowledge_assessment_invalid"
        assert len(requests["deepseek"]) == 1 and rows(settings) == 0


@pytest.mark.parametrize("phase", ["extraction", "verification", "write"])
def test_knowledge_failures_preserve_successful_chat(settings, phase, monkeypatch):
    deepseek, jev, _ = handlers()
    def failing_deepseek(request):
        if phase == "extraction" and "response_format" in json.loads(request.content):
            return httpx.Response(500, text="private upstream information")
        return deepseek(request)
    def failing_jev(request):
        if phase == "verification" and "entry_grounded" in json.loads(request.content)["questions"]:
            raise httpx.ReadTimeout("private upstream information", request=request)
        return jev(request)
    with TestClient(make_app(settings, failing_deepseek, jev_handler=failing_jev)) as client:
        if phase == "write":
            def fail(*args): raise sqlite3.OperationalError("private database details")
            monkeypatch.setattr(client.app.state.chat.knowledge.store, "save", fail)
        response = recognize(client)
        data = response.json()
        assert response.status_code == 200 and data["chat"]["status"] == "ready"
        assert data["chat"]["knowledge"]["status"] == "error" and rows(settings) == 0
        assert "private upstream information" not in response.text
        assert "private database details" not in response.text


def test_unmatched_or_unavailable_knowledge_is_not_given_to_deepseek(settings):
    deepseek, jev, requests = handlers(assessment=valuable(value=0.01))
    def matcher(request):
        body = json.loads(request.content)
        if any(name.startswith("match_") for name in body["questions"]):
            return httpx.Response(200, json={"model": "jev-1.13.0", "answers": {
                name: {"type": "noul", "noul": 0.1} for name in body["questions"]}})
        return jev(request)
    with TestClient(make_app(settings, deepseek, jev_handler=matcher)) as client:
        store = client.app.state.chat.knowledge.store
        store.save(KnowledgeDraft(**draft_data()), "a" * 32, "test", "test")
        data = recognize(client).json()
        assert data["chat"]["knowledge"]["source_ids"] == []
        assert "可参考的历史植物知识" not in requests["deepseek"][0]["messages"][0]["content"]


def test_search_parameters_cannot_execute_sql(settings):
    deepseek, jev, _ = handlers()
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        identifier = recognize(client).json()["chat"]["knowledge"]["entry_id"]
        response = client.get("/api/v1/knowledge", params={"query": "'); DROP TABLE knowledge_entries; --"})
        assert response.status_code == 200 and rows(settings) == 1
        assert client.get(f"/api/v1/knowledge/{identifier}").status_code == 200
        assert client.get("/api/v1/knowledge", params={"query": "病"}).status_code == 422
        assert client.get("/api/v1/knowledge", params={"query": "病害", "limit": 1000}).status_code == 422


def test_truncated_reply_is_not_extracted(settings):
    _, jev, _ = handlers()
    calls = []
    def deepseek(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=completion(draft_data()["answer"], finish_reason="length"))
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        data = recognize(client).json()
        assert data["chat"]["truncated"] is True
        assert data["chat"]["knowledge"]["error_code"] == "knowledge_incomplete_reply"
        assert len(calls) == 1 and rows(settings) == 0


def test_focus_ambiguity_does_not_override_positive_value_and_grounding(settings):
    assessment = valuable()
    assessment["answers"]["knowledge_focus"]["confidence"] = 0.6
    deepseek, jev, _ = handlers(assessment=assessment)
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        data = recognize(client).json()
        assert data["chat"]["knowledge"]["status"] == "stored"
        assert data["chat"]["knowledge"]["decision"]["focus_confidence"] == 0.6


def test_retrieval_outage_uses_no_unchecked_references_and_keeps_reply(settings):
    deepseek, jev, requests = handlers(assessment=valuable(value=0.01))
    def failing_matcher(request):
        if any(name.startswith("match_") for name in json.loads(request.content)["questions"]):
            return httpx.Response(500, text="private lookup diagnostics")
        return jev(request)
    with TestClient(make_app(settings, deepseek, jev_handler=failing_matcher)) as client:
        client.app.state.chat.knowledge.store.save(KnowledgeDraft(**draft_data()), "a" * 32, "test", "test")
        data = recognize(client).json()
        assert data["chat"]["status"] == "ready"
        assert data["chat"]["knowledge"]["retrieval_error_code"] == "jev_unavailable"
        assert data["chat"]["knowledge"]["source_ids"] == []
        assert "可参考的历史植物知识" not in requests["deepseek"][0]["messages"][0]["content"]


def test_stale_conversation_never_creates_shared_knowledge(settings, monkeypatch):
    deepseek, jev, requests = handlers()
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        data = recognize(client, auto_analyze="false").json()
        store = client.app.state.chat.store
        original = store.append_turn
        def changed(identifier, revision, question, reply, user_id=None, metadata=None):
            original(identifier, revision, "另一个已完成的提问", "另一个已完成的回复", user_id)
            return original(identifier, revision, question, reply, user_id, metadata)
        monkeypatch.setattr(store, "append_turn", changed)
        response = client.post("/api/v1/chat", json={"recognition_id": data["request_id"], "message": "苹果叶片斑点如何管理？"})
        assert response.status_code == 409 and response.json()["error"]["code"] == "conversation_changed"
        assert rows(settings) == 0 and len(requests["deepseek"]) == 1
