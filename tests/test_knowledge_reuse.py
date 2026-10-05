import json

from fastapi.testclient import TestClient
import httpx
import pytest

from Knowledge.schemas import KnowledgeDraft
from tests.test_chat import jev_result, make_app, recognize
from tests.test_knowledge import draft_data, handlers, rows, settings, valuable


def seed(client, **changes):
    store = client.app.state.chat.knowledge.store
    identifier, _ = store.save(KnowledgeDraft(**{**draft_data(), **changes}), "a" * 32, "test", "test")
    return store.get(identifier)


def ask(client, identifier, message=None, **extra):
    return client.post("/api/v1/chat", json={"recognition_id": identifier,
        "message": message or draft_data()["question"], **extra})


@pytest.mark.parametrize("direct", [0.85, 0.99])
def test_matching_answer_is_reused_without_deepseek_or_recapture_and_survives_restart(settings, direct):
    deepseek, jev, requests = handlers(direct=direct)
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        entry = seed(client)
        identifier = recognize(client, auto_analyze="false", confidence=0.9).json()["request_id"]
        response = ask(client, identifier)
        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "knowledge" and data["model"] == "knowledge"
        assert data["knowledge"]["status"] == "reused"
        assert data["knowledge"]["entry_id"] == entry.id
        assert data["knowledge"]["source_ids"] == [entry.id]
        assert data["knowledge"]["reuse_probability"] == direct
        assert data["knowledge"]["decision"]["store"] is True
        assert data["usage"] == {} and data["truncated"] is False
        assert data["reply"].startswith("当前识别仍有不确定性")
        assert entry.answer in data["reply"] and entry.applicability in data["reply"]
        assert entry.uncertainty in data["reply"]
        assert not requests["deepseek"] and rows(settings) == 1
        assert len(requests["jev"]) == 3
        assert requests["jev"][-1]["state"]["assistant_reply"] == data["reply"]
        assert requests["jev"][-1]["state"]["reference_knowledge"][0]["id"] == entry.id
        history = client.app.state.chat.store.get(identifier)["history"]
        assert history == [{"role": "user", "content": draft_data()["question"]},
                           {"role": "assistant", "content": data["reply"]}]
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        new_identifier = recognize(client, auto_analyze="false").json()["request_id"]
        assert ask(client, new_identifier).json()["knowledge"]["entry_id"] == entry.id
        response = ask(client, identifier, "那浇水的时候呢？")
        assert response.json()["history_length"] == 4
        assert requests["jev"][-2]["state"]["recent_conversation"] == history
        assert not requests["deepseek"] and rows(settings) == 1


@pytest.mark.parametrize("has_deepseek", [True, False])
def test_recognition_auto_analysis_reports_reuse_source_even_without_deepseek_key(settings, has_deepseek):
    if not has_deepseek:
        settings = settings.model_copy(update={"deepseek_api_key": None})
    deepseek, jev, requests = handlers(direct=0.99)
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        entry = seed(client)
        data = recognize(client).json()
        assert data["chat"]["status"] == "ready"
        assert data["chat"]["source"] == "knowledge" and data["chat"]["model"] == "knowledge"
        assert data["chat"]["configured"] is has_deepseek
        assert data["chat"]["guard_configured"] is True
        assert data["chat"]["knowledge"]["entry_id"] == entry.id
        assert not requests["deepseek"] and rows(settings) == 1


@pytest.mark.parametrize("direct,updates", [(0.849, {}), (0.96, {"knowledge_direct_min_match": 0.98}),
                                         (0.99, {"knowledge_direct_enabled": False})])
def test_helpful_but_not_directly_applicable_or_disabled_knowledge_goes_to_deepseek(settings, direct, updates):
    settings = settings.model_copy(update=updates)
    deepseek, jev, requests = handlers(assessment=valuable(value=0.01), direct=direct)
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        entry = seed(client)
        identifier = recognize(client, auto_analyze="false").json()["request_id"]
        response = ask(client, identifier)
        data = response.json()
        assert response.status_code == 200 and data["source"] == "deepseek"
        assert data["knowledge"]["status"] == "skipped" and data["knowledge"]["reuse_probability"] is None
        assert data["knowledge"]["source_ids"] == [entry.id]
        assert len(requests["deepseek"]) == 1 and rows(settings) == 1
        assert entry.id in requests["deepseek"][0]["messages"][0]["content"]
        matches = requests["jev"][1]["questions"]
        assert ("direct_0" in matches) is settings.knowledge_direct_enabled


def test_match_miss_without_deepseek_key_requires_configuration_and_keeps_history_empty(settings):
    settings = settings.model_copy(update={"deepseek_api_key": None})
    deepseek, jev, requests = handlers(direct=0.84)
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        seed(client)
        identifier = recognize(client, auto_analyze="false").json()["request_id"]
        response = ask(client, identifier)
        assert response.status_code == 503 and response.json()["error"]["code"] == "deepseek_not_configured"
        assert not requests["deepseek"] and rows(settings) == 1
        assert client.app.state.chat.store.get(identifier)["history"] == []


def test_rejected_preset_is_hidden_and_excluded_from_generation_references(settings):
    deepseek, jev, requests = handlers(assessment=valuable(value=0.01), direct=0.99)
    def guard(request):
        body = json.loads(request.content)
        if "assistant_reply" in body["state"] and "适用条件：" in body["state"]["assistant_reply"]:
            return httpx.Response(200, json=jev_result(off_topic=0.9))
        return jev(request)
    with TestClient(make_app(settings, deepseek, jev_handler=guard)) as client:
        entry = seed(client)
        identifier = recognize(client, auto_analyze="false").json()["request_id"]
        response = ask(client, identifier)
        data = response.json()
        assert response.status_code == 200 and data["source"] == "deepseek"
        assert "适用条件：" not in data["reply"]
        assert data["knowledge"]["retrieval_error_code"] == "knowledge_preset_rejected"
        assert data["knowledge"]["source_ids"] == []
        assert len(requests["deepseek"]) == 1
        assert entry.id not in requests["deepseek"][0]["messages"][0]["content"]
        assert client.app.state.chat.store.get(identifier)["history"][-1]["content"] == data["reply"]


@pytest.mark.parametrize("failure,code,status", [("timeout", "jev_timeout", 504), ("invalid", "jev_invalid_response", 502)])
def test_preset_output_check_failure_never_bypasses_guard_or_saves_history(settings, failure, code, status):
    deepseek, jev, requests = handlers(direct=0.99)
    def guard(request):
        if "assistant_reply" in json.loads(request.content)["state"]:
            if failure == "timeout":
                raise httpx.ReadTimeout("private upstream information", request=request)
            return httpx.Response(200, json={"model": "jev-test", "answers": {}})
        return jev(request)
    with TestClient(make_app(settings, deepseek, jev_handler=guard)) as client:
        seed(client)
        identifier = recognize(client, auto_analyze="false").json()["request_id"]
        response = ask(client, identifier)
        assert response.status_code == status and response.json()["error"]["code"] == code
        assert "private upstream" not in response.text
        assert not requests["deepseek"] and rows(settings) == 1
        assert client.app.state.chat.store.get(identifier)["history"] == []


def test_off_topic_question_cannot_use_presets_or_skip_scope_check(settings):
    deepseek, jev, requests = handlers(direct=0.99)
    def guard(request):
        if "assistant_reply" not in json.loads(request.content)["state"]:
            return httpx.Response(200, json=jev_result(off_topic=0.9))
        return jev(request)
    with TestClient(make_app(settings, deepseek, jev_handler=guard)) as client:
        seed(client)
        identifier = recognize(client, auto_analyze="false").json()["request_id"]
        response = ask(client, identifier, "给我写一段无关的代码")
        assert response.status_code == 403
        assert ask(client, identifier, source="knowledge").status_code == 422
        assert not requests["deepseek"] and not requests["jev"]
        assert client.app.state.chat.store.get(identifier)["history"] == []


@pytest.mark.parametrize("invalid", [None, True, 1.1])
def test_invalid_direct_match_scores_use_generation_without_unchecked_references(settings, invalid):
    deepseek, jev, requests = handlers(assessment=valuable(value=0.01), direct=invalid)
    with TestClient(make_app(settings, deepseek, jev_handler=jev)) as client:
        seed(client)
        data = recognize(client).json()["chat"]
        assert data["status"] == "ready" and data["source"] == "deepseek"
        assert data["knowledge"]["retrieval_error_code"] == "knowledge_match_invalid"
        assert data["knowledge"]["source_ids"] == []
        assert "可参考的历史植物知识" not in requests["deepseek"][0]["messages"][0]["content"]


def test_high_direct_score_still_requires_helpful_match_and_updated_context_is_checked(settings):
    deepseek, jev, requests = handlers(assessment=valuable(value=0.01), direct=0.99)
    def guard(request):
        body = json.loads(request.content)
        if "match_0" in body["questions"]:
            assert body["state"]["user_question"] == "我补充一下，其实是番茄而不是苹果，应该怎么管理？"
            rubric = body["questions"]["direct_0"]["instructions"]["question"]
            assert "ALL" in rubric
            assert "different crop" in body["questions"]["direct_0"]["criteria"]["false"]
            assert "Preserved uncertainty" in body["questions"]["direct_0"]["criteria"]["true"]
            return httpx.Response(200, json={"model": "jev-test", "answers": {
                "match_0": {"type": "noul", "noul": 0.1}, "direct_0": {"type": "noul", "noul": 0.99}}})
        return jev(request)
    with TestClient(make_app(settings, deepseek, jev_handler=guard)) as client:
        seed(client)
        identifier = recognize(client, auto_analyze="false").json()["request_id"]
        data = ask(client, identifier, "我补充一下，其实是番茄而不是苹果，应该怎么管理？").json()
        assert data["source"] == "deepseek" and data["knowledge"]["source_ids"] == []
        assert len(requests["deepseek"]) == 1


def test_best_complete_answer_selected_even_outside_reference_limit(settings):
    settings = settings.model_copy(update={"knowledge_top_k": 1})
    deepseek, jev, requests = handlers(direct=0.99)
    expected = None
    def guard(request):
        body = json.loads(request.content)
        if "match_0" in body["questions"]:
            scores = {}
            for index, entry in enumerate(body["state"]["knowledge_candidates"]):
                best = entry["id"] == expected
                scores[f"match_{index}"] = {"type": "noul", "noul": 0.9 if best else 0.99}
                scores[f"direct_{index}"] = {"type": "noul", "noul": 0.99 if best else 0.96}
            return httpx.Response(200, json={"model": "jev-test", "answers": scores})
        return jev(request)
    with TestClient(make_app(settings, deepseek, jev_handler=guard)) as client:
        seed(client)
        expected = seed(client, question="苹果疑似黑星病环境管理要点有哪些？").id
        data = recognize(client).json()["chat"]
        assert data["knowledge"]["entry_id"] == expected
        assert requests["jev"][-1]["state"]["reference_knowledge"][0]["id"] == expected
        assert not requests["deepseek"] and rows(settings) == 2
